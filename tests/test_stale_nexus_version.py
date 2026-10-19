# -*- coding: utf-8 -*-
"""回归自测：**N 网那条陈旧的 nexusData.version 不能压住导入记录**。

★ 起因（2026-10-19 用户的原话）：
    「帮我看看我的Mod更新管理器，这个mod我更新以后还是显示未更新，
      这是为什么？名字也都成V20了本地版本还是显示的V19」

  截图那一行是：状态「有更新」 / 名称「Bingus Shared Loader - v20」 /
  本地「19」 / 线上「v20」。

★ 根因（实测取证，见 _diag_bingus.py 的输出）：
    Arsenal 库里那条 `nexusData.version` 停在 **19**（陈旧值）——
    只有**从 N 网下载安装**时才会被写成新版本；用户是**手动导入一个
    zip**（Bingus-Shared-Loader-v20.zip，来自 GitHub）的，Arsenal 根本
    不知道版本变了，这个值就永远停在旧值。
    而本工具在导入那一刻**确切知道**装的是 20，并把它连同内容签名记进了
    `config.json` 的 `installed_versions`（实测：version=20，sig 与磁盘上
    目录的现场重算结果**完全一致** → 这条记录有效）。

    旧代码（arsenal.load_library）：
        auto = entry["nexus_version"] or _pick_local_version(entry, _imported)
    `or` 是**短路**的 —— nexus_version 只要非空就赢，后面的导入记录
    根本没机会被读到 → 本地版本永远显示 19 → 永远报「有更新」。

★ 本文件钉住的规则：
    ① N 网记的版本陈旧（19）、导入记录更新（20）→ **采用 20**
    ② 反过来（N 网更新、导入记录旧）→ 采用 N 网那个
    ③ 两边一样 → 当然是它
    ④ 比不了大小（格式不同）→ 保留 N 网那条（不引入新行为）
    ⑤ 只有一方有值 → 用那一方
    ⑥ 最终结论必须真的变成「已是最新」，而不只是数字对
    ⑦ 存量用户（**没有**导入记录的老数据）不能被这次改动弄坏

跑法：python tests/test_stale_nexus_version.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["HD2U_NO_DIALOG"] = "1"

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


def _isolated(func):
    """在**临时目录**里跑，绝不碰用户真实的数据目录 / Arsenal 库。"""
    old = os.environ.get("HD2U_DATA_DIR")
    guard = os.environ.get("HD2U_READONLY_GUARD")
    td = tempfile.mkdtemp(prefix="stale_nexus_")
    os.environ["HD2U_DATA_DIR"] = td
    os.environ["HD2U_READONLY_GUARD"] = "1"
    try:
        func(td)
    finally:
        if old is None:
            os.environ.pop("HD2U_DATA_DIR", None)
        else:
            os.environ["HD2U_DATA_DIR"] = old
        if guard is None:
            os.environ.pop("HD2U_READONLY_GUARD", None)
        else:
            os.environ["HD2U_READONLY_GUARD"] = guard
        shutil.rmtree(td, ignore_errors=True)


def _make_lib(td: str, uuid: str, label: str, dirname: str,
              nexus_version: str = "", n_patch: int = 3):
    """造一份最小可用的 Arsenal 库（形状必须与真实一致，见 issue1 测试的说明）。

    ``nexus_version`` 非空 = 模拟"Arsenal 库里那条 nexusData.version"，
    也就是本文件要打的靶子。
    """
    mods = os.path.join(td, "mods")
    mdir = os.path.join(mods, dirname)
    os.makedirs(mdir, exist_ok=True)
    for i in range(n_patch):
        with open(os.path.join(mdir, f"slot{i}.patch_0"), "wb") as fh:
            fh.write(b"content-%d-v20" % i)
    nexus_data = None
    if nexus_version:
        nexus_data = {"modId": "16292", "fileId": "67485",
                      "version": nexus_version, "updateTimestamp": 1791160130}
    entry = {"uuid": uuid, "label": label, "path": mdir, "description": "",
             "iconPath": None, "tags": ["misc"], "patchFileNames": [],
             "nexusData": nexus_data, "options": [],
             "contentHash": "", "addedAt": "2026-10-19T12:49:45.138Z",
             "previousVersions": []}
    prof = dict(entry)
    prof.update({"enabled": True, "deployed": True, "changed": False,
                 "new": False, "copiedFiles": [], "renamedFiles": []})
    df = os.path.join(td, "hd2a_data.json")
    with open(df, "w", encoding="utf-8") as f:
        json.dump({"modsLibrary": [entry],
                   "modsList": {"Default Profile": {"mods": [prof]}},
                   "selectedProfile": "Default Profile",
                   "userModsDir": mods, "userGameDir": "D:\\g",
                   "setTopPriority": False}, f, ensure_ascii=False)
    return df, mods, mdir


# ============================================================ 核心规则
def t_merge_rules():
    """①②③④⑤ 合并规则：冲突时取更新的那个。"""
    print("[①~⑤ N 网记录 vs 导入记录：取更新的那个]")

    def body(td):
        from hd2_updater import arsenal as Ar
        from hd2_updater.util import compare

        # ① N 网陈旧（19）vs 导入记录新（20）→ 20
        check("① N 网旧/导入新 → 取导入的 20",
              Ar._merge_nexus_and_imported("19", "20") == "20",
              Ar._merge_nexus_and_imported("19", "20"))
        # ② N 网新（21）vs 导入记录旧（20）→ 21
        check("② N 网新/导入旧 → 取 N 网的 21",
              Ar._merge_nexus_and_imported("21", "20") == "21",
              Ar._merge_nexus_and_imported("21", "20"))
        # ③ 一样 → 就是它
        check("③ 两边一样 → 原样",
              Ar._merge_nexus_and_imported("20", "20") == "20")
        # ④ 比不了大小（一个是日期、一个是版本号）→ 保留 N 网那条
        check("④ 比不了 → 保留 N 网那条",
              Ar._merge_nexus_and_imported("2026-09-24", "1.2.3") == "2026-09-24",
              Ar._merge_nexus_and_imported("2026-09-24", "1.2.3"))
        # ⑤ 只有一方
        check("⑤ 只有 N 网那条 → 用它",
              Ar._merge_nexus_and_imported("19", "") == "19")
        check("⑤ 只有导入记录 → 用它",
              Ar._merge_nexus_and_imported("", "20") == "20")
        check("⑤ 两边都空 → 空",
              Ar._merge_nexus_and_imported("", "") == "")
        # 工具函数本身别把"取更新的那个"用反了
        check("辅助：compare 方向正确（20 比 19 新）",
              compare("19", "20") == "newer", compare("19", "20"))

    _isolated(body)


# ============================================================ 端到端
def t_end_to_end_stale_nexus():
    """★ 端到端复现用户那一行：N 网记 19、实装 20 → 必须显示 20 且判「已最新」。"""
    print("[★ 端到端：N 网 19 / 实装 20 → 显示 20，判「已是最新」]")

    def body(td):
        from hd2_updater import arsenal as Ar
        from hd2_updater.engine import evaluate

        uuid = "612eaf70-d682-43c7-9efd-16dcc695f977"
        # 目录名是 N 网命名、版本段是 19（导入复用旧目录 → 目录名永久旧值）
        df, mods, mdir = _make_lib(
            td, uuid, "Bingus Shared Loader - v20",
            "Bingus Shared Loader 16292 19 2026-10-05T00-28Z eqrMD7Qqb_AR864690",
            nexus_version="19")
        # 导入那一刻记下的版本 + 当时的内容签名（= 现在磁盘上的内容）
        Ar.set_installed_version(uuid, "20", Ar.content_sig(mdir, fresh=True))

        e = Ar.load_library(df)[0]
        check("前提：N 网那条仍是陈旧的 19",
              e["nexus_version"] == "19", e["nexus_version"])
        check("前提：导入记录有效（内容签名核对通过）",
              e["imported_version"] == "20", e["imported_version"])
        check("★ 本地版本 = 20（不是 19）",
              e["installed_version"] == "20", e["installed_version"])
        check("★ 自动探测值 = 20",
              e["auto_version"] == "20", e["auto_version"])
        # 结论也必须跟着变：线上 v20 → 「已是最新」，不是「有更新」
        ev = evaluate(e, [{"provider": "github", "ok": True, "version": "v20",
                           "url": "x", "download_url": "y"}])
        check("★ 结论 = 已最新（不是「有更新」）",
              ev["status"] == "ok", ev["status"])
        # 线上是 v21 时，仍然要正确报「有更新」
        ev2 = evaluate(e, [{"provider": "github", "ok": True, "version": "v21",
                            "url": "x", "download_url": "y"}])
        check("线上 v21 时仍然报「有更新」（别把真更新也压掉）",
              ev2["status"] == "update", ev2["status"])

    _isolated(body)


def t_no_imported_record_not_broken():
    """⑦ 存量用户（没有导入记录的老数据）不能被这次改动弄坏。"""
    print("[⑦ 存量用户：没有导入记录 → 行为不变]")

    def body(td):
        from hd2_updater import arsenal as Ar

        # 情形 A：有 N 网记录、没有导入记录 → 仍用 N 网那条
        ua = "aaaa0000-0000-0000-0000-0000000000a1"
        dfa, _ma, da = _make_lib(td, ua, "A",
                                 "A 16493 5 2026-01-01T00-00Z a_AR1",
                                 nexus_version="5")
        ea = Ar.load_library(dfa)[0]
        check("A 有 N 网记录、无导入记录 → 仍用 N 网的 5",
              ea["installed_version"] == "5", ea["installed_version"])
        check("A 没有导入记录（不凭空造一个）",
              ea["imported_version"] == "", ea["imported_version"])

        # 情形 B：N 网记录陈旧、也**没有**导入记录 → 只能靠目录名/元数据
        #         （这时目录名也是旧值 → 保持旧行为，不假装知道）
        ub = "bbbb0000-0000-0000-0000-0000000000b2"
        dfb, _mb, db = _make_lib(td, ub, "B",
                                 "B 16493 19 2026-01-01T00-00Z b_AR2",
                                 nexus_version="19")
        eb = Ar.load_library(dfb)[0]
        check("B 无导入记录 → 不产生导入版本",
              eb["imported_version"] == "", eb["imported_version"])
        check("B 无导入记录 → 仍按旧逻辑给得出值（19）",
              eb["installed_version"] == "19", eb["installed_version"])

    _isolated(body)


def t_expired_imported_record_falls_back():
    """导入记录的内容签名失效（用户又更新了）→ 回到 N 网那条，不能卡住。"""
    print("[导入记录失效 → 回到 N 网那条]")

    def body(td):
        from hd2_updater import arsenal as Ar

        uuid = "cccc0000-0000-0000-0000-0000000000c3"
        df, _m, mdir = _make_lib(td, uuid, "C",
                                 "C 16493 19 2026-01-01T00-00Z c_AR3",
                                 nexus_version="19")
        Ar.set_installed_version(uuid, "20", Ar.content_sig(mdir, fresh=True))
        e = Ar.load_library(df)[0]
        check("★ 签名有效时用导入记录 20",
              e["installed_version"] == "20", e["installed_version"])
        # 用户又更新了（内容变了）→ 导入记录自动失效
        for i in range(3):
            with open(os.path.join(mdir, f"slot{i}.patch_0"), "wb") as fh:
                fh.write(b"changed %d" % i)
        e2 = Ar.load_library(df)[0]
        check("内容变过 → 导入记录失效",
              e2["imported_version"] == "", e2["imported_version"])
        check("★ 失效后回到 N 网那条（19），不悬空",
              e2["installed_version"] == "19", e2["installed_version"])

    _isolated(body)


def main() -> int:
    for fn in (t_merge_rules,
               t_end_to_end_stale_nexus,
               t_no_imported_record_not_broken,
               t_expired_imported_record_falls_back):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            check(f"{fn.__name__} 自身抛异常", False,
                  f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}")
    print()
    print(f"「N 网陈旧版本压住导入记录」自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
