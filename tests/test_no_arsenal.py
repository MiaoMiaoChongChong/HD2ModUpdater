# -*- coding: utf-8 -*-
"""「没装 HD2 Arsenal 的机器上会怎样」自测。

用户问过：「这个mod管理器，在**如果没有Arsenal的电脑上，可以正确运行吗**？」
实测答案是**不能** —— 本工具是 Arsenal 的配套件：模组清单、启用状态、加载顺序
三样都在 Arsenal 的 `hd2a_data.json` 里，检查更新和部署都以它为前提。

这个文件把那条实测结论**钉成断言**，并守住"提示必须说清原因"：
以前只说「没找到 Arsenal 模组库，请手动选择位置」，会让人以为是自己指错了路，
其实是缺一个前置软件。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HD2U_READONLY_GUARD"] = "1"

PASS = 0
FAIL = 0
FAILS: list[str] = []

P = "9ba626afa44a3aa3"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
        print(f"  FAIL: {name} {extra}")


def sandbox():
    """造一个"没装 Arsenal 的机器"：默认位置全指向空目录。"""
    root = tempfile.mkdtemp(prefix="hd2u_noars_")
    for n in ("LocalAppData", "Roaming", "tool"):
        os.makedirs(os.path.join(root, n), exist_ok=True)
    os.environ["HD2U_DATA_DIR"] = os.path.join(root, "tool")
    os.environ["LOCALAPPDATA"] = os.path.join(root, "LocalAppData")
    os.environ["APPDATA"] = os.path.join(root, "Roaming")
    return root


def t_core_depends_on_arsenal():
    print("[核心：没有 Arsenal 就没有清单/顺序 —— 实测钉住]")
    root = sandbox()
    try:
        from hd2_updater import arsenal, modimport as M
        p, how = arsenal.find_data_file()
        check("★ 自动探测找不到数据文件", p is None, f"{p!r} / {how}")
        # 没有路径时连读都读不了（明确抛错，不会静默给一个"空库"骗人）
        err0 = ""
        try:
            arsenal.load_library(os.path.join(root, "不存在.json"))
        except Exception as e:  # noqa: BLE001
            err0 = f"{type(e).__name__}"
        check("★ 库不存在时读库会明确报错（不是静默空库）", bool(err0), err0)

        # 拖入 ZIP：必须明确报错，而不是静默写坏东西
        z = os.path.join(root, "SomeMod.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("manifest.json", '{"Version":1,"Guid":"g","Name":"x"}')
            zf.writestr(f"{P}.patch_0", b"X")
        err = ""
        try:
            M.import_zip("", z)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        check("★ 拖入 ZIP 会明确报错（不是静默失败）", bool(err), err)
        check("报错里提到 Arsenal", "Arsenal" in err, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_deploy_is_self_contained():
    print("[有意思的一点：部署本身不依赖 Arsenal（依赖的是清单来源）]")
    root = sandbox()
    try:
        from hd2_updater import deploy as D
        game = os.path.join(root, "game")
        os.makedirs(os.path.join(game, "data"), exist_ok=True)
        open(os.path.join(game, "data", f"{P}.patch_0"), "wb").close()
        mods = os.path.join(root, "mods")
        d = os.path.join(mods, "m0", "options", "O0")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, f"{P}.patch_0"), "wb").write(b"y" * 8)
        plan = D.build_plan(
            [{"uuid": "m0", "label": "m0", "path": os.path.join(mods, "m0"),
              "options": [], "enabled": True, "in_profile": True,
              # ★ 关键：deploy.py 要的是"带顺序号的清单"。它自己不读 Arsenal 的
              #   文件 —— 顺序由调用方给（现在这个调用方就是 load_library(Arsenal)）。
              "deploy_order": 0}],
            {"m0"}, game, first_mod_priority=False)
        res = D.deploy(plan)
        check("★ 给一份自造清单（含顺序号）就能部署 —— deploy.py 本身不读 Arsenal",
              plan.slot_count == 1 and int(res.get("written") or 0) == 1,
              f"slots={plan.slot_count} written={res.get('written')}")
        check("游戏目录里真的写出了 patch",
              os.path.isfile(os.path.join(game, "data", f"{P}.patch_0")))
        # 但**没有顺序号**就拒绝 —— 这是它依赖"清单来源"的地方
        err = ""
        try:
            D.build_plan([{"uuid": "m0", "label": "m0",
                           "path": os.path.join(mods, "m0"), "options": [],
                           "enabled": True, "in_profile": True,
                           "deploy_order": None}],
                         {"m0"}, game, first_mod_priority=False)
        except Exception as e:  # noqa: BLE001
            err = str(e)
        check("★ 没有顺序号时明确拒绝（这正是依赖 Arsenal 的点）",
              "加载顺序" in err, err[:80])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_message_says_why():
    print("[提示必须说清『为什么』，不能只说『没找到』]")
    root = sandbox()
    try:
        from hd2_updater import gui as G
        shown: list[tuple[str, str]] = []

        class _MB:
            @staticmethod
            def showinfo(t, m, **k):
                shown.append((t, m))

            @staticmethod
            def showwarning(t, m, **k):
                shown.append((t, m))

            @staticmethod
            def showerror(t, m, **k):
                shown.append((t, m))

            @staticmethod
            def askyesno(*a, **k):
                return False

        G.messagebox = _MB
        G.filedialog.askdirectory = lambda **k: ""
        G.filedialog.askopenfilename = lambda **k: ""
        G.find_data_file = lambda explicit=None: (None, "自动探测没找到")
        app = G.App()
        app.withdraw()
        try:
            app._auto_detect()
            app.update_idletasks()
            titles = [t for t, _m in shown]
            check("★ 弹出了说明（而不是默默只改状态栏）", bool(shown), str(shown))
            check("★ 标题就点明要先装 Arsenal",
                  any("Arsenal" in t for t, _m in shown), str(titles))
            body = "\n".join(m for _t, m in shown)
            check("★ 说明了原因（清单/启用/顺序由 Arsenal 提供）",
                  "配套" in body and "加载顺序" in body, body[:80])
            check("★ 给了『已装但找不到』的出路",
                  "手动选择" in body and "hd2a_data.json" in body, body[:80])
            check("★ 给了『还没装』的出路（含 Nexus 编号）",
                  "4664" in body, body[-60:])
            check("状态栏也留了指引",
                  "Arsenal" in app.var_status.get(), app.var_status.get())
            # 只弹一次：再调不该重复打扰
            n = len(shown)
            app._auto_detect()
            app.update_idletasks()
            check("★ 只弹一次（不每次启动都弹）", len(shown) == n,
                  f"{n} -> {len(shown)}")
        finally:
            app.destroy()
            G.messagebox = G.messagebox  # noqa: PLW0127
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_readme_states_prerequisite():
    print("[README 必须把前置条件写在前面]")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    txt = ""
    try:
        with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
            txt = f.read()
    except OSError as e:
        check("能读到 README", False, str(e))
        return
    head = txt[:2000]
    check("★ README 开头就写明需要先装 Arsenal",
          "HD2 Arsenal" in head and ("前置" in head or "需要先" in head
                                     or "先安装" in head or "先装" in head),
          head[:200].replace("\n", " "))
    check("README 里给了 Nexus 编号 4664", "4664" in txt)


def main() -> int:
    for fn in (t_core_depends_on_arsenal, t_deploy_is_self_contained,
               t_message_says_why, t_readme_states_prerequisite):
        fn()
    print()
    print(f"无 Arsenal 环境自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
