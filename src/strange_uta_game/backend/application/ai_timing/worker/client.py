"""AI 打轴宿主侧 worker 客户端（阶段 C）。

职责：

- 以子进程方式启动一次性 worker（``python -m ...ai_timing.worker``）；
- 发送版本化 align 消息，接收 progress/result/cancelled/error；
- 协作取消：发送 cancel 消息并等待宽限期，超时 terminate（§8.3）；
- 崩溃隔离：worker 进程死亡/超时转换为中文异常，不拖垮宿主；
- 任务结束回收进程与管道。

注意：当前以 ``sys.executable + -m`` 启动，适用于开发与打包后的
standalone Python 环境；PyInstaller onedir 场景的启动器适配在阶段 H
统一处理（见计划文档 §11 阶段 H）。
"""

import os
import subprocess
import sys
import threading
from typing import Callable, Dict, List, Optional

from strange_uta_game.backend.application.ai_timing.alignment import (
    AlignmentRequest,
    AlignmentResult,
)
from strange_uta_game.backend.application.ai_timing.worker.protocol import (
    PROTOCOL_VERSION,
    WorkerProtocolError,
    decode_message,
    deserialize_result,
    encode_message,
    serialize_request,
)

ProgressCallback = Callable[[str, int, str], None]
"""on_progress(stage, percent, message)。"""


class AlignmentWorkerError(RuntimeError):
    """worker 启动/协议/执行失败（中文消息）。"""


class AlignmentWorkerCancelled(RuntimeError):
    """任务已取消。"""

    def __init__(self, message: str = "已取消 AI 打轴"):
        super().__init__(message)


class AlignmentWorkerTimeout(AlignmentWorkerError):
    """worker 超时被终止。"""


class AlignmentWorkerClient:
    """一次性 worker 进程的宿主侧驱动。

    典型用法（宿主线程）::

        client = AlignmentWorkerClient()
        try:
            result = client.run(request, audio_path, model_spec, on_progress)
        finally:
            client.close()
    """

    WORKER_MODULE = "strange_uta_game.backend.application.ai_timing.worker"

    def __init__(
        self,
        python_exe: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        cancel_grace_s: float = 5.0,
    ):
        self._python_exe = python_exe or sys.executable
        self._env_override = env
        self._cancel_grace_s = cancel_grace_s
        self._proc: Optional[subprocess.Popen] = None
        self._stdin_lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._finished = threading.Event()

    # ── 生命周期 ──

    @staticmethod
    def _package_root() -> "Path":
        """SUG 包根目录（含 ``strange_uta_game/`` 的目录，即 src 布局的 src）。"""
        from pathlib import Path

        import strange_uta_game

        return Path(strange_uta_game.__file__).resolve().parent.parent

    def _build_env(self, *, propagate_sys_path: bool) -> Dict[str, str]:
        env = dict(self._env_override or os.environ)
        if propagate_sys_path:
            # 仅当 worker 与宿主用同一解释器时传播完整 import 路径（src
            # 布局/开发模式需要）。外部 Runtime（专用 venv，自带 torch）
            # 绝不继承宿主 site-packages，否则其包版本会被宿主环境遮蔽。
            path_sep = os.pathsep
            existing = env.get("PYTHONPATH", "")
            entries: List[str] = [
                p for p in sys.path if p and p not in existing.split(path_sep)
            ]
            env["PYTHONPATH"] = path_sep.join(entries + ([existing] if existing else []))
        else:
            # 外部解释器：只带上 SUG 包根（worker 模块代码在那里），其余
            # 交给 venv 自身的 site-packages（torch/transformers 等）
            env.pop("PYTHONPATH", None)
            env["PYTHONPATH"] = str(self._package_root())
        # 强制子进程 stdout/stderr 为文本协议通道友好的环境
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # 模型走受控本地目录：杜绝 from_pretrained 的网络探测/遥测卡顿
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        env.setdefault("TOKENIZERS_PARALLELISM", "false")
        return env

    def _is_same_interpreter(self) -> bool:
        if not self._python_exe or self._python_exe == sys.executable:
            return True
        try:
            from pathlib import Path

            return Path(self._python_exe).resolve() == Path(sys.executable).resolve()
        except OSError:
            return False

    def _ensure_started(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._cancel_requested.clear()
        self._finished.clear()
        try:
            # stderr 落临时文件而非 DEVNULL：worker 崩溃时把 traceback 尾部
            # 并入错误消息，彻底告别「返回码 1」式静默失败
            import tempfile as _tf

            self._stderr_file = open(
                _tf.NamedTemporaryFile(
                    prefix="krok-aitiming-stderr-", delete=False
                ).name,
                "w",
                encoding="utf-8",
                errors="replace",
            )
            if self._is_same_interpreter():
                cmd = [self._python_exe, "-m", self.WORKER_MODULE]
                env = self._build_env(propagate_sys_path=True)
            else:
                # 外部解释器用 runpy 引导而不是 PYTHONPATH：嵌入式 Python
                # 发行版（托管 PyMSS runtime 就是）带 python312._pth，
                # 该文件存在时解释器完全忽略 PYTHONPATH，-m 会直接
                # ModuleNotFoundError。把包根作为 argv 注入 sys.path 后
                # runpy 等价于 -m。
                root = str(self._package_root())
                bootstrap = (
                    "import sys, runpy; sys.path.insert(0, sys.argv.pop(1));"
                    f" runpy.run_module({self.WORKER_MODULE!r},"
                    " run_name='__main__')"
                )
                cmd = [self._python_exe, "-c", bootstrap, root]
                env = self._build_env(propagate_sys_path=False)
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_file,
                env=env,
                text=True,
                encoding="utf-8",
                cwd=None,
            )
        except OSError as exc:
            raise AlignmentWorkerError(f"无法启动对齐进程：{exc}") from exc
        return self._proc

    def _send(self, obj: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise AlignmentWorkerError("对齐进程未启动")
        try:
            with self._stdin_lock:
                proc.stdin.write(encode_message(obj) + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, OSError):
            # worker 已退出：由读取循环统一转换为错误
            pass

    # ── 对外接口 ──

    def run(
        self,
        request: AlignmentRequest,
        audio_path: str,
        model_spec: Dict[str, object],
        on_progress: Optional[ProgressCallback] = None,
        timeout_s: Optional[float] = None,
    ) -> AlignmentResult:
        """阻塞执行一次对齐；取消/超时/崩溃分别抛对应异常。"""
        proc = self._ensure_started()

        watchdog: Optional[threading.Timer] = None
        timed_out = threading.Event()
        if timeout_s is not None:

            def _on_timeout() -> None:
                if not self._finished.is_set():
                    timed_out.set()
                    try:
                        proc.kill()
                    except OSError:
                        pass

            watchdog = threading.Timer(timeout_s, _on_timeout)
            watchdog.daemon = True
            watchdog.start()

        self._send(
            {
                "type": "align",
                "protocol": PROTOCOL_VERSION,
                "payload": {
                    "audio_path": str(audio_path),
                    "model": dict(model_spec),
                    "request": serialize_request(request),
                },
            }
        )

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    message = decode_message(line)
                except WorkerProtocolError:
                    continue  # 跳过非协议输出，保持通道健壮
                mtype = message.get("type")
                if mtype == "progress" and on_progress is not None:
                    on_progress(
                        str(message.get("stage", "")),
                        int(message.get("percent", 0)),
                        str(message.get("message", "")),
                    )
                elif mtype == "result":
                    self._finished.set()
                    return deserialize_result(message.get("payload") or {})
                elif mtype == "cancelled":
                    self._finished.set()
                    raise AlignmentWorkerCancelled()
                elif mtype == "error":
                    self._finished.set()
                    raise AlignmentWorkerError(str(message.get("message", "对齐失败")))
            # stdout 关闭（进程退出）而无结果
            self._finished.set()
            returncode = proc.wait()
            if self._cancel_requested.is_set():
                raise AlignmentWorkerCancelled()
            if timed_out.is_set():
                raise AlignmentWorkerTimeout("对齐超时，进程已终止")
            detail = ""
            try:
                self._stderr_file.flush()
                with open(
                    self._stderr_file.name, encoding="utf-8", errors="replace"
                ) as fh:
                    tail = fh.read().strip().splitlines()[-6:]
                if tail:
                    detail = "；详情：" + " | ".join(tail)
            except Exception:
                pass
            raise AlignmentWorkerError(
                f"对齐进程异常退出（返回码 {returncode}），未返回结果{detail}"
            )
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if not self._finished.is_set():
                self._finished.set()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass

    def cancel(self) -> None:
        """请求协作取消；宽限期后强制终止进程。

        可从其他线程调用；run() 会以 AlignmentWorkerCancelled 返回。
        """
        self._cancel_requested.set()
        proc = self._proc
        if proc is None:
            return
        self._send({"type": "cancel"})
        # 等待协作退出
        try:
            proc.wait(timeout=self._cancel_grace_s)
            return
        except subprocess.TimeoutExpired:
            pass
        # 宽限期超时：强制终止（§8.3 必要时终止本任务拥有的 worker）
        try:
            proc.kill()
            proc.wait(timeout=2)
        except OSError:
            pass

    def close(self) -> None:
        """回收进程与管道（幂等）。"""
        proc = self._proc
        self._proc = None
        self._kill_tree(proc)
        stderr_file = getattr(self, "_stderr_file", None)
        if stderr_file is not None:
            self._stderr_file = None
            try:
                stderr_file.close()
            except Exception:
                pass
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if proc.poll() is None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass

    @staticmethod
    def _kill_tree(proc) -> None:
        """回收进程树（torch 导入在某些环境会派生辅助子进程，不能只等父进程）。"""
        if proc is None or proc.poll() is not None:
            return
        import sys as _sys

        try:
            if _sys.platform == "win32":
                import subprocess as _sp

                _sp.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                proc.kill()
        except Exception:
            pass

    def __enter__(self) -> "AlignmentWorkerClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


__all__ = [
    "AlignmentWorkerClient",
    "AlignmentWorkerError",
    "AlignmentWorkerCancelled",
    "AlignmentWorkerTimeout",
]
