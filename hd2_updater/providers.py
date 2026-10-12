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

import html as _html
import re
import urllib.parse

from .util import (
    NetError,
    http_get,
    http_get_json,
    http_post_json,
    is_cf_block,
    is_prerelease_text,
    parse_date_only,
    friendly_error,
    short_err,
)

GITHUB_API = "https://api.github.com"
# N 网的按名字搜只能走 GraphQL v2（REST v1 没有搜索端点，实测 mods/search 等全 404）
NEXUS_GQL = "https://api.nexusmods.com/v2/graphql"
GAME_DOMAIN = "helldivers2"

_NEXUS_SEARCH_Q = """
query Search($filter: ModsFilter, $count: Int) {
  mods(filter: $filter, count: $count) {
    nodes { modId name version author downloads }
  }
}
"""


def nexus_search(name: str, api_key: str, count: int = 6,
                 timeout: int = 12, retries: int = 1) -> list[dict]:
    """在 N 网**按名字搜**，返回候选列表（按站上相关性排序）。

    为什么需要它：N 网 mod 编号只存在于 N 网自己的下载命名里；从网盘/整合包导入的
    mod 本地**一点编号线索都没有**（manifest 无 Sources、Arsenal 无 nexusData、
    目录名也是改过的），这时只能按名字去站上找。

    ★ **返回的是候选，不是答案。** 实测："Vanilla Plus Megapack" 的第一位返回的是
    它的**中文汉化版**，真身排第二 —— 所以调用方必须做名字核对与置信度判定，
    **绝不能拿第一位当结论**。

    搜不到/出错都返回空列表（这条路是"尽力而为"，不该让整次检查失败）。
    """
    nm = (name or "").strip()
    if not nm or not api_key:
        return []
    payload = {"query": _NEXUS_SEARCH_Q, "variables": {
        "filter": {"gameDomainName": {"value": GAME_DOMAIN, "op": "EQUALS"},
                   "name": {"value": nm, "op": "WILDCARD"}},
        "count": max(1, min(int(count or 6), 20)),
    }}
    try:
        data = http_post_json(NEXUS_GQL, payload,
                              headers=nexus_headers(api_key),
                              timeout=timeout, retries=retries)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict) or data.get("errors"):
        return []
    nodes = (((data.get("data") or {}).get("mods") or {}).get("nodes")) or []
    out = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("modId"):
            continue
        out.append({
            "mod_id": str(n.get("modId")),
            "name": str(n.get("name") or ""),
            "version": str(n.get("version") or ""),
            "author": str(n.get("author") or ""),
            "downloads": int(n.get("downloads") or 0),
        })
    return out


def unescape_html(text: str) -> str:
    """还原 HTML 实体（&#039; &amp; 等）。

    站点上中文/符号的标题经常写成实体，不还原的话索引里存的是
    「Helldivers&#039; Ammunition」这种，拿去跟本地名字比相似度必然偏低。
    """
    return _html.unescape(str(text or ""))


# --------------------------------------------------------------------------
# 失败原因的结构化分类
# --------------------------------------------------------------------------
# ★ 为什么要有这个（2026-10-12，用户报的 bug）：
#   以前下游（gui._error_note / engine.evaluate）靠**在错误文案里找子串**来判断
#   是哪一类失败。结果：
#     · provider 写的是「这个 mod 被**隐藏/删除**，或你的 key 无权访问」，
#       而下游找的子串是 `被删除` —— 对不上 → 那条分支**永远不生效**；
#       继续往下落到 `"key" in 文案` → 把"这个 mod 已经没了"显示成
#       **「需要 API key」**（用户明明填了 key，只会去设置页反复确认）。
#     · 上游以后改一个字（比如"已删除"→"下架"），下游会**静默失效**。
#   所以改成：**失败原因由产生它的地方打上结构化标签**，下游只认这个标签，
#   不再匹配文案。文案怎么改都不会再影响分类。
#
# 取值（下游按键分流，"用户下一步该干什么"排序）：
#   "gone"      这个 mod 没了 / 页面不存在了 → 换来源，别再查这个页面
#   "site"      站点不让查（Cloudflare 硬封锁等）→ 换节点 / 过会儿再试
#   "net"       网络层问题（超时 / 连不上）→ 过会儿再试
#   "rate"      被限流（429）→ 过会儿再试
#   "no_key"    用户没填凭据 → 去设置里填
#   "bad_key"   凭据无效 / 过期 → 去设置里换一个
#   "noperm"    有凭据但没权限看这个内容 → 多半是 mod 被隐藏/删除
#   "shape"     能连上但内容读不出来（站点改版 / 结构变了）→ 报给作者
#   "other"     其他
ERR_KINDS = ("gone", "site", "net", "rate", "no_key", "bad_key", "noperm",
             "shape", "other")


def _err(out: dict, kind: str, msg: str) -> dict:
    """给一个结果打上失败原因（kind 给程序判断，msg 给用户看）。"""
    out["ok"] = False
    out["err_kind"] = kind
    out["error"] = msg
    return out


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
        # 失败原因的结构化分类（下游靠它分流，**不要**再去匹配 error 文案）
        "err_kind": "",
        "note": "",
    }


def kind_of_http(code: int, body: str = "") -> str:
    """HTTP 状态码 + 响应体 → err_kind。

    ★ 关键事实（2026-10-12 带真实 key 实测 N 网）：
      · 已删除 / 下架的 mod → **403**，响应体 ``{"message":"Mod not available: N"}``
      · 编号不存在        → **404**，响应体 ``{"message":"No Mod Found: N"}``
      · 没带 key          → **401**，``{"message":"Please provide an authentication method"}``
    所以**光看状态码分不出"这个 mod 没了"和"你没权限"** —— 403 两种都可能，
    必须结合响应体。NetError 里带的 body 正好够用。
    """
    low = str(body or "").lower()
    if code == 404 or "no mod found" in low or "not found" in low:
        return "gone"
    if code == 403:
        # 明确的"mod 不存在/不可用" → gone；其余 403 归"没权限/被隐藏"
        if "mod not available" in low or "mod not found" in low:
            return "gone"
        if is_cf_block(body):
            return "site"
        return "noperm"
    if code == 401:
        return "no_key"          # 对 N 网 API 来说 401 = 没给凭据
    if code == 429:
        return "rate"
    if code in (502, 503, 504, 522, 525):
        return "net"
    if code == 418 or code == 402:
        return "other"
    if code and code >= 500:
        return "net"
    return "other"


def kind_of_net_error(msg: str) -> str:
    """把 ``NetError`` 的文本翻成 err_kind（这里是**唯一**允许匹配文案的地方，
    而且只在"底层网络异常 → 分类"这一层，不跨模块传播）。"""
    s = str(msg or "")
    low = s.lower()
    if "超时" in s or "timed out" in low or "timeout" in low:
        return "net"
    if "不可达" in s or "无法连接" in s or "connection" in low:
        return "net"
    if "限流" in s or "429" in s:
        return "rate"
    if "cloudflare" in low or is_cf_block(s):
        return "site"
    return "other"


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
    """查 GitHub 仓库最新 release（正式版优先，**没有正式版时也能看到测试版**）。

    ★ 关键：**不要用 `/releases/latest` 当主路径**。
    GitHub 官方语义是 "The latest release is the most recent **non-prerelease**,
    non-draft release" —— 也就是说 latest **明确跳过 pre-release**。
    踩过的坑（2026-10-11，用户质疑「既然 GitHub 上 r42 也是测试版，那不应该
    优先在 GitHub 上下载吗？」）：本仓库有 `r42-ui-recovery-test`（作者标了
    prerelease=True）和 `r41`（正式版），而 `/releases/latest` 只返回 `r41`
    → 工具**根本看不到 r42 测试版**，于是显示成"只有 a 站有测试版更新"，
    看起来像"GitHub 的 pre-release 没被识别"。实际上是我读错了端点。

    现在的顺序：
      1. `/releases?per_page=N`（一次拿到全部，含 prerelease）→ 按 GitHub 的
         发布顺序（它已按 created_at 倒序），**优先取最新的正式版**；
         若没有任何正式版，取最新的预发布版并标记 prerelease。
      2. 列表拿不到（限流等）→ 退 atom 源（不占配额）。

    这样"正式版优先、测试版兜底"的语义由**一处**（本函数）保证，
    engine.evaluate 再按 stable/beta 分类展示，两边一致。
    """
    out = _empty("github")
    repo = (repo or "").strip().strip("/")
    m = re.search(r"github\.com/([^/]+/[^/#?\s]+)", repo)
    if m:
        repo = m.group(1)
    if not repo or "/" not in repo:
        return _err(out, "other",
                    f"仓库名不对：{repo or '(空)'}（应形如 作者/仓库）")
    repo = repo.removesuffix(".git")
    out["url"] = f"https://github.com/{repo}"

    info = None
    try:
        rels = http_get_json(
            f"{GITHUB_API}/repos/{repo}/releases?per_page=15",
            headers=github_headers(token),
        )
    except NetError as e:
        msg = str(e)
        if "限流" in msg or "403" in msg:
            # 退到 atom（不占配额，但没有资产列表）
            return _github_via_atom(repo, out, note="REST 配额受限，改用 atom 源")
        if "404" in msg:
            return _err(out, "gone", f"GitHub 上没有这个仓库/发布（{repo}）")
        return _err(out, kind_of_net_error(msg) if "超时" in msg else "other",
                    friendly_error(msg))
    except Exception as e:  # noqa: BLE001
        return _err(out, "other", f"意外错误：{short_err(e)}")

    if not isinstance(rels, list):
        return _err(out, "shape", "GitHub 返回了意外的数据")
    rels = [r for r in rels if isinstance(r, dict) and not r.get("draft")]
    if not rels:
        return _err(out, "gone", "该仓库没有任何 release")
    # GitHub 已按发布时间倒序返回 → 第一项就是最新
    stable = [r for r in rels if not r.get("prerelease")]
    if stable:
        info = stable[0]
    else:
        info = rels[0]
        out["note"] = "该仓库只有预发布版，取的是最新的那个"

    # ★★ 附带把"最新的预发布版"也报出来（如果有）。
    # 为什么需要（2026-10-11 用户质疑「既然 GitHub 上 r42 也是测试版，
    # 那不应该优先在 GitHub 上下载吗？」）：
    # 主字段 version 走"正式版优先"（这样"已最新"的判定不会被测试版干扰），
    # 但如果**仓库里还有一个更新的测试版**，工具在"只有测试版可更"这一档
    # 也该把 GitHub 作为候选列出来 —— 否则会显示成"只有 a 站有测试版"，
    # 让人误以为 GitHub 的 pre-release 没被识别。
    # 这里只填 `prerelease_latest`（版本/链接/资产），由上层决定怎么用。
    pre = next((r for r in rels if r.get("prerelease")), None)
    if pre is not None:
        ptag = str(pre.get("tag_name") or pre.get("name") or "")
        if ptag and ptag != str(info.get("tag_name") or ""):
            out["prerelease_latest"] = {
                "version": ptag,
                "url": str(pre.get("html_url") or ""),
                "published": str(pre.get("published_at") or "")[:19].replace("T", " "),
                "assets": [
                    {"name": str(a.get("name") or ""),
                     "url": str(a.get("browser_download_url") or ""),
                     "size": a.get("size") or 0}
                    for a in (pre.get("assets") or [])
                    if str(a.get("name") or "").lower().endswith((".zip", ".7z", ".rar"))
                ],
            }

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
    """这段文字会显示在表格的「说明」列里 —— 不能带 markdown 星号。"""
    return (f"你这个「{variant}」变体在这个新版里没有（作者可能已经停更该变体），"
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
        return _err(out, kind_of_net_error(str(e)), friendly_error(str(e)))
    entries = re.findall(r"<entry>(.*?)</entry>", atom, re.S)
    if not entries:
        return _err(out, "gone", "这个仓库没有任何 release（atom 源是空的）")
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
        # （这段文字会**原样显示**在表格的「说明」列里，tkinter 不渲染
        #   markdown，所以不能写星号 —— 会显示成「现在**没有**」。）
        return pool[0], (
            f"这个 mod 现在没有「{variant}」变体了（作者可能已停更该变体）。"
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
        return _err(out, "gone",
                    f"Nexus mod id 不是数字：{mod_id or '(空)'}")
    if not api_key:
        r = _err(out, "no_key", "没填 Nexus API key")
        r["note"] = "在设置里填一个个人 API key 就能自动查 Nexus"
        return r

    try:
        data = http_get_json(
            f"{NEXUS_API}/games/helldivers2/mods/{mod_id}/files.json",
            headers=nexus_headers(api_key),
        )
    except NetError as e:
        msg = str(e)
        # ★ 分类交给 kind_of_http —— 它**看响应体**，因为 N 网对"已删除的 mod"
        #   返回的是 **403 `Mod not available`**，和"你没权限"同一个状态码。
        #   以前这里把 403 一律说成"被隐藏/删除**或**你的 key 无权访问"，
        #   两种含义混在一句里，下游再也分不开（issue：反馈说成"没填 key"）。
        m = re.search(r"HTTP (\d{3})", msg)
        code = int(m.group(1)) if m else 0
        kind = kind_of_http(code, msg) if code else kind_of_net_error(msg)
        if kind == "gone":
            return _err(out, "gone",
                        f"这个 mod 在 Nexus 上已经没有了（#{mod_id}）——"
                        "多半是作者删了或下架了")
        if kind == "noperm":
            return _err(out, "noperm",
                        f"看不到这个 mod（#{mod_id}）——它被隐藏/删除了，"
                        "或者你的 API key 没权限看它")
        if kind == "no_key":
            # 带了 key 还 401 → 是 key 本身无效（不是"没填"）
            return _err(out, "bad_key", "API key 无效或已过期")
        return _err(out, kind, friendly_error(msg))

    if not isinstance(data, dict):
        return _err(out, "shape", "Nexus 返回了意外的数据")

    files = data.get("files") or []
    if not files:
        # 200 说明页面还在（mod 没被删），只是没有任何文件 —— 别误报成"已删除"
        return _err(out, "other", "这个 mod 在 Nexus 上没有任何文件记录")

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

# /mods/ 下面这些段是站点的功能页，不是模组。用户很容易把「列表页」地址
# 粘进来（他那条地址栏里就是 /mods/games/helldivers-2.119/）。
# 以前会把 "games" 当 slug 去查，然后报"页面结构变了"，把人引到错误方向。
AYAKA_RESERVED = ("games", "categories", "tags", "members", "history", "download",
                  "search", "mods", "authors", "faq")

# slug 的字符范围：**不能**写死成 [a-z0-9-.] —— 实测站点上约三分之一的
# 模组是中文名（尤其中文社区的汉化/功能 mod），它们的 slug 是百分号编码的，
# 形如 %E6%9C%BA%E7%94%B2%E9%83%A8%E4%BD%8D%E8%A1%80%E9%87%8FHUD.4775。
# 写死字符集会把这些 mod 整个漏掉（索引里一个都进不去、手动填也认不出）。
_AYAKA_SLUG = r'[^"/?#]+'


def ayaka_decode(slug: str) -> str:
    """把 slug 里的百分号编码还原成人能看的字（显示、比较用）。"""
    return urllib.parse.unquote(str(slug or ""))


def ayaka_encode(slug: str) -> str:
    """把 slug 规范成可安全放进 URL 的形态。

    站点的 href 里已经是百分号编码；但用户从地址栏复制过来的可能是
    **原样中文**（浏览器会显示解码后的样子）。裸中文直接塞进
    urllib.request 会抛 UnicodeEncodeError，所以统一在这里规范化。

    做法是「先解码、再整体编码」，**不把 ``%`` 放进 safe**。
    为什么：如果 safe 里保留 ``%``，那么输入里本来就有的裸百分号
    （如 ``100%.456``）会被原样留下来，下一次再编码时它又被当成转义前缀
    吃掉后面的字符 —— 实测 ``%25477.123`` 会变成 ``%477.123``、
    再变 ``G7.123``，越编越烂。去掉 safe 里的 ``%`` 之后，
    裸百分号会被规范成 ``%25``，函数就是**幂等**的了
    （encode(encode(x)) == encode(x)），这对"用户反复粘贴/反复保存"很要紧。
    """
    s = str(slug or "").strip().strip("/")
    # quote 默认就会保留字母/数字/_.-~，safe 里不必再列一遍
    return urllib.parse.quote(urllib.parse.unquote(s), safe="")


def ayaka_slug(text: str) -> str:
    """从「完整网址」或「裸 slug」里取出 slug（保持站点上的原样编码）。

    容忍末尾没有斜杠、带 ?query 尾巴（用户从地址栏复制的常见形态）。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    m = re.search(r"/mods/(" + _AYAKA_SLUG + r")(?:/|\?|#|$)", t, re.I)
    if m:
        return m.group(1)
    if t.startswith("http"):
        return ""
    return t.strip("/")


def ayaka_url(slug: str) -> str:
    """拼出模组页地址（唯一出处，别再各处手写 f-string）。"""
    return f"{AYAKA_BASE}/mods/{ayaka_encode(slug)}/"


def ayaka_is_reserved(slug: str) -> bool:
    """这个 slug 是不是 /mods/ 下的功能页（不是模组）。"""
    return ayaka_decode(slug).strip().lower() in AYAKA_RESERVED


# --------------------------------------------------------------------------
# 从用户粘贴的网址判断来源站点
# --------------------------------------------------------------------------
# GitHub 的这几段不是仓库路径（用户可能粘了令牌页/设置页的地址）
_GITHUB_NOT_REPO = (
    "settings", "features", "about", "pricing", "marketplace",
    "orgs", "login", "signup", "notifications", "explore",
)


def detect_source(text: str) -> dict | None:
    """从用户粘贴的内容判断来源站点。

    返回 ``{"provider","id","url","display"}``，认不出返回 None。

    设计意图：用户只需粘贴模组的**页面地址**，不用管是哪个站点、不用管
    该填 ID 还是仓库名 —— 由程序识别。也容忍偷懒写法（不带 https://、
    带尾巴参数、GitHub 带 /releases/tag/... 等）。

    **专门支持中文/特殊符号的 ayakamods 地址**：实测站点上约三分之一的
    模组是中文名，它们的地址长这样（浏览器地址栏里显示的就是解码后的样子）：
        https://ayakamods.com/mods/机甲部位血量HUD.4775/
    复制过来可能是上面这种**原样中文**（浏览器会解码显示），也可能是
    页面源码里的百分号编码形态。两种都必须认出来 —— 旧实现的正则写死了
    [a-z0-9\\-]，中文地址一律返回 None，用户会被告知"认不出这是哪个站点的
    链接"，而地址明明是好的（他自己手动指定来源这条路就被堵死了）。

    ``display`` 是给人看的地址（中文还原后的），``url`` 是真正要用的形态。
    """
    t = (text or "").strip()
    if not t:
        return None

    # --- 先按用户原样判断「裸写法」（不带 http 的一律补 https://）---
    # README 和对话框里都给用户写过「GitHub 填 作者/仓库、Nexus 填数字 ID」，
    # 但老实现只认完整网址 —— 用户照着文档填反而被告知"认不出"，很打脸。
    # 这里把这几种裸写法也一并认掉。
    if not re.match(r"^https?://", t, re.I):
        bare = t.strip().strip("/")

        # 带 "mods/" 前缀的（用户从地址栏里只拖了一半的常见形态）：
        # 先按 ayakamods 判断，**不要**掉进下面的「作者/仓库」分支 ——
        # 否则 "mods/hd2runtime.4260" 会被编成
        # github.com/mods/hd2runtime.4260 这种根本不存在的仓库，
        # 而且因为标了 manual 就再也不会被自动纠正。
        if bare.lower().startswith("mods/"):
            rest = bare[5:].strip("/")
            if re.fullmatch(r"\d{1,8}", rest):
                url = f"https://www.nexusmods.com/helldivers2/mods/{rest}"
                return {"provider": "nexus", "id": rest, "url": url,
                        "display": url}
            if re.fullmatch(r".+\.\d+", rest) and not ayaka_is_reserved(rest):
                enc = ayaka_encode(rest)
                return {"provider": "ayaka", "id": enc, "url": ayaka_url(enc),
                        "display": f"{AYAKA_BASE}/mods/{ayaka_decode(enc)}/"}
            return None

        # 裸 slug：形如 "名字.数字"（中文名也满足）
        if ("/" not in bare and re.fullmatch(r".+\.\d+", bare)
                and re.search(r"[^\d.]", bare)
                and not ayaka_is_reserved(bare)):
            enc = ayaka_encode(bare)
            return {"provider": "ayaka", "id": enc,
                    "url": ayaka_url(enc),
                    "display": f"{AYAKA_BASE}/mods/{ayaka_decode(enc)}/"}

        # N 网裸编号
        m = re.fullmatch(r"(\d{1,8})", bare)
        if m:
            mid = m.group(1)
            url = f"https://www.nexusmods.com/helldivers2/mods/{mid}"
            return {"provider": "nexus", "id": mid, "url": url, "display": url}

        # GitHub 裸「作者/仓库」
        m = re.fullmatch(r"([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?", bare)
        if m and m.group(1).lower() not in _GITHUB_NOT_REPO:
            repo = f"{m.group(1)}/{m.group(2)}"
            url = f"https://github.com/{repo}"
            return {"provider": "github", "id": repo, "url": url, "display": url}

        t = "https://" + t.lstrip("/")

    low = t.lower()

    # --- GitHub：只要域名对，取 作者/仓库 ---
    if "github.com" in low:
        m = re.search(r"github\.com/([^/]+)/([^/#?\s]+)", t, re.I)
        if not m:
            return None
        repo = re.sub(r"\.git$", "", f"{m.group(1)}/{m.group(2)}")
        if repo.split("/")[0].lower() in _GITHUB_NOT_REPO:
            return None
        url = f"https://github.com/{repo}"
        return {"provider": "github", "id": repo, "url": url, "display": url}

    # --- Nexus：取数字 ID ---
    if "nexusmods.com" in low:
        m = re.search(r"/mods/(\d+)", t) or re.search(r"[?&]id=(\d+)", t)
        if not m:
            return None
        mid = m.group(1)
        url = f"https://www.nexusmods.com/helldivers2/mods/{mid}"
        return {"provider": "nexus", "id": mid, "url": url, "display": url}

    # --- ayakamods：取 slug（含中文/百分号编码两种形态）---
    if "ayakamods.com" in low:
        slug = ayaka_slug(t)
        if not slug or ayaka_is_reserved(slug):
            return None
        enc = ayaka_encode(slug)
        return {"provider": "ayaka", "id": enc,
                "url": ayaka_url(enc),
                "display": f"{AYAKA_BASE}/mods/{ayaka_decode(enc)}/"}
    return None


def ayaka_check(slug_or_url: str) -> dict:
    """抓 ayakamods 模组页，取版本号与「最后更新」时间。

    slug_or_url 可以是完整 URL（含中文/特殊符号，编码与否都行），
    也可以是 'hd2runtime.4260' 这样的裸 slug。
    """
    out = _empty("ayaka")
    raw = (slug_or_url or "").strip()
    if not raw:
        return _err(out, "other", "没给 ayakamods 地址")
    slug = ayaka_slug(raw)
    if not slug:
        out["url"] = raw
        return _err(out, "other",
                    "这个地址里没看出模组编号。请粘模组页面的地址"
                    "（形如 ayakamods.com/mods/名字.数字/）。")
    if ayaka_is_reserved(slug):
        out["url"] = raw
        return _err(out, "other",
                    "这是 ayakamods 的列表页/功能页地址，不是某个模组的页面。"
                    "请先打开那个模组，再复制它的地址。")
    slug = ayaka_encode(slug)
    url = ayaka_url(slug)
    out["url"] = url
    out["slug"] = slug

    try:
        html = http_get(url, accept="text/html")
    except NetError as e:
        return _err(out, kind_of_net_error(str(e)), friendly_error(str(e)))

    # 被 Cloudflare 挡住时页面会明显不同。
    # ★ 2026-10-11 实测补全：以前只认 "Just a moment"，但站点对本机出口回的是
    #   "Attention Required! / you have been blocked"（IP 级硬封锁）——
    #   那种响应会掉进下面的解析逻辑，最后报一个含糊的错，用户不知道该干嘛。
    if is_cf_block(html):
        return _err(out, "site",
                    "ayakamods 把这个请求挡在了门外（Cloudflare）——"
                    "不是你的网络断了。\n"
                    "实测这种封锁连真实浏览器都可能过不去，通常是站点不接受当前"
                    "出口 IP 的自动请求。\n"
                    "可以试：① 换一个代理节点 / 关掉代理再试；② 过一会儿再试；"
                    "③ 直接到浏览器打开这个页面看版本，再用「指定来源…」手工填。")
    if re.search(r"page cannot be found|This mod.*can'?t be found", html, re.I):
        return _err(out, "gone", "该页面不存在（mod 可能被删除或改名）")

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
    # 标题：页面上的真实结构是 `<h1> 名字 <span class="u-muted">版本</span> </h1>`
    # （**不带** class="p-title-value" —— 旧正则一直没匹配上，标题永远是空的）。
    # 先把版本那个 span 去掉再取文本，否则标题会拖着一个版本号，
    # 拿去跟本地名字比相似度会被拉低。
    title = field(r'<meta property="og:title" content="([^"]*)"')
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        if m:
            inner = re.sub(r'<span[^>]*class="u-muted"[^>]*>.*?</span>', " ",
                           m.group(1), flags=re.S | re.I)
            title = re.sub(r"<[^>]+>", "", inner).strip()
    title = re.sub(r"\s*-\s*Mod for Helldivers 2\s*$", "", title, flags=re.I)
    out["title"] = unescape_html(title)
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
        return _err(out, "shape",
                    "页面结构变了，没读到版本信息（站点可能改版）")
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

    slug 一律**保持站点上的百分号编码**（如 %E6%9C%BA%E7%94%B2....4775）——
    它是 URL 里真正要用的形态，拿去请求不会再被编码一次。
    给人看的时候用 ayaka_decode() 还原。
    """
    url = (
        f"{AYAKA_BASE}/mods/games/helldivers-2.119/"
        f"?order=last_update&direction=desc&page={page}"
    )
    try:
        html = http_get(url, accept="text/html")
    except NetError:
        return []
    # ★ 硬封锁也要当成"这页没抓到"：以前只认 "Just a moment"，
    #   而 "you have been blocked" 的封锁页里**没有** modCardLink，
    #   于是返回空列表 → 上层把索引重建"成功"成 0 条，看起来像站点没模组。
    if is_cf_block(html):
        return []

    items: list[dict] = []
    seen: set[str] = set()

    # 按卡片切块，逐块抓 slug / 标题 / 作者 / 描述
    blocks = re.split(r'class="cardModMainWrapperLink', html)
    for blk in blocks[1:]:
        m_slug = re.search(
            r'href="/mods/(' + _AYAKA_SLUG + r')/"\s+class="modCardLink"', blk, re.I)
        if not m_slug:
            m_slug = re.search(r'href="/mods/(' + _AYAKA_SLUG + r')/"', blk, re.I)
        if not m_slug:
            continue
        slug = m_slug.group(1)
        if slug in seen or ayaka_is_reserved(slug):
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
        title = unescape_html(m_t.group(1).strip()) if m_t else ""
        if not title:
            m_img = re.search(r'<img[^>]*alt="([^"]{1,200})"', blk)
            if m_img:
                title = unescape_html(m_img.group(1).strip())

        m_d = re.search(r'class="cMMBodyText"[^>]*>([^<]{1,300})', blk)
        desc = unescape_html(m_d.group(1).strip()) if m_d else ""

        items.append(
            {
                "slug": slug,
                # 标题取不到时，用**解码后**的 slug 兜底 —— 原来直接拿
                # 编码串把 "-" 换成空格，中文 mod 会显示成一串 %E6%9C...
                "title": title or ayaka_decode(
                    slug.rsplit(".", 1)[0].replace("-", " ")),
                "author": author,
                "desc": desc,
            }
        )
    return items


def ayaka_crawl(max_pages: int = 80) -> list[dict]:
    """把 HD2 模组列表整个爬下来（用于本地搜索索引）。

    max_pages 只是**上限**：翻到空页（或整页都是重复的）就停，所以调高它
    不花额外时间。原来写 30 页，而站点 2026-10 已经 44 页了 —— 排在后面的
    老模组会整段进不了索引，正是"有些模组识别不出来"的一个来源。
    """
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
