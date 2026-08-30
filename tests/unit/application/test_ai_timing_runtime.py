"""AI 打轴 Runtime 测试：变体判定（显卡/驱动检测含 WMI 兜底）、
install_from_release 快路径的 CPU→CUDA 升级契约、统一日志 ailog。

全部离线：nvidia-smi / WMI / pip / 下载均以 monkeypatch 注入。
"""

import io
import zipfile
from pathlib import Path

import pytest

from strange_uta_game.backend.application.ai_timing import runtime as rt
from strange_uta_game.backend.application.ai_timing.ailog import ai_log_path, ailog
from strange_uta_game.backend.application.ai_timing.runtime import (
    RUNTIME_VARIANT_CPU,
    RUNTIME_VARIANT_CUDA,
    RuntimeStatus,
)


@pytest.fixture(autouse=True)
def _reset_detection_caches(monkeypatch):
    """显卡/WMI/probe 结论缓存按用例隔离（模块级进程内缓存）。"""
    monkeypatch.setattr(rt, "_gpu_name_cache", None)
    monkeypatch.setattr(rt, "_wmi_cache", None)
    monkeypatch.setattr(rt, "_probe_log_state", {})
    yield


def _fake_pip_runner(recorder=None):
    def _run(python_exe, args, on_line, cancel):
        if recorder is not None:
            recorder.append(list(args))
        on_line("Successfully installed fake")
        return 0

    return _run


class _Usage:
    def __init__(self, free_gb):
        self.free = free_gb * 1024**3


# ── WMI 驱动版本解析 ──


@pytest.mark.parametrize(
    ("wmi_version", "expected"),
    [
        ("32.0.15.6094", (560, 94)),
        ("33.0.15.7065", (570, 65)),  # 恰好达标线
        ("32.0.15.7066", (570, 66)),
        ("560.94", (560, 94)),
        ("abc", None),
        ("1.2", None),
    ],
)
def test_nv_driver_tuple_from_wmi(wmi_version, expected):
    assert rt._nv_driver_tuple_from_wmi(wmi_version) == expected


# ── 显卡检测：nvidia-smi 失败时的 WMI 兜底（集显+独显） ──


def test_detect_gpu_wmi_fallback_hybrid_laptop(monkeypatch):
    monkeypatch.setattr(rt, "_nvidia_smi", lambda *a: "")
    monkeypatch.setattr(
        rt,
        "_wmi_adapters",
        lambda: [
            {
                "name": "Intel(R) UHD Graphics 630",
                "driver_version": "31.0.13.0",
                "status": "OK",
            },
            {
                "name": "NVIDIA GeForce GTX 1660 Ti",
                "driver_version": "32.0.15.7065",
                "status": "OK",
            },
        ],
    )
    assert rt.detect_nvidia_gpu() == "NVIDIA GeForce GTX 1660 Ti"
    assert rt.nvidia_driver_supports_cu128() is True
    variant, reason = rt.explain_release_variant()
    assert variant == RUNTIME_VARIANT_CUDA
    assert "1660 Ti" in reason


def test_detect_gpu_wmi_fallback_old_driver(monkeypatch):
    monkeypatch.setattr(rt, "_nvidia_smi", lambda *a: "")
    monkeypatch.setattr(
        rt,
        "_wmi_adapters",
        lambda: [
            {"name": "Intel(R) UHD", "driver_version": "31.0.13.0", "status": "OK"},
            {
                "name": "NVIDIA GeForce GTX 1660 Ti",
                "driver_version": "32.0.15.6094",
                "status": "OK",
            },
        ],
    )
    variant, reason = rt.explain_release_variant()
    assert variant == RUNTIME_VARIANT_CPU
    assert "低于" in reason and "570.65" in reason
    assert "1660 Ti" in reason


def test_nvidia_smi_low_driver_wins_over_wmi(monkeypatch):
    """nvidia-smi 给出明确版本时不走 WMI 兜底（避免新旧驱动混报）。"""

    def _smi(*args):
        if any("driver_version" in a for a in args):
            return "550.12\n"
        return "GPU\n"

    monkeypatch.setattr(rt, "_nvidia_smi", _smi)
    monkeypatch.setattr(
        rt,
        "_wmi_adapters",
        lambda: [
            {"name": "NVIDIA GPU", "driver_version": "33.0.15.9999", "status": "OK"}
        ],
    )
    assert rt.nvidia_driver_supports_cu128() is False


def test_no_gpu_anywhere(monkeypatch):
    monkeypatch.setattr(rt, "_nvidia_smi", lambda *a: "")
    monkeypatch.setattr(
        rt,
        "_wmi_adapters",
        lambda: [
            {"name": "Intel(R) UHD", "driver_version": "31.0.13.0", "status": "OK"}
        ],
    )
    variant, reason = rt.explain_release_variant()
    assert variant == RUNTIME_VARIANT_CPU
    assert "未检测到 NVIDIA" in reason


# ── RuntimeStatus.note（CPU 版 + 有显卡 → 升级指引） ──


def test_attach_gpu_note_driver_too_old(monkeypatch):
    monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "GTX 1660 Ti")
    monkeypatch.setattr(
        rt,
        "explain_release_variant",
        lambda: (
            RUNTIME_VARIANT_CPU,
            "检测到 NVIDIA 显卡（GTX 1660 Ti），但驱动 550.12 低于 570.65…",
        ),
    )
    status = rt._attach_gpu_note(
        RuntimeStatus(available=True, torch_version="2.11.0+cpu")
    )
    assert "低于" in status.note


def test_attach_gpu_note_upgrade_hint(monkeypatch):
    monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "GTX 1660 Ti")
    monkeypatch.setattr(
        rt,
        "explain_release_variant",
        lambda: (RUNTIME_VARIANT_CUDA, "驱动达标"),
    )
    status = rt._attach_gpu_note(
        RuntimeStatus(available=True, torch_version="2.11.0+cpu")
    )
    assert "升级" in status.note


def test_attach_gpu_note_noop_without_gpu(monkeypatch):
    monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "")
    status = rt._attach_gpu_note(
        RuntimeStatus(available=True, torch_version="2.11.0+cpu")
    )
    assert status.note == ""


# ── install_from_release 快路径：CPU→CUDA 升级契约 ──


def _zip_with_runtime(dummy_exe_bytes=b"fake") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("runtime/python.exe", dummy_exe_bytes)
    return buf.getvalue()


def _manifest_for(zip_bytes: bytes) -> dict:
    return {
        "schema": 1,
        "archive": {
            "parts": [
                {
                    "url": "http://fake/base.zip.001",
                    "size": len(zip_bytes),
                    "sha256": "0" * 64,
                }
            ]
        },
        "torch": {
            "wheel": {
                "url": "http://fake/torch.whl",
                "filename": "torch.whl",
                "size": 8,
                "sha256": "0" * 64,
            }
        },
        "files": [{"path": "runtime/python.exe", "size": len(b"fake")}],
    }


def _stub_shared(status=None, calls=None):
    def _shared(self, python_exe, **kwargs):
        if calls is not None:
            calls.append(python_exe)
        return status or RuntimeStatus(
            available=True,
            python_path=python_exe,
            torch_version="2.11.0+cu128",
            cuda_available=True,
        )

    return _shared


def _patch_download(monkeypatch, payloads: dict):
    def _fake(url, dest, **kwargs):
        Path(dest).write_bytes(payloads[url])
        prog = kwargs.get("progress")
        if prog:
            prog(100, "done")

    monkeypatch.setattr(rt, "_download_verified", _fake)


def test_release_fast_path_upgrades_cpu_to_cuda(monkeypatch, tmp_path):
    """宿主达标 CUDA + 现有环境 CPU 版：必须放弃增量快路径走全量重装。"""
    zip_bytes = _zip_with_runtime()
    variants_fetched = []

    def _fetch(variant, *, proxy=""):
        variants_fetched.append(variant)
        return _manifest_for(zip_bytes)

    monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "GTX 1660 Ti")
    monkeypatch.setattr(
        rt,
        "explain_release_variant",
        lambda: (RUNTIME_VARIANT_CUDA, "检测到 NVIDIA 显卡（GTX 1660 Ti），驱动达标"),
    )
    monkeypatch.setattr(rt, "detect_torch_build", lambda exe: ("2.11.0", "cpu"))
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda p: _Usage(100))
    _patch_download(
        monkeypatch,
        {"http://fake/base.zip.001": zip_bytes, "http://fake/torch.whl": b"12345678"},
    )
    shared_calls = []
    monkeypatch.setattr(
        rt.AiRuntimeManager, "install_shared", _stub_shared(calls=shared_calls)
    )

    target = tmp_path / "ai_runtime"
    target.mkdir()
    (target / "runtime").mkdir()
    (target / "runtime" / "python.exe").write_bytes(b"old-cpu")
    manager = rt.AiRuntimeManager(pip_runner=_fake_pip_runner())
    progress_log = []
    status = manager.install_from_release(
        target,
        progress=lambda p, m: progress_log.append(m),
        manifest_fetch=_fetch,
    )
    # 全量路线：按 CUDA 变体拉清单，重装底座后增量收尾
    assert variants_fetched == [RUNTIME_VARIANT_CUDA]
    assert shared_calls and shared_calls[0].endswith("python.exe")
    assert status.cuda_available is True
    assert (target / "runtime" / "python.exe").read_bytes() == b"fake"
    assert not (target / "staging").exists()
    assert any("CPU 版" in m for m in progress_log)


def test_release_fast_path_keeps_matching_cuda(monkeypatch, tmp_path):
    """现有 cu128 + 宿主达标：走增量快路径，不重新下载。"""
    fetched = []

    def _fetch(variant, *, proxy=""):
        fetched.append(variant)
        raise AssertionError("快路径不应拉取清单")

    monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "GTX 1660 Ti")
    monkeypatch.setattr(
        rt,
        "explain_release_variant",
        lambda: (RUNTIME_VARIANT_CUDA, "驱动达标"),
    )
    monkeypatch.setattr(rt, "detect_torch_build", lambda exe: ("2.11.0", "cu128"))
    shared_calls = []
    monkeypatch.setattr(
        rt.AiRuntimeManager, "install_shared", _stub_shared(calls=shared_calls)
    )

    target = tmp_path / "ai_runtime"
    target.mkdir()
    (target / "runtime").mkdir()
    (target / "runtime" / "python.exe").write_bytes(b"cu")
    manager = rt.AiRuntimeManager(pip_runner=_fake_pip_runner())
    progress_log = []
    status = manager.install_from_release(
        target,
        progress=lambda p, m: progress_log.append(m),
        manifest_fetch=_fetch,
    )
    assert fetched == []
    assert shared_calls
    assert status.available is True
    assert any("跳过下载" in m for m in progress_log)


def test_release_fast_path_keeps_cpu_when_host_not_qualified(monkeypatch, tmp_path):
    """现有 CPU + 宿主不达标（无显卡）：保持增量，不触发升级。"""
    monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "")
    monkeypatch.setattr(
        rt,
        "explain_release_variant",
        lambda: (RUNTIME_VARIANT_CPU, "未检测到 NVIDIA 显卡"),
    )
    monkeypatch.setattr(rt, "detect_torch_build", lambda exe: ("2.11.0", "cpu"))
    shared_calls = []
    monkeypatch.setattr(
        rt.AiRuntimeManager, "install_shared", _stub_shared(calls=shared_calls)
    )
    fetched = []

    def _fetch(variant, *, proxy=""):
        fetched.append(variant)
        raise AssertionError("快路径不应拉取清单")

    target = tmp_path / "ai_runtime"
    target.mkdir()
    (target / "runtime").mkdir()
    (target / "runtime" / "python.exe").write_bytes(b"cpu")
    manager = rt.AiRuntimeManager(pip_runner=_fake_pip_runner())
    status = manager.install_from_release(target, manifest_fetch=_fetch)
    assert fetched == []
    assert shared_calls
    assert status.available is True


def test_release_keeps_cu128_when_host_downgraded(monkeypatch, tmp_path):
    """现有 cu128 + 宿主不再达标：保留复用（CUDA wheel 在 CPU 上可用）。"""
    monkeypatch.setattr(rt, "detect_nvidia_gpu", lambda: "")
    monkeypatch.setattr(
        rt,
        "explain_release_variant",
        lambda: (RUNTIME_VARIANT_CPU, "未检测到 NVIDIA 显卡"),
    )
    monkeypatch.setattr(rt, "detect_torch_build", lambda exe: ("2.11.0", "cu128"))
    shared_calls = []
    monkeypatch.setattr(
        rt.AiRuntimeManager, "install_shared", _stub_shared(calls=shared_calls)
    )
    target = tmp_path / "ai_runtime"
    target.mkdir()
    (target / "runtime").mkdir()
    (target / "runtime" / "python.exe").write_bytes(b"cu")
    manager = rt.AiRuntimeManager(pip_runner=_fake_pip_runner())
    status = manager.install_from_release(
        target, manifest_fetch=lambda *a, **k: pytest.fail("不应重装")
    )
    assert shared_calls
    assert status.available is True


# ── ailog 统一日志 ──


def test_ailog_env_override_and_rotation(monkeypatch, tmp_path):
    log_file = tmp_path / "logs" / "ai_timing.log"
    monkeypatch.setenv("SUG_AI_TIMING_LOG", str(log_file))
    assert ai_log_path() == log_file
    ailog("unit", "第一行")
    ailog("unit", "第二行\n带换行")
    text = log_file.read_text(encoding="utf-8")
    assert "第一行" in text and "第二行" in text
    assert "⏎" in text  # 换行被压平，单行不被撕开


def test_ailog_rotates_over_limit(monkeypatch, tmp_path):
    log_file = tmp_path / "ai_timing.log"
    monkeypatch.setenv("SUG_AI_TIMING_LOG", str(log_file))
    ailog("unit", "x" * 100)
    # 手工膨胀越过轮转阈值后，下一条写入触发轮转
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write("y" * 2_100_000)
    ailog("unit", "轮转后的新行")
    rotated = tmp_path / "ai_timing.log.1"
    assert rotated.is_file()
    assert "轮转后的新行" in log_file.read_text(encoding="utf-8")


# ── 底座下载源尝试链：先走代理再走镜像 ──


GH_URL = (
    "https://github.com/karaoke-studio/karaoke-studio-runtime"
    "/releases/download/pymss-runtime-v2.0.18-r1/base.zip.001"
)


def test_github_mirror_candidates_follow_source_order():
    # 显式传 order，避免依赖本机 config.json 的源排序
    assert rt._github_mirror_candidates(GH_URL, order=["github"]) == []
    mirrors = rt._github_mirror_candidates(GH_URL, order=["gh-proxy", "github"])
    from strange_uta_game.updater.sources import GH_PROXY_PREFIXES

    assert mirrors == [f"{prefix}/{GH_URL}" for prefix in GH_PROXY_PREFIXES]
    # 非 GitHub URL（torch wheel 的 pytorch CDN）不套镜像
    assert (
        rt._github_mirror_candidates("https://download-r2.pytorch.org/whl/x.whl")
        == []
    )


def test_download_attempts_proxy_first_then_mirror(monkeypatch):
    monkeypatch.setattr(
        rt,
        "_github_mirror_candidates",
        lambda url, order=None: (
            [f"https://gh-proxy.com/{url}"]
            if url.startswith("https://github.com/")
            else []
        ),
    )
    proxy = "http://127.0.0.1:7897"
    proxied = {"http": proxy, "https": proxy}
    direct = {"http": None, "https": None}

    att = rt._download_attempts(GH_URL, proxy)
    # 1) 官方直链 + 代理；2) 镜像 + 代理；3) 镜像直连兜底
    assert att == [
        (GH_URL, proxied),
        (f"https://gh-proxy.com/{GH_URL}", proxied),
        (f"https://gh-proxy.com/{GH_URL}", direct),
    ]
    # 未配置代理：直连（proxies=None → requests 读环境变量）+ 镜像直连
    assert rt._download_attempts(GH_URL, "") == [
        (GH_URL, None),
        (f"https://gh-proxy.com/{GH_URL}", None),
    ]
    # 非 GitHub URL：代理 + 直连兜底，无镜像展开
    att3 = rt._download_attempts("https://download-r2.pytorch.org/whl/x.whl", proxy)
    assert att3 == [
        ("https://download-r2.pytorch.org/whl/x.whl", proxied),
        ("https://download-r2.pytorch.org/whl/x.whl", direct),
    ]


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self._payload


def test_download_verified_rotates_to_mirror(monkeypatch, tmp_path):
    """官方直连失败 → 自动换 gh-proxy 镜像；哈希校验照常。"""
    import hashlib

    monkeypatch.setattr(
        rt, "_github_mirror_candidates", lambda url, order=None: [f"https://gh-proxy.com/{url}"]
    )
    seen = []

    def fake_get(url, **kwargs):
        seen.append(url)
        if url == GH_URL:
            raise RuntimeError("direct blocked")
        return _FakeResp(b"hello-runtime")

    monkeypatch.setattr("requests.get", fake_get)
    dest = tmp_path / "base.zip.001"
    rt._download_verified(
        GH_URL,
        dest,
        expected_size=len(b"hello-runtime"),
        expected_sha256=hashlib.sha256(b"hello-runtime").hexdigest(),
        progress=lambda p, m: None,
    )
    assert seen == [GH_URL, f"https://gh-proxy.com/{GH_URL}"]
    assert dest.read_bytes() == b"hello-runtime"


def test_download_verified_all_sources_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rt, "_github_mirror_candidates", lambda url, order=None: [f"https://gh-proxy.com/{url}"]
    )

    def fake_get(url, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr("requests.get", fake_get)
    with pytest.raises(rt.AiRuntimeError) as ei:
        rt._download_verified(
            GH_URL,
            tmp_path / "x.zip",
            expected_size=1,
            expected_sha256="0" * 64,
        )
    assert "已尝试 2 个下载源" in str(ei.value)


def test_fetch_manifest_rotates_to_mirror(monkeypatch):
    """清单拉取同样走尝试链：直连失败自动换镜像。"""
    import types

    monkeypatch.setattr(
        rt, "_github_mirror_candidates", lambda url, order=None: [f"https://gh-proxy.com/{url}"]
    )

    def fake_get(url, **kwargs):
        if url == rt.release_manifest_url("windows-cpu"):
            raise RuntimeError("direct blocked")
        return types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"schema": 1, "archive": {"parts": [{"url": "x"}]}},
        )

    monkeypatch.setattr("requests.get", fake_get)
    manifest = rt.fetch_runtime_release_manifest("windows-cpu")
    assert manifest["schema"] == 1


def test_pip_default_index_is_aliyun():
    """pip 默认索引 = 阿里源（官方 PyPI 国内直连常超时）。"""
    assert rt.PIP_DEFAULT_INDEX == "https://mirrors.aliyun.com/pypi/simple/"
