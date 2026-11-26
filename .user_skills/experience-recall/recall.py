#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经验资产检索器（零依赖）。
命令：stats / search <kw> / card <片段> / state / flow <kw> / event <id> / list / gaps / match <文本>
      / lint / task / claim / sync（pack | diff | merge）— 多机同一份库靠它对齐
      / pref（add --pid <id> --from <文件|-> | list [N] | audit | show）— 个人偏好镜像，双写自豆包偏好
      / log status — 开工第一条：三本账到哪了、下一条该记什么号
      / log（defect | release）--title <标题> [--body <文件|->] — 记一笔，工具发号，并发不撞
      / log（defect | release | change）--text <文件|-> — 原样追加，会查撞号
      / guard（snap | check | diff <zip> | list | restore --tag）— 动库守卫：覆盖式写入必被查出
环境变量：EXP_ROOT（经验目录）MEM_DIR（记忆库目录，缺省按技能目录同级自动定位）
"""
import os, sys, re, json, glob, zipfile, subprocess, tempfile, hashlib, shutil, time
TMPB = os.environ.get('EXPRECALL_TMP') or tempfile.gettempdir()   # 临时根，换平台只改这一处

HOME = os.path.expanduser('~')

def _looks_like_lib(d):
    """这个目录像不像一座库：至少有一本台账或状态文件。"""
    try:
        if not os.path.isdir(d):
            return False
        for p in os.listdir(d):
            if p.startswith('缺陷台账') or p.startswith('发版流水') or p == 'current-work-state.md':
                return True
    except Exception:
        pass
    return False

def _root_cands():
    """库根的候选清单——**唯一实现**。

    为什么单列出来：`where` 曾经自己硬编码了一份路径表，与这里各有各的列表。
    两份实现的后果和 D174 一样——改一处、另一处还按老规矩说话，
    于是"检查说没问题"和"实际跑在哪"可以同时为真。check 必须读同一份清单。
    """
    cands = []
    # 所有平台都把 HOME/experience 放第一位：种子落库就落在这儿，
    # 若候选清单里没有它，Windows 上会出现"种子落完库、工具还是说没库"（D191）。
    cands.append(os.path.join(HOME, 'experience'))              # 持久盘，第一优先（D163/D191）
    try:
        skd = os.path.dirname(os.path.abspath(__file__))        # .../workspace/.user_skills/experience-recall
        cands.append(os.path.join(os.path.dirname(os.path.dirname(skd)), 'experience'))   # .../workspace/experience
    except Exception:
        pass
    cands.append(os.path.join(HOME, 'AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/experience'))
    cands.append(os.path.join(HOME, '.doubao/agent_mode/workspace/experience'))
    return cands


def _default_root():
    """默认库根：多候选逐个试，找得到的那个才算数。

    踩过的坑（D121）：原先只算 HOME/.doubao/...，而本机真库在
    AppData/Local/Doubao/User Data/Default/.doubao/... 下。不设 EXP_ROOT 时
    工具指向一个不存在的目录，`log status` 照样输出一张 0 行看板、exit 0
    ——"记进空库"和"库是空的"长得一模一样。别的窗口不知道要设 EXP_ROOT，
    就会静默记到虚空里，账从此分裂成两本，谁也不知道。

    候选顺序：技能目录反推（与安装位置自洽，最可靠）→ 常见安装形态 → 旧路径。
    """
    cands = []
    # D163：Linux 上库必须住持久盘。容器重置会把易失盘（/ 是 overlay）回滚到
    # 镜像状态，而镜像里当初只装了 .md——每次重置后库只剩卡和三本账，memory/、
    # 资产册/、探针台账、两把钥匙全没了。两份都"像库"时，只有持久盘那份是真的，
    # 所以 HOME/experience 放第一位；它不存在时（别人拿走包、新机器）自动落到
    # 下面的技能目录反推，行为不变。
    for c in _root_cands():
        try:
            # 镜像库不当库（2026-11-17）：/runtime 下的东西会随容器重置回滚，
            # 在那里记账等于记在沙上。实测新机上它会安静地认下 D162 的旧库、
            # 报 cards=246、exit 0——人以为库好好的，写进去的账却会消失。
            if os.path.realpath(c).startswith('/runtime/'):
                continue
            if _looks_like_lib(c):
                return os.path.abspath(c)
        except Exception:
            pass
    # 一个都不像库：返回"库本该在的地方"，让调用方明确报错，而不是假装空库。
    # 不能用镜像路径兜底——那等于"找不到库就认个会回滚的库"，实测会安静读进 D162 旧数据。
    for c in _root_cands():
        if not os.path.realpath(c).startswith('/runtime/'):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(HOME, 'experience'))

DEFAULT_ROOT = _default_root()
ROOT = os.environ.get('EXP_ROOT', DEFAULT_ROOT)
# 隔离守卫：EXP_ROOT 常被用来"拷一份跑破坏性测试"。但经验目录常是软链，
# `cp -r 源 目标` 会把软链本身拷过去（目标变成指向生产中），测试就打到真库。
# 判据必须钉在"生产库的固定位置"上，不能拿 DEFAULT_ROOT 比——DEFAULT_ROOT 是
# 按脚本自己所在目录反推的，脚本本身在副本里跑时两者必然相等，于是每次正常测试
# 都报"隔离失效"（实测：在 %TEMP% 的副本里跑，真库 mtime 纹丝不动，警告照报）。
# 警告喊多了就没人信了，那比不报更坏。
_PROD_ROOTS = [os.path.join(HOME, 'AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/experience'),
               os.path.join(HOME, '.doubao/agent_mode/workspace/experience')]
try:
    if os.environ.get('EXP_ROOT'):
        _r = os.path.realpath(ROOT)
        if any(os.path.realpath(p) == _r for p in _PROD_ROOTS):
            print('⚠ 隔离失效：EXP_ROOT 与生产库指向同一实体（多半是软链被 cp -r 原样拷过去了）。'
                  '\n  破坏性操作会打到真库。请用 cp -rL 或 rsync -aL 拷真实内容。')
except Exception:
    pass
def _mem_dir():
    """记忆索引的 memory_data 目录：候选逐个试，别一条路径判死。

    踩过的坑：原先只算 HOME/.doubao/...，而本机真目录在
    .../AppData/Local/Doubao/User Data/Default/.doubao/... 下，少一层中间目录，
    于是 stats 恒报 event_records=0——事件数据 44,506 字节一直在，工具看不见。
    "读到 0 条"和"真的没有"是两件事，路径没解析对时不许静默返回 0。
    """
    cands = []
    v = os.environ.get('MEM_DIR')
    if v:
        cands.append(v)
    # v6.27 起记忆数据搬进库里（ROOT/memory）。这里必须试它，且排第一：
    # 只认旧 memory-index 目录的话，别人机器上没有那个目录，就会报
    # "事件层不可达"——而数据其实就在包里。
    try:
        cands.append(os.path.join(ROOT, 'memory'))
    except Exception:
        pass
    # 工具边界（v6.44）：以前这里会回退到 memory-index 技能的数据目录。
    # 那是**另一个工具**的地盘——读它等于假定"两套工具共用一份数据"，
    # 而实际它们各有自己的库。已拆除：本工具的记忆只认 自己的库根/memory
    # （或显式 MEM_DIR）。没有就明说，不偷偷去别人家读。
    for c in cands:
        try:
            if os.path.isdir(c):
                return os.path.abspath(c)
        except Exception:
            pass
    return os.path.abspath(cands[-1])

if os.path.realpath(os.path.abspath(ROOT)).startswith('/runtime/'):
    sys.stderr.write('⚠ 库在镜像里（%s）：容器重置会回滚，这里记的账会消失。确认这是临时的。\n' % ROOT)

MEM = _mem_dir()


SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_seed')

def _bootstrap_from_seed():
    """本机没有库、但技能目录里带着安装种子 → 就地落一次库。

    为什么要它：平台只同步技能目录，不跟着搬 `experience/`。云端机器上因此
    "装了工具却没有库"，一开机就是 exit 5。种子不是第二份真相：它只在
    「本机没有库」时用一次，之后所有写入都落到真库。
    """
    try:
        if not os.path.isdir(SEED_DIR): return False
        zips = sorted(glob.glob(os.path.join(SEED_DIR, '*.zip')))
        if not zips: return False
        target = os.path.join(HOME, 'experience')
        os.makedirs(target, exist_ok=True)
        with zipfile.ZipFile(zips[-1]) as zf:
            for n in zf.namelist():
                rel = n[len('experience/'):] if n.startswith('experience/') else n
                if not rel or rel.endswith('/'): continue
                dst = os.path.join(target, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with zf.open(n) as src, open(dst, 'wb') as out:
                    out.write(src.read())
        print('已从安装种子落库：%s（%s）' % (target, os.path.basename(zips[-1])))
        return True
    except Exception as e:
        print('种子落库失败：%s' % e)
        return False

# 解析不到一座像库的目录：这是"库不在这里"，不是"库是空的"（D121 的老病）。
# 由 main() 统一拦：读/写数据的命令一律 exit 5，只放行 restore（落库）与 where（诊断）。
def _lib_missing():
    """库是不是不在这里——**每次调用现算**，不在启动时定格。

    为什么改：定格版在"进程里跑到一半、别人把库补回来了"这种情况会一直报缺；
    虽然每个 CLI 调用都是新进程、影响有限，但判据该跟事实走，不该跟启动瞬间走。
    """
    return (not os.environ.get('EXP_ROOT')) and (not _looks_like_lib(ROOT))

def _resolve(names):
    """在 ROOT、上级目录、以及包内改名版本中依次找文件（包内为可读改名：INDEX_MATCH.json / INDEX.md）。"""
    up = os.path.dirname(os.path.abspath(ROOT))
    alias = {'_match_index.json': ('INDEX_MATCH.json',), '_index.md': ('INDEX.md',), '匹配索引.json': ('INDEX_MATCH.json', '_match_index.json'), '时效台账.json': ('_时效台账.json',)}
    for d in (ROOT, os.path.join(ROOT, 'state'), os.path.join(ROOT, 'experience'), up):
        for n in names + tuple(alias.get(n, ()) for n in names):
            for nn in ([n] if isinstance(n, str) else n):
                p = os.path.join(d, nn)
                if os.path.exists(p): return p
    return os.path.join(ROOT, names[0])


def _card_files():
    """库里的 .md 卡片文件：库根，以及包内布局的 experience/ 子目录。

    v6.5 包解开就是 experience/ + state/ 分区布局，而部署态是摊平的。两种都要认，
    否则"包解出来就能用"是假话：实测解包后 build_index 只出 0 条。
    """
    ps = list(glob.glob(os.path.join(ROOT, '*.md')))
    exp = os.path.join(ROOT, 'experience')
    if os.path.isdir(exp):
        ps += list(glob.glob(os.path.join(exp, '*.md')))
    return sorted(ps)


STATE = _resolve(('current-work-state.md', 'state/current-work-state.md'))
TASK = _resolve(('任务清单_20261113.md', 'state/任务清单_20261113.md'))
FLOW = _resolve(('发版流水_202611.md', 'state/发版流水_202611.md'))
DEFECT = _resolve(('缺陷台账_20261112.md', 'state/缺陷台账_20261112.md'))
MIDX = _resolve(('匹配索引.json', '_match_index.json'))
SKIP = {'注销卡.md', 'current-work-state.md', '_index.md', '发版流水_202611.md', 'TEMPLATE_新卡片.md', 'README.md', 'INDEX.md', '在办登记.md', '模板_经验卡.md', '时效台账.md', '原始34块_重建件.md', '缺陷台账_20261112.md', '个人偏好与点评.md', '用户画像.md', '架构与降权矩阵.md', '目标态.md', '长期路线.md', '对齐表.md', '外部方法评估.md', '说明_检索技能.md', '体检报告.md', '故障防线矩阵.md'}

def _frontmatter(s):
    """只取开头 --- 与下一个 --- 之间的 YAML 头。正文里以 line:/state: 开头的行不得冒充字段。"""
    t = s.lstrip('\ufeff')
    if not t.startswith('---'):
        return ''
    end = t.find('\n---', 3)
    return t[:end] if end > 0 else ''


def field(s, k):
    m = re.search(r'^\s*' + k + r':\s*(.+)$', _frontmatter(s), re.M)
    return m.group(1).strip().strip('"\'') if m else ''

CTRL_CHARS = ('\n', '\t', '\u200b', '\u200e', '\u200f', '\u202a', '\u202b', '\u202c', '\u202d', '\u202e', '\u2066', '\u2067', '\u2068', '\u2069', '\u2028', '\u2029')

def _card_ok(name, text):
    """统一收录门槛：两个装载器（load_cards / build_index）必须用同一套判断，否则 diagnostics 两套口径。"""
    if any(ch in name for ch in CTRL_CHARS):
        return False, '文件名含控制/隐形字符'
    if not text.strip():
        return False, '空文件'
    if not re.search(r'^name\s*:', text, re.M):
        return False, '缺name'
    if not (re.search(r'^##\s*适用场景', text, re.M) or re.search(r'^##\s*判据', text, re.M)):
        return False, '无结构(缺 适用场景/判据 小节)'
    if '\x00' in text:
        return False, '含NUL字节(疑似二进制)'
    if text.count('\ufffd') > 40:
        return False, '正文乱码(替换字符>40)'
    return True, ''

def load_cards():
    out = []
    global _SKIPPED
    _SKIPPED = []
    for p in _card_files():
        n = os.path.basename(p)
        if n in SKIP:
            continue
        if os.path.islink(p):
            _SKIPPED.append(n + '(软链)'); continue
        try:
            s = open(p, encoding='utf-8', errors='replace').read()
        except Exception as ex:
            _SKIPPED.append(n + '(读失败)'); continue
        if not s.strip():
            _SKIPPED.append(n + '(空文件)'); continue
        _ok, _why = _card_ok(n, s)
        if not _ok:
            _SKIPPED.append(n + '(' + _why + ')'); continue
        out.append({'file': n, 'path': p, 'desc': field(s, 'description'), 'created': field(s, 'created_at'),
                    'tags': field(s, 'tags'), 'body': s,
                    'expiry': bool(re.search(r'^##\s*失效条件', s, re.M)),
                    'failpath': bool(re.search(r'^##\s*(失败路径|试错)', s, re.M))})
    return out

def events():
    ev = []
    p = os.path.join(MEM, 'memory_202611.jsonl')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if line:
                try: ev.append(json.loads(line))
                except Exception: pass
    return ev

_SKIPPED = []

def cmd_stats():
    cs = load_cards(); ev = events()
    if _SKIPPED:
        # v6.59：缺 name 的是文档（台账/模板/说明/派生镜像），不进索引属正常，不按"跳过"记账。
        _doc = [x for x in _SKIPPED if x.endswith('(缺name)')]
        _oth = [x for x in _SKIPPED if not x.endswith('(缺name)')]
        if _doc:
            print('# 文档 ' + str(len(_doc)) + ' 份不进索引（无 name 前言，属正常）：' + '、'.join(x[:-7] for x in _doc[:6]) + ('…' if len(_doc) > 6 else ''))
        if _oth:
            print('# 跳过 ' + str(len(_oth)) + ' 个文件：' + '、'.join(_oth[:6]) + ('…' if len(_oth) > 6 else ''))
    if not os.path.isdir(MEM):
        print('⚠ 事件层不可达：memory_data 目录不存在（%s）。event_records=0 是"读不到"，不是"真没有"。' % MEM)
    elif not ev:
        print('⚠ 事件层读到 0 条：%s 里没有可解析的记录。' % MEM)
    dates = sorted([c['created'] for c in cs if c['created']])
    print(json.dumps({'root': ROOT, 'cards': len(cs),
        'cards_with_expiry': sum(1 for c in cs if c['expiry']),
        'cards_with_failure_path': sum(1 for c in cs if c['failpath']),
        'event_records': len(ev),
        'mem_root': MEM,
        'state_lines': sum(1 for _ in open(STATE, encoding='utf-8')) if os.path.exists(STATE) else 0,
        'flow_lines': sum(1 for _ in open(FLOW, encoding='utf-8')) if os.path.exists(FLOW) else 0,
        'date_range': [dates[0], dates[-1]] if dates else []}, ensure_ascii=False, indent=2))

def hit(kw, text):
    return all(k.lower() in (text or '').lower() for k in kw.split())

def _bg(s):
    s = ''.join(ch.lower() for ch in s if ch.isalnum())
    return [s[i:i + 2] for i in range(len(s) - 1)]

_RECALL_STRONG = (0.26, 4)   # 强匹配：问句的"词质量"被覆盖≥26% 且命中≥4 个二元组
_RECALL_WEAK = (0.23, 4)

def _rank_by_overlap(kw, cards):
    """按 BM25 排序，并把"问句里有多少词质量被这张卡覆盖"一并算出来。

    为什么要有这一层：原先只有"整串包含"——用户说的词得和卡里写的**一模一样**才命中。
    实测 30 句人话改写召回 0 条（关键词问法却能命中），等于库要人先猜到卡里的措辞才用得上。

    为什么最后选了 BM25 而不是自造的覆盖率打分：先试过"越稀有的二元组权重越高"，
    诊断时发现被权重最高的恰恰是"看哈/认手/希能"这种**跨词边界的废片段**（df=0，库里根本没有），
    它们只把分母垫大，反过来把"哈希/ClinVar/PS1"这些真词淹掉。BM25 天然解决这件事：
    词频饱和（tf/(tf+…)）+ 文档长度归一（b）+ 标准 idf，废片段 tf=0 直接贡献 0，
    不再参与分母。这也是兄弟技能 kb-recall-tester 用的同一套算法。

    返回 [(BM25 分, 命中词数, 词质量比, 卡)]，已按 BM25 分降序。
    """
    if not cards:
        return []
    import math
    docs = [((c['desc'] or '') + ' ' + ' '.join(c.get('tags') or []) + ' ' + (c.get('body') or '')) for c in cards]
    df = {}
    tfs = []
    lens = []
    for d in docs:
        g = _bg(d)
        cnt = {}
        for x in g:
            cnt[x] = cnt.get(x, 0) + 1
        tfs.append(cnt)
        lens.append(len(g))
        for x in set(g):
            df[x] = df.get(x, 0) + 1
    N = len(cards)
    if N == 0:
        return []
    avgdl = (sum(lens) / N) or 1.0
    q = list({g: 1 for g in _bg(kw)})
    if not q:
        return []

    def idf(t):
        return math.log(1 + (N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))

    tot = sum(idf(t) for t in q) or 1.0
    k1, b = 1.5, 0.75
    out = []
    for i, c in enumerate(cards):
        cnt = tfs[i]
        L = lens[i] or 1
        s = 0.0
        nm = 0
        mass = 0.0
        for t in q:
            tf = cnt.get(t, 0)
            if not tf:
                continue
            nm += 1
            w = idf(t)
            mass += w
            s += w * tf * (k1 + 1) / (tf + k1 * (1 - b + b * L / avgdl))
        if nm:
            out.append((s, nm, mass / tot, c))
    out.sort(key=lambda x: (-x[0], -x[1], x[3]['file']))
    return out

def cmd_search(kw):
    if _too_weak(kw):
        print('查询过短或全是停用词，判不出相关性（换一句现象描述，例如"改名后功能消失"）；拒绝返回全部卡片'); return
    cs = [c for c in load_cards() if hit(kw, c['desc'] + c['tags'] + c['body'])]
    if not cs:
        rk = _rank_by_overlap(kw, load_cards())
        if rk:
            strong = [c for s, n, cov, c in rk if cov >= _RECALL_STRONG[0] and n >= _RECALL_STRONG[1]]
            weak = [c for s, n, cov, c in rk
                    if c not in strong and cov >= _RECALL_WEAK[0] and n >= _RECALL_WEAK[1]]
            if strong:
                print('[卡片] ' + str(len(strong)) + ' 张强匹配（问句与卡片判据对得上）')
                for c in strong[:12]:
                    print('  - ' + c['file'] + '  [' + (c['created'] or '—') + ']  ' + c['desc'][:78])
            if weak:
                print('[卡片] ' + str(len(weak)) + ' 张弱匹配（词面相近，不保证对得上；用来发现线索，别据此改判）')
                for c in weak[:8]:
                    print('  ? ' + c['file'] + '  [' + (c['created'] or '—') + ']  ' + c['desc'][:78])
            if not strong and not weak:
                print('[卡片] 0 张命中（问句里没有能与任何卡对上的具体词）')
            cs = strong
        else:
            toks = [t for t in re.split(r'[\s，。:：/]+', kw) if len(t) >= 2]
            if toks:
                ranked = sorted(((sum(1 for t in toks if t.lower() in (c['file'] + c['desc'] + c['body']).lower()), c) for c in load_cards()), key=lambda x: -x[0])
                need = max(2, (len(toks) + 1) // 2) if len(toks) > 1 else 2
                cs = [c for n, c in ranked if n >= need][:12]
                print('[卡片] ' + str(len(cs)) + ' 张命中（按词回退，非精确整串）')
            else:
                print('[卡片] 0 张命中')
    else:
        print('[卡片] ' + str(len(cs)) + ' 张命中（问句里带着卡上的原词）')
    for c in cs[:25]:
        print('  - ' + c['file'] + '  [' + (c['created'] or '—') + ']  ' + c['desc'][:78])
    ev = [e for e in events() if hit(kw, json.dumps(e, ensure_ascii=False))]
    if ev:
        print('[事件] ' + str(len(ev)) + ' 条命中')
        for e in ev[:10]:
            print('  - ' + str(e.get('event_id', '—')) + '  ' + str(e.get('name') or e.get('action') or '')[:70])
    if os.path.exists(FLOW):
        rows = [l for l in open(FLOW, encoding='utf-8').read().split('\n') if l.startswith('## ') and hit(kw, l)]
        if rows:
            print('[流水标题] ' + str(len(rows)) + ' 块')
            for l in rows[:10]: print('  - ' + l[3:])
    _search_docs(kw)

def cmd_card(kw):
    sel = [c for c in load_cards() if kw in c['file'] or kw in c['desc'] or kw in c['body']]
    if not sel:
        toks = [t for t in re.split(r'[\s，。:：/]+', kw) if len(t) >= 2]
        sel = [c for c in load_cards() if sum(1 for t in toks if t in c['file'] + c['desc'] + c['body']) >= max(1, len(toks) // 2)]
    if not sel: print('未命中'); return
    for c in sel[:3]:
        print('=' * 60); print(c['body'])

def cmd_state():
    print(open(STATE, encoding='utf-8').read() if os.path.exists(STATE) else '无状态文件')


def _sections(text):
    """按 '## ' 切小节：[(标题, 标题行下标, 结束下标)]。结束下标是下一节标题行或文件末尾。"""
    lines = text.split('\n')
    idx = [i for i, l in enumerate(lines) if l.startswith('## ')]
    out = []
    for k, i in enumerate(idx):
        end = idx[k + 1] if k + 1 < len(idx) else len(lines)
        out.append((lines[i][3:].strip(), i, end))
    return out


def _sec_body(lines, i, end):
    """小节正文 = 标题行之后、下一节之前。"""
    return '\n'.join(lines[i + 1:end])


def _sha8(body):
    import hashlib
    return hashlib.sha256((body or '').encode('utf-8')).hexdigest()[:8]


def _state_dir():
    return os.path.dirname(STATE) or '.'


def _state_hist():
    """变更史：只增不改，跟着库走。谁在什么时候动了哪一节，只有这里说得清。"""
    return os.path.join(_state_dir(), '状态变更史.jsonl')


SNAP_KEEP = 5


def _snap_root():
    return os.path.join(_state_dir(), '.state_snapshots')


def _sec_key(title):
    """节名 → 目录名。去掉路径分隔符与空白，保证可寻址、不越界。"""
    return (re.sub(r'[\\/:*?"<>|\s]+', '_', title).strip('_')[:80] or 'sec')


def _snap_prune(d, keep=SNAP_KEEP):
    """每节只留最近 keep 版（含刚写的这版）。

    为什么不无限留：它是"留痕"，不是"资产"。实测同一节 22 版，件数被它一路顶高，
    "件数一致"这个判据就慢慢失去意义；磁盘也只增不减。
    为什么还留几版：两个窗口同时改同一节时，后到者要拿"我读到的那版"做三方合并——
    只留 1 版会把可合并变成必须报错。
    """
    try:
        fs = [(os.path.getmtime(os.path.join(d, f)), f) for f in os.listdir(d)
              if f.endswith('.md')]
        if len(fs) <= keep:
            return 0
        fs.sort(reverse=True)
        gone = 0
        for _t, f in fs[keep:]:
            try:
                os.remove(os.path.join(d, f))
                gone += 1
            except Exception:
                pass
        return gone
    except Exception:
        return 0


def _snap_put(title, h, body):
    """把某节某一版正文存一份（按 sha8 命名，幂等）。

    为什么要存正文：两个窗口都基于同一版改同一节时，后到者要拿"我读到的那版"做三方合并。
    只存 sha8 没有正文，合并就无从谈起，只能退化成整节覆盖、或者报错让人手抄。
    """
    d = os.path.join(_snap_root(), _sec_key(title))
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
        p = os.path.join(d, h + '.md')
        if not os.path.exists(p):
            tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8', newline='') as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        _snap_prune(d)
        return True
    except Exception:
        return False


def _snap_get(title, h):
    p = os.path.join(_snap_root(), _sec_key(title), h + '.md')
    if os.path.exists(p):
        return open(p, encoding='utf-8', newline='').read()
    return None


def _hist_md_rebuild():
    """按 jsonl 全量重算 `状态变更史.md`（镜像）。幂等：内容没变就不落盘。

    为什么钉在写入点，而不是"提醒人跑脚本"或"读时自动修"：
    D233 的根因不是忘了刷新，是**没有任何东西规定这份镜像必须被刷新**。
    只提醒人 = 把一条恒等式降级成一个人的记性；读时自动修 = 让只读命令去写库、还得加锁。
    所以放在唯一追加点后面：写完 jsonl 顺手重算一遍。镜像坏了也不影响账，异常一律咽掉。
    """
    try:
        src = _state_hist()
        if not os.path.exists(src):
            return 0
        rows = []
        for l in open(src, encoding='utf-8'):
            l = l.strip()
            if not l:
                continue
            try:
                rows.append(json.loads(l))
            except Exception:
                continue
        out = ['# 状态变更史（派生只读 · 由 状态变更史.jsonl 生成）', '',
               '> 这份是**镜像**，不是台账。唯一事实是 `状态变更史.jsonl`；',
               '> 本文件由脚本按行渲染，覆盖式重写，不许手改。行数与 jsonl 必须相等。', '',
               '| 时间 | 机器 | 动作 | 这一节 | 说明 |', '|---|---|---|---|---|']
        for r in rows:
            out.append('| %s | %s | %s | %s | %s |' % (
                (r.get('ts') or '')[:19], r.get('host') or '',
                r.get('action') or ('note' if r.get('what') else ''),
                (r.get('section') or '').replace('|', '｜'),
                (r.get('note') or r.get('what') or '')[:120].replace('|', '｜').replace('\n', ' ')))
        body = '\n'.join(out) + '\n'
        md = os.path.join(_state_dir(), '状态变更史.md')
        if os.path.exists(md):
            try:
                if open(md, encoding='utf-8').read() == body:
                    return len(rows)
            except Exception:
                pass
        tmp = md + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(body)
        os.replace(tmp, md)
        return len(rows)
    except Exception:
        return 0


def _hist_append(rec):
    try:
        with open(_state_hist(), 'a', encoding='utf-8', newline='') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass
    _hist_md_rebuild()


def _hst():
    import socket
    try:
        return socket.gethostname()
    except Exception:
        return '?'


def _line_blocks(base, other):
    """base→other 的行级改动块：[(base起, base止, 替换成的行)]。纯插入时 起==止。"""
    import difflib
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != 'equal':
            out.append((i1, i2, other[j1:j2]))
    return out


def _replay(base, mine, theirs):
    """把 mine 相对 base 的改动，重放到 theirs（磁盘上的最新）上。

    返回 (合并后的行, 冲突列表)。做法是"块重放"而不是通用 diff3：
    每个 mine 块拿它对应的 base 原文去 theirs 里定位，唯一命中就是安全落点；
    定位不到、或命中多处，就是两人动了同一段——原样报出来，不猜。
    """
    cur = list(theirs)
    conflicts = []
    for i1, i2, mlines in _line_blocks(base, mine):
        n = i2 - i1
        if n > 0:
            anchor = base[i1:i2]
            hits = [k for k in range(len(cur) - n + 1) if cur[k:k + n] == anchor]
            if len(hits) != 1:
                conflicts.append((i1, i2, base[i1:i2], mlines))
                continue
            cur[hits[0]:hits[0] + n] = list(mlines)
            continue
        # 纯插入：加在节末是最常见的写法（多一条结论、多一条待办），这种绝不能判成冲突
        if all(x.strip() == '' for x in base[i1:]):
            while cur and cur[-1].strip() == '':
                cur.pop()
            cur.extend(list(mlines) + [''])
            continue
        j = i1 - 1
        while j >= 0 and base[j].strip() == '':
            j -= 1
        if j < 0:
            cur[0:0] = list(mlines)
            continue
        a = base[j:j + 1]
        hits = [k + 1 for k in range(len(cur)) if cur[k:k + 1] == a]
        if len(hits) != 1:
            conflicts.append((i1, i2, base[i1:i2], mlines))
            continue
        cur[hits[0]:hits[0]] = list(mlines)
    return cur, conflicts


def cmd_state_sec(args):
    """覆盖层分块写：按小节 / 按行写，永远不整文件覆盖。

    三档，冲突面从大到小：
      --write <f>                            整节替换。我这一节全重写，会盖掉别人在这节里加的行。
      --write <f> --expect-sha8 <8>          整节合并。基于"我读到的那版"改，工具只把我的改动重放到最新上。
      --add-line / --del-line / --rep-line   行级意图。只动我指名的那一行，别人的行碰不到。

    结构操作（同样锁内、做完逐节校验、写进变更史）：
      --new <标题> [--after <标题>|--before <标题>]   插一节
      --drop <标题> --expect-sha8 <8>                  删一节（必须给这一节当前的 sha8）
      --move <标题> --after <标题>|--before <标题>     移一节
      --section <标题> --retitle <新标题>              改一节标题（正文一字不动）

    为什么要有"合并"和"行级"：两个窗口同时往同一节写，整节替换一定有一个白干。
    加行是最常见的写法（多一条结论、多一条待办），这种必须两边都活下来；
    同一行被两人改成不同内容，那是语义冲突，工具不猜——停手、报出来、落一份冲突稿、把改动记进变更史。
    """
    import sys as _sys
    sec = wr = expect = None
    addl, dell = [], []
    rep, to = None, None
    newt = dropt = movet = None
    ret = None
    after = before = None
    want_list = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--sections':
            want_list = True; i += 1
        elif a == '--section' and i + 1 < len(args):
            sec = args[i + 1]; i += 2
        elif a == '--write' and i + 1 < len(args):
            wr = args[i + 1]; i += 2
        elif a == '--expect-sha8' and i + 1 < len(args):
            expect = args[i + 1]; i += 2
        elif a == '--add-line' and i + 1 < len(args):
            addl.append(args[i + 1]); i += 2
        elif a == '--del-line' and i + 1 < len(args):
            dell.append(args[i + 1]); i += 2
        elif a == '--rep-line' and i + 1 < len(args):
            rep = args[i + 1]; i += 2
        elif a == '--to' and i + 1 < len(args):
            to = args[i + 1]; i += 2
        elif a == '--new' and i + 1 < len(args):
            newt = args[i + 1]; i += 2
        elif a == '--drop' and i + 1 < len(args):
            dropt = args[i + 1]; i += 2
        elif a == '--move' and i + 1 < len(args):
            movet = args[i + 1]; i += 2
        elif a == '--retitle' and i + 1 < len(args):
            ret = args[i + 1]; i += 2
        elif a == '--after' and i + 1 < len(args):
            after = args[i + 1]; i += 2
        elif a == '--before' and i + 1 < len(args):
            before = args[i + 1]; i += 2
        else:
            print('用法：state [--sections] [--section <标题> [--write <文件|->] [--expect-sha8 <8位>]]')
            print('      state --section <标题> --add-line "<一行>"           节末加一行（永远不覆盖别人的行）')
            print('      state --section <标题> --del-line "<一行>"           删掉这一行（找不到就停手）')
            print('      state --section <标题> --rep-line "<旧>" --to "<新>"  改一行（旧行被别人改过就停手）')
            print('      state --new "<标题>" [--after|--before "<标题>"] --write <文件|->   插一节')
            print('      state --drop "<标题>" --expect-sha8 <8位>            删一节')
            print('      state --move "<标题>" --after|--before "<标题>"      移一节')
            print('      state --section "<标题>" --retitle "<新标题>"         只改这一节的标题，正文一字不动')
            print('  --expect-sha8  节级乐观锁；配 --write 时是"基于这一版合并"，不是覆盖')
            _sys.exit(2)

    if not os.path.exists(STATE):
        print('无状态文件：' + STATE); _sys.exit(2)
    text = open(STATE, encoding='utf-8', newline='').read()
    lines = text.split('\n')

    if want_list:
        ss = _sections(text)
        if not ss:
            print('这份状态层还没有 "## " 小节——分块写要先有节。')
            print('（有节之前，任何窗口都只能整文件覆盖，这正是账实不符的源头）')
            return
        print('共 %d 节：' % len(ss))
        for t, i2, e2 in ss:
            body = _sec_body(lines, i2, e2)
            print('  %-28s 行 %-4d 正文 %d 字节  sha8 %s'
                  % (t, i2 + 1, len(body.encode('utf-8')), _sha8(body)))
        return

    # ret 也必须算进"有目标"：漏掉它的话，只给 --retitle 不给 --section 会掉进
    # "打印全文"分支，静默 exit 0——看起来像成功了，其实一个字没改。
    if sec is None and not (newt or dropt or movet or ret):
        print(text); return

    def pick(ss, name):
        exact = [x for x in ss if x[0] == name]
        if exact:
            return exact
        return [x for x in ss if name in x[0]]

    def one(ss, name, why=''):
        cand = pick(ss, name)
        if not cand:
            print('⛔ 没有这一节：' + name)
            print('   可用小节：')
            for t, _a, _b in ss:
                print('     · ' + t)
            if why:
                print('   ' + why)
            _sys.exit(2)
        if len(cand) > 1:
            print('⛔ "%s" 匹配到 %d 节，无法判定是哪一节：' % (name, len(cand)))
            for t, _a, _b in cand:
                print('     · ' + t)
            _sys.exit(2)
        return cand[0]

    raw = None
    if wr is not None:
        raw = _sys.stdin.read() if wr == '-' else open(wr, encoding='utf-8', newline='').read()
        raw = raw.replace('\r\n', '\n')
        if text.count('\r\n') > text.count('\n') // 2:
            raw = raw.replace('\n', '\r\n') if '\r' not in raw else raw

    with _wlock('状态层分块写'):
        # 锁内重读：从我读到它到真的拿到锁，别人可能已经把同一节写完了
        text = open(STATE, encoding='utf-8', newline='').read()
        lines = text.split('\n')
        ss = _sections(text)
        before_map = {t: _sha8(_sec_body(lines, a1, b1)) for t, a1, b1 in ss}

        def commit(olines, action, title, note='', extra=None):
            out = '\n'.join(olines)
            tmp = STATE + '.tmp'
            with open(tmp, 'w', encoding='utf-8', newline='') as f:
                f.write(out)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE)
            after_text = open(STATE, encoding='utf-8', newline='').read()
            alines = after_text.split('\n')
            ass = _sections(after_text)
            after_map = {t: _sha8(_sec_body(alines, a1, b1)) for t, a1, b1 in ass}
            keep = set(before_map) - set([title])
            drift = [t for t in keep if t in after_map and after_map[t] != before_map[t]]
            lost = [t for t in keep if t not in after_map]
            if drift or lost:
                print('⛔ 其他小节被动了（不该发生）：' + '、'.join(drift + lost))
                _sys.exit(1)
            rec = {'ts': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
                   'host': _hst(), 'action': action, 'section': title,
                   'sha8_before': before_map.get(title), 'sha8_after': after_map.get(title),
                   'other_sections': len([t for t in after_map if t != title])}
            if note:
                rec['note'] = note
            if extra:
                rec.update(extra)
            _hist_append(rec)
            _auto_guard()
            return after_map.get(title)

        # ---------- 结构操作 ----------
        if newt:
            if any(t == newt for t, _a, _b in ss):
                print('⛔ 已经有同名节：' + newt); _sys.exit(2)
            body = (raw or '').rstrip('\n')
            block = ['## ' + newt, '']
            if body:
                block += body.split('\n')
            block += ['']
            if after:
                _t, _i, e2 = one(ss, after, '插节要知道插在谁后面。')
                pos = e2
            elif before:
                _t, i2, _e = one(ss, before, '插节要知道插在谁前面。')
                pos = i2
            else:
                pos = len(lines) if not ss else ss[-1][2]
            ol = lines[:pos] + block + lines[pos:]
            n8 = commit(ol, 'new', newt, note=('插在 %s 之后' % after if after else ('插在 %s 之前' % before if before else '追加到末尾')))
            print('已新增「%s」：%d 字节（sha8 %s）' % (newt, len(body.encode('utf-8')), n8))
            print('其他 %d 节逐节比对：一字未动' % len(ss))
            return

        if dropt:
            t0, i2, e2 = one(ss, dropt)
            cur = before_map.get(t0)
            if not expect:
                print('⛔ 删节必须给 --expect-sha8 <这一节当前的 sha8>（先 --sections 看）')
                _sys.exit(2)
            if expect != cur:
                print('⛔ 节级校验不过：这一节现在是 sha8 %s，你手上是 %s' % (cur, expect))
                _sys.exit(3)
            ol = lines[:i2] + lines[e2:]
            commit(ol, 'drop', t0, note='删除整节（sha8 %s）' % cur)
            print('已删除「%s」（原 sha8 %s）' % (t0, cur))
            print('其他 %d 节逐节比对：一字未动' % (len(ss) - 1))
            return

        if movet:
            t0, i2, e2 = one(ss, movet)
            anchor = after or before
            if not anchor:
                print('⛔ 移节要给 --after 或 --before <目标节>'); _sys.exit(2)
            ta = one(ss, anchor)[0]
            if ta == t0:
                print('⛔ 目标节和被移的是同一节'); _sys.exit(2)
            block = lines[i2:e2]
            rest = lines[:i2] + lines[e2:]
            rss = _sections('\n'.join(rest))
            ra = [x for x in rss if x[0] == ta][0]
            pos = ra[2] if after else ra[1]
            ol = rest[:pos] + block + rest[pos:]
            commit(ol, 'move', t0, note=('移到 %s %s' % (ta, '之后' if after else '之前')))
            print('已移动「%s」到「%s」%s' % (t0, ta, '之后' if after else '之前'))
            print('其他 %d 节逐节比对：一字未动' % (len(ss) - 1))
            return

        if ret:
            # 改标题：只动 "## <标题>" 这一行的文字，正文一个字节都不碰。
            # 为什么要单开一条：标题不在节正文范围内（_sec_body 从标题下一行算起），
            # --rep-line 够不着它。没有这条命令时，改标题只有两条路——整文件重写（会冲掉
            # 别人写的节），或者绕过锁手改（根本不在锁内）。两条都不该是常态。D133。
            if not sec:
                print('⛔ 改标题要给 --section "<现在的标题>"')
                _sys.exit(2)
            nret = (ret or '').strip()
            if not nret:
                print('⛔ --retitle 后面要给新标题'); _sys.exit(2)
            if '\n' in nret or '\r' in nret:
                print('⛔ 标题里不能有换行'); _sys.exit(2)
            if nret.startswith('#'):
                print('⛔ 新标题不要再带 #（工具自己加 "## "）'); _sys.exit(2)
            t0, i2, e2 = one(ss, sec, '改标题要先指名是哪一节。')
            if nret == t0:
                print('⛔ 新标题和旧标题一样：' + nret); _sys.exit(2)
            if any(t == nret for t, _a, _b in ss):
                print('⛔ 已经有同名节：' + nret); _sys.exit(2)
            cur8 = before_map.get(t0)
            if expect and expect != cur8:
                print('⛔ 节级校验不过：这一节现在是 sha8 %s，你手上是 %s' % (cur8, expect))
                _sys.exit(3)
            body_before = _sec_body(lines, i2, e2)
            ol = list(lines)
            ol[i2] = '## ' + nret
            tmp = STATE + '.tmp'
            with open(tmp, 'w', encoding='utf-8', newline='') as f:
                f.write('\n'.join(ol)); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, STATE)
            after_text = open(STATE, encoding='utf-8', newline='').read()
            alines = after_text.split('\n')
            ass = _sections(after_text)
            after_map = {t: _sha8(_sec_body(alines, a1, b1)) for t, a1, b1 in ass}
            # 四条硬校验：旧名消失、新名落地、正文没变、其他节没动。
            # 第三条是这一条命令的全部意义——改标题不该改内容，改了就停手。
            if t0 in after_map:
                print('⛔ 旧标题还在（不该发生）：' + t0); _sys.exit(1)
            if nret not in after_map:
                print('⛔ 新标题没落下去（不该发生）：' + nret); _sys.exit(1)
            if after_map[nret] != cur8:
                print('⛔ 正文被动了：改标题只该换名字（%s → %s）' % (cur8, after_map[nret]))
                _sys.exit(1)
            others = [t for t in before_map if t != t0]
            bad = [t for t in others if after_map.get(t) != before_map[t]]
            if bad:
                print('⛔ 其他小节被动了（不该发生）：' + '、'.join(bad)); _sys.exit(1)
            # 快照要挂到新名下：这一节的 sha8 没变，以后别人拿旧 sha8 做三方合并，得在新名下找得到 base
            _snap_put(nret, cur8, body_before)
            _hist_append({'ts': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
                          'host': _hst(), 'action': 'retitle', 'section': nret,
                          'sha8_before': cur8, 'sha8_after': cur8,
                          'note': '标题：%s → %s（正文未变）' % (t0, nret),
                          'other_sections': len(others)})
            print('已改标题：「%s」→「%s」' % (t0, nret))
            print('正文一字未动（sha8 %s 不变）；其他 %d 节逐节比对：一字未动' % (cur8, len(others)))
            return

        # ---------- 内容操作 ----------
        title, i2, e2 = one(ss, sec, '两个窗口各写各的节才不冲突；找不到就说明节名写错了，别硬写。')
        body_before = _sec_body(lines, i2, e2)
        cur8 = before_map[title]

        # 行级意图：只碰指名的那一行
        if addl or dell or rep is not None:
            parts = body_before.split('\n')
            tail = 0
            while len(parts) > tail and parts[len(parts) - 1 - tail].strip() == '':
                tail += 1
            bl = parts[:len(parts) - tail]
            if rep is not None:
                if to is None:
                    print('⛔ --rep-line 要配 --to "<新内容>"'); _sys.exit(2)
                if bl.count(rep) == 0:
                    print('⛔ 找不到这一行（可能别人已经删了或改了）：' + rep)
                    print('   先 `state --section "%s"` 看一眼再动手。' % title)
                    _sys.exit(3)
                if bl.count(rep) > 1:
                    print('⛔ 这一行在节里出现 %d 次，不替你猜是哪一行：%s' % (bl.count(rep), rep))
                    _sys.exit(2)
                bl[bl.index(rep)] = to
            for s in dell:
                if s not in bl:
                    print('⛔ 找不到要删的行（可能别人已经删了）：' + s)
                    _sys.exit(3)
                bl.remove(s)
            for s in addl:
                bl.append(s)
            new_body = '\n'.join(bl + [''] * max(tail, 1))
            _snap_put(title, cur8, body_before)
            ol = lines[:i2 + 1] + new_body.split('\n') + lines[e2:]
            if rep is not None:
                act = 'rep'
            elif addl and not dell:
                act = 'add'
            elif dell and not addl:
                act = 'del'
            else:
                act = 'edit'
            n8 = commit(ol, act, title, extra={'lines_added': len(addl), 'lines_del': len(dell),
                                               'replaced': 1 if rep is not None else 0})
            _snap_put(title, n8, new_body)
            print('已改「%s」：%d 字 → %d 字（sha8 %s → %s）'
                  % (title, len(body_before.encode('utf-8')), len(new_body.encode('utf-8')), cur8, n8))
            print('只动了你指名的那几行；其他 %d 节逐节比对：一字未动' % (len(ss) - 1))
            return

        if raw is None:
            print('## ' + title)
            print(body_before.rstrip('\n'))
            return

        if expect and expect == cur8:
            new_body = raw.rstrip('\n')
            _snap_put(title, cur8, body_before)
            ol = lines[:i2 + 1] + new_body.split('\n') + lines[e2:]
            n8 = commit(ol, 'write', title)
            _snap_put(title, n8, new_body)
            print('已写「%s」：%d 字 → %d 字（sha8 %s → %s）'
                  % (title, len(body_before.encode('utf-8')), len(new_body.encode('utf-8')), cur8, n8))
            print('其他 %d 节逐节比对：一字未动' % (len(ss) - 1))
            return

        if expect:
            # 有人在我读它之后改过这一节：拿快照里的 base 做三方合并，而不是覆盖
            base = _snap_get(title, expect)
            if base is None:
                print('⛔ 这一节现在是 sha8 %s，你手上是 %s，而且找不到你那一版的快照。' % (cur8, expect))
                print('   不能猜你的改动是什么。先 `state --section "%s"` 看最新内容，再重做一次。' % title)
                _sys.exit(3)
            mine = raw.rstrip('\n').split('\n')
            # 尾部空行是格式，不是内容：两边只在"节末空了几行"上不同，不能算冲突
            b_l = base.split('\n')
            t_l = body_before.split('\n')
            b_tail = 0
            while b_l and b_l[-1].strip() == '':
                b_l.pop(); b_tail += 1
            t_tail = 0
            while t_l and t_l[-1].strip() == '':
                t_l.pop(); t_tail += 1
            m_tail = 0
            while mine and mine[-1].strip() == '':
                mine.pop(); m_tail += 1
            merged, conflicts = _replay(b_l, mine, t_l)
            if not conflicts:
                merged += [''] * max(t_tail, 0)
            if conflicts:
                stamp = __import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')
                cpath = STATE + '.conflict.' + stamp + '.md'
                buf = ['# 节「%s」三方合并冲突（%s）' % (title, stamp), '',
                       'base=%s（你读到的那版）  mine=%s（你交的）  theirs=%s（磁盘最新）'
                       % (expect, _sha8('\n'.join(mine)), cur8), '']
                for lo, hi, bseg, mseg in conflicts:
                    buf += ['## 冲突：base 第 %d-%d 行' % (lo + 1, hi), '', 'base 原文：', '```']
                    buf += (bseg or ['(空)']) + ['```', '', '你提交的版本：', '```']
                    buf += (mseg or ['(空)']) + ['```', '']
                open(cpath, 'w', encoding='utf-8', newline='\n').write('\n'.join(buf) + '\n')
                _hist_append({'ts': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
                              'host': _hst(), 'action': 'conflict', 'section': title,
                              'sha8_before': cur8, 'sha8_after': cur8, 'conflicts': len(conflicts),
                              'conflict_file': os.path.basename(cpath)})
                print('⛔ 同一段内容两边都改了，合并不出合格结果（%d 处）：' % len(conflicts))
                for lo, hi, _b, mseg in conflicts[:3]:
                    print('   · base 第 %d-%d 行，你改成：%s' % (lo + 1, hi, (mseg[:1] or ['(空)'])[0]))
                print('   文件没动。冲突稿：' + cpath)
                print('   你的改动已记进变更史，不会丢；看一眼最新内容再重做。')
                _sys.exit(3)
            new_body = '\n'.join(merged)
            _snap_put(title, cur8, body_before)
            _snap_put(title, expect, base)
            ol = lines[:i2 + 1] + new_body.split('\n') + lines[e2:]
            n8 = commit(ol, 'merge', title, note='基于 %s 合并到 %s' % (expect, cur8))
            _snap_put(title, n8, new_body)
            print('已合并「%s」：把你基于 %s 的改动重放到最新（%s）上，%d 字 → %d 字（sha8 %s → %s）'
                  % (title, expect, cur8, len(body_before.encode('utf-8')),
                     len(new_body.encode('utf-8')), cur8, n8))
            print('两边都保留：别人的行原样在，你的改动也落了。其他 %d 节一字未动' % (len(ss) - 1))
            return

        # 既没给 expect 也没给行级意图：整节替换，必须明说会盖到别人
        new_body = raw.rstrip('\n')
        _snap_put(title, cur8, body_before)
        ol = lines[:i2 + 1] + new_body.split('\n') + lines[e2:]
        n8 = commit(ol, 'write-force', title, note='未带 --expect-sha8 的整节替换')
        _snap_put(title, n8, new_body)
        print('已写「%s」：%d 字 → %d 字（sha8 %s → %s）'
              % (title, len(body_before.encode('utf-8')), len(new_body.encode('utf-8')), cur8, n8))
        print('⚠ 这是整节替换：若别人在我读它之后往这一节加过行，那些行已被盖掉（原文在变更史里）。')
        print('  下次改同一节请带 --expect-sha8（自动合并），或改用 --add-line / --del-line。')


def _audit_evidence(root, skip):
    """把库与技能目录里的文本读成一批字符串，供 audit 查"这条做完的说法有没有实物/记录撑着"。"""
    out = []
    skd = os.path.dirname(os.path.abspath(__file__))
    for base in (root, skd):
        if not base or not os.path.isdir(base):
            continue
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in ('.git', '__pycache__', '.state_snapshots')]
            for fn in fns:
                if fn.endswith('.bak') or fn.endswith('.tmp'):
                    continue
                if not fn.endswith(('.md', '.json', '.jsonl', '.py', '.txt')):
                    continue
                p = os.path.join(dp, fn)
                if os.path.abspath(p) == skip:
                    continue
                try:
                    out.append(open(p, encoding='utf-8', errors='replace').read())
                except Exception:
                    pass
    return out


def _task_audit(lines, root):
    """任务清单"状态"列的批量核对：只核能自动判的，判不了的一律列给人。

    能自动判的只有两类，别的都是装懂：
      1. 说法里的实物文件在不在（`xxx.py` / `xxx.json` …）—— 存在性是可判的
      2. 说法里引的编号在台账/流水里查不查得到（D8x / b10x / U0x）—— 有记录是可判的
    "42/42 全过""连跑三轮 +21"这类要通过率，本机不放包、不实跑就没法判，
    必须显式标成"需实跑"交给人，不许拿一句"已核对"糊过去。
    """
    import sys as _sys
    import re as _re

    RE_FILE = _re.compile(r'`([A-Za-z0-9_.\-]+\.(?:py|json|jsonl|md|csv|zip))`')
    RE_ID = _re.compile(r'(?<![A-Za-z0-9])(D\d{2,3}|U\d{2,3}|b\d{2,3})(?![0-9A-Za-z])')
    RE_RATE = _re.compile(r'\d+\s*/\s*\d+|连跑|跑出|-10|exit\s*[0-9]')

    def sep(l):
        return l.startswith('|') and set(l.replace('|', '').strip()) <= set('-: ')

    heads = []
    for n in range(len(lines)):
        if lines[n].startswith('|') and n + 1 < len(lines) and sep(lines[n + 1]):
            heads.append((n, [c.strip() for c in lines[n].strip().strip('|').split('|')]))

    ev = _audit_evidence(root, os.path.abspath(TASK))
    alltext = '\n'.join(ev)
    skd = os.path.dirname(os.path.abspath(__file__))
    onames = set()
    for base in (root, skd):
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in ('.git', '__pycache__')]
            for fn in fns:
                onames.add(fn)

    rows, by = [], {}
    for hi, (hn, cells) in enumerate(heads):
        if '状态' not in cells:
            continue
        si = cells.index('状态')
        ii = cells.index('编号') if '编号' in cells else (cells.index('类') if '类' in cells else 0)
        end = heads[hi + 1][0] if hi + 1 < len(heads) else len(lines)
        for n in range(hn + 2, end):
            l = lines[n]
            if not l.startswith('|') or sep(l):
                continue
            cs = [c.strip() for c in l.strip().strip('|').split('|')]
            if len(cs) <= si:
                continue
            st = cs[si]
            if '✅' not in st:
                continue          # 未完成的行本来就是"没做完"，不用核
            num = cs[ii] if ii < len(cs) else ''
            # 编号经常写在"事项"列（如「D56–D61 入台账」），不能只看"怎么算做完"那一列
            how = ' '.join(cs[j] for j in range(len(cs)) if j != si)
            files = RE_FILE.findall(how)
            ids = RE_ID.findall(how)
            if files:
                miss = [f for f in files if f not in onames]
                if miss:
                    rows.append((num, cs[1] if len(cs) > 1 else '', '无实物',
                                 '、'.join(miss) + ' 在库与技能目录里都找不到'))
                else:
                    rows.append((num, cs[1] if len(cs) > 1 else '', 'PASS',
                                 '、'.join(files) + ' 磁盘上有（包内未核）'))
            elif ids:
                miss = [x for x in ids if not _re.search(r'(?<![A-Za-z0-9])' + x + r'(?![0-9])', alltext)]
                if miss:
                    rows.append((num, cs[1] if len(cs) > 1 else '', '无记录',
                                 '、'.join(miss) + ' 台账/流水里查不到'))
                else:
                    rows.append((num, cs[1] if len(cs) > 1 else '', 'PASS',
                                 '、'.join(ids) + ' 编号有记录'))
            elif RE_RATE.search(how):
                rows.append((num, cs[1] if len(cs) > 1 else '', '需实跑', how[:52]))
            else:
                rows.append((num, cs[1] if len(cs) > 1 else '', '判不了', how[:52]))
            by[num] = rows[-1][2]

    if not rows:
        print('清单里没有标 ✅ 的行，无需核对')
        return 0
    print('标"已完成"的行共 %d 条，逐一核对（只核实物存在性与编号记录）：' % len(rows))
    order = {'无实物': 0, '无记录': 1, '需实跑': 2, '判不了': 3, 'PASS': 4}
    for num, what, st, note in sorted(rows, key=lambda x: (order.get(x[2], 9), x[0])):
        print('  %-6s %-8s %-14s %s' % (num, st, what[:14], note))
    cnt = {}
    for _n, _w, st, _note in rows:
        cnt[st] = cnt.get(st, 0) + 1
    print('汇总：' + '、'.join('%s %d' % (k, cnt[k]) for k in sorted(cnt, key=lambda x: order.get(x, 9))))
    toobad = cnt.get('无实物', 0) + cnt.get('无记录', 0)
    human = cnt.get('需实跑', 0) + cnt.get('判不了', 0)
    if toobad:
        print('⛔ %d 条标了"已完成"但拿不出实物/记录 —— 要么补证据，要么把状态改回未完成。' % toobad)
    if human:
        print('交给人的 %d 条：要靠率/靠实跑/或说法太虚，工具判不了，不假装判了。' % human)
    return 1 if toobad else 0


def cmd_task(args):
    """覆盖层第二处：任务清单按行写。只碰自己那一行或追加一行，别的行逐字节保留。

    和状态层分块同理：整文件读写会让"改 A3 的窗口"把"刚加 B7 的窗口"一起写回旧版。
    """
    import hashlib
    import sys as _sys
    op = key = wr = expect = table = None
    want = False
    audit = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--rows':
            want = True; i += 1
        elif a == '--audit':
            audit = True; i += 1
        elif a == '--row' and i + 1 < len(args):
            op, key = 'row', args[i + 1]; i += 2
        elif a == '--append' and i + 1 < len(args):
            op, key = 'append', args[i + 1]; i += 2
        elif a == '--write' and i + 1 < len(args):
            wr = args[i + 1]; i += 2
        elif a == '--table' and i + 1 < len(args):
            table = args[i + 1]; i += 2
        elif a == '--expect-sha8' and i + 1 < len(args):
            expect = args[i + 1]; i += 2
        else:
            print('用法：task [--rows] [--row <编号> | --append <编号>] --write <文件|-> [--expect-sha8 <8位>] [--table <小节>]')
            print('  --rows          列出所有条目（行号 / 编号 / 摘要）')
            print('  --row <编号>     整行替换这一条（列数必须与表头一致，否则拒绝）')
            print('  --append <编号>  在该小节表格末尾加一行（--table 指定小节，省略则用最后一张表）')
            _sys.exit(2)

    if not os.path.exists(TASK):
        print('无任务清单：' + TASK); _sys.exit(2)
    text = open(TASK, encoding='utf-8', newline='').read()
    lines = text.split('\n')

    def cell0(l):
        return l.split('|')[1].strip() if l.count('|') >= 2 else ''

    def _sep(l):
        return l.startswith('|') and set(l.replace('|', '').strip()) <= set('-: ')

    def is_row(lines, n):
        """数据行：排除分隔行，也排除表头（表头的下一行是分隔行）。
        表头不排除的话，第一列会被当成编号"编号"，命中 7 张表——实测踩过。"""
        l = lines[n]
        if not l.startswith('|'):
            return False
        if _sep(l):
            return False
        if n + 1 < len(lines) and _sep(lines[n + 1]):
            return False
        return True

    if want:
        for n, l in enumerate(lines):
            if is_row(lines, n):
                print('  行 %-3d  %-6s %s' % (n + 1, cell0(l), l.strip()[:60]))
        return

    if audit:
        return _task_audit(lines, os.path.dirname(os.path.abspath(TASK)))

    if op is None or wr is None:
        print('需要 --row <编号> 或 --append <编号>，并给 --write'); _sys.exit(2)

    raw = _sys.stdin.read() if wr == '-' else open(wr, encoding='utf-8', newline='').read()
    raw = raw.replace('\r\n', '\n').strip('\n')
    newl = [x for x in raw.split('\n') if x.strip()]
    if len(newl) != 1:
        print('⛔ 只接受一行：给的是 %d 行。一行一条，多了就不是"按行写"了。' % len(newl))
        _sys.exit(2)
    nl = newl[0].strip()
    if not nl.startswith('|') or nl.count('|') < 3:
        print('⛔ 不是表格行：' + nl[:60]); _sys.exit(2)

    with _wlock('任务清单按行写'):
        text = open(TASK, encoding='utf-8', newline='').read()
        lines = text.split('\n')
        hits = [n for n in range(len(lines)) if is_row(lines, n) and cell0(lines[n]) == key]
        if op == 'row':
            if not hits:
                print('⛔ 清单里没有编号 %s' % key); _sys.exit(2)
            if len(hits) > 1:
                print('⛔ 编号 %s 出现 %d 次（行 %s），无法判定改哪一行' % (key, len(hits), hits)); _sys.exit(2)
            n = hits[0]
            old = lines[n]
            if nl.count('|') != old.count('|'):
                print('⛔ 列数不一致：原行 %d 个竖线，新行 %d 个——写进去会把表格写坏'
                      % (old.count('|'), nl.count('|')))
                _sys.exit(2)
            cur = hashlib.sha256(old.encode('utf-8')).hexdigest()[:8]
            if expect and expect != cur:
                print('⛔ 行级校验不过：这一行现在是 sha8 %s，你手上是 %s（有人改过，先看一眼）' % (cur, expect))
                _sys.exit(3)
            lines[n] = nl
            pos = n
        else:
            if hits:
                print('⛔ 编号 %s 已存在（行 %d），追加会重号；要改它用 --row' % (key, hits[0] + 1))
                _sys.exit(2)
            if table:
                ss = _sections(text)
                cand = [x for x in ss if x[0] == table] or [x for x in ss if table in x[0]]
                if len(cand) != 1:
                    print('⛔ 表格所在小节"%s"匹配到 %d 个' % (table, len(cand))); _sys.exit(2)
                a1, b1 = cand[0][1], cand[0][2]
            else:
                a1, b1 = 0, len(lines)
            last = None
            for n in range(a1, b1):
                if is_row(lines, n):
                    last = n
            if last is None:
                print('⛔ 该小节里没有表格行，不知道该加在哪'); _sys.exit(2)
            ref = lines[last].count('|')
            if nl.count('|') != ref:
                print('⛔ 列数不一致：表里是 %d 个竖线，新行 %d 个' % (ref, nl.count('|'))); _sys.exit(2)
            lines[last + 1:last + 1] = [nl]
            pos = last + 1

        before = {n: hashlib.sha256(l.encode('utf-8')).hexdigest() for n, l in enumerate(lines) if op == 'row' and n != pos}
        out = '\n'.join(lines)
        tmp = TASK + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(out); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, TASK)

        after_lines = open(TASK, encoding='utf-8', newline='').read().split('\n')
        if op == 'row':
            drift = [n for n, h in before.items()
                     if n < len(after_lines) and hashlib.sha256(after_lines[n].encode('utf-8')).hexdigest() != h]
            if drift:
                print('⛔ 其他行也变了（不该发生）：行 %s' % drift[:5]); _sys.exit(1)
            print('已改写第 %d 行（编号 %s）；其余 %d 行逐行比对一字未动' % (pos + 1, key, len(before)))
        else:
            print('已在第 %d 行后追加（编号 %s）；原有行全部原样' % (pos + 1, key))


def cmd_flow(kw):
    if not os.path.exists(FLOW): print('无流水文件'); return
    lines = open(FLOW, encoding='utf-8').read().split('\n')
    idx = [i for i, l in enumerate(lines) if l.startswith('## ')]
    out = 0
    for k, i in enumerate(idx):
        end = idx[k + 1] if k + 1 < len(idx) else len(lines)
        blk = '\n'.join(lines[i:end])
        if kw and kw not in blk: continue
        print('=' * 60); print(blk.rstrip()); out += 1
        if out >= 3: break
    if not out: print('未命中')

def _line_count(p):
    if not os.path.exists(p):
        return 0
    n = 0
    with open(p, 'rb') as f:
        for _ in f:
            n += 1
    return n


def _head_lines(path):
    """只取标题行（以 # 开头）。正文里提到的 v6.x / D1xx 不算编号占用。"""
    if not os.path.exists(path):
        return []
    return [l for l in open(path, encoding='utf-8').read().split('\n')
            if re.match(r'^#{2,4}\s', l)]


def _ids(path, pat=None):
    """台账里已占用的 D 编号。逐标题行找，兼容 `## b119 · 2026-11-16 · v6.17` 这种混合标题。"""
    out = []
    for l in _head_lines(path):
        m = re.search(r'\bD(\d+)\b', l)
        if m:
            out.append(int(m.group(1)))
    return out


def _parse_ver(txt):
    """把 `6.41` / `v6.41.1` 解析成 (6,41,0) / (6,41,1)；不合法返回 None。

    只有这一处解析。之前有三套（流水正则、SKILL 正则、log --version 手写 split），
    其中手写那套对 `6.41.0.0` 会拼出 4 元组，与库里的 3 元组永不相等——
    撞号检查被静默绕过（实测：已占用的 v6.41 照样写进去了）。
    """
    m = re.fullmatch(r'[vV]?(\d+)\.(\d+)(?:\.(\d+))?', (txt or '').strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _vers(path):
    """流水里已占用的版本号。**只认 `## ` 开头的块头**，`###` 子标题一律不算。

    为什么必须收到一级（D264）：以前扫 2–4 级标题，任何子标题里写一句"参考 v7.0 的做法"
    就被当成"本库已占用到 v7.0"，下一次发号直接跳到 v7.1，把整条版本线顶穿；
    顺带把 `###` 算成"块"，块数虚高（实测 96 而真块 48）。
    兼容 `## b119 · 2026-11-16 · v6.17` 这种中文序号块；交付物块（`## 交付物 v4.36`）
    记的是别人的版本，跳过不进池（D262）。三段版本号要认全，只认两段会把 6.41.1 截成 6.41（Q11 / D165）。
    """
    out = []
    if not os.path.exists(path):
        return []
    for l in open(path, encoding='utf-8').read().split('\n'):
        if not l.startswith('## '):
            continue
        if l[3:].strip().startswith('交付物'):
            continue
        m = re.search(r'\bv(\d+\.\d+(?:\.\d+)?)\b', l)
        if m:
            v = _parse_ver(m.group(1))
            if v:
                out.append(v)
    return out


def _vfmt(t):
    """(6,41,0) → 'v6.41'；(6,41,1) → 'v6.41.1'。三段只在第三段非 0 时显示。"""
    t = tuple(t) + (0,) * (3 - len(tuple(t)))
    return ('v%d.%d.%d' % t[:3]) if t[2] else ('v%d.%d' % t[:2])


def _skill_version_sync(v):
    """把 SKILL.md 的 version: 写成刚发的号。

    版本号只留一个真相：流水（工具发号）。SKILL.md 是投影，不再是人手填的第二真相。
    以前是"人先改 SKILL、工具再读它去比对"，两个源靠判据弥合——判据修不完。
    只在"库 = 默认库"时回写：副本/隔离库跑发版，不该改主安装位的说明书。
    """
    if os.path.realpath(ROOT) != os.path.realpath(DEFAULT_ROOT):
        print('  （隔离模式：这不是默认库，SKILL.md 不回写）')
        return False
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SKILL.md')
    try:
        src = open(p, encoding='utf-8').read()
        new = re.sub(r'(?m)^(\s*version\s*:\s*).*$', lambda m: m.group(1) + _vfmt(v)[1:], src, count=1)
        if new == src:
            return False
        tmp = p + '.tmp'
        open(tmp, 'w', encoding='utf-8').write(new)
        os.replace(tmp, p)
        print('  SKILL.md 版本已同步为 %s（以后不必手改）' % _vfmt(v))
        return True
    except Exception as e:
        print('  ⚠ SKILL.md 回写失败（%s）：发号成功，说明书落后一版，打包前手工对齐' % e)
        return False


def _skill_version():
    """SKILL.md 写的 version:，转成 (大, 小)。读不到就返回 None，不猜。

    D129：发号原先只看流水里最新块 +1，完全不看 SKILL 的 version:——
    这一轮实际发 v6.33，工具发出 v6.31，差两版。两套线各走各的，包版本和账版本
    从此对不上，而且没人会发现（两个数字看着都"正常"）。
    """
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SKILL.md')
        with open(p, encoding='utf-8') as f:
            head = f.read(3000)
        # 三段也要认：6.41.1 曾被截成 6.41，于是"发新版却被判撞号"（D165）。
        m = re.search(r'^\s*version\s*:\s*([vV]?\d+\.\d+(?:\.\d+)?)', head, re.M)
        if m:
            return _parse_ver(m.group(1))
    except Exception:
        pass
    return None


def _append_locked(path, eol, data):
    """锁内追加，返回写入前的行数 n0 与写入后的行数 n1。"""
    with _wlock('记账'):
        n0 = _line_count(path)
        need_nl = False
        if n0 and os.path.getsize(path) > 0:
            with open(path, 'rb') as f:
                f.seek(-1, os.SEEK_END)
                need_nl = f.read(1) != b'\n'
        with open(path, 'ab') as f:
            if need_nl:
                f.write(eol.encode('utf-8'))
                n0 += 1
            f.write(data)
        n1 = _line_count(path)
    return n0, n1


def _guard_hint():
    """开工第一条就顺手报一次账目倒退。放在 log status 末尾，不阻断任何流程。

    为什么挂这里：谁最可能发现账被覆盖？不是当事人——覆盖的人正忙着别的事，
    也不会回头看。是下一个开工的人，而他第一条命令就是 log status。
    """
    snaps = _guard_list()
    if not snaps:
        return
    n, m = snaps[-1]
    base = m.get('files') or {}
    try:
        cur = _guard_fp()
    except Exception:
        return
    bad, _chg = _guard_diff_fp(base, cur)
    if not bad:
        return
    print('\n⚠ 账面与快照 %s（%s）对不上：' % (n, m.get('ts')))
    for x in bad:
        print('  ✗ ' + x)
    print('  查清是谁覆盖的；能捞回来就 guard restore --tag %s' % m.get('name'))


def cmd_log_status():
    """新窗口开工第一条命令：不用问人、不用翻文件，就知道上一笔记到哪、下一条该记什么号。"""
    # v6.59：守卫告警提到最前面。以前它在输出最后一行，任何人截前 N 行看（Select-Object -First、
    # head、面板只留前若干行）都会漏掉——实测 11-23 那次就是告警在末尾被我自己截掉的。
    _guard_hint()
    d = _ids(DEFECT)
    v = _vers(FLOW)
    print('缺陷台账   %s' % DEFECT)
    print('  块 %d / 最大 D%d / 下一条 D%d / 文件 %d 行'
          % (len(d), max(d) if d else 0, (max(d) + 1) if d else 1, _line_count(DEFECT)))
    print('发版流水   %s' % FLOW)
    nxt = ''
    if v:
        mx = max(v)
        nv = (mx[0], mx[1] + 1, 0)
        skv = _skill_version()
        if skv and skv > nv:
            nv = skv
        nxt = ' / 下一版 ' + _vfmt(nv)
    print('  块 %d / 最新 v%s%s / 文件 %d 行'
          % (len(v), (_vfmt(max(v))[1:]) if v else '—', nxt, _line_count(FLOW)))
    h = _state_hist()
    print('状态变更史 %s' % h)
    print('  记录 %d 行' % _line_count(h))
    if os.path.exists(h):
        for line in open(h, encoding='utf-8').read().strip().split('\n')[-3:]:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                print('  · %s %s %s %s' % (r.get('ts', ''), r.get('host', ''),
                                           r.get('action', ''), r.get('section', r.get('what', ''))[:40]))
            except Exception:
                print('  · ' + line[:80])
    for name, p in (('使用台账', os.path.join(ROOT, '使用台账.jsonl')),
                    ('探针台账', os.path.join(ROOT, '探针执行台账.jsonl'))):
        print('%-9s   %s   记录 %d 行（走 %s）'
              % (name, p, _line_count(p), 'log use' if '使用' in name else 'probelog'))
    print('\n近期事由（别的窗口在干什么，按事由去重、留最新一笔）：')
    for p, lab in ((DEFECT, '缺陷'), (FLOW, '流水')):
        if not os.path.exists(p):
            continue
        cur, rows = '', []
        for l in open(p, encoding='utf-8'):
            if re.match(r'^#{2,4}\s', l):
                cur = l.strip()
            elif l.startswith('- 事由：'):
                rows.append((l.strip()[len('- 事由：'):], cur))
        seen = {}
        for tr, head in rows:
            # 事由行是「事由 · 机器 · 时间」，事由本身可能含「·」。从右切两刀，剩下的才是事由，
            # 取左边第一段会把「重构·状态层」这样的名字截成「重构」，两件事被误并成一件。
            name = tr.rsplit('·', 2)[0].strip() if tr.count('·') >= 2 else tr.split('·')[0].strip()
            seen[name] = (tr, head)
        for name, (tr, head) in list(seen.items())[-6:]:
            print('  [%s] %s' % (lab, tr))
            print('        ← %s' % head[:56])
        if not rows:
            print('  [%s] 还没有带事由的记录' % lab)
    print('任务清单   走 task --append；时效台账 走 verify。')
    print('记账：log defect|release 用 --title 让工具发号（防撞号、防格式漂）；'
          'log change --text <文件|->')


def cmd_log(args):
    """记一笔。锁内排队、先到先记、回执报行号；编号与标题格式由工具发，不靠人手抄。

    为什么这么做（2026-11-16）：这库是公地，任何窗口都能记，窗口还会不断更替。
    只要编号靠人写，两个窗口同时记一定撞号；只要标题格式靠人抄，一定会漂——
    实测就漂过：历史流水是 `## v6.18（日期）· 标题`，新记的写成 `### v6.19`；
    历史缺陷是 `### D109 · 标题`，新记的写成 `### D118 标题`。
    所以：扫描在锁内、发号在锁内、格式由工具拼，人只给标题和正文。

    用法：
      log status                                    上一笔记到哪、下一条什么号
      log defect  --title <标题> [--body <文件|->]   自动发 D 号
      log release --title <标题> [--body <文件|->]   自动发版本号（日期取今天）
      log release --version v6.20 --title <标题>     指定版本时仍查撞车
      log defect|release --text <文件|->             原样整块追加（会查编号是否已被占）
      log change  --text <文件|->                    状态变更史，自动带 ts/host
    """
    # 库根守卫（D121）：库不像库时明确报错，绝不输出一张 0 行看板假装正常。
    # 反例：不设 EXP_ROOT 且默认路径算错时，`log defect --title ...` 会 exit 0、
    # 回执"已记 D1"，账落在虚空里——记账从来没失败，也从来没记上。
    # 守卫必须排在 status 之前：0 行看板和"库是空的"长得一模一样，先骗自己再骗人。
    if not _looks_like_lib(ROOT):
        sys.stderr.write('✗ 库没找到：%s 不像一座库（无台账、无 current-work-state.md）\n' % ROOT)
        try:
            sys.stderr.write('  技能目录反推应到：%s\n' % os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'experience'))
        except Exception:
            pass
        sys.stderr.write('  指定真库：设 EXP_ROOT=<库根> 再跑同一条命令\n')
        sys.exit(2)

    if args and args[0] == 'status':
        return cmd_log_status()

    OPTS = ('--title', '--body', '--text', '--version', '--task', '--deliverable')
    kind = args[0] if args else ''
    if kind not in ('defect', 'release', 'change'):
        print('用法：log <defect|release|change> ...（输 log status 看账到哪了）')
        print('  log defect  --title <标题> [--body <文件|->] [--task <事由>]')
        print('  log release --title <标题> [--body <文件|->] [--task <事由>]')
        print('  log release --deliverable <外部版本> --title <标题>   记别人的发版，不占本库版本线')
        print('  log change  --text <文件|->')
        print('  --task 写清"这笔属于哪件事"，多窗口并行时全靠它事后对账')
        return
    opt = {}
    for i, x in enumerate(args):
        if x in OPTS and i + 1 < len(args):
            opt[x] = args[i + 1]

    def read_src(v):
        if v == '-':
            return sys.stdin.read()
        return open(v, encoding='utf-8', newline='').read()

    norm = lambda s: s.replace('\r\n', '\n').replace('\r', '\n').strip('\n')

    # ---------- change：只追加 ----------
    if kind == 'change':
        if '--text' not in opt:
            print('⛔ change 要给 --text <文件|->')
            sys.exit(1)
        raw = norm(read_src(opt['--text']))
        if not raw.strip():
            print('⛔ 内容是空的，不记')
            sys.exit(1)
        import datetime
        payload = json.dumps({'ts': datetime.datetime.now().isoformat(timespec='seconds'),
                              'host': _hst(), 'action': 'note', 'section': '',
                              'note': raw, 'what': raw}, ensure_ascii=False) + '\n'
        n0, n1 = _append_locked(_state_hist(), '\n', payload.encode('utf-8'))
        _hist_md_rebuild()
        print('已记「状态变更史」：第 %d-%d 行（现共 %d 行）' % (n0 + 1, n1, n1))
        _auto_guard()
        return

    path, eol, label = ((DEFECT, '\r\n', '缺陷台账') if kind == 'defect'
                        else (FLOW, '\r\n', '发版流水'))
    body = norm(read_src(opt['--body'])) if '--body' in opt else ''

    # ---------- -text：原样追加，但查撞号 ----------
    if '--text' in opt:
        raw = norm(read_src(opt['--text']))
        if not raw.strip():
            print('⛔ 内容是空的，不记')
            sys.exit(1)
        with _wlock('记账'):
            if kind == 'defect':
                used = _ids(DEFECT)
                hit = [int(m.group(1)) for m in re.finditer(r'^#{2,4}\s*D(\d+)', raw, re.M)]
            else:
                used = _vers(FLOW)
                hit = []
                for m in re.finditer(r'^#{2,4}\s*(?:b\d+\s*·\s*)?v(\d+\.\d+)', raw, re.M):
                    a, b = m.group(1).split('.')
                    hit.append((int(a), int(b)))
            dup = [x for x in hit if x in used]
            if dup:
                tag = ('D%d' % dup[0]) if kind == 'defect' else _vfmt(dup[0])
                print('⛔ %s 这个号已经被占了（共 %d 个已用）。' % (tag, len(used)))
                print('   改用 --title 让工具发号，别自己数；或者换个号再记。')
                sys.exit(1)
            n0 = _line_count(path)
            need_nl = False
            if n0 and os.path.getsize(path) > 0:
                with open(path, 'rb') as f:
                    f.seek(-1, os.SEEK_END)
                    need_nl = f.read(1) != b'\n'
            with open(path, 'ab') as f:
                if need_nl:
                    f.write(eol.encode('utf-8'))
                    n0 += 1
                f.write((raw.replace('\n', eol) + eol).encode('utf-8'))
            n1 = _line_count(path)
        print('已记「%s」：第 %d-%d 行（现共 %d 行）' % (label, n0 + 1, n1, n1))
        print('  ' + path)
        return

    # ---------- --title：工具发号，格式由工具拼 ----------
    title = (opt.get('--title') or '').strip()
    if not title:
        print('⛔ 要给 --title <标题>，或者用 --text <文件|-> 整块追加')
        sys.exit(1)
    # 正文必须是实内容（D263）：以前 --body 可省，写出一条只有标题+事由的空壳块，
    # 看着像"记过了"，实际改了什么、核证数字、包哈希一个字都没有。
    if not body.strip():
        print('⛔ 正文不能空：--body <文件|-> 必填。')
        print('   只写标题会落一条"看起来记过、实际没内容"的块，追溯时等于没记。')
        print('   要粘一段现成文本：log '<defect|release>' --title <标题> --body -   （然后从标准输入读）')
        sys.exit(1)
    title = title.replace('\n', ' ').strip()
    import datetime
    date = datetime.date.today().isoformat()

    with _wlock('记账'):
        if kind == 'defect':
            used = _ids(DEFECT)
            new_id = (max(used) + 1) if used else 1
            head = '### D%d · %s' % (new_id, title)
            tag = 'D%d' % new_id
        else:
            deliv = (opt.get('--deliverable') or '').strip()
            if deliv and '--version' in opt:
                print('⛔ --deliverable 与 --version 只能给一个：前者记别人的版本，后者是本库自己要发到哪一版。')
                sys.exit(2)
            if deliv:
                # 记别人的发版：块头用交付物版本，**不发本库号、不回写 SKILL.md**。
                # 这是 D262 的根——以前"记一条别项目发版"必然消耗本库版本号，还留下空号。
                dv = _parse_ver(deliv)
                if not dv:
                    print('⛔ --deliverable 版本号读不懂：%s。写成 4.36.0 这种（最多三段）。' % deliv)
                    sys.exit(2)
                head = '## 交付物 %s（%s）· %s' % (_vfmt(dv), date, title)
                tag = '交付物 ' + _vfmt(dv)
            else:
                used = _vers(FLOW)
                if '--version' in opt:
                    new_v = _parse_ver(opt['--version'])
                    if not new_v:
                        print('⛔ 版本号读不懂：%s。写成 6.43 或 6.43.1（最多三段）。' % opt['--version'])
                        sys.exit(2)
                    if new_v in used:
                        print('⛔ %s 这个版本已经被占了。用 --title 让工具发号。' % _vfmt(new_v))
                        sys.exit(1)
                else:
                    auto = (max(used)[0], max(used)[1] + 1, 0) if used else (1, 0, 0)
                    # 版本号只有一个真相：流水。SKILL.md 不再是第二个真相，也就不再拿它去比对。
                    # 要跳号就显式 --version（那是"人的意图"，走参数，不走改文件）。
                    new_v = auto
                head = '## %s（%s）· %s' % (_vfmt(new_v), date, title)
                tag = _vfmt(new_v)

        # 事由行：多窗口并行做不同的事，全靠这一行事后对上是哪件事。没有它，窗口一关就没人知道 D119 是谁留下的。
        task = (opt.get('--task') or '').strip().replace('\n', ' ') or '未标事由'
        trace = '- 事由：%s · %s · %s' % (task, _hst(),
                                        datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
        block = head + '\n\n' + trace + '\n\n' + (body + '\n' if body else '')
        n0 = _line_count(path)
        need_nl = False
        if n0 and os.path.getsize(path) > 0:
            with open(path, 'rb') as f:
                f.seek(-1, os.SEEK_END)
                need_nl = f.read(1) != b'\n'
        with open(path, 'ab') as f:
            if need_nl:
                f.write(eol.encode('utf-8'))
                n0 += 1
            f.write((block.replace('\n', eol) + eol).encode('utf-8'))
        n1 = _line_count(path)
        if 'new_v' in locals():   # 只有 release 块定义 new_v；defect 块没有版本号
            _skill_version_sync(new_v)

    print('已记「%s」%s：第 %d-%d 行（现共 %d 行）' % (label, tag, n0 + 1, n1, n1))
    print('  ' + path)
    _auto_guard()


def cmd_event(eid):
    ev = [e for e in events() if e.get('event_id') == eid]
    if not ev: print('未命中 event_id：' + eid); return
    for e in ev: print(json.dumps(e, ensure_ascii=False, indent=2))

def cmd_list():
    for c in load_cards():
        print(c['file'].ljust(56) + ' ' + (c['created'] or '—') + '  ' + c['desc'][:58])

def cmd_gaps():
    cs = load_cards()
    no_e = [c['file'] for c in cs if not c['expiry']]
    no_f = [c['file'] for c in cs if not c['failpath']]
    print('失效条件：' + str(len(cs) - len(no_e)) + '/' + str(len(cs)) + ' 已声明，待补 ' + str(len(no_e)))
    print('失败路径：' + str(len(cs) - len(no_f)) + '/' + str(len(cs)) + ' 已声明，待补 ' + str(len(no_f)))
    if len(sys.argv) > 2 and sys.argv[2] in ('expiry', 'failpath'):
        for f in (no_e if sys.argv[2] == 'expiry' else no_f): print('  - ' + f)

STOP = {'the','and','for','not','are','was','has','with','from','that','this','at','in','is','to','of','on','or','as','by','it','be'}

def _tok(t):
    out = []
    for x in re.split(r'[^0-9a-zA-Z\u4e00-\u9fa5]+', (t or '').lower()):
        if len(x) < 2 or x in STOP:
            continue
        if re.fullmatch(r'[a-z]+', x) and len(x) < 3:
            continue
        out.append(x)
    return out

STOPQ = set('的了是在有和就不我你他它这个一上下中都而及与或吗呢啊'.split())

def _too_weak(q):
    t = (q or '').strip()
    if len(t) < 2:
        return True
    return all(ch in STOPQ or ch in '的了吗呢啊是在有和' for ch in t)

def cmd_match(q):
    try: _budget = int(os.environ.get('EXPRECALL_MAX_PROC', '20'))
    except Exception: _budget = 20
    _ps = _procs()
    if len(_ps) >= _budget:
        print('⛔ 熔断：recall.py 进程数 ' + str(len(_ps)) + ' ≥ 预算 ' + str(_budget) + '，拒绝起闸。')
        print('   先跑 `recall.py reap` 存现场再两阶段终止；预算可用 EXPRECALL_MAX_PROC 调整。')
        sys.exit(4)
    # 静态门禁前置：代码里还有静默清理/丢环境变量的写法，先别验库（验的也是坏代码）
    try:
        cmd_scan(os.path.abspath(__file__))
    except SystemExit as _sx:
        if _sx.code:
            print('⛔ 静态门禁不过：先修代码，再验库（`recall.py scan` 看明细）。')
            sys.exit(1)

    _e_ok, _e_note = _ensure_index()
    if _e_note: print(_e_note)
    if _too_weak(q):
        print('查询过短或全是停用词，判不出相关性（换一句现象描述，例如"改名后功能消失"）；拒绝返回全部卡片'); return
    q = (q or '').strip()[:240]
    if not q:
        print('需要参数：match <报错原文/现象描述>'); return
    if not os.path.exists(MIDX):
        print('缺 匹配索引.json，先跑 build_index'); return
    try:
        idx = json.load(open(MIDX, encoding='utf-8'))
    except Exception as ex:
        print('索引文件损坏或非法 JSON（' + MIDX + '）：' + str(ex)[:60]); return
    qt = _tok(q); qlow = (q or '').lower(); scored = []
    # v6.55（C-1）：死路进检索域，但分两张表，不混排。
    # 为什么分表：实测把死路并进主打分后，30 句措辞对齐的评测集从 27/30 掉到 21/30，
    # 库外误报升到 25%——两类证据（措辞相同 vs 现象同构）混在一个分数里必然互相顶。
    # 所以主表只放"问句与卡上措辞对得上"的结论级命中（沿用 v6.54 判据，未改精度），
    # 死路另列一张线索表，只陈述"这张卡的失败路径里有类似现象"，不冒充结论。
    import math
    QB = sorted({b for b in _bg(q)})
    docs = {}
    for name, v in idx.items():
        docs[name] = {'A': ((v.get('desc') or '') + ' ' + ' '.join(v.get('tags') or [])).lower(),
                      'F': (v.get('fail') or '').lower()}
    df = {}
    for d in docs.values():
        for b in set(_bg(d['A'])):
            df[b] = df.get(b, 0) + 1
    N = len(docs) or 1

    def _idf(b):
        return math.log(1 + (N - df.get(b, 0) + 0.5) / (df.get(b, 0) + 0.5))

    tot = sum(_idf(b) for b in QB) or 1.0
    # 死路只在"问的是出了什么问题"时才有意义。没有现象词的问题（"怎么写""几分钟""要几步"）
    # 即使字面凑上，也不是在问死路——这一条把库外散布从 10% 压到 0。
    DEADWORD = re.search(r'(报|误|失败|不存在|漏|不符|错|假|漂移|截断|覆盖不全|塌|丢|撞|过期|失效|回滚|没核|只核|以为|当成|退回)', q)
    dead = []
    for name, v in docs.items():
        sc = 0; why = []; named = 0
        for tr in (idx[name].get('triggers') or []):
            if tr.lower() in qlow: sc += 8 + min(len(tr), 12); why.append('触发词' + tr); named += 1
        for t in qt:
            # v6.56：通用疑问词不算证据、也不给分。它们回答"问的是什么句式"，
            # 不回答"问的是哪件事"——"如何用/几步/为什么"在任何一张卡里都能撞上。
            if t in _GENERIC_Q:
                continue
            if t in v['A']: sc += 2; why.append('描述' + t); named += 1
            for tag in (idx[name].get('tags') or []):
                if t in tag.lower(): sc += 2; named += 1
            for e in (idx[name].get('entities') or []):
                if t in e.lower(): sc += 1; named += 1
            if sc == 0 and re.search(r'[\u4e00-\u9fa5]', t):
                bg = [t[i:i+2] for i in range(len(t)-1)]
                hb = sum(1 for b in bg if b in v['A'])
                if hb: sc += min(hb, 3)
        # v6.56（A）：错误结构命中给 +2。权重刻意压得比触发词（8+词长）低一档——
        # 结构词是"这类病的名字"，只是旁证；它只能把卡往前挪一点，不能替代"同一件事"的证据。
        for w in (idx[name].get('struct') or []):
            if w in q:
                sc += 2; why.append('结构' + w); named += 1
        tri = [q[i:i+3] for i in range(max(0, len(q)-2)) if re.search(r'[\u4e00-\u9fa5]', q[i:i+3])]
        hits3 = sum(1 for t3 in set(tri) if t3 and t3 in v['A'])
        if hits3: sc += 3 * min(hits3, 2)
        thr = 3 if (not re.search(r'[A-Za-z0-9_]', q) and len(q) <= 14) else 5
        # v6.56：强/弱的判据从"够分数"改成"够分数 **且有具名证据**"。
        # 为什么：实测 20 句库外问句里有 5 句被判"强"，依据栏是空的或只有一个通用词
        # （"描述部署"），全部靠双字/三字兜底凑到 5 分。分数能凑，证据不能凑——
        # 没有触发词/描述词/tag/实体/结构词任何一条具名证据的，一律只算线索（弱）。
        if sc >= thr: scored.append((sc, name, idx[name], why, named >= 1))
        # 死路侧：只看 fail 字段的覆盖度（正文/标签不算），用来回答"当初是怎么死的"
        if QB and v['F'] and DEADWORD:
            hitF = [b for b in QB if b in v['F']]
            if hitF:
                covF = sum(_idf(b) for b in hitF) / tot
                # 线索级的门槛比结论级低一档（0.23/4 元），因为改写问句必然丢几个虚词二元组；
                # 但它只会出现在"死路线索"里，永远不进主表，所以放宽不会污染结论。
                if covF >= _RECALL_WEAK[0] and len(hitF) >= _RECALL_WEAK[1]:
                    dead.append((covF, name, idx[name], len(hitF)))
    # v6.59：撤掉 bug_class（主干/细枝末节）次级排序。它是 0 张填写的手填分类，
    # 同分时"主干优先"从未真正生效；谁更贴题只看分数与具名证据，不靠人贴标签。
    scored.sort(key=lambda x: -x[0])
    dead.sort(key=lambda x: -x[0])
    try:
        top3 = [x[1] for x in scored[:3]]
        rec = {'ts': _today(), 'q': q[:120], 'top': top3[0] if top3 else None,
               'tops': top3, 'result': 'pending'}
        # 闸/探针里的调用不是"人的使用"：探针会反复跑同一句，实测一次 gate 就把
        # 同一问句追加 12 条，既让 eval --pending 永远清不完，也把真实使用淹掉。
        # 闸有自己的账（探针执行台账），使用台账只记真人真事。
        # v6.56 补：评测/诊断脚本批量喂问句时，不该写"人的使用账"。
        # 判据只看 _depth()==0 挡不住脚本（脚本不在探针里，depth 就是 0），
        # 实测诊断脚本跑 3 次就给台账灌进 60 条同句 pending，把"用没用过"淹掉。
        if _depth() == 0 and not os.environ.get('EXPRECALL_NO_RECORD'):
            _use_append_dedup(rec)
    except Exception: pass
    if not scored and not dead:
        print('未命中（换更具体的报错原文、命令名或文件名再试）'); return
    if scored:
        ns = sum(1 for x in scored if x[4])
        print('命中 ' + str(len(scored)) + ' 条（强 ' + str(ns) + ' / 弱 ' + str(len(scored) - ns) + '），按匹配度排序；只对前 3 条跑探针\n')
        if ns == 0:
            # v6.56：全是弱 = 只是字面沾边，别让人误读成"库里有答案"。
            print('（没有具名证据命中的卡：下面这些只是字面沾边，当线索看，别当结论）\n')
    for i, (sc, name, v, why, strong) in enumerate(scored[:6], 1):
        # v6.59：原来的 ［主干］/［细枝］ 换成"这张卡由哪条失效样本催生"（锚点）。
        # 有锚 = 这张卡背后有一条真实缺陷撑着；没锚 = 只是经验陈述，看的时候要知道差别。
        _se = str(v.get('source_event') or '').strip()
        print(str(i) + '. [' + ('强' if strong else '弱') + ' %.2f] ' % sc + name
              + '  半衰期:' + str(v.get('half_life', 'unknown'))
              + (('  锚:' + _se[:14]) if _se else '  锚:无'))
        print('    ' + v.get('desc', '')[:94])
        print('    依据: ' + '、'.join(sorted(set(why))[:4]))
        if i <= 3:
            pr = v.get('probe')
            if not pr:
                fs, _ = _freshness(name, _ledg())
                print('    探针: 无 —— ⚠ 时效未验（当参考，不可当闸）｜' + fs)
            else:
                try:
                    _argv, _ = _portable(pr['cmd'])
                    if _argv:
                        r = subprocess.run(_argv, capture_output=True, text=True, timeout=20, cwd=ROOT,
                                   env=dict(os.environ, EXPRECALL_DEPTH=str(_depth() + 1),
                                            EXPRECALL_PARENT_ROOT=os.path.realpath(ROOT), EXPRECALL_THIS_ROOT=os.path.realpath(ROOT),
                                            EXPRECALL_SK=os.path.abspath(__file__)))
                    else:
                        r = subprocess.run(pr['cmd'], shell=True, capture_output=True, text=True, timeout=20, cwd=ROOT,
                                   env=dict(os.environ, EXPRECALL_DEPTH=str(_depth() + 1),
                                            EXPRECALL_PARENT_ROOT=os.path.realpath(ROOT), EXPRECALL_THIS_ROOT=os.path.realpath(ROOT)))
                    tail = (r.stdout or r.stderr or '').strip().split('\n')[-1][:40]
                    if pr.get('expect_exit') is not None:
                        ok = (r.returncode == int(pr['expect_exit']))
                    elif pr.get('expect_min') is not None:
                        try: num = int(tail.split()[-1])
                        except Exception: num = 0
                        ok = num >= int(pr['expect_min'])
                    else:
                        ok = (r.returncode == 0)
                    l = _ledg()
                    l[name] = {'last_verified': _today(), 'result': 'ok' if ok else 'fail',
                               'next_due': _ttl(name, v, l), 'note': tail}
                    _save(l)
                    fs, nd = _freshness(name, l)
                    print('    探针: ' + ('仍成立' if ok else '⚠ 疑似失效，暂停当闸') + '  ' + tail)
                    print('    时效: ' + fs + ('（到期 ' + str(nd) + '）' if nd else ''))
                except Exception as ex:
                    print('    探针: 执行失败（' + str(ex)[:30] + '），按未验证处理')
        print()

    shown = {x[1] for x in scored[:6]}
    cand = [d for d in dead if d[1] not in shown][:3]
    if cand:
        print('死路线索（线索级：这些卡的「失败路径」里有相似现象，不是结论，别据此改判）')
        for covF, name, v, nm in cand:
            print('  · ' + name + '  死路覆盖 %.2f / %d 元' % (covF, nm))
            print('    ' + (v.get('desc') or '')[:80])
import datetime
LEDG = _resolve(('时效台账.json', '_时效台账.json'))
USE  = os.path.join(os.path.dirname(os.path.abspath(LEDG)), '使用台账.jsonl')


def _depth():
    """当前调用深度。父进程跑探针时会 +1 传下来；用来切断"闸→探针→闸"的自我递归。"""
    try: return int(os.environ.get('EXPRECALL_DEPTH', '0'))
    except Exception: return 0


def _use_append_dedup(rec):
    """追加一条使用记录；同一问句 / 同一天 / 同一批命中，视为同一次使用，不重复追加。

    为什么去重：使用台账按"一次检索一条"落账，但同一句话一天被问两遍很常见
    （闸里跑探针更会一次刷十几条）。重复行不携带任何新信息，却把 eval --pending
    的队列越堆越长——"待评价 200 多条"里大半是同一句。只压相邻的完全相同行，
    不做跨天合并：跨天再问一次，是两次使用，该各记一条。
    """
    try:
        if os.path.exists(USE):
            with open(USE, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 4096))
                tail = f.read().decode('utf-8', 'ignore').strip().splitlines()
            if tail:
                j = json.loads(tail[-1])
                if (j.get('ts') == rec['ts'] and j.get('q') == rec['q']
                        and j.get('tops') == rec['tops'] and (j.get('result') or 'pending') == 'pending'):
                    return False
    except Exception:
        pass
    try:
        with open(USE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return True
    except Exception:
        return False


class _SelfRecursion(Exception):
    pass
PLOG = os.path.join(os.path.dirname(os.path.abspath(LEDG)), '探针执行台账.jsonl')


def _plog(card, ok, why='', ms=None, out=''):
    """探针执行留痕：跑了哪根、成没成、多久、**在哪台机器**。

    Q06：280 条失败里约一半分不清是哪台机器跑的。不知道机器，"这根探针在这台机器
    不适用"和"这根探针坏了"就长得一模一样——两台机器互相给对方判死刑。

    out（D266 取证，v6.65）：失败时留子进程 stdout/stderr 的尾部 200 字。
    动机：台账原来只有 why='断言未满足' 四个字，内层退出码（4 熔断 / 5 缺件）、
    报错原文全部丢掉，于是"根因"只能靠读代码推演——上一轮就是这么推错的
    （把串行写成并行、把网络的锅扣给进程预算）。有 out 之后，下次红直接看现场。
    约束：换行压成空格（台账是一行一条 jsonl，留换行会撕行）；只留尾部
    （报错在末尾，头部的 banner 没用）；成功不写（不给台账凭空增肥）。
    """
    try:
        try:
            _host = __import__('socket').gethostname()
        except Exception:
            _host = ''
        rec = {'ts': _today(), 'card': card, 'ok': bool(ok), 'why': why[:40],
               'ms': ms, 'host': _host}
        if out:
            rec['out'] = re.sub(r'\s+', ' ', str(out)).strip()[-200:]
        with open(PLOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass
LEDG_MD = os.path.join(os.path.dirname(os.path.abspath(LEDG)), '时效台账.md')
DUE  = {'structural': None, 'environmental': 30, 'factual': 7, 'unknown': 30}

def _md_ledg():
    if not os.path.exists(LEDG_MD): return {}
    out = {}
    for l in open(LEDG_MD, encoding='utf-8'):
        parts = [x.strip() for x in l.strip().strip('|').split('|')]
        if len(parts) < 4 or parts[0] in ('卡', '卡名', '名称', '') or set(parts[0]) <= set('-:'):
            continue
        out[parts[0]] = {'last_verified': parts[1] if parts[1] not in ('—', 'None') else None,
                         'result': parts[2], 'next_due': parts[3] if parts[3] not in ('—', 'None') else None,
                         'note': parts[4] if len(parts) > 4 else ''}
    return out

def _ledg():
    if not os.path.exists(LEDG): return _md_ledg()
    try:
        return json.load(open(LEDG, encoding='utf-8'))
    except Exception:
        print('⚠ 时效台账损坏，已按空台账处理（请重跑 verify 重建）'); return {}

def _save(l):
    try:
        with _wlock('写台账'):
            cur = _ledg()        # 锁内重读：别拿旧快照覆盖别的会话刚写的结果
            cur.update(l)
            l = cur
        tmp = LEDG + '.tmp'
        json.dump(l, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.replace(tmp, LEDG)
    except Exception as ex:
        print('⚠ 台账写入失败（' + str(ex)[:40] + '）')
    try:
        rows = ['| 卡 | 上次验 | 结果 | 到期 | 备注 |', '|---|---|---|---|---|']
        for k, v in l.items():
            rows.append('| ' + k + ' | ' + str(v.get('last_verified')) + ' | ' + str(v.get('result')) + ' | ' + str(v.get('next_due')) + ' | ' + str(v.get('note',''))[:40] + ' |')
        tmp2 = LEDG_MD + '.tmp'
        open(tmp2, 'w', encoding='utf-8').write('\n'.join(rows) + '\n')
        os.replace(tmp2, LEDG_MD)
    except Exception:
        pass


DEFAULT_ROOT = os.path.realpath(_default_root())
# 批准清单跟着"被批准的库"走：默认库用技能目录里那份，副本库用副本目录里那份。
# 否则任何人在临时库 approve --yes 都能给生产库发通行证（实测踩过）。
APPROVED_FILE = os.path.join(ROOT, '探针批准.json')   # 治理件跟着库走，不跟代码走（D102）

def _pct(a, b):
    """0 分母不是 100%，返回 None 表示不可算。"""
    return None if not b else round(100 * a / b, 1)

import contextlib
# 跨平台文件锁：POSIX 用 fcntl.flock，Windows 没有 fcntl（只有 msvcrt.locking）。
# 实测：Windows 上这句 import 直接 ModuleNotFoundError，所有命令一条都跑不起来。
if os.name == 'nt':
    import msvcrt, time as _time
    def _lk_lock(f):
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1); return
            except OSError:
                _time.sleep(0.1)
    def _lk_unlock(f):
        try:
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl
    def _lk_lock(f): fcntl.flock(f, fcntl.LOCK_EX)
    def _lk_unlock(f):
        try: fcntl.flock(f, fcntl.LOCK_UN)
        except OSError: pass

def _lockpath():
    import hashlib, tempfile
    key = hashlib.sha256(os.path.abspath(ROOT).encode('utf-8')).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), 'exprecall_' + key + '.lock')


def _reap_stale_locks(keep_hours=168):
    """回收超期的死锁件（默认 7 天）。只删"早就没人碰"的文件，不碰正在用的。

    不按"库还在不在"判：锁名只存库路径哈希，反查不回原路径；按时间判最稳。
    7 天意味着任何真正被持有的锁都不会被误删（没有进程能把一把锁握一周）。
    """
    import tempfile, time
    d = tempfile.gettempdir()
    now = time.time()
    try:
        for n in os.listdir(d):
            if not (n.startswith('exprecall_') and n.endswith('.lock')):
                continue
            p = os.path.join(d, n)
            try:
                if now - os.path.getmtime(p) > keep_hours * 3600:
                    os.unlink(p)
            except Exception:
                pass
    except Exception:
        pass

@contextlib.contextmanager
def _wlock(what='写'):
    """所有写操作串行化。多会话同时改同一个库时，避免读-改-写互相覆盖（丢更新）。"""
    f = open(_lockpath(), 'a+')
    try:
        _lk_lock(f); yield
    finally:
        try: _lk_unlock(f)
        finally: f.close()

def _atomic_json(path, obj, indent=1):
    """先写临时文件再 os.replace：中断时旧文件仍完整，不会留下半截 JSON。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def _h8(c):
    import hashlib
    return hashlib.sha256((c or '').encode('utf-8')).hexdigest()[:16]

def _approved():
    try: return set(json.load(open(APPROVED_FILE, encoding='utf-8')).get('hashes', []))
    except Exception: return set()

def _plat():
    """本机平台标记：posix / win32。探针可在批准清单里挂 platform_blacklist 限定不适用平台。"""
    return 'win32' if os.name == 'nt' else 'posix'

def _blacklist():
    """卡名 → 不适用平台列表。只放批准清单，不写进卡片正文（避免改动卡片哈希）。"""
    try: return json.load(open(APPROVED_FILE, encoding='utf-8')).get('platform_blacklist', {})
    except Exception: return {}

def _pkg_root():
    """包目录（放 经验资产包_v*.zip 或 记忆与经验管理工具_v*.zip 的那一层）。卡片只写 `<PKG>`，换机/换平台由这里解析。
    优先环境变量 EXPRECALL_PKG；否则在 ~/Doubao/chats 下找带最新包的目录；都找不到返回空串（探针会如实报"没有包"）。"""
    v = os.environ.get('EXPRECALL_PKG')
    if v and os.path.isdir(v): return v
    try:
        import glob as _g
        home = os.path.expanduser('~')
        root = os.path.join(home, 'Doubao', 'chats')
        # 包可能落在 chats/<日期>/<会话>/ 两层里；逐层试，别只扫一层就判"没包"。
        # 这一条是 Windows 实机首测抓出来的：Linux 上包在 chats/<会话>/ 一层，Windows 上在两层。
        for depth in ('*', os.path.join('*', '*')):
            hits = []
            for _pat in ('经验资产包_v*.zip', '记忆与经验管理工具_v*.zip', '经验与记忆管理工具_v*.zip'):
                hits += _g.glob(os.path.join(root, depth, _pat))
            if hits:
                return os.path.dirname(max(hits, key=os.path.getmtime))
    except Exception:
        pass
    return ''

def _pkg_has_zip():
    """<PKG> 下到底有没有「包」可比对。

    为什么单独立这一条：有 4 根探针验的是**包本身**（包内中文名有没有乱码、
    包内是否含 recall.py、根目录有没有散落多份包、清单逐条哈希对不对）。它们
    要先把 zip 打开才有话可说。解压后的包里没有 zip（也不可能把 zip 放进自己），
    于是这些探针会以 max([]) 之类的方式崩掉，被记成「业务失败」——把「没有物料」
    误报成「业务缺陷」，正是 envbad 那条注释里说过的错，这里再兜一层。
    """
    d = _pkg_root()
    if not d:
        return False
    try:
        for pat in ('*_v*.zip', os.path.join('历史包', '*_v*.zip'), os.path.join('历史包', '*', '*_v*.zip')):
            if glob.glob(os.path.join(d, pat)):
                return True
    except Exception:
        pass
    return False


def _ph_map():
    """卡片可用的跨平台占位符 → 本机真实路径。卡片正文只写占位符，不写盘符/绝对路径。"""
    return [('<SKILL>', os.path.dirname(os.path.abspath(__file__))),
            ('<ROOT>', os.path.abspath(ROOT)),
            ('<TMP>', TMPB),
            ('<PKG>', _pkg_root())]

def _portable(cmd):
    """把探针命令归一化到本机执行。

    两级：①新写法（v6.3 起）= 卡片内只写占位符 `<SKILL>` / `<ROOT>` / `<TMP>` 与解释器名 `py`，
    换机器/换平台由闸替换成本机真实值，卡片正文里不含任何盘符或绝对路径；
    ②旧写法（v6.3 前的 40 张卡）= `python3 -c` / `python3 脚本` / `grep -c` / `test -f …` 四形态，
    由闸翻译成 argv 并替换写死的 /home/user/... 路径；替换处数计入 `_portfix`，
    用来让"卡片仍写死路径"这条债**归零可见**——占位符替换不计入，因为那已不是债。
    全程不经 shell：Windows 的 cmd 不认 `;`、不认 `$VAR`、没有 grep/test。
    返回 (argv 或 None, 替换处数)。
    """
    import shlex
    s = (cmd or '').strip()
    sk = os.path.abspath(__file__)
    skdir = os.path.dirname(sk)
    ROOTAB = os.path.abspath(ROOT)
    PH = _ph_map()
    old_sk = '/home/user/.doubao/agent_mode/workspace/.user_skills/experience-recall/recall.py'
    old_root = '/runtime/experience'
    old_root2 = '/home/user/.doubao/agent_mode/workspace/experience'
    n = 0
    m = re.match(r'(?:py|python3|python)\s+-c\s+(.*)$', s, re.S)
    if m:
        payload = m.group(1).strip()
        if len(payload) >= 2 and payload[0] == payload[-1] and payload[0] in '"\'':
            payload = payload[1:-1]
        # 顺序很重要：先翻旧字面量（这才计债），再换占位符（不计债）。
        # 反过来的话，占位符替换出的真实路径会再次命中旧字面量规则、被误记成债。
        for a, b in [("'python3'", repr(sys.executable)),
                     ('"' + old_sk + '"', repr(sk)), ("'" + old_sk + "'", repr(sk)),
                     ('"' + old_root + '"', repr(ROOTAB)),
                     ("'" + old_root + "'", repr(ROOTAB)),
                     ('"' + old_root2 + '"', repr(ROOTAB)),
                     ("'" + old_root2 + "'", repr(ROOTAB))]:
            if a in payload:
                n += payload.count(a); payload = payload.replace(a, b)
        for ph, val in PH:            # 占位符：转义后内容，供字符串字面量内嵌（Windows 反斜杠安全）
            if ph in payload:
                payload = payload.replace(ph, repr(val)[1:-1])
        for a, b in (("'<PY>'", repr(sys.executable)), ('"<PY>"', repr(sys.executable))):
            if a in payload:
                payload = payload.replace(a, b)
        if os.name == 'nt' and "'/tmp/" in payload:  # R3-ok: 归一化旧字面量，非新硬编码
            # 探针里写死的 '/tmp/...'：Windows 没有 /tmp，换成真正的临时根。
            payload, _k = re.subn(r"'/tmp/([^']*)'",  # R3-ok: 同上
                                  lambda mm: repr(os.path.join(TMPB, mm.group(1))), payload)
            n += _k
        return [sys.executable, '-c', payload], n
    m = re.match(r'(?:py|python3|python)\s+([^\s]+)(.*)$', s, re.S)
    if m:
        sp = m.group(1)
        for ph, val in PH:            # 脚本路径里的占位符：直接换成本机路径（argv 不经 shell）
            if ph in sp:
                sp = sp.replace(ph, val)
        skdir_old = '/home/user/.doubao/agent_mode/workspace/.user_skills/experience-recall'
        skdir_old2 = '~/.doubao/agent_mode/workspace/.user_skills/experience-recall'
        if sp.startswith(skdir_old + '/'):
            sp = os.path.join(skdir, sp[len(skdir_old) + 1:]); n += 1
        elif sp.startswith(skdir_old2 + '/'):
            sp = os.path.join(skdir, sp[len(skdir_old2) + 1:]); n += 1
        elif sp.startswith('~/'):
            sp = os.path.expanduser('~') + os.sep + sp[2:]; n += 1
        return [sys.executable, sp] + shlex.split(m.group(2)), n
    m = re.match(r'grep\s+-c\s+("[^"]*"|\'[^\']*\')\s+(\S+)$', s)
    if m:
        pat = m.group(1)[1:-1]; f = m.group(2)
        for _o in ('/home/user/.doubao/agent_mode/workspace/.user_skills/experience-recall',):
            if f.startswith(_o + '/'):
                f = os.path.join(skdir, f[len(_o) + 1:]); n += 1
        for ph, val in PH:
            if ph in f:
                f = f.replace(ph, val)
        code = ("import io,re,sys;t=io.open(sys.argv[1],encoding='utf-8').read();"
                "print(sum(1 for l in t.splitlines() if re.search(sys.argv[2],l)))")
        return [sys.executable, '-c', code, f, pat], n
    m = re.match(r'test\s+-f\s+(\S+)((?:\s*&&\s*test\s+-f\s+\S+)*)\s*&&\s*echo\s+1$', s)
    if m:
        files = [m.group(1)] + re.findall(r'test\s+-f\s+(\S+)', m.group(2))
        for i, f in enumerate(files):
            for ph, val in PH:
                if ph in f: files[i] = f = f.replace(ph, val)
            if f.startswith('./'): files[i] = f[2:]
        code = ("import os,sys;r=os.environ.get('EXPRECALL_THIS_ROOT') or '.';"
                "print(1 if all(os.path.exists(os.path.join(r,x)) if not os.path.isabs(x) else os.path.exists(x) for x in sys.argv[1:]) else '')")
        return [sys.executable, '-c', code] + files, n
    return None, 0

def _probe_ok(cmd):
    """两层：①静态危险串拦截 ②必须在我本人批准过的哈希清单里（卡片自己声明的探针不算数）。"""
    c = (cmd or '').strip()
    if not c: return False, '空命令'
    for b in ('rm ', 'mv ', 'dd ', 'curl', 'wget', 'chmod', 'chown', 'sudo', 'nc ', 'ssh '):
        if b in c: return False, '含危险片段:' + b.strip()
    if re.search(r'(^|\s)(\d?>>?|&>)\s*[^\s=]', c): return False, '含重定向'
    if _h8(c) not in _approved():
        return False, '探针未批准（压测/新卡自带的命令不能自己给自己发通行证）'
    return True, ''



def _clean_dates(ds, who='', dedupe=False):
    """剔除未来/非法日期。默认**保留同日多次**（频次是信息，不能被稀释）；dedupe=True 仅供按天统计。"""
    today = _today()
    out, bad = [], []
    for d in ds:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d):
            bad.append(d); continue
        if d > today:
            bad.append(d + '(未来)'); continue
        out.append(d)
    out.sort()
    if dedupe: out = sorted(set(out))
    if bad:
        print('⚠ 已剔除异常时间锚' + ('（' + who + '）' if who else '') + '：' + '、'.join(bad))
    return out


def _date_stats(ds):
    """次数与天数是两个量：n=总次数，days=覆盖天数，peak=单日最多次数，gaps=相邻**不同日期**的间隔。"""
    if not ds: return {'n': 0, 'days': [], 'peak': 0, 'gaps': []}
    days = sorted(set(ds))
    peak = max(ds.count(d) for d in days)
    ps = [datetime.date.fromisoformat(d) for d in days]
    gaps = [(ps[i+1]-ps[i]).days for i in range(len(ps)-1)]
    return {'n': len(ds), 'days': days, 'peak': peak, 'gaps': gaps}

def _anchors(body):
    """时间锚只认「## 时间锚」段；正文引用的示例日期不算。"""
    m = re.search(r'^##\s*时间锚\s*$([\s\S]*?)(?=^##\s|\Z)', body, re.M)
    seg = m.group(1) if m else body
    m2 = re.search(r'occurrences\s*=\s*["\']?([0-9,\-]+|unknown)', seg)
    if not m2: return []
    if m2.group(1) == 'unknown': return []
    return [d for d in m2.group(1).split(',') if d]

def _today():
    return datetime.date.today().isoformat()

def _ttl(name, v, l):
    if name in l and l[name].get('next_due'):
        return l[name]['next_due']
    days = DUE.get(v.get('half_life', 'unknown'))
    tags = ' '.join(v.get('tags', [])) + v.get('file', '')
    if days and 'skill' in tags.lower() and days <= 30: days = 60
    return None if days is None else (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

def _freshness(name, l):
    rec = l.get(name)
    if not rec: return '未验过', None
    nd = rec.get('next_due')
    if not nd: return '永久有效(结构性)', None
    left = (datetime.date.fromisoformat(nd) - datetime.date.today()).days
    if left >= 0:
        return ('新鲜(剩' + str(left) + '天)', nd)
    return ('⚠ 超期 ' + str(-left) + ' 天', nd)

def cmd_verify():
    import tempfile as _tf, shutil as _sh, atexit as _ae, time as _ts
    _t_start = _ts.time()
    _rundir = _tf.mkdtemp(prefix='exprecall_verify_')
    _ae.register(_sh.rmtree, _rundir, True)
    d = _depth()
    idx = _midx_ok(); l = _ledg(); bad = 0
    for name, v in idx.items():
        pr = v.get('probe')
        if not pr:
            l[name] = {'last_verified': None, 'result': 'unverified',
                       'next_due': _ttl(name, v, l) or (datetime.date.today() + datetime.timedelta(days=30)).isoformat(),
                       'note': '无探针，只能按期复核'}
            continue
        _t0 = __import__('time').time()
        try:
            _pd = __import__('tempfile').mkdtemp(prefix='exprecall_p_')
            try:
                _argv, _ = _portable(pr['cmd'])
                if _argv:
                    r = subprocess.run(_argv, capture_output=True, text=True, timeout=30, cwd=ROOT,
                                       env=dict(os.environ, EXPRECALL_DEPTH=str(d + 1),
                                                EXPRECALL_PARENT_ROOT=os.path.realpath(ROOT), EXPRECALL_THIS_ROOT=os.path.realpath(ROOT),
                                                EXPRECALL_SK=os.path.abspath(__file__), TMPDIR=_pd))
                else:
                    r = subprocess.run(pr['cmd'], shell=True, capture_output=True, text=True, timeout=30, cwd=ROOT,
                                       env=dict(os.environ, EXPRECALL_DEPTH=str(d + 1),
                                                EXPRECALL_PARENT_ROOT=os.path.realpath(ROOT), EXPRECALL_THIS_ROOT=os.path.realpath(ROOT), TMPDIR=_pd))
            finally:
                _rm_force(_pd)
            tail = (r.stdout or r.stderr or '').strip().split('\n')[-1][:60]
            if pr.get('expect_min') is not None:
                try: num = int(tail.split()[-1])
                except Exception: num = 0
                ok = num >= int(pr['expect_min'])
            else:
                ok = (r.returncode == 0)
        except Exception as ex:
            tail = str(ex)[:40]; ok = False
        l[name] = {'last_verified': _today(), 'result': 'ok' if ok else 'fail',
                   'next_due': _ttl(name, v, l), 'note': tail}
        if not ok: bad += 1
    _save(l)
    print('已验 ' + str(len([1 for v in idx.values() if v.get('probe')])) + ' 条探针，其中不成立 ' + str(bad))
    for name in l:
        f, _ = _freshness(name, l)
        print('  ' + name.ljust(50) + ' ' + l[name].get('result','') + '  ' + f)

def cmd_due():
    if not os.path.exists(LEDG) and not os.path.exists(LEDG_MD):
        print('时效台账不存在（先跑 verify 建账）'); return
    l = _ledg(); od = []; unv = []
    for name, rec in l.items():
        f, nd = _freshness(name, l)
        if '超期' in f: od.append((name, rec.get('next_due'), f))
        elif rec.get('result') == 'unverified': unv.append(name)
    print('台账条目 ' + str(len(l)) + '｜超期 ' + str(len(od)) + '｜从未验过 ' + str(len(unv)))
    print('说明：无探针的卡按 unknown 计 30 天到期，只作参考、不许当闸。')
    for n, nd, f in od[:15]: print('  ⚠ ' + n.ljust(50) + ' 到期 ' + str(nd) + '  ' + f)
    if len(od) > 15: print('  … 另有 ' + str(len(od) - 15) + ' 条')

def _ensure_index():
    """索引只是缓存：缺失就重建；卡片比索引新就提示陈旧。"""
    ip = os.path.join(ROOT, '匹配索引.json')
    if not os.path.exists(ip):
        cmd_build_index(); return True, '索引缺失→已自动重建'
    newest = 0
    for f in _card_files():
        bn = os.path.basename(f)
        if bn in SKIP or bn.startswith('时效台账') or bn == '缺陷台账_20261112.md':
            continue
        try: newest = max(newest, os.path.getmtime(f))
        except Exception: pass
    try:
        if newest > os.path.getmtime(ip):
            return True, '⚠ 索引可能陈旧（有卡片比索引新），建议 build_index'
    except Exception: pass
    return True, ''

def _midx_ok():
    if not os.path.exists(MIDX): print('缺 _match_index.json'); return {}
    return json.load(open(MIDX, encoding='utf-8'))



# ===== 开工一页（start）与体检（lint）=====

def cmd_start():
    print('=' * 62)
    print('【1】在办状态（读状态文件；如被他方会话改过，以流水块为准）')
    if os.path.exists(STATE):
        for i, l in enumerate(open(STATE, encoding='utf-8').read().split('\n')):
            if i >= 26: print('    …（余下见 state）'); break
            print('    ' + l)
    print('=' * 62)
    print('【2】时效（到期未验的判据不许当闸）')
    cmd_due()
    print('=' * 62)
    print('【3】最近 5 块流水（看上一轮到底做到哪）')
    if os.path.exists(FLOW):
        heads = [l for l in open(FLOW, encoding='utf-8').read().split('\n') if l.startswith('## ')]
        for l in heads[-5:]: print('    ' + l[3:])
    print('=' * 62)
    print('【4】上次检索记录（证明用没用过）')
    if os.path.exists(USE):
        tail = open(USE, encoding='utf-8').read().strip().split('\n')[-3:]
        for l in tail: print('    ' + l[:110])
    else:
        print('    （无记录）')

def cmd_lint():
    cs = load_cards(); issues = []
    if _SKIPPED:
        # v6.59：把"文档不进索引"与"真被门槛挡下"分开报——前者是正常，后者才是欠账。
        docn = [x for x in _SKIPPED if x.endswith('(缺name)')]
        other = [x for x in _SKIPPED if not x.endswith('(缺name)')]
        if docn:
            print('# 文档 ' + str(len(docn)) + ' 份不进索引（无 name 前言，属正常）')
        if other:
            print('# 收录门槛跳过 ' + str(len(other)) + ' 个文件（不进索引、不进分母）：' + '、'.join(other[:5]))
    need = ['## 适用场景', '## 关键做法', '## 踩过的坑', '## 失败路径', '## 失效条件']
    for c in cs:
        b = c['body']
        if not re.search(r'^\s*provenance:', b, re.M): issues.append(('缺provenance', c['file']))
        # v6.59：bug_class（主干/细枝末节）已撤——它要求人手填一个只有两个值的分类，
        # 实测 0 张卡填写过，等于一条从不生效的必填。锚点改由 source_event + trace 自动派生，
        # 不再要求人贴标签（贴了也不判分）。
        for n in need:
            if n not in b: issues.append(('缺段' + n.replace('## ', ''), c['file']))
        # 判据段：标题允许带括号后缀（模板就是 `## 判据（必填，三档选一…）`），
        # 档位允许写成列表项 `- 可执行：…`。v6.6 的 lint 用 `^##\s*判据\s*$` 精确匹配，
        # 照模板写的卡一律被判"缺判据"，而"称可执行却没探针"那条检查从未触发——假绿。
        mj = re.search(r'^##\s*判据[^\n]*\n([\s\S]*?)(?=^##\s|\Z)', b, re.M)
        if not mj:
            issues.append(('缺段判据', c['file']))
        else:
            q = mj.group(1).strip()
            first = ''
            for ln in q.split('\n'):
                ln2 = re.sub(r'^[\s\-*•]+', '', ln).strip()
                if ln2:
                    first = ln2; break
            tier = ('仅参考' if first.startswith('仅参考') else
                    '可执行' if first.startswith('可执行') else
                    '可复现' if first.startswith('可复现') else '')
            if not tier:
                issues.append(('判据段开头没写档位(可执行/可复现/仅参考)', c['file']))
            hits = [t for t in ('可执行', '可复现', '仅参考') if re.search(r'^\s*[-*•]?\s*' + t, q, re.M)]
            if len(hits) >= 3:
                issues.append(('判据段像模板占位(三档都没删)', c['file']))
            if tier == '仅参考' and not re.search(r'本机|依赖|未写|Windows|无独立|验不了|没法验', q):
                issues.append(('判据仅参考但没写为什么', c['file']))
            if tier == '可执行' and not re.search(r'^探针[:：]', b, re.M):
                issues.append(('判据称可执行但卡内无探针行', c['file']))
            if q.startswith('（兜底'): issues.append(('判据段是兜底摘录(等于没写)', c['file']))
        mm = re.search(r'^##\s*机制解释\s*$([\s\S]*?)(?=^##\s|\Z)', b, re.M)
        if mm and mm.group(1).strip().startswith('（兜底摘录'):
            issues.append(('机制解释仍是兜底摘录(警告)', c['file']))
        # 时效信息泄漏进正文（应只在指纹段）
        body_wo = re.split(r'^##\s*(指纹|失效条件)', b, maxsplit=1, flags=re.M)[0]
        for pat in (r'\bv\d\.\d\d\.\d+\b', r'C:\\Users', r'/home/user/'):
            if re.search(pat, body_wo): issues.append(('提示:正文含路径或版本(宜移指纹段)', c['file'])); break
    probes = [v for v in _midx_ok().values() if v.get('probe')]
    claim = [c['file'][:-3] for c in load_cards()
             if re.search(r'##\s*判据[^\n]*\n\s*[-*•]?\s*可执行', c['body'])]
    noprobe = [x for x in claim if not _midx_ok().get(x, {}).get('probe')]
    if noprobe:
        for x in noprobe:
            issues.append(('警告:声称"可执行"判据但没有探针(写着能验其实没闸)', x))
    noarch = [c['file'][:-3] for c in load_cards()
              if (field(c['body'], 'state') or 'active') in ('archived', 'dormant') and '## 归档原因' not in c['body']]
    if noarch:
        issues.append(('警告:已封存但没写归档原因(将来无从判断要不要叫醒)', str(len(noarch)) + ' 张，例：' + '、'.join(noarch[:3])))
    import unicodedata as _ud
    norm = {}
    for c in load_cards():
        norm.setdefault(_ud.normalize('NFC', c['file'][:-3]), []).append(c['file'][:-3])
    dup = {k: v for k, v in norm.items() if len(v) > 1}
    if dup:
        issues.append(('警告:Unicode 等价重名(肉眼一样、实际两张卡)', str(len(dup)) + ' 组（例 ' + '；'.join('/'.join(v) for v in list(dup.values())[:2]) + '）'))
    st = sum(1 for v in probes if v['probe'].get('kind') == 'structural')
    if probes and st:
        issues.append(('警告:结构断言探针(只验存在不验行为)', str(st) + '/' + str(len(probes)) + ' 根'))
    print('lint 发现 ' + str(len(issues)) + ' 条')
    agg = {}
    for k, f in issues: agg.setdefault(k, []).append(f)
    for k, fs in agg.items():
        print('  ' + k + '：' + str(len(fs)) + ' 张，例 ' + '、'.join(fs[:3]))
    # 索引与实物一致性
    cs2 = len(cs)
    print('卡片 ' + str(cs2) + ' 张；索引条目 ' + str(len(_midx_ok())) + ' 条')


# ===== 复发与比例（时间锚的可计算部分）=====

def cmd_hit(name):
    """撞到同一个坑时用：在 occurrences 里追加今天；同日重复调用不重复计数。"""
    cs = [c for c in load_cards() if name in c['file'] or name == c['file'][:-3]]
    if not cs: print('未命中卡：' + name); return
    c = cs[0]; b = c['body']; lines = b.split(chr(10)); idx = None
    for i, l in enumerate(lines):
        if l.startswith('occurrences='):
            idx = i; break
        if re.match(r'^##\s*时间锚\s*$', l):
            for j in range(i + 1, min(i + 4, len(lines))):
                if 'occurrences=' in lines[j] or 'first_seen=' in lines[j]: idx = j; break
            break
    if idx is None: print('该卡无时间锚：' + c['file']); return
    line = lines[idx]; today = _today()
    m = re.search(r'occurrences=([0-9,-]+|unknown)', line)
    ds = [] if (not m or m.group(1) == 'unknown') else m.group(1).split(',')
    if today in ds:
        print('今日已在册（第 ' + str(len(ds)) + ' 次），未重复计数：' + c['file']); return
    ds.append(today); ds.sort()
    lines[idx] = 'occurrences=' + ','.join(ds) + ' | 说明=本行是唯一的时间事实，first/last/recur 全部由它推导，不许另写'
    tmp = c['path'] + '.tmp'
    open(tmp, 'w', encoding='utf-8').write(chr(10).join(lines)); os.replace(tmp, c['path'])
    gap = ''
    if len(ds) > 1:
        gap = '，距上次 ' + str((datetime.date.fromisoformat(ds[-1]) - datetime.date.fromisoformat(ds[-2])).days) + ' 天'
    print('已记第 ' + str(len(ds)) + ' 次：' + c['file'] + gap)

def cmd_recur(name=''):
    if name:
        cs = [c for c in load_cards() if name in c['file'] or name == c['file'][:-3]]
        if not cs:
            print('未命中卡：' + name); sys.exit(3)
        b = cs[0]['body']
        ds = _clean_dates(_anchors(b), cs[0]['file'])
        if not ds:
            print('⚠ 时间锚缺失或非法（## 时间锚 段里没有 occurrences=）：' + cs[0]['file']); sys.exit(4)
        st = _date_stats(ds)
        head = cs[0]['file'] + '：共 ' + str(st['n']) + ' 次 / 覆盖 ' + str(len(st['days'])) + ' 天'
        if st['n'] > len(st['days']): head += '（同日重复 ' + str(st['n'] - len(st['days'])) + ' 次，单日最多 ' + str(st['peak']) + ' 次）'
        print(head + '  ' + '、'.join(st['days']))
        if st['gaps']:
            g = st['gaps']
            print('  间隔（按天算，同日不产生 0 天间隔）：平均 ' + str(round(sum(g)/len(g),1)) + ' 天，最短 ' + str(min(g)) + '，最长 ' + str(max(g)))
        return
    cs = load_cards(); import collections
    et = collections.Counter(); mon = collections.Counter(); gaps_all = []; multi = 0; trend = [0, 0, 0]; same_day = 0
    today = datetime.date.today(); per = []
    for c in cs:
        b = c['body']
        m = re.search(r'error_type[:=][ |｜]*([A-Za-z0-9_-]+)', b)
        et[m.group(1) if m else 'unknown'] += 1
        cm = re.search(r'^\s*created_at:\s*(\d{4}-\d{2}-\d{2})', b, re.M)
        if cm: mon[cm.group(1)[:7]] += 1
        ds = _clean_dates(_anchors(b), '')
        stt = _date_stats(ds)
        if stt['n'] > len(stt['days']): same_day += stt['n'] - len(stt['days'])
        if len(stt['days']) > 1:
            multi += 1
            g = stt['gaps']
            gaps_all += g
            if len(g) >= 2:
                if g[-1] < g[-2]: trend[0] += 1      # 越来越密 = 恶化
                elif g[-1] > g[-2]: trend[1] += 1    # 间隔变长 = 在收敛
                else: trend[2] += 1
        if ds: per.append(len(ds))
    print('卡片 ' + str(len(cs)) + ' 张；有逐次记录 ' + str(len(per)) + ' 张；跨天复发(>=2天) ' + str(multi) + ' 张；同日重复 ' + str(same_day) + ' 次')
    print('\n[失效类型占比]')
    _p = _pct(1, len(cs))
    for k, v in et.most_common(): print('  ' + k.ljust(18) + str(v).rjust(4) + '  ' + (str(_pct(v, len(cs))) + ' pct' if _p is not None else '（空库，不计算）'))
    print('\n[复发间隔]')
    if gaps_all:
        print('  平均 ' + str(round(sum(gaps_all) / len(gaps_all), 1)) + ' 天；最短 ' + str(min(gaps_all)) + ' 天；最长 ' + str(max(gaps_all)) + ' 天；样本 ' + str(len(gaps_all)) + ' 段')
    else:
        print('  无（还没有任何一张卡被记到第二次）')
    print('  趋势：恶化(间隔变短) ' + str(trend[0]) + ' 张 / 收敛(间隔变长) ' + str(trend[1]) + ' 张 / 持平 ' + str(trend[2]) + ' 张')
    print('\n[建卡月份]')
    for k, v in sorted(mon.items()): print('  ' + k + '  ' + str(v) + ' 张')


_STRUCT_WORDS = ('覆盖', '截断', '丢失', '漂移', '双真相', '静默失败', '假通过', '口径不一',
                 '路径写错', '编码', '权限', '并发', '锁', '缓存过期', '顺序假设',
                 '只验一维', '默认值', '超时', '回填', '影子副本')

# 通用疑问词：只表征句式，不指向任何一张卡。它们不进分、不算具名证据。
_GENERIC_Q = {'如何用', '怎么', '为什么', '什么', '哪个好', '几步', '多久', '需要', '哪些',
              '能不能', '是否', '多少', '怎么办', '咋办', '好不好', '怎么弄'}


def cmd_build_index():
    """从卡重建匹配索引：索引只是缓存，丢了就重生。"""
    import glob as _g
    SKIPS = set(SKIP)  # 只维护一处：SKIP 是超集，这里不再抄第二份（D103 类错误）
    out = {}
    docs = []
    for p in _card_files():
        n = os.path.basename(p)
        c_file = n
        if n in SKIPS or n.startswith('时效台账'): continue
        if os.path.islink(p) or not os.path.isfile(p):
            continue
        try:
            st = open(p, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        _ok, _why = _card_ok(n, st)
        if not _ok:
            # v6.59：缺 name 的是"文档"（台账/模板/说明/派生镜像），不是欠账——
            # 它们本来就该给人读，不该进检索域（搜"为什么"时跳出台账条目是污染）。
            # 以前每份都打一行 ⚠，9 份噪音把真问题（结构缺失/乱码）淹掉了。
            if _why == '缺name':
                docs.append(n)
            else:
                print('⚠ 跳过（' + n + '）：' + _why)
            continue
        v = {'file': n, 'desc': field(st, 'description'), 'sha16': _h8(st),
             'line': field(st, 'line') or field(st, 'owner'), 'state': field(st, 'state') or 'active',
             'tags': [], 'entities': [],
             'struct': [], 'source_event': '',
             'half_life': 'unknown', 'probe': None, 'triggers': []}
        m = re.search(r'tags:\s*\[(.*?)\]', st)
        if m: v['tags'] = [x.strip() for x in m.group(1).split(',') if x.strip()]
        # v6.59：原 v6.56（B）的 bug_class（主干/细枝末节）读取已删——0 张填写，撤。
        # 锚点走下面的 source_event，以及 trace 从正文提取的 D 编号（零推断）。
        # v6.56（C-2）：卡侧来源事件——这张卡是被哪条事件催出来的。
        se = (field(st, 'source_event') or '').strip()
        if se and se != '无':
            v['source_event'] = se
        # v6.56（A）：错误结构。20 词小词典，命中即记，不新开召回通道。
        sx = (field(st, 'struct') or '').strip()
        if sx and sx != '无':
            v['struct'] = [x.strip() for x in re.split(r'[，,、\s]+', sx) if x.strip() in _STRUCT_WORDS]
        else:
            v['struct'] = [w for w in _STRUCT_WORDS if w in st][:6]
        m = re.search(r'half_life[:=]\s*([a-z]+)', st)
        if m:
            v['half_life'] = m.group(1)
            if m.group(1) not in ('structural', 'env', 'factual', 'unknown'):
                print('⚠ 非法 half_life（' + n + '）：' + m.group(1) + ' → 按 unknown 处理并计入 lint')
                v['half_life'] = 'unknown'; v['bad_half_life'] = m.group(1)
        m3 = re.search(r'^探针[:：](.*)$', st, re.M)
        if m3:
            parts = [x.strip() for x in m3.group(1).split('|')]
            cmd = parts[0]
            exp = None; exp_exit = None
            for x in parts:
                if x.startswith('期望>='):
                    digits = ''.join(ch for ch in x if ch.isdigit())
                    if digits: exp = int(digits)
                elif re.match(r'^期望\s*=\s*-?\d+$', x):
                    exp_exit = int(x.split('=')[1])          # 出口码断言：要求命令以该码结束
            if cmd:
                _ok, _why = _probe_ok(cmd)
                if _ok:
                    v['probe'] = {'cmd': cmd, 'expect_min': exp, 'expect_exit': exp_exit,
                                  'kind': ('structural' if cmd.split()[0] in ('grep', 'test', 'wc', 'cat')
                                           else 'behavioral')}
                else:
                    print('⚠ 拒绝收录探针（' + c_file + '）：' + _why)
                    v['probe'] = None
                    v['probe_rejected'] = _why
            if len(parts) > 2:
                cand = parts[2].split('=')[-1].strip().strip('：:')
                if cand in ('structural', 'env', 'factual'):
                    v['half_life'] = cand
        cand = set()
        manual = []
        m2 = re.search(r'^触发词[:：](.*)$', st, re.M)
        if m2: manual = [x.strip() for x in re.split(r'[｜|,，]', m2.group(1)) if x.strip()]
        for x in re.findall(r'`([^`\n]{3,26})`', st)[:14]: cand.add(x)
        for x in re.findall(r'[「“"]([^」”"\n]{4,22})[」”"]', st)[:14]: cand.add(x)
        for x in re.findall(r'\b([A-Z]{4,}[A-Z_]*)\b', st)[:10]: cand.add(x)
        pit = re.search(r'##\s*(踩过的坑|失败路径)\s*$([\s\S]*?)(?=^##\s|\Z)', st, re.M)
        if pit:
            for cl in re.split(r'[，。；\n]', pit.group(2))[:40]:
                cl = cl.strip()
                if 8 <= len(cl) <= 26 and re.search(r'(报|误|改回|失败|缺失|漏|静默|拒绝|不存在|不符)', cl): cand.add(cl)
        STOP = {'JSON','YAML','README','SKILL','SKILL.md','MD','CSV','API','URL','HTTP','HTTPS','TRUE','FALSE','NULL','PID','CLI','FAQ','PDF','ZIP'}
        keep = []
        for t in cand:
            if '<' in t or t.startswith('$') or t.startswith('/'): continue
            if t.upper() in STOP: continue
            if re.fullmatch(r'[\x00-\x7f]+', t) and len(t) < 4: continue
            keep.append(t)
        auto = sorted(set(keep) - set(manual))
        v['triggers'] = manual[:8] + auto[:max(0, 10 - len(manual))]
        # v6.55（C-1）：死路进检索域。卡里「失败路径 / 踩过的坑」写着的试错标本，
        # 原先只在 search（吃正文）看得到，match（吃索引）完全看不见——等于"试错白记了"。
        # 这里把这两节抽成 fail 字段，只进索引、不动卡；截断到 900 字防长卡淹没短卡。
        mf = re.search(r'##\s*(失败路径|试错)\s*\n([\s\S]*?)(?=^##\s|\Z)', st, re.M)
        fail = ''
        if mf:
            fail = re.sub(r'\s+', ' ', mf.group(2)).strip()[:900]
        v['fail'] = fail
        v['manual'] = manual
        out[os.path.basename(p)[:-3]] = v
    # 文档频率清洗
    df = {}
    for v in out.values():
        for t in set(v['triggers']): df[t] = df.get(t, 0) + 1
    for v in out.values():
        v['triggers'] = [t for t in v['triggers'] if df.get(t, 0) < 12]
    with _wlock('建索引'):
        _atomic_json(os.path.join(ROOT, '匹配索引.json'), out)
    try:
        led = _ledg(); added = 0
        for k in out:
            if k not in led:
                led[k] = {'last_verified': None, 'result': 'unverified',
                          'next_due': (datetime.date.fromisoformat(_today()) + datetime.timedelta(days=30)).isoformat(),
                          'note': '建索引时补登（从未验，按 30 天倒计）'}
                added += 1
        if added:
            _atomic_json(LEDG, led)
            print('台账补登 ' + str(added) + ' 张（此前不在台账，时间债是隐形的）')
        elif not os.path.exists(LEDG):
            # D79 的源头：只有 md、没有 json 时，探针（写死要 .json）会假红。
            # 建索引本来就已经从 md 降级读到了完整台账，顺手固化成 json，
            # 让"派生件"真的被派生出来，而不是等某天恰好多一张新卡。
            _atomic_json(LEDG, led)
            print('台账固化 ' + str(len(led)) + ' 条到 json（此前只有 md，探针会假红）')
    except Exception as ex:
        print('⚠ 台账补登失败：' + str(ex)[:60])
    print('已重建索引：' + str(len(out)) + ' 条，触发词 ' + str(sum(len(v['triggers']) for v in out.values())))
    if docs:
        print('文档 ' + str(len(docs)) + ' 份不进索引（无 name 前言，属正常）：'
              + '、'.join(docs[:6]) + ('…' if len(docs) > 6 else ''))



def cmd_judge():
    """判据覆盖：三档分账 + 升级候选。"""
    cs = load_cards(); import collections
    tiers = collections.Counter(); reasons = collections.Counter(); upg = []
    for c in cs:
        m = re.search(r'^##\s*判据\s*$([\s\S]*?)(?=^##\s|\Z)', c['body'], re.M)
        body = m.group(1).strip() if m else ''
        if body.startswith('可执行'): tiers['可执行'] += 1
        elif body.startswith('可复现'): tiers['可复现'] += 1
        elif body.startswith('仅参考'): tiers['仅参考'] += 1
        else: tiers['未写'] += 1
        if 'Windows 本机' in body: reasons['判据在他机(Windows)'] += 1
        elif '未写复现步骤' in body: reasons['未写复现步骤'] += 1
        elif '可执行' in body: reasons['已可执行'] += 1
        if '未来可升级' in body: upg.append(c['file'][:-3])
    tot = len(cs)
    print('判据分账（共 ' + str(tot) + ' 张）：')
    for k in ('可执行', '可复现', '仅参考', '未写'):
        v = tiers.get(k, 0)
        print('  ' + k.ljust(6) + str(v).rjust(4) + '  ' + (str(_pct(v, tot)) + ' pct' if _pct(v, tot) is not None else '（空库，不计算）'))
    print('仅参考的原因分布：')
    for k, v in reasons.most_common(): print('  ' + k + '  ' + str(v))
    probes = [v for v in _midx_ok().values() if v.get('probe')]
    st = sum(1 for v in probes if v['probe'].get('kind') == 'structural')
    if probes:
        print('探针强度：行为断言 ' + str(len(probes) - st) + ' 根 / 结构断言 ' + str(st) + ' 根' + ('（结构断言只证明"东西在"，不证明"行为对"）' if st else ''))
    print('供料率（每 10 张新卡能写出判据的张数）：' + (str(round(10 * (tiers.get('可执行', 0) + tiers.get('可复现', 0)) / tot, 1)) if tot else '（空库，不计算）'))
    if upg: print('升级候选 ' + str(len(upg)) + ' 张：' + '、'.join(upg[:5]))


def cmd_mech():
    """机制解释质量三档：含根因词 / 兜底摘录 / 未记录。"""
    cs = load_cards(); import collections
    t = collections.Counter()
    for c in cs:
        m = re.search(r'^##\s*机制解释\s*$([\s\S]*?)(?=^##\s|\Z)', c['body'], re.M)
        b = m.group(1).strip() if m else ''
        if not b: t['未写'] += 1
        elif b.startswith('（未记录'): t['未记录'] += 1
        elif b.startswith('（兜底摘录'): t['兜底摘录'] += 1
        else:
            t['有机制表述'] += 1
            if any(k in b for k in ('因为','由于','导致','根因','本质','机制','其实是','所以','因此','一旦','反过来','契约','静默','前提')):
                t['其中可判'] += 1
    tot = len(cs)
    print('机制解释分档（共 ' + str(tot) + ' 张）：')
    for k in ('有机制表述', '兜底摘录', '未记录', '未写', '其中可判'):
        v = t.get(k, 0)
        print('  ' + k.ljust(6) + str(v).rjust(4) + '  ' + (str(_pct(v, tot)) + ' pct' if tot else '（空库，不计算）'))
    print('说明：关键词只是粗筛，不是真相；兜底摘录只能当线索，不能当因果结论。')


def cmd_conflicts():
    """多会话冲突体检：谁在工具之外改了库、哪些卡同名、哪些卡没进台账、哪些探针没人批准。"""
    idx = _midx_ok(); cs = load_cards()
    print('【1 外部改动】索引建立后被工具外改过的卡：')
    n = 0
    for k, v in idx.items():
        f = os.path.join(ROOT, v['file'])
        if not os.path.exists(f): print('  - ' + v['file'] + '（文件已删）'); n += 1; continue
        cur = _h8(open(f, encoding='utf-8', errors='replace').read())
        if v.get('sha16') and cur != v['sha16']:
            print('  - ' + v['file']); n += 1
    if not n: print('  无（当前索引与磁盘一致）')
    print('\n【2 同名卡】多个文件声明同一 name：')
    names = {}
    for c in cs:
        nm = field(c['body'], 'name') or c['file'][:-3]
        names.setdefault(nm, []).append(c['file'])
    n2 = 0
    for nm, fs in names.items():
        if len(fs) > 1: print('  - ' + nm + ' → ' + '、'.join(fs)); n2 += 1
    if not n2: print('  无')
    print('\n【3 台账缺卡】在索引里但不在时效台账（时间债隐形）：')
    led = _ledg(); miss = [k for k in idx if k not in led]
    print('  ' + ('无' if not miss else '、'.join(miss[:8]) + ('…' if len(miss) > 8 else '')))
    print('\n【4 未批准探针】卡片有探针行但命令未经批准（等于没闸）：')
    n4 = 0
    for k, v in idx.items():
        if v.get('probe_rejected'): print('  - ' + v['file'] + '：' + str(v['probe_rejected'])[:40]); n4 += 1
    if not n4: print('  无')

    print('\n【5 未标来源线】还没有 line 的卡（不是"无主"，是"还没挂号"）：')
    lined = [(c['file'], field(c['body'], 'line') or field(c['body'], 'owner')) for c in cs]
    nobody = [f for f, o in lined if not o]
    print('  未标 ' + str(len(nobody)) + '/' + str(len(cs)) + ' 张' + ('，例：' + '、'.join(nobody[:5]) if nobody else '')
          + '\n  说明：对话会消失，项目会结题，但卡是遗产。未标线只影响"按项目复盘"，不影响检索与判据。')

    print('\n【6 口径分歧候选】触发词重叠 >=3 且判据档位不同（同一件事两套说法，待人裁决）：')
    tiers = {}
    for c in cs:
        mj = re.search(r'^##\s*判据\s*$([\s\S]*?)(?=^##\s|\Z)', c['body'], re.M)
        q = (mj.group(1).strip() if mj else '')
        tiers[c['file']] = '可执行' if q.startswith('可执行') else ('可复现' if q.startswith('可复现') else '仅参考')
    cands = []
    for k, v in idx.items():
        tr = set(v.get('triggers') or [])
        for k2, v2 in idx.items():
            if k >= k2: continue
            tr2 = set(v2.get('triggers') or [])
            ov = tr & tr2
            if len(ov) >= 3 and tiers.get(v['file']) != tiers.get(v2['file']):
                cands.append((len(ov), v['file'], v2['file'], tiers.get(v['file']), tiers.get(v2['file'])))
    cands.sort(reverse=True)
    if cands:
        for ov, f1, f2, t1, t2 in cands[:6]:
            print('  - [' + str(ov) + ' 词重叠] ' + f1 + '(' + t1 + ') ↔ ' + f2 + '(' + t2 + ')')
        print('  共 ' + str(len(cands)) + ' 对；裁决写进 `裁决台账.md`，工具不替你选边。')
    else:
        print('  无')


# ---------- 动库守卫（v6.39）----------
# 为什么要有这一段（2026-11-17 实测）：锁只保护"调用工具的人"。我在维护时用解压整包的方式
# 改库，那是覆盖式写入——不过锁、不发号，别人刚记的 D146 被整件换成包里的旧版，工具不知道，
# 也不报错。工具拦不住外部进程直接写文件，能做的是"事前拍指纹、事后一定发现、发现后能捞回来"。
# 所以：动库前后各拍一次，比行数、比哈希、比卡片数、比状态层逐节。

GUARD_KEEP = 12          # 最多留 12 份快照，多了删最旧的
GUARD_APPEND = ('缺陷台账', '发版流水', '状态变更史')   # 只增不改：行数只许增，减少就是丢账


def _guard_dir():
    return os.path.join(_state_dir(), '.guard')


def _guard_targets():
    """动库最容易被整件覆盖的几件。append_only=True 的是只增不改的账。"""
    return [('缺陷台账', DEFECT, True), ('发版流水', FLOW, True),
            ('状态层', STATE, False), ('任务清单', TASK, False),
            ('状态变更史', _state_hist(), True)]


def _guard_fp():
    """库的指纹：每件的行数 / 字节 / sha8，加卡片数与状态层逐节哈希。

    刻意记行数，不只记 sha8：覆盖式写入最典型的特征就是"行数变少"（实测 4024 行退回 3059 行），
    只比哈希只能说"变了"，说不出"丢了 965 行"。
    """
    fp = {}
    for name, p, app in _guard_targets():
        if not os.path.exists(p):
            fp[name] = {'exists': False, 'lines': 0, 'bytes': 0,
                        'sha8': None, 'append_only': app}
            continue
        b = open(p, 'rb').read()
        nl = b.count(b'\n') + (0 if (not b or b.endswith(b'\n')) else 1)
        fp[name] = {'exists': True, 'lines': nl, 'bytes': len(b),
                    'sha8': hashlib.sha256(b).hexdigest()[:8], 'append_only': app}
    try:
        fp['_cards'] = {'count': len(load_cards())}
    except Exception:
        fp['_cards'] = {'count': -1}
    if os.path.exists(STATE):
        t = open(STATE, encoding='utf-8', newline='').read()
        lines = t.split('\n')
        secs = {}
        for title, i, end in _sections(t):
            body = _sec_body(lines, i, end)
            secs[title] = {'sha8': _sha8(body), 'len': len(body)}
        fp['_sections'] = secs
    else:
        fp['_sections'] = {}
    return fp


def _guard_snap(tag=''):
    """拍快照：指纹 + 三本账与状态层的正文副本。

    只存指纹不够——检出"丢了 965 行"之后，还得能把那 965 行捞回来。所以同时留正文副本。
    """
    ts = time.strftime('%Y%m%d-%H%M%S')
    name = ts + (('-' + re.sub(r'[^0-9A-Za-z\u4e00-\u9fa5-]', '_', tag)[:24]) if tag else '')
    d = os.path.join(_guard_dir(), name)
    try:
        os.makedirs(d)
    except Exception:
        pass
    fp = _guard_fp()
    copies = {}
    for nm, p, _app in _guard_targets():
        if os.path.exists(p):
            fn = os.path.basename(p)
            try:
                shutil.copy2(p, os.path.join(d, fn))
                copies[nm] = fn
            except Exception:
                pass
    meta = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'host': _host(),
            'tag': tag, 'name': name, 'files': fp, 'copies': copies}
    with open(os.path.join(d, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    # 只留最近 GUARD_KEEP 份。删的是自己的快照，不是库件。
    # 不用 rmtree(ignore_errors=True)：那正是本工具的静态门禁 R2 要抓的写法（D61）。
    try:
        olds = sorted(os.listdir(_guard_dir()))
        for x in olds[:-GUARD_KEEP]:
            _rm_force(os.path.join(_guard_dir(), x))
    except Exception:
        pass
    return d, fp


def _guard_list():
    d = _guard_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        mp = os.path.join(d, n, 'meta.json')
        if not os.path.exists(mp):
            continue
        try:
            m = json.load(open(mp, encoding='utf-8'))
        except Exception:
            continue
        out.append((n, m))
    return out


def _guard_diff_fp(base, cur):
    """比两份指纹，返回 (问题列表, 变化列表)。问题=丢账一类，变化=正常改动但要报出来。"""
    bad, chg = [], []
    for name in GUARD_APPEND:
        b, c = base.get(name), cur.get(name)
        if not b or not c:
            continue
        if not b.get('exists') and c.get('exists'):
            chg.append('%s：快照时没有，现在有了' % name)
            continue
        if b.get('exists') and not c.get('exists'):
            bad.append('%s：整件消失（快照时 %d 行）' % (name, b.get('lines', 0)))
            continue
        bl, cl = b.get('lines', 0), c.get('lines', 0)
        if cl < bl:
            bad.append('%s：行数从 %d 掉到 %d，少了 %d 行 —— 典型的覆盖式写入' % (name, bl, cl, bl - cl))
        elif cl > bl:
            chg.append('%s：%d → %d 行（追加 %d 行）' % (name, bl, cl, cl - bl))
        elif b.get('sha8') != c.get('sha8'):
            bad.append('%s：行数没变（%d），但内容被改写（%s → %s）—— 同长度替换'
                       % (name, bl, b.get('sha8'), c.get('sha8')))
    for name in ('状态层', '任务清单'):
        b, c = base.get(name), cur.get(name)
        if not b or not c:
            continue
        if b.get('exists') and not c.get('exists'):
            bad.append('%s：整件消失' % name)
            continue
        if b.get('sha8') != c.get('sha8'):
            bb, cb = b.get('bytes', 0), c.get('bytes', 0)
            if bb and cb and cb < bb * 0.75:
                bad.append('%s：体积从 %d 字节掉到 %d 字节（少了 %.0f%%）—— 疑似被旧版覆盖'
                           % (name, bb, cb, (1 - cb / float(bb)) * 100))
            else:
                chg.append('%s：内容变了（%d → %d 字节）' % (name, bb, cb))
    bc = base.get('_cards', {}).get('count', -1)
    cc = cur.get('_cards', {}).get('count', -1)
    if bc >= 0 and cc >= 0 and cc < bc:
        bad.append('卡片：从 %d 张掉到 %d 张，少了 %d 张' % (bc, cc, bc - cc))
    elif bc >= 0 and cc > bc:
        chg.append('卡片：%d → %d 张' % (bc, cc))
    # 状态层逐节：整件被换掉时，这里能指出具体哪几节没了
    bs = base.get('_sections') or {}
    cs = cur.get('_sections') or {}
    for t in bs:
        if t not in cs:
            bad.append('状态层节「%s」消失（快照时 %d 字节）' % (t, bs[t]['len']))
        elif bs[t]['sha8'] != cs[t]['sha8']:
            bL, cL = bs[t]['len'], cs[t]['len']
            if bL and cL < bL * 0.6:
                bad.append('状态层节「%s」从 %d 字节缩到 %d 字节（少了 %.0f%%）'
                           % (t, bL, cL, (1 - cL / float(bL)) * 100))
    for t in cs:
        if t not in bs:
            chg.append('状态层新增节「%s」' % t)
    return bad, chg


def _auto_guard():
    """写完自动核一次（**不阻断本次写入**）：只抓"丢件"与"骤降"。

    为什么不阻断：正常的写永远是追加/变大。真出现丢件时，既可能是平台同步干的
    （跟我们这一笔无关），也可能是我们写坏了（概率极低）。此时最该做的是**立刻报
    且不掩盖**，而不是把刚记的那一笔也回滚掉——账记上了、同时大喊一声，比两件都丢好。
    """
    try:
        import io as _i, contextlib as _c
        buf = _i.StringIO()
        with _c.redirect_stdout(buf):
            try:
                cmd_guard_commit([])
            except SystemExit:
                pass
        out = buf.getvalue()
        if 'GUARD NOT PASS' in out:
            print('⚠ 这一写之后，库里出现丢件/截断（不是本次该改的件）：')
            for l in out.splitlines():
                if ('✗' in l) or ('缺件' in l and '：' in l) or ('骤降' in l and '：' in l):
                    print(l)
            print('  处置：`guard pending` 看明细 → `restore --from <包>` 补齐；'
                  '若确实该变，`guard accept` 认作新基线')
    except Exception:
        pass


def _lib_scan(root=None):
    """全库逐件指纹：{相对路径: [字节, sha16]}。

    与 `_guard_fp()` 的区别：那个只盯三本账与状态层，**抓不到"某个文件整件没了"**。
    D162 丢的 50 件里，`memory/memory_202611.jsonl`、`探针批准.json`、`资产册/`
    一个都不在三本账名单上，所以旧指纹全绿、实际缺一半。基线必须扫全库。
    """
    root = root or ROOT
    out = {}
    for base, _d, fs in os.walk(root):
        for f in fs:
            p = os.path.join(base, f)
            rel = os.path.relpath(p, root).replace(os.sep, '/')
            # 自指件不能进基线：待裁决文件在"接受"那一步会被删掉，
            # 若它也记在基线里，下一次比对必然报它"缺"——自己撞自己，永远清不掉。
            if f == '.guard_pending.json' or f.endswith('.tmp') or '.conflict.' in f:
                continue
            # 快照是"留痕"，不是库内容：每次改状态层都会新增一件，且会按保留策略被删。
            # 把它算进件数，等于让一个只增不减的目录去淹没"有没有被覆盖"这个判据。
            if rel.startswith('.state_snapshots/'):
                continue
            # `.guard/` 是本机回滚缓冲（guard snap 可随时再生，按时刻命名），
            # 与快照同族：不进件数判据、不进包。否则每 snap 一次件数就涨一批，
            # 件数判据会被缓冲淹没——那正是 D209 第五条口径要挡的事。
            if rel.startswith('.guard/'):
                continue
            try:
                b = open(p, 'rb').read()
            except Exception:
                continue
            out[rel] = [len(b), hashlib.sha256(b).hexdigest()[:16]]
    return out


def _baseline_path():
    return os.path.join(ROOT, '.sync_baseline.json')


def _baseline_load():
    """读预期基线。兼容两种写法：旧 `{path: "sha16"}`（只有哈希）与新 `{path: [字节, sha16]}`。

    旧格式没有字节数，做不了"骤降"判据——那就只按"存在/缺失"判，并在输出里说清
    这一轮为什么判得比新格式弱。
    """
    p = _baseline_path()
    if not os.path.exists(p):
        return {}, 'none'
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        return {}, 'bad'
    files = d.get('files') or {}
    rich = any(isinstance(v, list) for v in files.values())
    return files, ('rich' if rich else 'legacy')


def _baseline_save(files):
    p = _baseline_path()
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'files': files, 'ts': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                   'by': os.environ.get('HOSTNAME') or (os.uname().nodename if hasattr(os, 'uname') else ''),
                   'note': '预期基线：guard commit 通过后自动更新；外部写入越界会先落到 .guard_pending.json 等人裁决'},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _pending_path():
    return os.path.join(ROOT, '.guard_pending.json')


PENDING_DOC = ('越界未裁决：这些件变了，但改动方没有声明要改它们。\n'
               '  guard accept            判"该变"：把当前状态认作新基线，留痕\n'
               '  restore --from <包>        判"不该变"：从包补齐/回滚\n'
               '  若缺的是你想删的卡 → retire --card <名> --why "<理由>"（归档+索引+基线一次做完）\n')


def cmd_guard_commit(argv):
    """写操作收尾：与预期基线比，**只抓"丢"和"缩"，不抓"增"**。

    为什么这样定判据：库里正常的写入永远是"追加或变大"——记一笔台账、加一行状态、
    重建索引。会变小的只有两种事：①文件被截断（今天的使用台账 12136B → 88B）；
    ②整件被抹掉（memory/、探针批准、部署清单）。把"变多"也拦，会天天假红，
    最后没人看告警——这比不拦更糟。

    退出码：0 = 无越界，基线已更新；7 = 发现越界，未更新基线，落在 .guard_pending.json；
    2 = 基线读不了（先跑 guard snap 或恢复基线）。
    """
    files_base, kind = _baseline_load()
    if kind in ('none', 'bad'):
        print('⛔ 没有可用的预期基线（%s）。先跑 `guard commit --init` 用当前库立基线。' % kind)
        sys.exit(2)
    cur = _lib_scan()
    missing, shrunk, grew = [], [], []
    for k, v in files_base.items():
        if k not in cur:
            missing.append((k, v))
            continue
        bsize = v[0] if isinstance(v, list) else None
        if bsize is not None and bsize > 200 and cur[k][0] < bsize * 0.5:
            shrunk.append((k, bsize, cur[k][0]))
    for k in cur:
        if k not in files_base:
            grew.append(k)
    print('GUARD COMMIT：基线 %d 件 / 当前 %d 件（基线格式 %s）' % (len(files_base), len(cur), kind))
    if missing or shrunk:
        print('  缺件 %d（基线有、现在没有）：' % len(missing))
        for k, v in missing[:20]:
            print('    ✗ %-56s %s' % (k, (str(v[0]) + 'B') if isinstance(v, list) else '有记录'))
        if len(missing) > 20:
            print('    ……另 %d 件' % (len(missing) - 20))
        print('  骤降 %d（不足原来一半）：' % len(shrunk))
        for k, a, b in shrunk[:15]:
            print('    ✗ %-56s %8dB → %8dB' % (k, a, b))
        pend = {'ts': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'missing': [k for k, _ in missing], 'shrunk': [[k, a, b] for k, a, b in shrunk],
                'added': grew[:400]}
        open(_pending_path(), 'w', encoding='utf-8').write(json.dumps(pend, ensure_ascii=False, indent=1))
        print('\nGUARD NOT PASS：越界 %d 项，基线未动，已落 .guard_pending.json' % (len(missing) + len(shrunk)))
        print(PENDING_DOC)
        sys.exit(7)
    if grew:
        print('  新增/变大 %d（正常增长，放行）：%s' % (len(grew), '、'.join(grew[:6]) + ('…' if len(grew) > 6 else '')))
    # 骨架卡不许认进基线。`new` 造的骨架带 state: skeleton，它"该不该在"还没答案；
    # 认进基线等于替人宣布"这张卡成立了"——今天两张空骨架卡就是这么混进基线的。
    skel = []
    for k in cur:
        if not k.endswith('.md'):
            continue
        try:
            head = open(os.path.join(ROOT, k), encoding='utf-8').read(300)
        except Exception:
            continue
        if re.search(r'^state:\s*skeleton\s*$', head, re.M):
            skel.append(k)
    if skel:
        print('⛔ 有 %d 张骨架卡没填（state: skeleton），不许认进基线：' % len(skel))
        for x in skel[:8]:
            print('    ✗ ' + x)
        if len(skel) > 8:
            print('    ……另 %d 张' % (len(skel) - 8))
        print('    处置：填完把 front matter 的 state 改成 active；不想留就 retire。')
        sys.exit(7)
    _baseline_save(cur)
    if os.path.exists(_pending_path()):
        os.remove(_pending_path())
    print('GUARD PASS：没有丢件、没有截断；基线已更新为当前 %d 件' % len(cur))
    return 0


def cmd_guard_pending(argv):
    p = _pending_path()
    if not os.path.exists(p):
        print('没有待裁决的越界。')
        return
    d = json.load(open(p, encoding='utf-8'))
    print('待裁决（%s）：' % d.get('ts'))
    if d.get('missing'):
        print('  缺件 %d：' % len(d['missing']))
        for k in d['missing'][:20]:
            print('    ✗ ' + k)
    if d.get('shrunk'):
        print('  骤降 %d：' % len(d['shrunk']))
        for k, a, b in d['shrunk'][:15]:
            print('    ✗ %s %dB → %dB' % (k, a, b))
    print('\n' + PENDING_DOC)


def cmd_guard_accept(argv):
    """人判"该变"：把当前状态认作新基线，清空待裁决，并在状态变更史留痕。"""
    cur = _lib_scan()
    _baseline_save(cur)
    if os.path.exists(_pending_path()):
        os.remove(_pending_path())
    try:
        _hist_append({'ts': datetime.datetime.now().isoformat(timespec='seconds'),
                      'host': _hst(), 'action': 'accept', 'section': '',
                      'note': '人判该变：越界件被接受为新基线（现 %d 件）' % len(cur)})
    except Exception as e:
        print('  （变更史留痕失败：%s）' % str(e)[:60])
    print('已接受：基线更新为当前 %d 件，待裁决已清空（留痕在状态变更史）' % len(cur))


def _use_ledger():
    p = os.path.join(ROOT, '使用台账.jsonl')
    if not os.path.exists(p):
        return []
    rows = []
    for ln, l in enumerate(open(p, encoding='utf-8'), 1):
        l = l.strip()
        if not l:
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        r['_ln'] = ln          # 行号是身份：台账只追加，前面行号永不移动
        rows.append(r)
    return rows


def _eval_merge(rows):
    """把评价叠到使用记录上：同一条使用记录只认最新一次评价。

    为什么用行号而不是 (问句, 日期)：实测同一问句同一天记了 37 条
    （`搜索摘要 实时页面 缓存 未变` / 2026-11-17），问句+日期根本不唯一。
    行号不会撞，也不会因为后面追加而变。

    为什么不回写原行：使用台账是只追加的账。评价若去改 result 字段，
    等于给"历史可以被重写"开了口子；所以评价另起一条（kind=eval），靠"取最新"决定解释权。
    """
    ev, drifted = {}, []
    for r in rows:
        if r.get('kind') == 'eval':
            rid = r.get('ref_id')
            if rid:
                # 行号只是"第几行"，不是身份证：别的窗口合并/重排台账时，
                # 同一个行号会换人。所以评价里同时存了问句指纹，读回时对一次；
                # 对不上就不许套用——否则会把"这条评价"贴到另一次使用上（假因果）。
                qh = r.get('ref_qh')
                base = next((x for x in rows if x.get('_ln') == rid and x.get('kind') != 'eval'), None)
                if qh and base is not None and _h8(base.get('q') or '') != qh:
                    drifted.append(r)
                    continue
                ev[rid] = r
            else:
                ev[('legacy', r.get('q'), r.get('ref_ts'))] = r
    _eval_merge.drifted = drifted
    out = []
    for r in rows:
        if r.get('kind') == 'eval':
            continue
        e = ev.get(r['_ln']) or ev.get(('legacy', r.get('q'), r.get('ts'))) or {}
        out.append(dict(r, _result=e.get('result', r.get('result')),
                        _boundary=e.get('boundary', ''),
                        _evaluated_at=e.get('ts', ''),
                        _by=e.get('by', ''),
                        _self=e.get('self', False)))
    return out


EVAL_WORDS = ('帮上', '没帮上', '不确定')


def _probe_cmd(body):
    """探针行 → 命令。**生产与样例共用这一个函数**。

    以前样例里又抄了一份 split，于是"样例全过"只证明副本对，不证明生产对——
    负控制当场抓到（把生产改回旧写法，样例仍 PASS）。规则只能有一份实现。
    """
    # 命令到第一个"元数据字段"为止。曾只按 '| 期望' 切，
    # 没有"期望"字段的探针（`探针: … | 说明=…`）整行被当成命令，
    # 哈希对不上，就被误判成"探针漂移"——那是解析造出来的假象（D174）。
    return re.split(r'\|\s*(?:期望|说明)', body, maxsplit=1)[0].strip()


def _card_probes(path):
    """取一张卡里的探针：返回 [(命令, 原始行)]。

    只认 `探针:` 开头（正文里以"探针"二字开头的句子不算——实测踩过）。
    命令取到 `| 期望` 之前那一段，因为管道的 `|` 属于分隔符，不属于命令。
    """
    out = []
    try:
        txt = open(path, encoding='utf-8').read()
    except Exception:
        return out
    for l in txt.splitlines():
        t = l.strip()
        if t.startswith('探针:'):
            body = t[3:]
            cmd = _probe_cmd(body)
            out.append((cmd, t))
    return out


DEFECT_XREF = os.path.join(ROOT, '资产册', '卡治缺陷.json')
# 认 "D123" 这种编号。两条边界都必须有：
#   前面不能是字母数字（否则 PKHD1 / PKD1 里的 D1 会被抓）
#   后面不能是字母数字（否则 D1S80、D4Z4 这类位点/重复元件名会被抓成 D1、D4）
# 再容忍三种写法：D-161 / D 161 / 全角 Ｄ１６１——漏记比误记好，但漏了也要收。
# 只认紧贴写法 D161。曾试过容忍 D-161 / D 161，实测立刻造出假阳性：
# "考核 D 1 项"→D1、"血型 D-1 型"→D1、"D 换行 161"→D161。
# 宁可漏（真有人写 D-161 就漏记，落卡规范要求写 D161），不可造。
_DRE = re.compile(r'(?<![A-Za-z0-9])D([0-9]{1,3})(?![0-9A-Za-z])')
_FW = str.maketrans('０１２３４５６７８９Ｄ', '0123456789D')


def _dnorm(text):
    """把全角数字与全角 D 折成半角，只为匹配用。"""
    return (text or '').translate(_FW)


def _max_defect():
    p = os.path.join(ROOT, '缺陷台账_20261112.md')
    n = 0
    if os.path.exists(p):
        for m in re.finditer(r'^###\s*D(\d+)', open(p, encoding='utf-8').read(), re.M):
            n = max(n, int(m.group(1)))
    return n


def _dxref_build():
    """只提取卡里已经写下的 D 编号，不做任何推断。

    为什么零推断：让工具去猜"这张卡大概治哪个缺陷"，会造出假指针——
    以后查 D161 指到一张不相干的卡，比没有索引更坏。
    """
    cards = load_cards()
    mx = _max_defect()
    idx, orphan, unlinked = {}, {}, 0
    for c in cards:
        name = c['file'][:-3] if c['file'].endswith('.md') else c['file']
        ds = sorted({int(x) for x in _DRE.findall(_dnorm(c['body']))})
        if not ds:
            unlinked += 1
            continue
        good = ['D%d' % d for d in ds if d <= mx]
        bad = ['D%d' % d for d in ds if d > mx]
        if good:
            idx[name] = good
        if bad:
            orphan[name] = bad
    doc = {'built_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
           'max_defect': 'D%d' % mx,
           'cards_total': len(cards),
           'cards_with_ref': len(idx),
           'unlinked': unlinked,
           'cards': idx, 'orphans': orphan}
    d = os.path.dirname(DEFECT_XREF)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    open(DEFECT_XREF, 'w', encoding='utf-8').write(json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
    return doc


def _dxref_load():
    """读派生表；**源账比它新就重建**。

    为什么要这句：记完 D173-D175 之后，`trace --stats` 还在报"台账最大 D172"——
    派生表是缓存，缓存不知道自己过期了，就会当着人的面说旧话。
    """
    if os.path.exists(DEFECT_XREF):
        try:
            d = json.load(open(DEFECT_XREF, encoding='utf-8'))
            live = _max_defect()
            if d.get('max_defect') == live:
                return d
        except Exception:
            pass
    try:
        return _dxref_build()
    except Exception:
        return None


PROV_FILE = os.path.join(ROOT, '资产册', '出处状态.json')


def _prov_blocks(text, pat):
    """把一本账切成 [（标识, 正文）] 的块，供"最近的块"定位。"""
    out = []
    cur, buf = None, []
    for line in text.splitlines():
        m = re.match(pat, line)
        if m:
            if cur is not None:
                out.append((cur, '\n'.join(buf)))
            cur, buf = m.group(1), [line]
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur, '\n'.join(buf)))
    return out


def _prov_build():
    """如实标出处状态，三档：台账 D<n> / 流水 v<x.y> / 无记录。

    只做"这张卡的名字出现在哪一段账里"的机械定位，不做语义推断——
    推断出处会造出假的家谱，比"无记录"更坏。

    为什么写成独立的派生表、而不是往 246 张卡正文里塞一行：
    塞进去就是 246 次哈希变动 + 索引重建 + 与台账不同步的漂移风险；
    而这份状态本来就是台账投影出来的，只该有"一处真相"。
    """
    cards = [c['file'][:-3] for c in load_cards()]
    dt = open(os.path.join(ROOT, '缺陷台账_20261112.md'), encoding='utf-8').read() \
        if os.path.exists(os.path.join(ROOT, '缺陷台账_20261112.md')) else ''
    fl = open(os.path.join(ROOT, '发版流水_202611.md'), encoding='utf-8').read() \
        if os.path.exists(os.path.join(ROOT, '发版流水_202611.md')) else ''
    # 定位方式：按"出现位置"向前找最近的编号标记，而不是按标题切块。
    # 为什么改：台账里有一整张汇总表（`| D98 | … |`），卡名出现在表格行里、
    # 头上并没有 `### D` 标题；按块切会把这几张判成"无记录"（实测漏了 4 张）。
    # 流水早期块又用"六十一、"这种中文序号，只认 v6.x 会把整段历史漏掉。
    _dmark = re.compile(r'###\s*(D\d+)|\|\s*(D\d+)\s*\|')
    _fmark = re.compile(r'^##\s+(.+)$', re.M)

    def _nearest_d(pos):
        last = None
        for mm in _dmark.finditer(dt, 0, pos):
            last = mm.group(1) or mm.group(2)
        return last

    def _nearest_v(pos):
        last = None
        for mm in _fmark.finditer(fl, 0, pos):
            h = mm.group(1)
            v = re.search(r'v\d+\.\d+', h)
            last = v.group(0) if v else h.strip()[:14]
        return last

    out, tally = {}, {'台账': 0, '流水': 0, '无记录': 0}
    for c in cards:
        pd = [mt.start() for mt in re.finditer(re.escape(c), dt)]
        pf = [mt.start() for mt in re.finditer(re.escape(c), fl)]
        dn = _nearest_d(pd[-1]) if pd else None
        vn = _nearest_v(pf[-1]) if pf else None
        if dn:
            out[c] = {'status': '台账 %s' % dn, 'all': [dn], 'sort': '台账'}
            tally['台账'] += 1
        elif vn:
            out[c] = {'status': '流水 %s' % vn, 'all': [vn], 'sort': '流水'}
            tally['流水'] += 1
        else:
            out[c] = {'status': '无记录', 'all': [], 'sort': '无记录'}
            tally['无记录'] += 1
    doc = {'built_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
           'cards_total': len(cards), 'tally': tally, 'cards': out,
           'note': '出处=卡名出现在哪一段账里；三档如实标，不承诺补齐；无记录不等于这张卡没价值'}
    d = os.path.dirname(PROV_FILE)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    open(PROV_FILE, 'w', encoding='utf-8').write(json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
    return doc


# ─────────────────────────────────────────────────────────────
# 事实轴（v6.44）：一次扫全库，四个入口只查不算。
#
# 为什么要有这层：trace / evidence / provenance / eval 各自回真源里重新嗅散文，
# 于是同一件事被解析四次、出四种样子（D173-D176 就是这四次的样子）。
# 现在真源只在 _axis_build 里读一次，其余四个命令只读这份派生表。
#
# 副产品是"过期看得见"：表头记源签名（台账/流水/卡正文/批准表/探针台账），
# 读前比对，不符即重建——不再出现"最大 D172 而台账到 D176"这种话。
# ─────────────────────────────────────────────────────────────
AXIS_FILE = os.path.join(ROOT, '资产册', '事实轴.json')
AXIS_VER = '2'   # v6.59：轴里新增 defect_ids（旧轴缺这键会让 trace 的"无锚缺陷"清单空掉）


def _axis_src_text():
    dt = open(DEFECT, encoding='utf-8').read() if os.path.exists(DEFECT) else ''
    fl = open(FLOW, encoding='utf-8').read() if os.path.exists(FLOW) else ''
    pl = os.path.join(ROOT, '探针执行台账.jsonl')
    ap = os.path.join(ROOT, '探针批准.json')
    runs = open(pl, encoding='utf-8').read() if os.path.exists(pl) else ''
    appr = open(ap, encoding='utf-8').read() if os.path.exists(ap) else ''
    return dt, fl, runs, appr


def _axis_sig():
    cs = load_cards()
    dt, fl, runs, appr = _axis_src_text()
    return {'dt': _h8(dt), 'fl': _h8(fl), 'runs': _h8(runs), 'appr': _h8(appr),
            'cards': _h8('|'.join(sorted(c['file'] for c in cs)) + '|' +
                         '|'.join(_h8(c['body']) for c in sorted(cs, key=lambda x: x['file']))),
            'ver': AXIS_VER}


def _axis_build():
    dt, fl, runs_txt, appr_txt = _axis_src_text()
    cs = load_cards()
    danch = [(m.start(), m.group(1) or m.group(2))
             for m in re.finditer(r'###\s*(D\d+)|\|\s*(D\d+)\s*\|', dt)]
    fanch = [(m.start(), m.group(1)) for m in re.finditer(r'^##\s+(.+)$', fl, re.M)]

    r_cd = {}
    for c in cs:
        ds = ['D' + n for n in _DRE.findall(_dnorm(c['body']))]
        if ds:
            r_cd[c['file'][:-3]] = ds

    r_cp, tally = {}, {'台账': 0, '流水': 0, '无记录': 0}
    for c in cs:
        name = c['file'][:-3]
        pd = [m.start() for m in re.finditer(re.escape(name), dt)]
        pf = [m.start() for m in re.finditer(re.escape(name), fl)]
        dn = None
        if pd:
            prev = [a for a in danch if a[0] <= pd[-1]]
            dn = prev[-1][1] if prev else None
        vn = None
        if pf:
            prev = [a for a in fanch if a[0] <= pf[-1]]
            if prev:
                mm = re.search(r'v\d+\.\d+', prev[-1][1])
                vn = mm.group(0) if mm else prev[-1][1].strip()[:14]
        if dn:
            r_cp[name] = {'status': '台账 %s' % dn, 'all': [dn], 'sort': '台账'}
            tally['台账'] += 1
        elif vn:
            r_cp[name] = {'status': '流水 %s' % vn, 'all': [vn], 'sort': '流水'}
            tally['流水'] += 1
        else:
            r_cp[name] = {'status': '无记录', 'all': [], 'sort': '无记录'}
            tally['无记录'] += 1

    probes = {}
    for c in cs:
        name = c['file'][:-3]
        for cmd, line in _card_probes(c['path']):
            probes.setdefault(name, []).append({'cmd': cmd, 'line': line, 'h': _h8(cmd)})

    approved = sorted(_approved())

    last, succ = {}, {}
    for l in runs_txt.splitlines():
        l = l.strip()
        if not l:
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        c = r.get('card')
        if not c:
            continue
        last[c] = r
        if r.get('ok'):
            succ[c] = r

    md_names = sorted(n for n in os.listdir(ROOT)
                      if n.endswith('.md') and not os.path.islink(os.path.join(ROOT, n)))

    doc = {'rule_ver': AXIS_VER, 'source_sig': _axis_sig(),
           'built_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
           'n_cards': len(cs), 'n_md': len(md_names), 'md_names': md_names,
           'card_names': sorted(c['file'][:-3] for c in cs),
           'max_defect': 'D%d' % _max_defect(),
           # v6.59：台账里出现过的全部 D 号（从上面已解析的 danch 去重，不另开一次读源）。
           # 给 trace 的"无锚缺陷"清单用，免得调用方自己去 open(DEFECT)（静态门禁 R5 不许）。
           'defect_ids': sorted({x[1] for x in danch}, key=lambda s: int(s[1:])),
           'r_card_defect': r_cd, 'r_card_prov': r_cp, 'tally': tally,
           'probes': probes, 'approved': approved, 'last_run': last, 'succ': succ}
    d = os.path.dirname(AXIS_FILE)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    # 原子写：别的窗口随时也在重建轴，直接 open('w') 会在写到一半时被人读到半个 JSON。
    # 半步产物读出来就是"看起来有表、其实是残的"，比没有更坏。
    tmp = AXIS_FILE + '.tmp%d' % os.getpid()
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, AXIS_FILE)
    return doc


def _axis_get(force=False):
    """读事实轴；源签名不符就重建（重建是幂等的，所以"过期"不是错误，只是重算一次）。"""
    if not force and os.path.exists(AXIS_FILE):
        try:
            d = json.load(open(AXIS_FILE, encoding='utf-8'))
            if d.get('source_sig') == _axis_sig() and d.get('rule_ver') == AXIS_VER:
                return d
        except Exception:
            pass
    return _axis_build()


def _axis_dxref(force=False):
    """给 trace 用的视图：卡→治D + 孤儿指针。"""
    a = _axis_get(force)
    mx = str(a['max_defect'])
    if not mx.startswith('D'):
        mx = 'D' + mx
    maxn = int(mx[1:]) if re.fullmatch(r'D\d+', mx) else 0
    keep, orph = {}, {}
    for c, ds in a['r_card_defect'].items():
        bad = [x for x in ds if int(x[1:]) > maxn]
        if bad:
            orph[c] = bad
            if len(bad) < len(ds):
                keep[c] = [x for x in ds if x not in bad]
        else:
            keep[c] = ds
    return {'cards': keep, 'cards_total': a['n_cards'], 'cards_with_ref': len(keep),
            'unlinked': a['n_cards'] - len(keep), 'max_defect': mx, 'orphans': orph,
            'defect_ids': a.get('defect_ids', [])}


def _axis_prov(force=False):
    """给 provenance 用的视图：卡→出处三档。"""
    a = _axis_get(force)
    return {'cards_total': a['n_cards'], 'tally': a['tally'], 'cards': a['r_card_prov']}


def _axis_probes(card):
    """给 evidence / eval 用：某张卡里的探针（命令 + 原行）。"""
    return [(p['cmd'], p['line']) for p in _axis_get()['probes'].get(card, [])]


# ─────────────────────────────────────────────────────────────
# 解析契约（契约 A）：每条解析规则必须被一组"正例/反例"钉住。
#
# 为什么：D173 与 D174 都是"规则改宽/改窄之后没人校"，改完自认对了就写进结论。
# 有了样例，同样的错误改完立刻红，走不到"写进方案"那一步。
# 限制要说清：样例只能挡住已经想到的坑，挡不住新坑——但"同一个坑踩第二次"变成不可能。
# ─────────────────────────────────────────────────────────────
_PARSE_SAMPLES = [
    ('D编号·紧贴写法', 'defect', '同 D95 的修法', [95]),
    ('D编号·多号并列', 'defect', 'D161/D162', [161, 162]),
    ('D编号·全角数字', 'defect', 'Ｄ１６１ 的问题', [161]),
    ('D编号·基因名拒收', 'defect', 'PKHD1 与 PKD1 不是缺陷号', []),
    ('D编号·位点名拒收', 'defect', 'D1S80、D4Z4 是位点', []),
    ('D编号·哈希片段拒收', 'defect', '07CDD79D 是哈希', []),
    ('D编号·空格写法拒收', 'defect', '考核 D 1 项', []),
    ('D编号·连字符拒收', 'defect', '血型 D-1 型', []),
    ('D编号·跨行拒收', 'defect', 'D\n161 行', []),
    ('探针·只有说明字段', 'probe', 'py <SKILL>/recall.py stats | 说明=能跑', 'py <SKILL>/recall.py stats'),
    ('探针·说明在前期望在后', 'probe', 'py <SKILL>/recall.py gate | 说明=x | 期望>=1', 'py <SKILL>/recall.py gate'),
    ('探针·只有期望字段', 'probe', 'py <SKILL>/recall.py gate | 期望>=1', 'py <SKILL>/recall.py gate'),
    ('探针·命令自带管道', 'probe', 'cat x | grep y | 期望>=1', 'cat x | grep y'),
]


def cmd_derive(argv=None):
    """解析契约自检：跑全部样例。任何一条不符即 exit 1。"""
    import argparse
    argv = sys.argv[2:] if argv is None else argv
    ap = argparse.ArgumentParser(prog='recall.py derive')
    ap.add_argument('--verify', action='store_true', help='跑解析样例')
    ap.add_argument('--axis', action='store_true', help='看事实轴状态')
    ns = ap.parse_args(argv)
    if ns.axis:
        a = _axis_get()
        print('事实轴：%s（卡 %d / 最大 %s / 建于 %s）'
              % (os.path.relpath(AXIS_FILE, ROOT), a['n_cards'], a['max_defect'], a['built_at']))
        print('源签名：%s' % ', '.join('%s=%s' % (k, v) for k, v in a['source_sig'].items()))
        return 0
    if not ns.verify:
        print('用法：derive --verify（跑解析样例）｜derive --axis（看轴状态）')
        return 0
    bad = []
    for name, kind, sample, want in _PARSE_SAMPLES:
        if kind == 'defect':
            got = [int(x) for x in _DRE.findall(_dnorm(sample))]
        else:
            got = _probe_cmd(sample)
        if got != want:
            bad.append((name, sample, want, got))
    if not bad:
        print('DERIVE PASS：解析样例 %d 条全过（D 编号 9 条 / 探针切分 4 条）' % len(_PARSE_SAMPLES))
        return 0
    print('DERIVE FAIL：%d 条不符' % len(bad))
    for name, sample, want, got in bad:
        print('  ✗ %s｜输入 %r｜期望 %r｜得到 %r' % (name, sample, want, got))
    sys.exit(1)


def cmd_where(argv=None):
    """谁是谁：代码源/安装位、库候选/被选中/影子入口，一次说清。

    与前几版的区别（2026-11-17 自检）：
    - 候选清单读 `_root_cands()`，不自己另写一份（两份实现 = D174 同病）；
    - 不只看 /runtime，把**每一个**候选都摊开：存在否、真身落在哪、最大缺陷号；
    - 红灯只在"真的会跑错库"时亮：持久盘那份在、工具却选到别处。
    """
    import argparse
    ap = argparse.ArgumentParser(prog='recall.py where')
    ap.add_argument('--json', action='store_true', help='按 JSON 输出')
    ns = ap.parse_args(argv or [])

    def h8f(pth):
        try:
            return hashlib.sha256(open(pth, 'rb').read()).hexdigest()[:8]
        except Exception:
            return ''

    def maxd(d):
        """库里的最大缺陷号：判"这份旧到哪一年"的硬指标，不看 mtime。"""
        try:
            ps = [x for x in os.listdir(d) if x.startswith('缺陷台账')]
            if not ps:
                return ''
            txt = open(os.path.join(d, ps[0]), encoding='utf-8', errors='ignore').read()
            heads = re.findall(r'(?m)^###\s*D(\d+)', txt)
            if heads:
                return 'D%d' % max(int(x) for x in heads)
            ds = re.findall(r'(?<![0-9A-Za-z])D(\d{1,4})(?![0-9])', txt)   # 兜底：前后不许贴字母数字，挡 log_id 里的 B2D427
            return 'D%d' % max(int(x) for x in ds) if ds else ''
        except Exception:
            return ''

    code_src = os.path.abspath(__file__)
    rows = [{'kind': '代码源（唯一可写）', 'path': code_src, 'sig': h8f(code_src), 'state': '源'}]

    # 安装位：只报签名与是否同一文件，不猜谁对
    inst = ['/runtime/user_skills/experience-recall/recall.py',
            os.path.join(os.path.expanduser('~'), '.doubao/agent_mode/workspace/.user_skills/experience-recall/recall.py')]
    for pth in inst:
        if not os.path.exists(pth):
            rows.append({'kind': '安装位', 'path': pth, 'sig': '', 'state': '不存在'})
            continue
        sig = h8f(pth)
        try:
            ino = os.stat(pth).st_ino
        except Exception:
            ino = None
        if ino and any(x.get('_ino') == ino for x in rows):
            state = '与上面那条安装位同一文件'
        elif sig == h8f(code_src):
            state = '一致'
        else:
            state = '落后于源（待装）'
        rows.append({'kind': '安装位', 'path': pth, 'sig': sig, 'state': state, '_ino': ino})

    # 库候选：与工具真正选库用同一份清单
    resolved = os.path.abspath(ROOT)
    wrong = False
    persistent = os.path.join(HOME, 'experience')
    seen = set()
    for c in _root_cands():
        ap_ = os.path.abspath(c)
        if ap_ in seen:
            continue
        seen.add(ap_)
        if not os.path.exists(ap_):
            rows.append({'kind': '库候选', 'path': ap_, 'sig': '', 'state': '不存在'})
            continue
        real = os.path.realpath(ap_)
        islink = os.path.islink(ap_)
        looks = _looks_like_lib(ap_)
        if ap_ == resolved:
            state = '选中的这份（工具正在用）'
            wrong = not looks
        else:
            state = ('影子入口 → %s' % real) if islink else ('另一座库，不会被选中' if looks else '空/不像库')
        rows.append({'kind': '库候选', 'path': ap_, 'sig': maxd(ap_) or '%d 项' % len(os.listdir(ap_)),
                     'state': state, '_real': real})
    _sig = maxd(resolved) if os.path.isdir(resolved) else ''
    if not _sig:
        _sig = ('%d 项' % len(os.listdir(resolved))) if os.path.isdir(resolved) else '—'
    rows.insert(1, {'kind': '库（工具正在用）', 'path': resolved, 'sig': _sig,
                    'state': '选中' if os.path.isdir(resolved) else '不存在'})

    # 红灯：持久盘那份明明在，却被选到别处（或选中的不像库）
    in_image = os.path.realpath(resolved).startswith('/runtime/')
    no_lib = not _looks_like_lib(resolved)
    stale_pick = no_lib or bool(wrong) or in_image or (os.path.isdir(persistent) and os.path.abspath(persistent) != resolved)

    if ns.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print('%-18s %-62s %-8s %s' % ('角色', '路径', '签名', '状态'))
        for r in rows:
            print('%-18s %-62s %-8s %s' % (r['kind'], r['path'], r['sig'], r['state']))
        print()
        if no_lib:
            print('⛔ 这里没有库：%s 不像一座库（没有台账/状态文件）。' % resolved)
            print('   镜像库已不再被认作库（会随重置回滚）。先落库：restore --from <包.zip>')
        elif in_image:
            print('⛔ 你在用镜像里的旧库（会随重置回滚）：%s' % resolved)
            print('   持久盘那份应在 %s；它不在时，先按权威包落库，别在镜像上记账。' % persistent)
        elif stale_pick:
            print('⛔ 跑错库了：持久盘那份在 %s，工具却选了 %s' % (persistent, resolved))
        else:
            print('✓ 选中的就是持久盘那份。影子入口只是别名，关掉它们不影响这个判定。')
        print('规则：源只有一个、且唯一可写；安装位是投影，只由装命令覆盖；')
        print('      影子入口要么删、要么认清它是别名——它指向谁，工具就用谁。')
    return 1 if stale_pick else 0


def cmd_provenance(argv):
    """卡出处状态：台账 D<n> / 流水 v<x.y> / 无记录。"""
    import argparse
    ap = argparse.ArgumentParser(prog='recall.py provenance')
    ap.add_argument('card', nargs='?', default='', help='看某张卡')
    ap.add_argument('--build', action='store_true', help='重扫两本账，重建状态表')
    ap.add_argument('--stats', action='store_true', help='看三档分布')
    ns = ap.parse_args(argv)
    doc = _axis_prov(ns.build)
    if ns.build:
        print('出处状态已重建：%s（台账 %d / 流水 %d / 无记录 %d）'
              % (os.path.relpath(AXIS_FILE, ROOT), doc['tally']['台账'], doc['tally']['流水'], doc['tally']['无记录']))
    if ns.stats or not ns.card:
        print('三档：台账追到 %d 张 / 流水追到 %d 张 / 无记录 %d 张（共 %d）'
              % (doc['tally']['台账'], doc['tally']['流水'], doc['tally']['无记录'], doc['cards_total']))
        print('  无记录不等于没价值：它是"这张卡什么时候进来的，账上没留下痕迹"。')
        print('  这里只如实标，不承诺补齐——补不出真出处，猜一个不如空着。')
        return 0
    c = ns.card[:-3] if ns.card.endswith('.md') else ns.card
    if c not in doc['cards']:
        print('⛔ 没这张卡：%s' % c)
        sys.exit(2)
    v = doc['cards'][c]
    print('%s：%s' % (c, v['status']))
    if v['all']:
        print('  全部出现：%s' % '、'.join(v['all']))
    return 0


def cmd_trace(argv):
    """经验卡 ↔ 缺陷台账 双向反查。

    只做提取：卡里写了 D 编号才认，没写的一律"未关联"，绝不猜。
    覆盖率低是现状（实测只有十几张卡写了），不是这套东西失败——
    如实标出来，才知道欠的是哪一段。
    """
    import argparse
    ap = argparse.ArgumentParser(prog='recall.py trace')
    ap.add_argument('key', nargs='?', default='', help='D161 或 卡名')
    ap.add_argument('--build', action='store_true', help='重扫所有卡，重建索引')
    ap.add_argument('--stats', action='store_true', help='看覆盖率与孤儿指针')
    ns = ap.parse_args(argv)

    doc = _axis_dxref(ns.build)
    if ns.build:
        print('索引已重建：%s（%d 张卡写了 D，%d 张未关联）'
              % (os.path.relpath(AXIS_FILE, ROOT), doc['cards_with_ref'], doc['unlinked']))

    if ns.stats or not ns.key:
        print('覆盖：%d/%d 张卡写了 D 编号，未关联 %d 张；台账最大 %s'
              % (doc['cards_with_ref'], doc['cards_total'], doc['unlinked'], doc['max_defect']))
        if doc['orphans']:
            print('⚠ 指向不存在缺陷的卡 %d 张：' % len(doc['orphans']))
            for k, v in doc['orphans'].items():
                print('   %s → %s' % (k, '、'.join(v)))
        cnt = {}
        for v in doc['cards'].values():
            for x in v:
                cnt[x] = cnt.get(x, 0) + 1
        top = sorted(cnt.items(), key=lambda kv: -kv[1])[:5]
        if top:
            print('被最多卡认领的缺陷：' + '、'.join('%s(%d 张)' % (k, v) for k, v in top))
        # v6.59 锚点许可证：探针/结构断言能不能上岗，看它有没有"失效样本"授权。
        # 这不是给人填的表，是从索引与台账两侧自动派生——填表会腐烂，派生件不会。
        try:
            midx = _midx_ok()
        except Exception:
            midx = {}
        # 只算真探针（含 3 根结构断言）：struct 是"错误结构词"标注，几乎每张卡都有，
        # 把它算成探针会把孤儿数虚增到 171（实测），反而看不出欠账在哪。
        unl = sorted(c for c, v in midx.items()
                     if c not in doc['cards'] and v.get('probe'))
        if unl:
            print('\n⚠ 孤儿探针 %d 张：有探针但没写清是哪条缺陷催生的' % len(unl))
            for c in unl[:8]:
                print('   ' + c)
            if len(unl) > 8:
                print('   …（其余 %d 张）' % (len(unl) - 8))
        else:
            print('\n孤儿探针 0 张：每根探针都能追到一条缺陷')
        ds = doc.get('defect_ids') or []
        claimed = set()
        for v in doc['cards'].values():
            claimed.update(v)
        bare = [d for d in ds[-30:] if d not in claimed]
        if bare:
            print('近 30 条缺陷里 %d 条没有卡认领：%s%s'
                  % (len(bare), '、'.join(bare[:10]), ' …' if len(bare) > 10 else ''))
            print('   （不是"缺陷没人管"，是"这一条的经验还没写成卡"）')
        print('\n查法：trace D161  → 哪些卡在治它；trace <卡名> → 这张卡治哪几个')
        return 0

    k = ns.key.strip()
    if re.fullmatch(r'D\d{1,3}', k, re.I):
        k = 'D' + str(int(k[1:]))
        hits = [c for c, v in doc['cards'].items() if k in v]
        if not hits:
            mx = doc['max_defect']
            if int(k[1:]) > int(mx[1:]):
                print('⛔ %s 超出台账范围（最大 %s）。要么编号写错，要么台账还没记这一条。' % (k, mx))
            else:
                print('%s 目前没有任何卡认领。' % k)
                print('   这不是"缺陷没人管"，是"这一条的经验还没写成卡"——如实反映，不猜。')
            return 0
        print('%s 被 %d 张卡认领：' % (k, len(hits)))
        for c in sorted(hits):
            print('  - %s' % c)
        return 0

    name = k[:-3] if k.endswith('.md') else k
    if name in doc['cards']:
        print('%s 治：%s' % (name, '、'.join(doc['cards'][name])))
        return 0
    if name in doc['orphans']:
        print('⛔ %s 写的编号指向不存在的缺陷：%s' % (name, '、'.join(doc['orphans'][name])))
        sys.exit(2)
    if not os.path.exists(os.path.join(ROOT, name + '.md')):
        print('⛔ 既不是缺陷编号，也没这张卡：%s' % k)
        sys.exit(2)
    print('%s 未关联任何缺陷（卡里没写 D 编号）。' % name)
    return 0


def cmd_evidence(argv):
    """证据档 E0–E2：区分"卡写了"与"卡被验过"。

    E0 已收录 / E1 有已批准的探针 / E2 最近有一次真实跑通。
    **E2=能用上，不等于管用；管不管用看 eval。** 这句话必须跟着档位一起出，
    否则下一轮就会有人把 E2 当成"这张卡的建议是对的"。
    档位只升不降（按方案定），但要把"最近一次执行"摆出来——
    通过过的卡后来跑挂了，档位不动，信号必须看得见。
    """
    import argparse
    ap = argparse.ArgumentParser(prog='recall.py evidence')
    ap.add_argument('--list', action='store_true', help='全库分布 + 按档列出')
    ap.add_argument('--card', default='', help='看单张卡的档位与依据')
    ns = ap.parse_args(argv)

    ax = _axis_get()
    cards = [n[:-3] for n in ax['md_names']]
    approved = set(ax['approved'])
    succ, last = ax['succ'], ax['last_run']

    def grade(c):
        pr = _axis_probes(c)
        ap_pr = [x for x in pr if _h8(x[0]) in approved]
        # E2 的底座是 E1：先得有一条"已批准的探针"，才谈得上"它跑通过"。
        # 实测有一张卡：台账里跑通过，但卡里的探针已经改过、不再在批准表里——
        # 那是"探针漂移"，旧通过不能顺着用（改了探针等于换了问题）。
        if ap_pr and c in succ:
            return 'E2', ap_pr, succ[c], last.get(c), ''
        if ap_pr:
            return 'E1', ap_pr, None, last.get(c), ''
        if c in succ:
            return 'E0', ap_pr, succ[c], last.get(c), 'drift'
        return 'E0', ap_pr, None, last.get(c), ''

    if ns.card:
        c = ns.card[:-3] if ns.card.endswith('.md') else ns.card
        if not os.path.exists(os.path.join(ROOT, c + '.md')):
            print('⛔ 没这张卡：%s' % c)
            sys.exit(2)
        g, ap_pr, s_ok, l_ok, note = grade(c)
        print('卡：%s' % c)
        print('  档位：%s' % g)
        if g == 'E2':
            print('  依据：%s 探针跑通%s' % (s_ok.get('ts'),
                  ('（%s ms）' % s_ok.get('ms')) if s_ok.get('ms') else ''))
        elif g == 'E1':
            print('  依据：卡内有 %d 条已批准探针，但台账里没有跑通的记录 → 只是"可验"，还没被验过' % len(ap_pr))
        else:
            pr_all = _axis_probes(c)
            if pr_all:
                print('  依据：卡内有 %d 条探针，但没有一条在批准表里 → 未验证'
                      % len(pr_all))
                print('        （新卡自带的命令不能自己给自己发通行证，得人批一次才作数）')
            else:
                print('  依据：卡里没有探针 → 未验证（这张卡只是"写着"，还没有可跑的证据）')
        print('  探针：%d 条，其中已批准 %d 条' % (len(_axis_probes(c)), len(ap_pr)))
        if note == 'drift':
            print('  ⚠ 探针漂移：台账里 %s 跑通过，但卡里当前的探针不在批准表里（被改过？）。'
                  % (s_ok.get('ts') if s_ok else '?')
                  + '改了探针等于换了问题，旧通过不算数——要重新批准并重跑。')
        if l_ok:
            why = l_ok.get('why') or ''
            if l_ok.get('ok'):
                tag = '通过'
            elif '平台不适用' in why or '环境' in why:
                # 环境/平台不适用不是"退步"，只是这台机器上验不了。读成失败会逼人去修不存在的问题。
                tag = '不适用（没判定，不算失败）'
            else:
                tag = '失败'
            print('  最近一次执行：%s %s%s' % (l_ok.get('ts'), tag, ('（%s）' % why) if why else ''))
        print('  E2=能用上，不等于管用；管不管用看 eval。')
        return 0

    import collections
    tally = collections.Counter()
    by = {'E2': [], 'E1': [], 'E0': []}
    drift = []
    for c in cards:
        r = grade(c)
        g = r[0]
        tally[g] += 1
        by[g].append(c)
        if r[4] == 'drift':
            drift.append(c)
    print('证据档分布（全库 %d 张卡）：E2 %d / E1 %d / E0 %d'
          % (len(cards), tally['E2'], tally['E1'], tally['E0']))
    print('  E2=能用上（探针真跑通过），不等于管用；管不管用看 eval。')
    if drift:
        print('  ⚠ 另有 %d 张"探针漂移"：跑通过，但探针改过、不在批准表里（%s）——旧通过不算数。'
              % (len(drift), '、'.join(drift[:3])))
    for g in ('E2', 'E1', 'E0'):
        if by[g]:
            print('\n%s（%d）：' % ({'E2': '最近跑通过', 'E1': '有已批准探针但没跑通过', 'E0': '未验证'}[g], len(by[g])))
            for c in by[g][:12]:
                print('  - %s' % c)
            if len(by[g]) > 12:
                print('  ……另 %d 张' % (len(by[g]) - 12))
    return 0


def cmd_eval(argv):
    """使用评价：给"这条经验到底帮没帮上"留下第一条真实数据。

    三条硬规矩：
    ① 只追加，不回写原行（原行是历史，评价是新事实）；
    ② 说"没帮上"必须写清"在什么情况下失效"——那不是抱怨，是边界；
    ③ 记"谁评的、是不是自评"，自评与他评分列，不混着算。
    """
    import argparse
    ap = argparse.ArgumentParser(prog='recall.py eval')
    ap.add_argument('--pending', action='store_true', help='列出还没评价的')
    ap.add_argument('--q', default='', help='被测的原始问题（可写片段）')
    ap.add_argument('--ref-id', type=int, default=0, help='使用记录的行号（--pending 里 #后面的数）')
    ap.add_argument('--result', default='', help='帮上 / 没帮上 / 不确定')
    ap.add_argument('--boundary', default='', help='补出来的适用边界（"没帮上"必填）')
    ap.add_argument('--boundary-probe', default='', help='卡里那条探针（原文片段），用来证明这条边界能被复现')
    ap.add_argument('--boundary-card', default='', help='探针挂在哪张卡上（默认取这次命中的卡）')
    ap.add_argument('--boundary-unverifiable', default='', help='为什么这条边界没法写成探针（给了才算交代）')
    ap.add_argument('--stats', action='store_true', help='按卡汇总')
    ns = ap.parse_args(argv)
    rows = _eval_merge(_use_ledger())

    if ns.pending:
        left = [r for r in rows if (r.get('_result') or 'pending') == 'pending']
        print('待评价 %d 条（共 %d 条使用记录）' % (len(left), len(rows)))
        for r in left[:25]:
            print('  #%-3d [%s] %s' % (r['_ln'], r.get('ts'), (r.get('q') or '')[:64]))
        if len(left) > 25:
            print('  ……另 %d 条。' % (len(left) - 25))
        print('\n评价：#N 为准：eval --ref-id <N> --result 帮上|没帮上|不确定 [--boundary "<边界>"]')
        print('      说"没帮上"必须写边界（在什么情况下失效），否则不予落盘。')
        return 0

    if ns.stats:
        import collections
        cnt = collections.Counter((r.get('_result') or 'pending') for r in rows)
        cards = {}
        for r in rows:
            c = r.get('top') or '（未命中任何卡）'
            cards.setdefault(c, collections.Counter())[(r.get('_result') or 'pending')] += 1
        print('使用记录 %d 条：帮上 %d / 没帮上 %d / 不确定 %d / 待评价 %d'
              % (len(rows), cnt['帮上'], cnt['没帮上'], cnt['不确定'], cnt['pending']))
        # 同一条使用记录可能被评多次（改主意、他评补一条）。统计只认最新那次，
        # 否则同一件事会被数成两条——"账上的数"比"账上的事"多，是最容易骗人的错数据。
        latest = {}
        for e in _use_ledger():
            if e.get('kind') == 'eval':
                latest[e.get('ref_id')] = e      # 后写的覆盖先写的 = 取最新
        neg = [e for e in latest.values() if e.get('result') == '没帮上']
        ver = [e for e in neg if e.get('boundary_verified')]
        unver = [e for e in neg if e.get('boundary_unverifiable')]
        def _bs(e):
            st = e.get('boundary_state') or ''
            if st:
                return st
            if e.get('boundary_unverifiable'):
                return 'unverifiable'
            return ''
        if neg:
            # 探针跑没跑通，读「探针执行台账」——真账在那边，eval 不自吹
            last = {}
            pl = os.path.join(ROOT, '探针执行台账.jsonl')
            if os.path.exists(pl):
                for l in open(pl, encoding='utf-8'):
                    l = l.strip()
                    if not l:
                        continue
                    try:
                        r = json.loads(l)
                    except Exception:
                        continue
                    if r.get('card'):
                        last[r['card']] = r          # 后写的覆盖先写的 = 最近一次
            import collections as _c
            k = _c.Counter(_bs(e) for e in neg)
            print('  "没帮上"共 %d 条：边界已挂探针 %d / 自述写不成探针 %d'
                  % (len(neg), k['probe'], k['unverifiable']))
            if k['probe']:
                pc = [e for e in neg if _bs(e) == 'probe']
                ok = fail = never = gone = 0
                for e in pc:
                    card = ((e.get('boundary_probe') or {}).get('card')) or ''
                    if card and not os.path.exists(os.path.join(ROOT, card + '.md')):
                        gone += 1            # 卡被 retire 了：探针跟着卡走，边界也失去载体
                        continue
                    r = last.get(card)
                    if not r:
                        never += 1
                    elif r.get('ok'):
                        ok += 1
                    else:
                        fail += 1
                print('    其中：最近一次跑通 %d / 最近一次失败 %d / 从没跑过 %d / 卡已退役 %d'
                      % (ok, fail, never, gone))
                if never:
                    print('    （从没跑过 = 这条边界只是"声称可验"，还没被验证过。那不是错，是欠账——gate 会记账）')
                if gone:
                    print('    （卡已退役 = 探针跟着卡一起注销了。这条边界的记录还在，但它已经不再保护任何东西）')
        dr = getattr(_eval_merge, 'drifted', [])
        if dr:
            print('  ⚠ 行号漂移 %d 条：台账被重排过，这些评价已失联（问句指纹对不上）。' % len(dr))
            for d in dr[:3]:
                print('     #%s 记的是「%s」' % (d.get('ref_id'), (d.get('q') or '')[:40]))
        self_n = sum(1 for r in rows if r.get('_self'))
        if self_n:
            print('  其中自评 %d 条（评价人就是写卡的人，天然偏向"我写的有用"，看到这个数就别当真）' % self_n)
        print('\n按卡：')
        for c, k in sorted(cards.items(), key=lambda x: -sum(x[1].values())):
            print('  %-42s 帮上 %d / 没帮上 %d / 不确定 %d / 待评 %d'
                  % (c[:42], k['帮上'], k['没帮上'], k['不确定'], k['pending']))
        try:
            n_cards = _axis_get()['n_cards']
            used = len([c for c in cards if c != '（未命中任何卡）'])
            print('\n全库 %d 张卡，被真实召回过的只有 %d 张。' % (n_cards, used))
            if used and n_cards:
                print('  剩下 %d 张从没被用过——它们的"有没有用"现在无从回答，这不怪卡，怪没用上。'
                      % (n_cards - used))
        except Exception:
            pass
        return 0

    if not ns.result:
        print('用法：eval --pending | eval --stats | eval --ref-id <N> --result 帮上|没帮上|不确定 [--boundary "<边界>"]')
        sys.exit(2)
    if ns.result not in EVAL_WORDS:
        print('⛔ --result 只接受三个词：帮上 / 没帮上 / 不确定。你写的是「%s」。' % ns.result)
        print('   写不出这三个词，说明这轮还没结论——用「不确定」，不要硬编一个。')
        sys.exit(2)
    if ns.result == '没帮上' and not ns.boundary.strip():
        print('⛔ "没帮上"必须写清边界：在什么情况下它失效了？')
        print('   这不是抱怨，是这张卡下一次能不能用的判据。写不出来就先用「不确定」。')
        sys.exit(2)
    # 空话不许混过去：边界要么能被一条命令显形，要么明说"没法用命令验"。
    # 为什么不用"条件词/长度"去猜语义——那种判据会误杀真边界，也会放过写得长的空话。
    # 只认两种交代方式，二者必居其一。
    if ns.result == '没帮上' and not (ns.boundary_probe.strip() or ns.boundary_unverifiable.strip()):
        print('⛔ 这条边界怎么复现？二选一，不许含糊：')
        print('   --boundary-probe "<卡里那条探针>"      边界要能复现，就得先在卡上写成探针，闸会跑它')
        print('   --boundary-unverifiable "<为什么>"      这条边界写不成探针，说清为什么')
        print('   只写"有时管用"这种话，三个月后没人能拿它做判断——那就是死数据。')
        sys.exit(2)

    bprobe = None
    bstate = ''
    if ns.boundary_probe.strip():
        card = (ns.boundary_card or '').strip()
        if not card:
            # 默认跟这次命中的卡走；top 为空说明这次没召回任何卡，必须点名
            for r in rows:
                if r['_ln'] == ns.ref_id and r.get('top'):
                    card = r['top']
                    break
        if not card:
            print('⛔ 这次使用没有命中任何卡，得用 --boundary-card 点名探针挂在哪张卡上。')
            sys.exit(2)
        _ax = _axis_get()
        if card not in _ax['card_names']:
            print('⛔ 卡不存在：%s' % card)
            sys.exit(2)
        plines = [p['line'] for p in _ax['probes'].get(card, [])]
        prov = ' '.join(ns.boundary_probe.split())
        if len(prov) < 12:
            print('⛔ 这段太短，分不清是哪条探针。写长一点，能唯一定位到卡里那一行。')
            sys.exit(2)
        hits = [l for l in plines if prov in ' '.join(l.split())]
        if len(hits) > 1:
            # 一张卡可以挂多条探针（实测最多 3 条）。片段落到两条上就是歧义，
            # 取第一条等于替人挑了一条——那正是"看起来挂上了、其实挂错"的假成功。
            print('⛔ 这段同时命中卡「%s」里的 %d 条探针，分不清是哪条：' % (card, len(hits)))
            for h in hits:
                print('    %s' % h[:100])
            print('   写长一点，能唯一定位到其中一条。')
            sys.exit(2)
        hit = hits[0] if hits else None
        if not hit:
            print('⛔ 卡「%s」里没有这段探针。' % card)
            print('   边界要能被复现，就得先在卡上写成一条探针（写法照 模板_经验卡.md 的 ## 判据 段）。')
            print('   卡里现有 %d 条探针；确实写不成探针的，用 --boundary-unverifiable 说清为什么。' % len(plines))
            sys.exit(2)
        # 只核对、不执行：跑探针是闸的事，账在「探针执行台账.jsonl」里，eval 不去冒充那个账
        bprobe = {'card': card, 'line': hit[:200]}
        bstate = 'probe'
        print('  探针已挂到卡「%s」：%s' % (card, hit[:90]))
        print('  这条探针跑没跑通，看「探针执行台账」和 gate，不在这里现场跑——记账不该顺手去执行东西。')

    if ns.ref_id:
        cand = [r for r in rows if r['_ln'] == ns.ref_id]
    else:
        cand = [r for r in rows if ns.q and ns.q in (r.get('q') or '')]
    if not cand:
        print('⛔ 找不到这条使用记录。')
        print('   先跑 eval --pending 看原文与行号；已有别的评价的条目不会出现在待评价里。')
        sys.exit(2)
    if len(cand) > 1:
        print('⛔ 命中 %d 条，分不清评的是哪一次。用 --ref-id 指定行号：' % len(cand))
        for r in cand[:8]:
            print('    --ref-id %-4d [%s] %s' % (r['_ln'], r.get('ts'), (r.get('q') or '')[:48]))
        sys.exit(2)
    tgt = cand[0]
    rec = {'ts': datetime.datetime.now().strftime('%Y-%m-%d'),
           'kind': 'eval', 'ref_id': tgt['_ln'], 'ref_ts': tgt.get('ts'),
           'q': tgt.get('q'), 'ref_qh': _h8(tgt.get('q') or ''), 'top': tgt.get('top'), 'result': ns.result,
           'boundary': ns.boundary.strip(),
           'boundary_probe': bprobe,
           'boundary_unverifiable': ns.boundary_unverifiable.strip(),
           'boundary_state': bstate or ('unverifiable' if ns.boundary_unverifiable.strip() else ''),
           'by': _hst(),
           'self': not bool(os.environ.get('EXP_EVAL_OTHER'))}
    with _wlock('使用评价'):
        with open(os.path.join(ROOT, '使用台账.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    print('已记评价 #%d：%s → %s' % (tgt['_ln'], (tgt.get('q') or '')[:50], ns.result))
    if ns.result == '没帮上':
        print('  边界已留档：%s' % ns.boundary.strip()[:80])
    print('  这一条算%s；要记成他评，评价时设 EXP_EVAL_OTHER=1 再跑一次同一条。'
          % ('自评' if rec['self'] else '他评'))
    return 0


def cmd_retire(argv):
    """声明式删除：退役一张（或几张）卡，把该做的四件事一次做完。

    为什么需要它：预期基线里记着每一张卡，直接 rm 会让 deploy_check 报红，
    而那个红长得跟"被平台覆盖丢件"一模一样——人分不清"我有意删的"和"库被人动了"。
    这条命令就是把"有意"变成可证明的：**把卡全文写进 `注销卡.md` 再删文件**（删正文不删历史），
    然后重建索引、只对**这几张**更新基线；若同时还缺别的件，一律不认、留给裁决。
    """
    import argparse
    ap = argparse.ArgumentParser(prog='recall.py retire')
    ap.add_argument('--card', action='append', default=[], help='卡名（可带/不带 .md），可重复')
    ap.add_argument('--why', default='', help='为什么退役（必填）')
    ap.add_argument('--already-deleted', action='store_true', help='文件已被手删，这里只补声明')
    ap.add_argument('--dry', action='store_true')
    ns = ap.parse_args(argv)
    if not ns.card:
        print('用法：retire --card <卡名> [--card …] --why "<为什么退役>" [--already-deleted] [--dry]')
        sys.exit(2)
    if not ns.why.strip():
        print('⛔ --why 必填：删卡要能说出为什么。否则三个月后没人分得清是删错了还是有意的。')
        sys.exit(2)

    declared = []
    for c in ns.card:
        name = c if c.endswith('.md') else c + '.md'
        if os.path.exists(os.path.join(ROOT, name)):
            if ns.already_deleted:
                print('⛔ %s 文件还在，--already-deleted 只用于"已经手删了"的卡。' % name)
                print('   这张还在：去掉 --already-deleted 走正常退役；或确认删错了就什么都别做。')
                sys.exit(2)
            declared.append(name)
        elif ns.already_deleted:
            declared.append(name)
            print('  · 已不存在，按"已手删"处理：%s' % name)
        else:
            print('⛔ 找不到卡：%s（如果已经手删了，加 --already-deleted 补声明）' % name)
            sys.exit(2)

    grave = os.path.join(ROOT, '注销卡.md')
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    for name in declared:
        src = os.path.join(ROOT, name)
        if not os.path.exists(src):
            continue
        body = open(src, encoding='utf-8', errors='ignore').read()
        head = ('\n## %s 退役《%s》\n\n- 理由：%s\n- 原文件：%s\n- 由 retire 归档，正文原样保留\n\n'
                % (stamp, name[:-3], ns.why.strip(), name))
        if ns.dry:
            print('  待退役：%s（%d 字节）' % (name, len(body.encode('utf-8'))))
            continue
        need_nl = os.path.exists(grave) and os.path.getsize(grave) > 0 and not open(grave,'rb').read()[-1:] == b'\n'
        with open(grave, 'a', encoding='utf-8') as f:
            if need_nl:
                f.write('\n')
            f.write(head + body.rstrip('\n') + '\n')
        os.remove(src)
        print('  已退役：%s（♪ 全文留档在 注销卡.md）' % name)
    if ns.dry:
        print('DRY：未动文件。去掉 --dry 才真删。')
        return 0

    # 重建索引（删了卡不重建，match 还会召回它）
    try:
        import io as _i, contextlib as _c
        buf = _i.StringIO()
        with _c.redirect_stdout(buf):
            cmd_build_index()
        print('  索引已重建（%s）' % ('有输出，见下' if buf.getvalue() else '静默'))
    except SystemExit:
        pass
    except Exception as e:
        print('  ⚠ 索引未重建：%s（请手跑 build_index）' % str(e)[:60])

    # 只认"我声明退役的这几件"，别的缺件一律不认
    files_base, _kind = _baseline_load()
    cur = _lib_scan()
    missing = [k for k in files_base if k not in cur]
    unexpected = [k for k in missing if k not in set(declared)]
    if unexpected:
        print('⚠ 除了这几张卡，还有 %d 件缺（不是这次声明要退役的）：' % len(unexpected))
        for k in unexpected[:15]:
            print('    ✗ ' + k)
        print('   → 基线未更新（不给别的东西顺带洗白）。处置：restore --from <包> 或 guard accept')
        try:
            pend = {'ts': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                    'missing': unexpected, 'shrunk': [], 'added': []}
            open(_pending_path(), 'w', encoding='utf-8').write(json.dumps(pend, ensure_ascii=False, indent=1))
        except Exception:
            pass
        sys.exit(7)
    _baseline_save(cur)
    if os.path.exists(_pending_path()):
        os.remove(_pending_path())
    try:
        _hist_append({'ts': datetime.datetime.now().isoformat(timespec='seconds'),
                      'host': _hst(), 'action': 'retire', 'section': '',
                      'note': '退役 %d 张卡（%s）；理由：%s' % (len(declared), '、'.join(declared)[:120], ns.why.strip()[:120])})
    except Exception as e:
        print('  （变更史留痕失败：%s）' % str(e)[:60])
    print('RETIRE DONE：%d 张已退役、正文留档、索引重建、基线已同步' % len(declared))
    print('           以后部署检查不会再为这几张报红；卡里那条经验仍在 注销卡.md 里可查可追。')
    return 0


def cmd_restore(argv):
    """重置后一条命令：从包补齐缺件 → 重建 /runtime 入口 → 全量核对。

    用法：restore --from <包.zip> [--root <库根>]
    不覆盖比包内更新的账（台账/状态层/变更史/流水四件按"本地更大则保留"处理），
    只补"包有库无"的件——这才是被重置抹掉的那部分。
    """
    import zipfile, argparse
    ap = argparse.ArgumentParser(prog='recall.py restore')
    ap.add_argument('--from', dest='zpath', required=True, help='权威包')
    ap.add_argument('--root', default=ROOT)
    ap.add_argument('--prefix', default='experience/')
    # 默认不再重建任何兼容入口：/runtime/experience 这个旧入口已按"单入口"原则删除。
    # 留着默认值，等于每次 restore 都把一个"看起来也能用"的僵尸库复活。
    ap.add_argument('--relink', default='', help='可选：要重建的兼容入口（软链）；不填则不建')
    # Q09：取单件不必把整包铺开。以前为了拿两个文件，要把 344 条目逐个比对一遍再落盘；
    # 包是同一个包，但"铺开 344 件"这个动作会顺带改掉一批本来不该动的件。
    ap.add_argument('--only', default='', help='只取这几件（逗号分隔，相对 experience/ 的路径）')
    ap.add_argument('--force', action='store_true', help='配合 --only：本地更大也照包覆盖')
    ns = ap.parse_args(argv)

    root = os.path.abspath(ns.root)
    if not os.path.isfile(ns.zpath):
        print('⛔ 找不到权威包：%s' % ns.zpath)
        print('   先取包（云盘权威版）再落库；不要在不存在的包上重试。')
        sys.exit(2)
    with zipfile.ZipFile(ns.zpath) as z:
        pkg = {}
        for i in z.infolist():
            if i.is_dir() or not i.filename.startswith(ns.prefix):
                continue
            pkg[i.filename[len(ns.prefix):]] = i
        def _put(rel, info, force=False):
            """落一件：本地等大或更大就保留（整包与 --only 共用这一条规则）。

            以前是两套：整包用 `>=`、--only 用 `>`——同一条"本地更新则保留"，
            在同大小文件上给出相反结论，--only 会静默覆盖本地改动。
            """
            dst = os.path.join(root, rel)
            if os.path.exists(dst) and not force and os.path.getsize(dst) >= info.file_size:
                return False
            os.makedirs(os.path.dirname(dst) or root, exist_ok=True)
            open(dst, 'wb').write(z.read(info))
            return True

        only = [x.strip() for x in (ns.only or '').split(',') if x.strip()]
        if only:
            miss = [x for x in only if x not in pkg]
            if miss:
                print('⛔ 包里没有这些件：' + '、'.join(miss))
                print('   可用的写法：相对 experience/ 的路径，区分大小写。先 `unzip -l <包>` 看一眼。')
                sys.exit(2)
            got, kept = [], []
            for rel in only:
                if _put(rel, pkg[rel], force=ns.force):
                    got.append(rel)
                else:
                    kept.append(rel)
            print('RESTORE --only：取 %d 件%s（%s）' % (len(got), ('，本地等大/更大保留 %d 件' % len(kept)) if kept else '', root))
            for x in got:
                print('  + ' + x)
            for x in kept:
                print('  = ' + x + '（本地等大/更大，未覆盖；要按包覆盖加 --force）')
            print('  提示：单件补齐不会重算索引——要参与检索/匹配，接着跑 `build_index`。')
            return 0
        added = 0
        skipped_newer = []
        for rel, info in pkg.items():
            if _put(rel, info):
                added += 1
            else:
                skipped_newer.append(rel)
    print('RESTORE：补齐 %d 件 / 本地更新保留 %d 件（%s）' % (added, len(skipped_newer), root))
    if skipped_newer:
        print('  保留：' + '、'.join(sorted(skipped_newer)[:8]) + ('…' if len(skipped_newer) > 8 else ''))
    if ns.relink:
        if os.path.islink(ns.relink):
            os.unlink(ns.relink)                      # 只删软链本身，不碰链指向的东西
            os.symlink(root, ns.relink)
            print('  入口已重建（旧的软链已换掉）：%s → %s' % (ns.relink, root))
        elif os.path.exists(ns.relink):
            # 那是个真目录，里面可能有数据。绝不能为了"重建入口"把它 rmtree 掉——
            # 实测 /runtime/experience 就是真目录，旧代码会在这一步直接删库（且 ignore_errors 把失败吞了）。
            print('⛔ 那个位置是个真目录，不是软链：%s' % ns.relink)
            print('   拒绝删除。要重建入口，先把那个目录挪走或改名，再跑一次。')
            sys.exit(5)
        else:
            os.symlink(root, ns.relink)
            print('  入口已建：%s → %s' % (ns.relink, root))
    cur = _lib_scan(root)
    miss = [k for k in pkg if k not in cur]
    if miss:
        print('⛔ 补完仍缺 %d 件：' % len(miss))
        for k in miss[:15]:
            print('    ✗ ' + k)
        sys.exit(5)
    _baseline_save(cur)
    print('RESTORE PASS：库 %d 件齐全，基线已更新' % len(cur))
    return 0


def cmd_guard(args):
    """动库守卫：动之前拍一次，动完比一次。丢了能发现，也能捞回来。

    用法：
      guard snap [--tag <标签>]        拍快照（指纹 + 三本账/状态层正文副本）
      guard check [--tag <标签>]       与最近一次（或指定标签）快照比，有丢账 exit 1
      guard diff <zip>                 只读：包里三本账比本机旧就 exit 1（不许整包解到库根）
      guard list                       列出已拍的快照
      guard restore --tag <标签>        从快照恢复三本账与状态层；恢复前把当前件存成 .before.<ts>
    """
    if not args:
        print(__doc_of_guard())
        return
    sub = args[0]

    if sub == 'snap':
        tag = ''
        if '--tag' in args and args.index('--tag') + 1 < len(args):
            tag = args[args.index('--tag') + 1]
        d, fp = _guard_snap(tag)
        print('已拍快照：%s' % d)
        for name, _p, _a in _guard_targets():
            f = fp.get(name, {})
            print('  %-8s %5d 行 / %8d 字节 / %s' % (name, f.get('lines', 0), f.get('bytes', 0),
                                                      f.get('sha8') or '—'))
        print('  %-8s %5d 张' % ('卡片', fp.get('_cards', {}).get('count', -1)))
        print('  %-8s %5d 节' % ('状态层', len(fp.get('_sections') or {})))
        return

    if sub == 'commit':
        return cmd_guard_commit(args[1:])
    if sub == 'pending':
        return cmd_guard_pending(args[1:])
    if sub == 'accept':
        return cmd_guard_accept(args[1:])
    if sub == 'check':
        tag = ''
        if '--tag' in args and args.index('--tag') + 1 < len(args):
            tag = args[args.index('--tag') + 1]
        snaps = _guard_list()
        if not snaps:
            print('⛔ 还没拍过快照：先跑 guard snap 再动库')
            sys.exit(2)
        pick = None
        for n, m in reversed(snaps):
            if not tag or m.get('tag') == tag:
                pick = (n, m)
                break
        if not pick:
            print('⛔ 没有 tag 为「%s」的快照' % tag)
            sys.exit(2)
        base = pick[1].get('files') or {}
        cur = _guard_fp()
        bad, chg = _guard_diff_fp(base, cur)
        print('对照快照：%s（%s，%s）' % (pick[0], pick[1].get('ts'), pick[1].get('host')))
        if chg:
            print('\n正常改动：')
            for x in chg:
                print('  · ' + x)
        if bad:
            print('\n⛔ 发现丢账 / 覆盖迹象：')
            for x in bad:
                print('  ✗ ' + x)
            print('\n处置：先看是不是整包解到了库根。能捞回来就 guard restore --tag %s' % pick[1].get('name'))
            print('      不许直接复制旧文件回来盖——会抹掉别人这几分钟里新记的账。')
            sys.exit(1)
        print('\n✓ 没有丢账迹象（%d 项追加、%d 项改动）' % (len(chg), 0))
        return

    if sub == 'list':
        snaps = _guard_list()
        if not snaps:
            print('还没拍过快照')
            return
        for n, m in snaps:
            fp = m.get('files') or {}
            print('%-26s %s  %-8s 台账 %4d 行 / 流水 %4d 行 / 卡 %d 张'
                  % (n, m.get('ts', ''), m.get('host', ''),
                     fp.get('缺陷台账', {}).get('lines', 0),
                     fp.get('发版流水', {}).get('lines', 0),
                     fp.get('_cards', {}).get('count', -1)))
        return

    if sub == 'diff':
        import zipfile
        if len(args) < 2:
            print('用法：guard diff <zip>   —— 只读，包里台账比本机旧就 exit 1')
            sys.exit(2)
        zp = os.path.abspath(args[1])
        if not os.path.exists(zp):
            print('包不存在：' + zp); sys.exit(1)
        old = []
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            for nm, p, app in _guard_targets():
                if not app:
                    continue
                cands = [x for x in names if os.path.basename(x) == os.path.basename(p)]
                if not cands:
                    continue
                b = z.read(cands[0])
                nl = b.count(b'\n') + (0 if (not b or b.endswith(b'\n')) else 1)
                cur = 0
                if os.path.exists(p):
                    cb = open(p, 'rb').read()
                    cur = cb.count(b'\n') + (0 if (not cb or cb.endswith(b'\n')) else 1)
                if nl < cur:
                    old.append((nm, nl, cur))
                else:
                    print('  %-8s 包 %d 行 / 本机 %d 行（包不比本机少行）' % (nm, nl, cur))
        if old:
            print('\n⛔ 这个包是旧账，不许整包解到库根：')
            for nm, nl, cur in old:
                print('  ✗ %-8s 包 %d 行 < 本机 %d 行（会丢 %d 行）' % (nm, nl, cur, cur - nl))
            print('\n改用：recall.py sync diff %s → sync merge %s --apply' % (zp, zp))
            sys.exit(1)
        print('\n✓ 包里这几件都不比本机少行 —— 只比了行数，没比内容和版本')
        print('  这只排除「用旧账换新账」，不等于整包解安全：整包解是覆盖式写入，')
        print('  绕锁、不发号、不留痕，会吞掉这台机器刚记的账。要放件仍走 sync merge。')
        return

    if sub == 'restore':
        tag = ''
        for i, x in enumerate(args):
            if x == '--tag' and i + 1 < len(args):
                tag = args[i + 1]
        if not tag:
            print('用法：guard restore --tag <标签或快照名>   —— 不给就不知道该恢复哪一版')
            sys.exit(2)
        snaps = _guard_list()
        pick = None
        for n, m in reversed(snaps):
            if n == tag or m.get('tag') == tag:
                pick = (n, m)
                break
        if not pick:
            print('⛔ 没有这个快照：%s' % tag); sys.exit(2)
        d = os.path.join(_guard_dir(), pick[0])
        ts = time.strftime('%Y%m%d-%H%M%S')
        done = []
        with _wlock('恢复'):
            for nm, p, _app in _guard_targets():
                fn = (pick[1].get('copies') or {}).get(nm)
                if not fn:
                    continue
                src = os.path.join(d, fn)
                if not os.path.exists(src):
                    continue
                if os.path.exists(p):
                    try:
                        shutil.copy2(p, p + '.before.' + ts)
                    except Exception:
                        pass
                shutil.copy2(src, p)
                done.append(nm)
        print('已从 %s 恢复 %d 件（原样先存了 .before.%s）：%s'
              % (pick[0], len(done), ts, '、'.join(done) if done else '无'))
        print('⚠ 恢复是整件覆盖：如果这几分钟里别人记了新账，会一起被换掉。')
        print('  正确做法是拿快照正文里的缺失段落 append 回去，而不是整件盖。')
        return

    print('未知 guard 子命令：%s' % sub)
    print(__doc_of_guard())


def __doc_of_guard():
    return ('守卫命令：\n'
            '  guard snap [--tag <标签>]     拍快照（指纹 + 三本账/状态层正文副本）\n'
            '  guard check [--tag <标签>]    与快照比，有丢账 exit 1\n'
            '  guard diff <zip>              只读：包里账更旧就 exit 1\n'
            '  guard list                    列出快照\n'
            '  guard restore --tag <标签>     从快照恢复（整件覆盖，慎用）\n')


def cmd_claim(args):
    """给卡定归属：claim <owner> <卡1,卡2,...>。写入 frontmatter 的 owner 字段，带锁。"""
    if len(args) < 2:
        print('用法：claim <owner> <卡名1,卡名2,...>（卡名可不带 .md）'); return
    owner = args[0].strip()
    stems = [x.strip()[:-3] if x.strip().endswith('.md') else x.strip() for x in args[1].split(',') if x.strip()]
    done, miss, skip = [], [], []
    with _wlock('定归属'):
        for st in stems:
            f = _card_path(st)
            if not os.path.exists(f): miss.append(st); continue
            t = open(f, encoding='utf-8').read()
            if re.search(r'^\s*owner:', t, re.M): skip.append(st); continue
            m = re.search(r'^(\s*created_at:.*)$', t, re.M)
            if m:
                t = t[:m.end()] + '\nowner: ' + owner + t[m.end():]
            else:
                m2 = re.search(r'^(\s*description:.*)$', t, re.M)
                t = (t[:m2.end()] + '\nowner: ' + owner + t[m2.end():]) if m2 else ('owner: ' + owner + '\n' + t)
            tmp = f + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh: fh.write(t)
            os.replace(tmp, f)
            done.append(st)
    print('已定归属 ' + str(len(done)) + ' 张 → ' + owner + ('；已存在跳过 ' + str(len(skip)) if skip else '') + ('；未找到 ' + '、'.join(miss[:4]) if miss else ''))
    if done: print('提示：跑 build_index 让索引认到 owner。')


# ===== 多机同步（同一份库在两台机器上各写一份，靠这个对齐）=====
# 为什么不用 git：这库跑在两台 Windows + 一台 Linux 上，git 会牵出凭证 / CRLF / 中文文件名 /
# 镜像仓分叉一串已知坑（库里已有三张卡记着）。这里只做一件事：
# 把"哪一件在这一侧被改过"算清楚，然后只写"安全的那几件"，冲突一律列出交人，不选边。
SYNC_EX_FILES = {'_index.md', 'INDEX.md', '匹配索引.json', 'INDEX_MATCH.json',
                 '时效台账.json', '_时效台账.json', '探针执行台账.jsonl', '.sync_baseline.json',
                 # 派生件：build_index 一跑内容就变，两边必然不同，同步它只会天天报冲突
                 '时效台账.md',
                 # 治理件：通行证是命令哈希，换一份可能让探针失效或越权；只随整包发版走，不跨机传
                 '部署清单.json', '探针批准.json'}
SYNC_JSONL = {'状态变更史.jsonl', '使用台账.jsonl'}   # 只增不改的日志：行级并集，不按整件比
SYNC_BASE = '.sync_baseline.json'


def _sha16_file(p):
    import hashlib
    try:
        return hashlib.sha256(open(p, 'rb').read()).hexdigest()[:16]
    except Exception:
        return None


def _sync_scope():
    """参与同步的文件：所有 .md（含卡、账、快照库）+ 只追加的 .jsonl。
    派生件不进（索引 / 匹配索引 / 时效台账 / 探针执行台账），治理件不进（批准表 / 部署清单），
    备份件不进。"""
    out = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if not d.startswith('.card_backup')
                  and d not in ('.sessions', '__pycache__', '.guard')]
        rel_d = os.path.relpath(dp, ROOT).replace('\\', '/')
        for fn in fns:
            rel = fn if rel_d == '.' else rel_d + '/' + fn
            if fn in SYNC_EX_FILES or '.before' in fn or fn.endswith('.bak') or fn.endswith('.tmp'):
                continue
            if fn.endswith('.md') or fn.endswith('.jsonl'):
                out.append(rel)
    return sorted(set(out))


def _jsonl_union(local_text, pkg_text):
    """只增不改的日志按行并集：两边记录都是真的，一条都不该丢。
    按整行去重；若每行都带 ts 字段则按 ts 稳定排序，否则本地在前、包新增在后。
    返回 (合并文本, 是否与本地相同)。"""
    ll = [x for x in (local_text or '').split('\n') if x.strip()]
    pl = [x for x in (pkg_text or '').split('\n') if x.strip()]
    seen = set(ll)
    extra = [x for x in pl if x not in seen]
    merged = ll + extra
    ts_all = merged and all(re.search(r'"ts"\s*:\s*"([^"]+)"', x) for x in merged)
    if ts_all:
        merged = [x for _, x in sorted(
            enumerate(merged), key=lambda t: (re.search(r'"ts"\s*:\s*"([^"]+)"', t[1]).group(1), t[0]))]
    txt = '\n'.join(merged) + ('\n' if merged else '')
    return txt, txt == (local_text or '')


def _sync_plan(man, base, cur, has_base, side=''):
    """返回 [(rel, 动作, 说明)]，动作 ∈ 同 / 写 / 留 / 冲突。

    三方判据：base = 上次同步后的哈希，loc = 本机现在的哈希，pkg = 包里那份的哈希。
      pkg==loc            → 同
      loc 不存在           → 写
      pkg==base           → 本机改过、包没动 → 留
      loc==base           → 本机没动、包改过 → 写
      其余（含无基线）      → 冲突（除非人显式选边）
    side 只由人给：'theirs' 包赢、'ours' 本机赢。工具自己从不选边。
    """
    plans = []
    for rel, ent in sorted(man.items()):
        pkg = ent.get('sha16'); loc = cur.get(rel); b = base.get(rel)
        if loc == pkg:
            plans.append((rel, '同', ''))
            continue
        if rel.split('/')[-1] in SYNC_JSONL:
            # 只增不改的日志：两边各记各的都是真的，整件比哈希只会天天冲突，改为行级并集
            plans.append((rel, '并', '只增不改的日志，按行并集'))
            continue
        elif loc is None:
            plans.append((rel, '写', '本机没有'))
        elif pkg == b:
            plans.append((rel, '留', '包里这份没变化，本机更新，保留本机'))
        elif loc == b:
            plans.append((rel, '写', '本机没动过，包里有新内容'))
        else:
            why = '首次对齐且两边不同' if not has_base else '两边都改了且不一样'
            if side == 'theirs':
                plans.append((rel, '写', '显式选边：包赢（' + why + '）'))
            elif side == 'ours':
                plans.append((rel, '留', '显式选边：本机赢（' + why + '）'))
            else:
                plans.append((rel, '冲突', why + '，不选边'))
    return plans


def cmd_sync(args):
    import zipfile
    if not args or args[0] not in ('pack', 'diff', 'merge'):
        print('用法：sync pack <out.zip> | sync diff <zip> | sync merge <zip> [--apply] [--theirs|--ours]')
        print('  范围：全部 .md（含卡与快照库）+ 只追加 .jsonl + 部署清单/探针批准；派生件不进')
        print('  口径：方向由人给。默认冲突一律列出、不选边；要选边就明说 --theirs（包赢）或 --ours（本机赢）')
        sys.exit(2)
    mode = args[0]

    if mode == 'pack':
        if len(args) < 2:
            print('用法：sync pack <out.zip>'); sys.exit(2)
        out = os.path.abspath(args[1])
        files = _sync_scope()
        man = {}
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for rel in files:
                p = os.path.join(ROOT, rel)
                b = open(p, 'rb').read()
                man[rel] = {'sha16': _sha16_file(p), 'bytes': len(b)}
                z.writestr(rel, b)
            z.writestr('MANIFEST.json', json.dumps(
                {'kind': 'shelf', 'files': man}, ensure_ascii=False, indent=1))
        print('已打包 %d 件 → %s' % (len(man), out))
        print('  清单件数 %d（MANIFEST 不在其内，免得给自己作证，同 D95）' % len(man))
        sys.exit(0)

    if len(args) < 2:
        print('用法：sync %s <zip>' % mode); sys.exit(2)
    zp = os.path.abspath(args[1])
    if not os.path.exists(zp):
        print('包不存在：' + zp); sys.exit(1)
    try:
        with zipfile.ZipFile(zp) as z:
            man = json.loads(z.read('MANIFEST.json').decode('utf-8'))['files']
    except Exception:
        print('不是可用的同步包（缺 MANIFEST.json 或打不开）：' + zp); sys.exit(1)
    # 包里可能带着旧版打进去的派生件/治理件；它们不在同步范围内，直接忽略并说清件数
    skip = [r for r in man if r.split('/')[-1] in SYNC_EX_FILES]
    for r in skip:
        man.pop(r, None)
    if skip:
        print('  忽略 %d 件（派生件或治理件，不在同步范围）：%s'
              % (len(skip), '、'.join(skip[:4]) + ('…' if len(skip) > 4 else '')))

    side = 'theirs' if '--theirs' in args else ('ours' if '--ours' in args else '')
    bp = os.path.join(ROOT, SYNC_BASE)
    try:
        base = json.load(open(bp, encoding='utf-8')).get('files', {})
        has_base = True
    except Exception:
        base, has_base = {}, False
    cur = {rel: _sha16_file(os.path.join(ROOT, rel)) for rel in man}

    if mode == 'diff':
        plans = _sync_plan(man, base, cur, has_base, side)
        # 只增不改的日志：预演阶段也算一遍并集，好让"已经一样了"显示成同，而不是天天说能并
        if any(p[1] == '并' for p in plans):
            with zipfile.ZipFile(zp) as z:
                fixed = []
                for rel, act, why in plans:
                    if act == '并':
                        p = os.path.join(ROOT, rel)
                        old = open(p, encoding='utf-8', newline='').read() if os.path.exists(p) else ''
                        txt, same = _jsonl_union(old, z.read(rel).decode('utf-8'))
                        if same:
                            fixed.append((rel, '同', '')); continue
                    fixed.append((rel, act, why))
                plans = fixed
        n = {'同': 0, '写': 0, '留': 0, '并': 0, '冲突': 0}
        for rel, act, why in plans:
            n[act] += 1
            if act != '同':
                print('  %-4s %s%s' % (act, rel, ('（' + why + '）') if why else ''))
        print('合计 %d 件：同 %d、可写 %d、留本机 %d、并入 %d、冲突 %d'
              % (len(plans), n['同'], n['写'], n['留'], n['并'], n['冲突']))
        print('基线：' + ('有' if has_base else '无（首次对齐，差异默认按冲突处理）'))
        sys.exit(1 if n['冲突'] else 0)

    # merge
    apply = '--apply' in args
    rc = 0
    with _wlock('同步'):
        # 锁内重算一次：别人可能刚好在这几秒里写了
        cur = {rel: _sha16_file(os.path.join(ROOT, rel)) for rel in man}
        plans = _sync_plan(man, base, cur, has_base, side)
        n = {'同': 0, '写': 0, '留': 0, '并': 0, '冲突': 0}
        if apply:
            wrote = set(); rejected = set()
            with zipfile.ZipFile(zp) as z:
                for rel, act, why in plans:
                    if act == '同':
                        n['同'] += 1
                        continue
                    if act == '并':
                        # 只增不改的日志：本地行 + 包里多出来的行，一条都不丢
                        p = os.path.join(ROOT, rel)
                        old = open(p, encoding='utf-8', newline='').read() if os.path.exists(p) else ''
                        txt, same = _jsonl_union(old, z.read(rel).decode('utf-8'))
                        if same:
                            n['同'] += 1
                            continue
                        d = os.path.dirname(p)
                        if d and not os.path.isdir(d):
                            os.makedirs(d, exist_ok=True)
                        tmp = p + '.tmp'
                        open(tmp, 'w', encoding='utf-8', newline='\n').write(txt)
                        os.replace(tmp, p)
                        n['并'] += 1
                        wrote.add(rel)
                        added = len([x for x in txt.split('\n') if x.strip()]) - len(
                            [x for x in old.split('\n') if x.strip()])
                        print('  并入 %s（+%d 行）' % (rel, added))
                        continue
                    if act != '写':
                        n[act] += 1
                        print('  %-4s %s%s' % (act, rel, ('（' + why + '）') if why else ''))
                        continue
                    # 包里的路径来自别人，先确认它落在库内：不许绝对路径，不许 ..
                    seg = rel.replace('\\', '/').split('/')
                    if rel.startswith('/') or '..' in seg or re.match(r'^[A-Za-z]:', rel):
                        n['冲突'] += 1
                        rejected.add(rel)
                        print('  拒绝 越界路径（包内不许带 .. 或绝对路径）：' + rel)
                        continue
                    p = os.path.join(ROOT, rel)
                    d = os.path.dirname(p)
                    if d and not os.path.isdir(d):
                        os.makedirs(d, exist_ok=True)
                    tmp = p + '.tmp'
                    open(tmp, 'wb').write(z.read(rel))
                    os.replace(tmp, p)
                    n['写'] += 1
                    wrote.add(rel)
                    print('  写入 %s' % rel)
            nb = dict(base)
            for rel, act, why in plans:
                if act == '同' or rel in wrote:
                    nb[rel] = _sha16_file(os.path.join(ROOT, rel))
            for rel in rejected:
                nb.pop(rel, None)
            _atomic_json(bp, {'files': nb})
        else:
            for rel, act, why in plans:
                n[act] += 1
                if act != '同':
                    print('  %-4s %s%s' % (act, rel, ('（' + why + '）') if why else ''))
        print('%s合计 %d 件：同 %d、写 %d、留本机 %d、并入 %d、冲突 %d'
              % ('已应用，' if apply else '预演（加 --apply 才写），', len(plans),
                 n['同'], n['写'], n['留'], n['并'], n['冲突']))
        if n['冲突']:
            print('冲突 %d 件没有动，本地文件原样。看最新内容后，要么人工合，要么下轮明说 --theirs / --ours。'
                  % n['冲突'])
            rc = 1
        if not apply:
            print('基线：' + ('有' if has_base else '无（首次对齐，差异默认按冲突处理）'))
    sys.exit(rc)


def cmd_export(path=''):
    """导出跨机可比清单：只记 name/file/sha16/owner/判据档，用于两台机器之间 diff。"""
    out_p = path or os.path.join(ROOT, '导出清单.json')
    cs = load_cards(); items = {}
    for c in cs:
        mj = re.search(r'^##\s*判据\s*$([\s\S]*?)(?=^##\s|\Z)', c['body'], re.M)
        q = mj.group(1).strip() if mj else ''
        items[c['file'][:-3]] = {'file': c['file'], 'sha16': _h8(c['body']),
                                 'owner': field(c['body'], 'owner') or '',
                                 'tier': '可执行' if q.startswith('可执行') else ('可复现' if q.startswith('可复现') else '仅参考')}
    obj = {'exported_at': _today(), 'root': ROOT, 'count': len(items), 'cards': items}
    _atomic_json(out_p, obj)
    # 同步基线：export 那一刻的指纹就是"共同祖先"，后续 diff 靠它判断谁改了
    _atomic_json(os.path.join(ROOT, '同步基线.json'),
                 {'baseline_at': _today(), 'cards': {k: v['sha16'] for k, v in items.items()}})
    print('已导出 ' + str(len(items)) + ' 张 → ' + out_p)


def cmd_diff(path):
    """比对另一台机器的导出清单：新增/缺失/两边都改（真冲突）/单边改动。"""
    if not path or not os.path.exists(path):
        print('用法：diff <导出清单.json>（另一台机器 export 出来的）'); return
    other_raw = json.load(open(path, encoding='utf-8')).get('cards', {})
    # 容错：导出清单是 {卡: {file, sha16, ...}}，同步基线是 {卡: "sha前16"}。
    # 两种都是本工具自己产出的，diff 不认其中一种就等于契约只对了一半。
    other = {}
    for k, v in other_raw.items():
        other[k] = {'sha16': (v if isinstance(v, str) else (v.get('sha16') or ''))}
    cs = load_cards(); mine = {}
    for c in cs:
        mine[c['file'][:-3]] = {'file': c['file'], 'sha16': _h8(c['body'])}
    bp = os.path.join(ROOT, '同步基线.json')
    if os.path.exists(bp):
        base = json.load(open(bp, encoding='utf-8')).get('cards', {})
    else:
        base = {}
        print('⚠ 没有同步基线（先跑 export 建一次），本轮只能判单边改动')
    only_mine = sorted(set(mine) - set(other)); only_other = sorted(set(other) - set(mine))
    both_differ, mine_changed, other_changed = [], [], []
    for k in sorted(set(mine) & set(other)):
        if mine[k]['sha16'] == other[k]['sha16']: continue
        mb = base.get(k); ob = other[k]['sha16']
        local_edited = (mb is not None and mb != mine[k]['sha16'])
        if local_edited and ob != mb: both_differ.append(k)
        elif local_edited: mine_changed.append(k)
        else: other_changed.append(k)
    print('本机 ' + str(len(mine)) + ' 张 / 对方 ' + str(len(other)) + ' 张')
    print('  仅本机有：' + str(len(only_mine)) + ('（例：' + '、'.join(only_mine[:4]) + '）' if only_mine else ''))
    print('  仅对方有：' + str(len(only_other)) + ('（例：' + '、'.join(only_other[:4]) + '）' if only_other else ''))
    print('  两边都改（真冲突，须人工裁决）：' + str(len(both_differ)) + ('（' + '、'.join(both_differ[:5]) + '）' if both_differ else ''))
    print('  仅本机改（可覆盖对方）：' + str(len(mine_changed)) + ('（例：' + '、'.join(mine_changed[:4]) + '）' if mine_changed else ''))
    print('  仅对方改（可吸收）：' + str(len(other_changed)) + ('（例：' + '、'.join(other_changed[:4]) + '）' if other_changed else ''))
    print('说明：真冲突不自动合并——工具只列，不替你选。裁决记进 `裁决台账.md`。')


STATE_ORDER = ('active', 'dormant', 'archived', 'superseded')

def cmd_mark(args):
    """标记生命周期：mark <active|dormant|archived|superseded> <卡1,卡2,...>。对话会消失，状态要能跟着走。"""
    if len(args) < 2 or args[0] not in STATE_ORDER:
        print('用法：mark <' + '|'.join(STATE_ORDER) + '> <卡1,卡2,...>'); return
    st = args[0]
    raw = args[1].strip()
    if raw.endswith('.md'): raw = raw[:-3]
    if os.path.exists(_card_path(raw)):
        stems = [raw]
    else:
        stems = [x.strip()[:-3] if x.strip().endswith('.md') else x.strip() for x in args[1].split(',') if x.strip()]
    done, miss = [], []
    with _wlock('标状态'):
        for stm in stems:
            f = _card_path(stm)
            if not os.path.exists(f): miss.append(stm); continue
            t = open(f, encoding='utf-8').read()
            was_archived = bool(re.search(r'^\s*state:\s*archived', t, re.M))
            if not t.lstrip().startswith('---'):
                print('⚠ 跳过（' + stm + '）：没有 YAML frontmatter，写状态会把裸字段塞进正文顶部'); miss.append(stm); continue
            if re.search(r'^\s*state:', t, re.M):
                t = re.sub(r'^\s*state:.*$', 'state: ' + st, t, count=1, flags=re.M)
            else:
                m = re.search(r'^(\s*created_at:.*)$', t, re.M)
                t = (t[:m.end()] + '\nstate: ' + st + t[m.end():]) if m else ('state: ' + st + '\n' + t)
            if st == 'active' and was_archived:
                t = t.rstrip() + '\n- 唤醒记录：' + __import__('datetime').date.today().isoformat() + '（从归档恢复）\n'
            tmp = f + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh: fh.write(t)
            os.replace(tmp, f)
            done.append(stm)
    print('已标 ' + st + '：' + str(len(done)) + ' 张' + ('；未找到 ' + '、'.join(miss[:4]) if miss else ''))


def cmd_estate():
    """遗产视图：按项目线聚合。对话/项目会结束，资产不会消失——它只是换状态。"""
    import collections
    cs = load_cards(); idx = _midx_ok()
    by_line = collections.defaultdict(lambda: {'n': 0, 'states': collections.Counter(), 'probes': 0})
    for c in cs:
        line = field(c['body'], 'line') or field(c['body'], 'owner') or '（未标线）'
        st = field(c['body'], 'state') or 'active'
        by_line[line]['n'] += 1
        by_line[line]['states'][st] += 1
    for k, v in idx.items():
        line = v.get('line') or '（未标线）'
        if v.get('probe'): by_line[line]['probes'] += 1
    print('【按项目线】共 ' + str(len(cs)) + ' 张：')
    for line, d in sorted(by_line.items(), key=lambda x: -x[1]['n']):
        st_str = '、'.join(k + ' ' + str(v) for k, v in d['states'].most_common())
        print('  ' + line.ljust(16) + str(d['n']).rjust(4) + ' 张  探针 ' + str(d['probes']).rjust(3) + '  ' + st_str)
    print('\n【公地提示】')
    dorm = [(c['file'][:-3], c['body']) for c in cs if (field(c['body'], 'state') or 'active') in ('dormant', 'archived')]
    print('  休眠/封存 ' + str(len(dorm)) + ' 张：文件仍在、检索照常命中，只是不再催验；满足条件可 `mark active` 唤醒。')
    for stm, body in dorm[:6]:
        m = re.search(r'-\s*为什么封存[:：]\s*(.+?)(?:\n-\s|\n##|$)', body, re.S)
        why = re.sub(r'[#|*`>]+', '', m.group(1)) if m else ''
        why = re.sub(r'\s+', ' ', why).strip()
        why = (why[:96] + '…') if len(why) > 96 else why
        if not why: why = '未写原因（建议补：为什么冷、什么条件下叫醒）'
        print('    · ' + stm.ljust(46) + why)
    led = _ledg()
    unv = [k for k, v in led.items() if v.get('result') in ('unverified', None)]
    print('  从未验 ' + str(len(unv)) + ' 张：它们是账单，不是垃圾。')


USE_RESULTS = ('worked', 'failed', 'partial')

def cmd_use(args):
    """用了就写回：use <worked|failed|partial> <卡> [一句话]。给共享库留"这次用成没有"的痕。"""
    if len(args) < 2 or args[0] not in USE_RESULTS:
        print('用法：use <' + '|'.join(USE_RESULTS) + '> <卡名> [备注]'); return
    res, stem = args[0], args[1].strip()
    if stem.endswith('.md'): stem = stem[:-3]
    if stem not in _midx_ok() and not os.path.exists(_card_path(stem)):
        print('⚠ 拒绝回写：库里没有这张卡「' + stem + '」。')
        print('  先 match 找到真实卡名，或 new 建卡；凭印象写回会做出"幽灵卡"，把活性统计污染掉。')
        sys.exit(2)
    note = ' '.join(args[2:])[:120]
    try:
        with _wlock('写使用台账'):
            with open(USE, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'ts': _today(), 'card': stem, 'result': res, 'note': note, 'q': ''}, ensure_ascii=False) + '\n')
            lines = open(USE, encoding='utf-8').read().strip().split('\n')
            for i in range(len(lines) - 1, -1, -1):
                try: d = json.loads(lines[i])
                except Exception: continue
                if d.get('top') == stem and d.get('result') == 'pending':
                    d['result'] = res
                    lines[i] = json.dumps(d, ensure_ascii=False)
                    break
            with open(USE, 'w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')
    except Exception as ex:
        print('⚠ 回写失败：' + str(ex)[:50]); return
    print('已回写：' + stem + ' → ' + res + ('（' + note + '）' if note else ''))
    if res == 'failed':
        import json as _j
        try: idx = _midx_ok()
        except Exception: idx = {}
        if stem in idx:
            me = set(idx[stem].get('tags', []))
            sib = [(k, len(me & set(v.get('tags', [])))) for k, v in idx.items() if k != stem]
            sib = [x for x in sib if x[1] >= 1]
            sib.sort(key=lambda x: -x[1])
            if sib:
                print('  提示：这次失败可能是场景不同。同主题还有 ' + '、'.join(x[0] for x in sib[:3]) + '，换一张再试；都跑不通就登记裁决台账。')


def _win_days():
    """活性窗口天数：默认 365，非法/越界一律夹回 [1, 3650]，不崩。"""
    raw = os.environ.get('EXPRECALL_WINDOW_DAYS', '365')
    try:
        w = int(raw)
    except Exception:
        print('⚠ EXPRECALL_WINDOW_DAYS 不是数字（' + str(raw) + '）→ 按 365 天处理')
        return 365
    if w < 1 or w > 3650:
        print('⚠ 窗口 ' + str(w) + ' 天越界 → 夹到 [' + str(max(1, min(w, 3650))) + '] 天内')
        return max(1, min(w, 3650))
    return w


def _usage_stats():
    """按卡聚合使用记录：累计/近期/结果/最后时间。近期窗口 30 天；日期非法/未来的一律不计。"""
    import collections, datetime
    tot = collections.Counter(); ok = collections.Counter(); bad = collections.Counter()
    last = {}; pend = 0; junk = 0; badts = 0; dates = collections.defaultdict(set)
    today = datetime.date.today()
    win = _win_days()
    recent = collections.Counter(); cut = (today - datetime.timedelta(days=win)).isoformat()
    if os.path.exists(USE):
        for line in open(USE, encoding='utf-8'):
            try: d = json.loads(line)
            except Exception: junk += 1; continue
            ts = str(d.get('ts', ''))
            try:
                td = datetime.date.fromisoformat(ts[:10])
                if td > today: raise ValueError('future')
            except Exception:
                badts += 1; continue
            c = d.get('card') or d.get('top')
            if d.get('result') == 'pending': pend += 1
            if not c: continue
            if d.get('result') == 'pending' and not d.get('card'): continue
            tot[c] += 1
            if ts >= cut: recent[c] += 1
            if ts > last.get(c, ''): last[c] = ts
            dates[c].add(ts[:10])
            if d.get('result') == 'worked': ok[c] += 1
            elif d.get('result') == 'failed': bad[c] += 1
    return tot, ok, bad, last, pend, recent, junk, badts, dates


def cmd_activity():
    """活性视图（带 30 天窗口）：热、转冷、从未使用三组分开。转冷只归档，不删。"""
    cs = load_cards(); tot, ok, bad, last, pend, recent, junk, badts, dates = _usage_stats()
    st_map = {c['file'][:-3]: (field(c['body'], 'state') or 'active') for c in cs}
    used = len(tot)
    print('【使用留痕】有效记录 ' + str(sum(tot.values())) + ' 条；命中过的卡 ' + str(used) + '/' + str(len(cs))
          + '；待回写 ' + str(pend) + ' 次')
    if junk or badts:
        print('  ⚠ 已跳过坏记录：JSON 坏行 ' + str(junk) + ' 条、日期非法/未来 ' + str(badts) + ' 条（不计入活性）')
    hot = [(c, n) for c, n in tot.items() if recent.get(c, 0) > 0]
    hot.sort(key=lambda x: -recent[x[0]])
    win = _win_days()
    print('\n【近 ' + str(win) + ' 天在用】' + str(len(hot)) + ' 张：')
    for c, _ in hot[:8]:
        tag = '' if st_map.get(c, 'active') == 'active' else '  [' + st_map.get(c, 'active') + ']'
        print('  ' + c.ljust(50) + '窗口内 ' + str(recent.get(c, 0)).rjust(2) + ' 次 / 累计 ' + str(tot[c]).rjust(3)
              + ' 次  用成 ' + str(ok[c]) + ' 失败 ' + str(bad[c]) + '  最后 ' + last.get(c, '-') + tag)
    cold = [(c, tot[c]) for c in tot if recent.get(c, 0) == 0]
    print('\n【转冷（用过但近 ' + str(win) + ' 天没再用）】' + str(len(cold)) + ' 张 —— 归档，不删：')
    for c, n in sorted(cold, key=lambda x: -x[1])[:6]:
        print('  ' + c.ljust(50) + '累计 ' + str(n).rjust(3) + ' 次  最后 ' + last.get(c, '-')
              + ('' if ok[c] else '  ⚠ 一次都没写"用成"'))
    conflict = [c for c in tot if ok[c] and bad[c]]
    if conflict:
        print('\n【结果相抵（待裁决）】' + str(len(conflict)) + ' 张：' + '、'.join(conflict[:5])
              + '\n  同一张卡既被记"用成"又被记"失败"——不是数据脏，是两个场景结论不同。请登记到 裁决台账.md。')
    import datetime as _dt2
    rows = []
    today = _dt2.date.today()
    for c, ds in dates.items():
        ds = sorted(ds)
        gaps = [( _dt2.date.fromisoformat(ds[i + 1]) - _dt2.date.fromisoformat(ds[i]) ).days
                for i in range(len(ds) - 1)]
        since = (today - _dt2.date.fromisoformat(ds[-1])).days
        maxgap = max(gaps + [since]) if (gaps or since >= 0) else 0
        rows.append((c, since, maxgap, len(ds)))
    rows.sort(key=lambda x: (-x[1], -x[2]))
    print('\n【冷却信号 · 最长未用间隔】窗口未满也能看：')
    print('  ' + '卡'.ljust(40) + '距上次  最长空窗  使用天数  用成/失败')
    for c, since, maxgap, nd in rows[:8]:
        tot_r = ok[c] + bad[c]
        q = (str(ok[c]) + '/' + str(bad[c])) if tot_r else '未回写'
        flag = ''
        if tot_r and ok[c] == 0 and bad[c] >= 2:
            flag = '  ⚠ 热但没用（翻过 ' + str(bad[c]) + ' 次全失败）'
        elif tot_r and ok[c] and since >= 30:
            flag = '  ✓ 冷但可靠'
        print('  ' + c.ljust(40) + str(since).rjust(4) + ' 天' + str(maxgap).rjust(8) + ' 天'
              + str(nd).rjust(8) + ' 天   ' + q.ljust(9) + flag)
    if rows:
        top_gap = max(r[1] for r in rows)
        hist_gap = max(r[2] for r in rows)
        print('  说明：最长空窗 = 相邻两次使用之间的最大天数（含至今未再用的天数）。')
        print('  现在：最久没人碰 ' + str(top_gap) + ' 天；历史上最长的空窗 ' + str(hist_gap) + ' 天。'
              + ('  ← 说明这张卡曾经被搁置过，不是一直在用。' if hist_gap >= 30 else ''))
    never = [c['file'][:-3] for c in cs if c['file'][:-3] not in tot]
    print('\n【一次没被翻过】' + str(len(never)) + ' 张 —— 不是废，是还没轮到它。')



def _card_path(name):
    """按卡名找实际文件：库根优先，再 experience/（包内分区布局）。
    与 _card_files 同一口径——只认库根时，归档命令在解包的库上找不到卡，静默 continue。"""
    if not name.endswith('.md'):
        name += '.md'
    for d in (ROOT, os.path.join(ROOT, 'experience')):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return os.path.join(ROOT, name)


def cmd_archive(args):
    """归档：热过→转冷的卡不删，标 archived 保留可检索。
    用法：archive <卡1,卡2,...> --why "触发场景/当时结论/唤醒条件"（原因必填，半年后靠它决定要不要叫醒）"""
    why = ''
    toks = []
    if args and args[0] == '--why':
        # 写法二：archive --why "原因" 卡1,卡2（原因先写，卡在后）
        why = args[1].strip() if len(args) > 1 else ''
        toks = [a for a in args[2:] if not a.startswith('--')]
    else:
        i = 0
        while i < len(args):
            if args[i] == '--why':
                why = ' '.join(args[i + 1:]).strip()
                break
            toks.append(args[i]); i += 1
    cards = ','.join(t for t in toks if t)
    if not cards:
        print('用法：archive <卡1,卡2,...> --why "为什么封存（含唤醒条件）"（--why 放前放后都行）'); return
    if not why:
        print('⚠ 拒绝归档：必须写 --why。')
        print('  要写清四件事：①当时结论是什么 ②为什么冷了（场景过去/被取代/只在一个项目用） ③什么条件下该叫醒 ④不写就无法判断将来要不要复用它。')
        sys.exit(2)   # 拒绝≠成功：退出码分开，脚本才判得出来
    raw = cards.strip()
    if raw.endswith('.md'): raw = raw[:-3]
    if os.path.exists(_card_path(raw)):
        stems = [raw]  # 文件名本身含逗号：整串当一张卡
    else:
        stems = [x.strip()[:-3] if x.strip().endswith('.md') else x.strip() for x in cards.split(',') if x.strip()]
    cmd_mark(['archived', ','.join(stems)])
    import datetime
    for stm in stems:
        f = _card_path(stm)
        if not os.path.exists(f): continue
        t = open(f, encoding='utf-8').read()
        if '## 归档原因' in t:
            print('  提示：' + stm + ' 已有归档原因，本次未覆盖（要改请手改卡尾；唤醒后再封才会重写）')
            continue
        blk = ('\n## 归档原因\n'
               '- 封存日期：' + datetime.date.today().isoformat() + '\n'
               '- 为什么封存：' + why + '\n'
               '- 唤醒条件：见上条"为什么封存"里的条件；满足即 `mark active ' + stm + '`，并在卡尾追加唤醒记录。\n')
        tmp = f + '.tmp'
        open(tmp, 'w', encoding='utf-8').write(t.rstrip() + '\n' + blk)
        os.replace(tmp, f)
    if stems and all(os.path.exists(_card_path(x)) for x in stems):
        print('归档语义：文件保留、检索照常命中、不再催验。要恢复用 `mark active <卡>`。')

def cmd_probelog():
    """探针执行留痕视图：每根跑过几次、失败几次、最近何时、平均耗时。"""
    import collections
    runs = collections.Counter(); fails = collections.Counter(); last = {}; ms = collections.defaultdict(list)
    total = 0
    if os.path.exists(PLOG):
        for line in open(PLOG, encoding='utf-8'):
            try: d = json.loads(line)
            except Exception: continue
            c = d.get('card'); 
            if not c: continue
            total += 1; runs[c] += 1
            if not d.get('ok'): fails[c] += 1
            ts = str(d.get('ts',''))
            if ts > last.get(c, ''): last[c] = ts
            if isinstance(d.get('ms'), int): ms[c].append(d['ms'])
    print('【探针留痕】共 ' + str(total) + ' 次执行；涉及 ' + str(len(runs)) + ' 张卡')
    if not runs:
        print('  还没有记录（跑一次 gate 就有了）'); return
    print('  ' + '卡'.ljust(44) + '次数  失败  最近        平均ms')
    for c, n in runs.most_common(12):
        avg = int(sum(ms[c]) / len(ms[c])) if ms[c] else 0
        print('  ' + c.ljust(44) + str(n).rjust(4) + str(fails[c]).rjust(6) + '  ' + last.get(c, '-').ljust(12) + str(avg).rjust(6))
    fp = [c for c in runs if fails[c]]
    if fp:
        print('\n  近期失败过：' + '、'.join(fp[:8]) + '（去看卡片判据是否过期，或探针是否绑死了措辞）')


def cmd_plan(n='10'):
    """轮检计划：把"从未验"的账排成每周配额。没有探针的卡只能按"用一次 = 验一次"偿还。"""
    try: quota = int(n)
    except Exception: quota = 10
    idx = _midx_ok(); l = _ledg()
    cand = []
    for k, v in idx.items():
        rec = l.get(k, {})
        if rec.get('result') in ('ok',):
            continue
        has = bool(v.get('probe'))
        cand.append((0 if has else 1, rec.get('next_due') or '9999', k, has))
    cand.sort()
    print('【轮检计划】本周建议复核 ' + str(min(quota, len(cand))) + ' 张（共 ' + str(len(cand)) + ' 张待复核）')
    print('  优先顺序：①有探针可自动跑的（先跑闸） ②到期早的 ③只能人工"用一次顺带验一次"的')
    for pri, due, k, has in cand[:quota]:
        why = '有探针，跑 gate 即验' if has else '无探针：下次用到它时顺手复核一遍，并在 use 里记结果'
        print('  - ' + k.ljust(44) + due.ljust(12) + why)
    print('  说明：无探针的卡不假装能验；它们的偿还方式是"被用到时顺带核对"，不是编探针。')


def cmd_repro(args=None):
    """复现步骤：列出缺"## 复现步骤"的卡；`repro --init <卡>` 插入四栏空模板（不编内容）。"""
    cs = load_cards()
    need = []
    for c in cs:
        b = c['body']
        if '## 复现步骤' in b: continue
        if re.search(r'##\s*判据\s*\n\s*(可复现|仅参考)', b):
            need.append(c['file'][:-3])
    if args and args[0] == '--init':
        done, miss = 0, []
        for stm in [a for a in (args[1:] and args[1].split(',') or []) if a]:
            f = _card_path(stm)
            if not os.path.exists(f): miss.append(stm); continue
            t = open(f, encoding='utf-8').read()
            if '## 复现步骤' in t: continue
            blk = ('\n## 复现步骤\n'
                   '- 环境/前置：\n- 步骤：1) 2) 3)\n- 预期结果：\n- 为什么现在验不了：\n')
            tmp = f + '.tmp'; open(tmp, 'w', encoding='utf-8').write(t.rstrip() + '\n' + blk)
            os.replace(tmp, f); done += 1
        print('已插入四栏模板：' + str(done) + ' 张' + ('；未找到 ' + '、'.join(miss) if miss else ''))
        print('提示：四栏是空的，必须人工填；空着不算写好，lint 会继续报。')
        return
    print('【复现步骤缺口】' + str(len(need)) + ' 张卡判据是"可复现/仅参考"，但没有"## 复现步骤"：')
    for x in need[:15]: print('  - ' + x)
    if len(need) > 15: print('  … 另有 ' + str(len(need) - 15) + ' 张')
    print('处理：`repro --init <卡1,卡2,...>` 插四栏空模板，再人工填；模板见 模板_复现步骤.md')


def _rm_force(path):
    """rmtree 的加强版：遇到只读目录/权限问题先 chmod 再删；失败两次才认输。"""
    import shutil as _sh, stat as _st, time as _t
    def _onexc(func, p_, exc):
        try:
            os.chmod(p_, _st.S_IWUSR | _st.S_IRUSR | _st.S_IXUSR)
            func(p_)
        except Exception:
            pass
    for _ in range(2):
        if not os.path.exists(path): return True
        try: _sh.rmtree(path, onexc=_onexc)
        except TypeError:
            try: _sh.rmtree(path)                                  # 老版本 py 无 onexc
            except Exception: pass
        except Exception: pass
        if not os.path.exists(path): return True
        _t.sleep(0.2)
    return not os.path.exists(path)


def _sweep_stale(age_sec=600):
    """清孤儿临时根：闸被超时/强杀时走不完 atexit，目录就留在 /tmp。
    只动自己命名（exprecall_gate_*）且够老的，别人的东西一概不碰。"""
    import glob as _g, time as _t
    gone = 0
    pats = [os.path.join(TMPB,'exprecall_gate_*'), os.path.join(TMPB,'exprecall_p_*'), os.path.join(TMPB,'exprecall_verify_*')]
    for pat in pats:
        for d in _g.glob(pat):
            try:
                if _t.time() - os.path.getmtime(d) > age_sec and _rm_force(d): gone += 1
            except Exception: pass
    # 我们拷出来的库副本：/tmp/tmpXXXX/e 里有 _index.md 或 current-work-state.md
    # 这类目录按"指纹"认，不看年龄——否则每轮跑完都要等 10 分钟才回收，期间还会继续堆
    for d in _g.glob(os.path.join(TMPB,'tmp*')):
        if not os.path.isdir(d): continue
        try:
            e = os.path.join(d, 'e')
            # 起跑前就清：副本指纹很强（子目录 e 里 ≥10 个 .md），上一轮的残留不必等 10 分钟
            try: _n = sum(1 for _f in os.scandir(e) if _f.name.endswith('.md')) if os.path.isdir(e) else 0
            except Exception: _n = 0
            if _n >= 10:
                if _rm_force(d): gone += 1
                continue
            if os.path.isdir(e) and (os.path.exists(os.path.join(e, '_index.md'))
                                     or os.path.exists(os.path.join(e, 'current-work-state.md'))):
                if _rm_force(d): gone += 1
        except Exception: pass
    # 通用残渣：/tmp/tmpXXXX 里 ≥50 个条目、且 1 分钟以上没动的，是探针留下的（整包解压/拷库各有形态）
    for d in _g.glob(os.path.join(TMPB,'tmp*')):
        try:
            if not os.path.isdir(d): continue
            n = sum(len(fs) for _r, _dd, fs in os.walk(d))
            if n >= 50 and _rm_force(d): gone += 1   # 不设年龄门槛：≥50 条目的 /tmp/tmpXXXX 就是探针残渣
        except Exception: pass
    # 锁文件：只清够老的（仍在用的锁删了也不会立刻出错，但没必要冒险）
    for f in _g.glob(os.path.join(TMPB,'exprecall_*.lock')):
        try:
            if _t.time() - os.path.getmtime(f) > 86400:
                os.remove(f)
        except Exception: pass
    return gone


def _rundir_state(grace_sec=600):
    """把 `exprecall_gate_*` 分成"跑着的"和"留下的"。

    为什么分：闸正在跑时临时根当然在，那是正常状态；只有**超过宽限期还没走**的，
    才说明清理路径没走到。以前一律计数，于是"紧接着 gate 跑 watch"会把在跑的闸
    当成没回收，报 exit 6 —— 同一份代码两次跑一红一绿（D152）。
    """
    import glob as _g, time as _t
    live, stale = [], []
    for d in _g.glob(os.path.join(TMPB, 'exprecall_gate_*')):
        try:
            age = _t.time() - os.path.getmtime(d)
        except Exception:
            continue
        (stale if age > grace_sec else live).append(d)
    return live, stale


def _res_check():
    """资源体检：inode 与临时文件数是"最容易被当成业务 bug"的环境故障源。"""
    import glob as _g
    out = {}
    if hasattr(os, 'statvfs'):                      # POSIX：inode 与磁盘都能量
        try:
            v = os.statvfs('/')
            out['inode_used'] = v.f_files - v.f_ffree
            out['inode_pct'] = round(100.0 * out['inode_used'] / v.f_files, 1) if v.f_files else 0
            out['free_gb'] = round(v.f_bavail * v.f_frsize / 1e9, 2)
        except Exception:
            out['inode_pct'] = None
    else:
        # Windows 没有 statvfs：inode 概念对应 NTFS 的 MFT 记录数，标准库取不到。
        # 以前这里返回 -1，看门狗就变成"永远显示正常"——瞎掉比报错更危险。
        out['inode_pct'] = None
    if out.get('free_gb') is None:
        try:
            import shutil as _sh
            out['free_gb'] = round(_sh.disk_usage(TMPB).free / 1e9, 2)
        except Exception:
            out['free_gb'] = None
    try: out['tmp_files'] = sum(1 for _ in os.scandir(TMPB))
    except Exception: out['tmp_files'] = -1
    out['tmp_swept'] = _sweep_stale()
    _live, _stale = _rundir_state()
    out['tmp_live'] = len(_live)
    out['tmp_rundirs'] = len(_stale)
    return out


_ENV_PAT = ('No space left on device', 'Errno 28', 'Disk quota exceeded', 'Read-only file system',
            'Too many open files', 'inode', 'Permission denied')


def _env_fail(text):
    """是不是"环境资源问题"而不是"业务断言不成立"。混在一起会让业务通过率失真。"""
    t = text or ''
    return any(x in t for x in _ENV_PAT)


def cmd_watch(args=None):
    """资源看门狗：inode 使用率、/tmp 文件数、残留临时根。阈值不炸才提醒。"""
    r = _res_check()
    print('【资源看门狗】')
    _ip = r.get('inode_pct')
    print('  inode 使用率：' + ('不适用（本机无 statvfs，inode 概念对应 NTFS MFT）' if _ip is None else str(_ip) + '%')
          + ('（阈值 90%）' if _ip is not None else ''))
    print('  可用空间：' + ('取不到' if r.get('free_gb') is None else str(r.get('free_gb')) + ' GB'))
    print('  临时目录顶层文件数：' + str(r.get('tmp_files')) + '（阈值 200000）')
    print('  闸临时根：跑着 %d 个（正常）/ 残留 %d 个（应为 0；本次顺手清掉过期 %d 个）'
          % (r.get('tmp_live', 0), r.get('tmp_rundirs', 0), r.get('tmp_swept', 0)))
    bad = []
    if (r.get('inode_pct') or 0) >= 90: bad.append('inode 使用率 ≥90%，写入随时会失败')
    if r.get('free_gb') is None: bad.append('可用空间取不到（看门狗等于瞎的，请先修采集而不是当成正常）')
    if r.get('tmp_files', 0) >= 200000: bad.append('临时目录文件数 ≥20 万，先清再跑')
    if r.get('tmp_rundirs', 0) > 0: bad.append('有闸临时根超过宽限期还没回收（说明清理路径没走到）')
    if bad:
        print('⚠ 告警：' + '；'.join(bad))
        sys.exit(6)
    print('✓ 资源正常。')


def _procs():
    """本机 recall.py 进程（含自己）。用来做"进程预算"熔断：闸自己先数一遍现场。"""
    import subprocess as sp
    me = os.getpid()
    if os.name == 'nt':
        # Windows 没有 ps：以前这里直接 return []，等于"永远数不到进程"，
        # 进程预算熔断和两阶段止损全部失效（还不会被发现）。
        try:
            out = sp.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True,
                         encoding='utf-8', errors='replace').stdout
        except Exception:
            return []
        rows = []
        for line in out.splitlines():
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == 'python.exe':
                rows.append((int(parts[1]), 'python.exe ' + (parts[-1] if parts[-1] != 'N/A' else '')))
        return [r for r in rows if r[0] != me]
    try:
        out = sp.run(['ps', '-eo', 'pid,cmd'], capture_output=True, text=True).stdout
    except Exception:
        return []
    return [(int(l.split()[0]), l.strip()) for l in out.split('\n')
            if 'recall.py' in l and 'ps -eo' not in l and int(l.split()[0] or 0) != 0]


def cmd_reap(args=None):
    """两阶段止损：①先存现场 ②SIGTERM 给清理窗口 ③仍不退再 SIGKILL。
    不再一上来就 -9：-9 没有落盘窗口，正在跑的校验/缓存会断在半路，现场也一起消失。"""
    import time as _t, signal as _sig
    budget = 20
    try: budget = int(os.environ.get('EXPRECALL_MAX_PROC', '20'))
    except Exception: pass
    ps = _procs()
    others = [(pid, c) for pid, c in ps if pid != os.getpid()]
    scene = os.path.join(ROOT, '事故现场')
    print('【现场】recall.py 进程 ' + str(len(ps)) + ' 个（不含自己 ' + str(len(others)) + ' 个），预算 ' + str(budget))
    if not others:
        print('没有需要回收的进程。'); return
    os.makedirs(scene, exist_ok=True)
    fn = os.path.join(scene, 'proc_' + _t.strftime('%Y%m%d_%H%M%S') + '.txt')
    with open(fn, 'w', encoding='utf-8') as f:
        f.write('时间：' + _t.strftime('%Y-%m-%d %H:%M:%S') + '\n预算：' + str(budget) + '\n\n')
        for pid, c in others: f.write(str(pid) + '\t' + c[:200] + '\n')
    print('① 现场已存：' + os.path.relpath(fn, ROOT) + '（进程树 + 命令行，够事后复盘）')
    alive = []
    for pid, _c in others:
        try: os.kill(pid, _sig.SIGTERM)
        except Exception: pass
    for i in range(6):
        _t.sleep(0.5)
        alive = [pid for pid, _c in others if _alive(pid)]
        if not alive: break
    print('② SIGTERM 后仍在的：' + str(len(alive)) + ' 个（给了 3 秒清理窗口）')
    for pid in alive:
        try: os.kill(pid, _sig.SIGKILL)
        except Exception: pass
    _t.sleep(0.5)
    left = [pid for pid, _c in others if _alive(pid)]
    print('③ 升级 SIGKILL 后残留：' + str(len(left)) + ' 个')
    print('提示：两阶段终止是可追溯的止损；一路 -9 只是止血，现场会一起没了。')


def _alive(pid):
    try:
        os.kill(pid, 0); return True
    except Exception:
        return False


def cmd_scan(path=''):
    """静态门禁：扫自己（或指定 .py）的四条自杀式写法。命中则 exit 1，可当前置闸。

    R1 mkdtemp/mkstemp 所在函数里没有清理调用 → 迟早吃干 inode（D56）
    R2 rmtree(..., ignore_errors=True) → 静默放弃清理，失败看不见（D61）
    R3 字符串字面量里写死 /tmp 绝对路径 → 换平台即断（跨平台欠账）
    R4 subprocess 调用没传 env → 环境变量（TMPDIR/EXPRECALL_DEPTH）在子进程里丢失
    R5 四个分支（trace/evidence/provenance/eval）直接回真源解析 → 事实轴被绕过，同一件事又被解析四次
    """
    if path == '--self-test':
        # 反向自检：造一段"坏代码"，静态门禁必须红；再造一段好的，必须绿。
        # 只验检测器本身有没有瞎，不验被检代码。
        _d = tempfile.mkdtemp(prefix='exprecall_scan_')
        try:
            _bad = os.path.join(_d, 'bad.py')
            open(_bad, 'w', encoding='utf-8').write(
                'import shutil\ndef f(p):\n    shutil.rmtree(p, ignore_errors=True)\n')
            _good = os.path.join(_d, 'good.py')
            open(_good, 'w', encoding='utf-8').write(
                'import shutil, os\ndef f(p):\n    if os.path.exists(p): shutil.rmtree(p)\n')
            _r1 = subprocess.run([sys.executable, os.path.abspath(__file__), 'scan', _bad],
                                 capture_output=True, text=True)
            _r2 = subprocess.run([sys.executable, os.path.abspath(__file__), 'scan', _good],
                                 capture_output=True, text=True)
            if _r1.returncode != 0 and _r2.returncode == 0:
                print('SELFTEST PASS：坏代码被抓（exit ' + str(_r1.returncode) + '），好代码放行')
                return
            print('SELFTEST FAIL：坏 exit ' + str(_r1.returncode) + ' / 好 exit ' + str(_r2.returncode))
            print((_r1.stdout or '')[-200:]); print((_r2.stdout or '')[-200:])
            sys.exit(1)
        finally:
            _rm_force(_d)
    import ast
    p = path or os.path.abspath(__file__)
    if not os.path.exists(p):
        print('文件不存在：' + p); sys.exit(2)
    txt = open(p, encoding='utf-8', errors='replace').read()
    try:
        tree = ast.parse(txt)
    except SyntaxError as e:
        print('解析失败：' + str(e)); sys.exit(1)
    lines = txt.split('\n')
    CLEAN = ('rmtree', 'mkdtemp_cleanup', 'cleanup', 'unlink', 'remove', 'rmdir', '_rm_force')

    def calls(node):
        out = []
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                f = n.func
                nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else '')
                out.append((nm, n))
        return out

    hits = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        names = [c[0] for c in calls(fn)]
        made = any(nm in ('mkdtemp', 'mkstemp', 'TemporaryDirectory') for nm in names)
        if made and not any(nm in CLEAN for nm in names):
            hits.append(('R1', fn.lineno, '函数 `' + fn.name + '` 建临时目录但函数内无任何清理调用'))
        for nm, n in calls(fn):
            if nm == 'rmtree':
                for kw in n.keywords:
                    if kw.arg == 'ignore_errors' and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        hits.append(('R2', n.lineno, '`rmtree(ignore_errors=True)`：清理失败会被静默吞掉'))
            if nm in ('run', 'Popen', 'call', 'check_output') and not any(k.arg == 'env' for k in n.keywords):
                kw = {k.arg for k in n.keywords}
                if 'shell' in kw or 'cwd' in kw:
                    hits.append(('R4', n.lineno, '`' + nm + '` 未传 `env=`：TMPDIR / EXPRECALL_* 会在子进程里丢失'))
    # R5 事实轴：四个分支不许自己回真源里重新嗅（v6.44）
    # 没有这条，过两周就会有人图省事在分支里直接 open(台账) 再 split，
    # 然后 D173-D176 原样长回去。这条是本结构唯一能证明"真改了"的判据。
    AXIS_CMDS = ('cmd_trace', 'cmd_evidence', 'cmd_provenance', 'cmd_eval')
    SRC_CALLS = ('load_cards', '_card_probes', '_approved', '_max_defect',
                 '_dxref_build', '_prov_build', '_dxref_load', '_prov_build')
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if fn.name not in AXIS_CMDS:
            continue
        for nm, n in calls(fn):
            if nm in SRC_CALLS:
                hits.append(('R5', n.lineno, '`' + fn.name + '` 里直接调 `' + nm
                             + '`：分支只许查事实轴，不许自己回真源解析'))
            if nm == 'open':
                arg = ast.get_source_segment(txt, n.args[0]) if n.args else ''
                arg = arg or ''
                if '使用台账' in arg:
                    continue          # eval 自己的账，本就该它写
                if any(k in arg for k in ('DEFECT', 'FLOW', '探针执行台账', '探针批准', '.md', 'ROOT')):
                    hits.append(('R5', n.lineno, '`' + fn.name + '` 里直接打开真源（' + arg[:40]
                                 + '）：只许读事实轴'))
    for i, ln in enumerate(lines, 1):
        if 'R3-ok' in ln:   # 显式豁免：本行的 /tmp 是旧字面量归一化目标，不是新写死路径
            continue
        if re.search(r"['\"]/tmp", ln) and 'environ' not in ln and '#' not in ln.split('/tmp')[0][-20:]:
            hits.append(('R3', i, '硬编码 /tmp：' + ln.strip()[:60]))
    if not hits:
        print('SCAN PASS：R1-R5 无命中（' + os.path.basename(p) + '，' + str(len(lines)) + ' 行）')
        return
    print('SCAN FAIL：' + str(len(hits)) + ' 处（' + os.path.basename(p) + '）')
    for code, ln, why in hits:
        print('  [' + code + '] 第 ' + str(ln) + ' 行  ' + why)
    sev = [h for h in hits if h[0] in ('R1', 'R2', 'R4', 'R5')]
    print('  严重 ' + str(len(sev)) + ' 处（R1/R2/R4/R5 会静默吃掉故障），提示 ' + str(len(hits) - len(sev)) + ' 处（R3 跨平台）')
    sys.exit(1 if sev else 0)


CMDS = {'mech': cmd_mech, 'judge': cmd_judge, 'build_index': cmd_build_index, 'hit': cmd_hit, 'recur': cmd_recur, 'stats': cmd_stats, 'state': cmd_state, 'list': cmd_list, 'gaps': cmd_gaps, 'verify': cmd_verify, 'due': cmd_due, 'start': cmd_start, 'lint': cmd_lint, 'conflicts': cmd_conflicts, 'claim': cmd_claim, 'export': cmd_export, 'diff': cmd_diff, 'mark': cmd_mark, 'estate': cmd_estate, 'use': cmd_use, 'activity': cmd_activity, 'archive': cmd_archive, 'probelog': cmd_probelog, 'plan': cmd_plan, 'repro': cmd_repro, 'reap': cmd_reap, 'watch': cmd_watch, 'scan': cmd_scan, 'derive': cmd_derive, 'where': cmd_where}


def cmd_new(title):
    """落卡入口：生成骨架卡，时间锚/判据/机制解释留待当场填，缺则 lint 会拦。"""
    import datetime
    if not title: print('用法：new <卡名或一句话标题>'); return
    slug = re.sub(r'[\s/:：，,。]+', '-', title.strip())[:40]
    slug = re.sub(r'[^0-9a-zA-Z\u4e00-\u9fa5-]', '', slug)
    slug = slug.strip('-.') or ('card-' + _today().replace('-', ''))
    if len(slug) < 3 or slug.startswith('-'):
        slug = 'card-' + _today().replace('-', '')
    path = os.path.join(ROOT, slug + '.md')
    if os.path.exists(path): print('已存在同名卡：' + slug + '.md（用 card 查看）'); return
    t = _today()
    body = (
        '---\nname: ' + slug + '\ncreated_at: ' + t + '\nstate: skeleton\nprovenance: 当场记录\n'
        'asset_tags: error_type=待标\n'
        'source_event: 无\n'
        '---\n\n'
        '# ' + title + '\n\n## 适用场景\n（什么时候会用到这张卡）\n\n'
        '## 关键做法\n（正确做法，一到三步）\n\n'
        '## 机制解释\n（必填：契约在哪、失效为何静默。写不出就写"（未记录：暂未想清）"，不许拿现象充机制）\n\n'
        '## 踩过的坑\n（当时具体怎么错的、报了什么、试了几次）\n\n'
        '## 失败路径\n（走过的死路，写清为什么不通）\n\n'
        '## 判据\n可执行：/ 可复现：/ 仅参考：（三档选一，只能来自真实失败现象或已跑通的命令）\n\n'
        '## 失效条件\n（什么变了它就废：哪个接口/版本/环境）\n\n'
        '## 时间锚\noccurrences=' + t + '\n'
    )
    open(path, 'w', encoding='utf-8').write(body)
    print('已建骨架：' + slug + '.md')
    print('下一步：填机制解释与判据，再跑 build_index；lint 会拦缺段与假可执行。')

def _probe_card_count():
    """数"卡里写了 探针: 行的卡"有几张。用来区分两种 0 探针：
    空库（真没东西可判，出口 2）vs 批准表丢了（本该有 42 根，出口 5）。"""
    n = 0
    for f in _card_files():
        bn = os.path.basename(f)
        if bn in SKIP or bn.startswith(('时效台账', '使用台账', '探针执行台账')):
            continue
        try:
            if re.search(r'^探针:', open(f, encoding='utf-8', errors='replace').read(), re.M):
                n += 1
        except Exception:
            pass
    return n


def _deploy_selfcheck():
    """D78/D79：先验这库是不是完整的，再起闸。
    缺必需件（尤其 探针批准.json）→ exit 5，明说"库不完整"，
    而不是让 42 根探针被静默判成"未批准"、闸报 0 根空转。
    缺派生件 → 返回清单，由调用方先 build_index（否则探针比工具严，假红）。

    边界：库里有 0 张带探针的卡时不做部署阻断——那是"空库"，
    语义是"无法判定"（出口 2），不是"环境坏了"（出口 5）。
    两件事必须分开，否则空库会从 2 变 5，把空闸契约顶掉。"""
    if _probe_card_count() == 0:
        return []
    dc = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deploy_check.py')
    if not os.path.exists(dc):
        return []
    import subprocess as _sp
    try:
        r = _sp.run([sys.executable, dc, '--root', ROOT, '--json'],
                    capture_output=True, text=True, timeout=60, encoding='utf-8')
        j = json.loads(r.stdout or '{}')
    except Exception as e:
        print('⛔ 部署自检无法判定：' + str(e)[:80])
        print('   库不完整时起闸，等于在空库上盖章。先跑 `python deploy_check.py --root ' + ROOT + '`')
        sys.exit(5)
    apn = (j.get('approved') or {}).get('count', 0)
    bad = [i for i in j.get('items', []) if i.get('kind') == 'required' and i.get('stage') != 'ok']
    if bad or apn <= 0:
        print('⛔ 部署自检未过：这库不完整，闸不在它上面跑。')
        for i in bad:
            print('   · 缺/坏必需件 %s：%s' % (i['path'], i.get('note') or ''))
        if apn <= 0:
            print('   · 批准表 %s 读不到或为空 → 全部探针会被判"未批准"，闸空转（D78）'
                  % (j.get('approved') or {}).get('file', '?'))
            print('     批准表跟着库走、不跟着技能走：拷一份进库根。')
        sys.exit(5)
    return [i['path'] for i in j.get('items', [])
            if i.get('kind') in ('derived', 'derived-alias') and str(i.get('stage', '')).startswith('missing')]


def cmd_gate():
    """真闸：跑所有探针，任一失败则退出码 1（可被脚本/CI 判成败）。
    索引陈旧时先重建：闸必须验"当前库"，不能验"上次建索引时的库"。"""
    d = _depth()
    _parent = os.environ.get('EXPRECALL_PARENT_ROOT', '')
    if d >= 1 and _parent and os.path.realpath(_parent) == os.path.realpath(ROOT):
        print('⛔ 阻断：禁止在同一个库上嵌套执行 gate（当前深度 ' + str(d) + '，库 ' + os.path.basename(ROOT) + '）。')
        print('   原因：某个探针在自己的校验里又调用了 gate，会形成无限自环。')
        print('   修法：探针只断言"自己这一小步"，别断言"整个流程"；要验流程请用临时库（cp -rL 出来的副本）。')
        sys.exit(3)
    try: _budget = int(os.environ.get('EXPRECALL_MAX_PROC', '20'))
    except Exception: _budget = 20
    _ps = _procs()
    if len(_ps) >= _budget:
        print('⛔ 熔断：recall.py 进程数 ' + str(len(_ps)) + ' ≥ 预算 ' + str(_budget) + '，拒绝起闸。')
        print('   先跑 `recall.py reap`：存现场 → SIGTERM → 仍不退再 SIGKILL。预算用 EXPRECALL_MAX_PROC 调。')
        sys.exit(4)
    _miss = _deploy_selfcheck()
    if _miss:
        print('⛔ 库缺派生件：' + '、'.join(_miss))
        print('   闸不替你建——闸只读，不改被测对象（自己铺路再判定，等于自己给自己发通行证）。')
        print('   修法：先跑 `recall.py build_index`，再起闸。')
        sys.exit(5)
    _e_ok, _e_note = _ensure_index()
    if _e_note and '陈旧' in _e_note:
        print('⚠ 索引陈旧 → 先重建再判（否则闸验的是旧库）')
        cmd_build_index()
    elif _e_note:
        print(_e_note)
    idx = _midx_ok(); l = _ledg(); bad = []; ok = 0; envbad = []; skipplat = []; skipmat = []
    _bl = _blacklist(); _cur_plat = _plat()

    def _snap():
        """库文件快照。排除"闸自己会写的东西"（台账/索引），否则每次都会诬告自己。"""
        out = {}
        for f in _card_files():
            bn = os.path.basename(f)
            if bn.startswith('时效台账') or bn.startswith('使用台账') or bn.startswith('探针执行台账') or bn in SKIP:
                continue
            try: out[bn] = _h8(open(f, encoding='utf-8', errors='replace').read())
            except Exception: pass
        return out

    import tempfile as _tf, shutil as _sh, atexit as _ae
    _r0 = _res_check()
    if (_r0.get('inode_pct') or 0) >= 90 or (_r0.get('tmp_files') or 0) >= 200000 or _r0.get('free_gb') is None:
        print('⚠ 资源告警：inode ' + str(_r0.get('inode_pct')) + '、临时目录 ' + str(_r0.get('tmp_files'))
              + ' 个文件。此时探针失败**可能不是业务问题**，先跑 `recall.py watch`。')
    before = _snap()
    if sum(1 for v in idx.values() if v.get('probe')) == 0:
        print('GATE 无法判定：0 根探针（空闸不等于通过）'); sys.exit(2)
    _rundir = _tf.mkdtemp(prefix='exprecall_gate_')     # 判定成立后才建临时根，避免早退也泄漏
    _ae.register(_sh.rmtree, _rundir, True)             # 异常/中断/早退都兜住（try..finally 的等价物）
    # 跑前记下已有的锁文件。每根探针把临时库当 ROOT，会各按自己的路径哈希留一把锁；
    # 临时库跑完就删，锁留在临时目录里就成了"库早没了、锁还在"的死件（实测一轮 48 个）。
    # 收尾时把"本轮新长出来的、又不是本库那把"清掉——锁的命名规则不变，探针判据也不用改。
    import glob as _g0
    _locks0 = set(_g0.glob(os.path.join(__import__('tempfile').gettempdir(), 'exprecall_*.lock')))
    _probe_names = [n for n, v in idx.items() if v.get('probe')]
    if len(_probe_names) != sum(1 for v in idx.values() if v.get('probe')):
        print('⚠ 探针清单自相矛盾，请查索引')
    for name in _probe_names:
        v = idx[name]
        pr = v.get('probe')
        if not pr: continue
        if _cur_plat in _bl.get(name, []):
            # 平台语义不支持（POSIX 专属：软链特权 / /etc/passwd / 特定包 fixture）。
            # 不执行、不判业务失败、不计入通过率；单独列一笔，exit 7 仅作提示不阻断。
            skipplat.append(name)
            _plog(name, None, '平台不适用:' + _cur_plat, 0)
            l[name] = {'last_verified': _today(), 'result': 'skipped',
                       'next_due': _ttl(name, v, l), 'note': '平台不适用 ' + _cur_plat}
            continue
        _ok, _why = _probe_ok(pr.get('cmd', ''))
        if not _ok:
            bad.append(name + '(探针被拒:' + _why + ')'); continue
        _t0 = __import__('time').time()
        _txt = ''                      # 取证用：异常路径下也要有定义，否则下面引用会 NameError
        try:
            _argv, _nfix = _portable(pr.get('cmd', ''))
            if _argv:
                if _nfix: _portfix = globals().setdefault('_portfix', [0, 0]); _portfix[0] += 1; _portfix[1] += _nfix
                r = subprocess.run(_argv, capture_output=True, text=True, timeout=30, cwd=ROOT,
                                   env=dict(os.environ, EXPRECALL_DEPTH=str(d + 1),
                                            EXPRECALL_PARENT_ROOT=os.path.realpath(ROOT), EXPRECALL_THIS_ROOT=os.path.realpath(ROOT),
                                            EXPRECALL_SK=os.path.abspath(__file__), PYTHONIOENCODING='utf-8'))
            else:
                r = subprocess.run(pr['cmd'], shell=True, capture_output=True, text=True, timeout=30, cwd=ROOT,
                                   env=dict(os.environ, EXPRECALL_DEPTH=str(d + 1),
                                            EXPRECALL_PARENT_ROOT=os.path.realpath(ROOT), EXPRECALL_THIS_ROOT=os.path.realpath(ROOT),
                                            EXPRECALL_SK=os.path.abspath(__file__), PYTHONIOENCODING='utf-8'))
            _txt = ((r.stdout or '') + '\n' + (r.stderr or ''))
            if pr.get('expect_exit') is not None:
                good = (r.returncode == int(pr['expect_exit']))
            elif pr.get('expect_min') is not None:
                try: num = int((r.stdout or '').strip().split('\n')[-1].split()[-1])
                except Exception: num = -1
                good = num >= int(pr['expect_min'])
            else:
                good = (r.returncode == 0)
        except Exception as _e:
            good = False
            _to = isinstance(_e, __import__('subprocess').TimeoutExpired)
            if _to:
                envbad.append(name + '(超时未返回)')
                _plog(name, False, '超时未返回', int((__import__('time').time() - _t0) * 1000),
                      out=_txt or repr(_e))
            else:
                bad.append(name)
                _plog(name, False, '异常', int((__import__('time').time() - _t0) * 1000),
                      out=_txt or repr(_e))
        if good:
            ok += 1
            _plog(name, True, '', int((__import__('time').time() - _t0) * 1000))
        else:
            if _env_fail(_txt):
                envbad.append(name + '(环境资源异常)')
                _plog(name, False, '环境资源异常', int((__import__('time').time() - _t0) * 1000), out=_txt)
            elif '<PKG>' in (pr.get('cmd') or '') and not _pkg_has_zip():
                # 这几根验的是「包」本身：没有包就无从比对，是缺物料，不是判据不成立。
                # 两种缺法都算跳过——这台机器连 <PKG> 目录都没有，或者目录在、里头没 zip。
                # 曾经把前者判成"环境异常"（exit 5），于是"没有包"这同一个事实报出两种
                # 出口码，账面写的 7 在真机上永远对不上（实测本机跑出 41/0/4env/2/0）。
                skipmat.append(name + ('(本机连 <PKG> 目录都没有)' if not _pkg_root() else '(本机没有包可比对)'))
                _plog(name, False, '缺物料跳过', int((__import__('time').time() - _t0) * 1000), out=_txt)
            else:
                bad.append(name)
                _plog(name, False, '断言未满足', int((__import__('time').time() - _t0) * 1000), out=_txt)
        l[name] = {'last_verified': _today(), 'result': 'ok' if good else 'fail',
                   'next_due': _ttl(name, v, l), 'note': 'gate'}
    _save(l)
    _left = 0
    try: _left = sum(len(fs) for _r, _d, fs in os.walk(_rundir))
    except Exception: pass
    _rm_force(_rundir)                                  # 探针的临时副本一并回收（失败要能看见）
    # 清掉本轮新长出来的锁（本库那把留着）。此时所有探针都已结束，不会误删正在用的。
    _keep_lock = os.path.basename(_lockpath())
    _dead_lock = 0
    for _p in _g0.glob(os.path.join(__import__('tempfile').gettempdir(), 'exprecall_*.lock')):
        if _p in _locks0 or os.path.basename(_p) == _keep_lock:
            continue
        try:
            os.unlink(_p); _dead_lock += 1
        except Exception:
            pass
    # 本轮跑出来的临时目录：不看指纹，只看"本轮新建"——跑完时所有探针都已结束，
    # 这些目录一律是残渣（有的探针解压整包、有的拷临时库，形态各不相同）
    _swept_all = 0
    for _d in __import__('glob').glob(os.path.join(TMPB,'tmp*')):
        try:
            if os.path.isdir(_d) and os.path.getmtime(_d) >= _t_start - 5:
                if _rm_force(_d): _swept_all += 1
        except Exception: pass
    # 本轮跑出来的副本目录（有些探针自己 mktemp、不走我们的 TMPDIR），按"本轮新建 + 库指纹"清掉
    _swept2 = 0
    for _d in __import__('glob').glob(os.path.join(TMPB,'tmp*')):
        try:
            if not os.path.isdir(_d): continue
            if os.path.getmtime(_d) < _t_start - 5: continue
            _e = os.path.join(_d, 'e')
            if os.path.isdir(_e):
                # 指纹放宽：子目录 e 里有 ≥10 个 .md，就是"我们的库副本"（副本可能拷的是临时库，没有 _index）
                try: _n = sum(1 for _f in os.scandir(_e) if _f.name.endswith('.md'))
                except Exception: _n = 0
                if _n >= 10 and _rm_force(_d): _swept2 += 1
        except Exception: pass
    # 本轮产生的锁文件：它们对应的是本轮临时库，库都删了锁就是死的
    _lk = 0
    for _f in __import__('glob').glob(os.path.join(TMPB,'exprecall_*.lock')):
        try:
            if os.path.getmtime(_f) >= _t_start - 5 and _ts.time() - os.path.getmtime(_f) > 2:
                os.remove(_f); _lk += 1
        except Exception: pass
    if _left or _swept2 or _swept_all or _lk:
        print('  · 已回收本轮临时文件 ' + str(_left) + ' 个、副本目录 ' + str(_swept2)
              + ' 个、任意残留目录 ' + str(_swept_all) + ' 个、锁文件 ' + str(_lk)
              + ' 个（探针不清理会把 inode 吃干）')
    after = _snap()
    changed = sorted(set(k for k in before if k in after and before[k] != after[k]))
    added = sorted(set(after) - set(before)); removed = sorted(set(before) - set(after))
    if changed or added or removed:
        print('⚠ 探针改动了库本身（闸不该改它正在验证的对象）：')
        for x in changed: print('  · 改：' + x)
        for x in added: print('  · 增：' + x)
        for x in removed: print('  · 删：' + x)
        bad.append('库被探针改动(' + str(len(changed)) + '改/' + str(len(added)) + '增/' + str(len(removed)) + '删)')
    print('  · 本轮实际执行探针 ' + str(len(_probe_names)) + ' 根：通过 ' + str(ok)
          + '、平台不适用 ' + str(len(skipplat)) + '、缺物料跳过 ' + str(len(skipmat)))
    tag = 'PASS' if not bad and not envbad else ('FAIL' if bad else 'UNKNOWN')
    print('GATE ' + tag + '：' + str(ok) + ' 通过 / ' + str(len(bad)) + ' 业务失败 / '
          + str(len(envbad)) + ' 环境异常 / ' + str(len(skipplat)) + ' 平台不适用 / ' + str(len(skipmat)) + ' 缺物料')
    for b in bad: print('  【业务校验失败】' + b)
    for b in envbad: print('  【环境资源异常】' + b + '  ← 不是业务 bug：先查资源与环境（磁盘/inode/权限/外部物料）')
    for b in skipplat: print('  【平台不适用】' + b + '  ← ' + _cur_plat + ' 上无法复现，不计入业务通过率')
    for b in skipmat: print('  【缺物料】' + b + '  ← 这几根验的是「包」本身；本机没有包可比对，不是包的毛病')
    if bad: sys.exit(1)
    if envbad: sys.exit(5)
    if skipplat or skipmat: sys.exit(7)

def cmd_review():
    """回顾入口：四个数与检索耗时，一次出齐（周期回顾用）。"""
    import time, collections
      # 1) 命中分布
    ledger = os.path.join(ROOT, '使用台账.jsonl')
    hits = collections.Counter()
    if os.path.exists(ledger):
        for line in open(ledger, encoding='utf-8'):
            try:
                d = json.loads(line)
                k = d.get('top') or d.get('card') or ''
                if k: hits[k] += 1
            except Exception: pass
    cs = load_cards(); tot = len(cs)
    hit_cards = len(hits); queries = 0
    if os.path.exists(ledger):
        queries = sum(1 for _ in open(ledger, encoding='utf-8'))
    print('【1 命中分布】' + str(queries) + ' 次检索，命中 ' + str(hit_cards) + '/' + str(tot) + ' 张卡' + ('（覆盖率 ' + str(_pct(hit_cards, tot)) + ' pct）' if tot else '（空库，覆盖率不计算）'))
    for k, v in hits.most_common(5): print('   ' + str(v).rjust(3) + ' 次  ' + k)
      # 2) 复发间隔
    multi = 0; gaps = []; same_day = 0
    for c in cs:
        ds = _clean_dates(_anchors(c['body']), '')
        stt = _date_stats(ds)
        if stt['n'] > len(stt['days']): same_day += stt['n'] - len(stt['days'])
        if len(stt['days']) < 2: continue
        multi += 1
        gaps += stt['gaps']
    if gaps:
        print('【2 复发间隔】跨天复发卡 ' + str(multi) + ' 张，平均 ' + str(round(sum(gaps)/len(gaps),1)) + ' 天，最短 ' + str(min(gaps)) + '，最长 ' + str(max(gaps)) + '；同日重复 ' + str(same_day) + ' 次（不计入间隔，避免被 0 天稀释）')
    else:
        print('【2 复发间隔】跨天复发卡 0 张（样本不足，不能判趋势）；同日重复 ' + str(same_day) + ' 次')
    if tot == 0:
        print('【空库】没有任何卡，比例类指标一律不计算（0 分母不是 100%）。')
      # 3) 判据失效率
    l = _ledg(); probe = 0; fail = 0
    for v in l.values():
        if v.get('result') in ('ok','fail'):
            probe += 1
            if v['result'] == 'fail': fail += 1
    print('【3 判据失效率】' + str(fail) + '/' + str(probe) + ' 条探针不成立' + ('（尚未跑过探针）' if not probe else ''))
      # 4) 空壳率
    hollow = 0
    for c in cs:
        m = re.search(r'^##\s*判据\s*$([\s\S]*?)(?=^##\s|\Z)', c['body'], re.M)
        q = m.group(1).strip() if m else ''
        if not q or q.startswith('仅参考'): hollow += 1
    print('【4 空壳率】判据为"仅参考/空" ' + str(hollow) + '/' + str(tot) + ('（' + str(_pct(hollow, tot)) + ' pct）' if tot else '（空库，比例不计算）'))
      # 5) 检索耗时
    t0 = time.time()
    try: _midx_ok()
    except Exception: pass
    print('【附 检索耗时】索引加载 + 一次匹配参考值 ' + str(round(time.time()-t0, 3)) + ' 秒')


# ===== 个人偏好镜像（双写自豆包偏好）=====
# 为什么单独一套：偏好不是经验卡。卡回答"这类问题怎么判"，偏好回答"这个人是什么样"。
# 硬塞进卡库会稀释 244 的分母，也会让"过期债"指标失真。所以另开一条只追加的流水。

PREF_FILE = _resolve(('个人偏好与点评.md',))
PREF_SEC = '## 五、偏好记录（只追加）'
PREF_MARKS = ('**记录**', '**好在哪**', '**差在哪**', '**与主流差异**')

# 文档层：可检索、但不是经验卡（无判据、不进卡数、不进 匹配索引.json）。
# 白名单只此一处，改动要发版——避免像 D103 那样同一规则抄两份各走各的。
DOCS = ('个人偏好与点评.md', '用户画像.md',
        '架构与降权矩阵.md', '目标态.md', '长期路线.md',
        '对齐表.md', '外部方法评估.md', '体检报告.md')

# 带日期的快照：内容会随代码演进过期，命中时把日期一起打出来，别当成现状引用。
# 体检报告是"2026-11-12 给这库自己拍的一张照片"：结构判断还成立，数字全过期（187 张卡时代）。
DOC_STALE = {'对齐表.md': '2026-11-12 快照',
             '外部方法评估.md': '2026-11-12 快照',
             '体检报告.md': '2026-11-12 旧照片，数字已过期'}


def _pref_read():
    try:
        return open(PREF_FILE, encoding='utf-8').read()
    except Exception:
        return ''


def _host():
    import socket
    try:
        return socket.gethostname() or ('win' if os.name == 'nt' else 'unix')
    except Exception:
        return 'unknown'


def _pref_blocks(text):
    """只切 PREF_SEC 之后的 '### ' 块；前面的正文（一至四节点评）一个字不碰。"""
    i = text.find(PREF_SEC)
    if i < 0:
        return []
    body = text[i + len(PREF_SEC):]
    return [('### ' + p).rstrip() for p in re.split(r'^###\s+', body, flags=re.M)[1:] if p.strip()]


def _pref_fields(blk):
    """把四个栏切出来：{标记: 内容}。顺序按标记在块里第一次出现的位置。"""
    pos = sorted((blk.find(m), m) for m in PREF_MARKS if blk.find(m) >= 0)
    out = {}
    for k, (p, m) in enumerate(pos):
        end = pos[k + 1][0] if k + 1 < len(pos) else len(blk)
        out[m] = blk[p + len(m):end].strip(' \t\n：:')
    return out


def _pref_add(args):
    """追加一条"记录 + 三栏点评"。四栏不许空（没有就写'未见'），空则整条拒绝落盘。"""
    pid = ''
    src = ''
    i = 0
    while i < len(args):
        if args[i] == '--pid' and i + 1 < len(args):
            pid = args[i + 1].strip(); i += 2
        elif args[i] == '--from' and i + 1 < len(args):
            src = args[i + 1]; i += 2
        else:
            i += 1
    if not pid:
        print('拒绝：缺 --pid（豆包 preference_id，用来串同一条偏好的历次改动）'); sys.exit(1)
    if not src:
        print('拒绝：缺 --from <文件|->（点评用文件传，命令行塞长中文会被引号/编码吃掉）'); sys.exit(1)
    if not os.path.exists(PREF_FILE):
        print('拒绝：库里没有 ' + PREF_FILE + '（先把文件放到库根，再写）'); sys.exit(1)
    try:
        blk = sys.stdin.read() if src == '-' else open(src, encoding='utf-8').read()
    except Exception as ex:
        print('拒绝：读不到来源（' + str(ex)[:60] + '）'); sys.exit(1)
    blk = blk.replace('\r\n', '\n').strip()
    if '###' in blk:
        print('拒绝：正文含 ###（会造出假条目）。条目标题由工具生成，别手写'); sys.exit(1)
    lack = [m for m in PREF_MARKS if m not in blk]
    if lack:
        print('拒绝：缺栏 ' + '、'.join(lack) + '（四栏都要写，没有就写"未见"）'); sys.exit(1)
    f = _pref_fields(blk)
    empty = [m for m in PREF_MARKS if not (f.get(m) or '').strip()]
    if empty:
        print('拒绝：' + '、'.join(empty) + ' 栏是空的（没得写就写"未见"，空着等于没点评）'); sys.exit(1)
    text = _pref_read()
    if PREF_SEC not in text:
        print('拒绝：文件里没有「' + PREF_SEC + '」这一节。先建好骨架再写，不替你猜结构'); sys.exit(1)
    pos = text.find(PREF_SEC)
    after = text[pos + len(PREF_SEC):]
    if re.search(r'^##\s+', after, re.M):
        print('拒绝：偏好记录节后面又有一级节，append 会插到别人头上。先看结构再写'); sys.exit(1)
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    head = '### ' + stamp + ' · ' + _host() + ' · ' + pid
    add = head + '\n\n' + blk + '\n'
    with _wlock('偏好追加'):
        cur = _pref_read()                      # 锁内重读：别人刚写过就是新的，不用旧快照
        tmp = PREF_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(cur.rstrip('\n') + '\n\n' + add)
            fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, PREF_FILE)
    n = len(_pref_blocks(_pref_read()))
    print('已追加 · ' + head[4:])
    print('  共 ' + str(n) + ' 条 · ' + PREF_FILE + ' · ' + str(os.path.getsize(PREF_FILE)) + ' 字节')


def _pref_list(args):
    n = 5
    if args and args[0].isdigit():
        n = max(1, int(args[0]))
    bs = _pref_blocks(_pref_read())
    if not bs:
        print('偏好记录 0 条（「' + PREF_SEC + '」里还没有条目）'); return
    print('偏好记录 ' + str(len(bs)) + ' 条，最近 ' + str(min(n, len(bs))) + ' 条：')
    for b in bs[-n:]:
        head = b.split('\n', 1)[0].lstrip('#').strip()
        bad = (_pref_fields(b).get('**差在哪**') or '').split('\n')[0][:64]
        print('  - ' + head)
        print('      差在哪：' + (bad or '—'))


def _pref_audit():
    if not os.path.exists(PREF_FILE):
        print('未找到 ' + PREF_FILE); return
    text = _pref_read()
    if PREF_SEC not in text:
        print('缺节「' + PREF_SEC + '」——文件在但结构没建好'); return
    bs = _pref_blocks(text)
    if not bs:
        print('偏好记录 0 条')
        print('  ! 「' + PREF_SEC + '」是空的：平台那边每记一条偏好，这里就该有一条镜像。')
        _profile_line(0)
        return
    bad = []
    pids = {}
    for b in bs:
        head = b.split('\n', 1)[0].lstrip('#').strip()
        f = _pref_fields(b)
        miss = [m for m in PREF_MARKS if not (f.get(m) or '').strip()]
        if miss:
            bad.append((head, miss))
        pid = head.split('·')[-1].strip() if '·' in head else '(无pid)'
        pids[pid] = pids.get(pid, 0) + 1
    print('偏好记录 ' + str(len(bs)) + ' 条；缺栏 ' + str(len(bad)) + ' 条')
    for head, miss in bad:
        print('  ✗ ' + head + '  缺 ' + '、'.join(miss))
    dup = {k: v for k, v in pids.items() if v > 1}
    if dup:
        print('同一 pid 多条（这是 update 的真实轨迹，按时间往下读就是这条偏好的变化史）：')
        for k, v in dup.items():
            print('  - ' + k + '：' + str(v) + ' 条')
    deep = sum(1 for b in bs if len(_pref_fields(b).get('**差在哪**') or '') >= 40)
    print('长点评（差在哪 ≥40 字）' + str(deep) + '/' + str(len(bs)) + ' 条' +
          ('（偏少：点评在敷衍）' if deep * 2 < len(bs) else ''))
    last = ''
    for b in bs:
        h = b.split('\n', 1)[0].lstrip('#').strip()
        m = re.match(r'(\d{4}-\d{2}-\d{2})', h)
        if m and m.group(1) > last:
            last = m.group(1)
    if last:
        try:
            days = (datetime.date.today() - datetime.date(int(last[:4]), int(last[5:7]), int(last[8:10]))).days
        except Exception:
            days = None
        if days is not None:
            tip = ' —— 已经 ' + str(days) + ' 天没抄新的了，先回头看看平台那边有没有漏' if days >= 30 else ''
            print('最近一条：' + last + '（' + str(days) + ' 天前）' + tip)
    _profile_line(len(bs))


def _profile_line(n):
    """画像一致性那一行：audit 在 0 条时也要报，别让"还没画像"被"0 条记录"吞掉。"""
    tail = _profile_count()
    if tail is None:
        print('画像：还没有（跑 pref profile 看素材，写完用 pref profile --save --from 落盘）')
    elif tail != n:
        print('画像：依据 ' + str(tail) + ' 条，当前记录 ' + str(n) + ' 条 —— 画像落后了，该重做')
    else:
        print('画像：依据 ' + str(tail) + ' 条，与记录数一致')


# ===== 画像层（覆盖式，与偏好档的只追加相反）=====
# 为什么单开一个文件：偏好档是"逐条记账"，只能追加；画像是"你现在是什么样"，会变。
# 混在一份文件里，要么破坏只追加的承诺，要么让画像越写越长最后没人读。

PROFILE_FILE = _resolve(('用户画像.md',))
PROFILE_SECS = ('## 一、一句话', '## 二、稳定的特点', '## 三、偏好清单（按领域）',
                '## 四、好在哪 · 差在哪 · 与主流差在哪', '## 五、还没改的')


def _profile_count():
    """画像头部那行"依据 N 条记录"里的 N；没画像返回 None。用它判画像有没有落后。"""
    try:
        t = open(PROFILE_FILE, encoding='utf-8').read()
    except Exception:
        return None
    m = re.search(r'依据\s*(\d+)\s*条偏好记录', t)
    return int(m.group(1)) if m else None


def _pref_material():
    """出料：把画像要用的原始材料摆齐，不做任何总结。
    总结是判断，不是统计——硬让脚本编一句"你是个什么样的人"，那是假的可执行。"""
    text = _pref_read()
    if not text:
        print('未找到 ' + PREF_FILE)
        return
    # 一至四节的存量点评：表格行 | 编号 | 偏好 | 点评 |
    head = text.split(PREF_SEC)[0]
    rows = re.findall(r'^\|\s*(\d+)\s*\|(.+?)\|(.+?)\|\s*$', head, re.M)
    bs = _pref_blocks(text)
    print('画像素材 · ' + PREF_FILE)
    print('  存量点评 ' + str(len(rows)) + ' 条（一至四节）· 新增记录 ' + str(len(bs)) + ' 条（第五节）')
    tail = _profile_count()
    print('  画像 ' + ('未生成' if tail is None else
                       ('依据 ' + str(tail) + ' 条' + ('（落后）' if tail != len(bs) else '（最新）'))))
    if rows:
        print('\n—— 存量点评里的"点评"栏（写画像时当背景，不要逐条复述）——')
        for n, pref, cm in rows:
            print('  ' + n + '. ' + re.sub(r'\s+', ' ', cm)[:150])
    if not bs:
        print('\n新增记录 0 条：第五节还是空的，先 pref add 攒几条，画像才有一手材料。')
        return
    print('\n—— 新增记录（按 pid 归并，同一 pid 的多条就是这条偏好的变化史）——')
    by = {}
    for b in bs:
        h = b.split('\n', 1)[0].lstrip('#').strip()
        pid = h.split('·')[-1].strip() if '·' in h else '(无pid)'
        by.setdefault(pid, []).append((h, _pref_fields(b)))
    for pid, items in by.items():
        print('  [' + pid + '] ' + str(len(items)) + ' 条')
        for h, f in items:
            print('    ' + h)
            print('      记录：' + re.sub(r'\s+', ' ', (f.get('**记录**') or ''))[:90])
            print('      好在哪：' + re.sub(r'\s+', ' ', (f.get('**好在哪**') or ''))[:90])
            print('      差在哪：' + re.sub(r'\s+', ' ', (f.get('**差在哪**') or ''))[:90])
            print('      与主流差异：' + re.sub(r'\s+', ' ', (f.get('**与主流差异**') or ''))[:90])
    print('\n写画像时四条口径：①稳定压过鲜明，只写反复出现的 ②肯说好 ③差在哪要具体到能改 ④别把一条偏好当整个人')


def _pref_profile(args):
    """无参 = 出料；--save --from <文件> = 落盘（覆盖式）。
    分开是故意的：代码负责摆料和拦不合格的稿，下判断这一步交回给写的人。"""
    src = ''
    for i, a in enumerate(args):
        if a == '--from' and i + 1 < len(args):
            src = args[i + 1]
    if '--save' not in args:
        _pref_material()
        return
    if not src:
        print('拒绝：缺 --from <文件>（画像长度和中文，命令行塞不进去）'); sys.exit(1)
    try:
        body = (sys.stdin.read() if src == '-' else open(src, encoding='utf-8').read())
    except Exception as ex:
        print('拒绝：读不到来源（' + str(ex)[:60] + '）'); sys.exit(1)
    body = body.replace('\r\n', '\n').strip()
    if '###' in body:
        print('拒绝：正文含 ###（画像只到二级节，子标题会把它变成又一份长文档）'); sys.exit(1)
    heads = [l.strip() for l in body.split('\n') if l.startswith('## ')]
    if len(heads) != len(PROFILE_SECS):
        print('拒绝：要恰好 ' + str(len(PROFILE_SECS)) + ' 个一级节，现在是 ' + str(len(heads)) + ' 个')
        print('      骨架：' + ' / '.join(PROFILE_SECS)); sys.exit(1)
    for got, want in zip(heads, PROFILE_SECS):
        if not got.startswith(want.split('（')[0].split(' ·')[0]):
            print('拒绝：节序或标题不对，第 ' + str(heads.index(got) + 1) + ' 个应是「' + want + '」，实际「' + got + '」')
            sys.exit(1)
    blanks = []
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else None
        seg = body.split(h, 1)[1]
        if end:
            seg = seg.split(end, 1)[0]
        if not seg.strip():
            blanks.append(h)
    if blanks:
        print('拒绝：' + '、'.join(blanks) + ' 是空的（没得写就写"未见"，空节等于没画像）'); sys.exit(1)
    txt = _pref_read()
    n = len(_pref_blocks(txt))
    m = len(re.findall(r'^\|\s*(\d+)\s*\|', txt.split(PREF_SEC)[0], re.M))
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    head = ('# 用户画像\n\n> 生成于 ' + stamp + ' · ' + _host() + ' · 依据 ' + str(n) +
            ' 条偏好记录 + ' + str(m) + ' 条存量点评 · 覆盖式文件（每次 pref profile --save 重写，不追加）\n')
    out = head + body + '\n'
    with _wlock('画像落盘'):
        tmp = PROFILE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(out); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, PROFILE_FILE)
    print('画像已写 · ' + PROFILE_FILE + ' · ' + str(os.path.getsize(PROFILE_FILE)) +
          ' 字节 · 依据 ' + str(n) + ' 条记录 + ' + str(m) + ' 条点评')
    print('  下次回顾跑：pref audit（看记录有没有缺栏）+ pref profile（重新出料）')


def cmd_pref(args):
    sub = args[0] if args else ''
    if sub == 'add':
        return _pref_add(args[1:])
    if sub == 'list':
        return _pref_list(args[1:])
    if sub == 'audit':
        return _pref_audit()
    if sub == 'profile':
        return _pref_profile(args[1:])
    if sub == 'show':
        print(_pref_read() or ('未找到 ' + PREF_FILE))
        return
    print('用法：pref add --pid <id> --from <文件|->  |  pref list [N]  |  pref audit  '
          '|  pref profile [--save --from <文件|->]  |  pref show')


def doc_files():
    """文档层文件：能检索，但不算卡。找不到就跳过，不编造。
    包内布局是 state/，装完后摊平到库根，两种都认。"""
    out = []
    for n in DOCS:
        for p in (os.path.join(ROOT, n), os.path.join(ROOT, 'experience', n), os.path.join(ROOT, 'state', n)):
            if os.path.isfile(p):
                try:
                    out.append({'file': n, 'body': open(p, encoding='utf-8', errors='replace').read()})
                except Exception:
                    pass
                break
    return out


def _search_docs(kw):
    # 文件名要一起参检：文档层没有 desc 字段，只搜正文会出现"打了文件名搜不到自己"。
    ds = [d for d in doc_files() if hit(kw, d['file'] + '\n' + d['body'])]
    if not ds:
        return
    print('[文档] ' + str(len(ds)) + ' 份命中（参考文档，非经验卡、无判据）')
    for d in ds:
        title = ''
        for ln in d['body'].split('\n'):
            if ln.startswith('# '):
                title = ln[2:].strip(); break
        tag = DOC_STALE.get(d['file'])
        print('  - ' + d['file'] + ('  [' + tag + ']' if tag else '') + '  ' + title[:70])



def _refresh_manifest_sha(rel):
    """批准表一动，把部署清单里它那条 sha8 一起刷新（Q08）。

    只刷这一条，不重写整份清单：别为了一个哈希去动 13 条的其余字段。
    清单不存在或没这一条就静默跳过——它不是必需件。
    """
    try:
        mp = os.path.join(ROOT, '部署清单.json')
        if not os.path.exists(mp):
            return
        d = json.load(open(mp, encoding='utf-8'))
        tgt = os.path.join(ROOT, rel)
        if not os.path.exists(tgt):
            return
        h = hashlib.sha256(open(tgt, 'rb').read()).hexdigest()[:8]
        hit = False
        for it in d.get('items', []):
            if it.get('path') == rel and it.get('sha8') != h:
                it['sha8'] = h
                hit = True
        if hit:
            json.dump(d, open(mp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except Exception:
        pass



def _save_approved(hashes, blacklist):
    """批准表的唯一写入口：一次 dump + 一次对齐清单 sha8。

    以前三处各写一遍 dump、各跟一句刷新；来第四条分支就必忘——
    Q08 的病就是"表改了、清单没跟上"，然后被当成坏必需件。
    写与刷新绑死，忘记不可能再发生。
    """
    hs = sorted(set(hashes))
    json.dump({'approved_at': _today(), 'count': len(hs), 'hashes': hs,
               'platform_blacklist': blacklist},
              open(APPROVED_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    _refresh_manifest_sha('探针批准.json')


def cmd_approve(args=None):
    """登记探针批准清单。默认只列"待批准"，不写文件；加 --yes 才落盘。

    为什么这么改：批准清单的源头是卡片，而卡片谁都能写。
    若 approve 直接全部登记，就等于"写卡的人自己给自己签通行证"——RCE 防线形同虚设。
    人工确认这一步不能省：批准 = 允许这段命令在本机执行。
    """
    import glob as _g
    cur = {}
    try: cur = set(json.load(open(APPROVED_FILE, encoding='utf-8')).get('hashes', []))
    except Exception: cur = set()
    try: _bl = json.load(open(APPROVED_FILE, encoding='utf-8')).get('platform_blacklist', {})
    except Exception: _bl = {}

    if '--revoke' in (args or []):
        tgt = [a for a in args if a != '--revoke']
        gone = []
        for t in tgt:
            if t in cur:
                cur.discard(t); gone.append(t); continue
            f = _card_path(t[:-3] if t.endswith('.md') else t)
            if os.path.exists(f):
                m = re.search(r'^探针[:：](.*)$', open(f, encoding='utf-8', errors='replace').read(), re.M)
                if m:
                    h = _h8(m.group(1).split('|')[0].strip())
                    if h in cur: cur.discard(h); gone.append(t + '(' + h + ')')
        _bl = {k: v for k, v in _bl.items() if k not in set(tgt)}
        _save_approved(cur, _bl)
        print('已撤销 ' + str(len(gone)) + ' 条：' + ('、'.join(gone) if gone else '（没找到对应条目）'))
        print('提示：被撤销的探针下次 build_index 会拒收，该卡仍可检索，只是判据不再执行。')
        return
    rows = []
    for f in _card_files():
        if os.path.islink(f): continue
        try: t = open(f, encoding='utf-8', errors='replace').read()
        except Exception: continue
        m = re.search(r'^探针[:：](.*)$', t, re.M)
        if not m: continue
        cmd = m.group(1).split('|')[0].strip()
        if cmd: rows.append((os.path.basename(f)[:-3], cmd, _h8(cmd)))
    pend = [r for r in rows if r[2] not in cur]
    if not pend:
        if '--yes' in (args or []):
            hs = sorted(set(r[2] for r in rows))
            drop = len(cur) - len([h for h in hs if h in cur])
            _save_approved(hs, _bl)
            print('没有待批准的探针；已按当前库重写清单：' + str(len(hs)) + ' 条'
                  + ('（清掉不再属于本库的 ' + str(drop) + ' 条）' if drop else ''))
            return
        print('没有待批准的探针（当前 ' + str(len(cur)) + ' 条已批准）'); return
    print('待批准 ' + str(len(pend)) + ' 条。批准 = 允许它在本机执行，请逐条看清：')
    for name, cmd, h in pend[:15]:
        print('  [' + h + '] ' + name.ljust(40) + cmd[:70])
    if len(pend) > 15: print('  … 另有 ' + str(len(pend) - 15) + ' 条')
    if '--yes' not in (args or []):
        print('\n未写入。确认没问题再加 --yes：recall.py approve --yes')
        print('拒绝的写法：只把该卡探针行删掉，再 approve --yes（未列入清单的命令 build_index 会拒收）')
        return
    hs = [r[2] for r in rows]
    _save_approved(hs, _bl)
    print('已批准 ' + str(len(set(hs))) + ' 条（其中本次新增 ' + str(len(pend)) + ' 条）')


def cmd_mem(args=None):
    """记忆模块入口：参数原样转给 core（记忆程序），数据目录固定指向本库的 memory/。

    为什么是转发而不是把程序并进来：记忆程序自带一套独立模块（core/），
    并进主程序要重写它的接口层，风险大于收益；转发等于"一个命令、两个引擎"，
    用户只需要记 recall.py 这一个入口。
    """
    import subprocess
    # v6.54：`mem record` 走项目层（带三段戳、要 project 字段），不是 core 的事件型写入。
    # 分流放在这里而不是 core 里：core 是"记一次任务"，项目是"记到哪条线上"，两件事。
    if args and args[0] == 'record':
        sys.exit(_proj_layer().cmd_record(list(args[1:])))
    skill = os.path.dirname(os.path.abspath(__file__))
    memdir = os.path.join(ROOT, 'memory')
    core = os.path.join(skill, 'core')
    if not os.path.isdir(memdir):
        print('记忆数据目录不在：' + memdir)
        print('补救：确认 experience/memory/ 随包落地了没有；没有就从云盘包重装。')
        sys.exit(2)
    if not os.path.isdir(core):
        print('记忆程序不在：' + core)
        print('补救：确认 .user_skills/experience-recall/core/ 随包落地了没有。')
        sys.exit(2)
    env = dict(os.environ, PYTHONIOENCODING='utf-8', MEMORY_INDEX_DIR=memdir)
    p = subprocess.run([sys.executable, '-m', 'core', '--dir', memdir] + list(args or []),
                       cwd=skill, env=env)
    sys.exit(p.returncode)


# ============================================================
# 资产册（数字资产管理）：技能 / 工具 / 知识库的台账
# 设计底线：台账数据一律由程序从磁盘现场扫，人不写"几版、几个文件"，
# 人只写"外部锚"和"为什么"。手写的版本记录必然与实物脱节。
# ============================================================

import time
import socket

ASSET_DIRNAME = '资产册'
ASSET_KB_HINTS = ('knowledge-base', '知识库', '知识库素材', 'kb')
ASSET_SKIP = ('__pycache__', '.git', 'node_modules', '.state_snapshots')


def _asset_dir():
    d = os.path.join(ROOT, ASSET_DIRNAME)
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def _asset_write(path, text):
    tmp = path + '.tmp%d' % os.getpid()
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def _asset_front(txt):
    """解析 SKILL.md 的 YAML front matter，支持 description: >- 多行折叠块。"""
    lines = txt.splitlines()
    if not lines or lines[0].strip() != '---':
        return '', '', ''
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ('---', '...'):
            end = i
            break
    if end is None:
        return '', '', ''
    fm = lines[1:end]
    fields = {}
    i = 0
    while i < len(fm):
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$', fm[i])
        if not m:
            i += 1
            continue
        k = m.group(1)
        v = m.group(2).strip()
        blk = []
        j = i + 1
        while j < len(fm) and (fm[j].strip() == '' or fm[j].startswith((' ', '\t'))):
            blk.append(fm[j].strip())
            j += 1
        if v in ('>', '>-', '|', '|-'):
            fields[k] = re.sub(r'\s+', ' ', ' '.join(x for x in blk if x)).strip()
            i = j
        else:
            fields[k] = v.strip('"').strip("'").strip()
            i += 1
    return fields.get('name', ''), fields.get('version', ''), fields.get('description', '')


def _asset_size(p):
    n = 0
    b = 0
    newest = 0
    for cur, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in ASSET_SKIP]
        for fn in files:
            fp = os.path.join(cur, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            n += 1
            b += st.st_size
            if st.st_mtime > newest:
                newest = st.st_mtime
    return n, b, newest


def _asset_tree(p):
    """目录内容指纹：按相对路径排序，逐文件算 sha256 再合并。

    为什么不用 mtime：拷贝、解压、跨机器复制都会改 mtime，那不代表内容变了。
    v6.28 第一次在云主机上跑，42 个技能全报"内容改过"，全是 mtime 造成的假阳性。
    """
    rels = []
    for cur, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in ASSET_SKIP]
        for fn in files:
            fp = os.path.join(cur, fn)
            rels.append((os.path.relpath(fp, p).replace(os.sep, '/'), fp))
    acc = hashlib.sha256()
    for rel, fp in sorted(rels):
        acc.update(rel.encode('utf-8') + b'\0')
        try:
            with open(fp, 'rb') as f:
                acc.update(hashlib.sha256(f.read()).digest())
        except OSError:
            acc.update(b'?')
    return acc.hexdigest()[:16]


def _asset_roots():
    skill = os.path.dirname(os.path.abspath(__file__))
    ub = os.path.dirname(skill)
    wb = os.path.dirname(ub)
    plat = os.path.join(wb, '.skills')
    if not os.path.isdir(plat):
        # 库和平台技能目录不一定同层（云主机上库在 /runtime，平台技能在 ~/.doubao/...）。
        # 不补这一步，平台那批技能会被整批漏掉，账看着"只有自建技能"。
        cand = os.path.expanduser(os.path.join('~', '.doubao', 'agent_mode', 'workspace', '.skills'))
        if os.path.isdir(cand):
            plat = cand
    if os.path.realpath(plat) == os.path.realpath(ub):
        return [('自建技能', ub)]
    return [('自建技能', ub), ('平台技能', plat)]


def _asset_walk():
    skills = []
    extra = []
    for kind, root in _asset_roots():
        if not os.path.isdir(root):
            continue
        for d in sorted(os.listdir(root)):
            full = os.path.join(root, d)
            if not os.path.isdir(full):
                continue
            sm = os.path.join(full, 'SKILL.md')
            n, b, nw = _asset_size(full)
            if not os.path.isfile(sm):
                extra.append({'kind': kind, 'dir': d, 'path': full,
                              'files': n, 'bytes': b, 'mtime': nw,
                              'why': '根目录下的共享资源目录，不是技能'})
                continue
            try:
                raw = open(sm, encoding='utf-8', errors='replace').read()
            except OSError:
                raw = ''
            nm, ver, desc = _asset_front(raw)
            purpose = re.split(r'[。；;]', re.sub(r'\s+', ' ', desc or ''))[0].strip()[:90]
            skills.append({'kind': kind, 'dir': d, 'name': nm or d, 'version': ver,
                           'purpose': purpose, 'path': full,
                           'files': n, 'bytes': b, 'mtime': nw,
                           'tree_sha': _asset_tree(full)})
    return skills, extra


def _asset_kbs(skills):
    out = []
    for it in skills:
        try:
            subs = sorted(os.listdir(it['path']))
        except OSError:
            continue
        for sub in subs:
            if sub.lower() in ASSET_KB_HINTS:
                sp = os.path.join(it['path'], sub)
                if os.path.isdir(sp):
                    n, b, nw = _asset_size(sp)
                    out.append({'form': '随技能自带', 'host': it['dir'], 'name': sub,
                                'path': sp, 'files': n, 'bytes': b, 'mtime': nw, 'rooms': None})
    return out


def _asset_anchors():
    p = os.path.join(_asset_dir(), '资产锚.json')
    if not os.path.isfile(p):
        return {}
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return {}


def _asset_load_prev():
    p = os.path.join(_asset_dir(), '资产册.json')
    if not os.path.isfile(p):
        return None
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return None


def _asset_ext_path():
    return os.path.join(_asset_dir(), '外部知识库.json')


def _asset_kb_rooms(path):
    p = os.path.join(path, 'index_total.json')
    if not os.path.isfile(p):
        return None
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        return None
    return len(d.get('rooms', [])) if isinstance(d, dict) else None


def _asset_ext_kbs(existing):
    """读"登记在外的知识库"清单：技能目录扫不到的知识库，靠人登记位置。"""
    p = _asset_ext_path()
    out = []
    if not os.path.isfile(p):
        return out
    try:
        reg = json.load(open(p, encoding='utf-8'))
    except Exception:
        return out
    paths = {k['path'] for k in existing}
    for item in reg:
        path = item.get('path') or ''
        if path in paths:
            continue
        rec = {'form': '登记的位置', 'host': item.get('host', '?'),
               'name': item.get('name') or os.path.basename(path),
               'path': path, 'rooms': item.get('rooms'), 'files': 0, 'bytes': 0, 'mtime': 0}
        if os.path.isdir(path):
            n, b, nw = _asset_size(path)
            rec['files'] = n
            rec['bytes'] = b
            rec['mtime'] = nw
            if item.get('rooms') == 'auto':
                rec['rooms'] = _asset_kb_rooms(path)
        else:
            rec['missing'] = True
            rec['files'] = -1
        out.append(rec)
    return out


def _asset_scan(force=False):
    skills, extra = _asset_walk()
    kbs = _asset_kbs(skills)
    kbs += _asset_ext_kbs(kbs)
    anchors = _asset_anchors()
    prev = _asset_load_prev()
    host = socket.gethostname()
    if prev and not force:
        ph = prev.get('host')
        if ph and ph != host:
            print('这份资产账是「%s」扫的，你现在在「%s」。' % (ph, host))
            print('直接重扫会把它覆盖掉。'
                  '确认要覆盖，加 --force；')
            print('多数情况你只是换台机器看账，用 asset check 就行，不必重扫。')
            return 2

    # 已登记但不在技能目录里的知识库
    for key, a in anchors.items():
        if not a.get('path') or not a.get('is_kb'):
            continue
        if any(k['path'] == a['path'] for k in kbs):
            continue
        if os.path.isdir(a['path']):
            n, b, nw = _asset_size(a['path'])
            kbs.append({'form': '登记的位置', 'host': a.get('host', '?'), 'name': a.get('name', key),
                        'path': a['path'], 'files': n, 'bytes': b, 'mtime': nw, 'rooms': None})
        else:
            kbs.append({'form': '登记的位置', 'host': a.get('host', '?'), 'name': a.get('name', key),
                        'path': a['path'], 'files': -1, 'bytes': 0, 'mtime': 0, 'rooms': None,
                        'missing': True})

    # 与上次扫描比对：内容变了但版本号没变 = 版本号在骗人
    drift = []
    if not prev:
        for s in skills:
            s['ver_sha'] = s.get('tree_sha') or ''
    if prev:
        idx = {(p['kind'], p['dir']): p for p in prev.get('skills', [])}
        for s in skills:
            old = idx.get((s['kind'], s['dir']))
            if not old:
                continue
            if old.get('version') == s['version'] and old.get('ver_sha'):
                # 版本没变：看内容有没有偏离"这个版本号登记时的样子"。
                # 不用"和上次扫描不同"做判据——账只是快照，
                # 加个临时文件再删掉，快照会报"改过"，实际什么都没留下。
                s['ver_sha'] = old['ver_sha']
                if s.get('tree_sha') and s['tree_sha'] != old['ver_sha']:
                    drift.append({'dir': s['dir'], 'kind': s['kind'], 'version': s['version'],
                                  'why': '内容偏离了这个版本号登记时的样子'})
            else:
                # 版本升了（合规），或第一次建基线（老账没有 ver_sha）：以本次内容为准
                s['ver_sha'] = s.get('tree_sha') or ''
        for s in skills:
            s.setdefault('ver_sha', s.get('tree_sha') or '')

    # 锚的键用「类型/目录名」，不用绝对路径：换机器、换安装路径后仍能对上
    for k in kbs:
        k['key'] = 'kb/%s/%s' % (k.get('host') or '?', k.get('name') or '?')
        k['mtime_s'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(k['mtime'])) if k.get('mtime') else ''
        k['anchor'] = anchors.get(k['key'], None)
    for s in skills:
        s['key'] = '%s/%s' % (s['kind'], s['dir'])
        s['mtime_s'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(s['mtime'])) if s.get('mtime') else ''
        s['anchor'] = anchors.get(s['key'], None)
    for e in extra:
        e['mtime_s'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(e['mtime'])) if e.get('mtime') else ''

    data = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'host': host,
        'root': ROOT,
        'skills_total': len(skills),
        'skills_with_version': sum(1 for s in skills if s['version']),
        'skills_without_version': sum(1 for s in skills if not s['version']),
        'selfbuilt_total': sum(1 for s in skills if s['kind'] == '自建技能'),
        'selfbuilt_without_version': sum(1 for s in skills if s['kind'] == '自建技能' and not s['version']),
        'shared_dirs': len(extra),
        'kb_total': len(kbs),
        'drift': drift,
        'skills': skills,
        'shared_resource_dirs': extra,
        'knowledge_bases': kbs,
    }
    _asset_write(os.path.join(_asset_dir(), '资产册.json'),
                 json.dumps(data, ensure_ascii=False, indent=1))

    print('已扫：技能 %d（有版本 %d / 没版本 %d，其中自建没版本 %d）'
          % (len(skills), data['skills_with_version'], data['skills_without_version'],
             data['selfbuilt_without_version']))
    print('      共享资源目录 %d；知识库 %d' % (len(extra), len(kbs)))
    if not prev:
        print('这是第一次扫描，没有对比基线；下次再扫就能看出"改了内容却没升版本"。')
    elif drift:
        print('【要点】%d 个资产内容变过但版本号没变：' % len(drift))
        for d in drift:
            print('   - %s（版本仍为 %s）' % (d['dir'], d['version'] or '无'))
    else:
        print('相比上次扫描：没有"改了内容却没升版本"的资产。')
    print('台账已写：' + os.path.join(_asset_dir(), '资产册.json'))
    return 0


def _asset_rows():
    p = os.path.join(_asset_dir(), '资产册.json')
    if not os.path.isfile(p):
        print('还没有资产册。先跑：asset scan')
        sys.exit(2)
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception as e:
        print('资产册读不了（%s）。重跑：asset scan' % e)
        sys.exit(2)


def _fmt(n):
    if n >= 1048576:
        return '%.1f MB' % (n / 1048576.0)
    if n >= 1024:
        return '%.1f KB' % (n / 1024.0)
    return '%d B' % n


def _asset_list(argv):
    kind = None
    only_no_ver = False
    for i, a in enumerate(argv):
        if a == '--kind' and i + 1 < len(argv):
            kind = argv[i + 1]
        if a == '--no-version':
            only_no_ver = True
    data = _asset_rows()
    rows = data['skills']
    if kind:
        rows = [r for r in rows if r['kind'] == kind]
    if only_no_ver:
        rows = [r for r in rows if not r['version']]
    print('技能 %d 个%s%s' % (len(rows), '（' + kind + '）' if kind else '',
                             '，只看没版本号' if only_no_ver else ''))
    for r in rows:
        print('  %-34s %-10s %-14s %s' % (r['dir'], r['version'] or '—', _fmt(r['bytes']), r['purpose'][:52]))
    return 0


def _asset_search(kw):
    data = _asset_rows()
    kw = kw.lower()
    hits = [r for r in data['skills'] if kw in (r['dir'] + ' ' + r['name'] + ' ' + r['purpose']).lower()]
    kb = [k for k in data['knowledge_bases'] if kw in (k['name'] + ' ' + k['host']).lower()]
    print('技能命中 %d 个 / 知识库命中 %d 处' % (len(hits), len(kb)))
    for r in hits:
        print('  [技能] %-30s %-8s %s' % (r['dir'], r['version'] or '—', r['purpose'][:56]))
    for k in kb:
        print('  [知识库] %-28s %-8s %s' % (k['name'], _fmt(k['bytes']), k['path']))
    return 0


def _asset_check():
    data = _asset_rows()
    anchors = _asset_anchors()
    problems = []
    warn = []
    _owner = data.get('host') or '?'
    _cross = _owner not in ('?', socket.gethostname())

    nv = [s for s in data['skills'] if s['kind'] == '自建技能' and not s['version']]
    if nv:
        problems.append('自建技能没有版本号 %d 个：%s' % (len(nv), '、'.join(s['dir'] for s in nv)))

    bad = [s for s in data['skills'] if s['version'] and not re.fullmatch(r'v?\d+[.\d]*', s['version'])]
    if bad:
        problems.append('版本号写法不规范 %d 个：%s' % (len(bad), '、'.join('%s=%s' % (s['dir'], s['version']) for s in bad)))

    for d in data.get('drift', []):
        if _cross:
            # 账是别的机器扫的，本机副本和它不同很正常，不能当成"有人偷改没升版"
            warn.append('%s 本机内容与账不同（账是「%s」扫的，版本仍为 %s）'
                        % (d['dir'], _owner, d['version'] or '无'))
        else:
            problems.append('%s 内容改过但版本号还是 %s' % (d['dir'], d['version'] or '无'))

    missing = [k for k in data['knowledge_bases'] if k.get('missing')]
    for k in missing:
        if k.get('form') == '随技能自带':
            # 技能目录里该有的知识库没了，那是技能装坏了，必须处理
            problems.append('技能自带的知识库不见了：%s（%s）' % (k['name'], k['path']))
        elif _cross:
            warn.append('知识库位置在本机不存在：%s（%s，这是「%s」上的路径）'
                        % (k['name'], k['path'], _owner))
        else:
            warn.append('登记的知识库在本机找不到：%s（%s）—— 可能装在别的机器上，也可能已搬走'
                        % (k['name'], k['path']))

    no_anchor = [s for s in data['skills'] if s['kind'] == '自建技能' and not s.get('anchor')]
    no_anchor_kb = [k for k in data['knowledge_bases'] if not k.get('anchor')]
    if no_anchor or no_anchor_kb:
        warn.append('没有外部锚：自建技能 %d 个、知识库 %d 处。没锚就无法判断"它跟外部世界还对不对得上"'
                    % (len(no_anchor), len(no_anchor_kb)))

    today = time.time()
    stale = []
    for path, a in anchors.items():
        asof = a.get('asof') or ''
        if not asof:
            continue
        try:
            t = time.mktime(time.strptime(asof, '%Y-%m-%d'))
        except ValueError:
            continue
        days = (today - t) / 86400.0
        if days > 180:
            stale.append('%s（锚定于 %s，已 %.0f 天）' % (a.get('name') or path, asof, days))
    if stale:
        warn.append('锚定超过半年未复核 %d 处：%s' % (len(stale), '、'.join(stale)))

    me = socket.gethostname()
    owner = data.get('host') or '?'
    print('资产体检：技能 %d / 知识库 %d / 已登记锚 %d'
          % (len(data['skills']), len(data['knowledge_bases']), len(anchors)))
    if owner not in ('?', me):
        print('  注：这份账是「%s」扫的，不是本机「%s」的现状；'
              '要看本机实况就 asset scan --force' % (owner, me))
    if problems:
        print('【必须处理】%d 条' % len(problems))
        for p in problems:
            print('  × ' + p)
    if warn:
        print('【该知道】%d 条' % len(warn))
        for w in warn:
            print('  ! ' + w)
    if not problems and not warn:
        print('干净：没有发现问题。')
        return 0
    if problems:
        return 1
    return 0


def _asset_anchor(argv):
    if not argv:
        print('用法：asset anchor <资产目录名> --source <外部来源> [--asof YYYY-MM-DD] [--state <当时外部状态>]')
        return 2
    target = argv[0]
    src = ''
    asof = ''
    state = ''
    path = ''
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--source' and i + 1 < len(argv):
            src = argv[i + 1]; i += 2; continue
        if a == '--asof' and i + 1 < len(argv):
            asof = argv[i + 1]; i += 2; continue
        if a == '--state' and i + 1 < len(argv):
            state = argv[i + 1]; i += 2; continue
        if a == '--path' and i + 1 < len(argv):
            path = argv[i + 1]; i += 2; continue
        i += 1
    if not src:
        print('必须给 --source：这个资产的外面对照物是什么（官网地址 / 版本号 / 文件来源）')
        return 2

    data = _asset_rows()
    hit = None
    for s in data['skills']:
        if s['dir'] == target or s['name'] == target:
            hit = s; break
    if hit is None:
        for k in data['knowledge_bases']:
            if k['name'] == target or k['path'] == target:
                hit = k; break
    if hit is None:
        print('资产册里没有这个资产：' + target)
        print('先跑 asset list 看准确名字，或 asset scan 重扫。')
        return 2

    if not asof:
        asof = time.strftime('%Y-%m-%d')
    key = hit.get('key')
    if not key:
        if hit.get('dir'):
            key = '%s/%s' % (hit.get('kind'), hit['dir'])
        else:
            key = 'kb/%s/%s' % (hit.get('host') or '?', hit.get('name') or '?')
    entry = {
        'key': key,
        'name': hit.get('name') or target,
        'host': hit.get('host') or hit.get('dir'),
        'path': hit.get('path') or path,
        'is_kb': hit.get('form') is not None,
        'source': src,
        'asof': asof,
        'state_at_asof': state,
        'registered_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    anchors = _asset_anchors()
    old = anchors.get(key)
    anchors[key] = entry
    _asset_write(os.path.join(_asset_dir(), '资产锚.json'),
                 json.dumps(anchors, ensure_ascii=False, indent=1))
    print(('已更新锚：' if old else '已登记锚：') + entry['name'])
    print('  外部来源：' + src)
    print('  锚定日期：%s%s' % (asof, ('，当时状态：' + state) if state else ''))
    print('  下次判断新旧时，拿这个日期和外部现状对，不看"预计到期"。')
    return 0


def _asset_kb_add(argv):
    if not argv:
        print('用法：asset kb-add <名字> --path <绝对路径> [--host <宿主说明>]')
        return 2
    name = argv[0]
    path = ''
    host = ''
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--path' and i + 1 < len(argv):
            path = argv[i + 1]; i += 2; continue
        if a == '--host' and i + 1 < len(argv):
            host = argv[i + 1]; i += 2; continue
        i += 1
    if not path:
        print('必须给 --path：这个知识库在硬盘上的绝对路径')
        return 2
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        print('路径不存在：' + path)
        return 2
    p = _asset_ext_path()
    reg = []
    if os.path.isfile(p):
        try:
            reg = json.load(open(p, encoding='utf-8'))
        except Exception:
            reg = []
    reg = [x for x in reg if x.get('path') != path]
    reg.append({'name': name, 'path': path, 'host': host or '?', 'rooms': 'auto',
                'registered_at': time.strftime('%Y-%m-%d %H:%M:%S')})
    _asset_write(p, json.dumps(reg, ensure_ascii=False, indent=1))
    print('已登记知识库：%s → %s' % (name, path))
    print('下次 asset scan 会自动纳入，并统计文件数与最后改动时间。')
    return 0


def cmd_asset(argv):
    act = argv[0] if argv else ''
    if act == 'scan':
        sys.exit(_asset_scan('--force' in argv))
    if act == 'list':
        sys.exit(_asset_list(argv[1:]))
    if act in ('search', 'find'):
        if len(argv) < 2:
            print('用法：asset search <关键词>')
            sys.exit(2)
        sys.exit(_asset_search(argv[1]))
    if act == 'check':
        sys.exit(_asset_check())
    if act == 'anchor':
        sys.exit(_asset_anchor(argv[1:]))
    if act == 'kb-add':
        sys.exit(_asset_kb_add(argv[1:]))
    print('资产册命令：')
    print('  asset scan [--force]       扫盘重建资产册，并报"改了内容没升版本"；账来自别的机器时要 --force')
    print('  asset list [--kind X] [--no-version]   列出技能')
    print('  asset search <词>          按名字/用途找资产')
    print('  asset check                体检（缺版本/漂移/无锚/锚过期），有问题 exit 1')
    print('  asset anchor <名> --source <外部来源> [--asof 日期] [--state 当时状态]')
    print('  asset kb-add <名> --path <绝对路径>   登记技能目录外的知识库')
    print('')
    print('为什么用 scan 而不是手填：手写的版本记录会跟实物脱节，库里已有教训卡。')
    sys.exit(0 if not act else 2)


def cmd_packcheck(argv):
    """打包后全量对拍：包内件数 vs 库内件数（D162 的落地）。

    为什么必须全量：部署清单只列 13 件，而包里有 326 件——memory/、资产册/、
    .state_snapshots/、探针执行台账这些**不在清单里**，deploy_check 永远不会
    报它们。D162 那 50 件缺失就是从这个缝里漏掉的：清单说"齐了"，实际缺一半。

    判据：**缺件数 == 0** 才过（exit 0/5 与 deploy_check 同码）。库比包"多"
    是允许的——那是本轮新增，只报告不拦。同名不同大小也报告：可能是本轮写新了，
    也可能像今天的使用台账那样被截断成 88 字节，得人看一眼。
    """
    import zipfile, argparse
    ap = argparse.ArgumentParser(prog='recall.py packcheck')
    ap.add_argument('--zip', required=True, help='要核对的包')
    ap.add_argument('--root', default=ROOT, help='库根，默认当前库')
    ap.add_argument('--prefix', default='experience/', help='包内库目录前缀')
    ap.add_argument('--verbose', action='store_true', help='列出"库多"的明细')
    ns = ap.parse_args(argv)

    try:
        z = zipfile.ZipFile(ns.zip)
    except Exception as e:
        print('PACKCHECK FAIL：包打不开 → %s' % e); sys.exit(2)

    pkg = {}
    for i in z.infolist():
        if i.is_dir() or not i.filename.startswith(ns.prefix):
            continue
        pkg[i.filename[len(ns.prefix):]] = i.file_size
    if not pkg:
        print('PACKCHECK FAIL：包内 "%s" 前缀下一件都没有，前缀写错了？' % ns.prefix); sys.exit(2)

    cur = {}
    for base, _d, fs in os.walk(ns.root):
        for f in fs:
            p = os.path.join(base, f)
            cur[os.path.relpath(p, ns.root)] = os.path.getsize(p)

    # v6.55 修：Windows 上 os.path.relpath 返回反斜杠，zip 内是正斜杠，
    # 不归一化就会把"同一件"报成"包有库无"（本轮实测误报 40 件快照）。
    norm = lambda k: k.replace('\\', '/')
    pkg = {norm(k): v for k, v in pkg.items()}
    cur = {norm(k): v for k, v in cur.items()}

    missing = sorted(k for k in pkg if k not in cur)

    extra = sorted(k for k in cur if k not in pkg)
    size_diff = sorted((k, pkg[k], cur[k]) for k in pkg if k in cur and pkg[k] != cur[k])

    print('PACKCHECK：包内 %d 件 / 库内 %d 件（%s）' % (len(pkg), len(cur), ns.root))
    if missing:
        print('  缺件 %d（包有库无，必拦）：' % len(missing))
        for k in missing[:20]:
            print('    %-58s %8dB' % (k, pkg[k]))
        if len(missing) > 20:
            print('    ……另 %d 件' % (len(missing) - 20))
    if extra:
        print('  库多 %d（本轮新增，只报告%s）' % (len(extra), '' if ns.verbose else '，--verbose 看明细'))
        if ns.verbose:
            for k in extra[:20]:
                print('    %s' % k)
    if size_diff:
        print('  同名不同大小 %d（可能写新了，也可能被截断，看一眼）：' % len(size_diff))
        for k, a, b in size_diff[:12]:
            print('    %-50s 包%8dB 库%8dB' % (k, a, b))
        if len(size_diff) > 12:
            print('    ……另 %d 件' % (len(size_diff) - 12))

    if missing:
        print('PACKCHECK NOT READY：缺 %d 件——这个包装上去就是缺胳膊少腿的' % len(missing))
        sys.exit(5)
    print('PACKCHECK PASS：该在的一件不缺')
    return 0


def _proj_layer():
    """项目层（记忆域）按需加载：它 import 本模块复用 ROOT/锁/原子写，不能在顶层互相 import。"""
    import project_layer
    project_layer.R = sys.modules[__name__]
    return project_layer


# v6.60 只读模式：给"只该检索、不该记账"的机器用（云主机就是这么一台）。
# 为什么需要它：缺陷号是"本地最大+1"，没有全局唯一性。云主机本地号池落后，
# 它发出来的号主库可能已经用掉——2026-11-23 就撞过一次 D247，两条不同内容同号，
# 最后靠 restore 覆盖统一，被覆盖的那条差点静默消失。
# 所以这台机器不是"不该装"，是"不该写"。读一律放行，写一律拒，并在拒绝时说清去哪写。
_RO_WRITE_CMDS = {'log', 'new', 'retire', 'restore', 'mark', 'use', 'archive', 'claim',
                  'reap', 'project', 'handover', 'repro', 'watch', 'task', 'state', 'pref',
                  'approve', 'sync', 'mem', 'asset', 'guard',
                  'recur', 'review', 'export'}   # gate/eval 只跑探针写临时根，不落账，放行


def _ro_active():
    """只读开关：库根有 .readonly，或环境变量 EXPRECALL_READONLY=1。
    两个例外：①显式 EXPRECALL_READONLY=0 关掉；②探针进程（有 EXPRECALL_PARENT_ROOT）豁免——
    闸把真库 cp 到临时根时会把 .readonly 一起带走，不豁免的话只读机上每根写类探针都会被误伤成红。"""
    v = os.environ.get('EXPRECALL_READONLY')
    if v == '0':
        return False
    if v == '1':
        return True
    if os.environ.get('EXPRECALL_PARENT_ROOT'):
        return False          # 探针跑在临时库：闸要验"行为对不对"，不是验"只读挡不挡"
    return os.path.exists(os.path.join(ROOT, '.readonly'))


def _ro_blocks(c, argv):
    """True = 这次调用会写库/写号，只读模式下应拒。双态命令按子命令判，不按命令粗判。"""
    if c not in _RO_WRITE_CMDS:
        return False
    sub = argv[0] if argv else ''
    if c == 'log' and sub == 'status':
        return False
    if c == 'guard' and sub in ('list', 'check', 'pending', 'diff'):
        return False
    if c == 'pref' and sub in ('list', 'show', 'audit', 'profile') and '--save' not in argv:
        return False
    if c == 'approve' and not ('--yes' in argv or '--revoke' in argv):
        return False
    if c == 'sync' and sub == 'diff':
        return False
    if c == 'mem' and sub in ('search', 'get', 'list'):
        return False
    if c == 'asset' and sub in ('list', 'search', 'check'):
        return False
    if c == 'task' and '--append' not in argv:
        return False
    if c == 'state' and not argv:
        return False
    if c == 'export':
        return False          # 写到调用方指定文件，不动库
    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    c = sys.argv[1]; a = sys.argv[2] if len(sys.argv) > 2 else ''
    if _ro_active() and _ro_blocks(c, sys.argv[2:]):
        print('⛔ 只读副本，不记账：%s' % ROOT)
        print('   这台机器的缺陷号池落后主库，它发出的号可能已被占用（2026-11-23 撞过 D247）。')
        print('   记账请到主库所在机器（Windows：%USERPROFILE%\\experience）跑同一条命令。')
        print('   检索/查询不受影响（search / card / match / trace / stats / log status 照常可用）。')
        print('   解除只读：删掉 %s，或设 EXPRECALL_READONLY=0。' % os.path.join(ROOT, '.readonly'))
        sys.exit(9)
    _reap_stale_locks()
    if c == 'state': return cmd_state_sec(sys.argv[2:])
    if c == 'task': return cmd_task(sys.argv[2:])
    if c == 'search' and a: return cmd_search(a)
    if c == 'card' and a: return cmd_card(a)
    if c == 'match' and a: return cmd_match(a)
    if c == 'flow': return cmd_flow(a)
    if c == 'log': return cmd_log(sys.argv[2:])
    if c == 'packcheck': return cmd_packcheck(sys.argv[2:])
    if c == 'event' and a: return cmd_event(a)
    if c == 'hit' and a: return cmd_hit(a)
    if c in ('search', 'card', 'match', 'hit') and not a:
        print('需要参数：' + c + ' <文本>'); return
    if c == 'event' and not a:
        print('需要参数：event <event_id>'); return
    if c == 'new' and a: return cmd_new(a)
    if c == 'new' and not a: print('用法：new <标题>'); return
    if c == 'pref': return cmd_pref(sys.argv[2:])
    if c == 'approve': return cmd_approve(sys.argv[2:])
    if c == 'gate': return cmd_gate()
    if c == 'review': return cmd_review()
    if c == 'recur' and a: return cmd_recur(a)
    if c == 'claim': return cmd_claim(sys.argv[2:])
    if c == 'guard': return cmd_guard(sys.argv[2:])
    if c == 'restore': return cmd_restore(sys.argv[2:])
    if c == 'retire': return cmd_retire(sys.argv[2:])
    if c == 'eval': return cmd_eval(sys.argv[2:])
    if c == 'project': return _proj_layer().cmd_project(sys.argv[2:])
    if c == 'handover': return _proj_layer().cmd_handover(sys.argv[2:])
    if c == 'evidence': return cmd_evidence(sys.argv[2:])
    if c == 'trace': return cmd_trace(sys.argv[2:])
    if c == 'provenance': return cmd_provenance(sys.argv[2:])
    if c == 'sync': return cmd_sync(sys.argv[2:])
    if c == 'export': return cmd_export(a)
    if c == 'diff': return cmd_diff(a)
    if c == 'mark': return cmd_mark(sys.argv[2:])
    if c == 'use': return cmd_use(sys.argv[2:])
    if c == 'activity': return cmd_activity()
    if c == 'archive': return cmd_archive(sys.argv[2:])
    if c == 'probelog': return cmd_probelog()
    if c == 'plan': return cmd_plan(sys.argv[2] if len(sys.argv) > 2 else '10')
    if c == 'repro': return cmd_repro(sys.argv[2:])
    if c == 'reap': return cmd_reap(sys.argv[2:])
    if c == 'watch': return cmd_watch(sys.argv[2:])
    if c == 'estate': return cmd_estate()
    if c == 'scan': return cmd_scan(a)
    if c == 'scan' and not a: return cmd_scan('')
    if c == 'mem': return cmd_mem(sys.argv[2:])
    if c == 'asset': return cmd_asset(sys.argv[2:])
    if _lib_missing() and c not in ('restore', 'where'):
        if _bootstrap_from_seed() and not _lib_missing():
            pass
        else:
            print('⛔ 没找到库：%s 不像一座库（没有台账/状态文件）。' % ROOT)
            print('   这不是"库是空的"，是"库不在这里"。先按权威包落库：')
            print('   python recall.py restore --from <包.zip> --root %s' % os.path.join(HOME, 'experience'))
            print('   诊断：python recall.py where')
            sys.exit(5)
    if c in CMDS: return CMDS[c]()
    print('未知命令：' + c); print(__doc__); sys.exit(2)

try:
    # 退出码必须由外层交代清楚：`main()` 的返回值以前被直接丢掉，
    # 于是"函数里 return 1"等于没发生（D171/D169 的老病，第三次踩）。
    # 命令自己 sys.exit 的仍按它自己的码走；return 一个整数的，这里替它升成退出码。
    _rc = main()
    if isinstance(_rc, int) and _rc:
        sys.exit(_rc)
except BrokenPipeError:
    try: sys.stdout.close()
    except Exception: pass









