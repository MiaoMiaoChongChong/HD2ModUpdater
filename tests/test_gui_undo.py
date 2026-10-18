# -*- coding: utf-8 -*-
"""界面级验证：真的把窗口建起来，走一遍「撤销来源并重新识别」。

只在能建 Tk 窗口的机器上跑（建不起来会跳过并返回 0）。
不联网：识别阶段用桩替换 matcher.candidates_for。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = os.path.join(HERE, "_tmp_gui_data")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["HD2U_DATA_DIR"] = _TMP

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
except Exception as e:  # noqa: BLE001
    print(f"跳过界面测试（这台机器建不起 Tk 窗口）：{e}")
    sys.exit(0)

from hd2_updater import engine, matcher, providers  # noqa: E402
from hd2_updater import gui as G  # noqa: E402
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


CN_SLUG_ENC = "%E6%9C%BA%E7%94%B2%E9%83%A8%E4%BD%8D%E8%A1%80%E9%87%8FHUD.4775"

# --- 造一个假的 Arsenal 库文件（绝不碰用户真实数据）---
# 用真文件而不是打桩 load_library：这样连"从 JSON 读出来"这一整条路径
# 都被测到，和用户实际跑的是同一套代码。
LIB_PATH = os.path.join(_TMP, "fake_hd2a_data.json")
with open(LIB_PATH, "w", encoding="utf-8") as f:
    json.dump({
        "userModsDir": os.path.join(_TMP, "mods"),
        "modsLibrary": [
            {"uuid": "u-cn", "label": "机甲部位血量HUD",
             "path": os.path.join(_TMP, "mods", "Mech part HUD V1.11.1"),
             "enabled": True},
            {"uuid": "u-en", "label": "HD2Runtime",
             "path": os.path.join(_TMP, "mods", "HD2Runtime-0.28.1-runtime"),
             "enabled": True},
        ],
    }, f, ensure_ascii=False, indent=1)

LIB = load_library(LIB_PATH)

app = G.App()
# 关掉启动后 120ms 的自动探测：它会把表格重建一遍、顺手清掉选中行，
# 让测试踩到"选中的是上一秒那一行"的时序陷阱（本测试的库是假的，
# 不需要探测）。被测的是撤销流程，不是探测。
app.cancel_startup_detect()
app.data_file = LIB_PATH
app.library = [dict(e) for e in LIB]
app._populate()

print("【界面】右键菜单")
labels = []
for i in range(app.menu.index("end") + 1):
    if app.menu.type(i) == "command":
        labels.append(app.menu.entrycget(i, "label"))
check("菜单里有「撤销来源并重新识别」",
      any("撤销来源" in l for l in labels), f"（{labels}）")
check("菜单里有「指定来源…」", any("指定来源" in l for l in labels))
check("撤销项排在「指定来源…」之后（成对出现，好找）",
      labels.index([l for l in labels if "撤销来源" in l][0])
      > labels.index([l for l in labels if "指定来源" in l][0]))

print("\n【界面】指定来源对话框：粘中文地址")
app.var_nexus_key.set("")
app.var_github_token.set("")
G.messagebox.showinfo = lambda *a, **k: None
G.messagebox.showwarning = lambda *a, **k: print("    [警告框]", a[1][:60] if len(a) > 1 else a)
G.messagebox.askyesno = lambda *a, **k: True
G.messagebox.showerror = lambda *a, **k: print("    [错误框]", a[1][:60] if len(a) > 1 else a)

dlg = G.SourceDialog(app, dict(LIB[0]), on_saved=app._after_source_change)
app.update()
dlg.var_url.set("https://ayakamods.com/mods/机甲部位血量HUD.4775/")
dlg._preview()
app.update()
txt = dlg.lbl_detect.cget("text")
check("实时预览显示「识别为 ayakamods」", "识别为" in txt, f"（{txt[:70]}）")
check("预览里显示中文、不是 %E6%9C 串", "%E6" not in txt, f"（{txt[:70]}）")
check("「浏览器打开看看」按钮可用",
      str(dlg.btn_verify.cget("state")) == "normal")
dlg.destroy()
app.update()

print("\n【界面】撤销流程（不联网）")
# 先手工存一条错来源，模拟用户手填后想撤销
engine.set_manual_source(LIB[0], {"provider": "ayaka", "id": "wrong-mod.9999",
                                 "url": "https://ayakamods.com/mods/wrong-mod.9999/"})
check("手工来源已存", bool(engine.load_sources().get("u-cn")))

# 让识别直接返回正确的中文来源（桩），并允许联网 check 被短路
_rot = matcher.candidates_for
matcher.candidates_for = lambda entry, **kw: [
    {"provider": "ayaka", "id": CN_SLUG_ENC,
     "url": providers.ayaka_url(CN_SLUG_ENC),
     "how": "（测试桩）索引命中", "confidence": "high"}]
# ★ 预搜也是**联网**的（会打 GitHub 搜索 API）。不短路的话，网络一慢
#   这个自称"不联网"的测试就会卡在"并发预搜 GitHub…"上，30 秒后误报失败
#   （2026-10-18 实测复现；同一份代码在别的网络环境下是绿的）。
matcher.batch_prefetch_github = lambda *a, **k: 0
matcher.ensure_index = lambda *a, **k: []
engine.check_one_source = lambda src, *a, **k: {
    **providers._empty(src.get("provider", "?")), "ok": True,
    "version": "1.2", "url": src.get("url", ""), "title": "机甲部位血量HUD"}

# 选中 u-cn 那一行 —— **不能**写死第 0 行：表格是按状态排序的，
# 没来源的行（u-en）会排在"未检查"（u-cn）前面。
def select_key(key):
    for w in app._row_widgets:
        if G.source_key(w["entry"]) == key:
            app.tree.selection_set(w["iid"])
            app.update()
            return True
    return False


app._populate()
check("能在表里选中 u-cn 那一行", select_key("u-cn"))

# 走真正的右键菜单命令
app._ctx_undo_source()
app.update()

# 等后台检查线程收尾
deadline = time.time() + 30
while time.time() < deadline:
    app.update()
    if app.worker and not app.worker.is_alive():
        break
    time.sleep(0.1)
for _ in range(40):          # 让 _pump 把消息处理完
    app.update()
    time.sleep(0.05)

matcher.candidates_for = _rot
# ★ 预搜的桩**故意不还原**：本文件后面还有一次"重识别一无所获"的流程，
#   那一次同样会走 resolve_sources → 真的去打 GitHub 搜索 API。
#   以前它在慢网络下会卡满 30 秒，然后被误判成"没把来源放回去"
#   （2026-10-18 实测：同一份代码在网络好的时候是绿的）。
#   这个测试自称"不联网"，那就不该有任何一次真实请求。

check("重新识别后来源表里是中文那个",
      (engine.load_sources().get("u-cn") or [{}])[0].get("id") == CN_SLUG_ENC,
      f"（{(engine.load_sources().get('u-cn') or [{}])[0]}）")
check("重新识别后不再是「手工指定」（撤掉了那条手动记录）",
      (engine.load_sources().get("u-cn") or [{}])[0].get("manual") is not True,
      f"（{(engine.load_sources().get('u-cn') or [{}])[0]}）")
row = next((r for r in app.results if G.source_key(r["entry"]) == "u-cn"), None)
check("表格里这一行有结果了", row is not None)
if row:
    check("表格里拿到的是线上版本 1.2",
          (row.get("best") or {}).get("version") == "1.2", f"（{row.get('best')}）")
check("还有别的一行没被误删（u-en 仍在库里）", len(app.results) == 2,
      f"（{len(app.results)} 行）")

print("\n【界面】_ensure_full：增量结果必须补全整库，且不重复")
_base = app._initial_rows()
check("底表覆盖库里全部模组", len(_base) == len(app.library),
      f"（{len(_base)} vs {len(app.library)}）")
_lib = app.library
_partial = [
    {"entry": _lib[0], "status": G.ST_UPDATE, "installed": "1", "best": {},
     "checks": []},
    {"entry": _lib[1], "status": G.ST_OK, "installed": "1", "best": {},
     "checks": []},
]
_merged = app._ensure_full(_partial)
_keys = [G.source_key(r["entry"]) for r in _merged]
check("补齐后行数 = 整库行数（「表格被压成一行」那个 bug 的回归）",
      len(_merged) == len(_lib), f"（{len(_merged)} vs {len(_lib)}）")
check("补齐后没有重复行", len(set(_keys)) == len(_keys), f"（{_keys}）")
check("被查过的那两条排在最前", _keys[:2] == [G.source_key(_lib[0]),
                                              G.source_key(_lib[1])],
      f"（{_keys}）")
_full = [{"entry": e, "status": G.ST_OK, "installed": "1", "best": {},
          "checks": []} for e in _lib]
check("全部都有结果时不会重复追加",
      len(app._ensure_full(_full)) == len(_lib))
check("空输入能补成整库", len(app._ensure_full([])) == len(_lib))

print("\n【界面】对没有来源的行点撤销：只提示、不报错")
_saved_before = dict(engine.load_sources() or {})
check("选中没有来源的 u-en 那一行", select_key("u-en"))
app._ctx_undo_source()
app.update()
# 原来这里是 check(..., True)（恒真）。换成真断言：
# ① 不抛异常 ② u-en 仍然没有来源 ③ 别的模组的记录没被误删
check("没有来源时点撤销不炸，且没乱动人家的记录",
      "u-en" not in (engine.load_sources() or {})
      and set((engine.load_sources() or {}).keys()) <= set(_saved_before.keys()),
      f"（现在 {sorted((engine.load_sources() or {}).keys())}）")

print("\n【界面】撤销前必须先确认「撤销后还能重识别」")
# 关键：删除是破坏性动作。如果先删、再发现跑不了（检查/识别正在进行、
# 或还没选 Arsenal 位置），用户的来源记录就白丢了。所以这两道判断必须在
# clear_source() **之前**。
engine.set_manual_source(LIB[0], {"provider": "ayaka", "id": "keep-me.1",
                                  "url": "https://ayakamods.com/mods/keep-me.1/"})
check("重新放一条手工来源", bool((engine.load_sources() or {}).get("u-cn")))
check("选中 u-cn", select_key("u-cn"))


class _FakeWorker:
    def is_alive(self):
        return True


app.worker = _FakeWorker()          # 假装正在忙
app._ctx_undo_source()
app.update()
check("正在忙时点撤销，来源记录**不能**被删掉",
      (engine.load_sources().get("u-cn") or [{}])[0].get("id") == "keep-me.1",
      f"（{(engine.load_sources().get('u-cn') or [{}])[0]}）")
app.worker = None

_saved_file = app.data_file
app.data_file = ""                  # 假装还没选 Arsenal 位置
app._ctx_undo_source()
app.update()
check("没选 Arsenal 位置时点撤销，来源记录也不能被删掉",
      (engine.load_sources().get("u-cn") or [{}])[0].get("id") == "keep-me.1",
      f"（{(engine.load_sources().get('u-cn') or [{}])[0]}）")
app.data_file = _saved_file

print("\n【界面】★★ 重识别一无所获时，必须把手工来源放回来（不能吃掉用户数据）")
# 这是两份独立复核都指出的最危险路径：撤销是"先删、再去重新识别"，
# 而识别是尽力而为的 —— 用户关掉「智能搜索来源」、或离线、或索引没建好时，
# ayakamods 那一路根本不会被尝试，必定返回空。那时如果只是报一句"已撤销"，
# 用户手工找来的地址就永久没了（自动来源删了能重建，手工来源删了就真没了）。
engine.set_manual_source(LIB[0], {"provider": "ayaka", "id": "precious.4711",
                                 "url": "https://ayakamods.com/mods/precious.4711/",
                                 "manual": True})
check("先放一条手工来源", (engine.load_sources().get("u-cn") or [{}])[0].get("id")
      == "precious.4711", f"（{engine.load_sources().get('u-cn')}）")
check("选中 u-cn", select_key("u-cn"))

_saved_cf = matcher.candidates_for
matcher.candidates_for = lambda entry, **kw: []      # 假装识别一无所获
matcher.ensure_index = lambda *a, **k: []
try:
    app._ctx_undo_source()
    app.update()
    deadline = time.time() + 30
    while time.time() < deadline:
        app.update()
        if app.worker and not app.worker.is_alive():
            break
        time.sleep(0.1)
    for _ in range(40):
        app.update()
        time.sleep(0.05)
finally:
    matcher.candidates_for = _saved_cf

_after = engine.load_sources().get("u-cn") or []
check("★ 重识别失败后，原来的手工来源被放回来了（没有丢）",
      bool(_after) and _after[0].get("id") == "precious.4711",
      f"（{_after}）")
check("放回来的仍然是「手工指定」（受保护、不会被自动结果覆盖）",
      bool(_after) and _after[0].get("manual") is True, f"（{_after}）")
check("还原动作只做一次（_undo_restore 已清空）",
      getattr(app, "_undo_restore", None) is None)

print("\n【界面】★ 界面心跳不能被单条坏消息打死")
# 两份复核都指出：_pump 原来把 after(150, self._pump) 写在末尾、不在 finally 里，
# 任何异常都会让重排程**永远不再发生** —— 进度条/状态栏/下载回调/接力全部停摆，
# 用户看到的是一个"点什么都没反应"的窗口。
_before_after = app.after
_calls = []
app.after = lambda ms, fn=None, *a: (_calls.append(ms), _before_after(ms, fn, *a))[1]
# 投一条会让处理逻辑炸的消息（payload 类型不对）
app.msgq.put(("prog", "这不是三元组"))
app._pump()
app.update()
check("处理坏消息后仍然重新排队了（心跳没死）", len(_calls) >= 1,
      f"（排队记录 {_calls}）")
app.after = _before_after

app.destroy()
print()
print("=" * 62)
if _fail:
    print(f"✗ {len(_fail)} 项没过（通过 {_pass} 项）：")
    for f in _fail:
        print("   · " + f)
    sys.exit(1)
print(f"✓ 界面测试全部通过（{_pass} 项）")
sys.exit(0)
