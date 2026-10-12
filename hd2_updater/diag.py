# -*- coding: utf-8 -*-
"""诊断与日志 —— 把「用户遇到了什么」变成「我能看懂、能复现的现场」。

为什么要这个模块（用户原话 2026-10-11）：
「让用户更方便反馈问题，让我也更方便解决用户反馈的问题」。

起因是一次真实的反馈：小黑盒用户只说「很多 mod 搜不到」+ 两张截图，
而工具**没有任何取证手段** —— 没有日志文件、没有全局异常捕获，
错误弹窗一关就什么都不剩（打包成 EXE 后连控制台都没有，后台线程的
traceback 只 print 到不存在的地方）。于是只能靠来回追问。

本模块提供三件事：
  1. 日志文件：``<数据目录>\\logs\\hd2u-<日期>.log``
     默认开、保留 7 天、单文件超过 2MB 自动轮转。
     ★ 脱敏是硬要求：Nexus key / GitHub token **永不落盘**。
  2. 操作时间线：最近 200 条「用户做了什么、结果如何」（内存里）。
     用户说不清「我点了什么」时，这份时间线就是现场。
  3. 诊断包：把环境、模组库概况、网络自检、时间线拼成一段可以直接
     粘贴的文本（长版）+ 一段适合截图的短摘要。

设计约束：
  · **永不抛异常** —— 记日志失败绝不能影响主流程。
  · 不联网、不上传：诊断文本只交给用户，由他自己贴到 GitHub / 小黑盒。
  · 测试护栏：设了 ``HD2U_READONLY_GUARD=1`` 时只允许写临时目录。
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from collections import deque
from datetime import datetime

from . import util

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
LOG_DIRNAME = "logs"
LOG_PREFIX = "hd2u-"
LOG_KEEP_DAYS = 7
LOG_MAX_BYTES = 2 * 1024 * 1024        # 单个日志文件上限，超了就轮转
TIMELINE_MAX = 200                     # 内存里保留的操作条数
REPO_URL = "https://github.com/MiaoMiaoChongChong/HD2ModUpdater"
ISSUES_URL = REPO_URL + "/issues/new"

# 网络自检用的站点。
#
# ★★ 铁律：**自检必须探"工具实际用的那条路"**，否则会报假警。
# 踩过的坑（2026-10-11 用户报「我的N网似乎一直都是显示能连上但是被挡？
# 明明更新里确实能查到是最新」）：N 网有两套完全不同的入口 ——
#   · `www.nexusmods.com`（网页版）：吃 Cloudflare 挑战，脚本一律 403；
#   · `api.nexusmods.com`（API）：**工具查更新真正走的路**，正常返回。
# 而自检原来探的是网页版 → 永远显示"被挡"，可查更新一切正常，纯属误导。
# 所以这里按"工具实际用哪个入口"来定：
#   · N 网 → 官方 API（并带上用户自己的 key）
#   · GitHub → API
#   · a 站 → 没有 API，只能爬网页；**探列表页**（重建索引走的就是它），
#     而不是首页 —— 首页通不代表爬取那条路通。
SITES = (
    ("GitHub", "https://api.github.com/rate_limit", "api"),
    ("Nexus Mods", "https://api.nexusmods.com/v1/games/helldivers2.json", "api"),
    ("ayakamods",
     "https://ayakamods.com/mods/games/helldivers-2.119/", "web"),
)

MASK = "***"
USER_MASK = "<用户>"

_lock = threading.RLock()
_enabled = True
_ready = False
_log_file = ""
_disabled_reason = ""
_timeline: deque = deque(maxlen=TIMELINE_MAX)
_secrets: list[str] = []
_secrets_at = 0.0
_user_names_cache: list[str] | None = None

# 兜底正则（就算拿不到 config 里的明文，也要把常见凭据形态盖掉）
_RE_GH_PAT = re.compile(r"github_pat_[A-Za-z0-9_]{10,}")
_RE_GH_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")
_RE_QUERY_SECRET = re.compile(r"(?i)([?&](?:key|apikey|api_key|token|access_token)=)[^&\s\"']+")
_RE_BEARER = re.compile(r"(?i)((?:authorization|bearer)\s*[:=]?\s*(?:bearer\s+)?)[A-Za-z0-9_\-\.]{16,}")
# 请求头形态的 key（如 ``apikey: xxx`` / ``x-api-key = xxx``）——
# 万一哪天有人把 headers 打进日志，这一道能兜住。
_RE_HEADER_KEY = re.compile(
    r"(?i)((?:x[-_])?api[-_]?key\s*['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9_\-\.]{8,}")


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------
def _load_secrets(force: bool = False) -> list[str]:
    """从 config.json 里取需要脱敏的凭据明文（缓存 30 秒）。

    精确替换比正则可靠：我们知道用户实际填了什么，就能保证它不出现在
    任何输出里。正则只是"万一"的第二道防线。
    """
    global _secrets, _secrets_at
    now = time.time()
    with _lock:
        if not force and _secrets and now - _secrets_at < 30:
            return list(_secrets)
    found: list[str] = []
    try:
        cfg = util.read_tool_config()
        for key in ("nexus_api_key", "github_token"):
            v = str(cfg.get(key) or "").strip()
            if len(v) >= 6:
                found.append(v)
    except Exception:  # noqa: BLE001
        found = []
    with _lock:
        _secrets = found
        _secrets_at = now
    return list(found)


def _user_names() -> list[str]:
    """当前 Windows 用户名（用于把路径里的用户名打码）。"""
    global _user_names_cache
    if _user_names_cache is not None:
        return _user_names_cache
    out: list[str] = []
    for key in ("USERNAME", "USER"):
        v = os.environ.get(key) or ""
        v = v.strip()
        if v and v not in out:
            out.append(v)
    try:
        base = os.path.basename(os.path.expanduser("~").rstrip("\\/"))
        if base and base not in out:
            out.append(base)
    except Exception:  # noqa: BLE001
        pass
    _user_names_cache = out
    return out


def refresh_secrets() -> None:
    """让脱敏立刻读到最新的凭据（用户刚在设置里填完 key 时调用）。

    平时 _load_secrets 有 30 秒缓存 —— 但那意味着"用户刚填的 key"
    可能在这 30 秒里还没进入脱敏表。设置保存时主动刷一次最稳。
    """
    _load_secrets(force=True)


def redact(text: object, hide_user: bool = True) -> str:
    """把凭据（和可选的用户名）从文本里抹掉。

    hide_user=True 时 ``C:\\Users\\张三\\...`` 会变成 ``C:\\Users\\<用户>\\...``
    —— 盘符和目录结构保留（对定位问题有用），只去掉能指认到人的那一节。
    """
    s = str(text if text is not None else "")
    if not s:
        return s
    for sec in _load_secrets():
        if sec and sec in s:
            s = s.replace(sec, MASK)
    s = _RE_GH_PAT.sub(MASK, s)
    s = _RE_GH_TOKEN.sub(MASK, s)
    s = _RE_QUERY_SECRET.sub(lambda m: m.group(1) + MASK, s)
    s = _RE_BEARER.sub(lambda m: m.group(1) + MASK, s)
    s = _RE_HEADER_KEY.sub(lambda m: m.group(1) + MASK, s)
    if hide_user:
        for name in _user_names():
            if len(name) >= 3 and name.lower() in s.lower():
                s = re.sub(re.escape(name), USER_MASK, s, flags=re.I)
    return s


# --------------------------------------------------------------------------
# 日志文件
# --------------------------------------------------------------------------
def logs_dir() -> str:
    """日志目录（数据目录下的 logs\\）。"""
    return os.path.join(util.app_data_dir(), LOG_DIRNAME)


def log_path() -> str:
    """当前日志文件路径（未启用时返回空串）。"""
    return _log_file


def enabled() -> bool:
    return bool(_enabled and _log_file)


def disabled_reason() -> str:
    """日志没开的原因（给界面/诊断用）。"""
    return _disabled_reason


def _guard_ok(path: str) -> bool:
    """测试护栏：设了 HD2U_READONLY_GUARD=1 时只允许写临时目录。

    与 arsenal_sync 里那道护栏同一个思路 —— 结构性保证"测试不可能写到
    用户真实的数据目录"。这里刻意不 import arsenal_sync（避免循环依赖），
    所以逻辑单独实现一份。
    """
    if not os.environ.get("HD2U_READONLY_GUARD"):
        return True
    roots = [tempfile.gettempdir()]
    for key in ("TEMP", "TMP", "TMPDIR", "HD2U_DATA_DIR"):
        v = os.environ.get(key)
        if v:
            roots.append(v)
    try:
        real = os.path.realpath(path).lower()
    except OSError:
        real = os.path.abspath(path).lower()
    for r in roots:
        if r and real.startswith(os.path.realpath(r).lower()):
            return True
    return False


def setup(log_dir: str = "", on_log: bool | None = None) -> str:
    """初始化日志（可重复调用）。返回日志文件路径，没开则返回空串。

    on_log=None 时读 config.json 的 ``log_enabled``（默认 True）。
    任何一步失败都只是"没有日志"，绝不影响程序运行。
    """
    global _enabled, _ready, _log_file, _disabled_reason
    with _lock:
        if on_log is None:
            want = True
            try:
                v = util.read_tool_config().get("log_enabled", True)
                want = v if isinstance(v, bool) else True
            except Exception:  # noqa: BLE001
                want = True
        else:
            want = bool(on_log)
        _ready = True
        _log_file = ""
        _disabled_reason = ""
        if os.environ.get("HD2U_NO_LOG"):
            _enabled = False
            _disabled_reason = "环境变量 HD2U_NO_LOG 已关闭日志"
            return ""
        if not want:
            _enabled = False
            _disabled_reason = "用户在设置里关掉了日志"
            return ""
        _enabled = True
        d = log_dir or logs_dir()
        path = os.path.join(d, f"{LOG_PREFIX}{datetime.now():%Y%m%d}.log")
        if not _guard_ok(path):
            _enabled = False
            _disabled_reason = "测试护栏：只允许写临时目录"
            return ""
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            _enabled = False
            _disabled_reason = f"建不了日志目录（{d}）：{e}"
            return ""
        _log_file = path
    prune_logs()
    return _log_file


def set_enabled(v: bool) -> str:
    """开关日志（写回 config.json，下次启动也生效）。返回新的日志路径。"""
    try:
        from . import arsenal
        arsenal.write_config({"log_enabled": bool(v)})
    except Exception:  # noqa: BLE001
        pass
    return setup(on_log=bool(v))


def _rotate(path: str) -> None:
    """把超限的日志挪成 ``*.1.log``（只留一代，够了）。"""
    base, ext = os.path.splitext(path)
    try:
        os.replace(path, base + ".1" + ext)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass


def _write(line: str) -> None:
    """写一行（内部用，**永不抛异常**）。"""
    with _lock:
        path = _log_file
        on = _enabled
    if not on or not path:
        return
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            _rotate(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def log(msg: object, level: str = "INFO") -> None:
    """记一行日志（自动脱敏）。"""
    text = str(msg if msg is not None else "")
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{level}] {redact(text)}"
    _write(line)


def event(what: str, detail: str = "") -> None:
    """记一条「用户做了什么 / 结果如何」。

    同时进日志和内存时间线 —— 时间线就是给诊断包用的现场记录。
    """
    text = str(what) + (f"：{detail}" if detail else "")
    stamp = datetime.now().strftime("%H:%M:%S")
    with _lock:
        _timeline.append(f"[{stamp}] {redact(text)}")
    log(text, "EVENT")


def timeline_lines() -> list[str]:
    """取一份时间线副本。"""
    with _lock:
        return list(_timeline)


def clear_timeline() -> None:
    with _lock:
        _timeline.clear()


def prune_logs() -> list[str]:
    """删掉超过保留天数的旧日志，返回删掉的文件名。"""
    d = logs_dir()
    if not os.path.isdir(d):
        return []
    cutoff = time.time() - LOG_KEEP_DAYS * 86400
    gone: list[str] = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for name in names:
        if not (name.startswith(LOG_PREFIX) and name.endswith(".log")):
            continue
        p = os.path.join(d, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
                gone.append(name)
        except OSError:
            pass
    return gone


def log_tail(lines: int = 200) -> str:
    """读日志文件最后若干行（给诊断包用）。"""
    path = _log_file
    if not path or not os.path.isfile(path):
        return "（没有日志文件）"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.readlines()
    except OSError as e:
        return f"（读日志失败：{e}）"
    return "".join(data[-max(1, lines):]).rstrip("\n")


# --------------------------------------------------------------------------
# 环境快照
# --------------------------------------------------------------------------
def _fingerprint(path: str) -> str:
    """库文件指纹：大小 + SHA256 前 16 位 + 最后修改时间。

    ★ 为什么用 SHA256 而不是只看修改时间：Arsenal 自己也会重写库并留
    备份，光看 mtime 会误判"库被人动过"。要比内容。
    """
    try:
        st = os.stat(path)
    except OSError as e:
        return f"读不到：{e}"
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as e:
        return f"读不到：{e}"
    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return (f"SHA256 {h.hexdigest()[:16]}…，{util.human_size(st.st_size)}，"
            f"最后修改 {mtime}")


def environment() -> dict:
    """环境快照（一眼排除掉一半问题）。任何一项失败都不影响其余。"""
    from . import APP_NAME, __version__

    info: dict = {}
    frozen = bool(getattr(sys, "frozen", False))
    info["工具版本"] = f"{APP_NAME} v{__version__}"
    info["运行方式"] = ("打包 EXE（免装 Python）" if frozen
                        else f"源码运行（Python {platform.python_version()}）")
    info["系统"] = (f"{platform.system()} {platform.release()}"
                    f"（build {platform.version()}，{platform.machine()}）")
    try:
        d = util.app_data_dir()
        info["数据目录"] = d + ("（自定义位置）" if util.is_custom_data_dir() else "（默认位置）")
    except Exception as e:  # noqa: BLE001
        info["数据目录"] = f"取不到：{e}"
    info["日志文件"] = _log_file or ("未启用（" + (_disabled_reason or "未知原因") + "）")
    try:
        from . import arsenal
        p, how = arsenal.find_data_file()
        if p:
            info["Arsenal 模组库"] = p
            info["库文件指纹"] = _fingerprint(p)
        else:
            info["Arsenal 模组库"] = f"没找到（{how}）"
    except Exception as e:  # noqa: BLE001
        info["Arsenal 模组库"] = f"读取出错：{type(e).__name__}: {e}"
    try:
        from . import arsenal_sync
        info["Arsenal 是否正在运行"] = "是（此时不要写它的数据）" if arsenal_sync.arsenal_running() else "否"
    except Exception as e:  # noqa: BLE001
        info["Arsenal 是否正在运行"] = f"检测失败：{type(e).__name__}: {e}"
    return info


def library_overview() -> dict:
    """模组库概况 + 每个模组的来源线索。

    这一段专门针对最高频的反馈类型：「很多 mod 搜不到」。
    刻意**不重跑识别**（那要发一堆网络请求、还可能改变现场），只把
    「判断来源所需的原始线索」摊开：目录名、manifest 里作者写的来源、
    Arsenal 的 nexusData、猜出的版本。有这些就足以判断为什么搜不到。
    """
    out: dict = {"ok": False, "error": "", "total": 0, "enabled": 0,
                 "deployed": 0, "game_dir": "", "mods_dir": "", "rows": []}
    try:
        from . import arsenal
        p, how = arsenal.find_data_file()
        if not p:
            out["error"] = f"没找到 Arsenal 模组库（{how}）"
            return out
        lib = arsenal.load_library(p)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    out["ok"] = True
    out["total"] = len(lib)
    out["enabled"] = sum(1 for e in lib if e.get("enabled"))
    out["deployed"] = sum(1 for e in lib if e.get("deployed"))
    if lib:
        out["game_dir"] = lib[0].get("game_dir") or ""
        out["mods_dir"] = lib[0].get("mods_dir") or ""
    rows = []
    for e in lib:
        try:
            ms = arsenal.read_manifest_sources(e)
        except Exception:  # noqa: BLE001
            ms = []
        # ★ 版本来源要说清是哪一种（issue #1：反馈者就是靠「手动填的」这五个字
        #   发现自己 30 个模组里 17 个被冻住）。三种：
        #     手动填的（还在生效） / 自动猜的 / 手动填的·已失效（内容变过了）
        if e.get("override_stale"):
            vsrc = f"手动填的，但**已自动失效**（你填的 {e.get('manual_version') or '?'}）"
        elif e.get("version_is_manual"):
            vsrc = "手动填的" + (
                "（还没核对过内容）" if "旧格式" in str(e.get("manual_state") or "")
                else "")
        else:
            vsrc = "自动猜的"
        rows.append({
            "label": e.get("label") or "（没有名字）",
            "dirname": e.get("dirname") or "",
            "uuid": e.get("uuid") or "",
            "version": e.get("installed_version") or "",
            "auto_version": e.get("auto_version") or "",
            "version_src": vsrc,
            "variant": e.get("variant") or "",
            "nexus_id": e.get("nexus_mod_id") or "",
            "enabled": bool(e.get("enabled")),
            "deployed": bool(e.get("deployed")),
            "manifest": ", ".join(
                f"{m.get('provider')}:{m.get('id') or m.get('url') or '?'}" for m in ms
            ),
        })
    out["rows"] = rows
    return out


# --------------------------------------------------------------------------
# 网络自检
# --------------------------------------------------------------------------
def _proxy_text() -> str:
    """当前**实际生效**的代理（只报地址，不含任何凭据）。

    为什么要说清"实际生效"：用户会问「检测到我 VPN 的端口，那别人端口不一样
    也能用吗？」。答案是能 —— 工具不写死端口，代理来自
    ``urllib.request.getproxies()``（Windows 上它会读系统代理设置，所以
    任何 VPN 客户端的端口都能被采用）。这里把**实际取到的那条**显示出来，
    用户一眼就能确认"工具确实走了我的代理"。

    ftp 那一路跟本工具无关（只发 HTTP/HTTPS），去掉噪音；
    三路指向同一个地址时只显示一次。
    """
    raw: list[str] = []
    try:
        got = urllib.request.getproxies()
    except Exception:  # noqa: BLE001
        got = {}
    for k in ("http", "https"):
        v = str(got.get(k) or "").strip()
        if v:
            raw.append(v)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        v = str(os.environ.get(k) or os.environ.get(k.lower()) or "").strip()
        if v:
            raw.append(v)
    seen: set[str] = set()
    uniq: list[str] = []
    for it in raw:
        if it.lower() not in seen:
            seen.add(it.lower())
            uniq.append(it)
    if not uniq:
        return "没有走代理（直连）"
    if len(uniq) == 1:
        return f"走代理 {uniq[0]}（工具实际用的出口）"
    return "走代理 " + "、".join(uniq) + "（工具实际用的出口）"


def network_check(timeout: int = 8) -> dict:
    """三个站点的连通性 + 代理设置 + **是否被 Cloudflare 挑战**。

    ★★ 最重要的一条：**探的必须是"工具实际用的那条路"**。
    N 网有两套入口：`www.nexusmods.com`（网页，吃 Cloudflare，脚本必被挡）
    和 `api.nexusmods.com`（API，**查更新真正走的**）。2026-10-11 用户报
    「N网一直显示能连上但是被挡，可更新里确实能查到最新」—— 就是自检探错
    入口造成的假警。所以现在 N 网探 API、并带上用户自己的 key。

    判据（这一条也容易搞错，实测踩过）：
      · 拿到**任何 HTTP 响应**（200 / 401 / 403 …）= 网络是通的。
      · **401 对 API 是"通了但没给凭据"**，不是故障（用户没填 key 时就是这样）。
      · 只有超时 / DNS 失败 / 连接被拒 = 真的不通。
      · 被 Cloudflare 挡住（`blocked="cf"`）单独标出来 —— 那是"站点要求能跑 JS
        的浏览器才放行"，浏览器能开、工具会被挡，既不是网络故障也不是登录过期。
    """
    out: dict = {"proxy": _proxy_text(), "sites": [], "when": util.now_str()}
    try:
        key = str(util.read_tool_config().get("nexus_api_key") or "").strip()
    except Exception:  # noqa: BLE001
        key = ""

    for name, url, kind in SITES:
        t0 = time.monotonic()
        ok = False
        detail = ""
        blocked = ""
        headers = None
        if kind == "api" and name.startswith("Nexus") and len(key) >= 6:
            headers = {"apikey": key, "Accept": "application/json"}
        try:
            util.http_get(url, timeout=timeout, retries=1, headers=headers)
            ok, detail = True, "正常"
        except Exception as e:  # noqa: BLE001
            raw = str(e)
            m = re.search(r"HTTP (\d{3})", raw)
            if m:
                code = m.group(1)
                ok = True                      # 拿到响应 = 网络通
                if util.is_cf_block(raw):
                    blocked = "cf"
                    detail = (f"能连上，但被 Cloudflare 挡在门外（HTTP {code}）"
                              f"—— 站点要求能跑 JS 的浏览器才放行，"
                              f"所以浏览器能开、工具会被挡")
                elif code == "401":
                    # API 回 401 = 网络没问题，只是没给凭据。
                    # ★ 绝不能把它算成"不通"，那会让用户去查网络。
                    if name.startswith("Nexus") and kind == "api":
                        detail = ("能连上（API 返回 401：没填 N网 key，"
                                  "填上就能查 N 网更新）")
                    else:
                        detail = "能连上，但需要凭据（HTTP 401）"
                elif code == "403":
                    detail = f"能连上，站点回了 HTTP 403（可能需要登录或没权限）"
                else:
                    detail = f"能连上，站点回了 HTTP {code}"
            else:
                detail = util.short_err(raw, 90)
        out["sites"].append({
            "name": name, "url": url, "ok": ok, "detail": detail,
            "blocked": blocked, "kind": kind,
            "sec": round(time.monotonic() - t0, 1),
        })
    return out


# --------------------------------------------------------------------------
# 诊断文本
# --------------------------------------------------------------------------
def _fmt_env(info: dict) -> list[str]:
    return [f"{k}：{v}" for k, v in info.items()]


def _fmt_net(net: dict | None) -> list[str]:
    if not net:
        return ["（没有检测网络 —— 点「重新检测网络」可以得到结果）"]
    lines = [f"检测时间：{net.get('when', '?')}",
             f"代理：{net.get('proxy', '?')}",
             "说明：这里探的是工具实际查询用的那条路（N 网走官方 API、"
             "GitHub 走 API、a 站只能爬网页），所以结果和「能不能查到更新」一致。",
             "能连上但站点回 401/403 属于正常；只有超时 / 连不上才算网络问题。"]
    if any(s.get("blocked") == "cf" for s in net.get("sites") or []):
        lines.append("标着「被 Cloudflare 挡住」的站点：站点要求能跑 JS 的浏览器才"
                     "放行，浏览器能开、工具会被挡 —— 不是网络故障，也不是登录过期。")
    for s in net.get("sites") or []:
        mark = "能连上" if s.get("ok") else "连不上"
        if s.get("blocked") == "cf":
            mark = "能连上（被 Cloudflare 挡住）"
        lines.append(f"{s.get('name')}：{mark}（{s.get('sec')}s）—— {s.get('detail')}")
    return lines


def _fmt_library(lib: dict, with_list: bool) -> list[str]:
    if not lib.get("ok"):
        return [f"读模组库失败：{lib.get('error') or '未知原因'}"]
    lines = [f"共 {lib['total']} 个模组（启用 {lib['enabled']}，已部署 {lib['deployed']}）",
             f"模组目录：{lib.get('mods_dir') or '?'}",
             f"游戏目录：{lib.get('game_dir') or '?'}"]
    if not with_list:
        lines.append("（模组清单未包含 —— 需要的话在生成时勾上）")
        return lines
    lines.append("模组清单：")
    for r in lib.get("rows") or []:
        flags = []
        if r["enabled"]:
            flags.append("已启用")
        if r["deployed"]:
            flags.append("已部署")
        if r["nexus_id"]:
            flags.append(f"N网{r['nexus_id']}")
        if r["manifest"]:
            flags.append(f"manifest[{r['manifest']}]")
        # ★ 「自动探测到」的值和实际采用的值分开写 —— issue #1 反馈者的诊断里
        #   就是这两列并排才看出矛盾（手动 1.1 vs 目录名 1.2）。这里同理。
        extra_ver = ""
        av = r.get("auto_version") or ""
        if av and av != (r.get("version") or ""):
            extra_ver = f" | 自动探测到 {av}"
        lines.append(
            f"  · {r['label']} | 版本 {r['version'] or '未知'}"
            f"（{r['version_src']}）"
            + extra_ver
            + (f" | 变体 {r['variant']}" if r["variant"] else "")
            + f" | 目录名 {r['dirname'] or '?'}"
            + (f" | uuid {r['uuid'][:8]}" if r["uuid"] else "")
            + (" | " + " ".join(flags) if flags else "")
        )
    return lines


def report(
    desc: str = "",
    steps: str = "",
    expect: str = "",
    *,
    include_library: bool = True,
    include_logs: bool = False,
    hide_user: bool = True,
    net: dict | None = None,
    context: str = "",
) -> str:
    """生成完整诊断文本（可直接粘贴到反馈里）。

    参数：
      desc/steps/expect —— 用户填的「现象 / 我做了什么 / 期望」
      include_library   —— 是否附模组清单（默认附，对查「搜不到」最关键）
      include_logs      —— 是否附日志尾部（默认不附；从错误弹窗生成时建议附）
      hide_user         —— 是否把路径里的 Windows 用户名打码（默认打码）
      net               —— network_check() 的结果（没有就写"没检测"）
      context           —— 额外的现场说明（如"从哪个错误弹窗生成的"）
    """
    from . import APP_NAME, __version__

    out: list[str] = []
    out.append("=" * 60)
    out.append(f"{APP_NAME} 诊断信息（v{__version__}）")
    out.append("=" * 60)
    out.append("这一段可以直接整段粘贴到反馈里；它不包含你的 API key / 令牌。")
    out.append("")

    out.append("--- 1. 环境 ---")
    out.extend(_fmt_env(environment()))
    if context:
        out.append(f"现场：{context}")
    out.append("")

    out.append("--- 2. 模组库 ---")
    try:
        lib = library_overview()
    except Exception as e:  # noqa: BLE001
        lib = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    out.extend(_fmt_library(lib, include_library))
    out.append("")

    out.append("--- 3. 网络自检 ---")
    out.extend(_fmt_net(net))
    out.append("")

    out.append(f"--- 4. 操作时间线（最近 {TIMELINE_MAX} 条）---")
    tl = timeline_lines()
    out.extend(tl if tl else ["（没有记录）"])
    out.append("")

    if include_logs:
        out.append("--- 5. 日志尾部 ---")
        out.append(log_tail(200))
        out.append("")

    out.append("--- 6. 我的描述 ---")
    out.append("现象（看到了什么）：")
    out.append(desc.strip() or "（没填）")
    out.append("")
    out.append("我做了什么（点过哪些按钮 / 走到哪一步）：")
    out.append(steps.strip() or "（没填）")
    out.append("")
    out.append("期望是什么：")
    out.append(expect.strip() or "（没填）")
    out.append("")
    out.append(f"（本工具开源地址：{REPO_URL}）")

    return redact("\n".join(out), hide_user=hide_user)


def summary(desc: str = "", *, net: dict | None = None,
            hide_user: bool = True) -> str:
    """短摘要（≤15 行）—— 专门为「截图发到小黑盒」这种场景准备。

    为什么要短版：贴吧/小黑盒的评论区发不了长文，用户习惯发截图。
    把最关键的几行压缩到一屏内，截图就是完整信息。
    """
    from . import APP_NAME, __version__

    lines = [f"【{APP_NAME} 反馈】v{__version__}"]
    try:
        info = environment()
        lines.append(f"系统：{info.get('系统', '?')}")
        lines.append(f"运行：{info.get('运行方式', '?')}")
        lines.append(f"Arsenal：{'已找到' if '没找到' not in str(info.get('Arsenal 模组库', '')) else '没找到'}"
                     f"（运行中：{info.get('Arsenal 是否正在运行', '?')}）")
    except Exception as e:  # noqa: BLE001
        lines.append(f"环境读取失败：{type(e).__name__}: {e}")
    try:
        lib = library_overview()
        if lib.get("ok"):
            lines.append(f"模组：{lib['total']} 个（启用 {lib['enabled']}，"
                         f"已部署 {lib['deployed']}）")
        else:
            lines.append(f"模组库读取失败：{lib.get('error')}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"模组库读取失败：{type(e).__name__}: {e}")
    if net:
        marks = " ".join(("✓" if s.get("ok") else "✗") + str(s.get("name"))
                         for s in net.get("sites") or [])
        lines.append(f"网络：{marks or '?'}")
        lines.append(f"代理：{net.get('proxy', '?')}")
    else:
        lines.append("网络：（未检测）")
    first = (desc or "").strip().splitlines()
    lines.append("问题：" + (first[0][:60] if first else "（没填）"))
    lines.append("完整诊断：点工具里的「复制完整诊断」")
    return redact("\n".join(lines), hide_user=hide_user)


def save_text(text: str, path: str) -> str:
    """把诊断文本存成文件（utf-8-sig，记事本打开不乱码）。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)
    return path


def default_report_name() -> str:
    return f"hd2u-反馈-{datetime.now():%Y%m%d-%H%M%S}.txt"


def issue_url(title: str = "", body: str = "") -> str:
    """GitHub「新建 issue」预填链接（用户在浏览器里点一下提交）。

    只放**短**内容进 URL —— 太长的 URL 会被浏览器/GitHub 截断，
    所以长诊断走「复制到剪贴板 + 粘贴」这条路。
    """
    from urllib.parse import quote

    t = title.strip() or "[反馈] 请在这里写一句话说明问题"
    b = body.strip()
    if len(b) > 4000:
        b = b[:4000] + "\n…（诊断信息太长，请把「复制完整诊断」的内容粘到这里）"
    return f"{ISSUES_URL}?title={quote(t)}&body={quote(b)}"


# --------------------------------------------------------------------------
# 全局异常兜底
# --------------------------------------------------------------------------
def install_hooks(on_error=None) -> None:
    """装全局异常钩子：未捕获异常一律进日志（并可选通知界面）。

    为什么必须有：打包成 EXE 后是 --windowed，**没有控制台**，
    未捕获异常在后台线程里只 print 到不存在的地方 —— 用户看到的是
    "点了没反应"，而我这边连一行证据都拿不到。
    """
    def _hook(exc_type, exc, tb) -> None:
        try:
            text = "".join(traceback.format_exception(exc_type, exc, tb))
        except Exception:  # noqa: BLE001
            text = f"{exc_type}: {exc}"
        log("未捕获异常：\n" + text, "ERROR")
        if on_error is not None:
            try:
                on_error(exc_type, exc, tb)
            except Exception:  # noqa: BLE001
                pass

    sys.excepthook = _hook
    try:
        def _thread_hook(args) -> None:  # noqa: ANN001
            _hook(args.exc_type, args.exc_value, args.exc_traceback)
        threading.excepthook = _thread_hook       # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        pass
