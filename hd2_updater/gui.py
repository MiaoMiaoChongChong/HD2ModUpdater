# -*- coding: utf-8 -*-
"""HD2 Mod 更新管理器 —— 中文图形界面。

设计要点：
  - 全程中文
  - 启动自动探测 Arsenal 位置，失败才让用户选（智能填入）
  - 每个 mod 显示：本地版本 / 线上版本 / 来源站点 / 状态
  - 能自动下载的直接下载（GitHub），需登录的走浏览器
  - 找不到来源的可手动填
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, matcher
from .arsenal import (
    default_downloads_dir,
    find_data_file,
    load_library,
    tool_downloads_dir,
    write_config,
)
from .engine import (
    ST_BETA,
    ST_ERROR,
    ST_NO_SOURCE,
    ST_OK,
    ST_UNCHECKED,
    ST_UPDATE,
    ST_UNKNOWN,
    Engine,
    clear_auto_sources,
    download,
    load_sources,
    open_in_browser,
    save_sources,
    set_manual_source,
    source_key,
)

# 配色（深色，贴近 Arsenal 的观感）
BG = "#1b1d21"
BG2 = "#24272c"
BG3 = "#2d3138"
FG = "#e6e6e6"
FG_DIM = "#9aa0a6"
ACCENT = "#c8a03c"      # 绝地潜兵的黄
GREEN = "#5fbf6a"
RED = "#e06060"
BLUE = "#5b9bd5"
ORANGE = "#e0a35c"

STATUS_TEXT = {
    ST_UPDATE: ("有更新", ORANGE),
    ST_BETA: ("仅测试版", BLUE),
    ST_OK: ("已最新", GREEN),
    ST_UNKNOWN: ("版本存疑", FG_DIM),
    ST_ERROR: ("查询失败", RED),
    ST_NO_SOURCE: ("未找到来源", FG_DIM),
    ST_UNCHECKED: ("未检查", FG_DIM),
}
PROV_NAME = {"github": "GitHub", "nexus": "N网", "ayaka": "ayakamods"}

# 悬停提示（鼠标放上去显示的解释）
HOVER_TIPS = {
    "btn_check": "检查所有模组的更新。\n"
                 "会先识别每个模组的来源（如果还没识别过），再逐个网站查最新版本。\n\n"
                 "第一次使用或刚加了新模组时，点这个就够了。",
    "btn_find": "只识别来源，不查版本。\n\n"
                "给每个模组找出它在网上对应哪个页面（GitHub / N网 / ayakamods），\n"
                "结果会保存下来，以后不用重复找。\n\n"
                "什么时候点：\n"
                "· 第一次在新电脑上用\n"
                "· 刚往 Arsenal 里加了新模组\n"
                "· 某些模组显示「未找到来源」\n\n"
                "识别完再点「检查更新」就会去查版本。",
    "btn_dl": "把「有更新」且能直接下载的（GitHub 的）全部下载到\n"
              "本工具的 downloads 文件夹，并校验 sha256。\n"
              "N网 / ayakamods 需要登录，不会在这里下。",
    "btn_index": "重建站点索引 = 把 ayakamods 上全部 HD2 模组的清单爬下来，\n"
                 "存成本地索引（约 505 个）。\n\n"
                 "为什么需要：ayakamods 不提供搜索接口（对脚本返回 403），\n"
                 "所以只能先把整站列表抓下来，之后在本地做名字匹配，\n"
                 "用来自动识别你的模组对应站上哪个页面。\n\n"
                 "索引会自动缓存（超过 14 天才需要重建），\n"
                 "平时不用点它 —— 「识别来源」会自动处理。\n"
                 "只有在 ayakamods 上架了很多新模组、导致识别不到时，才需要手动重建。",
    "btn_settings": "填写 N网 API key、GitHub token，\n查看下载位置，或清空已识别的来源。",
    "chk_search": "勾选＝检查时自动帮你搜索并识别每个模组的来源页面。\n"
                  "取消勾选＝只用已经确认过的来源，不做新的搜索（更快）。\n"
                  "默认勾选。",
    "ent_arsenal": "HD2 Arsenal 的模组库位置。\n"
                   "程序会自动探测，探测不到才需要手动选择。",
}


class Tooltip:
    """轻量悬停提示（tkinter 自带没有，这里自己实现一个）。"""

    def __init__(self, widget, text: str, delay: int = 420) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _ev=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:  # noqa: BLE001
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 14
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:  # noqa: BLE001
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        frame = tk.Frame(self._tip, bg="#3a3f47", bd=1, relief="solid")
        frame.pack()
        tk.Label(frame, text=self.text, justify="left", bg="#3a3f47", fg=FG,
                 font=("Microsoft YaHei UI", 9), padx=8, pady=6).pack()

    def _hide(self, _ev=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._tip = None


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{__version__}")
        self.configure(bg=BG)
        self.geometry("1180x720")
        self.minsize(940, 560)

        self.data_file: str | None = None
        self.library: list[dict] = []
        self.results: list[dict] = []
        self.engine: Engine | None = None
        self.msgq: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self._row_widgets: list[dict] = []

        self.var_arsenal = tk.StringVar(value="（正在探测…）")
        self.var_nexus_key = tk.StringVar()
        self.var_github_token = tk.StringVar()
        self.var_use_search = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="就绪")
        self.var_summary = tk.StringVar(value="")

        self._build_ui()
        self.after(120, self._auto_detect)
        self.after(150, self._pump)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=BG3)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=BG2)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Dim.TLabel", background=BG, foreground=FG_DIM)
        st.configure("Head.TLabel", background=BG, foreground=ACCENT,
                     font=("Microsoft YaHei UI", 13, "bold"))
        st.configure("TButton", background=BG3, foreground=FG, borderwidth=0, padding=(10, 5))
        st.map("TButton", background=[("active", "#3a3f47"), ("disabled", "#2a2d32")])
        st.configure("Accent.TButton", background=ACCENT, foreground="#1b1d21",
                     font=("Microsoft YaHei UI", 10, "bold"))
        st.map("Accent.TButton", background=[("active", "#ddb44a"), ("disabled", "#5a5340")])
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("TEntry", fieldbackground=BG3, foreground=FG, insertcolor=FG)
        st.configure("Treeview", background=BG2, fieldbackground=BG2, foreground=FG,
                     rowheight=26, borderwidth=0)
        st.configure("Treeview.Heading", background=BG3, foreground=FG, relief="flat")
        st.map("Treeview", background=[("selected", "#3d4450")])
        st.configure("TProgressbar", background=ACCENT, troughcolor=BG3, borderwidth=0)

        # ---------- 顶部：Arsenal 位置 ----------
        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Arsenal 模组库位置", style="Dim.TLabel").pack(anchor="w")
        row = ttk.Frame(top)
        row.pack(fill="x", pady=(3, 0))
        self.ent_arsenal = tk.Entry(
            row, textvariable=self.var_arsenal, bg=BG3, fg=FG,
            insertbackground=FG, relief="flat", font=("Consolas", 9),
        )
        self.ent_arsenal.pack(side="left", fill="x", expand=True, ipady=5)
        Tooltip(self.ent_arsenal, HOVER_TIPS["ent_arsenal"])
        b1 = ttk.Button(row, text="重新探测", command=self._auto_detect)
        b1.pack(side="left", padx=(6, 0))
        Tooltip(b1, "重新自动查找 Arsenal 的模组库（会检查默认位置和各个盘）。")
        b2 = ttk.Button(row, text="手动选择…", command=self._pick_arsenal)
        b2.pack(side="left", padx=(6, 0))
        Tooltip(b2, "自动找不到时，手动指定 hd2a_data.json 所在位置。")

        # ---------- 操作栏 ----------
        bar = ttk.Frame(self, padding=(12, 6))
        bar.pack(fill="x")
        self.btn_check = ttk.Button(bar, text="检查更新", style="Accent.TButton",
                                    command=self._start_check)
        self.btn_check.pack(side="left")
        Tooltip(self.btn_check, HOVER_TIPS["btn_check"])

        # 单独把「识别来源」暴露出来：新电脑第一次用、或加了新 mod 时，
        # 用户需要一个明确入口，而不是只能靠「检查更新」顺带做掉。
        self.btn_find = ttk.Button(bar, text="识别来源", command=self._find_sources)
        self.btn_find.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_find, HOVER_TIPS["btn_find"])

        self.btn_dl = ttk.Button(bar, text="下载全部可自动下载的", command=self._download_all,
                                 state="disabled")
        self.btn_dl.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_dl, HOVER_TIPS["btn_dl"])
        self.btn_index = ttk.Button(bar, text="重建站点索引", command=self._rebuild_index)
        self.btn_index.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_index, HOVER_TIPS["btn_index"])
        self.btn_settings = ttk.Button(bar, text="设置…", command=self._open_settings)
        self.btn_settings.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_settings, HOVER_TIPS["btn_settings"])

        # 「智能搜索来源」用勾选框会和「叉号」混淆，改成明确的双态按钮
        self.btn_search_mode = ttk.Button(bar, command=self._toggle_search_mode)
        self.btn_search_mode.pack(side="left", padx=(14, 0))
        Tooltip(self.btn_search_mode, HOVER_TIPS["chk_search"])
        self._sync_search_mode()

        # ---------- 上手提示条（首次使用/未识别时显示）----------
        self.hintbar = tk.Frame(self, bg="#3a3427")
        self.hint_label = tk.Label(
            self.hintbar, bg="#3a3427", fg="#e8d9a8", anchor="w", justify="left",
            font=("Microsoft YaHei UI", 9), padx=10, pady=6,
        )
        self.hint_label.pack(side="left", fill="x", expand=True)
        self.hint_btn = ttk.Button(self.hintbar, text="开始识别来源",
                                   style="Accent.TButton", command=self._find_sources)
        self.hint_btn.pack(side="right", padx=8, pady=4)
        self._hint_shown = False   # 由 _update_hint() 控制显示

        self.prog = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.prog.pack(fill="x", padx=12, pady=(2, 0))

        # ---------- 表格 ----------
        wrap = ttk.Frame(self, padding=(12, 8))
        wrap.pack(fill="both", expand=True)

        cols = ("status", "label", "local", "remote", "site", "note")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        heads = {
            "status": ("状态", 90), "label": ("模组", 330), "local": ("本地", 90),
            "remote": ("线上", 130), "site": ("来源", 110), "note": ("说明", 380),
        }
        for c, (t, w) in heads.items():
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c in ("label", "note") else "center")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        for st_name, col in STATUS_TEXT.values():
            self.tree.tag_configure(st_name, foreground=col)
        self.tree.bind("<Double-1>", self._on_row_activate)
        self.tree.bind("<Button-3>", self._on_right_click)

        # ---------- 底部 ----------
        bot = ttk.Frame(self, padding=(12, 6, 12, 10))
        bot.pack(fill="x")
        ttk.Label(bot, textvariable=self.var_status, style="Dim.TLabel").pack(side="left")
        ttk.Label(bot, textvariable=self.var_summary, style="Dim.TLabel").pack(side="right")
        tip = ("提示：双击一行可打开其发布页；右键可复制链接 / 指定来源 / "
               "打开下载目录")
        ttk.Label(self, text=tip, style="Dim.TLabel").pack(anchor="w", padx=14, pady=(0, 6))

        # 右键菜单
        self.menu = tk.Menu(self, tearoff=0, bg=BG3, fg=FG,
                            activebackground=ACCENT, activeforeground="#1b1d21")
        self.menu.add_command(label="打开发布页", command=self._ctx_open)
        self.menu.add_command(label="用浏览器下载（接力）", command=self._ctx_browser)
        self.menu.add_command(label="直接下载这个", command=self._ctx_download)
        self.menu.add_separator()
        self.menu.add_command(label="复制链接", command=self._ctx_copy)
        self.menu.add_command(label="指定来源…", command=self._ctx_set_source)
        self.menu.add_separator()
        self.menu.add_command(label="打开下载目录", command=self._open_dl_dir)

    def _toggle_search_mode(self) -> None:
        """在「智能搜索：开 / 关」之间切换。"""
        self.var_use_search.set(not self.var_use_search.get())
        self._sync_search_mode()

    def _sync_search_mode(self) -> None:
        """把按钮文字同步成当前状态（避免勾/叉的歧义）。"""
        on = bool(self.var_use_search.get())
        self.btn_search_mode.configure(
            text=("智能搜索来源：开 ✓" if on else "智能搜索来源：关 ✗"),
            style=("Accent.TButton" if on else "TButton"),
        )

    # ------------------------------------------------------- Arsenal 定位
    def _auto_detect(self) -> None:
        path, how = find_data_file()
        if path:
            self.data_file = path
            self.var_arsenal.set(path)
            try:
                self.library = load_library(path)
                self.var_status.set(f"已找到 Arsenal 模组库（{how}），共 {len(self.library)} 个模组")
                self._populate()
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("读取失败", f"读不了这个文件：\n{e}")
        else:
            self.var_arsenal.set("")
            self.var_status.set("没找到 Arsenal 模组库，请手动选择位置")
            self._pick_arsenal(silent_if_cancel=True)

    def _pick_arsenal(self, silent_if_cancel: bool = False) -> None:
        p = filedialog.askdirectory(
            title="请选择 HD2 Arsenal 的数据目录（内含 hd2a_data.json），或直接选 hd2a_data.json"
        )
        if not p:
            if not silent_if_cancel:
                return
            p = filedialog.askopenfilename(
                title="或直接选择 hd2a_data.json",
                filetypes=[("Arsenal 数据", "hd2a_data.json"), ("JSON", "*.json")],
            )
            if not p:
                self.var_status.set("未选择位置，无法继续")
                return
        path, how = find_data_file(p)
        if not path:
            messagebox.showwarning("没找到",
                                   "这个位置里没有 hd2a_data.json。\n\n"
                                   "通常它在：\n%LOCALAPPDATA%\\hd2arsenal\\\n\n"
                                   "你也可以直接选中该文件本身。")
            return
        self.data_file = path
        self.var_arsenal.set(path)
        write_config({"arsenal_data_path": path})
        try:
            self.library = load_library(path)
            self.var_status.set(f"已载入 {len(self.library)} 个模组")
            self._populate()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("读取失败", str(e))

    # ---------------------------------------------------------- 表格填充
    def _populate(self, results: list[dict] | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_widgets.clear()

        if results is None:
            # 还没检查：状态是「未检查」，但**要把已知来源显示出来**，
            # 免得表格说"没来源"、双击点进去却又看到来源（自相矛盾）。
            saved = load_sources()
            rows = []
            for e in self.library:
                srcs = saved.get(source_key(e)) or []
                known = None
                if srcs:
                    s0 = srcs[0]
                    known = {
                        "provider": s0.get("provider", ""),
                        "url": s0.get("url") or "",
                        "how": "已保存的来源（还没检查版本）",
                        "manual": bool(s0.get("manual")),
                    }
                    if not known["url"]:
                        if s0.get("provider") == "nexus" and s0.get("id"):
                            known["url"] = (
                                f"https://www.nexusmods.com/helldivers2/mods/{s0['id']}"
                            )
                        elif s0.get("provider") == "github" and s0.get("id"):
                            known["url"] = f"https://github.com/{s0['id']}"
                        elif s0.get("provider") == "ayaka":
                            known["url"] = (f"https://ayakamods.com/mods/"
                                            f"{str(s0.get('id','')).strip('/')}/")
                rows.append(
                    {
                        "entry": e,
                        "status": ST_UNCHECKED if known else ST_NO_SOURCE,
                        "installed": e.get("installed_version", ""),
                        "best": known,
                        "checks": [],
                    }
                )
        else:
            rows = results

        # 排序：有更新的排最前
        order = {ST_UPDATE: 0, ST_BETA: 1, ST_ERROR: 2, ST_UNKNOWN: 3,
                 ST_NO_SOURCE: 4, ST_UNCHECKED: 5, ST_OK: 6}
        rows = sorted(rows, key=lambda r: order.get(r["status"], 9))

        for r in rows:
            e = r["entry"]
            label = e.get("label") or e.get("dirname") or "(未命名)"
            best = r.get("best") or {}
            remote = best.get("version") or ""
            site = PROV_NAME.get(best.get("provider", ""), "")
            txt, _ = STATUS_TEXT.get(r["status"], ("?", FG))

            note = ""
            if r["status"] == ST_UPDATE:
                note = ("可自动下载" if best.get("download_url")
                        else "需在浏览器登录后下载")
                if best.get("prerelease"):
                    note += "（测试版）"
            elif r["status"] == ST_BETA:
                note = "只有测试版可更，自行决定"
            elif r["status"] == ST_ERROR:
                note = (best.get("error") or "")[:70]
            elif r["status"] == ST_NO_SOURCE:
                note = "没找到来源 —— 右键「指定来源…」填一次"
            elif r["status"] == ST_UNCHECKED:
                note = "还没检查 —— 点上面的「检查更新」"
            elif r["status"] == ST_OK:
                note = "无需处理"
            if best.get("how") and r["status"] in (ST_UPDATE, ST_OK):
                note = (best.get("how") or "")[:70]

            # 手工指定的来源，在说明里标出来
            checks = r.get("checks") or []
            if checks and any((c.get("_src") or {}).get("manual") for c in checks):
                note = ("【手工指定】" + note).strip()

            iid = self.tree.insert(
                "", "end", values=(txt, label[:60], r.get("installed") or "?", remote, site, note),
                tags=(STATUS_TEXT.get(r["status"], ("",))[0],),
            )
            self._row_widgets.append({"iid": iid, "row": r, "entry": e})

        n = len(rows)
        upd = sum(1 for r in rows if r["status"] == ST_UPDATE)
        beta = sum(1 for r in rows if r["status"] == ST_BETA)
        nosrc = sum(1 for r in rows if r["status"] == ST_NO_SOURCE)
        err = sum(1 for r in rows if r["status"] == ST_ERROR)
        unchecked = sum(1 for r in rows if r["status"] == ST_UNCHECKED)
        parts = [f"共 {n} 个"]
        if upd:
            parts.append(f"有更新 {upd}")
        if beta:
            parts.append(f"仅测试版 {beta}")
        if err:
            parts.append(f"失败 {err}")
        if nosrc:
            parts.append(f"未识别 {nosrc}")
        if unchecked:
            parts.append(f"未检查 {unchecked}")
        self.var_summary.set("　|　".join(parts))
        self.btn_dl.configure(state=("normal" if upd else "disabled"))
        self._update_hint(rows)

    def _row_of(self, iid: str) -> dict | None:
        for w in self._row_widgets:
            if w["iid"] == iid:
                return w
        return None

    # ------------------------------------------------------------ 检查
    def _update_hint(self, rows: list[dict] | None = None) -> None:
        """按情况显示/隐藏上手指引条。

        最需要引导的两种情况：
          1. 从没识别过任何来源（新电脑）—— 告诉用户点这个开始
          2. 还有模组没找到来源 —— 告诉用户可以补一次
        """
        if rows is None:
            rows = [
                {"status": ST_UNCHECKED if load_sources().get(source_key(e))
                 else ST_NO_SOURCE}
                for e in self.library
            ]
        if not rows:
            # 还没有模组（Arsenal 位置没选对）—— 不显示引导，先让用户选位置
            if self._hint_shown:
                self.hintbar.pack_forget()
                self._hint_shown = False
            return

        saved = load_sources()
        nosrc = sum(1 for r in rows if r.get("status") == ST_NO_SOURCE)
        total = len(rows)
        # 「第一次用」= 来源表完全是空的（一条都没存过）
        first_time = not saved

        msg = ""
        if first_time:
            msg = (f"第一次使用：还没有识别过模组来源。"
                   f"点右边「开始识别来源」，我会自动找出这 {total} 个模组"
                   f"各自在网上对应哪个页面（GitHub / N网 / ayakamods）。")
        elif nosrc:
            msg = (f"有 {nosrc} 个模组没找到来源。"
                   f"点右边可以再识别一次；个别认不出来的，"
                   f"右键「指定来源…」手动填。")

        if msg:
            self.hint_label.configure(text=msg)
            if not self._hint_shown:
                self.hintbar.pack(fill="x", padx=12, pady=(0, 4),
                                  before=self.prog)
                self._hint_shown = True
        elif self._hint_shown:
            self.hintbar.pack_forget()
            self._hint_shown = False

    # ------------------------------------------------------------ 识别来源
    def _find_sources(self) -> None:
        """只做来源识别（不查版本），结果保存下来。"""
        if not self.data_file:
            messagebox.showwarning("先选位置", "还没找到 Arsenal 模组库。")
            return
        if self.worker and self.worker.is_alive():
            return
        self.btn_find.configure(state="disabled", text="识别中…")
        self.btn_check.configure(state="disabled")
        self.prog.configure(value=0)

        eng = Engine(
            self.data_file,
            api_key=self.var_nexus_key.get().strip(),
            github_token=self.var_github_token.get().strip(),
            use_search=True,          # 识别来源必须搜索，无视开关
            log=lambda m: self.msgq.put(("log", m)),
            progress=lambda d, t, m: self.msgq.put(("prog", (d, t, m))),
        )
        self.engine = eng

        def run() -> None:
            try:
                eng.load()
                eng.resolve_sources()
                if eng.cancelled:
                    return
                eng.persist_resolved()
                n = sum(1 for v in eng.resolved.values() if v)
                self.msgq.put(("found", (n, len(eng.library))))
            except Exception as e:  # noqa: BLE001
                import traceback
                self.msgq.put(("error", f"{e}\n\n{traceback.format_exc()[:900]}"))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _start_check(self) -> None:
        if not self.data_file:
            messagebox.showwarning("先选位置", "还没找到 Arsenal 模组库。")
            return
        if self.worker and self.worker.is_alive():
            return
        self.btn_check.configure(state="disabled", text="检查中…")
        self.prog.configure(value=0)
        self.engine = Engine(
            self.data_file,
            api_key=self.var_nexus_key.get().strip(),
            github_token=self.var_github_token.get().strip(),
            use_search=self.var_use_search.get(),
            log=lambda m: self.msgq.put(("log", m)),
            progress=lambda d, t, m: self.msgq.put(("prog", (d, t, m))),
        )

        def run() -> None:
            try:
                self.engine.load()
                self.msgq.put(("log", "开始解析来源…"))
                self.engine.resolve_sources()
                if self.engine.cancelled:
                    return
                self.msgq.put(("log", "开始检查更新…"))
                res = self.engine.check()
                self.engine.persist_resolved()
                self.msgq.put(("done", res))
            except Exception as e:  # noqa: BLE001
                import traceback
                self.msgq.put(("error", f"{e}\n\n{traceback.format_exc()[:900]}"))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _pump(self) -> None:
        """主线程消费后台消息。"""
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "prog":
                    d, t, m = payload
                    self.prog.configure(maximum=max(1, t), value=d)
                    self.var_status.set(f"{m}  ({d}/{t})")
                elif kind == "log":
                    self.var_status.set(str(payload))
                elif kind == "done":
                    self.btn_check.configure(state="normal", text="检查更新")
                    self.prog.configure(value=0)
                    self.results = payload
                    self._populate(payload)
                    upd = sum(1 for r in payload if r["status"] == ST_UPDATE)
                    self.var_status.set(
                        f"检查完成：{upd} 个有更新" if upd else "检查完成：没有正式版更新"
                    )
                elif kind == "found":
                    n, total = payload
                    self.btn_find.configure(state="normal", text="识别来源")
                    self.btn_check.configure(state="normal")
                    self.prog.configure(value=0)
                    self.var_status.set(f"来源识别完成：{n}/{total} 个已识别")
                    # 重新读一遍来源表，刷新表格里的「来源」列
                    self._populate()
                    if n < total:
                        messagebox.showinfo(
                            "识别完成",
                            f"已识别 {n}/{total} 个模组的来源。\n\n"
                            f"剩下 {total - n} 个没找到 —— 这些右键「指定来源…」"
                            f"手动填一次即可（填一次就长期记住）。\n\n"
                            f"现在可以点「检查更新」去查版本了。",
                        )
                    else:
                        messagebox.showinfo(
                            "识别完成",
                            f"{total} 个模组的来源全部识别到了。\n\n"
                            f"现在点「检查更新」就会去查每个网站上的最新版本。",
                        )
                elif kind == "error":
                    self.btn_check.configure(state="normal", text="检查更新")
                    self.var_status.set("出错了")
                    messagebox.showerror("检查失败", str(payload))
                elif kind == "dl":
                    self.var_status.set(str(payload))
                elif kind == "dl_done":
                    self.btn_dl.configure(state="normal", text="下载全部可自动下载的")
                    self.var_status.set(str(payload))
                    messagebox.showinfo("下载完成", str(payload))
        except queue.Empty:
            pass
        self.after(150, self._pump)

    # ------------------------------------------------------------ 下载
    def _download_all(self) -> None:
        targets = [
            r for r in self.results
            if r["status"] == ST_UPDATE and (r.get("best") or {}).get("download_url")
        ]
        if not targets:
            messagebox.showinfo("没有可自动下载的",
                                "有更新的模组里没有可直接下载的。\n\n"
                                "N网 / ayakamods 需要登录，请用右键「用浏览器下载」。")
            return
        self.btn_dl.configure(state="disabled", text="下载中…")
        dest = tool_downloads_dir()

        def run() -> None:
            ok = 0
            for r in targets:
                b = r["best"]
                e = r["entry"]
                name = e.get("label") or e.get("dirname")
                self.msgq.put(("dl", f"正在下载：{name}"))
                try:
                    p = download(
                        b["download_url"], dest, b.get("asset_name", ""),
                        b.get("sha256", ""),
                        progress=lambda g, t, _n=name: self.msgq.put(
                            ("dl", f"下载 {_n}：{g//1024}KB" + (f"/{t//1024}KB" if t else ""))
                        ),
                    )
                    ok += 1
                    self.msgq.put(("dl", f"✓ {os.path.basename(p)}"))
                except Exception as ex:  # noqa: BLE001
                    self.msgq.put(("dl", f"✗ {name} 失败：{ex}"))
            self.msgq.put((
                "dl_done",
                f"下载完成 {ok}/{len(targets)} 个。\n\n文件在：\n{dest}\n\n"
                f"接下来：打开 Arsenal，用 Add mods / Import 把这些 ZIP 导入，"
                f"然后把旧的那份禁用并删除。",
            ))

        threading.Thread(target=run, daemon=True).start()

    # ---------------------------------------------------------- 右键操作
    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        return self._row_of(sel[0]) if sel else None

    def _on_right_click(self, ev) -> None:
        iid = self.tree.identify_row(ev.y)
        if iid:
            self.tree.selection_set(iid)
            try:
                self.menu.tk_popup(ev.x_root, ev.y_root)
            finally:
                self.menu.grab_release()

    def _on_row_activate(self, _ev) -> None:
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        url = best.get("url") or best.get("release_url")
        if url:
            open_in_browser(url)
        else:
            self._ctx_set_source()

    def _ctx_open(self) -> None:
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        url = best.get("release_url") or best.get("url")
        if url:
            open_in_browser(url)
        else:
            messagebox.showinfo("没有链接", "这个模组还没找到来源。")

    def _ctx_browser(self) -> None:
        """浏览器接力：打开页面 + 监视下载目录。"""
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        url = best.get("url") or best.get("release_url")
        if not url:
            messagebox.showinfo("没有链接", "先给这个模组指定来源。")
            return
        open_in_browser(url)
        messagebox.showinfo(
            "已打开页面",
            "已在浏览器打开该模组的页面。\n\n"
            "请点页面上的下载按钮（你已登录，不用重新输密码），\n"
            "下完后文件会在你的「下载」文件夹里，\n"
            "用「下载全部可自动下载的」旁的右键菜单，或手动导入 Arsenal。",
        )

    def _ctx_download(self) -> None:
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        if not best.get("download_url"):
            messagebox.showinfo(
                "不能直接下载",
                "这个来源需要登录才能下（N网 / ayakamods）。\n"
                "请用「用浏览器下载」。",
            )
            return
        dest = tool_downloads_dir()

        def run() -> None:
            try:
                p = download(best["download_url"], dest, best.get("asset_name", ""),
                             best.get("sha256", ""))
                self.msgq.put(("dl_done", f"已下载：\n{p}"))
            except Exception as ex:  # noqa: BLE001
                self.msgq.put(("dl_done", f"下载失败：{ex}"))

        threading.Thread(target=run, daemon=True).start()

    def _ctx_copy(self) -> None:
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        url = best.get("download_url") or best.get("url") or ""
        if url:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.var_status.set("链接已复制到剪贴板")

    def _ctx_set_source(self) -> None:
        """手动指定来源。"""
        w = self._selected()
        if not w:
            return
        SourceDialog(self, w["entry"], on_saved=self._after_source_change)

    def _after_source_change(self) -> None:
        self.var_status.set("来源已保存，重新「检查更新」即可生效")

    def _open_dl_dir(self) -> None:
        d = tool_downloads_dir()
        try:
            os.startfile(d)  # noqa: S606
        except Exception:  # noqa: BLE001
            messagebox.showinfo("下载目录", d)

    # ---------------------------------------------------------- 其他
    def _rebuild_index(self) -> None:
        self.var_status.set("正在重建 ayakamods 索引（可能要一两分钟）…")

        def run() -> None:
            try:
                items = matcher.rebuild_index(
                    progress=lambda m: self.msgq.put(("log", m))
                )
                self.msgq.put(("log", f"索引重建完成：{len(items)} 个模组"))
            except Exception as e:  # noqa: BLE001
                self.msgq.put(("log", f"索引重建失败：{e}"))

        threading.Thread(target=run, daemon=True).start()

    def _open_settings(self) -> None:
        SettingsDialog(self)


class SourceDialog(tk.Toplevel):
    """手动指定来源。"""

    def __init__(self, master: App, entry: dict, on_saved=None) -> None:
        super().__init__(master)
        self.title("指定来源")
        self.configure(bg=BG)
        self.geometry("620x360")
        self.resizable(False, False)
        self.entry_data = entry
        self.on_saved = on_saved
        self.transient(master)
        self.grab_set()

        key = source_key(entry)
        saved = load_sources().get(key) or []

        ttk.Label(self, text=f"模组：{entry.get('label') or entry.get('dirname')}",
                  style="Head.TLabel").pack(anchor="w", padx=14, pady=(12, 2))
        ttk.Label(self, text=f"本地版本：{entry.get('installed_version') or '未知'}　"
                             f"目录：{entry.get('dirname')}",
                  style="Dim.TLabel", wraplength=580).pack(anchor="w", padx=14)

        ttk.Label(self, text="站点", style="TLabel").pack(anchor="w", padx=14, pady=(12, 2))
        self.var_prov = tk.StringVar(value=(saved[0]["provider"] if saved else "github"))
        pf = ttk.Frame(self)
        pf.pack(anchor="w", padx=14)
        for val, txt in (("github", "GitHub"), ("nexus", "Nexus（N网）"), ("ayaka", "ayakamods")):
            ttk.Radiobutton(pf, text=txt, value=val, variable=self.var_prov).pack(side="left", padx=(0, 14))

        ttk.Label(self, text="标识（GitHub 填 作者/仓库；N网填数字 ID；ayakamods 填页面地址或 slug）",
                  style="Dim.TLabel", wraplength=580).pack(anchor="w", padx=14, pady=(12, 2))
        self.var_id = tk.StringVar(
            value=(saved[0].get("id") or saved[0].get("url") or "") if saved else ""
        )
        tk.Entry(self, textvariable=self.var_id, bg=BG3, fg=FG, insertbackground=FG,
                 relief="flat", font=("Consolas", 10)).pack(fill="x", padx=14, ipady=5)

        ttk.Label(self, text="示例：  SkyeShade/HD2Runtime　|　15298　|　"
                             "https://ayakamods.com/mods/hd2runtime.4260/",
                  style="Dim.TLabel").pack(anchor="w", padx=14, pady=(4, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=14, pady=16)
        ttk.Button(btns, text="保存", style="Accent.TButton",
                   command=self._save).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="用浏览器打开验证",
                   command=self._verify).pack(side="left")

    def _save(self) -> None:
        prov = self.var_prov.get()
        ident = self.var_id.get().strip()
        if not ident:
            messagebox.showwarning("填一下", "标识不能为空。", parent=self)
            return
        src = {"provider": prov, "id": ident, "url": ""}
        if prov == "ayaka":
            if ident.startswith("http"):
                src["url"] = ident
            else:
                src["url"] = f"https://ayakamods.com/mods/{ident.strip('/')}/"
            src["id"] = ident
        elif prov == "github":
            src["id"] = ident.replace("https://github.com/", "").strip("/")
        elif prov == "nexus":
            import re
            m = re.search(r"(\d+)", ident)
            if not m:
                messagebox.showwarning("格式不对", "N网要填数字 ID，例如 15298。", parent=self)
                return
            src["id"] = m.group(1)

        key = source_key(self.entry_data)
        set_manual_source(self.entry_data, src)   # 打上 manual 标记，受保护
        if self.on_saved:
            self.on_saved()
        messagebox.showinfo(
            "已保存",
            "来源已保存（标记为「手工指定」）。\n\n"
            "以后自动识别不会覆盖它，「清空来源缓存」也不会删掉它。\n"
            "重新点「检查更新」即可生效。",
            parent=self.master,
        )
        self.destroy()

    def _verify(self) -> None:
        prov = self.var_prov.get()
        ident = self.var_id.get().strip()
        if not ident:
            return
        if prov == "ayaka":
            url = ident if ident.startswith("http") else f"https://ayakamods.com/mods/{ident.strip('/')}/"
        elif prov == "github":
            url = "https://github.com/" + ident.replace("https://github.com/", "").strip("/")
        else:
            import re
            m = re.search(r"(\d+)", ident)
            url = f"https://www.nexusmods.com/helldivers2/mods/{m.group(1)}" if m else ""
        if url:
            open_in_browser(url)


class SettingsDialog(tk.Toplevel):
    """设置：N网 API key、GitHub token 等。"""

    # 权威来源：Nexus 官方帮助文档《API Acceptable Use Policy》明确写
    #   "All users have the ability to generate a Personal API key,
    #    from the API Access page" —— 指向下面这个地址。
    NEXUS_KEY_URL = "https://www.nexusmods.com/users/myaccount?tab=api"
    NEXUS_HELP_URL = "https://help.nexusmods.com/article/114-api-acceptable-use-policy"
    # GitHub 令牌页。依据 GitHub 官方文档《Managing your personal access tokens》：
    # 该文档整篇唯一引用的令牌地址就是 settings/personal-access-tokens/new，
    # 并明确 "recommends that you use fine-grained personal access tokens
    # instead of personal access tokens (classic)"。
    # （老的 settings/tokens 是 classic 页，仍可用但非推荐。）
    GITHUB_TOKEN_URL = "https://github.com/settings/personal-access-tokens/new"
    GITHUB_TOKEN_LIST_URL = "https://github.com/settings/personal-access-tokens"
    GITHUB_HELP_URL = (
        "https://docs.github.com/en/authentication/"
        "keeping-your-account-and-data-secure/managing-your-personal-access-tokens"
    )

    def __init__(self, master: App) -> None:
        super().__init__(master)
        self.title("设置")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.master_app = master
        self.transient(master)
        self.grab_set()

        ttk.Label(self, text="设置", style="Head.TLabel").pack(anchor="w", padx=16, pady=(14, 4))

        tip = ("点右边按钮会在浏览器打开对应页面；拿到后粘贴到框里，"
               "再点该行右边的「保存」即可（也可以直接用底部的「全部保存」）。")
        ttk.Label(self, text=tip, style="Dim.TLabel", wraplength=690,
                  justify="left").pack(anchor="w", padx=16)

        # ---------------- Nexus ----------------
        self._section(
            "① Nexus Mods 个人 API key",
            "可选。填了才能自动检查 N 网模组的更新（你有 12 个 N 网模组）。\n"
            "个人自用是 Nexus 官方允许的（见其 API Acceptable Use Policy）。",
            self.var_key(master),
            help_url=self.NEXUS_KEY_URL,
            help_text="去获取 Nexus key",
            help2=(self.NEXUS_HELP_URL, "官方说明"),
            on_save=self._save,
            hint=("页面上找到 Personal API Key → 点复制。\n"
                  "注意：不要给别人这个 key，它等同于你的账号凭据。"),
        )

        # ---------------- GitHub ----------------
        self._section(
            "② GitHub Token",
            "可选。不填也能用（未登录限额 60 次/小时）。\n"
            "填了可提到 5000 次/小时，模组多的时候更稳。",
            self.var_key2(master),
            help_url=self.GITHUB_TOKEN_URL,
            help_text="新建 GitHub token",
            help2=(self.GITHUB_HELP_URL, "官方说明"),
            on_save=self._save,
            hint=("会打开 GitHub 的「新建 fine-grained token」页（官方推荐的类型）。\n"
                  "权限一个都不用勾（本工具只读公开仓库）→ 页面底部点 "
                  "Generate token → 复制生成的 github_pat_…\n"
                  "已有令牌想复用：点右边「已建的令牌」。"),
            help3=(self.GITHUB_TOKEN_LIST_URL, "已建的令牌"),
        )

        # ---------------- 路径信息 ----------------
        info = ttk.Frame(self)
        info.pack(fill="x", padx=16, pady=(12, 0))
        ttk.Label(info, text="下载保存位置：", style="Dim.TLabel").pack(anchor="w")
        row1 = ttk.Frame(info)
        row1.pack(fill="x")
        ttk.Label(row1, text=tool_downloads_dir(), style="Dim.TLabel").pack(side="left")
        ttk.Button(row1, text="打开", width=6,
                   command=self._open_dl).pack(side="right")

        ttk.Label(self, text="浏览器接力时监视的下载文件夹：",
                  style="Dim.TLabel").pack(anchor="w", padx=16, pady=(8, 0))
        ttk.Label(self, text=default_downloads_dir(), style="Dim.TLabel").pack(anchor="w", padx=16)

        # ---------------- 底部按钮 ----------------
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=16, pady=(16, 14))
        ttk.Button(btns, text="全部保存并关闭", style="Accent.TButton",
                   command=self._save_and_close).pack(side="right")
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="清空来源缓存（保留手工指定的）",
                   command=self._clear).pack(side="left")

        # 让窗口按内容自适应高度，避免按钮被挤出可视区
        self.update_idletasks()
        w = max(700, self.winfo_reqwidth())
        h = self.winfo_reqheight() + 8
        self.geometry(f"{w}x{h}")
        # 居中到父窗口
        try:
            px = master.winfo_rootx() + (master.winfo_width() - w) // 2
            py = master.winfo_rooty() + (master.winfo_height() - h) // 3
            self.geometry(f"+{max(0, px)}+{max(0, py)}")
        except Exception:  # noqa: BLE001
            pass

    # 取 master 上的变量（两个 key 各一个输入框）
    def var_key(self, master) -> tk.StringVar:
        return master.var_nexus_key

    def var_key2(self, master) -> tk.StringVar:
        return master.var_github_token

    def _section(self, title: str, desc: str, var, help_url: str = "",
                 help_text: str = "", help2: tuple | None = None,
                 help3: tuple | None = None, hint: str = "",
                 on_save=None) -> None:
        """一个「标题 + 说明 + 输入框 + 保存按钮 + 跳转按钮」的标准区块。

        每行自带「保存」：免去用户找不到底部按钮的困扰。
        """
        box = ttk.Frame(self)
        box.pack(fill="x", padx=16, pady=(12, 0))

        ttk.Label(box, text=title, style="TLabel",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        ttk.Label(box, text=desc, style="Dim.TLabel", wraplength=690,
                  justify="left").pack(anchor="w", pady=(2, 4))

        # 输入框 + 保存 + 跳转按钮
        row = ttk.Frame(box)
        row.pack(fill="x")
        ent = tk.Entry(row, textvariable=var, bg=BG3, fg=FG,
                       insertbackground=FG, relief="flat", show="•",
                       font=("Consolas", 10))
        ent.pack(side="left", fill="x", expand=True, ipady=5)
        if on_save:
            b = ttk.Button(row, text="保存", style="Accent.TButton",
                           command=on_save)
            b.pack(side="left", padx=(6, 0))
            Tooltip(b, "保存这一行填的内容（保存后立即生效，不用关窗口）。")
        if help_url:
            b2 = ttk.Button(row, text=help_text,
                            command=lambda u=help_url: self._open(u))
            b2.pack(side="left", padx=(4, 0))
            Tooltip(b2, f"在浏览器打开：\n{help_url}")
        if help3:
            b3 = ttk.Button(row, text=help3[1],
                            command=lambda u=help3[0]: self._open(u))
            b3.pack(side="left", padx=(4, 0))
            Tooltip(b3, f"在浏览器打开：\n{help3[0]}")
        if help2:
            b4 = ttk.Button(row, text=help2[1],
                            command=lambda u=help2[0]: self._open(u))
            b4.pack(side="left", padx=(4, 0))
            Tooltip(b4, f"在浏览器打开：\n{help2[0]}")

        # 显示/隐藏 的切换（key 默认打码，方便核对是否填对）
        if on_save:
            var_show = tk.BooleanVar(value=False)

            def toggle(_v=None, e=ent, s=var_show) -> None:
                e.configure(show="" if s.get() else "•")

            cb = ttk.Checkbutton(box, text="显示内容", variable=var_show,
                                 command=toggle)
            cb.pack(anchor="w", pady=(2, 0))

        if hint:
            ttk.Label(box, text=hint, style="Dim.TLabel", wraplength=690,
                      justify="left").pack(anchor="w", pady=(4, 0))

    def _open(self, url: str) -> None:
        if not open_in_browser(url):
            # 打不开就显示出来让用户手动复制
            self.clipboard_clear()
            self.clipboard_append(url)
            messagebox.showinfo(
                "链接已复制",
                f"没能自动打开浏览器。\n链接已复制到剪贴板，请手动粘贴打开：\n\n{url}",
                parent=self,
            )

    def _open_dl(self) -> None:
        d = tool_downloads_dir()
        try:
            os.startfile(d)  # noqa: S606
        except Exception:  # noqa: BLE001
            messagebox.showinfo("下载目录", d, parent=self)

    def _save(self) -> None:
        """保存两个 key（每行自己的「保存」按钮也走这里）。"""
        write_config({
            "nexus_api_key": self.master_app.var_nexus_key.get().strip(),
            "github_token": self.master_app.var_github_token.get().strip(),
        })
        nk = "已填" if self.master_app.var_nexus_key.get().strip() else "空"
        gk = "已填" if self.master_app.var_github_token.get().strip() else "空"
        self.master_app.var_status.set(
            f"设置已保存（N网 key：{nk}　GitHub token：{gk}）——下次检查即生效"
        )

    def _save_and_close(self) -> None:
        self._save()
        messagebox.showinfo(
            "已保存",
            "设置已保存到本机，立即生效。\n\n"
            "· 填了 N网 key → 那 12 个 N 网模组也能自动查更新\n"
            "· 想验证效果，回主界面点「检查更新」",
            parent=self.master_app,
        )
        self.destroy()

    def _clear(self) -> None:
        auto = sum(
            1 for items in (load_sources() or {}).values()
            for i in (items or []) if not i.get("manual")
        )
        manual = sum(
            1 for items in (load_sources() or {}).values()
            for i in (items or []) if i.get("manual")
        )
        msg = ("清空来源缓存？\n\n"
               f"将清除 {auto} 条自动识别的来源，下次检查会重新智能搜索。\n")
        if manual:
            msg += (f"\n有 {manual} 条是**你手工指定**的，会被保留，不会删除。\n")
        if not messagebox.askyesno("确认", msg, parent=self):
            return
        n = clear_auto_sources()
        self.master_app.var_status.set(
            f"已清空 {n} 条自动来源" + (f"，保留了 {manual} 条手工指定的" if manual else "")
        )
        # 立刻刷新表格，让用户看到变化
        self.master_app._populate()


def main() -> None:
    # 读回上次的 key
    from .arsenal import read_config
    cfg = read_config()
    app = App()
    app.var_nexus_key.set(cfg.get("nexus_api_key", ""))
    app.var_github_token.set(cfg.get("github_token", ""))
    app.mainloop()


if __name__ == "__main__":
    main()
