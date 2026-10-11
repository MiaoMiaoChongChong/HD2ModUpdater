# -*- coding: utf-8 -*-
"""静态检查：**各处声明的版本号是否与 `hd2_updater/__init__.py` 一致**。

为什么需要（同一个毛病已经犯过三次，每次都靠人眼才发现）：
  ① v1.0.1 时 README 引用的截图标题栏还写着 v1.0.0；
  ② v1.8.11 时 `docs/screenshot-main.png` 干脆被换成了**一张 Windows 壁纸**；
  ③ v1.8.11 时 README 末尾的 `*版本 1.8.0*` 还停在四个版本之前。
共同点：**版本号散落在多处，改代码时只改了 `__init__.py`**。

能自动查的（本脚本）：
  · README 末尾的 `*版本 X*`
  · README 的更新日志里有没有当前版本的条目（改了版本号却忘了写日志）
  · `docs/release-v*.md` 里最新的一份是否就是当前版本
  · `build_exe.py` / `HD2ModUpdater.spec` 里若硬写了版本号也要对上

查不了的（要靠人/别的工具）：
  · **截图里的标题栏** —— 那是图片像素，只能靠 `tools/make_screenshots.py`
    的自检断言（生成时就核对）或人眼看图。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_version() -> str:
    src = (ROOT / "hd2_updater" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
    if not m:
        raise SystemExit("[X] 读不到 hd2_updater/__init__.py 里的 __version__")
    return m.group(1)


def main() -> int:
    ver = read_version()
    print(f"当前版本（hd2_updater/__init__.py） = {ver}\n")
    problems: list[str] = []

    # ---- ① README 末尾的 *版本 X* ----
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    foot = re.findall(r"^\*版本\s*([0-9][0-9.]*)\*\s*$", readme, re.M)
    if not foot:
        problems.append("README 末尾没有 `*版本 X*` 一行（约定：末尾要标当前版本）")
    else:
        for v in foot:
            if v != ver:
                problems.append(f"README 末尾写的是 `*版本 {v}*`，但当前版本是 {ver}")

    # ---- ② README 更新日志里要有当前版本的条目 ----
    if f"> **v{ver}" not in readme:
        problems.append(
            f"README 的更新日志里没有 v{ver} 的条目"
            "（改了版本号就要在更新日志开头补一条）")

    # ---- ③ docs/ 里最新一份 release 说明应是当前版本 ----
    rel = sorted(
        (p for p in (ROOT / "docs").glob("release-v*.md")),
        key=lambda p: [int(x) for x in re.findall(r"\d+", p.stem)],
    )
    if not rel:
        problems.append("docs/ 下没有 release-v*.md")
    else:
        newest = rel[-1].stem.replace("release-v", "")
        if newest != ver:
            problems.append(
                f"docs/ 里最新的 release 说明是 v{newest}，但当前版本是 {ver}"
                "（发版时忘了写 docs/release-vX.Y.Z.md？）")
        else:
            print(f"  ✓ 最新 release 说明 = {rel[-1].name}")

    # ---- ④ 打包脚本里若硬写了版本号 ----
    for name in ("build_exe.py", "HD2ModUpdater.spec"):
        p = ROOT / name
        if not p.is_file():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'(?:version|VERSION)\s*[=:]\s*["\']([0-9][0-9.]*)["\']', txt):
            if m.group(1) != ver:
                problems.append(f"{name} 里硬写了版本 {m.group(1)}，与 {ver} 不一致")

    print()
    if problems:
        for x in problems:
            print("  ✗ " + x)
        print(f"\n发现 {len(problems)} 处版本号不一致 —— 请统一成 {ver}。")
        return 1
    print(f"✓ 各处版本号一致（{ver}）。")
    print("  注：**截图标题栏**是图片像素，本脚本查不了 —— "
          "靠 tools/make_screenshots.py 的自检，或人眼看图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
