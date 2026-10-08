# -*- coding: utf-8 -*-
"""通用工具：HTTP 请求、节流、版本比较、错误翻译。

只依赖 Python 标准库。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

# 让中文在 Windows 控制台 / 重定向时都能正确输出
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 45
RETRIES = 3

# ayakamods 在 Cloudflare 后面，请求太密会被拦；同站点逐个请求、间隔数秒
SLOW_HOSTS = ("ayakamods.com",)
SLOW_HOST_DELAY = 3.0
_last_hit: dict[str, float] = {}
_throttle_lock = threading.Lock()

_CTX = ssl.create_default_context()


class NetError(Exception):
    """网络请求失败（带用户可读信息）。"""


def throttle(url: str) -> None:
    """对已知敏感的站点做请求间隔控制（线程安全）。"""
    host = next((h for h in SLOW_HOSTS if h in url), None)
    if not host:
        return
    with _throttle_lock:
        prev = _last_hit.get(host)
        if prev is not None:
            wait = SLOW_HOST_DELAY - (time.monotonic() - prev)
            if wait > 0:
                time.sleep(wait)
        _last_hit[host] = time.monotonic()


def http_get(
    url: str,
    accept: str = "*/*",
    headers: dict | None = None,
    timeout: int = TIMEOUT,
    retries: int = RETRIES,
) -> str:
    """带重试的 GET，返回文本；失败抛 NetError。"""
    last = ""
    for attempt in range(1, retries + 1):
        throttle(url)
        hdrs = {
            "User-Agent": UA,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            last = f"HTTP {e.code} {e.reason} {body}".strip()
            if e.code in (400, 401, 403, 404):
                break
            if 500 <= e.code < 600 and attempt < retries:
                time.sleep(4 * attempt)
                continue
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(2 * attempt)
    raise NetError(last or "未知错误")


def http_get_json(
    url: str,
    headers: dict | None = None,
    timeout: int = TIMEOUT,
    retries: int = RETRIES,
) -> object:
    """GET 并解析 JSON。"""
    text = http_get(
        url,
        accept="application/json",
        headers=headers,
        timeout=timeout,
        retries=retries,
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise NetError(f"返回的不是合法 JSON：{e}") from None


# --------------------------------------------------------------------------
# 版本比较
# --------------------------------------------------------------------------
def ver_key(text: str):
    """版本串 -> 可比较元组。'v0.28.1'->(0,28,1)；'r41'->(41,)；'2026-10-04'->日期。

    返回 (kind, tuple)，kind 用于避免**跨类型误比**：
      "date"  纯日期（ayakamods 常把版本栏填成日期）
      "num"   普通数字版本
    """
    if not text:
        return None
    t = str(text).strip()
    # 纯日期
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return ("date", (int(m.group(1)), int(m.group(2)), int(m.group(3))))
    # 带前缀的版本号（v1.2.3 / r41 / R42）
    m = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)", t)
    if m:
        return ("num", tuple(int(x) for x in m.group(1).split(".")))
    nums = re.findall(r"\d+", t)
    if not nums:
        return None
    return ("num", tuple(int(n) for n in nums[:6]))


def compare(installed: str, latest: str) -> str:
    """以 installed 视角比较。返回 newer / same / older / unknown。

    关键规则：**类型不同就不比**（日期 vs 版本号），返回 unknown。
    否则会把 ayakamods 的日期版本（2026-09-24）跟本地 0.2.2 硬比，
    得出「(2026,9,24) > (0,2,2) → 有更新」的错误结论 —— 实测踩过。
    """
    ka, kb = ver_key(installed), ver_key(latest)
    if ka is None or kb is None:
        return "unknown"
    ta, a = ka
    tb, b = kb
    if ta != tb:
        return "unknown"          # 类型不同，无法比较
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    if b > a:
        return "newer"
    if b < a:
        return "older"
    return "same"


def parse_date_only(text: str) -> tuple | None:
    """把 '2026-10-04' / 'Oct 4, 2026 at 03:13' 之类转成 (y,m,d)（拿不到就 None）。"""
    if not text:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m = re.search(r"([A-Za-z]{3})\w*\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        mon = months.get(m.group(1).lower())
        if mon:
            return (int(m.group(3)), mon, int(m.group(2)))
    return None


def is_prerelease_text(text: str) -> bool:
    """版本串里是否带测试版字样。"""
    v = (text or "").lower()
    return any(k in v for k in ("rc", "beta", "alpha", "test", "preview", "dev", "nightly"))


# --------------------------------------------------------------------------
# 错误信息处理
# --------------------------------------------------------------------------
def short_err(text: str, limit: int = 140) -> str:
    """把错误压成一行：去掉 HTML、合并空白、截断。"""
    t = str(text or "")
    if "<" in t and ">" in t:
        t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
        t = re.sub(r"<[^>]{0,400}>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return "（无详细信息）"
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def friendly_error(err: str) -> str:
    """把底层错误翻译成人话。"""
    s = str(err or "")
    m = re.search(r"HTTP (\d{3})", s)
    code = m.group(1) if m else ""
    if code == "522" or "timed out" in s.lower() or "TimeoutError" in s:
        return "站点暂时不可达（超时）——过一会儿再试"
    if code == "525":
        return "站点 HTTPS 握手失败（Cloudflare 525）——站点侧临时故障"
    if code in ("502", "503", "504"):
        return f"站点暂时故障（HTTP {code}）——稍后再试"
    if code == "429":
        return "请求太频繁被临时限流——过一会儿再试"
    if code == "403":
        return "被站点拒绝（HTTP 403）——可能是 Cloudflare 拦截或需要登录"
    if code == "401":
        return "需要 API key 或授权（HTTP 401）"
    if "GitHub API 限流" in s:
        return s
    if code.startswith("5"):
        return f"站点服务端错误（HTTP {code}）——稍后再试"
    return short_err(s)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def human_size(n: int | None) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def safe_filename(name: str) -> str:
    """把名字变成安全的文件名。"""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip(" .")
    return s or "download"


# --------------------------------------------------------------------------
# 数据目录解析
#
# 优先级（从高到低）：
#   1. 环境变量 HD2U_DATA_DIR     —— 便于脚本/便携用法，优先级最高
#   2. 指针文件里的路径            —— 用户在「设置…」里自定义的目标
#   3. 默认位置 %LOCALAPPDATA%\hd2-updater
#
# 为什么要"指针文件"：config.json 本身就在数据目录里，若想从 config 读
# 自定义路径就会先有鸡还是先有蛋。所以指针单独放默认位置、只存一行路径，
# 真正的配置仍在（可能已搬走的）数据目录里。
# --------------------------------------------------------------------------
ENV_DATA_DIR = "HD2U_DATA_DIR"
DEFAULT_DIRNAME = "hd2-updater"
POINTER_FILENAME = "data_dir.txt"


def default_data_dir() -> str:
    """默认数据目录（未自定义时用的那个）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, DEFAULT_DIRNAME)


def pointer_path() -> str:
    """指针文件路径（始终在默认位置）。"""
    return os.path.join(default_data_dir(), POINTER_FILENAME)


def _read_pointer() -> str:
    try:
        with open(pointer_path(), encoding="utf-8-sig") as f:
            p = f.read().strip()
        return p
    except Exception:  # noqa: BLE001
        return ""


def set_data_dir(path: str) -> None:
    """把数据目录指向 path。传空字符串 = 恢复默认。

    只写指针文件，**不搬数据** —— 搬家由 GUI 负责（它要能报进度/报错）。
    """
    ptr = pointer_path()
    os.makedirs(os.path.dirname(ptr), exist_ok=True)
    if not path:
        try:
            os.remove(ptr)
        except OSError:
            pass
        return
    with open(ptr, "w", encoding="utf-8") as f:
        f.write(os.path.abspath(os.path.expandvars(os.path.expanduser(path))))


def current_data_dir() -> str:
    """当前生效的数据目录（不创建）。"""
    env = os.environ.get(ENV_DATA_DIR)
    if env and env.strip():
        return os.path.abspath(os.path.expandvars(os.path.expanduser(env.strip())))
    p = _read_pointer()
    if p:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(p)))
    return default_data_dir()


def is_custom_data_dir() -> bool:
    """当前是否用了自定义目录。"""
    return os.path.normcase(current_data_dir()) != os.path.normcase(default_data_dir())


# 数据目录回退时的告警（供界面提示用户，只报一次）
_last_fallback_reason = ""


def take_fallback_reason() -> str:
    """取走（并清空）上次回退的原因，供界面提示。"""
    global _last_fallback_reason
    r = _last_fallback_reason
    _last_fallback_reason = ""
    return r


def _dir_usable(d: str) -> tuple[bool, str]:
    """检查目录是否真的能用（存在或可创建 + 可写）。"""
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as e:
        return False, f"无法创建/访问：{e.strerror or e}"
    if not os.path.isdir(d):
        return False, "不是一个目录"
    if not os.access(d, os.W_OK):
        return False, "目录不可写"
    return True, ""


def read_tool_config() -> dict:
    """读工具自己的 config.json（只读，容错）。

    注意：不要和 arsenal.read_config() 混用 —— 那个是同一个文件，
    这里提供一个不依赖 arsenal 模块的入口，避免 util 反向依赖 arsenal。
    """
    import json
    p = os.path.join(app_data_dir(), "config.json")
    try:
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def read_config_dismissed() -> bool:
    """用户是否已忽略过"填令牌"的提示（避免反复打扰）。"""
    return bool(read_tool_config().get("token_hint_dismissed"))


def app_data_dir() -> str:
    """本工具的配置/缓存目录（实际使用位置，可能是自定义的）。

    **保证不抛异常**：自定义位置不可用（U 盘拔了、盘符不存在、权限不足、
    指针文件被改坏）时，自动回退到默认位置，并记下原因供界面提示一次。
    这条"永不崩"的保证很重要 —— 否则用户会看到莫名的 FileNotFoundError，
    完全联想不到是数据目录的问题。
    """
    global _last_fallback_reason
    wanted = current_data_dir()

    ok, why = _dir_usable(wanted)
    if ok:
        return wanted

    # 回退到默认位置
    fallback = default_data_dir()
    if os.path.normcase(fallback) != os.path.normcase(wanted):
        fok, _fwhy = _dir_usable(fallback)
        if fok:
            _last_fallback_reason = f"原先的数据目录不可用（{wanted}）：{why}，已临时改用默认位置"
            return fallback

    # 连默认位置都不行 —— 退到临时目录，至少别崩
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), DEFAULT_DIRNAME)
    os.makedirs(tmp, exist_ok=True)
    _last_fallback_reason = (
        f"数据目录不可用（{wanted}：{why}），默认位置也建不了，"
        f"已临时改用 {tmp}（数据不会被长期保存）"
    )
    return tmp


# 会被搬移的文件/目录（相对数据目录）
MOVE_ITEMS = ("config.json", "sources.json", "ayaka_index.json", "downloads",
              "downloads.json")


def existing_conflicts(new_dir: str) -> list[str]:
    """列出目标目录里**已存在、且会被覆盖**的项（搬移前提示用户用）。"""
    out = []
    for name in MOVE_ITEMS:
        src = os.path.join(current_data_dir(), name)
        dst = os.path.join(new_dir, name)
        if os.path.exists(src) and os.path.exists(dst):
            out.append(name)
    return out


def move_data_dir(
    new_dir: str,
    progress=None,
    allow_overwrite: bool = False,
) -> tuple[bool, str]:
    """把数据从当前目录搬到 new_dir。

    返回 (是否成功, 说明)。**先复制、校验通过后再删源**，避免搬一半丢数据。
    搬完顺带把指针改过去。

    allow_overwrite=False（默认）时，若目标目录已有同名文件，**直接拒绝并
    列出冲突项** —— 避免静默覆盖掉用户以前的备份或别的工具的数据。
    """
    import shutil

    old = current_data_dir()
    new = os.path.abspath(os.path.expandvars(os.path.expanduser(new_dir)))
    if os.path.normcase(old) == os.path.normcase(new):
        return False, "新位置和当前是同一个目录"

    # 防呆：不允许选中旧目录的子目录（会自我嵌套）
    try:
        if os.path.normcase(os.path.commonpath([old, new])) == os.path.normcase(old):
            return False, "新位置不能是当前目录的子目录"
    except ValueError:
        pass  # 不同盘，commonpath 会抛错 —— 那正好说明安全

    try:
        os.makedirs(new, exist_ok=True)
    except OSError as e:
        return False, f"建不了目标目录：{e}"

    ok, why = _dir_usable(new)
    if not ok:
        return False, f"目标目录不可用：{why}"

    # 防呆：目标已有同名文件 —— 不静默覆盖
    conflicts = existing_conflicts(new)
    if conflicts and not allow_overwrite:
        return False, (
            "目标目录里已经有同名文件，会被覆盖：\n  "
            + "\n  ".join(conflicts)
            + "\n\n如果那是你以前的备份，请换一个空目录；"
              "确实要覆盖的话，请重新操作并确认覆盖。"
        )

    moved: list[str] = []
    for name in MOVE_ITEMS:
        src = os.path.join(old, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(new, name)
        try:
            if progress:
                progress(f"搬移 {name} …")
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            # 校验：文件大小一致才算成功
            if os.path.isfile(src):
                if os.path.getsize(src) != os.path.getsize(dst):
                    return False, f"{name} 复制后大小不一致，已中止（原数据未删）"
            moved.append(name)
        except Exception as e:  # noqa: BLE001
            return False, f"搬移 {name} 失败：{e}（原数据未删）"

    # 全部复制成功，才删源
    for name in moved:
        src = os.path.join(old, name)
        try:
            if os.path.isdir(src):
                shutil.rmtree(src, ignore_errors=True)
            else:
                os.remove(src)
        except OSError:
            pass  # 删不掉也无妨，指针已改，不影响使用

    set_data_dir(new)
    if progress:
        progress("完成")
    return True, f"已搬运 {len(moved)} 项到 {new}"
