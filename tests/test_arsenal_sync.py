# -*- coding: utf-8 -*-
"""arsenal_sync 的自测：镜像接管、冲突检测、安全写回。全部用临时文件。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 护栏：本测试只允许写临时目录（防止误写真实 Arsenal 库）
os.environ["HD2U_READONLY_GUARD"] = "1"

from hd2_updater import arsenal_sync as S  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {extra}".strip())
        print(f"  FAIL: {name} {extra}")


def make_lib(root: str) -> str:
    """造一份最小可用的 Arsenal 库。"""
    p = os.path.join(root, "hd2a_data.json")
    data = {
        "selectedProfile": "default",
        "deployedProfile": "default",
        "modsList": {"default": {"mods": [
            {"uuid": "a", "label": "甲", "enabled": True, "deployed": True,
             "changed": False, "path": "x"},
            {"uuid": "b", "label": "乙", "enabled": True, "deployed": True,
             "changed": True, "path": "y"},
            {"uuid": "c", "label": "丙", "enabled": True, "deployed": False,
             "changed": False, "path": "z"},
            {"uuid": "d", "label": "丁", "enabled": False, "deployed": False,
             "changed": False, "path": "w"},
        ]}},
        "modsLibrary": [],
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def read(p: str) -> dict:
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def t_mirror():
    print("[镜像接管：读出已部署的]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib(root)
        check("读出已部署 uuid", S.deployed_uuids(p) == ["a", "b"],
              str(S.deployed_uuids(p)))
        check("未部署的不算", "c" not in S.deployed_uuids(p))
        check("禁用的不算", "d" not in S.deployed_uuids(p))
        labels = S.deployed_labels(p)
        check("标签映射正确", labels == {"a": "甲", "b": "乙"}, str(labels))
        # 保持 Arsenal 的加载顺序（数组顺序）
        check("顺序与库一致", S.deployed_uuids(p) == ["a", "b"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_mirror_robust():
    print("[镜像接管：坏数据的容错]")
    root = tempfile.mkdtemp()
    try:
        p = os.path.join(root, "hd2a_data.json")
        cases = [
            ("空对象", {}),
            ("modsList 不是 dict", {"modsList": []}),
            ("mods 不是 list", {"modsList": {"default": {"mods": "x"}}}),
            ("配置档不存在", {"modsList": {"other": {"mods": []}},
                              "selectedProfile": "missing"}),
        ]
        for name, data in cases:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f)
            try:
                got = S.deployed_uuids(p)
                check(f"{name} 不崩且返回空", got == [], str(got))
            except Exception as e:  # noqa: BLE001
                check(f"{name} 不崩且返回空", False, str(e))
        # 文件损坏
        with open(p, "w", encoding="utf-8") as f:
            f.write("{坏 JSON")
        try:
            S.deployed_uuids(p)
            check("损坏文件应报可读错误", False)
        except S.SyncError as e:
            check("损坏文件应报可读错误", "读取" in str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_writeback():
    print("[写回：只改 deployed/changed]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib(root)
        before = read(p)
        # 实际只部署了 a 和 c
        res = S.sync_deployed_state(p, {"a", "c"}, force=True)
        after = read(p)
        check("有备份产生", os.path.isfile(res["backup"]))
        check("备份内容=改动前", read(res["backup"]) == before)
        mods = {m["uuid"]: m for m in after["modsList"]["default"]["mods"]}
        check("a 仍标记已部署", mods["a"]["deployed"] is True)
        check("b 改为未部署", mods["b"]["deployed"] is False)
        check("c 改为已部署", mods["c"]["deployed"] is True)
        check("d 仍未部署", mods["d"]["deployed"] is False)
        check("changed 被清掉", all(not m["changed"] for m in mods.values()))
        check("变更计数正确", res["changed"] == 2, str(res["changed"]))
        # 关键：enabled / 顺序 / 其它字段一律不能动
        check("enabled 未被改动",
              [m["enabled"] for m in after["modsList"]["default"]["mods"]]
              == [m["enabled"] for m in before["modsList"]["default"]["mods"]])
        check("数组顺序未变",
              [m["uuid"] for m in after["modsList"]["default"]["mods"]]
              == [m["uuid"] for m in before["modsList"]["default"]["mods"]])
        check("其它键未丢", set(after.keys()) >= set(before.keys()))
        check("modsLibrary 未被动", after["modsLibrary"] == before["modsLibrary"])
        check("deployedProfile 写入", after.get("deployedProfile") == "default")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_running_guard():
    print("[冲突检测：Arsenal 运行时禁止写回]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib(root)
        before = read(p)
        orig = S.arsenal_running
        S.arsenal_running = lambda: True
        try:
            try:
                S.sync_deployed_state(p, {"a"})
                check("运行时应拒绝写回", False)
            except S.SyncError as e:
                check("运行时应拒绝写回", "正在运行" in str(e))
            check("拒绝时文件未被改动", read(p) == before)
            # force 应能绕过（仅供测试）
            S.sync_deployed_state(p, {"a"}, force=True)
            check("force 可绕过检查", read(p)["modsList"]["default"]["mods"][1]["deployed"] is False)
        finally:
            S.arsenal_running = orig
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_snapshot():
    print("[写回：部署快照同步]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib(root)
        files = [{"name": "9ba626afa44a3aa3.patch_0", "size": 100, "modified": 1},
                 {"name": "9ba626afa44a3aa3.patch_0.stream", "size": 0, "modified": 1}]
        res = S.sync_deployed_state(p, {"a", "c"}, live_files=files, force=True)
        check("快照已写", res["snapshot"] is True)
        snap = read(os.path.join(root, S.SNAPSHOT_NAME))
        check("快照含 timestamp", isinstance(snap["timestamp"], int))
        check("快照 deployedMods 与部署集合一致",
              sorted(m["uuid"] for m in snap["deployedMods"]) == ["a", "c"],
              str(snap["deployedMods"]))
        check("快照 deployedFiles 完整", len(snap["deployedFiles"]) == 2)
        # 不传 live_files 时应跳过快照而不是报错
        res2 = S.sync_deployed_state(p, {"a"}, force=True)
        check("不传文件清单时跳过快照", res2["snapshot"] is False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_atomic():
    print("[原子写：不留半个文件]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib(root)
        S.sync_deployed_state(p, {"a"}, force=True)
        # 目录里不应残留临时文件
        leftovers = [f for f in os.listdir(root) if "hd2u-tmp" in f]
        check("无残留临时文件", leftovers == [], str(leftovers))
        read(p)  # 能正常解析即说明没写坏
        check("写回后仍是合法 JSON", True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_live_files():
    print("[扫描游戏目录生成文件清单]")
    root = tempfile.mkdtemp()
    try:
        d = os.path.join(root, "data")
        os.makedirs(d)
        for n in ("9ba626afa44a3aa3.patch_0", "9ba626afa44a3aa3.patch_0.stream",
                  "9ba626afa44a3aa3.patch_1", "bundles.nxa", "other.patch_0"):
            with open(os.path.join(d, n), "wb") as f:
                f.write(b"x")
        got = S.live_file_entries(d, "9ba626afa44a3aa3")
        names = [e["name"] for e in got]
        check("只收该前缀的 patch 文件", len(got) == 3, str(names))
        check("不含游戏原生文件", "bundles.nxa" not in names)
        check("不含别的前缀", "other.patch_0" not in names)
        check("条目含 size/modified",
              all(isinstance(e["size"], int) and isinstance(e["modified"], int)
                  for e in got))
        check("目录不存在时返回空", S.live_file_entries(os.path.join(root, "nope"), "x") == [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_desc():
    print("[结果描述]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib(root)
        r = S.sync_deployed_state(p, {"a"}, force=True)
        s = S.describe_sync(r)
        check("描述可读", "Arsenal" in s and "备份" in s, s)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def make_lib_with_options(root: str) -> str:
    """带选项树与加载顺序的库，供选项/顺序测试用。"""
    p = os.path.join(root, "hd2a_data.json")
    data = {
        "selectedProfile": "default",
        "setTopPriority": False,
        "modsList": {"default": {"mods": [
            {"uuid": "a", "label": "甲", "enabled": True, "deployed": True,
             "changed": False, "path": "x"},
            {"uuid": "b", "label": "乙", "enabled": True, "deployed": True,
             "changed": False, "path": "y"},
            {"uuid": "c", "label": "丙", "enabled": True, "deployed": False,
             "changed": False, "path": "z"},
        ]}},
        "modsLibrary": [
            {"uuid": "a", "label": "甲", "options": [
                {"name": "Core", "include": ["Core"], "enabled": True,
                 "suboptions": []},
                {"name": "Pack", "include": [], "enabled": True, "suboptions": [
                    {"name": "Rows", "include": ["Rows"], "enabled": True,
                     "suboptions": []},
                    {"name": "Vanilla", "include": ["Vanilla"], "enabled": False,
                     "suboptions": []},
                ]},
            ]},
            {"uuid": "b", "label": "乙", "options": []},
        ],
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def t_read_options():
    print("[选项：读取]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib_with_options(root)
        opts = S.read_options(p, "a")
        check("顶层选项数", len(opts) == 2, str([o["name"] for o in opts]))
        check("名字正确", [o["name"] for o in opts] == ["Core", "Pack"])
        check("子选项被带出", len(opts[1]["suboptions"]) == 2,
              str(opts[1]["suboptions"]))
        check("子选项状态正确",
              [s["enabled"] for s in opts[1]["suboptions"]] == [True, False])
        check("没有选项的模组返回空", S.read_options(p, "b") == [])
        check("不存在的模组返回空", S.read_options(p, "nope") == [])
        check("摘要：含关闭项", "已关闭" in S.option_summary(p, "a"),
              S.option_summary(p, "a"))
        check("摘要：无选项", S.option_summary(p, "b") == "没有可选项")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_set_options():
    print("[选项：写回]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib_with_options(root)
        before = read(p)
        # 打开 Vanilla（子选项，键用 组名/子选项名），关掉 Core
        res = S.set_options(p, "a", {"Pack/Vanilla": True, "Core": False},
                            force=True)
        check("应用了 2 项", res["applied"] == 2, str(res))
        check("产生了备份", os.path.isfile(res["backup"]))
        after = read(p)
        opts = after["modsLibrary"][0]["options"]
        check("Core 已关闭", opts[0]["enabled"] is False, str(opts[0]))
        check("Vanilla 已打开", opts[1]["suboptions"][1]["enabled"] is True)
        check("Rows 未被动", opts[1]["suboptions"][0]["enabled"] is True)
        check("Pack 组本身未被动", opts[1]["enabled"] is True)
        # 其它模组、其它字段不能动
        check("其它模组未动",
              after["modsLibrary"][1] == before["modsLibrary"][1])
        check("modsList 顺序未动",
              [m["uuid"] for m in after["modsList"]["default"]["mods"]]
              == [m["uuid"] for m in before["modsList"]["default"]["mods"]])
        # 选项变了 → 该模组应标记 changed，提示需要重新部署
        ch = {m["uuid"]: m["changed"] for m in after["modsList"]["default"]["mods"]}
        check("改过选项的模组标为 changed", ch["a"] is True, str(ch))
        check("没改的模组不标 changed", ch["b"] is False, str(ch))

        # 值没变时不产生写入
        r2 = S.set_options(p, "a", {"Core": False}, force=True)
        check("无变化时 applied=0", r2["applied"] == 0, str(r2))
        check("无变化不写文件", r2["changed_file"] is False)

        # 不存在的选项名要报出来
        r3 = S.set_options(p, "a", {"Nope": True, "Core": True}, force=True)
        check("未知选项被报告", r3["missing"] == ["Nope"], str(r3["missing"]))
        check("已知选项仍生效", r3["applied"] == 1, str(r3))

        # 找不到模组要报错且不改文件
        snap = read(p)
        try:
            S.set_options(p, "nope", {"x": True}, force=True)
            check("不存在的模组应报错", False)
        except S.SyncError as e:
            check("不存在的模组应报错", "找不到" in str(e))
        check("报错时文件未改", read(p) == snap)
        # 空 map 要报错
        try:
            S.set_options(p, "a", {}, force=True)
            check("空选项表应报错", False)
        except S.SyncError as e:
            check("空选项表应报错", "没有要修改" in str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_set_options_running_guard():
    print("[选项：Arsenal 运行时禁止写回]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib_with_options(root)
        before = read(p)
        orig = S.arsenal_running
        S.arsenal_running = lambda: True
        try:
            try:
                S.set_options(p, "a", {"Core": False})
                check("运行时应拒绝", False)
            except S.SyncError as e:
                check("运行时应拒绝", "正在运行" in str(e))
            check("拒绝时文件未改", read(p) == before)
        finally:
            S.arsenal_running = orig
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_order():
    print("[加载顺序]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib_with_options(root)
        order = S.read_order(p)
        check("读出 3 个", len(order) == 3, str(len(order)))
        check("顺序正确", [o["uuid"] for o in order] == ["a", "b", "c"],
              str(order))
        check("带 enabled/deployed 标志",
              order[0]["enabled"] is True and order[0]["deployed"] is True)

        # 把 c 排到最后（默认设置下 = 优先级最高）
        res = S.set_order(p, ["a", "b", "c"], force=True)
        check("顺序写回成功", res["count"] == 3, str(res))
        check("产生备份", os.path.isfile(res["backup"]))
        check("顺序保持", [o["uuid"] for o in S.read_order(p)] == ["a", "b", "c"])

        # 真正重排：c 提到 a 前面
        S.set_order(p, ["c", "b", "a"], force=True)
        check("重排生效", [o["uuid"] for o in S.read_order(p)] == ["c", "b", "a"],
              str([o["uuid"] for o in S.read_order(p)]))

        # 元素内容不能写丢
        data = read(p)
        m = data["modsList"]["default"]["mods"][-1]
        check("元素内容完整", m["uuid"] == "a" and m["label"] == "甲"
              and m["deployed"] is True, str(m))
        check("元素总数未变", len(data["modsList"]["default"]["mods"]) == 3)

        # 只提到一个 uuid：其余必须原地不动，且这一个也留在自己的槽位
        # （早期实现会把"未提到的"整体挪到前面，那是会改用户数据的错误语义）
        before_order = [o["uuid"] for o in S.read_order(p)]
        S.set_order(p, ["b"], force=True)
        got = [o["uuid"] for o in S.read_order(p)]
        check("未提到的模组仍存在", set(got) == {"a", "b", "c"}, str(got))
        check("只提一个时顺序完全不变", got == before_order,
              f"{before_order} -> {got}")

        # 全部 uuid 都不认识 → 报错且不改
        snapshot = read(p)
        try:
            S.set_order(p, ["zzz"], force=True)
            check("未知 uuid 应报错", False)
        except S.SyncError as e:
            check("未知 uuid 应报错", "没有一个" in str(e))
        check("报错时文件未改", read(p) == snapshot)

        # 空列表 → 报错
        try:
            S.set_order(p, [], force=True)
            check("空顺序应报错", False)
        except S.SyncError as e:
            check("空顺序应报错", "没有新的顺序" in str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_order_running_guard():
    print("[加载顺序：Arsenal 运行时禁止写]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib_with_options(root)
        before = read(p)
        orig = S.arsenal_running
        S.arsenal_running = lambda: True
        try:
            try:
                S.set_order(p, ["c", "b", "a"])
                check("运行时应拒绝改顺序", False)
            except S.SyncError as e:
                check("运行时应拒绝改顺序", "正在运行" in str(e))
            check("拒绝时顺序未改", read(p) == before)
        finally:
            S.arsenal_running = orig
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_set_enabled():
    print("[启用开关]")
    root = tempfile.mkdtemp()
    try:
        p = make_lib_with_options(root)
        # c 原本 enabled=True → 关掉
        res = S.set_enabled(p, "c", False, force=True)
        check("关闭生效", res["changed"] is True, str(res))
        check("产生备份", os.path.isfile(res["backup"]))
        st = {m["uuid"]: m for m in read(p)["modsList"]["default"]["mods"]}
        check("c 已停用", st["c"]["enabled"] is False, str(st["c"]))
        check("其它模组未动",
              st["a"]["enabled"] is True and st["b"]["enabled"] is True)
        check("顺序未动",
              [m["uuid"] for m in read(p)["modsList"]["default"]["mods"]]
              == ["a", "b", "c"])
        # 重新启用 → 应标记 changed（提示需要重新部署）
        res2 = S.set_enabled(p, "c", True, force=True)
        check("启用生效", res2["changed"] is True)
        st2 = {m["uuid"]: m for m in read(p)["modsList"]["default"]["mods"]}
        check("c 已启用", st2["c"]["enabled"] is True)
        check("启用后标记 changed", st2["c"]["changed"] is True, str(st2["c"]))
        # 值没变 → 不写
        r3 = S.set_enabled(p, "c", True, force=True)
        check("无变化时不写", r3["changed"] is False)
        # 不在配置档的模组 → 报错
        try:
            S.set_enabled(p, "zzz", True, force=True)
            check("未知模组应报错", False)
        except S.SyncError as e:
            check("未知模组应报错", "不在 Arsenal 当前配置档" in str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_running_cache():
    print("[冲突检测：结果缓存（避免每次操作卡 360ms）]")
    calls = {"n": 0}
    orig = S._probe_arsenal

    def counting():
        calls["n"] += 1
        return False
    S._probe_arsenal = counting
    try:
        # 清空缓存，模拟"用户刚做了一次操作"
        S._RUNNING_CACHE["v"] = None
        S._RUNNING_CACHE["t"] = 0.0
        before = calls["n"]
        # 连续问 5 次（同一次用户动作里就是这样）应只探测一次
        for _ in range(5):
            S.arsenal_running()
        check("连续查询只探测一次", calls["n"] - before == 1,
              f"探测了 {calls['n'] - before} 次")
        # max_age=0 必须强制重探
        before = calls["n"]
        S.arsenal_running(max_age=0)
        check("max_age=0 强制重探", calls["n"] - before == 1)
        # 探测异常时向上抛（由上层兜住），不静默当成"未运行"
        def boom():
            raise RuntimeError("no tasklist")
        S._probe_arsenal = boom
        S._RUNNING_CACHE["v"] = None
        try:
            S.arsenal_running(max_age=0)
            check("探测异常向上抛，不静默当成 False", False)
        except RuntimeError:
            check("探测异常向上抛，不静默当成 False", True)
    finally:
        S._probe_arsenal = orig
        S._RUNNING_CACHE["v"] = None
        S._RUNNING_CACHE["t"] = 0.0


def t_probe_paths():
    print("[冲突检测：快慢两条探测路径]")
    if not sys.platform.startswith("win"):
        print("  （非 Windows，跳过）")
        return
    snap = S._probe_by_snapshot()
    task = S._probe_by_tasklist()
    check("快路径有结果（None 表示失败）", snap is not None, str(snap))
    check("快慢两条路径结论一致", snap == task, f"snapshot={snap} tasklist={task}")

    # 快路径必须真的能检出"正在运行"的进程 —— 拿 python 自身当样本
    orig = S.ARSENAL_PROCS
    S.ARSENAL_PROCS = (Path(sys.executable).stem.lower(),)
    try:
        found = S._probe_by_snapshot()
        check("能检出确实在运行的进程（用 python 自身验证）", found is True,
              f"结果={found}")
    finally:
        S.ARSENAL_PROCS = orig

    # 快路径要明显快于起子进程（否则等于没优化）
    t0 = time.perf_counter()
    for _ in range(5):
        S._probe_by_snapshot()
    fast = (time.perf_counter() - t0) / 5 * 1000
    check("快路径单次 < 60ms", fast < 60, f"{fast:.1f}ms")

    # 构造"快路径失败"的场景 → 应自动退回 tasklist，而不是当成没运行
    orig_snap = S._probe_by_snapshot
    S._probe_by_snapshot = lambda: None
    try:
        r = S._probe_arsenal()
        check("快路径失败时退回慢路径（仍给出结论）", r == task, str(r))
    finally:
        S._probe_by_snapshot = orig_snap


def t_order_partial_keeps_others():
    print("[加载顺序：只提到部分模组时，其余必须原地不动]")
    root = tempfile.mkdtemp()
    try:
        # 5 个模组：a b c d e，只把 b 和 d 互换
        p = os.path.join(root, "hd2a_data.json")
        lib = [{"uuid": u, "label": u, "enabled": True, "deployed": True,
                "changed": False, "path": "x", "options": []}
               for u in "abcde"]
        json.dump({"selectedProfile": "default", "setTopPriority": False,
                   "modsList": {"default": {"mods": lib}}, "modsLibrary": lib},
                  open(p, "w", encoding="utf-8"), ensure_ascii=False)
        S.set_order(p, ["d", "b"], force=True)
        got = [m["uuid"] for m in read(p)["modsList"]["default"]["mods"]]
        # b 在 1、d 在 3 → 交换后 a d c b e
        check("只置换被提到的槽位", got == ["a", "d", "c", "b", "e"], str(got))
        check("未提到的模组位置未变",
              got[0] == "a" and got[2] == "c" and got[4] == "e", str(got))

        # ★ 关键回归：未提到的模组**不能**被挪到最前面。
        #   早期实现是"未提到的排前面 + 被提到的排后面"，曾把用户库里
        #   两个未启用的模组挪到了列表最前（靠备份才发现）。
        p2 = os.path.join(root, "hd2a_data2.json")
        lib2 = [{"uuid": u, "label": u, "enabled": (u not in "bd"),
                 "deployed": True, "changed": False, "path": "x", "options": []}
                for u in "abcde"]
        json.dump({"selectedProfile": "default", "setTopPriority": False,
                   "modsList": {"default": {"mods": lib2}}, "modsLibrary": lib2},
                  open(p2, "w", encoding="utf-8"), ensure_ascii=False)
        S.set_order(p2, ["c", "a"], force=True)     # 只提到已启用的 a、c
        g2 = [m["uuid"] for m in read(p2)["modsList"]["default"]["mods"]]
        check("未提到的未启用模组没有被挪到最前",
              g2[0] != "b" and g2[0] != "d", str(g2))
        check("未提到的留在原位（b 仍在 1、d 仍在 3）",
              g2[1] == "b" and g2[3] == "d", str(g2))
        check("被提到的按给定顺序置换",
              g2[0] == "c" and g2[2] == "a", str(g2))
        check("内容无丢无重", sorted(g2) == list("abcde"), str(g2))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_write_guard():
    print("[护栏：测试模式下禁止写真实路径]")
    root = tempfile.mkdtemp()
    try:
        import tempfile as _tf
        # 临时目录内允许
        p_ok = os.path.join(root, "hd2a_data.json")
        json.dump({}, open(p_ok, "w", encoding="utf-8"))
        try:
            S._atomic_write_json(p_ok, {"x": 1})
            check("临时目录内允许写", True)
        except S.SyncError:
            check("临时目录内允许写", False, "被误拦")
        # 临时目录外拒绝
        outside = os.path.join(os.path.dirname(_tf.gettempdir()),
                               "hd2u_guard_probe.json")
        try:
            S._atomic_write_json(outside, {"x": 1})
            check("临时目录外被拦住", False, "居然写成功了")
            if os.path.exists(outside):
                os.remove(outside)
        except S.SyncError as e:
            check("临时目录外被拦住", "护栏" in str(e), str(e))
        check("被拦时没有产生文件", not os.path.exists(outside))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_add_to_profile():
    print("[加入配置档]")
    root = tempfile.mkdtemp()
    try:
        import tempfile as _tf
        p = os.path.join(root, "hd2a_data.json")
        # 库 3 个，配置档只放 a（b 在库里但不在配置档）
        lib = [{"uuid": u, "label": u.upper(), "path": f"/{u}", "tags": ["misc"],
                "nexusData": {"modId": "1"}, "options": [{"name": "O"}],
                "contentHash": "h", "addedAt": "2026-01-01", "previousVersions": [],
                "patchFileNames": [], "description": "d", "iconPath": None}
               for u in ("a", "b", "c")]
        prof = [dict(lib[0], enabled=True, deployed=True, changed=False, new=False,
                     copiedFiles=[], renamedFiles=[])]
        json.dump({"selectedProfile": "default", "setTopPriority": False,
                   "modsList": {"default": {"mods": prof}}, "modsLibrary": lib},
                  open(p, "w", encoding="utf-8"), ensure_ascii=False)

        before_lib = read(p)["modsLibrary"]
        res = S.add_to_profile(p, "b", force=True)
        check("加入成功", res["added"] is True, str(res))
        check("产生备份", os.path.isfile(res["backup"]))
        after = read(p)
        mods = after["modsList"]["default"]["mods"]
        check("配置档多了一个", len(mods) == 2, str(len(mods)))
        check("新条目在末尾", [m["uuid"] for m in mods] == ["a", "b"],
              str([m["uuid"] for m in mods]))
        check("库未被改动", after["modsLibrary"] == before_lib)
        nb = mods[1]
        check("enabled 按参数", nb["enabled"] is True)
        check("deployed 默认 False（还没部署）", nb["deployed"] is False)
        check("changed 默认 False", nb["changed"] is False)
        check("补齐了那 6 个字段",
              {"enabled", "deployed", "changed", "new", "copiedFiles",
               "renamedFiles"} <= set(nb), str(sorted(nb)))
        # ★ 不能丢库里的任何字段（实测配置档条目 = 库条目 + 6 个字段）
        check("库条目的字段一个不少",
              not (set(lib[1]) - set(nb)), str(set(lib[1]) - set(nb)))
        check("库字段的值原样带过来",
              nb["path"] == lib[1]["path"] and nb["contentHash"] == "h"
              and nb["nexusData"] == {"modId": "1"}, str(nb))
        check("键顺序与既有条目一致",
              list(nb.keys()) == list(mods[0].keys()),
              f"{list(nb.keys())} vs {list(mods[0].keys())}")

        # 重复加入：应幂等、不重复添加
        r2 = S.add_to_profile(p, "b", force=True)
        check("重复加入不重复添加", r2["added"] is False, str(r2))
        check("配置档仍是 2 个",
              len(read(p)["modsList"]["default"]["mods"]) == 2)

        # enabled=False 参数
        S.add_to_profile(p, "c", enabled=False, force=True)
        mc = {m["uuid"]: m for m in read(p)["modsList"]["default"]["mods"]}
        check("enabled=False 生效", mc["c"]["enabled"] is False)

        # 库里没有的 uuid → 报错且不改
        snap = read(p)
        try:
            S.add_to_profile(p, "zzz", force=True)
            check("未知 uuid 应报错", False)
        except S.SyncError as e:
            check("未知 uuid 应报错", "模组库里没有" in str(e))
        check("报错时文件未改", read(p) == snap)

        # 运行时拒绝
        orig = S.arsenal_running
        S.arsenal_running = lambda: True
        try:
            try:
                S.add_to_profile(p, "b")
                check("运行时应拒绝", False)
            except S.SyncError as e:
                check("运行时应拒绝", "正在运行" in str(e))
        finally:
            S.arsenal_running = orig
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_remove_library_mod():
    print("[删除模组：库 + 所有配置档 + 可选删文件]")
    root = tempfile.mkdtemp()
    try:
        mods = os.path.join(root, "mods")
        os.makedirs(mods, exist_ok=True)

        def mk(u):
            d = os.path.join(mods, u)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "9ba626afa44a3aa3.patch_0"), "wb") as f:
                f.write(b"x")
            return d

        lib = [{"uuid": u, "path": mk(u), "label": "模组" + u, "description": "",
                "iconPath": None, "tags": ["misc"], "patchFileNames": [],
                "nexusData": None, "options": [], "contentHash": "h",
                "addedAt": "t", "previousVersions": []} for u in ("a", "b", "c")]

        def pe(m, en=True, dep=True):
            e = dict(m)
            e.update(enabled=en, deployed=dep, changed=False, new=False,
                     copiedFiles=[], renamedFiles=[])
            return e

        p = os.path.join(root, "hd2a_data.json")
        json.dump({"selectedProfile": "default", "userModsDir": mods,
                   "modsList": {"default": {"mods": [pe(lib[0]), pe(lib[1])]},
                                "second": {"mods": [pe(lib[1], False, False)]}},
                   "modsLibrary": lib},
                  open(p, "w", encoding="utf-8"), ensure_ascii=False)

        check("profile_names_with 能列出所有档",
              S.profile_names_with(p, "b") == ["default", "second"],
              str(S.profile_names_with(p, "b")))
        check("profile_names_with 对不在档里的返回空",
              S.profile_names_with(p, "c") == [])

        # ① 只从列表移除（保留文件）
        r = S.remove_library_mod(p, "b")
        check("产生了备份", os.path.isfile(r["backup"]))
        check("从库里移除了", [m["uuid"] for m in read(p)["modsLibrary"]] == ["a", "c"],
              str([m["uuid"] for m in read(p)["modsLibrary"]]))
        check("从 default 档也移除了",
              [m["uuid"] for m in read(p)["modsList"]["default"]["mods"]] == ["a"],
              str([m["uuid"] for m in read(p)["modsList"]["default"]["mods"]]))
        check("从 second 档也移除了",
              read(p)["modsList"]["second"]["mods"] == [],
              str(read(p)["modsList"]["second"]["mods"]))
        check("★ 不留僵尸配置档条目（否则它还能被部署出去）",
              all(m["uuid"] != "b"
                  for prof in read(p)["modsList"].values()
                  for m in prof["mods"]))
        check("默认不删磁盘文件", os.path.isdir(os.path.join(mods, "b")))
        check("报告了从哪些档移除",
              r["removed_profiles"] == ["default", "second"],
              str(r["removed_profiles"]))
        check("其它模组没被动",
              os.path.isdir(os.path.join(mods, "a"))
              and os.path.isdir(os.path.join(mods, "c")))

        # ② 连文件一起删
        r2 = S.remove_library_mod(p, "a", delete_files=True)
        check("文件夹被删了", not os.path.isdir(os.path.join(mods, "a")))
        check("报告了删掉的路径", r2["deleted_folder"].endswith("a"),
              str(r2["deleted_folder"]))
        check("库少了一条", [m["uuid"] for m in read(p)["modsLibrary"]] == ["c"])

        # ③ 删不存在的
        try:
            S.remove_library_mod(p, "zzz")
            check("删不存在的应报错", False)
        except S.SyncError as e:
            check("删不存在的应报错", "不在 Arsenal 的模组库里" in str(e), str(e))

        # ④ ★ 路径在 mods 目录之外 → 拒绝删文件，且**文件一个字节都不能写**
        #    （注意：uuid 必须用新的，否则 next() 会先找到库里本来那条）
        outside = os.path.join(root, "outside")
        os.makedirs(outside, exist_ok=True)
        lib_out = dict(lib[2])
        lib_out["uuid"] = "outside-mod"
        lib_out["path"] = outside
        j = read(p)
        j["modsLibrary"].append(lib_out)
        json.dump(j, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        before = open(p, "rb").read()
        try:
            S.remove_library_mod(p, "outside-mod", delete_files=True)
            check("外部路径应拒绝", False)
        except S.SyncError as e:
            check("外部路径应拒绝", "不在 Arsenal 的模组目录里" in str(e), str(e))
        check("★ 拒绝时文件字节完全未变", open(p, "rb").read() == before)
        check("外部文件夹没被删", os.path.isdir(outside))
        check("拒绝时条目仍在库里",
              "outside-mod" in [m["uuid"] for m in read(p)["modsLibrary"]],
              str([m["uuid"] for m in read(p)["modsLibrary"]]))
        # 只从列表移除（不删文件）时，外部路径也应该放行
        r3 = S.remove_library_mod(p, "outside-mod")
        check("不删文件时外部路径可以移除条目",
              "outside-mod" not in [m["uuid"] for m in read(p)["modsLibrary"]])
        check("外部文件夹依然完好", os.path.isdir(outside))

        # ⑤ 运行时拒绝
        orig = S.arsenal_running
        S.arsenal_running = lambda *a, **k: True
        try:
            try:
                S.remove_library_mod(p, "c")
                check("运行时应拒绝", False)
            except S.SyncError as e:
                check("运行时应拒绝", "正在运行" in str(e))
        finally:
            S.arsenal_running = orig
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    for fn in (t_mirror, t_mirror_robust, t_writeback, t_running_guard,
               t_snapshot, t_atomic, t_live_files, t_desc,
               t_read_options, t_set_options, t_set_options_running_guard,
               t_order, t_order_running_guard, t_set_enabled, t_running_cache,
               t_probe_paths, t_order_partial_keeps_others, t_write_guard,
               t_add_to_profile, t_remove_library_mod):
        fn()
    print(f"\nArsenal 同步自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())