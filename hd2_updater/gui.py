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
    ST_UPDATE,
    ST_UNKNOWN,
    Engine,
    download,
    load_sources,
    open_in_browser,
    save_sources,
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
}
PROV_NAME = {"github": "GitHub", "nexus": "N网", "ayaka": "ayakamods"}


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
        ttk.Button(row, text="重新探测", command=self._auto_detect).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="手动选择…", command=self._pick_arsenal).pack(side="left", padx=(6, 0))

        # ---------- 操作栏 ----------
        bar = ttk.Frame(self, padding=(12, 6))
        bar.pack(fill="x")
        self.btn_check = ttk.Button(bar, text="检查更新", style="Accent.TButton",
                                    command=self._start_check)
        self.btn_check.pack(side="left")
        self.btn_dl = ttk.Button(bar, text="下载全部可自动下载的", command=self._download_all,
                                 state="disabled")
        self.btn_dl.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="重建站点索引", command=self._rebuild_index).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="设置…", command=self._open_settings).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(bar, text="智能搜索来源", variable=self.var_use_search).pack(side="left", padx=(14, 0))

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
            # 还没检查，先列出模组
            rows = [
                {
                    "entry": e, "status": ST_NO_SOURCE, "installed": e.get("installed_version", ""),
                    "best": None, "checks": [],
                }
                for e in self.library
            ]
        else:
            rows = results

        # 排序：有更新的排最前
        order = {ST_UPDATE: 0, ST_BETA: 1, ST_ERROR: 2, ST_UNKNOWN: 3, ST_NO_SOURCE: 4, ST_OK: 5}
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
                note = (f"可自动下载" if best.get("download_url")
                        else "需在浏览器登录后下载")
                if best.get("prerelease"):
                    note += "（测试版）"
            elif r["status"] == ST_BETA:
                note = "只有测试版可更，自行决定"
            elif r["status"] == ST_ERROR:
                note = (best.get("error") or "")[:60]
            elif r["status"] == ST_NO_SOURCE:
                note = "没找到来源 —— 双击打开搜索，或右键指定"
            elif r["status"] == ST_OK:
                note = "无需处理"
            if best.get("how") and r["status"] in (ST_UPDATE, ST_OK):
                note = (best.get("how") or "")[:70]

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
        parts = [f"共 {n} 个"]
        if upd:
            parts.append(f"有更新 {upd}")
        if beta:
            parts.append(f"仅测试版 {beta}")
        if err:
            parts.append(f"失败 {err}")
        if nosrc:
            parts.append(f"未识别 {nosrc}")
        self.var_summary.set("　|　".join(parts))
        self.btn_dl.configure(state=("normal" if upd else "disabled"))

    def _row_of(self, iid: str) -> dict | None:
        for w in self._row_widgets:
            if w["iid"] == iid:
                return w
        return None

    # ------------------------------------------------------------ 检查
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
        data = load_sources()
        data[key] = [src]
        save_sources(data)
        if self.on_saved:
            self.on_saved()
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

    def __init__(self, master: App) -> None:
        super().__init__(master)
        self.title("设置")
        self.configure(bg=BG)
        self.geometry("640x430")
        self.resizable(False, False)
        self.master_app = master
        self.transient(master)
        self.grab_set()

        ttk.Label(self, text="设置", style="Head.TLabel").pack(anchor="w", padx=16, pady=(14, 6))

        ttk.Label(self, text="Nexus Mods 个人 API key（可选，但填了才能自动查 N网更新）",
                  style="TLabel").pack(anchor="w", padx=16)
        ttk.Label(self,
                  text="到 nexusmods.com → 账号设置 → API Keys → 复制 Personal API Key。"
                       "个人自用是允许的。",
                  style="Dim.TLabel", wraplength=600).pack(anchor="w", padx=16)
        e1 = tk.Entry(self, textvariable=master.var_nexus_key, bg=BG3, fg=FG,
                      insertbackground=FG, relief="flat", show="•", font=("Consolas", 10))
        e1.pack(fill="x", padx=16, pady=(4, 14), ipady=5)

        ttk.Label(self, text="GitHub Token（可选，能提高查询配额）", style="TLabel").pack(anchor="w", padx=16)
        ttk.Label(self, text="不填也行（每小时 60 次）。填了可到 5000 次/小时。",
                  style="Dim.TLabel").pack(anchor="w", padx=16)
        e2 = tk.Entry(self, textvariable=master.var_github_token, bg=BG3, fg=FG,
                      insertbackground=FG, relief="flat", show="•", font=("Consolas", 10))
        e2.pack(fill="x", padx=16, pady=(4, 14), ipady=5)

        ttk.Label(self, text=f"下载保存位置：{tool_downloads_dir()}",
                  style="Dim.TLabel", wraplength=600).pack(anchor="w", padx=16)
        ttk.Label(self, text=f"下载文件夹（浏览器接力时监视）：{default_downloads_dir()}",
                  style="Dim.TLabel", wraplength=600).pack(anchor="w", padx=16, pady=(2, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=16, pady=16)
        ttk.Button(btns, text="保存", style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="清空来源缓存（强制重新识别）",
                   command=self._clear).pack(side="left")

    def _save(self) -> None:
        write_config({
            "nexus_api_key": self.master_app.var_nexus_key.get().strip(),
            "github_token": self.master_app.var_github_token.get().strip(),
        })
        self.destroy()

    def _clear(self) -> None:
        if messagebox.askyesno("确认", "清空已识别的来源？\n下次检查会重新智能搜索。", parent=self):
            save_sources({})
            self.master_app.var_status.set("来源缓存已清空")


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
