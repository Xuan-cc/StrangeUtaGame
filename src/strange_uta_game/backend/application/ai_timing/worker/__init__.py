"""AI 打轴 worker 子包（阶段 C）。

一次性 worker 进程：版本化 stdio 协议（protocol）、provider 实现
（providers：wav2vec2 微调 / MMS_FA / fake）、进程入口（__main__）
与宿主侧客户端（client）。

真实模型（wav2vec2 / MMS_FA）烟测需要安装 PyTorch Runtime；CI 使用
fake provider 验证协议、生命周期与取消语义（§12.3）。
"""

from .protocol import (
    PROTOCOL_VERSION,
    WorkerProtocolError,
    decode_message,
    deserialize_request,
    deserialize_result,
    encode_message,
    serialize_request,
    serialize_result,
)
from .providers import (
    AlignmentCancelledError,
    AlignmentProviderError,
    FakeProvider,
    ForcedAlignmentProvider,
    MmsFaProvider,
    Wav2Vec2LatnProvider,
    create_provider,
    normalize_latn_text,
)
from .client import (
    AlignmentWorkerCancelled,
    AlignmentWorkerClient,
    AlignmentWorkerError,
    AlignmentWorkerTimeout,
)

__all__ = [
    "PROTOCOL_VERSION",
    "WorkerProtocolError",
    "encode_message",
    "decode_message",
    "serialize_request",
    "deserialize_request",
    "serialize_result",
    "deserialize_result",
    "ForcedAlignmentProvider",
    "AlignmentProviderError",
    "AlignmentCancelledError",
    "FakeProvider",
    "Wav2Vec2LatnProvider",
    "MmsFaProvider",
    "create_provider",
    "normalize_latn_text",
    "AlignmentWorkerClient",
    "AlignmentWorkerError",
    "AlignmentWorkerCancelled",
    "AlignmentWorkerTimeout",
]
