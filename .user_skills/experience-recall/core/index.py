"""索引目录：从 JSONL 构建可检索的 event 索引（零依赖，仅标准库）。

对应设计方案 §8.2 索引层：记 event_id / 时间 / 标签 / 重要性 / 指向原始文件的定位；
检索先查索引，再定位读原文——解决"存多了直接扫描变慢"。

索引文件为纯文本 JSON，可进 Git，随记忆数据一起"拔下就走"。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .store import MemoryStore
from .schema import RECORD_TYPE_EVENT

INDEX_FILENAME = "index.json"


class MemoryIndex:
    """事件索引目录：event_id 聚合 + 定位 + 模糊检索。"""

    def __init__(self, store: MemoryStore, index_path: Optional[str] = None):
        self.store = store
        self.index_path = index_path or os.path.join(store.base_dir, INDEX_FILENAME)
        self._events: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 构建 / 保存 / 加载
    # ------------------------------------------------------------------
    def build(self) -> "MemoryIndex":
        """全量扫描分片，构建 event 索引（含每条记录的 shard+行号定位）。"""
        self._events = {}
        for shard in self.store.event_shard_paths():
            self._index_shard(shard)
        return self

    def _index_shard(self, shard: str) -> None:
        with open(shard, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("record_type", RECORD_TYPE_EVENT) != RECORD_TYPE_EVENT:
                    continue
                eid = rec.get("event_id")
                if not eid:
                    continue
                entry = self._events.setdefault(eid, {
                    "event_id": eid,
                    "event_name": rec.get("event_name") or PENDING_NAME,
                    "task_type": rec.get("task_type"),
                    "importance": rec.get("importance"),
                    "tags": list(rec.get("tags") or []),
                    "model": set(),
                    "session_id": set(),
                    "first_ts": rec.get("timestamp"),
                    "last_ts": rec.get("timestamp"),
                    "records": [],
                    "count": 0,
                })
                entry["count"] += 1
                entry["records"].append({"shard": os.path.basename(shard), "line": line_no})
                if rec.get("timestamp"):
                    ts = rec["timestamp"]
                    if entry["first_ts"] is None or ts < entry["first_ts"]:
                        entry["first_ts"] = ts
                    if entry["last_ts"] is None or ts > entry["last_ts"]:
                        entry["last_ts"] = ts
                if rec.get("model"):
                    entry["model"].add(str(rec["model"]))
                if rec.get("session_id"):
                    entry["session_id"].add(str(rec["session_id"]))
        # set -> list 便于 JSON 序列化
        for eid, entry in self._events.items():
            entry["model"] = sorted(entry["model"])
            entry["session_id"] = sorted(entry["session_id"])
        return

    def save(self) -> None:
        payload = {"version": 1, "events": self._events}
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def load(self) -> bool:
        if not os.path.exists(self.index_path):
            return False
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self._events = payload.get("events", {})
            return True
        except (json.JSONDecodeError, OSError):
            return False

    # ------------------------------------------------------------------
    # 精确 / 模糊检索
    # ------------------------------------------------------------------
    def find_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        return self._events.get(event_id)

    def search(self, query: str, limit: int = 10,
               time_range: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """模糊匹配：按关键词在 event_name / tags / 事件内容里打分，返回候选。

        供"弹窗兜底"（§4.3）选候选事件。命中唯一时上层自动注入，不弹窗。
        """
        q = (query or "").strip()
        qs = [t for t in q.lower().replace("，", ",").replace(" ", ",").split(",") if t]
        scored = []
        for eid, entry in self._events.items():
            if time_range:
                if not self._in_range(entry.get("last_ts") or "", time_range):
                    continue
            text = " ".join([
                str(entry.get("event_name") or ""),
                str(entry.get("task_type") or ""),
                " ".join(entry.get("tags") or []),
            ]).lower()
            score = 0
            for token in qs:
                if token and token in text:
                    score += 1
            if q and q in text:
                score += 2
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: (-x[0], -(x[1].get("count") or 0)))
        return [e for _, e in scored[:limit]]

    def events_in_range(self, time_range: Dict[str, str],
                        limit: int = 50) -> List[Dict[str, Any]]:
        out = [e for e in self._events.values()
               if self._in_range(e.get("last_ts") or "", time_range)]
        out.sort(key=lambda e: (e.get("last_ts") or ""), reverse=True)
        return out[:limit]

    def all_events(self) -> List[Dict[str, Any]]:
        return list(self._events.values())

    @staticmethod
    def _in_range(ts: str, time_range: Dict[str, str]) -> bool:
        start = time_range.get("start")  # YYYY-MM-DD
        end = time_range.get("end")
        day = ts[:10]
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True


PENDING_NAME = "(未命名事件)"
