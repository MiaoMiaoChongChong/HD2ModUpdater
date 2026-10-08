# -*- coding: utf-8 -*-
"""HD2 Mod 更新管理器 —— 启动入口。

用法：
    python run.py            # 启动图形界面
    python run.py --check    # 命令行检查（不开窗口，便于排查）
"""
from __future__ import annotations

import os
import sys

# 让脚本无论从哪运行都能 import 到包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hd2_updater.gui import main  # noqa: E402


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--check", "-c"):
        from hd2_updater.cli import main as cli_main
        sys.exit(cli_main(sys.argv[2:]))
    main()
