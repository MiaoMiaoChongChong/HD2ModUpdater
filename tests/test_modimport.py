# -*- coding: utf-8 -*-
"""判定 modimport + upsert_library_mod 的自测（全部在临时目录里跑）。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 护栏：只允许写临时目录
os.environ["HD2U_READONLY_GUARD"] = "1"

from hd2_updater import arsenal_sync as S  # noqa: E402
from hd2_updater import modimport as M  # noqa: E402

# 测试不能依赖"本机此刻有没有开着 Arsenal" —— 统一打桩成"没开"，
# 需要验证护栏的那条用例再单独把它改成 True。
_REAL_RUNNING = S.arsenal_running
S.arsenal_running = lambda *a, **k: False

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


GUID = "11112222-3333-4444-5555-666677778888"
P = b"9ba626afa44a3aa3"


def make_zip(path: str, files: dict, wrap: str = "") -> str:
    """files: {相对路径: 内容}"""
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in files.items():
            zf.writestr(f"{wrap}{rel}" if wrap else rel, data)
    return path


def simple_manifest(guid=GUID, name="测试模组", options=None, icon="cover.png"):
    m = {"Version": 1, "Guid": guid, "Name": name, "Description": "说明文字"}
    if icon:
        m["IconPath"] = icon
    if options is not None:
        m["Options"] = options
    return json.dumps(m, ensure_ascii=False).encode()


def fresh_env():
    root = tempfile.mkdtemp(prefix="hd2u_imp_")
    mods = os.path.join(root, "mods")
    os.makedirs(mods, exist_ok=True)
    data = os.path.join(root, "hd2a_data.json")
    json.dump({"selectedProfile": "default", "userModsDir": mods,
               "modsList": {"default": {"mods": []}}, "modsLibrary": []},
              open(data, "w", encoding="utf-8"), ensure_ascii=False)
    return root, data, mods


# ---------------------------------------------------------------- 基本导入
def t_import_simple():
    print("[导入：最简单的单文件 mod]")
    root, data, mods = fresh_env()
    try:
        z = make_zip(os.path.join(root, "MyMod-v1.zip"), {
            "manifest.json": simple_manifest(),
            "cover.png": b"PNGDATA",
            f"{P.decode()}.patch_0": b"PATCH-CONTENT",
        })
        r = M.import_zip(data, z, mods_dir=mods)
        check("导入成功", r["added"] is True, str(r))
        check("uuid 取自 manifest.Guid", r["uuid"] == GUID, str(r["uuid"]))
        check("产生了备份", os.path.isfile(r["backup"]))
        j = json.load(open(data, encoding="utf-8-sig"))
        check("库里多了一条", len(j["modsLibrary"]) == 1, str(len(j["modsLibrary"])))
        e = j["modsLibrary"][0]
        check("12 个字段齐全",
              sorted(e) == sorted(S.LIBRARY_FIELDS), str(sorted(e)))
        check("label 来自 Name", e["label"] == "测试模组", e["label"])
        check("description 来自 manifest", e["description"] == "说明文字")
        check("tags 是 ['misc']", e["tags"] == ["misc"], str(e["tags"]))
        check("patchFileNames 为空", e["patchFileNames"] == [])
        check("nexusData 为 None（不猜）", e["nexusData"] is None)
        check("previousVersions 为空", e["previousVersions"] == [])
        check("addedAt 是 ISO+Z 形状",
              e["addedAt"].endswith("Z") and "T" in e["addedAt"], e["addedAt"])
        check("path 在 mods 目录下",
              os.path.dirname(e["path"]) == mods, e["path"])
        check("path 文件夹真的建出来了", os.path.isdir(e["path"]))
        check("patch 文件已解出",
              os.path.isfile(os.path.join(e["path"], f"{P.decode()}.patch_0")))
        # contentHash：单槽位必须精确等于 patch 内容 sha256
        want = hashlib.sha256(b"PATCH-CONTENT").hexdigest()
        check("单槽位 contentHash 精确", e["contentHash"] == want,
              f"{e['contentHash'][:16]} vs {want[:16]}")
        # iconPath 指向真实存在的文件
        check("iconPath 指向存在的图", e["iconPath"] and os.path.isfile(e["iconPath"]),
              str(e["iconPath"]))
        # 没进配置档
        check("没有自动进配置档", j["modsList"]["default"]["mods"] == [])
        # 不留临时目录
        check("没留下 staging 目录",
              not [n for n in os.listdir(mods) if n.startswith(M.STAGE_PREFIX)],
              str(os.listdir(mods)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 选项映射
def t_import_options():
    print("[导入：选项/子选项映射]")
    root, data, mods = fresh_env()
    try:
        opts = [{
            "Name": "核心 Core", "Description": "d", "Image": "a.png",
            "Include": ["Core"],
            "SubOptions": [
                {"Name": "子 A", "Description": "sa", "Include": ["Core/A"]},
                {"Name": "子 B", "Description": "sb", "Include": ["Core/B"]},
            ],
        }, {"Name": "另一项", "Description": "", "Include": ["Other"]}]
        z = make_zip(os.path.join(root, "OptMod.zip"), {
            "manifest.json": simple_manifest(options=opts),
            "a.png": b"P",
            "Core/A/x.patch_0": b"A",
            "Core/B/x.patch_0": b"B",
            "Other/x.patch_0": b"O",
        })
        M.import_zip(data, z, mods_dir=mods)
        e = json.load(open(data, encoding="utf-8-sig"))["modsLibrary"][0]
        check("选项数正确", len(e["options"]) == 2, str(len(e["options"])))
        o0 = e["options"][0]
        check("选项键与 Arsenal 一致",
              sorted(o0) == ["description", "enabled", "iconPath", "include",
                             "name", "suboptions"], str(sorted(o0)))
        check("name 映射", o0["name"] == "核心 Core", o0["name"])
        check("include 映射", o0["include"] == ["Core"], str(o0["include"]))
        check("enabled 默认 True", o0["enabled"] is True)
        check("image 键被丢掉（不残留 image）", "image" not in o0, str(sorted(o0)))
        check("图标解析成绝对路径",
              o0["iconPath"] and os.path.isfile(o0["iconPath"]), str(o0["iconPath"]))
        check("子选项用 suboptions（全小写）", len(o0["suboptions"]) == 2,
              str(len(o0["suboptions"])))
        check("子选项 include 保留层级",
              o0["suboptions"][0]["include"] == ["Core/A"],
              str(o0["suboptions"][0]["include"]))
        check("无图选项 iconPath 为 None", e["options"][1]["iconPath"] is None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 覆盖
def t_import_overwrite():
    print("[导入：重复导入 = 覆盖旧的（与 Arsenal 行为一致）]")
    root, data, mods = fresh_env()
    try:
        z1 = make_zip(os.path.join(root, "Mod-v1.zip"), {
            "manifest.json": simple_manifest(name="模组 v1"),
            f"{P.decode()}.patch_0": b"V1",
        })
        r1 = M.import_zip(data, z1, mods_dir=mods)
        p1 = r1["path"]
        h1 = json.load(open(data, encoding="utf-8-sig"))["modsLibrary"][0]["contentHash"]

        z2 = make_zip(os.path.join(root, "Mod-v2.zip"), {
            "manifest.json": simple_manifest(name="模组 v2"),
            f"{P.decode()}.patch_0": b"V2-NEW",
        })
        r2 = M.import_zip(data, z2, mods_dir=mods)
        check("第二次是覆盖不是新增", r2["added"] is False and r2["replaced"] is True,
              str(r2))
        j = json.load(open(data, encoding="utf-8-sig"))
        check("库里仍然只有一条", len(j["modsLibrary"]) == 1,
              str(len(j["modsLibrary"])))
        e = j["modsLibrary"][0]
        check("label 更新成新的", e["label"] == "模组 v2", e["label"])
        check("contentHash 变了", e["contentHash"] != h1)
        check("复用原来的目录（不新建文件夹）", e["path"] == p1, f"{e['path']} vs {p1}")
        check("内容确实换成新的",
              open(os.path.join(e["path"], f"{P.decode()}.patch_0"), "rb").read()
              == b"V2-NEW")
        check("旧内容留了备份目录（不直接删）",
              r2["old_dir"] and os.path.isdir(r2["old_dir"]), str(r2.get("old_dir")))
        check("旧备份里还是旧内容",
              open(os.path.join(r2["old_dir"], f"{P.decode()}.patch_0"), "rb").read()
              == b"V1")
        check("旧备份不在库里被引用",
              r2["old_dir"] not in [x["path"] for x in j["modsLibrary"]])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 配置档同步
def t_profile_sync():
    print("[导入：该模组已在配置档里 → 同步那份副本]")
    root, data, mods = fresh_env()
    try:
        z = make_zip(os.path.join(root, "M.zip"), {
            "manifest.json": simple_manifest(name="同步测试"),
            f"{P.decode()}.patch_0": b"X",
        })
        r = M.import_zip(data, z, mods_dir=mods)
        j = json.load(open(data, encoding="utf-8-sig"))
        # 手工把它放进配置档（模拟用户已经在用这个 mod 的旧版本）
        entry = dict(j["modsLibrary"][0])
        entry.update({"enabled": True, "deployed": True, "changed": False,
                      "new": False, "copiedFiles": [], "renamedFiles": []})
        j["modsList"]["default"]["mods"] = [entry]
        json.dump(j, open(data, "w", encoding="utf-8"), ensure_ascii=False)

        z2 = make_zip(os.path.join(root, "M2.zip"), {
            "manifest.json": simple_manifest(name="同步测试 v2"),
            f"{P.decode()}.patch_0": b"Y",
        })
        r2 = M.import_zip(data, z2, mods_dir=mods)
        check("报告了同步配置档", r2["profile_updated"] is True, str(r2))
        j2 = json.load(open(data, encoding="utf-8-sig"))
        pm = j2["modsList"]["default"]["mods"][0]
        check("配置档副本的 label 也更新了", pm["label"] == "同步测试 v2", pm["label"])
        check("配置档 path 与库一致",
              pm["path"] == j2["modsLibrary"][0]["path"],
              f"{pm['path']} vs {j2['modsLibrary'][0]['path']}")
        check("changed 置 True（提示要重新部署）", pm["changed"] is True)
        check("enabled 原样保留", pm["enabled"] is True)
        check("deployed 原样保留（游戏里仍是旧内容，但确实还在用）",
              pm["deployed"] is True)
        check("copiedFiles/renamedFiles 保留", pm["copiedFiles"] == []
              and pm["renamedFiles"] == [])
        check("配置档条目字段数不变", len(pm) == len(entry), str(len(pm)))
        check("库仍是一条", len(j2["modsLibrary"]) == 1)
        check("配置档仍是一条", len(j2["modsList"]["default"]["mods"]) == 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 拒绝非法输入
def t_reject_bad():
    print("[导入：不该接受的要明确拒绝]")
    root, data, mods = fresh_env()
    try:
        # 没有 patch 文件
        z1 = make_zip(os.path.join(root, "src.zip"), {"README.md": b"hi"})
        try:
            M.import_zip(data, z1, mods_dir=mods)
            check("无 patch 文件应拒绝", False)
        except M.ModImportError as e:
            check("无 patch 文件应拒绝", ".patch_" in str(e) or "模组文件" in str(e),
                  str(e))
        # 非 zip
        p = os.path.join(root, "a.rar")
        open(p, "wb").write(b"x")
        try:
            M.import_zip(data, p, mods_dir=mods)
            check("非 zip 应拒绝", False)
        except M.ModImportError as e:
            check("非 zip 应拒绝", "zip" in str(e).lower(), str(e))
        # 损坏的 zip
        z2 = os.path.join(root, "broken.zip")
        open(z2, "wb").write(b"not a zip at all")
        try:
            M.import_zip(data, z2, mods_dir=mods)
            check("损坏的 zip 应拒绝", False)
        except M.ModImportError as e:
            check("损坏的 zip 应拒绝", "打不开" in str(e) or "损坏" in str(e), str(e))
        # 目录穿越
        z3 = os.path.join(root, "evil.zip")
        with zipfile.ZipFile(z3, "w") as zf:
            zf.writestr("../evil.patch_0", b"x")
        try:
            M.import_zip(data, z3, mods_dir=mods)
            check("路径穿越应拒绝", False)
        except M.ModImportError as e:
            check("路径穿越应拒绝", "不安全" in str(e) or "越界" in str(e), str(e))
        # 拒绝之后库必须没变
        j = json.load(open(data, encoding="utf-8-sig"))
        check("被拒后库没变", j["modsLibrary"] == [], str(j["modsLibrary"]))
        check("被拒后没留下 staging",
              not [n for n in os.listdir(mods) if n.startswith(M.STAGE_PREFIX)],
              str(os.listdir(mods)))
        check("被拒后没留下新文件夹",
              [n for n in os.listdir(mods)] == [], str(os.listdir(mods)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 形态兼容
def t_zip_shapes():
    print("[导入：ZIP 形态兼容（套一层目录 / 无 manifest / 末尾无 figure）]")
    root, data, mods = fresh_env()
    try:
        # ① 内容套在一层目录里
        z1 = make_zip(os.path.join(root, "wrapped.zip"), {
            "manifest.json": simple_manifest(name="套目录"),
            f"{P.decode()}.patch_0": b"W",
        }, wrap="MyMod/")
        r1 = M.import_zip(data, z1, mods_dir=mods)
        check("套一层目录也能导入", r1["added"] is True, str(r1))
        e1 = json.load(open(data, encoding="utf-8-sig"))["modsLibrary"][0]
        check("内容解到了根上（剥掉了那层目录）",
              os.path.isfile(os.path.join(e1["path"], f"{P.decode()}.patch_0")),
              str(sorted(os.listdir(e1["path"]))))

        # ② 没有 manifest → uuid 自动生成、label 用文件名
        z2 = make_zip(os.path.join(root, "NoManifest-Mod.zip"), {
            f"{P.decode()}.patch_0": b"N",
            "pic.png": b"P",
        })
        r2 = M.import_zip(data, z2, mods_dir=mods)
        j = json.load(open(data, encoding="utf-8-sig"))
        e2 = next(x for x in j["modsLibrary"] if x["uuid"] == r2["uuid"])
        check("无 manifest 也能导入", r2["added"] is True)
        check("uuid 是自动生成的（不是 manifest 的）",
              r2["uuid_from_manifest"] is False and e2["uuid"] != GUID)
        check("label 用压缩包文件名", e2["label"] == "NoManifest-Mod", e2["label"])
        check("没有选项时 options 为空", e2["options"] == [], str(e2["options"]))
        check("自挑了目录里的 png 当图标",
              e2["iconPath"] and e2["iconPath"].endswith("pic.png"), str(e2["iconPath"]))
        check("两条互不影响", len(j["modsLibrary"]) == 2)

        # ③ 文件名里有空格等（safe_folder_name）
        check("非法字符被替换",
              "/" not in M.safe_folder_name("a/b:c*d") and
              M.safe_folder_name("a/b:c*d").startswith("a_b"), M.safe_folder_name("a/b:c*d"))
        check("空名字有兜底", M.safe_folder_name("   ") == "mod")
        check("超长名字被截断", len(M.safe_folder_name("x" * 200)) <= 110,
              str(len(M.safe_folder_name("x" * 200))))
        # ★ 限长不能把 N 网命名的尾巴切掉（那条尾巴是识别来源用的）
        long_nex = ("Very Long Mod Name " + "x" * 90 +
                    " 17165 2 2026-10-10T15-18Z QXOHm3L7i")
        cut = M.safe_folder_name(long_nex)
        check("截断后仍能解析出 N 网编号",
              (M.parse_nexus_name(cut) or {}).get("mod_id") == "17165",
              cut)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 批量 + 拖放解析
def t_batch_and_drop():
    print("[批量导入 + 拖放路径解析]")
    root, data, mods = fresh_env()
    try:
        z1 = make_zip(os.path.join(root, "A.zip"), {
            "manifest.json": simple_manifest(guid="aaaa1111-0000-0000-0000-000000000001",
                                             name="A"),
            f"{P.decode()}.patch_0": b"A"})
        z2 = make_zip(os.path.join(root, "B.zip"), {"README.md": b"no patch"})
        z3 = make_zip(os.path.join(root, "C.zip"), {
            "manifest.json": simple_manifest(guid="cccc1111-0000-0000-0000-000000000003",
                                             name="C"),
            f"{P.decode()}.patch_0": b"C"})
        r = M.import_paths(data, [z1, z2, z3], mods_dir=mods)
        check("两个成功", len(r["done"]) == 2, str([p for p, _ in r["done"]]))
        check("一个失败但不影响其它", len(r["failed"]) == 1, str(r["failed"]))
        check("失败的是 B", r["failed"][0][0].endswith("B.zip"))
        j = json.load(open(data, encoding="utf-8-sig"))
        check("成功的两个都在库里", len(j["modsLibrary"]) == 2,
              str(len(j["modsLibrary"])))

        # tkdnd 传来的路径串（含空格的用 {} 包起来）
        s = r"{C:\a b\My Mod.zip} D:\x\Plain.zip {E:\y\z z.zip}"
        got = M.split_drop_paths(s)
        check("拆出 3 个路径", len(got) == 3, str(got))
        check("带空格的完整保留", got[0] == r"C:\a b\My Mod.zip", str(got))
        check("不带括号的也认", got[1] == r"D:\x\Plain.zip", str(got))
        check("空串返回空表", M.split_drop_paths("") == [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 运行中拒写
def t_running_guard():
    print("[护栏：Arsenal 运行中拒绝写库]")
    root, data, mods = fresh_env()
    try:
        z = make_zip(os.path.join(root, "M.zip"), {
            "manifest.json": simple_manifest(),
            f"{P.decode()}.patch_0": b"X"})
        orig = S.arsenal_running
        S.arsenal_running = lambda *a, **k: True
        try:
            r = M.import_paths(data, [z], mods_dir=mods)
            check("运行中导入应失败", len(r["failed"]) == 1, str(r))
            check("失败原因说明正在运行", "正在运行" in r["failed"][0][1],
                  r["failed"][0][1])
            check("库里没被写进东西",
                  json.load(open(data, encoding="utf-8-sig"))["modsLibrary"] == [])
        finally:
            S.arsenal_running = orig
        # 恢复成"没开"的桩，别影响后面的用例
        S.arsenal_running = lambda *a, **k: False
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 写入护栏
def t_write_guard_upsert():
    print("[护栏：upsert_library_mod 的字段校验与外部路径防护]")
    root, data, mods = fresh_env()
    try:
        # 缺字段必须拒绝
        try:
            S.upsert_library_mod(data, {"uuid": "u1", "path": "x"}, force=True)
            check("缺字段应拒绝", False)
        except S.SyncError as e:
            check("缺字段应拒绝", "缺少字段" in str(e), str(e))
        # 没 uuid
        e = {k: None for k in S.LIBRARY_FIELDS}
        e["uuid"] = ""
        try:
            S.upsert_library_mod(data, e, force=True)
            check("无 uuid 应拒绝", False)
        except S.SyncError as ex:
            check("无 uuid 应拒绝", "没有 uuid" in str(ex), str(ex))
        # 正常写入一条（追加）
        e2 = {k: None for k in S.LIBRARY_FIELDS}
        e2.update({"uuid": "u-1", "path": os.path.join(mods, "m1"),
                   "label": "m1", "description": "", "tags": ["misc"],
                   "patchFileNames": [], "options": [], "contentHash": "h",
                   "addedAt": "2026-01-01T00:00:00.000Z", "previousVersions": [],
                   "iconPath": None, "nexusData": None})
        r1 = S.upsert_library_mod(data, e2, force=True)
        check("新增成功", r1["added"] is True)
        # 再用同 uuid 写 → 覆盖
        e3 = dict(e2, label="m1-new")
        r2 = S.upsert_library_mod(data, e3, force=True)
        check("同 uuid 覆盖", r2["added"] is False and r2["replaced"] is True)
        j = json.load(open(data, encoding="utf-8-sig"))
        check("没有变成两条", len(j["modsLibrary"]) == 1, str(len(j["modsLibrary"])))
        check("内容被更新", j["modsLibrary"][0]["label"] == "m1-new")
        check("library_uuids 能列出来", S.library_uuids(data) == ["u-1"],
              str(S.library_uuids(data)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_nexus_recognition():
    print("[N 网来源：从压缩包名认出编号（回归：曾经被改名丢掉）]")
    root, data, mods = fresh_env()
    try:
        # ① 解析：用你库里真实存在的 5 个 N 网命名逐个核对
        samples = [
            ("G60-Lock-Line-V1.2.2 17165 2 2026-10-10T15-18Z QXOHm3L7i.zip",
             "17165", "2"),
            ("Glowing Supply Icons v4.2 but it glows again 9120 4.2 "
             "2026-09-01T02-37Z IzUPRha2W.zip", "9120", "4.2"),
            ("Helmet Headlamp 16320 1.3.0 2026-10-01T15-03Z X1m0uaeMD.zip",
             "16320", "1.3.0"),
            ("HD2 C4 Quick Actions 16340 1.13.1 2026-10-05T09-23Z 2Gg9W5PUj.zip",
             "16340", "1.13.1"),
            ("Bingus Shared Loader 16292 19 2026-10-05T00-28Z eqrMD7Qqb.zip",
             "16292", "19"),
        ]
        ok = True
        for name, mid, ver in samples:
            got = M.parse_nexus_name(name)
            if not got or got["mod_id"] != mid or got["version"] != ver:
                ok = False
                print(f"     解析失败: {name} → {got}")
        check("5 个真实 N 网命名全部解析正确", ok)
        check("非 N 网命名解析为 None",
              M.parse_nexus_name("HD2-G60-Smart-Targeting-0.1-beta.1.zip") is None)
        check("空名返回 None", M.parse_nexus_name("") is None)

        # ② 文件夹名必须用**压缩包原名**（保住那串编号），显示名仍用 manifest
        z = os.path.join(root,
                         "G60-Lock-Line-V1.2.2 17165 2 2026-10-10T15-18Z QXOHm3L7i.zip")
        make_zip(z, {"manifest.json": simple_manifest(name="G60 锁定指示线 V1.2.2"),
                     f"{P.decode()}.patch_0": b"X"})
        # 打桩 N 网查询：返回一个 files.json，其中 file_id 用真实值 68188
        calls = {}

        def fake_get(url, headers=None, **kw):
            calls["url"] = url
            return {"files": [
                {"file_id": 68188, "version": "2", "uploaded_timestamp": 1791645520,
                 "category_name": "MAIN", "name": "G60 Lock Line"},
                {"file_id": 11111, "version": "1.5", "uploaded_timestamp": 1700000000,
                 "category_name": "MAIN", "name": "old"},
            ]}

        real_get = M.Pv.http_get_json
        M.Pv.http_get_json = fake_get
        try:
            r = M.import_zip(data, z, mods_dir=mods, api_key="fake-key")
        finally:
            M.Pv.http_get_json = real_get

        check("文件夹名保留了 N 网命名",
              "17165" in r["folder"] and "2026-10-10T15-18Z" in r["folder"],
              r["folder"])
        check("文件夹名不再直接用中文显示名",
              "锁定指示线" not in r["folder"], r["folder"])
        check("显示名仍来自 manifest（用户看到的没变）",
              r["label"] == "G60 锁定指示线 V1.2.2", r["label"])
        check("查了正确的 modId 端点", "mods/17165/files.json" in calls.get("url", ""),
              calls.get("url", ""))
        j = json.load(open(data, encoding="utf-8-sig"))
        e = j["modsLibrary"][0]
        nd = e["nexusData"] or {}
        check("nexusData 写入了 modId", nd.get("modId") == "17165", str(nd))
        check("nexusData 的 fileId 与 N 网一致（68188）",
              nd.get("fileId") == "68188", str(nd))
        check("nexusData 的字段与 Arsenal 的形状一致",
              sorted(nd) == ["fileId", "modId", "updateTimestamp", "version"],
              str(sorted(nd)))
        check("按版本挑对了文件（没选 1.5 那个）", nd.get("version") == "2", str(nd))

        # ③ ★ 最关键：matcher 真的能从新目录名认出 N 网来源
        from hd2_updater import matcher
        ent = {"dirname": os.path.basename(e["path"]), "label": e["label"],
               "nexus_mod_id": str(nd.get("modId") or ""), "path": e["path"],
               "uuid": e["uuid"]}
        cands = matcher.candidates_for(ent, use_github_search=False, ayaka_index=[])
        check("matcher 认出 N 网来源",
              any(c.get("provider") == "nexus" and c.get("id") == "17165"
                  for c in cands),
              str([(c.get("provider"), c.get("id")) for c in cands]))

        # ④ 回归：**旧命名**必须复现"一个候选都没有"（这就是用户报的症状）
        ent_old = dict(ent, dirname="G60 锁定指示线 V1.2.2_AR251352",
                       nexus_mod_id="")
        cands_old = matcher.candidates_for(ent_old, use_github_search=False,
                                          ayaka_index=[])
        check("旧命名（丢了编号）确实认不出来（复现症状）",
              not [c for c in cands_old if c.get("provider") == "nexus"],
              str([(c.get("provider"), c.get("id")) for c in cands_old]))

        # ⑤ 查不到 fileId 时：不写 nexusData，但**识别照样能用**
        root2, data2, mods2 = fresh_env()
        z2 = os.path.join(root2,
                          "Another Mod 17777 3 2026-10-11T01-02Z aaaBBccdd.zip")
        make_zip(z2, {"manifest.json": simple_manifest(name="另一个模组"),
                      f"{P.decode()}.patch_0": b"Y"})
        r2 = M.import_zip(data2, z2, mods_dir=mods2, api_key="")   # 没 key
        e2 = json.load(open(data2, encoding="utf-8-sig"))["modsLibrary"][0]
        check("没 key 时不写 nexusData", e2["nexusData"] is None, str(e2["nexusData"]))
        check("但报告了为什么没补上", "API" in (r2.get("nexus_why") or "")
              or r2.get("nexus_why"), str(r2.get("nexus_why")))
        check("文件夹名仍保留编号（识别不受影响）",
              "17777" in r2["folder"], r2["folder"])
        ent2 = {"dirname": os.path.basename(e2["path"]), "label": e2["label"],
                "nexus_mod_id": "", "path": e2["path"], "uuid": e2["uuid"]}
        c2 = matcher.candidates_for(ent2, use_github_search=False, ayaka_index=[])
        check("matcher 仍能靠目录名认出它",
              any(c.get("provider") == "nexus" and c.get("id") == "17777"
                  for c in c2),
              str([(c.get("provider"), c.get("id")) for c in c2]))
        shutil.rmtree(root2, ignore_errors=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    for fn in (t_import_simple, t_import_options, t_import_overwrite,
               t_profile_sync, t_reject_bad, t_zip_shapes, t_batch_and_drop,
               t_running_guard, t_write_guard_upsert, t_nexus_recognition):
        fn()
    print()
    print(f"模组导入自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
