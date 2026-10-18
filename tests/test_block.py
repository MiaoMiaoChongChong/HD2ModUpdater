# -*- coding: utf-8 -*-
"""离线验证：「屏蔽这个模组的更新与查找」（用户 2026-10-18 提的功能）。

要钉住的几条：
  ① 屏蔽表（blocked.json）能读写、坏文件当空、不留 .tmp；
  ② **引擎**在「识别来源」和「检查更新」两处都跳过屏蔽的模组
     （一个网络请求都不发 —— 这是"屏蔽"的实质）；
  ③ 界面上：状态列写「已屏蔽」、说明里写清怎么恢复、汇总里计数；
  ④ 右键菜单项的**写法跟着状态变**（已屏蔽 → 「取消屏蔽」）；
  ⑤ 两个页面（更新检查 / 部署）都能做同一件事，且共用一条路径；
  ⑥ 屏蔽的模组不会被「下载全部」带走；
  ⑦ 显式操作（只查这一个 / 双击）会先问要不要取消屏蔽，而不是静默什么都不做。

全程不联网：providers / matcher 都换成桩。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = os.path.join(HERE, "_tmp_block_data")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["HD2U_DATA_DIR"] = _TMP
os.environ["HD2U_READONLY_GUARD"] = "1"

from hd2_updater import engine  # noqa: E402

_fail: list[str] = []
_pass = 0


def check(name, cond, detail=""):
    global _pass
    if cond:
        _pass += 1
        print(f"  ✓ {name}")
    else:
        _fail.append(f"{name}　{detail}")
        print(f"  ✗ {name}　{detail}")


# ---------------------------------------------------------------- 假库
LIB = os.path.join(_TMP, "fake_hd2a_data.json")
with open(LIB, "w", encoding="utf-8") as f:
    json.dump({
        "userModsDir": os.path.join(_TMP, "mods"),
        "modsLibrary": [
            {"uuid": "u-a", "label": "HD2Runtime",
             "path": os.path.join(_TMP, "mods", "HD2Runtime-0.30.3"),
             "enabled": True},
            {"uuid": "u-b", "label": "Megapack",
             "path": os.path.join(_TMP, "mods", "Megapack-1.2.0"),
             "enabled": True},
            {"uuid": "u-c", "label": "NoSourceMod",
             "path": os.path.join(_TMP, "mods", "NoSourceMod"),
             "enabled": False},
        ],
    }, f, ensure_ascii=False, indent=1)

ENTRY_B = {"uuid": "u-b", "label": "Megapack",
           "path": os.path.join(_TMP, "mods", "Megapack-1.2.0")}

print("【数据】屏蔽表读写")
check("一开始是空的", engine.load_blocked() == {}, str(engine.load_blocked()))
on = engine.set_blocked(ENTRY_B, True)
check("set_blocked(True) 返回已屏蔽", on is True)
check("blocked.json 真的落盘了", os.path.isfile(engine.blocked_path()))
check("读得回来、且记住了名字",
      engine.load_blocked().get("u-b", {}).get("label") == "Megapack",
      str(engine.load_blocked()))
check("没留下 .tmp 半成品",
      not os.path.exists(engine.blocked_path() + ".tmp"))
check("is_blocked() 认得它", engine.is_blocked(ENTRY_B) is True)
check("别的模组不受影响", engine.is_blocked({"uuid": "u-a"}) is False)

with open(engine.blocked_path(), "w", encoding="utf-8") as f:
    f.write("[1,2,3]")      # 被手工改坏 / 被并发写坏
check("坏文件当空、不抛异常", engine.load_blocked() == {}, str(engine.load_blocked()))
engine.set_blocked(ENTRY_B, True)
check("坏文件之后还能正常写", "u-b" in engine.load_blocked())

print("\n【引擎】识别来源时跳过屏蔽的模组")
searched: list[str] = []


def fake_candidates(entry, **_kw):
    searched.append(entry.get("uuid"))
    return [{"provider": "github", "id": f"x/{entry.get('uuid')}",
             "confidence": "high", "how": "桩：认出来了"}]


engine.matcher.candidates_for = fake_candidates
eng = engine.Engine(LIB, use_search=False)
eng.load()
check("引擎读到了屏蔽表", "u-b" in eng.blocked, str(eng.blocked))
eng.resolve_sources()
check("屏蔽的那个**没有被搜索**", "u-b" not in searched, str(searched))
check("没屏蔽的两个都搜了", {"u-a", "u-c"} <= set(searched), str(searched))
check("屏蔽的那个也没有被存下来", not eng.resolved.get("u-b"),
      str(eng.resolved.get("u-b")))

print("\n【引擎】检查更新时跳过屏蔽的模组")
asked: list[str] = []


def fake_check_all(src, api_key, github_token, variant=""):
    asked.append(str(src.get("id") or src.get("url")))
    return [{"provider": src.get("provider"), "ok": True, "version": "r99",
             "url": "https://example.invalid/x"}]


engine.check_one_source_all = fake_check_all
eng2 = engine.Engine(LIB, use_search=False)
eng2.load()
eng2.resolved = {k: [{"provider": "github", "id": k}]
                 for k in ("u-a", "u-b", "u-c")}
res = eng2.check()
keys = [engine.source_key(r["entry"]) for r in res]
check("结果里没有屏蔽的那个", "u-b" not in keys, str(keys))
check("屏蔽的那个一个请求都没发", "u-b" not in asked, str(asked))
check("没屏蔽的照常查了", {"u-a", "u-c"} <= set(asked), str(asked))
check("其余两个的结果还在", {"u-a", "u-c"} <= set(keys), str(keys))

# 增量检查（只查这一个）也要拦 —— 引擎层是最后一道闸
asked.clear()
eng2.check(only_keys={"u-b"})
check("只查屏蔽的那个 → 也不会发请求", asked == [], str(asked))

# ---------------------------------------------------------------- 界面
try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    _GUI = True
except Exception as e:  # noqa: BLE001
    _GUI = False
    print(f"\n跳过界面测试（这台机器建不起 Tk 窗口）：{e}")

if _GUI:
    from hd2_updater import gui as G  # noqa: E402
    from hd2_updater.arsenal import load_library  # noqa: E402

    G.find_data_file = lambda explicit=None: (LIB, "测试假库")
    import hd2_updater.arsenal as _arsenal
    _arsenal.find_data_file = G.find_data_file
    # 弹窗全部打桩：本测试只看"该不该弹/弹完做了什么"
    G.messagebox.showinfo = lambda *a, **k: None
    G.messagebox.showwarning = lambda *a, **k: None
    G.messagebox.showerror = lambda *a, **k: None

    app = G.App()
    app.cancel_startup_detect()
    app.data_file = LIB
    app.library = [dict(e) for e in load_library(LIB)]
    app._populate()
    app.update()

    def iid_of(uuid: str) -> str | None:
        for w in app._row_widgets:
            if (w.get("entry") or {}).get("uuid") == uuid:
                return w["iid"]
        return None

    print("\n【界面·更新检查页】屏蔽的那一行长什么样")
    iid_b = iid_of("u-b")
    vals = app.tree.item(iid_b, "values")
    check("状态列写「已屏蔽」", vals[0] == "已屏蔽", str(vals[0]))
    check("说明里写清不查更新、不找来源", "不查更新" in vals[6] and "找来源" in vals[6],
          str(vals[6]))
    check("说明里给了恢复的入口", "取消屏蔽" in vals[6], str(vals[6]))
    check("汇总里计数「已屏蔽 1」", "已屏蔽 1" in app.var_summary.get(),
          app.var_summary.get())
    check("屏蔽的行排在最后",
          app.tree.get_children()[-1] == iid_b,
          str(app.tree.get_children()))
    check("没屏蔽的行状态不受影响",
          app.tree.item(iid_of("u-a"), "values")[0] != "已屏蔽")

    print("\n【界面·右键菜单】写法跟着状态变")
    check("屏蔽的行 → 菜单写「取消屏蔽」",
          app._block_menu_label(ENTRY_B) == "取消屏蔽（恢复更新与查找）",
          app._block_menu_label(ENTRY_B))
    check("没屏蔽的行 → 菜单写「屏蔽…」",
          app._block_menu_label({"uuid": "u-a"}) == "屏蔽这个模组的更新与查找",
          app._block_menu_label({"uuid": "u-a"}))
    # 真的走一遍"右键那一刻同步文案"这条路径
    app.menu.tk_popup = lambda *a, **k: None
    app.tree.identify_row = lambda y: iid_b
    app.tree.selection_set(iid_b)
    app._on_right_click(type("E", (), {"y": 1, "x_root": 0, "y_root": 0})())
    check("右键弹出的那一项真的是「取消屏蔽」",
          "取消屏蔽" in app.menu.entrycget(app._block_menu_index, "label"),
          app.menu.entrycget(app._block_menu_index, "label"))

    print("\n【界面·取消屏蔽】点一下要立刻恢复")
    app.tree.selection_set(iid_b)
    app._ctx_toggle_block()
    check("屏蔽表里没了", "u-b" not in engine.load_blocked(),
          str(engine.load_blocked()))
    # ★ 重画后 iid 会变（_populate 是整表重建），所以要重新取
    iid_b = iid_of("u-b")
    vals2 = app.tree.item(iid_b, "values")
    check("那一行不再是「已屏蔽」", vals2[0] != "已屏蔽", str(vals2[0]))
    check("汇总里不再有「已屏蔽」", "已屏蔽" not in app.var_summary.get(),
          app.var_summary.get())
    check("状态栏如实说了下次会重查", "取消屏蔽" in app.var_status.get(),
          app.var_status.get())

    print("\n【界面·再屏蔽一次】")
    app.tree.selection_set(iid_b)
    app._ctx_toggle_block()
    iid_b = iid_of("u-b")
    check("又变成已屏蔽", "u-b" in engine.load_blocked())
    check("状态列立刻变回「已屏蔽」",
          app.tree.item(iid_b, "values")[0] == "已屏蔽",
          str(app.tree.item(iid_b, "values")[0]))

    print("\n【界面·下载全部】屏蔽的不该被带走")
    fake_row = {"entry": dict(ENTRY_B), "status": engine.ST_UPDATE,
                "best": {"provider": "github", "version": "r99",
                         "download_url": "https://example.invalid/a.zip"}}
    check("屏蔽的模组不进「下载全部」清单",
          app._auto_download_targets([fake_row]) == [],
          str(app._auto_download_targets([fake_row])))
    engine.set_blocked(ENTRY_B, False)
    check("取消屏蔽后又回到了清单里",
          len(app._auto_download_targets([fake_row])) == 1)
    engine.set_blocked(ENTRY_B, True)

    print("\n【界面·显式操作】先问一句，而不是静默什么都不做")
    asked_box: list[str] = []
    G.messagebox.askyesno = lambda *a, **k: (asked_box.append(a[1]), False)[1]
    started: list = []
    orig_start = app._start_check
    app._start_check = lambda only_keys=None: started.append(only_keys)
    app.tree.selection_set(iid_b)
    app._on_row_activate()           # 双击屏蔽的行
    check("双击会先问（而不是直接开网页/下载）", len(asked_box) == 1, str(asked_box))
    check("问的内容说清了屏蔽意味着什么",
          "检查更新时跳过它" in asked_box[0], asked_box[0] if asked_box else "")
    check("用户说「不」→ 什么都没开始", started == [], str(started))
    check("用户说「不」→ 仍然是屏蔽状态", "u-b" in engine.load_blocked())

    G.messagebox.askyesno = lambda *a, **k: True
    app._on_row_activate()
    check("用户说「要」→ 先取消屏蔽", "u-b" not in engine.load_blocked(),
          str(engine.load_blocked()))
    check("用户说「要」→ 然后才发起检查",
          started and started[-1] == {"u-b"}, str(started))
    app._start_check = orig_start
    engine.set_blocked(ENTRY_B, True)

    print("\n【界面·旧结论】屏蔽前查到的「有更新」不能顶掉「已屏蔽」")
    engine.set_blocked(ENTRY_B, True)
    app.results = [{"entry": dict(ENTRY_B), "status": engine.ST_UPDATE,
                    "installed": "1.0.0", "checks": [],
                    "best": {"provider": "github", "version": "9.9",
                             "download_url": "https://example.invalid/a.zip"}}]
    app._populate(app.results)
    iid_old = iid_of("u-b")
    old_vals = app.tree.item(iid_old, "values")
    check("有旧结果时状态列仍是「已屏蔽」", old_vals[0] == "已屏蔽",
          str(old_vals[0]))
    check("有旧结果时说明列写的仍是屏蔽的说明（不是下载提示）",
          "不查更新" in old_vals[6] and "取消屏蔽" in old_vals[6], str(old_vals[6]))
    check("「下载全部」不会带走它（self.results 里那条也被过滤）",
          app._auto_download_targets() == [],
          str(app._auto_download_targets()))

    print("\n【界面·部署页】同一件事也要能做、也要看得见")
    app._show_page("deploy")
    app.update()
    panel = app._deploy_panel
    panel._render()
    check("部署页的「状态」列标出「更新已屏蔽」",
          "更新已屏蔽" in str(panel.tree.item("u-b", "values")[4]),
          str(panel.tree.item("u-b", "values")))
    check("部署页右键也有这一项（不用跑回另一页）",
          "屏蔽" in panel.menu.entrycget(panel._block_menu_index, "label"),
          panel.menu.entrycget(panel._block_menu_index, "label"))
    panel.menu.tk_popup = lambda *a, **k: None
    panel.tree.identify_row = lambda y: "u-b"
    panel.tree.selection_set("u-b")
    panel._on_deploy_right_click(type("E", (), {"y": 1, "x_root": 0, "y_root": 0})())
    check("部署页右键弹出时也写「取消屏蔽」",
          "取消屏蔽" in panel.menu.entrycget(panel._block_menu_index, "label"),
          panel.menu.entrycget(panel._block_menu_index, "label"))
    panel.tree.selection_set("u-b")
    panel._toggle_block_selected()
    check("部署页点它能取消屏蔽（两页共用一条路径）",
          "u-b" not in engine.load_blocked(), str(engine.load_blocked()))
    check("部署页标记立刻消失",
          "更新已屏蔽" not in str(panel.tree.item("u-b", "values")[4]),
          str(panel.tree.item("u-b", "values")))
    panel.tree.selection_set("u-b")
    panel._toggle_block_selected()
    check("再点一次又能屏蔽", "u-b" in engine.load_blocked())

    print("\n【界面·部署页的「检查这些有没有更新」】跳过屏蔽的")
    app._show_page("update")
    started2: list = []
    app._start_check = lambda only_keys=None: started2.append(only_keys)
    app._check_deployed_keys({"u-a", "u-b"})
    check("只把没屏蔽的交给检查", started2 == [{"u-a"}], str(started2))
    app._start_check = orig_start

    print("\n【持久化】重启后仍然记得")
    app.destroy()
    app2 = G.App()
    app2.cancel_startup_detect()
    app2.data_file = LIB
    app2.library = [dict(e) for e in load_library(LIB)]
    app2._populate()
    app2.update()
    vals3 = [app2.tree.item(i, "values")[0] for i in app2.tree.get_children()]
    check("新开的窗口里那一行还是「已屏蔽」", "已屏蔽" in vals3, str(vals3))
    app2.destroy()

print("\n" + "=" * 62)
if _fail:
    print(f"✗ {len(_fail)} 项未通过（通过 {_pass}）：")
    for x in _fail:
        print("   · " + x)
    sys.exit(1)
print(f"✓ 全部通过（{_pass} 项）")
