# -*- coding: utf-8 -*-
"""多机同步用例：pack / diff / merge 的三方判定、显式选边、幂等、冲突不落盘、越界路径。

断言口径：不信工具自述，由本脚本自己算 sha256、比对字节、检查库外是否被写。
每个用例一个独立临时库，跑完必回收（闸会查 mkdtemp 未回收）。

用法：python test_sync.py [--root <库>]
退出码：0 全过 / 1 有用例未按预期
"""
import hashlib, io, json, os, shutil, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
RECALL = os.path.join(HERE, "recall.py")
FAIL = []
DIRS = []


def check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))
    if not ok:
        FAIL.append(name)


def sha16(p):
    try:
        return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
    except Exception:
        return None


def mk(tag="sync_"):
    d = tempfile.mkdtemp(prefix=tag)
    DIRS.append(d)
    return d


def w(p, s):
    x = os.path.dirname(p)
    if x and not os.path.isdir(x):
        os.makedirs(x, exist_ok=True)
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)


def run(root, args):
    env = dict(os.environ, EXP_ROOT=root)
    r = subprocess.run([sys.executable, RECALL, "sync"] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


CARD = """---
name: card-%s
description: %s
metadata:
 type: work_experience
 source: harness
 created_at: 2026-11-16
 tags: [测试]
---
## 适用场景
%s
## 判据（必填，三档选一）
- 仅参考：测试用，本机依赖不存在，验不了
"""


def seed(d, n=2):
    for i in range(1, n + 1):
        w(os.path.join(d, "card%d.md" % i), CARD % (i, "卡" + str(i), "场景" + str(i)))
    w(os.path.join(d, "_index.md"), "# 派生索引\n")
    w(os.path.join(d, "匹配索引.json"), "{}")
    w(os.path.join(d, "时效台账.json"), "{}")
    w(os.path.join(d, "状态变更史.jsonl"), '{"a":1}\n')


def main():
    Z = mk("syncZ_")
    A = mk("syncA_"); B = mk("syncB_")
    seed(A); seed(B)

    # A1 pack 范围：卡与账进、派生件不进、MANIFEST 不自我收录
    za = os.path.join(Z, "a.zip")
    rc, out = run(A, ["pack", za])
    check("A1 pack 成功", rc == 0, "exit=%d" % rc)
    with zipfile.ZipFile(za) as z:
        names = set(z.namelist())
        man = json.loads(z.read("MANIFEST.json").decode("utf-8"))["files"]
    check("A1 收录卡与变更史", {"card1.md", "card2.md", "状态变更史.jsonl"} <= names, sorted(names))
    check("A1 派生件不进包", not ({"_index.md", "匹配索引.json", "时效台账.json"} & names), sorted(names))
    check("A1 MANIFEST 不收自己", "MANIFEST.json" not in man)

    # A2 自己和自己比：全同，exit 0
    rc, out = run(A, ["diff", za])
    check("A2 自比全同", rc == 0 and "可写 0" in out and "冲突 0" in out, out.strip().splitlines()[-2])

    # A3 对端改了卡2，本机没动；首次无基线 → 默认按冲突处理（不猜谁对）
    w(os.path.join(B, "card2.md"), CARD % (2, "卡2", "场景2-B侧改过") + "\n- B 侧追加一行\n")
    zb = os.path.join(Z, "b.zip")
    run(B, ["pack", zb])
    rc, out = run(A, ["diff", zb])
    check("A3 首次对齐判冲突", rc == 1 and "冲突 1" in out, out.strip().splitlines()[-2])

    # A4 人显式选边 --theirs（包赢）→ 真落盘，字节与包内一致
    src = sha16(os.path.join(B, "card2.md"))
    rc, out = run(A, ["merge", zb, "--apply", "--theirs"])
    check("A4 选边落盘 exit=0", rc == 0, "exit=%d" % rc)
    check("A4 本地卡2 与包内字节相同", sha16(os.path.join(A, "card2.md")) == src)

    # A5 幂等：再跑一次全同（此时基线被刷成"双方一致"的那一版）
    rc, out = run(A, ["merge", zb, "--apply"])
    check("A5 幂等（第二次全同）", rc == 0 and "写 0" in out and "冲突 0" in out, out.strip().splitlines()[-1])

    # A5b 基线每件哈希都等于本地实物（全同之后成立；"留本机"的件故意不刷基线，那是设计）
    bl = json.load(open(os.path.join(A, ".sync_baseline.json"), encoding="utf-8"))["files"]
    bad = [k for k, v in bl.items() if os.path.exists(os.path.join(A, k))
           and sha16(os.path.join(A, k)) != v]
    check("A5b 基线哈希=本地实物", not bad and len(bl) >= 3, "不符 %s / 共 %d 件" % (bad, len(bl)))

    # A6 有基线后，两边都改同一张卡 → 冲突，不落盘
    w(os.path.join(A, "card1.md"), CARD % (1, "卡1", "A 侧改过") + "\n- A 侧追加\n")
    w(os.path.join(B, "card1.md"), CARD % (1, "卡1", "B 侧改过") + "\n- B 侧追加\n")
    zb2 = os.path.join(Z, "b2.zip")
    run(B, ["pack", zb2])
    a1 = sha16(os.path.join(A, "card1.md"))
    rc, out = run(A, ["merge", zb2, "--apply"])
    check("A6 两边都改 → exit=1", rc == 1, "exit=%d" % rc)
    check("A6 冲突件本地未变", sha16(os.path.join(A, "card1.md")) == a1)
    check("A6 输出点明冲突件", "card1.md" in out)

    # A7 --ours（本机赢）：同一批冲突件保持本机内容，且不再报冲突
    rc, out = run(A, ["merge", zb2, "--apply", "--ours"])
    check("A7 本机赢 exit=0", rc == 0 and "冲突 0" in out, out.strip().splitlines()[-1])
    check("A7 本机赢不改文件", sha16(os.path.join(A, "card1.md")) == a1)

    # A8 预演（不带 --apply）不写盘
    w(os.path.join(B, "card2.md"), CARD % (2, "卡2", "B 第三次改"))
    zb3 = os.path.join(Z, "b3.zip")
    run(B, ["pack", zb3])
    a2 = sha16(os.path.join(A, "card2.md"))
    rc, out = run(A, ["merge", zb3])
    check("A8 预演不写盘", sha16(os.path.join(A, "card2.md")) == a2, out.strip().splitlines()[-1])
    check("A8 预演报可写", "可写 1" in out or "写 1" in out, out.strip().splitlines()[-2:-1])

    # A9 坏包
    bad = os.path.join(Z, "bad.zip")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("x.md", "no manifest")
    rc, out = run(A, ["diff", bad])
    check("A9 坏包 exit=1", rc == 1 and "MANIFEST" in out, out.strip()[:50])

    # A10 越界路径不许写，库外不得生成文件
    evil = os.path.join(Z, "evil.zip")
    ename = "evil_" + os.path.basename(A) + ".md"
    outside = os.path.join(os.path.dirname(A), ename)
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("MANIFEST.json", json.dumps(
            {"kind": "shelf", "files": {"../" + ename: {"sha16": "0" * 16}}}))
        z.writestr("../" + ename, "should not land")
    rc, out = run(A, ["merge", evil, "--apply"])
    check("A10 越界路径被拒 exit=1", rc == 1 and "越界" in out, out.strip()[:60])
    check("A10 库外未生成文件", not os.path.exists(outside))

    # A11 被拒绝的越界路径不许留在基线里（否则下次比对会拿一条库外记录吓人）
    bl2 = json.load(open(os.path.join(A, ".sync_baseline.json"), encoding="utf-8"))["files"]
    check("A11 基线不含越界键", not any(k.startswith("../") or ".." in k.split("/") for k in bl2), list(bl2)[:4])

    # A12 子目录（快照库）能落地
    w(os.path.join(B, ".state_snapshots", "甲节", "abcd1234.md"), "- 快照正文\n")
    zb4 = os.path.join(Z, "b4.zip")
    run(B, ["pack", zb4])
    run(A, ["merge", zb4, "--apply", "--theirs"])
    check("A12 子目录文件落地",
          os.path.exists(os.path.join(A, ".state_snapshots", "甲节", "abcd1234.md")))

    # A13 本机独有、包里没有 → 不删
    w(os.path.join(A, "onlyA.md"), CARD % (9, "A独有", "场景9"))
    rc, out = run(A, ["merge", zb4, "--apply"])
    check("A13 本机独有文件不被删", os.path.exists(os.path.join(A, "onlyA.md")))

    # A14 只增不改的日志按行并集：两边各记各的都要在，一条不丢
    ONLY = '{"ts": "%s", "host": "%s", "act": "%s"}\n'
    w(os.path.join(A, "状态变更史.jsonl"), ONLY % ("2026-11-16T10:00:00", "A", "a1"))
    w(os.path.join(B, "状态变更史.jsonl"), ONLY % ("2026-11-16T09:00:00", "B", "b1"))
    zb5 = os.path.join(Z, "b5.zip")
    run(B, ["pack", zb5])
    rc, out = run(A, ["merge", zb5, "--apply", "--theirs"])
    txt = open(os.path.join(A, "状态变更史.jsonl"), encoding="utf-8").read()
    check("A14 并集含双方记录", "a1" in txt and "b1" in txt, txt.strip().replace("\n", " | ")[:80])
    check("A14 并集按时间排", txt.index("09:00") < txt.index("10:00"), "B 的早记录在前")
    check("A14 不做整件冲突", "冲突" not in out or "冲突 0" in out, out.strip().splitlines()[-1][:60])

    # A15 并集幂等：同一包再并一次，行数不变
    before = len([x for x in txt.split("\n") if x.strip()])
    rc, out = run(A, ["merge", zb5, "--apply", "--theirs"])
    after = len([x for x in open(os.path.join(A, "状态变更史.jsonl"), encoding="utf-8").read().split("\n") if x.strip()])
    check("A15 并集幂等不重复追加", before == after, "%d -> %d" % (before, after))
    rc, out = run(A, ["diff", zb5])
    check("A15b 并过之后预演显示全同", "并入 0" in out and "冲突 0" in out, out.strip().splitlines()[-1][:60])

    # A16 派生件/治理件不进同步范围：打包不带、包里带了也忽略
    w(os.path.join(B, "时效台账.md"), "派生件\n")
    w(os.path.join(B, "探针批准.json"), '{"x": 1}\n')
    zb6 = os.path.join(Z, "b6.zip")
    run(B, ["pack", zb6])
    with zipfile.ZipFile(zb6) as z:
        names = z.namelist()
    check("A16 派生件与治理件不进包",
          "时效台账.md" not in names and "探针批准.json" not in names, names[:3])

    print("")
    if FAIL:
        print("未按预期 %d 项：" % len(FAIL))
        for f in FAIL:
            print("   " + f)
        return 1
    print("结论：同步用例全过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for d in DIRS:
            shutil.rmtree(d, ignore_errors=True)
