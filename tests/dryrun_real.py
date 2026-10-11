# -*- coding: utf-8 -*-
"""拿真实 Arsenal 库做只读验证 —— 只生成计划，绝不写游戏目录。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hd2_updater import arsenal, deploy as D  # noqa: E402

data_file, note = arsenal.find_data_file()
print(f"库文件：{data_file}\n（{note}）")
if not data_file:
    sys.exit(1)

lib = arsenal.load_library(data_file)
print(f"模组总数：{len(lib)}")

enabled = [e for e in lib if e.get("enabled")]
in_profile = [e for e in lib if e.get("in_profile")]
print(f"在配置档里：{len(in_profile)}；其中已启用：{len(enabled)}")

# 只部署「Arsenal 里已启用」的 —— 与 Arsenal 自己会部署的集合保持一致
sel = {e["uuid"] for e in enabled}
game_dir = next((e["game_dir"] for e in lib if e.get("game_dir")), "")
print(f"游戏目录：{game_dir}")

print("\n===== 生成部署计划（只读）=====")
plan = D.build_plan(lib, sel, game_dir)
print(f"前缀：{plan.prefix}")
print(f"槽位：{plan.slot_count}")
print(f"体量：{plan.summary()}")

print("\n编号方向校验（默认设置：位次递增 → 编号递增，越靠后越赢）：")
by_mod: dict[str, list[int]] = {}
for it in plan.items:
    by_mod.setdefault(it["mod_label"], []).append(it["index"])
order = sorted(
    [(e["label"], e["deploy_order"]) for e in enabled],
    key=lambda t: t[1],
)
prev_max = None
ok = True
for label, _o in order:
    idxs = by_mod.get(label, [])
    if not idxs:
        print(f"  !! {label}: 计划里没有它")
        ok = False
        continue
    mx = max(idxs)
    desc = f"编号 {sorted(idxs)}"
    if prev_max is not None and mx > prev_max:
        print(f"  !! {label}: 编号 {mx} 大于优先级更高的前一项 {prev_max} —— 顺序反了")
        ok = False
    prev_max = mx
    print(f"  {label[:42]:<44} {desc}")
print(f"\n倒序一致性：{'通过' if ok else '失败'}")

print("\n编号连续性：", "通过" if sorted(i["index"] for i in plan.items) == list(range(plan.slot_count)) else "失败")

print("\n===== 现有 Arsenal 部署产物（对照，不改动）=====")
gd = D.game_data_dir(game_dir)
print(D.describe_state(gd, plan.prefix))
existing = D.scan_existing(gd, plan.prefix)
print(f"现有文件 {len(existing)} 个，本工具将写入 {plan.slot_count * 3} 个")
print(f"（数量不同属正常：Arsenal 会把多个选项合并成更少的二进制 patch）")