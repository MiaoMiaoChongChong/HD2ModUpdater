# -*- coding: utf-8 -*-
"""智能匹配：替一个已安装的 mod 找出它在网上的发布页。

匹配依据按可信度从高到低：
  1. mod 自带 manifest 里作者写的 Sources / NexusData（必然准确）
  2. Arsenal 库里的 nexusData.modId（N 网下载的必然准确）
  3. 目录名里解析出的 Nexus mod id（N 网命名规则）
  4. GitHub 搜索（实测准确率很高）
  5. ayakamods 本地索引 + 模糊匹配
  6. 都失败 -> 交给用户手动填

用户的优先级要求：GitHub > Nexus > ayakamods。
"""
from __future__ import annotations

import json
import os
import re
import threading
from difflib import SequenceMatcher

from . import providers
from .arsenal import read_manifest_sources
from .util import app_data_dir

# 用户要求的站点优先级
PROVIDER_PRIORITY = {"github": 0, "nexus": 1, "ayaka": 2}

# 名字里这些词对搜索没用，去掉能显著提高命中率
STOPWORDS = {
    "v", "version", "mod", "hd2", "helldivers", "helldivers2", "update",
    "final", "fixed", "fix", "new", "the", "a", "of", "for", "and",
}


def normalize(name: str) -> str:
    """把 mod 名标准化，方便比较。"""
    s = str(name or "").lower()
    s = re.sub(r"[_\-\.\[\]\(\)\{\}（）【】]+", " ", s)
    s = re.sub(r"\b(v?\d+(\.\d+)*)\b", " ", s)     # 去版本号
    s = re.sub(r"\s+", " ", s).strip()
    return s


def keywords(name: str) -> list[str]:
    """抽出有意义的词，用于站点搜索。"""
    words = [w for w in normalize(name).split() if w and w not in STOPWORDS]
    # 太短的单词单独搜没意义，但整串保留
    return words


def similarity(a: str, b: str) -> float:
    """两个名字的相似度 0~1。"""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # 一个包含另一个，给高分
    if na in nb or nb in na:
        return 0.9
    return SequenceMatcher(None, na, nb).ratio()


# 能证明「这是绝地潜兵2 的东西」的词
HD2_EVIDENCE = (
    "helldiver", "hd2", "stratagem", "super earth", "superearth",
    "terminid", "automatons", "illuminate", "arrowhead", "diver",
    "liber-tea", "liberty", "helldivers", "liberator", "exosuit", "exo",
    "patriot", "hellpod", "eagle", "sentry", "bile titan", "charger",
)
# 明显属于别的游戏/领域的词（出现则一票否决）
WRONG_GAME = (
    "arma", "blender", "minecraft", "skyrim", "fallout", "wow", "warcraft",
    "dota", "league", "valorant", "csgo", "cs2", "pubg", "fortnite",
    "genshin", "starfield", "cyberpunk", "witcher", "elden", "dark souls",
    "factorio", "rimworld", "terraria", "stardew", "palworld", "vscode",
    "react", "vue", "django", "flask", "tensorflow", "pytorch", "kubernetes",
    # 现实物品 / 无关领域
    "cyclist", "headlamp and side", "bicycle", "motorcycle", "helmet for",
    "3d print", "stl", "cad", "photography", "nintendo", "switch game",
)


def _hd2_evidence(hit: dict) -> int:
    """判断一个 GitHub 候选是否真的跟 HD2 有关。

    返回 1=有正面证据，0=中性，-1=明显是别的游戏/领域。
    宁可漏掉也别错配 —— 错配会让人去更新错的东西。

    实测教训：「Helmet Headlamp」会搜到摩托车骑行头盔（相似度 90%），
    「HD2 HUD+」会搜到 DefaultHUD-Plus（相似度 90%），
    它们名字像但不是 HD2 的 mod。
    """
    text = (hit.get("full_name", "") + " " + hit.get("description", "")).lower()
    if any(w in text for w in WRONG_GAME):
        return -1
    if any(w in text for w in HD2_EVIDENCE):
        return 1
    return 0


def search_terms(entry: dict) -> list[str]:
    """为这个 mod 生成多组搜索词（从精确到宽松）。

    实测教训：直接把 label / 目录名整串拿去搜命中率很低（带版本号、
    括号说明、构建号）。要**逐级降噪**，先给最干净的名字。
    """
    label = entry.get("label") or ""
    dirname = entry.get("dirname") or ""
    raw: list[str] = []

    for base in (dirname, label):
        if not base:
            continue
        b = base
        b = re.sub(r"_AR\d+.*$", "", b)                        # Arsenal 后缀
        b = re.sub(r"\s+\d{3,7}\s+\d+(?:\.\d+)*\s+\d{4}-\d{2}-\d{2}T\S*", "", b)
        b = re.sub(r"\s+[A-Za-z0-9]{7,}_AR.*$", "", b)
        # Nexus 命名尾部那个随机 slug（形如 vcLNeByhN / IzUPRha2W / KHd6InFpy）
        # 特征：长度 7~12、只有字母数字、且同时含大小写（自然词很少这样）
        b = re.sub(r"\s+[A-Za-z0-9]{7,12}\s*$", lambda m: (
            " " if (any(c.isupper() for c in m.group().strip())
                    and any(c.islower() for c in m.group().strip())
                    and len(m.group().strip()) >= 7)
            else m.group()
        ), b)
        b = re.sub(r"\[[^\]]*\]", " ", b)                      # 方括号说明
        b = re.sub(r"\([^)]*\)", " ", b)                       # 圆括号说明
        b = re.sub(r"（[^）]*）", " ", b)
        b = re.sub(r"\bbuilds?\b.*$", "", b, flags=re.I)        # builds 后缀
        b = re.sub(r"\b(v|V)?\d+(\.\d+)*\b", " ", b)           # 版本号
        b = re.sub(r"[_\-]+", " ", b)
        b = re.sub(r"\s+", " ", b).strip(" -_")
        if b:
            raw.append(b)

    terms: list[str] = []

    def push(t: str) -> None:
        t = re.sub(r"\s+", " ", t).strip()
        if t and t.lower() not in {x.lower() for x in terms}:
            terms.append(t)

    # 1) 完整干净名
    for r in raw:
        push(r)
    # 2) 去掉通用词的版本（Interlocked / Bridge / Wild 等修饰）
    noise = {"wild", "bridge", "edition", "variant", "version", "pack",
             "the", "and", "for", "with", "updated", "fix", "fixed", "new"}
    for r in raw:
        parts = [w for w in r.split() if w.lower() not in noise]
        if len(parts) >= 2:
            push(" ".join(parts))
    # 3) 只保留最有辨识度的 2~3 个词
    for r in raw:
        parts = [w for w in r.split() if w.lower() not in STOPWORDS
                 and w.lower() not in noise]
        if len(parts) >= 2:
            push(" ".join(parts[:3]))
            push(" ".join(parts[:2]))

    # 4) 带上 HD2 上下文再试一遍 —— 实测这招很有效（"Auto Reload" -> 
    #    "Auto Reload HD2" 就能精准命中 HD2-Auto-Reload）。
    #    放在后面，让精确词先用。
    ctx: list[str] = []
    for t in terms[:3]:
        if not re.search(r"hd2|helldiver", t, re.I):
            ctx.append(f"{t} HD2")
    terms.extend(ctx)

    return terms


# --------------------------------------------------------------------------
# 本地 ayakamods 索引
# --------------------------------------------------------------------------
# 站点索引的保鲜期（天）。超过就自动重建。
# 为什么是 7 天：ayakamods 上每天都有新模组上架，索引太旧会导致新模组
# 识别不到；而重建要爬 500+ 页（约 90 秒），也不宜太频繁。
INDEX_TTL_DAYS = 7

# 防止并发抓取：后台自动刷新和用户点「识别来源」可能撞在一起
_crawl_lock = threading.Lock()
_crawl_state = {"running": False}


def crawl_in_progress() -> bool:
    """是否有抓取正在跑（供界面/引擎判断要不要等）。"""
    return bool(_crawl_state.get("running"))


def index_path() -> str:
    return os.path.join(app_data_dir(), "ayaka_index.json")


def load_index() -> list[dict]:
    try:
        with open(index_path(), encoding="utf-8-sig") as f:
            return json.load(f) or []
    except Exception:  # noqa: BLE001
        return []


def save_index(items: list[dict]) -> None:
    try:
        tmp = index_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, index_path())
    except Exception:  # noqa: BLE001
        pass


def index_age_days() -> float:
    """索引有多旧（天）。没索引返回 -1。"""
    p = index_path()
    if not os.path.isfile(p):
        return -1
    import time
    return (time.time() - os.path.getmtime(p)) / 86400.0


def index_is_stale() -> bool:
    """索引是否需要重建（不存在或过期）。"""
    return index_age_days() < 0 or index_age_days() > INDEX_TTL_DAYS


def index_status_text() -> str:
    """给界面用的一句话状态描述。"""
    age = index_age_days()
    if age < 0:
        return "尚未建立（首次使用会自动建立）"
    if age < 1:
        return "刚刚更新过"
    days = int(age)
    if index_is_stale():
        return f"已 {days} 天未更新，下次使用时会自动重建"
    return f"{days} 天前更新（每 {INDEX_TTL_DAYS} 天自动更新）"


def rebuild_index(progress=None) -> list[dict]:
    """重新爬 ayakamods 列表建索引。

    并发安全：若已有抓取在跑，会等它结束后直接复用结果，不重复爬。
    """
    if not _crawl_lock.acquire(blocking=False):
        if progress:
            progress("已有索引更新在进行中，等它完成…")
        _crawl_lock.acquire()          # 等对方做完
        _crawl_lock.release()
        return load_index()

    try:
        _crawl_state["running"] = True
        if progress:
            progress("正在抓取 ayakamods 模组列表（约 1~2 分钟）…")
        items = providers.ayaka_crawl()
        items = [i for i in items if i.get("slug")]
        if items:
            save_index(items)
        return items
    finally:
        _crawl_state["running"] = False
        _crawl_lock.release()


def ensure_index(progress=None, force: bool = False) -> list[dict]:
    """按需保证索引可用：不存在或过期就重建，否则直接用现成的。

    这是给引擎调用的统一入口 —— 之前 engine 里那套 if 判断写错了层级，
    导致"过期自动重建"永远不会触发（索引一旦建立就再没更新过）。
    """
    idx = load_index()
    age = index_age_days()
    if force or not idx or age < 0 or age > INDEX_TTL_DAYS:
        why = "首次建立" if not idx else f"已 {int(age)} 天未更新"
        if progress:
            progress(f"站点索引需要更新（{why}）…")
        fresh = rebuild_index(progress=progress)
        return fresh or idx
    return idx


def unescape(text: str) -> str:
    """还原 HTML 实体（&#039; &amp; 等）。"""
    import html as _html
    return _html.unescape(str(text or ""))


def search_ayaka_index(query: str, items: list[dict], top: int = 5) -> list[dict]:
    """在本地索引里模糊搜。"""
    scored = []
    for it in items:
        title = unescape(it.get("title") or "")
        slug_txt = unescape((it.get("slug") or "").replace("-", " ").rsplit(".", 1)[0])
        desc = unescape(it.get("desc") or "")
        s = max(
            similarity(query, title),
            similarity(query, slug_txt),
            similarity(query, desc) * 0.8,      # 描述只作弱证据
        )
        if s >= 0.55:
            scored.append((s, it))
    scored.sort(key=lambda x: -x[0])
    return [
        {
            "slug": it["slug"],
            "title": unescape(it.get("title") or it["slug"]),
            "score": round(s, 3),
            "author": it.get("author", ""),
            "desc": unescape(it.get("desc") or "")[:120],
        }
        for s, it in scored[:top]
    ]


def disambiguate_ayaka(
    hits: list[dict],
    installed_version: str,
    check_version=None,
) -> list[dict]:
    """在多个相似的 ayakamods 候选里挑最可能的那个。

    关键洞察：**本地版本号能与网页版本对上**的那个，几乎必然是正确的 mod。
    比如本地 "0.6.4" 对应 HD2 Auto Reload（0.6.4），而 Auto Reload Assist
    的版本号对不上。

    注意：本地版本号为空时**不做核对**（没依据），只按名字相似度排。
    另外同名的旧页面（如 .4035 与 .4037）要优先选版本更高的那个。
    """
    if len(hits) <= 1:
        return hits

    from .util import compare, ver_key

    # 没有本地版本可核对时：同名多页的情况下，取 slug 末尾编号更大的
    # （站点上编号是递增的，越大越新；如 4037 > 4035）
    if not installed_version or check_version is None:
        def slug_num(h: dict) -> int:
            m = re.search(r"\.(\d+)$", h.get("slug") or "")
            return int(m.group(1)) if m else 0
        return sorted(hits, key=lambda h: (-h["score"], -slug_num(h)))

    scored: list[tuple] = []
    for h in hits:
        bonus = 0.0
        try:
            info = check_version(h["slug"])
            if info and info.get("ok"):
                v = compare(installed_version, info.get("version", ""))
                if v in ("same", "older"):
                    bonus = 0.35          # 版本对得上，强证据
                elif v == "newer":
                    bonus = 0.15          # 只是有新版，也算线索
        except Exception:  # noqa: BLE001
            pass
        scored.append((h["score"] + bonus, h, bonus))
    scored.sort(key=lambda x: -x[0])
    out = []
    for total, h, bonus in scored:
        h = dict(h)
        h["score"] = round(min(1.0, total), 3)
        if bonus:
            h["verified"] = True
        out.append(h)
    return out


# --------------------------------------------------------------------------
# 主匹配流程
# --------------------------------------------------------------------------
def candidates_for(
    entry: dict,
    github_token: str = "",
    use_github_search: bool = True,
    ayaka_index: list[dict] | None = None,
    log=None,
) -> list[dict]:
    """为一个 mod 收集所有可能的来源候选，按用户优先级排序。"""

    def say(msg: str) -> None:
        if log:
            log(msg)

    found: list[dict] = []

    def add(provider: str, ident: str, url: str, how: str, confidence: str, **extra) -> None:
        if not ident and not url:
            return
        # 去重
        for f in found:
            if f["provider"] == provider and (f["id"] == ident or (url and f["url"] == url)):
                return
        found.append(
            {
                "provider": provider,
                "id": ident,
                "url": url,
                "how": how,
                "confidence": confidence,
                **extra,
            }
        )

    # --- 1) manifest 里作者写的来源（最可信） ---
    for src in read_manifest_sources(entry):
        prov = src["provider"]
        prov = "ayaka" if prov in ("ayakamods", "ayaka") else prov
        if prov not in ("github", "nexus", "ayaka"):
            continue
        url = src.get("url") or ""
        if not url:
            if prov == "nexus" and src.get("id"):
                url = f"https://www.nexusmods.com/helldivers2/mods/{src['id']}"
            elif prov == "github" and src.get("id"):
                url = f"https://github.com/{src['id']}"
            elif prov == "ayaka" and src.get("id"):
                url = f"https://ayakamods.com/mods/{src['id']}/"
        add(prov, src.get("id", ""), url, src["how"], "high")
        say(f"manifest 里找到 {prov} 来源")

    # --- 2) Arsenal 库里的 nexusData ---
    if entry.get("nexus_mod_id"):
        add(
            "nexus",
            entry["nexus_mod_id"],
            f"https://www.nexusmods.com/helldivers2/mods/{entry['nexus_mod_id']}",
            "Arsenal 库里的 N 网编号",
            "high",
        )
        say("Arsenal 库里识别出 N 网来源")

    # --- 3) 目录名里的 N 网编号 ---
    d = entry.get("dirname") or ""
    m = re.search(r"\s(\d{3,7})\s+\d+(?:\.\d+)*\s+\d{4}-\d{2}-\d{2}T", d)
    if m and m.group(1) != entry.get("nexus_mod_id"):
        add(
            "nexus",
            m.group(1),
            f"https://www.nexusmods.com/helldivers2/mods/{m.group(1)}",
            "从安装目录名解析出 N 网编号",
            "medium",
        )
        say(f"目录名里读N网编号 #{m.group(1)}")

    # --- 4) GitHub 搜索 ---
    if use_github_search:
        terms = search_terms(entry)
        tried = 0
        for term in terms:
            if tried >= 5:          # 最多试 5 组，别把配额烧光
                break
            tried += 1
            hits = providers.github_search(term, github_token)
            if not hits:
                continue
            best, bestscore = None, 0.0
            for h in hits:
                repo_name = h["full_name"].split("/")[-1]
                sc = max(
                    similarity(term, repo_name),
                    similarity(term, h["full_name"]),
                    similarity(entry.get("label") or "", repo_name) * 0.95,
                )
                # 万星无关仓库（awesome-* 之类）压低
                if h["stars"] > 2000 and sc < 0.75:
                    sc *= 0.4
                # ★ 关键：HD2 相关性门槛。只有名字像还不够 ——
                #   必须能看出这是绝地潜兵2 的 mod，否则宁可交给用户。
                if _hd2_evidence(h) < 0:
                    sc *= 0.5
                if sc > bestscore:
                    best, bestscore = h, sc
            # 阈值与证据要求：宁可漏，不可错配
            if not best:
                continue
            ev = _hd2_evidence(best)
            if ev < 0:
                continue                      # 明显是别的游戏，直接丢
            if ev == 1 and bestscore >= 0.72:
                conf = "high" if bestscore >= 0.85 else "medium"
            elif ev == 0 and bestscore >= 0.9:
                # 名字极像但看不出是 HD2 —— 交给用户确认，不自动采纳
                conf = "low"
            else:
                continue
            add(
                "github",
                best["full_name"],
                f"https://github.com/{best['full_name']}",
                f"GitHub 搜索「{term}」命中（相似度 {bestscore:.0%}，★{best['stars']}）"
                + ("（看不出与 HD2 有关，请你确认）" if conf == "low" else ""),
                conf,
                stars=best["stars"],
                desc=best.get("description", ""),
            )
            say(f"GitHub 搜到 {best['full_name']}（{bestscore:.0%}，证据={ev}）")
            break

    # --- 5) ayakamods 本地索引 ---
    if ayaka_index:
        for term in search_terms(entry)[:3]:
            hits = search_ayaka_index(term, ayaka_index, top=5)
            if not hits:
                continue
            # 多个相似候选时，用本地版本号去网页核对，挑对得上的那个
            if len(hits) > 1:
                hits = disambiguate_ayaka(
                    hits,
                    entry.get("installed_version") or "",
                    check_version=lambda slug: providers.ayaka_check(slug),
                )
            top = hits[0]
            # 门槛：相似度要够高，或版本号核对一致。否则宁可交给用户，
            # 免得像 "机甲部位血量HUD" 那样被 56% 的噪声匹到无关 mod。
            if not top.get("verified") and top["score"] < 0.75:
                continue
            # 防「长名字套短名字」的假匹配：
            # 例如 "HD2 HUD+" 会命中 "HD2 HUD Resource Markers"（开头重合），
            # 但多出来的 "Resource Markers" 说明是**另一个 mod**。
            # 判据：站上名字比本地名字多出太多实词时，不算命中。
            if not top.get("verified"):
                local_words = set(normalize(entry.get("label") or "").split())
                site_words = set(normalize(top.get("title") or "").split())
                extra = {w for w in (site_words - local_words)
                         if w not in STOPWORDS and len(w) > 2}
                if len(extra) >= 2:
                    say(f"索引命中 {top['slug']} 但站上名字多出 {sorted(extra)}，判为不同 mod")
                    continue
            conf = "high" if top.get("verified") or top["score"] >= 0.85 else "medium"
            add(
                "ayaka",
                top["slug"],
                f"https://ayakamods.com/mods/{top['slug']}/",
                f"ayakamods 索引命中（站上叫「{top['title']}」，相似度 {top['score']:.0%}"
                + ("，版本号已核对一致" if top.get("verified") else "") + "）",
                conf,
                title=top.get("title", ""),
                alternatives=[h["slug"] for h in hits[1:3]],
            )
            say(f"ayakamods 搜到 {top['slug']}（{top['score']:.0%}）")
            break

    # 按用户要求的优先级排序：GitHub > Nexus > ayakamods
    # 同站点内高可信度优先
    found.sort(
        key=lambda c: (
            PROVIDER_PRIORITY.get(c["provider"], 9),
            0 if c.get("confidence") == "high" else 1,
        )
    )
    return found


def best_source(cands: list[dict]) -> dict | None:
    """按优先级挑首选来源。"""
    return cands[0] if cands else None
