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

    def download_file(
        self, repo_id, revision, filename, dest, *, expected_size, progress, cancel
    ):
        if filename in self.fail_on:
            raise ModelRegistryError(f"下载 {filename} 失败：模拟网络中断")
        if cancel():
            raise ModelRegistryError("已取消")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(self.files[filename])
        progress(100, f"完成 {filename}")
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
        """把 venv.create 替换为只放置假 python.exe 的轻量实现。

        runtime.install 懒加载 venv（嵌入式 Python 没有 venv 模块，
        顶层 import 会让 worker 崩溃），因此这里注入 sys.modules 假模块。
        """
        import types

        def fake_create(target, **kwargs):
            target = Path(target)
            python = (
                target / "Scripts" / "python.exe"
                if sys.platform == "win32"
                else target / "bin" / "python"
            )
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("#fake", encoding="utf-8")

        fake_module = types.ModuleType("venv")
        fake_module.create = fake_create
        monkeypatch.setitem(sys.modules, "venv", fake_module)

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

    def test_install_dry_run_totals_and_package_progress(
        self, tmp_path, monkeypatch
    ):
        """dry-run 包数预估生效；pip 行按包粒度推进并带包/分速度。

        回归：此前 dry-run 引用了未定义的 ``args``（UnboundLocalError 被
        静默吞掉），包数量预估从未生效，进度退化为逐行 +1。
        """
        self._fake_venv(monkeypatch, tmp_path)
        monkeypatch.setattr(
            AiRuntimeManager,
            "probe",
            lambda self, python_exe="", timeout_s=30.0: RuntimeStatus(
                available=True, python_path=str(python_exe)
            ),
        )
        import json

        def fake_runner(exe, args, on_line, cancel):
            if "--dry-run" in args:
                report = {
                    "install": [
                        {"download_info": {"archive_info": {"size": size}}}
                        for size in (100, 200, 300)
                    ]
                }
                path = Path(args[args.index("--report") + 1])
                path.write_text(json.dumps(report), encoding="utf-8")
                on_line("Collecting torch\n")
                on_line("Collecting torchaudio\n")
                return 0
            for line in (
                "Collecting torch\n",
                "Downloading torch-2.9.1-cp314.whl.metadata (3.2 kB)\n",
                "Downloading torch-2.9.1-cp314.whl (765.9 MB)\n",
                "Using cached soundfile-0.13.whl (1.2 MB)\n",
                "Downloading librosa-0.10.2.whl (3.5 MB)\n",
                "Successfully installed torch torchaudio soundfile\n",
            ):
                on_line(line)
            return 0

        events = []
        manager = AiRuntimeManager(pip_runner=fake_runner)
        status = manager.install(
            tmp_path / "rt", progress=lambda p, m: events.append((p, m))
        )
        assert status.available
        msgs = [m for _, m in events]
        assert any("共 3 个包" in m for m in msgs)
        fetch_msgs = [m for m in msgs if "获取依赖" in m]
        assert fetch_msgs and all("包/分" in m for m in fetch_msgs)
        # .metadata 行不推进计数：torch 大包是第 1 个，末尾到 3/3
        assert "获取依赖 1/3" in fetch_msgs[0]
        assert "获取依赖 3/3" in fetch_msgs[-1]
        assert any("Successfully installed" in m for m in msgs)
        assert events[-1][1] == "运行环境就绪"
        # 百分比单调不回退（解析阶段停在原地而非回落）
        pcts = [p for p, _ in events]
        assert pcts == sorted(pcts)

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

    def test_install_uses_cuda_index_when_gpu_present(
        self, tmp_path, monkeypatch
    ):
        """检测到 NVIDIA GPU：走 PyTorch CUDA 索引 + -U（CPU 版原地升级）。

        回归：PyPI 的 Windows torch 默认 CPU-only wheel，此前安装器
        从不指定 CUDA 索引，装出来的环境 GPU 永远不可用。
        """
        self._fake_venv(monkeypatch, tmp_path)
        monkeypatch.setattr(
            AiRuntimeManager,
            "probe",
            lambda self, python_exe="", timeout_s=30.0: RuntimeStatus(
                available=True, python_path=str(python_exe)
            ),
        )
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "RTX 4080")
        seen = {}

        def fake_runner(exe, args, on_line, cancel):
            seen.setdefault("args", args)
            return 0

        events = []
        manager = AiRuntimeManager(pip_runner=fake_runner)
        status = manager.install(
            tmp_path / "rt", progress=lambda p, m: events.append((p, m))
        )
        assert status.available
        args = seen["args"]
        i = args.index("--extra-index-url")
        assert args[i + 1] == rt.TORCH_CUDA_INDEX_URL
        assert "-U" in args
        # PyPI torch 版本可能高于 CUDA 索引：必须钉「版本+cu128 本地标签」
        # 才能命中 CUDA wheel（且不会被已装的同版本 CPU 变体视为已满足）
        pin = f"=={rt.TORCH_CUDA_VERSION}+{rt.TORCH_CUDA_TAG}"
        assert f"torch{pin}" in args
        assert f"torchaudio{pin}" in args
        assert "librosa==0.10.2.post1" in args  # 既有钉子不受影响
        assert any("CUDA 版" in m for _, m in events)

    def test_install_cpu_route_without_gpu(self, tmp_path, monkeypatch):
        """无 NVIDIA GPU：不加 CUDA 索引/-U，镜像参数不受影响。"""
        self._fake_venv(monkeypatch, tmp_path)
        monkeypatch.setattr(
            AiRuntimeManager,
            "probe",
            lambda self, python_exe="", timeout_s=30.0: RuntimeStatus(
                available=True, python_path=str(python_exe)
            ),
        )
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "")
        seen = {}

        def fake_runner(exe, args, on_line, cancel):
            seen.setdefault("args", args)
            return 0

        events = []
        manager = AiRuntimeManager(pip_runner=fake_runner)
        manager.install(
            tmp_path / "rt",
            mirror="https://pypi.example/simple",
            progress=lambda p, m: events.append((p, m)),
        )
        args = seen["args"]
        assert "--extra-index-url" not in args
        assert "-U" not in args
        assert "-i" in args and "https://pypi.example/simple" in args
        assert any("CPU 版" in m for _, m in events)


class TestGpuDetection:
    def test_detect_nvidia_gpu_parses_name(self, monkeypatch):
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        class _C:
            returncode = 0
            stdout = "NVIDIA GeForce RTX 4080\n"

        monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: _C())
        assert rt.detect_nvidia_gpu() == "NVIDIA GeForce RTX 4080"

    def test_detect_no_gpu_returns_empty(self, monkeypatch):
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        def _raise(*a, **k):
            raise OSError("nvidia-smi 不存在")

        monkeypatch.setattr(rt.subprocess, "run", _raise)
        assert rt.detect_nvidia_gpu() == ""

    def test_probe_fills_gpu_name_from_torch_or_sm(self, monkeypatch):
        """CUDA 可用时用 torch 的设备名；CPU 版环境退回 nvidia-smi。"""
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        class _C:
            def __init__(self, stdout):
                self.returncode = 0
                self.stdout = stdout

        outputs = {
            "cuda": _C(
                '{"torch": "2.9.1+cu128", "cuda": true, '
                '"gpu": "NVIDIA GeForce RTX 4080", '
                '"transformers": "5.15.0"}'
            ),
            "cpu": _C(
                '{"torch": "2.9.1", "cuda": false, "gpu": "", '
                '"transformers": "5.15.0"}'
            ),
        }
        monkeypatch.setattr(
            rt.subprocess, "run", lambda *a, **k: outputs["cuda"]
        )
        manager = rt.AiRuntimeManager()
        status = manager.probe("")  # 空 = 当前解释器，跳过路径存在检查
        assert status.cuda_available
        assert status.gpu_name == "NVIDIA GeForce RTX 4080"

        monkeypatch.setattr(
            rt.subprocess, "run", lambda *a, **k: outputs["cpu"]
        )
        monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "RTX 4080")
        status = manager.probe("")
        assert not status.cuda_available
        assert status.gpu_name == "RTX 4080"


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


class TestStreamingTransport:
    """HfHubTransport 直连流式下载（本地 http.server，离线）。"""

    def _serve(self, tmp_path):
        import http.server
        import threading

        payload = tmp_path / "blob.bin"
        payload.write_bytes(b"x" * 300_000)
        handler = type(
            "H",
            (http.server.SimpleHTTPRequestHandler,),
            {"log_message": lambda *a: None, "translate_path": lambda s, p: str(payload)},
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_address[1]}/blob.bin"

    def test_progress_and_atomic_complete(self, tmp_path):
        from strange_uta_game.backend.application.ai_timing.models import (
            HfHubTransport,
        )

        server, url = self._serve(tmp_path)
        try:
            transport = HfHubTransport(endpoint=url.rsplit("/", 1)[0])
            dest = tmp_path / "out" / "blob.bin"
            percents = []
            transport.download_file(
                "repo",
                "rev",
                "blob.bin",
                dest,
                expected_size=300_000,
                progress=lambda p, m: percents.append(p),
                cancel=lambda: False,
            )
            assert dest.stat().st_size == 300_000
            assert not dest.with_name(dest.name + ".part").exists()
            assert percents and percents[-1] == 100
            assert percents[0] < percents[-1]
        finally:
            server.shutdown()

    def test_cancel_midstream_keeps_part(self, tmp_path):
        from strange_uta_game.backend.application.ai_timing.models import (
            HfHubTransport,
        )

        server, url = self._serve(tmp_path)
        try:
            transport = HfHubTransport(endpoint=url.rsplit("/", 1)[0])
            dest = tmp_path / "out" / "blob.bin"
            calls = {"n": 0}

            def cancel():
                calls["n"] += 1
                return calls["n"] > 1  # 收到首块后取消

            with pytest.raises(ModelRegistryError, match="已取消"):
                transport.download_file(
                    "repo",
                    "rev",
                    "blob.bin",
                    dest,
                    expected_size=300_000,
                    progress=lambda p, m: None,
                    cancel=cancel,
                )
            part = dest.with_name(dest.name + ".part")
            assert part.is_file() and 0 < part.stat().st_size < 300_000
        finally:
            server.shutdown()

    def test_resume_from_part(self, tmp_path):
        from strange_uta_game.backend.application.ai_timing.models import (
            HfHubTransport,
        )

        server, url = self._serve(tmp_path)
        try:
            transport = HfHubTransport(endpoint=url.rsplit("/", 1)[0])
            dest = tmp_path / "out" / "blob.bin"
            part = dest.with_name(dest.name + ".part")
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(b"x" * 100_000)  # 预置断点
            transport.download_file(
                "repo",
                "rev",
                "blob.bin",
                dest,
                expected_size=300_000,
                progress=lambda p, m: None,
                cancel=lambda: False,
            )
            assert dest.stat().st_size == 300_000  # 续传补齐
        finally:
            server.shutdown()


class TestSharedRuntimeInstall:
    """方案 B：向宿主托管 Runtime 增量安装（不建 venv、不装 torch）。"""

    def _fake_managed_python(self, tmp_path):
        exe = tmp_path / "managed" / "python.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#fake", encoding="utf-8")
        return str(exe)

    def test_detect_torch_build_parses_variants(self, monkeypatch):
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        class _C:
            def __init__(self, stdout="", rc=0):
                self.returncode = rc
                self.stdout = stdout

        cases = {
            "2.7.1+cu128\n": ("2.7.1", "cu128"),
            "2.7.1\n": ("2.7.1", "cpu"),
            "2.13.0+cpu\n": ("2.13.0", "cpu"),
        }
        for stdout, expected in cases.items():
            monkeypatch.setattr(
                rt.subprocess, "run", lambda *a, _s=stdout, **k: _C(_s)
            )
            assert rt.detect_torch_build("C:/fake/python.exe") == expected
        # torch 不可导入 → None
        monkeypatch.setattr(
            rt.subprocess, "run", lambda *a, **k: _C(rc=1)
        )
        assert rt.detect_torch_build("C:/fake/python.exe") is None

    def test_install_shared_pins_matching_torchaudio_without_torch(
        self, tmp_path, monkeypatch
    ):
        exe = self._fake_managed_python(tmp_path)
        monkeypatch.setattr(
            AiRuntimeManager,
            "probe",
            lambda self, python_exe="", timeout_s=30.0: RuntimeStatus(
                available=True, python_path=str(python_exe)
            ),
        )
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        monkeypatch.setattr(
            rt, "detect_torch_build", lambda exe: ("2.7.1", "cu128")
        )
        seen = {}

        def fake_runner(python, args, on_line, cancel):
            seen.setdefault("args", list(args))
            seen.setdefault("python", str(python))
            return 0

        events = []
        manager = AiRuntimeManager(pip_runner=fake_runner)
        status = manager.install_shared(
            exe,
            mirror="https://pypi.example/simple",
            progress=lambda p, m: events.append((p, m)),
        )
        assert status.available and status.python_path == exe
        args = seen["args"]
        # torchaudio 与 torch 同版本同变体；torch 本身绝不安装
        assert "torchaudio==2.7.1+cu128" in args
        i = args.index("--extra-index-url")
        assert args[i + 1] == "https://download.pytorch.org/whl/cu128"
        assert "-i" in args and "https://pypi.example/simple" in args
        assert "torch" not in args and "torch==2.7.1+cu128" not in args
        assert "transformers==5.15.0" in args and "librosa==0.10.2.post1" in args
        assert any("增量" in m or "复用" in m for _, m in events)
        assert events[-1][1] == "运行环境就绪"

    def test_install_shared_without_torch_raises(self, tmp_path, monkeypatch):
        exe = self._fake_managed_python(tmp_path)
        import strange_uta_game.backend.application.ai_timing.runtime as rt

        monkeypatch.setattr(rt, "detect_torch_build", lambda exe: None)
        manager = AiRuntimeManager(pip_runner=lambda *a: 0)
        with pytest.raises(AiRuntimeError, match="PyTorch"):
            manager.install_shared(exe)

    def test_install_shared_missing_interpreter_raises(self, tmp_path):
        manager = AiRuntimeManager(pip_runner=lambda *a: 0)
        with pytest.raises(AiRuntimeError, match="不存在"):
            manager.install_shared(str(tmp_path / "nope" / "python.exe"))
