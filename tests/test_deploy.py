# -*- coding: utf-8 -*-
"""部署模块的离线自测 —— 用临时目录搭假模组树，不碰真实游戏目录。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hd2_updater import deploy as D  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}{(' -> ' + extra) if extra else ''}")
        print(f"  FAIL: {name} {extra}")


def make_mod(root: str, label: str, n_slots: int, with_companion: bool = True) -> dict:
    """造一个假模组目录：n_slots 个子目录，每目录一个 patch 三件套。"""
    d = os.path.join(root, label)
    os.makedirs(d, exist_ok=True)
    for i in range(n_slots):
        sd = os.path.join(d, "options", f"Opt{i}")
        os.makedirs(sd, exist_ok=True)
        main = os.path.join(sd, "9ba626afa44a3aa3.patch_0")
        with open(main, "wb") as f:
            f.write(f"{label}-{i}".encode() * 4)
        if with_companion:
            for suf in D.COMPANION_SUFFIXES:
                with open(main + suf, "wb") as f:
                    f.write(b"x")
    return {"uuid": f"u-{label}", "label": label, "path": d,
            "enabled": True, "deploy_order": None}


def build(root: str, game: str, lib: list[dict], sel: set, prefix=None,
          assign_order: bool = True) -> D.DeployPlan:
    # 默认按列表顺序补 deploy_order；测试要能故意设坏值验证报错，所以留了开关
    if assign_order:
        for i, e in enumerate(lib):
            if e["deploy_order"] is None:
                e["deploy_order"] = i
    return D.build_plan(lib, sel, game, prefix)


def t_prefix():
    print("[前缀推断]")
    root = tempfile.mkdtemp()
    try:
        gd = os.path.join(root, "data")
        os.makedirs(gd)
        for n in ("9ba626afa44a3aa3.patch_0", "9ba626afa44a3aa3.patch_0.stream"):
            open(os.path.join(gd, n), "wb").close()
        check("单一前缀", D.find_prefix(gd) == "9ba626afa44a3aa3")

        gd2 = os.path.join(root, "d2")
        os.makedirs(gd2)
        for p in ("aabbccddeeff0011", "1122334455667788"):
            open(os.path.join(gd2, f"{p}.patch_0"), "wb").close()
        try:
            D.find_prefix(gd2)
            check("多前缀应报错", False)
        except D.DeployError as e:
            check("多前缀应报错", "多个不同" in str(e))

        gd3 = os.path.join(root, "d3")
        os.makedirs(gd3)
        check("无前缀应报错",
              _raises(lambda: D.find_prefix(gd3), "无法确定模组部署文件的前缀"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _raises(fn, needle: str) -> bool:
    try:
        fn()
        return False
    except D.DeployError as e:
        return needle in str(e)


def t_slots():
    print("[槽位识别]")
    root = tempfile.mkdtemp()
    try:
        e = make_mod(root, "Glow", 3)
        check("三个槽位", len(D.collect_slots(e["path"])) == 3)
        # 同一模组内同名同前缀的文件分处不同目录 —— 必须按目录分组
        d = e["path"]
        os.makedirs(os.path.join(d, "A"), exist_ok=True)
        os.makedirs(os.path.join(d, "B"), exist_ok=True)
        for sub in ("A", "B"):
            with open(os.path.join(d, sub, "9ba626afa44a3aa3.patch_0"), "wb") as f:
                f.write(b"dup")
        check("同名文件按目录区分", len(D.collect_slots(e["path"])) == 5)
        # 伴生文件不得被当成独立槽位
        check("伴生文件不算槽位",
              all(not os.path.basename(p).endswith(D.COMPANION_SUFFIXES)
                  for p in D.collect_slots(e["path"])))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_options():
    print("[选项开关过滤]")
    root = tempfile.mkdtemp()
    try:
        e = make_mod(root, "Opt", 3)
        # 把三个槽位改成有意义的目录名，模拟 Arsenal 的 include 结构
        base = e["path"]
        shutil.rmtree(base)
        for name in ("Core", "Default Mode - Task Light", "Default Mode - Gameplay"):
            d = os.path.join(base, name)
            os.makedirs(d)
            m = os.path.join(d, "9ba626afa44a3aa3.patch_0")
            with open(m, "wb") as f:
                f.write(name.encode())
        e["options"] = [
            {"name": "Core", "include": ["Core"], "enabled": True, "suboptions": []},
            {"name": "Default mode", "include": [], "enabled": True, "suboptions": [
                {"name": "Task Light", "include": ["Default Mode - Task Light"],
                 "enabled": True, "suboptions": []},
                # 单选组里被禁用的那个 —— 绝不能装进游戏
                {"name": "Gameplay", "include": ["Default Mode - Gameplay"],
                 "enabled": False, "suboptions": []},
            ]},
        ]
        # ★ 必须递归进 suboptions：顶层 include 为空，真实目录名在子选项里
        incs = D.option_include_names(e)
        check("递归取到子选项 include",
              set(incs) == {"Core", "Default Mode - Task Light"}, str(incs))
        check("禁用的子选项被排除", "Default Mode - Gameplay" not in incs)
        check("禁用选项不产出槽位（3 槽 → 2 槽）",
              len(D.collect_slots(e["path"], e)) == 2,
              str(D.collect_slots(e["path"], e)))
        check("不过滤时仍是 3 槽", len(D.collect_slots(e["path"])) == 3)

        # include 全为空（老 mod 没有选项声明）→ 不过滤，照原样部署
        e2 = make_mod(root, "NoOpt", 2)
        e2["options"] = []
        check("无选项声明时不误删", len(D.collect_slots(e2["path"], e2)) == 2)

        # 顶层 dict 无 options 键也不能崩
        e3 = make_mod(root, "NoKey", 1)
        e3.pop("options", None)
        check("缺 options 键不崩", len(D.collect_slots(e3["path"], e3)) == 1)

        # include 是字符串（实测两种形态都有）
        e4 = make_mod(root, "StrInc", 1)
        sub = os.path.join(e4["path"], "options", "Opt0")
        e4["options"] = [{"name": "X", "include": "Opt0", "enabled": True,
                          "suboptions": []}]
        check("include 为字符串时可用",
              len(D.collect_slots(e4["path"], e4)) == 1, str(D.collect_slots(e4["path"], e4)))
        check("Opt0 目录存在", os.path.isdir(sub))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_plan():
    print("[计划与编号方向]")
    root = tempfile.mkdtemp()
    try:
        game = os.path.join(root, "game")
        os.makedirs(os.path.join(game, "data"))
        open(os.path.join(game, "data", "9ba626afa44a3aa3.patch_0"), "wb").close()

        lib = [make_mod(root, "A", 1), make_mod(root, "B", 2), make_mod(root, "C", 1)]
        plan = build(root, game, lib, {"u-A", "u-B", "u-C"})
        # Arsenal 默认（setTopPriority=False）：顺序编号 —— 列表位次 0 → 编号 0，
        # 越靠后编号越大、后加载、优先级越高。实测锚定：位次0→patch_0、位次17→patch_44。
        order = [(it["index"], it["mod_label"]) for it in plan.items]
        check("槽位总数 4", plan.slot_count == 4, str(order))
        check("A（列表第0位）拿编号 0", order[0] == (0, "A"), str(order))
        check("B 中间两槽 1,2",
              sorted(i for i, l in order if l == "B") == [1, 2], str(order))
        check("C（列表最后）拿最大编号 3", order[-1] == (3, "C"), str(order))
        check("编号连续无空洞",
              sorted(it["index"] for it in plan.items) == list(range(plan.slot_count)))
        # 稳定：同一输入两次生成的计划必须完全一致
        plan2 = build(root, game, lib, {"u-A", "u-B", "u-C"})
        check("计划可重复",
              [(i["index"], i["mod_label"], i["src_dir"]) for i in plan.items]
              == [(i["index"], i["mod_label"], i["src_dir"]) for i in plan2.items])

        # 开了「第一个模组优先」→ 方向反过来：列表第一项反而拿最大编号（后加载、赢）
        plan3 = D.build_plan(lib, {"u-A", "u-B", "u-C"}, game,
                             first_mod_priority=True)
        idx_of = {}
        for it in plan3.items:
            idx_of.setdefault(it["mod_label"], []).append(it["index"])
        check("first-mod priority 时 A（位次0）拿最大编号",
              max(idx_of["A"]) == plan3.slot_count - 1, str(idx_of))
        check("first-mod priority 时 C（末位）拿 0", min(idx_of["C"]) == 0, str(idx_of))
        check("反转后 A 的编号大于 C",
              min(idx_of["A"]) > max(idx_of["C"]), str(idx_of))
        check("反转后编号仍连续",
              sorted(it["index"] for it in plan3.items) == list(range(plan3.slot_count)))
        # 默认方向下 A 的编号应小于 C（与上面相反），确保两种模式真的有区别
        d_idx = {}
        for it in plan.items:
            d_idx.setdefault(it["mod_label"], []).append(it["index"])
        check("默认方向 A 编号小于 C", max(d_idx["A"]) < min(d_idx["C"]), str(d_idx))
        # 同一模组内多槽位编号必须各不相同（不能共用）
        check("同模组多槽位编号互不相同",
              len({it["index"] for it in plan.items}) == plan.slot_count)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_plan_errors():
    print("[计划期错误]")
    root = tempfile.mkdtemp()
    try:
        game = os.path.join(root, "game")
        os.makedirs(os.path.join(game, "data"))
        open(os.path.join(game, "data", "9ba626afa44a3aa3.patch_0"), "wb").close()
        lib = [make_mod(root, "A", 1), make_mod(root, "B", 1)]

        lib[0]["enabled"] = False
        check("未启用应报错", _raises(lambda: build(root, game, lib, {"u-A"}), "未启用"))
        lib[0]["enabled"] = True

        lib[1]["deploy_order"] = None
        check("不在配置档应报错",
              _raises(lambda: build(root, game, lib, {"u-B"}, assign_order=False),
                      "不在 Arsenal 当前配置档"))
        lib[1]["deploy_order"] = 1

        check("未选中应报错", _raises(lambda: build(root, game, lib, set()), "没有选中任何模组"))
        # 重复顺序号
        lib[0]["deploy_order"] = 5
        lib[1]["deploy_order"] = 5
        check("重复顺序号应报错",
              _raises(lambda: build(root, game, lib, {"u-A", "u-B"}, assign_order=False),
                      "重复"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_deploy():
    print("[执行部署]")
    root = tempfile.mkdtemp()
    try:
        game = os.path.join(root, "game")
        gd = os.path.join(game, "data")
        os.makedirs(gd)
        open(os.path.join(gd, "9ba626afa44a3aa3.patch_0"), "wb").close()
        # 预置一个"别人的"文件，应被备份后清掉
        stale = os.path.join(gd, "9ba626afa44a3aa3.patch_9")
        with open(stale, "wb") as f:
            f.write(b"OLDOLDOLD")

        lib = [make_mod(root, "A", 1), make_mod(root, "B", 1)]
        plan = build(root, game, lib, {"u-A", "u-B"})
        res = D.deploy(plan, do_backup=True)

        check("删除旧文件数", res["removed"] == 2, str(res["removed"]))
        check("写入槽位数", res["written"] == 2, str(res["written"]))
        check("清单已写", os.path.isfile(res["manifest"]))
        check("备份已建", res["backup"] and os.path.isdir(res["backup"]))
        check("备份含旧文件", os.path.isfile(os.path.join(res["backup"], "9ba626afa44a3aa3.patch_9")))

        # 关键：内容必须逐字节一致，且编号与列表位次同向
        a_idx = next(i["index"] for i in plan.items if i["mod_label"] == "A")
        b_idx = next(i["index"] for i in plan.items if i["mod_label"] == "B")
        check("A 编号 < B 编号（位次在前=先加载）", a_idx < b_idx, f"A={a_idx} B={b_idx}")
        with open(os.path.join(gd, f"9ba626afa44a3aa3.patch_{a_idx}"), "rb") as f:
            ca = f.read()
        with open(next(v for i in plan.items if i["mod_label"] == "A" for v in [i["files"][""]]), "rb") as f:
            sa = f.read()
        check("A 内容逐字节一致", ca == sa)
        with open(os.path.join(gd, f"9ba626afa44a3aa3.patch_{b_idx}"), "rb") as f:
            cb = f.read()
        check("B 内容逐字节一致", cb == b"B-0" * 4, repr(cb))

        # 清单内容
        m = D.last_manifest(gd)
        check("清单含前缀", m and m["prefix"] == "9ba626afa44a3aa3")
        check("清单记录槽位", m and len(m["mods"]) == 2)
        check("清单记录备份位置", m and m.get("backupDir"))

        # 不属于该前缀的文件必须原样保留
        keep = os.path.join(gd, "02582f3da1f8daf5")
        with open(keep, "wb") as f:
            f.write(b"GAME")
        r2 = D.deploy(plan, do_backup=False)
        check("二次部署正常", r2["written"] == 2)
        check("游戏原生文件未被动", os.path.isfile(keep))
        check("描述可用", "本工具" in D.describe_state(gd, "9ba626afa44a3aa3"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_no_backup():
    print("[无备份部署]")
    root = tempfile.mkdtemp()
    try:
        game = os.path.join(root, "game")
        gd = os.path.join(game, "data")
        os.makedirs(gd)  # 空的 data 目录 —— 前缀须从模组目录推断，这正是原缺陷场景
        lib = [make_mod(root, "A", 1)]
        plan = build(root, game, lib, {"u-A"})
        check("空目录也能推断前缀", plan.prefix == "9ba626afa44a3aa3", plan.prefix)
        r = D.deploy(plan, do_backup=False)
        check("无备份时不建目录", not os.path.isdir(D.backup_dir(gd)))
        check("仍完成部署", r["written"] == 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_idempotent():
    print("[重复部署]")
    root = tempfile.mkdtemp()
    try:
        game = os.path.join(root, "game")
        gd = os.path.join(game, "data")
        os.makedirs(gd)
        lib = [make_mod(root, "A", 2), make_mod(root, "B", 1)]
        for _ in range(3):
            plan = build(root, game, lib, {"u-A", "u-B"})
            D.deploy(plan, do_backup=False)
        check("反复部署后槽位数稳定",
              len(D.scan_existing(gd, "9ba626afa44a3aa3")) == 9,  # 3 槽 × 3 件套
              str(len(D.scan_existing(gd, "9ba626afa44a3aa3"))))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_empty_manifest():
    print("[清单缺失/损坏]")
    root = tempfile.mkdtemp()
    try:
        gd = root
        check("无清单返回 None", D.last_manifest(gd) is None)
        with open(os.path.join(gd, D.MANIFEST_NAME), "w", encoding="utf-8") as f:
            f.write("{not json")
        check("损坏清单返回 None", D.last_manifest(gd) is None)
        check("损坏时仍能描述", isinstance(D.describe_state(gd, "9ba626afa44a3aa3"), str))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    for fn in (t_prefix, t_slots, t_options, t_plan, t_plan_errors, t_deploy,
               t_no_backup, t_idempotent, t_empty_manifest):
        fn()
    print(f"\n部署自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())