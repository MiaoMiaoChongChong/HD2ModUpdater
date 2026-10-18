# -*- coding: utf-8 -*-
"""HD2 Mod 更新管理器 —— 启动入口。

用法：
    python run.py            # 启动图形界面
    python run.py --check    # 命令行检查（不开窗口，便于排查）
    python run.py --version  # 只看版本号（不启动界面）
    python run.py --help     # 看用法
"""
from __future__ import annotations

import os
import sys

# 让脚本无论从哪运行都能 import 到包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

USAGE = """HD2 Mod 更新管理器

用法：
  HD2ModUpdater.exe            启动图形界面
  HD2ModUpdater.exe --check    命令行检查更新（不开窗口，便于排查）
  HD2ModUpdater.exe --selftest 打包/环境自检
  HD2ModUpdater.exe --version  只看版本号
  HD2ModUpdater.exe --help     看这段说明
"""


if __name__ == "__main__":
    args = sys.argv[1:]
    first = args[0] if args else ""
    # ★ 显式处理 --version / --help：以前它们会被当成"没参数"→ 直接开窗口。
    #   实测踩过：想核一下版本号，结果**又开了一个界面实例**；两个实例同时
    #   写 config.json，就把用户填的 key 冲掉了（2026-10-18）。
    if first in ("--version", "-V", "-v"):
        from hd2_updater import APP_NAME, __version__
        print(f"{APP_NAME} v{__version__}")
        sys.exit(0)
    if first in ("--help", "-h", "/?"):
        print(USAGE)
        sys.exit(0)
    if first in ("--check", "-c", "--selftest"):
        from hd2_updater.cli import main as cli_main
        sys.exit(cli_main(args))
    # 只有真的要开界面时才 import gui —— 上面那几个参数不能顺手建一个 Tk 窗口
    from hd2_updater.gui import main

    main()
