# -*- coding: utf-8 -*-
"""三个站点的查询实现：GitHub / Nexus / ayakamods。

统一返回结构（所有 provider 都产出这个形状）：
{
    "provider": "github" | "nexus" | "ayaka",
    "ok": bool,
    "version": str,          # 线上最新版本号
    "url": str,              # 模组页地址
    "file_url": str,         # 该版本所在的「文件页」深链（打开就停在那一个文件上）
    "download_url": str,     # 可直接下载的地址（GitHub 才有）
    "asset_name": str,       # 文件名
    "sha256": str,           # 校验和（有则给）
    "size": int,
    "updated": str,          # 最后更新时间
    "prerelease": bool,      # 是否测试版
    "error": str,            # ok=False 时填
    "note": str,             # 补充说明
}
"""
from __future__ import annotations

import re
import urllib.parse

from .util import (
    NetError,
    http_get,
    http_get_json,
    is_prerelease_text,
    parse_date_only,
    friendly_error,
    short_err,
)

GITHUB_API = "https://api.github.com"


def _empty(provider: str) -> dict:
    return {
        "provider": provider,
        "ok": False,
        "version": "",
        "url": "",
        "file_url": "",
        "download_url": "",
        "asset_name": "",
        "sha256": "",
        "size": 0,
        "updated": "",
        "prerelease": False,
        "error": "",
        "note": "",
    }


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------
def github_headers(token: str = "") -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def github_check(repo: str, token: str = "", asset_glob: str = "",
                 variant: str = "") -> dict:
    """查 GitHub 仓库最新 release。

    优先正式版 /releases/latest；若只有预发布版（latest 会 404），
    退一步列 releases 取最新，并标记 prerelease。

    ``variant`` 是本地安装的变体名 —— 一个 release 里放多个变体时
    （如 Megapack 的 Rows 版），靠它挑对资产而不是"取最大的"。
    """
    out = _empty("github")
    repo = (repo or "").strip().strip("/")
    m = re.search(r"github\.com/([^/]+/[^/#?\s]+)", repo)
    if m:
        repo = m.group(1)
    if not repo or "/" not in repo:
        out["error"] = f"仓库名不对：{repo or '(空)'}（应形如 作者/仓库）"
        return out
    repo = repo.removesuffix(".git")
    out["url"] = f"https://github.com/{repo}"

    info = None
    try:
        info = http_get_json(
            f"{GITHUB_API}/repos/{repo}/releases/latest", headers=github_headers(token)
        )
    except NetError as e:
        msg = str(e)
        if "限流" in msg or "403" in msg:
            # 退到 atom（不占配额，但没有资产列表）
            return _github_via_atom(repo, out, note="REST 配额受限，改用 atom 源")
        if "404" in msg:
            # 可能只有预发布版，也可能压根没 release
            try:
                rels = http_get_json(
                    f"{GITHUB_API}/repos/{repo}/releases?per_page=10",
                    headers=github_headers(token),
                )
                rels = [r for r in rels if not r.get("draft")]
                if not rels:
                    out["error"] = "该仓库没有任何 release"
                    return out
                stable = [r for r in rels if not r.get("prerelease")]
                info = stable[0] if stable else rels[0]
                if not stable:
                    out["note"] = "该仓库只有预发布版，取的是最新的那个"
            except NetError as e2:
                return _github_via_atom(repo, out, note=f"REST 失败({short_err(e2)})，改用 atom 源")
        else:
            out["error"] = friendly_error(msg)
            return out
    except Exception as e:  # noqa: BLE001
        out["error"] = f"意外错误：{short_err(e)}"
        return out

    if not isinstance(info, dict):
        out["error"] = "GitHub 返回了意外的数据"
        return out

    tag = str(info.get("tag_name") or info.get("name") or "")
    out["version"] = tag
    out["ok"] = True
    out["prerelease"] = bool(info.get("prerelease")) or is_prerelease_text(tag)
    pub = str(info.get("published_at") or "")
    out["updated"] = pub[:19].replace("T", " ")
    out["release_url"] = str(info.get("html_url") or "")

    # 挑资产：优先 .zip，其次 .7z/.rar；有 asset_glob 就按它过滤
    cands = []
    for a in info.get("assets") or []:
        name = str(a.get("name") or "")
        low = name.lower()
        if not low.endswith((".zip", ".7z", ".rar")):
            continue
        # 明显是源码/工具包就跳过
        if any(k in low for k in ("source", "sdk", "template", "example", "-src")):
            continue
        if asset_glob and not _fnmatch(name, asset_glob):
            continue
        cands.append(a)

    if cands:
        # 挑哪个资产：**先认变体，再按大小**
        #
        # 原来一律"取最大的"，但同一个 release 常并行放多个变体：
        #   Vanilla-Plus-Megapack-Rows-v35.zip (2,896,271)
        #   Vanilla-Plus-Megapack-v35.zip      (2,890,903)   ← 只差 5KB
        # 差这么点，谁大谁小纯属偶然 —— 按大小挑等于**随机换变体**。
        # 实测踩过：用户装的 Rows 版会被换成普通版。
        exact = [a for a in cands if _variant_in(str(a.get("name")), variant)]

        if variant and not exact:
            # 这一版里**没有**你装的变体。分两种情况，都不能静静换掉。
            out["_glob"] = asset_glob
            found = _github_variant_lookup(repo, variant, token, out)
            out.pop("_glob", None)
            if found:
                # 已在历史发布里找到该变体的最后一版，out 已填好（含 pick_reason）
                return out
            # 历史里都找不到 → 用户那个变体命名对不上，老实退回默认并说明
            pick = _pick_asset(cands, "")
            _fill_asset(out, pick)
            out["pick_reason"] = (
                f"这个 mod 里找不到你装的「{variant}」变体，"
                f"给的是最新发布里的「{out['asset_name']}」—— 装之前请确认"
            )
            out["note"] = ((out.get("note", "") + " " + out["pick_reason"]).strip())
            return out

        pick = max(exact, key=lambda a: a.get("size") or 0) if exact else _pick_asset(cands, "")
        _fill_asset(out, pick)
        if exact:
            out["variant"] = variant
            out["pick_reason"] = f"匹配到同变体「{variant}」"
            if len(cands) > 1:
                out["note"] = ((out.get("note", "") +
                                f" 已按你装的变体「{variant}」挑中的包。").strip())
        else:
            if len(cands) > 1:
                names = "、".join(str(a.get("name")) for a in cands[:4])
                out["pick_reason"] = (
                    f"认不出你装的是哪个变体，该发布有多个压缩包（{names}），"
                    f"默认选了最大的 {out['asset_name']}"
                )
                out["note"] = (out.get("note", "") + " " + out["pick_reason"]).strip()
            elif variant:
                out["pick_reason"] = f"没匹配到变体「{variant}」，用默认挑选"
    return out


def _fill_asset(out: dict, pick: dict) -> None:
    """把挑中的资产写进结果（三处都要用，抽出来免得漏字段）。"""
    out["download_url"] = str(pick.get("browser_download_url") or "")
    out["asset_name"] = str(pick.get("name") or "")
    out["size"] = int(pick.get("size") or 0)
    out["sha256"] = str(pick.get("digest") or "").replace("sha256:", "")


def _asset_candidates(info: dict, asset_glob: str = "") -> list[dict]:
    """从一次发布里筛出「像是 mod 包」的资产（排除源码包等）。"""
    cands = []
    for a in info.get("assets") or []:
        name = str(a.get("name") or "")
        low = name.lower()
        if not low.endswith((".zip", ".7z", ".rar")):
            continue
        # 明显是源码/工具包就跳过
        if any(k in low for k in ("source", "sdk", "template", "example", "-src")):
            continue
        if asset_glob and not _fnmatch(name, asset_glob):
            continue
        cands.append(a)
    return cands


def _variant_missing_note(variant: str) -> str:
    return (f"你这个「{variant}」变体在这个新版里**没有**（作者可能已经停更该变体），"
            f"下面给的是同一发布里的其它包，装之前先确认是不是你要的。")


def _github_variant_lookup(repo: str, variant: str, token: str,
                           out: dict) -> bool:
    """新版里没有本地这个变体时，回头找「**最后一个还有该变体**的发布」。

    为什么要这么做：实测踩过 Vanilla Plus Megapack —— 用户装的是 Rows 版，
    作者在 v35 之后停更了 Rows，只发普通版。原来的代码会**静静地把普通版
    塞给用户**，用户装完才发现自己的变体被换掉了。

    正确做法是把话说清楚：
      · 若"最后一个含该变体的发布"就是本地版本 → 你这个变体已是最新，
        但要告诉用户"作者停更了这个变体"（是否迁移由用户决定）；
      · 若它比本地新 → 就在该变体内更新。

    返回 True 表示已经填好 out（调用方直接返回即可）。
    """
    try:
        rels = http_get_json(
            f"{GITHUB_API}/repos/{repo}/releases?per_page=100",
            headers=github_headers(token),
        )
    except NetError:
        return False
    if not isinstance(rels, list):
        return False

    for r in rels:
        if r.get("draft"):
            continue
        cands = _asset_candidates(r, out.get("_glob", ""))
        if not cands:
            continue
        hit = [a for a in cands if _variant_in(str(a.get("name")), variant)]
        if not hit:
            continue
        # 找到最后一个含该变体的发布
        pick = max(hit, key=lambda a: a.get("size") or 0)
        tag = str(r.get("tag_name") or r.get("name") or "")
        out["version"] = tag
        out["ok"] = True
        out["prerelease"] = bool(r.get("prerelease")) or is_prerelease_text(tag)
        out["updated"] = str(r.get("published_at") or "")[:19].replace("T", " ")
        out["release_url"] = str(r.get("html_url") or "")
        out["download_url"] = str(pick.get("browser_download_url") or "")
        out["asset_name"] = str(pick.get("name") or "")
        out["size"] = int(pick.get("size") or 0)
        out["sha256"] = str(pick.get("digest") or "").replace("sha256:", "")
        out["variant"] = variant
        out["pick_reason"] = (
            f"「{variant}」停更在 {tag}（更新的发布里已经没有它了），"
            f"这是该变体的最后一版 —— 要不要换成普通版由你决定"
        )
        out["note"] = (f"你的「{variant}」变体停更在这一版（更新的发布里已经没有它了）——"
                       f"这是该变体的最后一版。要不要换成普通版由你决定。")
        return True
    return False


def _variant_in(name: str, variant: str) -> bool:
    """资产名里是否含这个变体名（把 Nexus 的空格写法也能对上 GitHub 的连字符写法）。

    Nexus: "Vanilla Plus Megapack - Rows"
    GitHub: "Vanilla-Plus-Megapack-Rows-v35.zip"
    把两边都归一成"只留字母数字、转小写"再比，就都能命中。
    """
    if not variant:
        return False
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return norm(variant) in norm(name)


def _pick_asset(cands: list[dict], variant: str = "") -> dict:
    """在多个发布资产里挑一个：**先认变体，认不出再按大小**。

    这是修「Rows 版被换成普通版」的关键 —— 多个变体的大小几乎一样，
    "取最大的"实质上等于随机挑一个变体。
    """
    if variant:
        same = [a for a in cands if _variant_in(str(a.get("name")), variant)]
        if same:
            # 同变体里若还有多个（不同版本），取最大的那个（通常是完整包）
            return max(same, key=lambda a: a.get("size") or 0)
    return max(cands, key=lambda a: a.get("size") or 0)


def _github_via_atom(repo: str, out: dict, note: str = "") -> dict:
    """用 releases.atom 兜底（不占 API 配额，但只有版本号）。"""
    try:
        atom = http_get(
            f"https://github.com/{repo}/releases.atom", accept="application/atom+xml"
        )
    except NetError as e:
        out["error"] = friendly_error(str(e))
        return out
    entries = re.findall(r"<entry>(.*?)</entry>", atom, re.S)
    if not entries:
        out["error"] = "atom 源里没有 release 条目"
        return out
    first = entries[0]
    t = re.search(r"<title[^>]*>(.*?)</title>", first, re.S)
    u = re.search(r"<updated>(.*?)</updated>", first, re.S)
    l = re.search(r'<link[^>]*href="([^"]+)"', first)
    tag = t.group(1).strip() if t else ""
    out["version"] = tag
    out["ok"] = True
    out["prerelease"] = is_prerelease_text(tag)
    out["updated"] = (u.group(1) if u else "")[:19].replace("T", " ")
    out["release_url"] = l.group(1) if l else f"https://github.com/{repo}/releases"
    out["url"] = f"https://github.com/{repo}"
    out["note"] = (note + "（atom 源只有版本号，没有下载直链）").strip()
    return out


def _fnmatch(name: str, pattern: str) -> bool:
    rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, name, re.I) is not None


def github_search(query: str, token: str = "") -> list[dict]:
    """按名字搜仓库，返回候选列表（用于智能匹配）。

    两个实测教训，都会显著影响命中率：
      1. **不要加 sort=stars** —— 按星数排会把 awesome-python 这类
         万星无关仓库顶到最前；用 GitHub 默认的「最佳匹配」排序才对。
      2. **不要限定 in:name** —— HD2 的 mod 仓库名常不含 mod 全名
         （如 mod 叫 "Auto Reload"，仓库叫 HD2-Auto-Reload），
         限定只看名字会漏掉。
    """
    q = urllib.parse.quote_plus(query)
    try:
        data = http_get_json(
            f"{GITHUB_API}/search/repositories?q={q}&per_page=10",
            headers=github_headers(token),
        )
    except NetError:
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for it in data.get("items") or []:
        out.append(
            {
                "full_name": it.get("full_name") or "",
                "description": (it.get("description") or "")[:160],
                "stars": it.get("stargazers_count") or 0,
                "updated": str(it.get("pushed_at") or "")[:10],
            }
        )
    return out


# --------------------------------------------------------------------------
# Nexus Mods
# --------------------------------------------------------------------------
NEXUS_API = "https://api.nexusmods.com/v1"


def handoff_url(best: dict) -> str:
    """「用浏览器取文件」该打开哪个地址 —— GUI 和命令行共用同一份判断。

    各站点的差别（都是实测出来的，别随手改成统一取 file_url）：

      · Nexus 用**深链** `?tab=files&file_id=…&nmm=1` —— 打开就停在要更新的
        那个文件上，省掉"在文件列表里翻找"这一步。
      · ayakamods **故意用模组页而不是 /download**：未登录时 /download 直接
        返回 403 错误页，用户会以为工具坏了；模组页则好好地写着
        "Log in or sign up to download"，登录后按钮即可点。
      · GitHub 打开仓库/release 页（有直链的其实用「直接下载」更快）。
    """
    best = best or {}
    if (best.get("provider") or "").lower() == "nexus":
        return best.get("file_url") or best.get("url") or ""
    return best.get("url") or best.get("release_url") or ""


def nexus_headers(api_key: str) -> dict:
    return {
        "apikey": api_key,
        "Application-Name": "HD2 Mod Updater",
        "Application-Version": "1.0",
        "Accept": "application/json",
    }


def _pick_nexus_file(files: list[dict], variant: str = "") -> tuple[dict, str]:
    """在 Nexus 的文件列表里挑出「该更新到的那一个」。

    返回 (选中的文件, 为什么选它)。

    优先级（每一层都是为了让"多变体 mod"别下错东西）：
      1. 只要本地能看出变体名，就**严格挑同名变体**的最新版
         （只看 MAIN / OLD_VERSION 里名字对得上的）
      2. 对不上就退回"最新 MAIN"
      3. 连 MAIN 都没有，就退回"最新的任何文件"

    注意第 1 层故意**不跨变体兜底** —— 找不到同名变体时不能悄悄换成另一个，
    否则就是本 bug 的重演；这种情况交给调用方去提示用户。
    """
    def ver_ts(f: dict) -> int:
        return int(f.get("uploaded_timestamp") or 0)

    def is_main(f: dict) -> bool:
        return str(f.get("category_name") or "").upper() == "MAIN"

    if variant:
        v = variant.strip().lower()
        # 同时比「显示名」和「原始文件名」，两种命名方式都能命中
        same = [f for f in files
                if v in str(f.get("name") or "").lower()
                or v in str(f.get("file_name") or "").lower()]
        if same:
            pool = [f for f in same if is_main(f)] or same
            pool = sorted(pool, key=ver_ts, reverse=True)
            best = pool[0]
            # 顺带看看：同一 mod 里有没有**比它还新**的别的变体？
            # 有的话要提醒用户（作者可能停更了他用的那个变体），
            # 但**绝不能**直接换成那个 —— 那就是本 bug 本身。
            newest_any = sorted([f for f in files if is_main(f)] or files,
                                key=ver_ts, reverse=True)[0]
            if newest_any.get("file_id") != best.get("file_id"):
                return best, (
                    f"已按你装的变体「{variant}」选（{best.get('version')}）；"
                    f"该 mod 还有更新的其它变体 {newest_any.get('version')}"
                    f"「{newest_any.get('name')}」，要不要换由你决定"
                )
            return best, f"匹配到同变体「{variant}」"

    main = [f for f in files if is_main(f)]
    pool = main or files
    pool = sorted(pool, key=ver_ts, reverse=True)

    if variant:
        # 没找到同变体 —— **绝不能**静静换成另一个（那就是本 bug 本身）。
        # 明确说明"这不是你要的那个变体"，由界面去提醒用户。
        return pool[0], (
            f"这个 mod 现在**没有**「{variant}」变体了（作者可能已停更该变体）。"
            f"下面是同一 mod 的其它变体「{pool[0].get('name')}」"
            f"（v{pool[0].get('version')}），装之前请确认是不是你要的"
        )
    return pool[0], "最新 MAIN"


def nexus_check(mod_id: str, api_key: str, variant: str = "") -> dict:
    """查 Nexus 模组最新文件版本。需要个人 API key（免费申请）。

    ``variant`` 是本地安装的**变体名**（见 arsenal.local_variant）。
    同一个 mod 有多个并行变体时（如 Megapack 的 Rows 版），靠它挑对文件 ——
    否则会把用户装的 Rows 版更新成普通版。
    """
    out = _empty("nexus")
    mod_id = str(mod_id or "").strip()
    out["url"] = f"https://www.nexusmods.com/helldivers2/mods/{mod_id}"
    if not mod_id.isdigit():
        out["error"] = f"Nexus mod id 不是数字：{mod_id or '(空)'}"
        return out
    if not api_key:
        out["error"] = "没填 Nexus API key"
        out["note"] = "在设置里填一个个人 API key 就能自动查 Nexus"
        return out

    try:
        data = http_get_json(
            f"{NEXUS_API}/games/helldivers2/mods/{mod_id}/files.json",
            headers=nexus_headers(api_key),
        )
    except NetError as e:
        msg = str(e)
        if "401" in msg:
            out["error"] = "API key 无效或已过期"
        elif "403" in msg:
            out["error"] = "这个 mod 被隐藏/删除，或你的 key 无权访问"
        elif "404" in msg:
            out["error"] = f"Nexus 上没有这个 mod（#{mod_id}）"
        else:
            out["error"] = friendly_error(msg)
        return out

    if not isinstance(data, dict):
        out["error"] = "Nexus 返回了意外的数据"
        return out

    files = data.get("files") or []
    if not files:
        out["error"] = "该 mod 没有文件记录"
        return out

    # 挑哪个文件：**先认变体，再认新旧**
    #
    # 为什么不能只看 MAIN + 最新：同一个 mod 常并行发布多个变体，它们共用
    # 一个页面、共用版本号序列，只看"最新 MAIN"会**下错变体**。
    # 实测踩过：Vanilla Plus Megapack 的 Rows 版（用户装的）被下成普通版 ——
    # 两者都是 MAIN，只是 file_name 不同（`... - Rows ...` vs `... ...`）。
    top, why = _pick_nexus_file(files, variant)
    out["ok"] = True
    out["version"] = str(top.get("version") or "")
    out["asset_name"] = str(top.get("file_name") or top.get("name") or "")
    out["variant"] = str(top.get("name") or "")     # 变体名（Nexus 的「文件名」栏）
    out["pick_reason"] = why
    out["size"] = int(top.get("size_kb") or 0) * 1024
    ts = top.get("uploaded_timestamp")
    if ts:
        try:
            from datetime import datetime
            out["updated"] = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            pass
    out["note"] = "Nexus 下载需在浏览器登录后手动进行"
    # 记录精确的 file id，方便拼直达链接
    out["file_id"] = str(top.get("file_id") or "")
    # 深链：打开就直接落在 Files 页签的这一个文件上，不用自己去列表里找。
    # 格式依据：Nexus 官方/自家项目里都在用
    #   https://www.nexusmods.com/{game}/mods/{mod_id}?tab=files&file_id={file_id}
    #
    # 【不要再加 &nmm=1】—— 实测踩过：带上它页面会给「交给管理器下载」的入口，
    # 本机 nxm:// 又指向 HD2 Arsenal，于是用户双击后**直接被 Arsenal 接管**，
    # 而 Arsenal 当时并未登录 N 网，日志里是
    #   Error parsing NXM URL: ... AxiosError: Request failed with status code 401
    # 用户被迫手动改回浏览器下载。
    # 本工具的定位是「开好页面 + 收文件」，不该把下载甩给别的程序。
    if out["file_id"]:
        out["file_url"] = (
            f"https://www.nexusmods.com/helldivers2/mods/{mod_id}"
            f"?tab=files&file_id={out['file_id']}"
        )
    return out


def nexus_check_many(mod_ids: list[str], api_key: str) -> dict[str, dict]:
    """批量查（Nexus 有 updated.json 聚合接口，但为稳妥逐个查并缓存）。"""
    res: dict[str, dict] = {}
    for mid in mod_ids:
        res[mid] = nexus_check(mid, api_key)
    return res


# --------------------------------------------------------------------------
# ayakamods
# --------------------------------------------------------------------------
AYAKA_BASE = "https://ayakamods.com"


def ayaka_check(slug_or_url: str) -> dict:
    """抓 ayakamods 模组页，取版本号与「最后更新」时间。

    slug_or_url 可以是完整 URL，也可以是 'hd2runtime.4260' 这样的 slug。
    """
    out = _empty("ayaka")
    raw = (slug_or_url or "").strip()
    if not raw:
        out["error"] = "没给 ayakamods 地址"
        return out
    if raw.startswith("http"):
        url = raw if raw.endswith("/") else raw + "/"
        m = re.search(r"/mods/([a-z0-9\-\.]+)/", url, re.I)
        slug = m.group(1) if m else ""
    else:
        slug = raw.strip("/")
        url = f"{AYAKA_BASE}/mods/{slug}/"
    out["url"] = url
    out["slug"] = slug

    try:
        html = http_get(url, accept="text/html")
    except NetError as e:
        out["error"] = friendly_error(str(e))
        return out

    # 被 Cloudflare 挑战挡住时页面会明显不同
    if re.search(r"Just a moment|checking your browser|cf_chl", html, re.I):
        out["error"] = "ayakamods 正在要求人机验证（Cloudflare）——过一会儿再试"
        return out
    if re.search(r"page cannot be found|This mod.*can'?t be found", html, re.I):
        out["error"] = "该页面不存在（mod 可能被删除或改名）"
        return out

    def field(*patterns: str) -> str:
        for p in patterns:
            m = re.search(p, html, re.S | re.I)
            if m:
                v = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if v:
                    return v
        return ""

    version = field(r"<dt>\s*Version\s*</dt>\s*<dd[^>]*>(.*?)</dd>")
    updated = field(
        r"<dt>\s*Last update\s*</dt>\s*<dd[^>]*>.*?title=\"([^\"]+)\"",
        r"<dt>\s*Last update\s*</dt>\s*<dd[^>]*>(.*?)</dd>",
    )
    title = field(r"<h1 class=\"p-title-value\">(.*?)</h1>")
    out["title"] = title
    out["version"] = version
    out["updated"] = updated

    # 未登录时下载按钮是禁用的 span，这里明确告知
    if re.search(r"Log in or sign up to download", html, re.I):
        out["needs_login"] = True
        out["note"] = "下载需要登录 ayakamods（未登录时站点不提供下载链接）"

    # 下载页：登录后点「Download」会走这里。列出来方便开网页后少找一步。
    # （未登录时访问它会返回 403，所以**只作为网址展示/复制**，不拿它当直链。）
    if slug:
        out["file_url"] = f"{AYAKA_BASE}/mods/{slug}/download"

    if not version and not updated:
        out["error"] = "页面结构变了，没读到版本信息（站点可能改版）"
        return out
    out["ok"] = True
    return out


def ayaka_list_page(page: int = 1) -> list[dict]:
    """抓 HD2 模组列表页，返回 [{slug,title,author,desc}]（做本地索引用）。

    页面的卡片结构（2026-10 实测）：
        <div class="cardModMainWrapperLink js-inlineModContainer" data-author="X">
          <a href="/mods/<slug>/" class="modCardLink">
            <div class="cardModMain">
              <div class="cMMPictureWrapper"><img alt="<标题>" ...></div>
              <div class="cMMBody">
                <div class="cMMBodyTitle">标题</div>
                <div class="cMMBodyText">描述</div>
              </div>
    """
    url = (
        f"{AYAKA_BASE}/mods/games/helldivers-2.119/"
        f"?order=last_update&direction=desc&page={page}"
    )
    try:
        html = http_get(url, accept="text/html")
    except NetError:
        return []
    if re.search(r"Just a moment|checking your browser", html, re.I):
        return []

    items: list[dict] = []
    seen: set[str] = set()

    # 按卡片切块，逐块抓 slug / 标题 / 作者 / 描述
    blocks = re.split(r'class="cardModMainWrapperLink', html)
    for blk in blocks[1:]:
        m_slug = re.search(r'href="/mods/([a-z0-9\-\.]+)/"\s+class="modCardLink"', blk, re.I)
        if not m_slug:
            m_slug = re.search(r'href="/mods/([a-z0-9\-\.]+)/"', blk, re.I)
        if not m_slug:
            continue
        slug = m_slug.group(1)
        if slug in seen:
            continue
        seen.add(slug)

        m_auth = re.search(r'data-author="([^"]*)"', blk)
        # 作者在卡片开头（被切掉了），用卡块内的用户名链接兜底
        author = m_auth.group(1) if m_auth else ""
        if not author:
            m_u = re.search(r'href="/members/[^"]+"', blk)
            if m_u:
                m_u2 = re.search(r'<a href="/members/([^\./"]+)', blk)
                author = m_u2.group(1) if m_u2 else ""

        m_t = re.search(r'class="cMMBodyTitle"[^>]*>([^<]{1,200})', blk)
        title = m_t.group(1).strip() if m_t else ""
        if not title:
            m_img = re.search(r'<img[^>]*alt="([^"]{1,200})"', blk)
            if m_img:
                title = m_img.group(1).strip()

        m_d = re.search(r'class="cMMBodyText"[^>]*>([^<]{1,300})', blk)
        desc = m_d.group(1).strip() if m_d else ""

        items.append(
            {
                "slug": slug,
                "title": title or slug.rsplit(".", 1)[0].replace("-", " "),
                "author": author,
                "desc": desc,
            }
        )
    return items


def ayaka_crawl(max_pages: int = 30) -> list[dict]:
    """把 HD2 模组列表整个爬下来（用于本地搜索索引）。"""
    allitems: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        got = ayaka_list_page(page)
        if not got:
            break
        fresh = 0
        for it in got:
            if it["slug"] not in allitems:
                allitems[it["slug"]] = it
                fresh += 1
        if fresh == 0:
            break
    return list(allitems.values())
