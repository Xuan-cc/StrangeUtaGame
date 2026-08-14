"""AI 打轴 worker 进程入口（阶段 C）。

一次性进程：从 stdin 读取一条 align 消息，执行后向 stdout 写
progress/result/cancelled/error 消息并退出。进程退出即释放模型与
CUDA 上下文。宿主通过
``python -m strange_uta_game.backend.application.ai_timing.worker`` 启动。

取消：宿主向 stdin 追加一条 ``{"type": "cancel"}``；后台读线程置位
取消标记，provider 在阶段边界协作停止，worker 输出 cancelled 后退出。
宿主超时未收到退出时可直接 terminate（崩溃隔离边界）。

stderr 留给 provider/依赖的原生日志（不干扰协议通道）。
"""

import sys
import threading

from strange_uta_game.backend.application.ai_timing.alignment import (
    AlignmentValidationError,
)
from strange_uta_game.backend.application.ai_timing.worker.protocol import (
    PROTOCOL_VERSION,
    WorkerProtocolError,
    decode_message,
    deserialize_request,
    encode_message,
    serialize_result,
)
from strange_uta_game.backend.application.ai_timing.worker.providers import (
    AlignmentCancelledError,
    AlignmentProviderError,
    create_provider,
)


def _emit(obj: dict) -> None:
    sys.stdout.write(encode_message(obj) + "\n")
    sys.stdout.flush()


class _CancelState:
    """跨线程取消标记（后台读线程置位，主循环在阶段边界检查）。"""

    def __init__(self) -> None:
        self.event = threading.Event()

    def set(self) -> None:
        self.event.set()

    def __call__(self) -> bool:
        return self.event.is_set()


def _start_cancel_reader(cancel_state: _CancelState) -> threading.Thread:
    """后台读取 stdin 剩余消息；收到 cancel 置位标记，EOF/异常静默退出。"""

    def _reader() -> None:
        try:
            for line in sys.stdin:
                try:
                    message = decode_message(line)
                except WorkerProtocolError:
                    continue
                if message.get("type") == "cancel":
                    cancel_state.set()
                    return
        except Exception:
            return

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    return thread


def main(argv=None) -> int:
    provider = None
    try:
        line = sys.stdin.readline()
        if not line:
            _emit({"type": "error", "message": "worker 未收到任务指令"})
            return 1
        message = decode_message(line)
        if message.get("type") != "align":
            _emit({"type": "error", "message": "worker 期望首条消息为 align"})
            return 1
        if int(message.get("protocol", 0)) != PROTOCOL_VERSION:
            _emit(
                {
                    "type": "error",
                    "message": (
                        f"进程协议版本不匹配（宿主 {message.get('protocol')}，"
                        f"worker {PROTOCOL_VERSION}）"
                    ),
                }
            )
            return 1

        payload = message.get("payload") or {}
        audio_path = str(payload.get("audio_path") or "")
        model_spec = dict(payload.get("model") or {})
        request = deserialize_request(payload.get("request") or {})

        if not audio_path:
            _emit({"type": "error", "message": "未提供音频文件路径"})
            return 1

        cancel_state = _CancelState()
        _start_cancel_reader(cancel_state)

        def progress(percent: int, message_cn: str) -> None:
            _emit(
                {
                    "type": "progress",
                    "stage": "align",
                    "percent": max(0, min(100, int(percent))),
                    "message": message_cn,
                }
            )

        provider = create_provider(model_spec)
        provider.validate_model(model_spec)
        _emit(
            {
                "type": "progress",
                "stage": "load",
                "percent": 5,
                "message": "初始化对齐环境",
            }
        )
        provider.load(model_spec, progress, cancel_state)
        result = provider.align(request, audio_path, progress, cancel_state)
        provider.unload()
        _emit({"type": "result", "payload": serialize_result(result)})
        return 0
    except AlignmentCancelledError:
        if provider is not None:
            provider.unload()
        _emit({"type": "cancelled"})
        return 0
    except (AlignmentProviderError, AlignmentValidationError) as exc:
        if provider is not None:
            provider.unload()
        _emit({"type": "error", "message": str(exc)})
        return 1
    except WorkerProtocolError as exc:
        _emit({"type": "error", "message": str(exc)})
        return 1
    except Exception as exc:  # 崩溃隔离：worker 内部异常不拖垮宿主
        _emit({"type": "error", "message": f"对齐进程内部错误：{exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
