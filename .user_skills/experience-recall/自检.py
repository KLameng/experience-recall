# -*- coding: utf-8 -*-
"""经验与记忆管理工具 —— 装好了没有，跑一下就知道。零依赖。

用法：python 自检.py
退出码：0 = 能用；2 = 找不到仓库（摆错位置了）；3 = 仓库在、但账或卡不全
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, 'recall.py')
UP = os.path.dirname(os.path.dirname(HERE))
LIB = os.path.join(UP, 'experience')


def call(args):
    env = {k: v for k, v in os.environ.items() if k != 'EXP_ROOT'}
    p = subprocess.run([sys.executable, MAIN] + args,
                       capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main():
    line = '=' * 62
    print(line)
    print('  经验与记忆管理工具 · 自检')
    print('  （也写作「记忆与经验管理工具」，同一个工具）')
    print(line)

    if not os.path.isfile(MAIN):
        print('  [X] 缺主程序 recall.py，这个包不完整')
        return 2

    print('  程序位置：%s' % HERE)
    print('  应该在这儿找仓库：%s' % LIB)

    rc, out = call(['stats'])
    if rc != 0:
        print('  [X] 主程序跑不起来（退出码 %d）' % rc)
        print('      ' + (out.strip()[-300:] or '(无输出)'))
        print(line)
        print('  大概率是两个文件夹没摆成并排。摆法见同目录的「装法.txt」。')
        return 2
    # stats 会先打两行告警再打数据，取中间的 JSON 块
    s = None
    a, b = out.find('{'), out.rfind('}')
    if a >= 0 and b > a:
        try:
            s = json.loads(out[a:b + 1])
        except Exception:
            s = None
    if s is None:
        print('  [?] 认不出统计输出，把上面这段发给我')
        return 3

    root = s.get('root', '')
    cards = s.get('cards', 0)
    print('  实际用的仓库：%s' % root)
    print('  经验卡：%d 张' % cards)
    ok = True
    # 记忆（过程记录）现在并在本包里，走 mem 入口读
    rc3, out3 = call(['mem', 'stats'])
    m = None
    if rc3 == 0:
        a3, b3 = out3.find('{'), out3.rfind('}')
        if a3 >= 0 and b3 > a3:
            try:
                m = json.loads(out3[a3:b3 + 1])
            except Exception:
                m = None
    if m is None:
        print('  [X] 记忆模块跑不起来（退出码 %d）' % rc3)
        print('      多半是 core/ 或 experience/memory/ 没随包落地')
        ok = False
    else:
        print('  记忆（过程记录）：%s 条，分 %s 个文件' % (m.get('event_records', 0), m.get('event_shards', 0)))
    rc2, out2 = call(['log', 'status'])
    if rc2 == 0:
        hits = [ln.strip() for ln in out2.splitlines() if '块' in ln][:2]
        print('  三本账：读得通')
        for h in hits:
            print('    %s' % h)
    else:
        print('  [X] 账读不出来（退出码 %d）' % rc2)
        ok = False

    # 评价闭环：记了"取回什么"，还得有人回答"管不管用"。
    # 这里只报待评条数，不替人评——评价必须带边界，机器编不出来。
    rc4, out4 = call(['eval', '--stats'])
    if rc4 == 0:
        _ev = [ln.strip() for ln in out4.splitlines() if ln.startswith('使用记录')]
        if _ev:
            print('  ' + _ev[0])
    else:
        print('  [?] 评价台账读不出来（退出码 %d）' % rc4)

    # v6.44 单调判据：库只能长，不能悄悄变短。
    # 这条治的是"假绿"——D158/D160 那次整目录被平台覆盖，50 件没了，
    # 当时的自检照样报"能用"，因为它只看"读得通"，没看"该在的还在不在"。
    basep = os.path.join(root, '.sync_baseline.json')
    if os.path.isfile(basep):
        try:
            bd = json.load(open(basep, encoding='utf-8'))
            expect = bd.get('files', {})
            miss = [rel for rel in expect if not os.path.exists(os.path.join(root, rel))]
            if miss:
                print('  [X] 比上次少 %d 件（该在的不在了）：' % len(miss))
                for x in miss[:6]:
                    print('      %s' % x)
                if len(miss) > 6:
                    print('      …还有 %d 件' % (len(miss) - 6))
                print('      这多半不是"你删的"，而是整目录被覆盖/拷贝不全。')
                print('      处置：按云盘权威包补齐，再跑 guard commit 认一次。')
                ok = False
            else:
                print('  件数：%d 件，与上次一致（没有悄悄少件）' % len(expect))
                # v6.47 双向判据：只防"少"不防"多"，等于给"悄悄加东西"开了后门。
                # 实测 2026-11-18：别的窗口新建 4 张卡（其中 2 张是空骨架），
                # 基线里没有它们，自检照样报"289 件，与上次一致"。
                # 这里只报不判死：新建卡是正当动作，但必须让人看见一次。
                act = set()
                for r_, ds_, fs_ in os.walk(root):
                    ds_[:] = [d for d in ds_ if d not in ('.state_snapshots', '__pycache__', '.guard')]
                    for f_ in fs_:
                        # 与 _lib_scan（基线扫描）同口径：待裁决件/临时件不算库内容。
                        # 两处口径不齐的代价很具体：guard 自己的 .guard_pending.json 在
                        # 这里会被判成"多出 1 件"，而在基线里根本没有它——左右互打。
                        if f_ == '.guard_pending.json' or f_.endswith('.tmp') or '.conflict.' in f_:
                            continue
                        act.add(os.path.relpath(os.path.join(r_, f_), root).replace(os.sep, '/'))
                extra = sorted(act - set(expect))
                if extra:
                    print('  [!] 多出 %d 件（不在基线里，得判该不该）：' % len(extra))
                    for x in extra[:8]:
                        print('      + %s' % x)
                    if len(extra) > 8:
                        print('      …还有 %d 件' % (len(extra) - 8))
                    print('      该变就 guard commit 认一次；不该变就撤掉。')
        except Exception as e:
            print('  [?] 基线读不了（%s），跳过件数比对' % e)
    else:
        print('  件数：还没有基线（跑一次 guard snap 就有了）')

    # 未裁决的越界：文件还在、但内容骤降（如台账被截断）也算"不能用"。
    # 这条补的是"件数一致但账短了"的盲区——实测：台账 1405 行截到 300 行，
    # 件数仍是 334、部署自检仍绿，自检照样报"能用"。
    pend = os.path.join(root, '.guard_pending.json')
    if os.path.isfile(pend):
        try:
            pj = json.load(open(pend, encoding='utf-8'))
            miss = pj.get('missing') or []
            shr = pj.get('shrunk') or []
            add = pj.get('added') or []
            n = len(miss) + len(shr) + len(add)
            print('  [X] 有 %d 项未裁决的越界（文件还在，但内容变了/短了）：' % n)
            for x in miss[:3]:
                print('      缺件：%s' % x)
            for x in shr[:3]:
                print('      骤降：%s（%s → %s 字节）' % (x[0], x[1], x[2]))
            for x in add[:3]:
                print('      新增：%s' % x)
            print('      处置：确认该变就 `guard accept`；不该变就从权威包补齐。')
            ok = False
        except Exception:
            print('  [X] 有未裁决的越界（.guard_pending.json 在），先裁决再谈能用')
            ok = False

    # 平台别名提示：它名字叫 experience，但指向镜像里的旧库。工具已跳过，
    # 但人会顺着名字看——所以明说一句，免得有人照老路径手工读账。
    for alias in ('~/.doubao/agent_mode/workspace/experience',):
        ap_ = os.path.expanduser(alias)
        if os.path.islink(ap_) or os.path.isdir(ap_):
            real = os.path.realpath(ap_)
            if real.startswith('/runtime/'):
                print('  · 平台别名 %s → %s（镜像旧库，工具已跳过；别手工读）' % (alias, real))

    # 部署自检：必需件齐备才叫"能用"
    dchk = os.path.join(HERE, 'deploy_check.py')
    if os.path.isfile(dchk) and os.path.isfile(os.path.join(root, '部署清单.json')):
        env2 = {k: v for k, v in os.environ.items() if k != 'EXP_ROOT'}
        dp = subprocess.run([sys.executable, dchk, '--root', root],
                            capture_output=True, text=True, env=env2)
        if dp.returncode != 0:
            tail = ((dp.stdout or '') + (dp.stderr or '')).strip().splitlines()
            print('  [X] 部署自检未过（退出码 %d）：' % dp.returncode)
            for l in tail[-4:]:
                print('      %s' % l)
            ok = False
        else:
            print('  部署自检：必需件齐备')

    # 资产册：技能/工具/知识库的台账，程序扫盘生成
    ap = os.path.join(root, '资产册', '资产册.json')
    if os.path.isfile(ap):
        try:
            ad = json.load(open(ap, encoding='utf-8'))
            print('  资产册：技能 %d 个（自建 %d），知识库 %d 处'
                  % (ad.get('skills_total', 0), ad.get('selfbuilt_total', 0), ad.get('kb_total', 0)))
            if ad.get('selfbuilt_without_version'):
                print('    其中 %d 个还没版本号（跑 asset check 看是哪些）' % ad['selfbuilt_without_version'])
        except Exception:
            print('  [?] 资产册读不了，重跑 asset scan')
    else:
        print('  资产册：还没有（这是正常的）')
        print('    包里故意不带这本——它是「本机扫描结果」，换台机器扫出来的必然不同。')
        print('    跑一次就有了：python recall.py asset scan')

    # v6.41：认可两种合法布局。
    #   A 并排（老布局）：experience 就在技能目录旁边。
    #   B 持久盘（Linux 新布局）：库住 ~/experience。并排那份在易失盘上，容器重置
    #     会回滚成只含 .md 的残库（D163），所以不能反过来要求"必须并排"。
    _lib = os.path.abspath(LIB)
    _persist = os.path.join(os.path.expanduser('~'), 'experience')
    _same = (root == _lib) or (os.path.realpath(root) == os.path.realpath(_lib))
    _on_persist = os.path.realpath(root) == os.path.realpath(_persist)
    if _on_persist and not _same:
        print('  库位置：持久盘 %s（v6.41 起的正常布局）' % root)
        print('    库住持久卷，容器重置不会把它回滚成残库；程序留在技能目录，重置了重装即可。')
    elif not (_same or _on_persist):
        print('  [X] 它用的仓库不是脚边这个：')
        print('      脚边应该有：%s' % _lib)
        print('      实际在用：  %s' % root)
        print('      两个文件夹没摆成并排，它去别处找了。摆法见「装法.txt」。')
        ok = False

    print(line)
    if ok and cards == 0:
        print('  能用（空库）。两个文件夹都在该在的位置上，只是还没有经验卡。')
        print('  这不是错：空库就是给人从零开始记的。')
        print('  下一步：把自己的第一张卡放进来，写法见「模板_经验卡.md」；')
        print('          先看「从头读我.md」知道这个库怎么用。')
        return 0
    if ok and cards > 0:
        print('  能用。两个文件夹都在该在的位置上。')
        print('  查历史经验：python .user_skills/%s/recall.py search 关键词' % os.path.basename(HERE))
        print('  查过程记忆：python .user_skills/%s/recall.py mem search --q 关键词' % os.path.basename(HERE))
        print('  查资产台账：python .user_skills/%s/recall.py asset search 关键词' % os.path.basename(HERE))
        return 0
    print('  [X] 有问题，见上面')
    return 2 if root != os.path.abspath(LIB) else 3


if __name__ == '__main__':
    sys.exit(main())
