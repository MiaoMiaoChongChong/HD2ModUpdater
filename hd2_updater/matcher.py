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
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

from . import providers
from .arsenal import read_manifest_sources
from .util import app_data_dir

# 用户要求的站点优先级
PROVIDER_PRIORITY = {"github": 0, "nexus": 1, "ayaka": 2}

# ---- N 网"按名字搜"的门槛（用户拍板：只有确实搞不清楚才打扰用户）----
# 「说得通」= 算得上一个像样的候选；有 ≥2 个这样的就要请用户来选。
PLAUSIBLE_SCORE = 0.62
# 「对得很严」= 只有一个候选时可以放心采纳（实测：Improved Railgun Sights 这类
# 站上名字与本地完全同名，搜到的唯一候选就是它）。
STRICT_SCORE = 0.90

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


def _contains_as_words(short: str, long_: str) -> bool:
    """short 是否以**独立单词**的形式出现在 long_ 里。

    不能用裸 `in`：实测 "e"（某模组的描述就一个字）是 "mech part hud" 的子串，
    于是被判成 90% 相似 —— 而描述只按 0.8 折算，仍有 0.72，直接越过 0.55 的
    门槛，最后把一个中文 HUD 模组错配成 "VRC Tell CE-07 CM-21"。
    用前后不能紧挨字母数字的方式判定，能挡掉这种"嵌在单词中间"的假包含。
    """
    if not short or not long_:
        return False
    return re.search(r"(?<![0-9a-z])" + re.escape(short) + r"(?![0-9a-z])",
                     long_) is not None


# 「包含」关系成立时给的高分。
_BONUS_FOR_CONTAINS = 0.9
# 参与"包含"判定的最短长度。
#
# 这个数字是**量出来的**，不是拍的。在站上真实的 871 条索引上跑
# 「用条目自己的站上名字能不能找回自己」/「会不会自动采纳到别人（high）」：
#   gate=2 -> 自召回 825 / 错配 18
#   gate=3 -> 自召回 825 / 错配 18     <-- 与 2 完全相同，但更严格
#   gate=4 -> 自召回 821 / 错配 16     <-- 少了 4 条真 mod
# 即：门槛 4 会白丢 4 条真实模组（「解锁台（Unlock Kit）」、'DSS Supreme'、
# 'Uzi Stalwart'、'(Blue Archive) HOD Harvester'）—— 名字短是它们的特征，
# 不是噪声；而错配数几乎不随门槛变化（真正把关的是**词边界**和下面
# 「长名套短名」那道防呆）。所以取 3：不丢真 mod，又比 2 更保守一点
# （挡住两字符的退化输入）。
_MIN_CONTAINS_LEN = 3


def similarity(a: str, b: str) -> float:
    """两个名字的相似度 0~1。"""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # 一个包含另一个，给高分。但必须是**词级**包含、且短的那个不至于退化 ——
    # 否则一两个字符的描述/名字会把毫不相干的模组抬到高分（见上面注释）。
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= _MIN_CONTAINS_LEN and _contains_as_words(short, long_):
        return _BONUS_FOR_CONTAINS
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
        # N 网完整命名的尾巴：`<编号> <版本> <日期>T<时间>Z <随机slug>`
        # ★ slug（形如 vcLNeByhN / IzUPRha2W）**必须绑在这条尾巴里一起剥**，
        #   不能单独按"7~12 位、含大小写"去猜 —— 实测踩过：那个启发式会把
        #   正常的**英文单词**也删掉，`Resupply Boxes Emissive` 被削成
        #   `Resupply Boxes`（"Emissive" 正好 8 位、首字母大写），
        #   于是搜索词残缺、三个站点全都搜不到。这个 bug 拖累了 GitHub /
        #   ayakamods / N 网 所有搜索路径。
        b = re.sub(r"\s+\d{3,7}\s+\d+(?:\.\d+)*\s+\d{4}-\d{2}-\d{2}T\S*"
                   r"(?:\s+[A-Za-z0-9]{7,12})?", "", b)
        b = re.sub(r"\s+[A-Za-z0-9]{7,}_AR.*$", "", b)
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

    # 0) ★ 先放「紧凑词」（把空格去掉）—— 必须排在最前面。
    #    两个实测原因（2026-10-11）：
    #    ① **GitHub 搜索按词 AND 匹配**，多词组合经常直接 0 结果
    #       （'DiversBestFriend R41 MissionBlacklist' → 0 个），
    #       而仓库名往往是连写的（'HWG90/DiversBestFriend'）；
    #    ② 搜索循环有 `tried >= 5` 的配额截断 —— 排在后面的词**根本不会被执行**。
    #    实测：'DiversBestFriend' 命中目标，相似度 1.00、HD2 证据 1 → 可采纳。
    #
    #    ★ 但也不能乱塞：**单字词**（如 "Diver's"）会搜出一堆无关仓库、
    #    白白占掉一个配额（实测它返回 10 个全不相干的结果）；
    #    **超长连写**（如 "ResupplyBoxesEmissive" 21 字符）GitHub 搜不出东西，
    #    同样白占配额。所以只收 4~20 字符、且不含撇号的紧凑词。
    compact_terms: list[str] = []
    for base in (dirname, label):
        if not base:
            continue
        b = re.sub(r"_AR\d+.*$", "", base)
        b = re.sub(r"\s+\d{3,7}\s+\d+(?:\.\d+)*\s+\d{4}-\d{2}-\d{2}T\S*"
                   r"(?:\s+[A-Za-z0-9]{7,12})?", "", b)
        b = re.sub(r"[_\-]+", " ", b)
        b = re.sub(r"\([^)]*\)|（[^）]*）|\[[^\]]*\]", " ", b)
        # ★ 版本号要连**大写 R 开头**的（R41 / r41 / r35 是这类 mod 的常见版本形
        #   式）；只剥 v/V 会留下 "R41"，紧凑词就变成 "DiversBestFriendR41…"，
        #   GitHub 搜不出东西（实测踩过）。
        b = re.sub(r"\b[RrVv]?\d+(\.\d+)*\b", " ", b)
        words = [w for w in b.split() if w and w.lower() != "hd2"]
        if len(words) >= 2:
            comp = "".join(words)
            if 4 <= len(comp) <= 20:
                compact_terms.append(comp)
    # 目录名**不含空格**时（形如 `DiversBestFriend-R41-MissionBlacklist`），
    # 首个 `-`/`_` 分段通常就是仓库名形态 —— 单独也试一次。
    # ★ 为什么要挑"不含空格"：目录名带空格的多半是"英文短语标题"
    #   （如 `Resupply Boxes Emissive`），首词（"Resupply"）会搜出一堆无关仓库、
    #   白占一个配额（实测 10 个全不相干）。这样能精确命中而不添噪。
    d0 = re.sub(r"_AR\d+.*$", "", dirname).strip()
    if d0 and " " not in d0:
        seg = re.split(r"[_\-]", d0)[0]
        if len(seg) >= 5 and re.fullmatch(r"[A-Za-z0-9]+", seg):
            compact_terms.append(seg)
    for c in compact_terms:
        push(c)

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
# 识别不到；而重建要翻 40+ 页（约 2 分钟），也不宜太频繁。
INDEX_TTL_DAYS = 7

# 防止并发抓取：后台自动刷新和用户点「识别来源」可能撞在一起
_crawl_lock = threading.Lock()
_crawl_state = {"running": False}


def crawl_in_progress() -> bool:
    """是否有抓取正在跑（供界面/引擎判断要不要等）。"""
    return bool(_crawl_state.get("running"))


def index_path() -> str:
    return os.path.join(app_data_dir(), "ayaka_index.json")


# 索引的**格式版本**。加这一层是因为：老索引是用写死 [a-z0-9-.] 的正则爬的，
# 里面**一条中文 mod 都没有**（实测 500 条里零条）。如果只按时间判断新鲜度，
# 用户的旧索引还会继续用满 7 天，中文化的那些模组就一直识别不出来 ——
# 典型的「修了代码但用户那里没生效」。
# 一旦解析逻辑变了（或站点结构变了导致内容缺失），把这里 +1 即可强制重建。
INDEX_FORMAT = 2


def load_index() -> list[dict]:
    """读索引。老格式（裸 list）或任何损坏形态都读成空 —— 触发重建。"""
    try:
        with open(index_path(), encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []          # 老格式（裸 list）或其他怪东西：一律重建
    try:
        fmt = int(data.get("format") or 0)
    except (TypeError, ValueError):
        # format 被改坏了（"two" / [2] 之类）。不能让它抛出去 ——
        # 这个函数在界面和引擎的热路径上被调用，抛异常会连累整条流程；
        # 当作格式不对、触发重建才是正确处置。
        return []
    if fmt != INDEX_FORMAT:
        return []
    items = data.get("items")
    return items if isinstance(items, list) else []


def save_index(items: list[dict]) -> None:
    try:
        tmp = index_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"format": INDEX_FORMAT, "items": items}, f,
                      ensure_ascii=False, indent=1)
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
    """索引是否需要重建（不存在、格式不对、或过期）。"""
    if not load_index():          # 不存在 / 老格式 / 格式版本对不上
        return True
    return index_age_days() > INDEX_TTL_DAYS


def index_status_text() -> str:
    """给界面用的一句话状态描述。

    注意必须**先看索引是不是真的能用**（load_index 会拒掉老格式）。
    只看文件修改时间的话，老格式的索引文件明明刚写过、会显示
    "刚刚更新过（每 7 天自动更新）"，而实际上一读就是空的、马上要重建 ——
    设置里那行状态就成了假消息。
    """
    if not load_index():
        if index_age_days() < 0:
            return "尚未建立（首次使用会自动建立）"
        return "格式已升级，下次使用时会自动重建"
    age = index_age_days()
    if age < 1:
        return "刚刚更新过"
    days = int(age)
    if index_is_stale():
        return f"已 {days} 天未更新，下次使用时会自动重建"
    return f"{days} 天前更新（每 {INDEX_TTL_DAYS} 天自动更新）"


def _why_crawl_empty() -> str:
    """一条都没抓到时说清原因（给界面/命令行看的）。

    现场再打一枪列表页，区分两种完全不同的情况 —— 给用户的建议也不一样：
      · Cloudflare 封锁/挑战页 → 站点不接受本机出口的自动请求（换节点/等待）
      · 超时、连不上           → 本机网络/代理问题
    """
    url = f"{providers.AYAKA_BASE}/mods/games/helldivers-2.119/"
    try:
        html = providers.http_get(url, accept="text/html", retries=1, timeout=12)
    except Exception as e:  # noqa: BLE001
        return ("没能从 ayakamods 抓到任何模组："
                + providers.friendly_error(str(e))
                + "\n（原索引已保留，不受影响。）")
    if providers.is_cf_block(html):
        return ("没能从 ayakamods 抓到任何模组：站点把这次请求挡在了门外"
                "（Cloudflare）。\n"
                "这不是你本机网络断了 —— 实测这种封锁连真实浏览器都可能过不去。\n"
                "可以试：① 换一个代理节点 / 关掉代理再试；② 过一会儿再试。\n"
                "（原索引已保留，不受影响；已有的来源识别结果也还能用。）")
    if not html.strip():
        return "没能从 ayakamods 抓到任何模组：列表页返回了空内容。（原索引已保留。）"
    return ("没能从 ayakamods 抓到任何模组：页面结构可能变了（原索引已保留）。")


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
        before = load_index()
        items = providers.ayaka_crawl()
        items = [i for i in items if i.get("slug")]

        # ★ 只有**明显完整**的结果才允许覆盖盘上的索引。
        # 为什么：ayaka_crawl 一遇空页就 break，所以网络抖一下、或被 Cloudflare
        # 拦在第 N 页，就会返回一个"只有前 N 页"的残缺列表。原来只看 `if items:`
        # 就落盘 —— 于是一次抖动会把 800+ 条的好索引换成几十条，而且
        # index_is_stale() 会认为它很新鲜、锁死 7 天，用户在这 7 天里
        # 一直在用一个残缺索引去识别来源（表现为"很多 mod 突然认不出来了"）。
        if not items:
            # ★ 必须说出**为什么**没抓到：用户点了「重建站点索引」，结果
            #   只看到"完成：0 个模组"或者干脆没变化，会以为是自己哪里点错了。
            #   实测（2026-10-11）：站点对当前出口 IP 返回 Cloudflare 封锁页，
            #   抓取必然为空 —— 这时要明确指向"换个网络/代理节点再试"。
            if progress:
                progress(_why_crawl_empty())
            return before
        if before and len(items) < len(before) * 0.8:
            if progress:
                progress(f"本次只抓到 {len(items)} 条（原有 {len(before)} 条），"
                         f"看起来没抓完 —— 保留原索引")
            return before
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


def slug_text(slug: str) -> str:
    """slug -> 能拿去跟本地名字比的可读文本。

    必须**先做百分号解码**再去掉 "-"、编号：中文 mod 的 slug 在站点上是
    `%E6%9C%BA%E7%94%B2...%E8%A1%80%E9%87%8FHUD.4775` 这种，
    不解码就等于拿一串 %E6%9C 去跟「机甲部位血量HUD」比相似度，永远是 0 分，
    这些 mod 一辈子识别不出来。
    """
    s = unescape(providers.ayaka_decode(slug))
    s = s.replace("-", " ").rsplit(".", 1)[0]
    return s.strip()


def search_ayaka_index(query: str, items: list[dict], top: int = 5) -> list[dict]:
    """在本地索引里模糊搜。"""
    scored = []
    for it in items:
        title = unescape(it.get("title") or "")
        stext = slug_text(it.get("slug") or "")
        desc = unescape(it.get("desc") or "")
        s = max(
            similarity(query, title),
            similarity(query, stext),
            similarity(query, desc) * 0.8,      # 描述只作弱证据
        )
        if s >= 0.55:
            scored.append((s, it))
    scored.sort(key=lambda x: -x[0])
    return [
        {
            "slug": it["slug"],
            "title": unescape(it.get("title") or slug_text(it["slug"])),
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

    ★ 只有 ``same``（版本号**完全相等**）才算 ``verified``。这一条是血泪教训：

      · ``older`` 曾经也算强证据，理由是"同一个 mod 的站点页面可能落后于本地"。
        但版本比较是**数字元组**比较，随便一个无关 mod 只要版本号比本地小
        就会命中 ``older`` —— 实测本地「机甲部位血量HUD 1.11.1」会被判成
        "版本号已核对一致"、相似度凭空涨到 99%，从而**自动采纳**
        站点上毫不相干的「Wrench Repair 1.3」（真实名字相似度只有 0.64）。
        认错会让人去更新一个完全无关的模组，是本工具最严重的失效模式。
      · ``newer``（线上更新）对"这个模组有更新"是正常的，但对"这是不是同一个
        mod"只是**弱线索** —— 任何无关 mod 只要有更高版本号都符合。给一点加分
        参与排序，但同样不算核对过。

    因此顺序上：``same`` 才算强证据；``older`` 不给任何加分（它太容易误命中）；
    ``newer`` 给少量加分但不标 verified。
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
        strong = False
        try:
            info = check_version(h["slug"])
            if info and info.get("ok"):
                v = compare(installed_version, info.get("version", ""))
                if v == "same":
                    bonus, strong = 0.35, True     # 版本号完全相等，强证据
                elif v == "newer":
                    bonus = 0.15                   # 有新版：弱线索，不算核对
                # older：不给加分。它太容易被无关 mod 撞上（见上面注释）。
        except Exception:  # noqa: BLE001
            pass
        scored.append((h["score"] + bonus, h, bonus, strong))
    scored.sort(key=lambda x: -x[0])
    out = []
    for total, h, bonus, strong in scored:
        h = dict(h)
        # 保留**纯名字分**：界面要显示的是"站上名字跟你的有多像"，
        # 不能把版本加分算进去 —— 否则会说出「相似度 99%」这种假数字
        # （真实只有 64%），用户就被这个百分比骗着信任了一个错配。
        h["name_score"] = h["score"]
        h["score"] = round(min(1.0, total), 3)
        if strong:
            h["verified"] = True
        out.append(h)
    return out


# --------------------------------------------------------------------------
# 主匹配流程
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# GitHub 搜索结果的会话内缓存（query -> hits）
#
# 为什么要有这层：候选评估（candidates_for）必须**保持串行、逐条记录日志**
# （用户看着识别过程），但 GitHub 搜索本身是纯网络等待、彼此独立。
# 于是把「搜索」这一步抽出来：识别前用 batch_prefetch_github() **并发**地把
# 所有要搜的词一次搜完填进这里，之后 candidates_for 走到第 4 步时直接命中
# 缓存、不再发请求。这样搜索的等待从「逐个串行累加」变成「一次并发并行」，
# 而评估/日志/落盘的顺序与原来完全一致（测试里对 candidates_for 的桩替换
# 也因此不受影响）。
_gh_cache: dict[str, list[dict]] = {}
_gh_lock = threading.Lock()


def _github_search_cached(query: str, token: str = "") -> list[dict]:
    """github_search 的会话内缓存版：同一批识别里同一 query 只发一次请求。"""
    key = (query or "").strip()
    with _gh_lock:
        if key in _gh_cache:
            return _gh_cache[key]
    hits = providers.github_search(query, token)
    with _gh_lock:
        _gh_cache[key] = hits
    return hits


def batch_prefetch_github(
    entries: list[dict],
    github_token: str = "",
    max_workers: int = 6,
    log=None,
) -> int:
    """识别前**并发**预搜：把这一批模组可能用到的 GitHub 搜索词一次搜完。

    每个模组最多取 search_terms() 的前 5 组词（与 candidates_for 内部的
    `tried >= 5` 上限保持一致，别预搜了用不到的白烧配额）。所有词去重后
    并发请求，结果全部落进 _gh_cache。

    返回实际发起请求的搜索词数（供日志/测试用）。网络失败的词由
    github_search 自己返回 [] 并同样被缓存 —— 与串行路径行为一致
    （串行时失败也是拿到 []、继续试下一组词）。
    """
    queries: list[str] = []
    seen: set[str] = set()
    for e in entries or []:
        try:
            terms = search_terms(e)
        except Exception:  # noqa: BLE001
            continue
        for t in terms[:5]:
            k = (t or "").strip().lower()
            if k and k not in seen:
                seen.add(k)
                queries.append(t)
    if not queries:
        return 0
    if log:
        log(f"并发预搜 GitHub：{len(queries)} 组词（{len(entries)} 个模组）…")

    def fetch(q: str) -> None:
        hits = providers.github_search(q, github_token)
        with _gh_lock:
            _gh_cache[q.strip()] = hits

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as ex:
        list(ex.map(fetch, queries))
    return len(queries)


def candidates_for(
    entry: dict,
    github_token: str = "",
    use_github_search: bool = True,
    ayaka_index: list[dict] | None = None,
    nexus_api_key: str = "",
    log=None,
    only_providers: set[str] | None = None,
) -> list[dict]:
    """为一个 mod 收集所有可能的来源候选，按用户优先级排序。

    ``only_providers`` 非空时**只跑这几个站点的识别路**（其余一律跳过）——
    给「来源总览」里"只重新查找这一个站点"用（用户 2026-10-11 提的：
    「可以单独查找某一个来源或者全部重新查找」）。
    只查一个站点时能省掉另外两站的网络请求，也避免把用户手工指定的来源覆盖掉。
    """

    def want(prov: str) -> bool:
        return only_providers is None or prov in only_providers

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
        if not want(prov):
            continue
        url = src.get("url") or ""
        if not url:
            if prov == "nexus" and src.get("id"):
                url = f"https://www.nexusmods.com/helldivers2/mods/{src['id']}"
            elif prov == "github" and src.get("id"):
                url = f"https://github.com/{src['id']}"
            elif prov == "ayaka" and src.get("id"):
                url = providers.ayaka_url(src["id"])
        add(prov, src.get("id", ""), url, src["how"], "high")
        say(f"manifest 里找到 {prov} 来源")

    # --- 2) Arsenal 库里的 nexusData ---
    if entry.get("nexus_mod_id") and want("nexus"):
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
    if m and m.group(1) != entry.get("nexus_mod_id") and want("nexus"):
        add(
            "nexus",
            m.group(1),
            f"https://www.nexusmods.com/helldivers2/mods/{m.group(1)}",
            "从安装目录名解析出 N 网编号",
            "medium",
        )
        say(f"目录名里读N网编号 #{m.group(1)}")

    # --- 4) GitHub 搜索 ---
    if use_github_search and want("github"):
        terms = search_terms(entry)
        tried = 0
        for term in terms:
            if tried >= 5:          # 最多试 5 组，别把配额烧光
                break
            tried += 1
            # 命中预填缓存则免发请求（batch_prefetch_github 已并发搜过）；
            # 缓存里没有才走网络 —— 单条「识别这一个」时也自动退化为联网单搜。
            hits = _github_search_cached(term, github_token)
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
    if ayaka_index and want("ayaka"):
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
                    # 用解码后的名字（或站上标题）说明，不要把百分号编码的原始 slug
                    # 甩给用户看 —— 中文 mod 的 slug 是 %E6%9C%BA%E7%94%B2… 这种，
                    # 直接把编码串当"模组名"显示，用户会以为程序出错了
                    # （这正是他反馈的那类问题）。
                    shown = top.get("title") or providers.ayaka_decode(top["slug"])
                    say(f"索引命中「{shown}」但站上名字多出 {sorted(extra)}，判为不同 mod")
                    continue
            conf = "high" if top.get("verified") or top["score"] >= 0.85 else "medium"
            # 文案里报**纯名字分**（name_score）：加了版本加分的那个数会被说成
            # 「相似度 99%」，而真实名字只有 64% —— 那种假百分比会让用户
            # 放心地接受一个错配。版本加分单独用一句话说明。
            name_pct = top.get("name_score", top["score"])
            how = (f"ayakamods 索引命中（站上叫「{top['title']}」，"
                   f"名字相似度 {name_pct:.0%}"
                   + ("，版本号完全一致" if top.get("verified") else "") + "）")
            add("ayaka", top["slug"], providers.ayaka_url(top["slug"]), how,
                conf, title=top.get("title", ""),
                # 备选也用**可读名字**：这个字段是给人看的（将来要在界面上
                # 列"还可能是哪几个"）。存原始 slug 的话，中文 mod 就是
                # 一串 %E6%9C，露出去又是同一类毛病。
                alternatives=[h.get("title") or providers.ayaka_decode(h["slug"])
                              for h in hits[1:3]])
            say(f"ayakamods 搜到 {providers.ayaka_decode(top['slug'])}"
                f"（名字相似度 {name_pct:.0%}"
                + ("，版本号一致" if top.get("verified") else "") + "）")
            break

    # --- 6) N 网「按名字搜」（REST v1 没有搜索端点，走 GraphQL v2）---
    # 为什么需要它：N 网 mod 编号只存在于 N 网自己的下载命名里；从网盘/整合包
    # 导入的 mod 本地一点线索都没有（manifest 无 Sources、Arsenal 无 nexusData、
    # 目录名也改过），五条老路全够不到，只能按名字去站上找。
    #
    # 只在**前面没有确定的答案**时才跑：既省请求，也避免"已经确定了还冒出候选
    # 来打扰用户"（用户明确要求：不能每个都问）。
    if nexus_api_key and not any(c.get("confidence") == "high" for c in found):
        for term in search_terms(entry)[:2]:
            hits = providers.nexus_search(term, nexus_api_key)
            if not hits:
                continue
            scored: list[tuple[float, dict]] = []
            for h in hits:
                sc, extra = site_name_score(entry, h.get("name", ""))
                if len(extra) >= 2:
                    # 站上名多出太多实词 = 另一个 mod（实测：Megapack 的中文汉化版）
                    say(f"N 网搜到「{h.get('name')}」但多出 {sorted(extra)}，判为不同 mod")
                    continue
                if sc >= PLAUSIBLE_SCORE:
                    scored.append((sc, h))
            if not scored:
                continue
            scored.sort(key=lambda t: -t[0])
            for sc, h in scored[:5]:
                # 只有一个候选且对得很严 → 可以放心采纳；否则交给用户判断
                conf = "high" if (len(scored) == 1 and sc >= STRICT_SCORE) else "medium"
                add("nexus", h["mod_id"],
                    f"https://www.nexusmods.com/helldivers2/mods/{h['mod_id']}",
                    f"N 网按名字搜「{term}」命中（站上叫「{h['name']}」，"
                    f"名字相似度 {sc:.0%}）",
                    conf, score=sc, site_name=h.get("name", ""),
                    alternatives=[x[1].get("name", "") for x in scored if x[1] is not h][:3])
            say(f"N 网按名字搜「{term}」得到 {len(scored)} 个候选"
                + ("（要请用户确认）" if len(scored) >= 2 else ""))
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


def strip_annotations(name: str) -> str:
    """去掉名字里的**括号类注释**（`(LUT edit)` / `（外挂）` / `[test]` / `【…】`）。

    为什么要单独剥掉：括号里通常是**说明性文字**，不是区分不同 mod 的实词。
    实测踩过 —— 站上名 `Resupply Boxes Emissive (LUT edit)` 与本地名
    `Resupply Boxes Emissive` 本来是同一个 mod，但 `(LUT edit)` 会被算成
    "多出 2 个实词"，被既有防呆判成"另一个 mod"而误杀。
    剥掉注释后它就能正确命中；而真正的不同 mod（如 Megapack 的
    `Chinese localization`，没有括号）仍会被多实词判据拦住。
    """
    n = name or ""
    for pat in (r"\([^)]*\)", r"（[^）]*）", r"\[[^\]]*\]", r"【[^】]*】"):
        n = re.sub(pat, " ", n)
    return re.sub(r"\s+", " ", n).strip()


def site_name_score(entry: dict, site_name: str) -> tuple[float, set]:
    """本地名字 vs 站上名字：返回（相似度, 站上多出来的实词集合）。

    多出来的实词很关键 —— 复用本项目既有的防呆判据：
    **站上名字比本地名字多出太多实词时，那是"另一个 mod"**。
    实测踩过：搜 "Vanilla Plus Megapack" 第一位返回的是它的**中文汉化版**
    （多出 chinese / localization），真身排第二。

    比对前两边都先剥掉括号注释（见 :func:`strip_annotations`）。
    """
    local = strip_annotations(entry.get("label") or "") \
        or strip_annotations(entry.get("dirname") or "")
    site = strip_annotations(site_name or "")
    if not local or not site:
        return 0.0, set()
    ln, sn = normalize(local), normalize(site)
    if not ln or not sn:
        return 0.0, set()
    score = similarity(ln, sn)
    extra = {w for w in (set(sn.split()) - set(ln.split()))
             if w not in STOPWORDS and len(w) > 2}
    return score, extra


def ambiguous_reason(cands: list[dict]) -> str:
    """这批候选是不是"确实搞不清楚、需要用户来选"？返回原因，'' = 不需要。

    ★ 用户定的规则（2026-10-11）：**只有两种情况才允许打扰用户** ——
      ① 有 ≥2 个候选"都说得通"（同名/难分）；
      ② 有多个变体/文件、分不清该跟哪个（这条在下载阶段判定）。
    **其余一律不打扰**：
      · 已经有确定答案（high）→ 不用问；
      · 完全没候选 → 归到"没找到来源"，也不逐条弹窗；
      · 只有一个候选但名字对不严 → **既不采纳也不问**（用户明确不要这种打扰）。
    """
    if not cands:
        return ""
    if any(c.get("confidence") == "high" for c in cands):
        return ""
    goods = [c for c in cands if float(c.get("score") or 0) >= PLAUSIBLE_SCORE]
    if len(goods) >= 2:
        return "有多个候选都说得通"
    return ""


def best_source(cands: list[dict]) -> dict | None:
    """按优先级挑首选来源。"""
    return cands[0] if cands else None
