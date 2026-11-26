# -*- coding: utf-8 -*-
"""任务清单按行写的用例（随包走，跨平台）。

和状态层一样：断言由本脚本自己算每行哈希比对，不采信工具的自述。

用法：python test_task_row.py [--root <库>]
退出码：0 全过 / 1 未按预期
"""
import hashlib, io, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RECALL = os.path.join(HERE, "recall.py")
def _guess_root():
    """默认库根：多候选逐个试。

    原来写死 "~/.doubao/agent_mode/workspace/experience"。本机真库在
    AppData/Local/Doubao/User Data/Default/.doubao/... 下，于是不带 --root 跑，
    回归件一律报"库里没有 current-work-state.md" exit 2——默认状态就是红的，
    看起来像"这条命令有问题"，其实是找错了地方（本轮实测亲历）。
    候选顺序与主程序一致：技能目录反推 → AppData 形态 → 旧路径。
    """
    cands = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cands.append(os.path.join(os.path.dirname(os.path.dirname(here)), 'experience'))
    except Exception:
        pass
    cands.append(os.path.expanduser("~/AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/experience"))
    cands.append(os.path.expanduser("~/.doubao/agent_mode/workspace/experience"))
    for c in cands:
        if os.path.exists(os.path.join(c, 'current-work-state.md')):
            return os.path.realpath(c)
    return os.path.realpath(cands[0] if cands else 'experience')


DEFAULT_ROOT = _guess_root()
TASK = "任务清单_20261113.md"


def root_of(argv):
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            return os.path.realpath(argv[i + 1])
    return os.path.realpath(os.environ.get("EXP_ROOT", DEFAULT_ROOT))


def rows(text):
    """数据行（排除分隔行与表头——表头下一行是分隔行）。"""
    lines = text.split("\n")
    out = []

    def sep(l):
        return l.startswith("|") and set(l.replace("|", "").strip()) <= set("-: ")

    for n, l in enumerate(lines):
        if not l.startswith("|") or sep(l):
            continue
        if n + 1 < len(lines) and sep(lines[n + 1]):
            continue
        out.append((n, l.split("|")[1].strip(), l))
    return out


def hs(text):
    return {n: hashlib.sha256(l.encode("utf-8")).hexdigest() for n, l in enumerate(text.split("\n"))}


def run(root, args, stdin=None):
    env = dict(os.environ); env["EXP_ROOT"] = root
    r = subprocess.run([sys.executable, RECALL, "task"] + args, capture_output=True,
                       text=True, env=env, encoding="utf-8", errors="replace",
                       input=stdin if stdin is not None else "")
    return r.returncode, (r.stdout or "") + (r.stderr or "")



def src_of(exp, n):
    """库根找不到就往 state/ experience/ tools/ 找：包解开是分区布局。"""
    for d in ("", "state", "experience", "tools"):
        p = os.path.join(exp, d, n) if d else os.path.join(exp, n)
        if os.path.exists(p):
            return p
    return None

def mk(exp):
    d = tempfile.mkdtemp(prefix="taskrow_")
    src = src_of(exp, TASK)
    if not os.path.exists(src):
        shutil.rmtree(d, ignore_errors=True); return None
    shutil.copy2(src, os.path.join(d, TASK)); return d


def read(d):
    return io.open(os.path.join(d, TASK), encoding="utf-8", newline="").read()


def main(argv):
    exp = root_of(argv)
    if not src_of(exp, TASK):
        print("库里没有 %s" % TASK); return 2
    ok = True; dirs = []

    def check(name, cond, note=""):
        nonlocal ok
        ok = ok and cond
        print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  " + note) if note else ""))

    try:
        base = read(mk(exp)) if mk(exp) else ""
        rs = rows(base)
        if len(rs) < 3:
            print("清单行太少，测不了"); return 2
        key1, key2 = rs[0][1], rs[1][1]
        line1 = rs[0][2]

        # A 改一行：只有这一行变
        d = mk(exp); dirs.append(d)
        t0 = read(d); h0 = hs(t0)
        newrow = line1.replace("|", "|", 1).rstrip("|") + " 已改 |" if line1.rstrip().endswith("|") else line1
        # 造一条列数一致、内容明显不同的行
        cells = line1.strip().strip("|").split("|")
        cells[-1] = " 测试改写 "
        newrow = "|" + "|".join(cells) + "|"
        rc, out = run(d, ["--row", key1, "--write", "-"], stdin=newrow + "\n")
        t1 = read(d); h1 = hs(t1)
        drift = [n for n in h0 if h0[n] != h1.get(n)]
        check("A1 改一行 → exit 0", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
        check("A2 只有目标行变", drift == [rs[0][0]], "变了的行：" + str(drift))
        check("A3 行数不变", len(t0.split("\n")) == len(t1.split("\n")))
        check("A4 改的那行内容确实换了", "测试改写" in dict((n, l) for n, _, l in rows(t1)).get(rs[0][0], ""))

        # B 两行各改各处：串行执行，两处都在
        d = mk(exp); dirs.append(d)
        t0 = read(d)
        r1, o1 = run(d, ["--row", key1, "--write", "-"], stdin="|" + "|".join([" " + key1 + " ", " 甲改 ", " ✅ ", " 甲 "]) + "|\n")
        r2, o2 = run(d, ["--row", key2, "--write", "-"], stdin="|" + "|".join([" " + key2 + " ", " 乙改 ", " ✅ ", " 乙 "]) + "|\n")
        tB = read(d)
        check("B1/B2 两窗口各改一行都成功", r1 == 0 and r2 == 0, o1.strip()[-40:] + " | " + o2.strip()[-40:])
        g = dict((k, l) for _, k, l in rows(tB))
        check("B3 甲改的还在", "甲改" in g.get(key1, ""))
        check("B4 乙改的还在", "乙改" in g.get(key2, ""))

        # C 列数不一致：拒绝（否则表格被写坏）
        d = mk(exp); dirs.append(d)
        t0 = read(d)
        rc, out = run(d, ["--row", key1, "--write", "-"], stdin="| 少一列 |\n")
        check("C 列数不一致 → exit 2", rc == 2, out.strip().splitlines()[0] if out.strip() else "")
        check("C 文件未动", read(d) == t0)

        # D 不存在的编号：拒绝
        d = mk(exp); dirs.append(d)
        t0 = read(d)
        rc, out = run(d, ["--row", "ZZ999", "--write", "-"], stdin="| x | y | z | w |\n")
        check("D 不存在编号 → exit 2", rc == 2)
        check("D 文件未动", read(d) == t0)

        # E 追加一行：原有行全部原样，新行在末尾（列数按表里最后一行的实际结构造）
        d = mk(exp); dirs.append(d)
        t0 = read(d); h0 = hs(t0)
        rr = rows(t0); ref = rr[-1][2]
        ncol = ref.count("|") - 1
        cellsE = [" Z9 ", " 追加测试 ", " ⏳ ", " 新行也在表里 ", " 备 "]
        newE = "|" + "|".join(cellsE[:ncol]) + "|\n"
        rc, out = run(d, ["--append", "Z9", "--write", "-"], stdin=newE)
        tE = read(d); hE = hs(tE)
        shifted = [n for n in h0 if n > rr[-1][0]]
        check("E1 追加 → exit 0", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
        check("E2 新编号出现在表里", "Z9" in dict((k, l) for _, k, l in rows(tE)))
        check("E3 原有行逐行不变（含序号未错位）",
              all(h0[n] == hE.get(n) for n in h0 if n <= rr[-1][0]))

        # F 重复编号追加：拒绝
        d = mk(exp); dirs.append(d)
        t0 = read(d)
        rc, out = run(d, ["--append", key1, "--write", "-"],
                      stdin="|" + "|".join([" " + key1 + " ", " 重号 ", " ⏳ ", " x "]) + "|\n")
        check("F 重号追加 → exit 2", rc == 2, out.strip().splitlines()[0] if out.strip() else "")
        check("F 文件未动", read(d) == t0)

        print("\n结论：%s" % ("按行写全过" if ok else "有用例未按预期，需修"))
        return 0 if ok else 1
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
