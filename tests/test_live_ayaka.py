# -*- coding: utf-8 -*-
"""联网端到端验证：真实重建索引 + 真实爬页面 + 真实匹配中文 mod。

**隔离**：用临时数据目录（HD2U_DATA_DIR），不碰用户的 config/sources。
只读用户的 Arsenal 库（不写）。

跑法：python tests/test_live_ayaka.py
联网、约 3~5 分钟（站点 44 页、每页间隔 3 秒）。退出码 0 = 全绿。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = os.path.join(tempfile.gettempdir(), "hd2u_live_test")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["HD2U_DATA_DIR"] = _TMP

from hd2_updater import arsenal, matcher, providers  # noqa: E402

_fail: list[str] = []
_pass = 0


def check(name, cond, detail=""):
    global _pass
    if cond:
        _pass += 1
        print(f"  ✓ {name}")
    else:
        _fail.append(f"{name}　{detail}")
        print(f"  ✗ {name}　{detail}")


print("【联网】真实重建 ayakamods 索引（44 页）")
items = matcher.rebuild_index(progress=lambda m: None)
print(f"  抓到 {len(items)} 个模组")
enc = [i for i in items if "%" in i["slug"]]
print(f"  其中中文/百分号编码的：{len(enc)} 个")
check("索引条数明显多于老实现的 500（老实现漏掉全部中文 mod）",
      len(items) > 560, f"（{len(items)}）")
check("中文 mod 进索引了（老实现这里是 0）", len(enc) > 50,
      f"（{len(enc)} 个）")
check("索引里的标题都解码过了（没有 %E6%9C 串）",
      all("%" not in i["title"] for i in enc),
      f"（例子 {[i['title'] for i in enc[:3]]}）")

print("\n【联网】真的去爬一个中文 mod 的页面")
sample = next((i for i in enc if "机甲" in i["title"] or "HUD" in i["title"]), enc[0])
print(f"  样本：{sample['title']}（slug {sample['slug'][:40]}…）")
info = providers.ayaka_check(sample["slug"])
check("拿到了版本号", bool(info.get("version")), f"（{info.get('version')}）")
check("拿到了标题（老实现 Title 正则一直匹配不上）",
      bool(info.get("title")), f"（{info.get('title')}）")
check("ok=True", info.get("ok") is True, f"（error={info.get('error')}）")
check("拼出的网址是干净的 ASCII URL（可请求）",
      providers.ayaka_url(sample["slug"]).isascii())
print(f"    版本={info.get('version')}  标题={info.get('title')}  更新={info.get('updated')}")

print("\n【联网】用完整网址（含中文原样）再查一次，应与上面一致")
info2 = providers.ayaka_check(
    "https://ayakamods.com/mods/" + providers.ayaka_decode(sample["slug"]) + "/")
check("原样中文网址也能查到同样的版本",
      info2.get("version") == info.get("version"),
      f"（{info2.get('version')} vs {info.get('version')}）")

print("\n【联网】用真实 Arsenal 库跑一遍来源解析（只读用户的库）")
path, how = arsenal.find_data_file()
print(f"  库：{path}（{how}）")
if path:
    lib = arsenal.load_library(path)
    cn = [e for e in lib if any(ord(c) > 127 for c in (e.get("label") or ""))]
    print(f"  {len(lib)} 个模组，其中名字带中文的 {len(cn)} 个：")
    for e in cn:
        print(f"    · {e['label']}")

    # 只对中文名的那几个跑匹配（省时间，也避开 GitHub 配额）
    matched_cn = 0
    for e in cn:
        cands = matcher.candidates_for(
            e, github_token="", use_github_search=False, ayaka_index=items)
        ay = [c for c in cands if c["provider"] == "ayaka"]
        print(f"    {e['label'][:28]:30s} -> "
              + (f"ayaka {providers.ayaka_decode(ay[0]['id'])[:40]} "
                 f"({ay[0]['confidence']})" if ay else "（未自动命中 ayaka）"))
        if ay and ay[0]["confidence"] == "high":
            matched_cn += 1

    # ★ 这条是本次最要紧的回归：实测踩过「机甲部位血量HUD」被 high 错配到
    #   "VRC Tell CE-07 CM-21"（站点上那条的描述只有一个字母 "e"，
    #   而 similarity 的老实现用裸 `in`，把 "e" 当成 "mech part hud" 的子串
    #   给了 90%）。宁可识别不出来，也**绝不能**认错 —— 认错会让人去更新
    #   一个完全无关的模组。
    mech = next((e for e in lib if "机甲" in (e.get("label") or "")), None)
    if mech:
        cands = matcher.candidates_for(
            mech, github_token="", use_github_search=False, ayaka_index=items)
        ay = [c for c in cands if c["provider"] == "ayaka"]
        bad = [c for c in ay if "vrc-tell" in c["id"]]
        check("没有把「机甲部位血量HUD」错配到 VRC Tell（曾经的 false positive）",
              not bad, f"（错配成了 {[c['id'] for c in bad]}）")
        check("它的 N 网来源仍然识别得对",
              any(c["provider"] == "nexus" and c["confidence"] == "high"
                  for c in cands),
              f"（{[(c['provider'], c['confidence']) for c in cands]}）")

    # 说明：这台机器上的 3 个中文名模组**本来就不在 ayakamods 上**
    # （它们是 N 网专属：16514 / 17024 / 16627），所以"没匹配到 ayaka"
    # 才是正确行为，不是缺陷。真正要证明的是：**确实在站上的中文 mod
    # 现在能被名字认出来**（老实现下索引里根本没有中文条目，永远认不出）。
    print("\n【联网】拿站上真实存在的中文 mod 名字，验证能被认出来")
    cn_items = [i for i in items if "%" in i["slug"] and len(i["title"]) > 4]
    probes = [i for i in cn_items if "机甲" in i["title"]][:1] or cn_items[:1]
    ok_probe = 0
    for it in probes:
        fake = {"uuid": "probe", "label": it["title"], "dirname": it["title"],
                "installed_version": ""}
        cands = matcher.candidates_for(
            fake, github_token="", use_github_search=False, ayaka_index=items)
        ay = [c for c in cands if c["provider"] == "ayaka"]
        hit = ay[0] if ay else None
        print(f"    「{it['title'][:34]}」 -> "
              + (f"{providers.ayaka_decode(hit['id'])[:34]} ({hit['confidence']})"
                 if hit else "（未命中）"))
        if hit and hit["id"] == it["slug"]:
            ok_probe += 1
    check("站上真实存在的中文 mod，用它的中文名能认出来（老实现做不到）",
          ok_probe >= 1, f"（{ok_probe}/{len(probes)}）")
else:
    print("  （这台机器上没找到 Arsenal 库，跳过这一段）")

print()
print("=" * 64)
if _fail:
    print(f"✗ {len(_fail)} 项没过（通过 {_pass} 项）：")
    for f in _fail:
        print("   · " + f)
    sys.exit(1)
print(f"✓ 联网端到端全部通过（{_pass} 项）")
sys.exit(0)
