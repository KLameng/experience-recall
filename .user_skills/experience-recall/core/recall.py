"""Recall 注入层：事件级精确检索 + token 预算注入 + 审计标记（零依赖，仅标准库）。

对应设计方案 §4（按需双入口 / 事件级精确注入 / 弹窗兜底）/
§5（参考 / 只读 + 审计标记）。

设计要点
--------
- 事件级精确注入：只注入用户提到的那个 event_id 的聚合历史，无关事件一概不注入。
- token 预算：按 2K / 4K / 8K / 不设限 截断，不占满窗口、不算溢出。
- 审计标记：每次引用记忆后输出使用模式（参考 / 只读），用户可见、可统计。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .store import MemoryStore
from .index import MemoryIndex
from .schema import (
    MODE_REFERENCE, MODE_CN,
    TOKEN_BUDGET_4K, PENDING,
)

# 粗略 token 估算：中文约 1 字≈1 token，英文/数字约 4 字符≈1 token。
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cn = sum(1 for ch in text if ord(ch) > 127)
    other = len(text) - cn
    return int(cn * 1.0 + other / 4.0)


def _truncate(text: str, budget: Optional[int]) -> str:
    if budget is None or estimate_tokens(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "\n…[已按 token 预算截断]"


class Recall:
    """注入层：检索 + 组装注入文本 + 审计标记。"""

    def __init__(self, store: MemoryStore, index: Optional[MemoryIndex] = None):
        self.store = store
        self.index = index or MemoryIndex(store)
        self._ensure_index()

    def _ensure_index(self) -> None:
        """保证检索前索引就绪：优先 load；无索引（或为空）则自动 build。

        检索永远基于最新数据，不依赖手动执行 index 命令。
        """
        if self.index.all_events():
            return
        if self.index.load() and self.index.all_events():
            return
        self.index.build()
        self.index.save()

    # ------------------------------------------------------------------
    # 事件级精确注入（§4.2）
    # ------------------------------------------------------------------
    def recall_event(self, event_id: str,
                     token_budget: Optional[int] = TOKEN_BUDGET_4K,
                     mode: str = MODE_REFERENCE,
                     include_payload_keys: Optional[List[str]] = None
                     ) -> Dict[str, Any]:
        """按 event_id 精确聚合该事件全部记录，按 token 预算注入。

        返回 {event, records, payload, record_count, token_budget,
              injected_tokens, mode, audit}
        """
        records = [r for r in self.store.read_event_records()
                   if r.get("event_id") == event_id]
        self._ensure_index()
        event = self.index.find_event(event_id) or {}
        payload = self._compose(records, include_payload_keys=include_payload_keys)
        injected = _truncate(payload, token_budget)
        return {
            "event": event,
            "records": records,
            "payload": injected,
            "record_count": len(records),
            "token_budget": token_budget,
            "injected_tokens": estimate_tokens(injected),
            "mode": mode,
            "audit": self.audit_marker(event_id, event.get("event_name"),
                                       len(records), mode),
        }

    # ------------------------------------------------------------------
    # 语义 / 关键词检索（供入口 A + 弹窗兜底 §4.1 / §4.3）
    # ------------------------------------------------------------------
    def search_candidates(self, query: str,
                          time_range: Optional[Dict[str, str]] = None,
                          limit: int = 10) -> List[Dict[str, Any]]:
        """模糊匹配返回候选事件（供弹窗选择）。命中唯一则由上层直接注入，不弹窗。"""
        self._ensure_index()
        return self.index.search(query, limit=limit, time_range=time_range)

    def recall_by_query(self, query: str,
                        token_budget: Optional[int] = TOKEN_BUDGET_4K,
                        mode: str = MODE_REFERENCE,
                        time_range: Optional[Dict[str, str]] = None,
                        ) -> Dict[str, Any]:
        """按检索词找回：命中唯一 → 直接事件级注入；命中多个 → 返回候选（弹窗兜底）。"""
        candidates = self.search_candidates(query, time_range=time_range, limit=10)
        if not candidates:
            return {"hit": "none", "message": "未找到匹配的记忆事件",
                    "audit": self.audit_marker(None, None, 0, mode)}
        if len(candidates) == 1:
            r = self.recall_event(candidates[0]["event_id"],
                                  token_budget=token_budget, mode=mode)
            r["hit"] = "exact"
            return r
        # 多个候选 → 弹窗兜底，交给上层让用户选
        return {"hit": "ambiguous", "candidates": candidates,
                "message": f"匹配到 {len(candidates)} 个事件，请选择",
                "audit": self.audit_marker(None, None, len(candidates), mode)}

    # ------------------------------------------------------------------
    # 审计标记（§5 记忆使用边界，可统计字段）
    # ------------------------------------------------------------------
    @staticmethod
    def audit_marker(event_id: Optional[str], event_name: Optional[str],
                     n_records: int, mode: str) -> str:
        mode_txt = MODE_CN.get(mode, mode)
        return (
            f"[记忆引用] 事件：{event_name or event_id or '(无)'} · 引用历史 {n_records} 条\n"
            f"  处理模式：{mode_txt}"
        )

    # ------------------------------------------------------------------
    # 内部：把事件记录组装成注入文本
    # ------------------------------------------------------------------
    @staticmethod
    def _compose(records: List[Dict[str, Any]],
                 include_payload_keys: Optional[List[str]] = None) -> str:
        if not records:
            return ""
        order = {"任务启动": 0, "过程节点": 1, "决策点": 2, "结论点": 3,
                 "异常点": 4, "任务结束": 5}
        records = sorted(records, key=lambda r: (order.get(r.get("trigger"), 9),
                                                 r.get("timestamp") or ""))
        lines = []
        seen = set()
        for r in records:
            trigger = r.get("trigger") or ""
            tag = f"[{trigger}]" if trigger else ""
            body = _record_body(r, include_payload_keys)
            key = (tag, body)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{tag} {body}".rstrip())
        return "\n".join(lines)


def _record_body(rec: Dict[str, Any], include: Optional[List[str]]) -> str:
    parts = []
    for k in ("cause", "process", "intermediate", "decision", "reason",
              "rejected_options", "outcome", "note", "problem", "handling",
              "before", "after", "summary", "input_ref", "tool_trace"):
        v = rec.get(k)
        if v is None or str(v).strip() == "" or str(v).strip() == PENDING:
            continue
        if include is not None and k not in include:
            continue
        parts.append(str(v))
    meta = []
    for k in ("model", "task_type", "tags"):
        v = rec.get(k)
        if v:
            meta.append(f"{k}={v}")
    if meta:
        parts.append("(" + " ".join(meta) + ")")
    return " · ".join(parts) if parts else ""
