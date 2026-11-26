# -*- coding: utf-8 -*-
"""deploy_check 的负向用例（随包走，跨平台）。

每个用例用独立副本目录——共用目录会让上一个用例复制进去的文件留在下一个用例里，
那是测试脚手架自己的假绿/假红，不是被测件的行为。

用法：
    python test_deploy_check.py            # 查 EXP_ROOT 或默认库
    python test_deploy_check.py --root <库>
退出码：0 全过 / 1 有用例未按预期
"""
import io, json, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "deploy_check.py")
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
MAN = "部署清单.json"


def root_of(argv):
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            return os.path.realpath(argv[i + 1])
    return os.path.realpath(os.environ.get("EXP_ROOT", DEFAULT_ROOT))


ALIAS = {"_index.md": ("INDEX.md",), "INDEX.md": ("_index.md",),
         "匹配索引.json": ("INDEX_MATCH.json", "_match_index.json"),
         "时效台账.json": ("_时效台账.json",)}


def src_of(exp, n):
    """批准表在默认库上取技能目录那份（跟着被批准的库走），副本要照这个口径装。

    库根找不到还要往 state/ experience/ tools/ 找：v6.5 包解开是分区布局，
    只认库根会让所有用例都装配成"缺必需件"，B/C 的通过就成了同一个原因的假绿。
    """
    for name in (n,) + ALIAS.get(n, ()):
        for d in ("", "state", "experience", "tools"):
            p = os.path.join(exp, d, name) if d else os.path.join(exp, name)
            if os.path.exists(p):
                return p
    alt = os.path.join(HERE, n)
    if os.path.exists(alt):
        return alt
    return None


def build(exp, d, need, drop=(), truncate=()):
    os.makedirs(d, exist_ok=True)
    for n in need:
        if n in drop:
            continue
        src = src_of(exp, n)
        if not src:
            continue
        if n in truncate:
            io.open(os.path.join(d, n), "w", encoding="utf-8").write("{}\n")
        else:
            shutil.copy2(src, os.path.join(d, n))
    return d


def run(d):
    env = dict(os.environ)
    env["EXP_ROOT"] = d
    r = subprocess.run([sys.executable, CHECK], capture_output=True, text=True,
                       env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "")


def run_plain():
    """不设 EXP_ROOT、不传 --root：专门测"默认根反推"这一条路。

    这条路长期是红的：主程序把默认根写死成 ~/.doubao/...，而本机真库在
    AppData 下，于是它扫一个不存在的目录，清单读不到，再拿
    root == DEFAULT_ROOT 判"这是生产库"（两者都是同一个旧路径，必然相等），
    对着一个明明装着清单的真库报"缺清单" exit 5。
    负向用例全是显式装 EXP_ROOT 跑的，所以这条路从来没被覆盖过。
    """
    env = dict(os.environ)
    env.pop("EXP_ROOT", None)
    r = subprocess.run([sys.executable, CHECK], capture_output=True, text=True,
                       env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "")


def main(argv):
    exp = root_of(argv)
    man = os.path.join(exp, MAN)
    if not os.path.exists(man):
        print("库里没有 %s，先造一份再测（%s）" % (MAN, exp))
        return 2
    items = json.load(io.open(man, encoding="utf-8"))["items"]
    need = [it["path"] for it in items] + [MAN]

    cases = [
        ("A 正常库", (), (), 0),
        ("B 缺批准表（D78）", ("探针批准.json",), (), 5),
        ("C 批准表为空（D78 变体）", (), ("探针批准.json",), 5),
        ("D 缺时效台账.json（D79）", ("时效台账.json",), (), 0),
        ("E 批准表内容被改", (), (), 5),
        ("F 缺流水（required）", ("发版流水_202611.md",), (), 5),
    ]

    tmp = tempfile.mkdtemp(prefix="dlvchk_")
    ok = True
    try:
        for i, (title, drop, trunc, want) in enumerate(cases):
            d = build(exp, os.path.join(tmp, "case%d" % i), need, drop=drop, truncate=trunc)
            if title.startswith("E"):
                p = os.path.join(d, "探针批准.json")
                s = io.open(p, encoding="utf-8").read()
                # 不能写死 "count": 42——批准条数随探针增删而变（真库已 47 条），
                # 写死就等于这个用例只在某一天有效，之后替换匹配不上、篡改根本没造出来，
                # 用例年年假绿（实测：改成 43 没生效，工具报 exit 0，用例 FAIL 却不指向真因）。
                # 改成"按实际条数 +1"，哪台机器、多少条都能造出篡改。
                m = re.search(r'"count"\s*:\s*(\d+)', s)
                if m:
                    s = s[:m.start(1)] + str(int(m.group(1)) + 1) + s[m.end(1):]
                io.open(p, "w", encoding="utf-8").write(s)
            rc, out = run(d)
            hit = (rc == want)
            ok = ok and hit
            print("[%s] exit=%d 期望=%d  %s" % ("PASS" if hit else "FAIL", rc, want, title))
            for line in out.splitlines():
                if any(k in line for k in ("✗", "NOT READY", "待生成")):
                    print("      " + line.strip())
        # G 默认根反推：不设 EXP_ROOT，工具必须自己落到真库并报齐备。
        # 默认根确实落在真库（有清单）时才判，否则跳过——别在别的机器上假红。
        if os.path.exists(os.path.join(DEFAULT_ROOT, MAN)):
            rc, out = run_plain()
            hit = (rc == 0)
            ok = ok and hit
            print("[%s] exit=%d 期望=0  %s" % ("PASS" if hit else "FAIL", rc,
                                              "G 不带 --root 落真库（默认根反推）"))
            for line in out.splitlines():
                if any(k in line for k in ("✗", "NOT READY")):
                    print("      " + line.strip())
        else:
            print("[SKIP] 默认根没有 %s，跳过 G（%s）" % (MAN, DEFAULT_ROOT))

        print("\n结论：%s" % ("负向用例全过" if ok else "有用例未按预期，需修"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
