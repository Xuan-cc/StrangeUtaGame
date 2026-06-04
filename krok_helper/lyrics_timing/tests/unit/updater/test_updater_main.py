"""``updater_app/main.py`` 内部工具函数的单元测试。

只覆盖纯逻辑函数，不涉及网络与文件系统的实际更新流程。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _ensure_path():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    yield


def _get_module():
    import importlib
    return importlib.import_module("updater_app.main")


# ───────────────────────── _retry_on_permission_error ─────────────────────────


class TestRetryOnPermissionError:
    """验证关键文件操作的重试逻辑（应对 Windows 文件锁延迟释放）。"""

    def test_succeeds_first_try(self):
        mod = _get_module()
        log = logging.getLogger("test")
        calls = []

        def op():
            calls.append("ok")
            return 42

        result = mod._retry_on_permission_error("test", op, log, max_retries=3, interval=0.01)
        assert result == 42
        assert calls == ["ok"]

    def test_recovers_after_permission_error(self):
        mod = _get_module()
        log = logging.getLogger("test")
        counter = {"n": 0}

        def op():
            counter["n"] += 1
            if counter["n"] < 3:
                raise PermissionError("locked")
            return "done"

        result = mod._retry_on_permission_error("test", op, log, max_retries=5, interval=0.01)
        assert result == "done"
        assert counter["n"] == 3

    def test_recovers_after_winerror_5(self):
        """模拟 Windows 拒绝访问 (WinError 5)。"""
        mod = _get_module()
        log = logging.getLogger("test")
        counter = {"n": 0}

        def op():
            counter["n"] += 1
            if counter["n"] < 2:
                exc = OSError("access denied")
                exc.winerror = 5  # type: ignore[attr-defined]
                raise exc
            return None

        result = mod._retry_on_permission_error("test", op, log, max_retries=4, interval=0.01)
        assert result is None
        assert counter["n"] == 2

    def test_recovers_after_winerror_32(self):
        """模拟 Windows 文件被占用 (WinError 32)。"""
        mod = _get_module()
        log = logging.getLogger("test")
        counter = {"n": 0}

        def op():
            counter["n"] += 1
            if counter["n"] < 2:
                exc = OSError("file in use")
                exc.winerror = 32  # type: ignore[attr-defined]
                raise exc
            return None

        result = mod._retry_on_permission_error("test", op, log, max_retries=4, interval=0.01)
        assert result is None
        assert counter["n"] == 2

    def test_does_not_retry_on_other_oserror(self):
        """非 PermissionError / WinError 5/32 的 OSError 不重试，直接抛出。"""
        mod = _get_module()
        log = logging.getLogger("test")
        counter = {"n": 0}

        def op():
            counter["n"] += 1
            exc = OSError("no such file")
            exc.winerror = 2  # type: ignore[attr-defined]
            raise exc

        with pytest.raises(OSError) as excinfo:
            mod._retry_on_permission_error("test", op, log, max_retries=5, interval=0.01)
        assert excinfo.value.winerror == 2
        assert counter["n"] == 1

    def test_exhausts_retries_and_raises(self):
        mod = _get_module()
        log = logging.getLogger("test")
        counter = {"n": 0}

        def op():
            counter["n"] += 1
            raise PermissionError("always locked")

        with pytest.raises(PermissionError):
            mod._retry_on_permission_error(
                "test", op, log, max_retries=3, interval=0.01,
            )
        assert counter["n"] == 3


class TestModuleConstants:
    def test_constants_sane(self):
        mod = _get_module()
        assert mod.WAIT_PID_TIMEOUT > 0
        assert mod.POST_EXIT_GRACE_SECONDS > 0
        assert mod.FILE_LOCK_RETRY_COUNT >= 3
        assert mod.FILE_LOCK_RETRY_INTERVAL > 0
