# -*- coding: utf-8 -*-
"""负向验证：故意塞进用户可见文本的 `**`，检查脚本必须抓到。

只写临时文件，不动任何真源码。
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_display_text as C  # noqa: E402

CASES = {
    # ① 显示类函数的实参（老检查就能抓）
    "showwarning": (
        'from tkinter import messagebox\n'
        'def f():\n'
        '    messagebox.showwarning("t", "请贴**模组页面**的地址")\n',
        True,
    ),
    # ② 界面直接显示的字段（这次新加的一类，老检查漏掉）
    "visible_field": (
        'def f(out):\n'
        '    out["pick_reason"] = ("现在**没有**这个变体了")\n'
        '    return out\n',
        True,
    ),
    # ③ 放进 error 字段、再被界面读走
    "error_field": (
        'def f(out):\n'
        '    out["error"] = "这是**列表页**，不是模组页"\n',
        True,
    ),
    # ④ docstring 里的 `**` 不该报（不显示给用户）
    "docstring_ok": (
        'def f():\n'
        '    """这里写**加粗**没关系，不影响显示。"""\n'
        '    return 1\n',
        False,
    ),
    # ⑤ 纯注释也不该报
    "comment_ok": (
        '# 这里 **强调** 一下\n'
        'X = 1\n',
        False,
    ),
    # ⑥ 正常文案不该报
    "clean_ok": (
        'def f(out):\n'
        '    out["note"] = "这个 mod 现在没有新版本了"\n',
        False,
    ),
    # ⑦ 真 markdown 用于文档/README 的字符串（不在显示调用/字段里）不该报
    "plain_ok": (
        'MSG = "**重点**"\n',
        False,
    ),
}

fails = []
for name, (code, should_flag) in CASES.items():
    tmp = Path(tempfile.mkdtemp()) / "sample.py"
    tmp.write_text(code, encoding="utf-8")
    got = C.check(tmp)
    ok = bool(got) == should_flag
    print(("  ✓ " if ok else "  ✗ ") + f"{name}: 抓到={bool(got)} 期望={should_flag}"
          + (f"  {got}" if got else ""))
    if not ok:
        fails.append(name)

print()
if fails:
    print(f"✗ 负向验证失败：{fails}")
    sys.exit(1)
print("✓ 负向验证通过：该抓的抓到了，不该报的没误报")
