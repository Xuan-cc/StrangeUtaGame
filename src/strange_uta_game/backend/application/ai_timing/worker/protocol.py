"""AI 打轴 worker 协议（阶段 C）。

宿主与 forced-alignment worker 进程之间通过 stdin/stdout 的 JSON Lines
交换版本化消息。进程为一次性：接收一个 align 任务，输出结果/取消/错误
后退出——进程退出即保证释放模型与 CUDA 上下文（§10）。

消息一览：

宿主 → worker::

    {"type": "align", "protocol": 1, "payload": {...}}
    {"type": "cancel"}

worker → 宿主::

    {"type": "progress", "stage": "load"|"align", "percent": 0-100, "message": "..."}
    {"type": "result", "payload": {...}}
    {"type": "cancelled"}
    {"type": "error", "message": "中文错误"}

payload 结构与阶段 B 的 AlignmentRequest / AlignmentResult 一一对应；
location 元组序列化为 JSON 数组，反序列化时还原为元组。
"""

import json
from typing import Any, Dict

from strange_uta_game.backend.application.ai_timing.alignment import (
    SCHEMA_VERSION,
    AlignmentRequest,
    AlignmentResult,
    AlignmentToken,
    EmissionSpan,
    MediaIdentity,
)

PROTOCOL_VERSION = 1
"""进程协议版本（与 SCHEMA_VERSION 独立演进）。"""


class WorkerProtocolError(RuntimeError):
    """协议消息非法（中文消息可直接展示）。"""


# ──────────────────────────────────────────────
# 请求/结果 序列化
# ──────────────────────────────────────────────


def serialize_request(request: AlignmentRequest) -> Dict[str, Any]:
    media = None
    if request.media is not None:
        media = {
            "source_path": request.media.source_path,
            "content_sha256": request.media.content_sha256,
            "duration_ms": request.media.duration_ms,
        }
    return {
        "schema_version": request.schema_version,
        "annotation_digest": request.annotation_digest,
        "media": media,
        "options": dict(request.options),
        "tokens": [
            {
                "index": t.index,
                "text": t.text,
                "raw_reading": t.raw_reading,
                "location": list(t.location),
                "line_idx": t.line_idx,
            }
            for t in request.tokens
        ],
    }


def deserialize_request(payload: Dict[str, Any]) -> AlignmentRequest:
    try:
        media = None
        if payload.get("media"):
            media = MediaIdentity(
                source_path=payload["media"].get("source_path", ""),
                content_sha256=payload["media"].get("content_sha256", ""),
                duration_ms=int(payload["media"].get("duration_ms", 0)),
            )
        tokens = [
            AlignmentToken(
                index=int(t["index"]),
                text=str(t["text"]),
                raw_reading=str(t.get("raw_reading", "")),
                location=tuple(int(x) for x in t["location"]),
                line_idx=int(t.get("line_idx", t["location"][0])),
            )
            for t in payload["tokens"]
        ]
        return AlignmentRequest(
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            annotation_digest=str(payload.get("annotation_digest", "")),
            media=media,
            options=dict(payload.get("options") or {}),
            tokens=tokens,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerProtocolError(f"对齐请求数据无效：{exc}") from exc


def serialize_result(result: AlignmentResult) -> Dict[str, Any]:
    return {
        "schema_version": result.schema_version,
        "annotation_digest": result.annotation_digest,
        "model_id": result.model_id,
        "spans": [
            {
                "token_index": s.token_index,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "score": s.score,
            }
            for s in result.spans
        ],
    }


def deserialize_result(payload: Dict[str, Any]) -> AlignmentResult:
    try:
        return AlignmentResult(
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            annotation_digest=str(payload.get("annotation_digest", "")),
            model_id=str(payload.get("model_id", "")),
            spans=[
                EmissionSpan(
                    token_index=int(s["token_index"]),
                    start_ms=int(s["start_ms"]),
                    end_ms=int(s["end_ms"]),
                    score=float(s.get("score", 1.0)),
                )
                for s in payload["spans"]
            ],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerProtocolError(f"对齐结果数据无效：{exc}") from exc


# ──────────────────────────────────────────────
# 消息编解码
# ──────────────────────────────────────────────


def encode_message(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False)


def decode_message(line: str) -> Dict[str, Any]:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        raise WorkerProtocolError(f"协议消息不是合法 JSON：{exc}") from exc
    if not isinstance(obj, dict) or "type" not in obj:
        raise WorkerProtocolError("协议消息缺少 type 字段")
    return obj


__all__ = [
    "PROTOCOL_VERSION",
    "WorkerProtocolError",
    "serialize_request",
    "deserialize_request",
    "serialize_result",
    "deserialize_result",
    "encode_message",
    "decode_message",
]
