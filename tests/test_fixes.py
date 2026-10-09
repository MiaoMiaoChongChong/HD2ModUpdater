# -*- coding: utf-8 -*-
"""离线测试：把用户报的两个问题钉死，改坏了立刻能发现。

不需要联网（用 tests/fixtures/ 下的真实页面 HTML）、不需要开窗口。
跑法：
    python tests/test_fixes.py
返回 0 = 全通过。

覆盖的两个问题（都来自用户报告）：
  ① a 网（ayakamods）里带中文/特殊符号的模组，程序认不出、也进不了索引
  ② 手动指定来源后，没有任何办法撤销并回到自动识别
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 测试必须完全隔离：不许碰用户真实的数据目录
_TMP = os.path.join(HERE, "_tmp_test_data")
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["HD2U_DATA_DIR"] = _TMP

from hd2_updater import engine, matcher, providers  # noqa: E402

FIX = os.path.join(HERE, "fixtures")

# 站点上真实存在的中文 mod（实测取自列表页）
CN_SLUG_ENC = "%E6%9C%BA%E7%94%B2%E9%83%A8%E4%BD%8D%E8%A1%80%E9%87%8FHUD.4775"
CN_SLUG_RAW = "机甲部位血量HUD.4775"
CN_URL_ENC = "https://ayakamods.com/mods/" + CN_SLUG_ENC + "/"
CN_URL_RAW = "https://ayakamods.com/mods/" + CN_SLUG_RAW + "/"

_fail: list[str] = []
_pass = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _pass
    if cond:
        _pass += 1
        print(f"  ✓ {name}")
    else:
        _fail.append(f"{name}　{detail}")
        print(f"  ✗ {name}　{detail}")


def eq(name: str, got, want) -> None:
    check(name, got == want, f"（得到 {got!r}，期望 {want!r}）")


# ==========================================================================
# 问题① a 网中文/特殊符号
# ==========================================================================
print("\n【问题①】a 网中文/特殊符号的模组")

print(" · slug 编解码")
eq("原样中文 -> 百分号编码", providers.ayaka_encode(CN_SLUG_RAW), CN_SLUG_ENC)
eq("已编码的不会二次编码", providers.ayaka_encode(CN_SLUG_ENC), CN_SLUG_ENC)
eq("编码 -> 中文（给人看）", providers.ayaka_decode(CN_SLUG_ENC), CN_SLUG_RAW)
eq("英文 slug 不受影响", providers.ayaka_encode("hd2runtime.4260"), "hd2runtime.4260")
eq("URL 里的空格/安全字符",
   providers.ayaka_encode("a b_c-d.e.123"), "a%20b_c-d.e.123")

# 幂等性：用户可能反复粘贴、反复保存同一条来源。不幂等的话会"越编越烂" ——
# 实测踩过：safe 里保留了 "%"，于是 %25477.123 -> %477.123 -> G7.123。
print(" · 编码必须幂等（用户反复粘贴/反复保存同一条时不能越编越烂）")
for raw in (CN_SLUG_RAW, CN_SLUG_ENC, "hd2runtime.4260", "100%.456",
            "%25477.123", "a#b.1", "a?b.1", "a&b.1", "a+b.1",
            "全角（括号）.999", "Ünïcödé-ünï.88"):
    once = providers.ayaka_encode(raw)
    twice = providers.ayaka_encode(once)
    check(f"幂等：{raw[:22]!r}", once == twice, f"（{once!r} -> {twice!r}）")
    check(f"  └ URL 是纯 ASCII：{raw[:18]!r}", providers.ayaka_url(raw).isascii())

print(" · 从各种粘法里取 slug")
eq("完整网址（编码）", providers.ayaka_slug(CN_URL_ENC), CN_SLUG_ENC)
eq("完整网址（原样中文）", providers.ayaka_slug(CN_URL_RAW), CN_SLUG_RAW)
eq("没有结尾斜杠", providers.ayaka_slug(CN_URL_ENC.rstrip("/")), CN_SLUG_ENC)
eq("带 ?query 尾巴", providers.ayaka_slug(CN_URL_ENC + "?do=download"), CN_SLUG_ENC)
eq("裸 slug", providers.ayaka_slug(CN_SLUG_RAW), CN_SLUG_RAW)

print(" · 手动指定来源（右键「指定来源…」）")
for label, text in (
    ("中文网址·编码形态", CN_URL_ENC),
    ("中文网址·原样形态", CN_URL_RAW),
    ("中文裸 slug", CN_SLUG_RAW),
    ("全角括号（better-orbital-strikes）",
     "https://ayakamods.com/mods/"
     "%E6%9B%B4%E5%A5%BD%E7%9A%84%E8%BD%A8%E9%81%93%E6%89%93%E5%87%BB"
     "%EF%BC%88better-orbital-strikes%EF%BC%89.4405/"),
):
    got = providers.detect_source(text)
    check(f"识别 {label}", got is not None and got.get("provider") == "ayaka",
          f"（得到 {got}）")
    if got:
        check(f"  └ 存的是可请求的编码形态 {label}",
              "%" in got["id"] and " " not in got["id"], f"（{got['id']}）")
        check(f"  └ 显示的是中文（不是 %E6%9C） {label}",
              "%" not in got["display"], f"（{got['display']}）")

print(" · README 里教过的「偷懒写法」也要能用")
# README/对话框都写过「GitHub 填 作者/仓库、Nexus 填数字 ID」，
# 但老实现只认完整网址 —— 照文档填会被判"认不出"，属于文档与实现不一致。
for label, text, want_prov, want_id in (
    ("GitHub 裸 作者/仓库", "SkyeShade/HD2Runtime", "github", "SkyeShade/HD2Runtime"),
    ("GitHub 裸 + .git", "SkyeShade/HD2Runtime.git", "github", "SkyeShade/HD2Runtime"),
    ("N 网裸数字 ID", "15298", "nexus", "15298"),
    ("N 网 裸 mods/数字", "mods/15298", "nexus", "15298"),
    ("a 网裸英文 slug", "hd2runtime.4260", "ayaka", "hd2runtime.4260"),
):
    got = providers.detect_source(text)
    check(f"识别 {label}", got is not None and got["provider"] == want_prov
          and got["id"] == want_id, f"（得到 {got}）")
check("GitHub 设置页的裸写法也不认",
      providers.detect_source("settings/tokens") is None)
# 「mods/xxx」是用户从地址栏只拖了一半的常见形态。以前会掉进「作者/仓库」
# 分支，编出 github.com/mods/hd2runtime.4260 这种不存在的仓库，
# 而且标了 manual 就再不会被自动纠正 —— 新的错认面，必须钉住。
check("mods/<a网 slug> 不会变成假的 GitHub 仓库",
      (providers.detect_source("mods/hd2runtime.4260") or {}).get("provider") == "ayaka",
      f"（{providers.detect_source('mods/hd2runtime.4260')}）")
check("mods/<数字> 认成 N 网",
      (providers.detect_source("mods/15298") or {}).get("provider") == "nexus",
      f"（{providers.detect_source('mods/15298')}）")
check("mods/ 后面的乱码不被硬编成 GitHub 仓库",
      providers.detect_source("mods/games") is None,
      f"（{providers.detect_source('mods/games')}）")

print(" · 原有识别能力不许退化（加了裸写法支持后逐条回归）")
for text, want in (
    ("https://github.com/SkyeShade/HD2Runtime", "github"),
    ("https://github.com/SkyeShade/HD2Runtime/releases/tag/v1", "github"),
    ("https://github.com/SkyeShade/HD2Runtime.git", "github"),
    ("github.com/SkyeShade/HD2Runtime", "github"),
    ("https://www.nexusmods.com/helldivers2/mods/15298", "nexus"),
    ("https://www.nexusmods.com/helldivers2/mods/15298?tab=files", "nexus"),
    ("https://www.nexusmods.com/helldivers2/mods/15298/files?file_id=99", "nexus"),
    ("www.nexusmods.com/helldivers2/mods/15298", "nexus"),
    ("https://ayakamods.com/mods/hd2runtime.4260/", "ayaka"),
    ("https://ayakamods.com/mods/机甲部位血量HUD.4775/", "ayaka"),
):
    got = providers.detect_source(text)
    check(f"{text[:46]!r} -> {want}",
          got is not None and got["provider"] == want, f"（得到 {got}）")
for text in ("", "   ", "https://github.com/settings/tokens",
             "settings/tokens", "https://ayakamods.com/", "hello world",
             "https://example.com/whatever"):
    check(f"仍然拒绝 {text[:40]!r}", providers.detect_source(text) is None)

print(" · 不能认错的东西（防呆）")
check("列表页地址不被当成模组",
      providers.detect_source(
          "https://ayakamods.com/mods/games/helldivers-2.119/?page=2") is None)
check("站点主页不被当成模组",
      providers.detect_source("https://ayakamods.com/") is None)
check("GitHub 设置页不被当成仓库",
      providers.detect_source("https://github.com/settings/tokens") is None)
check("空字符串不报异常", providers.detect_source("") is None)
check("乱码不报异常", providers.detect_source("随便一句话") is None)
eq("英文地址仍然照常识别",
   (providers.detect_source("https://ayakamods.com/mods/hd2runtime.4260/") or {}).get("id"),
   "hd2runtime.4260")
eq("N 网照常", (providers.detect_source(
    "https://www.nexusmods.com/helldivers2/mods/15298") or {}).get("id"), "15298")
eq("GitHub 照常", (providers.detect_source(
    "https://github.com/SkyeShade/HD2Runtime") or {}).get("id"), "SkyeShade/HD2Runtime")
eq("ayaka_check 拿到列表页时给的是「这是列表页」而不是「站点改版」",
   "列表页" in (providers.ayaka_check(
       "https://ayakamods.com/mods/games/helldivers-2.119/?page=2").get("error") or ""),
   True)

print(" · 爬虫能抓到中文卡片（用真实页面 HTML）")
list_html = open(os.path.join(FIX, "ayaka_list_p1.html"), encoding="utf-8").read()

# 直接调解析：把 http_get 换成读本地文件
_orig_get = providers.http_get
providers.http_get = lambda *a, **k: list_html
try:
    items = providers.ayaka_list_page(1)
finally:
    providers.http_get = _orig_get

enc_slugs = [i["slug"] for i in items if "%" in i["slug"]]
check("抓到的卡片里有中文 mod", len(enc_slugs) >= 5,
      f"（只有 {len(enc_slugs)} 个）")
check("中文 mod 的标题是中文（不是 %E6%9C 串）",
      all("%" not in i["title"] for i in items if "%" in i["slug"]),
      f"（例子：{[i['title'] for i in items if '%' in i['slug']][:2]}）")
check("抓到的 slug 都能拼出可请求的地址（不含裸中文/空格）",
      all(" " not in providers.ayaka_url(i["slug"])
          and providers.ayaka_url(i["slug"]).isascii() for i in items))
_bad_reserved = [i["slug"] for i in items if providers.ayaka_is_reserved(i["slug"])]
eq("没有把列表页/功能页混进来", _bad_reserved, [])
eq("索引去重后没有重复 slug", len({i["slug"] for i in items}), len(items))

print(" · 索引搜索能匹配中文 mod（老代码这里是 0 分）")
idx = [{"slug": CN_SLUG_ENC, "title": "机甲部位血量HUD", "author": "", "desc": ""},
       {"slug": "hd2runtime.4260", "title": "HD2Runtime", "author": "", "desc": ""}]
hits = matcher.search_ayaka_index("机甲部位血量HUD", idx, top=5)
check("本地名字「机甲部位血量HUD」能命中中文索引条目",
      bool(hits) and hits[0]["slug"] == CN_SLUG_ENC,
      f"（得到 {[h['slug'] for h in hits]}）")
eq("slug_text 解出了中文", matcher.slug_text(CN_SLUG_ENC), "机甲部位血量HUD")

# --------------------------------------------------------------------------
# 错配（false positive）：这是本工具最严重的失效模式 —— 认不出来只是没帮上
# 忙，认错了会让人跑去更新一个完全无关的模组。
# 实测踩到过：中文 HUD 模组被 high 置信度错配到「VRC Tell CE-07 CM-21」。
# --------------------------------------------------------------------------
print(" · 相似度不能把「一两个字符」当成包含关系（错配的根因）")
eq("单字符描述不再被当成包含",
   round(matcher.similarity("Mech part HUD", "e"), 3), 0.143)
check("毫不相干的名字相似度必须很低",
      matcher.similarity("Mech part HUD", "VRC Tell CE-07 CM-21") < 0.3,
      f"（{matcher.similarity('Mech part HUD', 'VRC Tell CE-07 CM-21'):.3f}）")
check("真正的包含关系仍然给高分",
      matcher.similarity("Auto Reload", "HD2 Auto Reload") >= 0.9,
      f"（{matcher.similarity('Auto Reload', 'HD2 Auto Reload'):.3f}）")
check("单词级包含仍然给高分（带版本/后缀的站上名）",
      matcher.similarity("HD2Runtime", "HD2Runtime 简体中文") >= 0.85,
      f"（{matcher.similarity('HD2Runtime', 'HD2Runtime 简体中文'):.3f}）")
check("一两个字符的退化输入不会被当成包含",
      matcher.similarity("Mech part HUD", "e") < 0.3
      and matcher.similarity("Mech part HUD", "a") < 0.3,
      f"（{matcher.similarity('Mech part HUD', 'e'):.3f}）")
# 门槛值本身是量出来的（见 matcher._MIN_CONTAINS_LEN 的注释）：
# 真实 871 条索引上，本实现（词边界 + 门槛 3）比原实现（裸 in）
#   自召回 809 -> 822（更好），真错配 21 -> 13（更好）
# 两个指标同时改善，所以这不是"为了修一个而弄坏另一个"的妥协。
check("门槛取 3（实测既不丢短名的真 mod、又比裸 in 少错配）",
      matcher._MIN_CONTAINS_LEN == 3, f"（{matcher._MIN_CONTAINS_LEN}）")
check("短名仍然能被词级包含认出来（DSS / Uzi 这类，门槛=4 会丢）",
      matcher.similarity("DSS", "DSS Supreme") >= 0.9
      and matcher.similarity("Uzi Stalwart", "Uzi Stalwart") == 1.0,
      f"（{matcher.similarity('DSS', 'DSS Supreme'):.3f}）")
check("通用短词不会因为包含就贴上无关长名",
      matcher.similarity("HUD", "VRC Tell CE-07 CM-21") < 0.3,
      f"（{matcher.similarity('HUD', 'VRC Tell CE-07 CM-21'):.3f}）")

print(" · 「有更新的版本」是弱线索，不能冒充「版本号已核对一致」")
# 注意 disambiguate_ayaka 在 hits <= 1 时直接返回（没有可消歧的），
# 所以这里必须给两个候选，才真的走到版本核对那一步。
_vrc = [
    {"slug": "vrc-tell-ce-07-cm-21.2483", "title": "VRC Tell CE-07 CM-21",
     "score": 0.72, "author": "", "desc": "e"},
    {"slug": "p-34-breacher-6-spare-rounds.4102", "title": "P-34 Breacher",
     "score": 0.556, "author": "", "desc": ""},
]


def _ver_only_newer(slug):
    """本地 1、线上 2 → 只能得到 newer（弱线索）。"""
    return {"ok": True, "version": "2"}


_out = matcher.disambiguate_ayaka(list(_vrc), "1", check_version=_ver_only_newer)
_v = next(h for h in _out if h["slug"] == "vrc-tell-ce-07-cm-21.2483")
check("只有 newer 时不标 verified", not _v.get("verified"), f"（{_v}）")
_same = matcher.disambiguate_ayaka(
    list(_vrc), "2",
    check_version=lambda s: {"ok": True, "version": "2"})
_v2 = next(h for h in _same if h["slug"] == "vrc-tell-ce-07-cm-21.2483")
check("版本真的对得上才标 verified", _v2.get("verified") is True, f"（{_v2}）")

print(" · 面向用户的文案里不能出现百分号编码的原始 slug")
# 用户反馈的原话就是"不支持带中文/特殊符号的网址"——把 %E6%9C%BA%E7%94%B2…
# 当模组名直接甩给他看，在他看来程序就是坏的。凡是人看得见的地方都必须解码。
_say_log: list[str] = []
_idx2 = [
    {"slug": CN_SLUG_ENC, "title": "机甲部位血量HUD", "author": "", "desc": ""},
    {"slug": "other-mod.1234", "title": "", "author": "", "desc": ""},
]
_cands = matcher.candidates_for(
    {"uuid": "u", "label": "机甲部位血量HUD", "dirname": "Mech part HUD",
     "installed_version": ""},
    github_token="", use_github_search=False, ayaka_index=_idx2,
    log=_say_log.append)
# 注意不能简单地找 "%" ——「相似度 100%」里的百分号是正常文案。
# 要找的是真正的百分号**转义序列**（%XX 形式，如 %E6）。
_PCT_ESCAPE = __import__("re").compile(r"%[0-9A-Fa-f]{2}")
_bad_logs = [m for m in _say_log if _PCT_ESCAPE.search(m)]
check("状态栏日志里不出现 %XX 转义序列", not _bad_logs, f"（{_bad_logs}）")
check("候选文案里也不出现 %XX 转义序列",
      all(not _PCT_ESCAPE.search(str(c.get("alternatives", ""))) for c in _cands),
      f"（{[c.get('alternatives') for c in _cands]}）")
check("候选的 how 文案不出现 %XX 转义序列",
      all(not _PCT_ESCAPE.search(c.get("how") or "")
          for c in _cands if c["provider"] == "ayaka"),
      f"（{[c.get('how') for c in _cands]}）")
# 反向自检：这个正则必须真能抓到编码串，否则上面三条是在空转
check("（自检）该正则可识别真正的编码串",
      bool(_PCT_ESCAPE.search("ayakamods 搜到 %E6%9C%BA%E7%94%B2.4775")) and
      not bool(_PCT_ESCAPE.search("名字相似度 100%")))

print(" · 索引格式版本（否则老索引会继续用满 7 天、修复对用户不生效）")
matcher.save_index(items)
check("索引落盘带格式版本号",
      matcher.INDEX_FORMAT >= 2 and bool(matcher.load_index()),
      f"（读回 {len(matcher.load_index())} 条）")
# 模拟"老格式"（裸 list）：必须被当成无效、触发重建
import json  # noqa: E402
with open(matcher.index_path(), "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False)
eq("老格式索引被判为需要重建", matcher.load_index(), [])
# 模拟"格式版本对不上"：同样必须重建
with open(matcher.index_path(), "w", encoding="utf-8") as f:
    json.dump({"format": 1, "items": items}, f, ensure_ascii=False)
eq("旧版本号索引被判为需要重建", matcher.load_index(), [])
matcher.save_index(items)
check("重新保存后可用", len(matcher.load_index()) == len(items))

print(" · 索引状态文案不能说假话（老格式时不能显示「刚刚更新过」）")
with open(matcher.index_path(), "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False)          # 刚写过 = mtime 很新
check("老格式时状态文案提示要重建，而不是「刚刚更新过」",
      "刚刚更新过" not in matcher.index_status_text(),
      f"（{matcher.index_status_text()}）")
check("老格式被判为 stale", matcher.index_is_stale() is True)
matcher.save_index(items)
check("恢复成新格式后状态正常",
      matcher.index_status_text() == "刚刚更新过",
      f"（{matcher.index_status_text()}）")
check("新格式不算 stale", matcher.index_is_stale() is False)

print(" · 索引文件被改坏时，读索引不能抛异常（界面/引擎的热路径）")
for bad in ('{"format": "two", "items": []}', '{"format": [2], "items": []}',
            '{"format": null, "items": []}', "[1,2,3]", '"a string"', "123", ""):
    with open(matcher.index_path(), "w", encoding="utf-8") as f:
        f.write(bad)
    try:
        got = matcher.load_index()
        txt = matcher.index_status_text()
        check(f"坏索引 {bad[:28]!r} 不抛异常", got == [],
              f"（返回 {got}，状态「{txt}」）")
    except Exception as ex:  # noqa: BLE001
        check(f"坏索引 {bad[:28]!r} 不抛异常", False,
              f"（{type(ex).__name__}: {ex}）")
matcher.save_index(items)

print(" · 爬取没抓完时不许覆盖好索引（否则一次抖动锁死 7 天）")
# ayaka_crawl 一遇空页就 break：被 Cloudflare 拦在第 N 页时会返回残缺列表。
# 原来只看 `if items:` 就落盘 —— 800+ 条的好索引会被换成几十条，
# 而且 index_is_stale() 认为它新鲜，用户 7 天都在用残缺索引。
_full = matcher.load_index()
check("先放一份完整索引", len(_full) == len(items), f"（{len(_full)}）")
short = [{"slug": f"fake-{i}.1", "title": f"Fake {i}", "author": "", "desc": ""}
         for i in range(5)]
_saved_crawl = providers.ayaka_crawl
providers.ayaka_crawl = lambda *a, **k: list(short)
try:
    got = matcher.rebuild_index()
finally:
    providers.ayaka_crawl = _saved_crawl
check("残缺结果被拒，盘上仍是完整索引",
      len(matcher.load_index()) == len(items),
      f"（盘上 {len(matcher.load_index())} 条，残缺结果只有 {len(short)} 条）")
check("返回的也是完整索引（不是残缺的）", len(got) == len(items),
      f"（{len(got)}）")
# 反例：正常的更大的结果应该被接受
_bigger = list(items) + [{"slug": "brand-new.9999", "title": "Brand New",
                          "author": "", "desc": ""}]
providers.ayaka_crawl = lambda *a, **k: list(_bigger)
try:
    matcher.rebuild_index()
finally:
    providers.ayaka_crawl = _saved_crawl
check("完整的抓取结果仍然正常落盘", len(matcher.load_index()) == len(_bigger),
      f"（{len(matcher.load_index())} vs {len(_bigger)}）")
matcher.save_index(items)          # 还原成测试用的那份

# ==========================================================================
# 问题② 撤销手动指定的来源
# ==========================================================================
print("\n【问题②】撤销来源并重新识别")

LIB = [
    {"uuid": "u-1", "label": "机甲部位血量HUD", "dirname": "Mech part HUD",
     "installed_version": "1.11.1"},
    {"uuid": "u-2", "label": "HD2Runtime", "dirname": "HD2Runtime",
     "installed_version": "0.28.1"},
]

# 用户手动填了一个**错的**来源（这正是他说的"手动指定以后没法重新自动匹配"）
engine.set_manual_source(LIB[0], {"provider": "ayaka", "id": "wrong-mod.9999",
                                 "url": "https://ayakamods.com/mods/wrong-mod.9999/"})
saved = engine.load_sources()
check("手工来源已落盘并带 manual 标记",
      saved.get("u-1", [{}])[0].get("manual") is True, f"（{saved.get('u-1')}）")

print(" · 全库清缓存不会动它（这是原本的「保护」，也正是用户没有出口的原因）")
n = engine.clear_auto_sources()
check("清自动来源后手工记录仍在", bool(engine.load_sources().get("u-1")),
      f"（清了 {n} 条，但 u-1 被保留了）")

print(" · 单个模组的撤销")
# 先给**另一个**模组也存一条来源，用来验证撤销真的是"只删这一个"
engine.set_manual_source(LIB[1], {"provider": "nexus", "id": "99999",
                                 "url": "https://www.nexusmods.com/helldivers2/mods/99999"})
check("准备：u-2 也有一条自己的来源",
      (engine.load_sources().get("u-2") or [{}])[0].get("id") == "99999")
gone = engine.clear_source(LIB[0])
eq("撤销返回被删掉的记录", [g["id"] for g in gone], ["wrong-mod.9999"])
check("撤销后这一条真的没了", "u-1" not in engine.load_sources())
# 这条断言原来写成 check(..., True)（恒真、零信息量）——
# 换成真的去看 u-2 的记录还在不在。
check("撤销只影响这一个模组（u-2 的记录没被碰）",
      (engine.load_sources().get("u-2") or [{}])[0].get("id") == "99999",
      f"（现在 {engine.load_sources()}）")

print(" · 撤销后能重新自动识别（不再用手工那条）")
# 隔离：识别时只喂本地索引，不联网
eng = engine.Engine(os.path.join(HERE, "_nonexistent_lib.json"), use_search=True)
eng.library = [dict(LIB[0])]
providers_backup = None
_rot = matcher.candidates_for


def _fake_candidates(entry, **kw):
    return [{"provider": "ayaka", "id": CN_SLUG_ENC,
             "url": providers.ayaka_url(CN_SLUG_ENC),
             "how": "（测试桩）索引命中", "confidence": "high"}]


matcher.candidates_for = _fake_candidates
try:
    eng.resolve_sources()
finally:
    matcher.candidates_for = _rot
new_src = eng.resolved.get("u-1") or []
check("重新识别拿到了自动结果", bool(new_src), f"（{eng.resolved}）")
check("自动结果指向的是新识别的来源，不是手工那条",
      bool(new_src) and new_src[0]["id"] == CN_SLUG_ENC,
      f"（{new_src[:1]}）")

print(" · 撤销后没有来源记录时，不该报错、也不该乱删")
# 用一个**从没存过来源**的模组（LIB[1] 前面被用来验证"别的记录没被碰"，有力据）
_fresh = {"uuid": "u-never", "label": "从没指定过来源", "dirname": "never"}
eq("对没有记录的可重入调用返回空", engine.clear_source(_fresh), [])
eq("对同一个再调一次也是空", engine.clear_source(_fresh), [])

print(" · sources.json 被改坏时，撤销不能崩、也不能误删别的模组")
# 用户可能手工编辑过这个文件（或在旧版本里写坏过）。所有情况都必须：
# 不抛异常、且**绝不**动到别的模组的记录。
_saved_backup = engine.load_sources()
for name, content in (
    ("空文件", ""),
    ("不是 JSON", "{ this is not json"),
    ("空对象", "{}"),
    ("值是 null", '{"u-1": null}'),
    ("值是字符串", '{"u-1": "oops"}'),
    ("值是数字", '{"u-1": 123}'),
    ("正常一条", '{"u-1": [{"provider": "ayaka", "id": "x.1"}],'
                 ' "keep": [{"provider": "nexus", "id": "9"}]}'),
):
    with open(engine.sources_path(), "w", encoding="utf-8") as f:
        f.write(content)
    try:
        gone = engine.clear_source(LIB[0])
        after = engine.load_sources() or {}
        if name == "正常一条":
            check(f"坏档[{name}]：只删了该删的、别的模组还在",
                  gone and "keep" in after and "u-1" not in after,
                  f"（gone={gone} after={sorted(after)}）")
        else:
            check(f"坏档[{name}]：不抛异常", True)
    except Exception as ex:  # noqa: BLE001
        check(f"坏档[{name}]：不抛异常", False, f"（{type(ex).__name__}: {ex}）")
engine.save_sources(_saved_backup)

print(" · 连续撤销两次也不炸")
engine.set_manual_source(LIB[0], {"provider": "ayaka", "id": "x.1"})
_first = engine.clear_source(LIB[0])
_second = engine.clear_source(LIB[0])
check("第一次拿到记录、第二次拿到空", bool(_first) and _second == [],
      f"（{_first} / {_second}）")

print(" · 增量解析：只在选中的模组上花时间（原来会跑全库）")
# 注意要用**没有已保存来源**的模组：resolve_sources 对盘上已有的 key 会直接
# 采纳、不再调 candidates_for，用它当探针会看不出"有没有只跑选中的那几个"。
_fresh1 = {"uuid": "u-fresh-1", "label": "探针甲", "dirname": "probe-a"}
_fresh2 = {"uuid": "u-fresh-2", "label": "探针乙", "dirname": "probe-b"}
eng2 = engine.Engine(os.path.join(HERE, "_nonexistent_lib.json"), use_search=False)
eng2.library = [dict(_fresh1), dict(_fresh2)]
touched: list[str] = []


def _spy(entry, **kw):
    touched.append(entry["uuid"])
    return []


matcher.candidates_for = _spy
try:
    eng2.resolve_sources(only_keys={"u-fresh-2"})
finally:
    matcher.candidates_for = _rot
eq("只解析被指定的那一个", touched, ["u-fresh-2"])
check("结果里也只含被指定的那一个", set(eng2.resolved) <= {"u-fresh-2"},
      f"（{set(eng2.resolved)}）")

# ==========================================================================
print()
print("=" * 62)
if _fail:
    print(f"✗ {len(_fail)} 项没过（通过 {_pass} 项）：")
    for f in _fail:
        print("   · " + f)
    sys.exit(1)
print(f"✓ 全部通过（{_pass} 项）")
sys.exit(0)
