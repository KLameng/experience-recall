"""Store 持久层：JSONL 单库 + 滚动分片（零依赖，仅标准库）。

对应设计方案 §2（唯一真相源）/ §3.3（事件型+偏好型单库）/
§7 步骤 1（JSONL 存储）/ §8.2（全量保留 + 滚动分片）。

设计要点
--------
- 事件型记录按"年-月"滚动分片落盘：memory_YYYYMM.jsonl，仅组织、不丢数据。
- 偏好型记录集中存 preferences.jsonl，按 pref_key 覆盖更新（最新为准）。
- 全量保留：不压缩、不降级、不自动删。
"""
from __future__ import annotations

import datetime
import glob
import json
import os
from typing import Any, Dict, Iterator, List, Optional

from .schema import RECORD_TYPE_EVENT, RECORD_TYPE_PREFERENCE, iso_now

_EVENT_PREFIX = "memory"
_PREF_FILENAME = "preferences.jsonl"


class MemoryStore:
    """JSONL 持久层：读写事件分片 + 偏好文件 + 统计。"""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)
        self.pref_path = os.path.join(base_dir, _PREF_FILENAME)

    # ------------------------------------------------------------------
    # 路径 / 分片
    # ------------------------------------------------------------------
    def _event_shard_path(self, timestamp: Optional[str] = None) -> str:
        """按 timestamp（或当前时间）定位到对应月份分片。"""
        ts = timestamp or iso_now()
        try:
            dt = datetime.datetime.strptime(ts[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            dt = datetime.datetime.now()
        return os.path.join(self.base_dir, f"{_EVENT_PREFIX}_{dt:%Y%m}.jsonl")

    def event_shard_paths(self) -> List[str]:
        return sorted(glob.glob(os.path.join(self.base_dir, f"{_EVENT_PREFIX}_*.jsonl")))

    # ------------------------------------------------------------------
    # 事件型：追加
    # ------------------------------------------------------------------
    def append_event(self, record: Dict[str, Any]) -> str:
        """追加一条事件型记录到对应月份分片，返回落盘路径。"""
        path = self._event_shard_path(record.get("timestamp"))
        self._append_jsonl(path, record)
        return path

    # ------------------------------------------------------------------
    # 偏好型：覆盖更新
    # ------------------------------------------------------------------
    def upsert_preference(self, record: Dict[str, Any]) -> str:
        """按 pref_key 覆盖更新偏好记录（最新为准），返回落盘路径。"""
        key = record.get("pref_key")
        if not key:
            raise ValueError("preference 记录缺少 pref_key")
        prefs = self.read_preferences()
        prefs[key] = record
        self._write_jsonl(self.pref_path, list(prefs.values()))
        return self.pref_path

    def read_preferences(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self.pref_path):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for rec in self._iter_jsonl(self.pref_path):
            key = rec.get("pref_key")
            if key:
                out[key] = rec
        return out

    # ------------------------------------------------------------------
    # 事件型：读取
    # ------------------------------------------------------------------
    def iter_events(self, shards: Optional[List[str]] = None) -> Iterator[Dict[str, Any]]:
        """遍历事件分片（可指定 shards 列表限定范围）。"""
        paths = shards or self.event_shard_paths()
        for p in paths:
            for rec in self._iter_jsonl(p):
                if rec.get("record_type", RECORD_TYPE_EVENT) == RECORD_TYPE_EVENT:
                    yield rec

    def read_event_records(self, shards: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return list(self.iter_events(shards))

    def shards_in_range(self, start: Optional[str] = None,
                        end: Optional[str] = None) -> List[str]:
        """按时间范围筛选分片（start/end 形如 'YYYY-MM'）。仅需扫相关片。"""
        paths = self.event_shard_paths()
        if not start and not end:
            return paths
        start_ym = (start or "0000-00")[:7]
        end_ym = (end or "9999-99")[:7]

        def ym_of(p: str) -> str:
            name = os.path.basename(p)
            try:
                return name[len(_EVENT_PREFIX) + 1:len(_EVENT_PREFIX) + 8]
            except Exception:
                return "0000-00"

        return [p for p in paths if start_ym <= ym_of(p) <= end_ym]

    # ------------------------------------------------------------------
    # 统计（供容量告警 / 统计）
    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        shards = self.event_shard_paths()
        n_events = 0
        n_bytes = 0
        for p in shards:
            n_bytes += os.path.getsize(p) if os.path.exists(p) else 0
            n_events += sum(1 for _ in self._iter_jsonl(p))
        pref_bytes = os.path.getsize(self.pref_path) if os.path.exists(self.pref_path) else 0
        return {
            "event_records": n_events,
            "event_shards": len(shards),
            "event_bytes": n_bytes,
            "preference_bytes": pref_bytes,
            "total_bytes": n_bytes + pref_bytes,
            "base_dir": self.base_dir,
        }

    # ------------------------------------------------------------------
    # JSONL 底层
    # ------------------------------------------------------------------
    @staticmethod
    def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_jsonl(path: str, records: List[Dict[str, Any]]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _iter_jsonl(path: str) -> Iterator[Dict[str, Any]]:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # 坏行跳过（不破坏整体可读性），可进 Git 审计
                    continue
