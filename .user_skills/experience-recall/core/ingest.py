"""Ingest 写入层：六节点写入 + 六要素校验 + 重要性判定（零依赖，仅标准库）。

对应设计方案 §2（Ingest 层）/ §3.4（六要素）/ §3.5（重要性三档）/
§6.1（六节点触发点）。

每个任务 = 一个 event_id（§6 任务粒度）；六节点各自落一条记录；
end_event 时聚合校验六要素完整性，缺则整体标"待补充"。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .store import MemoryStore
from .schema import (
    RECORD_TYPE_EVENT, RECORD_TYPE_PREFERENCE,
    SIX_ELEMENTS, SIX_ELEMENTS_CN, PENDING,
    IMPORTANCE_DETAIL, IMPORTANCE_STANDARD, IMPORTANCE_BRIEF,
    DEFAULT_IMPORTANCE, DETAIL_LEVEL,
    TRIGGER_START, TRIGGER_STEP, TRIGGER_DECISION, TRIGGER_CONCLUSION,
    TRIGGER_ANOMALY, TRIGGER_END,
    new_event_id, new_record_id, iso_now, normalize_tags, fill_missing_elements,
)

# 重要性自动判定的线索（§3.5 满足其一即重要）
_IMPORTANT_TASK_TYPES = {"评级", "acmg", "评级技能", "研发", "开发", "咨询", "遗传咨询",
                         "carrier", "wgs", "wes", "pgt", "cnv"}
_DEEP_ROUNDS = 3      # 与 AI 反复讨论（多轮）阈值
_LONG_DURATION_HINT = {"长", "跨天", "跨周", "多阶段", "多天", "长进程"}

# v6.56（C-2）：事件三级指针。由 CLI 层填（--parent / --related / --resolved-from），
# 这里只负责把它们挂到每一条记录上——写入口一个，指针不会"只有一半事件挂得上"。
POINTERS: Dict[str, Any] = {}


class Ingest:
    """写入层：组装记录、落库。"""

    def __init__(self, store: MemoryStore):
        self.store = store

    # ------------------------------------------------------------------
    # 重要性判定（§3.5）：自动 + 拿不准建议弹窗
    # ------------------------------------------------------------------
    def judge_importance(self, task_type: Optional[str] = None, rounds: int = 0,
                         duration_hint: Optional[str] = None,
                         explicit: Optional[str] = None,
                         ) -> Dict[str, Any]:
        """按规则自动判档；置信度低时置 need_confirm=True（上层据此弹窗）。

        返回 {"importance": 档位, "confidence": 0~1, "need_confirm": bool, "reason": str}
        """
        if explicit in (IMPORTANCE_DETAIL, IMPORTANCE_STANDARD, IMPORTANCE_BRIEF):
            return {"importance": explicit, "confidence": 1.0,
                    "need_confirm": False, "reason": "用户显式标注"}
        reasons = []
        if task_type and any(k in str(task_type).lower() for k in _IMPORTANT_TASK_TYPES):
            reasons.append("任务类型：核心任务")
        if rounds >= _DEEP_ROUNDS:
            reasons.append("交互深度：多轮反复讨论")
        if duration_hint and any(k in str(duration_hint) for k in _LONG_DURATION_HINT):
            reasons.append("进程长度：长进程")
        if reasons:
            return {"importance": IMPORTANCE_DETAIL, "confidence": 0.85,
                    "need_confirm": False, "reason": "；".join(reasons)}
        # 无明确重要线索 → 默认标准级，但置信度不足，建议弹窗兜底
        return {"importance": DEFAULT_IMPORTANCE, "confidence": 0.55,
                "need_confirm": True, "reason": "无可疑重要线索，拿不准，建议确认档位"}

    # ------------------------------------------------------------------
    # 事件型：六节点写入
    # ------------------------------------------------------------------
    def start_event(self, event_name: str, task_type: str,
                    cause: Optional[str] = None,
                    person: Optional[str] = None,
                    model: Optional[str] = None,
                    session_id: Optional[str] = None,
                    tags: Optional[Any] = None,
                    importance: Optional[str] = None,
                    input_ref: Optional[str] = None,
                    ) -> Dict[str, Any]:
        """任务启动：开新 event_id，记录起因 / 目标 / 输入。"""
        event_id = new_event_id()
        record = self._base_record(
            event_id=event_id, event_name=event_name, task_type=task_type,
            trigger=TRIGGER_START, model=model, session_id=session_id, tags=tags,
            importance=importance,
            payload={
                "cause": cause or PENDING,
                "input_ref": input_ref or PENDING,
            },
            person=person,
        )
        self.store.append_event(record)
        return record

    def record_step(self, event_id: str, event_name: str = "", action: str = "",
                    intermediate: Optional[str] = None,
                    input_ref: Optional[str] = None,
                    tool_trace: Optional[str] = None,
                    model: Optional[str] = None, session_id: Optional[str] = None,
                    tags: Optional[Any] = None,
                    importance: Optional[str] = None) -> Dict[str, Any]:
        """过程节点：每个关键步骤完成，记录动作 + 输入 + 中间结果。"""
        record = self._base_record(
            event_id=event_id, event_name=event_name, task_type=None,
            trigger=TRIGGER_STEP, model=model, session_id=session_id, tags=tags,
            importance=importance,
            payload={"process": action, "intermediate": intermediate or PENDING,
                     "input_ref": input_ref or PENDING,
                     "tool_trace": tool_trace or PENDING},
        )
        self.store.append_event(record)
        return record

    def record_decision(self, event_id: str, decision: str,
                        reason: Optional[str] = None,
                        rejected_options: Optional[str] = None,
                        event_name: str = "",
                        model: Optional[str] = None, session_id: Optional[str] = None,
                        tags: Optional[Any] = None,
                        importance: Optional[str] = None) -> Dict[str, Any]:
        """决策点：做了决策 / 方案确认，记决策内容、理由、否决项。

        v1.1 借鉴旧版决策日志（决策/原因/否决）：补 rejected_options，
        复盘时能看见"当时否掉什么、为什么"。
        """
        record = self._base_record(
            event_id=event_id, event_name=event_name, task_type=None,
            trigger=TRIGGER_DECISION, model=model, session_id=session_id, tags=tags,
            importance=importance,
            payload={"decision": decision, "reason": reason or PENDING,
                     "rejected_options": rejected_options or PENDING},
        )
        self.store.append_event(record)
        return record

    def record_conclusion(self, event_id: str, outcome: Optional[str] = None,
                          decision: Optional[str] = None, note: Optional[str] = None,
                          event_name: str = "", model: Optional[str] = None,
                          session_id: Optional[str] = None, tags: Optional[Any] = None,
                          importance: Optional[str] = None) -> Dict[str, Any]:
        """结论点：产出结论（outcome / decision / note）。"""
        record = self._base_record(
            event_id=event_id, event_name=event_name, task_type=None,
            trigger=TRIGGER_CONCLUSION, model=model, session_id=session_id, tags=tags,
            importance=importance,
            payload={"outcome": outcome or PENDING, "decision": decision or PENDING,
                     "note": note or PENDING},
        )
        self.store.append_event(record)
        return record

    def record_anomaly(self, event_id: str, problem: str,
                       handling: Optional[str] = None, before: Optional[str] = None,
                       after: Optional[str] = None, event_name: str = "",
                       lesson: Optional[str] = None, lesson_key: Optional[str] = None,
                       model: Optional[str] = None, session_id: Optional[str] = None,
                       tags: Optional[Any] = None,
                       importance: Optional[str] = None) -> Dict[str, Any]:
        """异常点：报错 / 修复 / 参数调整，记问题、处理、调整前后。

        v1.1 借鉴旧版"历史踩坑与修正"：可带 lesson，异常落库后同步提炼一条
        常驻"教训"偏好（preference 型，防重蹈覆辙）。
        """
        record = self._base_record(
            event_id=event_id, event_name=event_name, task_type=None,
            trigger=TRIGGER_ANOMALY, model=model, session_id=session_id, tags=tags,
            importance=importance,
            payload={"problem": problem, "handling": handling or PENDING,
                     "before": before or PENDING, "after": after or PENDING},
        )
        self.store.append_event(record)
        result: Dict[str, Any] = {"record": record}
        if lesson:
            key = lesson_key or f"教训:{event_name or event_id}"
            result["lesson"] = self.set_preference(
                pref_key=key, value=lesson, source="anomaly", model=model)
        return result

    def end_event(self, event_id: str, summary: Optional[str] = None,
                  audit: Optional[str] = None, event_name: str = "",
                  model: Optional[str] = None, session_id: Optional[str] = None,
                  tags: Optional[Any] = None) -> Dict[str, Any]:
        """任务结束：记录总结 + 审计标记；并返回该事件六要素聚合校验结果。"""
        record = self._base_record(
            event_id=event_id, event_name=event_name, task_type=None,
            trigger=TRIGGER_END, model=model, session_id=session_id, tags=tags,
            importance=None,
            payload={"summary": summary or PENDING, "audit": audit or PENDING},
        )
        self.store.append_event(record)
        six_status = self.verify_six_elements(event_id)
        return {"record": record, "six_elements": six_status}

    # ------------------------------------------------------------------
    # 偏好型：覆盖更新（§3.3）
    # ------------------------------------------------------------------
    def set_preference(self, pref_key: str, value: Any,
                       source: Optional[str] = None, model: Optional[str] = None,
                       confirmed_at: Optional[str] = None,
                       scope: Optional[str] = None,
                       is_redline: Optional[bool] = None) -> Dict[str, Any]:
        """偏好型记录：按 pref_key 覆盖更新，最新为准。

        v1.1 借鉴旧版"长期硬规则库"：补 confirmed_at / scope / is_redline 元数据，
        让长期规则可追溯（确认时间）、可定位（适用场景）、可分级（红线 vs 一般）。
        """
        record = {
            "record_type": RECORD_TYPE_PREFERENCE,
            "pref_key": pref_key,
            "value": value,
            "updated": iso_now(),
            "source": source or "manual",
            "model": model,
        }
        if confirmed_at:
            record["confirmed_at"] = confirmed_at
        if scope:
            record["scope"] = scope
        if is_redline is not None:
            record["is_redline"] = bool(is_redline)
        self.store.upsert_preference(record)
        return record

    # ------------------------------------------------------------------
    # 六要素完整性（§3.4 铁律 #6）
    # ------------------------------------------------------------------
    def verify_six_elements(self, event_id: str) -> Dict[str, str]:
        """聚合一个 event 的全部记录，校验六要素是否都"已填写"。"""
        status: Dict[str, str] = {}
        for el in SIX_ELEMENTS:
            status[el] = PENDING
        for rec in self.store.read_event_records():
            if rec.get("event_id") != event_id:
                continue
            narrative = rec.get("narrative") or {}
            for el in SIX_ELEMENTS:
                val = narrative.get(el)
                if val and str(val).strip() and str(val).strip() != PENDING:
                    status[el] = "已填写"
        return status

    def verify_all_events(self) -> Dict[str, Dict[str, str]]:
        """所有事件的整体六要素校验（供统计）。"""
        eids = {r["event_id"] for r in self.store.read_event_records()}
        return {eid: self.verify_six_elements(eid) for eid in eids}

    # ------------------------------------------------------------------
    # 内部：组装一条记录
    # ------------------------------------------------------------------
    def _base_record(self, event_id: str, event_name: str, task_type: Optional[str],
                     trigger: str, model: Optional[str], session_id: Optional[str],
                     tags: Optional[Any], importance: Optional[str],
                     payload: Dict[str, Any], person: Optional[str] = None,
                     ) -> Dict[str, Any]:
        level = importance or DEFAULT_IMPORTANCE
        narrative = self._narrative(trigger=trigger, payload=payload,
                                    person=person, model=model,
                                    session_id=session_id)
        record = {
            "record_type": RECORD_TYPE_EVENT,
            "record_id": new_record_id(),
            "event_id": event_id,
            "event_name": event_name or PENDING,
            "trigger": trigger,
            "timestamp": iso_now(),
            "model": model,
            "session_id": session_id,
            "task_type": task_type,
            "tags": normalize_tags(tags),
            "importance": level,
            "detail_level": DETAIL_LEVEL.get(level, "summarized"),
            "narrative": narrative,
        }
        record.update(payload)  # cause/process/decision/outcome/summary 等
        # v6.56（C-2）：事件三级指针。三条都只在"有"的时候落字段——没有就不写，
        # 免得满库都是 "parent_event: None"，把"没接线"和"接到了空"混成一种。
        for k, v in (POINTERS or {}).items():
            if v:
                record[k] = v
        return record

    def _narrative(self, trigger: str, payload: Dict[str, Any],
                   person: Optional[str], model: Optional[str],
                   session_id: Optional[str]) -> Dict[str, Any]:
        """把六节点 payload 映射进叙事六要素（§3.4 六要素映射表）。"""
        n: Dict[str, Any] = {}
        # 时间：每条记录由 timestamp 承载
        n["time"] = iso_now()
        # 地点：session_id / model / 环境
        n["place"] = " / ".join(x for x in [session_id, model] if x) or PENDING
        # 人物：参与者
        n["person"] = person or "用户"
        # 起因：任务启动承载
        n["cause"] = payload.get("cause", PENDING) if trigger == TRIGGER_START else PENDING
        # 经过：过程节点 / 决策 / 异常承载
        if trigger == TRIGGER_STEP:
            n["process"] = payload.get("process", PENDING)
        elif trigger == TRIGGER_DECISION:
            d = str(payload.get("decision", PENDING))
            rej = str(payload.get("rejected_options", PENDING))
            n["process"] = "决策：" + d + (f"（否决：{rej}）" if rej != PENDING else "")
        elif trigger == TRIGGER_ANOMALY:
            n["process"] = "异常：" + str(payload.get("problem", PENDING))
        else:
            n["process"] = PENDING
        # 结果：结论点 / 任务结束承载
        if trigger == TRIGGER_CONCLUSION:
            n["result"] = str(payload.get("outcome") or payload.get("decision") or PENDING)
        elif trigger == TRIGGER_END:
            n["result"] = str(payload.get("summary") or PENDING)
        else:
            n["result"] = PENDING
        return fill_missing_elements(n)
