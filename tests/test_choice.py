# -*- coding: utf-8 -*-
"""来源"候选 / 需用户确认"的自测：三站统一攒候选 + 只有确实搞不清楚才问用户。

全部离线（联网的 nexus_search / github 搜索都打桩），且只写临时目录。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HD2U_READONLY_GUARD"] = "1"

from hd2_updater import engine as E  # noqa: E402
from hd2_updater import matcher as M  # noqa: E402
from hd2_updater import providers as P  # noqa: E402

PASS = 0
FAIL = 0
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
        print(f"  FAIL: {name} {extra}")


# ------------------------------------------------------------------ 关键词清洗
def t_search_terms():
    print("[关键词清洗：★回归 —— 别把正常英文单词当 N 网随机码删掉]")
    e = {"label": "", "dirname": "Resupply Boxes Emissive_AR359919"}
    terms = M.search_terms(e)
    check("★ 末尾的正常单词 Emissive 被保住",
          terms and terms[0] == "Resupply Boxes Emissive", str(terms))
    # 这条 bug 让搜索词残缺，三个站点的搜索全被拖累（实测 Resupply 搜不到）
    check("没被削成 Resupply Boxes", "Resupply Boxes" not in terms[:1], str(terms))

    # 但 N 网命名的尾巴 + 随机 slug 仍要剥干净。
    # ★ 注意断言方式：v1.8.5 起「紧凑词」（去空格的连写形式，如
    #   BingusSharedLoader）会**有意排在第一位** —— 因为 GitHub 搜索按词
    #   AND 匹配，多词组合经常 0 结果（见 test_settings_scroll 的
    #   t_compact_search_terms）。所以这里断言"清洗后的完整名在列表里"，
    #   而不是"它必须是第 0 位"，否则会误判。
    e2 = {"label": "", "dirname":
          "Bingus Shared Loader 16292 19 2026-10-05T00-28Z eqrMD7Qqb_AR864690"}
    t2 = M.search_terms(e2)
    check("N 网尾巴+随机码仍被剥掉",
          "Bingus Shared Loader" in t2, str(t2))
    check("尾巴里的编号/随机码没残留",
          all("16292" not in t and "eqrMD7Qqb" not in t for t in t2), str(t2))
    check("紧凑词排在前面（GitHub 搜索优先用它）",
          t2 and t2[0] == "BingusSharedLoader", str(t2))
    e3 = {"label": "", "dirname":
          "Glowing Supply Icons v4.2 but it glows again 9120 4.2 "
          "2026-09-01T02-37Z IzUPRha2W_AR244007"}
    t3 = M.search_terms(e3)
    check("带 slug 的长名也洗干净",
          any("IzUPRha2W" not in t and "glows again" in t for t in t3), str(t3))


def t_strip_annotations():
    print("[剥括号注释]")
    check("圆括号英文", M.strip_annotations("Resupply Boxes Emissive (LUT edit)")
          == "Resupply Boxes Emissive")
    check("中文全角括号", M.strip_annotations("HD2 HUD Plus 简体中文（外挂）")
          == "HD2 HUD Plus 简体中文")
    check("方括号", M.strip_annotations("Foo [test] Bar") == "Foo Bar")
    check("没有括号时原样", M.strip_annotations("Plain Name") == "Plain Name")


def t_site_name_score():
    print("[站上名核对：多出实词 = 另一个 mod]")
    sc, ex = M.site_name_score({"label": "Resupply Boxes Emissive"},
                               "Resupply Boxes Emissive (LUT edit)")
    check("★ (LUT edit) 是注释、不算多余实词（曾经被误杀）",
          len(ex) == 0 and sc >= M.STRICT_SCORE, f"{sc:.2f} {sorted(ex)}")

    sc2, ex2 = M.site_name_score({"label": "Vanilla Plus Megapack"},
                                 "Vanilla Plus Megapack Chinese localization")
    check("★ 中文汉化版被多实词判据拦住",
          len(ex2) >= 2, f"{sc2:.2f} {sorted(ex2)}")

    sc3, ex3 = M.site_name_score({"label": "HD2 HUD+"},
                                 "HD2 HUD Resource Markers")
    check("长名套短名也被拦住（既有的假匹配案例）",
          len(ex3) >= 2, f"{sc3:.2f} {sorted(ex3)}")

    sc4, ex4 = M.site_name_score({"label": "Improved Railgun Sights"},
                                 "Improved Railgun Sights")
    check("完全同名 → 满分", abs(sc4 - 1.0) < 1e-6 and not ex4, f"{sc4:.2f}")


def t_ambiguous_reason():
    print("[什么时候才允许打扰用户 —— 只有两种情况]")
    check("没候选 → 不问", M.ambiguous_reason([]) == "")
    check("已有确定答案 → 不问",
          M.ambiguous_reason([{"confidence": "high", "score": 0.9}]) == "")
    check("一个说得通 → 不问（用户明确不要这种打扰）",
          M.ambiguous_reason([{"confidence": "medium", "score": 0.7}]) == "")
    check("★ 两个都说得通 → 问",
          M.ambiguous_reason([{"confidence": "medium", "score": 0.7},
                              {"confidence": "medium", "score": 0.65}])
          == "有多个候选都说得通")
    check("两个都不够像 → 不问",
          M.ambiguous_reason([{"confidence": "medium", "score": 0.3},
                              {"confidence": "low", "score": 0.1}]) == "")
    check("一个是 high 一个说得通 → 已有答案，不问",
          M.ambiguous_reason([{"confidence": "high", "score": 0.9},
                              {"confidence": "medium", "score": 0.7}]) == "")


# ------------------------------------------------------------------ 第 6 条路
def t_nexus_search_path():
    print("[N 网按名字搜这条新路]")
    real = P.nexus_search

    def stub(cases):
        def f(name, key, *a, **k):
            for key_part, hits in cases:
                if key_part.lower() in (name or "").lower():
                    return hits
            return []
        return f

    def run(entry, hits, key="k"):
        P.nexus_search = stub([("", hits)])
        try:
            return M.candidates_for(entry, use_github_search=False,
                                    ayaka_index=[], nexus_api_key=key)
        finally:
            P.nexus_search = real

    entry = {"label": "Resupply Boxes Emissive", "dirname": "x"}

    # ① 只有一个、且对得很严 → 直接采纳（high），不打扰用户
    c = run(entry, [{"mod_id": "6780", "name": "Resupply Boxes Emissive (LUT edit)"}])
    check("唯一且对得严 → 采纳为 high", len(c) == 1 and c[0]["confidence"] == "high",
          str([(x["id"], x["confidence"]) for x in c]))
    check("给的编号对", c and c[0]["id"] == "6780")
    check("不打扰用户", M.ambiguous_reason(c) == "")

    # ② 两个都说得通 → 都要列出来、且需要用户确认
    c2 = run(entry, [
        {"mod_id": "6780", "name": "Resupply Boxes Emissive"},
        {"mod_id": "9999", "name": "Resupply Boxes Emissive Redux"},
    ])
    check("两个候选都列为 medium",
          len(c2) == 2 and all(x["confidence"] == "medium" for x in c2),
          str([(x["id"], x["confidence"]) for x in c2]))
    check("★ 判定为需要用户确认",
          M.ambiguous_reason(c2) == "有多个候选都说得通", M.ambiguous_reason(c2))
    check("候选带上了相似度分数（供界面排序/展示）",
          all(float(x.get("score") or 0) > 0 for x in c2))

    # ③ 站上名多出两个实词 → 判为另一个 mod，直接丢掉（不采纳也不打扰）
    c3 = run(entry, [{"mod_id": "16627",
                      "name": "Resupply Boxes Emissive Chinese localization"}])
    check("多出实词的候选被丢掉", c3 == [], str(c3))

    # ④ 完全不相关 → 丢掉
    c4 = run(entry, [{"mod_id": "1", "name": "Totally Different Thing"}])
    check("不相关的丢掉", c4 == [], str(c4))

    # ⑤ 没 key 时这条路不跑（省请求，也不误报）
    P.nexus_search = stub([("", [{"mod_id": "6780",
                                  "name": "Resupply Boxes Emissive"}])])
    try:
        c5 = M.candidates_for(entry, use_github_search=False, ayaka_index=[],
                              nexus_api_key="")
    finally:
        P.nexus_search = real
    check("没 key 时不走这条路", c5 == [], str(c5))

    # ⑥ 已经有**确定答案**（high：nexusData / manifest 来源）→ 不该再去搜
    called = {"n": 0}

    def counting(name, key, *a, **k):
        called["n"] += 1
        return []

    P.nexus_search = counting
    try:
        M.candidates_for({"label": "X", "dirname": "X", "nexus_mod_id": "16294"},
                         use_github_search=False, ayaka_index=[],
                         nexus_api_key="k")
    finally:
        P.nexus_search = real
    check("已有高可信答案时不浪费请求", called["n"] == 0, str(called))

    # 但"目录名解析出的 medium 候选"**不算**确定答案 —— 仍该去搜一次
    # （medium 只是猜的编号，按名字搜可能找到更靠谱的）
    called["n"] = 0
    P.nexus_search = counting
    try:
        M.candidates_for({"label": "X", "dirname": "X 16294 35 2026-09-29T19-48Z aaa",
                          "nexus_mod_id": ""},
                         use_github_search=False, ayaka_index=[],
                         nexus_api_key="k")
    finally:
        P.nexus_search = real
    check("只有 medium 时仍会去搜（medium 不算确定）", called["n"] > 0, str(called))


# ------------------------------------------------------------------ 引擎侧
def t_engine_ambiguous_and_accept():
    print("[引擎：攒下待确认 + 用户选完**立刻落盘**]")
    tmp = tempfile.mkdtemp(prefix="hd2u_choice_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        real = P.nexus_search
        P.nexus_search = lambda name, key, *a, **k: [
            {"mod_id": "6780", "name": "Resupply Boxes Emissive"},
            {"mod_id": "9999", "name": "Resupply Boxes Emissive Redux"},
        ]
        lib_entry = {"uuid": "u-1", "label": "Resupply Boxes Emissive",
                     "dirname": "Resupply Boxes Emissive_AR1", "path": "p",
                     "nexus_mod_id": "", "options": []}
        eng = E.Engine("dummy.json", api_key="k")
        eng.library = [lib_entry]
        try:
            eng.resolve_sources(only_keys=None)
        finally:
            P.nexus_search = real

        key = E.source_key(lib_entry)
        check("引擎记下了“需用户确认”", key in eng.ambiguous, str(list(eng.ambiguous)))
        rec = eng.ambiguous.get(key) or {}
        check("原因写清了", rec.get("reason") == "有多个候选都说得通", str(rec.get("reason")))
        check("候选都带上了", len(rec.get("candidates") or []) == 2)
        check("★ 没被自动采纳（resolved 为空）", not eng.resolved.get(key),
              str(eng.resolved.get(key)))

        # 用户选了第二个 → 必须**立刻**写进 sources.json
        ok = eng.accept_choice(key, rec["candidates"][1])
        check("采纳成功", ok is True)
        data = E.load_sources()
        check("★ 立刻落盘了（不攒到最后）",
              key in data and data[key] and data[key][0].get("id") == "9999",
              str(data.get(key)))
        check("标成受保护的 manual 记录",
              bool(data[key][0].get("manual")), str(data[key][0]))
        check("采纳后不再算“待确认”", key not in eng.ambiguous)
        check("采纳后 resolved 变成 high",
              eng.resolved.get(key) and eng.resolved[key][0]["confidence"] == "high",
              str(eng.resolved.get(key)))

        # 再跑一次识别：已记住的会直接采纳、不会再问
        eng2 = E.Engine("dummy.json", api_key="k")
        eng2.library = [lib_entry]
        eng2.resolve_sources(only_keys=None)
        check("★ 记住之后不再问用户", key not in eng2.ambiguous,
              str(list(eng2.ambiguous)))
        check("记住之后直接采纳", bool(eng2.resolved.get(key)))

        # 只有一个候选且严格 → 不该进 ambiguous
        P.nexus_search = lambda name, key, *a, **k: [
            {"mod_id": "6780", "name": "Resupply Boxes Emissive"}]
        eng3 = E.Engine("dummy.json", api_key="k")
        eng3.library = [lib_entry]
        try:
            eng3.resolve_sources(only_keys=None)
        finally:
            P.nexus_search = real
        check("唯一严格候选 → 不打扰", not eng3.ambiguous, str(list(eng3.ambiguous)))
        check("唯一严格候选 → 自动采纳", bool(eng3.resolved.get(key)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    for fn in (t_search_terms, t_strip_annotations, t_site_name_score,
               t_ambiguous_reason, t_nexus_search_path,
               t_engine_ambiguous_and_accept):
        fn()
    print()
    print(f"来源选择自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
