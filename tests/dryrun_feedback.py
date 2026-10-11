# -*- coding: utf-8 -*-
"""真机演练（不进测试套件）：在真实环境里走一遍「反馈 / 帮助」流程。

跑它之前问自己三个问题（用户交付前的惯例）：
  1. 真实数据目录里，日志真的建起来、真的有内容吗？
  2. 网络自检真的能跑通三站吗（而不是只在测试里被跳过）？
  3. 生成的诊断报告里，真的没有我的 key 吗？

用法：python tests/dryrun_feedback.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hd2_updater import diag  # noqa: E402
from hd2_updater import gui as G  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print(("  ✓ " if cond else "  ✗ ") + name + (f" —— {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def main() -> int:
    print("[1] 启动真实 GUI（窗口隐藏，不打扰桌面）")
    app = G.App()
    app.withdraw()
    app.cancel_startup_detect()
    app.update()

    lp = diag.log_path()
    ck("真实数据目录里日志文件已建立", bool(lp), lp)
    ck("日志目录名是 logs", bool(lp) and os.path.basename(os.path.dirname(lp)) == "logs")

    print("[2] 打开「反馈 / 帮助」页")
    app._show_page("feedback")
    app.update()
    panel = app._feedback_panel
    ck("面板已 pack（不是白屏）", panel is not None and panel.winfo_manager() == "pack",
       f"manager={panel.winfo_manager() if panel else None!r}")
    ck("页面上显示了日志状态", bool(panel.var_log.get()),
       panel.var_log.get().splitlines()[0] if panel.var_log.get() else "")

    print("[3] 真实网络自检（会真的请求三个站点，约几秒）")
    app.net_check_async(force=True)
    t0 = time.time()
    while app._net_running and time.time() - t0 < 90:
        app.update()
        time.sleep(0.2)
    res = app._net_result or {}
    for s in res.get("sites") or []:
        print(f"    {s['name']}: {'通' if s['ok'] else '不通'} ({s['sec']}s) {s['detail']}")
    ck("网络自检真的跑出了结果", bool(res.get("sites")), str(res)[:80])
    ck("代理信息有输出", bool(res.get("proxy")), str(res.get("proxy"))[:60])
    app._handle_msg("net", res)
    app.update()
    ck("页面显示了自检结果",
       "能连上" in panel.var_net.get() or "连不上" in panel.var_net.get(),
       panel.var_net.get().replace("\n", " | ")[:100])

    print("[4] 生成诊断报告并存成文件")
    dlg = G.FeedbackDialog(app, app, context="真机演练")
    dlg.withdraw()
    dlg.update()
    dlg.txt_desc.insert("1.0", "真机演练：检查报告内容是否完整")
    dlg.txt_steps.insert("1.0", "打开反馈页 → 生成反馈信息")
    dlg._regen()
    text = dlg.txt_out.get("1.0", "end")
    out = os.path.join(os.environ.get("TEMP") or ".", "hd2u-dryrun-report.txt")
    diag.save_text(text, out)
    print(f"    报告：{out}（{len(text)} 字符）")
    ck("报告里有环境一节", "--- 1. 环境 ---" in text)
    ck("报告里有模组库一节", "--- 2. 模组库 ---" in text)
    ck("报告里有网络自检一节", "--- 3. 网络自检 ---" in text)
    ck("报告里有时间线一节", "--- 4. 操作时间线" in text)
    ck("报告里有用户描述", "真机演练：检查报告内容是否完整" in text)
    ck("报告里带上了真实模组清单", "模组清单：" in text)

    print("[5] 脱敏：报告里不能出现真实 key")
    key = ""
    try:
        with open(os.path.join(os.environ["LOCALAPPDATA"], "hd2-updater",
                               "config.json"), encoding="utf-8-sig") as f:
            cfg = json.load(f)
        key = str(cfg.get("nexus_api_key") or "")
    except Exception as e:  # noqa: BLE001
        print(f"    （读不到 config.json：{e}）")
    if len(key) >= 6:
        ck("★ 报告里没有 N网 key", key not in text)
    else:
        print("    （没填 key，跳过这条）")
    user = os.environ.get("USERNAME") or ""
    ck("★ 报告里没有明文用户名", (not user) or (user.lower() not in text.lower()),
       "已打码为 <用户>" if user and user.lower() not in text.lower() else user)

    print("[6] 日志真的在长")
    diag.event("真机演练", "写一行看它落盘")
    tail = diag.log_tail(5)
    ck("★ 刚写的事件出现在日志尾部", "真机演练" in tail, tail.splitlines()[-1][:70] if tail else "")

    print("[7] 从报错弹窗那条路也要能用")
    d = G.ErrorDialog(app, "真机演练报错", "这是一条演练用的错误信息", app=app,
                      context="真机演练")
    d.withdraw()
    d.update()
    btns = []
    stack = [d]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        try:
            if "text" in w.keys():
                btns.append(str(w.cget("text")))
        except Exception:  # noqa: BLE001
            pass
    ck("报错弹窗上有「生成反馈信息…」", any("生成反馈信息" in b for b in btns), str(btns))
    d.destroy()
    dlg.destroy()
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
