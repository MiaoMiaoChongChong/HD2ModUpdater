# -*- coding: utf-8 -*-
"""备份轮转自测：留几份、怎么删、以及**绝不能删错东西**。

背景：备份原来只增不减 —— 用户机器上游戏目录的部署备份攒到 1.63 GB
（每份约 230 MB，每次部署再加一份）。用户问"有没有上限"，于是补了这个模块
+ 侧边栏「备份」页（看 + 手动删）。

★ 这里最重要的一组断言是**安全边界**：只删我们自己 `.hd2u-` 命名的产物、
只在预期目录里动手、**绝不碰 Arsenal 自己写的 `hd2a_data_<ISO>.json.bak`**。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HD2U_READONLY_GUARD"] = "1"

from hd2_updater import backups as B  # noqa: E402

PASS = 0
FAIL = 0
FAILS: list[str] = []

P = "9ba626afa44a3aa3"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
        print(f"  FAIL: {name} {extra}")


def world(n_game=5, n_bak=4, n_old=3, old_per_mod=1):
    """造一个假世界：游戏目录 + 模组目录 + 数据文件，各放几份备份。"""
    root = tempfile.mkdtemp(prefix="hd2u_bk_")
    game = os.path.join(root, "game")
    gdata = os.path.join(game, "data")
    mods = os.path.join(root, "mods")
    os.makedirs(gdata, exist_ok=True)
    os.makedirs(mods, exist_ok=True)
    open(os.path.join(gdata, f"{P}.patch_0"), "wb").close()

    stamps = [f"20261011-1{i}0000" for i in range(max(n_game, 1))]
    for s in stamps[:n_game]:
        d = os.path.join(gdata, B.BACKUP_DIR_NAME, s)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{P}.patch_0"), "wb") as f:
            f.write(b"x" * 1000)

    data = os.path.join(root, "hd2a_data.json")
    json.dump({"userModsDir": mods, "userGameDir": game,
               "selectedProfile": "default", "deployedProfile": "default",
               "modsList": {"default": {"mods": []}}, "modsLibrary": []},
              open(data, "w", encoding="utf-8"), ensure_ascii=False)
    for s in stamps[:n_bak]:
        open(f"{data}.hd2u-{s}.bak", "w", encoding="utf-8").write("{}")
    # Arsenal 自己写的备份（**绝不能碰**）
    open(os.path.join(root, "hd2a_data_2026-10-11T18-21-57.json.bak"),
         "w", encoding="utf-8").write("{}")
    for i in range(n_old):
        for m in range(old_per_mod):
            os.makedirs(os.path.join(mods, f"模组{m}.hd2u-old-{stamps[i]}"),
                        exist_ok=True)
    # 一个"看起来像但不是我们产物"的目录（绝不能被删）
    os.makedirs(os.path.join(mods, "别的程序留下的目录"), exist_ok=True)
    open(os.path.join(mods, "别的程序留下的目录", "x.txt"), "w").write("keep")
    return root, game, mods, data


def t_scan_and_keep():
    print("[扫描 + 保留份数]")
    root, game, mods, data = world(n_game=5, n_bak=4, n_old=2)
    try:
        keep = dict(B.DEFAULTS)
        keep.update({"keep_game_backups": 3, "keep_old_mod_dirs": 1,
                     "keep_data_baks": 2})
        rep = B.scan(game, mods, data, keep)
        check("游戏备份都扫到了", len(rep["game"]) == 5, str(len(rep["game"])))
        check("库备份都扫到了", len(rep["data_baks"]) == 4, str(len(rep["data_baks"])))
        check("旧版本目录都扫到了", len(rep["old_mods"]) == 2,
              str(len(rep["old_mods"])))
        check("★ 超出上限的标成 drop",
              sum(1 for x in rep["game"] if x["drop"]) == 2,
              str([x["drop"] for x in rep["game"]]))
        check("★ 库备份里**最早那份永不 drop**",
              rep["data_baks"][-1]["drop"] is False,
              str([x["drop"] for x in rep["data_baks"]]))
        # 可释放 = 2 份游戏备份(5-3) + 1 份旧版本(2-1) + 1 份库备份(4 份里留最新 2
        # 且最早那份锚点不删 → 只多出 1 份)
        check("可释放 = 4 项（2 游戏 + 1 旧版本 + 1 库备份）",
              rep["freeable_items"] == 4, str(rep["freeable_items"]))
        check("总数/可释放字节数算得出来",
              rep["total_bytes"] > 0 and rep["freeable_bytes"] > 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_prune_keeps_newest():
    print("[清理：只删超出的，保留最新的 N 份]")
    root, game, mods, data = world(n_game=5, n_bak=4, n_old=2)
    try:
        keep = {"keep_game_backups": 3, "keep_old_mod_dirs": 1,
                "keep_data_baks": 2}
        got = B.prune(game, mods, data, keep)
        check("删了 4 项（2 游戏 + 1 旧版本 + 1 库备份）", len(got["deleted"]) == 4,
              str(len(got["deleted"])))
        check("释放的字节数是正数", got["freed_bytes"] > 0)
        left = sorted(os.listdir(os.path.join(game, "data", B.BACKUP_DIR_NAME)))
        check("★ 留下的是**最新** 3 份（不是最旧的）",
              left == ["20261011-120000", "20261011-130000",
                       "20261011-140000"], str(left))
        baks = sorted(n for n in os.listdir(os.path.dirname(data))
                      if n.startswith("hd2a_data.json.hd2u-"))
        check("★ 库备份留最新 2 份 + 最早那份",
              len(baks) == 3 and "20261011-100000" in baks[0], str(baks))
        check("★ 锚点（最早那份）确实还在",
              os.path.isfile(f"{data}.hd2u-20261011-100000.bak"))
        check("再扫一遍已经没有超出上限的了",
              B.scan(game, mods, data, keep)["freeable_items"] == 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_safety_never_delete_others():
    print("[★ 安全边界：绝不删别人的东西]")
    root, game, mods, data = world(n_game=5)
    try:
        # ① Arsenal 自己写的备份 —— 即使保留份数设成 1 也不能碰
        got = B.prune(game, mods, data, {"keep_game_backups": 1,
                                         "keep_old_mod_dirs": 0,
                                         "keep_data_baks": 1})
        arsenal_bak = os.path.join(os.path.dirname(data),
                                   "hd2a_data_2026-10-11T18-21-57.json.bak")
        check("★ Arsenal 自己的 .json.bak 没被删", os.path.isfile(arsenal_bak))
        check("★ 别人的目录没被删",
              os.path.isdir(os.path.join(mods, "别的程序留下的目录")))

        # ② 手选删除也必须过两道关：目录内 + 名字合规
        outsider = os.path.join(root, "重要文件.txt")
        open(outsider, "w").write("don't delete me")
        got2 = B.prune_selected([outsider], {"game_dir": game, "mods_dir": mods,
                                             "data_file": data})
        check("★ 目录外的东西删不掉", os.path.isfile(outsider), str(got2))
        check("并且记了原因", bool(got2["failed"]), str(got2["failed"]))

        # 名字不合规（在 mods 目录里但不是 .hd2u-old- 形态）也删不掉
        weird = os.path.join(mods, "正常模组目录")
        os.makedirs(weird, exist_ok=True)
        got3 = B.prune_selected([weird], {"game_dir": game, "mods_dir": mods,
                                          "data_file": data})
        check("★ 名字不合规的目录删不掉", os.path.isdir(weird), str(got3))

        # 名字**完全合规**但在**别的目录**里 → 只有"目录范围校验"能挡住它。
        # （这条是"撤销验证"逼出来的：把目录校验去掉时，别的断言全都还绿，
        #   说明那道关当时没被任何用例覆盖。）
        elsewhere = tempfile.mkdtemp(prefix="hd2u_elsewhere_")
        sneaky = os.path.join(elsewhere,
                              "hd2a_data.json.hd2u-20260101-000000.bak")
        open(sneaky, "w", encoding="utf-8").write("{}")
        got5 = B.prune_selected([sneaky], {"game_dir": game, "mods_dir": mods,
                                           "data_file": data})
        check("★ 名字像我们的、但在别的目录 → 删不掉", os.path.isfile(sneaky),
              str(got5))
        check("并记了原因（不在备份目录内）",
              any("不在备份目录内" in str(w) for _p, w in got5["failed"]),
              str(got5["failed"]))
        shutil.rmtree(elsewhere, ignore_errors=True)

        # ③ 手选删除正常工作（**用一份确实还在的备份**，别用可能已被清掉的）
        target = os.path.join(game, "data", B.BACKUP_DIR_NAME, "20261011-140000")
        check("前提：这份备份还在", os.path.isdir(target))
        before_size = B._dir_size(target)
        got4 = B.prune_selected([target], {"game_dir": game, "mods_dir": mods,
                                           "data_file": data})
        check("手选删除能删掉自己的备份", not os.path.exists(target), str(got4))
        check("★ 并统计了释放的字节（= 删之前量到的体积）",
              got4["freed_bytes"] == before_size and before_size > 0,
              f"{got4['freed_bytes']} vs {before_size}")

        # ④ dry_run 只算不删
        before = len(os.listdir(os.path.join(game, "data", B.BACKUP_DIR_NAME)))
        B.prune(game, mods, data, {"keep_game_backups": 1,
                                   "keep_old_mod_dirs": 0,
                                   "keep_data_baks": 1}, dry_run=True)
        after = len(os.listdir(os.path.join(game, "data", B.BACKUP_DIR_NAME)))
        check("★ dry_run 不真删", before == after, f"{before} -> {after}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_retention_config():
    print("[保留份数：能读能写，且会夹到合法范围]")
    tmp = tempfile.mkdtemp(prefix="hd2u_bk_cfg_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        d = B.retention()
        check("默认值 = 3 / 1 / 10",
              (d["keep_game_backups"], d["keep_old_mod_dirs"],
               d["keep_data_baks"]) == (3, 1, 10), str(d))
        got = B.save_retention({"keep_game_backups": 5, "keep_data_baks": 20,
                                "keep_old_mod_dirs": 2})
        check("写进去能读回来",
              (got["keep_game_backups"], got["keep_data_baks"],
               got["keep_old_mod_dirs"]) == (5, 20, 2), str(got))
        # 越界要被夹住（0 份会把安全网清光，必须挡住）
        got2 = B.save_retention({"keep_game_backups": 0, "keep_data_baks": 99999,
                                 "keep_old_mod_dirs": -5})
        lo, hi = B.LIMITS["keep_game_backups"]
        check("★ 0 份被夹到下限", got2["keep_game_backups"] == lo, str(got2))
        check("★ 超大值被夹到上限", got2["keep_data_baks"] == B.LIMITS["keep_data_baks"][1],
              str(got2))
        check("负数被夹到下限",
              got2["keep_old_mod_dirs"] == B.LIMITS["keep_old_mod_dirs"][0], str(got2))
        # 乱填（非数字）回落到默认
        got3 = B.save_retention({"keep_game_backups": "abc"})
        check("乱填回落默认", got3["keep_game_backups"] == B.DEFAULTS["keep_game_backups"],
              str(got3))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_skip_mods_when_arsenal_running():
    print("[Arsenal 开着时：跳过模组目录，只清游戏/库备份]")
    root, game, mods, data = world(n_game=5, n_old=2)
    try:
        old_dir = os.path.join(mods, "模组0.hd2u-old-20261011-100000")
        check("前提：旧版本目录在", os.path.isdir(old_dir))
        got = B.prune_for_context(data, game, mods, arsenal_running=True)
        check("★ Arsenal 开着 → 模组目录那部分没动", os.path.isdir(old_dir),
              str(got["deleted"]))
        check("游戏备份照常清", len(got["deleted"]) == 2, str(got["deleted"]))
        got2 = B.prune_for_context(data, game, mods, arsenal_running=False)
        check("Arsenal 关着 → 旧版本目录会被清", not os.path.isdir(old_dir),
              str(got2["deleted"]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------ 界面
def t_backups_page():
    print("[「备份」页：列出三类 + 手动删 + 清理多余 + 真的被布局出来]")
    root, game, mods, data = world(n_game=5, n_bak=4, n_old=2)
    tmp = tempfile.mkdtemp(prefix="hd2u_bk_ui_")
    os.environ["HD2U_DATA_DIR"] = tmp
    try:
        from hd2_updater import gui as G
        G.find_data_file = lambda explicit=None: (data, "测试")
        G.arsenal_running = lambda *a, **k: False
        app = G.App()
        app.withdraw()
        try:
            check("导航里有「备份」这一页", "backups" in app.nav_buttons,
                  str(sorted(app.nav_buttons)))
            app._show_page("backups")
            app.update_idletasks()
            p = app._backups_panel
            check("★ 面板真的被 pack 了（不是只有对象）",
                  p is not None and p.winfo_manager() == "pack",
                  p.winfo_manager() if p else "(无)")
            kids = p.tree.get_children()
            check("三类备份都列出来了", len(kids) == 5 + 4 + 2, str(len(kids)))
            kinds = {p.tree.item(i, "values")[1] for i in kids}
            check("列里有游戏部署备份", "游戏部署备份" in kinds, str(kinds))
            check("列里有模组旧版本", "模组旧版本" in kinds, str(kinds))
            check("列里有 Arsenal 库备份", "Arsenal 库备份" in kinds, str(kinds))
            # 锚点行必须标成"永不删"
            anchor = [i for i in kids
                      if p.tree.item(i, "values")[0] == "永不删"]
            check("★ 最早那份库备份标成「永不删」", len(anchor) == 1, str(anchor))
            check("超出上限的被标出来",
                  any(p.tree.item(i, "values")[0] == "超出上限" for i in kids))
            check("说明里报了总数和可释放量",
                  "共" in p.var_note.get() and "MB" in p.var_note.get(),
                  p.var_note.get())
            # 接线断言（本项目吃过"函数在、接线断"的亏）
            check("绑定了选中变化事件", bool(p.tree.bind("<<TreeviewSelect>>")),
                  str(p.tree.bind("<<TreeviewSelect>>")))
            check("绑定了右键菜单", bool(p.tree.bind("<Button-3>")))
            check("没选中时「删除选中」不可按",
                  not p.btn_del.instate(["!disabled"]))
            p.tree.selection_set(kids[0])
            p.tree.event_generate("<<TreeviewSelect>>")
            app.update_idletasks()
            check("选中后「删除选中」可按", p.btn_del.instate(["!disabled"]))

            # 手动删一份（走确认框，打桩成"同意"）
            real_mb = G.messagebox

            class _MB:
                @staticmethod
                def askyesno(*a, **k):
                    return True

                @staticmethod
                def showinfo(*a, **k):
                    pass

                @staticmethod
                def showwarning(*a, **k):
                    pass

                @staticmethod
                def showerror(*a, **k):
                    pass

            G.messagebox = _MB
            try:
                n_before = len(p.tree.get_children())
                p.tree.selection_set(kids[0])
                p._delete_selected()
                app.update_idletasks()
                check("★ 手动删掉一项后列表少一行",
                      len(p.tree.get_children()) == n_before - 1,
                      f"{n_before} -> {len(p.tree.get_children())}")
                # 清理多余的
                n2 = len(p.tree.get_children())
                p._prune_extra()
                app.update_idletasks()
                check("★ 清理多余之后不再有「超出上限」的行",
                      not any(p.tree.item(i, "values")[0] == "超出上限"
                              for i in p.tree.get_children()),
                      str([p.tree.item(i, "values")[0]
                           for i in p.tree.get_children()]))
                check("清理确实删掉了东西",
                      len(p.tree.get_children()) < n2,
                      f"{n2} -> {len(p.tree.get_children())}")
            finally:
                G.messagebox = real_mb

            # 自动清理：Arsenal 开着时**不动**模组目录；关着时才清
            old_dir = os.path.join(mods, "模组0.hd2u-old-20261011-100000")
            if not os.path.isdir(old_dir):
                # 前面「清理多余的」可能已经把它删了 → 再造一个来验
                os.makedirs(old_dir, exist_ok=True)
            G.arsenal_running = lambda *a, **k: True
            app.auto_prune_backups("测试")
            check("★ Arsenal 开着 → auto_prune 不碰模组目录",
                  os.path.isdir(old_dir))
            G.arsenal_running = lambda *a, **k: False
            app.auto_prune_backups("测试")
            check("Arsenal 关着 → auto_prune 会清掉它",
                  not os.path.isdir(old_dir), str(old_dir))
        finally:
            app.destroy()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    for fn in (t_scan_and_keep, t_prune_keeps_newest,
               t_safety_never_delete_others, t_retention_config,
               t_skip_mods_when_arsenal_running, t_backups_page):
        fn()
    print()
    print(f"备份轮转自测：{PASS} 通过，{FAIL} 失败")
    for f in FAILS:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
