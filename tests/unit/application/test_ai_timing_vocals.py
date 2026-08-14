"""AI 打轴阶段 E：人声发现与 AI 缓存测试。

覆盖验收门槛：相同源音频、模型和参数不重复分离（缓存命中）；
变化任一关键字段均正确失效；严格 ``_人声`` 匹配；中断写入不可见；
LRU 各保留最近 2 项；锁保护与根目录越界安全。
"""

from pathlib import Path

import pytest

from strange_uta_game.backend.application.ai_timing.vocals import (
    AiCache,
    VocalPreparationService,
    alignment_cache_metadata,
    cache_key,
    find_sibling_vocals,
    vocal_cache_metadata,
)


def _meta(media="m1", model="inst_v1e", stem="人声", params=None):
    return vocal_cache_metadata(
        media_sha256=media,
        separation_model=model,
        stem=stem,
        params=params,
    )


@pytest.fixture()
def vocal_file(tmp_path):
    """按需生成独立人声文件（每次调用返回新文件）。"""
    counter = {"n": 0}

    def _make(data: bytes = b"vocal") -> Path:
        counter["n"] += 1
        p = tmp_path / f"vocal_{counter['n']}.wav"
        p.write_bytes(data)
        return p

    return _make


class TestSiblingMatch:
    def test_strict_suffix_match(self, tmp_path):
        source = tmp_path / "song.flac"
        source.write_bytes(b"x")
        vocal = tmp_path / "song_人声.wav"
        vocal.write_bytes(b"v")
        (tmp_path / "song_人声演唱会.wav").write_bytes(b"noise")
        (tmp_path / "song_vocals.wav").write_bytes(b"noise")
        (tmp_path / "other_人声.wav").write_bytes(b"noise")
        assert find_sibling_vocals(source) == [vocal]

    def test_lossless_preferred_and_deterministic(self, tmp_path):
        source = tmp_path / "song.mp3"
        source.write_bytes(b"x")
        mp3 = tmp_path / "song_人声.mp3"
        wav = tmp_path / "song_人声.wav"
        flac = tmp_path / "song_人声.flac"
        for p in (mp3, wav, flac):
            p.write_bytes(b"v")
        # 无损档（wav/flac）优先于有损；无损内部按声明顺序 wav 优先
        #（工作台分离默认输出 wav）
        assert find_sibling_vocals(source) == [wav, flac, mp3]

    def test_no_match(self, tmp_path):
        source = tmp_path / "song.flac"
        source.write_bytes(b"x")
        assert find_sibling_vocals(source) == []


class TestAiCacheVocals:
    def test_store_and_lookup_roundtrip(self, tmp_path, vocal_file):
        cache = AiCache(tmp_path / "ai")
        stored = cache.store_vocal(_meta(), vocal_file())
        assert stored.is_file()
        assert cache.lookup_vocal(_meta()) == stored

    def test_key_field_change_invalidates(self, tmp_path, vocal_file):
        cache = AiCache(tmp_path / "ai")
        cache.store_vocal(_meta(), vocal_file())
        # 媒体 / 模型 / stem / 参数：任一变化都必须 miss
        assert cache.lookup_vocal(_meta(media="m2")) is None
        assert cache.lookup_vocal(_meta(model="model2")) is None
        assert cache.lookup_vocal(_meta(stem="Vocals")) is None
        assert cache.lookup_vocal(_meta(params={"aggression": 1})) is None

    def test_lookup_detects_tampering(self, tmp_path, vocal_file):
        cache = AiCache(tmp_path / "ai")
        stored = cache.store_vocal(_meta(), vocal_file())
        stored.write_bytes(b"tampered-different-length")
        assert cache.lookup_vocal(_meta()) is None

    def test_incomplete_store_invisible(self, tmp_path):
        """中断（无 manifest）的条目不可见。"""
        cache = AiCache(tmp_path / "ai")
        entry = cache.vocals_dir() / cache_key(_meta())
        entry.mkdir(parents=True)
        (entry / "vocals.wav").write_bytes(b"partial")
        assert cache.lookup_vocal(_meta()) is None

    def test_lru_keeps_last_two_per_type(self, tmp_path, vocal_file):
        cache = AiCache(tmp_path / "ai")
        cache.store_vocal(_meta(media="m0"), vocal_file())
        cache.store_vocal(_meta(media="m1"), vocal_file())
        # 在触发逐出前刷新 m0：应保留 m0（最近使用）与 m2（最后写入）
        cache.lookup_vocal(_meta(media="m0"))
        cache.store_vocal(_meta(media="m2"), vocal_file())
        dirs = {p.name for p in cache.vocals_dir().iterdir() if p.is_dir()}
        assert len(dirs) == 2
        assert cache_key(_meta(media="m0")) in dirs
        assert cache_key(_meta(media="m2")) in dirs

    def test_locked_entry_survives_prune(self, tmp_path, vocal_file):
        cache = AiCache(tmp_path / "ai")
        cache.store_vocal(_meta(media="m0"), vocal_file())
        token = cache.lock("vocals", _meta(media="m0"))
        for i in range(1, 4):
            cache.store_vocal(_meta(media=f"m{i}"), vocal_file())
        dirs = {p.name for p in cache.vocals_dir().iterdir() if p.is_dir()}
        assert cache_key(_meta(media="m0")) in dirs  # 带锁条目不被清理
        cache.unlock("vocals", _meta(media="m0"), token)
        cache.prune()
        dirs = {p.name for p in cache.vocals_dir().iterdir() if p.is_dir()}
        assert len(dirs) == 2
        assert cache_key(_meta(media="m0")) not in dirs

    def test_clean_work(self, tmp_path):
        cache = AiCache(tmp_path / "ai")
        work = cache.work_dir()
        work.mkdir(parents=True)
        (work / "tmp.bin").write_bytes(b"x")
        cache.clean_work()
        assert work.is_dir()
        assert not (work / "tmp.bin").exists()


class TestAiCacheAlignment:
    def test_roundtrip_and_invalidation(self, tmp_path):
        cache = AiCache(tmp_path / "ai")
        meta = alignment_cache_metadata(
            media_sha256="m1",
            alignment_model="NextFire/x",
            annotation_digest="d1",
            options={"tail_snap": True},
        )
        payload = {"spans": [{"token_index": 0, "start_ms": 10, "end_ms": 20}]}
        cache.store_alignment(meta, payload)
        assert cache.lookup_alignment(meta) == payload
        changed = alignment_cache_metadata(
            media_sha256="m1",
            alignment_model="NextFire/x",
            annotation_digest="d2",  # 标注变化 → 失效
            options={"tail_snap": True},
        )
        assert cache.lookup_alignment(changed) is None


class TestVocalPreparation:
    def test_session_vocal_wins(self, tmp_path):
        cache = AiCache(tmp_path / "ai")
        session = tmp_path / "session.wav"
        session.write_bytes(b"s")
        svc = VocalPreparationService(cache, session_vocal_finder=lambda sha: session)
        result = svc.find_vocal(
            tmp_path / "song.flac",
            media_sha256="m1",
            separation_model="inst_v1e",
            stem="人声",
        )
        assert result.state == "session"
        assert result.path == session

    def test_cache_hit_before_sibling(self, tmp_path, vocal_file):
        cache = AiCache(tmp_path / "ai")
        cached = cache.store_vocal(_meta(), vocal_file())
        (tmp_path / "song_人声.wav").write_bytes(b"v")
        svc = VocalPreparationService(cache)
        result = svc.find_vocal(
            tmp_path / "song.flac",
            media_sha256="m1",
            separation_model="inst_v1e",
            stem="人声",
        )
        assert result.state == "cache"
        assert result.path == cached

    def test_unique_sibling(self, tmp_path):
        cache = AiCache(tmp_path / "ai")
        source = tmp_path / "song.flac"
        source.write_bytes(b"x")
        vocal = tmp_path / "song_人声.wav"
        vocal.write_bytes(b"v")
        result = VocalPreparationService(cache).find_vocal(
            source,
            media_sha256="m1",
            separation_model="inst_v1e",
            stem="人声",
        )
        assert result.state == "sibling"
        assert result.path == vocal

    def test_multiple_siblings_need_choice(self, tmp_path):
        cache = AiCache(tmp_path / "ai")
        source = tmp_path / "song.flac"
        source.write_bytes(b"x")
        wav = tmp_path / "song_人声.wav"
        mp3 = tmp_path / "song_人声.mp3"
        wav.write_bytes(b"v")
        mp3.write_bytes(b"v")
        result = VocalPreparationService(cache).find_vocal(
            source,
            media_sha256="m1",
            separation_model="inst_v1e",
            stem="人声",
        )
        assert result.state == "needs_choice"
        assert result.choices == [wav, mp3]  # 无损优先展示

    def test_missing_requires_separation(self, tmp_path):
        cache = AiCache(tmp_path / "ai")
        source = tmp_path / "song.flac"
        source.write_bytes(b"x")
        result = VocalPreparationService(cache).find_vocal(
            source,
            media_sha256="m1",
            separation_model="inst_v1e",
            stem="人声",
        )
        assert result.state == "separation"

    def test_register_separated_vocal_then_cache_hit(self, tmp_path, vocal_file):
        cache = AiCache(tmp_path / "ai")
        source = tmp_path / "song.flac"
        source.write_bytes(b"x")
        svc = VocalPreparationService(cache)
        registered = svc.register_separated_vocal(
            source,
            media_sha256="m1",
            separation_model="inst_v1e",
            stem="人声",
            vocal_path=vocal_file(),
        )
        result = svc.find_vocal(
            source,
            media_sha256="m1",
            separation_model="inst_v1e",
            stem="人声",
        )
        assert result.state == "cache"
        assert result.path == registered
