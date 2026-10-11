# -*- coding: utf-8 -*-
"""「反馈 / 诊断」自测 —— 让用户一次把问题说清楚，让我拿到能复现的现场。

用户需求原话（2026-10-11）：
「我的模组更新管理器，现在还想加个新需求，让用户更方便反馈问题，
  让我也更方便解决用户反馈的问题」。

这个文件守住五件事：
  · 脱敏 —— N网 key / GitHub token 绝不能出现在诊断文本里（硬要求）
  · 取证 —— 日志、时间线、环境快照、模组清单要真的落进报告
  · 入口 —— 侧边栏有「反馈 / 帮助」页，且**建完必须 pack**（别的页白屏过）
  · 出口 —— 报错弹窗上就有「生成反馈信息」，不再是关掉就没
  · 兜底 —— 未捕获异常会被记下来，不再"点了没反应、事后零证据"

全部离线，只写临时目录。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="hd2u_diag_")
os.environ["HD2U_DATA_DIR"] = TMP
# 护栏：只允许写临时目录（diag 自己的日志也守这条）
os.environ["HD2U_READONLY_GUARD"] = "1"

PASS = 0
FAIL = 0
FAILS: list[str] = []

FAKE_NEXUS = "NEXUS-FAKE-KEY-abcdef123456"
FAKE_GH = "github_pat_FAKETOKEN1234567890ABCDEF"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}".strip())
        print(f"  FAIL: {name} {extra}")


def write_config() -> None:
    """往（临时的）数据目录里放一份带假凭据的 config.json。"""
    with open(os.path.join(TMP, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"nexus_api_key": FAKE_NEXUS, "github_token": FAKE_GH}, f)


def texts_of(widget) -> list[str]:
    """递归收集一个窗口里所有带 text 的控件文字（找按钮用）。"""
    out: list[str] = []
    try:
        kids = widget.winfo_children()
    except Exception:  # noqa: BLE001
        return out
    for w in kids:
        try:
            if "text" in w.keys():
                out.append(str(w.cget("text")))
        except Exception:  # noqa: BLE001
            pass
        out.extend(texts_of(w))
    return out


# ------------------------------------------------------------------ 脱敏
def t_redact():
    print("[脱敏：凭据绝不能出现在诊断文本里]")
    from hd2_updater import diag
    write_config()
    diag.setup()
    diag.refresh_secrets()

    txt = diag.redact(f"key={FAKE_NEXUS} token={FAKE_GH}")
    check("★ N网 key 被抹掉", "NEXUS-FAKE-KEY" not in txt, txt)
    check("★ GitHub token 被抹掉", "FAKETOKEN1234" not in txt, txt)

    # 正则兜底：config 里没有的形态（别的来源/别人机器上的）也要盖掉
    t2 = diag.redact("https://api.nexusmods.com/v1/x?apikey=deadbeefcafe&game=hd2")
    check("URL 查询串里的 apikey 被抹掉", "deadbeefcafe" not in t2, t2)
    t3 = diag.redact("Authorization: Bearer abcdefghijklmnop123456")
    check("Bearer 串被抹掉", "abcdefghijklmnop123456" not in t3, t3)
    t4 = diag.redact("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
    check("ghp_ 形态被抹掉", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in t4, t4)
    t4b = diag.redact("headers={'apikey': 'zzzz1111yyyy2222', 'x-api-key': 'qqqq3333'}")
    check("★ 请求头形态的 key 也被抹掉",
          "zzzz1111yyyy2222" not in t4b and "qqqq3333" not in t4b, t4b)

    user = (os.environ.get("USERNAME") or "").strip()
    if len(user) >= 3:
        t5 = diag.redact(rf"C:\Users\{user}\AppData\Local\hd2-updater")
        check("★ 路径里的 Windows 用户名被替换成 <用户>",
              user not in t5 and "<用户>" in t5, t5)
        t6 = diag.redact(rf"C:\Users\{user}\x", hide_user=False)
        check("hide_user=False 时保留完整路径", user in t6, t6)
        check("★ 盘符与目录结构保留（对定位问题有用）",
              "AppData" in t5 or "hd2-updater" in t5, t5)


# ------------------------------------------------------------------ 日志
def t_log():
    print("[日志：默认开、能轮转、会清理]")
    from hd2_updater import diag
    p = diag.setup()
    check("★ 日志文件已建立", bool(p) and p.endswith(".log"), p)
    check("日志写在数据目录下的 logs\\", os.path.basename(os.path.dirname(p)) == "logs", p)

    diag.event("自测事件", "细节 A")
    diag.log("普通一行")
    tail = diag.log_tail(50)
    check("事件写进了日志", "自测事件" in tail, tail[-120:])
    check("普通日志写进了日志", "普通一行" in tail)
    check("★ 事件同时进了内存时间线",
          any("自测事件" in x for x in diag.timeline_lines()))

    # 轮转：把上限压小，写几行就该转出 .1.log
    old_max = diag.LOG_MAX_BYTES
    try:
        diag.LOG_MAX_BYTES = 300
        for _ in range(60):
            diag.log("y" * 40)
    finally:
        diag.LOG_MAX_BYTES = old_max
    rotated = [n for n in os.listdir(diag.logs_dir()) if n.endswith(".1.log")]
    check("★ 超过单文件上限会轮转出 .1.log", bool(rotated),
          str(os.listdir(diag.logs_dir())))

    # 清理：造一个 30 天前的日志，prune 应该删掉它
    oldp = os.path.join(diag.logs_dir(), "hd2u-19990101.log")
    with open(oldp, "w", encoding="utf-8") as f:
        f.write("old")
    stamp = time.time() - 30 * 86400
    os.utime(oldp, (stamp, stamp))
    gone = diag.prune_logs()
    check("★ 超过保留天数的旧日志被删掉", not os.path.exists(oldp), str(gone))

    # 开关
    diag.set_enabled(False)
    check("关掉后没有日志路径", diag.log_path() == "")
    check("关掉时有原因说明", bool(diag.disabled_reason()), diag.disabled_reason())
    diag.set_enabled(True)
    check("再开启又有日志路径", bool(diag.log_path()))


# ------------------------------------------------------------------ 报告
def t_report():
    print("[诊断报告：环境 / 模组库 / 网络 / 时间线 / 用户描述]")
    from hd2_updater import diag
    diag.setup()
    diag.event("自测事件", "报告用")
    net = {"proxy": "没有设置代理（直连）", "when": "2026-10-11 15:00:00",
           "sites": [{"name": "GitHub", "ok": True, "sec": 0.5, "detail": "通"},
                     {"name": "Nexus Mods", "ok": False, "sec": 8.0,
                      "detail": "超时"}]}
    r = diag.report(desc="很多 mod 搜不到", steps="点了检查更新",
                    expect="能查到", include_library=True, include_logs=False,
                    net=net)
    check("含工具版本", "HD2 Mod Updater" in r)
    check("含系统信息", "系统：" in r)
    check("含数据目录", "数据目录：" in r)
    check("含 Arsenal 定位结果", "Arsenal 模组库：" in r)
    check("含模组库一节", "--- 2. 模组库 ---" in r)
    check("含网络自检结果", "GitHub：能连上" in r and "Nexus Mods：连不上" in r)
    check("含代理说明", "代理：" in r)
    check("★ 报告里写清了「403 不算网络问题」这个判据",
          "只有超时" in r or "连不上才算网络问题" in r)
    check("含操作时间线", "自测事件" in r)
    check("含用户描述", "很多 mod 搜不到" in r and "点了检查更新" in r
          and "能查到" in r)
    check("手动生成时默认不带日志尾部", "--- 5. 日志尾部 ---" not in r)
    check("★ 报告里不出现任何凭据",
          "NEXUS-FAKE-KEY" not in r and "github_pat_" not in r)

    r2 = diag.report(include_logs=True, include_library=False)
    check("勾了日志就有日志尾部", "--- 5. 日志尾部 ---" in r2)
    check("不勾模组清单时明说没包含", "模组清单未包含" in r2)

    # 现场说明（从哪个弹窗来的）
    r3 = diag.report(context="从报错弹窗生成（检查失败）")
    check("带上了现场说明", "现场：从报错弹窗生成" in r3)

    # 模组库概况本身要能跑（本机有/没有 Arsenal 都不能崩）
    lib = diag.library_overview()
    check("★ 读模组库概况不抛异常", isinstance(lib, dict) and "ok" in lib,
          str(lib)[:80])
    if lib.get("ok"):
        check("★ 概况里有模组清单（查「搜不到」的关键）",
              isinstance(lib.get("rows"), list) and len(lib["rows"]) == lib["total"],
              f"{len(lib.get('rows') or [])}/{lib.get('total')}")
        if lib["rows"]:
            row = lib["rows"][0]
            check("每行带目录名（识别来源的原始线索）",
                  "dirname" in row and "nexus_id" in row and "manifest" in row,
                  str(row)[:80])


# ------------------------------------------------------------------ 短摘要
def t_net_semantics():
    print("[网络自检的判据：403 算能连上，只有超时才算不通]")
    from hd2_updater import diag, util
    real = util.http_get

    def fake(url, **_k):
        if "github" in url:
            return "{}"
        if "nexusmods" in url:
            raise util.NetError("HTTP 403 Forbidden Just a moment... <meta name")
        raise util.NetError("TimeoutError: timed out")

    util.http_get = fake
    try:
        r = diag.network_check(timeout=1)
    finally:
        util.http_get = real
    by = {s["name"]: s for s in r["sites"]}
    check("★ 站点回 403 算「能连上」（不是用户的网络问题）",
          by["Nexus Mods"]["ok"] is True, by["Nexus Mods"]["detail"])
    # v1.8.3 起措辞更准：不再笼统说"不影响工具使用"，而是说清"浏览器能开、
    # 工具会被挡"——那才是用户真正困惑的点（他问过"为什么浏览器能访问"）。
    check("★ CF 挑战的说明点明「浏览器能开、工具会被挡」",
          "浏览器能开" in by["Nexus Mods"]["detail"]
          and "工具会被挡" in by["Nexus Mods"]["detail"],
          by["Nexus Mods"]["detail"])
    check("★ 并标出 blocked=cf（供界面/报告区分）",
          by["Nexus Mods"].get("blocked") == "cf", str(by["Nexus Mods"]))
    check("★ 超时才算「连不上」", by["ayakamods"]["ok"] is False,
          by["ayakamods"]["detail"])
    check("正常拿到响应也是能连上", by["GitHub"]["ok"] is True)
    txt = "\n".join(diag._fmt_net(r))
    check("报告里把判据写清楚了", "只有超时" in txt)


def t_summary():
    print("[短摘要：≤15 行，专供截图发帖]")
    from hd2_updater import diag
    net = {"proxy": "直连", "when": "x",
           "sites": [{"name": "GitHub", "ok": True, "sec": 0.4, "detail": "通"}]}
    s = diag.summary("很多 mod 搜不到", net=net)
    check("★ 短摘要不超过 15 行", len(s.splitlines()) <= 15, str(len(s.splitlines())))
    check("第一行点明是反馈并带版本", "反馈" in s.splitlines()[0]
          and "v" in s.splitlines()[0], s.splitlines()[0])
    check("含问题描述", "很多 mod 搜不到" in s)
    check("含网络一行", "网络：" in s)
    check("提到「复制完整诊断」这个出口", "复制完整诊断" in s)
    check("★ 短摘要里也没有凭据",
          "NEXUS-FAKE-KEY" not in s and "github_pat_" not in s)


def t_issue_url():
    print("[反馈页链接：标题正文预填、超长会截断]")
    from hd2_updater import diag
    u = diag.issue_url("[反馈] 搜不到", "正文内容")
    check("★ 指向仓库的新建 issue 页",
          "github.com" in u and "issues/new" in u, u[:80])
    check("带 title 参数", "title=" in u)
    check("带 body 参数", "body=" in u)
    u2 = diag.issue_url("t", "x" * 9000)
    check("★ 超长正文会被截断（URL 不能被撑爆）", len(u2) < 6000, str(len(u2)))
    from urllib.parse import unquote
    check("截断后仍提示去粘贴完整诊断", "复制完整诊断" in unquote(u2))


def t_save_text():
    print("[存成文件：utf-8-sig，记事本不乱码]")
    from hd2_updater import diag
    p = os.path.join(TMP, "out", "报告.txt")
    diag.save_text("中文内容 ABC", p)
    check("文件写出来了", os.path.isfile(p), p)
    raw = open(p, "rb").read()
    check("★ 带 BOM（Windows 记事本友好）", raw.startswith(b"\xef\xbb\xbf"), str(raw[:6]))
    check("内容可读回", "中文内容" in open(p, encoding="utf-8-sig").read())
    check("默认文件名带时间戳", "hd2u-反馈-" in diag.default_report_name(),
          diag.default_report_name())


# ------------------------------------------------------------------ 兜底
def t_hooks():
    print("[全局兜底：未捕获异常会留下证据]")
    from hd2_updater import diag
    diag.setup()
    old_sys = sys.excepthook
    old_thr = getattr(threading, "excepthook", None)
    try:
        diag.install_hooks()
        check("sys.excepthook 被替换", sys.excepthook is not sys.__excepthook__)
        check("threading.excepthook 被替换",
              getattr(threading, "excepthook", None) is not None
              and threading.excepthook is not threading.__excepthook__)
        try:
            raise ValueError("自测异常-XYZ")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        tail = diag.log_tail(120)
        check("★ 未捕获异常进了日志", "自测异常-XYZ" in tail, tail[-160:])
        check("★ 标成了 ERROR 级别", "[ERROR]" in tail)
        check("日志里有调用栈", "Traceback" in tail or "ValueError" in tail)
    finally:
        sys.excepthook = old_sys
        if old_thr is not None:
            threading.excepthook = old_thr


# ------------------------------------------------------------------ 界面
def _mk_app():
    from hd2_updater import gui as G
    app = G.App()
    app.withdraw()
    app.cancel_startup_detect()
    return app


def t_gui_page():
    print("[界面：侧边栏「反馈 / 帮助」页 —— 建完必须 pack（防白屏）]")
    from hd2_updater import gui as G, diag
    try:
        app = _mk_app()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试（建不起 Tk 窗口）：{e}")
        return
    try:
        check("★ 侧边栏有「反馈 / 帮助」入口", "feedback" in app.nav_buttons,
              str(list(app.nav_buttons)))
        check("导航文字点明用途",
              "反馈" in app.nav_buttons["feedback"].cget("text"),
              app.nav_buttons["feedback"].cget("text"))
        app._show_page("feedback")
        app.update_idletasks()
        check("反馈页已切换", app._current_page == "feedback")
        check("反馈面板已构建", app._feedback_panel is not None)
        # ★ 用 winfo_manager()（窗口 withdraw 了，ismapped 恒为 0）
        check("★ 反馈面板已被 pack（页面非空白）",
              app._feedback_panel.winfo_manager() == "pack",
              f"manager={app._feedback_panel.winfo_manager()!r}")
        check("反馈页容器已被 pack", app.page_feedback.winfo_manager() == "pack")
        check("面板里控件齐全", len(app._feedback_panel.winfo_children()) >= 5,
              str(len(app._feedback_panel.winfo_children())))
        btn_texts = texts_of(app._feedback_panel)
        check("★ 页面上有「生成反馈信息」入口",
              any("生成反馈信息" in t for t in btn_texts), str(btn_texts))
        check("★ 页面上有「复制短摘要」入口（贴吧/小黑盒用）",
              any("短摘要" in t for t in btn_texts), str(btn_texts))
        check("★ 页面上有反馈页/日志入口",
              any("反馈页" in t for t in btn_texts)
              and any("日志" in t for t in btn_texts), str(btn_texts))
        check("显示了日志状态",
              bool(app._feedback_panel.var_log.get()),
              app._feedback_panel.var_log.get()[:60])
        check("网络自检有显示", bool(app._feedback_panel.var_net.get()),
              app._feedback_panel.var_net.get()[:60])
        # 页面互斥
        app._show_page("update")
        app.update_idletasks()
        check("切走后反馈页取消布局", app.page_feedback.winfo_manager() == "")
        app._show_page("feedback")
        app.update_idletasks()
        check("再切回仍布局", app._feedback_panel.winfo_manager() == "pack")
    finally:
        app.destroy()


def t_feedback_dialog():
    print("[对话框：勾选项真的改变报告内容]")
    from hd2_updater import gui as G, diag
    try:
        app = _mk_app()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    dlg = None
    try:
        dlg = G.FeedbackDialog(app, app)
        dlg.withdraw()
        dlg.update_idletasks()
        body = dlg.txt_out.get("1.0", "end")
        check("★ 打开就有生成好的诊断文本", "诊断信息" in body and len(body) > 300,
              str(len(body)))
        check("默认包含模组清单", dlg.var_lib.get() is True)
        check("★ 手动生成时默认不带日志（按用户选的范围）",
              dlg.var_logs.get() is False)
        check("默认把用户名打码", dlg.var_hide.get() is True)

        dlg.txt_desc.delete("1.0", "end")
        dlg.txt_desc.insert("1.0", "某个 mod 搜不到")
        dlg._regen()
        body = dlg.txt_out.get("1.0", "end")
        check("★ 填的现象进了报告", "某个 mod 搜不到" in body)

        dlg.var_logs.set(True)
        dlg._regen()
        check("★ 勾上日志后报告里出现日志尾部",
              "日志尾部" in dlg.txt_out.get("1.0", "end"))
        dlg.var_logs.set(False)
        dlg._regen()
        check("取消勾选后日志尾部又消失",
              "日志尾部" not in dlg.txt_out.get("1.0", "end"))
        dlg.var_lib.set(False)
        dlg._regen()
        check("取消模组清单后明说没包含",
              "模组清单未包含" in dlg.txt_out.get("1.0", "end"))
        dlg.var_lib.set(True)

        dlg._copy_summary()
        check("复制短摘要给了反馈（不是静默）",
              "复制" in dlg.var_hint.get() or "失败" in dlg.var_hint.get(),
              dlg.var_hint.get())
        dlg._copy_full()
        check("复制完整诊断给了反馈",
              "复制" in dlg.var_hint.get() or "失败" in dlg.var_hint.get(),
              dlg.var_hint.get())
        check("★ 复制的内容里没有凭据",
              "NEXUS-FAKE-KEY" not in dlg.txt_out.get("1.0", "end"))
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_error_dialog():
    print("[报错弹窗：出口就在现场]")
    from hd2_updater import gui as G, diag
    try:
        app = _mk_app()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    shown: list[tuple] = []

    class _MB:
        @staticmethod
        def showinfo(t, m, **k):
            shown.append(("info", t, m))

        @staticmethod
        def showwarning(t, m, **k):
            shown.append(("warn", t, m))

        @staticmethod
        def showerror(t, m, **k):
            shown.append(("error", t, m))

        @staticmethod
        def askyesno(*a, **k):
            return True

    real = G.messagebox
    G.messagebox = _MB
    d = None
    try:
        diag.clear_timeline()
        G.error_box("自测报错", "出错了 XYZ", parent=app, app=app, context="自测")
        check("★ 测试环境走普通弹窗（不会卡住等人点）",
              bool(shown) and shown[0][1] == "自测报错", str(shown)[:80])
        tail = diag.log_tail(80)
        check("★ 报错写进了日志（用户不点任何按钮也留痕）",
              "自测报错" in tail and "出错了 XYZ" in tail, tail[-120:])
        check("★ 报错进了时间线",
              any("自测报错" in x for x in diag.timeline_lines()))

        # 真弹窗版本：构造后立刻销毁，不进 wait_window
        d = G.ErrorDialog(app, "标题X", "内容Y", app=app, context="自测")
        d.withdraw()
        d.update_idletasks()
        btns = texts_of(d)
        check("★ 错误弹窗上有「生成反馈信息」",
              any("生成反馈信息" in t for t in btns), str(btns))
        check("错误弹窗上有「打开日志」",
              any("日志" in t for t in btns), str(btns))
        check("错误弹窗里显示了详细信息",
              "内容Y" in d.txt.get("1.0", "end"))
    finally:
        if d is not None:
            try:
                d.destroy()
            except Exception:  # noqa: BLE001
                pass
        G.messagebox = real
        app.destroy()


def t_error_paths_logged():
    print("[主流程报错也走同一个出口]")
    from hd2_updater import gui as G, diag
    try:
        app = _mk_app()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    shown: list[tuple] = []

    class _MB:
        @staticmethod
        def showinfo(t, m, **k):
            shown.append(("info", t, m))

        @staticmethod
        def showwarning(t, m, **k):
            shown.append(("warn", t, m))

        @staticmethod
        def showerror(t, m, **k):
            shown.append(("error", t, m))

        @staticmethod
        def askyesno(*a, **k):
            return True

    real = G.messagebox
    G.messagebox = _MB
    try:
        diag.clear_timeline()
        app._handle_msg("error", "模拟检查失败 ABC")
        check("检查失败弹了窗", bool(shown), str(shown)[:60])
        check("★ 检查失败进了日志", "模拟检查失败 ABC" in diag.log_tail(80))
        check("★ 状态栏也说明了出错", "错" in app.var_status.get(),
              app.var_status.get())
        check("检查按钮恢复可用",
              str(app.btn_check.cget("state")) == "normal",
              str(app.btn_check.cget("state")))
    finally:
        G.messagebox = real
        app.destroy()


def t_settings_entry():
    print("[设置里也有入口 + 日志开关]")
    from hd2_updater import gui as G, diag
    try:
        app = _mk_app()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    dlg = None
    try:
        dlg = G.SettingsDialog(app)
        dlg.withdraw()
        dlg.update_idletasks()
        check("★ 设置里有日志开关", hasattr(dlg, "var_log_on"))
        check("日志开关默认跟着实际状态",
              bool(dlg.var_log_on.get()) == bool(diag.log_path()),
              f"{dlg.var_log_on.get()} / {diag.log_path()}")
        t = texts_of(dlg)
        check("★ 设置里有「生成反馈信息」入口",
              any("生成反馈信息" in x for x in t), str(t)[:120])
        check("设置里有打开日志文件夹", any("日志文件夹" in x for x in t))
        check("显示了当前日志路径或原因", bool(dlg.lbl_log.cget("text")),
              dlg.lbl_log.cget("text")[:60])
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_no_arsenal_safe():
    print("[没装 Arsenal 的机器上：诊断本身不能崩]")
    from hd2_updater import diag
    root = tempfile.mkdtemp(prefix="hd2u_diag_noars_")
    old = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "APPDATA", "HD2U_DATA_DIR")}
    try:
        os.environ["LOCALAPPDATA"] = os.path.join(root, "LocalAppData")
        os.environ["APPDATA"] = os.path.join(root, "Roaming")
        os.environ["HD2U_DATA_DIR"] = os.path.join(root, "tool")
        os.makedirs(os.environ["HD2U_DATA_DIR"], exist_ok=True)
        p = diag.setup()
        check("换一台机器（新数据目录）日志照样能建", bool(p), p)
        env = diag.environment()
        check("环境快照不抛异常且字段齐全",
              "工具版本" in env and "Arsenal 模组库" in env, str(list(env)))
        r = diag.report(desc="没装 Arsenal 时导出诊断")
        check("★ 没装 Arsenal 也能生成诊断文本", "诊断信息" in r and len(r) > 200)
        check("★ 文本里说明了 Arsenal 状态", "Arsenal 模组库：" in r)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        diag.setup()          # 恢复成主测试的数据目录
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    for fn in (t_redact, t_log, t_report, t_net_semantics, t_summary,
               t_issue_url, t_save_text, t_hooks, t_gui_page, t_feedback_dialog,
               t_error_dialog, t_error_paths_logged, t_settings_entry,
               t_no_arsenal_safe):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            check(f"{fn.__name__} 自身抛异常", False,
                  f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}")
    print()
    print(f"反馈 / 诊断自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
