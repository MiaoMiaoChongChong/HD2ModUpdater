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
import re
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, arsenal, matcher, providers, util
from .arsenal import (
    find_data_file,
    first_mod_priority,
    load_library,
    read_config,
    tool_downloads_dir,
    write_config,
)
from .deploy import DeployError, collect_slots
from .deploy import build_plan
from .deploy import deploy as deploy_plan
from .arsenal_sync import (
    SyncError,
    arsenal_running,
    library_uuids,
    profile_names_with,
    read_options,
    remove_library_mod,
    running_warning,
    set_options,
)
from .modimport import ModImportError, import_paths, inspect_zip, split_drop_paths
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
    clear_source,
    download,
    load_sources,
    open_in_browser,
    restore_sources as engine_restore_sources,
    save_sources,
    set_manual_source,
    source_key,
)
from . import backups
from . import diag
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

# 「需要先装 HD2 Arsenal」的说明文案。
# 为什么单独抽出来：这是**前置条件**，不是"你指错了路"。实测过（把
# LOCALAPPDATA/APPDATA 指到空目录模拟没装 Arsenal 的机器）：没有它的话，
# 更新检查没清单可查、拖入 ZIP 会报 SyncError、部署会报"没有加载顺序" ——
# 也就是说这个工具是 Arsenal 的配套件，不是独立管理器。
# （Nexus 编号 4664 已用 API 核实：HD2 Arsenal - Mod Manager - Companion Desktop App）
NEED_ARSENAL_TITLE = "需要先安装 HD2 Arsenal"
NEED_ARSENAL_TEXT = (
    "没有找到 HD2 Arsenal 的模组库。\n\n"
    "本工具是 HD2 Arsenal 的配套工具，不自己维护模组清单 ——\n"
    "「你装了哪些 mod、哪些启用、加载顺序」这三样都在 Arsenal 的数据文件里，\n"
    "检查更新和部署都以它为前提。所以这台电脑没装 Arsenal 的话，本工具用不了。\n\n"
    "· 已经装了 Arsenal？点「手动选择…」指向这个文件：\n"
    "    %LOCALAPPDATA%\\hd2arsenal\\hd2a_data.json\n\n"
    "· 还没装？到 Nexus 搜「HD2 Arsenal」（mod 4664）装好再回来。"
)

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
              "那些双击对应那一行即可：会打开下载页，下完自动收回来。\n\n"
              "★ 下载只是把文件拿回来，还不会装进 Arsenal。\n"
              "要应用它：到左边的「下载 / 导入」页点「导入并更新」，\n"
              "再到「部署模组」页部署一次。",
    "btn_index": "重建站点索引 = 把 ayakamods 上全部 HD2 模组的清单爬下来，\n"
                 "存成本地索引（约 500 个，需 1~2 分钟）。\n\n"
                 "为什么需要：ayakamods 不提供搜索接口（对脚本返回 403），\n"
                 "所以只能先把整站列表抓下来，之后在本地做名字匹配，\n"
                 "用来自动识别你的模组对应站上哪个页面。\n\n"
                 "平时完全不用点：\n"
                 "· 首次使用会自动建立\n"
                 "· 超过 7 天会自动重建（程序启动时在后台悄悄做）\n"
                 "· 点了「识别来源」时也会按需更新\n\n"
                 "只有当你急着识别新模组、不想等自动流程时才需要手动触发。",
    "btn_deployed": "只查「部署」列是「✓已部署」的模组有没有新版本。\n\n"
                    "这些是你游戏里正在用的，优先看它们要不要更新最有效率；\n"
                    "存在电脑里但没部署到游戏的会被跳过，不查。\n\n"
                    "比「检查更新」快（模组少、也不重建站点索引）。\n"
                    "想把全部模组都查一遍就点「检查更新」。",
    "btn_settings": "填写 N网 API key、GitHub token，\n查看下载位置，或清空已识别的来源。",
    "btn_deploy": "把「部署」列里已启用的模组部署到游戏，让它们生效。\n\n"
                  "这是本工具自己做部署（不走 Arsenal），所以：\n"
                  "· 勾选哪些就部署哪些，可以只放一部分模组\n"
                  "· 加载顺序按 Arsenal 里的顺序：默认设置下越靠后优先级越高\n"
                  "· 会先备份游戏目录里现有的部署文件\n\n"
                  "注意：部署后请不要再用 Arsenal 部署，\n"
                  "两边会互相覆盖（同一次只能有一个来部署）。",
    "chk_search": "勾选＝检查时自动帮你搜索并识别每个模组的来源页面。\n"
                  "取消勾选＝只用已经确认过的来源，不做新的搜索（更快）。\n"
                  "默认勾选。",
    "ent_arsenal": "HD2 Arsenal 的模组库位置。\n"
                   "程序会自动探测，探测不到才需要手动选择。",
    "btn_feedback": "遇到问题、想反馈的时候用这里。\n\n"
                    "它会把这个工具的版本、系统、模组清单、"
                    "最近做过哪些操作、网络通不通，拼成一段文字。\n"
                    "你把这段贴到反馈里，我就能直接定位问题 —— "
                    "不用再来回问你「你装了什么、点了哪一步」。\n\n"
                    "★ 里面不会有你的 N网 key 和 GitHub token。",
    "btn_feedback_page": "生成反馈信息（可复制、可存文件、可直接打开反馈页）。",
    "btn_log": "打开日志文件。\n"
               "工具会把每次检查/下载/导入/部署的关键步骤记在这里，"
               "保留最近 7 天。\n\n"
               "出问题时把它（或「生成反馈信息」的内容）发给我就行。",
}


def fit_and_center(dlg: tk.Toplevel, master, min_w: int = 620,
                   min_h: int = 0, pad: int = 12, max_h: int = 0) -> None:
    """按内容自适应窗口大小并居中到父窗口。

    为什么要有这个公用函数：写死 geometry("宽x高") 会导致内容超出可视区、
    底部按钮被挤出屏幕 —— 这个 bug 在 SettingsDialog 上犯过一次，
    接着又在 SourceDialog 上原样犯了一次（只修了「那一个」而不是「这一类」）。
    所以统一走这里，新增对话框一律调用它。

    - 宽度取内容所需与 min_w 的较大者（避免太窄难看）
    - 高度严格按内容所需 + pad（保证按钮一定看得见）
    - max_h 非 0 时给高度封顶 —— 内容本来就该滚动的对话框（如设置）
      用它把窗口限制在合理大小，否则内容一多就顶到屏幕边缘
    - 再做一次上限保护：不超过父窗口的 95%，避免超出屏幕
    """
    dlg.update_idletasks()
    w = max(min_w, dlg.winfo_reqwidth())
    h = max(min_h, dlg.winfo_reqheight() + pad)
    if max_h:
        h = min(h, max_h)
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


# --------------------------------------------------------------- 文件拖放支持
# tkinter 原生**不支持**从资源管理器拖文件进来，要靠 tkdnd（走 tkinterdnd2）。
# 它是可选依赖：装了就能拖，没装/加载失败也不影响程序启动，只是退化成
# "点按钮选文件" —— 绝不因为一个附加功能让程序打不开。
try:
    import tkinterdnd2 as _tkdnd

    _DND_FILES = _tkdnd.DND_FILES
except Exception:  # noqa: BLE001
    _tkdnd = None
    _DND_FILES = None


def _pick_root_class():
    """选出根窗口的基类：能加载 tkdnd 就用支持拖放的，否则退回普通 Tk。

    这里**真的试建一次**再销毁 —— 光"import 成功"不代表运行期 tkdnd 库
    能加载（打包漏了平台文件就是这样），那种情况下必须安静降级而不是崩掉。
    """
    if _tkdnd is None:
        return tk.Tk
    try:
        probe = _tkdnd.TkinterDnD.Tk()
        probe.withdraw()
        probe.destroy()
        return _tkdnd.TkinterDnD.Tk
    except Exception:  # noqa: BLE001
        return tk.Tk


_ROOT_CLASS = _pick_root_class()
DND_READY = _ROOT_CLASS is not tk.Tk


def register_drop(widget, handler) -> bool:
    """把某个控件登记成"可以接收文件拖放"的目标。不支持时返回 False（不抛错）。"""
    if not DND_READY:
        return False
    try:
        widget.drop_target_register(_DND_FILES)
        widget.dnd_bind("<<Drop>>", handler)
        return True
    except Exception:  # noqa: BLE001
        return False


class ScrollFrame(ttk.Frame):
    """带垂直滚动条的容器：内容比窗口高时，用户能滚下去看。

    为什么要它（用户原话 2026-10-11）：
    「设置页面现在太长了，显示不完全，改成可以上下滚动的那种吧」。
    设置页内容一路加下来（密钥 / 下载位置 / 数据位置 / 备份保留 / 维护 /
    反馈与日志），而 fit_and_center 是**按内容高度**撑窗口的 —— 撑到屏幕
    边缘之后，最后那几段就永远看不见了。

    用法：把它当普通 Frame 用，往里塞内容请塞 ``.inner``：

        sc = ScrollFrame(parent)
        sc.pack(fill="both", expand=True)
        ttk.Label(sc.inner, text="...").pack()

    滚轮：Windows 上滚轮事件只发给鼠标底下的控件，而内容里全是子控件，
    所以在 canvas 上绑是没用的 —— 这里用 ``bind_all``（模态对话框里
    作用域就是它自己），并在窗口销毁时解绑，避免影响别的窗口。
    """

    def __init__(self, parent, **kw) -> None:
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.vs = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vs.set)
        self.vs.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner,
                                              anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self._wheel_on = False
        # 鼠标移进移出时开关滚轮：只在指针真的在这个滚动区里才响应
        self.canvas.bind("<Enter>", lambda _e: self.enable_wheel())
        self.canvas.bind("<Leave>", lambda _e: self.disable_wheel())
        self.inner.bind("<Enter>", lambda _e: self.enable_wheel())
        self.inner.bind("<Leave>", lambda _e: self.disable_wheel())

    def _on_inner(self, _ev=None) -> None:
        """内容尺寸变了 → 同步可滚动范围。"""
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            pass

    def _on_canvas(self, ev) -> None:
        """窗口宽度变了 → 让内容跟着变宽（否则右边留一条空白）。"""
        try:
            self.canvas.itemconfigure(self._win, width=ev.width)
        except tk.TclError:
            pass

    def enable_wheel(self) -> None:
        if self._wheel_on:
            return
        self._wheel_on = True
        try:
            self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        except tk.TclError:
            pass

    def disable_wheel(self) -> None:
        if not self._wheel_on:
            return
        self._wheel_on = False
        try:
            self.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass

    def _on_wheel(self, event) -> None:
        """滚轮滚动内容。

        输入框 / 微调框上的滚轮留给它们自己（微调框滚轮改值、输入框滚轮
        在部分场景会切历史），不要抢 —— 否则用户在「备份保留」那里想滚
        页面，结果把份数改了。
        """
        cls = ""
        try:
            cls = event.widget.winfo_class()
        except Exception:  # noqa: BLE001
            pass
        if cls in ("TSpinbox", "Spinbox", "TEntry", "Entry", "Text", "TCombobox"):
            return
        delta = getattr(event, "delta", 0)
        if not delta:
            return
        try:
            self.canvas.yview_scroll(int(-delta / 120), "units")
        except tk.TclError:
            pass

    def scroll_to_top(self) -> None:
        try:
            self.canvas.yview_moveto(0.0)
        except tk.TclError:
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


class App(_ROOT_CLASS):  # type: ignore[misc,valid-type]
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

        # ---- 诊断与日志（越早开越好：启动阶段的异常也要留下现场）----
        # 放在这里的原因：self.msgq 已建好（钩子捕获的异常要能投递到界面），
        # 而 _build_ui 还没跑（界面回调异常也能被 report_callback_exception 兜住）。
        self._net_result: dict | None = None
        self._net_running = False
        self._last_feedback_dialog = None
        diag.setup()
        diag.install_hooks(self._on_uncaught)
        diag.event("启动", f"{APP_NAME} v{__version__}")

        self.var_arsenal = tk.StringVar(value="（正在探测…）")
        self.var_nexus_key = tk.StringVar()
        self.var_github_token = tk.StringVar()
        self.var_use_search = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="就绪")
        self.var_summary = tk.StringVar(value="")

        self._build_ui()
        # 检查数据目录是否发生回退（自定义位置不可用时，app_data_dir 会兜底）
        self.after(80, self._warn_if_data_dir_fallback)
        self.after(100, self._warn_if_watch_mismatch)
        # 存下 id：这个探测会重建整张表并清掉当前选中行，所以必须能被取消
        # （测试要在假库上验证别的流程时就得先把它关掉，否则会踩到
        #   "操作的是上一秒那一行"的时序陷阱）。
        self._startup_detect_id = self.after(120, self._auto_detect)
        self.after(150, self._pump)

    # ------------------------------------------------- 诊断 / 异常兜底
    def _on_uncaught(self, exc_type, exc, tb) -> None:
        """未捕获异常的统一出口（**可能来自任意线程**）。

        只投消息、不碰界面 —— tkinter 不是线程安全的，从工作线程直接弹窗
        会让程序卡死或崩得更难看。
        """
        try:
            text = "".join(traceback.format_exception(exc_type, exc, tb))
        except Exception:  # noqa: BLE001
            text = f"{exc_type}: {exc}"
        try:
            self.msgq.put(("uncaught", text))
        except Exception:  # noqa: BLE001
            pass

    def report_callback_exception(self, exc_type, exc, tb) -> None:
        """tkinter 回调里的异常（按钮一点就炸的那种）。

        默认实现只往 stderr 打一行 —— 打包成 --windowed 的 EXE 后
        **根本没有 stderr**，用户看到的就是"点了没反应"，我这边零证据。
        这里改成：写日志 + 弹窗告诉用户 + 顺手给出反馈入口。
        """
        try:
            text = "".join(traceback.format_exception(exc_type, exc, tb))
        except Exception:  # noqa: BLE001
            text = f"{exc_type}: {exc}"
        diag.log("界面回调异常：\n" + text, "ERROR")
        self.msgq.put(("uncaught", text))

    def _log(self, m: str) -> None:
        """引擎的过程日志 → 界面状态栏 **+ 日志文件**。

        为什么必须一起落盘：这些行就是「为什么这个模组没识别出来 /
        为什么查不到更新」的完整推理过程。以前它们只在状态栏一闪而过，
        用户报障时一行都拿不到 —— 只能靠猜。
        """
        diag.log(m)
        self.msgq.put(("log", m))

    def _progress(self, d: int, t: int, m: str) -> None:
        """进度回调：同样落盘（事后能看出卡在哪一个模组上）。"""
        diag.log(f"[{d}/{t}] {m}")
        self.msgq.put(("prog", (d, t, m)))

    def net_check_async(self, force: bool = False) -> None:
        """后台跑一次网络自检（结果通过消息队列回到界面，不卡 GUI）。"""
        if self._net_running:
            return
        if self._net_result is not None and not force:
            return
        if _dialogs_suppressed():
            # 测试环境：不发真实网络请求，直接给一个"未检测"的结果，
            # 界面渲染逻辑照旧能验证。
            self._net_running = False
            self._net_result = {"proxy": "（测试环境，未检测）", "sites": [],
                                "when": util.now_str()}
            self.msgq.put(("net", self._net_result))
            return
        self._net_running = True

        def run() -> None:
            try:
                r = diag.network_check()
            except Exception as e:  # noqa: BLE001
                r = {"proxy": f"检测出错：{e}", "sites": [],
                     "when": util.now_str()}
            self.msgq.put(("net", r))

        threading.Thread(target=run, daemon=True).start()

    def cancel_startup_detect(self) -> None:
        """取消启动时排队的那次 Arsenal 位置探测。"""
        try:
            self.after_cancel(self._startup_detect_id)
        except Exception:  # noqa: BLE001
            pass

    def _alive(self) -> bool:
        """窗口还在吗？—— 延时回调（after）统一先问这一句。

        窗口关掉后，已排队的 after 回调仍会被触发，而它引用的控件已经销毁，
        Tk 会刷一串 `invalid command name "..."`。真实用户关窗口时也会遇到
        （只是没控制台看不见），测试里则每次都刷屏、把真正的失败埋掉。
        """
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _warn_if_data_dir_fallback(self) -> None:
        """自定义数据目录不可用时，明确告诉用户（而不是让他看到莫名报错）。"""
        if not self._alive():
            return
        reason = util.take_fallback_reason()
        if reason:
            self.var_status.set(reason)
            messagebox.showwarning(
                "数据目录不可用",
                f"{reason}\n\n"
                f"可能原因：那块盘没插好/已拔掉、盘符变了、或路径被改过。\n\n"
                f"程序已临时改用默认位置，不会丢数据；\n"
                f"要修好的话：把盘接回来，或到「设置…→数据位置」重新指定。",
            )
        # 启动后延迟一点在**后台**刷新站点索引（若已过期），
        # 这样用户真正点「识别来源」时就不必再等那一两分钟。
        self.after(1500, self._prewarm_index)

    def _warn_if_watch_mismatch(self) -> None:
        """接力相关的两个位置落在同一块盘上吗？跨盘现在能搬，但值得提一句。

        背景：这个检查最初是因为一个真 bug —— 浏览器下载夹在 C 盘、
        工具下载夹被改到 E 盘时，搬文件用的 os.replace 会抛
        ``WinError 17 系统无法将文件移到不同的磁盘驱动器``，而调用方没看结果，
        于是弹出"已收到文件（0 个）"，用户看着文件还在下载夹里一头雾水。

        现在搬移已经换成能跨盘的 shutil.move，跨盘不再是问题；
        这里只是**顺手提示**一下更省事的配置，不是报错，也不阻止使用。
        """
        if not self._alive():
            return
        try:
            watch = arsenal.browser_watch_dir()
            tool = arsenal.tool_downloads_dir()
        except Exception:  # noqa: BLE001
            return
        dw = os.path.splitdrive(os.path.abspath(watch))[0].upper()
        dt = os.path.splitdrive(os.path.abspath(tool))[0].upper()
        if not dw or not dt or dw == dt:
            return
        # 只在用户**自定义**过位置时才提（默认情况下的跨盘也是正常用法）
        if not (arsenal.is_custom_tool_downloads() or arsenal.is_custom_browser_watch()):
            return
        self.var_status.set(f"提示：下载夹在 {dw} 盘、工具下载夹在 {dt} 盘（跨盘搬移已支持，不影响使用）")
        self.msgq.put(("log", "接力位置跨盘：会用复制方式搬文件，大文件会多花一点时间。"))

    def _prewarm_index(self) -> None:
        """后台预热 ayakamods 索引：只在过期且当前没在忙时做。"""
        if not self._alive():
            return
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
                    progress=lambda m: self._log(f"[后台] {m}")
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

        # ---------- 外壳：左侧导航 + 右侧内容区 ----------
        # 「部署」会改游戏目录，是独立工作流，单独成页；更新检查的既有布局原样搬进
        # 右侧内容区，结构尽量不动（它上面的回归坑已经踩过几次）。
        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(shell, bg=BG2, width=170)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.content = ttk.Frame(shell)
        self.content.pack(side="left", fill="both", expand=True)

        self.page_update = ttk.Frame(self.content)
        self.page_downloads = ttk.Frame(self.content)
        self.page_deploy = ttk.Frame(self.content)
        self.page_backups = ttk.Frame(self.content)
        self.page_feedback = ttk.Frame(self.content)
        # 顺序按用户实际流程排：先查更新 → 把下好的导进来 → 再部署进游戏 → 备份收尾
        # 「反馈 / 帮助」放最后：它是"出问题时才去"的地方，不该挤在日常动线上。
        self._pages = {"update": self.page_update,
                       "downloads": self.page_downloads,
                       "deploy": self.page_deploy,
                       "backups": self.page_backups,
                       "feedback": self.page_feedback}
        # 部署页延后到第一次进入时才构建 —— 否则启动就要读一遍 Arsenal 库
        self._deploy_panel = None
        self._downloads_panel = None
        self._backups_panel = None
        self._feedback_panel = None
        # 「需要先装 Arsenal」的弹窗只弹一次（之后靠状态栏 + 手动选择按钮）
        self._need_arsenal_shown = False

        ttk.Label(self.sidebar, text=APP_NAME, style="Head.TLabel",
                  wraplength=150).pack(anchor="w", padx=12, pady=(14, 0))
        ttk.Label(self.sidebar, text=f"v{__version__}", style="Dim.TLabel").pack(
            anchor="w", padx=12, pady=(2, 14))

        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, text in (("update", "更新检查"), ("downloads", "下载 / 导入"),
                          ("deploy", "部署模组"), ("backups", "备份"),
                          ("feedback", "反馈 / 帮助")):
            b = ttk.Button(self.sidebar, text=text,
                           command=lambda k=key: self._show_page(k))
            b.pack(fill="x", padx=10, pady=3)
            self.nav_buttons[key] = b

        # ---------- 顶部：Arsenal 位置 ----------
        top = ttk.Frame(self.page_update, padding=(12, 10, 12, 6))
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
        bar = ttk.Frame(self.page_update, padding=(12, 6))
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
        self.hintbar = tk.Frame(self.page_update, bg="#3a3427")
        self.hint_label = tk.Label(
            self.hintbar, bg="#3a3427", fg="#e8d9a8", anchor="w", justify="left",
            font=("Microsoft YaHei UI", 9), padx=10, pady=6,
        )
        self.hint_label.pack(side="left", fill="x", expand=True)
        self.hint_btn = ttk.Button(self.hintbar, text="开始识别来源",
                                   style="Accent.TButton", command=self._find_sources)
        self.hint_btn.pack(side="right", padx=8, pady=4)
        self._hint_shown = False   # 由 _update_hint() 控制显示

        # 必须和 hintbar 同父 —— _update_hint 用 pack(before=self.prog) 定位，
        # 两者不同父时 Tk 会直接报 "can't pack ... inside ."
        self.prog = ttk.Progressbar(self.page_update, mode="determinate", maximum=100)
        self.prog.pack(fill="x", padx=12, pady=(2, 0))

        # ---------- 表格 ----------
        wrap = ttk.Frame(self.page_update, padding=(12, 8))
        wrap.pack(fill="both", expand=True)

        # 「配置档」是**只读**状态列：更新检查页只是让你一眼看到每个模组在
        # Arsenal 里的状态（已启用/未启用/未加入）。操作统一在部署页做，
        # 避免又变成"两个页面都能改"。
        cols = ("status", "profile", "label", "local", "remote", "site", "note")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        heads = {
            "status": ("状态", 84), "profile": ("配置档", 84), "label": ("模组", 320),
            "local": ("本地", 84), "remote": ("线上", 124), "site": ("来源", 100),
            "note": ("说明", 330),
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
        bot = ttk.Frame(self.page_update, padding=(12, 6, 12, 10))
        bot.pack(fill="x")
        ttk.Label(bot, textvariable=self.var_status, style="Dim.TLabel").pack(side="left")
        ttk.Label(bot, textvariable=self.var_summary, style="Dim.TLabel").pack(side="right")
        tip = ("提示：双击一行就能干活 —— 有更新的会直接下载（GitHub），"
               "或打开下载页并自动把文件收回来（N网 / ayakamods）；"
               "已下载的则打开那个 ZIP。右键还有更多操作。")
        ttk.Label(self.page_update, text=tip, style="Dim.TLabel").pack(
            anchor="w", padx=14, pady=(0, 6))

        # 右键菜单
        self.menu = tk.Menu(self, tearoff=0, bg=BG3, fg=FG,
                            activebackground=ACCENT, activeforeground="#1b1d21")
        self.menu.add_command(label="来源总览…（各站点的来源与版本，可直接下载）",
                              command=self._ctx_why_failed)
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
        self.menu.add_command(label="撤销来源并重新识别",
                             command=self._ctx_undo_source)
        self.menu.add_separator()
        self.menu.add_command(label="删除这个模组…", command=self._ctx_delete_mod)
        self.menu.add_separator()
        self.menu.add_command(label="打开下载目录", command=self._open_dl_dir)

        # 默认停在「更新检查」页
        self._show_page("update")
        self._enable_drops()

    # ------------------------------------------------------------ 文件拖放
    def _enable_drops(self) -> None:
        """把主要控件登记成"能接收 mod 压缩包拖放"的目标。

        登记的范围尽量广（根窗口 + 导航栏 + 两页 + 两个列表），
        这样用户"随便往界面上一拖"都能生效，不用瞄准某个小区域。
        """
        self.dnd_enabled = False
        targets = [self, self.sidebar, self.content, self.page_update,
                   getattr(self, "tree", None), getattr(self, "page_deploy", None)]
        if self._deploy_panel is not None:
            targets += [self._deploy_panel, self._deploy_panel.tree]
        for w in targets:
            if w is not None and register_drop(w, self._on_drop_files):
                self.dnd_enabled = True

    def _on_drop_files(self, event):
        """拖放进来的入口（tkdnd 回调）。"""
        paths = split_drop_paths(getattr(event, "data", "") or "")
        if not paths:
            return
        self._import_mod_files(paths)

    def _import_mod_files(self, paths: list[str]) -> None:
        """把拖/选进来的压缩包加进 Arsenal 的模组库，并在部署页给出结果。"""
        self._show_page("deploy")
        self._ensure_deploy_panel()
        self._deploy_panel.import_files(paths)

    # ------------------------------------------------------------ 删除模组
    def delete_mod(self, uuid: str, label: str = "",
                   parent_widget=None) -> bool:
        """删除一个模组（两个页面共用这一条路径，保证行为一致）。

        删除是不可逆的破坏性操作，所以这里是**唯一入口**：
        先把"会发生什么"问清楚，再动手；用户取消就什么都不做。
        返回是否真的删了。
        """
        if not uuid:
            return False
        if not self.data_file:
            error_box("找不到 Arsenal 模组库",
                      "没有读到 Arsenal 的数据文件，无法删除。",
                      parent=parent_widget or self, app=self,
                      context="删除模组时找不到 Arsenal 库")
            return False
        if arsenal_running():
            messagebox.showwarning("HD2 Arsenal 正在运行", running_warning(),
                                   parent=parent_widget or self)
            return False

        # 先查出这个模组的实际情况，好如实告诉用户
        path, deployed = "", False
        name = label
        try:
            for e in load_library(self.data_file):
                if e.get("uuid") == uuid:
                    path = e.get("path") or ""
                    deployed = bool(e.get("deployed"))
                    name = e.get("label") or name
                    break
        except Exception:  # noqa: BLE001
            pass
        try:
            profiles = profile_names_with(self.data_file, uuid)
        except Exception:  # noqa: BLE001
            profiles = []

        dlg = DeleteModDialog(parent_widget or self, name, path=path,
                              profiles=profiles, deployed=deployed)
        self._last_delete_dialog = dlg
        try:
            dlg.wait_window()
        except tk.TclError:
            pass
        if not dlg.result:
            return False
        delete_files = dlg.result[1]

        try:
            res = remove_library_mod(self.data_file, uuid,
                                     delete_files=delete_files)
        except SyncError as e:
            error_box("删除失败", str(e), parent=parent_widget or self, app=self,
                      context=f"删除模组「{name}」时被同步层拒绝")
            return False
        except Exception as e:  # noqa: BLE001
            error_box("删除失败", f"删除时出错：\n{e}",
                      parent=parent_widget or self, app=self,
                      context=f"删除模组「{name}」时发生意外错误")
            return False

        self._refresh_after_delete()
        diag.event("删除模组", f"「{res['label']}」"
                              + ("（含磁盘文件）" if res["deleted_folder"] else "（保留磁盘文件）")
                              + f"，库已备份为 {os.path.basename(res['backup'])}")
        lines = [f"已删除「{res['label']}」。",
                 f"Arsenal 库已备份为 {os.path.basename(res['backup'])}"]
        if res["removed_profiles"]:
            lines.append("已从配置档里移除：" + "、".join(res["removed_profiles"]))
        if res["deleted_folder"]:
            lines.append(f"磁盘文件已删除：\n{res['deleted_folder']}")
        else:
            lines.append("磁盘文件保留着（只是不再出现在列表里）。")
        if deployed:
            lines.append("")
            lines.append("⚠ 它之前是已部署状态：游戏里现有的文件还在，"
                         "重新部署一次才会被清理掉。")
        messagebox.showinfo("删除完成", "\n".join(lines),
                            parent=parent_widget or self)
        return True

    def _refresh_after_delete(self) -> None:
        """删完把两个页面都刷新一遍（列表来源是同一个库）。"""
        try:
            if self._deploy_panel is not None:
                self._deploy_panel.reload()
            else:
                self._ensure_deploy_panel()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.results = self._merge_keep_results()
            self._populate(self.results)
        except Exception:  # noqa: BLE001
            pass

    def _merge_keep_results(self) -> list[dict]:
        """重读库，但**保留已检查出来的结果**，只去掉已经不存在的模组。

        两个坑：
        · 结果行的身份在 ``r["entry"]["uuid"]``（**不是**顶层 uuid）；
        · 底表由 ``_initial_rows()`` 按 ``self.library`` 生成，所以要先刷新 library。
        另外**绝不能空参调 `_populate()`** —— 那等于"还没检查过"，
        会把用户已经查出来的结果全清掉（本项目踩过这个坑）。
        """
        self.library = load_library(self.data_file)
        keep = {}
        for r in (self.results or []):
            u = (r.get("entry") or {}).get("uuid")
            if u:
                keep[u] = r
        init = {(r.get("entry") or {}).get("uuid"): r
                for r in self._initial_rows()}
        out = []
        for e in self.library:
            u = e.get("uuid")
            row = keep.get(u) or init.get(u)
            if row:
                out.append(row)
        return out

    # ------------------------------------------------------------------ 导航
    def _show_page(self, key: str) -> None:
        """切换右侧内容区显示哪一页。"""
        if key == "deploy":
            self._ensure_deploy_panel()
        elif key == "downloads":
            self._ensure_downloads_panel()
            # 每次进来都重读一遍记录（下载可能是在别的页发生的）
            self._downloads_panel.refresh()
        elif key == "backups":
            self._ensure_backups_panel()
            # 每次进来重扫一遍（别的地方可能刚产生/清掉备份）
            self._backups_panel.refresh()
        elif key == "feedback":
            self._ensure_feedback_panel()
            # 每次进来刷新：日志有没有在长、网络检测有没有结果
            self._feedback_panel.refresh()
        for k, frame in self._pages.items():
            if k == key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for k, btn in self.nav_buttons.items():
            try:
                btn.configure(style="Accent.TButton" if k == key else "TButton")
            except tk.TclError:
                pass
        self._current_page = key

    def _ensure_downloads_panel(self) -> None:
        """第一次进入「下载」页时才构建它。"""
        if self._downloads_panel is None:
            self._downloads_panel = DownloadsPanel(self.page_downloads, self)
            # ★ 建完必须 pack —— 少了这步整页就是一片空白（面板没进布局、
            #   尺寸 1x1）。**部署页当初就是这样白屏的**，我在新页面上又犯了一次；
            #   测试也因此补了 winfo_manager() 断言（只断言"对象存在"抓不到）。
            self._downloads_panel.pack(fill="both", expand=True)

    def _ensure_backups_panel(self) -> None:
        """第一次进入「备份」页时才构建它。"""
        if self._backups_panel is None:
            self._backups_panel = BackupsPanel(self.page_backups, self)
            # ★ 建完必须 pack（同上下载页/部署页的教训）
            self._backups_panel.pack(fill="both", expand=True)

    def _ensure_feedback_panel(self) -> None:
        """第一次进入「反馈 / 帮助」页时才构建它。"""
        if self._feedback_panel is None:
            self._feedback_panel = FeedbackPanel(self.page_feedback, self)
            # ★ 建完必须 pack（下载页/部署页/备份页都在这里白屏过）
            self._feedback_panel.pack(fill="both", expand=True)

    def backup_context(self) -> dict:
        """清理/查看备份需要的三处路径（+ Arsenal 是否开着）。

        统一在这里取，免得「备份」页和设置里的清理各算一套、口径不一致。

        ★ **自己解析路径，不依赖 App 的异步探测**（`_auto_detect` 是
        `after(120ms)` 才跑的，启动早期/测试里 `data_file`、`game_dir` 都是空的
        —— 那样这一页会显示"没有任何备份"，看起来像功能没做）。
        路径顺序：先用 App 已经探到的，没有再自己从 Arsenal 数据文件里读。
        """
        data_file = self.data_file or ""
        # ★ 用 getattr 安全取：App 是 tk.Tk 子类，**它身上没有 game_dir 属性**
        #   （那是 DeployPanel 的字段）。直接 `self.game_dir` 会走到 tkinter 的
        #   __getattr__ 抛 AttributeError —— 备份页会整页报错。
        game_dir = getattr(self, "game_dir", "") or ""
        if not data_file:
            try:
                data_file = find_data_file()[0] or ""
            except Exception:  # noqa: BLE001
                data_file = ""
        # 游戏目录也能从 Arsenal 数据文件里的 userGameDir 读出来
        # （与 arsenal.load_library 的 game_dir 同一来源，口径一致）
        if data_file and not game_dir:
            try:
                from . import arsenal_sync as _as
                _d = _as._load(data_file)
                game_dir = str(_d.get("userGameDir") or "")
            except Exception:  # noqa: BLE001
                game_dir = ""
        mods_dir = ""
        if data_file:
            try:
                from . import arsenal_sync as _as
                mods_dir = _as.mods_dir_of(_as._load(data_file), data_file)
            except Exception:  # noqa: BLE001
                mods_dir = ""
        if not mods_dir and data_file:
            mods_dir = os.path.join(os.path.dirname(data_file), "mods")
        running = False
        try:
            running = bool(arsenal_running())
        except Exception:  # noqa: BLE001
            running = True          # 检测失败按"在运行"保守处理
        return {"data_file": data_file, "game_dir": game_dir,
                "mods_dir": mods_dir, "arsenal_running": running}

    def auto_prune_backups(self, why: str = "") -> None:
        """按保留设置自动清一次旧备份（启动时、以及每次部署成功后）。

        为什么放在这两处：部署是唯一会大量产生备份的动作（每次约 230 MB），
        所以部署完立刻按上限收一次，用户就不用管了。
        失败**绝不打扰用户** —— 清理只是省空间，失败不影响主流程。
        """
        try:
            ctx = self.backup_context()
            if not (ctx["data_file"] or ctx["game_dir"]):
                return
            rep = backups.prune_for_context(
                data_file=ctx["data_file"], game_dir=ctx["game_dir"],
                mods_dir=ctx["mods_dir"],
                arsenal_running=ctx["arsenal_running"])
            if rep["deleted"]:
                mb = rep["freed_bytes"] / 1048576
                self.var_status.set(
                    f"已清理 {len(rep['deleted'])} 项旧备份，释放约 {mb:.1f} MB"
                    + (f"（{why}）" if why else ""))
            # 页面若已建好，让它跟着刷新
            if self._backups_panel is not None:
                try:
                    self._backups_panel.refresh()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    def _refresh_downloads_if_open(self) -> None:
        """下载完成后主动刷新「下载」页（若它已经建好了）。

        为什么要主动刷：以前只在你**切到这一页**时才重读记录，所以"下载完 →
        停留在这一页"时新记录不会出现，得先切走再切回来。用户报过
        "下载好了已经在 GUI 显示出来一会了，才变到可以按的"。
        """
        panel = self._downloads_panel
        if panel is None:
            return
        try:
            panel.refresh()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_deploy_panel(self) -> None:
        """第一次进入部署页时才构建它（构建时要读 Arsenal 库）。"""
        if self._deploy_panel is None:
            self._deploy_panel = DeployPanel(self.page_deploy, self)
            # ★ 建完必须 pack —— 少了这步页面就是一片空白（面板 mapped=0、尺寸 1x1）
            self._deploy_panel.pack(fill="both", expand=True)
        else:
            # 每次切回来都重读一次库：用户可能刚在 Arsenal 里改了启用/部署状态
            self._deploy_panel.reload()

    def _open_deploy_dialog(self) -> None:
        """仍然保留对话框入口（外部/测试用）。"""
        dlg = DeployDialog(self)
        self._last_deploy_dialog = dlg

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
        if not self._alive():
            return
        path, how = find_data_file()
        if path:
            self.data_file = path
            self.var_arsenal.set(path)
            try:
                self.library = load_library(path)
                self.var_status.set(f"已找到 Arsenal 模组库（{how}），共 {len(self.library)} 个模组")
                diag.event("Arsenal 定位", f"找到（{how}）：{path}，"
                                          f"共 {len(self.library)} 个模组")
                self._populate()
            except Exception as e:  # noqa: BLE001
                error_box("读取失败", f"读不了这个文件：\n{e}",
                          parent=self, app=self,
                          context="读取 Arsenal 模组库失败")
            # 定位成功后顺手按保留份数收一次旧备份（失败不打扰用户）
            self.auto_prune_backups("启动时")
        else:
            self.var_arsenal.set("")
            self.var_status.set(
                "没找到 HD2 Arsenal 的模组库 —— 本工具需要先安装 Arsenal（见下方说明）")
            # ★ 说清"为什么"：以前只说"没找到，请手动选择位置"，会让人以为是自己
            #   指错了路，其实是**缺一个前置软件**（用户问过"没装 Arsenal 的电脑上
            #   能运行吗"，答案是不能，而且当时的提示没告诉他原因）。
            #   只弹一次，之后靠状态栏和「手动选择…」按钮，别每次启动都弹。
            if not self._need_arsenal_shown:
                self._need_arsenal_shown = True
                messagebox.showinfo(NEED_ARSENAL_TITLE, NEED_ARSENAL_TEXT,
                                    parent=self)
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
                # ★ 别用"未选择位置，无法继续"这种没信息量的话盖掉上面的提示 ——
                #   用户此刻最需要知道的是"为什么用不了、该怎么办"。
                self.var_status.set(
                    "没找到 HD2 Arsenal 的模组库 —— 本工具需要先安装 Arsenal；"
                    "已装的话请点「手动选择…」指向 hd2a_data.json")
                return
        path, how = find_data_file(p)
        if not path:
            messagebox.showwarning("没找到",
                                   "这个位置里没有 hd2a_data.json。\n\n"
                                   "通常它在：\n%LOCALAPPDATA%\\hd2arsenal\\\n\n"
                                   "你也可以直接选中该文件本身。\n\n"
                                   "如果这台电脑还没装 HD2 Arsenal，本工具用不了 —— "
                                   "模组清单、启用状态、加载顺序都由 Arsenal 提供。",
                                   parent=self)
            return
        self.data_file = path
        self.var_arsenal.set(path)
        write_config({"arsenal_data_path": path})
        try:
            self.library = load_library(path)
            self.var_status.set(f"已载入 {len(self.library)} 个模组")
            self._populate()
        except Exception as e:  # noqa: BLE001
            error_box("读取失败", str(e), parent=self, app=self,
                      context="手动选择 Arsenal 数据文件后读取失败")

    # ---------------------------------------------------------- 表格填充
    def _initial_rows(self) -> list[dict]:
        """还没检查过的「底表」：库里每个模组一行，状态是「未检查 / 没来源」。

        但**要把已知来源显示出来**，免得表格说"没来源"、双击点进去却又看到
        来源（自相矛盾）。
        """
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
                    elif s0.get("provider") == "ayaka" and s0.get("id"):
                        # 走 ayaka_url()：中文 mod 的来源 id 是百分号编码的，
                        # 这里再手写一遍拼接容易漏掉编码处理。
                        known["url"] = providers.ayaka_url(s0["id"])
            rows.append(
                {
                    "entry": e,
                    "status": ST_UNCHECKED if known else ST_NO_SOURCE,
                    "installed": e.get("installed_version", ""),
                    "best": known,
                    "checks": [],
                }
            )
        return rows

    def _ensure_full(self, rows: list[dict]) -> list[dict]:
        """保证列表覆盖库里**所有**模组，缺的补成底表那一行。

        为什么需要：增量检查（「只检查这一个」「只查未检查的」、以及撤销来源后
        的重查）返回的**只含被查的那几条**。原来把结果直接合并进 self.results，
        而 self.results 在"还没做过完整检查"时是空的 —— 于是表格被压成只剩
        那一条，用户会以为"我 20 个模组丢了 19 个"。
        """
        have = {source_key(r.get("entry") or {}) for r in rows}
        missing = [r for r in self._initial_rows()
                   if source_key(r["entry"]) not in have]
        return list(rows) + missing

    @staticmethod
    def _deploy_cell(e: dict) -> tuple[str, str]:
        """部署列的（文字, 颜色tag）。e 是模组 entry（arsenal.load_library 给的）。

        已部署 / 启用未部署（在配置档、开了开关但还没部署）/ 未部署（其余）。
        """
        if e.get("deployed"):
            return ("✓已部署", "dep_yes")
        if e.get("in_profile") and e.get("enabled"):
            return ("○未部署", "dep_no")
        return ("—未部署", "dep_no")

    @staticmethod
    def _profile_cell(e: dict) -> str:
        """更新检查页「配置档」列的**只读**文字（三态，用词不靠符号猜）。"""
        if not e.get("in_profile"):
            return "未加入"
        return "已启用" if e.get("enabled") else "未启用"

    def _populate(self, results: list[dict] | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_widgets.clear()

        if results is None:
            rows = self._initial_rows()
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
                # ★ 这一栏要回答的是「别的站点是不是也查过了」——用户看到
                #   「仅测试版」+ 来源是 a 站，会怀疑"是不是没查 GitHub"
                #   （2026-10-11 实际被问到：「是不是因为 GitHub 还没检测
                #   Pre-release，所以显示来源是 a 站，这个是对的吗？」）。
                #   表格列窄，所以这里只点出"其它来源也查过、没有正式版更新"，
                #   具体版本留在悬停提示 / 双击说明里。
                note = "只有测试版可更，自行决定"
                others = [c for c in (r.get("checks") or [])
                          if c is not best and c.get("ok")]
                if others:
                    note += "（其它来源也查过，无正式版更新）"
            elif r["status"] == ST_ERROR:
                # 表格这一列窄，放不下多行说明 —— 但「被站点挡住」和
                # 「页面不存在」要一眼能分开，所以这里压成一句话，
                # 完整出路在双击行时给出的弹窗里（见 _show_error_detail）。
                note = self._error_note(best.get("error") or "")
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
                "", "end",
                values=(txt, self._profile_cell(e), label[:60],
                        r.get("installed") or "?", remote, site, note),
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

    @staticmethod
    def _error_note(err: str) -> str:
        """把一条失败原因压成表格里放得下的一句话。

        为什么要分类：用户看到一列「查询失败」最需要立刻分清的是
        「站点不让我查」（换节点/等待）还是「这个 mod 本身有问题」（换来源），
        而不是一句笼统的"被站点拒绝"。
        """
        s = str(err or "").strip()
        if not s:
            return "查询失败（没有详细信息）——双击这一行看怎么办"
        low = s.lower()
        if "cloudflare" in low or "被站点" in s or "挡住" in s:
            return "站点挡住了自动查询 —— 双击这一行看怎么办"
        if "不存在" in s or "被删除" in s or "改名" in s:
            return "这个页面不存在了（mod 可能已删除/改名）——双击看怎么办"
        if "超时" in s or "不可达" in s:
            return "站点暂时连不上（超时）——过一会儿再试"
        if "限流" in s or "429" in s:
            return "请求太频繁被限流 —— 过一会儿再试"
        if "401" in s or "key" in low:
            return "需要 API key —— 到「设置…」里填"
        first = s.splitlines()[0]
        return (first[:70] + "…") if len(first) > 70 else first

    def _update_note(self, best: dict) -> str:
        """「有更新」那一行右边写什么 —— 讲清**怎么拿到它**，别让人猜。

        变体相关的提醒优先：如果这次挑的不是用户装的那个变体
        （作者停更了他用的变体、或认不出），必须写在最显眼的位置 ——
        实测踩过"Rows 版被悄悄换成普通版"，用户装完才发现变体被换了。
        """
        why = str(best.get("pick_reason") or "")
        warn = ""
        # 「匹配到同变体…」= 顺利用了你装的那个变体，不打扰；
        # 其余提到变体的都要提醒（包括"有更新的其它变体"这种软提醒）。
        if why and not why.startswith("匹配到同变体"):
            warn = "⚠ 变体：" + why[:74] + "　"
        if best.get("download_url"):
            return warn + "双击这一行 = 直接下载（GitHub）"
        prov = best.get("provider")
        if prov == "nexus":
            return warn + "双击这一行 = 打开文件页（登录后点 SLOW DOWNLOAD），下完自动收"
        if prov == "ayaka":
            return warn + "双击这一行 = 打开页面（登录后点 Download），下完自动收"
        return warn + "双击这一行 = 打开下载页"

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
            "两个令牌都是可选的，不填也能用，但填了会明显更顺：\n\n"
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
            log=self._log,
            progress=self._progress,
        )
        self.engine = eng

        def run() -> None:
            try:
                eng.load()
                diag.event("识别来源", "开始")
                eng.resolve_sources()
                if eng.cancelled:
                    diag.event("识别来源", "被用户取消")
                    return
                eng.persist_resolved()
                n = sum(1 for v in eng.resolved.values() if v)
                diag.event("识别来源", f"完成：{n}/{len(eng.library)} 个已识别")
                self.msgq.put(("found", (n, len(eng.library))))
            except Exception as e:  # noqa: BLE001
                diag.log("识别来源失败：\n" + traceback.format_exc(), "ERROR")
                diag.event("识别来源", f"失败：{e}")
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
            log=self._log,
            progress=self._progress,
        )
        eng = self.engine

        def run() -> None:
            try:
                eng.load()
                diag.event("检查更新",
                           f"开始（{'增量，' + str(len(only_keys)) + ' 个' if incremental else '全部模组'}）")
                # 增量模式只解析选中的那几条 —— 原来这里没传 only_keys，
                # 「只检查这一个」会把整库的来源解析全跑一遍（含 GitHub 搜索），
                # 白等十几秒还烧配额。
                eng.resolve_sources(only_keys=only_keys)
                if eng.cancelled:
                    diag.event("检查更新", "被用户取消")
                    return
                self._log("开始检查更新…")
                res = eng.check(only_keys=only_keys)
                # 增量也要落盘：否则「撤销来源并重新识别」新认出来的来源只活在
                # 这次运行的内存里，重启后又变回"没来源"，用户会以为撤销没生效。
                # persist_resolved 只补**高可信**且**盘上没有的** key，
                # 而且是重新读盘后合并，不会覆盖手工记录或别的行。
                eng.persist_resolved()
                try:
                    upd = sum(1 for r in res if r.get("status") == ST_UPDATE)
                    err = sum(1 for r in res if r.get("status") == ST_ERROR)
                    nosrc = sum(1 for r in res if r.get("status") == ST_NO_SOURCE)
                    diag.event("检查更新",
                               f"完成：共 {len(res)} 个，有更新 {upd}，"
                               f"查询失败 {err}，没来源 {nosrc}")
                except Exception:  # noqa: BLE001
                    diag.event("检查更新", f"完成：共 {len(res)} 个")
                self.msgq.put(("done_partial" if incremental else "done",
                               res))
            except Exception as e:  # noqa: BLE001
                diag.log("检查更新失败：\n" + traceback.format_exc(), "ERROR")
                diag.event("检查更新", f"失败：{e}")
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

    def _deployed_keys(self) -> set[str]:
        """「已部署」模组的 key 集合（数据以 library 为准，不依赖已检查结果）。

        「部署」概念已移到部署页，但更新检查仍然需要它 —— 用于「只查已部署的」。
        """
        src = self.library or [r.get("entry") for r in self.results if r.get("entry")]
        return {source_key(e) for e in src if e and e.get("deployed")}

    def _check_deployed_keys(self, keys: set[str]) -> None:
        """检查指定的一批模组（部署页「检查这些有没有更新」走这里）。

        没有已部署的模组时给明确提示，而不是静默什么都不做。
        """
        if not keys:
            messagebox.showinfo(
                "没有已部署的模组",
                "没发现任何「已部署」的模组。\n\n"
                "如果你确实在 Arsenal 里部署过，先点「重新探测」确认读的是当前库。",
                parent=self)
            return
        self._start_check(only_keys=set(keys))

    def _check_this_one(self) -> None:
        """只检查右键选中的那一个模组。"""
        w = self._selected()
        if not w:
            return
        if self._busy_guard():
            return
        self._start_check(only_keys={source_key(w["entry"])})

    def _pump(self) -> None:
        """主线程消费后台消息。

        ★ 整个处理过程必须**绝对不会**中断重排程：这个函数是界面唯一的心跳，
        它一旦不再排队，进度条、状态栏、下载回调、接力消息就全部静默停摆，
        用户看到的是一个"点什么都没反应"的窗口，而控制台只留一行 traceback。
        所以外层用 except 兜住任何异常（记录到状态栏，而不是让它冒出去），
        重排程放在 finally 里 —— 无论中途发生什么，心跳都必须继续。
        """
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                try:
                    self._handle_msg(kind, payload)
                except Exception as e:  # noqa: BLE001
                    # 单条消息处理失败不该拖垮整个界面：记下来、继续处理后面的
                    import traceback
                    self.var_status.set(f"处理消息时出错（已跳过）：{e}")
                    print(f"[pump] {kind} 处理失败：{e}\n{traceback.format_exc()}")
        except queue.Empty:
            pass
        except Exception as e:  # noqa: BLE001
            import traceback
            self.var_status.set(f"界面刷新出错（已恢复）：{e}")
            print(f"[pump] {e}\n{traceback.format_exc()}")
        finally:
            # ★ 窗口已经关掉时不要再排下一次 —— 否则 Tk 会报
            #   `invalid command name "..._pump"`（回调指向已销毁的控件）。
            #   测试里每次 App.destroy() 都会刷一串这种报错，真实用户
            #   关窗口时同样会出现（只是没控制台看不到，白留噪音）。
            try:
                if self.winfo_exists():
                    self.after(150, self._pump)
            except tk.TclError:
                pass

    def _handle_msg(self, kind: str, payload) -> None:
        """处理一条后台消息（从 _pump 里拆出来，便于单独兜异常）。"""
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
            # 增量检查：把新结果**合并**回原有列表，其余保持不变。
            # _ensure_full 会把库里没被选中、因此没结果的那些补回来 ——
            # 否则首屏（还没完整检查过）做一次增量，整张表会只剩那一行。
            self.btn_check.configure(state="normal", text="检查更新")
            self.btn_find.configure(state="normal")
            self.prog.configure(value=0)
            fresh = {source_key(r["entry"]): r for r in payload}
            merged = []
            for r in (self.results or []):
                k = source_key(r["entry"])
                merged.append(fresh.pop(k, r))   # 有新的就用新的
            merged.extend(fresh.values())         # 之前没有的补进来
            merged = self._ensure_full(merged)
            self.results = merged
            self._populate(merged)
            # 若是"撤销来源"触发的重查，而它没找到任何来源 → 把原记录放回去
            self._maybe_restore_after_undo(merged)
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
            # payload 是 {"n": 条数, "wrote": 这次有没有真的写盘, "before": 原有条数}
            info = payload if isinstance(payload, dict) else {"n": payload,
                                                              "wrote": True,
                                                              "before": 0}
            n = int(info.get("n") or 0)
            if info.get("wrote"):
                self.var_status.set(f"索引重建完成：{n} 个模组")
                messagebox.showinfo(
                    "索引重建完成",
                    f"已抓取 {n} 个 ayakamods 模组到本地索引。\n\n"
                    f"现在可以点「识别来源」或「检查更新」了。",
                )
            else:
                # ★ 一条都没抓到（站点把请求挡住了）—— 必须如实说，不能报"完成"。
                #   以前这里报"已抓取 902 个模组"，而用户看到设置里仍写着
                #   "1 天前更新"，只会以为自己点错了（2026-10-11 实际发生）。
                self.var_status.set("索引没能更新（没抓到内容）——原索引保留")
                why = matcher._why_crawl_empty()
                messagebox.showwarning(
                    "索引没能更新",
                    f"这次一条都没抓到，所以原来的索引被保留了"
                    f"（仍是 {info.get('before') or n} 条，还能正常用）。\n\n"
                    f"原因：\n{why}\n\n"
                    f"★ 这不是你的登录过期 —— 本工具的索引抓取是匿名的，"
                    f"和你的 a 站账号没关系。浏览器能登录进去是因为浏览器能过"
                    f"站点的自动请求拦截，而工具/脚本会被挡。\n\n"
                    f"换一个代理节点、或过一会儿再点一次，通常就好了。",
                )
        elif kind == "index_fail":
            self.prog.stop()
            self.prog.configure(mode="determinate", value=0)
            self.var_status.set("索引重建失败")
            error_box("索引重建失败", str(payload), parent=self, app=self,
                      context="重建 ayakamods 站点索引时失败")
        elif kind == "handoff":
            # 接力线程在发这条消息之前已经写好下载记录了 → 顺手刷新「下载」页，
            # 用户若正停在那页就不用切走再切回来（"下载好了…才变到可以按的"）
            self._refresh_downloads_if_open()
            ok, dest, moved, failed = payload
            if not ok and not failed:
                # 压根没扫到新文件
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
            elif failed:
                # 扫到了但没搬成 —— 必须如实说，不能报"已收到"。
                # 实测踩过：跨盘搬移失败时仍弹"已收到文件（0 个）"，
                # 用户看着文件还在下载夹里，完全对不上。
                self.var_status.set(f"接力：{len(failed)} 个文件没搬成功")
                detail = "\n".join(f"· {n}\n  {e}" for n, e in failed[:5])
                more = f"\n…另有 {len(failed) - 5} 个" if len(failed) > 5 else ""
                if moved:
                    messagebox.showwarning(
                        "部分文件没搬成",
                        f"成功搬走 {moved} 个，但有 {len(failed)} 个没搬动：\n\n"
                        f"{detail}{more}\n\n"
                        f"列表里已搬走的那几条会标成「已下载」。\n"
                        f"没搬动的还留在原文件夹里，你可以手动移过来。",
                    )
                else:
                    error_box(
                        "文件没能搬过来",
                        f"在下载文件夹里找到了 {len(failed)} 个文件，"
                        f"但搬到工具下载夹时失败了：\n\n"
                        f"{detail}{more}\n\n"
                        f"文件还在原来的下载文件夹里，没有丢，"
                        f"但工具这次没收到它。\n\n"
                        f"可以到「设置…→下载位置」把两个位置改到同一块盘上，"
                        f"或者手动把文件移过来。",
                        parent=self, app=self, context="浏览器接力收文件时搬移失败",
                    )
            else:
                self.var_status.set(f"接力收到 {moved} 个文件 → {dest}")
                # 重画，让这一条变成「已下载」
                if self.results:
                    self._populate(self.results)
                messagebox.showinfo(
                    "已收到文件",
                    f"收到 {moved} 个文件，已放进：\n  {dest}\n\n"
                    f"列表里这一条已标成「已下载」。\n\n"
                    f"接下来：到左边的「下载 / 导入」页，点「导入并更新」"
                    f"把它装进模组列表（旧内容会留一份备份），"
                    f"再到「部署模组」页部署一次才真正进游戏。",
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
                error_box("搬移失败", msg, parent=self, app=self,
                          context="搬移数据目录时失败")
            else:
                self.var_status.set(f"数据位置已改为 {util.current_data_dir()}，请重启程序")
                messagebox.showinfo(
                    "搬移完成",
                    f"{msg}\n\n请关闭并重新打开程序，之后就会用新位置。\n\n"
                    f"（指针文件仍在默认位置，只存一行路径，不影响使用）",
                )
                try:
                    self._last_settings_dialog.destroy()
                except Exception:  # noqa: BLE001
                    pass
        elif kind == "refind_done":
            # 「来源总览」重查完成 → 就地刷新那个框
            info = payload or {}
            dlg = getattr(self, "_last_query_fail_dialog", None)
            if dlg is not None:
                try:
                    if info.get("error"):
                        dlg.refresh_sources(f"重查失败：{util.short_err(info['error'], 60)}")
                    else:
                        # ★ 用**新的来源/新的查询结果**重算这一行的状态，
                        #   别让"当前结论"停在旧值。
                        #   （用户报过："下面显示已找到了，上面还是显示的没找到"）
                        self._recompute_row_after_refind(dlg, info.get("result"))
                        # 先刷新（_fill 会按新的 row 重算 self.sources），
                        # 再报条数 —— 顺序反了就会报出刷新前的旧数字。
                        dlg.refresh_sources()
                        dlg.var_hint.set(self._refind_hint(info, dlg))
                except Exception:  # noqa: BLE001
                    pass
            if self.results:
                self._populate(self.results)
        elif kind == "net":
            # 网络自检结果到了：更新缓存并通知所有正在看它的窗口
            self._net_running = False
            self._net_result = payload
            for target in (self._feedback_panel, self._last_feedback_dialog):
                if target is not None:
                    try:
                        target.refresh_net()
                    except Exception:  # noqa: BLE001
                        pass
        elif kind == "uncaught":
            # 界面回调里抛出来的异常：必须让用户看得见（以前是静默的），
            # 而且当场就能把现场交出来。
            self.var_status.set("出错了（详情已记入日志）")
            error_box("程序遇到一个错误", str(payload), parent=self, app=self,
                      context="界面操作时抛出的未捕获异常")
        elif kind == "error":
            self.btn_check.configure(state="normal", text="检查更新")
            self.var_status.set("出错了")
            error_box("检查失败", str(payload), parent=self, app=self,
                      context="检查更新时失败")
        elif kind == "dl":
            self.var_status.set(str(payload))
        elif kind == "dl_done":
            self.btn_dl.configure(state="normal", text="下载全部可自动下载的")
            self.var_status.set(str(payload)[:120])
            # 下完立刻重画：把这几条变成「已下载」，省得用户以为没成功
            if self.results:
                self._populate(self.results)
            # 「下载 / 导入」页也顺手刷一下：用户可能正停在那页等
            self._refresh_downloads_if_open()
            messagebox.showinfo("下载完成", str(payload))

    def _download_all(self) -> None:
        targets = self._auto_download_targets()
        if not targets:
            messagebox.showinfo("没有可自动下载的",
                                "有更新的模组里没有可直接下载的（只有 GitHub 能直连下载）。\n\n"
                                "N网 / ayakamods 需要登录，而且必须你亲自点一下下载按钮。\n"
                                "双击列表里那几行就会打开对应的下载页，"
                                "下完的文件会自动收进工具的下载夹。")
            return
        self.btn_dl.configure(state="disabled", text="下载中…")
        dest = tool_downloads_dir()

        def run() -> None:
            ok = 0
            diag.event("下载全部", f"开始：{len(targets)} 个可自动下载的")
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
                    diag.event("下载", f"{name} ← {b.get('provider')} "
                                       f"{b.get('version') or ''} 成功")
                    self.msgq.put(("dl", f"✓ {os.path.basename(p)}"))
                except Exception as ex:  # noqa: BLE001
                    diag.event("下载", f"{name} 失败：{util.short_err(str(ex), 80)}")
                    self.msgq.put(("dl", f"✗ {name} 失败：{ex}"))
            diag.event("下载全部", f"完成：成功 {ok}/{len(targets)}")
            self.msgq.put((
                "dl_done",
                f"下载完成 {ok}/{len(targets)} 个。\n\n文件在：\n{dest}\n\n"
                f"列表里这几条已经标成「已下载」（右键可「打开已下载的文件」）。\n\n"
                f"接下来：到左边的「下载 / 导入」页，点「导入并更新」把它们装进"
                f"模组列表，再到「部署模组」页部署一次才真正进游戏。",
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

        # ★ 「查不到」这一类，双击先给说明和出路（用户 2026-10-11 报
        #   「a 站的 mod 都查询失败」时定的）—— 直接甩一个网页给他，
        #   他并不知道这是站点拦截、也不知道下一步该换节点还是换来源。
        if st in (ST_ERROR, ST_NO_SOURCE):
            self._open_query_fail(row)
            return

        # ★ 双击一行 = 那个状态最自然的动作（见下）。"想看看有哪些来源、
        #   想换一个来源"走右键的「来源总览…」（用户 2026-10-11 提议把它做成
        #   来源总览：「直接改成来源总览，显示该mod的全部来源？然后玩家可以
        #   自己根据来源去下载」—— 已按此实现）。
        #   双击行为保持原样：改老交互比加新入口更容易伤到用户习惯。

        if st == ST_DOWNLOADED:
            # ★ 这行的意图是"我下好了，接下来把它用上" → 直接送到「下载 / 导入」页，
            #   而不是只把 ZIP 打开让用户自己去别处拖。
            #   （想单纯看看 ZIP 内容：右键菜单里有「打开压缩包」。）
            self._goto_downloads(w)
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

    @staticmethod
    def _refind_hint(info: dict, dlg) -> str:
        """重查完成后那行提示 —— 必须**如实**说清这次到底做成了什么。

        为什么不能只写"已重新查找"（用户 2026-10-12 报的正是这个）：
        「重新查找只是显示已经重新查找，结果是什么也没显示」——
        提示只报"动作发生了"，不报"结果是什么"，用户无法判断成没成。
        三种情况要分开说：
          · 查到了候选、也采用了 → 报新条数
          · 查到了候选、但可信度不够没敢采用 → **明说**（并指路"手工指定来源"），
            否则用户看到"没找到"会以为按钮白点了
          · 一个候选都没有 → 说"这次没找到"
        """
        provs = "、".join(PROV_NAME.get(p, p) for p in (info.get("providers") or []))
        stats = info.get("stats") or {}
        n = len(getattr(dlg, "sources", []) or [])
        cands = int(stats.get("cands") or 0)
        trusted = int(stats.get("trusted") or 0)
        if cands and not trusted:
            return (f"已重新查找：{provs}　→　查到 {cands} 个候选，"
                    f"但都拿不太准，没敢替你采用 —— 可用「手工指定来源…」填一次")
        if cands:
            return (f"已重新查找：{provs}　→　查到 {cands} 个候选"
                    f"（采用 {trusted} 个），现在共 {n} 条来源")
        return f"已重新查找：{provs}　→　这次没找到（现在共 {n} 条来源）"

    def _recompute_row_after_refind(self, dlg, result: dict | None = None) -> None:
        """「来源总览」重查后，用**新的来源/新的查询结果**重算这一行的状态。

        为什么要它（用户 2026-10-11）：「在来源查找界面，下面显示已找到了，
        但是上面还是显示的没找到，是不是没做同步？」—— 那个框顶部的"当前结论"
        取自 `row["status"]`，而 status 是**打开框那一刻**算出来的；重查只改了
        磁盘上的来源，没重算 status，于是上下两处自相矛盾。

        ``result`` 是重查时顺手跑的那次 `engine.check()` 结果（带版本、直链）。
        有它就直接用 —— 那是最准的；没有（如查询被取消）才退回用旧 checks 兜底。
        """
        entry = dlg.row.get("entry") or {}
        key = source_key(entry)
        try:
            if result and result.get("entry"):
                # 重查时真的查过版本了：直接用这份结果
                dlg.row["entry"] = result["entry"]
                dlg.row["status"] = result.get("status")
                dlg.row["best"] = result.get("best") or {}
                dlg.row["installed"] = result.get("installed") or ""
                dlg.row["checks"] = result.get("checks") or []
                for r in (self.results or []):
                    if source_key(r.get("entry") or {}) == key:
                        r.update({"status": result.get("status"),
                                  "best": result.get("best") or {},
                                  "installed": result.get("installed") or "",
                                  "checks": result.get("checks") or []})
                        break
                return
            from .engine import Engine, evaluate
            eng = Engine(
                self.data_file or "",
                api_key=self.var_nexus_key.get().strip(),
                github_token=self.var_github_token.get().strip(),
                use_search=True,
                log=lambda m: None, progress=lambda *_a: None,
            )
            eng.load()
            fresh = next((e for e in eng.library if source_key(e) == key), None)
            if fresh is None:
                return
            srcs = eng.resolved.get(key) or load_sources().get(key) or []
            # 用**已有结果**当 checks（不重新联网查版本），只重算"有没有来源"
            checks = [c for c in (dlg.row.get("checks") or [])]
            if not checks and srcs:
                # 之前没有 checks（那时没来源），现在有了 —— 至少要反映"找到了"
                checks = [{"provider": s.get("provider"), "ok": True,
                           "url": s.get("url"), "how": s.get("how")}
                          for s in srcs]
            res = evaluate(fresh, checks)
            dlg.row["entry"] = fresh
            dlg.row["status"] = res["status"]
            dlg.row["best"] = res.get("best") or {}
            dlg.row["installed"] = res.get("installed") or ""
            if not res.get("checks"):
                dlg.row["checks"] = checks
            # 列表里那一行也更新，避免"框里对、列表里旧"
            for r in (self.results or []):
                if source_key(r.get("entry") or {}) == key:
                    r.update({"status": res["status"], "best": res.get("best") or {},
                              "installed": res.get("installed") or ""})
                    break
        except Exception as e:  # noqa: BLE001
            diag.log("重算这一行状态失败：\n" + traceback.format_exc(), "ERROR")

    def _ctx_why_failed(self) -> None:
        """右键「为什么查不到」—— 和双击同一件事（右键里也要够得到）。"""
        w = self._selected()
        if not w:
            return
        self._open_query_fail(w["row"])

    def _open_query_fail(self, row: dict) -> None:
        """打开「这一行为什么查不到」说明框。"""
        try:
            dlg = QueryFailDialog(self, self, row)
            self._last_query_fail_dialog = dlg
        except Exception as e:  # noqa: BLE001
            error_box("打不开说明", str(e), parent=self, app=self,
                      context="打开查询失败说明框时出错")

    def download_source(self, entry: dict, src: dict) -> None:
        """下载指定来源（供「来源总览」用）。

        与列表里双击=下载同一套动作：有直链就下，没有就转成浏览器接力。
        """
        if src.get("download_url"):
            dest = tool_downloads_dir()
            ver = str(src.get("version") or "")
            prov = str(src.get("provider") or "")
            label = entry.get("label") or entry.get("dirname") or ""
            key = source_key(entry)

            def run() -> None:
                try:
                    p = download(src["download_url"], dest,
                                 src.get("asset_name", ""), src.get("sha256", ""))
                    records.mark(key, ver, p, prov, label)
                    diag.event("下载", f"{label} ← {prov} {ver}（来源总览）")
                    self.msgq.put(("dl_done", f"已下载：\n{p}"))
                except Exception as ex:  # noqa: BLE001
                    diag.event("下载", f"{label} 失败：{util.short_err(str(ex), 80)}")
                    self.msgq.put(("dl_done", f"下载失败：{ex}"))

            self.var_status.set(f"正在下载 {label}（{prov}）…")
            threading.Thread(target=run, daemon=True).start()
            return
        # 没有直链（N网 / a 站）→ 打开页面 + 盯下载夹
        url = self._handoff_url(src) or src.get("url") or src.get("release_url")
        if not url:
            messagebox.showinfo("没有链接", "这个来源没有可打开的地址。", parent=self)
            return
        self._relay_open(url)

    def _relay_open(self, url: str) -> None:
        """打开页面并开始浏览器接力（不依赖当前选中行）。"""
        watch = arsenal.browser_watch_dir()
        if not os.path.isdir(watch):
            messagebox.showwarning(
                "监视的文件夹不存在",
                f"要监视的文件夹不存在：\n  {watch}\n\n"
                f"请到「设置… → 下载位置」里改成一个真实存在的文件夹。",
                parent=self,
            )
            return
        if self._relay_cancel is not None:
            self._relay_cancel.set()
        import time as _t
        before = self._snapshot_dir(watch)
        cancel = threading.Event()
        self._relay_cancel = cancel

        def run() -> None:
            try:
                open_in_browser(url)
                found = self._watch_for_archive(before, watch, cancel=cancel)
                self.msgq.put(("handoff", (bool(found), watch, found, [])))
            except Exception as e:  # noqa: BLE001
                self.msgq.put(("handoff", (False, "", 0, [(url, str(e))])))

        self.var_status.set("已打开页面 —— 正在盯着下载文件夹，下完自动收…")
        threading.Thread(target=run, daemon=True).start()

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
                self.msgq.put(("handoff", (False, "", 0, [])))
                return

            moved = 0
            failed: list[tuple[str, str]] = []   # (文件名, 原因)
            for fp in got:
                name = os.path.basename(fp)
                try:
                    dst = os.path.join(dest, name)
                    if os.path.abspath(fp) != os.path.abspath(dst):
                        # 必须用能跨盘的搬移 —— 用户下载夹在 C 盘、
                        # 工具下载夹常被改到 D/E 盘，os.replace 会直接抛
                        # WinError 17（实测踩过，导致"收到 0 个文件"的假成功）
                        util.move_file(fp, dst)
                    moved += 1
                    # 接力也算「已下载」，界面上要跟着变
                    records.mark(key, ver, dst, prov or "", label)
                    self.msgq.put(("log", f"接力收到：{name}"))
                except Exception as e:  # noqa: BLE001
                    failed.append((name, str(e)))
                    self.msgq.put(("log", f"搬移失败 {name}：{e}"))

            # 只要有搬失败的，就不能报"成功" —— 文件还在下载夹里，
            # 报成功会让用户以为工具收走了，实际没动
            self.msgq.put(("handoff", (moved > 0, dest, moved, failed)))

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

    def _goto_downloads(self, w: dict | None = None) -> None:
        """跳到「下载 / 导入」页，并尽量选中这个模组对应的那条记录。

        为什么要跳过去而不是就地处理：那一页才是"下载 → 应用"的正规入口，
        在那里用户能看到「下载的版本 / 现在装的版本 / 待导入还是已导入」，
        也能顺便看看别的下载。就地弹一个导入框会让同一条路径有两个入口，
        行为容易走偏（本项目吃过"两处各写一份"的亏）。
        """
        key = ""
        if w:
            try:
                key = source_key(w["entry"])
            except Exception:  # noqa: BLE001
                key = ""
        self._show_page("downloads")
        panel = self._downloads_panel
        if panel is None:
            return
        if key and panel.tree.exists(key):
            panel.tree.selection_set(key)
            panel.tree.see(key)
            panel.tree.focus(key)
        elif key:
            # 记录里没有它 —— 说明这个"已下载"状态是别的原因（如文件已不在）。
            panel.var_note.set(
                "这一页里没有它的下载记录 —— 可能文件已被移走或导入过。")
        panel._sync_buttons()

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

    def _ctx_delete_mod(self) -> None:
        """右键删除选中的模组（更新检查页这一侧）。"""
        w = self._selected()
        if not w:
            return
        entry = w["entry"]
        self.delete_mod(entry.get("uuid") or "",
                        entry.get("label") or entry.get("dirname") or "")

    def _ctx_undo_source(self) -> None:
        """撤销来源并重新识别（把手工指定的记录也一并撤掉）。

        为什么需要它：手工指定的来源是**受保护**的，永远不会被自动识别覆盖，
        「清空来源缓存」也只清自动的。用户一旦手填错（或站点改名了），
        原来没有办法回到"自动识别"，只能一直用着错的记录 —— 这个菜单就是出口。

        撤销后立刻**只对这一条**重跑识别（不重建索引、不动别的结果），
        识别完顺手把版本也查了，用户不用再点一次「检查更新」。
        """
        w = self._selected()
        if not w:
            return
        entry = w["entry"]
        key = source_key(entry)
        label = entry.get("label") or entry.get("dirname") or "(未命名)"

        # ★ 先检查"撤销之后能不能真的重识别"，再做删除这种破坏性动作。
        #   反过来的话（先删、再发现不能跑）会把来源记录删掉却什么都没补回来，
        #   用户手里就真的少了一条记录。
        if not self.data_file:
            messagebox.showwarning("先选位置",
                                   "还没找到 Arsenal 模组库，没法重新识别。")
            return
        if self._busy_guard():
            return

        saved = load_sources().get(key) or []
        if not saved:
            messagebox.showinfo(
                "没有来源记录",
                f"「{label}」现在没有已保存的来源，不需要撤销。\n\n"
                f"如果它识别不出来，用右键「指定来源…」手动填一次。",
            )
            return

        manual = [s for s in saved if s.get("manual")]
        detail = "\n".join(
            f"· {PROV_NAME.get(s.get('provider',''), s.get('provider',''))}"
            f"　{s.get('url') or s.get('id') or ''}"
            + ("　（手工指定）" if s.get("manual") else "　（自动识别）")
            for s in saved
        )
        # 关掉「智能搜索来源」时，ayakamods 那一路**根本不会被尝试**，
        # 重识别必然一无所获。这种情况必须先说清楚，别让用户以为删了还能找回来。
        if not self.var_use_search.get():
            warn = ("\n\n注意：你现在的「智能搜索来源」是**关闭**状态。\n"
                    "撤销后就没法自动识别 ayakamods 来源了 —— 如果重识别\n"
                    "什么也没找到，我会把原来的来源**放回去**，不会丢。")
        else:
            warn = ""
        if not messagebox.askyesno(
            "撤销来源并重新识别",
            f"要撤销「{label}」现在的来源记录吗？\n\n{detail}"
            + ("\n\n注意：其中 " + str(len(manual))
               + " 条是你手工指定的，也会一起删掉。" if manual else "")
            + warn
            + "\n\n撤销后会立刻重新智能识别一次（不会再用手工那条）。"
              "\n若重识别没找到任何来源，原来的记录会被还原。",
        ):
            return

        gone = clear_source(entry)
        self.var_status.set(f"已撤销「{label}」的 {len(gone)} 条来源，正在重新识别…")
        # 撤销后这一条先回到「未检查」，别让表里留着旧的结论
        self._after_source_change(entry)
        self._undo_restore = {"key": key, "entry": entry, "gone": gone}
        self._start_check(only_keys={key})

    def _maybe_restore_after_undo(self, rows: list[dict]) -> None:
        """撤销+重识别之后，如果这一条仍然没有任何来源，就把原记录放回去。

        这是"撤销"的安全网：识别是尽力而为的（用户可能关了智能搜索、
        索引没建好、或当前离线），失败时宁可恢复成"手工指定"，
        也不能让用户手工找来的地址凭空消失。
        """
        state = getattr(self, "_undo_restore", None)
        if not state:
            return
        key = state["key"]
        row = next((r for r in rows
                    if source_key(r.get("entry") or {}) == key), None)
        if row is not None and row.get("status") != ST_NO_SOURCE:
            self._undo_restore = None      # 识别到了，不用还原
            return
        engine_restore_sources(state["entry"], state["gone"])
        self._undo_restore = None
        n = len(state["gone"])
        self.var_status.set(
            f"没能重新识别出来源，已把「{state['entry'].get('label') or '这个模组'}」"
            f"原来的 {n} 条来源放回去了（还是手工指定的那条）。"
        )
        messagebox.showinfo(
            "没找到新来源，已还原",
            f"重新识别没能找到「{state['entry'].get('label') or '这个模组'}」的来源，"
            f"所以把原来的 {n} 条记录放回去了，没有丢。\n\n"
            "可能的原因：\n"
            "· 「智能搜索来源」是关闭状态（ayakamods 靠它来匹配）\n"
            "· 站点索引还没建好，或当前网络不通\n"
            "· 这个模组在 ayakamods 上确实没有对应的页面\n\n"
            "如果确实想换成别的来源，用右键「指定来源…」直接填新的地址。",
        )

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
        """重建 ayakamods 索引（实测约 2 分钟：要翻页爬 40+ 页、约 870 个模组）。"""
        self.var_status.set("正在重建 ayakamods 索引（约 1~2 分钟，请稍候）…")

        # 有进度条的话动起来，让用户知道没卡死
        self.prog.configure(mode="indeterminate")
        try:
            self.prog.start(60)
        except Exception:  # noqa: BLE001
            pass

        def run() -> None:
            try:
                diag.event("重建站点索引", "开始")
                before = len(matcher.load_index())
                items = matcher.rebuild_index(
                    progress=self._log
                )
                n = len(items)
                # ★ 抓 0 条时 rebuild_index 会**保留原索引**并返回它 —— 于是
                #   n 等于原索引条数，看起来"重建成功"。用户点的明明是重建，
                #   却还是"一天前更新"，会以为自己哪里点错了（2026-10-11 实际发生）。
                #   这里用「盘上文件的年龄」判断这次到底写没写成功。
                fresh = matcher.index_age_days() < (1.0 / 1440.0)   # 1 分钟内
                diag.event("重建站点索引",
                           f"结束：返回 {n} 个，本次真的写盘={fresh}（原有 {before} 个）")
                self.msgq.put(("index_done", {"n": n, "wrote": fresh,
                                              "before": before}))
            except Exception as e:  # noqa: BLE001
                self.msgq.put(("index_fail", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        self._last_settings_dialog = dlg

    def _open_deploy_dialog(self) -> None:
        dlg = DeployDialog(self)
        self._last_deploy_dialog = dlg


class DeleteModDialog(tk.Toplevel):
    """删除模组的确认框。

    删除是破坏性操作，所以这里把"到底会发生什么"逐条写清楚，并且把
    **要不要连磁盘文件一起删**交给用户当场决定（默认不删 —— 最保守：
    文件留着，只是不再出现在列表里）。

    Arsenal 自己也有这个区分：它的 ``removeFromLibrary(uuid, b)`` 里
    ``b`` 就是"要不要连文件夹一起删"，默认不删。
    """

    def __init__(self, master, label: str, *, path: str = "",
                 profiles: list[str] | None = None, deployed: bool = False) -> None:
        super().__init__(master)
        self.title("删除模组")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.result: tuple[bool, bool] | None = None
        self.delete_files = False

        pad = ttk.Frame(self, padding=(18, 14, 18, 12))
        pad.pack(fill="both", expand=True)

        ttk.Label(pad, text="要删除这个模组吗？", style="Head.TLabel").pack(anchor="w")
        ttk.Label(pad, text=label, foreground=ACCENT, anchor="w",
                  wraplength=520, justify="left").pack(anchor="w", pady=(2, 8))

        lines = ["· 从 Arsenal 的模组列表里移除"]
        if profiles:
            names = "、".join(profiles[:3]) + ("…" if len(profiles) > 3 else "")
            lines.append(f"· 从配置档里移除（{len(profiles)} 个：{names}）")
        else:
            lines.append("· 它不在任何配置档里，只从列表移除")
        if deployed:
            lines.append("· ⚠ 它现在正被部署：游戏里已有的文件不会立刻消失，"
                         "要重新部署一次才会被清理")
        ttk.Label(pad, text="\n".join(lines),
                  style="Dim.TLabel", justify="left", wraplength=520).pack(anchor="w")

        # ★ 双态文字按钮（不用勾选框 —— 深色主题下打叉容易被误读，本项目有这条约定）
        self.btn_files = ttk.Button(pad, command=self._toggle_files)
        self.btn_files.pack(anchor="w", pady=(10, 0))
        self.lbl_path = ttk.Label(pad, text="", style="Dim.TLabel",
                                  justify="left", wraplength=520)
        self.lbl_path.pack(anchor="w", pady=(2, 0))
        self._path_text = path
        self._sync()

        bar = ttk.Frame(pad)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="删除", style="Accent.TButton",
                   command=self._confirm).pack(side="right", padx=(0, 8))

        fit_and_center(self, master, min_w=560, min_h=330)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())

    # ---- 内部 ----
    def _sync(self) -> None:
        on = self.delete_files
        self.btn_files.configure(
            text=f"同时删除磁盘上的模组文件：{'开' if on else '关'}",
            style="Accent.TButton" if on else "TButton")
        if on:
            self.lbl_path.configure(
                text=f"会删掉这个文件夹（不可恢复）：\n{self._path_text or '（未知路径）'}")
        else:
            self.lbl_path.configure(
                text="文件会留在磁盘上（只是不再出现在列表里，可随时再导入）。")

    def _toggle_files(self) -> None:
        self.delete_files = not self.delete_files
        self._sync()

    def _confirm(self) -> None:
        self.result = (True, self.delete_files)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class DeployPanel(ttk.Frame):
    """部署界面 —— 既可嵌在左侧导航的独立页里，也可放进对话框。

    设计要点：
    · **镜像接管**：默认勾选「Arsenal 里已部署的」+「已启用的」 —— 只勾一部分时
      不会把 Arsenal 原本装好的模组从游戏里悄悄抹掉。
    · **写回**：部署后把实际结果同步回 Arsenal 库，让它的「已部署」显示与事实一致。
      Arsenal 运行时禁止写回（它会用内存里的旧状态把我们改的覆盖掉）。
    · 加载顺序沿用 Arsenal 的排列，方向在界面上写明。
    """

    def __init__(self, master, app: App | None = None) -> None:
        super().__init__(master)
        self.app = app
        self.entries: list[dict] = []
        self.vars: dict[str, tk.BooleanVar] = {}
        self.plan = None
        self.data_file = ""
        self.game_dir = ""
        self.first_mod_priority = False
        self._mirror: set[str] = set()      # Arsenal 当前已部署的
        self._slot_cache: dict[str, list] = {}
        self._drag_iid: str | None = None
        self.var_sync = tk.BooleanVar(value=True)
        # 调整顺序后是否立刻写回 Arsenal（默认开，和"同步部署状态"保持一致）
        self.var_autosave_order = tk.BooleanVar(value=True)
        self.var_info = tk.StringVar(value="")
        self.var_note = tk.StringVar(value="")
        self.var_order = tk.StringVar(value="")
        self.var_game = tk.StringVar(value="")
        self.var_mirror = tk.StringVar(value="")
        self._build()
        self.reload()

    # ---------- 界面 ----------
    def _build(self) -> None:
        head = ttk.Frame(self, padding=(14, 12, 14, 0))
        head.pack(fill="x")
        ttk.Label(head, text="先启用要用的模组，再部署到游戏",
                  style="Head.TLabel").pack(anchor="w")
        self.lbl_order = ttk.Label(head, textvariable=self.var_order,
                                   style="Dim.TLabel", wraplength=640, justify="left")
        self.lbl_order.pack(anchor="w", pady=(4, 0))
        self.lbl_game = ttk.Label(head, textvariable=self.var_game,
                                  style="Dim.TLabel", wraplength=640, justify="left")
        self.lbl_game.pack(anchor="w", pady=(2, 0))

        # 镜像接管说明 —— 让用户明白这些勾是从哪来的
        self.lbl_mirror = ttk.Label(head, textvariable=self.var_mirror,
                                    style="Dim.TLabel", wraplength=640, justify="left")
        self.lbl_mirror.pack(anchor="w", pady=(2, 0))

        # ---------- 拖入 mod 压缩包 → 加进模组库 ----------
        # ★ 放在最显眼的位置（标题下方）—— 它是个"拖到这里"的操作区，
        #   贴着页面底部的话用户根本注意不到。
        #   （tkinter 的布局顺序 = pack 顺序，所以下面的 wrap 故意留到最后才 pack）
        self.dropbar = tk.Frame(self, bg="#2c3a2c", highlightthickness=1,
                                highlightbackground="#4a6a4a")
        self.dropbar.pack(fill="x", padx=14, pady=(8, 0))
        self.lbl_drop = tk.Label(
            self.dropbar, bg="#2c3a2c", fg="#cfe6cf", anchor="w", justify="left",
            text=("把 mod 压缩包拖到这里 → 加进模组列表（只进列表，不会自动启用；"
                  "想启用再在下面勾选）" if DND_READY else
                  "点右边「添加模组…」选择 mod 压缩包 → 加进模组列表"
                  "（当前环境不支持拖放）"))
        self.lbl_drop.pack(side="left", padx=10, pady=7)
        self.btn_add_mod = ttk.Button(self.dropbar, text="添加模组…",
                                      command=self._pick_mod_files)
        self.btn_add_mod.pack(side="right", padx=10, pady=7)
        Tooltip(self.btn_add_mod,
                "选择下载好的 mod 压缩包（.zip），把它加入 Arsenal 的模组列表。\n"
                "只会进列表、不会自动启用；同一个 mod 再导入会覆盖旧的。\n"
                "需要先关闭 HD2 Arsenal（它开着时不能改它的库）。")
        self._drop_targets = [self.dropbar, self.lbl_drop]
        for _w in self._drop_targets:
            register_drop(_w, self._on_drop_files)

        # 列表用 Treeview（而不是 Listbox）—— 需要多列 + 每行可点的状态列，
        # 更接近 Arsenal 那种"一行一个模组、右边有操作"的观感。
        # ★ 列表**最后才 pack**：布局顺序由 pack 决定，它带 expand 会占满剩余空间，
        #   放前面就会把工具栏/拖放区挤到页面底部去（踩过）。
        wrap = ttk.Frame(self, padding=(14, 8))
        cols = ("state", "order", "label", "slots", "mark")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 selectmode="browse")
        for c, (t, w, anchor) in {
            "state": ("启用", 74, "center"),
            "order": ("#", 46, "center"),
            "label": ("模组", 340, "w"),
            "slots": ("槽位", 62, "center"),
            "mark": ("状态", 150, "w"),
        }.items():
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=anchor)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("on", foreground=FG)
        self.tree.tag_configure("off", foreground="#7a7466")
        self.tree.tag_configure("inuse", foreground=GREEN)
        # 内容更新过、还没重新部署：用"要处理"的橙色，别让它看起来像已经好了
        self.tree.tag_configure("stale", foreground=ORANGE)
        # 拖动中的行：加个明显底色，做出"被拎起来"的观感
        self.tree.tag_configure("dragging", background="#4a4326", foreground=ACCENT)
        # ★ 点「启用」列或双击整行 = 启用/停用该模组（Arsenal 也是行内开关）
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", lambda _e: self._toggle_selected())
        self.tree.bind("<space>", lambda _e: self._toggle_selected())
        self.tree.bind("<Return>", lambda _e: self._toggle_selected())
        self.tree.bind("<Button-3>", self._on_deploy_right_click)

        # 部署页也给它一个右键菜单（原来这页没有）—— 常用动作都在手边，
        # 不用来回跑到上面的工具栏按钮
        self.menu = tk.Menu(self, tearoff=0, bg=BG3, fg=FG,
                            activebackground=ACCENT, activeforeground="#1b1d21")
        self.menu.add_command(label="启用 / 停用",
                              command=self._toggle_selected)
        self.menu.add_command(label="选项…", command=self._edit_options)
        self.menu.add_separator()
        self.menu.add_command(label="↑ 上移", command=lambda: self._nudge(-1))
        self.menu.add_command(label="↓ 下移", command=lambda: self._nudge(1))
        self.menu.add_separator()
        self.menu.add_command(label="删除这个模组…",
                              command=self._delete_selected_mod)
        # 拖动排序：按住行上下拖即可改加载优先级（越靠下优先级越高）
        self.tree.bind("<ButtonPress-1>", self._drag_start, add="+")
        self.tree.bind("<B1-Motion>", self._drag_move)
        self.tree.bind("<ButtonRelease-1>", self._drag_drop)

        row = ttk.Frame(self, padding=(14, 0))
        row.pack(fill="x")
        self.btn_toggle = ttk.Button(row, text="启用 / 停用选中项",
                                     command=self._toggle_selected)
        self.btn_toggle.pack(side="left")
        Tooltip(self.btn_toggle,
                "启用 / 停用选中那一行。\n"
                "勾上=要它生效（会立刻写回 Arsenal），取消=停用它。\n"
                "真正放进游戏要点右下角的「部署」。\n"
                "也可以直接点列表里的「启用」列，或双击整行。")
        self.btn_opts = ttk.Button(row, text="选项…", command=self._edit_options)
        self.btn_opts.pack(side="left", padx=(6, 0))
        Tooltip(self.btn_opts,
                "编辑选中模组的可选项（像 Arsenal 那样勾选）。\n"
                "比如 Megapack 里有 21 个可单独开关的功能。\n"
                "改动会写回 Arsenal，并标记为「需要重新部署」。")
        self.btn_up = ttk.Button(row, text="↑ 上移", width=7,
                                 command=lambda: self._nudge(-1))
        self.btn_up.pack(side="left", padx=(6, 0))
        self.btn_down = ttk.Button(row, text="↓ 下移", width=7,
                                   command=lambda: self._nudge(1))
        self.btn_down.pack(side="left", padx=(4, 0))
        Tooltip(self.btn_up,
                "调整加载优先级。\n"
                "默认设置下越靠下优先级越高（后加载的覆盖先加载的）。\n"
                "也可以直接在列表里拖动行。")
        ttk.Button(row, text="全选", width=8,
                   command=lambda: self._set_all(True)).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="全不选", width=8,
                   command=lambda: self._set_all(False)).pack(side="left", padx=(6, 0))
        self.btn_default = ttk.Button(row, text="恢复默认", command=self._set_default)
        self.btn_default.pack(side="left", padx=(6, 0))
        ttk.Button(row, text="重新读取", command=self.reload).pack(side="left", padx=(6, 0))
        # 更新检查相关的入口放这里 —— 用户在这个页面看着"哪些在用"，
        # 顺手就能查这些有没有新版本（原来在更新检查页，已按用户要求搬过来）
        self.btn_check_dep = ttk.Button(
            row, text="检查这些有没有更新",
            command=self._check_in_use_updates)
        self.btn_check_dep.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_check_dep,
                "只查「Arsenal 里正在用的」那些模组有没有新版本。\n"
                "它们正装在游戏里，优先更新最有效率。")

        opt = ttk.Frame(self, padding=(14, 6, 14, 0))
        opt.pack(fill="x")
        # ★ 这两项原来用勾选框（Checkbutton）—— 深色主题下"打叉"到底是开还是关
        #   很容易误读（用户就此提过意见，项目里也犯过一次同样的错）。
        #   改用明确的双态文字按钮：字面写清"开 / 关"，不靠符号让人猜。
        self.btn_sync = ttk.Button(opt, command=self._toggle_sync)
        self.btn_sync.pack(side="left")
        Tooltip(self.btn_sync,
                "部署完顺手更新 Arsenal 库里的「已部署」标记，让它的界面与实际一致。\n"
                "Arsenal 运行时不会写（它退出时会覆盖，反而写坏）。")
        self.btn_autosave = ttk.Button(opt, command=self._toggle_autosave)
        self.btn_autosave.pack(side="left", padx=(10, 0))
        Tooltip(self.btn_autosave,
                "拖动行或按上移/下移改优先级后，是否立刻把新顺序写回 Arsenal。\n"
                "关掉的话只在本工具内预览，不改 Arsenal 的排列。")
        self._sync_toggle_buttons()

        ttk.Label(self, textvariable=self.var_info, style="Dim.TLabel",
                  wraplength=640, justify="left").pack(anchor="w", padx=14, pady=(6, 0))
        ttk.Label(self, textvariable=self.var_note, style="Dim.TLabel",
                  wraplength=640, justify="left").pack(anchor="w", padx=14)

        # 列表放在这些之后 pack —— 它 expand，占满剩下的空间（见上面说明）
        wrap.pack(fill="both", expand=True)

        bar = ttk.Frame(self, padding=(14, 10))
        bar.pack(fill="x", side="bottom")
        self.btn_go = ttk.Button(bar, text="部署", style="Accent.TButton",
                                 command=self._do_deploy)
        self.btn_go.pack(side="right")

    def _sync_toggle_buttons(self) -> None:
        """把两个开关按钮的文字/颜色同步成当前状态（不靠符号，直接写"开/关"）。"""
        on1 = bool(self.var_sync.get())
        self.btn_sync.configure(
            text=f"部署后同步回 Arsenal 的显示：{'开' if on1 else '关'}",
            style="Accent.TButton" if on1 else "TButton")
        on2 = bool(self.var_autosave_order.get())
        self.btn_autosave.configure(
            text=f"改顺序后立刻写回 Arsenal：{'开' if on2 else '关'}",
            style="Accent.TButton" if on2 else "TButton")

    def _toggle_sync(self) -> None:
        self.var_sync.set(not self.var_sync.get())
        self._sync_toggle_buttons()

    def _toggle_autosave(self) -> None:
        self.var_autosave_order.set(not self.var_autosave_order.get())
        self._sync_toggle_buttons()

    # ---------- 数据 ----------
    def reload(self) -> None:
        """重新读 Arsenal 库并重建勾选状态（镜像接管在这里发生）。"""
        data_file, _note = find_data_file()
        if not data_file:
            self.var_note.set("读不到 HD2 Arsenal 的模组库。请先在「更新检查」页"
                              "点「重新探测」，或到「设置…」里指定位置。")
            self.btn_go.state(["disabled"])
            return
        self.data_file = data_file
        try:
            self.entries = load_library(data_file)
        except Exception as e:  # noqa: BLE001
            self.var_note.set(f"读取 Arsenal 模组库失败：{e}")
            self.btn_go.state(["disabled"])
            return
        self.game_dir = next(
            (e.get("game_dir") for e in self.entries if e.get("game_dir")), "")
        # 模组安装目录：用 Arsenal 数据文件里的 userModsDir（权威来源），
        # 拖入的压缩包就解到这里，与 Arsenal 自己的行为一致
        try:
            from . import arsenal_sync as _as
            self.mods_dir = _as.mods_dir_of(_as._load(data_file), data_file)
        except Exception:  # noqa: BLE001
            self.mods_dir = os.path.join(os.path.dirname(data_file), "mods")
        try:
            self.first_mod_priority = first_mod_priority(data_file)
        except Exception:  # noqa: BLE001
            self.first_mod_priority = False

        order_tip = ("列表越靠后优先级越高（后加载的会覆盖先加载的）"
                     if not self.first_mod_priority else
                     "列表越靠前优先级越高（Arsenal 开了「第一个模组优先」）")
        self.var_order.set(
            f"加载顺序按 Arsenal 里的排列：{order_tip}。勾选顺序不影响优先级。")
        self.var_game.set(f"游戏目录：{self.game_dir or '（未知）'}")
        if not self.game_dir:
            self.var_game.set("⚠ 模组库里没有游戏目录信息，无法部署。")

        # 镜像接管：Arsenal 已部署的，这里默认也勾上
        try:
            from . import arsenal_sync as _as
            self._mirror = set(_as.deployed_uuids(data_file))
        except Exception:  # noqa: BLE001
            self._mirror = set()

        # ★ 先记住用户当前的勾选，再重建 vars。
        #   顺序很重要：`self.vars = {}` 会把它清空，晚一步读就什么都留不下 ——
        #   那用户刚勾的东西会被默认值抹掉（"刚启用但还没部署"的模组不在
        #   deployed 里，按默认就是未勾选，表现像"我勾了它又自己取消了"）。
        _keep = {u: v.get() for u, v in (self.vars or {}).items()}
        self.vars = {}
        visible = self._visible()
        use_mirror = bool(self._mirror)
        # 每个模组会写入几个槽位 —— 列表里直接显示，部署前就有数
        self._slot_cache = {}
        for e in visible:
            try:
                self._slot_cache[e["uuid"]] = collect_slots(e["path"], e)
            except Exception:  # noqa: BLE001
                self._slot_cache[e["uuid"]] = []
        vis_uuids = {e["uuid"] for e in visible}
        mirror_hit = self._mirror & vis_uuids
        # 默认勾选 = Arsenal 当前已部署的（镜像接管，避免把在用的模组顶掉）。
        # Arsenal 一个都没部署（或读不到）时退回"配置档里已启用的"。
        # **未启用 / 未加入配置档的一律默认不勾** —— 不动用户还没在用的东西。
        #
        # ★ 每个模组都算一遍 `default` 并记进 `_default_checked`（"恢复默认"
        #   按钮要用它），**不受当前勾选影响**；实际勾选值再让用户当前的勾选
        #   覆盖（`_keep`）—— 早先写成"在 _keep 里就 continue"，把
        #   `_default_checked` 也一起跳过了，导致重读一次库之后「恢复默认」就
        #   什么都不恢复。
        self._default_checked = set()
        for e in visible:
            u = e["uuid"]
            if not e.get("in_profile") or not e.get("enabled"):
                default = False
            else:
                default = (u in mirror_hit) if use_mirror else True
            if default:
                self._default_checked.add(u)
            self.vars[u] = tk.BooleanVar(value=_keep.get(u, default))
        n_enable = sum(1 for e in visible if e.get("in_profile") and e.get("enabled"))
        n_disabled = sum(1 for e in visible
                         if e.get("in_profile") and not e.get("enabled"))
        n_outside = sum(1 for e in visible if not e.get("in_profile"))
        n_mirror = len(mirror_hit)
        self.btn_default.configure(text=f"恢复默认（已启用 {n_enable} 个）")
        msg = ""
        if n_mirror:
            msg = (f"已自动勾选 {n_mirror} 个「Arsenal 里正在用的」模组 —— "
                   "这样只改你要动的部分，不会把别的从游戏里顶掉。")
        elif self._mirror:
            msg = "Arsenal 里标记为已部署的模组当前都不在启用状态。"
        else:
            msg = "读不到 Arsenal 的部署记录，已按「已启用的」默认勾选。"
        if n_disabled or n_outside:
            parts = []
            if n_disabled:
                parts.append(f"{n_disabled} 个「Arsenal 里未启用」")
            if n_outside:
                parts.append(f"{n_outside} 个「未加入当前配置档」")
            msg += ("\n列表里还包含 " + "、".join(parts)
                    + " 的模组（灰显）—— 勾选它们会自动放行。")
        self.var_mirror.set(msg)
        self._render()
        self._refresh_summary()

    def _visible(self) -> list[dict]:
        """列表内容：**整个模组库**（不只是配置档里的）。

        排序分三档，把"越难发现的"往后放，默认视图仍然从常用的开始：
          ① 配置档里已启用的（按 Arsenal 加载位次）
          ② 配置档里未启用的
          ③ **未加入当前配置档的**（用户报过：这类模组原来在哪个页面都管不了）
        """
        def rank(e: dict) -> tuple:
            if e.get("in_profile") and e.get("enabled"):
                return (0, e.get("deploy_order") or 0, "")
            if e.get("in_profile"):
                return (1, e.get("deploy_order") or 0, "")
            return (2, 0, (e.get("label") or "").lower())

        return sorted(self.entries,
                      key=lambda x: (rank(x)[0], rank(x)[1], rank(x)[2]))

    def _row_values(self, e: dict) -> tuple:
        """一行的显示值。

        ★ 三件事要分清（用户明确要求）：
        · **启用/停用** = 这一行的开关（勾上=要它生效），会立刻写回 Arsenal；
        · **已部署** = 真正把文件放进游戏了（只有点「部署」才会发生）；
        · **优先级** = 那个「#」列。
        所以状态列必须把"已启用但还没部署"和"已部署（游戏里生效中）"分开写，
        不能都笼统说成"部署"。
        """
        on = self.vars[e["uuid"]].get()
        deployed = e["uuid"] in self._mirror
        enabled = bool(e.get("enabled"))
        # ★ 第四态：内容变过、但还没重新部署。
        #   导入更新（或改了选项）会把 Arsenal 的 `changed` 置 True，而游戏目录里
        #   还是**旧内容** —— 这时若仍显示"已部署（游戏里生效中）"，用户会以为
        #   已经好了（他就是这么问的："导入并更新后还需要重新部署吗？"）。
        #   部署成功后 Arsenal 会把 changed 清掉，这一态自动消失。
        stale = bool(e.get("deploy_changed")) and deployed
        if not e.get("in_profile"):
            mark = "未加入配置档 —— 勾选会加入并启用"
        elif not enabled:
            mark = "Arsenal 里未启用 —— 勾选会启用"
        elif stale:
            mark = "内容已更新 —— 需要重新部署"
        elif deployed and on:
            mark = "已部署（游戏里生效中）"
        elif deployed:
            mark = "已停用（游戏里仍有旧文件）"
        elif on:
            mark = "已启用，还没部署"
        else:
            mark = "未启用"
        n_slots = len(self._slot_cache.get(e["uuid"], []))
        order = e.get("deploy_order")
        return ("● 启用" if on else "○ 未启用",
                order if order is not None else "—",
                e.get("label") or e.get("uuid"),
                n_slots,
                mark)

    def _render(self) -> None:
        """按当前勾选状态重画整张表（行数少，整体重画最简单也最不易出错）。"""
        sel = self.tree.selection()
        keep = sel[0] if sel else None
        self.tree.delete(*self.tree.get_children())
        for e in self._visible():
            on = self.vars[e["uuid"]].get()
            in_use = e["uuid"] in self._mirror
            if not e.get("in_profile") or not e.get("enabled"):
                # 未加入配置档 / 未启用的行整体压暗，一眼能分辨
                tag = "off"
            elif e.get("deploy_changed") and in_use:
                # 内容更新过、但还没重新部署 —— 用"待处理"的橙色提醒
                tag = "stale"
            elif on and in_use:
                tag = "inuse"
            else:
                tag = "on" if on else "off"
            self.tree.insert("", "end", iid=e["uuid"],
                             values=self._row_values(e), tags=(tag,))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)

    def _row_by_index(self, i: int) -> str | None:
        kids = self.tree.get_children()
        return kids[i] if 0 <= i < len(kids) else None

    def _toggle_uuid(self, uuid: str) -> None:
        """勾选/取消勾选一行 = **启用 / 停用这个模组**（立即写回 Arsenal）。

        ★ 为什么是"立即生效"而不是"等部署时再算"：
        原来勾选只改内存里的勾，而"未加入配置档"的模组**不在配置档里 → 没有
        `deploy_order`**，于是排序逻辑会把它过滤掉（`_apply_order` 只重排配置档内的），
        `_render()` 又把它排回末尾 —— 用户看到的就是**"启用以后拖不动"**（拖了弹回）。
        实测复现过：勾选后 `deploy_order` 仍是 None，nudge/拖动都无效。
        现在勾选即写库（未加入的就先加入配置档并启用，已有但关着的就启用），
        它立刻变成一条正常条目 —— 能排序、能拖动、和别的模组一视同仁。
        写失败（Arsenal 开着 / 库异常）就**把勾回滚**并说明原因，绝不假装成功。
        """
        if uuid not in self.vars:
            return
        v = self.vars[uuid]
        want = not v.get()
        e = next((x for x in self.entries if x.get("uuid") == uuid), None)
        if e is None:
            return
        if not self.data_file:
            self.var_note.set("读不到 Arsenal 模组库，无法启用/停用。")
            return

        try:
            if want:
                if not e.get("in_profile"):
                    # 未加入配置档 → 加进去并启用（追加到末尾 = 默认优先级最高）
                    from . import arsenal_sync as _as
                    res = _as.add_to_profile(self.data_file, uuid, enabled=True)
                    self.var_note.set(
                        ("已把「" + (e.get("label") or uuid) + "」加入配置档并启用"
                         "（排在末尾）。现在可以拖动它调整优先级。")
                        if res.get("added") else
                        "它已经在配置档里了，已启用。")
                elif not e.get("enabled"):
                    from . import arsenal_sync as _as
                    _as.set_enabled(self.data_file, uuid, True)
                    self.var_note.set(f"已启用「{e.get('label') or uuid}」。"
                                      "点「部署」才会真正放进游戏。")
                else:
                    self.var_note.set(
                        f"「{e.get('label') or uuid}」本来就是启用状态。")
            else:
                if e.get("in_profile") and e.get("enabled"):
                    from . import arsenal_sync as _as
                    _as.set_enabled(self.data_file, uuid, False)
                    self.var_note.set(
                        f"已停用「{e.get('label') or uuid}」。"
                        + ("它现在还部署在游戏里 —— 重新部署一次才会清理掉。"
                           if e.get("deployed") else ""))
        except SyncError as ex:
            self.var_note.set(f"没能改动 Arsenal：{ex}")
            return
        except Exception as ex:  # noqa: BLE001
            self.var_note.set(f"改动 Arsenal 时出错：{ex}")
            return

        # 写成功才更新勾选状态并重读（顺序/状态都变了，重新读最踏实）
        v.set(want)
        self.reload()

    def _toggle_selected(self) -> None:
        """切换当前选中行（对应「启用 / 停用选中项」按钮）。"""
        sel = self.tree.selection()
        if not sel:
            # 没有任何选中时，明确提示而不是静默无反应
            self.var_note.set("先在列表里点一行，再点这个按钮。")
            return
        self._toggle_uuid(sel[0])

    def _on_tree_click(self, event) -> None:
        """点「启用」列 = 直接切换该行；点其它列只做选中。"""
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row:
            return
        # #1 = 第一列（state），点它就切换
        if col == "#1":
            self._toggle_uuid(row)
            return "break"

    # ---------- 拖动排序（改加载优先级）----------
    def _drag_start(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        self._drag_iid = iid or None
        if not iid:
            return
        # 记下拖动前的顺序，松手时若没变化就不写库
        self._drag_origin = list(self.tree.get_children())
        # "拎起来"的视觉：给被拖的行换一个明显的底色
        self.tree.item(iid, tags=("dragging",))

    def _drag_move(self, event) -> None:
        """拖动中：**实时把行挪到光标所在位置** —— 这样手指到哪、行就到哪。

        早期版本只改选中项、松手才重排，用户看到的是"拖的时候毫无反应、
        一松手生硬跳一下"。ttk 没有真正的"悬浮行"，所以用实时移动 + 高亮
        底色来做出同样的手感。
        """
        src = getattr(self, "_drag_iid", None)
        if not src or not self.tree.exists(src):
            return
        target = self.tree.identify_row(event.y)
        if not target or target == src:
            return
        kids = list(self.tree.get_children())
        if src not in kids or target not in kids:
            return
        ti = kids.index(target)
        # tree.move 是原生操作，比删掉重插轻得多，拖动时才跟得上手
        self.tree.move(src, "", ti)
        self.tree.item(src, tags=("dragging",))
        self.tree.see(src)

    def _drag_drop(self, event) -> None:
        src = getattr(self, "_drag_iid", None)
        origin = getattr(self, "_drag_origin", None)
        self._drag_iid = None
        self._drag_origin = None
        if not src:
            return
        order = list(self.tree.get_children())
        # 恢复该行的正常配色
        if self.tree.exists(src):
            e = next((x for x in self._visible() if x["uuid"] == src), None)
            if e:
                on = self.vars[e["uuid"]].get()
                in_use = e["uuid"] in self._mirror
                tag = "off" if not e.get("enabled") else (
                    "inuse" if (on and in_use) else ("on" if on else "off"))
                self.tree.item(src, values=self._row_values(e), tags=(tag,))
            self.tree.selection_set(src)
        if order == origin:
            return          # 没动过，不写库
        self._apply_order(order, note=f"已调整顺序：{self._label_of(src)}")

    def _nudge(self, delta: int) -> None:
        """把选中行上移/下移一格（键盘/按钮用，比拖动更精确）。"""
        sel = self.tree.selection()
        if not sel:
            self.var_note.set("先在列表里点一行，再用上移/下移。")
            return
        kids = list(self.tree.get_children())
        i = kids.index(sel[0])
        j = i + delta
        if j < 0 or j >= len(kids):
            return
        kids[i], kids[j] = kids[j], kids[i]
        self._apply_order(kids, note=f"已调整顺序：{self._label_of(sel[0])}")

    def _label_of(self, uuid: str) -> str:
        e = next((x for x in self._visible() if x["uuid"] == uuid), None)
        return (e.get("label") if e else uuid) or uuid

    def _apply_order(self, ordered_uuids: list[str], note: str = "") -> None:
        """把新顺序写回 Arsenal 并重画列表（失败不静默）。

        编号规则与 :func:`arsenal_sync.set_order` 保持一致：
        **只在被提到模组原本占据的槽位之间置换**，没提到的模组原地不动。

        两处要注意：
        · 必须产出**完整且唯一**的编号 —— 早期只给被提到的改号、其余留旧号，
          会出现两个模组撞成同一个号（实测踩过：p1 和 dis 都是 2），build_plan 会拒绝部署。
        · 早期把"没提到的模组排到最前面"，等于**擅自改了它们的优先级** ——
          实测导致用户库里两个未启用模组被挪到最前（靠备份才发现）。现已改掉。
        """
        if not self.data_file:
            return
        inprof = [e for e in self.entries if e.get("in_profile")]
        known = {e["uuid"] for e in inprof}
        want = [u for u in ordered_uuids if u in known]
        if not want:
            return
        # 按当前位置取"槽位下标"，把新顺序填进去（与 set_order 同一套规则）
        cur = sorted(inprof, key=lambda x: (x.get("deploy_order") is None,
                                            x.get("deploy_order") or 0))
        slots = sorted(cur.index(e) for e in cur if e["uuid"] in set(want))
        newseq = list(cur)
        order_of = {e["uuid"]: i for i, e in enumerate(cur)}
        for slot, uuid in zip(slots, want):
            newseq[slot] = cur[order_of[uuid]]
        # 重编号 —— 保证 0..n-1 无重复
        for i, e in enumerate(newseq):
            e["deploy_order"] = i
        self._render()
        self._refresh_summary()
        if self.var_autosave_order.get():
            try:
                from . import arsenal_sync as _as
                res = _as.set_order(self.data_file, want)
                self.var_note.set(
                    f"{note}（已写回 Arsenal，备份 {os.path.basename(res['backup'])}）")
            except SyncError as e:
                self.var_note.set(f"{note}；但没能写回 Arsenal：{e}")
            except Exception as e:  # noqa: BLE001
                self.var_note.set(f"{note}；但写回 Arsenal 时出错：{e}")
        else:
            self.var_note.set(f"{note}（仅本地预览，未写回 Arsenal）")

    # ---------- 选项编辑 ----------
    def _edit_options(self) -> None:
        """打开选中模组的选项编辑对话框（改动写回 Arsenal）。"""
        sel = self.tree.selection()
        if not sel:
            self.var_note.set("先在列表里点一行，再用「选项…」。")
            return
        uuid = sel[0]
        entry = next((e for e in self._visible() if e["uuid"] == uuid), None)
        if entry is None:
            return
        if not self.data_file:
            return
        try:
            opts = read_options(self.data_file, uuid)
        except SyncError as e:
            error_box("读选项失败", str(e), parent=self.winfo_toplevel(),
                      app=self.app, context="打开模组选项对话框时读取选项失败")
            return
        if not opts:
            messagebox.showinfo(
                "没有可选项",
                f"「{entry.get('label')}」没有可单独开关的选项。",
                parent=self.winfo_toplevel())
            return
        dlg = OptionsDialog(self.winfo_toplevel(), entry, opts, self.data_file,
                            on_saved=self._after_options_saved)
        self._options_dialog = dlg

    def _after_options_saved(self, n_changed: int) -> None:
        """选项写回后：重读库（槽位数/部署计划会变），并提示需要重新部署。"""
        self.reload()
        self.var_note.set(
            f"已更新 {n_changed} 个选项并写回 Arsenal。"
            "选项变了，建议重新部署一次让它生效。")

    def _set_all(self, v: bool) -> None:
        for e in self._visible():
            self.vars[e["uuid"]].set(v)
        self._render()
        self._refresh_summary()

    # ---------- 右键菜单 / 删除 ----------
    def _on_deploy_right_click(self, ev) -> None:
        """右键选中一行并弹出菜单（原来这页没有右键菜单）。"""
        iid = self.tree.identify_row(ev.y)
        if iid:
            self.tree.selection_set(iid)
            try:
                self.menu.tk_popup(ev.x_root, ev.y_root)
            finally:
                self.menu.grab_release()

    def _delete_selected_mod(self) -> None:
        """删除选中的模组。

        删除走 :meth:`App.delete_mod` 这**唯一一条**路径（两个页面共用），
        确认框里会写清"从配置档移除 / 游戏里现有文件还在不在"，
        以及让用户当场决定要不要连磁盘文件一起删。
        """
        sel = self.tree.selection()
        if not sel:
            self.var_note.set("先在列表里点一行，再删除。")
            return
        uuid = sel[0]
        entry = next((e for e in self.entries if e.get("uuid") == uuid), None)
        label = (entry or {}).get("label") or uuid
        app = self.app if self.app is not None else self.winfo_toplevel()
        if not hasattr(app, "delete_mod"):
            return
        if app.delete_mod(uuid, label, parent_widget=self.winfo_toplevel()):
            self.var_note.set(f"已删除「{label}」。")
            self.reload()

    def _set_default(self) -> None:
        """恢复成镜像接管的默认集合（不是无脑全选）。"""
        default = getattr(self, "_default_checked", set())
        for e in self._visible():
            self.vars[e["uuid"]].set(e["uuid"] in default)
        self._render()
        self._refresh_summary()

    def _checked(self) -> list[str]:
        return [u for u, v in self.vars.items() if v.get()]

    def _check_in_use_updates(self) -> None:
        """查「Arsenal 里正在用的」模组有没有新版本（切回更新检查页看结果）。"""
        keys = {source_key(e) for e in self.entries
                if e.get("uuid") in self._mirror} if self.app is not None else set()
        if self.app is None or not hasattr(self.app, "_check_deployed_keys"):
            return
        if not keys:
            messagebox.showinfo(
                "没有正在用的模组",
                "Arsenal 当前没有标记为已部署的模组，没有可查的。", parent=self)
            return
        # 结果在「更新检查」页展示，所以切过去再发起检查
        self.app._show_page("update")
        self.app._check_deployed_keys(keys)

    def _pending_enable(self, sel: set[str]) -> set[str]:
        """勾选中「在配置档里但没启用」的模组 —— 部署时会顺带启用它们。"""
        return {u for u in sel
                if (lambda e: bool(e) and e.get("in_profile")
                    and not e.get("enabled"))(
                    next((e for e in self.entries if e.get("uuid") == u), None))}

    def _pending_add(self, sel: set[str]) -> list[str]:
        """勾选中「还没加入当前配置档」的模组 —— 部署时会先把它们加进配置档。

        顺序按它们在列表里的显示顺序，这样"追加到末尾"的结果与用户看到的顺序一致。
        """
        out = []
        for e in self._visible():
            if e["uuid"] in sel and not e.get("in_profile"):
                out.append(e["uuid"])
        return out

    def _plan_library(self, sel: set[str]) -> list[dict]:
        """给 build_plan 用的库副本：把"勾了但还不能部署"的模组按"即将可用"处理。

        build_plan 会拒绝未启用 / 不在配置档的模组（那是安全设计），但用户在部署页
        勾选它们的意图很明确 = 要它们生效。所以这里：
        · 未启用的 → 视为已启用
        · 未加入配置档的 → 视为已加入，并**按其将被追加到的位置**给出顺序号
          （追加到末尾 = 现有最大顺序 + 1 起，与 Arsenal 默认下"越靠后越优先"一致）
        真正写回 enabled / 加入配置档的动作放在部署时做。
        """
        pend_e = self._pending_enable(sel)
        pend_a = self._pending_add(sel)
        if not pend_e and not pend_a:
            return self.entries
        max_order = max((e.get("deploy_order") or 0)
                        for e in self.entries if e.get("in_profile")) \
            if any(e.get("in_profile") for e in self.entries) else 0
        add_order = {u: max_order + 1 + i for i, u in enumerate(pend_a)}
        out = []
        for e in self.entries:
            u = e["uuid"]
            if u in pend_e or u in pend_a:
                e = dict(e)
                e["enabled"] = True
                if u in pend_a:
                    e["in_profile"] = True
                    e["deploy_order"] = add_order[u]
            out.append(e)
        return out

    # ---------- 拖入 / 选择 mod 压缩包 ----------
    def _on_drop_files(self, event):
        """拖放回调：把拖进来的文件交给导入流程。"""
        paths = split_drop_paths(getattr(event, "data", "") or "")
        if paths:
            self.import_files(paths)

    def _pick_mod_files(self) -> None:
        """退路入口：不支持拖放时也能用（点按钮选文件）。"""
        files = filedialog.askopenfilenames(
            parent=self.winfo_toplevel(), title="选择 mod 压缩包",
            filetypes=[("mod 压缩包", "*.zip"), ("所有文件", "*.*")])
        if files:
            self.import_files(list(files))

    def import_files(self, paths: list[str]) -> None:
        """把压缩包加入 Arsenal 的模组库。

        顺序上刻意"先只读检查、再问、最后才写"：
        坏包一开始就说清原因，不会写到一半才发现；会覆盖别人的先征求同意。
        """
        top = self.winfo_toplevel()
        if not self.data_file:
            error_box(
                "找不到 Arsenal 模组库",
                "没有读到 Arsenal 的数据文件，无法加入模组。\n"
                "请先在「更新检查」页确认 Arsenal 位置。", parent=top,
                app=self.app, context="导入模组时找不到 Arsenal 库")
            self.var_note.set("没有读到 Arsenal 模组库，无法加入模组。")
            return
        if arsenal_running():
            messagebox.showwarning("HD2 Arsenal 正在运行", running_warning(),
                                   parent=top)
            # 状态栏也要说清，不然用户只看底部会以为"什么都没发生"
            self.var_note.set("没导入：HD2 Arsenal 正在运行，请先完全关闭它"
                              "（含右下角托盘图标）。")
            return

        try:
            known = set(library_uuids(self.data_file))
        except Exception:  # noqa: BLE001
            known = set()

        good, dupes, bad = [], [], []
        for p in paths:
            try:
                info = inspect_zip(p)
            except ModImportError as e:
                bad.append((p, str(e)))
                continue
            if info["uuid"] in known:
                dupes.append((p, info["label"]))
            else:
                good.append((p, info["label"]))

        if not good and not dupes:
            detail = "\n\n".join(f"{os.path.basename(p)}：\n  {why}"
                                 for p, why in bad[:4])
            error_box("这些压缩包不能用", detail or "没有可导入的文件。",
                      parent=top, app=self.app,
                      context="拖入的压缩包都无法解析")
            self.var_note.set("拖入的文件不是可用的 mod 压缩包。")
            return

        if dupes:
            names = "\n".join(f"· {lb}" for _p, lb in dupes[:6])
            more = f"\n…还有 {len(dupes) - 6} 个" if len(dupes) > 6 else ""
            if not messagebox.askyesno(
                "这些已经在列表里了",
                f"下面这些 mod 已经在模组列表里，继续的话会用压缩包里的内容"
                f"覆盖原来的内容（旧内容会留一份备份文件夹）：\n\n{names}{more}"
                "\n\n要继续吗？",
                parent=top,
            ):
                self.var_note.set("已取消导入。")
                return

        todo = [p for p, _ in good] + [p for p, _ in dupes]
        self.var_note.set(f"正在导入 {len(todo)} 个压缩包…")
        self.update_idletasks()
        # 带上 N 网 key：能从压缩包名认出 N 网编号时会顺带把 fileId 补齐
        try:
            _key = read_config().get("nexus_api_key") or ""
        except Exception:  # noqa: BLE001
            _key = ""
        res = import_paths(self.data_file, todo, mods_dir=self.mods_dir,
                           api_key=_key)

        done, failed = res["done"], list(res["failed"])
        for p, why in bad:
            failed.append((p, why))

        self.reload()          # 列表要立刻反映出来

        lines = []
        if done:
            lines.append(f"已加入 {len(done)} 个 mod：")
            for _p, r in done[:8]:
                act = "新增" if r.get("added") else "覆盖"
                extra = ""
                nx = r.get("nexus") or {}
                if nx.get("mod_id"):
                    if (r.get("nexus_data") or {}).get("fileId"):
                        extra = (f"（认出是 N 网 mod #{nx['mod_id']}，"
                                 "fileId 已补齐）")
                    else:
                        why = r.get("nexus_why") or "没查到对应文件"
                        extra = (f"（压缩包名里有 N 网编号 #{nx['mod_id']}，"
                                 f"但没补上 fileId：{why}；"
                                 "来源识别会用目录名里那串编号，不受影响）")
                elif r.get("profile_updated"):
                    extra = "（它在配置档里，已同步，标记为需要重新部署）"
                lines.append(f"  · {r['label']} —— {act}{extra}")
            if len(done) > 8:
                lines.append(f"  …还有 {len(done) - 8} 个")
            lines.append("")
            lines.append("它们现在只在「模组列表」里，还没启用 —— "
                         "想用哪个就在上面点「启用」，然后点「部署」。")
        if failed:
            lines.append("")
            lines.append(f"有 {len(failed)} 个没能导入：")
            for p, why in failed[:5]:
                lines.append(f"  · {os.path.basename(p)}：{why.splitlines()[0]}")
        text = "\n".join(lines) or "没有变动。"
        self.var_note.set(text.splitlines()[0] if lines else "没有变动。")
        if done:
            diag.event("导入模组", f"完成：成功 {len(done)} 个"
                                  + (f"，失败 {len(failed)} 个" if failed else ""))
        else:
            diag.event("导入模组", f"失败：{text.splitlines()[0] if lines else '未知原因'}")
        if done:
            messagebox.showinfo("导入完成", text, parent=top)
        else:
            error_box("导入失败", text, parent=top, app=self.app,
                      context="把下载的模组导入 Arsenal 时失败")

    # ---------- 计划 ----------
    def _refresh_summary(self) -> None:
        sel = self._checked()
        if not sel:
            self.plan = None
            self.var_info.set("还没有勾选任何模组。")
            self.btn_go.state(["disabled"])
            return
        if not self.game_dir:
            self.plan = None
            self.var_info.set("缺少游戏目录信息，无法生成部署计划。")
            self.btn_go.state(["disabled"])
            return
        try:
            self.plan = build_plan(self._plan_library(set(sel)), set(sel),
                                   self.game_dir,
                                   first_mod_priority=self.first_mod_priority)
        except DeployError as e:
            self.plan = None
            self.var_info.set(str(e))
            self.btn_go.state(["disabled"])
            return
        except Exception as e:  # noqa: BLE001
            self.plan = None
            self.var_info.set(f"生成部署计划时出错：{e}")
            self.btn_go.state(["disabled"])
            return
        n_mods = len({i["mod_uuid"] for i in self.plan.items})
        will_enable = self._pending_enable(set(sel))
        will_add = self._pending_add(set(sel))
        extra = ""
        if will_add:
            names = [next((e.get("label") or u for e in self.entries
                           if e.get("uuid") == u), u) for u in will_add]
            extra += ("\n其中 " + str(len(will_add)) + " 个还没加入当前配置档，"
                      "部署时会先把它们加入（追加到末尾，即优先级最高）：\n　"
                      + "、".join(names[:4]) + ("…" if len(names) > 4 else ""))
        if will_enable:
            names = [next((e.get("label") or u for e in self.entries
                           if e.get("uuid") == u), u) for u in will_enable]
            extra += ("\n其中 " + str(len(will_enable)) + " 个在 Arsenal 里未启用，"
                      "部署时会顺带启用：\n　"
                      + "、".join(names[:4]) + ("…" if len(names) > 4 else ""))
        # 内容更新过、还没重新部署的（导入过更新或改过选项）—— 明确点出来，
        # 否则用户会以为"导入完就好了"，而游戏里其实还是旧内容
        stale = [e.get("label") or e.get("uuid") for e in self.entries
                 if e.get("deploy_changed") and e.get("uuid") in set(sel)
                 and e["uuid"] in self._mirror]
        if stale:
            extra += ("\n其中 " + str(len(stale)) + " 个内容更新过、还没重新部署"
                      "（现在部署才会换成新内容）：\n　"
                      + "、".join(stale[:4]) + ("…" if len(stale) > 4 else ""))
        self.var_info.set(
            f"将要部署：{n_mods} 个模组、{self.plan.slot_count} 个槽位、"
            f"约 {self.plan.total_bytes / 1048576:.1f} MB。\n"
            "游戏 data 目录里现有的部署文件会先备份，再被替换成这些。" + extra)
        self.btn_go.state(["!disabled"])

    # ---------- 执行 ----------
    def _do_deploy(self) -> None:
        if not self.plan:
            return
        running = arsenal_running()
        warn = ""
        if running:
            warn = ("\n⚠ HD2 Arsenal 正在运行。现在部署的话，如果你之后在 Arsenal 里"
                    "再点一次「部署」，游戏目录会被它重写。\n")
        will_enable = self._pending_enable({i["mod_uuid"] for i in self.plan.items})
        will_add = self._pending_add({i["mod_uuid"] for i in self.plan.items})
        enable_note = ""
        if will_add:
            enable_note += (f"\n其中 {len(will_add)} 个还没加入当前配置档，"
                            "会先把它们加入（追加到末尾）。\n")
        if will_enable:
            enable_note += (f"\n其中 {len(will_enable)} 个在 Arsenal 里是未启用的，"
                            "会先启用它们再部署。\n")
        if not messagebox.askyesno(
            "确认部署",
            f"即将把 {self.plan.summary()} 部署到游戏。\n\n"
            "游戏 data 目录里现有的模组部署文件会先备份，然后被替换。\n"
            f"{enable_note}\n"
            "重要：本工具和 Arsenal 不能同时部署 —— 两边写同一批文件，"
            "谁后部署谁生效。以后请固定用其中一个。\n"
            f"{warn}\n确定继续吗？",
            parent=self.winfo_toplevel(),
        ):
            return

        self.btn_go.state(["disabled"])
        self.var_info.set("正在部署…")
        self.update_idletasks()

        # 先把"勾了但 Arsenal 里未启用 / 还没加入配置档"的处理好 ——
        # 否则它们不会被真正加载（配置档里没有 = 没有加载顺序）
        added_ok = 0
        enabled_ok = 0
        enable_err = ""
        if will_add or will_enable:
            try:
                from . import arsenal_sync as _as
                for u in will_add:
                    if _as.add_to_profile(self.data_file, u, enabled=True).get("added"):
                        added_ok += 1
                for u in will_enable:
                    if _as.set_enabled(self.data_file, u, True).get("changed"):
                        enabled_ok += 1
            except SyncError as e:
                enable_err = str(e)
            except Exception as e:  # noqa: BLE001
                enable_err = f"{e}"

        try:
            res = deploy_plan(self.plan, do_backup=True)
        except DeployError as e:
            error_box("部署失败", str(e), parent=self.winfo_toplevel(),
                      app=self.app, context="部署到游戏目录时被拒绝")
            self._refresh_summary()
            return
        except Exception as e:  # noqa: BLE001
            # 以前这里只 traceback.print_exc() —— 打包成 EXE 后没有控制台，
            # 等于什么都没留下。改成同时写日志（诊断包里就能看到）。
            diag.log("部署时发生意外错误：\n" + traceback.format_exc(), "ERROR")
            error_box("部署失败", f"部署时发生意外错误：\n\n{e}",
                      parent=self.winfo_toplevel(), app=self.app,
                      context="部署时发生未预期的错误")
            self._refresh_summary()
            return

        deployed = {i["mod_uuid"] for i in self.plan.items}
        diag.event("部署", f"完成：{len(deployed)} 个模组，写入 {res['written']} 个槽位，"
                          f"清理 {res['removed']} 个旧文件"
                          + (f"，备份 {res['backup']}" if res.get("backup") else ""))
        if enable_err:
            diag.log(f"部署时部分模组没能在 Arsenal 里启用：{enable_err}", "WARN")
        lines = [f"部署完成：写入 {res['written']} 个槽位，"
                 f"清理了 {res['removed']} 个旧文件。",
                 f"备份位置：\n{res['backup'] or '（本次没有旧文件需要备份）'}"]
        if added_ok:
            lines.append(f"已把 {added_ok} 个模组加入 Arsenal 当前配置档（排在末尾）。")
        if enabled_ok:
            lines.append(f"已在 Arsenal 里启用 {enabled_ok} 个模组。")
        if enable_err:
            lines.append(f"注意：有模组没能在 Arsenal 里启用（{enable_err}），"
                         "它们可能不会生效。")

        # 写回 Arsenal 的显示状态（可选）
        if self.var_sync.get():
            try:
                from . import arsenal_sync as _as
                files = _as.live_file_entries(self.plan.game_dir, self.plan.prefix)
                sr = _as.sync_deployed_state(self.data_file, deployed,
                                             live_files=files)
                lines.append("")
                lines.append(_as.describe_sync(sr))
            except SyncError as e:
                lines.append("")
                lines.append(f"未同步回 Arsenal：{e}")
            except Exception as e:  # noqa: BLE001
                lines.append("")
                lines.append(f"未同步回 Arsenal（{e}）。游戏本身不受影响。")

        lines.append("")
        lines.append("现在可以启动游戏了。")
        lines.append("提醒：以后请固定用本工具部署，不要再用 Arsenal 部署 —— "
                     "两边写同一批文件，会互相覆盖。")
        messagebox.showinfo("部署完成", "\n".join(lines),
                            parent=self.winfo_toplevel())
        self.reload()
        # ★ 部署是唯一会大量产生备份的动作（每次约 230 MB）→ 收尾时按保留
        #   份数自动清一次，用户不用自己管（可在「备份」页/设置里看到和调整）
        try:
            self.app.auto_prune_backups("部署后")
        except Exception:  # noqa: BLE001
            pass


class DownloadsPanel(ttk.Frame):
    """「下载」页：把"下好了"和"用上了"之间那一环补上。

    为什么单开一页：工具下载完只把 ZIP 收进下载夹，**不碰 Arsenal 的模组库**
    （既定原则"下载自动化、安装始终手动"）。以前用户只能自己去翻出那个 ZIP
    再拖进窗口 —— 用户原话：「**下载了就自动拖到文件夹里但是没有自动更新**」。
    这一页把已下载的东西列出来，让他挑哪个要导入。

    ★ 还要解决一处更隐蔽的混乱：ZIP 导入后**文件还在原地**，所以只按
    "文件在不在"判断，那行会永远挂着「已下载」——用户以为刚才那一下没成功。
    所以这里用**三态**：待导入 / 已导入 / 文件已不在（见 records.state）。

    ★ 导入后**不自动部署**（用户拍板）：只更新模组列表，真正进游戏仍由他在
    部署页点。所以导入完给一个「去部署页」的按钮，而不是替他做决定。
    """
    COLS = (("state", "状态", 96), ("label", "模组", 300),
            ("dlver", "下载的版本", 110), ("nowver", "现在装的版本", 120),
            ("at", "下载时间", 130), ("prov", "来源", 80))

    def __init__(self, parent: tk.Widget, app: "App") -> None:
        super().__init__(parent)
        self.app = app
        self.recs: list[tuple[str, dict]] = []
        self.lib_by_key: dict[str, dict] = {}
        self.data_file = ""

        head = ttk.Frame(self, padding=(12, 10, 12, 4))
        head.pack(fill="x")
        ttk.Label(head, text="已下载的模组", style="Head.TLabel").pack(anchor="w")
        ttk.Label(
            head,
            text="这里列出的是你下载好、但还没装进 Arsenal 的东西。"
                 "选中一条点「导入并更新」即可用它的内容替换原来的模组"
                 "（旧内容会留备份文件夹）。导入完请到「部署模组」页部署一次，"
                 "它才会真正进游戏。",
            style="Dim.TLabel", wraplength=760, justify="left").pack(
            anchor="w", pady=(2, 0))

        bar = ttk.Frame(self, padding=(12, 6))
        bar.pack(fill="x")
        self.btn_import = ttk.Button(bar, text="导入并更新",
                                     command=self._import_selected)
        self.btn_import.pack(side="left")
        Tooltip(self.btn_import,
                "把这个下载的内容导入 Arsenal 模组库。\n"
                "如果列表里已经有同一个模组，就是「更新」它"
                "（旧的留一份备份文件夹）；\n"
                "如果是个新模组，就是「新增」一条。\n"
                "★ 不会自动进游戏 —— 导入后到「部署模组」页部署一次才生效。")
        b_open = ttk.Button(bar, text="打开文件位置",
                            command=self._open_location)
        b_open.pack(side="left", padx=(6, 0))
        Tooltip(b_open, "在资源管理器里定位到这个压缩包。")
        b_zip = ttk.Button(bar, text="打开压缩包", command=self._open_zip)
        b_zip.pack(side="left", padx=(6, 0))
        Tooltip(b_zip, "先看看里面是什么（不确定这个下载要不要用时）。")
        b_hide = ttk.Button(bar, text="不再显示", command=self._forget_selected)
        b_hide.pack(side="left", padx=(6, 0))
        Tooltip(b_hide,
                "只是从这一页移除这条记录，**不删文件、也不动模组**。")
        b_ref = ttk.Button(bar, text="刷新", command=self.reload)
        b_ref.pack(side="left", padx=(6, 0))
        self.btn_go = ttk.Button(bar, text="去部署页 →",
                                 command=lambda: self.app._show_page("deploy"))
        self.btn_go.pack(side="right")

        wrap = ttk.Frame(self, padding=(12, 0, 12, 6))
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            wrap, columns=[c[0] for c in self.COLS], show="headings",
            selectmode="browse")
        for key, title, width in self.COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width,
                             anchor="w" if key in ("label",) else "center")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="left", fill="y")

        # 三态配色：待处理的最显眼，已导入的淡下去，文件丢了用红
        self.tree.tag_configure("pending", foreground=ORANGE)
        self.tree.tag_configure("applied", foreground=GREEN)
        self.tree.tag_configure("missing", foreground=RED)

        self.var_note = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.var_note, style="Dim.TLabel",
                  wraplength=760, justify="left").pack(
            anchor="w", padx=12, pady=(0, 10))

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="导入并更新", command=self._import_selected)
        self.menu.add_separator()
        self.menu.add_command(label="打开文件位置", command=self._open_location)
        self.menu.add_command(label="打开压缩包", command=self._open_zip)
        self.menu.add_separator()
        self.menu.add_command(label="不再显示这条记录",
                              command=self._forget_selected)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", lambda e: self._import_selected())
        self.tree.bind("<Delete>", lambda e: self._forget_selected())
        # ★ 选中行变化 → 立刻更新按钮可用状态。
        #   少了这条绑定，**用鼠标点一行按钮不会变亮**（只有右键那条路才同步），
        #   用户看到的是"下载好了、行也在，但按钮一直是灰的"（实测复现过）。
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_buttons())

    # ------------------------------------------------------------------ 数据
    def reload(self) -> None:
        """重读下载记录 + 模组库，重建列表。

        ★ 自己调 ``find_data_file()``，**不依赖 App 的异步探测**（那是
        `after(120ms)` 才跑的）。部署页也是这么自立的 —— 依赖"App 已经探测完"
        会让这一页在启动早期/测试里拿到空路径，表现成"点了导入什么都没发生"。
        """
        try:
            self.recs = records.all_records()
        except Exception:  # noqa: BLE001
            self.recs = []
        self.data_file = self.app.data_file or ""
        if not self.data_file:
            try:
                self.data_file = find_data_file()[0] or ""
            except Exception:  # noqa: BLE001
                self.data_file = ""
        self.lib_by_key = {}
        if self.data_file:
            try:
                for e in load_library(self.data_file):
                    k = e.get("uuid") or e.get("label") or ""
                    if k:
                        self.lib_by_key[k] = e
            except Exception:  # noqa: BLE001
                self.lib_by_key = {}

        for iid in self.tree.get_children():
            self.tree.delete(iid)
        n_pending = 0
        first_pending = ""
        for key, rec in self.recs:
            st = records.state(key) or "missing"
            ent = self.lib_by_key.get(key)
            now = (ent or {}).get("installed_version") or ""
            if st == "pending":
                n_pending += 1
                mark, tag = "待导入", "pending"
                if not first_pending:
                    first_pending = key
            elif st == "applied":
                mark, tag = "已导入", "applied"
            else:
                mark, tag = "文件已不在", "missing"
            self.tree.insert(
                "", "end", iid=key, tags=(tag,),
                values=(mark,
                        rec.get("label") or (ent or {}).get("label") or key,
                        rec.get("version") or "",
                        now or "—",
                        rec.get("at") or "",
                        PROV_NAME.get(str(rec.get("provider") or ""),
                                      rec.get("provider") or "")))
        if not self.recs:
            self.var_note.set("这里还没有下载记录。到「更新检查」页检查后，"
                              "对有更新的模组点下载即可。")
        else:
            self.var_note.set(
                f"共 {len(self.recs)} 条记录，其中 {n_pending} 条还没导入。"
                + ("双击一行就能导入它。" if n_pending else
                   "（都处理过了 —— 若已导入还没进游戏，去「部署模组」页部署一次。）"))
        # ★ 自动选中第一条「待导入」的：这一页的用途就是"挑一条装上"，
        #   不预选的话进来按钮是灰的，用户得先点一下才知道能按
        #   （用户报过"下载好了…才变到可以按的"）。只在这一条确实存在时预选。
        if first_pending and not self.tree.selection():
            self.tree.selection_set(first_pending)
            self.tree.see(first_pending)
        self._sync_buttons()

    def _selected_key(self) -> str:
        sel = self.tree.selection()
        return sel[0] if sel else ""

    def _rec_of(self, key: str) -> dict:
        return next((r for k, r in self.recs if k == key), {})

    def _sync_buttons(self) -> None:
        key = self._selected_key()
        st = records.state(key) if key else ""
        ok = bool(key) and st in ("pending", "applied")
        self.btn_import.state(["!disabled"] if ok else ["disabled"])

    # ------------------------------------------------------------------ 操作
    def _on_right_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        self._sync_buttons()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _open_location(self) -> None:
        fp = str(self._rec_of(self._selected_key()).get("file") or "")
        if not fp:
            return
        if not os.path.isfile(fp):
            messagebox.showwarning(
                "文件不在了",
                f"这个下载的文件已经不在原处：\n{fp}\n\n"
                "多半是它已经被导入、或你自己移走/删掉了。\n"
                "可以点「不再显示」把这条记录清掉。",
                parent=self.winfo_toplevel())
            return
        # 优先"在资源管理器里选中这个文件"，不行就退回打开它所在目录
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(fp)])
            return
        except Exception:  # noqa: BLE001
            pass
        try:
            os.startfile(os.path.dirname(fp))  # noqa: S606
        except Exception:  # noqa: BLE001
            messagebox.showinfo("文件位置", fp)

    def _open_zip(self) -> None:
        fp = str(self._rec_of(self._selected_key()).get("file") or "")
        if not fp or not os.path.isfile(fp):
            self.var_note.set("这个下载的文件不在了，没法打开。")
            return
        try:
            os.startfile(fp)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            messagebox.showinfo("打开失败", f"{fp}\n\n{e}")

    def _forget_selected(self) -> None:
        key = self._selected_key()
        if not key:
            self.var_note.set("先在列表里点一条记录。")
            return
        records.forget(key)
        self.reload()
        self.var_note.set("已从这一页移除该记录（文件没删、模组也没动）。")

    def _import_selected(self) -> None:
        """把选中的下载导入 Arsenal 模组库（覆盖同 uuid 的旧条目）。"""
        key = self._selected_key()
        if not key:
            self.var_note.set("先在列表里点一条记录，再点「导入并更新」。")
            return
        rec = self._rec_of(key)
        fp = str(rec.get("file") or "")
        top = self.winfo_toplevel()
        if not fp or not os.path.isfile(fp):
            messagebox.showwarning(
                "文件不在了",
                f"这个下载的文件已经不在原处：\n{fp}\n\n"
                "没法导入。可以点「不再显示」清掉这条记录。", parent=top)
            # 状态栏也要说 —— 只看底部的人不该以为"点了没反应"
            self.var_note.set("没导入：这个下载的文件已经不在原处了。")
            return
        if not self.data_file:
            error_box(
                "找不到 Arsenal 模组库",
                "没有读到 Arsenal 的数据文件，无法导入。\n"
                "请先在「更新检查」页确认 Arsenal 位置。", parent=top,
                app=self.app, context="导入已下载的模组时找不到 Arsenal 库")
            self.var_note.set("没导入：读不到 Arsenal 模组库。")
            return
        if arsenal_running():
            messagebox.showwarning("HD2 Arsenal 正在运行", running_warning(),
                                   parent=top)
            self.var_note.set("没导入：HD2 Arsenal 正在运行，请先完全关闭它。")
            return

        # 先只读检查：坏包当场说清，不写一半
        try:
            info = inspect_zip(fp)
        except ModImportError as e:
            error_box("这个压缩包不能用", f"{os.path.basename(fp)}\n\n{e}",
                      parent=top, app=self.app,
                      context=f"导入 {os.path.basename(fp)} 时压缩包解析失败")
            self.var_note.set("这个下载的压缩包不能用，没法导入。")
            return

        label = rec.get("label") or info.get("label") or ""
        # ★ 没有 manifest 的包没有 uuid → 导入是**新增**而不是更新。
        #   不提示的话用户会以为"更新了那个模组"，实际多出一条重复的。
        if not info.get("uuid_from_manifest") or info["uuid"] != key:
            ent = self.lib_by_key.get(key) or {}
            if not messagebox.askyesno(
                "这会新增一条，而不是更新它",
                f"这个压缩包（{os.path.basename(fp)}）里没有能对上「"
                f"{ent.get('label') or label}」的身份信息"
                f"（mod 自带的 manifest 里没有 Guid）。\n\n"
                "所以导入它不会替换原来那个模组，而会在模组列表里"
                "多出一条。\n"
                "如果只想更新原来那个，请到「更新检查」页重新下载它的更新包。\n\n"
                "仍然要导入吗？", parent=top):
                self.var_note.set("已取消导入。")
                return

        self.var_note.set(f"正在导入 {os.path.basename(fp)}…")
        self.update_idletasks()
        try:
            _key = read_config().get("nexus_api_key") or ""
        except Exception:  # noqa: BLE001
            _key = ""
        # mods_dir 交给 import_paths 自己从库里的 userModsDir 读（不猜）
        res = import_paths(self.data_file, [fp], api_key=_key)
        done, failed = res["done"], list(res["failed"])
        if not done:
            why = failed[0][1] if failed else "未知原因"
            error_box("导入失败", f"{os.path.basename(fp)}\n\n{why}",
                      parent=top, app=self.app,
                      context=f"导入 {os.path.basename(fp)} 时失败")
            self.var_note.set(f"导入失败：{why}")
            return

        # ★ 记成"已导入"：不然文件还在原地，这行会永远挂着「待导入」
        records.mark_applied(key)
        _p, r = done[0]
        act = "新增" if r.get("added") else "更新"
        diag.event("导入模组", f"{act}「{r.get('label') or label}」"
                              f"（{os.path.basename(fp)}）")
        self.reload()
        self.var_note.set(
            f"已{act}「{r.get('label') or label}」—— 现在去「部署模组」页部署一次，"
            "它才会真正进游戏。")
        messagebox.showinfo(
            "已导入模组列表",
            f"已{act}「{r.get('label') or label}」。\n\n"
            + ("它原来就是这个模组的旧版本，内容已经被替换"
               "（旧内容留在备份文件夹里）。\n"
               if not r.get("added") else "这是列表里的新模组。\n")
            + "★ 还没有进游戏 —— 到「部署模组」页启用/部署一次才生效。",
            parent=top)

    # 供 App._show_page 调用
    def refresh(self) -> None:
        self.reload()


class BackupsPanel(ttk.Frame):
    """「备份」页：把历史备份摊开给用户看，并允许手动删。

    为什么要有这一页（用户原话）：「**也添加个GUI界面，让玩家看到历史备份，
    并且可以手动删除吧，也是在侧边栏那里加一栏**」。
    起因是他发现备份只增不减 —— 实测游戏目录的部署备份攒到 1.63 GB
    （每份约 230 MB，每次部署再加一份）。

    三类备份（都只认我们自己 `.hd2u-` 的命名，别的文件一概不碰）：
      · 游戏目录的部署备份 `<游戏>\\data\\hd2u_backup\\<时间戳>\\` —— 大头
      · 模组目录里的旧版本文件夹 `<模组名>.hd2u-old-<时间戳>`
      · Arsenal 库/配置备份 `hd2a_data.json.hd2u-<时间戳>.bak`
        ★ 最早的那一份**永不删**（那是库的原始状态，用户的安全网）
    """
    COLS = (("keep", "保留", 70), ("kind", "类别", 150),
            ("name", "名称", 330), ("when", "时间", 140),
            ("size", "大小", 90), ("note", "说明", 260))

    def __init__(self, parent: tk.Widget, app: "App") -> None:
        super().__init__(parent)
        self.app = app
        self.items: list[dict] = []
        self.ctx: dict = {}

        head = ttk.Frame(self, padding=(12, 10, 12, 4))
        head.pack(fill="x")
        ttk.Label(head, text="历史备份", style="Head.TLabel").pack(anchor="w")
        self.lbl_intro = ttk.Label(
            head,
            text="这些是工具在改动前留下的备份，出错时靠它们退回上一步。\n"
                 "保留份数可以自己定（默认：游戏部署备份留最近 3 份、"
                 "每个模组的旧版本留 1 份、库备份留 10 份且最早那份永不删）。",
            style="Dim.TLabel", wraplength=780, justify="left")
        self.lbl_intro.pack(anchor="w", pady=(2, 0))

        bar = ttk.Frame(self, padding=(12, 6))
        bar.pack(fill="x")
        self.btn_del = ttk.Button(bar, text="删除选中", command=self._delete_selected)
        self.btn_del.pack(side="left")
        Tooltip(self.btn_del,
                "删掉选中的那一份备份（只删这一份，别的备份和模组都不动）。\n"
                "删掉后就没法再退回到那一刻了。")
        b_clean = ttk.Button(bar, text="清理多余的（按保留份数）",
                             command=self._prune_extra)
        b_clean.pack(side="left", padx=(6, 0))
        Tooltip(b_clean, "按设置里的保留份数，把超出上限的旧备份删掉。\n"
                         "删除前会先告诉你要删几项、能释放多少。")
        b_open = ttk.Button(bar, text="打开所在位置", command=self._open_location)
        b_open.pack(side="left", padx=(6, 0))
        b_ref = ttk.Button(bar, text="刷新", command=self.refresh)
        b_ref.pack(side="left", padx=(6, 0))
        b_set = ttk.Button(bar, text="保留份数设置…", command=self._open_settings)
        b_set.pack(side="right")

        wrap = ttk.Frame(self, padding=(12, 0, 12, 6))
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            wrap, columns=[c[0] for c in self.COLS], show="headings",
            selectmode="extended")
        for key, title, width in self.COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width,
                             anchor="w" if key in ("name", "note", "kind") else "center")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="left", fill="y")
        # 三态配色：会被清理的用橙色提醒，锚点（永不删）用绿色
        self.tree.tag_configure("extra", foreground=ORANGE)
        self.tree.tag_configure("anchor", foreground=GREEN)
        self.tree.tag_configure("keep", foreground=FG)

        self.var_note = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.var_note, style="Dim.TLabel",
                  wraplength=780, justify="left").pack(
            anchor="w", padx=12, pady=(0, 10))

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="删除选中的备份", command=self._delete_selected)
        self.menu.add_separator()
        self.menu.add_command(label="打开所在位置", command=self._open_location)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Delete>", lambda _e: self._delete_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_buttons())

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        """重扫三类备份，重建列表（只读）。"""
        try:
            self.ctx = self.app.backup_context()
        except Exception:  # noqa: BLE001
            self.ctx = {"data_file": "", "game_dir": "", "mods_dir": "",
                        "arsenal_running": False}
        try:
            self.keep = backups.retention()
        except Exception:  # noqa: BLE001
            self.keep = dict(backups.DEFAULTS)
        try:
            rep = backups.scan(self.ctx.get("game_dir", ""),
                               self.ctx.get("mods_dir", ""),
                               self.ctx.get("data_file", ""), self.keep)
        except Exception as e:  # noqa: BLE001
            self.var_note.set(f"扫描备份时出错：{e}")
            return

        self.items = []
        for x in rep["game"]:
            self.items.append({"path": x["path"], "is_dir": True, "kind": "游戏部署备份",
                               "note": "部署前那一套游戏文件的副本",
                               "bytes": x["bytes"], "drop": x["drop"]})
        for x in rep["old_mods"]:
            self.items.append({"path": x["path"], "is_dir": True, "kind": "模组旧版本",
                               "note": f"「{x['base']}」被覆盖前的旧内容",
                               "bytes": x["bytes"], "drop": x["drop"]})
        n_bak = len(rep["data_baks"])
        for i, x in enumerate(rep["data_baks"]):
            anchor = (i == n_bak - 1)
            self.items.append({"path": x["path"], "is_dir": False,
                               "kind": "Arsenal 库备份",
                               "note": ("★ 最早的一份，永不自动删除（库的原始状态）"
                                        if anchor else "写库前的自动备份"),
                               "bytes": x["bytes"], "drop": x["drop"],
                               "anchor": anchor})

        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for i, it in enumerate(self.items):
            name = os.path.basename(it["path"])
            if it.get("anchor"):
                tag, keep_txt = "anchor", "永不删"
            elif it["drop"]:
                tag, keep_txt = "extra", "超出上限"
            else:
                tag, keep_txt = "keep", "保留"
            self.tree.insert("", "end", iid=str(i), tags=(tag,),
                             values=(keep_txt, it["kind"], name,
                                     self._stamp_of(name),
                                     self._mb(it["bytes"]), it["note"]))

        total = rep["total_bytes"] / 1048576
        free = rep["freeable_bytes"] / 1048576
        if not self.items:
            self.var_note.set("还没有任何备份。检查更新不会产生备份；"
                              "部署模组、导入更新时才会。")
        else:
            self.var_note.set(
                f"共 {len(self.items)} 项备份，占用 {total:.1f} MB。"
                + (f"其中 {rep['freeable_items']} 项超出保留上限，"
                   f"点「清理多余的」可释放约 {free:.1f} MB。" if rep["freeable_items"]
                   else "都在保留份数以内，暂时不用清理。"))
            if self.ctx.get("arsenal_running"):
                self.var_note.set(self.var_note.get()
                                  + "（HD2 Arsenal 正在运行：模组目录里的旧版本这次不会被清理。）")
        self._sync_buttons()

    @staticmethod
    def _mb(n: int) -> str:
        b = int(n or 0)
        if b >= 1048576:
            return f"{b / 1048576:.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.0f} KB"
        return f"{b} B"

    @staticmethod
    def _stamp_of(name: str) -> str:
        """从名字里把 `20261011-214730` 抠出来显示成 `2026-10-11 21:47:30`。"""
        m = re.search(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", name or "")
        if not m:
            return "—"
        y, mo, d, h, mi, s = m.groups()
        return f"{y}-{mo}-{d} {h}:{mi}:{s}"

    # ------------------------------------------------------------------ 操作
    def _selected_items(self) -> list[dict]:
        out = []
        for iid in self.tree.selection():
            try:
                out.append(self.items[int(iid)])
            except (ValueError, IndexError):
                pass
        return out

    def _sync_buttons(self) -> None:
        has = bool(self.tree.selection())
        self.btn_del.state(["!disabled"] if has else ["disabled"])

    def _on_right_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        self._sync_buttons()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _open_location(self) -> None:
        sel = self._selected_items()
        p = sel[0]["path"] if sel else ""
        if not p:
            self.var_note.set("先选中一项，再点「打开所在位置」。")
            return
        d = p if os.path.isdir(p) else os.path.dirname(p)
        try:
            os.startfile(d)  # noqa: S606
        except Exception:  # noqa: BLE001
            messagebox.showinfo("位置", d, parent=self.winfo_toplevel())

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.app)
        dlg.after(120, lambda: None)
        self.app._last_settings_dialog = dlg

    def _delete_selected(self) -> None:
        """删除选中的备份（逐项确认，绝不静默删）。"""
        sel = self._selected_items()
        if not sel:
            self.var_note.set("先在列表里选一项（可按住 Ctrl/Shift 多选）。")
            return
        top = self.winfo_toplevel()
        total = sum(x["bytes"] for x in sel)
        anchors = [x for x in sel if x.get("anchor")]
        if anchors:
            # 锚点是用户库的原始状态 —— 允许删，但必须问得更重
            if not messagebox.askyesno(
                "这一份是「库的原始状态」",
                "你选中的里面有「最早的那一份库备份」—— 它是 Arsenal 模组库"
                "最初的样子，删掉之后就没法再退回那一步了。\n\n"
                "确定要删吗？", parent=top):
                self.var_note.set("已取消删除。")
                return
        names = "\n".join(f"· {x['kind']}：{os.path.basename(x['path'])}"
                          for x in sel[:6])
        more = f"\n…还有 {len(sel) - 6} 项" if len(sel) > 6 else ""
        if not messagebox.askyesno(
            "删除这些备份？",
            f"要删除 {len(sel)} 项备份，约 {self._mb(total)}：\n\n{names}{more}\n\n"
            "删掉之后就无法再退回这些时间点了（模组和游戏本身不受影响）。\n\n"
            "确定删除吗？", parent=top):
            self.var_note.set("已取消删除。")
            return
        rep = backups.prune_selected([x["path"] for x in sel], self.ctx)
        self.refresh()
        diag.event("删除备份", f"手动删除 {len(rep['deleted'])} 项，"
                              f"释放 {rep['freed_bytes'] / 1048576:.1f} MB"
                              + (f"，{len(rep['failed'])} 项没删掉" if rep["failed"] else ""))
        self.var_note.set(
            f"已删除 {len(rep['deleted'])} 项，释放约 "
            f"{rep['freed_bytes'] / 1048576:.1f} MB。"
            + (f"（{len(rep['failed'])} 项没删掉）" if rep["failed"] else ""))

    def _prune_extra(self) -> None:
        """按保留份数清掉超出的那些（先算给用户看，再动手）。"""
        try:
            rep = backups.scan(self.ctx.get("game_dir", ""),
                               self.ctx.get("mods_dir", ""),
                               self.ctx.get("data_file", ""), self.keep)
        except Exception as e:  # noqa: BLE001
            self.var_note.set(f"扫描备份时出错：{e}")
            return
        if not rep["freeable_items"]:
            self.var_note.set("没有超出的备份 —— 当前数量在保留份数以内。")
            return
        top = self.winfo_toplevel()
        keep = self.keep
        if not messagebox.askyesno(
            "清理多余的备份？",
            f"按你设置的保留份数：\n"
            f"· 游戏部署备份留最近 {keep['keep_game_backups']} 份\n"
            f"· 每个模组的旧版本留 {keep['keep_old_mod_dirs']} 份\n"
            f"· 库备份留 {keep['keep_data_baks']} 份（最早那份永不删）\n\n"
            f"将删除 {rep['freeable_items']} 项，释放约 "
            f"{rep['freeable_bytes'] / 1048576:.1f} MB。\n\n"
            "确定吗？", parent=top):
            self.var_note.set("已取消清理。")
            return
        got = backups.prune(game_dir=self.ctx.get("game_dir", ""),
                            mods_dir=self.ctx.get("mods_dir", ""),
                            data_file=self.ctx.get("data_file", ""),
                            keep=self.keep,
                            skip_mods=bool(self.ctx.get("arsenal_running")))
        self.refresh()
        diag.event("清理备份", f"按保留份数清理 {len(got['deleted'])} 项，"
                              f"释放 {got['freed_bytes'] / 1048576:.1f} MB")
        self.var_note.set(
            f"已清理 {len(got['deleted'])} 项，释放约 "
            f"{got['freed_bytes'] / 1048576:.1f} MB。")

    # 供 App._show_page 调用
    def reload(self) -> None:
        self.refresh()


# ==========================================================================
# 反馈 / 诊断
#
# 为什么有这一整块（用户原话 2026-10-11）：
# 「我的模组更新管理器，现在还想加个新需求，让用户更方便反馈问题，
#   让我也更方便解决用户反馈的问题」。
#
# 起因是一次真实的反馈：小黑盒用户只说「很多 mod 搜不到」+ 两张截图，
# 而工具当时没有任何取证手段 —— 没有日志、没有异常捕获、错误弹窗一关
# 就什么都不剩。于是只能来回追问「你装了什么、点了哪一步、网通不通」。
# 这一块就是把那些问题一次性回答完。
# ==========================================================================
class FeedbackDialog(tk.Toplevel):
    """「生成反馈信息」对话框：把现场拼成一段可以直接粘贴的文字。

    三个出口对应三种真实渠道：
      · 复制完整诊断 —— 贴到 GitHub issue / 小黑盒 / 直接发给我
      · 复制短摘要   —— 15 行以内，适合**截图**发帖（评论区发不了长文）
      · 打开反馈页   —— GitHub 新建 issue，标题正文预填好

    ★ 输出里不会有 N网 key / GitHub token（diag 层按实际值精确抹掉）。
    """

    def __init__(self, master, app: "App | None", context: str = "",
                 with_logs: bool = False, desc: str = "") -> None:
        super().__init__(master)
        self.title("生成反馈信息")
        self.configure(bg=BG)
        self.app = app
        self.context = context
        self.transient(master)
        self.grab_set()

        ttk.Label(self, text="生成反馈信息", style="Head.TLabel").pack(
            anchor="w", padx=16, pady=(14, 2))
        ttk.Label(
            self,
            text="下面三个框随便填一下（写不清就只写现象），"
                 "然后点「复制完整诊断」，粘到反馈里发出来就行。\n"
                 "这段文字里带着我定位问题需要的东西：工具版本、系统、"
                 "模组清单、最近做过哪些操作、网络通不通。\n"
                 "★ 里面不会有你的 N网 key 和 GitHub token。",
            style="Dim.TLabel", wraplength=740, justify="left",
        ).pack(anchor="w", padx=16)

        self.txt_desc = self._text_box("现象（看到了什么）：", 3, desc)
        self.txt_steps = self._text_box("我做了什么（点过哪些按钮、走到哪一步）：", 3, "")
        self.txt_expect = self._text_box("期望是什么：", 2, "")

        opt = ttk.Frame(self, padding=(16, 6))
        opt.pack(fill="x")
        self.var_lib = tk.BooleanVar(value=True)
        self.var_logs = tk.BooleanVar(value=bool(with_logs))
        self.var_hide = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="包含模组清单与目录名（查「搜不到」时最关键）",
                        variable=self.var_lib, command=self._regen).pack(anchor="w")
        ttk.Checkbutton(opt, text="包含日志尾部（报错、崩溃类问题建议勾上）",
                        variable=self.var_logs, command=self._regen).pack(anchor="w")
        ttk.Checkbutton(opt, text="把我的 Windows 用户名替换成 <用户>",
                        variable=self.var_hide, command=self._regen).pack(anchor="w")

        netrow = ttk.Frame(self, padding=(16, 6))
        netrow.pack(fill="x")
        self.var_net = tk.StringVar(value="网络自检：还没检测")
        ttk.Label(netrow, textvariable=self.var_net, style="Dim.TLabel",
                  wraplength=560, justify="left").pack(side="left")
        b_net = ttk.Button(netrow, text="重新检测网络", command=self._net)
        b_net.pack(side="right")
        Tooltip(b_net, "分别试一下 GitHub / N网 / ayakamods 能不能连上，"
                       "以及有没有走代理。\n\n"
                       "判据是「有没有拿到响应」：站点回 401/403 也算能连上"
                       "（那是它在挡自动请求，工具平时照样能用）；"
                       "只有超时、连不上才算网络问题。")

        ttk.Label(self, text="要发出去的内容（可以直接在这里改）：",
                  style="Dim.TLabel").pack(anchor="w", padx=16)
        wrap = ttk.Frame(self, padding=(16, 2))
        wrap.pack(fill="both", expand=True)
        self.txt_out = tk.Text(wrap, height=13, bg=BG3, fg=FG, relief="flat",
                               insertbackground=FG, wrap="none",
                               font=("Consolas", 9))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.txt_out.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.txt_out.xview)
        self.txt_out.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.txt_out.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        btns = ttk.Frame(self, padding=(16, 8, 16, 14))
        btns.pack(fill="x")
        ttk.Button(btns, text="复制完整诊断", style="Accent.TButton",
                   command=self._copy_full).pack(side="left")
        ttk.Button(btns, text="复制短摘要（适合截图）",
                   command=self._copy_summary).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="保存成文件…",
                   command=self._save).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="打开反馈页（GitHub）",
                   command=self._open_issue).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right")
        self.var_hint = tk.StringVar(value="")
        ttk.Label(btns, textvariable=self.var_hint, style="Dim.TLabel").pack(
            side="right", padx=(0, 10))

        self._regen()
        if self.app is not None:
            self.app.net_check_async()
        self.refresh_net()
        fit_and_center(self, master, min_w=800)

    # ------------------------------------------------------------------ 构件
    def _text_box(self, label: str, height: int, initial: str) -> tk.Text:
        ttk.Label(self, text=label, style="Dim.TLabel").pack(
            anchor="w", padx=16, pady=(6, 0))
        box = tk.Text(self, height=height, bg=BG3, fg=FG, relief="flat",
                      insertbackground=FG, wrap="word",
                      font=("Microsoft YaHei UI", 9))
        box.pack(fill="x", padx=16)
        if initial:
            box.insert("1.0", initial)
        return box

    @staticmethod
    def _val(box: tk.Text) -> str:
        return box.get("1.0", "end").strip()

    def _net(self) -> None:
        if self.app is not None:
            self.app.net_check_async(force=True)
        self.refresh_net()

    def refresh_net(self) -> None:
        """把网络自检结果显示出来（结果一到就刷新，并同步进报告）。"""
        r = getattr(self.app, "_net_result", None) if self.app is not None else None
        if not r:
            running = bool(getattr(self.app, "_net_running", False))
            self.var_net.set("网络自检：检测中…" if running else "网络自检：还没检测")
            return
        parts = []
        for s in (r.get("sites") or []):
            mark = "能连上" if s.get("ok") else "连不上"
            if s.get("blocked") == "cf":
                mark = "能连上但被挡住"
            parts.append(f"{s.get('name')}{mark}")
        self.var_net.set("网络自检：" + "　".join(parts)
                         + f"　（代理：{r.get('proxy')}）")
        self._regen()

    def _regen(self) -> None:
        """按当前填写与勾选重新生成要发出去的那段文字。"""
        try:
            text = diag.report(
                desc=self._val(self.txt_desc),
                steps=self._val(self.txt_steps),
                expect=self._val(self.txt_expect),
                include_library=bool(self.var_lib.get()),
                include_logs=bool(self.var_logs.get()),
                hide_user=bool(self.var_hide.get()),
                net=(getattr(self.app, "_net_result", None) if self.app else None),
                context=self.context,
            )
        except Exception as e:  # noqa: BLE001
            text = (f"生成诊断信息时出错：{type(e).__name__}: {e}\n"
                    f"请把这一行连同你的描述一起发给我。")
        self.txt_out.delete("1.0", "end")
        self.txt_out.insert("1.0", text)

    # ------------------------------------------------------------------ 动作
    def _to_clipboard(self, text: str) -> bool:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
            return True
        except tk.TclError as e:
            self.var_hint.set(f"复制失败：{e}")
            return False

    def _copy_full(self) -> None:
        self._regen()          # 复制前按当前勾选重生成，避免发出旧内容
        if self._to_clipboard(self.txt_out.get("1.0", "end").rstrip()):
            self.var_hint.set("已复制完整诊断 ✓")
            diag.event("导出反馈信息", "复制完整诊断")

    def _copy_summary(self) -> None:
        s = diag.summary(self._val(self.txt_desc),
                         net=(getattr(self.app, "_net_result", None) if self.app else None),
                         hide_user=bool(self.var_hide.get()))
        if self._to_clipboard(s):
            self.var_hint.set("已复制短摘要 ✓（可直接截图发帖）")
            diag.event("导出反馈信息", "复制短摘要")

    def _save(self) -> None:
        self._regen()
        p = filedialog.asksaveasfilename(
            parent=self, title="把反馈信息存成文件",
            initialfile=diag.default_report_name(),
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not p:
            return
        try:
            diag.save_text(self.txt_out.get("1.0", "end").rstrip(), p)
        except OSError as e:
            messagebox.showerror("保存失败", str(e), parent=self)
            return
        self.var_hint.set("已保存 ✓")
        diag.event("导出反馈信息", f"存成文件：{p}")

    def _open_issue(self) -> None:
        """打开 GitHub 新建 issue（并把完整诊断放进剪贴板）。"""
        self._regen()
        full = self.txt_out.get("1.0", "end").rstrip()
        self._to_clipboard(full)     # 先复制，用户到页面里 Ctrl+V 即可
        desc = self._val(self.txt_desc)
        first = desc.splitlines()[0][:50] if desc.strip() else "请在这里写一句话说明问题"
        body = diag.summary(
            desc, net=(getattr(self.app, "_net_result", None) if self.app else None),
            hide_user=bool(self.var_hide.get()))
        body += "\n\n（完整诊断已经复制到剪贴板了：在下面按 Ctrl+V 粘上即可）"
        url = diag.issue_url("[反馈] " + first, body)
        if open_in_browser(url):
            self.var_hint.set("已打开反馈页；完整诊断在剪贴板里 ✓")
            diag.event("导出反馈信息", "打开反馈页")
        else:
            self.var_hint.set("打不开浏览器 —— 内容已复制，粘到任意地方发出来即可")


class FeedbackPanel(ttk.Frame):
    """「反馈 / 帮助」页：出问题时来这里。

    用户原话（2026-10-11）：「让用户更方便反馈问题，让我也更方便解决
    用户反馈的问题」。所以这一页做三件事：
      · 一键把现场打包（生成反馈信息 / 复制短摘要 / 打开反馈页）
      · 网络自检 —— 先排掉"其实是网不通"这一类
      · 把日志摊开给用户看（在哪、多大、最近记了什么）

    为什么日志也要"看得见"：用户此前反复要求「状态与操作都要可见可及」
    （备份页也是这么来的）—— 只写文件不给人看，等于没有。
    """

    def __init__(self, parent: tk.Widget, app: "App") -> None:
        super().__init__(parent)
        self.app = app

        head = ttk.Frame(self, padding=(12, 10, 12, 4))
        head.pack(fill="x")
        ttk.Label(head, text="反馈 / 帮助", style="Head.TLabel").pack(anchor="w")
        ttk.Label(
            head,
            text="遇到问题（模组搜不到、下载失败、部署报错…）就到这里来。\n"
                 "点「生成反馈信息」会把定位问题需要的东西打包成一段文字："
                 "工具版本、系统、模组清单、最近做过哪些操作、网络通不通。\n"
                 "把那段文字发出来就行 —— 这样就不用再来回问"
                 "「你装了什么、点了哪一步」。",
            style="Dim.TLabel", wraplength=820, justify="left").pack(
            anchor="w", pady=(2, 0))

        bar = ttk.Frame(self, padding=(12, 8))
        bar.pack(fill="x")
        b1 = ttk.Button(bar, text="生成反馈信息…", style="Accent.TButton",
                        command=self._open_dialog)
        b1.pack(side="left")
        Tooltip(b1, HOVER_TIPS["btn_feedback"])
        b2 = ttk.Button(bar, text="复制短摘要（适合截图）",
                        command=self._copy_summary)
        b2.pack(side="left", padx=(6, 0))
        Tooltip(b2, "15 行以内的一小段，专门给发帖/截图用：\n"
                    "版本、系统、模组数、网络、以及你的问题一句话。")
        b3 = ttk.Button(bar, text="打开反馈页（GitHub）", command=self._open_issue)
        b3.pack(side="left", padx=(6, 0))
        Tooltip(b3, "在浏览器打开 GitHub 的新建 issue 页面。\n"
                    "没有 GitHub 账号也没关系：把复制到的内容贴到"
                    "小黑盒/贴吧/群里同样可以。")

        net = ttk.LabelFrame(self, text="网络自检", padding=(10, 6))
        net.pack(fill="x", padx=12, pady=(4, 0))
        self.var_net = tk.StringVar(value="还没检测")
        ttk.Label(net, textvariable=self.var_net, style="Dim.TLabel",
                  wraplength=780, justify="left").pack(anchor="w")
        nb = ttk.Button(net, text="重新检测", command=self._net)
        nb.pack(anchor="w", pady=(4, 0))
        Tooltip(nb, "分别试 GitHub / N网 / ayakamods 能不能连上，以及有没有走代理。\n\n"
                    "判据是「有没有拿到响应」：站点回 401/403 也算能连上"
                    "（那是它在挡自动请求，工具平时照样能用）；"
                    "只有超时、连不上才是网络问题。\n"
                    "「查不到更新」里有一部分其实是网络/代理问题，先在这里排掉。")

        logbox = ttk.LabelFrame(self, text="日志", padding=(10, 6))
        logbox.pack(fill="x", padx=12, pady=(10, 0))
        self.var_log = tk.StringVar(value="")
        ttk.Label(logbox, textvariable=self.var_log, style="Dim.TLabel",
                  wraplength=780, justify="left").pack(anchor="w")
        lrow = ttk.Frame(logbox)
        lrow.pack(fill="x", pady=(4, 0))
        self.btn_open_log = ttk.Button(lrow, text="打开日志文件",
                                       command=self._open_log)
        self.btn_open_log.pack(side="left")
        Tooltip(self.btn_open_log, HOVER_TIPS["btn_log"])
        ttk.Button(lrow, text="打开日志文件夹",
                   command=self._open_log_dir).pack(side="left", padx=(6, 0))
        self.btn_log_toggle = ttk.Button(lrow, text="", command=self._toggle_log)
        self.btn_log_toggle.pack(side="left", padx=(6, 0))
        ttk.Button(lrow, text="看最近日志",
                   command=self._show_tail).pack(side="left", padx=(6, 0))

        wrap = ttk.Frame(self, padding=(12, 6, 12, 10))
        wrap.pack(fill="both", expand=True)
        self.txt = tk.Text(wrap, height=10, bg=BG2, fg=FG, relief="flat",
                           wrap="none", font=("Consolas", 9))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=vs.set)
        self.txt.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        self.refresh()

    # ------------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        """刷新日志状态与网络自检显示。"""
        p = diag.log_path()
        if p:
            mb = diag.LOG_MAX_BYTES // 1024 // 1024
            self.var_log.set(
                f"日志文件：{p}\n"
                f"保留最近 {diag.LOG_KEEP_DAYS} 天（单个文件超过 {mb} MB 会自动轮转）。\n"
                f"记的是每次检查/下载/导入/部署的关键步骤 —— 反馈时一起发出来最省事。")
            self.btn_log_toggle.configure(text="关闭日志记录")
        else:
            self.var_log.set("日志当前是关着的（"
                             + (diag.disabled_reason() or "未知原因")
                             + "）。建议打开：出问题时才有东西可查。")
            self.btn_log_toggle.configure(text="开启日志记录")
        try:
            self.btn_open_log.configure(state="normal" if p else "disabled")
        except tk.TclError:
            pass
        self.refresh_net()
        if not self.txt.get("1.0", "end").strip():
            self._show_tail()

    def refresh_net(self) -> None:
        r = getattr(self.app, "_net_result", None)
        if not r:
            running = bool(getattr(self.app, "_net_running", False))
            self.var_net.set("检测中…（几秒后出结果）" if running else "还没检测")
            if not running:
                self.app.net_check_async()
            return
        parts = []
        for s in r.get("sites") or []:
            mark = "能连上" if s.get("ok") else "连不上"
            if s.get("blocked") == "cf":
                mark = "能连上，但被站点挡住"
            parts.append(f"{s.get('name')}：{mark}"
                         + ("" if s.get("ok") else f"（{s.get('detail')}）"))
        note = ""
        cf = [s.get("name") for s in r.get("sites") or []
              if s.get("blocked") == "cf"]
        if cf:
            note = ("\n★ " + "、".join(cf) + " 显示「被挡住」：那是它要求能跑 JS 的"
                    "浏览器才放行，所以你的浏览器能打开、本工具会被挡 —— "
                    "不是网络故障，也不是你的登录过期。\n"
                    "受影响的只有这几个站点的自动查询；其它站点照常，"
                    "已识别好的来源也不会丢。换代理节点或过一会儿再试通常就好。")
        self.var_net.set(
            f"检测时间 {r.get('when')}　" + "　".join(parts)
            + f"\n代理：{r.get('proxy')}"
            + "\n（这里探的就是工具实际查询用的那条路：N 网走官方 API、"
              "GitHub 走 API、a 站只能爬网页。）"
            + note)

    # ------------------------------------------------------------------ 动作
    def _net(self) -> None:
        self.app.net_check_async(force=True)
        self.var_net.set("检测中…（几秒后出结果）")

    def _open_dialog(self) -> None:
        dlg = FeedbackDialog(self.app, self.app)
        self.app._last_feedback_dialog = dlg

    def _copy_summary(self) -> None:
        s = diag.summary("", net=getattr(self.app, "_net_result", None))
        try:
            self.app.clipboard_clear()
            self.app.clipboard_append(s)
            self.app.var_status.set("短摘要已复制到剪贴板 —— 可以直接截图发帖")
            diag.event("导出反馈信息", "复制短摘要")
        except tk.TclError as e:
            messagebox.showerror("复制失败", str(e), parent=self)

    def _open_issue(self) -> None:
        url = diag.issue_url("[反馈] 请在这里写一句话说明问题",
                             diag.summary("", net=getattr(self.app, "_net_result", None)))
        if open_in_browser(url):
            self.app.var_status.set("已打开反馈页")
            diag.event("导出反馈信息", "打开反馈页")
        else:
            messagebox.showinfo(
                "打不开浏览器",
                f"没能自动打开浏览器。可以手动访问：\n{diag.ISSUES_URL}\n\n"
                f"或者直接点「复制短摘要」/「生成反馈信息」，"
                f"把内容贴到小黑盒、贴吧、群里同样可以。",
                parent=self)

    def _open_log(self) -> None:
        p = diag.log_path()
        if not p or not os.path.isfile(p):
            messagebox.showinfo("没有日志文件",
                                "现在还没有日志文件（日志可能刚打开）。",
                                parent=self)
            return
        try:
            os.startfile(p)  # noqa: S606
        except OSError as e:
            messagebox.showinfo("日志文件", f"{p}\n\n（打不开：{e}）", parent=self)

    def _open_log_dir(self) -> None:
        d = diag.logs_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606
        except OSError as e:
            messagebox.showinfo("日志文件夹", f"{d}\n\n（打不开：{e}）", parent=self)

    def _toggle_log(self) -> None:
        on = not bool(diag.log_path())
        p = diag.set_enabled(on)
        diag.event("日志", "已开启" if p else "已关闭")
        self.refresh()
        self.app.var_status.set("日志已开启" if p else "日志已关闭")

    def _show_tail(self) -> None:
        text = diag.log_tail(80)
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", text)


class ErrorDialog(tk.Toplevel):
    """报错弹窗 —— 比 messagebox 多三个出口。

    为什么不用 messagebox：它关掉就什么都不剩，用户报障时手上没有任何
    能给我的东西（工具以前也没有日志）。而「报错那一刻」正是现场最新鲜的
    时候，所以把「交出现场」的按钮直接做在这个弹窗上。
    """

    def __init__(self, master, title: str, message: str,
                 app: "App | None" = None, context: str = "") -> None:
        super().__init__(master)
        self.title(title)
        self.configure(bg=BG)
        self.app = app
        self.context = context
        self.message = message
        self.transient(master)
        self.grab_set()

        ttk.Label(self, text=title, style="Head.TLabel").pack(
            anchor="w", padx=16, pady=(14, 2))
        ttk.Label(self,
                  text="详细信息如下（可以选中复制）。\n"
                       "这一段已经记进日志了；想让我帮你查，点"
                       "「生成反馈信息」把现场一起发出来最省事。",
                  style="Dim.TLabel", wraplength=660, justify="left").pack(
            anchor="w", padx=16)

        wrap = ttk.Frame(self, padding=(16, 6))
        wrap.pack(fill="both", expand=True)
        self.txt = tk.Text(wrap, height=10, width=76, bg=BG3, fg=FG,
                           relief="flat", wrap="word",
                           font=("Microsoft YaHei UI", 9))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=vs.set)
        self.txt.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.txt.insert("1.0", message)

        btns = ttk.Frame(self, padding=(16, 6, 16, 14))
        btns.pack(fill="x")
        ttk.Button(btns, text="生成反馈信息…", style="Accent.TButton",
                   command=self._feedback).pack(side="left")
        ttk.Button(btns, text="复制这段信息",
                   command=self._copy).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="打开日志",
                   command=self._open_log).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right")
        self.var_hint = tk.StringVar(value="")
        ttk.Label(btns, textvariable=self.var_hint, style="Dim.TLabel").pack(
            side="right", padx=(0, 10))

        fit_and_center(self, master, min_w=700)

    def _feedback(self) -> None:
        ctx = self.context or f"从报错弹窗生成（{self.title()}）"
        dlg = FeedbackDialog(self, self.app, context=ctx, with_logs=True,
                             desc=self.title())
        if self.app is not None:
            self.app._last_feedback_dialog = dlg

    def _copy(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self.message)
            self.update_idletasks()
            self.var_hint.set("已复制 ✓")
        except tk.TclError as e:
            self.var_hint.set(f"复制失败：{e}")

    def _open_log(self) -> None:
        p = diag.log_path()
        if not p or not os.path.isfile(p):
            messagebox.showinfo("没有日志文件", "现在还没有日志文件。", parent=self)
            return
        try:
            os.startfile(p)  # noqa: S606
        except OSError as e:
            messagebox.showinfo("日志文件", f"{p}\n\n（打不开：{e}）", parent=self)


def _dialogs_suppressed() -> bool:
    """当前是否处于"不要弹自定义窗口"的环境（测试 / 无人值守）。

    为什么要这个开关：新的报错弹窗是模态的（和 messagebox 一样），
    测试里一旦走到错误分支就会**卡住等人点关闭**。所以测试环境下退回
    普通 messagebox —— 测试早就把 messagebox 打桩了，于是行为与加这个
    功能之前完全一致（断言弹窗内容也照旧有效）。
    """
    return bool(os.environ.get("HD2U_NO_DIALOG")
                or os.environ.get("HD2U_READONLY_GUARD"))


def error_box(title: str, message: str, parent=None, app: "App | None" = None,
              context: str = "") -> None:
    """统一的报错弹窗：先留痕（日志 + 时间线），再弹窗，弹窗里就有反馈出口。

    所有「失败了」的地方都走这里 —— 保证每一次报错都在日志里留下痕迹，
    即使用户什么都不点，下次反馈时也查得到。
    """
    diag.log(f"报错：{title} —— {message}", "ERROR")
    diag.event("报错", f"{title}：{util.short_err(message, 60)}")
    if _dialogs_suppressed():
        messagebox.showerror(title, message, parent=parent)
        return
    try:
        dlg = ErrorDialog(parent, title, message, app=app, context=context)
    except Exception:  # noqa: BLE001
        messagebox.showerror(title, message, parent=parent)
        return
    try:
        if parent is not None and hasattr(parent, "wait_window"):
            parent.wait_window(dlg)     # 和 messagebox 一样是模态的
    except Exception:  # noqa: BLE001
        pass


class QueryFailDialog(tk.Toplevel):
    """「来源总览」—— 固定列出三个站点，各自"找到没找到"，且能单独重查。

    ★ 两轮用户反馈共同塑造了它（2026-10-11）：
    · 第一轮：「要是不好适配要不直接改成来源总览，显示该mod的全部来源？然后玩家
      可以自己根据来源去下载（a网和n网的都是打开，GitHub是下载，和列表里差不多）」
      → 从"解释型"改成"总览 + 可操作"。
    · 第二轮（附截图）：「来源总览里面要不直接改成三个来源，然后后面写是否找到，
      然后可以单独查找某一个来源或者全部重新查找这样，给更多可以操控的地方，
      现在里面就一个莫名其妙的空白窗和几行字」
      → ① 那个空白窗是我改造时**忘了删旧文本框**（已删）；
        ② 改成**固定三行**（GitHub / N网 / a 站），每行都写"找到 / 没找到"
           —— 动态只列"找到的"，用户就看不出"哪个站点没找到"；
        ③ 每行一个「只查这个」，底部一个「全部重新查找」。

    动作与列表里一致：GitHub 有直链→直接下载；N网/a站→打开页面 + 接力收文件。
    """

    # 固定展示的三个站点（顺序 = 用户心中的优先级 github > nexus > ayaka）
    PROVIDERS = (("github", "GitHub"), ("nexus", "N网"), ("ayaka", "ayakamods"))

    def __init__(self, master, app: "App", row: dict) -> None:
        super().__init__(master)
        self.title("来源总览")
        self.configure(bg=BG)
        self.app = app
        self.row = row
        self.transient(master)
        self.grab_set()

        entry = row.get("entry") or {}
        name = entry.get("label") or entry.get("dirname") or "（未命名）"

        ttk.Label(self, text=name, style="Head.TLabel").pack(
            anchor="w", padx=16, pady=(14, 2))
        # ★ 顶部这行用 StringVar，**重查完要跟着更新**。
        #   踩过的坑（用户 2026-10-11）：「在来源查找界面，下面显示已找到了，
        #   但是上面还是显示的没找到，是不是没做同步？」—— 正是这个问题：
        #   这句原来是 __init__ 里写死的静态 Label，refresh_sources() 只重画了
        #   下面三行，顶部就停在旧结论上，两处自相矛盾。
        self.var_head = tk.StringVar()
        ttk.Label(self, textvariable=self.var_head,
                  style="Dim.TLabel").pack(anchor="w", padx=16)
        ttk.Label(
            self,
            text=("下面是三个站点各自的结果 —— 想用哪个就点它右边的按钮"
                  "（GitHub 有直链会直接下载；N网 / a 站会打开页面并自动收回文件）。\n"
                  "某个站点写「没找到」时，可以点「只查这个」单独再找一次。"),
            style="Dim.TLabel", wraplength=720, justify="left").pack(
            anchor="w", padx=16, pady=(4, 0))

        # ★ 不再用那个大文本框（用户原话："就一个莫名其妙的空白窗"）——
        #   信息全在下面固定的三行里。
        self.rows_box = ttk.Frame(self, padding=(16, 8))
        self.rows_box.pack(fill="both", expand=True)

        btns = ttk.Frame(self, padding=(16, 6, 16, 14))
        btns.pack(fill="x")
        self.btn_refind = ttk.Button(btns, text="全部重新查找",
                                     style="Accent.TButton",
                                     command=self._refind_all)
        self.btn_refind.pack(side="left")
        Tooltip(self.btn_refind,
                "把三个站点都重新找一遍（联网搜索 + 用本地站点索引匹配）。\n"
                "已找到的来源会按新结果更新；没有结果的站点不会影响其它站点。")
        b_src = ttk.Button(btns, text="手工指定来源…",
                           command=lambda: (self.destroy(), app._ctx_set_source()))
        b_src.pack(side="left", padx=(6, 0))
        Tooltip(b_src, "手工把它的网页地址贴一次 —— 贴一次就长期记住，"
                       "以后自动识别不会覆盖它。")
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right")
        self.var_hint = tk.StringVar(value="")
        ttk.Label(btns, textvariable=self.var_hint, style="Dim.TLabel").pack(
            side="right", padx=(0, 10))

        self.sources: list[dict] = []
        self._fill()
        self._sync_head()      # ★ 必须在 _fill() 之后（结论要看 self.sources）
        self._build_rows()
        fit_and_center(self, master, min_w=780)

    # ------------------------------------------------------------------ 数据
    def _fill(self) -> None:
        """收集这个 mod 的**全部来源**（已保存的 + 本次查到的）。

        ★ 归组键是 `(站点, id 或 url)` —— **不含版本**。
        组内优先显示"本次查询结果"（带版本、直链、失败原因），只有当这一组
        完全没有查询结果时，才用"已保存的来源"占位。

        为什么这么改（用户 2026-10-12 报的 bug）：
        「来源总览里重新查找只是显示已经重新查找，结果是什么也没显示，
        明明 GitHub 有这个模组也还是没找到」。
        原来按 `(站点, 版本)` 去重，而"已保存的来源"里**没有版本字段** ——
        于是同一个 GitHub 被拆成两行：「找到（还没查版本）」+「找到 r42」。
        更糟的是重查结果根本没落盘（见 engine.persist_resolved），
        于是列表里只剩「没找到」。
        按站点归组后：同一条来源只会出现一次，且**带上查到的版本**；
        同一站点真有多个版本（GitHub 的正式版 + 预发布版）时，
        它们仍然各自成行（两条都在 checks 里，不会被并掉）。
        """
        row = self.row
        entry = row.get("entry") or {}
        best = row.get("best") or {}

        groups: dict[tuple, dict] = {}
        order: list[tuple] = []

        def bucket(src: dict) -> dict:
            prov = str(src.get("provider") or "")
            ident = str(src.get("id") or src.get("url") or "")
            k = (prov, ident)
            if not prov:
                k = ("", "")
            if k not in groups:
                groups[k] = {"saved": [], "checks": [], "best": []}
                order.append(k)
            return groups[k]

        def provider_group(prov: str) -> dict | None:
            """该站点已有的组（用于把"只有 provider 的兜底条目"并进去）。"""
            for kk in order:
                if kk[0] == prov:
                    return groups[kk]
            return None

        # ① 已保存的来源（工具认下来的，往往**没有版本信息**）
        try:
            saved = load_sources().get(source_key(entry)) or []
        except Exception:  # noqa: BLE001
            saved = []
        for s in saved:
            bucket(dict(s))["saved"].append(dict(s))

        # ② 本次查询结果（含版本、直链、失败原因、是否测试版）
        for c in (row.get("checks") or []):
            src = dict(c.get("_src") or {})
            src.update({"provider": c.get("provider") or src.get("provider"),
                        "url": c.get("url") or src.get("url"),
                        "version": c.get("version"),
                        "prerelease": c.get("prerelease"),
                        "ok": c.get("ok"),
                        "error": c.get("error"),
                        "download_url": c.get("download_url"),
                        "asset_name": c.get("asset_name"),
                        "release_url": c.get("release_url")})
            bucket(src)["checks"].append(src)

        # ③ 兜底：当前这一行显示的那个来源。
        # ★ 只在**该站点还没有任何组**时才加 —— `best` 本身就是从 checks 或
        #   已保存来源里挑出来的（evaluate 的产物），重复加会多出一行
        #   「找到 r42-ui-recovery-test / 打开页面」（实测踩到过）。
        if best.get("provider") and provider_group(str(best["provider"])) is None:
            bucket(dict(best))["best"].append(dict(best))

        out: list[dict] = []
        for k in order:
            g = groups[k]
            # 有查询结果就只用查询结果（它带版本/直链/失败原因）；
            # 没有才退回"已保存的"，最后才是 best。
            picked = g["checks"] or g["saved"] or g["best"]
            for s in picked:
                out.append(s)
        self.sources = out
        # 把"当前显示的那一条"排到最前，方便一眼对上
        self.sources.sort(key=lambda s: 0 if s.get("provider") == best.get("provider")
                          else 1)

    def _build_rows(self) -> None:
        """固定三行（GitHub / N网 / a 站），每行写"找到没找到" + 单独查找。

        ★ 用户 2026-10-11 附截图提的：「改成三个来源，然后后面写是否找到，
        然后可以单独查找某一个来源或者全部重新查找」。
        为什么固定三行比"只列找到的"好：**用户能一眼看出哪个站点没找到**
        —— 只列找到的，他无从知道是"没找到"还是"没查"。
        同一个站点有多条（GitHub 的正式版 + 预发布版）时，都在这一行里列出。
        """
        by_prov: dict[str, list[dict]] = {}
        for s in self.sources:
            by_prov.setdefault(str(s.get("provider") or ""), []).append(s)

        for prov, pname in self.PROVIDERS:
            items = by_prov.get(prov) or []
            card = ttk.LabelFrame(self.rows_box, text=pname, padding=(10, 6))
            card.pack(fill="x", pady=(0, 6))

            if not items:
                # 没找到 —— 明说，并给"只查这个"
                ttk.Label(card, text="没找到（可以点右边单独再查一次）",
                          style="Dim.TLabel").pack(side="left")
            else:
                box = ttk.Frame(card)
                box.pack(side="left", fill="x", expand=True)
                for s in items:
                    ver = str(s.get("version") or "")
                    if s.get("ok") is False:
                        state = "查询失败：" + util.short_err(
                            str(s.get("error") or ""), 46)
                    elif ver:
                        pre = bool(s.get("prerelease")) or util.is_prerelease_text(ver)
                        state = f"找到 {ver}" + ("（测试版）" if pre else "（正式版）")
                    else:
                        state = "找到（还没查版本）"
                    r = ttk.Frame(box)
                    r.pack(fill="x")
                    ttk.Label(r, text=state, style="Dim.TLabel",
                              wraplength=430, justify="left").pack(side="left")
                    can_dl = bool(s.get("download_url"))
                    # 按钮带版本：同站点多条时能分清该点哪个
                    blabel = (f"下载 {ver[:16]}" if can_dl and ver
                              else ("下载" if can_dl else "打开页面"))
                    b = ttk.Button(r, text=blabel, width=18,
                                   command=lambda _s=s: self._act(_s))
                    b.pack(side="right")
                    Tooltip(b, "直接下载这个文件（GitHub 直链），"
                               "下完可在「下载 / 导入」页导入。"
                               if can_dl else
                               "会打开对应页面，并把下好的文件自动收回工具的下载夹。")
            # 每个站点都有「只查这个」
            b_one = ttk.Button(card, text="只查这个", width=10,
                               command=lambda _p=prov, _n=pname:
                               self._refind_one(_p, _n))
            b_one.pack(side="right")
            Tooltip(b_one, f"只重新查找 {pname} 这一个站点 —— 别的站点不受影响，"
                           f"已找到的来源也不会丢。")

    def _act(self, src: dict) -> None:
        """按来源动手：有直链就下载，否则打开页面 + 接力。"""
        self.destroy()
        self.app.download_source(self.row.get("entry") or {}, src)


    def _refind_one(self, prov: str, pname: str) -> None:
        """只重新查找**某一个站点**（用户要的"单独查找某一个来源"）。"""
        self._refind({prov}, f"正在重新查找 {pname}…")

    def _refind_all(self) -> None:
        """三个站点全部重新查找。"""
        self._refind({"github", "nexus", "ayaka"}, "正在重新查找全部三个站点…")

    def _refind(self, providers: set[str], hint: str) -> None:
        """后台重查指定站点，完成后就地刷新这个框。

        ★ 关键：走 `resolve_sources(only_providers=...)` —— 它会**跳过
        "已保存来源"的短路**（否则用户点"只查 GitHub"会因为已有 a 站来源
        而什么都不做），并把新结果**合并**进原有来源（别的站点不受影响）。
        """
        entry = self.row.get("entry") or {}
        self.var_hint.set(hint)
        try:
            self.btn_refind.configure(state="disabled")
        except tk.TclError:
            return
        diag.event("来源总览", f"重新查找 {entry.get('label') or ''}："
                             f"{'、'.join(sorted(providers))}")

        def run() -> None:
            try:
                eng = Engine(
                    self.app.data_file or "",
                    api_key=self.app.var_nexus_key.get().strip(),
                    github_token=self.app.var_github_token.get().strip(),
                    use_search=True,
                    log=lambda m: diag.log(m),
                    progress=lambda *_a: None,
                )
                eng.load()
                key = source_key(entry)
                eng.resolve_sources(only_keys={key},
                                    only_providers=providers)
                # ★ 必须传 refresh_keys：否则"已存在的 key 一律跳过"会把这次
                #   重查结果整个丢掉 —— 界面上提示"查到 1 个候选"，磁盘上却
                #   还是原来那条，用户看到的就是"重新查找什么也没显示"
                #   （2026-10-12 他报的正是这个）。
                eng.persist_resolved(refresh_keys={key})
                # 顺手把版本也查了 —— 否则这一行只能显示"找到（还没查版本）"，
                # 用户点完还是不知道线上是什么版本。
                rows = eng.check(only_keys={key})
                self.app.msgq.put(("refind_done", {
                    "key": key,
                    "providers": sorted(providers),
                    "stats": eng.refind_stats.get(key) or {},
                    "result": (rows or [None])[0],
                }))
            except Exception as e:  # noqa: BLE001
                diag.log("来源总览重查失败：\n" + traceback.format_exc(), "ERROR")
                self.app.msgq.put(("refind_done", {"key": source_key(entry),
                                                   "error": str(e)}))

        threading.Thread(target=run, daemon=True).start()

    def _sync_head(self) -> None:
        """刷新顶部那句"本地版本 + 当前结论"。

        ★ 不照抄打开对话框时的 status（用户 2026-10-11 报的正是
        「下面显示已找到了，但是上面还是显示的没找到，是不是没做同步？」）。
        这里**直接按当前来源与版本重算**，不去依赖那个可能已经过期的 status：
          · 一条来源都没有        → 「没找到来源」
          · 有来源、但还没查过版本 → 「已找到来源（还没查版本）」
          · 有来源且版本已知       → 照实说更新情况
        """
        row = self.row
        local = str(row.get("installed") or "") or "?"
        items = self.sources or []
        if not items:
            concl = "没找到来源"
        else:
            # 有来源时看版本：能看出"有更新/已最新"就说，看不出就说"还没查版本"
            best = row.get("best") or {}
            ver = str(best.get("version") or "")
            # 从来源列表里挑一个已知版本（重查后 best 可能还没更新）
            if not ver:
                for s in items:
                    if s.get("version"):
                        ver = str(s["version"])
                        break
            if not ver:
                concl = "已找到来源（还没查版本，回列表点「检查更新」）"
            else:
                st = row.get("status")
                txt = STATUS_TEXT.get(st, ("", ""))[0]
                # status 若还停在"没找到/未检查"，以"已找到来源"为准
                if st in (ST_NO_SOURCE, ST_UNCHECKED):
                    concl = f"已找到来源（线上 {ver}）"
                else:
                    concl = f"{txt}（线上 {ver}）" if txt else f"线上 {ver}"
        self.var_head.set(f"本地装的版本：{local}　当前结论：{concl}")

    def refresh_sources(self, hint: str = "") -> None:
        """重查完成后就地刷新（由 App 通过 msgq 调用）。"""
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if hint:
            self.var_hint.set(hint)
        try:
            self.btn_refind.configure(state="normal")
        except tk.TclError:
            return
        self._fill()
        for w in self.rows_box.winfo_children():
            w.destroy()
        self._build_rows()
        # ★ 顶部结论也要重算 —— 否则"下面找到、上面还说没找到"（用户报过）
        self._sync_head()





class OptionsDialog(tk.Toplevel):
    """编辑某个模组的可选项（像 Arsenal 那样勾选）。

    要点：
    · 树形展示，父选项与子选项（``suboptions``）都列出来 —— 本次实测发现
      有的模组（Helmet Headlamp）真正的选项就写在子选项里，只列顶层会漏。
    · 父选项的勾选框与子选项**联动**：勾父 = 全开子，取消父 = 全关子；
      子选项有开有关时父显示为「部分」。
    · 只提交**用户动过的**项（对比打开时的状态），不动的字段一律不写。
    """

    def __init__(self, master, entry: dict, options: list[dict],
                 data_file: str, on_saved=None) -> None:
        super().__init__(master)
        self.title("编辑选项")
        self.configure(bg=BG)
        self.minsize(680, 460)
        self.transient(master)
        self.grab_set()

        self.entry = entry
        self.data_file = data_file
        self.on_saved = on_saved
        self._rows: list[tuple[tk.BooleanVar, dict]] = []
        self._orig: dict[str, bool] = {}
        self._key_of: dict[int, str] = {}

        head = ttk.Frame(self, padding=(14, 12, 14, 0))
        head.pack(fill="x")
        ttk.Label(head, text=f"选项：{entry.get('label') or entry.get('uuid')}",
                  style="Head.TLabel", wraplength=620).pack(anchor="w")
        ttk.Label(head,
                  text="勾选要启用的功能。改动会写回 Arsenal，并把它标记为"
                       "「需要重新部署」—— 重新部署一次才会生效。",
                  style="Dim.TLabel", wraplength=620, justify="left").pack(
            anchor="w", pady=(4, 0))

        wrap = ttk.Frame(self, padding=(14, 8))
        wrap.pack(fill="both", expand=True)
        self.canvas_like = tk.Frame(wrap, bg=BG)
        self.canvas_like.pack(fill="both", expand=True)
        self._build_tree(self.canvas_like, options, 0, "")

        self.var_info = tk.StringVar(value=self._count_text())
        ttk.Label(self, textvariable=self.var_info, style="Dim.TLabel").pack(
            anchor="w", padx=14)

        bar = ttk.Frame(self, padding=(14, 10))
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right")
        self.btn_save = ttk.Button(bar, text="保存", style="Accent.TButton",
                                   command=self._save)
        self.btn_save.pack(side="right", padx=(0, 8))

        fit_and_center(self, master, min_w=680, min_h=460)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_tree(self, parent, options: list[dict], depth: int,
                    prefix: str) -> None:
        for o in options:
            name = o.get("name") or "（未命名）"
            key = f"{prefix}{name}"
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", pady=(2 if depth == 0 else 0, 0))
            var = tk.BooleanVar(value=bool(o.get("enabled", True)))
            self._orig[key] = bool(o.get("enabled", True))
            self._key_of[id(var)] = key
            subs = o.get("suboptions") or []
            cb = ttk.Checkbutton(row, text=name, variable=var,
                                 command=lambda k=key, v=var: self._on_toggle(k, v))
            cb.pack(side="left", padx=(depth * 22, 0))
            self._rows.append((var, {"key": key, "depth": depth, "parent": prefix,
                                     "subs": subs, "var": var}))
            desc = (o.get("description") or "").strip()
            if desc:
                ttk.Label(row, text=desc[:70], style="Dim.TLabel").pack(
                    side="left", padx=(8, 0))
            if subs:
                self._build_tree(parent, subs, depth + 1, key + "/")
                # 注意：**不要在这里联动父选项** —— 初值必须原样反映库里的存储值。
                # 早期版本在这里调了 _sync_parent，结果"父开、子一开一关"的组
                # 一打开就被改写成"父关"，用户什么都没动保存却产生了改动。

    def _child_vars(self, key: str) -> list[tk.BooleanVar]:
        """直接子选项的勾选变量（只认下一层，不含孙辈）。

        子项记录的 ``parent`` 是父项的 key 加斜杠（如 ``"Mode/"``），
        顶层项的 parent 是空串 —— 这里统一补上斜杠再比。
        """
        want = key + "/"
        return [var for var, meta in self._rows if meta["parent"] == want]

    def _on_toggle(self, key: str, var: tk.BooleanVar) -> None:
        """勾/取消父选项时联动它的直接子选项。"""
        kids = self._child_vars(key)
        for c in kids:
            c.set(var.get())
        self._sync_all_parents()
        self.var_info.set(self._count_text())

    def _sync_parent(self, key: str) -> None:
        kids = self._child_vars(key)
        if not kids:
            return
        for var, meta in self._rows:
            if meta["key"] == key:
                states = {c.get() for c in kids}
                if states == {True}:
                    var.set(True)
                elif states == {False}:
                    var.set(False)
                else:
                    var.set(False)   # 部分开 → 不整体算开
                break

    def _sync_all_parents(self) -> None:
        # 只看有子项的键
        for var, meta in list(self._rows):
            if meta["subs"]:
                self._sync_parent(meta["key"])

    def _count_text(self) -> str:
        total = len(self._rows)
        off = sum(1 for var, _m in self._rows if not var.get())
        if off == 0:
            return f"共 {total} 个选项，全部启用。"
        return f"共 {total} 个选项，{off} 个已关闭。"

    def _changed(self) -> dict:
        out = {}
        for var, meta in self._rows:
            k = meta["key"]
            if k in self._orig and var.get() != self._orig[k]:
                out[k] = var.get()
        return out

    def _save(self) -> None:
        changes = self._changed()
        if not changes:
            self.destroy()
            return
        try:
            res = set_options(self.data_file, self.entry["uuid"], changes)
        except SyncError as e:
            error_box("保存失败", str(e), parent=self, app=self.master_app,
                      context="保存模组选项时被同步层拒绝")
            return
        except Exception as e:  # noqa: BLE001
            diag.log("保存选项时出错：\n" + traceback.format_exc(), "ERROR")
            error_box("保存失败", f"保存选项时出错：\n\n{e}", parent=self,
                      app=self.master_app, context="保存模组选项时发生意外错误")
            return
        if res.get("missing"):
            messagebox.showwarning(
                "部分选项没找到",
                "这些选项在库里找不到，已跳过：\n  " + "\n  ".join(res["missing"]),
                parent=self)
        if self.on_saved:
            try:
                self.on_saved(res.get("applied", 0))
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


class DeployDialog(tk.Toplevel):
    """对话框外壳 —— 内容就是 DeployPanel，保留它便于单独弹出。"""

    def __init__(self, master: App) -> None:
        super().__init__(master)
        self.title("部署模组到游戏")
        self.configure(bg=BG)
        self.minsize(700, 480)
        self.transient(master)
        self.panel = DeployPanel(self, master)
        self.panel.pack(fill="both", expand=True)
        bar = ttk.Frame(self, padding=(14, 6, 14, 10))
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="关闭", command=self.destroy).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def __getattr__(self, name: str):
        # 让外部（含既有测试）仍能像以前那样访问面板上的属性
        panel = self.__dict__.get("panel")
        if panel is not None:
            return getattr(panel, name)
        raise AttributeError(name)


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
            # 走 ayaka_url()/ayaka_decode()：存下来的 id 是百分号编码形态
            # （如 %E6%9C%BA%E7%94%B2....4775）。直接回填编码串会让用户看到
            # 一坨 %E6%9C 而以为自己粘错了 —— README 明确承诺过"中文地址
            # 直接粘就行，不用自己转码"，这里回显**解码后的中文**才一致。
            # （保存时程序会自己再编码，用户看到什么就存什么。）
            return f"{providers.AYAKA_BASE}/mods/{providers.ayaka_decode(ident)}/"
        return ""

    def _detect(self, text: str) -> dict | None:
        """从网址判断是哪个站点，返回 {'provider','id','url','display'}；认不出返回 None。

        真正的判断逻辑放在 ``providers.detect_source()`` —— **GUI 和命令行共用
        一份**，也方便写离线测试（这里只是薄薄一层转发）。
        """
        return providers.detect_source(text)

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
            # 显示**解码后**的地址：中文 mod 的地址在源码里是 %E6%9C... 一串，
            # 直接回显会让人以为自己粘错了。
            show = got.get("display") or got["url"]
            self.lbl_detect.configure(
                text=f"✓ 识别为 {name}　（{show}）", foreground=GREEN)
            self.btn_verify.configure(state="normal")
        else:
            self.lbl_detect.configure(
                text="✗ 认不出这是哪个站点的链接。请确认是 "
                     "github.com / nexusmods.com / ayakamods.com 的模组页面地址"
                     "（ayakamods 的中文名地址也可以直接粘）。",
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
                "请贴这三个站点的模组页面地址（不是下载直链、也不是列表页）：\n\n"
                "· github.com/作者/仓库\n"
                "· nexusmods.com/helldivers2/mods/数字\n"
                "· ayakamods.com/mods/xxx.数字/\n\n"
                "最省事的做法：在浏览器打开那个模组的页面，\n"
                "复制地址栏整条链接粘过来。\n\n"
                "（ayakamods 的中文/特殊符号地址也能直接粘，程序认得。）",
                parent=self,
            )
            return

        set_manual_source(self.entry_data, {
            k: v for k, v in got.items() if k != "display"})   # 打 manual 标记，受保护
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

        # ★ 内容区可滚动（用户要求：「设置页面现在太长了，显示不完全，
        #   改成可以上下滚动的那种吧」）。
        #   ★ 注意 pack 顺序：底部按钮**先**用 side="bottom" 占位，滚动区再用
        #   side="top" 吃掉剩余空间。反过来写的话 expand 会把按钮挤出窗口
        #   （实测：按钮 y=0，看不见了 —— 正是用户抱怨的"显示不完全"）。
        btns = ttk.Frame(self)
        btns.pack(side="bottom", fill="x", padx=16, pady=(10, 14))
        ttk.Button(btns, text="全部保存并关闭", style="Accent.TButton",
                   command=self._save_and_close).pack(side="right")
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="清空来源缓存（保留手工指定的）",
                   command=self._clear).pack(side="left")

        sc = ScrollFrame(self)
        sc.pack(side="top", fill="both", expand=True, pady=(4, 0))
        body = sc.inner
        # ★ 存到 self 上：_section() 内部要用它当父容器（那里不能写死 self，
        #   否则那两块会跑到滚动区外面 —— "上半能滚、下半滚不动"就是这个原因）。
        self.body = body
        self.scroll = sc

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
        dlbox = ttk.LabelFrame(body, text="下载位置", padding=(10, 6))
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
        dbox = ttk.LabelFrame(body, text="数据位置", padding=(10, 6))
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

        # ---------------- 备份保留 ----------------
        # 为什么需要这一段：备份原来只增不减。实测用户机器上游戏目录的部署备份
        # 攒到 1.63 GB（每份约 230 MB，每次部署再加一份）。这里让用户自己定留几份。
        bk = ttk.LabelFrame(body, text="备份保留（在「备份」页可以查看和手动删）",
                            padding=(10, 6))
        bk.pack(fill="x", padx=16, pady=(14, 0))
        self._bk_vars: dict[str, tk.StringVar] = {}
        rows = (
            ("keep_game_backups", "游戏目录的部署备份",
             "每份约 230 MB。出错时靠它退回上一次部署前的状态。"),
            ("keep_old_mod_dirs", "模组旧版本文件夹（每个模组）",
             "覆盖导入更新时留下的旧内容，按模组分别计数。"),
            ("keep_data_baks", "Arsenal 库/配置备份",
             "每份约 100 KB。★ 最早的那一份永远保留（那是库的原始状态）。"),
        )
        cur = backups.retention()
        for key, title, tip in rows:
            lo, hi = backups.LIMITS[key]
            r = ttk.Frame(bk)
            r.pack(fill="x", pady=2)
            ttk.Label(r, text=title, width=26).pack(side="left")
            var = tk.StringVar(value=str(cur[key]))
            self._bk_vars[key] = var
            sp = ttk.Spinbox(r, from_=lo, to=hi, width=5, textvariable=var)
            sp.pack(side="left")
            ttk.Label(r, text=f"份（{lo}~{hi}）", style="Dim.TLabel").pack(
                side="left", padx=(4, 10))
            ttk.Label(r, text=tip, style="Dim.TLabel").pack(side="left")
        b_now = ttk.Button(bk, text="立即按上面的份数清理一次",
                           command=self._prune_now)
        b_now.pack(anchor="w", pady=(6, 0))
        Tooltip(b_now, "先保存设置再清理。只删我们自己产生的备份，"
                       "不动 Arsenal 自己的备份、也不动游戏原生文件。")

        # ---------------- 维护 ----------------
        maint = ttk.LabelFrame(body, text="维护", padding=(10, 6))
        maint.pack(fill="x", padx=16, pady=(14, 0))
        b_idx = ttk.Button(maint, text="重建站点索引",
                           command=self._rebuild_index_now)
        b_idx.pack(side="left")
        Tooltip(b_idx, HOVER_TIPS["btn_index"])
        # 显示当前索引状态（含"每 7 天自动更新"的说明）。
        # ★ 用 StringVar 而不是写死的文字：重建完要能就地刷新，
        #   否则用户点完重建、这里还写着"1 天前更新"，看起来像没生效
        #   （2026-10-11 用户就是这么发现的）。
        self.var_index = tk.StringVar(value=self._index_status_line())
        ttk.Label(maint, textvariable=self.var_index,
                  style="Dim.TLabel", justify="left").pack(side="left", padx=(10, 0))

        # ---------------- 反馈与日志 ----------------
        # 用户需求（2026-10-11）：「让用户更方便反馈问题，让我也更方便解决
        # 用户反馈的问题」。这里是三个入口里的第三个（另两个：侧边栏的
        # 「反馈 / 帮助」页、每个报错弹窗上的「生成反馈信息」）。
        fbox = ttk.LabelFrame(body, text="反馈与日志", padding=(10, 6))
        fbox.pack(fill="x", padx=16, pady=(14, 0))
        self.var_log_on = tk.BooleanVar(value=bool(diag.log_path()))
        cb_log = ttk.Checkbutton(fbox, text="记录日志（排查问题用，保留最近 7 天）",
                                 variable=self.var_log_on, command=self._toggle_log)
        cb_log.pack(anchor="w")
        Tooltip(cb_log, "勾上后，工具会把每次检查 / 下载 / 导入 / 部署的关键步骤"
                        "写进日志文件。\n出问题时把日志（或「生成反馈信息」的内容）"
                        "发出来，我就能直接定位，不用来回问你。\n默认开启，随时可以关。")
        self.lbl_log = ttk.Label(fbox, text="", style="Dim.TLabel",
                                 wraplength=660, justify="left")
        self.lbl_log.pack(anchor="w", pady=(2, 4))
        frow = ttk.Frame(fbox)
        frow.pack(fill="x")
        ttk.Button(frow, text="生成反馈信息…",
                   command=self._open_feedback).pack(side="left")
        ttk.Button(frow, text="打开日志文件夹",
                   command=self._open_log_dir).pack(side="left", padx=(6, 0))
        self._sync_log_label()

        # 宽度按内容自适应，**高度封顶** —— 内容再长也不会顶出屏幕，
        # 超出的部分靠上面的滚动区看。min_h 给个下限，别缩成一条缝。
        # （底部按钮已在上面用 side="bottom" 占好位，这里只管尺寸。）
        fit_and_center(self, master, min_w=700, min_h=520, max_h=720)
        # 既然内容能滚了，就允许用户自己拉大窗口（以前写死不可缩放）
        self.resizable(True, True)
        self.minsize(700, 420)

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

        ★ 父容器取 `self.body`（滚动区），**不能写死 self** —— 写死的话
        这两块（N网 key / GitHub token）就挂在对话框本体上、跑到滚动区
        外面去了，表现为"只有上半能滚，下面填 API 的地方滚不动"
        （用户 2026-10-11 报的正是这个）。
        """
        box = ttk.Frame(self.body)
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
        """保存两个 key + 备份保留份数（每行自己的「保存」按钮也走这里）。"""
        write_config({
            "nexus_api_key": self.master_app.var_nexus_key.get().strip(),
            "github_token": self.master_app.var_github_token.get().strip(),
        })
        # 刚填的 key/token 立刻纳入脱敏表 —— 否则接下来 30 秒内的日志
        # 里还可能带着明文（脱敏表有缓存）。
        diag.refresh_secrets()
        # 备份保留份数（顺带夹到合法范围）
        try:
            vals = {k: v.get() for k, v in (self._bk_vars or {}).items()}
            if vals:
                got = backups.save_retention(vals)
                # 回填夹过的值，让用户看到实际生效的数
                for k, v in (self._bk_vars or {}).items():
                    v.set(str(got.get(k, v.get())))
        except Exception:  # noqa: BLE001
            pass
        nk = "已填" if self.master_app.var_nexus_key.get().strip() else "空"
        gk = "已填" if self.master_app.var_github_token.get().strip() else "空"
        diag.event("保存设置", f"N网 key：{nk}，GitHub token：{gk}")
        self.master_app.var_status.set(
            f"设置已保存（N网 key：{nk}　GitHub token：{gk}）——下次检查即生效"
        )

    # ---- 反馈与日志 ----
    def _sync_log_label(self) -> None:
        p = diag.log_path()
        self.lbl_log.configure(
            text=(f"当前日志：{p}" if p
                  else f"日志当前关着（{diag.disabled_reason() or '未开启'}）"))

    def _toggle_log(self) -> None:
        p = diag.set_enabled(bool(self.var_log_on.get()))
        diag.event("日志", "已开启" if p else "已关闭")
        self.var_log_on.set(bool(p))
        self._sync_log_label()
        self.master_app.var_status.set("日志已开启" if p else "日志已关闭")

    # ---- 维护 ----
    @staticmethod
    def _index_status_line() -> str:
        """设置页里那行索引状态（读的是当前真实状态，不缓存）。"""
        try:
            status = matcher.index_status_text()
        except Exception as e:  # noqa: BLE001
            return f"读取状态出错：{e}"
        return f"当前状态：{status}\n平时不用点 —— 过期会自动重建。"

    def _rebuild_index_now(self) -> None:
        """点「重建站点索引」：重建 + 完事后就地把状态刷新出来。

        为什么要就地刷新：以前重建完，设置页这行还是打开窗口时的旧文字
        （"1 天前更新"），用户会以为按钮没生效（2026-10-11 实际发生）。
        """
        self.master_app._rebuild_index()
        # 抓取要 1~2 分钟，先挂个"进行中"，等真结束了再刷成真实状态
        self.var_index.set("当前状态：正在重建…（约 1~2 分钟，完成后这里会自动刷新）")
        self._watch_index_done(0)

    def _watch_index_done(self, tick: int) -> None:
        """等后台抓取结束，然后把状态刷成真实的。"""
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        busy = bool(getattr(matcher, "_crawl_state", {}).get("running"))
        if not busy or tick > 600:          # 600 * 500ms = 5 分钟上限
            self.var_index.set(self._index_status_line())
            return
        self.after(500, lambda: self._watch_index_done(tick + 1))

    def _open_feedback(self) -> None:
        dlg = FeedbackDialog(self, self.master_app)
        self.master_app._last_feedback_dialog = dlg

    def _open_log_dir(self) -> None:
        d = diag.logs_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606
        except OSError as e:
            messagebox.showinfo("日志文件夹", f"{d}\n\n（打不开：{e}）", parent=self)

    def _prune_now(self) -> None:
        """按当前填的份数清理一次（先保存设置，再清理）。"""
        self._save()
        try:
            ctx = self.master_app.backup_context()
            rep = backups.prune(game_dir=ctx["game_dir"], mods_dir=ctx["mods_dir"],
                                data_file=ctx["data_file"],
                                skip_mods=ctx["arsenal_running"])
            n = len(rep["deleted"])
            mb = rep["freed_bytes"] / 1048576
            if not n:
                messagebox.showinfo("没有要清理的",
                                    "当前备份数量没超过你设置的份数，不用清理。",
                                    parent=self)
                return
            messagebox.showinfo(
                "清理完成",
                f"删掉了 {n} 项旧备份，释放约 {mb:.1f} MB。\n\n"
                + ("（Arsenal 正开着，模组目录里的旧版本文件夹这次没动。）"
                   if ctx["arsenal_running"] else ""),
                parent=self)
        except Exception as e:  # noqa: BLE001
            error_box("清理失败", str(e), parent=self, app=self.master_app,
                      context="按保留份数清理旧备份时失败")

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
            "请选中你的浏览器实际下载到的那个文件夹 —— "
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
                "这些文件在目标目录里已经存在，继续会覆盖它们：\n\n  "
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
                    progress=self.master_app._log,
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
            msg += (f"\n有 {manual} 条是你手工指定的，会被保留，不会删除。\n")
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
