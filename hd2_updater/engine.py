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
ST_BLOCKED = "blocked"      # 用户主动屏蔽：不查更新、也不找来源


def sources_path() -> str:
    return os.path.join(app_data_dir(), "sources.json")


def load_sources() -> dict:
    """读来源表：{mod_uuid 或 label: [{provider,id,url,...,"manual":True?}]}

    ``manual: True`` 的条目是**用户手工填写/确认过的**，
    永远不会被自动识别结果覆盖，也不会被「清空来源缓存」抹掉。

    对损坏的文件一律当空：文件可能被手工编辑坏、或被并发写坏。
    注意要检查**顶层**是不是 dict —— 只写 `json.load(f) or {}` 的话，
    一个内容是 `[1,2,3]` 的文件会原样返回 list，后面所有 `.get()` 全炸。
    """
    try:
        with open(sources_path(), encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


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


def clear_source(entry: dict) -> list[dict]:
    """撤销**这一个**模组的来源记录，返回被删掉的那些来源。

    对应右键菜单里的「撤销来源并重新识别」—— 用户手动指定来源后，
    如果发现指错了（或站点那边改名了），原来没有任何办法回到"自动识别"，
    只能一直用着那条错的手工记录（手工记录受保护、不会被自动结果覆盖）。
    这里把这一条记录整个删掉（手工的和自动的都删），删完再重新识别。

    对损坏的 sources.json（手工改坏、值不是列表等）也要**只在它确实是
    列表时**才当成来源，否则一律当空 —— 否则 `list(123)` 会抛 TypeError，
    在界面上就是一个没头没尾的报错。注意"先取出、确认没问题、再删"，
    异常不会发生在删除之后，所以不会出现"删了却没返回"的半成品状态。
    """
    data = load_sources()
    key = source_key(entry)
    raw = data.get(key)
    gone = list(raw) if isinstance(raw, list) else []
    if key in data:
        del data[key]
        save_sources(data)
    return gone


def restore_sources(entry: dict, items: list[dict]) -> None:
    """把 clear_source() 删掉的那些来源**原样放回去**。

    为什么必须有这个：撤销之后要靠自动识别把来源找回来，而识别是
    "尽力而为" —— 用户关掉了「智能搜索来源」、或索引还没建好、或当前离线时，
    ayakamods 这一路**根本不会被尝试**，结果必然是空。那时如果只是一句
    "已撤销"，用户手工找来的地址就永久没了（自动来源删了能重建，
    手工来源删了就真没了）。
    所以撤销失败时把原记录还原回去，宁可回到"手工指定"的旧状态，
    也不能把用户的数据吃掉。
    """
    if not items:
        return
    data = load_sources()
    key = source_key(entry)
    data[key] = items
    save_sources(data)


def source_key(entry: dict) -> str:
    """给一个 mod 生成稳定 key（优先 uuid）。"""
    return entry.get("uuid") or entry.get("label") or entry.get("dirname") or ""


# --------------------------------------------------------------- 屏蔽
# 「屏蔽」= 这个模组**不参与自动/批量的检查更新与来源搜索**。
# 为什么需要（用户 2026-10-18 提的）：
#   有些模组他就是要停在旧版（或作者天天发新版但他不跟），
#   每次「检查更新」都跳一行"有更新"，时间一长整张表全是噪音；
#   还有些模组永远认不出来源，每次"识别来源"都白跑一遍。
#   这些都应该能**逐个**静音，而不是把整个检查关掉。
#
# 存储放在**独立文件** blocked.json（不塞进 config.json）：
#   config.json 是"全局设置"，屏蔽是"逐条模组的状态"；
#   混在一起既不好批量清空，也容易被设置页保存时顺手写坏。
BLOCKED_FILE = "blocked.json"


def blocked_path() -> str:
    return os.path.join(app_data_dir(), BLOCKED_FILE)


def load_blocked() -> dict:
    """读屏蔽表：``{模组 key: {"label": 名字, "at": 屏蔽时间}}``。

    容错口径与 `load_sources()` 一致：文件损坏、被手工改坏、顶层不是 dict
    一律当空 —— 屏蔽不是关键数据（丢了只是噪音重新出现），
    但绝不能因为一个坏文件让整个界面报错。
    """
    try:
        with open(blocked_path(), encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    mods = data.get("mods")
    if not isinstance(mods, dict):
        # 兼容"直接就是 {key: {...}}"的写法（手工编辑过也认）
        mods = data
    out: dict = {}
    for k, v in mods.items():
        if isinstance(k, str) and k:
            out[k] = v if isinstance(v, dict) else {}
    return out


def save_blocked(mods: dict) -> None:
    """原子写盘（先写 .tmp 再 os.replace）。**失败会抛异常**。

    ★ 和 `arsenal.write_config()` 不同：那个是"顺手记一笔"，失败静默；
      屏蔽是用户**明确点出来的动作** —— 写不进去必须让他知道，
      否则他以为屏蔽好了、下次检查又冒出来，只会觉得功能是坏的。
    """
    p = blocked_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "mods": mods}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def set_blocked(entry: dict, on: bool, label: str = "") -> bool:
    """屏蔽 / 取消屏蔽一个模组（**立刻落盘**）。返回操作后是否处于屏蔽状态。"""
    key = source_key(entry)
    if not key:
        raise ValueError("这个模组没有可用的标识（uuid 和名字都为空），没法记录屏蔽")
    mods = load_blocked()
    if on:
        mods[key] = {
            "label": label or entry.get("label") or entry.get("dirname") or key,
            "at": now_str(),
        }
    else:
        mods.pop(key, None)
    save_blocked(mods)
    return bool(on)


def is_blocked(entry: dict, blocked: dict | None = None) -> bool:
    """这个模组是否被屏蔽（``blocked`` 可传入已读好的表，免得重复读盘）。"""
    key = source_key(entry)
    if not key:
        return False
    return key in (load_blocked() if blocked is None else blocked)


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
    return providers._err(out, "other", f"不认识的来源类型：{prov}")


def check_one_source_all(src: dict, api_key: str, github_token: str,
                         variant: str = "") -> list[dict]:
    """查一个来源，返回**全部**结果（可能多于一条）。

    ★ 为什么要多返回一条（2026-10-11 用户质疑后定的）：
    GitHub 仓库常常同时挂着「正式版 r41」和「更新的测试版 r42」。以前只报
    正式版，于是"只有测试版可更"这一档里 GitHub 永远缺席 —— 用户看到的是
    "只有 a 站有测试版更新"，而他知道 GitHub 上也有那个测试版，就会以为
    「GitHub 的 pre-release 没被识别」。
    现在：正式版仍走主结果；若仓库里还有一个**更新的预发布版**，就补一条
    标着 prerelease 的结果，让它在 beta 档里正常参与比较与展示。
    """
    out = check_one_source(src, api_key, github_token, variant)
    if not isinstance(out, dict):
        return [out]
    extra: list[dict] = []
    pre = out.get("prerelease_latest")
    if out.get("ok") and pre:
        # 只有"预发布版比正式版更新"时才补 —— 否则是冗余候选
        try:
            newer = compare(str(out.get("version") or ""),
                            str(pre.get("version") or "")) == "newer"
        except Exception:  # noqa: BLE001
            newer = False
        if newer:
            assets = pre.get("assets") or []
            pick = assets[0] if assets else {}
            e = providers._empty(out.get("provider") or "github")
            e.update({
                "ok": True,
                "version": str(pre.get("version") or ""),
                "prerelease": True,
                "url": str(pre.get("url") or out.get("url") or ""),
                "release_url": str(pre.get("url") or ""),
                "updated": str(pre.get("published") or ""),
                "asset_name": str(pick.get("name") or ""),
                "download_url": str(pick.get("url") or ""),
                "how": "GitHub 上的预发布版（作者标了 pre-release）",
            })
            extra.append(e)
    return [out] + extra


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
            # err_kind 清空：这不是"查询失败"，是"版本比不了"。
            # 留着上游的 kind 会让下游把「版本存疑」误当网络失败处理。
            c2 = {**c, "ok": False, "error": why, "unknown_ver": True,
                  "err_kind": ""}
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

    # 特别提示：只有 N 网来源但**没填** API key —— 这不是"失败"，
    # 而是"需要设置一下"，给用户明确的下一步。
    #
    # ★★ 2026-10-12 收窄条件（用户报的 bug）：
    #   旧的判断是 `if "API key" in err or "没填" in err:` ——
    #   它会连带命中 provider 的 `API key 无效或已过期`，把"你的 key 有毛病
    #   （该去换一个）"改写成"你没填 key（该去填）"。两件事被说成一件，
    #   用户拿着一个明明填着的框反复确认。
    #   现在**只认结构化标签** `err_kind == "no_key"`（真的没填才提示去填）；
    #   "key 无效" 是另一类（bad_key），保留 provider 的原话。
    #   另外 `"没填" in err` 也太宽（任何含"没填"的文案都会被改写），一并去掉。
    if status == ST_ERROR and best is not None:
        if str(best.get("err_kind") or "") == "no_key":
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
        # 并发 4 → 8：三站 API 在这个量级都安全（GitHub 5000 次/小时、
        # Nexus 个人 key 也远不止），20 个模组从 5 批缩到 3 批，检查近减半。
        # ayaka 的限速在 util.throttle 里按线程各自控制，不会猛打站点。
        max_workers: int = 8,
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
        # 「来源总览」重查统计：key -> {cands, trusted, total}
        # 用途：候选找到了但可信度不够、没被采用时，界面要如实说出来
        # （否则用户点完"只查这个"只看到"没找到"，会以为按钮没生效）。
        self.refind_stats: dict[str, dict] = {}
        # 需要用户来选的（"确实搞不清楚"的那些）：key -> {label, reason, candidates}
        self.ambiguous: dict[str, dict] = {}
        self.results: list[dict] = []
        # 被用户屏蔽的模组（key -> {label, at}）：**任何自动检查/搜索都跳过它们**
        self.blocked: dict[str, dict] = load_blocked()

    def cancel(self) -> None:
        self._cancel.set()

    def accept_choice(self, key: str, cand: dict) -> bool:
        """采纳用户从候选里选定的来源 —— **立刻落盘**（受保护记录）。

        ★ 用户明确要求：「每选完一个都要记住，别选一半还有一半没选，
        用户关了就连前面的信息也丢了」→ 所以这里**逐个立刻写盘**，
        绝不攒到最后一起存。
        """
        if not key or not cand:
            return False
        entry = next((e for e in self.library if source_key(e) == key), None)
        if entry is None:
            return False
        set_manual_source(entry, cand)
        keep = {k: v for k, v in cand.items()
                if k in ("provider", "id", "url", "asset")}
        keep["confidence"] = "high"
        self.resolved[key] = [keep]
        self.ambiguous.pop(key, None)
        self._log(f"已按你的选择记住来源：{cand.get('provider')} "
                  f"{cand.get('id') or cand.get('url')}")
        return True

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---------------------------------------------------------------- 加载
    def load(self) -> list[dict]:
        self.library = load_library(self.data_file)
        # 每次开跑都重读屏蔽表：用户可能刚在右键里改过，别用启动时那份旧表
        self.blocked = load_blocked()
        self._log(f"从 Arsenal 读到 {len(self.library)} 个模组"
                  + (f"（其中 {len(self.blocked)} 个已屏蔽，跳过）"
                     if self.blocked else ""))
        return self.library

    def is_blocked(self, entry: dict) -> bool:
        """这个模组是否被屏蔽（引擎内的唯一判断口径）。"""
        key = source_key(entry)
        return bool(key) and key in self.blocked

    # ------------------------------------------------------- 解析来源
    def resolve_sources(self, force_search: bool = False,
                        only_keys: set[str] | None = None,
                        only_providers: set[str] | None = None) -> None:
        """给每个 mod 找出来源。已手工确认过的优先用。

        站点索引过期（默认 7 天）会**自动重建**，不需要用户手动点。

        ``only_keys`` 非空时只解析这些模组 —— 用于「撤销来源并重新识别」
        这种单条操作，免得为一个模组把全库重跑一遍。

        ``only_providers`` 非空时**只查这几个站点**（给「来源总览」里
        "单独查找某一个来源"用）。★ 这种情况下**不能被"已保存的来源"短路** ——
        用户点"只查 GitHub"的意思就是"再去找一遍 GitHub"，
        如果因为盘上已有 a 站来源就直接跳过，这个按钮就白点了。
        """
        saved = load_sources()
        index: list[dict] = []
        if self.use_search and (only_providers is None or "ayaka" in only_providers):
            # ensure_index 内部会判断：不存在、或超过保鲜期 → 自动重建。
            # （旧代码把「过期检查」写在了「索引不存在」的分支里，是死代码，
            #   导致索引建好后再也不更新。）
            index = matcher.ensure_index(progress=self._log, force=force_search)
            if index:
                self._log(f"使用站点索引（{matcher.index_status_text()}，"
                          f"{len(index)} 个模组）")

        todo = [
            e for e in self.library
            if (only_keys is None or source_key(e) in only_keys)
            # ★ 屏蔽的模组不参与来源搜索（用户右键「屏蔽这个模组的更新与查找」）
            and not self.is_blocked(e)
        ]

        # GitHub 搜索并发预填：只有「真要联网搜」的模组才需要 ——
        # 盘上已有来源（手工或此前自动识别的）在下面的循环里会直接采纳、
        # 根本不会走到 GitHub 那一步，提前为它们搜索纯属白烧配额。
        do_search = self.use_search and (only_providers is None
                                        or "github" in only_providers)
        if do_search and not self.cancelled:
            need = [e for e in todo if only_providers is not None
                    or not saved.get(source_key(e))]
            if need:
                try:
                    matcher.batch_prefetch_github(
                        need, self.github_token, log=self._log,
                    )
                except Exception as e:  # noqa: BLE001
                    # 预填失败不该拖垮识别：下面走缓存未命中时会退化为逐条联网搜
                    self._log(f"GitHub 并发预搜失败（退化为逐条搜索）：{e}")

        total = len(todo)
        for i, entry in enumerate(todo, 1):
            if self.cancelled:
                return
            key = source_key(entry)
            self._progress(i - 1, total, f"解析来源：{entry.get('label') or entry['dirname']}")

            # 用户手工确认过的直接采纳。
            # ★ 但"只查某个站点"时必须跳过这个短路 —— 否则用户点
            #   "只查 GitHub" 会因为它已有 a 站来源而什么都不做。
            if only_providers is None and key in saved and saved[key]:
                self.resolved[key] = saved[key]
                continue

            cands = matcher.candidates_for(
                entry,
                github_token=self.github_token,
                use_github_search=do_search,
                ayaka_index=index,
                nexus_api_key=self.api_key,
                log=lambda m, e=entry: self._log(f"[{e.get('label') or e['dirname']}] {m}"),
                only_providers=only_providers,
            )
            if only_providers is not None:
                # 只查某个站点：把新找到的**合并**进原有来源（不覆盖别的站点）
                self.resolved[key] = self._merge_sources(saved.get(key) or [], cands)
                n = len(self.resolved[key])
                # 记下"查到几个候选 / 采用了几个" —— 界面要靠它如实说明
                # （候选可信度不够、没敢替你采用时，必须说清楚，不能只报"没找到"）。
                self.refind_stats[key] = {
                    "cands": len(cands),
                    "trusted": len(self._trusted(cands)),
                    "total": n,
                }
                self._log(f"{entry.get('label')}：{'、'.join(sorted(only_providers))} "
                          f"查到 {len(cands)} 个候选，合并后共 {n} 个来源")
                continue
            # 只自动采纳高可信度的；中低可信留给用户确认
            auto = [c for c in cands if c.get("confidence") == "high"]
            if auto:
                self.resolved[key] = auto
                self._log(f"自动识别：{entry.get('label')} -> {auto[0]['provider']}：{auto[0]['how']}")
                continue
            # 没有确定答案时：**只有"确实搞不清楚"才请用户来选**（用户拍板）。
            # 其余情况一律不打扰 —— 尤其是"只有一个候选但名字对不严"，
            # 按用户要求既不采纳也不问（归到"没找到来源"）。
            reason = matcher.ambiguous_reason(cands)
            if reason:
                self.ambiguous[key] = {
                    "label": entry.get("label") or entry.get("dirname") or "",
                    "reason": reason,
                    "candidates": cands,
                }
                self._log(f"需用户确认：{entry.get('label')}（{reason}，"
                          f"{len(cands)} 个候选）")
            else:
                self._log(f"没能确定来源：{entry.get('label')}"
                          + (f"（有 {len(cands)} 个候选但都不够确定，"
                             "不打扰你）" if cands else ""))
            self.resolved[key] = []
        # 增量模式只看了选中的那几条，分母要用实际处理的条数，
        # 否则会打出「2/20 个已识别」这种看着像失败的数字。
        n_done = sum(1 for e in todo if self.resolved.get(source_key(e)))
        self._log(f"来源解析完成：{n_done}/{total} 个已识别")

    @staticmethod
    def _merge_sources(old: list[dict], new: list[dict]) -> list[dict]:
        """把新查到的来源合并进原有列表（给"只查某一个站点"用）。

        规则（都是为了不破坏用户已有的东西）：
          · 同一个站点**确实重新认出来了**时，替换它（用户点"只查 GitHub"
            就是想让 GitHub 重新认）；
          · ★ 但若这次对该站点只找到 medium/low 的候选（"名字像、不敢确定"），
            就**保留原来的记录**，不要用一条可疑的结果换掉用户手上能用的那条 ——
            宁可维持原状，也不要把来源弄丢。
          · 其它站点的记录**原样保留**（绝不因为只查了一个站点就把别的站点删掉）；
          · 手工指定的记录（manual）**保留其 manual 标记**，除非它属于这次重查的站点。

        新候选的 ``confidence`` **保留**（不剥掉）：下游要靠它区分
        "这次真的认出来了（high）"和"只是有个候选、可信度不够"。
        """
        trusted_new = Engine._trusted(new)
        replaced = {str(c.get("provider") or "") for c in trusted_new}
        out: list[dict] = []
        for o in old:
            prov = str(o.get("provider") or "")
            if prov in replaced:
                continue            # 这个站点被可信地重查了 → 用新结果
            out.append(dict(o))
        # 只把"可信的新候选"和"没被替换掉的那些站点"合起来。
        # 不可信的新候选不入列表：它们既不能替换旧记录，也不该参与后续版本比较
        # （否则会拿一个可疑匹配的版本号去判定"有更新"，把用户引到错的页面）。
        for c in trusted_new:
            out.append(dict(c))
        return out

    @staticmethod
    def _trusted(cands: list[dict]) -> list[dict]:
        """从候选里挑出**可以长期采用**的那些。

        判据（与原 `persist_resolved` 的落盘规则一致，别放宽）：
          · ``confidence == "high"`` —— 这次确实认出来了；
          · **没有** ``confidence`` 字段 —— 这条来自磁盘上已有的记录
            （`_merge_sources` 保留的其它站点条目），它本来就被信任。

        medium / low 一律不算：它们正是"名字像但不敢确定"的那种，
        落盘会固化成错误的"已确认来源"，下次直接拿它去比版本，
        用户会被引到错误的 mod 页面上去（这是原有注释里写明的原因）。
        """
        out = []
        for c in cands or []:
            conf = c.get("confidence")
            if conf is None or conf == "high":
                out.append(c)
        return out

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
            # ★ 屏蔽的模组：一个网络请求都不发（"屏蔽"就是别再去查它）
            if self.is_blocked(entry):
                continue
            if only_keys is not None and key not in only_keys:
                continue
            for src in self.resolved.get(key) or []:
                jobs.append((entry, key, src))
        # 被选中但没有任何来源的模组：也要给出结果（未找到来源）
        entries_to_do = [
            e for e in self.library
            if not self.is_blocked(e)
            and (only_keys is None or source_key(e) in only_keys)
        ]

        total = len(jobs)
        if not total:
            self.results = [self._result_for(e, []) for e in entries_to_do]
            return self.results

        by_key: dict[str, list[dict]] = {k: [] for k in self.resolved}
        done = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {
                ex.submit(check_one_source_all, src, self.api_key, self.github_token,
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
                    got = fut.result()
                except Exception as e:  # noqa: BLE001
                    got = providers._empty(src.get("provider", "?"))
                    got = providers._err(got, "other", f"意外错误：{e}")
                # check_one_source_all 可能返回多条（GitHub 正式版 + 更新的预发布版）
                for res in (got if isinstance(got, list) else [got]):
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
    def persist_resolved(self, refresh_keys: set[str] | None = None) -> None:
        """把自动识别结果写回来源表，下次直接用。

        默认行为（两道保护，确保**用户手工填写的来源永远不被覆盖**）：
          1. 已存在的 key 一律跳过（无论手工还是自动）；
          2. 只保存高可信度的结果 —— 低可信（如索引里 56% 的噪声匹配）
             绝不落盘，否则会固化成错误的"已确认来源"，
             下次直接拿它去比版本，用户会被引到错误的 mod 页面上去。

        ``refresh_keys`` 是**用户刚刚在「来源总览」里显式重查过**的那些 key。
        对它们必须**照写不误** —— 否则用户点了"只查 GitHub"、界面里也提示
        "查到 1 个候选"，结果一落盘就被"已存在就跳过"挡掉，磁盘上还是原来那条
        （用户 2026-10-12 报的正是这个：「重新查找只是显示已经重新查找，结果
        是什么也没显示，明明 GitHub 有这个模组也还是没找到」）。

        注意 ``self.resolved[key]`` 在重查路径下已经是 `_merge_sources` 的产物：
        被重查的站点用新结果、别的站点原样保留（含 manual 标记）。
        但**新候选里 medium/low 的仍然不能落盘** —— 所以这里过一遍
        `_trusted()`，只写"确实认出来的"和"盘上本来就有的"。
        """
        saved = load_sources()
        refresh = refresh_keys or set()
        added = 0
        for key, cands in self.resolved.items():
            explicit = key in refresh
            if not cands:
                continue
            if key in saved and not explicit:
                continue
            if explicit:
                # 重查路径：`self.resolved[key]` 是 `_merge_sources` 的产物 ——
                # "可信的新候选 + 原样保留的旧记录（含 manual）"。
                # ★ 这里**再过一遍 `_trusted()`**：红线要钉在"写盘"这个边界上，
                #   而不是只靠上游记得过滤（上游哪天多一条路径就漏了）。
                picked = self._trusted(cands)
            else:
                picked = [c for c in cands if c.get("confidence") == "high"]
            keep = [
                {k: v for k, v in c.items()
                 if k in ("provider", "id", "url", "asset", "manual")}
                for c in picked
            ]
            if not keep:
                # 显式重查但一个可信的都没认出来（比如只搜到个 medium）——
                # **保留原来那条**，别把用户手上的来源清掉。
                continue
            saved[key] = keep
            added += 1
        if added:
            save_sources(saved)
            self._log(f"已保存 {added} 个来源")


def summarize(results: list[dict]) -> dict:
    """统计各状态数量。"""
    n = {ST_UPDATE: 0, ST_BETA: 0, ST_OK: 0, ST_ERROR: 0, ST_UNKNOWN: 0,
         ST_NO_SOURCE: 0, ST_DOWNLOADED: 0, ST_BLOCKED: 0}
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
