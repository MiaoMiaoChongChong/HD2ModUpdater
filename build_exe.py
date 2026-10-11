# -*- coding: utf-8 -*-
"""把本工具打包成**免装 Python、可单独运行**的单个 EXE。

用法：
    python build_exe.py

产物：dist\\HD2ModUpdater.exe（双击即用，目标机器无需安装 Python）

要点说明：
  - --onefile   打成单文件，方便直接发给人
  - --windowed  不弹黑框控制台（这是 GUI 程序）
  - 显式 --hidden-import 本包的各模块，避免被漏收
  - 排除 numpy/pandas/PIL 等用不到的大件，缩小体积
  - PyInstaller 会自动带上 tkinter（无需手动指定）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "HD2ModUpdater"

# 要显式声明的本包模块（PyInstaller 静态分析偶尔会漏）
HIDDEN = [
    "hd2_updater",
    "hd2_updater.gui",
    "hd2_updater.cli",
    "hd2_updater.engine",
    "hd2_updater.providers",
    "hd2_updater.matcher",
    "hd2_updater.arsenal",
    "hd2_updater.arsenal_sync",
    "hd2_updater.deploy",
    "hd2_updater.modimport",
    "hd2_updater.records",
    "hd2_updater.backups",
    "hd2_updater.diag",
    "hd2_updater.util",
]

# 需要把数据文件一起打进去的包。
# tkinterdnd2 自带各平台的 tkdnd 二进制（Tcl 扩展），**不 collect 的话
# 打包出来就没有拖放能力**（程序仍能跑，会安静降级成"点按钮选文件"）。
# 实测：--collect-all tkinterdnd2 后，单文件 EXE 里 tkdnd 2.10.3 能正常加载。
COLLECT_ALL = ["tkinterdnd2"]

# 明确用不到、可排除以缩小体积的第三方库
EXCLUDE = [
    "numpy", "pandas", "matplotlib", "scipy", "PIL", "cv2",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "pytest", "setuptools", "pip", "wheel",
    "unittest", "pydoc_data", "test",
]


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[X] 没装 PyInstaller。请先运行：")
        print("    pip install pyinstaller")
        return 2

    # 清理上次产物，避免拿到旧的 EXE
    for d in ("build", "dist"):
        p = os.path.join(HERE, d)
        if os.path.isdir(p):
            print(f"清理 {d}/ …")
            shutil.rmtree(p, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",
        f"--name={NAME}",
        "--paths", HERE,
    ]
    for m in HIDDEN:
        cmd.append(f"--hidden-import={m}")
    for m in EXCLUDE:
        cmd.append(f"--exclude-module={m}")
    # tkinterdnd2 带平台相关的 tkdnd 二进制，必须整体收集（缺了就没有拖放）
    for m in COLLECT_ALL:
        cmd.append(f"--collect-all={m}")
    # 需要动态 import 的模块（tkinter 的消息框、文件对话框）
    for m in ("tkinter", "tkinter.ttk", "tkinter.messagebox",
              "tkinter.filedialog", "tkinter.font"):
        cmd.append(f"--hidden-import={m}")
    cmd.append(os.path.join(HERE, "run.py"))

    print("执行：")
    print("  " + " ".join(cmd))
    print()
    rc = subprocess.call(cmd, cwd=HERE)
    if rc != 0:
        print(f"\n[X] 打包失败（退出码 {rc}）")
        return rc

    exe = os.path.join(HERE, "dist", f"{NAME}.exe")
    if not os.path.isfile(exe):
        print("\n[X] 没找到产物，请检查上面的输出。")
        return 1

    size_mb = os.path.getsize(exe) / 1024 / 1024
    print()
    print("=" * 62)
    print(f"  打包完成")
    print("=" * 62)
    print(f"  文件：{exe}")
    print(f"  大小：{size_mb:.1f} MB")
    print()
    print("  这个 EXE 可以单独运行，目标机器**不需要安装 Python**。")
    print()
    print("  注意：")
    print("   · 首次运行 Windows SmartScreen 可能警告（未签名），")
    print("     点「更多信息」→「仍要运行」即可。")
    print("   · 拖到别的电脑也能用，但要求 64 位 Windows 10/11。")
    print("   · 配置和数据仍存在 %LOCALAPPDATA%\\hd2-updater\\。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
