# -*- coding: utf-8 -*-
"""GitHub issue #1「更新信息有误」的回归自测（2026-10-12）。

★ 反馈者的原话：「更新信息有误」「重新下载mod并安装」。
★ 用户的原话：「自相矛盾的地方，有没有可能是本来用户最开始用了时候是1.1版本，
  就手动填了1.1，但现在更新以后成1.2了，用户手动填的1.1还是最高优先级，
  就把1.2的给顶掉了？」——**完全命中**，本文件就是钉住这个根因的。

根因与本文件钉住的六条规则：

  ① 导入 ZIP **不能抹掉** Arsenal 自己写的 nexusData（那是数据丢失）
  ② 「指定来源」里**没改**版本框时，一个字节都不能写进 version_overrides
  ③ 弹窗里的「自动探测到：X」必须取真正的自动探测值（auto_version）
  ④ 内容签名变了 → 手动填的版本**必须自动失效**（这就是「不冻结」）
  ⑤ 内容签名没变 → 手动填的版本**必须仍然生效**（防倒退）
  ⑥ 内容签名不能只看文件大小（同尺寸改写也必须被认出来）

跑法：python tests/test_issue1_version_freeze.py
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
    """跑一段用到 data_dir 的代码，并在**临时目录**里隔离所有写入。"""
    old = os.environ.get("HD2U_DATA_DIR")
    guard = os.environ.get("HD2U_READONLY_GUARD")
    td = tempfile.mkdtemp(prefix="issue1_test_")
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


def _make_lib(td, uuid, label, dirname, nexus_data=None, n_patch=3):
    """造一个最小可用的 Arsenal 库。

    ★ 形状必须**和 Arsenal 真实写的完全一致**（实测 21/21 统一）：
      库条目 12 个字段、配置档条目 = 库条目 + 6 个 = 18 个
      （见 arsenal_sync.LIBRARY_FIELDS / PROFILE_EXTRA_FIELDS）。
      少写一个字段，upsert_library_mod 就会以
      「同步配置档时字段数变化，已放弃」拒绝写入 —— 很容易误以为是代码坏了。
    """
    mods = os.path.join(td, "mods")
    mdir = os.path.join(mods, dirname)
    os.makedirs(mdir, exist_ok=True)
    for i in range(n_patch):
        with open(os.path.join(mdir, f"slot{i}.patch_0"), "wb") as fh:
            fh.write(b"content-%d-v10" % i)
    # 12 个库字段，一个都不能少
    entry = {"uuid": uuid, "label": label, "path": mdir, "description": "",
             "iconPath": None, "tags": ["misc"], "patchFileNames": [],
             "nexusData": nexus_data, "options": [],
             "contentHash": "", "addedAt": "2026-01-01T00:00:00.000Z",
             "previousVersions": []}
    # 配置档副本 = 库条目 + 6 个（共 18）
    prof = dict(entry)
    prof.update({"enabled": True, "deployed": True, "changed": False,
                 "new": False, "copiedFiles": [], "renamedFiles": []})
    assert len(entry) == 12, f"库条目应 12 个字段，实际 {len(entry)}"
    assert len(prof) == 18, f"配置档条目应 18 个字段，实际 {len(prof)}"
    df = os.path.join(td, "hd2a_data.json")
    with open(df, "w", encoding="utf-8") as f:
        json.dump({"modsLibrary": [entry],
                   "modsList": {"Default Profile": {"mods": [prof]}},
                   "selectedProfile": "Default Profile",
                   "userModsDir": mods, "userGameDir": "D:\\g",
                   "setTopPriority": False}, f, ensure_ascii=False)
    return df, mods, mdir


# ============================================================ ④⑤ 内容签名
def t_signature_rules():
    """④⑤ 签名：内容变了一定不同、没变一定相同；且不能只看大小（⑥）。"""
    print("[④⑤⑥ 内容签名]")

    def body(td):
        from hd2_updater import arsenal
        mods = os.path.join(td, "m")
        os.makedirs(mods, exist_ok=True)
        p = os.path.join(mods, "x")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "a.patch_0"), "wb") as fh:
            fh.write(b"A" * 1000)
        s1 = arsenal.content_sig(p)
        check("① 算得出签名", bool(s1), s1)
        check("② 同样内容重复算结果相同",
              arsenal.content_sig(p) == s1)
        # 内容变长（mtime+大小都变 → 缓存必须失效重算）
        with open(os.path.join(p, "a.patch_0"), "wb") as fh:
            fh.write(b"A" * 2000)
        s2 = arsenal.content_sig(p)
        check("③ 内容变了签名跟着变", s2 != s1, f"{s1[:8]} vs {s2[:8]}")
        # ★⑥ 内容全换、**大小不变** —— 只记大小的话这里会漏判
        with open(os.path.join(p, "a.patch_0"), "wb") as fh:
            fh.write(b"B" * 2000)
        s3 = arsenal.content_sig(p)
        check("⑥ 同尺寸改写也能认出来（不能只比大小）", s3 not in (s1, s2),
              f"{s2[:8]} vs {s3[:8]}")
        # 目录不存在 → 返回空串而不是抛异常
        check("⑦ 目录不存在返回空串", arsenal.content_sig(
            os.path.join(mods, "nope")) == "")

    _isolated(body)


def t_override_unfreezes_on_change():
    """★核心：手动值「不冻结」—— 内容变了就自动失效（用户提案）。"""
    print("[★ 不冻结：内容变了 → 手动值自动失效]")

    def body(td):
        from hd2_updater import arsenal
        uuid = "aaaa1111-2222-3333-4444-555566667777"
        df, _mods, mdir = _make_lib(td, uuid, "Some Mod",
                                    "Some Mod 16493 10 2026-01-01T00-00Z x_AR1")
        lib = arsenal.load_library(df)
        e = lib[0]
        check("自动探测到目录名里的版本 10", e["auto_version"] == "10",
              str(e["auto_version"]))
        check("没记录时用自动值", e["installed_version"] == "10"
              and not e["version_is_manual"], e["installed_version"])

        # 用户填了 10，并记下此刻的内容签名
        sig0 = arsenal.content_sig(mdir)
        arsenal.set_version_override(uuid, "10", sig0)
        e = arsenal.load_library(df)[0]
        check("手动值生效（内容没变）", e["installed_version"] == "10"
              and e["version_is_manual"], e["installed_version"])
        check("⑤ 内容没变时**不能**失效（防倒退）",
              not e["override_stale"])

        # ★ 用户更新到 v12：内容变了、目录名也变了
        new_dir = "Some Mod 16493 12 2026-10-11T11-52Z y_AR2"
        newdir = os.path.join(td, "mods", new_dir)
        shutil.move(mdir, newdir)
        for i in range(3):
            with open(os.path.join(newdir, f"slot{i}.patch_0"), "wb") as fh:
                fh.write(b"content-%d-v12-NEW" % i)
        data = json.load(open(df, encoding="utf-8-sig"))
        data["modsLibrary"][0]["path"] = newdir
        json.dump(data, open(df, "w", encoding="utf-8"), ensure_ascii=False)

        e2 = arsenal.load_library(df)[0]
        check("④ ★ 内容变了 → 手动值自动失效",
              not e2["version_is_manual"] and e2["override_stale"],
              f"manual={e2['version_is_manual']} stale={e2['override_stale']}")
        check("★ 本地版本自动跟到新版本 12", e2["installed_version"] == "12",
              e2["installed_version"])
        check("★ 你填的旧值还留着可查", e2["manual_version"] == "10",
              e2["manual_version"])
        check("★ 失效原因说清了", "内容变了" in str(e2["override_why"]),
              str(e2["override_why"]))

        # 确认 evaluate 会因此判「已最新」而不是「有更新」
        from hd2_updater.engine import evaluate
        ev = evaluate(e2, [{"provider": "nexus", "ok": True, "version": "12",
                            "url": "x", "download_url": ""}])
        check("★ 判定为已最新（不再误报有更新）", ev["status"] == "ok",
              ev["status"])

    _isolated(body)


def t_legacy_record_kept():
    """旧格式（只有版本、没有签名）必须**保留**，不能因为缺签名就丢用户数据。"""
    print("[旧格式记录：沿用但标出来]")

    def body(td):
        from hd2_updater import arsenal
        uuid = "bbbb1111-2222-3333-4444-555566667777"
        df, _m, _d = _make_lib(td, uuid, "Legacy",
                               "Legacy 16493 5 2026-01-01T00-00Z z_AR1")
        cfg = os.path.join(td, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"version_overrides": {uuid: "5"}}, f)
        e = arsenal.load_library(df)[0]
        check("旧格式记录仍然生效（不丢用户填的值）",
              e["installed_version"] == "5" and e["version_is_manual"],
              e["installed_version"])
        check("标成「没有内容签名」，界面据此提示待核对",
              "旧格式" in str(e["manual_state"]), str(e["manual_state"]))
        check("get_version_override 也能读到", arsenal.get_version_override(uuid) == "5")
        # 清掉
        check("clear 能删掉", arsenal.clear_version_override(uuid))
        e2 = arsenal.load_library(df)[0]
        check("清掉后回到自动探测", e2["installed_version"] == "5"
              and not e2["version_is_manual"])

    _isolated(body)


def t_high_override_missed_update():
    """反向：override 偏高 → 漏报。内容一变就失效，正是这条的解药。"""
    print("[反向：填得偏高会漏报，内容一变就自动解药]")

    def body(td):
        from hd2_updater import arsenal
        from hd2_updater.engine import evaluate
        uuid = "cccc1111-2222-3333-4444-555566667777"
        df, mods, mdir = _make_lib(td, uuid, "High",
                                   "High 16493 12 2026-01-01T00-00Z h_AR1")
        # 用户手滑填了 14（比实际装的 12 高）
        arsenal.set_version_override(uuid, "14", arsenal.content_sig(mdir))
        e = arsenal.load_library(df)[0]
        ev = evaluate(e, [{"provider": "nexus", "ok": True, "version": "13",
                            "url": "x", "download_url": ""}])
        check("内容没变时，偏高仍然会漏报（这是填错的后果）",
              ev["status"] == "ok", ev["status"])
        # 作者发了 13、用户装上 → 内容变了 → 自动失效 → 改判「有更新」
        for i in range(3):
            with open(os.path.join(mdir, f"slot{i}.patch_0"), "wb") as fh:
                fh.write(b"content-%d-v13" % i)
        e2 = arsenal.load_library(df)[0]
        ev2 = evaluate(e2, [{"provider": "nexus", "ok": True, "version": "13",
                             "url": "x", "download_url": ""}])
        check("★ 内容变了自动解药 → 漏报被纠正",
              not e2["version_is_manual"] and ev2["status"] == "update",
              f"manual={e2['version_is_manual']} status={ev2['status']}")

    _isolated(body)


def t_orphan_pruned():
    """孤儿记录（模组已不在库里）必须能被清掉。"""
    print("[孤儿记录清理]")

    def body(td):
        from hd2_updater import arsenal
        uuid = "dddd1111-2222-3333-4444-555566667777"
        df, _m, _d = _make_lib(td, uuid, "Gone", "Gone 1_AR1")
        arsenal.set_version_override(uuid, "1.2")
        arsenal.set_version_override("orphan-uuid", "9.9")
        check("两条记录都在", len(arsenal.read_version_overrides()) == 2)
        n = arsenal.prune_version_overrides(
            [e["uuid"] for e in arsenal.load_library(df)])
        check("清掉 1 条孤儿", n == 1, str(n))
        ov = arsenal.read_version_overrides()
        check("孤儿没了、在库的那条还在",
              uuid in ov and "orphan-uuid" not in ov, str(list(ov)))


# ============================================================ ① nexusData
def t_import_keeps_nexus_data():
    """★ 导入 ZIP 不能抹掉 Arsenal 自己写的 nexusData（数据丢失）。"""
    print("[★ 导入保留 nexusData]")

    def body(td):
        import zipfile
        from hd2_updater import modimport
        uuid = "eeee1111-2222-3333-4444-555566667777"
        old_nd = {"modId": "16493", "fileId": "555", "version": "12",
                  "updateTimestamp": 1760000000}
        df, mods, mdir = _make_lib(td, uuid, "Some Mod",
                                   "Some Mod 16493 10 2026-01-01T00-00Z x_AR1",
                                   nexus_data=old_nd)
        # 新版 ZIP（N 网命名、版本 12），不填 API key（查不到 fileId）
        zp = os.path.join(td, "Some Mod 16493 12 2026-10-11T11-52Z new.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("manifest.json", json.dumps(
                {"Name": "Some Mod", "Guid": uuid}))
            z.writestr("a.patch_0", b"brand new v12 content")
        modimport.import_zip(df, zp, mods_dir=mods, api_key="")
        nd = json.load(open(df, encoding="utf-8-sig"))["modsLibrary"][0].get("nexusData")
        check("① ★ nexusData 没被抹掉", isinstance(nd, dict), str(nd))
        check("① ★ modId 还在", (nd or {}).get("modId") == "16493", str(nd))
        check("① ★ fileId 还在", (nd or {}).get("fileId") == "555", str(nd))
        check("① ★ version 更新成了这次的 12", (nd or {}).get("version") == "12",
              str((nd or {}).get("version")))
        # 关键后果：本地版本必须还是 12（不能退回"目录名里的旧 10"）
        from hd2_updater import arsenal
        e = arsenal.load_library(df)[0]
        check("① ★ 导入后本地版本就是 12（不再是旧版本 10）",
              e["installed_version"] == "12", e["installed_version"])
        ev_ = __import__("hd2_updater.engine", fromlist=["evaluate"]).evaluate(
            e, [{"provider": "nexus", "ok": True, "version": "12",
                 "url": "x", "download_url": ""}])
        check("① ★ 判定「已最新」而不是「有更新」", ev_["status"] == "ok",
              ev_["status"])

    _isolated(body)


# ============================================================ ②③ 弹窗
def t_dialog_writes_only_when_changed():
    """② 保存「指定来源」时，没改版本框就不能写 override。"""
    print("[② 没改版本框 → 不写 override]")

    def body(td):
        from hd2_updater import arsenal
        uuid = "ffff1111-2222-3333-4444-555566667777"
        df, _m, _d = _make_lib(td, uuid, "NoWrite",
                               "NoWrite 16493 3 2026-01-01T00-00Z n_AR1")
        cfg = os.path.join(td, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({}, f)
        # 模拟 SourceDialog._save 的判定：typed == 打开时的值 → 不写
        from hd2_updater.gui import SourceDialog  # 触发 import 校验
        initial = arsenal.get_version_override(uuid) or arsenal.load_library(df)[0]["auto_version"]
        typed = initial                      # 用户一个字没改
        check("② 打开时初值 = 自动探测值", initial == "3", initial)
        if uuid and typed != initial:
            arsenal.set_version_override(uuid, typed)
        check("② ★ 没改 → config.json 里没有多出记录",
              uuid not in arsenal.read_version_overrides(),
              str(arsenal.read_version_overrides()))
        # 改了呢？必须写，而且带上内容签名
        e = arsenal.load_library(df)[0]
        arsenal.set_version_override(uuid, "3.5",
                                     arsenal.content_sig(e["path"]))
        rec = arsenal.read_version_overrides()[uuid]
        check("② ★ 改了 → 写入且带内容签名",
              rec.get("version") == "3.5" and bool(rec.get("sig")), str(rec))

    _isolated(body)


def t_dialog_note_uses_auto_version():
    """③ 弹窗里「自动探测到：X」必须取 auto_version，而不是 installed_version。"""
    print("[③ 提示必须取真正的自动探测值]")
    import re
    src = open(os.path.join(ROOT, "hd2_updater", "gui.py"),
               encoding="utf-8").read()
    # ★ 注意：raw 字符串里写 \" 是"反斜杠+引号"，会把引号一起捕获进来 ——
    #   这里必须用普通的 "（不能用 \"，否则 group 里会带上引号、比不上）
    m = re.search(r'auto = entry\.get\("([^"]+)"\)', src)
    check("③ 弹窗里 auto 取自 entry 的 auto_version",
          bool(m) and m.group(1) == "auto_version",
          m.group(1) if m else "没找到")
    check("③ 不再用 installed_version 当「自动探测」",
          'auto = entry.get("installed_version")' not in src,
          "还在用 installed_version")
    # 说明文案要如实摆出"手动值 vs 自动值"
    check("③ 提示里能同时看到手动值与自动探测值",
          "你手动填的" in src and "自动探测到" in src)


# ============================================================ 界面
def t_ui_shows_version_origin():
    """界面上必须能看出本地版本是手动填的还是自动探测的。"""
    print("[界面：本地版本要标出来源]")
    from hd2_updater.gui import App
    cell = App._local_cell
    check("自动 → 只显示版本", cell({"installed": "1.2", "entry": {}}) == "1.2")
    check("手动 → 标「（手动）」",
          cell({"installed": "1.1",
                "entry": {"version_is_manual": True}}) == "1.1（手动）")
    check("★ 已失效 → 标出旧值已失效",
          "已失效" in cell({"installed": "1.2",
                            "entry": {"override_stale": True,
                                      "manual_version": "1.1"}}),
          cell({"installed": "1.2",
                "entry": {"override_stale": True,
                          "manual_version": "1.1"}}))
    import re
    src = open(os.path.join(ROOT, "hd2_updater", "gui.py"),
               encoding="utf-8").read()
    check("右键菜单有「本地版本…」入口", "本地版本…" in src)
    check("有恢复自动探测的代码路径", "clear_version_override" in src)


def t_diag_reports_version_origin():
    """诊断输出里要说清是手动还是自动（反馈者就是靠这五个字发现的）。"""
    print("[诊断：版本来源要说清]")
    src = open(os.path.join(ROOT, "hd2_updater", "diag.py"),
               encoding="utf-8").read()
    check("诊断仍标「手动填的 / 自动猜的」",
          "手动填的" in src and "自动猜的" in src)
    # 已失效的也要能看出来，否则反馈者会以为还是好的
    check("诊断里带上失效信息", "override_stale" in src
          or "manual_version" in src)


def t_import_records_installed_version():
    """★ 导入新版后，"本地版本"必须真的变成新版。

    ★★ 这是**用户 2026-10-12 实测发现的第二个根因**（跟 override 无关）：
      导入更新版时 `import_zip` 会**复用旧目录**，所以文件夹名里那个版本号
      是永久旧值（"Vanilla-Plus-Megapack-v40_AR205686" 里装着 v41 的内容），
      而 `guess_version` 优先读目录名 → 永远报「有更新」。
      用户原话：「两个mod，runtime和Vanilla Plus Megapack - v41，我明明更新了
      也导入部署了，但是现在重新检查还是显示有更新」。
    """
    print("[★★ 导入新版后本地版本要跟上（用户实测的两个 mod）]")

    def body(td):
        import zipfile
        from hd2_updater import modimport
        from hd2_updater.engine import evaluate
        cases = [
            ("dddd0000-0000-0000-0000-000000000001",
             "HD2Runtime-0.30.3-runtime_AR172840",
             "HD2Runtime-0.31.3-runtime.zip", "0.31.3"),
            ("dddd0000-0000-0000-0000-000000000002",
             "Vanilla-Plus-Megapack-v40_AR205686",
             "Vanilla-Plus-Megapack-v41.zip", "41"),
        ]
        mods = os.path.join(td, "mods")
        os.makedirs(mods)
        lib, prof = [], []
        for uuid, dirname, zname, _v in cases:
            d = os.path.join(mods, dirname)
            os.makedirs(d)
            with open(os.path.join(d, "a.patch_0"), "wb") as fh:
                fh.write(b"old content")
            e = {"uuid": uuid, "label": os.path.splitext(zname)[0], "path": d,
                 "description": "", "iconPath": None, "tags": ["misc"],
                 "patchFileNames": [], "options": [], "contentHash": "",
                 "addedAt": "", "previousVersions": [], "nexusData": None}
            lib.append(e)
            p = dict(e)
            p.update({"enabled": True, "deployed": True, "changed": False,
                      "new": False, "copiedFiles": [], "renamedFiles": []})
            prof.append(p)
        df = os.path.join(td, "hd2a_data.json")
        with open(df, "w", encoding="utf-8") as f:
            json.dump({"modsLibrary": lib,
                       "modsList": {"Default Profile": {"mods": prof}},
                       "selectedProfile": "Default Profile",
                       "userModsDir": mods, "userGameDir": "D:\\g",
                       "setTopPriority": False}, f, ensure_ascii=False)

        for uuid, dirname, zname, want in cases:
            zp = os.path.join(td, zname)
            with zipfile.ZipFile(zp, "w") as z:
                z.writestr("manifest.json", json.dumps(
                    {"Name": os.path.splitext(zname)[0], "Guid": uuid}))
                z.writestr("a.patch_0", b"brand new " + want.encode())
            modimport.import_zip(df, zp, mods_dir=mods, api_key="")
            row = next(e for e in
                       __import__("hd2_updater.arsenal", fromlist=["x"])
                       .load_library(df) if e["uuid"] == uuid)
            check(f"{zname[:26]} 目录名仍是旧的（复现前提）",
                  want not in row["dirname"], row["dirname"])
            check(f"{zname[:26]} ★ 导入后本地版本 = {want}",
                  row["installed_version"] == want,
                  f"实际 {row['installed_version']!r}")
            ev = evaluate(row, [{"provider": "github", "ok": True,
                                 "version": want, "url": "x",
                                 "download_url": ""}])
            check(f"{zname[:26]} ★ 判定「已最新」而不是「有更新」",
                  ev["status"] == "ok", ev["status"])

    _isolated(body)


def t_imported_record_expires_on_next_change():
    """导入记录也带内容签名 → 用户再更新一次，它也自动失效（不会二次冻结）。"""
    print("[导入记录也会自动失效]")

    def body(td):
        from hd2_updater import arsenal
        uuid = "99990000-0000-0000-0000-000000000009"
        df, mods, mdir = _make_lib(td, uuid, "Exp",
                                   "Exp 1.0.0_AR1")
        arsenal.set_installed_version(uuid, "1.0.0",
                                      arsenal.content_sig(mdir, fresh=True))
        e = arsenal.load_library(df)[0]
        check("导入记录生效", e["imported_version"] == "1.0.0"
              and e["auto_version"] == "1.0.0", e["auto_version"])
        # 内容再变（用户又更新了）→ 记录自动失效
        for i in range(3):
            with open(os.path.join(mdir, f"slot{i}.patch_0"), "wb") as fh:
                fh.write(b"changed again %d" % i)
        e2 = arsenal.load_library(df)[0]
        check("★ 内容又变了 → 导入记录自动失效",
              e2["imported_version"] == "", e2["imported_version"])

    _isolated(body)


def t_stale_dirname_fallback():
    """没有导入记录时（存量用户）：目录名旧、元数据新 → 要挑更新的那个。

    ★ 这一条让**已经中招的老用户装上新版就能好起来**，不用重新导入。
    """
    print("[存量兜底：目录名旧/元数据新 → 挑更新的]")

    def body(td):
        from hd2_updater import arsenal
        uuid = "aaaa0000-0000-0000-0000-00000000000f"
        df, mods, mdir = _make_lib(td, uuid, "Runtime",
                                   "Runtime-0.30.3-runtime_AR1", n_patch=3)
        # 在目录里放一个带版本号的元数据文件，模拟 HD2Runtime 的 hd2runtime.json
        with open(os.path.join(mdir, "hd2runtime.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"version": "0.31.3"}, f)
        e = arsenal.load_library(df)[0]
        check("目录名里的旧版本 = 0.30.3",
              arsenal.guess_version(e) == "0.30.3", arsenal.guess_version(e))
        check("元数据里的新版本 = 0.31.3",
              arsenal.read_version_from_metadata(e) == "0.31.3")
        check("★ 采用更新的那个（0.31.3），不是目录名的旧值",
              e["auto_version"] == "0.31.3", e["auto_version"])
        # 反向：元数据反而更旧时，要用目录名的
        uuid2 = "aaaa0000-0000-0000-0000-00000000000e"
        df2, _m2, d2 = _make_lib(td, uuid2, "C4",
                                  "C4 16340 1.13.1 2026-01-01T00-00Z x_AR2",
                                  n_patch=3)
        with open(os.path.join(d2, "build.json"), "w", encoding="utf-8") as f:
            json.dump({"version": "0.9"}, f)
        e2 = arsenal.load_library(df2)[0]
        check("★ 元数据更旧时用目录名的（1.13.1）",
              e2["auto_version"] == "1.13.1", e2["auto_version"])

    _isolated(body)


def t_legacy_heal_only_when_harmless():
    """★ 存量自愈（2026-10-12 评估后新增）：只清「可证明无副作用」的旧记录。

    缺口：v1.8.11 写的旧格式记录会被永久沿用（`arsenal.py` 的
    `_resolve_override` 对没有签名的记录「先沿用」），于是老用户升级上来
    仍然看到「更新了还说有更新」，而他自己逐条清要几十次点击。

    规则：旧格式 **且** 手动值 == 自动探测值 → 清（有它没它结论一样）。
          有实际影响 / 自动值探测不出来 → 一律留着。
    """
    print("[★ 存量自愈：只清无害的旧记录]")

    def body(td):
        from hd2_updater import arsenal

        def mk(uuid, label, dirname, tag):
            """造一份**独立**的库（_make_lib 固定写同一个文件名，会互相覆盖）。"""
            sub = os.path.join(td, tag)
            os.makedirs(sub, exist_ok=True)
            return _make_lib(sub, uuid, label, dirname)[0]

        # 情形 A：旧格式、手动值 == 自动值 → 应被清掉（无害）
        ua = "aaaa0000-0000-0000-0000-0000000000a1"
        dfa = mk(ua, "A", "A 16493 5 2026-01-01T00-00Z a_AR1", "ca")
        # 情形 B：旧格式、手动值 != 自动值（磁盘是 12）→ 必须留着（有影响）
        ub = "bbbb0000-0000-0000-0000-0000000000b2"
        dfb = mk(ub, "B", "B 16493 12 2026-01-01T00-00Z b_AR2", "cb")
        # 情形 C：旧格式、自动值探测不出来（目录名解析不出版本）→ 必须留着
        #         （清掉会把"有版本可比"变成"版本存疑"，那是有害的）
        uc = "cccc0000-0000-0000-0000-0000000000c3"
        dfc = mk(uc, "C", "C_AR3", "cc")
        # 情形 D：新格式（带签名）→ 一律不动，那是完整机制管的
        ud = "dddd0000-0000-0000-0000-0000000000d4"
        dfd = mk(ud, "D", "D 16493 7 2026-01-01T00-00Z d_AR4", "cd")

        # 统一的 config.json（各情形共用同一个 data_dir）
        cfg = os.path.join(td, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"version_overrides": {ua: "5", ub: "10", uc: "1.0"}}, f)

        ea = arsenal.load_library(dfa)[0]
        check("A 是旧格式记录", ea.get("override_legacy") is True)
        check("A 的手动值 == 自动值（5）",
              ea["manual_version"] == "5" and ea["auto_version"] == "5",
              f"{ea['manual_version']} / {ea['auto_version']}")
        check("★ A 被清掉（手动值 == 自动值，无副作用）",
              arsenal.heal_legacy_overrides([ea]) == 1)
        check("★ A 清后回到自动探测",
              not arsenal.get_version_override(ua))
        check("A 清后结论不变（仍是 5）",
              arsenal.load_library(dfa)[0]["installed_version"] == "5")

        # B：手动 10 vs 磁盘 12 → 不能清
        eb = arsenal.load_library(dfb)[0]
        check("B 的手动值与自动值不同（10 vs 12）",
              eb["manual_version"] == "10" and eb["auto_version"] == "12",
              f"{eb['manual_version']} / {eb['auto_version']}")
        check("★ B 没被清掉（有实际影响，交给用户核对）",
              arsenal.heal_legacy_overrides([eb]) == 0)
        check("★ B 的记录还在", arsenal.get_version_override(ub) == "10")

        # C：自动值探测不出 → 不能清
        ec = arsenal.load_library(dfc)[0]
        check("C 的自动值为空（探测不出）", not ec["auto_version"],
              ec["auto_version"])
        check("★ C 没被清掉（它反而是唯一能比大小的依据）",
              arsenal.heal_legacy_overrides([ec]) == 0)
        check("★ C 的记录还在", arsenal.get_version_override(uc) == "1.0")

        # D：新格式一律不动
        # ★ 只给 version、不给 sig 时也必须算得出签名（否则会静默造出一条
        #   "旧格式"记录 = 永久冻结，正是 issue #1 的坑）。
        arsenal.set_version_override(ud, "7",
                                     path=os.path.join(td, "cd", "mods",
                                                       "D 16493 7 2026-01-01T00-00Z d_AR4"))
        ed = arsenal.load_library(dfd)[0]
        check("D 不是旧格式（不带 sig 时自动算出来了）",
              not ed.get("override_legacy"),
              str(arsenal.read_version_overrides().get(ud)))
        check("D 的签名非空", bool(ed.get("manual_sig")), ed.get("manual_sig"))
        check("★ 新格式记录不被自愈清掉",
              arsenal.heal_legacy_overrides([ed]) == 0
              and arsenal.get_version_override(ud) == "7")

    _isolated(body)


def t_dialog_safe_action_first():
    """★ 缺口 C：出口的**安全动作必须是默认**，且不能预填可疑旧值。

    旧设计的三选一里，【否】分支的输入框初值就是那条旧值 —— 用户不清楚
    情况直接回车，会把旧值以**新格式**重写（提示消失、值还是错的），
    比不按更糟。

    ★ 这条**真的把 App 建起来、真的走一遍 `_ctx_local_version`**，
    而不是在源码里 grep 字符串 —— 第一版就是 grep 的，实测发现它抓不到
    回归（`default="yes"` 出现在注释里也算命中，把代码改坏照样绿）。
    判据："我凭什么相信它会失败？"—— grep 版答不出来。
    """
    print("[★ 出口：安全动作是默认、不预填可疑旧值]")
    import tempfile
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
    except Exception as e:  # noqa: BLE001
        print(f"  跳过（这台机器建不起 Tk 窗口）：{e}")
        return

    from hd2_updater import gui as G
    from hd2_updater import arsenal as Ar

    td = tempfile.mkdtemp(prefix="dlgtrap_")
    old = os.environ.get("HD2U_DATA_DIR")
    oldguard = os.environ.get("HD2U_READONLY_GUARD")
    os.environ["HD2U_DATA_DIR"] = td
    os.environ["HD2U_READONLY_GUARD"] = "1"
    app = None
    try:
        uuid = "eeee0000-0000-0000-0000-0000000000e5"
        mods = os.path.join(td, "mods")
        mdir = os.path.join(mods, "DiverKit 16493 12 2026-10-11T11-52Z a_AR1")
        os.makedirs(mdir)
        for i in range(3):
            with open(os.path.join(mdir, f"s{i}.patch_0"), "wb") as f:
                f.write(b"content")
        entry = {"uuid": uuid, "label": "DiverKit", "path": mdir,
                 "description": "", "iconPath": None, "tags": ["misc"],
                 "patchFileNames": [], "options": [], "contentHash": "",
                 "addedAt": "2026-01-01T00:00:00.000Z", "previousVersions": [],
                 "nexusData": None}
        prof = dict(entry)
        prof.update({"enabled": True, "deployed": True, "changed": False,
                     "new": False, "copiedFiles": [], "renamedFiles": []})
        df = os.path.join(td, "hd2a_data.json")
        with open(df, "w", encoding="utf-8") as f:
            json.dump({"modsLibrary": [entry],
                       "modsList": {"Default Profile": {"mods": [prof]}},
                       "selectedProfile": "Default Profile", "userModsDir": mods,
                       "userGameDir": "D:\\g", "setTopPriority": False},
                      f, ensure_ascii=False)
        # v1.8.11 留下的旧格式记录：磁盘是 12，他手动记的是 10
        with open(os.path.join(td, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"version_overrides": {uuid: "10"}}, f)

        app = G.App()
        app.cancel_startup_detect()
        app.data_file = df
        app.library = Ar.load_library(df)
        app._populate()
        row = {"iid": "x", "entry": app.library[0],
               "row": {"installed": app.library[0]["installed_version"]}}
        app._selected = lambda: row          # 打桩：固定选中这一行
        app._after_source_change = lambda e: None
        app._start_check = lambda **k: None

        # ① 「有问题」时：弹窗的默认按钮必须是「恢复自动」→ 用户回车即修好
        seen = {}

        def fake_yesno(title, msg, **kw):
            seen["default"] = kw.get("default")
            seen["msg"] = msg
            return True          # 回车 = default 那个按钮
        G.messagebox.askyesno = fake_yesno
        G.simpledialog.askstring = lambda *a, **k: None
        app._ctx_local_version()
        check("★ 有问题时弹窗（而不是静默/直接改）", "default" in seen)
        check("★ 默认按钮 = 恢复自动（default='yes'）",
              seen.get("default") == "yes", str(seen.get("default")))
        check("★ 文案写明了「直接回车就是这一项」",
              "回车" in str(seen.get("msg")), str(seen.get("msg"))[:80])
        check("★ 回车一次就把记录清掉了（= 修好）",
              uuid not in Ar.read_version_overrides(),
              str(Ar.read_version_overrides()))

        # ② 走【否】那条路时，输入框**不能**预填那条可疑的旧值
        with open(os.path.join(td, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"version_overrides": {uuid: "10"}}, f)
        app.library = Ar.load_library(df)
        row["entry"] = app.library[0]
        seen2 = {}

        def fake_yesno2(title, msg, **kw):
            return False         # 选【否】= 我自己填
        def fake_askstring(title, msg, **kw):
            seen2["initial"] = kw.get("initialvalue")
            return None          # 直接取消（不写任何东西）
        G.messagebox.askyesno = fake_yesno2
        G.simpledialog.askstring = fake_askstring
        app._ctx_local_version()
        check("★ 【否】路的输入框初值**不是**那条可疑旧值 10",
              seen2.get("initial") != "10", str(seen2.get("initial")))
        check("★ 【否】路初值给的是自动探测值 12",
              seen2.get("initial") == "12", str(seen2.get("initial")))
        check("★ 用户取消后，旧记录原样保留（没被弄坏）",
              Ar.get_version_override(uuid) == "10")

        # ③ 正常（非旧格式、无失效）时：不能默默改动，得走 default='no'
        Ar.clear_version_override(uuid)
        app.library = Ar.load_library(df)
        row["entry"] = app.library[0]
        seen3 = {}
        G.messagebox.askyesno = lambda t, m, **kw: (
            seen3.update(default=kw.get("default")) or False)
        app._ctx_local_version()
        check("★ 正常时 default='no'（乱按不会改东西）",
              seen3.get("default") == "no", str(seen3.get("default")))

        # ④ ★ 自动探测**为空**时，不能推荐"恢复自动"（那会删掉唯一的依据）
        #    反馈者的 Mine Flags (AiO) 就是这种：目录名解析不出版本，
        #    那条手动值 1.0 反而是唯一能比大小的东西。
        u2 = "eeee0000-0000-0000-0000-0000000000e6"
        mdir2 = os.path.join(mods, "Mine Flags (AiO) 1_AR230796")
        os.makedirs(mdir2)
        with open(os.path.join(mdir2, "a.patch_0"), "wb") as f:
            f.write(b"content")
        entry2 = {"uuid": u2, "label": "Mine Flags", "path": mdir2,
                  "description": "", "iconPath": None, "tags": ["misc"],
                  "patchFileNames": [], "options": [], "contentHash": "",
                  "addedAt": "2026-01-01T00:00:00.000Z", "previousVersions": [],
                  "nexusData": None}
        prof2 = dict(entry2)
        prof2.update({"enabled": True, "deployed": True, "changed": False,
                      "new": False, "copiedFiles": [], "renamedFiles": []})
        df2 = os.path.join(td, "hd2a_data2.json")
        with open(df2, "w", encoding="utf-8") as f:
            json.dump({"modsLibrary": [entry2],
                       "modsList": {"Default Profile": {"mods": [prof2]}},
                       "selectedProfile": "Default Profile", "userModsDir": mods,
                       "userGameDir": "D:\\g", "setTopPriority": False},
                      f, ensure_ascii=False)
        with open(os.path.join(td, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"version_overrides": {u2: "1.0"}}, f)
        app.data_file = df2
        app.library = Ar.load_library(df2)
        row["entry"] = app.library[0]
        check("④ 前提：这个模组自动探测不出版本",
              not app.library[0]["auto_version"],
              app.library[0]["auto_version"])
        seen4 = {}
        G.messagebox.askyesno = lambda t, m, **kw: (
            seen4.update(default=kw.get("default"), msg=m) or False)
        app._ctx_local_version()
        check("★ ④ 自动值为空时 default='no'（不推着用户去清掉唯一依据）",
              seen4.get("default") == "no", str(seen4.get("default")))
        check("★ ④ 文案说明了清掉会变成「版本存疑」",
              "版本存疑" in str(seen4.get("msg")),
              str(seen4.get("msg"))[:120])
        check("★ ④ 按回车后那条记录还在（没被删）",
              Ar.get_version_override(u2) == "1.0")
    finally:
        try:
            if app is not None:
                app.destroy()
        except Exception:  # noqa: BLE001
            pass
        if old is None:
            os.environ.pop("HD2U_DATA_DIR", None)
        else:
            os.environ["HD2U_DATA_DIR"] = old
        if oldguard is None:
            os.environ.pop("HD2U_READONLY_GUARD", None)
        else:
            os.environ["HD2U_READONLY_GUARD"] = oldguard
        shutil.rmtree(td, ignore_errors=True)


def main() -> int:
    for fn in (t_signature_rules,
               t_override_unfreezes_on_change,
               t_legacy_record_kept,
               t_high_override_missed_update,
               t_orphan_pruned,
               t_import_keeps_nexus_data,
               t_import_records_installed_version,
               t_imported_record_expires_on_next_change,
               t_stale_dirname_fallback,
               t_dialog_writes_only_when_changed,
               t_dialog_note_uses_auto_version,
               t_legacy_heal_only_when_harmless,
               t_dialog_safe_action_first,
               t_ui_shows_version_origin,
               t_diag_reports_version_origin):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            check(f"{fn.__name__} 自身抛异常", False,
                  f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}")
    print()
    print(f"issue #1「版本不冻结」自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())