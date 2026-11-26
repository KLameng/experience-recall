# -*- coding: utf-8 -*-
"""验闸时临时把云盘权威包摆成 <PKG>，跑完立刻删。

为什么要有这个：探针里有一批要断言“包的件数/清单哈希”，所以闸必须有 `<PKG>`。
但 `<PKG>` 的惯例做法是“放一个工具包 zip 的目录”，于是产生两个坑：
①留一份散落副本在 /tmp 或 chats 里，违“云盘权威版一份”的铁律；
②不留副本，闸就 exit 5，而 exit 5 看起来像“环境问题”，实际是“一根探针都没跑”（D101）。
这个脚本把两头都堵上：用时从云盘取，用完当场删，任何情况下都不在磁盘上留第二份。

用法：python gate_with_pkg.py [gate 的额外参数]
退出码原样透传 gate 的（0/1/2/3/4/5/6/7）。
"""
import os, shutil, subprocess, sys, tempfile

TOKEN = "UFG8beGNuo0ro8xtI6BcnQohnMe"
HERE = os.path.dirname(os.path.abspath(__file__))

CAND = [
    os.environ.get("LARK_CLI"),
    "/home/user/vm/resource-loader/bin/lark-cli",
]
# 原先这里还有第三条：一条硬编码的 Windows 绝对路径，带用户名和某次运行时的 env id。
# 两处都坏：①把「谁在哪台机器上用」写进了交付物；②env id 每次重装都会变，
# 写死必然过期——过期后它只会让报错长得像「没装 lark-cli」。
# 找不到就把路径放进环境变量 LARK_CLI，或让它在 PATH 里。


def _lark():
    for c in CAND:
        if c and os.path.exists(c):
            return c
    return shutil.which("lark-cli") or ""


def main():
    cli = _lark()
    if not cli:
        print("没找到 lark-cli：用环境变量 LARK_CLI 指定；或手工把包摆到目录后设 EXPRECALL_PKG")
        return 5
    d = tempfile.mkdtemp(prefix="gatepkg_")
    try:
        zp = os.path.join(d, "dl.zip")
        r = subprocess.run([cli, "drive", "+download", "--file-token", TOKEN, "--output", zp],
                           capture_output=True, text=True)
        if not os.path.exists(zp) or os.path.getsize(zp) < 1000:
            print("下载失败：%s" % ((r.stdout + r.stderr).strip()[-200:]))
            return 5
        dst = os.path.join(d, "记忆与经验管理工具_v_cloud.zip")   # 规则要求名字匹配 记忆与经验管理工具_v*.zip
        shutil.move(zp, dst)
        print("临时 <PKG>：%s（%d 字节，用完即删）" % (d, os.path.getsize(dst)))
        env = dict(os.environ)
        env["EXPRECALL_PKG"] = d
        p = subprocess.run([sys.executable, os.path.join(HERE, "recall.py"), "gate"] + sys.argv[1:],
                           env=env)
        return p.returncode
    finally:
        shutil.rmtree(d, ignore_errors=True)


sys.exit(main())
