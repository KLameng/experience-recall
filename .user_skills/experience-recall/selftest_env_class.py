#!/usr/bin/env python3
"""自检：环境资源异常必须与业务失败分开计数（闸应 exit 5，而不是 1）。

为什么单独成文件：这条自检要在临时库里造一张"探针报 Errno 28"的卡，
卡片里的字段分隔符是 `|`，塞进 python -c 的单行命令里引号必然打架。
写成文件，逻辑清楚，也便于人肉复现（`python3 selftest_env_class.py`）。
"""
import os, sys, tempfile, subprocess, shutil

SK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recall.py')
CARD = ('---\nname: env-bad\ndescription: 模拟写盘失败\ncreated_at: 2026-11-13\n---\n\n'
        '## 判据\n\n可执行：\n探针: python3 -c "import sys;sys.stderr.write(\'Errno 28 No space left on device\');sys.exit(1)"'
        ' | 期望>=1 | 说明=模拟环境资源异常\n')

def main():
    d = tempfile.mkdtemp(prefix='selftest_env_')
    try:
        e = os.path.join(d, 'e'); os.makedirs(e)
        open(os.path.join(e, 'env-bad.md'), 'w', encoding='utf-8').write(CARD)
        env = dict(os.environ, EXP_ROOT=e, EXPRECALL_DEPTH='0')
        env.pop('EXPRECALL_PARENT_ROOT', None)
        subprocess.run([sys.executable, SK, 'approve', '--yes'], env=env, capture_output=True)
        subprocess.run([sys.executable, SK, 'build_index'], env=env, capture_output=True)
        r = subprocess.run([sys.executable, SK, 'gate'], env=env, capture_output=True, text=True)
        if r.returncode == 5:
            print(1, end=''); return 0
        print('0', end='')
        print('\n[自检失败] 期望 exit 5（环境异常），实际 exit ' + str(r.returncode), file=sys.stderr)
        print((r.stdout or '')[-400:], file=sys.stderr)
        return 1
    finally:
        # 不走 rmtree(ignore_errors=True)：那条会静默放弃清理，正好是 D61 的坑
        for _ in range(3):
            if not os.path.exists(d): break
            try: shutil.rmtree(d)
            except Exception:
                for _r, _dd, _ff in os.walk(d):
                    for _f in _ff:
                        try: os.chmod(os.path.join(_r, _f), 0o600)
                        except Exception: pass

if __name__ == '__main__':
    sys.exit(main())
