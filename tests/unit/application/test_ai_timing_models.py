"""AI 打轴阶段 D：模型注册表 / 下载服务 / Runtime 探测 / 设置测试。

全部离线：下载走注入的 FakeTransport，pip 走注入 runner，
Runtime 探测用当前解释器。真实 Hub 下载与 venv 安装属于 §12.3 手动
烟测矩阵。
"""

import sys
from pathlib import Path

import pytest

from strange_uta_game.backend.application.ai_timing.models import (
    MANIFEST_NAME,
    ModelDownloadService,
    ModelFileEntry,
    ModelManifest,
    ModelRegistry,
    ModelRegistryError,
    filter_model_files,
    sha256_of_file,
    slugify_model_id,
)
from strange_uta_game.backend.application.ai_timing.runtime import (
    AiRuntimeError,
    AiRuntimeManager,
    RuntimeStatus,
)
from strange_uta_game.backend.application.ai_timing.settings import (
    AiTimingSettings,
    default_model_root,
    load_ai_timing_settings,
    resolve_model_root,
    save_ai_timing_settings,
)


class _FakeTransport:
    """内存假传输：可配置失败文件。"""

    def __init__(self, files=None, fail_on=None):
        self.files = files or {}  # filename -> bytes
        self.fail_on = fail_on or set()
        self.downloaded = []

    def list_files(self, repo_id, revision):
        return sorted((name, len(data)) for name, data in self.files.items())

    def download_file(self, repo_id, revision, filename, dest, progress, cancel):
        if filename in self.fail_on:
            raise ModelRegistryError(f"下载 {filename} 失败：模拟网络中断")
        if cancel():
            raise ModelRegistryError("已取消")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(self.files[filename])
        part.replace(dest)
        self.downloaded.append(filename)


def _hf_files():
    return {
        "config.json": b'{"model_type": "wav2vec2"}',
        "model.safetensors": b"weights-bytes",
        "vocab.json": b'{"a": 1}',
        "tf_model.h5": b"excluded",
        ".gitattributes": b"excluded",
        "onnx/model.onnx": b"excluded",
    }


class TestSlugAndFilter:
    def test_slugify(self):
        assert slugify_model_id("NextFire/mms-300m-X") == "NextFire__mms-300m-X"
        assert slugify_model_id("a b/c") == "a_b__c"

    def test_filter_model_files(self):
        filtered = filter_model_files(
            sorted((k, len(v)) for k, v in _hf_files().items())
        )
        names = [n for n, _ in filtered]
        assert "model.safetensors" in names
        assert "config.json" in names
        assert "tf_model.h5" not in names
        assert ".gitattributes" not in names
        assert "onnx/model.onnx" not in names


class TestModelRegistry:
    def test_missing(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        status = registry.validate("some/model")
        assert status.state == "missing"
        assert registry.resolve_model_path("some/model") is None

    def test_register_and_validate_ok(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        registry.register(
            ModelManifest(model_id="a/b", provider="wav2vec2", revision="main")
        )
        status = registry.validate("a/b")
        assert status.state == "ok"
        assert status.model_dir == tmp_path / "a__b"
        assert registry.resolve_model_path("a/b") == tmp_path / "a__b"

    def test_manifest_corrupt_json(self, tmp_path):
        model_dir = tmp_path / "m__x"
        model_dir.mkdir()
        (model_dir / MANIFEST_NAME).write_text("not json", encoding="utf-8")
        status = ModelRegistry(tmp_path).validate("m/x")
        assert status.state == "incomplete"

    def test_file_missing_and_size_mismatch(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        manifest = ModelManifest(
            model_id="a/b",
            provider="wav2vec2",
            revision="main",
            files=[ModelFileEntry(filename="model.safetensors", size=10, sha256="x")],
        )
        registry.register(manifest)
        status = registry.validate("a/b")
        assert status.state == "corrupt"
        assert "缺失" in status.message

        (registry.model_dir("a/b") / "model.safetensors").write_bytes(b"short")
        status = registry.validate("a/b")
        assert status.state == "corrupt"
        assert "大小不符" in status.message

    def test_deep_validation_detects_digest_mismatch(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        model_dir = registry.model_dir("a/b")
        model_dir.mkdir(parents=True)
        data = b"payload"
        (model_dir / "model.safetensors").write_bytes(data)
        registry.register(
            ModelManifest(
                model_id="a/b",
                provider="wav2vec2",
                revision="main",
                files=[
                    ModelFileEntry(
                        filename="model.safetensors",
                        size=len(data),
                        sha256=sha256_of_file(model_dir / "model.safetensors"),
                    )
                ],
            )
        )
        assert registry.validate("a/b", deep=True).state == "ok"
        # 等长篡改：绕过大小校验，专测 sha256 深度校验
        (model_dir / "model.safetensors").write_bytes(b"qayload")
        status = registry.validate("a/b", deep=True)
        assert status.state == "corrupt"
        assert "校验失败" in status.message

    def test_clear_partial_downloads(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        model_dir = registry.model_dir("a/b")
        model_dir.mkdir(parents=True)
        (model_dir / "x.part").write_bytes(b"1")
        (model_dir / "config.json").write_bytes(b"2")
        removed = registry.clear_partial_downloads("a/b")
        assert removed == 1
        assert not (model_dir / "x.part").exists()
        assert (model_dir / "config.json").is_file()

    def test_list_installed(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        registry.register(
            ModelManifest(model_id="a/one", provider="wav2vec2", revision="main")
        )
        registry.register(
            ModelManifest(model_id="a/two", provider="mms_fa", revision="main")
        )
        ids = {m.model_id for m in registry.list_installed()}
        assert ids == {"a/one", "a/two"}


class TestModelDownloadService:
    def test_download_registers_manifest_atomically(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        transport = _FakeTransport(files=_hf_files())
        service = ModelDownloadService(registry, transport)
        events = []
        target = service.download(
            "NextFire/demo",
            "wav2vec2",
            license_text="CC-BY-NC-SA-4.0",
            progress=lambda p, m: events.append((p, m)),
        )
        assert target == tmp_path / "NextFire__demo"
        assert (target / MANIFEST_NAME).is_file()
        assert (target / "model.safetensors").read_bytes() == b"weights-bytes"
        manifest = registry.read_manifest("NextFire/demo")
        assert manifest.license == "CC-BY-NC-SA-4.0"
        names = {f.filename for f in manifest.files}
        assert "tf_model.h5" not in names
        assert registry.validate("NextFire/demo").state == "ok"
        assert events[-1] == (100, "模型下载完成")
        # 幂等：已安装直接返回，不重复下载
        again = service.download("NextFire/demo", "wav2vec2")
        assert again == target
        assert transport.downloaded.count("model.safetensors") == 1

    def test_interrupted_download_not_registered(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        transport = _FakeTransport(files=_hf_files(), fail_on={"model.safetensors"})
        service = ModelDownloadService(registry, transport)
        with pytest.raises(ModelRegistryError, match="模拟网络中断"):
            service.download("NextFire/demo", "wav2vec2")
        status = registry.validate("NextFire/demo")
        assert status.state == "incomplete"
        assert registry.resolve_model_path("NextFire/demo") is None
        assert not (registry.model_dir("NextFire/demo") / MANIFEST_NAME).exists()

    def test_cancel_before_manifest(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        transport = _FakeTransport(files=_hf_files())
        service = ModelDownloadService(registry, transport)
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 3  # 下载 1-2 个文件后取消

        with pytest.raises(ModelRegistryError, match="已取消"):
            service.download("NextFire/demo", "wav2vec2", cancel=cancel)
        assert not (registry.model_dir("NextFire/demo") / MANIFEST_NAME).exists()

    def test_empty_repo_rejected(self, tmp_path):
        registry = ModelRegistry(tmp_path)
        transport = _FakeTransport(files={".gitattributes": b"x"})
        service = ModelDownloadService(registry, transport)
        with pytest.raises(ModelRegistryError, match="没有可下载的文件"):
            service.download("NextFire/empty", "wav2vec2")


class TestRuntimeProbe:
    def test_probe_current_interpreter(self):
        status = AiRuntimeManager().probe()
        if status.available:
            pytest.skip("本机已具备完整对齐依赖，跳过缺失路径断言")
        assert not status.available
        assert "对齐" in status.message

    def test_probe_nonexistent_path(self, tmp_path):
        status = AiRuntimeManager().probe(str(tmp_path / "nope" / "python.exe"))
        assert not status.available
        assert "路径不存在" in status.message

    def test_summary(self):
        ok = RuntimeStatus(
            available=True,
            torch_version="2.9.0",
            transformers_version="4.44.0",
            cuda_available=True,
        )
        assert "CUDA" in ok.summary
        bad = RuntimeStatus(available=False, message="缺少对齐依赖（torch）")
        assert bad.summary == "缺少对齐依赖（torch）"


class TestRuntimeInstall:
    def _fake_venv(self, monkeypatch, tmp_path_factory):
        """把 venv.create 替换为只放置假 python.exe 的轻量实现。"""
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        def fake_create(target, **kwargs):
            target = Path(target)
            python = (
                target / "Scripts" / "python.exe"
                if sys.platform == "win32"
                else target / "bin" / "python"
            )
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("#fake", encoding="utf-8")

        monkeypatch.setattr(rt.venv, "create", fake_create)

    def test_install_with_injected_pip_runner(self, tmp_path, monkeypatch):
        self._fake_venv(monkeypatch, tmp_path)
        monkeypatch.setattr(
            AiRuntimeManager,
            "probe",
            lambda self, python_exe="", timeout_s=30.0: RuntimeStatus(
                available=True,
                python_path=python_exe,
                torch_version="2.9.0",
                transformers_version="4.44.0",
                cuda_available=False,
            ),
        )
        manager = AiRuntimeManager(pip_runner=lambda exe, args, on_line, cancel: 0)
        events = []
        status = manager.install(
            tmp_path / "rt", progress=lambda p, m: events.append((p, m))
        )
        assert status.available
        assert events[-1][1] == "运行环境就绪"

    def test_install_pip_failure_raises(self, tmp_path, monkeypatch):
        self._fake_venv(monkeypatch, tmp_path)
        manager = AiRuntimeManager(pip_runner=lambda exe, a, o, c: 3)
        with pytest.raises(AiRuntimeError, match="pip 返回码 3"):
            manager.install(tmp_path / "rt")

    def test_install_cancel_raises(self, tmp_path, monkeypatch):
        self._fake_venv(monkeypatch, tmp_path)
        manager = AiRuntimeManager(pip_runner=lambda exe, a, o, c: 0)
        with pytest.raises(AiRuntimeError, match="已取消"):
            manager.install(tmp_path / "rt", cancel=lambda: True)


class TestSettings:
    def test_roundtrip(self):
        store = {}

        def getter(path, default=None):
            cur = store
            for key in path.split("."):
                if isinstance(cur, dict) and key in cur:
                    cur = cur[key]
                else:
                    return default
            return cur

        def setter(path, value):
            keys = path.split(".")
            cur = store
            for key in keys[:-1]:
                cur = cur.setdefault(key, {})
            cur[keys[-1]] = value

        settings = AiTimingSettings(
            provider="mms_fa",
            device="cpu",
            download_mirror="https://hf-mirror.com",
            runtime_python="C:/rt/python.exe",
        )
        save_ai_timing_settings(setter, settings)
        assert load_ai_timing_settings(getter) == settings

    def test_defaults_when_empty(self):
        loaded = load_ai_timing_settings(lambda path, default: default)
        assert loaded == AiTimingSettings()
        assert loaded.provider == "wav2vec2"
        assert loaded.tail_snap is True

    def test_resolve_model_root_explicit(self, tmp_path):
        settings = AiTimingSettings(model_root=str(tmp_path))
        assert resolve_model_root(settings) == tmp_path

    def test_default_root_not_in_cache(self):
        from strange_uta_game.app_dirs import cache_dir

        root = default_model_root()
        assert root.name == "ai_models"
        assert cache_dir() not in root.parents  # 模型不放自动清理目录
