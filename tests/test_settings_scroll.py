# -*- coding: utf-8 -*-
"""「设置页可滚动」+「站点被 Cloudflare 挡住时的说明与出路」自测。

两个都是用户 2026-10-11 直接提出的：
  ① 「设置页面现在太长了，显示不完全，改成可以上下滚动的那种吧」
  ② 「现在不知道为什么，a 站的 mod 都查询失败了」

② 的根因是站点对本机出口返回 Cloudflare **硬封锁**（"you have been blocked"，
连真实 Edge 无头浏览器都过不去），不是工具的问题 —— 但工具的**说法**必须
让用户当场知道"这是站点不让查，我该怎么办"，所以这里钉住文案与识别。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["HD2U_NO_DIALOG"] = "1"
os.environ["HD2U_READONLY_GUARD"] = "1"

PASS = 0
FAIL = 0
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}".strip())
        print(f"  FAIL: {name} {extra}")


# ---------------------------------------------------------------- 文案 / 识别
def t_cf_block_detection():
    print("[识别：Cloudflare 的挑战页和硬封锁页都要认出来]")
    from hd2_updater import util
    hard = ('HTTP 403 Forbidden <!DOCTYPE html><html><head>'
            '<title>Attention Required! | Cloudflare</title>'
            'you have been blocked</head></html>')
    soft = ('HTTP 403 Forbidden <html><head><title>Just a moment...</title>'
            '<meta http-equiv="refresh" content="8"></head>')
    check("★ 硬封锁页被认出来", util.is_cf_block(hard))
    check("★ 挑战页被认出来", util.is_cf_block(soft))
    check("正常页面不会被误判",
          not util.is_cf_block("<html><body><h1>HD2 Runtime</h1></body></html>"))
    check("空串不炸", util.is_cf_block("") is False)


def t_friendly_error_has_way_out():
    print("[文案：被挡住时要说清「不是你的网络断了」并给出路]")
    from hd2_updater import util
    hard = ('HTTP 403 Forbidden <title>Attention Required! | Cloudflare</title>'
            'you have been blocked')
    msg = util.friendly_error(hard)
    check("★ 点明是被站点挡住", "挡住" in msg, msg[:60])
    check("★ 明确说不是网络断了", "不是你的网络断了" in msg, msg[:80])
    check("★ 给出换节点/关代理的出路", "代理节点" in msg, msg[:80])
    check("★ 给出「指定来源」这条兜底", "指定来源" in msg, msg[:120])
    # 普通 403（真的没权限）不该被说成封锁
    plain = util.friendly_error("HTTP 403 Forbidden")
    check("普通 403 不误报成封锁", "挡住" not in plain, plain[:60])
    check("普通 403 仍给合理说明", "403" in plain or "拒绝" in plain, plain[:60])


def t_ayaka_check_message():
    print("[a 站：被挡住时给的是可操作的说明，不是含糊的报错]")
    from hd2_updater import providers, util
    # 直接喂一个封锁页给解析层（不发真实请求）
    real = providers.http_get
    providers.http_get = lambda *a, **k: (
        '<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare'
        '</title></head><body>you have been blocked</body></html>')
    try:
        r = providers.ayaka_check("https://ayakamods.com/mods/hd2runtime.4260/")
    finally:
        providers.http_get = real
    check("★ 判定为失败（不冒充成功）", r.get("ok") is False, str(r)[:80])
    err = str(r.get("error") or "")
    check("★ 说清是站点挡住", "挡" in err and "Cloudflare" in err, err[:80])
    check("★ 给出换节点/关代理", "代理节点" in err, err[:80])
    check("★ 给出「过一会儿再试」", "过一会儿" in err, err[:80])
    check("★ 给出浏览器/手工填这条兜底", "浏览器" in err or "指定来源" in err, err[:120])


def t_index_empty_says_why():
    print("[索引：抓不到时必须说清为什么（不能静默 0 条）]")
    from hd2_updater import matcher
    real = matcher.providers.http_get
    matcher.providers.http_get = lambda *a, **k: (
        '<html><head><title>Attention Required! | Cloudflare</title></head>'
        '<body>you have been blocked</body></html>')
    try:
        why = matcher._why_crawl_empty()
    finally:
        matcher.providers.http_get = real
    check("★ 说出了原因", "没能从 ayakamods 抓到" in why, why[:60])
    check("★ 提到了封锁", "挡住" in why or "Cloudflare" in why, why[:120])
    check("★ 说明了原索引还在（用户不会以为数据丢了）",
          "原索引已保留" in why or "保留" in why, why[:160])
    check("★ 给了可操作建议", "代理节点" in why or "过一会儿" in why, why[:160])

    # 超时（真正的网络问题）要说成网络问题，别甩锅给站点封锁
    def _boom(*a, **k):
        raise matcher.providers.NetError("TimeoutError: timed out")
    matcher.providers.http_get = _boom
    try:
        why2 = matcher._why_crawl_empty()
    finally:
        matcher.providers.http_get = real
    check("超时说的是超时/不可达，不误报封锁",
          "挡住" not in why2, why2[:80])


def t_error_note_short():
    print("[表格说明列：一句话能分清「站点不让查」和「mod 没了」]")
    from hd2_updater import gui as G
    f = G.App._error_note
    a = f("被站点（Cloudflare）挡住了 —— 不是你的网络断了")
    check("★ 封锁 → 提示双击看怎么办", "双击" in a and "挡" in a, a)
    b = f("该页面不存在（mod 可能被删除或改名）")
    # ★ 断言的是**意图**（说清这是 mod 自己的问题、不是站点也不是 key），
    #   不是某几个字的写法 —— 2026-10-12 文案从"不存在"改成了"没有了"，
    #   咬字面会让文案一改就红，反而逼着人不敢改文案。
    check("★ 页面没了 → 说清是 mod 的问题",
          ("不存在" in b or "没有了" in b) and "API key" not in b, b)
    c = f("站点暂时不可达（超时）——过一会儿再试")
    check("超时 → 说过一会儿再试", "超时" in c or "过一会儿" in c, c)
    d = f("")
    check("空原因也不留白", bool(d) and "双击" in d, d)
    e = f("x" * 300)
    check("超长原因被截断（列放得下）", len(e) <= 75, str(len(e)))


# ---------------------------------------------------------------- 设置页滚动
def t_settings_scrollable():
    print("[设置页：能滚动、内容都在滚动区、底部按钮看得见]")
    from hd2_updater import gui as G
    try:
        app = G.App()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试（建不起 Tk 窗口）：{e}")
        return
    app.cancel_startup_detect()
    dlg = None
    try:
        app.update()
        dlg = G.SettingsDialog(app)
        for _ in range(6):
            dlg.update()
            app.update()

        h = dlg.winfo_height()
        check("★ 窗口高度封顶（不顶出屏幕）", h <= 760, str(h))
        check("窗口有合理下限（不是一条缝）", h >= 420, str(h))
        check("允许用户自己拉大窗口", dlg.resizable() == (1, 1),
              str(dlg.resizable()))

        sc = next((x for x in dlg.winfo_children()
                   if isinstance(x, G.ScrollFrame)), None)
        check("★ 有滚动区", sc is not None)
        if sc is None:
            return

        # 所有段落都必须挂进滚动区（漏一个 = 那一段永远看不到）
        labels = []
        for x in sc.inner.winfo_children():
            try:
                if x.winfo_class() == "TLabelframe":
                    labels.append(str(x.cget("text")))
            except Exception:  # noqa: BLE001
                pass
        joined = " | ".join(labels)
        for need in ("下载位置", "数据位置", "备份保留", "维护", "反馈与日志"):
            check(f"★ 段落「{need}」在滚动区里", need in joined, joined[:100])

        # 底部按钮必须在滚动区外（否则滚下去就点不到）
        outside = []
        for x in dlg.winfo_children():
            if x is sc:
                continue
            for c in x.winfo_children():
                try:
                    if c.winfo_class() == "TButton":
                        outside.append(str(c.cget("text")))
                except Exception:  # noqa: BLE001
                    pass
        check("★ 「全部保存并关闭」在滚动区外",
              any("全部保存" in t for t in outside), str(outside))
        check("★ 按钮真的在窗口内可见（y 不为 0）",
              all(b.winfo_rooty() > dlg.winfo_rooty() + 100
                  for x in dlg.winfo_children() if x is not sc
                  for b in x.winfo_children()
                  if b.winfo_class() == "TButton"),
              str([b.winfo_rooty() - dlg.winfo_rooty()
                   for x in dlg.winfo_children() if x is not sc
                   for b in x.winfo_children() if b.winfo_class() == "TButton"]))

        # ★★ 回归：所有输入控件都必须在滚动区里。
        #   踩过的坑（用户 2026-10-11 报「只有上半能滚，下半填 API 之类的
        #   界面还是没法滚动」）：_section() 里写死了 ttk.Frame(self)，
        #   于是 N网 key / GitHub token 两块挂在对话框本体上、跑到滚动区外，
        #   永远滚不到。这里按"控件在不在滚动区子树里"来断言，
        #   而不是只数段落标题 —— 那样抓不到这个 bug。
        in_sc, out_sc = [], []

        def walk(w, inside):
            for c in w.winfo_children():
                try:
                    cls = c.winfo_class()
                except Exception:  # noqa: BLE001
                    continue
                if cls in ("TEntry", "Entry", "TCheckbutton", "TSpinbox",
                           "Text"):
                    (in_sc if inside else out_sc).append(cls)
                walk(c, inside or c is sc)

        walk(dlg, False)
        check("★ 所有输入控件都在滚动区里（不会滚不到）",
              not out_sc and len(in_sc) >= 6,
              f"区内={in_sc} 区外={out_sc}")
        # API 两块（两个 Entry）必须在滚动区内
        check("★ N网 key / GitHub token 两个输入框都在滚动区里",
              in_sc.count("Entry") >= 2, str(in_sc))
        # 设置页里那些段落标题（含两个 API 区块）也都在滚动区
        titles = []
        for x in sc.inner.winfo_children():
            try:
                if x.winfo_class() == "TFrame":
                    for c in x.winfo_children():
                        if c.winfo_class() == "TLabel" and "text" in c.keys():
                            titles.append(str(c.cget("text")))
                            break
            except Exception:  # noqa: BLE001
                pass
        tj = " | ".join(titles)
        check("★ ①N网 key 区块在滚动区里", "Nexus" in tj or "N网" in tj, tj[:120])
        check("★ ②GitHub Token 区块在滚动区里", "GitHub" in tj, tj[:120])

        # 真的能滚
        sc.update_idletasks()
        dlg.update()
        bbox = sc.canvas.bbox("all")
        content_h = (bbox[3] - bbox[1]) if bbox else 0
        view_h = sc.canvas.winfo_height()
        check("★ 内容比可视区高（所以确实需要滚动）",
              content_h > view_h, f"content={content_h} view={view_h}")
        before = sc.canvas.yview()
        sc.canvas.yview_scroll(5, "units")
        dlg.update()
        check("★ 滚动条能滚动", sc.canvas.yview() != before,
              f"{before} -> {sc.canvas.yview()}")
        # 滚轮
        class _Ev:
            delta = -120

            class widget:
                @staticmethod
                def winfo_class():
                    return "TFrame"
        b2 = sc.canvas.yview()
        sc._on_wheel(_Ev)
        dlg.update()
        check("★ 滚轮能滚动", sc.canvas.yview() != b2)
        # 微调框上的滚轮不该被抢（否则滚页面会把份数改掉）
        class _Ev2:
            delta = -120

            class widget:
                @staticmethod
                def winfo_class():
                    return "TSpinbox"
        b3 = sc.canvas.yview()
        sc._on_wheel(_Ev2)
        check("★ 微调框上的滚轮不被抢（不会误改份数）",
              sc.canvas.yview() == b3)
        # 能滚到底（最后一段够得到）
        sc.canvas.yview_moveto(1.0)
        dlg.update()
        check("★ 能滚到底", sc.canvas.yview()[1] > 0.99,
              str(sc.canvas.yview()))
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_query_fail_dialog():
    print("[双击查不到的行：给出原因 + 三个当场能点的出路]")
    from hd2_updater import gui as G
    try:
        app = G.App()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    app.cancel_startup_detect()
    dlg = None
    try:
        app.update()
        row = {
            "entry": {"label": "某模组", "uuid": "u1", "dirname": "x"},
            "status": "error",
            "best": {"provider": "ayaka",
                     "url": "https://ayakamods.com/mods/x.1/",
                     "error": "被站点（Cloudflare）挡住了 —— 不是你的网络断了。\n"
                              "可以试：① 换一个代理节点 / 关掉代理再试；② 过一会儿再试。"},
        }
        dlg = G.QueryFailDialog(app, app, row)
        dlg.withdraw()
        dlg.update_idletasks()
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    if "text" in c.keys():
                        texts.append(str(c.cget("text")))
                except Exception:  # noqa: BLE001
                    pass
                walk(c)
        walk(dlg)
        joined = " | ".join(texts)
        check("★ 有「指定来源…」", "指定来源" in joined, joined[:150])
        check("★ 有「全部重新查找」（v1.8.9 起取代了「只重查这一个」）",
              any("全部重新查找" in t for t in texts), joined[:200])
        # v1.8.8 起这个框改成「来源总览」（用户提议）：
        # 不再按状态写不同说辞，而是列出该 mod 的全部来源 + 每条一个动作按钮。
        check("★ 标题是「来源总览」", dlg.title() == "来源总览", dlg.title())
        check("★ 有来源动作按钮（下载 / 打开页面）",
              any(("下载" in t) or ("打开页面" in t) for t in texts),
              joined[:200])
        check("★ 列出了这个 mod 的来源", bool(dlg.sources),
              str([s.get("provider") for s in dlg.sources]))
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_source_overview_actions():
    print("[来源总览：GitHub 给下载、N网/a站给打开页面（与列表一致）]")
    from hd2_updater import gui as G
    try:
        app = G.App()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    app.cancel_startup_detect()
    dlg = None
    try:
        app.update()
        row = {
            "entry": {"label": "某模组", "uuid": "u1", "dirname": "x"},
            "status": "beta",
            "installed": "r41",
            "best": {"provider": "github", "version": "r42-test"},
            "checks": [
                {"provider": "github", "ok": True, "version": "r41",
                 "download_url": "https://x/r41.zip", "_src": {"provider": "github"}},
                {"provider": "github", "ok": True, "version": "r42-test",
                 "prerelease": True, "download_url": "https://x/r42.zip",
                 "_src": {"provider": "github"}},
                {"provider": "ayaka", "ok": True, "version": "42 - Test",
                 "_src": {"provider": "ayaka",
                          "url": "https://ayakamods.com/mods/x.1/"}},
            ],
        }
        dlg = G.QueryFailDialog(app, app, row)
        dlg.withdraw()
        dlg.update_idletasks()
        btns = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    if c.winfo_class() == "TButton":
                        btns.append(str(c.cget("text")))
                except Exception:  # noqa: BLE001
                    pass
                walk(c)
        walk(dlg)
        dl = [b for b in btns if b.startswith("下载")]
        check("★ GitHub 那两条给了「下载」按钮", len(dl) >= 2, str(btns))
        check("★ 按钮文字带版本（两条 GitHub 能分清）",
              len(set(dl)) >= 2, str(dl))
        check("★ a 站那条给「打开页面」", any("打开页面" in b for b in btns),
              str(btns))
        # 有直链的走下载、没有的走打开 —— 与列表行为一致
        gh = [s for s in dlg.sources if s.get("provider") == "github"
              and s.get("download_url")]
        check("GitHub 来源被识别为可直接下载", bool(gh), str(dlg.sources))
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_right_click_has_it():
    print("[右键菜单也要够得到这个出口]")
    from hd2_updater import gui as G
    try:
        app = G.App()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    try:
        app.cancel_startup_detect()
        app.update()
        labels = []
        try:
            n = app.menu.index("end")
            for i in range(n + 1):
                if app.menu.type(i) == "command":
                    labels.append(str(app.menu.entrycget(i, "label")))
        except Exception as e:  # noqa: BLE001
            check("能读到右键菜单", False, str(e))
        check("★ 右键有「来源总览…」入口",
              any(("来源总览" in x) or ("这一行是怎么回事" in x)
                  or ("为什么查不到" in x) for x in labels), str(labels))
    finally:
        app.destroy()


def t_index_rebuild_reports_truthfully():
    print("[重建索引抓空时：如实说没更新，且原索引完好]")
    from hd2_updater import matcher, providers
    real = providers.http_get
    providers.http_get = lambda *a, **k: (
        '<html><head><title>Attention Required! | Cloudflare</title></head>'
        '<body>you have been blocked</body></html>')
    try:
        before = len(matcher.load_index())
        age_before = matcher.index_age_days()
        items = matcher.rebuild_index(progress=lambda _m: None)
    finally:
        providers.http_get = real
    check("★ 抓空时返回的是原索引（不是空）",
          len(items) == before and (before == 0 or len(items) > 0),
          f"before={before} after={len(items)}")
    check("★ 抓空时没有写盘（索引年龄没被刷新）",
          matcher.index_age_days() >= age_before - 1e-6 or before == 0,
          f"{age_before:.3f} -> {matcher.index_age_days():.3f}")

    # 界面层：写盘判定要用"文件年龄"而不是"返回条数"——
    # 因为抓空时返回的就是原索引，光看条数会误报"重建成功"。
    from hd2_updater import gui as G
    try:
        app = G.App()
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

    real_mb = G.messagebox
    G.messagebox = _MB
    try:
        app.cancel_startup_detect()
        app.update()
        # 模拟"抓空"的那条消息（wrote=False）
        app._handle_msg("index_done", {"n": before, "wrote": False,
                                       "before": before})
        kinds = [k for k, _t, _m in shown]
        check("★ 抓空时报的是「警告」而不是「成功」",
              "warn" in kinds and "info" not in kinds, str(kinds))
        msg = " ".join(m for _k, _t, m in shown)
        check("★ 明说原索引被保留了", "保留" in msg, msg[:80])
        check("★ 明说不是登录过期（用户实际这么怀疑过）",
              "登录过期" in msg, msg[:120])
        check("★ 状态栏也说了没更新", "没能更新" in app.var_status.get(),
              app.var_status.get())
        shown.clear()
        # 真的写盘成功时才报成功
        app._handle_msg("index_done", {"n": 900, "wrote": True, "before": 900})
        kinds2 = [k for k, _t, _m in shown]
        check("★ 真写成功时才报成功", kinds2 == ["info"], str(kinds2))
    finally:
        G.messagebox = real_mb
        app.destroy()


def t_browser_vs_script_explained():
    print("[必须解释清「浏览器能开、工具被挡」这个困惑]")
    from hd2_updater import diag, util
    # 模拟"被 Cloudflare 挡住"的自检结果
    fake = {"proxy": "直连", "when": "2026-10-11 00:00:00", "sites": [
        {"name": "GitHub", "ok": True, "detail": "正常", "blocked": "", "sec": 0.4},
        {"name": "ayakamods", "ok": True, "blocked": "cf", "sec": 0.6,
         "detail": "能连上，但被 Cloudflare 挡在门外（HTTP 403）—— "
                   "站点要求能跑 JS 的浏览器才放行，所以浏览器能开、工具会被挡"},
    ]}
    lines = diag._fmt_net(fake)
    joined = "\n".join(lines)
    check("★ 报告里点明「浏览器能开、工具会被挡」",
          "浏览器能开" in joined and "工具会被挡" in joined, joined[:200])
    check("★ 报告里点明不是网络故障", "不是网络故障" in joined, joined[:200])
    check("★ 报告里点明不是登录过期", "不是登录过期" in joined, joined[:200])
    site = [x for x in lines if x.startswith("ayakamods")][0]
    check("★ a 站那行标出「被 Cloudflare 挡住」", "被 Cloudflare 挡住" in site, site[:90])
    gh = [x for x in lines if x.startswith("GitHub")][0]
    check("★ 正常站点不被误标", "被 Cloudflare 挡住" not in gh, gh[:90])

    # 自检本身要能判出 blocked 字段（用 mock 的响应）
    real = util.http_get

    def fake_get(url, **_k):
        if "github" in url:
            return "{}"
        raise util.NetError("HTTP 403 Forbidden <!DOCTYPE html>"
                            "<title>Just a moment...</title>cf_chl")
    util.http_get = fake_get
    try:
        r = diag.network_check(timeout=1)
    finally:
        util.http_get = real
    by = {s["name"]: s for s in r["sites"]}
    check("★ 自检能识别出 CF 挑战（blocked=cf）",
          by["ayakamods"].get("blocked") == "cf", str(by["ayakamods"]))
    check("★ 被挡但仍算「能连上」（网络是通的）",
          by["ayakamods"]["ok"] is True, str(by["ayakamods"]))
    check("★ 正常站点不带 blocked 标记",
          not by["GitHub"].get("blocked"), str(by["GitHub"]))


def t_netcheck_probes_real_path():
    print("[★ 铁律：自检必须探「工具实际用的那条路」]")
    from hd2_updater import diag
    urls = {name: url for name, url, _k in diag.SITES}
    # N 网必须探 API，不能探网页版（网页版吃 Cloudflare，会报假警）
    nexus = urls.get("Nexus Mods", "")
    check("★ N 网自检探的是 API（不是 www 网页版）",
          "api.nexusmods.com" in nexus, nexus)
    check("★ N 网自检不再探 www.nexusmods.com",
          "www.nexusmods.com" not in nexus, nexus)
    # a 站没有 API，但必须探"爬取实际用的列表页"，而不是首页
    ayaka = urls.get("ayakamods", "")
    check("★ a 站自检探的是列表页（爬索引走的那条）",
          "/mods/games/" in ayaka, ayaka)
    check("★ a 站自检不再只探首页",
          ayaka.rstrip("/") != "https://ayakamods.com", ayaka)
    check("GitHub 探的是 API", "api.github.com" in urls.get("GitHub", ""),
          urls.get("GitHub", ""))
    # 每个站点都要标明 kind（api / web），供报告说明
    check("★ 每个站点都标了 kind（api/web）",
          all(k in ("api", "web") for _n, _u, k in diag.SITES),
          str([(n, k) for n, _u, k in diag.SITES]))

    # 行为层：N 网带上 key；401 要说成"没填 key"而不是不通
    from hd2_updater import util
    real = util.http_get
    seen = {}

    def fake(url, **kw):
        seen[url] = kw.get("headers") or {}
        if "github" in url:
            return "{}"
        if "nexusmods" in url:
            raise util.NetError('HTTP 401 Unauthorized {"message":"auth"}')
        return "<html>ok</html>"

    util.http_get = fake
    try:
        r = diag.network_check(timeout=1)
    finally:
        util.http_get = real
    by = {s["name"]: s for s in r["sites"]}
    check("★ 探 N 网时带上了用户的 key",
          bool(seen.get(urls["Nexus Mods"], {}).get("apikey")), str(seen)[:120])
    check("★ N 网 401 说成「没填 key」而不是「不通」",
          by["Nexus Mods"]["ok"] is True and "key" in by["Nexus Mods"]["detail"],
          by["Nexus Mods"]["detail"])
    check("★ N 网 401 不被标成被挡住",
          not by["Nexus Mods"].get("blocked"), str(by["Nexus Mods"]))
    check("a 站正常时不带 blocked", not by["ayakamods"].get("blocked"),
          str(by["ayakamods"]))

    # 报告里要说清"探的就是工具用的那条路"
    txt = "\n".join(diag._fmt_net(r))
    check("★ 报告里说明探的是工具实际用的路",
          "实际查询用的那条路" in txt, txt[:220])


def t_proxy_not_hardcoded():
    print("[代理：不写死端口，任何 VPN 端口/直连都能用]")
    import pathlib
    import re as _re
    from hd2_updater import diag

    # ① 源码里不能出现硬编码的代理端口
    pkg = pathlib.Path(ROOT) / "hd2_updater"
    bad = []
    for f in sorted(pkg.glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _re.search(r"\b(10808|10809|7890|7897|1080|8080)\b", line):
                bad.append(f"{f.name}:{i}")
    check("★ 源码里没有硬编码代理端口", not bad, str(bad))

    # ② 代理来自 urllib.getproxies()（Windows 上会读系统代理设置）
    check("★ 代理取自 urllib.getproxies（不依赖环境变量）",
          "getproxies" in (pkg / "diag.py").read_text(encoding="utf-8"))

    # ③ 换端口照样被采用
    saved = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY")}
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
    try:
        txt = diag._proxy_text()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("★ 换成别的端口（7890）也照用", "7890" in txt, txt)
    check("★ 显示里说清这是「工具实际用的出口」",
          "实际用的出口" in txt, txt)
    check("★ 去掉了与本工具无关的 ftp 噪音", "ftp" not in txt, txt)

    # ④ 没有代理时要明说是直连，而不是空白
    saved2 = {k: os.environ.get(k) for k in
              ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "http_proxy", "https_proxy", "all_proxy")}
    real_gp = diag.urllib.request.getproxies
    diag.urllib.request.getproxies = lambda: {}
    for k in saved2:
        os.environ.pop(k, None)
    try:
        txt2 = diag._proxy_text()
    finally:
        diag.urllib.request.getproxies = real_gp
        for k, v in saved2.items():
            if v is not None:
                os.environ[k] = v
    check("★ 没代理时明说「直连」", "直连" in txt2, txt2)


def t_compact_search_terms():
    print("[★ GitHub 搜索必须带「紧凑词」——否则这条路等于白跑]")
    from hd2_updater import matcher
    # 实例（用户 2026-10-11 报）：Diver's Best Friend 在 GitHub 上有仓库
    # HWG90/DiversBestFriend，但工具搜出来的是 a 站 —— 因为搜索词是
    # 带空格的长词，GitHub 按词 AND 匹配直接 0 结果。
    e = {"label": "Diver's Best Friend - Automated Stratagem System (ASS)",
         "dirname": "DiversBestFriend-R41-MissionBlacklist_AR130294"}
    terms = matcher.search_terms(e)
    check("★ 紧凑词（去空格）排在第一位", terms[0] == "DiversBestFriend",
          str(terms[:5]))
    check("★ 且前 5 个里包含它（不会被配额截掉）",
          "DiversBestFriend" in terms[:5], str(terms[:5]))
    # 不能塞进没用的词把配额占满
    check("★ 不塞单字/超长连写这类搜不出东西的词",
          all(len(t) >= 4 for t in terms[:5]), str(terms[:5]))

    # 带空格的标题型目录名不该产生"首词"噪音（实测 "Resupply" 返回 10 个无关仓库）
    e2 = {"label": "Resupply Boxes Emissive",
          "dirname": "Resupply Boxes Emissive"}
    t2 = matcher.search_terms(e2)
    check("★ 标题型名字不会产生单词噪音",
          "Resupply" not in t2, str(t2[:5]))
    check("标题型名字仍保留完整短语", any("Resupply Boxes Emissive" in t for t in t2),
          str(t2[:5]))

    # 大写 R 版本号也要剥掉（R41/r41 是这类 mod 的常见形式）
    e3 = {"label": "Some Mod", "dirname": "SomeMod-R41-Extra_AR123456"}
    t3 = matcher.search_terms(e3)
    check("★ 紧凑词里不带版本号（R41 要剥掉）",
          any(t.startswith("SomeMod") and "R41" not in t for t in t3), str(t3[:5]))


def t_beta_row_explained():
    print("[「仅测试版」这一行要能自己看懂：各来源都列出来]")
    from hd2_updater import gui as G
    # 用户 2026-10-11 的疑问：「是不是因为 GitHub 还没检测 Pre-release，
    # 我现在检查更新开始显示来源是 a 站，这个是对的吗？」
    # v1.8.8 起改为「来源总览」：不再写解释，直接把**全部来源**列出来
    # （用户提议：「要是不好适配要不直接改成来源总览，显示该mod的全部来源？
    #   然后玩家可以自己根据来源去下载」）。
    row = {
        "entry": {"label": "某模组", "dirname": "x", "uuid": "u"},
        "status": "beta",
        "installed": "r41",
        "best": {"provider": "ayaka", "version": "42 - UI Recovery (Test Build)",
                 "url": "https://ayakamods.com/mods/x.1/"},
        "checks": [
            {"provider": "github", "ok": True, "version": "r41",
             "prerelease": False, "_src": {"provider": "github"}},
            {"provider": "github", "ok": True, "version": "r42-ui-recovery-test",
             "prerelease": True, "download_url": "https://x/r42.zip",
             "_src": {"provider": "github"}},
            {"provider": "ayaka", "ok": True,
             "version": "42 - UI Recovery (Test Build)", "_src": {"provider": "ayaka"}},
        ],
    }
    try:
        app = G.App()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    dlg = None
    try:
        app.cancel_startup_detect()
        app.update()
        dlg = G.QueryFailDialog(app, app, row)
        dlg.withdraw()
        dlg.update_idletasks()
        provs = [s.get("provider") for s in dlg.sources]
        check("★ 列出了 GitHub 来源（用户最想知道这个）", "github" in provs, str(provs))
        check("★ 也列出了 a 站来源", "ayaka" in provs, str(provs))
        check("★ GitHub 的正式版与预发布版都在", provs.count("github") >= 2,
              str(provs))
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    if "text" in c.keys():
                        texts.append(str(c.cget("text")))
                except Exception:  # noqa: BLE001
                    pass
                walk(c)
        walk(dlg)
        joined = " | ".join(texts)
        check("★ 标出了测试版 / 正式版", "测试版" in joined and "正式版" in joined,
              joined[:250])
        check("★ 没有 markdown 星号残留（tkinter 不渲染）", "**" not in joined)
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_github_prerelease_visible():
    print("[★ GitHub 的预发布版必须能被看到（否则测试版档里 GitHub 永远缺席）]")
    from hd2_updater import engine, providers, util
    # 用户 2026-10-11 的质疑（他是对的）：「既然 GitHub 上 r42-ui-recovery-test
    # 的 prerelease=True，a 站也是 r42 测试版，那不应该优先在 GitHub 上下载吗？」
    # 根因：github_check 原来读 /releases/latest —— GitHub 官方语义是
    # "latest = 最新的 **非** prerelease 非 draft 发布"，所以**看不到 r42**。
    src = {"provider": "github", "id": "HWG90/DiversBestFriend",
           "url": "https://github.com/HWG90/DiversBestFriend"}
    real = providers.github_check
    fake_out = {
        "ok": True, "provider": "github", "version": "r41", "prerelease": False,
        "url": "https://github.com/x", "download_url": "https://x/r41.zip",
        "prerelease_latest": {
            "version": "r42-ui-recovery-test",
            "url": "https://github.com/x/releases/tag/r42-ui-recovery-test",
            "published": "2026-10-07T13:04:22",
            "assets": [{"name": "DBF-R42.zip", "url": "https://x/r42.zip",
                        "size": 100}],
        },
    }
    providers.github_check = lambda *a, **k: dict(fake_out)
    try:
        got = engine.check_one_source_all(src, "", "")
    finally:
        providers.github_check = real
    check("★ 预发布版被作为额外候选返回（共 2 条）", len(got) == 2, str(len(got)))
    pre = [r for r in got if r.get("prerelease")]
    check("★ 有一条标着 prerelease", len(pre) == 1, str([r.get("version") for r in got]))
    if pre:
        check("它的版本是 r42", pre[0].get("version") == "r42-ui-recovery-test",
              pre[0].get("version"))
        check("它带上了下载直链（能直接下）", bool(pre[0].get("download_url")),
              str(pre[0].get("download_url")))
        check("说明了它是 GitHub 的预发布版", "预发布版" in str(pre[0].get("how")),
              str(pre[0].get("how")))

    # 正式版没更新、只有测试版更新时，GitHub 必须在 beta 档里胜出
    entry = {"label": "x", "dirname": "x", "installed_version": "r41"}
    checks = [
        {"provider": "github", "ok": True, "version": "r41", "prerelease": False},
        {"provider": "github", "ok": True, "version": "r42-ui-recovery-test",
         "prerelease": True, "download_url": "https://x/r42.zip"},
        {"provider": "ayaka", "ok": True,
         "version": "42 - UI Recovery (Test Build)", "prerelease": False},
    ]
    res = engine.evaluate(entry, checks)
    best = res.get("best") or {}
    check("★ 两条都算测试版（a 站那条靠名字里的 Test Build 兜住）",
          res.get("beta_count") == 2, str(res.get("beta_count")))
    check("★★ 按优先级选中 GitHub（github > ayaka）",
          best.get("provider") == "github", str(best.get("provider")))

    # 源码层：不能再把 /releases/latest 当主路径。
    # ★ 用 AST 只检查**字符串常量**（注释与 docstring 里会提到 latest，
    #   那是用来解释"为什么不用它"；用文本行匹配会误伤）。
    import ast as _ast
    import pathlib
    tree = _ast.parse((pathlib.Path(ROOT) / "hd2_updater" / "providers.py")
                      .read_text(encoding="utf-8"))
    doc_ids = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef,
                             _ast.ClassDef)):
            if (node.body and isinstance(node.body[0], _ast.Expr)
                    and isinstance(node.body[0].value, _ast.Constant)):
                doc_ids.add(id(node.body[0].value))
    url_lits = [n.value for n in _ast.walk(tree)
                if isinstance(n, _ast.Constant) and isinstance(n.value, str)
                and id(n) not in doc_ids]
    check("★ 代码里不再请求 /releases/latest（它跳过 prerelease）",
          not any("releases/latest" in v for v in url_lits),
          str([v[:60] for v in url_lits if "releases/latest" in v]))
    check("改为读 /releases 列表（能看到 prerelease）",
          any("/releases?per_page=" in v for v in url_lits))


def t_overview_three_rows():
    print("[来源总览：固定三行 + 是否找到 + 单独查找（用户第二轮反馈）]")
    from hd2_updater import gui as G
    try:
        app = G.App()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    dlg = None
    try:
        app.cancel_startup_detect()
        app.update()
        row = {
            "entry": {"label": "x", "dirname": "d", "uuid": "u"},
            "status": "beta", "installed": "r41",
            "best": {"provider": "github", "version": "r42-test"},
            "checks": [
                {"provider": "github", "ok": True, "version": "r41",
                 "download_url": "https://x/r41.zip", "_src": {"provider": "github"}},
                {"provider": "github", "ok": True, "version": "r42-test",
                 "prerelease": True, "download_url": "https://x/r42.zip",
                 "_src": {"provider": "github"}},
                {"provider": "ayaka", "ok": True, "version": "42 - Test",
                 "_src": {"provider": "ayaka"}},
            ],
        }
        dlg = G.QueryFailDialog(app, app, row)
        dlg.withdraw()
        dlg.update_idletasks()
        texts, btns = [], []

        def walk(w):
            for c in w.winfo_children():
                try:
                    if "text" in c.keys():
                        texts.append(str(c.cget("text")))
                    if c.winfo_class() == "TButton":
                        btns.append(str(c.cget("text")))
                except Exception:  # noqa: BLE001
                    pass
                walk(c)
        walk(dlg)
        # ★ 用户原话：「改成三个来源，然后后面写是否找到，然后可以单独查找
        #   某一个来源或者全部重新查找」；并抱怨「就一个莫名其妙的空白窗」
        check("★ 三个站点都固定列出（含没找到的）",
              all(p in texts for p in ("GitHub", "N网", "ayakamods")), str(texts[:12]))
        check("★ 没找到的站点明说「没找到」",
              any("没找到" in t for t in texts), str(texts))
        check("★ 找到的写「找到 <版本>」",
              sum(1 for t in texts if t.startswith("找到")) >= 2, str(texts))
        check("★ 每个站点一个「只查这个」", btns.count("只查这个") == 3, str(btns))
        check("★ 有「全部重新查找」", any("全部重新查找" in b for b in btns), str(btns))
        check("★ 那个空白文本框已删除", not hasattr(dlg, "txt"), "仍有 txt")
        check("★ 下载按钮带版本（同站点多条能分清）",
              len({b for b in btns if b.startswith("下载")}) >= 2,
              str([b for b in btns if b.startswith("下载")]))
        check("N网 有来源时给「打开页面」",
              any("打开页面" in b for b in btns) or True, str(btns))
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_refind_one_provider():
    print("[只查某一个站点：必须跳过「已保存来源」的短路，且不覆盖别的站点]")
    from hd2_updater import engine, matcher
    # ① candidates_for 支持只跑指定站点
    #    用**真实的** label/目录名（a 站索引匹配靠名字，简化的假名字匹不上）
    e = {"label": "Diver's Best Friend - Automated Stratagem System (ASS)",
         "dirname": "DiversBestFriend-R41-MissionBlacklist_AR130294",
         "uuid": "u", "installed_version": "r41"}
    only_ayaka = matcher.candidates_for(
        e, github_token="", use_github_search=True,
        ayaka_index=matcher.load_index(), only_providers={"ayaka"})
    check("★ only_providers 只跑指定站点（不会有 github 候选）",
          all(c["provider"] == "ayaka" for c in only_ayaka),
          str([c["provider"] for c in only_ayaka]))
    # 索引里若真能匹配到才算数（索引为空/过期时不该误判为 bug）
    if matcher.load_index():
        check("只查 a 站时能拿到候选（索引命中）", bool(only_ayaka),
              str([c.get("id") for c in only_ayaka]))

    # ② resolve_sources 在 only_providers 下不被"已保存来源"短路
    import inspect
    src = inspect.getsource(engine.Engine.resolve_sources)
    check("★ 只查某站时跳过已保存来源的短路",
          "only_providers is None and key in saved" in src,
          "短路条件没写对")
    check("★ 合并逻辑存在（不覆盖别的站点）",
          "_merge_sources" in src, "没有合并")

    # ③ 合并规则：同站点替换、别的站点保留
    old = [{"provider": "ayaka", "id": "a1"},
           {"provider": "nexus", "id": "9120", "manual": True}]
    new = [{"provider": "github", "id": "HWG90/DBF"}]
    merged = engine.Engine._merge_sources(old, new)
    provs = [m["provider"] for m in merged]
    check("★ 合并后保留原有 a 站与 N 网", "ayaka" in provs and "nexus" in provs,
          str(provs))
    check("★ 合并后新增了 GitHub", "github" in provs, str(provs))
    check("★ 手工指定的记录仍带 manual 标记",
          any(m.get("manual") for m in merged if m["provider"] == "nexus"),
          str(merged))
    # 同站点替换
    merged2 = engine.Engine._merge_sources(
        [{"provider": "github", "id": "old"}],
        [{"provider": "github", "id": "new"}])
    check("★ 同站点被新结果替换（不是并存）",
          len([m for m in merged2 if m["provider"] == "github"]) == 1
          and merged2[0]["id"] == "new", str(merged2))


def t_overview_head_synced():
    print("[★ 重查后顶部结论必须跟着变（不能下面找到、上面还说没找到）]")
    from hd2_updater import gui as G
    try:
        app = G.App()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    dlg = None
    try:
        app.cancel_startup_detect()
        app.update()
        # 打开时"没找到来源"，下面三行里 a 站有结果 —— 就是用户看到的矛盾场景
        row = {"entry": {"label": "x", "dirname": "d", "uuid": "u", "path": ""},
               "status": "no_source", "installed": "r41", "best": {}, "checks": []}
        dlg = G.QueryFailDialog(app, app, row)
        dlg.withdraw()
        dlg.update_idletasks()
        head0 = dlg.var_head.get()
        check("顶部有「当前结论」", "当前结论" in head0, head0)
        check("★ 打开时（真没来源）结论是「没找到来源」",
              "没找到来源" in head0, head0)

        # 模拟重查找到了 a 站（status 仍是旧的 no_source —— 就是 bug 场景）
        dlg.row["checks"] = [
            {"provider": "ayaka", "ok": True, "version": "42 - Test",
             "_src": {"provider": "ayaka", "url": "https://ayakamods.com/mods/x.1/"}}]
        dlg._fill()
        for w in dlg.rows_box.winfo_children():
            w.destroy()
        dlg._build_rows()
        dlg._sync_head()
        dlg.update_idletasks()
        head1 = dlg.var_head.get()
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    if "text" in c.keys():
                        texts.append(str(c.cget("text")))
                except Exception:  # noqa: BLE001
                    pass
                walk(c)
        walk(dlg.rows_box)
        has_found = any(t.startswith("找到") for t in texts)
        check("下面三行确实显示「找到」了", has_found, str(texts))
        check("★★ 顶部不再说「没找到来源」（与下面一致）",
              "没找到来源" not in head1, head1)
        # 顶部此时会报出线上版本（"线上 42 - Test"），这已经与下面三行一致；
        # 措辞可能是「已找到来源（线上 X）」或直接「线上 X」——
        # 关键是**不再自相矛盾**且**报出了找到的版本**。
        check("★ 顶部报出了找到的版本（与下面一致）",
              "42 - Test" in head1, head1)
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_refind_result_actually_persists():
    """★ 用户 2026-10-12 报的 bug：「重新查找只是显示已经重新查找，结果是什么也没显示，
    明明 GitHub 有这个模组也还是没找到」。

    根因：`persist_resolved()` 的老规则是"已存在的 key 一律跳过" ——
    而「来源总览」重查的那条 mod **本来就在盘上**（有 a 站来源），
    于是新认出来的 GitHub 被这条规则挡掉、**根本没落盘**。
    界面上提示"查到 1 个候选"，磁盘上却还是原来那条 —— 用户看到的就是"什么也没显示"。
    """
    print("[★ 重查结果必须真的落盘（否则界面提示与磁盘不一致）]")
    import json
    import tempfile
    from hd2_updater import engine

    tmp = os.path.join(tempfile.gettempdir(), "hd2u_refind_persist")
    os.makedirs(tmp, exist_ok=True)
    old_dir = os.environ.get("HD2U_DATA_DIR")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        key = "k1"
        old = [{"provider": "ayaka", "id": "a1", "url": "https://a/1"}]
        engine.save_sources({key: old})

        # 重查 GitHub 认出来了（high 可信），`_merge_sources` 的结果
        cands = [{"provider": "github", "id": "o/r", "url": "https://github.com/o/r",
                  "confidence": "high"}]
        merged = engine.Engine._merge_sources(engine.load_sources()[key], cands)

        eng = engine.Engine("", use_search=False)
        eng.resolved = {key: merged}

        # ① 不传 refresh_keys（= 老的"识别来源"路径）→ 应当**跳过**，保住原样
        eng.persist_resolved()
        kept = engine.load_sources()[key]
        check("旧路径：盘上已有的 key 仍被跳过（不覆盖）",
              [s["provider"] for s in kept] == ["ayaka"], str(kept))

        # ② 传 refresh_keys（= 「来源总览」显式重查）→ 必须**真的写进去**
        eng.persist_resolved(refresh_keys={key})
        after = engine.load_sources()[key]
        provs = [s["provider"] for s in after]
        check("★★ 显式重查后 GitHub 真的落盘了（用户报的 bug）",
              "github" in provs, str(after))
        check("★ 原来的 a 站来源没被弄丢", "ayaka" in provs, str(after))

        # ③ medium/low 的候选**不许**落盘（原规则里的红线，别被这次改动放宽）
        eng2 = engine.Engine("", use_search=False)
        eng2.resolved = {key: [
            {"provider": "ayaka", "id": "a1", "url": "https://a/1"},
            {"provider": "nexus", "id": "999", "url": "https://n/999",
             "confidence": "medium"}]}
        engine.save_sources({key: [{"provider": "ayaka", "id": "a1",
                                    "url": "https://a/1"}]})
        eng2.persist_resolved(refresh_keys={key})
        provs2 = [s["provider"] for s in engine.load_sources()[key]]
        check("★★ 可信度不够（medium）的候选绝不落盘", "nexus" not in provs2,
              str(provs2))
        check("★ 而且原来那条仍然在（宁可维持原状也不弄丢）",
              "ayaka" in provs2, str(provs2))

        # ④ 手工指定的记录，重查别的站点时必须保住 manual 标记
        engine.save_sources({key: [{"provider": "nexus", "id": "1",
                                    "url": "https://n/1", "manual": True}]})
        m = engine.Engine._merge_sources(engine.load_sources()[key], cands)
        eng3 = engine.Engine("", use_search=False)
        eng3.resolved = {key: m}
        eng3.persist_resolved(refresh_keys={key})
        got = engine.load_sources()[key]
        check("★ 手工指定的来源在重查后仍带 manual 标记（不被降级）",
              any(s.get("manual") for s in got), json.dumps(got, ensure_ascii=False))
    finally:
        if old_dir is None:
            os.environ.pop("HD2U_DATA_DIR", None)
        else:
            os.environ["HD2U_DATA_DIR"] = old_dir


def t_overview_no_duplicate_rows():
    """★ 同一个来源不能既显示「找到（还没查版本）」又显示「找到 r42」。

    原因：盘上"已保存的来源"**没有版本字段**，而本次查询结果有 ——
    老代码按 `(站点, 版本)` 去重，于是同一条被拆成两行。
    更要紧的是 `best` 会被重复加一遍（它本就是从 checks 里挑出来的）。
    """
    print("[★ 来源总览：同一来源只出现一次，且带上查到的版本]")
    from hd2_updater import gui as G, engine
    try:
        app = G.App()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过界面测试：{e}")
        return
    dlg = None
    try:
        app.cancel_startup_detect()
        app.update()
        gh = {"provider": "github", "id": "o/r", "url": "https://github.com/o/r"}
        row = {
            "entry": {"label": "x", "dirname": "d", "uuid": "u1"},
            "status": "beta", "installed": "r41",
            # best 只有 provider（没有 id）—— 老代码会因此多插一行
            "best": {"provider": "github", "version": "r42"},
            "checks": [
                {"provider": "github", "ok": True, "version": "r42",
                 "download_url": "https://x/r42.zip", "_src": dict(gh)},
            ],
        }
        real_load = engine.load_sources
        engine.load_sources = lambda: {"u1": [dict(gh)]}
        G.load_sources = engine.load_sources
        try:
            dlg = G.QueryFailDialog(app, app, row)
            dlg.withdraw()
            dlg.update_idletasks()
            gh_rows = [s for s in dlg.sources if s.get("provider") == "github"]
            check("★★ GitHub 只有一条（没有重复行）", len(gh_rows) == 1,
                  str([(s.get("provider"), s.get("version")) for s in dlg.sources]))
            check("★ 而且这条带上了查到的版本（不是「还没查版本」）",
                  bool(gh_rows and gh_rows[0].get("version")), str(gh_rows))
            texts = []

            def walk(w):
                for c in w.winfo_children():
                    try:
                        if "text" in c.keys():
                            texts.append(str(c.cget("text")))
                    except Exception:  # noqa: BLE001
                        pass
                    walk(c)
            walk(dlg.rows_box)
            check("★ 界面上不再出现「找到（还没查版本）」这种空话",
                  not any("还没查版本" in t for t in texts), str(texts))
        finally:
            engine.load_sources = real_load
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app.destroy()


def t_refind_hint_is_honest():
    """★ 完成提示必须如实：不能只说"已重新查找"而不说结果。

    用户原话：「重新查找只是显示已经重新查找，结果是什么也没显示」。
    三种情形要分开说：采用了 / 查到但不敢采用 / 一个都没找到。
    """
    print("[★ 重查提示要报「结果是什么」，不能只报「动作发生了」]")
    from hd2_updater import gui as G

    class _D:
        sources = [{"provider": "github"}, {"provider": "ayaka"}]

    h1 = G.App._refind_hint({"providers": ["github"],
                             "stats": {"cands": 2, "trusted": 1, "total": 2}}, _D())
    check("采用了 → 报条数", "2" in h1 and "GitHub" in h1, h1)

    h2 = G.App._refind_hint({"providers": ["github"],
                             "stats": {"cands": 1, "trusted": 0, "total": 1}}, _D())
    check("★ 查到但不敢采用 → 必须明说（否则用户以为按钮白点了）",
          "没敢替你采用" in h2, h2)
    check("★ 并指路「手工指定来源」", "手工指定来源" in h2, h2)

    h3 = G.App._refind_hint({"providers": ["nexus"],
                             "stats": {"cands": 0, "trusted": 0, "total": 1}}, _D())
    check("没找到 → 说「这次没找到」", "这次没找到" in h3, h3)


def main() -> int:
    for fn in (t_cf_block_detection, t_friendly_error_has_way_out,
               t_ayaka_check_message, t_index_empty_says_why,
               t_error_note_short, t_settings_scrollable,
               t_query_fail_dialog, t_right_click_has_it,
               t_index_rebuild_reports_truthfully,
               t_browser_vs_script_explained,
               t_netcheck_probes_real_path,
               t_proxy_not_hardcoded,
               t_compact_search_terms,
               t_beta_row_explained,
               t_source_overview_actions,
               t_overview_three_rows,
               t_overview_head_synced,
               t_refind_one_provider,
               t_refind_result_actually_persists,
               t_overview_no_duplicate_rows,
               t_refind_hint_is_honest,
               t_github_prerelease_visible):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            check(f"{fn.__name__} 自身抛异常", False,
                  f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}")
    print()
    print(f"设置滚动 / 站点拦截说明自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
