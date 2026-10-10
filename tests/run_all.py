# -*- coding: utf-8 -*-
"""跑全部检查 + 测试（一条命令）。

    python tests/run_all.py

退出码 0 = 全绿。
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable

STEPS = [
    ("静态检查：未定义的名字", ["tools/check_names.py"]),
    ("静态检查：用户可见文案里的 markdown 星号", ["tools/check_display_text.py"]),
    ("负向验证：检查脚本本身能抓到问题", ["tests/test_checker_negative.py"]),
    ("离线测试：两个修复（中文来源 / 撤销来源）", ["tests/test_fixes.py"]),
    ("离线+界面测试：部署状态读取与「检查已部署的」", ["tests/test_deploy_status.py"]),
    ("界面测试：撤销来源并重新识别", ["tests/test_gui_undo.py"]),
]

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"

failed = []
for title, args in STEPS:
    print("\n" + "=" * 70)
    print("  " + title)
    print("=" * 70)
    r = subprocess.run([PY] + args, cwd=ROOT, env=env)
    if r.returncode != 0:
        failed.append(title)

print("\n" + "=" * 70)
if failed:
    print("✗ 未通过：")
    for f in failed:
        print("   · " + f)
    sys.exit(1)
print("✓ 全部检查与测试通过")
sys.exit(0)
