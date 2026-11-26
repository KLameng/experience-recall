# -*- coding: utf-8 -*-
"""状态层分块写的负向与并发用例（随包走，跨平台）。

每个用例用独立副本目录。断言不是读工具的自述，而是由本脚本自己算每节 sha8 比对——
工具说"其他节一字未动"不算证据，文件里每一节的哈希才算。

用法：
    python test_state_section.py                 # 用 EXP_ROOT 或默认库
    python test_state_section.py --root <库>
退出码：0 全过 / 1 有用例未按预期
"""
import hashlib, io, os, re, shutil, subprocess, sys, tempfile

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
STATE = "current-work-state.md"
SEC_A = "三、下一步（未闭环）"
SEC_B = "一、在办（原文）"


def root_of(argv):
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            return os.path.realpath(argv[i + 1])
    return os.path.realpath(os.environ.get("EXP_ROOT", DEFAULT_ROOT))


def secs(text):
    lines = text.split("\n")
    idx = [i for i, l in enumerate(lines) if l.startswith("## ")]
    out = []
    for k, i in enumerate(idx):
        e = idx[k + 1] if k + 1 < len(idx) else len(lines)
        out.append((lines[i][3:].strip(), "\n".join(lines[i + 1:e])))
    return out


def shas(text):
    return {t: hashlib.sha256(b.encode("utf-8")).hexdigest() for t, b in secs(text)}


def sh8(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]


def run(root, args, stdin=None):
    env = dict(os.environ)
    env["EXP_ROOT"] = root
    r = subprocess.run([sys.executable, RECALL, "state"] + args, capture_output=True,
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

def mk(exp, tag):
    d = tempfile.mkdtemp(prefix="stsec_" + tag + "_")
    src = src_of(exp, STATE)
    if not os.path.exists(src):
        shutil.rmtree(d, ignore_errors=True)
        return None
    shutil.copy2(src, os.path.join(d, STATE))
    return d


def read(root):
    return io.open(os.path.join(root, STATE), encoding="utf-8", newline="").read()


def main(argv):
    exp = root_of(argv)
    if not src_of(exp, STATE):
        print("库里没有 %s" % STATE); return 2
    ok = True
    dirs = []

    def check(name, cond, note=""):
        nonlocal ok
        ok = ok and cond
        print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  " + note) if note else ""))

    try:
        # A 正常替换：别的小节必须逐节不变
        d = mk(exp, "A"); dirs.append(d)
        t0 = read(d); before = shas(t0)
        body = shas(t0)[SEC_A] if SEC_A in before else None
        if body is None:
            cand = [t for t in before if t.startswith("三、")]
            SEC_A_LOCAL = cand[0] if cand else list(before)[-1]
        else:
            SEC_A_LOCAL = SEC_A
        rc, out = run(d, ["--section", SEC_A_LOCAL, "--write", "-"], stdin="本节由测试改写。\n")
        t1 = read(d); after = shas(t1)
        changed = [k for k in after if before.get(k) != after[k]]
        check("A 正常替换一节：exit 0", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
        check("A 只有目标节变，其他 %d 节逐字节不变" % (len(before) - 1),
              changed == [SEC_A_LOCAL], "实际变了的：" + (("、".join(changed)) or "无"))
        check("A 节数没有增减", len(before) == len(after), "%d → %d" % (len(before), len(after)))

        # B 节级乐观锁：对的 sha8 能写，错的停手且文件不动
        d = mk(exp, "B"); dirs.append(d)
        t0 = read(d); cur = sh8(dict(secs(t0))[SEC_A_LOCAL])
        rc1, o1 = run(d, ["--section", SEC_A_LOCAL, "--write", "-", "--expect-sha8", cur], stdin="锁对，写入。\n")
        check("B1 --expect-sha8 命中 → exit 0", rc1 == 0)
        t1 = read(d); new8 = sh8(dict(secs(t1))[SEC_A_LOCAL])
        rc2, o2 = run(d, ["--section", SEC_A_LOCAL, "--write", "-", "--expect-sha8", cur], stdin="锁错，不该写进去。\n")
        t2 = read(d)
        check("B2 --expect-sha8 不符 → exit 3", rc2 == 3, o2.strip().splitlines()[0] if o2.strip() else "")
        check("B3 被拒绝后文件一字未动", t1 == t2)
        m = re.search(r"sha8 ([0-9a-f]{8}) \u2192 ([0-9a-f]{8})", o1)
        check("B4 打印的新 sha8 = 落盘后该节 sha8（差一个空行也算错）",
              bool(m) and m.group(2) == new8,
              "打印的是 %s，实际是 %s" % (m.group(2) if m else "无", new8))

        # C 小节不存在：拒绝，不猜
        d = mk(exp, "C"); dirs.append(d)
        t0 = read(d)
        rc, out = run(d, ["--section", "这一节不存在", "--write", "-"], stdin="x\n")
        check("C 不存在的节 → exit 2", rc == 2)
        check("C 文件未动", read(d) == t0)
        check("C 报出了可用小节", "可用小节" in out)

        # D 模糊匹配到多节：拒绝，不替人挑
        d = mk(exp, "D"); dirs.append(d)
        t0 = read(d)
        rc, out = run(d, ["--section", "、", "--write", "-"], stdin="x\n")
        check("D 模糊匹配多节 → exit 2", rc == 2, out.strip().splitlines()[0] if out.strip() else "")
        check("D 文件未动", read(d) == t0)

        # E 两个"窗口"各改自己那节：串行执行，两处都要活下来
        d = mk(exp, "E"); dirs.append(d)
        t0 = read(d)
        allsec = list(shas(t0))
        sa = SEC_A_LOCAL
        sb = SEC_B if SEC_B in allsec else allsec[0]
        rb, ob = run(d, ["--section", sb, "--write", "-"], stdin="窗口B写到这一节。\n")
        ra, oa = run(d, ["--section", sa, "--write", "-"], stdin="窗口A写到那一节。\n")
        tE = read(d)
        check("E1 窗口B 写成功", rb == 0)
        check("E2 窗口A 写成功", ra == 0)
        check("E3 B 写的还在", "窗口B写到这一节" in dict(secs(tE)).get(sb, ""))
        check("E4 A 写的也在", "窗口A写到那一节" in dict(secs(tE)).get(sa, ""))
        o0 = {k: v for k, v in shas(t0).items() if k not in (sa, sb)}
        oE = {k: v for k, v in shas(tE).items() if k not in (sa, sb)}
        check("E5 除这两节外，其余 %d 节逐字节仍原样" % len(o0), o0 == oE,
              "变了的：" + ("、".join([k for k in o0 if o0[k] != oE.get(k)])) if o0 != oE else "")

        # F 对照：整文件覆盖会把别人那一节冲掉（这正是要防的事）
        d = mk(exp, "F"); dirs.append(d)
        t0 = read(d)
        with io.open(os.path.join(d, STATE), "w", encoding="utf-8", newline="") as f:
            f.write("整个文件被覆盖成新内容\n")
        check("F 对照组：整文件覆盖确实会冲掉所有节（说明为什么需要它）",
              len(secs(read(d))) == 0)

        print("\n结论：%s" % ("分块写全过" if ok else "有用例未按预期，需修"))
        return 0 if ok else 1
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
