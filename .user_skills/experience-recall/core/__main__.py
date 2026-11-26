"""记忆索引 · 核心层 CLI（零依赖，仅标准库）。

适配层（dsh 插件壳 / 豆包技能壳）通过本 CLI 调用核心层，保持解耦；
同时也可作为独立工具手动使用、做自动化测试。

用法示例
--------
  python -m core --dir ./memory_data start-event --name "COL2A1评级" --type "评级" --cause "..."
  python -m core --dir ./memory_data record-step --event <eid> --action "查 gnomAD" --intermediate "频率 0.0001"
  python -m core --dir ./memory_data record-conclusion --event <eid> --outcome "LP(+9)"
  python -m core --dir ./memory_data end-event --event <eid> --summary "..."
  python -m core --dir ./memory_data recall --event <eid> --budget 4096 --mode reference
  python -m core --dir ./memory_data search --q "COL2A1"
  python -m core --dir ./memory_data pref-set --key "回复语言" --value "简体中文"
  python -m core --dir ./memory_data stats
  python -m core --dir ./memory_data judge --type "评级" --rounds 5

所有命令输出 JSON，便于上层解析。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

DEFAULT_DIR_ENV = "MEMORY_INDEX_DIR"


def _default_dir() -> str:
    env = os.environ.get(DEFAULT_DIR_ENV)
    if env:
        return env
    return os.path.join(os.getcwd(), "memory_data")


def _build(store_dir: str):
    from .store import MemoryStore
    from .index import MemoryIndex
    from .ingest import Ingest
    from .recall import Recall
    store = MemoryStore(store_dir)
    index = MemoryIndex(store)
    index.load()
    return store, index, Ingest(store), Recall(store, index)


def _emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _budget(value: Optional[str]) -> Optional[int]:
    if value in (None, "", "unlimited", "不设限", "0"):
        return None
    try:
        return int(value)
    except ValueError:
        return 4096


def _bool(value: Optional[str]) -> bool:
    """argparse 原生 type=bool 对任何非空串都返回 True，这里做显式解析。"""
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="core", description="记忆索引核心层 CLI")
    parser.add_argument("--dir", default=None, help="记忆数据目录（默认取 $MEMORY_INDEX_DIR 或 ./memory_data）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start-event")
    p.add_argument("--name", required=True)
    p.add_argument("--type", dest="task_type", default=None)
    p.add_argument("--cause", default=None)
    p.add_argument("--person", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--session", dest="session_id", default=None)
    p.add_argument("--tags", default=None)
    p.add_argument("--importance", default=None)
    p.add_argument("--input-ref", dest="input_ref", default=None)
    # v6.56（C-2）：三级指针（父事件 / 相关卡 / 由哪条事件解决）
    p.add_argument("--parent", dest="parent", default=None, help="父事件号：这条事件接在哪条后面")
    p.add_argument("--related", dest="related", default=None, help="相关卡名，逗号分隔")
    p.add_argument("--resolved-from", dest="resolved_from", default=None, help="由哪条事件解决（异常/收口用）")

    p = sub.add_parser("record-step")
    p.add_argument("--event", dest="event_id", required=True)
    p.add_argument("--name", dest="event_name", default="")
    p.add_argument("--action", required=True)
    p.add_argument("--intermediate", default=None)
    p.add_argument("--input-ref", dest="input_ref", default=None)
    p.add_argument("--tool-trace", dest="tool_trace", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--session", dest="session_id", default=None)
    p.add_argument("--tags", default=None)
    p.add_argument("--importance", default=None)

    p = sub.add_parser("record-decision")
    p.add_argument("--event", dest="event_id", required=True)
    p.add_argument("--name", dest="event_name", default="")
    p.add_argument("--decision", required=True)
    p.add_argument("--reason", default=None)
    p.add_argument("--rejected", dest="rejected_options", default=None,
                   help="否决选项（v1.1：被否掉的方案，复盘用）")
    p.add_argument("--model", default=None)
    p.add_argument("--session", dest="session_id", default=None)
    p.add_argument("--tags", default=None)
    p.add_argument("--importance", default=None)

    p = sub.add_parser("record-conclusion")
    p.add_argument("--event", dest="event_id", required=True)
    p.add_argument("--name", dest="event_name", default="")
    p.add_argument("--outcome", default=None)
    p.add_argument("--decision", default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--session", dest="session_id", default=None)
    p.add_argument("--tags", default=None)
    p.add_argument("--importance", default=None)

    p = sub.add_parser("record-anomaly")
    p.add_argument("--event", dest="event_id", required=True)
    p.add_argument("--name", dest="event_name", default="")
    p.add_argument("--problem", required=True)
    p.add_argument("--handling", default=None)
    p.add_argument("--before", default=None)
    p.add_argument("--after", default=None)
    p.add_argument("--lesson", default=None, help="v1.1：提炼常驻教训（写 preference 型记录）")
    p.add_argument("--lesson-key", dest="lesson_key", default=None,
                   help="v1.1：教训的 pref_key，缺省为 教训:<事件名>")
    p.add_argument("--model", default=None)
    p.add_argument("--session", dest="session_id", default=None)
    p.add_argument("--tags", default=None)
    p.add_argument("--importance", default=None)

    p = sub.add_parser("end-event")
    p.add_argument("--event", dest="event_id", required=True)
    p.add_argument("--name", dest="event_name", default="")
    p.add_argument("--summary", default=None)
    p.add_argument("--audit", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--session", dest="session_id", default=None)
    p.add_argument("--tags", default=None)

    p = sub.add_parser("pref-set")
    p.add_argument("--key", dest="pref_key", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--source", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--confirmed-at", dest="confirmed_at", default=None,
                   help="v1.1：硬规则确认时间（可追溯）")
    p.add_argument("--scope", default=None, help="v1.1：硬规则适用范围（可定位）")
    p.add_argument("--is-redline", dest="is_redline", type=_bool, default=None,
                   help="v1.1：是否红线规则（可分级）")

    p = sub.add_parser("recall")
    p.add_argument("--event", dest="event_id", required=True)
    p.add_argument("--budget", default="4096")
    p.add_argument("--mode", default="reference", choices=["reference", "readonly"])
    p.add_argument("--include", dest="include", default=None, help="逗号分隔的字段白名单")

    p = sub.add_parser("search")
    p.add_argument("--q", dest="query", required=True)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("judge")
    p.add_argument("--type", dest="task_type", default=None)
    p.add_argument("--rounds", type=int, default=0)
    p.add_argument("--duration", dest="duration_hint", default=None)
    p.add_argument("--explicit", default=None)

    p = sub.add_parser("six")
    p.add_argument("--event", dest="event_id", default=None, help="校验单个事件；缺省校验全部")

    p = sub.add_parser("stats")
    sub.add_parser("index")

    args = parser.parse_args(argv)
    store_dir = args.dir or _default_dir()

    try:
        return _dispatch(args, store_dir)
    except Exception as exc:  # noqa: BLE001
        _emit({"ok": False, "error": str(exc)})
        return 1


def _dispatch(args: argparse.Namespace, store_dir: str) -> int:
    store, index, ingest, recall = _build(store_dir)

    # v6.56（C-2）：三级指针由命令行给，落库前统一挂到记录上。
    # 必须改**模块**上的 POINTERS，不是实例属性——Ingest 读的是模块级那一份。
    from . import ingest as _ingest_mod
    _ingest_mod.POINTERS = {
        'parent_event': getattr(args, 'parent', None),
        'related_cards': getattr(args, 'related', None),
        'resolved_from': getattr(args, 'resolved_from', None),
    }

    if args.cmd == "start-event":
        r = ingest.start_event(
            event_name=args.name, task_type=args.task_type, cause=args.cause,
            person=args.person, model=args.model, session_id=args.session_id,
            tags=args.tags, importance=args.importance, input_ref=args.input_ref)
        _emit({"ok": True, "record": r, "event_id": r["event_id"]})

    elif args.cmd == "record-step":
        r = ingest.record_step(
            event_id=args.event_id, event_name=args.event_name, action=args.action,
            intermediate=args.intermediate, input_ref=args.input_ref,
            tool_trace=args.tool_trace, model=args.model, session_id=args.session_id,
            tags=args.tags, importance=args.importance)
        _emit({"ok": True, "record": r})

    elif args.cmd == "record-decision":
        r = ingest.record_decision(
            event_id=args.event_id, decision=args.decision, reason=args.reason,
            rejected_options=args.rejected_options,
            event_name=args.event_name, model=args.model, session_id=args.session_id,
            tags=args.tags, importance=args.importance)
        _emit({"ok": True, "record": r})

    elif args.cmd == "record-conclusion":
        r = ingest.record_conclusion(
            event_id=args.event_id, outcome=args.outcome, decision=args.decision,
            note=args.note, event_name=args.event_name, model=args.model,
            session_id=args.session_id, tags=args.tags, importance=args.importance)
        _emit({"ok": True, "record": r})

    elif args.cmd == "record-anomaly":
        r = ingest.record_anomaly(
            event_id=args.event_id, problem=args.problem, handling=args.handling,
            before=args.before, after=args.after, event_name=args.event_name,
            lesson=args.lesson, lesson_key=args.lesson_key,
            model=args.model, session_id=args.session_id, tags=args.tags,
            importance=args.importance)
        _emit({"ok": True, "record": r["record"],
               "lesson": r.get("lesson")})

    elif args.cmd == "end-event":
        r = ingest.end_event(
            event_id=args.event_id, summary=args.summary, audit=args.audit,
            event_name=args.event_name, model=args.model, session_id=args.session_id,
            tags=args.tags)
        _emit({"ok": True, "record": r["record"], "six_elements": r["six_elements"]})

    elif args.cmd == "pref-set":
        r = ingest.set_preference(
            pref_key=args.pref_key, value=args.value, source=args.source,
            model=args.model, confirmed_at=args.confirmed_at, scope=args.scope,
            is_redline=args.is_redline)
        _emit({"ok": True, "record": r})

    elif args.cmd == "recall":
        include = [x for x in (args.include or "").split(",") if x] or None
        r = recall.recall_event(
            event_id=args.event_id, token_budget=_budget(args.budget),
            mode=args.mode, include_payload_keys=include)
        _emit({"ok": True, **r})

    elif args.cmd == "search":
        time_range = {"start": args.start, "end": args.end} if (args.start or args.end) else None
        _emit({"ok": True, "candidates": recall.search_candidates(
            args.query, time_range=time_range, limit=args.limit)})

    elif args.cmd == "judge":
        _emit({"ok": True, **ingest.judge_importance(
            task_type=args.task_type, rounds=args.rounds,
            duration_hint=args.duration_hint, explicit=args.explicit)})

    elif args.cmd == "six":
        if args.event_id:
            _emit({"ok": True, "six_elements": ingest.verify_six_elements(args.event_id)})
        else:
            _emit({"ok": True, "all": ingest.verify_all_events()})

    elif args.cmd == "stats":
        _emit({"ok": True, **store.stats()})

    elif args.cmd == "index":
        index.build()
        index.save()
        _emit({"ok": True, "events": len(index.all_events()), "index_path": index.index_path})

    else:
        _emit({"ok": False, "error": f"未知命令 {args.cmd}"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
