# -*- coding: utf-8 -*-
"""抓一份真实的 ayakamods 列表页 HTML 存成测试 fixture（只跑一次）。

为什么要存真页面：中文 mod 的百分号编码、卡片的真实 DOM 结构、
`data-author` 的位置都是"实测才知道"的细节。用假 HTML 写的测试
只能证明"我按我以为的结构解析对了"，证明不了站上真的长这样。
"""
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures")
os.makedirs(OUT, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0", "Accept": "text/html",
      "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                 timeout=45).read().decode("utf-8", "replace")


# 列表页：挑一页同时含中文和英文 mod 的
for page in (1, 3):
    url = ("https://ayakamods.com/mods/games/helldivers-2.119/"
           f"?order=last_update&direction=desc&page={page}")
    html = get(url)
    n_cn = len([1 for s in re.findall(r'href="/mods/([^"/?#]+)/"', html) if "%" in s])
    path = os.path.join(OUT, f"ayaka_list_p{page}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"page {page}: {len(html)} bytes, {n_cn} pct-encoded -> {path}")
    time.sleep(3)

# 单个模组页：一个中文名、一个英文名（用于测字段解析）
for slug, name in (("%E6%9C%BA%E7%94%B2%E9%83%A8%E4%BD%8D%E8%A1%80%E9%87%8FHUD.4775", "cn"),
                   ("hd2runtime.4260", "en")):
    html = get(f"https://ayakamods.com/mods/{slug}/")
    path = os.path.join(OUT, f"ayaka_mod_{name}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"mod {name}: {len(html)} bytes -> {path}")
    time.sleep(3)
