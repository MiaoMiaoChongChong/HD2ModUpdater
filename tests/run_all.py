# -*- coding: utf-8 -*-
"""跑全部检查 + 测试（一条命令）。

    python tests/run_all.py

退出码 0 = 全绿。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable

STEPS = [
    ("静态检查：未定义的名字", ["tools/check_names.py"]),
    ("静态检查：用户可见文案里的 markdown 星号", ["tools/check_display_text.py"]),
    ("静态检查：各处版本号是否一致", ["tools/check_version_consistency.py"]),
    ("负向验证：检查脚本本身能抓到问题", ["tests/test_checker_negative.py"]),
    ("离线测试：config.json 的写入安全（原子写 / 不抹掉 key）",
     ["tests/test_config_safety.py"]),
    ("离线测试：两个修复（中文来源 / 撤销来源）", ["tests/test_fixes.py"]),
    ("离线+界面测试：部署状态读取与「检查已部署的」", ["tests/test_deploy_status.py"]),
    ("离线测试：部署到游戏（纯复制 + 编号方向）", ["tests/test_deploy.py"]),
    ("离线测试：与 Arsenal 同步（镜像接管 / 写回 / 冲突检测）", ["tests/test_arsenal_sync.py"]),
    ("离线测试：拖入压缩包加入模组库", ["tests/test_modimport.py"]),
    ("离线测试：来源候选与“需用户确认”", ["tests/test_choice.py"]),
    ("离线测试：issue #1「本地版本不冻结」（内容签名 / 导入保留 nexusData）",
     ["tests/test_issue1_version_freeze.py"]),
    ("离线测试：错误分类（已删除的 mod 不能说成「没填 API key」）",
     ["tests/test_issue1_error_kind.py"]),
    ("离线测试：下载页（下载 → 导入）", ["tests/test_downloads.py"]),
    ("离线+界面测试：屏蔽模组的更新与查找（右键开关）", ["tests/test_block.py"]),
    ("离线测试：备份轮转与「备份」页", ["tests/test_backups.py"]),
    ("离线测试：无 Arsenal 环境的行为", ["tests/test_no_arsenal.py"]),
    ("界面测试：部署页与勾选", ["tests/test_deploy_dialog.py"]),
    ("界面测试：撤销来源并重新识别", ["tests/test_gui_undo.py"]),
    ("离线+界面测试：反馈 / 诊断（日志、脱敏、报告、入口）", ["tests/test_diag.py"]),
    ("离线+界面测试：设置页可滚动 + 站点拦截的说明与出路",
     ["tests/test_settings_scroll.py"]),
]

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
# ★ 全套测试一律跑在**临时数据目录**上。
#   起因（2026-10-18）：有几个测试没设 HD2U_DATA_DIR，于是直接读写用户真实的
#   config.json —— 测试留下的 installed_versions 跑进了真实配置；更糟的是
#   "测试进程 + 一个多余的界面实例"同时写 config.json，把用户填的 N 网 key
#   冲掉了。这里统一兜住：任何测试都碰不到用户的数据目录。
env["HD2U_DATA_DIR"] = tempfile.mkdtemp(prefix="hd2u_runall_")
# ★ 护栏：禁止测试写到**真实的** Arsenal 库（只允许写临时目录）。
#   本项目曾有一次 ad-hoc 脚本没打桩就跑在真实库上，把用户模组库改了顺序;
#   靠结构性护栏比靠"记得打桩"可靠。
env["HD2U_READONLY_GUARD"] = "1"
# ★ 报错弹窗现在是自定义的模态窗口（比 messagebox 多「生成反馈信息」出口）。
#   测试里不能让它弹出来等人点关闭 —— 设了这个开关后，error_box 退回普通
#   messagebox（各测试早就把它打桩了），行为与加这个功能之前完全一致。
env["HD2U_NO_DIALOG"] = "1"

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
