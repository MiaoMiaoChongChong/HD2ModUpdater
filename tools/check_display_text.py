# -*- coding: utf-8 -*-
"""静态检查：**用户可见文本里的 markdown 星号**。

为什么需要：本项目是 tkinter 程序，**不渲染 markdown**。
文案里写 `**重点**` 会原样显示成两个星号，用户看到的是
「那些**双击对应那一行**即可」这种半成品 —— 我在弹窗、状态栏、
悬停提示里前后写漏了 20 多处，肉眼根本查不过来。

这个脚本用 AST 找出「会显示给用户」的字符串常量里的 `**`，
docstring 和注释不算（那些不影响显示）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# 这些函数/方法的字符串实参是要显示给用户看的
# 注意 print 也算 —— 命令行模式的输出同样是用户可见文案
DISPLAY_FUNCS = {
    "showinfo", "showwarning", "showerror", "askyesno", "askokcancel",
    "askquestion", "askretrycancel", "set", "configure", "insert", "print",
}
# 这些名字里放的字符串也是给用户看的（悬停提示表等）
DISPLAY_VARS = {"HOVER_TIPS", "TIP", "TIPS", "HELP"}

# 这些 dict 字段会被界面**原样**填进表格/提示（不是显示函数的实参，
# 所以需要单独盯）。来源：gui.py 的 _populate / _update_note 读了它们。
VISIBLE_FIELDS = {
    "pick_reason", "error", "note", "how", "title", "message", "text", "status",
}


def collect_docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                ids.add(id(node.body[0].value))
    return ids


def func_name(node: ast.AST) -> str:
    f = getattr(node, "func", None)
    if f is None:
        return ""
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else ""
        return f"{base}.{f.attr}" if base else f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def check(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    doc_ids = collect_docstring_ids(tree)

    parent: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node

    def in_display_call(node: ast.AST) -> str:
        """往上走，看这个常量是不是喂给了显示类 API；是则返回那个 API 名。

        注意判断顺序：**先判 Call**，再判"包装节点"。
        第一版把 Call 也当成"继续往上走的包装节点"，于是
        `print("**x**")` 里那个常量一路往上、永远碰不到函数名判断 → 漏报。
        这个 bug 是靠"负向验证"发现的（故意塞一个 `**` 进去看能不能抓到）。
        """
        cur: ast.AST = node
        for _ in range(8):
            p = parent.get(id(cur))
            if p is None:
                return ""
            if isinstance(p, ast.Call):
                nm = func_name(p)
                short = nm.split(".")[-1]
                if short in DISPLAY_FUNCS or nm.endswith(".set"):
                    return nm
                return ""
            if isinstance(p, (ast.keyword, ast.Tuple, ast.List, ast.Dict,
                              ast.JoinedStr, ast.BinOp)):
                cur = p
                continue
            return ""
        return ""

    def in_visible_field(node: ast.AST) -> str:
        """这个常量是不是被放进了「界面上会原样显示」的字段里。

        为什么需要这第二类检查：GUI 的「说明」列是拿 ``best["pick_reason"]``、
        ``best["error"]`` 直接填的（providers/engine 里返回）。这些字符串
        **不是**显示类调用的实参，第一版检查完全看不到它们 ——
        实测就是这样漏掉了两处（表格里会显示成「现在**没有**」）。

        关键点（第一次写错在这里）：字段名在**赋值的目标**上，
        比如 ``out["pick_reason"] = "..."`` —— 那个 Subscript 是 Assign 的
        target，**不是**字符串常量的祖先节点。所以要走到 Assign 时
        回头看它的 targets，而不是指望能往上遇到 Subscript。
        """
        cur: ast.AST = node
        for _ in range(8):
            p = parent.get(id(cur))
            if p is None:
                return ""
            if isinstance(p, ast.Assign):
                for t in p.targets:
                    if (isinstance(t, ast.Subscript)
                            and isinstance(t.slice, ast.Constant)
                            and isinstance(t.slice.value, str)
                            and t.slice.value in VISIBLE_FIELDS):
                        return t.slice.value
                cur = p
                continue
            if isinstance(p, ast.AnnAssign):
                t = p.target
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and isinstance(t.slice.value, str)
                        and t.slice.value in VISIBLE_FIELDS):
                    return t.slice.value
                return ""
            if isinstance(p, (ast.keyword, ast.Tuple, ast.List, ast.Dict,
                              ast.JoinedStr, ast.BinOp, ast.IfExp, ast.Return,
                              ast.Call)):
                cur = p
                continue
            return ""
        return ""

    problems: list[str] = []
    # ① 悬停提示之类的字典
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n in DISPLAY_VARS for n in names):
                for sub in ast.walk(node.value):
                    if (isinstance(sub, ast.Constant)
                            and isinstance(sub.value, str)
                            and "**" in sub.value):
                        problems.append(
                            f"{path.name}:{sub.lineno}: {'/'.join(names)} 里有 `**`"
                            f"（tkinter 不渲染，会显示成星号）"
                        )

    # ② 显示类函数调用的实参 + 界面直接显示的字段
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if "**" not in node.value or id(node) in doc_ids:
            continue
        sink = in_display_call(node)
        if not sink:
            field = in_visible_field(node)
            if field:
                sink = f"界面显示的字段 [{field}]"
        if sink:
            first = node.value.strip().splitlines()[0][:40]
            problems.append(
                f"{path.name}:{node.lineno}: {sink} 的文本里有 `**` —— {first}"
            )
    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    targets = sorted((root / "hd2_updater").glob("*.py"))
    targets = [t for t in targets if t.is_file()]
    if len(targets) < 3:
        print(f"[X] 只找到 {len(targets)} 个文件（{root}）—— 路径不对，检查没真正执行。")
        return 2

    bad = 0
    for t in targets:
        msgs = check(t)
        if msgs:
            for m in msgs:
                print("  ✗ " + m)
            bad += len(msgs)
        else:
            print(f"  ✓ {t.name}")
    print()
    if bad:
        print(f"发现 {bad} 处 markdown 星号 —— 它们会原样显示给用户，请去掉 `**`。")
        return 1
    print(f"扫了 {len(targets)} 个文件，用户可见文案里没有残留的 `**`。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
