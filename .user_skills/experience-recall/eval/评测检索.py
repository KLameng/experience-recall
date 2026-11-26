#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索质量评测（经验卡库）。这不是"能不能用"，是"问得准不准"。

口径（2026-11-18 定，来源见 缺陷台账 D207）：
  样本 A：30 句人话改写（不看卡片措辞，按"局外人怎么问"写），目标卡已知
  样本 B：20 句库外问题（库里确实没有对应卡），该判"没命中"
  指标：强命中率、强+弱命中率、强首位率、库外强误命中数、库外弱误命中数
  底线：强 ≥60%、强+弱 ≥85%、强首位 ≥60%、库外强误 ≤15%、库外弱误 ≤25%

为什么要分强/弱两层：只调一个阈值时，"召回高"必然"噪声高"。
强匹配给结论，弱匹配只给线索并标明"未必对得上"——确切归确切，不明确画边界。
"""
import io, contextlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)
# 评测不是"人的使用"：本脚本批量喂 50 句，写进使用台账就是一串 pending 噪声。
# 实测诊断脚本各跑 3 次就给台账灌进 60 条同句，把"这张卡用过没用过"淹掉。
os.environ['EXPRECALL_NO_RECORD'] = '1'
import importlib.util
spec = importlib.util.spec_from_file_location('rc', os.path.join(SKILL, 'recall.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FLOOR = {'strong': 60.0, 'both': 85.0, 'top1': 60.0, 'false_strong': 15.0, 'false_weak': 25.0}
DISTRACTORS = [
    'Rust 里怎么用 tokio 写一个 WebSocket 服务端', '婚礼现场逆光怎么布灯', '高铁票改签后退票收多少手续费',
    'Photoshop 怎么把头发抠干净', '结婚证公证需要哪些材料', '股票 K 线里的黄昏之星形态怎么认',
    '北京小客车指标摇号中签率', '用 Docker 部署 Next.js 要几步', '烤箱烤戚风蛋糕为什么塌腰',
    '基金定投和一次性买入哪个好', '驾照科目二倒车入库技巧', '猫咪驱虫药多久喂一次',
    '颈椎病做什么运动好', '手机电池健康度怎么查', 'React useEffect 依赖数组怎么写',
    '三体里黑暗森林法则是什么意思', '红酒开了没喝完怎么保存', '羽毛球拍磅数怎么选',
    '如何用 Pandas 合并两个表', '上海到杭州高铁多久',
]


def run(q):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.cmd_search(q)
    t = buf.getvalue()
    return re.findall(r'^  - (\S+\.md)', t, re.M), re.findall(r'^  \? (\S+\.md)', t, re.M)


def main():
    probes = json.load(open(os.path.join(HERE, '检索评测集_v1.json'), encoding='utf-8'))
    n = len(probes)
    strong = both = top1 = 0
    for p in probes:
        f = p['expect'] if p['expect'].endswith('.md') else p['expect'] + '.md'
        s, w = run(p['q'])
        if f in s:
            strong += 1
            if s.index(f) == 0:
                top1 += 1
        if f in s or f in w:
            both += 1
    fs = sum(1 for q in DISTRACTORS if run(q)[0])
    fw = sum(1 for q in DISTRACTORS if run(q)[1])
    a, b, c = 100.0 * strong / n, 100.0 * both / n, 100.0 * top1 / n
    ds, dw = 100.0 * fs / len(DISTRACTORS), 100.0 * fw / len(DISTRACTORS)
    print('样本 A %d 句 / B %d 句' % (n, len(DISTRACTORS)))
    print('强命中 %.1f%%（底线 ≥%.0f%%）' % (a, FLOOR['strong']))
    print('强+弱命中 %.1f%%（底线 ≥%.0f%%）' % (b, FLOOR['both']))
    print('强首位 %.1f%%（底线 ≥%.0f%%）' % (c, FLOOR['top1']))
    print('库外强误命中 %d/%d = %.0f%%（底线 ≤%.0f%%）' % (fs, len(DISTRACTORS), ds, FLOOR['false_strong']))
    print('库外弱误命中 %d/%d = %.0f%%（底线 ≤%.0f%%）' % (fw, len(DISTRACTORS), dw, FLOOR['false_weak']))
    ok = a >= FLOOR['strong'] and b >= FLOOR['both'] and c >= FLOOR['top1'] and ds <= FLOOR['false_strong'] and dw <= FLOOR['false_weak']
    print(('评测 PASS：检索能接住人话，且不瞎给' if ok else '评测 FAIL：低于底线，别发版'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
