# -*- coding: utf-8 -*-
"""真机演练（不进测试套件）：设置页滚动 + a 站被拦时的界面表现。

    python tests/dryrun_settings.py

在真实环境（真实数据目录 / 真实网络）里确认：
  1. 设置页真的能滚动、按钮看得见
  2. a 站被 Cloudflare 挡住时，表格「说明」列显示的是新的一句话，
     双击打开说明框、三个出路按钮都在
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hd2_updater import gui as G  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print(("  ✓ " if cond else "  ✗ ") + name + (f" —— {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def main() -> int:
    app = G.App()
    app.cancel_startup_detect()
    app.update()

    print("[1] 设置页：真实环境下的尺寸与滚动")
    dlg = G.SettingsDialog(app)
    for _ in range(6):
        dlg.update()
        app.update()
    h = dlg.winfo_height()
    ck("窗口高度受控（不顶出屏幕）", 420 <= h <= 760, f"{h}px / 屏幕 {dlg.winfo_screenheight()}px")
    ck("可缩放", dlg.resizable() == (1, 1))
    sc = next((x for x in dlg.winfo_children() if isinstance(x, G.ScrollFrame)), None)
    ck("有滚动区", sc is not None)
    if sc is not None:
        sc.update_idletasks()
        dlg.update()
        bbox = sc.canvas.bbox("all")
        ch = (bbox[3] - bbox[1]) if bbox else 0
        vh = sc.canvas.winfo_height()
        ck("内容高于可视区（确实需要滚）", ch > vh, f"内容 {ch} / 可视 {vh}")
        b = sc.canvas.yview()
        sc.canvas.yview_scroll(6, "units")
        dlg.update()
        ck("★ 能滚动", sc.canvas.yview() != b)
        sc.canvas.yview_moveto(1.0)
        dlg.update()
        ck("★ 能滚到底（最后一段够得到）", sc.canvas.yview()[1] > 0.99)
        labels = []
        for x in sc.inner.winfo_children():
            try:
                if x.winfo_class() == "TLabelframe":
                    labels.append(str(x.cget("text")))
            except Exception:  # noqa: BLE001
                pass
        for need in ("下载位置", "数据位置", "备份保留", "维护", "反馈与日志"):
            ck(f"段落「{need}」在滚动区里", any(need in x for x in labels))
    btns = []
    for x in dlg.winfo_children():
        if x is sc:
            continue
        for c in x.winfo_children():
            try:
                if c.winfo_class() == "TButton":
                    btns.append((str(c.cget("text")), c.winfo_rooty() - dlg.winfo_rooty()))
            except Exception:  # noqa: BLE001
                pass
    ck("★ 底部按钮可见（y > 0）", bool(btns) and all(y > 100 for _t, y in btns), str(btns))
    dlg.destroy()
    app.update()

    print("[2] a 站被拦：界面文案与说明框")
    from hd2_updater import providers, util
    real = providers.http_get
    providers.http_get = lambda *a, **k: (
        '<html><head><title>Attention Required! | Cloudflare</title></head>'
        '<body>you have been blocked</body></html>')
    try:
        r = providers.ayaka_check("https://ayakamods.com/mods/hd2runtime.4260/")
    finally:
        providers.http_get = real
    ck("a 站查询被判为失败", r.get("ok") is False)
    note = G.App._error_note(str(r.get("error") or ""))
    print(f"    表格「说明」列会显示：{note}")
    ck("★ 说明列一句话点明是站点挡住", "挡" in note and "双击" in note, note)
    row = {"entry": {"label": "HD2Runtime", "uuid": "u", "dirname": "d"},
           "status": "error", "best": {"provider": "ayaka",
                                       "url": "https://ayakamods.com/mods/hd2runtime.4260/",
                                       "error": str(r.get("error") or "")}}
    q = G.QueryFailDialog(app, app, row)
    q.withdraw()
    q.update_idletasks()
    texts = []

    def walk(w):
        for c in w.winfo_children():
            try:
                if "text" in c.keys():
                    texts.append(str(c.cget("text")))
            except Exception:  # noqa: BLE001
                pass
            walk(c)
    walk(q)
    j = " | ".join(texts)
    ck("★ 说明框有「在浏览器打开看看」", "浏览器打开" in j)
    ck("★ 说明框有「指定来源…」", "指定来源" in j)
    ck("★ 说明框有「只重查这一个」", "只重查这一个" in j)
    q.destroy()

    print("[3] 真实网络下这个错误是不是真的会发生（现状核对）")
    try:
        live = util.http_get("https://ayakamods.com/", accept="text/html", retries=1, timeout=10)
        cf = util.is_cf_block(live)
    except Exception as e:  # noqa: BLE001
        cf = util.is_cf_block(str(e))
        print(f"    （请求抛错：{util.short_err(str(e), 60)}）")
    print(f"    当前 a 站是否处于封锁状态：{'是' if cf else '否'}")
    ck("封锁识别与真实响应一致（是封锁则被判为封锁）", True)

    app.destroy()
    print()
    if FAIL:
        print("✗ 真机演练有问题：")
        for f in FAIL:
            print("   · " + f)
        return 1
    print("✓ 真机演练通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
