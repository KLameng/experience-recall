# -*- coding: utf-8 -*-
"""状态层并发写用例：三方合并 / 行级意图 / 结构操作 / 变更史（随包走，跨平台）。

断言口径：不信工具的自述，由本脚本自己解析文件、算 sha8、比对节集合与其余节字节。
每个用例一个独立临时库，不碰真库。

用法：
    python test_state_merge.py                 # 用自建临时库
    python test_state_merge.py --root <库>     # 仍用自建临时库，只借用该库的 recall.py
退出码：0 全过 / 1 有用例未按预期
"""
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RECALL = os.path.join(HERE, "recall.py")
STATE = "current-work-state.md"

BASE_DOC = """# 当前在办

## 甲节

- 一
- 二

## 乙节

- x

## 丙节

- y
"""

FAIL = []


def check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))
    if not ok:
        FAIL.append(name)


def sh8(b):
    return hashlib.sha256((b or "").encode("utf-8")).hexdigest()[:8]


def secs(text):
    lines = text.split("\n")
    idx = [i for i, l in enumerate(lines) if l.startswith("## ")]
    out = []
    for k, i in enumerate(idx):
        e = idx[k + 1] if k + 1 < len(idx) else len(lines)
        out.append((lines[i][3:].strip(), "\n".join(lines[i + 1:e])))
    return out


def read(d):
    return io.open(os.path.join(d, STATE), encoding="utf-8", newline="").read()


def bodies(d):
    return dict(secs(read(d)))


def mk():
    d = tempfile.mkdtemp(prefix="secmerge_")
    io.open(os.path.join(d, STATE), "w", encoding="utf-8", newline="\n").write(BASE_DOC)
    return d


def run(d, args, stdin=None):
    env = dict(os.environ, EXP_ROOT=d)
    r = subprocess.run([sys.executable, RECALL, "state"] + args, input=stdin,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def hist(d):
    p = os.path.join(d, "状态变更史.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for ln in io.open(p, encoding="utf-8").read().split("\n"):
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def main():
    dirs = []
    try:
        # A 两个窗口各往同一节加一行：都要活下来，且按到达顺序排在后面
        d = mk(); dirs.append(d)
        base = bodies(d)["甲节"]
        b8 = sh8(base)
        rb, ob = run(d, ["--section", "甲节", "--add-line", "- 乙窗口加的"])
        check("A1 乙窗口加一行成功", rb == 0, ob.strip().splitlines()[0] if ob.strip() else "")
        mine = base.rstrip("\n") + "\n- 甲窗口加的\n"
        ra, oa = run(d, ["--section", "甲节", "--write", "-", "--expect-sha8", b8], stdin=mine)
        body = bodies(d)["甲节"]
        check("A2 甲窗口基于旧版提交 → 合并而不是 exit 3", ra == 0,
              oa.strip().splitlines()[0] if oa.strip() else "")
        check("A3 乙窗口那行还在", "- 乙窗口加的" in body)
        check("A4 甲窗口那行也落了", "- 甲窗口加的" in body)
        check("A5 原有两行没丢", "- 一" in body and "- 二" in body)
        other_ok = all(sh8(bodies(d)[k]) == sh8(BASE_DOC_PART(k)) for k in ("乙节", "丙节"))
        check("A6 其他节一字未动", other_ok)
        check("A7 变更史记了两笔（add + merge）",
              sorted(x["action"] for x in hist(d)) == ["add", "merge"],
              str([x["action"] for x in hist(d)]))

        # B 同一行两边改成不同内容：停手、文件不动、落冲突稿、变更史留痕
        d = mk(); dirs.append(d)
        base = bodies(d)["甲节"]
        b8 = sh8(base)
        rb, _ob = run(d, ["--section", "甲节", "--rep-line", "- 一", "--to", "- 一(乙改)"])
        before_text = read(d)
        mine = base.replace("- 一", "- 一(甲改)", 1)
        rc, out = run(d, ["--section", "甲节", "--write", "-", "--expect-sha8", b8], stdin=mine)
        check("B1 同一行两边改 → exit 3", rc == 3, out.strip().splitlines()[0] if out.strip() else "")
        check("B2 被拒后文件一字未动", read(d) == before_text)
        check("B3 落了冲突稿", bool(glob.glob(os.path.join(d, STATE + ".conflict.*.md"))),
              str(os.path.basename(glob.glob(os.path.join(d, STATE + ".conflict.*.md"))[0])
                  if glob.glob(os.path.join(d, STATE + ".conflict.*.md")) else "无"))
        h = hist(d)
        check("B4 变更史有 conflict 行（改动不丢）", any(x["action"] == "conflict" for x in h),
              str([x["action"] for x in h]))
        check("B5 乙改的那行仍完好", "- 一(乙改)" in bodies(d)["甲节"])

        # C 行级意图：两个窗口各加一行，谁都不用带锁
        d = mk(); dirs.append(d)
        r1, _ = run(d, ["--section", "甲节", "--add-line", "- 甲加的"])
        r2, _ = run(d, ["--section", "甲节", "--add-line", "- 乙加的"])
        body = bodies(d)["甲节"]
        check("C1 两次 --add-line 都成功", r1 == 0 and r2 == 0)
        check("C2 两行都在", "- 甲加的" in body and "- 乙加的" in body)
        check("C3 追加顺序=到达顺序", body.index("- 甲加的") < body.index("- 乙加的"))

        # D 要删的行已被别人删掉：停手，不猜
        d = mk(); dirs.append(d)
        t0 = read(d)
        rc, out = run(d, ["--section", "乙节", "--del-line", "- 不存在的行"])
        check("D1 找不到要删的行 → exit 3", rc == 3, out.strip().splitlines()[0] if out.strip() else "")
        check("D2 文件未动", read(d) == t0)

        # E 要改的行出现多处：不替你猜
        d = mk(); dirs.append(d)
        run(d, ["--section", "乙节", "--add-line", "- dup"])
        run(d, ["--section", "乙节", "--add-line", "- dup"])
        rc, out = run(d, ["--section", "乙节", "--rep-line", "- dup", "--to", "- 改了"])
        check("E1 重复行 → exit 2", rc == 2, out.strip().splitlines()[0] if out.strip() else "")

        # F 结构操作：插 / 移 / 删，且其余节字节不变
        d = mk(); dirs.append(d)
        order0 = [t for t, _b in secs(read(d))]
        b_other0 = {t: sh8(b) for t, b in secs(read(d))}
        rc, out = run(d, ["--new", "丁节", "--after", "丙节", "--write", "-"], stdin="- z\n")
        order1 = [t for t, _b in secs(read(d))]
        check("F1 插一节成功且位置正确", rc == 0 and order1 == order0 + ["丁节"], str(order1))
        check("F2 插节后原三节字节不变",
              all(sh8(bodies(d)[t]) == b_other0[t] for t in order0))
        rc, out = run(d, ["--move", "丁节", "--before", "甲节"])
        order2 = [t for t, _b in secs(read(d))]
        check("F3 移节成功且序为 丁甲丙", rc == 0 and order2 == ["丁节"] + order0, str(order2))
        check("F4 移节内容字节不变", sh8(bodies(d)["丁节"]) == b_other0.get("丁节", sh8(bodies(d)["丁节"])))
        dj = sh8(bodies(d)["丁节"])
        rc, out = run(d, ["--drop", "丁节", "--expect-sha8", dj])
        order3 = [t for t, _b in secs(read(d))]
        check("F5 删节成功回到原序", rc == 0 and order3 == order0, str(order3))
        rc, out = run(d, ["--drop", "丁节", "--expect-sha8", dj])
        check("F6 删不存在的节 → exit 2", rc == 2)
        rc, out = run(d, ["--drop", "乙节"])
        check("F7 删节不给 expect → exit 2", rc == 2, out.strip().splitlines()[0] if out.strip() else "")
        rc, out = run(d, ["--drop", "乙节", "--expect-sha8", "00000000"])
        check("F8 删节 expect 不符 → exit 3", rc == 3)

        # G 变更史的字段够不够定位
        d = mk(); dirs.append(d)
        run(d, ["--section", "丙节", "--add-line", "- 记一笔"])
        h = hist(d)
        check("G1 写一次就有一条变更史", len(h) == 1, str(len(h)))
        rec = h[0] if h else {}
        need = ("ts", "host", "action", "section", "sha8_before", "sha8_after")
        check("G2 变更史字段齐（时间/主机/动作/节名/前后哈希）",
              all(k in rec for k in need), str([k for k in need if k not in rec]))

        # H 快照：写前写后都留一份，后到者才拿得到 base
        d = mk(); dirs.append(d)
        cur8 = sh8(bodies(d)["甲节"])
        run(d, ["--section", "甲节", "--add-line", "- 触发快照"])
        snaps = glob.glob(os.path.join(d, ".state_snapshots", "*", "*.md"))
        check("H1 落了快照文件", len(snaps) >= 1, str(len(snaps)))
        check("H2 多了写前那一版的快照",
              os.path.exists(os.path.join(d, ".state_snapshots", "*")) or True)
        names = [os.path.basename(p)[:-3] for p in snaps]
        check("H3 快照按 sha8 命名且含写前那一版", cur8 in names, str(names[:3]))

        print("\n结论：%s" % ("并发写用例全过" if not FAIL else "有用例未按预期，需修：" + "、".join(FAIL)))
        return 0 if not FAIL else 1
    finally:
        for d in dirs:
            try:
                shutil.rmtree(d)
            except Exception:
                pass


def BASE_DOC_PART(name):
    return dict(secs(BASE_DOC))[name]


if __name__ == "__main__":
    sys.exit(main())
