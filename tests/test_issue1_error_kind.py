# -*- coding: utf-8 -*-
"""回归自测：「N 网已删除的 mod 被说成『你没填 API key』」（2026-10-12）。

用户原话：「这里有个在N网已经被删除的mod，**之前一直显示的是此Mod已被删除
无法查看之类的，现在直接显示我没有填API了，但实际上我填入了API，
这可能会误导用户**」。

两个根因，本文件各钉一组断言：

  ① `gui.App._error_note` 靠**匹配错误文案的子串**分类，而
     `"被删除" in s` 对不上 provider 的「这个 mod 被**隐藏/删除**…」
     → 那条分支永远不生效 → 落到 `"key" in low` → 显示「需要 API key」。
  ② `engine.evaluate` 的兜底 `"API key" in err` 会把
     「API key 无效或已过期」改写成「你没填 key」（两件事被说成一件）。

修法：失败原因由 provider 打**结构化标签** `err_kind`，下游只认标签、
不再匹配文案；分支顺序按"用户下一步该干什么"排（mod 级先于凭据级）。

跑法：python tests/test_issue1_error_kind.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["HD2U_NO_DIALOG"] = "1"
os.environ["HD2U_READONLY_GUARD"] = "1"

PASS = 0
FAIL = 0
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}".strip())
        print(f"  FAIL: {name} {extra}")


ENTRY = {"uuid": "x", "label": "m", "dirname": "d", "path": "",
         "installed_version": "1.0", "auto_version": "1.0",
         "nexus_version": "", "version_is_manual": False}


def _note_for(kind: str, msg: str) -> str:
    """走一遍"provider 结果 → evaluate → _error_note"，返回表格里那句。"""
    from hd2_updater.engine import evaluate
    from hd2_updater.gui import App
    ev = evaluate(ENTRY, [{"provider": "nexus", "ok": False, "error": msg,
                           "err_kind": kind, "url": ""}])
    best = ev.get("best") or {}
    return App._error_note(best.get("error") or "", best)


# ============================================================ ① 分类正确
def t_gone_is_not_apikey():
    """① ★ 核心：已删除的 mod 不能被说成"需要 API key"。"""
    print("[① 已删除 ≠ 需要 API key]")
    note = _note_for("gone", "这个 mod 在 Nexus 上已经没有了（#17024）")
    check("★ 已删除 → 说的是『没有了/已删除』", "没有了" in note or "删除" in note,
          note)
    check("★ 已删除 → **不再**说『需要 API key』",
          "API key" not in note and "api key" not in note.lower(), note)

    # 用户报的那句原文案（provider 旧文案）走"文案兜底"也必须分类正确
    from hd2_updater.gui import App
    legacy = "这个 mod 被隐藏/删除，或你的 key 无权访问"
    n2 = App._error_note(legacy)          # 不带 best → 走文案兜底
    check("★ 旧文案（不带结构化标签）也能分对：不说 key 的事",
          "API key" not in n2 and ("删除" in n2 or "没有了" in n2), n2)


def t_kind_mapping():
    """各种 err_kind → 给用户的下一步必须各不一样。"""
    print("[err_kind 逐类 → 出路]")
    cases = [
        ("gone", "mod 没了", "删除"),
        ("site", "站点挡住", "挡住"),
        ("net", "网络超时", "连不上"),
        ("rate", "限流", "限流"),
        ("no_key", "没填 key", "填"),
        ("bad_key", "key 无效", "换一个"),
        ("noperm", "没权限看", "看不到"),
        ("shape", "结构变了", "改版"),
    ]
    for kind, desc, must_have in cases:
        note = _note_for(kind, f"（{desc}）")
        check(f"{kind} → 提示里含『{must_have}』", must_have in note, note)
    # ★ 关键区分：no_key 和 bad_key 必须是**两种出路**
    a = _note_for("no_key", "没填 Nexus API key")
    b = _note_for("bad_key", "API key 无效或已过期")
    check("★ 『没填』与『无效』给出不同的出路（一个去填、一个去换）",
          a != b and "填" in a and "换" in b, f"{a!r} vs {b!r}")


def t_order_mod_before_cred():
    """② 分支顺序：文案同时含"删除"和"key"时，必须先判 mod 级。"""
    print("[② 顺序：mod 级问题优先于凭据级]")
    from hd2_updater.gui import App
    mixed = "这个 mod 被隐藏/删除，或你的 key 无权访问"
    note = App._error_note(mixed)
    check("★ 文案同时含『删除』和『key』→ 判为 mod 级（不是 key 问题）",
          "API key" not in note, note)
    # 反向：真的没填 key，仍要提示去填
    note2 = App._error_note("没填 Nexus API key")
    check("真的没填 key → 仍然提示去填", "填" in note2, note2)


# ============================================================ ② engine 改写
def t_engine_rewrite_narrowed():
    """② engine 的兜底改写只对『真的没填』生效，不许吃掉『无效』。"""
    print("[② engine 兜底改写已收窄]")
    from hd2_updater.engine import evaluate

    # 真没填 → 改写成"去设置里填"
    ev = evaluate(ENTRY, [{"provider": "nexus", "ok": False,
                           "err_kind": "no_key", "error": "没填 Nexus API key",
                           "url": ""}])
    got = str((ev.get("best") or {}).get("error") or "")
    check("没填 key → 改写成『去设置里填』", "设置" in got, got)

    # key 无效 → **保留原话**（以前会被误改成"你没填"）
    ev2 = evaluate(ENTRY, [{"provider": "nexus", "ok": False,
                            "err_kind": "bad_key",
                            "error": "API key 无效或已过期", "url": ""}])
    got2 = str((ev2.get("best") or {}).get("error") or "")
    check("★ key 无效 → **不**被改写成『你没填 key』",
          "无效或已过期" in got2 and "没填" not in got2, got2)

    # 已删除 → 完全不该被改写
    ev3 = evaluate(ENTRY, [{"provider": "nexus", "ok": False,
                            "err_kind": "gone",
                            "error": "这个 mod 在 Nexus 上已经没有了（#2）",
                            "url": ""}])
    got3 = str((ev3.get("best") or {}).get("error") or "")
    check("★ 已删除 → 原样保留，不被改写成 key 的事",
          "没有了" in got3 and "key" not in got3.lower(), got3)


# ============================================================ provider 打标签
def t_provider_sets_kind():
    """provider 必须在**产生错误的地方**打好标签（下游才不用猜文案）。"""
    print("[provider 打结构化标签]")
    from hd2_updater import providers as P

    # 没填 key
    out = P.nexus_check("16493", "")
    check("没填 key → err_kind=no_key", out.get("err_kind") == "no_key",
          str(out.get("err_kind")))
    # 非法 id
    out2 = P.nexus_check("abc", "SOMEKEY")
    check("非法 mod id → err_kind=gone", out2.get("err_kind") == "gone",
          str(out2.get("err_kind")))
    # _empty 必须带这个字段（否则下游拿不到）
    e = P._empty("nexus")
    check("_empty() 自带 err_kind 字段", "err_kind" in e)
    check("ERR_KINDS 常量存在且含关键项",
          all(k in P.ERR_KINDS for k in ("gone", "no_key", "bad_key",
                                         "noperm", "site", "net")))
    # HTTP 码 → kind 的映射（★ 关键：403 要读响应体才能分开"删了"和"没权限"）
    check("403 + Mod not available → gone（不是 noperm）",
          P.kind_of_http(403, '{"message":"Mod not available: 2"}') == "gone")
    check("403 + 普通 → noperm",
          P.kind_of_http(403, "Forbidden") == "noperm")
    check("404 → gone", P.kind_of_http(404, '{"message":"No Mod Found: 9"}')
          == "gone")
    check("401 → no_key（对 N 网 API = 没给凭据）",
          P.kind_of_http(401, "") == "no_key")
    check("429 → rate", P.kind_of_http(429, "") == "rate")
    check("503 → net", P.kind_of_http(503, "") == "net")


def t_unknown_ver_clears_kind():
    """「版本存疑」不是查询失败 —— 不能带 err_kind，否则会被当网络错误。"""
    print("[版本存疑不带 err_kind]")
    from hd2_updater.engine import ST_UNKNOWN, evaluate
    ev = evaluate(ENTRY, [{"provider": "nexus", "ok": True, "version": "2026-10-04",
                           "url": "", "download_url": ""}])
    check("版本类型不同 → 判为版本存疑", ev["status"] in (ST_UNKNOWN, "ok"),
          ev["status"])
    best = ev.get("best") or {}
    check("★ 存疑项的 err_kind 必须为空（不是查询失败）",
          not best.get("err_kind"), repr(best.get("err_kind")))


def main() -> int:
    for fn in (t_gone_is_not_apikey,
               t_kind_mapping,
               t_order_mod_before_cred,
               t_engine_rewrite_narrowed,
               t_provider_sets_kind,
               t_unknown_ver_clears_kind):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            check(f"{fn.__name__} 自身抛异常", False,
                  f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}")
    print()
    print(f"错误分类 / 「已删除被说成没填 key」自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())