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


def app_data_dir() -> str:
    """本工具的配置/缓存目录（放在用户数据区，避免与项目文件混在一起）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "hd2-updater")
    os.makedirs(d, exist_ok=True)
    return d
