"""AI 打轴人声发现与 AI 缓存（阶段 E）。

发现顺序（§6.1，严格降级、不模糊猜测）：

1. embedded 宿主当前会话中与原音频身份匹配的人声（阶段 G 注入）；
2. ``.cache/ai_timing/vocals`` 中校验通过的人声；
3. 原音频同目录内严格匹配的 ``原文件名_人声.<ext>``（无损优先，
   多个严格候选时返回全部交由用户选择）；
4. 都没有 → 需要分离（由上层通过宿主/分离服务执行，完成后
   ``register_separated_vocal`` 落入缓存供下次复用）。

缓存结构（§7.2）::

    .cache/ai_timing/
    ├── vocals/<key>/{manifest.json, vocals.wav}
    ├── alignment/<key>/{manifest.json, result.json}
    ├── work/
    └── logs/

缓存键 = sha256(规范 JSON(metadata))，人声键覆盖 媒体内容摘要 + 分离
模型身份 + stem + 参数 + 协议版本（§6.4）；对齐键另加 对齐模型与选项。
manifest 校验通过（complete + 大小 + 摘要）才算命中；中断写入不可见。
人声与对齐缓存默认各只保留最近使用的 2 个条目（LRU），带锁条目
（正在运行）不参与清理；清理严格限定在 ai_timing 根目录内。
"""

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

CACHE_PROTOCOL_VERSION = 1

# 同目录严格匹配支持的扩展名（无损优先）
VOCAL_EXTENSIONS: List[str] = ["wav", "flac", "m4a", "mp3", "ogg", "opus", "aac", "wma"]
_LOSSLESS_EXTENSIONS = {"wav", "flac"}

VOCAL_SUFFIX = "_人声"

_CHUNK_SIZE = 4 * 1024 * 1024


class AiCacheError(RuntimeError):
    """AI 缓存操作错误（中文消息）。"""


def sha256_of_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(metadata: Dict[str, object]) -> str:
    """规范 JSON → sha256 缓存键（字段排序保证稳定性）。"""
    encoded = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def vocal_cache_metadata(
    *,
    media_sha256: str,
    separation_model: str,
    stem: str,
    params: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """人声缓存键元数据（§6.4：媒体 + 分离模型 + stem + 参数 + 协议版本）。"""
    return {
        "kind": "vocals",
        "protocol": CACHE_PROTOCOL_VERSION,
        "media_sha256": media_sha256,
        "separation_model": separation_model,
        "stem": stem,
        "params": dict(params or {}),
    }


def alignment_cache_metadata(
    *,
    media_sha256: str,
    alignment_model: str,
    annotation_digest: str,
    options: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """对齐结果缓存键元数据（任一字段变化即失效）。"""
    return {
        "kind": "alignment",
        "protocol": CACHE_PROTOCOL_VERSION,
        "media_sha256": media_sha256,
        "alignment_model": alignment_model,
        "annotation_digest": annotation_digest,
        "options": dict(options or {}),
    }


# ──────────────────────────────────────────────
# 同目录严格匹配
# ──────────────────────────────────────────────


def find_sibling_vocals(source: Path) -> List[Path]:
    """原音频同目录内严格 ``<stem>_人声.<ext>`` 候选（无损优先，确定性排序）。

    只接受严格 ``_人声`` 后缀与受支持扩展名；不做任何模糊/相似度匹配。
    """
    source = Path(source)
    stem = source.stem
    candidates: List[Path] = []
    for ext in VOCAL_EXTENSIONS:
        candidate = source.with_name(f"{stem}{VOCAL_SUFFIX}.{ext}")
        if candidate.is_file():
            candidates.append(candidate)
    # 无损优先，其次按扩展名声明顺序（确定性）
    candidates.sort(
        key=lambda p: (
            0 if p.suffix.lstrip(".").lower() in _LOSSLESS_EXTENSIONS else 1,
            VOCAL_EXTENSIONS.index(p.suffix.lstrip(".").lower()),
        )
    )
    return candidates


# ──────────────────────────────────────────────
# AI 缓存
# ──────────────────────────────────────────────


class AiCache:
    """.cache/ai_timing 人声/对齐缓存（校验 manifest + 原子写 + LRU）。"""

    def __init__(self, root: Path, *, keep_per_type: int = 2):
        self._root = Path(root)
        self._keep_per_type = max(1, int(keep_per_type))

    @property
    def root(self) -> Path:
        return self._root

    def vocals_dir(self) -> Path:
        return self._root / "vocals"

    def alignment_dir(self) -> Path:
        return self._root / "alignment"

    def work_dir(self) -> Path:
        return self._root / "work"

    def _entry_dir(self, kind: str, key: str) -> Path:
        base = self.vocals_dir() if kind == "vocals" else self.alignment_dir()
        return base / key

    def _entry_is_inside_root(self, entry: Path) -> bool:
        """清理/读取前的安全检查：条目必须位于解析后的根目录内（§7.3）。"""
        try:
            entry.resolve().relative_to(self._root.resolve())
            return True
        except ValueError:
            return False

    # ── 通用 manifest ──

    def _read_manifest(self, entry: Path) -> Optional[dict]:
        try:
            payload = json.loads(
                (entry / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("complete") is not True:
            return None
        return payload

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    # ── 人声 ──

    def lookup_vocal(self, metadata: Dict[str, object]) -> Optional[Path]:
        """校验通过则返回人声文件路径并刷新 LRU；否则 None。"""
        key = cache_key(metadata)
        entry = self._entry_dir("vocals", key)
        payload = self._read_manifest(entry)
        if payload is None or payload.get("key") != key:
            return None
        vocal = entry / "vocals.wav"
        try:
            if (
                not vocal.is_file()
                or vocal.stat().st_size != int(payload.get("size", -1))
                or sha256_of_path(vocal) != str(payload.get("sha256", ""))
            ):
                return None
        except OSError:
            return None
        payload["last_used_at"] = int(time.time())
        self._write_json(entry / "manifest.json", payload)
        return vocal

    def store_vocal(
        self, metadata: Dict[str, object], vocal_path: Path
    ) -> Path:
        """把已完成的最终人声写入缓存（原子：.part → 改名 → manifest 最后）。"""
        vocal_path = Path(vocal_path)
        if not vocal_path.is_file():
            raise AiCacheError(f"人声文件不存在：{vocal_path}")
        key = cache_key(metadata)
        entry = self._entry_dir("vocals", key)
        entry.mkdir(parents=True, exist_ok=True)
        target = entry / "vocals.wav"
        partial = entry / f".vocals.{uuid.uuid4().hex}.part"
        try:
            with vocal_path.open("rb") as src, partial.open("xb") as dst:
                shutil.copyfileobj(src, dst, _CHUNK_SIZE)
                dst.flush()
                os.fsync(dst.fileno())
            digest = sha256_of_path(partial)
            os.replace(partial, target)
            now = int(time.time())
            self._write_json(
                entry / "manifest.json",
                {
                    "kind": "vocals",
                    "complete": True,
                    "key": key,
                    "metadata": metadata,
                    "size": target.stat().st_size,
                    "sha256": digest,
                    "created_at": now,
                    "last_used_at": now,
                },
            )
        finally:
            partial.unlink(missing_ok=True)
        self.prune()
        return target

    # ── 对齐结果 ──

    def lookup_alignment(self, metadata: Dict[str, object]) -> Optional[dict]:
        """返回缓存的对齐结果 payload；校验失败返回 None。"""
        key = cache_key(metadata)
        entry = self._entry_dir("alignment", key)
        payload = self._read_manifest(entry)
        if payload is None or payload.get("key") != key:
            return None
        try:
            result = json.loads(
                (entry / "result.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        payload["last_used_at"] = int(time.time())
        self._write_json(entry / "manifest.json", payload)
        return result

    def store_alignment(
        self, metadata: Dict[str, object], result_payload: dict
    ) -> Path:
        key = cache_key(metadata)
        entry = self._entry_dir("alignment", key)
        entry.mkdir(parents=True, exist_ok=True)
        result_path = entry / "result.json"
        partial = entry / f".result.{uuid.uuid4().hex}.part"
        try:
            with partial.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(result_payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(partial, result_path)
            now = int(time.time())
            self._write_json(
                entry / "manifest.json",
                {
                    "kind": "alignment",
                    "complete": True,
                    "key": key,
                    "metadata": metadata,
                    "created_at": now,
                    "last_used_at": now,
                },
            )
        finally:
            partial.unlink(missing_ok=True)
        self.prune()
        return result_path

    # ── 锁与清理 ──

    def lock(self, kind: str, key_or_metadata) -> str:
        """为正在运行的条目创建锁；返回锁 token（unlock 用）。"""
        key = (
            cache_key(key_or_metadata)
            if isinstance(key_or_metadata, dict)
            else str(key_or_metadata)
        )
        entry = self._entry_dir(kind, key)
        entry.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        (entry / f".lock.{token}").write_text(token, encoding="utf-8")
        return token

    def unlock(self, kind: str, key_or_metadata, token: str) -> None:
        key = (
            cache_key(key_or_metadata)
            if isinstance(key_or_metadata, dict)
            else str(key_or_metadata)
        )
        entry = self._entry_dir(kind, key)
        (entry / f".lock.{token}").unlink(missing_ok=True)

    def _is_locked(self, entry: Path) -> bool:
        try:
            return any(entry.glob(".lock.*"))
        except OSError:
            return False

    def prune(self) -> None:
        """人声与对齐缓存各保留最近使用的 keep_per_type 个条目。

        只清理解析后仍位于 ai_timing 根目录内的条目；带锁（正在运行）
        条目不参与清理（§7.3）；模型与 Runtime 永不在此目录、也永不被清理。
        """
        for base in (self.vocals_dir(), self.alignment_dir()):
            if not base.is_dir():
                continue
            entries: List[tuple] = []
            for entry in base.iterdir():
                if not entry.is_dir() or not self._entry_is_inside_root(entry):
                    continue
                if self._is_locked(entry):
                    continue
                payload = self._read_manifest(entry) or {}
                touched = int(
                    payload.get("last_used_at", payload.get("created_at", 0))
                )
                entries.append((touched, entry))
            entries.sort(key=lambda item: item[0], reverse=True)
            for _, entry in entries[self._keep_per_type :]:
                shutil.rmtree(entry, ignore_errors=True)

    def clean_work(self) -> None:
        """清理可再生工作文件（任务结束与下次启动时调用）。"""
        work = self.work_dir()
        if work.is_dir() and self._entry_is_inside_root(work):
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────
# 人声准备服务（发现顺序编排）
# ──────────────────────────────────────────────


@dataclass
class VocalCandidate:
    """一次人声发现的结果。"""

    state: str
    """session / cache / sibling / needs_choice / missing / separation"""

    path: Optional[Path] = None
    """可直接使用的人声文件（session/cache/唯一 sibling 时非空）。"""

    choices: List[Path] = field(default_factory=list)
    """state=needs_choice 时的全部严格候选（交由用户选择）。"""

    source_detail: str = ""


class VocalPreparationService:
    """按 §6.1 顺序发现可复用人声；分离执行由上层编排（F/G 阶段接线）。

    Args:
        cache: AI 缓存。
        session_vocal_finder: 宿主注入的会话人声查找器
            ``(media_sha256) -> Path | None``（embedded；standalone 为 None）。
    """

    def __init__(
        self,
        cache: AiCache,
        session_vocal_finder: Optional[Callable[[str], Optional[Path]]] = None,
    ):
        self._cache = cache
        self._session_vocal_finder = session_vocal_finder

    def find_vocal(
        self,
        source_path: Path,
        *,
        media_sha256: str,
        separation_model: str,
        stem: str,
        params: Optional[Dict[str, object]] = None,
        fingerprint: Optional[Callable[[Path], str]] = None,
    ) -> VocalCandidate:
        """执行 §6.1 的发现顺序（不含分离本身）。"""
        # ① 宿主会话人声（身份匹配由 finder 自行校验）
        if self._session_vocal_finder is not None:
            found = self._session_vocal_finder(media_sha256)
            if found is not None and Path(found).is_file():
                return VocalCandidate(
                    state="session",
                    path=Path(found),
                    source_detail="工作台本次会话已分离的人声",
                )

        # ② AI 缓存
        metadata = vocal_cache_metadata(
            media_sha256=media_sha256,
            separation_model=separation_model,
            stem=stem,
            params=params,
        )
        cached = self._cache.lookup_vocal(metadata)
        if cached is not None:
            return VocalCandidate(
                state="cache", path=cached, source_detail="AI 打轴人声缓存"
            )

        # ③ 同目录严格匹配
        siblings = find_sibling_vocals(Path(source_path))
        if len(siblings) == 1:
            return VocalCandidate(
                state="sibling",
                path=siblings[0],
                source_detail="原音频同目录的人声文件",
            )
        if len(siblings) > 1:
            return VocalCandidate(
                state="needs_choice", choices=siblings, source_detail="多个严格匹配的人声文件"
            )

        # ④ 需要分离
        return VocalCandidate(
            state="separation", source_detail="无可复用人声，需要执行分离"
        )

    def register_separated_vocal(
        self,
        source_path: Path,
        *,
        media_sha256: str,
        separation_model: str,
        stem: str,
        params: Optional[Dict[str, object]] = None,
        vocal_path: Path,
    ) -> Path:
        """分离完成后登记最终人声（进入缓存，供后续任务复用）。"""
        metadata = vocal_cache_metadata(
            media_sha256=media_sha256,
            separation_model=separation_model,
            stem=stem,
            params=params,
        )
        return self._cache.store_vocal(metadata, Path(vocal_path))


def default_ai_cache_root() -> Path:
    """standalone 默认 AI 缓存根目录（embedded 由宿主注入，§7.2）。"""
    from strange_uta_game.app_dirs import cache_dir

    return cache_dir() / "ai_timing"


__all__ = [
    "CACHE_PROTOCOL_VERSION",
    "VOCAL_EXTENSIONS",
    "VOCAL_SUFFIX",
    "AiCacheError",
    "sha256_of_path",
    "cache_key",
    "vocal_cache_metadata",
    "alignment_cache_metadata",
    "find_sibling_vocals",
    "AiCache",
    "VocalCandidate",
    "VocalPreparationService",
    "default_ai_cache_root",
]
