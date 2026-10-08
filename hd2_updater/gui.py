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

from . import APP_NAME, __version__, arsenal, matcher, providers, util
from .arsenal import (
    find_data_file,
    load_library,
    tool_downloads_dir,
    write_config,
)
from .engine import (
    ST_BETA,
    ST_DOWNLOADED,
    ST_ERROR,
    ST_NO_SOURCE,
    ST_OK,
    ST_UNCHECKED,
    ST_UPDATE,
    ST_UNKNOWN,
    Engine,
    apply_download_records,
    clear_auto_sources,
    download,
    load_sources,
    open_in_browser,
    save_sources,
    set_manual_source,
    source_key,
)
from . import records

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
CYAN = "#5fc7c7"        # 「已下载」——和「有更新」的橙要一眼分开

STATUS_TEXT = {
    ST_UPDATE: ("有更新", ORANGE),
    ST_DOWNLOADED: ("已下载", CYAN),
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
    "btn_unchecked": "只检查状态是「未检查」或「未找到来源」的模组，\n"
                     "已经在列表里有结果的不动。\n\n"
                     "什么时候点：\n"
                     "· 刚给某个模组指定了来源或填了版本号\n"
                     "· 刚加了新模组\n\n"
                     "比「检查更新」快得多（只查有变化的那几个，\n"
                     "不会重跑全部、也不会重建站点索引）。\n\n"
                     "右键单行还有「只检查这一个」，粒度更细。",
    "btn_dl": "把「有更新」且能直接下载的（只有 GitHub 的）全部下载到\n"
              "本工具的下载夹，并校验 sha256。\n\n"
              "下好的条目会在列表里变成「已下载」，不会重复下第二次。\n\n"
              "N网 / ayakamods 这一栏下不了 —— 它们要求你先登录、\n"
              "而且必须由你亲自点一下下载按钮（站点设计如此）。\n"
              "那些**双击对应那一行**即可：会打开下载页，下完自动收回来。",
    "btn_index": "重建站点索引 = 把 ayakamods 上全部 HD2 模组的清单爬下来，\n"
                 "存成本地索引（约 500 个，需 1~2 分钟）。\n\n"
                 "为什么需要：ayakamods 不提供搜索接口（对脚本返回 403），\n"
                 "所以只能先把整站列表抓下来，之后在本地做名字匹配，\n"
                 "用来自动识别你的模组对应站上哪个页面。\n\n"
                 "**平时完全不用点**：\n"
                 "· 首次使用会自动建立\n"
                 "· 超过 7 天会自动重建（程序启动时在后台悄悄做）\n"
                 "· 点了「识别来源」时也会按需更新\n\n"
                 "只有当你急着识别新模组、不想等自动流程时才需要手动触发。",
    "btn_settings": "填写 N网 API key、GitHub token，\n查看下载位置，或清空已识别的来源。",
    "chk_search": "勾选＝检查时自动帮你搜索并识别每个模组的来源页面。\n"
                  "取消勾选＝只用已经确认过的来源，不做新的搜索（更快）。\n"
                  "默认勾选。",
    "ent_arsenal": "HD2 Arsenal 的模组库位置。\n"
                   "程序会自动探测，探测不到才需要手动选择。",
}


def fit_and_center(dlg: tk.Toplevel, master, min_w: int = 620,
                   min_h: int = 0, pad: int = 12) -> None:
    """按内容自适应窗口大小并居中到父窗口。

    为什么要有这个公用函数：写死 geometry("宽x高") 会导致内容超出可视区、
    底部按钮被挤出屏幕 —— 这个 bug 在 SettingsDialog 上犯过一次，
    接着又在 SourceDialog 上原样犯了一次（只修了「那一个」而不是「这一类」）。
    所以统一走这里，新增对话框一律调用它。

    - 宽度取内容所需与 min_w 的较大者（避免太窄难看）
    - 高度严格按内容所需 + pad（保证按钮一定看得见）
    - 再做一次上限保护：不超过父窗口的 95%，避免超出屏幕
    """
    dlg.update_idletasks()
    w = max(min_w, dlg.winfo_reqwidth())
    h = max(min_h, dlg.winfo_reqheight() + pad)
    try:
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        w = min(w, int(sw * 0.95))
        h = min(h, int(sh * 0.90))
    except Exception:  # noqa: BLE001
        pass
    dlg.geometry(f"{w}x{h}")
    try:
        px = master.winfo_rootx() + (master.winfo_width() - w) // 2
        py = master.winfo_rooty() + (master.winfo_height() - h) // 3
        dlg.geometry(f"+{max(0, px)}+{max(0, py)}")
    except Exception:  # noqa: BLE001
        pass


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
        self._last_settings_dialog = None   # 搬移完成后要回调它收尾
        # 接力用**独立**的工作线程，不占 self.worker —— 否则接力那 15 分钟里，
        # 「检查更新」「识别来源」会因为"worker 还活着"而**静默什么都不做**，
        # 用户点了没反应，最难查的一类 bug。
        self._relay_cancel: threading.Event | None = None
        self._relay_thread: threading.Thread | None = None

        self.var_arsenal = tk.StringVar(value="（正在探测…）")
        self.var_nexus_key = tk.StringVar()
        self.var_github_token = tk.StringVar()
        self.var_use_search = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="就绪")
        self.var_summary = tk.StringVar(value="")

        self._build_ui()
        # 检查数据目录是否发生回退（自定义位置不可用时，app_data_dir 会兜底）
        self.after(80, self._warn_if_data_dir_fallback)
        self.after(120, self._auto_detect)
        self.after(150, self._pump)

    def _warn_if_data_dir_fallback(self) -> None:
        """自定义数据目录不可用时，明确告诉用户（而不是让他看到莫名报错）。"""
        reason = util.take_fallback_reason()
        if reason:
            self.var_status.set(reason)
            messagebox.showwarning(
                "数据目录不可用",
                f"{reason}\n\n"
                f"可能原因：那块盘没插好/已拔掉、盘符变了、或路径被改过。\n\n"
                f"程序已临时改用默认位置，**不会丢数据**；\n"
                f"要修好的话：把盘接回来，或到「设置…→数据位置」重新指定。",
            )
        # 启动后延迟一点在**后台**刷新站点索引（若已过期），
        # 这样用户真正点「识别来源」时就不必再等那一两分钟。
        self.after(1500, self._prewarm_index)

    def _prewarm_index(self) -> None:
        """后台预热 ayakamods 索引：只在过期且当前没在忙时做。"""
        if not self.var_use_search.get():
            return
        try:
            if not matcher.index_is_stale():
                return
        except Exception:  # noqa: BLE001
            return
        if self.worker and self.worker.is_alive():
            return

        def run() -> None:
            try:
                n = len(matcher.ensure_index(
                    progress=lambda m: self.msgq.put(("log", f"[后台] {m}"))
                ))
                self.msgq.put(("log", f"站点索引准备就绪（{n} 个模组）"))
            except Exception as e:  # noqa: BLE001
                self.msgq.put(("log", f"后台更新索引失败（不影响使用）：{e}"))

        threading.Thread(target=run, daemon=True).start()

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
        # 顺序有意如此：检查完最想点的是「下载」，所以让它紧跟「检查更新」
        self.btn_check = ttk.Button(bar, text="检查更新", style="Accent.TButton",
                                    command=self._start_check)
        self.btn_check.pack(side="left")
        Tooltip(self.btn_check, HOVER_TIPS["btn_check"])

        self.btn_dl = ttk.Button(bar, text="下载全部可自动下载的", command=self._download_all,
                                 state="disabled")
        self.btn_dl.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_dl, HOVER_TIPS["btn_dl"])

        # 「识别来源」是"准备阶段"的操作，放在下载之后
        self.btn_find = ttk.Button(bar, text="识别来源", command=self._find_sources)
        self.btn_find.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_find, HOVER_TIPS["btn_find"])

        # 增量检查：只查还没检查过的（填完来源/版本后常用）
        self.btn_unchecked = ttk.Button(bar, text="只查未检查的",
                                        command=self._check_unchecked)
        self.btn_unchecked.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_unchecked, HOVER_TIPS["btn_unchecked"])

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
        tip = ("提示：双击一行就能干活 —— 有更新的会直接下载（GitHub），"
               "或打开下载页并自动把文件收回来（N网 / ayakamods）；"
               "已下载的则打开那个 ZIP。右键还有更多操作。")
        ttk.Label(self, text=tip, style="Dim.TLabel").pack(anchor="w", padx=14, pady=(0, 6))

        # 右键菜单
        self.menu = tk.Menu(self, tearoff=0, bg=BG3, fg=FG,
                            activebackground=ACCENT, activeforeground="#1b1d21")
        self.menu.add_command(label="只检查这一个", command=self._check_this_one)
        self.menu.add_separator()
        self.menu.add_command(label="下载 / 打开下载页（= 双击这一行）",
                              command=self._on_row_activate)
        self.menu.add_command(label="只在浏览器打开，不自动收文件",
                              command=self._ctx_open)
        self.menu.add_command(label="打开已下载的文件", command=self._ctx_open_file)
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
            # 叠加「已下载」状态：同一个线上版本若已经下过，就不再催着下
            rows = apply_download_records(results)

        # 排序：有更新的排最前（已下载的排在「有更新」之后 —— 它也算"待处理"，
        # 但不用再动手下载，所以往后放一格，别抢了真正要下载的那些的位置）
        order = {ST_UPDATE: 0, ST_DOWNLOADED: 1, ST_BETA: 2, ST_ERROR: 3,
                 ST_UNKNOWN: 4, ST_NO_SOURCE: 5, ST_UNCHECKED: 6, ST_OK: 7}
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
                # 这一栏要回答的是「我该干什么」，所以给动作提示，
                # 而不是给「怎么认出这个来源的」（那是溯源，用户不关心）
                note = self._update_note(best)
                if best.get("prerelease"):
                    note += "（测试版）"
            elif r["status"] == ST_DOWNLOADED:
                rec = r.get("downloaded") or {}
                note = (f"已下好 {rec.get('version') or '?'}"
                        f"（{rec.get('at') or ''}）：{os.path.basename(str(rec.get('file') or ''))}"
                        f"　→ 去 Arsenal 导入即可")
            elif r["status"] == ST_BETA:
                note = "只有测试版可更，自行决定"
            elif r["status"] == ST_ERROR:
                note = (best.get("error") or "")[:70]
            elif r["status"] == ST_NO_SOURCE:
                note = "没找到来源 —— 右键「指定来源…」填一次"
            elif r["status"] == ST_UNCHECKED:
                note = "还没检查 —— 点上面的「检查更新」"
            elif r["status"] == ST_UNKNOWN:
                # 这一栏原来空着 —— 但「版本存疑」恰恰是最需要说明的一种状态：
                # 用户看到它只会想"是模组坏了还是站点坏了"，必须写清缺什么。
                note = (best.get("error") or "")[:110] or "两边版本没法比较（信息不足）"
            elif r["status"] == ST_OK:
                # 无需动作时，才把「凭什么认定它是这个来源」显示出来
                note = (best.get("how") or "无需处理")[:70]

            # 上一版下过、文件却不在原处了 —— 说清楚，免得用户以为是新问题
            if r.get("prev_download"):
                note += "　（注意：上次下的那个文件已不在原处）"

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
        done = sum(1 for r in rows if r["status"] == ST_DOWNLOADED)
        beta = sum(1 for r in rows if r["status"] == ST_BETA)
        nosrc = sum(1 for r in rows if r["status"] == ST_NO_SOURCE)
        err = sum(1 for r in rows if r["status"] == ST_ERROR)
        unchecked = sum(1 for r in rows if r["status"] == ST_UNCHECKED)
        parts = [f"共 {n} 个"]
        if upd:
            parts.append(f"有更新 {upd}")
        if done:
            parts.append(f"已下载 {done}")
        if beta:
            parts.append(f"仅测试版 {beta}")
        if err:
            parts.append(f"失败 {err}")
        if nosrc:
            parts.append(f"未识别 {nosrc}")
        if unchecked:
            parts.append(f"未检查 {unchecked}")
        self.var_summary.set("　|　".join(parts))
        # 「下载全部」只看还能下的那些（已下载的不再列进去）
        auto = self._auto_download_targets(rows)
        self.btn_dl.configure(state=("normal" if auto else "disabled"))
        if done and auto:
            self.btn_dl.configure(text=f"下载剩余 {len(auto)} 个")
        else:
            self.btn_dl.configure(text="下载全部可自动下载的")
        self._update_hint(rows)

    def _update_note(self, best: dict) -> str:
        """「有更新」那一行右边写什么 —— 讲清**怎么拿到它**，别让人猜。"""
        if best.get("download_url"):
            return "双击这一行 = 直接下载（GitHub）"
        prov = best.get("provider")
        if prov == "nexus":
            return "双击这一行 = 打开文件页（登录后点 SLOW DOWNLOAD），下完自动收"
        if prov == "ayaka":
            return "双击这一行 = 打开页面（登录后点 Download），下完自动收"
        return "双击这一行 = 打开下载页"

    def _auto_download_targets(self, rows: list[dict] | None = None) -> list[dict]:
        """还能自动下载的那些（有直链、且**这一版还没下过**的）。

        注意：判断时不能只看 status —— self.results 里存的是**原始**检查结果
        （「已下载」是渲染时才叠加的），所以这里要再问一次 records，
        否则点两次「下载全部」就会把同一版重复下一遍。
        """
        out = []
        for r in (self.results if rows is None else rows) or []:
            if r["status"] != ST_UPDATE:
                continue
            best = r.get("best") or {}
            if not best.get("download_url"):
                continue
            key = source_key(r.get("entry") or {})
            if records.match(key, str(best.get("version") or "")):
                continue        # 已经下过这一版了
            out.append(r)
        return out

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

        # 还没填任何凭据？第一次用时优先提示这件事 —— 新手不知道有令牌可填，
        # 常常一上手就撞上 GitHub 限额或"N网需要 API key"。
        has_nexus = bool(self.var_nexus_key.get().strip())
        has_github = bool(self.var_github_token.get().strip())

        msg = ""
        btn_text = "开始识别来源"
        btn_cmd = self._find_sources
        no_token = not (has_nexus or has_github)

        if first_time and no_token:
            msg = ("第一次使用：还没填过访问令牌（可选，但建议填）。\n"
                   "填了之后：GitHub 限额从 60 次/小时提到 5000；"
                   "N 网的更新也能自动检查。都是免费的，点右边去设置。")
            btn_text = "去设置令牌…"
            btn_cmd = self._prompt_tokens
        elif first_time:
            msg = (f"第一次使用：还没有识别过模组来源。"
                   f"点右边「开始识别来源」，我会自动找出这 {total} 个模组"
                   f"各自在网上对应哪个页面（GitHub / N网 / ayakamods）。")
        elif no_token and not util.read_config_dismissed():
            # 已经在用了但一直没填令牌 —— 提醒一次（可跳过），优先于"未找到来源"
            msg = ("提示：你还没填访问令牌。填了能减少 GitHub 被限流的情况，"
                   "并让 N 网的更新也能自动检查。　点右边去设置（可跳过）。")
            btn_text = "去设置令牌…"
            btn_cmd = self._prompt_tokens
        elif nosrc:
            msg = (f"有 {nosrc} 个模组没找到来源。"
                   f"点右边可以再识别一次；个别认不出来的，"
                   f"右键「指定来源…」手动填。")

        self.hint_btn.configure(text=btn_text, command=btn_cmd)

        if msg:
            self.hint_label.configure(text=msg)
            if not self._hint_shown:
                self.hintbar.pack(fill="x", padx=12, pady=(0, 4),
                                  before=self.prog)
                self._hint_shown = True
        elif self._hint_shown:
            self.hintbar.pack_forget()
            self._hint_shown = False

    def _prompt_tokens(self) -> None:
        """引导用户去填令牌（首次使用/一直没填时用）。"""
        ans = messagebox.askyesno(
            "填访问令牌（可选）",
            "两个令牌都是**可选**的，不填也能用，但填了会明显更顺：\n\n"
            "① Nexus API key —— 填了才能自动检查 N 网模组的更新\n"
            "    （你有 12 个 N 网模组；不填它们只能手动看）\n\n"
            "② GitHub token —— 把查询限额从 60 次/小时提到 5000\n"
            "    （模组多的时候不容易被限流）\n\n"
            "都是免费的，个人自用是官方允许的。\n\n"
            "现在就去设置里填吗？\n"
            "（点「否」我就先不提醒了，以后随时能在「设置…」里填）",
            parent=self,
        )
        if ans:
            self._open_settings()
        else:
            # 记下来，别反复打扰
            arsenal.write_config({"token_hint_dismissed": True})

    # ------------------------------------------------------------ 识别来源
    def _busy_guard(self) -> bool:
        """有检查/识别在跑时返回 True 并**给出提示**。

        原来是 `if self.worker...: return` —— 静默什么都不做，用户点了没反应，
        最容易误判成"坏了"。哪怕只是个防御性分支，也要说一句话。
        """
        if self.worker and self.worker.is_alive():
            self.var_status.set("正在忙（检查或识别进行中），请等它跑完再点。")
            return True
        return False

    def _find_sources(self) -> None:
        """只做来源识别（不查版本），结果保存下来。"""
        if not self.data_file:
            messagebox.showwarning("先选位置", "还没找到 Arsenal 模组库。")
            return
        if self._busy_guard():
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

    def _start_check(self, only_keys: set[str] | None = None) -> None:
        """检查更新。only_keys 非空时只查这些模组（增量刷新）。"""
        if not self.data_file:
            messagebox.showwarning("先选位置", "还没找到 Arsenal 模组库。")
            return
        if self._busy_guard():
            return
        incremental = bool(only_keys)
        label = "检查中…" if not incremental else "检查中…"
        self.btn_check.configure(state="disabled", text=label)
        self.btn_find.configure(state="disabled")
        self.prog.configure(value=0)
        self.engine = Engine(
            self.data_file,
            api_key=self.var_nexus_key.get().strip(),
            github_token=self.var_github_token.get().strip(),
            use_search=self.var_use_search.get(),
            log=lambda m: self.msgq.put(("log", m)),
            progress=lambda d, t, m: self.msgq.put(("prog", (d, t, m))),
        )
        eng = self.engine

        def run() -> None:
            try:
                eng.load()
                if incremental:
                    # 增量：只解析这些模组的来源，不重建索引、不动其他结果
                    self.msgq.put(("log", f"只检查选中的 {len(only_keys)} 个模组…"))
                    eng.resolve_sources()
                else:
                    self.msgq.put(("log", "开始解析来源…"))
                    eng.resolve_sources()
                if eng.cancelled:
                    return
                self.msgq.put(("log", "开始检查更新…"))
                res = eng.check(only_keys=only_keys)
                if not incremental:
                    eng.persist_resolved()
                self.msgq.put(("done_partial" if incremental else "done",
                               res))
            except Exception as e:  # noqa: BLE001
                import traceback
                self.msgq.put(("error", f"{e}\n\n{traceback.format_exc()[:900]}"))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _check_unchecked(self) -> None:
        """只检查还没检查的模组（增量）。"""
        if not self.results:
            # 还没检查过 —— 那"未检查的"就是全部，走完整流程
            self._start_check()
            return
        todo = {
            source_key(r["entry"])
            for r in self.results
            if r.get("status") in (ST_UNCHECKED, ST_NO_SOURCE)
        }
        if not todo:
            messagebox.showinfo("没有要检查的",
                                "所有模组都已经检查过了。\n\n"
                                "如果想全部重查一遍，点「检查更新」。")
            return
        self._start_check(only_keys=todo)

    def _check_this_one(self) -> None:
        """只检查右键选中的那一个模组。"""
        w = self._selected()
        if not w:
            return
        if self._busy_guard():
            return
        self._start_check(only_keys={source_key(w["entry"])})

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
                    self.btn_find.configure(state="normal")
                    self.prog.configure(value=0)
                    self.results = payload
                    self._populate(payload)
                    upd = sum(1 for r in payload if r["status"] == ST_UPDATE)
                    self.var_status.set(
                        f"检查完成：{upd} 个有更新" if upd else "检查完成：没有正式版更新"
                    )
                elif kind == "done_partial":
                    # 增量检查：把新结果**合并**回原有列表，其余保持不变
                    self.btn_check.configure(state="normal", text="检查更新")
                    self.btn_find.configure(state="normal")
                    self.prog.configure(value=0)
                    fresh = {source_key(r["entry"]): r for r in payload}
                    merged = []
                    for r in (self.results or []):
                        k = source_key(r["entry"])
                        merged.append(fresh.pop(k, r))   # 有新的就用新的
                    merged.extend(fresh.values())         # 之前没有的补进来
                    self.results = merged
                    self._populate(merged)
                    upd = sum(1 for r in merged if r["status"] == ST_UPDATE)
                    self.var_status.set(
                        f"已更新 {len(payload)} 个模组；当前共 {upd} 个有更新"
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
                            f"手动贴一次页面地址即可（贴一次就长期记住）。\n\n"
                            f"现在可以点「检查更新」去查版本了。",
                        )
                    else:
                        messagebox.showinfo(
                            "识别完成",
                            f"{total} 个模组的来源全部识别到了。\n\n"
                            f"现在点「检查更新」就会去查每个网站上的最新版本。",
                        )
                elif kind == "index_done":
                    self.prog.stop()
                    self.prog.configure(mode="determinate", value=0)
                    self.var_status.set(f"索引重建完成：{payload} 个模组")
                    messagebox.showinfo(
                        "索引重建完成",
                        f"已抓取 {payload} 个 ayakamods 模组到本地索引。\n\n"
                        f"现在可以点「识别来源」或「检查更新」了。",
                    )
                elif kind == "index_fail":
                    self.prog.stop()
                    self.prog.configure(mode="determinate", value=0)
                    self.var_status.set("索引重建失败")
                    messagebox.showerror("索引重建失败", str(payload))
                elif kind == "handoff":
                    ok, dest, moved = payload
                    if ok:
                        self.var_status.set(f"接力收到 {moved} 个文件 → {dest}")
                        # 重画，让这一条变成「已下载」
                        if self.results:
                            self._populate(self.results)
                        messagebox.showinfo(
                            "已收到文件",
                            f"收到 {moved} 个文件，已放进：\n  {dest}\n\n"
                            f"列表里这一条已标成「已下载」。\n\n"
                            f"接下来：在 Arsenal 里用 Add mods / Import 导入，"
                            f"然后把旧的那份禁用并删除。",
                        )
                    else:
                        self.var_status.set("接力：没等到新文件")
                        messagebox.showinfo(
                            "没等到文件",
                            "15 分钟内没在监视的文件夹里发现新的压缩包。\n\n"
                            "可能原因：\n"
                            "· 还没点下载，或没下完（N网免费账号有等待时间）\n"
                            "· 浏览器的下载位置和监视的不一样\n"
                            "  （到「设置…→下载位置」里改成实际位置）\n\n"
                            "也可以手动把下好的文件拖进工具的下载夹。",
                        )
                elif kind == "moved":
                    ok, msg = payload
                    # 收尾：停进度条、恢复按钮
                    self.prog.stop()
                    self.prog.configure(mode="determinate", value=0)
                    try:
                        self._last_settings_dialog._busy(False)
                    except Exception:  # noqa: BLE001
                        pass
                    if not ok:
                        self.var_status.set("搬移未完成")
                        messagebox.showerror("搬移失败", msg)
                    else:
                        self.var_status.set(f"数据位置已改为 {util.current_data_dir()}，请重启程序")
                        messagebox.showinfo(
                            "搬移完成",
                            f"{msg}\n\n**请关闭并重新打开程序**，之后就会用新位置。\n\n"
                            f"（指针文件仍在默认位置，只存一行路径，不影响使用）",
                        )
                        try:
                            self._last_settings_dialog.destroy()
                        except Exception:  # noqa: BLE001
                            pass
                elif kind == "error":
                    self.btn_check.configure(state="normal", text="检查更新")
                    self.var_status.set("出错了")
                    messagebox.showerror("检查失败", str(payload))
                elif kind == "dl":
                    self.var_status.set(str(payload))
                elif kind == "dl_done":
                    self.btn_dl.configure(state="normal", text="下载全部可自动下载的")
                    self.var_status.set(str(payload)[:120])
                    # 下完立刻重画：把这几条变成「已下载」，省得用户以为没成功
                    if self.results:
                        self._populate(self.results)
                    messagebox.showinfo("下载完成", str(payload))
        except queue.Empty:
            pass
        self.after(150, self._pump)

    # ------------------------------------------------------------ 下载
    def _download_all(self) -> None:
        targets = self._auto_download_targets()
        if not targets:
            messagebox.showinfo("没有可自动下载的",
                                "有更新的模组里没有可直接下载的（只有 GitHub 能直连下载）。\n\n"
                                "N网 / ayakamods 需要登录，而且必须你亲自点一下下载按钮。\n"
                                "**双击列表里那几行**就会打开对应的下载页，"
                                "下完的文件会自动收进工具的下载夹。")
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
                    # 记一笔，界面上就变成「已下载」了
                    records.mark(source_key(e), b.get("version", ""), p,
                                 b.get("provider", ""), name)
                    ok += 1
                    self.msgq.put(("dl", f"✓ {os.path.basename(p)}"))
                except Exception as ex:  # noqa: BLE001
                    self.msgq.put(("dl", f"✗ {name} 失败：{ex}"))
            self.msgq.put((
                "dl_done",
                f"下载完成 {ok}/{len(targets)} 个。\n\n文件在：\n{dest}\n\n"
                f"列表里这几条已经标成「已下载」（右键可「打开已下载的文件」）。\n\n"
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

    def _on_row_activate(self, _ev=None) -> None:
        """双击一行 = 直接干这件事，不用先去想"该用哪个菜单"。

        · 已下载 → 打开那个 ZIP
        · 有更新的（且能自动下载）→ 直接下载
        · 需要登录的（N网 / ayakamods）→ 走接力：开页面 + 盯着下载夹
        · 其它情况（已最新 / 没来源 / 查失败）→ 打开它的发布页

        为什么双击=接力而不是只开网页：用户看到的是「有更新」这一行，
        最自然的动作就是双击它。原来只开网页、不盯下载夹，等于"帮了一半点"，
        文件下完还得自己去搬。现在双击就把接力整件事做完。
        """
        w = self._selected()
        if not w:
            return
        row = w["row"]
        st = row.get("status")
        best = (row.get("best") or {})

        if st == ST_DOWNLOADED:
            self._ctx_open_file()
            return
        if st == ST_UPDATE and best.get("download_url"):
            self._ctx_download()
            return
        if st in (ST_UPDATE, ST_BETA) and self._handoff_url(best):
            self._ctx_browser()
            return
        url = self._handoff_url(best) or best.get("url") or best.get("release_url")
        if url:
            open_in_browser(url)
        else:
            self._ctx_set_source()

    def _ctx_open(self) -> None:
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        url = self._handoff_url(best) or best.get("url")
        if url:
            open_in_browser(url)
        else:
            messagebox.showinfo("没有链接", "这个模组还没找到来源。")

    def _ctx_browser(self) -> None:
        """浏览器接力：打开页面 + **真的监视下载目录**，下完自动收进工具的下载夹。

        打开的地址分三种情况（都是为了少让用户找）：
          · N网 —— 直接落到「文件」页签的**这一个文件**上
                   （`?tab=files&file_id=xxx&nmm=1`；nmm=1 会让页面出现
                   「交给管理器下载」的按钮，本机已注册 nxm:// 归 Arsenal），
                   并且用已有的 Nexus 登录态；免费账号仍要过一次等待。
          · ayakamods —— 打开模组页（登录后点 Download）
          · GitHub —— 打开 release 页（有直链的其实用「直接下载这个」更快）
        """
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        url = self._handoff_url(best)
        if not url:
            messagebox.showinfo("没有链接", "先给这个模组指定来源。")
            return

        watch = arsenal.browser_watch_dir()
        if not os.path.isdir(watch):
            messagebox.showwarning(
                "监视的文件夹不存在",
                f"要监视的文件夹不存在：\n  {watch}\n\n"
                f"请到「设置… → 下载位置」里改成一个真实存在的文件夹\n"
                f"（浏览器里 edge://settings/downloads 可以看它实际存到哪）。",
                parent=self,
            )
            return

        # 同一时间只跑一个接力：两个接力同时盯同一个下载夹，
        # 谁先扫到就算谁的 —— 会把 A 下回来的文件记到 B 头上。
        if self._relay_cancel is not None:
            self._relay_cancel.set()

        # 快照 + 开始监视（与旧脚本同思路：忽略临时后缀、等大小稳定）
        import time as _t
        before = self._snapshot_dir(watch)
        started = _t.time()
        cancel = threading.Event()
        self._relay_cancel = cancel
        open_in_browser(url)

        prov = best.get("provider")
        hint = {
            "nexus": "页面会停在那个文件上 —— 点 SLOW DOWNLOAD（免费账号要等一小会儿），"
                     "下完会自动收走。",
            "ayaka": "登录后点 Download，下完会自动收走。",
        }.get(prov, "下完会自动收走。")
        self.var_status.set(f"已打开页面，正在监视 {watch} …（{hint}）")
        dest = arsenal.tool_downloads_dir()
        ver = best.get("version", "")
        key = source_key(w["entry"])
        label = w["entry"].get("label") or w["entry"].get("dirname") or ""

        def run() -> None:
            got = self._watch_for_archive(
                before, watch, timeout_sec=15 * 60, started_at=started,
                cancel=cancel,
            )
            if cancel.is_set():
                # 被后开的接力顶掉了 —— 静默退出，别弹"没等到文件"吓人
                return
            if self._relay_cancel is cancel:
                self._relay_cancel = None
            if not got:
                self.msgq.put(("log", "接力：没等到新文件（可能还没下完或下到了别处）"))
                self.msgq.put(("handoff", (False, "", 0)))
                return
            moved = 0
            for fp in got:
                name = os.path.basename(fp)
                try:
                    dst = os.path.join(dest, name)
                    if os.path.abspath(fp) != os.path.abspath(dst):
                        os.replace(fp, dst)
                    moved += 1
                    # 接力也算「已下载」，界面上要跟着变
                    records.mark(key, ver, dst, prov or "", label)
                    self.msgq.put(("log", f"接力收到：{name}"))
                except Exception as e:  # noqa: BLE001
                    self.msgq.put(("log", f"搬移失败 {name}：{e}"))
            self.msgq.put(("handoff", (True, dest, moved)))

        self._relay_thread = threading.Thread(target=run, daemon=True)
        self._relay_thread.start()

    @staticmethod
    def _handoff_url(best: dict) -> str:
        """接力时该打开哪个地址。

        逻辑放在 providers.handoff_url 里，**GUI 和命令行共用一份**，
        免得哪天改了一处忘了另一处（各站点为什么不一样，见那边的注释）。
        """
        return providers.handoff_url(best)

    @staticmethod
    def _snapshot_dir(folder: str) -> dict:
        """记录目录现有文件（小写名 -> 大小），用于识别新落地的下载。"""
        out = {}
        try:
            for name in os.listdir(folder):
                fp = os.path.join(folder, name)
                try:
                    if os.path.isfile(fp):
                        out[name.lower()] = os.path.getsize(fp)
                except OSError:
                    pass
        except OSError:
            pass
        return out

    @staticmethod
    def _watch_for_archive(before: dict, folder: str, timeout_sec: int = 900,
                           started_at: float | None = None,
                           settle: int = 2,
                           cancel: threading.Event | None = None) -> list[str]:
        """等新的压缩包出现并大小稳定。

        与旧脚本一致的要点：忽略 .crdownload 等下载中后缀；
        要求连续两次检查大小不变才算下完；比监视开始更早的文件当老文件。

        ``cancel`` 被 set 时立刻返回空 —— 用于"用户又双击了另一行"的情形：
        旧的接力必须让位，否则两个接力会争抢同一个下载夹里的文件。
        """
        import time as _t

        if started_at is None:
            started_at = _t.time()
        deadline = _t.monotonic() + timeout_sec
        seen: set[str] = set()
        sizes: dict[str, tuple[int, int]] = {}
        done: list[str] = []
        inprog = (".crdownload", ".part", ".tmp", ".download", ".partial", ".opdownload")

        while _t.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                return []
            try:
                names = os.listdir(folder)
            except OSError:
                _t.sleep(1)
                continue
            for name in names:
                low = name.lower()
                if low in seen:
                    continue
                if any(low.endswith(s) for s in inprog):
                    continue
                if not low.endswith((".zip", ".7z", ".rar")):
                    continue
                fp = os.path.join(folder, name)
                if not os.path.isfile(fp):
                    continue
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                prev = before.get(low)
                if prev is not None and st.st_size == prev:
                    seen.add(low)
                    continue
                if st.st_mtime < started_at - 3:
                    seen.add(low)
                    continue
                last, stable = sizes.get(low, (-1, 0))
                stable = stable + 1 if st.st_size == last and st.st_size > 0 else 0
                sizes[low] = (st.st_size, stable)
                if stable >= settle:
                    seen.add(low)
                    done.append(fp)
            if done:
                return done
            _t.sleep(1)
        return done

    def _ctx_download(self) -> None:
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        if not best.get("download_url"):
            # 走到这里通常是从「下载全部」进来的（N网 / ayakamods 没有直链）。
            # 不弹窗说教，直接转成接力：开页面 + 盯下载夹，
            # 这样用户不管从哪条路进来，结果都是"能拿到文件"。
            self._ctx_browser()
            return
        dest = tool_downloads_dir()
        entry = w["entry"]
        ver = best.get("version", "")
        prov = best.get("provider", "")
        label = entry.get("label") or entry.get("dirname") or ""
        key = source_key(entry)

        def run() -> None:
            try:
                p = download(best["download_url"], dest, best.get("asset_name", ""),
                             best.get("sha256", ""))
                records.mark(key, ver, p, prov, label)
                self.msgq.put(("dl_done", f"已下载：\n{p}"))
            except Exception as ex:  # noqa: BLE001
                self.msgq.put(("dl_done", f"下载失败：{ex}"))

        threading.Thread(target=run, daemon=True).start()

    def _ctx_open_file(self) -> None:
        """打开这个模组已下载的压缩包（用系统默认程序 / 或定位到文件夹）。"""
        w = self._selected()
        if not w:
            return
        rec = (w["row"].get("downloaded") or
               records.get(source_key(w["entry"])) or {})
        fp = str(rec.get("file") or "")
        if not fp or not os.path.isfile(fp):
            messagebox.showinfo(
                "没有可打开的文件",
                "这个模组还没有下载记录，或文件已经被移走了。\n\n"
                f"记录的位置：{fp or '（无）'}",
            )
            return
        try:
            os.startfile(fp)  # noqa: S606
            self.var_status.set(f"已打开：{fp}")
        except Exception:  # noqa: BLE001
            try:
                os.startfile(os.path.dirname(fp))  # noqa: S606
            except Exception:  # noqa: BLE001
                messagebox.showinfo("文件位置", fp)

    def _ctx_copy(self) -> None:
        w = self._selected()
        if not w:
            return
        best = (w["row"].get("best") or {})
        # 优先给「下载直链」；没有就给要打开的页面（N网给深链，更省事）
        url = (best.get("download_url") or self._handoff_url(best)
               or best.get("url") or "")
        if url:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.var_status.set(f"已复制：{url[:90]}")

    def _ctx_set_source(self) -> None:
        """手动指定来源。"""
        w = self._selected()
        if not w:
            return
        SourceDialog(self, w["entry"], on_saved=self._after_source_change,
                     on_saved_row=w["entry"])

    def _after_source_change(self, entry: dict | None = None) -> None:
        """手工指定来源/版本后，**只刷新这一行**，不动其他检查结果。

        踩过的坑：上一版这里直接调 `_populate()`（不带参数 = 还没检查过的
        初始状态），结果用户填一个版本号，**整张表的检查结果全被清空**，
        得重新「检查更新」才能看回来 —— 修"不刷新"时反而引入了"全丢"。

        正确做法：拿已有的 self.results，只替换/补上这一条的结果。
        """
        if not entry:
            return
        key = source_key(entry)
        new_row = {
            "entry": entry,
            "status": ST_UNCHECKED,
            "installed": entry.get("installed_version", ""),
            "best": None,
            "checks": [],
        }
        # 该模组在现有结果里的位置
        idx = None
        for i, r in enumerate(self.results or []):
            if source_key(r.get("entry") or {}) == key:
                idx = i
                break

        if self.results and idx is not None:
            # 有检查结果 → 只替换这一条，其余原样保留
            self.results[idx] = new_row
            self._populate(self.results)
            self.var_status.set(
                "已更新这一条。其余结果保持不变；点「检查更新」可重查全部。"
            )
        else:
            # 还没检查过（初始列表）→ 那就整体重绘，反正没有结果会丢
            self._populate()
            self.var_status.set("来源已保存。点「检查更新」即可去这个页面查版本。")

    def _open_dl_dir(self) -> None:
        d = tool_downloads_dir()
        try:
            os.startfile(d)  # noqa: S606
        except Exception:  # noqa: BLE001
            messagebox.showinfo("下载目录", d)

    # ---------------------------------------------------------- 其他
    def _rebuild_index(self) -> None:
        """重建 ayakamods 索引（实测约 90 秒：要翻页爬 500 多个模组）。"""
        self.var_status.set("正在重建 ayakamods 索引（约 1~2 分钟，请稍候）…")

        # 有进度条的话动起来，让用户知道没卡死
        self.prog.configure(mode="indeterminate")
        try:
            self.prog.start(60)
        except Exception:  # noqa: BLE001
            pass

        def run() -> None:
            try:
                items = matcher.rebuild_index(
                    progress=lambda m: self.msgq.put(("log", m))
                )
                self.msgq.put(("index_done", len(items)))
            except Exception as e:  # noqa: BLE001
                self.msgq.put(("index_fail", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        self._last_settings_dialog = dlg


class SourceDialog(tk.Toplevel):
    """手动指定来源。"""

    def __init__(self, master: App, entry: dict, on_saved=None,
                 on_saved_row: dict | None = None) -> None:
        super().__init__(master)
        self.title("指定来源")
        self.configure(bg=BG)
        # 不写死尺寸 —— 内容会随提示文字/示例按钮变化，
        # 最后统一交给 fit_and_center() 按内容算。
        self.minsize(620, 300)
        self.entry_data = entry
        self.on_saved = on_saved
        self.on_saved_row = on_saved_row or entry
        self.transient(master)
        self.grab_set()

        key = source_key(entry)
        saved = load_sources().get(key) or []

        ttk.Label(self, text=f"模组：{entry.get('label') or entry.get('dirname')}",
                  style="Head.TLabel").pack(anchor="w", padx=14, pady=(12, 2))
        ttk.Label(self, text=f"本地版本：{entry.get('installed_version') or '未知'}",
                  style="Dim.TLabel").pack(anchor="w", padx=14)

        # ---- 只填一个网址，站点由程序自己判断，降低学习成本 ----
        ttk.Label(self, text="① 把这个模组的网页地址粘到下面",
                  style="TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(
            anchor="w", padx=14, pady=(14, 2))
        ttk.Label(self,
                  text="三个网站都填页面地址即可，不用管是哪个站点 —— 程序会自动识别。",
                  style="Dim.TLabel", wraplength=650, justify="left").pack(anchor="w", padx=14)

        self.var_url = tk.StringVar(value=self._initial_url(saved))
        ent = tk.Entry(self, textvariable=self.var_url, bg=BG3, fg=FG,
                       insertbackground=FG, relief="flat", font=("Consolas", 10))
        ent.pack(fill="x", padx=14, pady=(5, 0), ipady=5)
        ent.focus_set()
        ent.bind("<KeyRelease>", lambda _e: self._preview())
        ent.bind("<Return>", lambda _e: self._save())

        # 实时显示「识别成了什么」
        self.lbl_detect = ttk.Label(self, text="", style="Dim.TLabel", wraplength=650,
                                    justify="left")
        self.lbl_detect.pack(anchor="w", padx=14, pady=(4, 0))

        # ---- 本地版本（有些 mod 没写版本号，需要用户填）----
        verbox = ttk.Frame(self)
        verbox.pack(fill="x", padx=14, pady=(10, 0))
        auto = entry.get("installed_version") or ""
        cur = arsenal.get_version_override(entry.get("uuid") or "") or auto
        ttk.Label(verbox, text="② 本地版本（可选）",
                  style="TLabel", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        row_v = ttk.Frame(verbox)
        row_v.pack(fill="x")
        self.var_localver = tk.StringVar(value=cur)
        tk.Entry(row_v, textvariable=self.var_localver, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=("Consolas", 10),
                 width=18).pack(side="left", ipady=4)
        note = ("自动探测不到版本号，请手动填你装的那个版本"
                if not auto else f"自动探测到：{auto}")
        ttk.Label(row_v, text=note, style="Dim.TLabel").pack(side="left", padx=(8, 0))
        ttk.Label(verbox,
                  text="用途：版本号用来判断「线上有没有更新」。若这里留空，"
                       "这个模组会显示「版本存疑」，而不是误报有更新。",
                  style="Dim.TLabel", wraplength=650,
                  justify="left").pack(anchor="w", pady=(3, 0))

        # ---- 示例（可点击直接填入）----
        ttk.Label(self, text="③ 不知道网址在哪找？点下面的例子看看格式",
                  style="TLabel").pack(anchor="w", padx=14, pady=(10, 2))
        ex = ttk.Frame(self)
        ex.pack(anchor="w", padx=14)
        examples = [
            ("GitHub 示例", "https://github.com/SkyeShade/HD2Runtime"),
            ("N网示例", "https://www.nexusmods.com/helldivers2/mods/15298"),
            ("ayakamods 示例", "https://ayakamods.com/mods/hd2runtime.4260/"),
        ]
        for text, url in examples:
            b = ttk.Button(ex, text=text, width=18,
                           command=lambda u=url: (self.var_url.set(u), self._preview()))
            b.pack(side="left", padx=(0, 6))
            Tooltip(b, f"点一下就把这个例子填进输入框：\n{url}")

        ttk.Label(self,
                  text="提示：在浏览器里打开那个模组的页面，复制地址栏的整条链接粘过来就行。",
                  style="Dim.TLabel", wraplength=650,
                  justify="left").pack(anchor="w", padx=14, pady=(8, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=14, pady=(16, 12))
        ttk.Button(btns, text="保存", style="Accent.TButton",
                   command=self._save).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))
        self.btn_verify = ttk.Button(btns, text="用浏览器打开看看",
                                     command=self._verify, state="disabled")
        self.btn_verify.pack(side="left")

        self._preview()
        # 按内容定尺寸（务必在最后调用，此时所有控件都已 pack）
        fit_and_center(self, master, min_w=620)

    def _initial_url(self, saved: list) -> str:
        """已有来源时，把它的网址填进去方便修改。"""
        if not saved:
            return ""
        s = saved[0]
        url = s.get("url") or ""
        if url:
            return url
        prov, ident = s.get("provider"), str(s.get("id") or "")
        if prov == "github" and ident:
            return f"https://github.com/{ident}"
        if prov == "nexus" and ident:
            return f"https://www.nexusmods.com/helldivers2/mods/{ident}"
        if prov == "ayaka" and ident:
            return f"https://ayakamods.com/mods/{ident.strip('/')}/"
        return ""

    def _detect(self, text: str) -> dict | None:
        """从网址判断是哪个站点，返回 {'provider','id','url'}；认不出返回 None。

        设计意图：用户只需粘贴模组的**页面地址**，不用管是哪个站点、不用管
        该填 ID 还是仓库名 —— 由程序识别。也容忍偷懒写法（不带 https://、
        带尾巴参数、GitHub 带 /releases/tag/... 等）。
        """
        import re
        t = (text or "").strip()
        if not t:
            return None

        # 先按用户原样判断「裸 slug」（只认这个，别的一律补 https://）
        m = re.fullmatch(r"([a-z0-9][a-z0-9\-]*[a-z0-9])\.(\d+)", t.strip("/"), re.I)
        if m:
            slug = f"{m.group(1)}.{m.group(2)}"
            return {"provider": "ayaka", "id": slug,
                    "url": f"https://ayakamods.com/mods/{slug}/"}

        if not re.match(r"^https?://", t, re.I):
            t = "https://" + t.lstrip("/")

        low = t.lower()
        # GitHub：只要域名对，取 作者/仓库
        if "github.com" in low:
            m = re.search(r"github\.com/([^/]+)/([^/#?\s]+)", t, re.I)
            if m:
                repo = f"{m.group(1)}/{m.group(2)}"
                repo = re.sub(r"\.git$", "", repo)
                # 排除 github.com/settings/... 这类非仓库路径
                if repo.split("/")[0].lower() in (
                    "settings", "features", "about", "pricing", "marketplace",
                    "orgs", "login", "signup", "notifications", "explore",
                ):
                    return None
                return {"provider": "github", "id": repo,
                        "url": f"https://github.com/{repo}"}
            return None
        # Nexus：取数字 ID
        if "nexusmods.com" in low:
            m = re.search(r"/mods/(\d+)", t)
            if not m:
                m = re.search(r"[?&]id=(\d+)", t)
            if m:
                mid = m.group(1)
                return {"provider": "nexus", "id": mid,
                        "url": f"https://www.nexusmods.com/helldivers2/mods/{mid}"}
            return None
        # ayakamods：取 slug
        if "ayakamods.com" in low:
            m = re.search(r"/mods/([a-z0-9\-]+\.\d+)", t, re.I)
            if m:
                slug = m.group(1)
                return {"provider": "ayaka", "id": slug,
                        "url": f"https://ayakamods.com/mods/{slug}/"}
            return None
        return None

    def _preview(self) -> None:
        """实时提示识别结果。"""
        text = self.var_url.get().strip()
        if not text:
            self.lbl_detect.configure(
                text="（粘贴网址后会在这里显示识别结果）", foreground=FG_DIM)
            self.btn_verify.configure(state="disabled")
            return
        got = self._detect(text)
        if got:
            name = PROV_NAME.get(got["provider"], got["provider"])
            self.lbl_detect.configure(
                text=f"✓ 识别为 {name}　（{got['url']}）", foreground=GREEN)
            self.btn_verify.configure(state="normal")
        else:
            self.lbl_detect.configure(
                text="✗ 认不出这是哪个站点的链接。请确认是 "
                     "github.com / nexusmods.com / ayakamods.com 的**模组页面**地址。",
                foreground=RED)
            self.btn_verify.configure(state="disabled")

    def _save(self) -> None:
        text = self.var_url.get().strip()
        if not text:
            messagebox.showwarning("还没填", "请把模组的网页地址粘进来。", parent=self)
            return
        got = self._detect(text)
        if not got:
            messagebox.showwarning(
                "认不出这个链接",
                "请贴这三个站点的**模组页面**地址（不是下载直链）：\n\n"
                "· github.com/作者/仓库\n"
                "· nexusmods.com/helldivers2/mods/数字\n"
                "· ayakamods.com/mods/xxx.数字/\n\n"
                "最省事的做法：在浏览器打开那个模组的页面，\n"
                "复制地址栏整条链接粘过来。",
                parent=self,
            )
            return

        set_manual_source(self.entry_data, got)   # 打 manual 标记，受保护
        # 顺便记住用户填的本地版本（用于判断有没有更新）
        uv = self.entry_data.get("uuid") or ""
        if uv:
            arsenal.set_version_override(uv, self.var_localver.get().strip())
        # 让主界面更新本地版本显示（改了 override 后 entry 需要重读）
        if uv:
            try:
                from .arsenal import load_library
                fresh = {e["uuid"]: e for e in load_library(
                    self.master_app.data_file)}
                if uv in fresh:
                    self.on_saved_row = fresh[uv]
            except Exception:  # noqa: BLE001
                pass
        if self.on_saved:
            self.on_saved(self.on_saved_row)
        name = PROV_NAME.get(got["provider"], got["provider"])
        messagebox.showinfo(
            "已保存",
            f"已记录为 {name} 来源（标记为「手工指定」）。\n\n"
            "· 以后自动识别不会覆盖它\n"
            "· 「清空来源缓存」也不会删掉它\n\n"
            "现在点「检查更新」就会去这个页面查版本。",
            parent=self.master,
        )
        self.destroy()

    def _verify(self) -> None:
        got = self._detect(self.var_url.get())
        if got:
            open_in_browser(got["url"])


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
        # ---------------- 下载位置 ----------------
        dlbox = ttk.LabelFrame(self, text="下载位置", padding=(10, 6))
        dlbox.pack(fill="x", padx=16, pady=(14, 0))

        # ① 工具下载夹（自动下载的 ZIP 存这）
        ttk.Label(dlbox, text="① 工具下载夹（自动下载的 ZIP 存这里）：",
                  style="Dim.TLabel").pack(anchor="w")
        r1 = ttk.Frame(dlbox)
        r1.pack(fill="x", pady=(1, 6))
        self.lbl_tool_dl = ttk.Label(r1, text=tool_downloads_dir(), style="Dim.TLabel")
        self.lbl_tool_dl.pack(side="left")
        ttk.Button(r1, text="打开", width=6,
                   command=self._open_dl).pack(side="right")
        ttk.Button(r1, text="更改…", width=7,
                   command=self._change_tool_dl).pack(side="right", padx=(0, 6))
        if arsenal.is_custom_tool_downloads():
            ttk.Button(r1, text="恢复默认", width=9,
                       command=self._reset_tool_dl).pack(side="right", padx=(0, 6))

        # ② 浏览器监视夹（接力时盯这里）
        ttk.Label(dlbox, text="② 浏览器接力时监视的文件夹（你点下载后，工具在这里收文件）：",
                  style="Dim.TLabel", wraplength=660,
                  justify="left").pack(anchor="w")
        r2 = ttk.Frame(dlbox)
        r2.pack(fill="x", pady=(1, 2))
        self.lbl_watch = ttk.Label(r2, text=arsenal.browser_watch_dir(),
                                   style="Dim.TLabel")
        self.lbl_watch.pack(side="left")
        ttk.Button(r2, text="打开", width=6,
                   command=self._open_watch).pack(side="right")
        ttk.Button(r2, text="更改…", width=7,
                   command=self._change_watch).pack(side="right", padx=(0, 6))
        if arsenal.is_custom_browser_watch():
            ttk.Button(r2, text="自动探测", width=9,
                       command=self._reset_watch).pack(side="right", padx=(0, 6))
        ttk.Label(dlbox,
                  text="不知道浏览器存到哪？在浏览器里打开 edge://settings/downloads "
                       "（Chrome 是 chrome://settings/downloads）看「位置」那一行。",
                  style="Dim.TLabel", wraplength=660,
                  justify="left").pack(anchor="w")

        # ---------------- 数据位置 ----------------
        dbox = ttk.LabelFrame(self, text="数据位置", padding=(10, 6))
        dbox.pack(fill="x", padx=16, pady=(14, 0))
        cur = util.current_data_dir()
        custom = util.is_custom_data_dir()
        ttk.Label(dbox, text=f"当前：{cur}"
                             + ("　（自定义）" if custom else "　（默认位置）"),
                  style="Dim.TLabel", wraplength=660,
                  justify="left").pack(anchor="w")
        ttk.Label(dbox,
                  text="存放：设置（含密钥）、已识别的来源、站点索引、下载的 ZIP。\n"
                       "换到别处（比如非系统盘）后，数据会自动搬过去。",
                  style="Dim.TLabel", wraplength=660,
                  justify="left").pack(anchor="w", pady=(2, 4))
        drow = ttk.Frame(dbox)
        drow.pack(fill="x", pady=(2, 0))
        b_move = ttk.Button(drow, text="更改位置…", command=self._change_data_dir)
        b_move.pack(side="left")
        Tooltip(b_move, "选一个新目录，现有数据会自动搬过去（先复制、校验成功才删源）。\n"
                        "换完需要重启程序。")
        b_open = ttk.Button(drow, text="打开目录", command=self._open_data_dir)
        b_open.pack(side="left", padx=(6, 0))
        if custom:
            b_rst = ttk.Button(drow, text="恢复默认位置",
                               command=self._reset_data_dir)
            b_rst.pack(side="left", padx=(6, 0))
            Tooltip(b_rst, "把数据搬回 %LOCALAPPDATA%\\hd2-updater。")

        # ---------------- 维护 ----------------
        maint = ttk.LabelFrame(self, text="维护", padding=(10, 6))
        maint.pack(fill="x", padx=16, pady=(14, 0))
        b_idx = ttk.Button(maint, text="重建站点索引",
                           command=self.master_app._rebuild_index)
        b_idx.pack(side="left")
        Tooltip(b_idx, HOVER_TIPS["btn_index"])
        # 显示当前索引状态（含"每 7 天自动更新"的说明）
        try:
            status = matcher.index_status_text()
        except Exception:  # noqa: BLE001
            status = ""
        ttk.Label(maint, text=f"当前状态：{status}\n"
                              f"平时不用点 —— 过期会自动重建。",
                  style="Dim.TLabel", justify="left").pack(side="left", padx=(10, 0))

        # ---------------- 底部按钮 ----------------
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=16, pady=(16, 14))
        ttk.Button(btns, text="全部保存并关闭", style="Accent.TButton",
                   command=self._save_and_close).pack(side="right")
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="清空来源缓存（保留手工指定的）",
                   command=self._clear).pack(side="left")

        # 让窗口按内容自适应高度，避免按钮被挤出可视区（统一走公用函数）
        fit_and_center(self, master, min_w=700)

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

    def _open_data_dir(self) -> None:
        d = util.current_data_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606
        except Exception:  # noqa: BLE001
            messagebox.showinfo("数据目录", d, parent=self)

    # ---- 下载位置：工具下载夹 ----
    def _change_tool_dl(self) -> None:
        p = filedialog.askdirectory(
            title="选择「工具下载夹」——自动下载的 ZIP 会存到这里", parent=self)
        if not p:
            return
        arsenal.write_config({"tool_downloads_dir": os.path.abspath(p)})
        self.lbl_tool_dl.configure(text=arsenal.tool_downloads_dir())
        self.master_app.var_status.set(f"工具下载夹已改为 {arsenal.tool_downloads_dir()}")
        self._refresh_dl_buttons()

    def _reset_tool_dl(self) -> None:
        arsenal.write_config({"tool_downloads_dir": ""})
        self.lbl_tool_dl.configure(text=arsenal.tool_downloads_dir())
        self.master_app.var_status.set("工具下载夹已恢复默认（数据目录下的 downloads）")
        self._refresh_dl_buttons()

    def _open_dl(self) -> None:
        d = arsenal.tool_downloads_dir()
        try:
            os.startfile(d)  # noqa: S606
        except Exception:  # noqa: BLE001
            messagebox.showinfo("工具下载夹", d, parent=self)

    # ---- 下载位置：浏览器监视夹 ----
    def _change_watch(self) -> None:
        cands = arsenal.guess_browser_download_dirs()
        tip = ("\n\n常见位置（可直接把路径粘进上一层的选择框）:\n  "
               + "\n  ".join(cands[:5])) if cands else ""
        if not messagebox.askyesno(
            "选择监视的文件夹",
            "接下来会打开文件夹选择框。\n\n"
            "请选中**你的浏览器实际下载到的那个文件夹** —— "
            "可以到浏览器里 edge://settings/downloads 看「位置」。" + tip,
            parent=self,
        ):
            return
        p = filedialog.askdirectory(
            title="选择浏览器实际下载到的文件夹", parent=self)
        if not p:
            return
        arsenal.write_config({"browser_watch_dir": os.path.abspath(p)})
        self.lbl_watch.configure(text=arsenal.browser_watch_dir())
        self.master_app.var_status.set(f"浏览器监视夹已改为 {arsenal.browser_watch_dir()}")
        self._refresh_dl_buttons()

    def _reset_watch(self) -> None:
        arsenal.write_config({"browser_watch_dir": ""})
        self.lbl_watch.configure(text=arsenal.browser_watch_dir())
        self.master_app.var_status.set("浏览器监视夹已改回自动探测（系统「下载」目录）")
        self._refresh_dl_buttons()

    def _open_watch(self) -> None:
        d = arsenal.browser_watch_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606
        except Exception:  # noqa: BLE001
            messagebox.showinfo("浏览器的下载文件夹", d, parent=self)

    def _refresh_dl_buttons(self) -> None:
        """路径变了就重建这几行按钮（「恢复默认/自动探测」按需出现）。"""
        # 简单起见：重开设置窗会刷新；这里只更新标签，够用且不出错
        pass

    def _change_data_dir(self) -> None:
        """选新目录并把数据搬过去（后台执行，不卡界面）。"""
        # 防呆④：正在跑检查/识别时不许搬，避免搬移与后台写入交错
        if self.master_app.worker and self.master_app.worker.is_alive():
            messagebox.showwarning(
                "先等一等",
                "现在正在检查更新 / 识别来源。\n\n"
                "请等它跑完（或先关掉程序）再更改数据位置 —— "
                "否则搬移会和后台写入撞在一起。",
                parent=self,
            )
            return

        new = filedialog.askdirectory(
            title="选择新的数据目录（现有数据会自动搬过去）",
            parent=self,
        )
        if not new:
            return
        old = util.current_data_dir()
        new_abs = os.path.abspath(new)
        if os.path.normcase(new_abs) == os.path.normcase(old):
            messagebox.showinfo("没变化", "选的就是当前目录。", parent=self)
            return

        # 防呆①：目标已有同名文件 —— 明确列出并让用户决定
        conflicts = util.existing_conflicts(new_abs)
        allow_overwrite = False
        if conflicts:
            allow_overwrite = messagebox.askyesno(
                "目标目录已有同名文件",
                "这些文件在目标目录里**已经存在**，继续会覆盖它们：\n\n  "
                + "\n  ".join(conflicts)
                + "\n\n如果那是你以前的备份或别的数据，请点「否」换个空目录。\n\n"
                  "确定要覆盖吗？",
                icon="warning",
                parent=self,
            )
            if not allow_overwrite:
                return

        if not messagebox.askyesno(
            "确认搬移",
            f"把数据从\n  {old}\n搬到\n  {new_abs}\n\n"
            f"搬移过程是「先复制、校验成功再删原件」，中途出错不会丢数据。\n\n"
            f"搬完需要重启程序生效。要继续吗？",
            parent=self,
        ):
            return

        # 实际搬移放后台，避免大目录/跨盘复制时界面卡死（防呆③）
        self.master_app.var_status.set("正在搬移数据，请稍候…")
        self._busy(True)
        try:
            self.prog.configure(mode="indeterminate")
            self.prog.start(60)
        except Exception:  # noqa: BLE001
            pass

        def run() -> None:
            err = ""
            try:
                ok, msg = util.move_data_dir(
                    new_abs,
                    progress=lambda m: self.master_app.msgq.put(("log", m)),
                    allow_overwrite=allow_overwrite,
                )
            except Exception as e:  # noqa: BLE001
                ok, msg = False, f"意外错误：{e}"
            self.master_app.msgq.put(("moved", (ok, msg)))

        threading.Thread(target=run, daemon=True).start()

    def _busy(self, on: bool) -> None:
        """搬移期间禁用会碰数据的按钮。"""
        state = "disabled" if on else "normal"
        for b in (self.master_app.btn_check, self.master_app.btn_find,
                  self.master_app.btn_dl, self.master_app.btn_unchecked):
            try:
                b.configure(state=state)
            except Exception:  # noqa: BLE001
                pass

    def _reset_data_dir(self) -> None:
        """搬回默认位置。"""
        if self.master_app.worker and self.master_app.worker.is_alive():
            messagebox.showwarning("先等一等",
                                   "正在检查更新 / 识别来源，请等它跑完再操作。",
                                   parent=self)
            return
        default = util.default_data_dir()
        if not messagebox.askyesno(
            "恢复默认位置",
            f"把数据搬回：\n  {default}\n\n继续吗？",
            parent=self,
        ):
            return
        self.master_app.var_status.set("正在搬回默认位置…")
        self._busy(True)
        try:
            self.prog.configure(mode="indeterminate")
            self.prog.start(60)
        except Exception:  # noqa: BLE001
            pass

        def run() -> None:
            try:
                ok, msg = util.move_data_dir(default, allow_overwrite=True)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, f"意外错误：{e}"
            self.master_app.msgq.put(("moved", (ok, msg)))

        threading.Thread(target=run, daemon=True).start()

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
