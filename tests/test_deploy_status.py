# -*- coding: utf-8 -*-
"""离线测试：部署状态读取（arsenal.load_library）+ 界面「部署」列与「检查已部署的」筛选。

不联网、不碰用户真实 Arsenal 数据 —— 用一份假的 hd2a_data.json。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = os.path.join(HERE, "_tmp_deploy_data")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["HD2U_DATA_DIR"] = _TMP

from hd2_updater.arsenal import load_library  # noqa: E402

_fail = []
_pass = 0


def check(name, cond, detail=""):
    global _pass
    if cond:
        _pass += 1
        print(f"  ✓ {name}")
    else:
        _fail.append(f"{name}　{detail}")
        print(f"  ✗ {name}　{detail}")


# --- 造一份假的 hd2a_data.json：modsLibrary 是库，modsList 是配置档（含部署标志）---
# 覆盖四种情形：已部署 / 启用未部署 / 在配置档但禁用 / 不在配置档。
LIB_PATH = os.path.join(_TMP, "fake_hd2a_data.json")


def write_lib(**over):
    base = {
        "userModsDir": os.path.join(_TMP, "mods"),
        "userGameDir": os.path.join(_TMP, "game"),
        "selectedProfile": "default",
        "modsLibrary": [
            {"uuid": "u-dep", "label": "已部署的",
             "path": os.path.join(_TMP, "mods", "A"), "nexusData": None},
            {"uuid": "u-en-nodep", "label": "启用未部署",
             "path": os.path.join(_TMP, "mods", "B"), "nexusData": None},
            {"uuid": "u-dis", "label": "配置档禁用",
             "path": os.path.join(_TMP, "mods", "C"), "nexusData": None},
            {"uuid": "u-out", "label": "不在配置档",
             "path": os.path.join(_TMP, "mods", "D"), "nexusData": None},
        ],
        "modsList": {
            "default": {
                "label": "Default Profile",
                "mods": [
                    {"uuid": "u-dep", "enabled": True, "deployed": True, "changed": False},
                    {"uuid": "u-en-nodep", "enabled": True, "deployed": False, "changed": False},
                    {"uuid": "u-dis", "enabled": False, "deployed": False, "changed": False},
                ],
            },
        },
    }
    base.update(over)
    with open(LIB_PATH, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=1)


write_lib()
LIB = load_library(LIB_PATH)
by = {e["uuid"]: e for e in LIB}

print("【解析】部署状态从 modsList 读、按 uuid 关联 modsLibrary")
check("读到 4 个模组", len(LIB) == 4, f"（{len(LIB)}）")
check("已部署的 deployed=True", by["u-dep"]["deployed"] is True)
check("已部署的 deploy_order=0", by["u-dep"]["deploy_order"] == 0)
check("启用未部署 enabled=True / deployed=False",
      by["u-en-nodep"]["enabled"] is True and by["u-en-nodep"]["deployed"] is False)
check("配置档禁用 enabled=False / deployed=False",
      by["u-dis"]["enabled"] is False and by["u-dis"]["deployed"] is False)
check("不在配置档 in_profile=False",
      by["u-out"]["in_profile"] is False and by["u-out"]["deployed"] is False)
check("在配置档的 in_profile=True",
      by["u-dep"]["in_profile"] is True and by["u-dis"]["in_profile"] is True)
check("game_dir 读出来了", by["u-dep"]["game_dir"].endswith("game"))
check("changed 标志读出来了", by["u-dep"]["deploy_changed"] is False)

print("\n【解析】损坏结构不崩（modsList 不是 dict / 缺配置档）")
write_lib(modsList=None)
lib2 = load_library(LIB_PATH)
check("modsList=None 时全部 in_profile=False",
      all(e["in_profile"] is False and e["deployed"] is False for e in lib2))
write_lib(modsList={"default": "不是dict"})
lib3 = load_library(LIB_PATH)
check("配置档节点不是 dict 时不崩", len(lib3) == 4
      and all(e["in_profile"] is False for e in lib3))
write_lib()  # 还原成正常库供下面 GUI 用

# --- 界面部分（建不起 Tk 的机器上跳过）---
try:
    import tkinter as tk  # noqa: F401
    _r = tk.Tk()
    _r.destroy()
    _can_gui = True
except Exception as e:  # noqa: BLE001
    print(f"\n跳过界面测试（建不起 Tk 窗口）：{e}")
    _can_gui = False

if _can_gui:
    from hd2_updater import gui as G

    print("\n【界面】部署列三态文字")
    check("已部署 → ✓已部署/dep_yes",
          G.App._deploy_cell({"deployed": True, "in_profile": True, "enabled": True})
          == ("✓已部署", "dep_yes"))
    check("启用未部署 → ○未部署/dep_no",
          G.App._deploy_cell({"deployed": False, "in_profile": True, "enabled": True})
          == ("○未部署", "dep_no"))
    check("配置档禁用 → —未部署/dep_no",
          G.App._deploy_cell({"deployed": False, "in_profile": True, "enabled": False})
          == ("—未部署", "dep_no"))
    check("不在配置档 → —未部署/dep_no",
          G.App._deploy_cell({"deployed": False, "in_profile": False, "enabled": False})
          == ("—未部署", "dep_no"))

    print("\n【界面】更新检查页不再带部署列（已搬到部署页）")
    app = G.App()
    app.cancel_startup_detect()
    app.data_file = LIB_PATH
    app.library = [dict(e) for e in load_library(LIB_PATH)]
    app._populate()
    app.update()
    cols = app.tree["columns"]
    check("更新检查页没有 deploy 列", "deploy" not in cols, f"（{cols}）")
    check("更新检查页没有「部署选中」按钮", not hasattr(app, "btn_deploy"))
    check("更新检查页没有「检查已部署更新」按钮", not hasattr(app, "btn_deployed"))

    print("\n【数据】「已部署」筛选仍然可用（更新检查要用它）")
    dk = app._deployed_keys()
    check("_deployed_keys 只含已部署那一个", dk == {"u-dep"}, f"（{dk}）")
    labels = [app.menu.entrycget(i, "label")
              for i in range(app.menu.index("end") + 1)
              if app.menu.type(i) == "command"]
    check("右键菜单不再有「检查所有已部署的」",
          not any("检查所有已部署" in l for l in labels), f"（{labels}）")

    # 只查已部署这条能力仍要能用（入口在部署页，但底层筛选不变）
    captured = {}
    orig = app._start_check
    app._start_check = lambda only_keys=None: captured.update(keys=only_keys)
    G.messagebox.showinfo = lambda *a, **k: None
    app._check_deployed_keys({"u-dep"})
    check("按已部署筛选的检查只传已部署的 key",
          captured.get("keys") == {"u-dep"}, f"（{captured}）")
    app._start_check = orig
    app.destroy()

print("\n" + "=" * 62)
if _fail:
    print(f"✗ {len(_fail)} 项没过（通过 {_pass} 项）：")
    for f in _fail:
        print("   · " + f)
    sys.exit(1)
print(f"✓ 部署状态测试全部通过（{_pass} 项）")
sys.exit(0)
