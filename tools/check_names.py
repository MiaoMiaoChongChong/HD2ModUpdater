# -*- coding: utf-8 -*-
"""静态检查：找出「用了但没定义/没导入」的名字。

为什么需要：改动里我写了 `providers.handoff_url(best)` 却忘了在 gui.py 里
`from . import providers` —— 语法检查（py_compile）**发现不了**，因为那只是
名字解析问题；要真的跑到那一行才会 NameError。GUI 里正好是右键菜单的路径，
不点就炸不了，属于最难受的一类 bug。

这个脚本用 AST 把每个模块「用到的名字」和「定义过的名字」对一遍，
把缺口直接列出来。不依赖第三方库。
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path


def collect_module_names(tree: ast.Module) -> set[str]:
    """模块级定义过的名字：import / def / class / 赋值 / for / with / 参数等。"""
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            # 函数参数（含 *args/**kwargs）
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                for arg in (a.posonlyargs + a.args + a.kwonlyargs):
                    names.add(arg.arg)
                if a.vararg:
                    names.add(a.vararg.arg)
                if a.kwarg:
                    names.add(a.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            names.update(node.names)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.comprehension,)):
            pass
    return names


def check(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    defined = collect_module_names(tree) | set(dir(builtins))
    # 模块自身的名字 & __future__ 之类
    defined |= {"__name__", "__file__", "__doc__", "__package__", "__version__",
                "__all__", "__builtins__", "__spec__", "__loader__", "self", "cls"}

    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in defined:
                problems.append(f"{path.name}:{node.lineno}: 未定义的名字 `{node.id}`")
    # 去重（同一名字多处）
    seen = set()
    out = []
    for p in problems:
        key = p.split("`")[-1]
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def main() -> int:
    # 本脚本在 tools/ 下，项目根是它的上一层
    root = Path(__file__).resolve().parent.parent
    targets = sorted((root / "hd2_updater").glob("*.py"))
    targets += [root / "run.py", root / "build_exe.py"]
    targets = [t for t in targets if t.is_file()]

    # 防呆：一个文件都没扫到就"通过"，是最毒的一种假成功。
    # （第一版就踩了：HERE 指向 tools/ 而不是项目根，glob 全空、却报"全部通过"。）
    if len(targets) < 3:
        print(f"[X] 只找到 {len(targets)} 个文件（{root}）—— 路径不对，检查没真正执行。")
        return 2

    bad = 0
    for t in targets:
        msgs = check(t)
        if msgs:
            for msg in msgs:
                print("  ✗ " + msg)
            bad += len(msgs)
        else:
            print(f"  ✓ {t.name}")
    print()
    if bad:
        print(f"发现 {bad} 处「用了但没定义」的名字 —— 这些位置一旦执行就会 NameError。")
        return 1
    print(f"扫了 {len(targets)} 个文件，全部通过：没有未定义名。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
