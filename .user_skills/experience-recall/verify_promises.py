#!/usr/bin/env python3
"""承诺逐条实测：把版本能力总账里"承诺过的能力"一条条拿现包跑一遍。

原则：
- 只读命令直接跑；写命令一律在库的**临时副本**里跑（`cp -rL`，源是软链，-r 会保留软链导致隔离是假的）。
- 能实测的才标"实测"；只能静态断言的标"静态"；纯人工守的标"仅文档"。
- 任何一条不成立就记 FAIL，不四舍五入成"大体符合"。

用法：python3 verify_promises.py [--json]
"""
import os, sys, json, shutil, subprocess, tempfile, re

SK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recall.py')
ENV0 = dict(os.environ)


def _guess_root():
    """默认库根：多候选逐个试（与 recall.py / deploy_check.py / ledger_check.py 同口径）。

    原先写死 "~/.doubao/agent_mode/workspace/experience"。本机真库在 AppData 下，
    于是不带 EXP_ROOT 跑，下面 copytree 直接 FileNotFoundError 崩掉——
    "承诺验收单"承诺的是"32 个命令逐条真跑"，它自己先跑不起来。
    这和 D137（三份测试找错库）、D140（deploy_check 找错库）是同一处写死。
    """
    cands = []
    # 顺序：真实库（~/experience）优先。镜像里的"技能同级库"是易失的，
    # 容器一重置就退回旧库，拿它跑 gate 会假红（2026-11-18 实测：/runtime/experience 又被重置）。
    cands.append(os.path.expanduser('~/experience'))
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cands.append(os.path.join(os.path.dirname(os.path.dirname(here)), 'experience'))
    except Exception:
        pass
    cands.append(os.path.expanduser(
        "~/AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/experience"))
    cands.append(os.path.expanduser("~/.doubao/agent_mode/workspace/experience"))
    for c in cands:
        if os.path.exists(os.path.join(c, 'current-work-state.md')):
            return os.path.realpath(c)
    return os.path.realpath(cands[0] if cands else 'experience')


def _run(args, root, extra_env=None, timeout=180, inp=None):
    """跑一条命令。默认 180 秒，不是 60。

    实测：云主机上 `verify` 要 64 秒（245 张卡 + 时效台账），60 秒就被砍，
    然后整张验收单抛 TimeoutExpired 崩掉——后面几十条用例一条都没跑。
    外面看到的是"验收单崩了"，而不是"第三条命令慢"。
    一条慢命令不该把整张单子带走：超时只判这一条失败，继续往下跑。
    """
    env = dict(ENV0)
    env['EXP_ROOT'] = root
    env.pop('EXPRECALL_PARENT_ROOT', None)
    env['EXPRECALL_DEPTH'] = '0'
    if extra_env:
        env.update(extra_env)
    try:
        r = subprocess.run([sys.executable, SK] + args, env=env, capture_output=True,
                           text=True, timeout=timeout, cwd=root, input=inp)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except subprocess.TimeoutExpired:
        return 124, '超时（>%d 秒）：%s' % (timeout, ' '.join(args))


def _card(root, pick='dual-loader-divergence'):
    p = os.path.join(root, pick + '.md')
    return pick if os.path.exists(p) else None


def main():
    src = os.path.realpath(os.environ.get('EXP_ROOT') or _guess_root())
    # 库根不存在要说清楚，而不是让 copytree 甩一段 FileNotFoundError——
    # 那是"路径没找对"，长得却像"工具坏了"，最容易被误读成代码有 bug。
    if not os.path.isdir(src):
        print('库根不存在：%s\n  修法：设 EXP_ROOT=<库根>，或确认工具目录与库目录并排摆放' % src)
        return 2
    tmp = tempfile.mkdtemp(prefix='exprecall_promise_')
    root = os.path.join(tmp, 'e')
    shutil.copytree(src, root, symlinks=False)   # 关键：解引用软链，否则"隔离是假的"
    res = []

    def add(pid, what, ver, how, got, ok, note=''):
        res.append({'id': pid, 'what': what, 'ver': ver, 'how': how,
                    'got': got, 'ok': ok, 'note': note})

    card = _card(root)

    # ---------- A. 32 个命令：真跑 ----------
    READ = [('stats', ['stats']), ('state', ['state']), ('list', ['list']), ('gaps', ['gaps']),
            ('due', ['due']), ('verify', ['verify']), ('lint', ['lint']), ('judge', ['judge']),
            ('mech', ['mech']), ('conflicts', ['conflicts']), ('activity', ['activity']),
            ('probelog', ['probelog']), ('plan', ['plan']), ('repro', ['repro']),
            ('estate', ['estate']), ('build_index', ['build_index']), ('export', ['export']),
            ('start', ['start']), ('search', ['search', '探针']), ('card', ['card', card or 'stats']),
            ('flow', ['flow', '']), ('match', ['match', '探针临时目录不回收']),
            ('recur', ['recur', card or 'stats']), ('scan', ['scan']), ('scan-test', ['scan', '--self-test'])]
    for pid, args in READ:
        if pid in ('flow',):
            rc, out = _run(['flow'], root)
        else:
            rc, out = _run(args, root)
        ok = rc == 0 and len(out.strip()) > 0
        add('CMD-' + pid, '`' + ' '.join(a for a in args if a) + '`', '-', '实测',
            'exit ' + str(rc) + ' · ' + str(len(out.split(chr(10))) ) + ' 行', ok,
            '' if ok else out.strip()[-120:])

    # gate：实跑一次（10 秒级），放在写命令之前——这一刻副本还跟真库一致。
    # 两个坑都实测了：
    #   ① 判据原先写 rc == 0。本机没有 <PKG>（包在云盘、不留本地副本），必有 4 条
    #      缺物料，闸按口径 exit 7——这条用例于是年年假红，红了也没人看。
    #   ② 原先位置在写命令之后。前面 new 造的那张"没判据的卡"还没归档干净，
    #      闸把它算成业务失败，真红 exit 1，看起来像"闸坏了"。
    # 现在判据 = 闸自己说 GATE PASS，且退出码属于不阻断集合（0 全通 / 7 缺物料+平台不适用）。
    rc, out = _run(['gate'], root, timeout=300)
    ok = ('GATE PASS' in out) and rc in (0, 7)
    add('CMD-gate', '`gate` 全探针通过', 'v2.8', '实测',
        'exit ' + str(rc) + ' · ' + out.strip().split('\n')[-1][:60], ok,
        '' if ok else out.strip()[-160:])

    # 写命令（都在副本里）
    NEW = '承诺实测临时卡'
    rc, out = _run(['new', NEW], root)
    ok = rc == 0 and os.path.exists(os.path.join(root, '承诺实测临时卡.md'))
    add('CMD-new', '`new <标题>` 生成骨架卡', 'v2.1', '实测', 'exit ' + str(rc), ok, '' if ok else out[-100:])
    rc, out = _run(['hit', '承诺实测临时卡'], root)
    ok = rc == 0 and '第' in out
    add('CMD-hit', '`hit <卡>` 追加一次命中', 'v1.8', '实测', 'exit ' + str(rc), ok, out.strip()[:80])
    rc, out = _run(['use', 'worked', '承诺实测临时卡', '承诺实测'], root)
    ok = rc == 0 and os.path.exists(os.path.join(root, '使用台账.jsonl'))
    add('CMD-use', '`use worked|failed|partial <卡>`', 'v4.2', '实测', 'exit ' + str(rc), ok, out.strip()[:80])
    rc, out = _run(['mark', 'active', '承诺实测临时卡'], root)
    ok = rc == 0
    add('CMD-mark', '`mark <状态> <卡>`', 'v4.1', '实测', 'exit ' + str(rc), ok, out.strip()[:60])
    rc, out = _run(['claim', '承诺实测', '承诺实测临时卡'], root)
    ok = rc == 0 or '已有 owner' in out or '跳过' in out
    add('CMD-claim', '`claim <线> <卡>` 写来源线', 'v3.9', '实测', 'exit ' + str(rc), ok, out.strip()[:60])
    rc, out = _run(['archive', '承诺实测临时卡', '--why', '承诺实测用'], root)
    ok = rc == 0 and '未写原因' not in out
    add('CMD-archive', '`archive <卡> --why "<原因>"`', 'v4.3', '实测', 'exit ' + str(rc), ok, out.strip()[:60])
    rc, out = _run(['export'], root)
    ef = os.path.join(root, '导出清单.json')
    rc2, out2 = _run(['diff', '导出清单.json'], root)
    ok = os.path.exists(ef) and rc2 == 0 and 'TypeError' not in out2
    # 再用同步基线（裸 sha 串格式）diff 一次，验"两种清单形态都认"
    rc3, out3 = _run(['diff', '同步基线.json'], root)
    ok = ok and rc3 == 0
    add('CMD-diff', '`diff <导出清单>` 跨机比对', 'v3.9', '实测', 'exit ' + str(rc2), ok, out2.strip()[:60])
    rc, out = _run(['approve', '--yes'], root)
    _run(['build_index'], root)   # 批准后必须重建索引，否则索引里仍是"未批准"，闸会 0 根
    ok = rc == 0 and os.path.exists(os.path.join(root, '探针批准.json'))
    add('CMD-approve', '`approve --yes` 落批准清单', '-', '实测', 'exit ' + str(rc), ok, out.strip()[:60])

    # watch：实测只在副本，越线才 6；这里只验"能跑且四项都报"
    rc, out = _run(['watch'], root)
    four = all(k in out for k in ('inode', '空间', '临时'))
    add('CMD-watch', '`watch` 资源四项', 'v5.4', '实测', 'exit ' + str(rc) + ' · 四项齐=' + str(four),
        rc == 0 and four, out.strip()[:90])

    # reap：不实跑（会杀全局 recall.py 进程，包括本进程的兄弟），改静态断言两阶段顺序
    t = open(SK, encoding='utf-8').read()
    b = t.index('def cmd_reap')
    seg = t[b:b + 2200]
    order_ok = ('SIGTERM' in seg) and ('SIGKILL' in seg) and seg.index('SIGTERM') < seg.index('SIGKILL')
    scene_ok = '事故现场' in seg
    add('CMD-reap', '`reap` 存现场→SIGTERM→SIGKILL 顺序', 'v5.3', '静态',
        'SIGTERM<KILL=' + str(order_ok) + ' · 存现场=' + str(scene_ok),
        order_ok and scene_ok, '实跑会杀全局进程，改静态断言（已在 b85 实测过顺序）')

    # ---------- B. 规则：能机械验的都验 ----------
    # R1 判据三档：造一张没有判据段的卡，lint 必须报
    p = os.path.join(root, '没有判据的卡.md')
    open(p, 'w', encoding='utf-8').write('---\nname: 没有判据的卡\ncreated_at: 2026-11-13\n---\n\n## 适用场景\n测试\n')
    rc, out = _run(['lint'], root)
    ok = '缺段判据' in out or '没有判据的卡' in out
    add('RULE-judge-tiers', '每卡必须有 `## 判据`（三档之一），否则 lint 拦', 'v2.3', '实测',
        'lint 命中=' + str(ok), ok)
    os.remove(p)

    # R2 归档必须写原因：不带 --why 必须拒
    rc, out = _run(['archive', card], root)
    ok = ('拒绝归档' in out) and rc != 0
    add('RULE-archive-why', '归档必须写原因与唤醒条件', 'v4.3/v4.5', '实测', out.strip()[:70], ok)

    # R3 用就写回：幽灵卡必须被拒
    rc, out = _run(['use', 'worked', '根本不存在的卡xyz'], root)
    ok = ('拒绝回写' in out) or ('库里没有' in out)
    add('RULE-use-no-ghost', '回写不许造幽灵卡（库里没有就拒）', 'v4.2', '实测', out.strip()[:70], ok)

    # R4 冲突不选边：跑 conflicts 后不得自动写裁决台账
    jf = os.path.join(root, '裁决台账.md')
    before = os.path.getmtime(jf) if os.path.exists(jf) else 0
    rc, out = _run(['conflicts'], root)
    after = os.path.getmtime(jf) if os.path.exists(jf) else 0
    ok = before == after and '不替你选边' in out
    add('RULE-no-arbitration', '工具不选边：只让冲突可见', 'v3.8', '实测', '裁决台账未被自动改写=' + str(before == after), ok)

    # R5 环境异常不入业务：selftest 必须 exit 5
    st = os.path.join(os.path.dirname(SK), 'selftest_env_class.py')
    r = subprocess.run([sys.executable, st], capture_output=True, text=True, timeout=120)
    ok = r.returncode == 0 and r.stdout.strip().endswith('1')
    add('RULE-env-separated', '环境异常 exit 5，不混入业务通过率', 'v5.4', '实测',
        'selftest exit ' + str(r.returncode), ok)

    # R6 同库嵌套禁止：在副本里嵌套调 gate 必须 exit 3
    rc, out = _run(['gate'], root, extra_env={'EXPRECALL_DEPTH': '1',
                                              'EXPRECALL_PARENT_ROOT': os.path.realpath(root)})
    ok = rc == 3 and '阻断' in out
    add('RULE-nested-block', '同库嵌套 gate 直接 exit 3', 'v5.2', '实测', 'exit ' + str(rc), ok)

    # R7 时间锚逐次：recur 必须能解析 occurrences 并给出次数
    rc, out = _run(['recur', card], root)
    ok = rc == 0 and '共' in out and '次' in out
    add('RULE-anchors', '时间锚逐次 occurrences（可算次数/间隔）', 'v1.9', '实测', out.strip()[:60], ok)

    # R8 闸对账行：实跑 N 根 ≠ 只报通过数
    rc, out = _run(['gate'], root, timeout=300)
    ok = ('实际执行探针' in out) and ('通过' in out)
    add('RULE-gate-reconcile', '闸报"实跑 N 根 / 通过 M 根"对账', 'v5.1', '实测', '对账行在=' + str(ok), ok)

    # R9 静态门禁反向自检
    rc, out = _run(['scan', '--self-test'], root)
    ok = rc == 0 and 'SELFTEST PASS' in out
    add('RULE-scan-selftest', '静态门禁能抓坏代码、放行好代码', 'v5.7', '实测', out.strip()[:70], ok)

    # ---------- C. 只能"静态/文档"的：明说，不冒充实测 ----------
    DOC = [
        ('RULE-layers', '语义层可覆盖 / 情景层只追加', 'v1.9', '情景层=JSONL 只追加、状态层=覆盖 + 状态变更史兜底'),
        ('RULE-accumulate', '积累期只增不减，不许删卡/合并/砍字段', 'v2.1', '纯过程纪律，工具不拦；只能靠人守'),
        ('RULE-provenance', '来源等级写 provenance，回溯标 inferred', 'v1.8', 'lint 未强制该字段，属软约束'),
        ('RULE-single-source', '标准/口径只一处承载', 'v3.8', '属治理纪律，工具不检测"两处各自定义"'),
        ('RULE-lock', '写操作走同一文件锁、tmp+rename', 'v3.8', '静态：`_wlock`/`_atomic_json` 在代码里，未做并发压测'),
        ('RULE-no-delete', '归档≠删除，不能删别人的卡', 'v4.3', '无删除命令，只能 archive/mark，属结构保证'),
    ]
    for pid, what, ver, note in DOC:
        add(pid, what, ver, '仅文档/静态', note, None,
            '不冒充实测：' + note)

    # 清理
    shutil.rmtree(tmp, ignore_errors=True)

    okn = sum(1 for r in res if r['ok'] is True)
    fail = [r for r in res if r['ok'] is False]
    soft = [r for r in res if r['ok'] is None]
    out = {'total': len(res), 'pass': okn, 'fail': len(fail), 'soft': len(soft), 'rows': res}
    if '--json' in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for r in res:
            mark = 'PASS' if r['ok'] is True else ('FAIL' if r['ok'] is False else 'SOFT')
            print('[' + mark + '] ' + r['id'].ljust(22) + r['what'][:52] + ' | ' + r['got'][:60])
        print('\n合计 ' + str(len(res)) + ' 条：PASS ' + str(okn) + ' / FAIL ' + str(len(fail)) + ' / 仅文档 ' + str(len(soft)))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
