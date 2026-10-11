# -*- coding: utf-8 -*-
"""「下载 / 导入」页自测：把"下好了"和"用上了"之间那一环补上。

全部离线、只写临时目录。覆盖：
· records 的三态（待导入 / 已导入 / 文件已不在）
· 导入并更新：真的覆盖同 uuid 的条目、旧内容留备份
· 没有 manifest 的包 → 必须先问清楚（不能静默新增一条）
· 导入后记录变「已导入」，检查页不再挂「已下载」
· 面板渲染、按钮可用性、检查页双击跳过来
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HD2U_READONLY_GUARD"] = "1"

PASS = 0
FAIL = 0
FAILS: list[str] = []

P = "9ba626afa44a3aa3"
GUID = "11111111-2222-3333-4444-555555555555"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
        print(f"  FAIL: {name} {extra}")


def make_zip(path: str, files: dict) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return path


def manifest(guid=GUID, name="测试模组", version=1):
    return json.dumps({"Version": 1, "Guid": guid, "Name": name,
                       "Description": "说明", "IconPath": "cover.png"},
                      ensure_ascii=False).encode()


def fresh_env(mods_slots=1):
    """假世界：一个模组库 + 一个已导入的模组 m0。"""
    root = tempfile.mkdtemp(prefix="hd2u_dl_")
    mods = os.path.join(root, "mods")
    os.makedirs(mods, exist_ok=True)
    game = os.path.join(root, "game")
    os.makedirs(os.path.join(game, "data"), exist_ok=True)
    open(os.path.join(game, "data", f"{P}.patch_0"), "wb").close()
    mdir = os.path.join(mods, f"测试模组_{GUID[:6]}")
    sd = os.path.join(mdir, "options", "O0")
    os.makedirs(sd, exist_ok=True)
    open(os.path.join(sd, f"{P}.patch_0"), "wb").write(b"OLD")
    data = os.path.join(root, "hd2a_data.json")
    lib = {"uuid": GUID, "path": mdir, "label": "测试模组", "description": "",
           "iconPath": None, "tags": [], "patchFileNames": [],
           "nexusData": None, "options": [], "contentHash": "",
           "addedAt": "", "previousVersions": []}
    json.dump({"selectedProfile": "default", "userModsDir": mods,
               "userGameDir": game, "deployedProfile": "default",
               "modsList": {"default": {"mods": [
                   dict(lib, enabled=True, deployed=True, changed=False,
                        new=False, copiedFiles=[], renamedFiles=[])]}},
               "modsLibrary": [lib]},
              open(data, "w", encoding="utf-8"), ensure_ascii=False)
    return root, data, mods, mdir


# ------------------------------------------------------------------ records
def t_records_states():
    print("[下载记录的三态 —— 解决“导入完还一直显示已下载”]")
    tmp = tempfile.mkdtemp(prefix="hd2u_rec_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        from hd2_updater import records as R
        z = os.path.join(tmp, "a.zip")
        open(z, "wb").write(b"PK")
        R.mark("k1", "v2", z, "github", "某模组")
        check("刚下载 = 待导入", R.state("k1") == "pending", R.state("k1"))
        check("待导入时算「已下载」", R.match("k1", "v2") is not None)

        R.mark_applied("k1")
        check("★ 导入后变「已导入」", R.state("k1") == "applied", R.state("k1"))
        check("★ 导入后不再算「已下载」（检查页不会一直挂着）",
              R.match("k1", "v2") is None)
        check("applied() 可查", R.applied("k1") is True)

        # 又下了一个新版本 → 应该重新变回"待导入"
        R.mark("k1", "v3", z, "github", "某模组")
        check("★ 重新下载会清掉“已导入”标记",
              R.state("k1") == "pending", R.state("k1"))

        os.remove(z)
        check("★ 文件没了 = 文件已不在", R.state("k1") == "missing",
              R.state("k1"))
        check("未知 key 返回空", R.state("nope") == "")

        check("all_records 能列出来", len(R.all_records()) == 1,
              str(len(R.all_records())))
        R.forget("k1")
        check("forget 之后没了", R.state("k1") == "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ 导入
def t_import_updates_same_uuid():
    print("[导入并更新：同一个 mod 的包 → 覆盖原来那条]")
    root, data, mods, mdir = fresh_env()
    tmp = tempfile.mkdtemp(prefix="hd2u_rec2_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        from hd2_updater import records as R
        from hd2_updater import modimport as M
        # 新版本的包（同 Guid → 同一个 mod，目录结构也与原来一致）
        z = make_zip(os.path.join(root, "测试模组-v2.zip"), {
            "manifest.json": manifest(),
            "cover.png": b"PNG",
            f"options/O0/{P}.patch_0": b"NEW-CONTENT",
        })
        R.mark(GUID, "v2", z, "nexus", "测试模组")
        check("导入前是待导入", R.state(GUID) == "pending")

        res = M.import_zip(data, z, mods_dir=mods)
        check("判成覆盖而不是新增", res.get("added") is False, str(res.get("added")))
        check("路径还是原来那个（没有新建重复条目）",
              res.get("path") == mdir, f"{res.get('path')} vs {mdir}")

        lib = json.load(open(data, encoding="utf-8-sig"))["modsLibrary"]
        check("★ 库条目没变多（还是 1 条）", len(lib) == 1, str(len(lib)))
        check("库条目的 uuid 没变", lib[0]["uuid"] == GUID)

        # 内容真的换了
        got = open(os.path.join(mdir, "options", "O0", f"{P}.patch_0"), "rb").read()
        check("★ 内容已被新版本替换", got == b"NEW-CONTENT", str(got))

        # 旧内容留了备份文件夹
        olds = [d for d in os.listdir(mods) if ".hd2u-old-" in d]
        check("★ 旧内容留了备份文件夹", len(olds) == 1, str(olds))

        R.mark_applied(GUID)
        check("★ 记成已导入", R.state(GUID) == "applied", R.state(GUID))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def t_no_manifest_needs_confirm():
    print("[没有 manifest 的包 → 必须先问清楚，不能静默多出一条]")
    root, data, mods, mdir = fresh_env()
    tmp = tempfile.mkdtemp(prefix="hd2u_rec3_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        from hd2_updater import gui as G
        from hd2_updater import records as R
        z = make_zip(os.path.join(root, "no-manifest.zip"), {
            f"{P}.patch_0": b"X",
        })
        R.mark(GUID, "v9", z, "nexus", "测试模组")

        asked = {"n": 0}

        class _MB:
            @staticmethod
            def askyesno(*a, **k):
                asked["n"] += 1
                return False          # 用户拒绝

            @staticmethod
            def showinfo(*a, **k):
                pass

            @staticmethod
            def showwarning(*a, **k):
                pass

            @staticmethod
            def showerror(*a, **k):
                pass

        real_mb = G.messagebox
        G.messagebox = _MB
        G.find_data_file = lambda explicit=None: (data, "测试")
        G.arsenal_running = lambda *a, **k: False
        app = G.App()
        app.update()
        try:
            app._show_page("downloads")
            panel = app._downloads_panel
            panel.tree.selection_set(GUID)
            panel._sync_buttons()
            panel._import_selected()
            check("★ 问过用户了", asked["n"] == 1, str(asked))
            lib = json.load(open(data, encoding="utf-8-sig"))["modsLibrary"]
            check("★ 用户拒绝后没有新增条目", len(lib) == 1, str(len(lib)))
            check("拒绝后记录仍是待导入", R.state(GUID) == "pending",
                  R.state(GUID))
            check("状态栏说明了取消", "取消" in panel.var_note.get(),
                  panel.var_note.get())
        finally:
            app.destroy()
            G.messagebox = real_mb
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def t_panel_render_and_guard():
    print("[面板渲染 + 文件不在时的拒绝 + 按钮可用性]")
    root, data, mods, mdir = fresh_env()
    tmp = tempfile.mkdtemp(prefix="hd2u_rec4_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        from hd2_updater import gui as G
        from hd2_updater import records as R
        z = make_zip(os.path.join(root, "ok.zip"), {
            "manifest.json": manifest(), f"{P}.patch_0": b"X"})
        R.mark(GUID, "v2", z, "nexus", "测试模组")
        gone = os.path.join(root, "gone.zip")
        R.mark("other-key", "v1", gone, "github", "另一个模组")

        real_mb = G.messagebox
        warned = {"n": 0}

        class _MB:
            @staticmethod
            def askyesno(*a, **k):
                return True

            @staticmethod
            def showinfo(*a, **k):
                pass

            @staticmethod
            def showwarning(*a, **k):
                warned["n"] += 1

            @staticmethod
            def showerror(*a, **k):
                warned["n"] += 1

        G.messagebox = _MB
        G.find_data_file = lambda explicit=None: (data, "测试")
        G.arsenal_running = lambda *a, **k: False
        app = G.App()
        app.update()
        try:
            app._show_page("downloads")
            panel = app._downloads_panel
            kids = list(panel.tree.get_children())
            check("两条记录都列出来了", len(kids) == 2, str(kids))
            check("文件在的显示「待导入」",
                  panel.tree.item(GUID, "values")[0] == "待导入",
                  str(panel.tree.item(GUID, "values")))
            check("★ 文件不在的显示「文件已不在」",
                  panel.tree.item("other-key", "values")[0] == "文件已不在",
                  str(panel.tree.item("other-key", "values")))
            check("显示了模组名",
                  panel.tree.item(GUID, "values")[1] == "测试模组",
                  str(panel.tree.item(GUID, "values")))
            check("★ 显示了现在装的版本这一列", len(panel.tree["columns"]) >= 5)

            # 文件不在的那条 → 点导入必须被拦住
            panel.tree.selection_set("other-key")
            panel._sync_buttons()
            panel._import_selected()
            check("★ 文件不在时拒绝导入并提示", warned["n"] == 1, str(warned))
            check("并说明文件不在了", "不在" in panel.var_note.get(),
                  panel.var_note.get())

            # 正常那条 → 导入成功、变已导入
            panel.tree.selection_set(GUID)
            panel._sync_buttons()
            panel._import_selected()
            check("★ 导入后显示「已导入」",
                  panel.tree.item(GUID, "values")[0] == "已导入",
                  str(panel.tree.item(GUID, "values")))
            check("导入后提示去部署页",
                  "部署" in panel.var_note.get(), panel.var_note.get())
            lib = json.load(open(data, encoding="utf-8-sig"))["modsLibrary"]
            check("库里仍然只有 1 条（是更新不是新增）", len(lib) == 1,
                  str(len(lib)))
            check("★ 记录已标成已导入", R.applied(GUID) is True)
        finally:
            app.destroy()
            G.messagebox = real_mb
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def t_check_page_jumps_here():
    print("[检查页「已下载」那行双击 → 跳到这一页并选中]")
    root, data, mods, mdir = fresh_env()
    tmp = tempfile.mkdtemp(prefix="hd2u_rec5_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        from hd2_updater import gui as G
        from hd2_updater import records as R
        z = make_zip(os.path.join(root, "ok.zip"), {
            "manifest.json": manifest(), f"{P}.patch_0": b"X"})
        R.mark(GUID, "v2", z, "nexus", "测试模组")
        G.find_data_file = lambda explicit=None: (data, "测试")
        G.arsenal_running = lambda *a, **k: False
        app = G.App()
        app.update()
        try:
            app._show_page("update")
            check("初始在更新页", app._current_page == "update")
            app._goto_downloads({"entry": {"uuid": GUID, "label": "测试模组"}})
            app.update()
            check("★ 跳到了下载页", app._current_page == "downloads",
                  app._current_page)
            panel = app._downloads_panel
            check("★ 并选中了对应那条", panel.tree.selection() == (GUID,),
                  str(panel.tree.selection()))
            # 没有记录的 uuid 跳过来也不能崩
            app._goto_downloads({"entry": {"uuid": "不存在", "label": "x"}})
            app.update()
            check("没有记录时也不崩，并给出说明",
                  "没有它的下载记录" in panel.var_note.get(),
                  panel.var_note.get())
        finally:
            app.destroy()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def t_page_is_really_laid_out():
    """★ 回归：页面必须真的被布局出来。

    用户实测报过「下载/导入页面是空的」—— 面板对象建好了、数据也读到了，
    但**漏了 pack()**，整页一片空白（面板没进布局、尺寸 1x1）。
    部署页当初也是这样白屏的，属于同一个坑的第二次。
    只断言"面板对象不是 None"抓不到它，必须断言**布局管理器真的挂上了**。
    """
    print("[★ 回归：两页都必须真的被布局出来（不是只有对象）]")
    root, data, mods, mdir = fresh_env()
    tmp = tempfile.mkdtemp(prefix="hd2u_rec6_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        from hd2_updater import gui as G
        G.find_data_file = lambda explicit=None: (data, "测试")
        G.arsenal_running = lambda *a, **k: False
        app = G.App()
        app.withdraw()
        try:
            for page, attr in (("downloads", "_downloads_panel"),
                               ("deploy", "_deploy_panel")):
                app._show_page(page)
                app.update_idletasks()
                panel = getattr(app, attr)
                check(f"{page} 页的面板被创建了", panel is not None)
                # withdraw 的窗口里 winfo_ismapped() 恒为 0，所以看 winfo_manager()
                check(f"★ {page} 页的面板真的被 pack 了",
                      panel is not None and panel.winfo_manager() == "pack",
                      panel.winfo_manager() if panel else "(无面板)")
                frame = app._pages[page]
                check(f"★ {page} 页的容器在显示（切页生效）",
                      frame.winfo_manager() == "pack", frame.winfo_manager())
            # 页面上得有可见内容（标题/列表/按钮），不是个空壳
            dp = app._downloads_panel
            check("下载页有列表控件", dp.tree.winfo_manager() == "pack",
                  dp.tree.winfo_manager())
            check("下载页有导入按钮", dp.btn_import.winfo_manager() == "pack",
                  dp.btn_import.winfo_manager())
            check("下载页有说明文字", bool(dp.var_note.get()))
            # 来回切两次，确认不会互相顶掉
            app._show_page("update")
            app.update_idletasks()
            check("切回更新页后下载页收起",
                  app._pages["downloads"].winfo_manager() == "")
            app._show_page("downloads")
            app.update_idletasks()
            check("再切回下载页仍然显示",
                  app._pages["downloads"].winfo_manager() == "pack")
            check("下载页面板仍在布局里",
                  dp.winfo_manager() == "pack", dp.winfo_manager())
        finally:
            app.destroy()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def t_button_enable_and_live_refresh():
    """★ 回归：用户报「下载好了已经在 GUI 显示出来一会了，才变到可以按的」。

    两个真问题：
    · `DownloadsPanel` **没有绑定 `<<TreeviewSelect>>`** → 用鼠标点一行按钮不变亮，
      只有右键那条路才同步（实测复现：点行后按钮仍禁用、右键后可用）；
    · 下载完成时**不会刷新这一页**（只在切页时刷新），正停在这一页就看不到新记录。
    """
    print("[★ 回归：按钮可用性 + 下载完成后主动刷新]")
    root, data, mods, mdir = fresh_env()
    tmp = tempfile.mkdtemp(prefix="hd2u_rec7_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        from hd2_updater import gui as G
        from hd2_updater import records as R
        z = make_zip(os.path.join(root, "ok.zip"), {
            "manifest.json": manifest(), f"{P}.patch_0": b"X"})
        R.mark(GUID, "v2", z, "nexus", "测试模组")
        gone = os.path.join(root, "gone.zip")
        R.mark("gone-key", "v1", gone, "github", "丢了文件的")
        R.mark("applied-key", "v3", z, "github", "已导入过的")
        R.mark_applied("applied-key")

        G.find_data_file = lambda explicit=None: (data, "测试")
        G.arsenal_running = lambda *a, **k: False
        app = G.App()
        app.withdraw()
        try:
            app._show_page("downloads")
            app.update_idletasks()
            p = app._downloads_panel

            # ① 接线必须真的存在（函数在、绑定没挂上，是本项目吃过的亏）
            check("★ 绑定了 <<TreeviewSelect>>（点行会同步按钮）",
                  bool(p.tree.bind("<<TreeviewSelect>>")),
                  str(p.tree.bind("<<TreeviewSelect>>")))
            # ② 进页面自动选中第一条「待导入」→ 按钮立刻可用（不用先点一下）
            check("★ 进页面就自动选中了待导入的那条",
                  p.tree.selection() == (GUID,), str(p.tree.selection()))
            check("★ 进页面按钮就可按", p.btn_import.instate(["!disabled"]))
            # ③ 走真实鼠标路径：选中变化 + Tk 发 TreeviewSelect
            p.tree.selection_remove(*p.tree.selection())
            p._sync_buttons()
            check("没选中时按钮不可按", not p.btn_import.instate(["!disabled"]))
            p.tree.selection_set(GUID)
            p.tree.event_generate("<<TreeviewSelect>>")
            app.update_idletasks()
            check("★ 用鼠标点一行后按钮变可按",
                  p.btn_import.instate(["!disabled"]))
            # ④ 文件已不在的行：不该让用户按
            p.tree.selection_set("gone-key")
            p.tree.event_generate("<<TreeviewSelect>>")
            app.update_idletasks()
            check("★ 文件已不在的行不给按", not p.btn_import.instate(["!disabled"]))
            # ⑤ 已导入过的行仍可导入（= 重装）
            p.tree.selection_set("applied-key")
            p.tree.event_generate("<<TreeviewSelect>>")
            app.update_idletasks()
            check("已导入的行仍可再导一次", p.btn_import.instate(["!disabled"]))

            # ⑥ ★ 不切页也能看到新下载 —— 走**真实的"下载完成"消息路径**。
            #   注意不能直接调 `_refresh_downloads_if_open()`：那样只验了 helper
            #   本身，**调用点断了测试照样绿**（本项目反复吃过的"函数在、接线断"）。
            real_mb = G.messagebox

            class _MB:
                @staticmethod
                def showinfo(*a, **k):
                    pass

                @staticmethod
                def showwarning(*a, **k):
                    pass

                @staticmethod
                def showerror(*a, **k):
                    pass

                @staticmethod
                def askyesno(*a, **k):
                    return True

            G.messagebox = _MB
            try:
                n_before = len(p.tree.get_children())
                z2 = make_zip(os.path.join(root, "new.zip"), {
                    "manifest.json": manifest(
                        guid="99999999-0000-0000-0000-000000000000", name="新下的"),
                    f"{P}.patch_0": b"Y"})
                R.mark("brand-new", "v1", z2, "github", "新下的")
                check("刷新前列表里还没有它", not p.tree.exists("brand-new"))
                # 模拟后台"批量下载完成"这条消息（真实入口）
                app._handle_msg("dl_done", "下载完成 1/1 个。")
                app.update_idletasks()
                check("★ 收到「下载完成」后不切页也能看到新记录",
                      p.tree.exists("brand-new"), str(p.tree.get_children()))
                check("记录数变多了",
                      len(p.tree.get_children()) == n_before + 1,
                      f"{n_before} -> {len(p.tree.get_children())}")

                # 接力那条路（N网/A网 下完收文件）也要能刷新
                R.mark("relay-new", "v1", z2, "nexus", "接力收到的")
                check("接力前列表里还没有它", not p.tree.exists("relay-new"))
                app._handle_msg("handoff", (True, root, 1, []))
                app.update_idletasks()
                check("★ 收到「接力完成」后也能立刻看到",
                      p.tree.exists("relay-new"), str(p.tree.get_children()))
            finally:
                G.messagebox = real_mb

            # 面板还没建时调用也不能崩
            app2 = G.App()
            app2.withdraw()
            try:
                app2._refresh_downloads_if_open()   # 面板是 None
                check("面板还没建时调用不崩", True)
            finally:
                app2.destroy()
        finally:
            app.destroy()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    for fn in (t_records_states, t_import_updates_same_uuid,
               t_no_manifest_needs_confirm, t_panel_render_and_guard,
               t_check_page_jumps_here, t_page_is_really_laid_out,
               t_button_enable_and_live_refresh):
        fn()
    print()
    print(f"下载页自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
