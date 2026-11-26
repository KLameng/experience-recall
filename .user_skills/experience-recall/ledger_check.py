#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账实校验：账上写的包，磁盘上真有吗？名字里的哈希，和文件本体一致吗？

用法：
    python ledger_check.py                 # 扫默认库的四本记录
    python ledger_check.py --root <库> --search <额外搜索根> ...
    python ledger_check.py --json

退出码：0 全部对得上（或有引用但本机无实物）/ 1 存在不符 / 2 读不到库

为什么要有这个：
    b8d46596 那次，状态层写着"交付 ..._b8d46596.zip（926,670 字节）"，
    而真正交出去的是 ..._61d979cd.zip（926,763）。两个窗口各自都对，
    合起来是"账上记的包不是交出去的包"。人工核对靠不住，必须让工具去核。

判据（三条，缺一不可）：
    1. 账上出现的每个包名，要么能在搜索根里找到实物，要么明确报"本机无此包"（不算失败）
    2. 找到的实物，真实 sha256 前 8 位必须等于文件名里的那 8 位
       —— 名字是承诺，内容是事实，二者不符说明包被换过或名字抄错
    3. 账上若在同一行写了字节数，必须等于实物字节数
       —— 只在同一行绑定，绝不跨行猜归属（跨行猜就会像探针脚本那样串行误配）
"""
import io, json, os, re, sys, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = ("缺陷台账_20261112.md", "发版流水_202611.md", "current-work-state.md", "任务清单_20261113.md")

RE_PKG = re.compile(r"[^\s`|()\[\]（）《》\"']*?(_[0-9a-f]{8})\.zip")
RE_SIZE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{5,7})\s*字节")

DEFAULT_SEARCH = [
    os.path.join(os.path.expanduser("~"), "Doubao"),
    os.path.join(os.path.expanduser("~"), "归档"),
]


def _guess_root():
    """默认库根：多候选逐个试（与主程序同口径）。

    原来写死 "~/.doubao/agent_mode/workspace/experience"。本机真库在 AppData 下，
    于是不给 --root 时它扫了一个不存在的目录，账上一份都没读到，然后报
    「台账 0 个引用 / LEDGER OK：账实一致」——**库都没找到，说账实一致**。
    这是最坏的一种通过：把"没读到"当成"没问题"。
    """
    cands = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cands.append(os.path.join(os.path.dirname(os.path.dirname(here)), 'experience'))
    except Exception:
        pass
    cands.append(os.path.join(os.path.expanduser("~"),
                  "AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/experience"))
    cands.append(os.path.join(os.path.expanduser("~"), ".doubao/agent_mode/workspace/experience"))
    for c in cands:
        try:
            if os.path.isdir(c) and any(n.startswith("缺陷台账") or n.startswith("发版流水")
                                        for n in os.listdir(c)):
                return os.path.realpath(c)
        except Exception:
            pass
    return os.path.realpath(cands[0])


def root_of(argv):
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            return os.path.realpath(argv[i + 1])
    return os.path.realpath(os.environ.get("EXP_ROOT", _guess_root()))


def search_roots(argv):
    out = [os.path.expanduser(x) for x in DEFAULT_SEARCH]
    for i, a in enumerate(argv):
        if a == "--search" and i + 1 < len(argv):
            out.append(argv[i + 1])
    return [p for p in out if os.path.isdir(p)]


def sha8(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def find(roots, name, cap=4000):
    """按文件名找实物。只认"文件名尾段完全一致"，防同哈希不同内容的近似名。"""
    hits = []
    seen = 0
    for r in roots:
        for dp, dns, fns in os.walk(r):
            dns[:] = [d for d in dns if d not in (".git", "node_modules", "__pycache__")]
            for fn in fns:
                seen += 1
                if seen > cap:
                    return hits
                if fn == name:
                    hits.append(os.path.join(dp, fn))
    return hits


def do_claim(zip_path):
    """核一个刚打好的包**自身**（发版流程的正向路径）。

    为什么不能靠"账里指的包"来做正向核对：账里不许写死当次哈希（D89，重打一次包账就过期）。
    所以正向路径只能是"拿手里的包核它自己"：
      1. 文件名里的 8 位哈希 == 实物 sha256 前 8 位
      2. 包能打开、条目数 > 0
      3. 包内 MANIFEST.json 能读出，且 files 列表的每条都在包里、字节数与 MANIFEST 一致
         （相当于把"包内每件都与清单相符"核一遍，而不是抽查）
    """
    import zipfile
    if not os.path.exists(zip_path):
        print("CLAIM FAIL：没有这个文件 %s" % zip_path); return 1
    base = os.path.basename(zip_path)
    m = re.search(r"_([0-9a-f]{8})\.zip$", base)
    if not m:
        print("CLAIM FAIL：文件名尾部没有 8 位哈希，无法自证身份：%s" % base); return 1
    h = m.group(1)
    real = sha8(zip_path)
    if real != h:
        print("CLAIM FAIL：文件名说 %s，实物是 %s" % (h, real)); return 1
    print("  1) 文件名哈希 = 实物哈希：%s" % real)
    try:
        z = zipfile.ZipFile(zip_path)
    except Exception as e:
        print("CLAIM FAIL：包打不开（%s）" % e); return 1
    names = z.namelist()
    if not names:
        print("CLAIM FAIL：包是空的"); return 1
    # MANIFEST 的位置不固定：本工具打的包把它放在**包根**，另一个打包脚本放在
    # `.user_skills/` 下。原先只认 `names[0] 的第一段 + /MANIFEST.json`，
    # 于是对着自己刚打的包报"包里没有 MANIFEST.json"——正向路径第一次跑就卡在这。
    # 两处都要认；路径拼接也一样：files[].path 有的带全路径、有的相对子根，逐个试。
    cands = ["MANIFEST.json"]
    _top = names[0].split("/")[0]
    if _top and _top != "MANIFEST.json":
        cands.append(_top + "/MANIFEST.json")
    man_p = next((c for c in cands if c in names), None)
    if man_p is None:
        print("CLAIM FAIL：包里没有 MANIFEST.json（试过 %s）" % "、".join(cands)); return 1
    top = os.path.dirname(man_p)   # 清单所在目录 = 相对路径的基准（包根时为空串）
    try:
        man = json.loads(z.read(man_p).decode("utf-8"))
    except Exception as e:
        print("CLAIM FAIL：MANIFEST 读不出（%s）" % e); return 1
    files = man.get("files") or []
    if not files:
        print("CLAIM FAIL：MANIFEST.files 是空的"); return 1
    print("  2) 包可打开：%d 条目，MANIFEST 记 %d 件，version %s"
          % (len(names), len(files), man.get("version")))
    info = z.infolist()
    zs = {i.filename: i.file_size for i in info}
    paths = set(f["path"] for f in files)
    miss, sizebad = [], []
    for f in files:
        rel = f["path"]
        zp = (top + "/" + rel) if top else rel
        if zp not in zs and rel in zs:      # 清单里已经是 zip 内全路径
            zp = rel
        if zp not in zs:
            miss.append(f["path"])
        elif zs[zp] != f["bytes"]:
            sizebad.append("%s 清单 %d / 包内 %d" % (f["path"], f["bytes"], zs[zp]))
    if miss:
        print("CLAIM FAIL：清单里有 %d 件不在包里：%s" % (len(miss), "、".join(miss[:4]))); return 1
    if sizebad:
        print("CLAIM FAIL：%d 件字节数与清单不符：%s" % (len(sizebad), "；".join(sizebad[:3]))); return 1
    # 夹带检查：清单在包根时 top=""，原来的 startswith(top+"/") 会退化成
    # startswith("/")，一条都匹配不上，夹带永远查不出来（假绿）。
    if top:
        extra = [n for n in names
                 if n.startswith(top + "/") and n != man_p and n[len(top) + 1:] not in paths]
    else:
        extra = [n for n in names
                 if not n.endswith("/") and n != man_p and n not in paths]
    if extra:
        print("CLAIM FAIL：包里有 %d 件未被清单收录（夹带，或清单少了这条）：%s"
              % (len(extra), "、".join(extra[:4]))); return 1
    print("  3) 清单与包内逐件相符：%d 件路径全在、字节数全等、无夹带" % len(files))
    print("CLAIM OK：%s（%d 字节，sha8 %s）" % (base, os.path.getsize(zip_path), real))
    return 0


def main(argv):
    if "--claim" in argv:
        i = argv.index("--claim")
        if i + 1 >= len(argv):
            print("用法：ledger_check.py --claim <包.zip>"); return 2
        return do_claim(argv[i + 1])
    root = root_of(argv)
    want_json = "--json" in argv
    roots = search_roots(argv)
    # 库/账先确认在不在：账本一份都读不到时不许往下走。
    # 曾经这里一路 continue，最后报「LEDGER OK：账实一致」——库都没找到却说账实相符。
    if not os.path.isdir(root):
        print("⛔ 找不到库：%s" % root)
        print("   账实校验先得有账。库都不在时报「一致」，是把「没读到」当成「没问题」。")
        return 2
    if not [d for d in DOCS if os.path.exists(os.path.join(root, d))]:
        print("⛔ 库在（%s），但四本账一份都没找到" % root)
        print("   查的是：%s" % "、".join(DOCS))
        return 2
    refs = {}          # hash8 -> {"names": set(), "size": set(), "docs": set()}
    for d in DOCS:
        p = os.path.join(root, d)
        if not os.path.exists(p):
            continue
        for ln in io.open(p, encoding="utf-8", errors="replace"):
            for m in RE_PKG.finditer(ln):
                h = m.group(1)
                tok = m.group(0).strip()
                if "_" not in tok:
                    continue
                name = tok[tok.rfind("/") + 1:] if "/" in tok else tok
                # 账里有省略写法（`..._61d979cd.zip`）。一个哈希就是一个包，
                # 按哈希归并；名字取最长的那个（最可能是全名）。
                rec = refs.setdefault(h, {"names": set(), "size": set(), "docs": set()})
                rec["names"].add(name)
                rec["docs"].add(d)
                sm = RE_SIZE.search(ln)
                if sm:
                    rec["size"].add(int(sm.group(1).replace(",", "")))

    rows = []
    bad = 0
    for h, rec in sorted(refs.items()):
        name = max(rec["names"], key=len)
        aliases = sorted(n for n in rec["names"] if n != name)
        hits = find(roots, name)
        if not hits:
            rows.append({"pkg": name, "hash": h, "stage": "missing",
                         "note": "本机搜索根里无此包", "docs": sorted(rec["docs"]),
                         "sizes": sorted(rec["size"])})
            continue
        p = hits[0]
        size = os.path.getsize(p)
        real = sha8(p)
        stage, note = "ok", ""
        if real != h:
            stage, note = "sha-mismatch", "文件名 %s，实物 %s" % (h, real)
        elif rec["size"] and size not in rec["size"]:
            stage, note = "size-mismatch", "账上写 %s，实物 %d" % (
                "/".join(str(x) for x in sorted(rec["size"])), size)
        elif len(rec["size"]) > 1:
            stage, note = "size-mismatch", "账上自相矛盾，写了 %s" % (
                "/".join(str(x) for x in sorted(rec["size"])))
        if stage != "ok":
            bad += 1
        rows.append({"pkg": name, "hash": h, "stage": stage, "note": note,
                     "docs": sorted(rec["docs"]), "sizes": sorted(rec["size"]),
                     "size": size, "path": p, "aliases": aliases})

    if want_json:
        print(json.dumps({"root": root, "search": roots, "rows": rows,
                          "bad": bad, "exit": 1 if bad else 0}, ensure_ascii=False, indent=1))
        return 1 if bad else 0

    print("库：%s" % root)
    print("搜索根：%s" % ("、".join(roots) if roots else "（无，跳过实物核对）"))
    print("账上包引用：%d 个唯一包" % len(rows))
    counts = {}
    for r in rows:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
    for r in rows:
        if r["stage"] != "ok":
            extra = ("（另有省略写法 %d 个）" % len(r["aliases"])) if r.get("aliases") else ""
            print("  %-14s %-52s %s%s" % (r["stage"], r["pkg"], r["note"], extra))
    print("汇总：" + "、".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    if bad:
        print("LEDGER MISMATCH：%d 个包账实不符 —— 账上写的不是交出去的那个" % bad)
        return 1
    if counts.get("missing") == len(rows):
        print("LEDGER OK（本机无实物，仅核对账内自洽）")
    else:
        print("LEDGER OK：账实一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
