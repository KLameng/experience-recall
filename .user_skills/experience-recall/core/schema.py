"""记忆索引 · 核心层 schema 定义（零依赖，仅标准库）。

对应设计方案 §3.1 记录结构化字段 / §3.3 事件型+偏好型 /
§3.4 叙事六要素 / §3.5 重要性三档 / §4.3 token 预算 / §6.1 写入触发点 / §5 使用模式。
"""
from __future__ import annotations

import uuid
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# record_type：事件型 + 偏好型（同一单库，用该字段区分，互不污染）
# ---------------------------------------------------------------------------
RECORD_TYPE_EVENT = "event"            # 事件型：追加、历史档案、按需注入
RECORD_TYPE_PREFERENCE = "preference"  # 偏好型：覆盖更新、当前状态、常驻注入
RECORD_TYPES = [RECORD_TYPE_EVENT, RECORD_TYPE_PREFERENCE]

# ---------------------------------------------------------------------------
# 叙事六要素（§3.4 铁律 #6）：时间 / 地点 / 人物 / 起因 / 经过 / 结果
# 缺则显式标"待补充"，不静默留空。
# ---------------------------------------------------------------------------
SIX_ELEMENTS = ["time", "place", "person", "cause", "process", "result"]
SIX_ELEMENTS_CN = {
    "time": "时间", "place": "地点", "person": "人物",
    "cause": "起因", "process": "经过", "result": "结果",
}
PENDING = "待补充"  # 六要素缺时的显式占位

# ---------------------------------------------------------------------------
# 重要性三档（§3.5）：不重要的简单记，重要的详细记；三档六要素都齐全，
# 只差"经过"颗粒度。
# ---------------------------------------------------------------------------
IMPORTANCE_DETAIL = "详细级"    # 重要：评级 / 反复讨论 / 长进程
IMPORTANCE_STANDARD = "标准级"  # 一般：普通任务
IMPORTANCE_BRIEF = "精简级"     # 不重要：琐碎小任务
IMPORTANCE_LEVELS = [IMPORTANCE_DETAIL, IMPORTANCE_STANDARD, IMPORTANCE_BRIEF]

# detail_level：决定"经过"的落库颗粒度
DETAIL_LEVEL = {
    IMPORTANCE_DETAIL: "step_by_step",  # 过程轨迹每步详记
    IMPORTANCE_STANDARD: "summarized",  # 经过概括 + 关键决策
    IMPORTANCE_BRIEF: "minimal",        # 经过极简（一句 + 结果）
}
DEFAULT_IMPORTANCE = IMPORTANCE_STANDARD

# ---------------------------------------------------------------------------
# 写入触发点（§6.1 六节点）
# ---------------------------------------------------------------------------
TRIGGER_START = "任务启动"
TRIGGER_STEP = "过程节点"
TRIGGER_DECISION = "决策点"
TRIGGER_CONCLUSION = "结论点"
TRIGGER_ANOMALY = "异常点"
TRIGGER_END = "任务结束"
TRIGGER_POINTS = [TRIGGER_START, TRIGGER_STEP, TRIGGER_DECISION,
                  TRIGGER_CONCLUSION, TRIGGER_ANOMALY, TRIGGER_END]

# ---------------------------------------------------------------------------
# token 预算档位（§4.3 弹窗选项）
# ---------------------------------------------------------------------------
TOKEN_BUDGET_2K = 2048
TOKEN_BUDGET_4K = 4096
TOKEN_BUDGET_8K = 8192
TOKEN_BUDGET_UNLIMITED = None
TOKEN_BUDGETS = [TOKEN_BUDGET_2K, TOKEN_BUDGET_4K,
                 TOKEN_BUDGET_8K, TOKEN_BUDGET_UNLIMITED]
TOKEN_BUDGET_CN = {
    TOKEN_BUDGET_2K: "2K", TOKEN_BUDGET_4K: "4K",
    TOKEN_BUDGET_8K: "8K", TOKEN_BUDGET_UNLIMITED: "不设限",
}

# ---------------------------------------------------------------------------
# 使用模式（§5 记忆使用边界）
# ---------------------------------------------------------------------------
MODE_REFERENCE = "reference"  # 参考：历史 + 已全量走新流程（默认）
MODE_READONLY = "readonly"    # 只读：用户显式声明，仅历史，未走新流程
MODE_CN = {
    MODE_REFERENCE: "参考（历史 + 已全量走新流程）",
    MODE_READONLY: "只读（用户显式声明，未走新流程）",
}

# ---------------------------------------------------------------------------
# ID / 时间
# ---------------------------------------------------------------------------
def new_record_id() -> str:
    return "rec_" + uuid.uuid4().hex[:12]


def new_event_id() -> str:
    return "evt_" + uuid.uuid4().hex[:12]


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def normalize_tags(tags: Optional[Any]) -> List[str]:
    """把 tags 归一成去重后的字符串列表（保证可统计、可索引）。"""
    if not tags:
        return []
    if isinstance(tags, str):
        return [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
    out = []
    for t in tags:
        s = str(t).strip()
        if s and s not in out:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# 六要素校验
# ---------------------------------------------------------------------------
def check_six_elements(record: Dict[str, Any]) -> Dict[str, str]:
    """检查一条记录承载的六要素，缺则标"待补充"。

    返回 {要素: "已填写"/"待补充"}。单条记录天然只承载部分要素
    （过程节点只承载"经过"），整体完整性由 end_event 聚合校验兜底。
    """
    narrative = record.get("narrative") or {}
    status: Dict[str, str] = {}
    for el in SIX_ELEMENTS:
        val = narrative.get(el)
        if val and str(val).strip() and str(val).strip() != PENDING:
            status[el] = "已填写"
        else:
            status[el] = PENDING
    return status


def fill_missing_elements(narrative: Dict[str, Any]) -> Dict[str, Any]:
    """把 narrative 中缺失的要素补齐为"待补充"，返回新 dict（不就地改）。"""
    out = dict(narrative or {})
    for el in SIX_ELEMENTS:
        val = out.get(el)
        if not val or not str(val).strip():
            out[el] = PENDING
    return out
