# -*- coding: utf-8 -*-
"""更新检查引擎：把 Arsenal 库、来源表、三站点 provider 串起来。

核心规则（按用户要求）：
  - 站点优先级 GitHub > Nexus > ayakamods
  - 只要任一来源显示有更新，就算需要更新
  - 只把「正式版」更新算作需要处理；只有测试版可更时单独列出
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import matcher, providers, records
from .arsenal import (
    load_library,
    tool_downloads_dir,
    write_config,
)
from .util import compare, app_data_dir, now_str, is_prerelease_text

# 检查结果状态
ST_UPDATE = "update"        # 有正式版更新
ST_BETA = "beta"            # 只有测试版可更
ST_OK = "ok"                # 已最新
ST_UNKNOWN = "unknown"      # 版本无法比较
ST_ERROR = "error"          # 查询失败
ST_NO_SOURCE = "nosource"   # 查过了，但没找到来源
ST_UNCHECKED = "unchecked"  # 还没检查（刚打开程序时的初始状态）
ST_DOWNLOADED = "downloaded"  # 有更新，而且这个版本**已经下过了**


def sources_path() -> str:
    return os.path.join(app_data_dir(), "sources.json")


def load_sources() -> dict:
    """读来源表：{mod_uuid 或 label: [{provider,id,url,...,"manual":True?}]}

    ``manual: True`` 的条目是**用户手工填写/确认过的**，
    永远不会被自动识别结果覆盖，也不会被「清空来源缓存」抹掉。
    """
    try:
        with open(sources_path(), encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_sources(data: dict) -> None:
    tmp = sources_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, sources_path())


def set_manual_source(entry: dict, src: dict) -> None:
    """写入一条**用户手工指定**的来源（打 manual 标记，受保护）。"""
    data = load_sources()
    key = source_key(entry)
    rec = {k: v for k, v in src.items() if k in ("provider", "id", "url", "asset")}
    rec["manual"] = True
    data[key] = [rec]
    save_sources(data)


def clear_auto_sources() -> int:
    """清掉自动识别的来源，**保留手工指定的**。返回清掉的条数。"""
    data = load_sources()
    kept = {}
    removed = 0
    for key, items in (data or {}).items():
        manual = [i for i in (items or []) if i.get("manual")]
        if manual:
            kept[key] = manual
        else:
            removed += 1
    save_sources(kept)
    return removed


def source_key(entry: dict) -> str:
    """给一个 mod 生成稳定 key（优先 uuid）。"""
    return entry.get("uuid") or entry.get("label") or entry.get("dirname") or ""


def check_one_source(src: dict, api_key: str, github_token: str,
                     variant: str = "") -> dict:
    """查单个来源。

    ``variant`` 是本地装的变体名（如 "Vanilla Plus Megapack - Rows"）——
    当一个 mod 有并行变体时，靠它挑对文件，避免把 Rows 版更新成普通版。
    只有该行自己的来源才用得到，所以一路透传给 provider。
    """
    prov = (src.get("provider") or "").lower()
    ident = str(src.get("id") or "").strip()
    url = str(src.get("url") or "").strip()
    if prov == "github":
        return providers.github_check(ident or url, github_token,
                                      src.get("asset", ""), variant)
    if prov == "nexus":
        return providers.nexus_check(ident or url, api_key, variant)
    if prov == "ayaka":
        return providers.ayaka_check(url or ident)
    out = providers._empty(prov or "?")
    out["error"] = f"不认识的来源类型：{prov}"
    return out


def evaluate(entry: dict, checks: list[dict]) -> dict:
    """把多个来源的查询结果汇总成一个结论。"""
    installed = entry.get("installed_version") or ""
    stable_upd, beta_upd, oks, errs, unknown = [], [], [], [], []
    for c in checks:
        if not c.get("ok"):
            errs.append(c)
            continue
        v = compare(installed, c.get("version", ""))
        if v == "newer":
            if c.get("prerelease") or is_prerelease_text(c.get("version", "")):
                beta_upd.append(c)
            else:
                stable_upd.append(c)
        elif v in ("same", "older"):
            oks.append(c)
        else:
            # 比不出来 —— 分两种情况给出**可操作**的说明
            online = c.get("version", "") or "?"
            if not installed:
                why = (f"本地没记录版本号，没法比较（线上是 {online}）。"
                       f"要么是你手动导入的 mod 本身没写版本，"
                       f"要么是安装目录名里没有版本信息。")
            else:
                why = (f"两边格式不同，没法比大小：本地「{installed}」vs 线上「{online}」。"
                       f"大多是站点把「版本」栏填成了日期。")
            c2 = {**c, "ok": False, "error": why, "unknown_ver": True}
            unknown.append(c2)

    def rank(c: dict) -> tuple:
        # 能直接下载的优先，其次按站点优先级
        return (
            0 if c.get("download_url") else 1,
            matcher.PROVIDER_PRIORITY.get(c.get("provider"), 9),
        )

    if stable_upd:
        best = sorted(stable_upd, key=rank)[0]
        status = ST_UPDATE
    elif beta_upd:
        best = sorted(beta_upd, key=rank)[0]
        status = ST_BETA
    elif oks:
        best = sorted(oks, key=rank)[0]
        status = ST_OK
    elif unknown and not errs:
        # 纯粹是"版本比不了"（不是网络失败）—— 单列一类，别混进"查询失败"
        best = unknown[0]
        status = ST_UNKNOWN
    elif errs:
        best = errs[0]
        status = ST_ERROR
    elif unknown:
        best = unknown[0]
        status = ST_UNKNOWN
    else:
        best = None
        status = ST_NO_SOURCE

    # 特别提示：只有 N 网来源但没有 API key —— 这不是"失败"，
    # 而是"需要设置一下"，给用户明确的下一步。
    if status == ST_ERROR and best is not None:
        err = str(best.get("error") or "")
        if "API key" in err or "没填" in err:
            status = ST_ERROR
            best = dict(best)
            best["error"] = "需要设置 N网 API key（设置… 里填）—— 填了才能自动查 N网"

    return {
        "status": status,
        "installed": installed,
        "best": best,
        "checks": checks,
        "stable_count": len(stable_upd),
        "beta_count": len(beta_upd),
    }


class Engine:
    """负责一次完整的更新检查。"""

    def __init__(
        self,
        data_file: str,
        api_key: str = "",
        github_token: str = "",
        use_search: bool = True,
        max_workers: int = 4,
        log=None,
        progress=None,
    ) -> None:
        self.data_file = data_file
        self.api_key = api_key
        self.github_token = github_token
        self.use_search = use_search
        self.max_workers = max(1, int(max_workers))
        self._log = log or (lambda m: None)
        self._progress = progress or (lambda done, total, msg: None)
        self._cancel = threading.Event()
        self.library: list[dict] = []
        self.resolved: dict[str, list[dict]] = {}   # key -> 来源候选
        self.results: list[dict] = []

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---------------------------------------------------------------- 加载
    def load(self) -> list[dict]:
        self.library = load_library(self.data_file)
        self._log(f"从 Arsenal 读到 {len(self.library)} 个模组")
        return self.library

    # ------------------------------------------------------- 解析来源
    def resolve_sources(self, force_search: bool = False) -> None:
        """给每个 mod 找出来源。已手工确认过的优先用。

        站点索引过期（默认 7 天）会**自动重建**，不需要用户手动点。
        """
        saved = load_sources()
        index: list[dict] = []
        if self.use_search:
            # ensure_index 内部会判断：不存在、或超过保鲜期 → 自动重建。
            # （旧代码把「过期检查」写在了「索引不存在」的分支里，是死代码，
            #   导致索引建好后再也不更新。）
            index = matcher.ensure_index(progress=self._log, force=force_search)
            if index:
                self._log(f"使用站点索引（{matcher.index_status_text()}，"
                          f"{len(index)} 个模组）")

        total = len(self.library)
        for i, entry in enumerate(self.library, 1):
            if self.cancelled:
                return
            key = source_key(entry)
            self._progress(i - 1, total, f"解析来源：{entry.get('label') or entry['dirname']}")

            # 用户手工确认过的直接采纳
            if key in saved and saved[key]:
                self.resolved[key] = saved[key]
                continue

            cands = matcher.candidates_for(
                entry,
                github_token=self.github_token,
                use_github_search=self.use_search,
                ayaka_index=index,
                log=lambda m, e=entry: self._log(f"[{e.get('label') or e['dirname']}] {m}"),
            )
            # 只自动采纳高可信度的；中低可信留给用户确认
            auto = [c for c in cands if c.get("confidence") == "high"]
            if auto:
                self.resolved[key] = auto
                self._log(f"自动识别：{entry.get('label')} -> {auto[0]['provider']}：{auto[0]['how']}")
            else:
                self.resolved[key] = []
        self._log(f"来源解析完成：{sum(1 for v in self.resolved.values() if v)}/{total} 个已识别")

    # ---------------------------------------------------------- 检查更新
    def check(self, only_keys: set[str] | None = None) -> list[dict]:
        """对已识别来源的 mod 检查更新（并发）。

        only_keys 非空时，**只检查这些 key 对应的模组**，其余不动作
        （用于「只检查未检查的」这类增量场景，免得为一个模组重跑全库）。
        返回值仍只有被检查的那些模组的结果。
        """
        jobs = []
        for entry in self.library:
            key = source_key(entry)
            if only_keys is not None and key not in only_keys:
                continue
            for src in self.resolved.get(key) or []:
                jobs.append((entry, key, src))
        # 被选中但没有任何来源的模组：也要给出结果（未找到来源）
        entries_to_do = [
            e for e in self.library
            if only_keys is None or source_key(e) in only_keys
        ]

        total = len(jobs)
        if not total:
            self.results = [self._result_for(e, []) for e in entries_to_do]
            return self.results

        by_key: dict[str, list[dict]] = {k: [] for k in self.resolved}
        done = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {
                ex.submit(check_one_source, src, self.api_key, self.github_token,
                          entry.get("variant") or ""): (entry, key, src)
                for entry, key, src in jobs
            }
            for fut in as_completed(futs):
                if self.cancelled:
                    for f in futs:
                        f.cancel()
                    break
                entry, key, src = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001
                    res = providers._empty(src.get("provider", "?"))
                    res["error"] = f"意外错误：{e}"
                res["_src"] = src
                by_key.setdefault(key, []).append(res)
                done += 1
                self._progress(
                    done, total,
                    f"检查中：{entry.get('label') or entry['dirname']}（{src.get('provider')}）",
                )

        # 只返回这次真正查了的模组（增量模式下不要覆盖其他结果）
        self.results = [
            self._result_for(e, by_key.get(source_key(e), []))
            for e in entries_to_do
        ]
        return self.results

    def _result_for(self, entry: dict, checks: list[dict]) -> dict:
        ev = evaluate(entry, checks)
        return {"entry": entry, "checks": checks, **ev}

    # ------------------------------------------------------------- 保存
    def persist_resolved(self) -> None:
        """把自动识别结果写回来源表，下次直接用。

        两道保护，确保**用户手工填写的来源永远不被覆盖**：
          1. 已存在的 key 一律跳过（无论手工还是自动）；
          2. 只保存高可信度的结果 —— 低可信（如索引里 56% 的噪声匹配）
             绝不落盘，否则会固化成错误的"已确认来源"，
             下次直接拿它去比版本，用户会被引到错误的 mod 页面上去。
        """
        saved = load_sources()
        added = 0
        for key, cands in self.resolved.items():
            if not cands or key in saved:
                continue
            keep = [
                {k: v for k, v in c.items() if k in ("provider", "id", "url", "asset")}
                for c in cands
                if c.get("confidence") == "high"
            ]
            if not keep:
                continue
            saved[key] = keep
            added += 1
        if added:
            save_sources(saved)
            self._log(f"已保存 {added} 个自动识别的来源（只存高可信的）")


def summarize(results: list[dict]) -> dict:
    """统计各状态数量。"""
    n = {ST_UPDATE: 0, ST_BETA: 0, ST_OK: 0, ST_ERROR: 0, ST_UNKNOWN: 0,
         ST_NO_SOURCE: 0, ST_DOWNLOADED: 0}
    for r in results:
        n[r["status"]] = n.get(r["status"], 0) + 1
    return n


# --------------------------------------------------------------------------
# 「这一版我下过了」这层状态
# --------------------------------------------------------------------------
def apply_download_records(rows: list[dict]) -> list[dict]:
    """把下载记录叠加到检查结果上，**返回新列表，不改原数据**。

    规则（重要，别改坏）：
      · 记的版本 == 现在线上版本，且文件还在 → 标成「已下载」
      · 版本对不上（线上又更新了）→ 保持「有更新」，
        否则用户会以为新版本也下好了
      · 版本对得上但文件被移走/删了 → 保持「有更新」，
        但附一句说明（很可能已经导入 Arsenal 了）

    之所以放在渲染前算而不是塞进 evaluate()：下载记录会**独立于检查结果变化**
    （用户手动删了文件、或线上又发新版），每次渲染都得重新判断一次。
    """
    out: list[dict] = []
    for r in rows:
        if r.get("status") != ST_UPDATE:
            out.append(r)
            continue
        best = r.get("best") or {}
        ver = str(best.get("version") or "")
        key = source_key(r.get("entry") or {})
        rec = records.match(key, ver)
        if rec:
            out.append({**r, "status": ST_DOWNLOADED, "downloaded": rec})
            continue
        gone = records.file_missing(key, ver)
        if gone:
            out.append({**r, "prev_download": gone})
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------
def download(url: str, dest_dir: str, filename: str = "", sha256: str = "",
             progress=None, cancel: threading.Event | None = None) -> str:
    """下载文件到 dest_dir，带 sha256 校验。返回落地路径。"""
    import urllib.request
    import hashlib
    from .util import UA, _CTX, NetError

    name = filename or os.path.basename(url.split("?")[0]) or "mod.zip"
    from .util import safe_filename
    name = safe_filename(name)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    tmp = dest + ".part"

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=60, context=_CTX) as resp:
            declared = int(resp.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise NetError("已取消")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, declared)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    if sha256:
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual.lower() != sha256.lower():
            os.remove(tmp)
            raise NetError(
                f"sha256 校验失败（期望 {sha256[:16]}…，实际 {actual[:16]}…），已删除"
            )
    os.replace(tmp, dest)
    return dest


def open_in_browser(url: str) -> bool:
    """用系统默认浏览器打开链接。"""
    try:
        if hasattr(os, "startfile"):
            os.startfile(url)  # noqa: S606
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import webbrowser
        return webbrowser.open(url)
    except Exception:  # noqa: BLE001
        return False
