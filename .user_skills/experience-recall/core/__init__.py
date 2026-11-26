"""记忆索引 · 核心层（零依赖，仅标准库）。

解耦设计（§2.2）：本核心层不依赖任何平台 API（dsh / cordis / 豆包），
可独立运行 / CLI 测试；记忆数据（JSONL + 索引目录）是纯文本，插上能用、拔下就走。
"""
from .schema import *  # noqa: F401,F403
from .store import MemoryStore
from .index import MemoryIndex
from .ingest import Ingest
from .recall import Recall

__all__ = [
    "MemoryStore", "MemoryIndex", "Ingest", "Recall",
    "RECORD_TYPE_EVENT", "RECORD_TYPE_PREFERENCE",
    "SIX_ELEMENTS", "PENDING", "IMPORTANCE_LEVELS", "TRIGGER_POINTS",
    "TOKEN_BUDGETS", "MODE_REFERENCE", "MODE_READONLY",
]
__version__ = "1.0.0"
