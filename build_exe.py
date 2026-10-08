# -*- coding: utf-8 -*-
"""把本工具打包成免装 Python 的单个 EXE。

用法：
    pip install pyinstaller
    python build_exe.py

产物在 dist\\HD2ModUpdater.exe。

说明：
  - 用 --onefile 打成单文件，方便分享
  - 用 --windowed 隐藏黑色控制台窗口
  - tkinter 由 PyInstaller 自动带上
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "HD2ModUpdater"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("没装 PyInstaller。先运行：")
        print("    pip install pyinstaller")
        return 2

    # 清理旧的产物
    for d in ("build", "dist"):
        p = os.path.join(HERE, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",
        f"--name={NAME}",
        "--paths", HERE,
        # 让 exe 里能找到包
        "--hidden-import=hd2_updater",
        "--hidden-import=hd2_updater.gui",
        "--hidden-import=hd2_updater.cli",
        "--hidden-import=hd2_updater.engine",
        "--hidden-import=hd2_updater.providers",
        "--hidden-import=hd2_updater.matcher",
        "--hidden-import=hd2_updater.arsenal",
        "--hidden-import=hd2_updater.util",
        # 排除用不到的大件，缩小体积
        "--exclude-module=numpy",
        "--exclude-module=pandas",
        "--exclude-module=matplotlib",
        "--exclude-module=PIL",
        "--exclude-module=PyQt5",
        "--exclude-module=PySide2",
        os.path.join(HERE, "run.py"),
    ]
    print("执行：", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=HERE)
    if rc != 0:
        print(f"\n打包失败（退出码 {rc}）")
        return rc

    exe = os.path.join(HERE, "dist", f"{NAME}.exe")
    if os.path.isfile(exe):
        size = os.path.getsize(exe) / 1024 / 1024
        print(f"\n完成：{exe}  ({size:.1f} MB)")
        print("\n这个 exe 可以直接双击运行，不需要装 Python。")
        print("首次运行 Windows SmartScreen 可能警告（未签名），点「更多信息 → 仍要运行」。")
    else:
        print("\n没找到产物，请检查上面的输出。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
