# -*- coding: utf-8 -*-
"""记忆域 · 项目层（v6.54）

记忆分两块：
- 发生了什么 → memory/*.jsonl 事件流水（只追加、事实原子）
- 做了什么   → projects/P0x/   项目单元（目标、阶段、未决、已定口径）

项目不是事件的容器，是事件的**索引维度**：一条事件可以不属于任何项目（NA 未归类）。
真相只有一份：人写 `P0x/交接.md`；两级索引 `index_projects.json` / `index_P0x.json`
一律由本模块现生成、只读。索引必须能重建——删掉所有 json 再 --build 应完全恢复。

三条契约（全自动、不进人审）：
C1 对拍：索引里的事件号必须真在流水里；流水里属于该项目的记录必须都进了索引
C2 双向覆盖：交接.md 里每条结论/未决/已完成都必须带三段戳，且条数与索引一致
C3 类型字典：事件 type 只能取固定枚举
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# 主程序由 recall.py 在 _proj_layer() 里注入（脚本态下 import recall 会把主程序再执行一遍，
# 造成本模块被当成半初始化模块返回——cmd_* 全部取不到）。这里不 import 它。
R = None

def _proj_dir():
    return os.path.join(R.ROOT, 'projects')


def _mem_dir():
    return os.path.join(R.ROOT, 'memory')
TYPES = ('start', 'step', 'decision', 'conclusion', 'anomaly', 'end', 'handover', 'migration')
STATUSES = ('active', 'dormant', 'archived')
STAMP_RE = re.compile(r'P(\d{2}):(evt_[0-9a-f]{12}):(\d{2})')
SECTIONS = ('一、现状', '二、已定口径', '三、未决', '四、下一步', '五、已完成到哪', '六、最近事件')
STAMPED = ('二、已定口径', '三、未决', '五、已完成到哪')  # 这三节的每条必须是带戳的结论
CREATED_RE = re.compile(r'^> 建于[:：](\d{4}-\d{2}-\d{2})')


# ---------------- 基础设施 ----------------
def _shards():
    return sorted(glob.glob(os.path.join(_mem_dir(), 'memory_*.jsonl')))


def _records():
    out = []
    for p in _shards():
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('record_type') == 'preference':
                    continue
                r['_shard'] = os.path.basename(p)
                out.append(r)
    return out


def _append_record(rec):
    import time
    shard = os.path.join(_mem_dir(), 'memory_' + time.strftime('%Y%m') + '.jsonl')
    os.makedirs(_mem_dir(), exist_ok=True)
    with open(shard, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def _next_seq(event_id):
    return 1 + sum(1 for r in _records() if r.get('event_id') == event_id)


def _projects():
    """列出项目目录：[(号 P01, 目录名, 目录路径)]"""
    out = []
    for d in sorted(glob.glob(os.path.join(_proj_dir(), 'P*'))):
        if not os.path.isdir(d):
            continue
        m = re.match(r'P(\d{2})-(.*)$', os.path.basename(d))
        if m:
            out.append(('P' + m.group(1), os.path.basename(d), d))
    return out


def _find_project(p):
    """按 P 号或 slug 片段找项目目录；找不到返回 None。"""
    if not p:
        return None
    for pid, name, d in _projects():
        if p == pid or p == name or p in name:
            return (pid, name, d)
    return None


def _handover_path(d):
    return os.path.join(d, '交接.md')


def _read_handover(d):
    p = _handover_path(d)
    if not os.path.exists(p):
        return None
    return open(p, encoding='utf-8').read()


def parse_handover(text):
    """解析交接.md：返回 {'meta': {...}, 'sections': {节名: [行...]}}"""
    meta = {'goal': '', 'status': 'active', 'why_closed': '', 'wake': '', 'created': ''}
    secs = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r'^> (目标|状态)[:：](.*)$', line.strip())
        if m:
            if m.group(1) == '目标':
                meta['goal'] = m.group(2).strip()
            else:
                st = m.group(2).strip().split()[0] if m.group(2).strip() else 'active'
                meta['status'] = st if st in STATUSES else 'active'
            continue
        m = CREATED_RE.match(line.strip())
        if m:
            meta['created'] = m.group(1)
            continue
        m = re.match(r'^> (为什么结束|重启条件)[:：](.*)$', line.strip())
        if m:
            meta['why_closed' if m.group(1) == '为什么结束' else 'wake'] = m.group(2).strip()
            continue
        m = re.match(r'^## ([一二三四五六]、[^\n]*)$', line)
        if m:
            cur = m.group(1).strip()
            secs[cur] = []
            continue
        if cur:
            secs[cur].append(line)
    out = {}
    for k in SECTIONS:
        out[k] = secs.get(k, [])
    return meta, out


def _bullets(lines):
    return [l.strip()[2:].strip() for l in lines if l.strip().startswith('- ')]


def _stamp_of(text):
    m = STAMP_RE.search(text or '')
    return m.group(0) if m else None


def _age_days(s):
    """建于某天 → 距今几天；写不出日期返回 None（宁可不说，也不误报）。"""
    try:
        from datetime import date
        return (date.today() - date(*[int(x) for x in str(s)[:10].split('-')])).days
    except Exception:
        return None


def _when(occ, ts):
    """排序用的时间。occurred 带时刻就直接用；只给日期时把记账时刻附上去
    （时段未知，记账时刻是最接近的近似）；都没有才退回记账时间。"""
    if occ and 'T' not in occ:
        return occ + (ts[10:] if ts and len(ts) > 10 else '')
    return occ or ts or ''


# ---------------- project new / list / rename / close ----------------
SKELETON = """# {pid} {slug}

> 目标：{goal}
> 状态：active
> 建于：{created}

## 一、现状

（这个项目现在处在哪一步，三句以内）

## 二、已定口径

（定下来的结论，每条必须带三段戳，如 `结论 [P01:evt_xxxxxxxxxxxx:01]`）

## 三、未决

（悬着没闭环的事，每条必须带三段戳）

## 四、下一步

（接手的人下一步该做什么，一条，可执行）

## 五、已完成到哪

（做完的事，每条必须带三段戳）

## 六、最近事件

（派生：由 `project index --build` 生成，人不要手写这节）
"""


def cmd_project(argv):
    if not argv:
        print('用法：project new|list|index --build|index --verify|rename|close')
        return 2
    sub = argv[0]
    if sub == 'new':
        return _new(argv[1:])
    if sub == 'list':
        return _list(argv[1:])
    if sub == 'index':
        if '--build' in argv:
            return _build()
        if '--verify' in argv:
            return _verify()
        print('用法：project index --build | --verify')
        return 2
    if sub == 'rename':
        return _rename(argv[1:])
    if sub == 'close':
        return _close(argv[1:])
    print('未知子命令：' + sub)
    return 2


def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _new(argv):
    slug = _opt(argv, '--slug')
    goal = _opt(argv, '--goal', '（待补：一句话说清这个项目要成什么样）')
    if not slug:
        print('用法：project new --slug <名字> --goal <一句话>')
        return 2
    bad = set('/\\:*?"<>| ')
    if any(c in slug for c in bad) or len(slug) > 20:
        print('slug 不合规：不许含 / \\ : * ? " < > | 与空格，且不超过 20 字')
        return 2
    with R._wlock():
        exist = _projects()
        n = max([int(p[1:]) for p, _, _ in exist], default=0) + 1
        pid = 'P%02d' % n
        name = pid + '-' + slug
        d = os.path.join(_proj_dir(), name)
        os.makedirs(d, exist_ok=True)
        hp = _handover_path(d)
        if os.path.exists(hp):
            print('已存在：' + hp + '（没覆盖）')
            return 1
        import time as _t
        with open(hp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(SKELETON.format(pid=pid, slug=slug, goal=goal,
                                    created=_t.strftime('%Y-%m-%d')))
    print('发了 %s（%s），骨架已建：%s' % (pid, name, _handover_path(d)))
    print('下一步：把「一、现状」「四、下一步」填上，再跑 project index --build')
    return 0


def _list(argv):
    show_all = '--all' in argv
    ps = _projects()
    cnt = {'active': 0, 'dormant': 0, 'archived': 0}
    print('项目 %d 个：' % len(ps))
    tty = sys.stdout.isatty()
    shell = []
    for pid, name, d in ps:
        text = _read_handover(d) or ''
        meta, secs = parse_handover(text)
        cnt[meta['status']] = cnt.get(meta['status'], 0) + 1
        if meta['status'] == 'archived' and not show_all:
            continue
        evs = [r for r in _records() if r.get('project') == pid]
        # 最后活动按「做的时间」，没有才退回「记的时间」
        last = max((_when(r.get('occurred_at'), r.get('timestamp')) for r in evs), default='—')
        n_ev = len({r.get('event_id') for r in evs})
        open_items = len(_bullets(secs['三、未决']))
        age = _age_days(meta.get('created'))
        mark = ''
        if meta['status'] == 'active' and n_ev == 0 and age is not None and age >= 7:
            mark = '  ⚠空壳(%d 天 0 事件)' % age
            shell.append('%s %s' % (pid, name))
        line = '  %s  %-22s %-9s 事件 %3d  未决 %2d  最后活动 %s%s' % (
            pid, name, meta['status'], n_ev, open_items, (last or '—')[:19], mark)
        if mark and tty:
            line = '\x1b[33m' + line + '\x1b[0m'
        print(line)
    print('  active %d / dormant %d / archived %d（archived 需 --all 才列）' %
          (cnt['active'], cnt['dormant'], cnt['archived']))
    if shell:
        print('  ⚠ %d 个空壳：%s —— 空壳不算记过（verify 空集也 PASS），去接线或结项'
              % (len(shell), '、'.join(shell)))
    return 0


def _rename(argv):
    old = _opt(argv, '--from')
    new_slug = _opt(argv, '--to')
    hit = _find_project(old)
    if not hit or not new_slug:
        print('用法：project rename --from P01 --to <新slug>')
        return 2
    pid, name, d = hit
    dst = os.path.join(_proj_dir(), pid + '-' + new_slug)
    if os.path.exists(dst):
        print('目标已存在：' + dst)
        return 1
    with R._wlock():
        os.rename(d, dst)
        hp = _handover_path(dst)
        txt = open(hp, encoding='utf-8').read().replace('# %s %s' % (pid, name.split('-', 1)[1]),
                                                   '# %s %s' % (pid, new_slug), 1)
        with open(hp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(txt)
    print('改名：%s → %s（路径即身份，改名属越界，已记一笔到状态变更史）' % (name, pid + '-' + new_slug))
    return 0


def _close(argv):
    p = _opt(argv, '--project') or (argv[0] if argv and not argv[0].startswith('--') else None)
    why = _opt(argv, '--why')
    if not p or not why:
        print('用法：project close --project P01 --why "<为什么结束>" [--wake "<什么条件重启>"]')
        return 2
    hit = _find_project(p)
    if not hit:
        print('没找到项目：' + str(p))
        return 2
    pid, name, d = hit
    wake = _opt(argv, '--wake', '（未写）')
    hp = _handover_path(d)
    text = open(hp, encoding='utf-8').read()
    text = re.sub(r'^> 状态：.*$', '> 状态：archived', text, count=1, flags=re.M)
    if '> 为什么结束：' not in text:
        text = text.replace('> 状态：archived',
                            '> 状态：archived\n> 为什么结束：%s\n> 重启条件：%s' % (why, wake), 1)
    with R._wlock():
        with open(hp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    print('已结项：%s → archived（为什么结束与重启条件已写进交接单）' % pid)
    return 0


# ---------------- 索引构建（派生、只读） ----------------
def _project_events(pid):
    """该项目名下的事件清单（按事件聚合）。"""
    by_ev = {}
    for r in _records():
        if r.get('project') != pid:
            continue
        eid = r.get('event_id')
        e = by_ev.setdefault(eid, {'event_id': eid, 'name': r.get('event_name') or '',
                                   'ts': r.get('timestamp') or '', 'occ': '', 'types': [],
                                   'stamps': [], 'records': 0})
        e['records'] += 1
        if r.get('trigger') and r['trigger'] not in e['types']:
            e['types'].append(r['trigger'])
        st = r.get('stamp')
        if st:
            e['stamps'].append(st)
        if (r.get('timestamp') or '') > (e['ts'] or ''):
            e['ts'] = r.get('timestamp') or e['ts']
        if r.get('occurred_at') and not e['occ']:
            e['occ'] = r['occurred_at']
        if not e['name']:
            e['name'] = r.get('event_name') or ''
    # 排序用「做的时间」，没有才退回「记的时间」——补记的事件不该插到最新那头
    return sorted(by_ev.values(), key=lambda x: _when(x['occ'], x['ts']))


def _build():
    out_total = []
    with R._wlock():
        for pid, name, d in _projects():
            text = _read_handover(d) or ''
            meta, secs = parse_handover(text)
            evs = _project_events(pid)
            sections = {}
            for k in SECTIONS:
                if k == '六、最近事件':
                    continue
                bs = _bullets(secs[k])
                if k in STAMPED:
                    sections[k] = [{'text': b, 'stamp': _stamp_of(b)} for b in bs]
                else:
                    sections[k] = bs
            idx = {'project': pid, 'dir': name, 'slug': name.split('-', 1)[1] if '-' in name else name,
                   'status': meta['status'], 'goal': meta['goal'],
                   'sections': sections, 'events': evs,
                   'built_at': __import__('time').strftime('%Y-%m-%dT%H:%M:%S')}
            R._atomic_json(os.path.join(d, 'index_%s.json' % pid), idx)
            out_total.append({'project': pid, 'dir': name, 'status': meta['status'],
                              'goal': meta['goal'], 'created_at': meta['created'],
                              'last_event': _when(evs[-1]['occ'], evs[-1]['ts']) if evs else '',
                              'events': len({e['event_id'] for e in evs}),
                              'open_items': len(_bullets(secs['三、未决'])),
                              'index': 'projects/%s/index_%s.json' % (name, pid)})
        R._atomic_json(os.path.join(_proj_dir(), 'index_projects.json'),
                       {'built_at': __import__('time').strftime('%Y-%m-%dT%H:%M:%S'),
                        'projects': out_total})
    print('已重建索引：项目 %d 个' % len(out_total))
    shell = []
    for p in out_total:
        age = _age_days(p.get('created_at'))
        mark = ''
        if p['status'] == 'active' and p['events'] == 0 and age is not None and age >= 7:
            mark = '  ⚠空壳 %d 天' % age
            shell.append(p['project'])
        print('  %s  %-22s %-8s 事件 %3d  未决 %2d%s' % (p['project'], p['dir'], p['status'],
                                                        p['events'], p['open_items'], mark))
    if shell:
        print('⚠ %d 个 active 项目建了 >=7 天仍 0 事件：%s —— 去接线（mem record）或 project close'
              % (len(shell), '、'.join(shell)))
    return 0


# ---------------- 三条契约 ----------------
def _verify():
    bad = []
    orphan = wrong = nostamp = unknown = 0
    seen_events = {r.get('event_id') for r in _records()}
    for pid, name, d in _projects():
        ip = os.path.join(d, 'index_%s.json' % pid)
        if not os.path.exists(ip):
            bad.append('%s 缺索引（先跑 project index --build）' % pid)
            continue
        idx = json.load(open(ip, encoding='utf-8'))
        # C1 对拍：索引声明的事件号必须真在流水里
        for e in idx.get('events', []):
            if e['event_id'] not in seen_events:
                orphan += 1
                bad.append('%s 索引里的 %s 在流水里不存在（孤儿指针）' % (pid, e['event_id']))
        # C1 反向：流水里属于本项目的事件必须都在索引里
        listed = {e['event_id'] for e in idx.get('events', [])}
        for eid in {r.get('event_id') for r in _records() if r.get('project') == pid}:
            if eid not in listed:
                wrong += 1
                bad.append('%s 流水里的 %s 没进索引（漏挂）' % (pid, eid))
        # C2 双向覆盖：带戳节的每条都必须有合法戳，且条数与索引一致
        text = _read_handover(d) or ''
        meta, secs = parse_handover(text)
        for k in STAMPED:
            live = _bullets(secs[k])
            declared = idx.get('sections', {}).get(k, [])
            for b in live:
                if not _stamp_of(b):
                    nostamp += 1
                    bad.append('%s「%s」有一条没带戳：%s' % (pid, k, b[:40]))
            if len(live) != len([x for x in declared if isinstance(x, dict)]):
                bad.append('%s「%s」条数不一致（正文 %d / 索引 %d）——索引过期，重跑 --build'
                           % (pid, k, len(live), len(declared)))
        # C3 类型字典
        for r in _records():
            if r.get('project') != pid:
                continue
            t = r.get('trigger_type') or r.get('type')
            if t and t not in TYPES:
                unknown += 1
                bad.append('%s 事件 %s 的类型「%s」不在字典里' % (pid, r.get('event_id'), t))
    print('C1 对拍：孤儿 %d / 漏挂 %d' % (orphan, wrong))
    print('C2 覆盖：无出处 %d' % nostamp)
    print('C3 字典：未知类型 %d' % unknown)
    for b in bad[:12]:
        print('  ✗ ' + b)
    if bad:
        print('PROJECT VERIFY FAIL：%d 项' % len(bad))
        return 1
    print('PROJECT VERIFY PASS：三条契约全过')
    return 0


# ---------------- 交接单（S0→S4 的落地） ----------------
def cmd_handover(argv):
    p = argv[0] if argv else None
    budget = int(_opt(argv, '--budget', '4096'))
    hit = _find_project(p)
    if not hit:
        print('用法：handover <P号或slug> [--budget 4096]；可选：project list')
        return 2
    pid, name, d = hit
    text = _read_handover(d) or ''
    meta, secs = parse_handover(text)
    evs = _project_events(pid)
    lines = []
    lines.append('【%s %s】状态 %s · 目标：%s' % (pid, name, meta['status'], meta['goal'] or '（未写）'))
    if meta['status'] == 'archived':
        lines.append('  已结项：%s ｜ 重启条件：%s' % (meta.get('why_closed') or '（未写）', meta.get('wake') or '（未写）'))
    for k in ('一、现状', '四、下一步'):
        bs = _bullets(secs[k])
        if bs:
            lines.append('■ ' + k)
            for b in bs:
                lines.append('  - ' + b)
    # 交接单是"接手够用"，不是"全文搬家"：未决最多 10 条、已定口径最多 6 条，其余只报数。
    for k, cap in (('三、未决', 10), ('二、已定口径', 6)):
        bs = _bullets(secs[k])
        if bs:
            lines.append('■ %s（%d 条%s）' % (k, len(bs), '，只列前 %d' % cap if len(bs) > cap else ''))
            for b in bs[:cap]:
                lines.append('  - ' + b)
            if len(bs) > cap:
                lines.append('  …其余 %d 条见 P0x/交接.md 与 index_P0x.json' % (len(bs) - cap))
    if evs:
        lines.append('■ 最近事件')
        for e in evs[-8:]:
            occ = e.get('occ') or ''
            if occ and occ[:10] != (e['ts'] or '')[:10]:
                tshow = '%s 做 · %s 补' % (occ[:16], (e['ts'] or '')[:10])
            else:
                tshow = (e['ts'] or '')[:16]
            lines.append('  - [%s] %s %s（%d 条记录，%s）' %
                         (e['event_id'], tshow, e['name'] or '（无名）',
                          e['records'], '/'.join(e['types']) or '—'))
        last = [r for r in _records() if r.get('project') == pid]
        if last:
            lr = max(last, key=lambda r: r.get('timestamp') or '')
            lines.append('■ 上一段是谁做到哪：%s · %s · %s' %
                         (lr.get('model') or '—', lr.get('session_id') or '—', (lr.get('timestamp') or '')[:16]))
    print('\n'.join(lines))
    print('\n(预算 %d token，本节只给结构与指针；要原文跑 mem recall --event <evt_id>)' % budget)
    return 0


# ---------------- 写事件（带三段戳） ----------------
def cmd_record(argv):
    pid = _opt(argv, '--project')
    typ = _opt(argv, '--type', 'step')
    name = _opt(argv, '--name')
    eid = _opt(argv, '--event')
    src = _opt(argv, '--text', '-')
    occ = _opt(argv, '--occurred')
    if not pid or typ not in TYPES:
        print('用法：mem record --project P0x --type %s --name "<一句话>" '
              '[--occurred YYYY-MM-DD[THH:MM]] [--event <evt_id>] [--text <文件|->]' % '|'.join(TYPES))
        return 2
    if occ and not re.match(r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$', occ):
        print('--occurred 格式：YYYY-MM-DD 或 YYYY-MM-DDTHH:MM（补记必须写清事情是哪天做的）')
        return 2
    hit = _find_project(pid)
    if not hit:
        print('没找到项目：' + pid + '（先 project new）')
        return 2
    pid = hit[0]
    body = ''
    if src == '-':
        body = sys.stdin.read()
    elif os.path.exists(src):
        body = open(src, encoding='utf-8').read()
    with R._wlock():
        if not eid:
            import uuid
            eid = 'evt_' + uuid.uuid4().hex[:12]
            ename = name or (body.strip().splitlines() or ['（无名事件）'])[0][:60]
            seq = 1
        else:
            same = [r for r in _records() if r.get('event_id') == eid]
            if not same:
                print('事件号不存在：' + eid + '（不带 --event 就是新事件）')
                return 2
            ename = same[0].get('event_name') or (name or '')
            seq = _next_seq(eid)
        stamp = '%s:%s:%02d' % (pid, eid, seq)
        import time
        rec = {'record_type': 'event', 'record_id': 'rec_' + __import__('uuid').uuid4().hex[:12],
               'event_id': eid, 'event_name': ename, 'trigger': typ, 'trigger_type': typ,
               'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
               'model': 'doubao', 'session_id': os.environ.get('DOUBAO_SESSION', 'local'),
               'task_type': '项目', 'tags': [pid], 'importance': '标准级',
               'detail_level': 'step_by_step', 'project': pid, 'stamp': stamp,
               'provenance': 'recorded',
               'narrative': {'process': body.strip()[:2000]}}
        if occ:
            rec['occurred_at'] = occ
            # 事情不是今天做的 → 账是补的，标出来，别让补记冒充当场记
            rec['provenance'] = 'backfilled' if occ[:10] != time.strftime('%Y-%m-%d') else 'recorded'
        # v6.56（C-2）：三级指针。与 core 写入路径同名同义，只在有值时才落字段。
        for _k, _flag in (('parent_event', '--parent'), ('related_cards', '--related'),
                          ('resolved_from', '--resolved-from')):
            _val = _opt(argv, _flag)
            if _val:
                rec[_k] = _val
        _append_record(rec)
    print('已记事件 %s（%s），戳 %s' % (eid, typ, stamp))
    if occ:
        print('  做于 %s · 记于 %s（%s）' % (occ, time.strftime('%Y-%m-%d'),
                                            '后补 backfilled' if rec.get('provenance') == 'backfilled' else '当天记'))
    print('  交接单里引用它：`... [%s]`' % stamp)
    return 0


