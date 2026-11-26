#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署清单校验：库缺了什么，一次说清，不让闸空转。

用法：
    python deploy_check.py                # 查默认库（EXP_ROOT；否则 技能目录反推→AppData→旧路径 逐个试）
    python deploy_check.py --root <库目录>
    python deploy_check.py --fix          # 只补可派生项（跑 build_index 由 recall.py 负责）
    python deploy_check.py --json         # 机器读

退出码：0 齐备 / 5 缺必需件（环境异常，不是业务失败）/ 2 清单本身读不到

为什么要有这个：
    D78 —— 批准表找不到时，_approved() 静默返回空集，42 根探针全判"未批准"，
            闸报"0 根探针"exit 2。工具不报错，只安静地空转。
    D79 —— 派生件（时效台账.json）没生成时，探针比工具严，一条探针假红。
    这两类都不是业务缺陷，是"库不完整"。缺件必须有一处能说清楚。
"""
import io, json, os, sys, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))


def baseline_check(root):
    """全量存在性：预期基线里有的件，现在还有没有。

    为什么要有这一层：清单只 13 件（语义必需），库里 328 件——**315 件没有任何人看管**。
    实测删掉 `资产册/资产锚.json`（在基线里、不在清单里），deploy_check 照样报
    `DEPLOY OK` 退出 0。D162 那次丢 50 件、3 件截断却"全绿"，就是这个洞。

    判据只判"存在/缺失"，**不判 sha8**：正常记账会让台账哈希天天变，判哈希等于把闸
    变成每天假红（红到没人看，比不拦更糟）。库里比基线**多** = 正常增长（新增卡），不报。

    基线读不到时退回包内 MANIFEST 作下限；两者都没有 → 明说"未判"，不假装知道。
    """
    bp = os.path.join(root, ".sync_baseline.json")
    src, files = None, {}
    if os.path.exists(bp):
        try:
            d = json.load(io.open(bp, encoding="utf-8"))
            files = d.get("files") or {}
            src = "预期基线"
        except Exception:
            files, src = {}, None
    if not files:
        for cand in (os.path.join(root, "MANIFEST.json"),
                     os.path.join(os.path.dirname(root), "MANIFEST.json")):
            if os.path.exists(cand):
                try:
                    m = json.load(io.open(cand, encoding="utf-8"))
                    for it in (m.get("files") or []):
                        p = it.get("path", "")
                        if p.startswith("experience/"):
                            files[p[len("experience/"):]] = None
                    src = "包内 MANIFEST（下限）"
                    break
                except Exception:
                    pass
    if not files:
        return {"ok": False, "source": None, "total": 0, "missing": [],
                "note": "预期基线、包内 MANIFEST 都读不到 → 全量存在性未判（不假装知道）"}
    missing = []
    for k in files:
        if k in (".guard_pending.json",) or k.endswith(".tmp"):
            continue  # 临时件，不该进基线也不该判
        if not os.path.exists(os.path.join(root, k)):
            missing.append(k)
    return {"ok": not missing, "source": src, "total": len(files),
            "missing": missing, "note": ""}


def _guess_root():
    """默认库根：多候选逐个试（与主程序 recall.py、ledger_check.py 同一口径）。

    原先写死 "~/.doubao/agent_mode/workspace/experience"。本机真库在
    AppData/Local/Doubao/User Data/Default/.doubao/... 下，于是不给 --root 时
    它扫的是一个不存在的目录：清单自然读不到，接着走"生产库缺清单"那一支，
    对着一个明明装着清单的真库报 exit 5 —— 部署校验在本机永远是红的。
    这就是 D137 那批漏网的第 4 处：只修了三份测试的默认根，没修主程序自己。
    """
    cands = []
    try:
        cands.append(os.path.join(os.path.dirname(os.path.dirname(HERE)), 'experience'))
    except Exception:
        pass
    cands.append(os.path.expanduser(
        "~/AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/experience"))
    cands.append(os.path.expanduser("~/.doubao/agent_mode/workspace/experience"))
    for c in cands:
        if os.path.exists(os.path.join(c, 'current-work-state.md')):
            return os.path.realpath(c)
    return os.path.realpath(cands[0] if cands else 'experience')


DEFAULT_ROOT = _guess_root()
# 判"这是不是本机生产库"必须钉在生产库的固定位置，不能拿 root == DEFAULT_ROOT 比：
# DEFAULT_ROOT 是反推出来的，反推结果当然等于它自己，副本库也会被判成生产库
# （D134 同款——隔离守卫就栽在这上面，每次正常测试都报"隔离失效"，喊多了没人信）。
_PROD_ROOTS = [os.path.expanduser(
    "~/AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/experience"),
    os.path.expanduser("~/.doubao/agent_mode/workspace/experience")]


def _is_prod_root(root):
    r = os.path.realpath(root)
    return any(os.path.realpath(p) == r for p in _PROD_ROOTS)


CAND_DOCS = ("部署清单.json", "state/部署清单.json")


def root_of(argv):
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            return os.path.realpath(argv[i + 1])
    return os.path.realpath(os.environ.get("EXP_ROOT", DEFAULT_ROOT))


def sha8(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def approved_file(root):
    """复刻 recall.py 的真实解析规则，并把"取自何处"报出来——这正是 D78 的坑。"""
    return find_in(os.path.realpath(root), "探针批准.json"), "库目录（治理件跟库走，只有库根一份）"


ALIAS = {"_index.md": ("INDEX.md",), "INDEX.md": ("_index.md",),
         "匹配索引.json": ("INDEX_MATCH.json", "_match_index.json"),
         "时效台账.json": ("_时效台账.json",)}


def find_in(root, rel):
    """库根找不到时，往包内布局的子目录找（state/ experience/ tools/）。

    v6.5 包解开是分区布局，部署态是摊平的；只认库根一层，就会出现"包里明明有、
    自检却说缺"——实测解包后报缺 4 件必需件，全是 state/ 下的。
    """
    for name in (rel,) + ALIAS.get(rel, ()):
        for d in ("", "state", "experience", "tools"):
            p = os.path.join(root, d, name) if d else os.path.join(root, name)
            if os.path.exists(p):
                return p
    return os.path.join(root, rel)


def main(argv):
    root = root_of(argv)
    want_json = "--json" in argv
    items, manifest = [], None
    for c in CAND_DOCS:
        p = find_in(root, c)
        if os.path.exists(p):
            manifest = p
            try:
                items = json.load(io.open(p, encoding="utf-8")).get("items", [])
            except Exception as e:
                print("清单读不出来：%s（%s）" % (c, e))
                return 2
            break

    rows, bad_required, missing_derived = [], [], []

    if manifest is None:
        if _is_prod_root(root):
            # D84：清单自己丢了，这时退回"内置最小集"是错的——内置集把批准表算成
            # 库内必需件（其实它跟着库走），于是报"缺 探针批准.json"，指错了地方。
            # 缺清单就是缺清单，说清楚，不去猜别的件坏没坏。
            print("⛔ 缺 部署清单.json：这是本机生产库，清单应随包发布、解包后落在库根。")
            print("   清单丢了就无从判完整性；退回内置最小集会误报别的件"
                  "（实测把正常的批准表报成缺失）。")
            print("   修法：从包里取回 部署清单.json 放回库根。")
            return 5
        # 副本库（探针自建的临时小库）不强制带清单：按内置最小集判，
        # 批准表归 approval（位置随库走，不按库内文件查）。
        items = [
            {"path": "探针批准.json", "kind": "approval", "why": "位置随库走，单独由 approved 段报"},
            {"path": "时效台账.md", "kind": "required", "why": "缺则时效无从谈起"},
            {"path": "匹配索引.json", "kind": "derived", "why": "build_index 生成"},
            {"path": "时效台账.json", "kind": "derived", "why": "build_index 生成；缺则单根探针假红"},
            {"path": "current-work-state.md", "kind": "required", "why": "状态层，开工第一读"},
            {"path": "发版流水_202611.md", "kind": "required", "why": "流水，发版唯一凭据"},
            {"path": "缺陷台账_20261112.md", "kind": "required", "why": "缺陷台账"},
        ]

    for it in items:
        rel = it["path"]
        if it.get("kind") == "approval":
            # 位置随库而变，存在性由下面的 approved 段统一报；
            # 但若清单锁了哈希、文件又在，内容被动过仍要抓（防止"名字对、内容换"）。
            p0 = find_in(root, rel)
            if os.path.exists(p0) and it.get("sha8"):
                got = sha8(p0)
                if got != it["sha8"]:
                    # 正常重批/撤销会由 approve 写完立刻刷新清单 sha8（实测：重批后
                    # 清单 6c0a74af = 文件实 sha8）。所以"对不上"只剩一种含义：
                    # 有人绕过写入口直接改了批准表 —— 那是越界，不是提示。
                    rows.append({"path": rel, "kind": "approval", "stage": "approval-tampered",
                                 "size": os.path.getsize(p0),
                                 "note": "清单记 %s，实际 %s：绕过 approve 直接改过批准表"
                                         % (it["sha8"], got)})
            continue
        p = find_in(root, rel)
        ok = os.path.exists(p)
        size = os.path.getsize(p) if ok else 0
        stage = "ok"
        note = ""
        if not ok:
            if it.get("kind") == "optional":
                stage = "missing(optional)"
                note = it.get("why", "") or "可选，缺不算坏"
            elif it.get("kind") in ("derived", "derived-alias"):
                stage = "missing(derived)"
                missing_derived.append(rel)
                note = it.get("why", "") or "可派生"
            else:
                stage = "missing(required)"
                bad_required.append(rel)
                note = it.get("why", "")
        elif it.get("sha8") and sha8(p) != it["sha8"]:
            stage = "sha-mismatch"
            if it.get("kind") in ("derived", "derived-alias"):
                missing_derived.append(rel)
                note = "内容与清单不符（派生件会被 build 覆盖）"
            else:
                bad_required.append(rel)
                note = "清单记 %s，实际 %s" % (it["sha8"], sha8(p))
        rows.append({"path": rel, "kind": it.get("kind", "required"), "stage": stage,
                     "size": size, "note": note})

    # ---- 全量存在性（清单之外的 315 件，靠预期基线兜）----
    bl = baseline_check(root)
    for rel in bl.get("missing", []):
        rows.append({"path": rel, "kind": "required", "stage": "missing(baseline)",
                     "size": 0,
                     "note": "预期基线里有、现在没有（%s）" % (bl.get("source") or "")})
        bad_required.append(rel)

    ap, where = approved_file(root)
    ap_ok = os.path.exists(ap)
    ap_n = 0
    ap_count_bad = False
    if ap_ok:
        try:
            d = json.load(io.open(ap, encoding="utf-8"))
            # 字段名以实物为准：真件是 hashes / count / approved_at
            ap_n = len(d.get("hashes") or d.get("approved") or [])
            # 批准表自校验：count 必须等于实际条数。
            # 为什么要有这条：count 只是文件里写的一个数字，没人核对就等于没人管——
            # 实测把 count 47 改成 43，闸和自检照报 OK（回归件 E 用例一直 FAIL）。
            # 它抓不到"改一条哈希"（那要连 hashes 一起改），但能抓"条数被动"这一类，
            # 而且不必给清单再锁一层 sha8（那会把 D87 的三环再绕一圈）。
            c = d.get("count")
            if isinstance(c, int) and c != ap_n:
                ap_count_bad = True
                rows.append({"path": ap, "kind": "approval", "stage": "count-mismatch",
                             "size": os.path.getsize(ap),
                             "note": "count 写 %d，实际 %d 条" % (c, ap_n)})
        except Exception as e:
            ap_n = -1
            rows.append({"path": ap, "kind": "approval", "stage": "unreadable",
                         "size": os.path.getsize(ap), "note": str(e)[:60]})

    if want_json:
        print(json.dumps({"root": root, "manifest": manifest, "items": rows,
                          "baseline": {"ok": bl.get("ok"), "source": bl.get("source"),
                                       "total": bl.get("total"), "missing": bl.get("missing"),
                                       "note": bl.get("note")},
                          "approved": {"file": ap, "from": where, "ok": ap_ok, "count": ap_n,
                                       "count_consistent": not ap_count_bad},
                          "exit": 5 if (bad_required or not ap_ok or ap_n <= 0 or ap_count_bad) else 0},
                         ensure_ascii=False, indent=1))
        return 0 if not (bad_required or not ap_ok or ap_n <= 0 or ap_count_bad) else 5

    print("库：%s" % root)
    print("清单：%s" % (manifest or "（无，按内置最小集）"))
    shown_bl = 0
    for r in rows:
        if r["stage"] == "missing(baseline)":
            shown_bl += 1
            if shown_bl > 12:
                continue  # 缺得太多时不必逐条刷屏，总数在下面一行
        mark = {"ok": "ok  ", "missing(derived)": "缺*  ", "missing(required)": "缺!  ",
                "missing(baseline)": "缺!  ",
                "sha-mismatch": "错!  ", "unreadable": "坏!  ", "count-mismatch": "改!  "}.get(r["stage"], r["stage"])
        print("  %s%-34s %8d  %s" % (mark, r["path"], r["size"], r["note"]))
    if len(bl.get("missing") or []) > 12:
        print("  ……另 %d 件缺（只列前 12）" % (len(bl["missing"]) - 12))
    if not bl.get("source"):
        print("全量存在性：未判 —— 基线读不到，这一段没有保护（不拦，但也不假装知道）")
    elif bl.get("ok"):
        print("全量存在性：%s → 基线 %d 件齐全" % (bl["source"], bl["total"]))
    else:
        print("全量存在性：%s → 基线 %d 件里缺 %d 件"
              % (bl["source"], bl["total"], len(bl["missing"])))
        print('  → 有意删的卡：python recall.py retire --card <卡名> --why "<理由>"'
              '（归档+重建索引+同步基线，一次做完）')
        print('  → 不该丢的：python recall.py restore --from <权威包>（从包补齐，本地更新的件保留）')
    print("批准表：%s" % ap)
    print("        取自 %s → %s，%d 条" % (where, "在" if ap_ok else "不在", ap_n))
    if not ap_ok or ap_n <= 0:
        print("  ✗ 批准表读不到或为空：闸会把全部探针判成\"未批准\"，空转到 exit 2（D78）")
    if ap_count_bad:
        print("  ✗ 批准表的 count 与实际条数不符：这份表被人改过")
    ap_tampered = any(r.get('stage') == 'approval-tampered' for r in rows)
    if ap_tampered:
        print("  ✗ 批准表被绕过写入口改过：清单记的 sha8 与实际不符（正常重批会自动对齐）")
    if bad_required or not ap_ok or ap_n <= 0 or ap_count_bad or ap_tampered:
        if bad_required:
            print("DEPLOY NOT READY：缺/坏必需件 %d 项 → %s" % (len(bad_required), "、".join(bad_required)))
        elif ap_count_bad:
            print("DEPLOY NOT READY：批准表不可信（count 与条数不符）")
        elif ap_tampered:
            print("DEPLOY NOT READY：批准表内容被绕过写入口改过（不是重批，重批会自动对齐）")
        else:
            print("DEPLOY NOT READY：批准表不可用 → 闸无法判定任何探针")
        return 5
    if missing_derived:
        print("待生成/待补：%s" % "、".join(missing_derived))
    print("DEPLOY OK：必需件齐备")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
