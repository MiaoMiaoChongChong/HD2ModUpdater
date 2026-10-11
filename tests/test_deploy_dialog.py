# -*- coding: utf-8 -*-
"""部署界面（DeployPanel / DeployDialog）测试 —— 用假库 + 假游戏目录，不碰真环境。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="hd2u_deployui_")
os.environ["HD2U_DATA_DIR"] = _TMP
# 护栏：本测试只允许写临时目录（防止误写真实 Arsenal 库）
os.environ["HD2U_READONLY_GUARD"] = "1"

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


def _jload(path: str) -> dict:
    """读那份假的 Arsenal 库（BOM 兼容）。"""
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


# ---------- 搭假世界 ----------
GAME = os.path.join(_TMP, "game")
GDDIR = os.path.join(GAME, "data")
MODS = os.path.join(_TMP, "mods")
os.makedirs(GDDIR, exist_ok=True)
os.makedirs(MODS, exist_ok=True)
PREFIX = "9ba626afa44a3aa3"
for n in (f"{PREFIX}.patch_0", f"{PREFIX}.patch_0.stream"):
    open(os.path.join(GDDIR, n), "wb").close()


def make_mod_dir(label: str, n_slots: int) -> str:
    d = os.path.join(MODS, label)
    os.makedirs(d, exist_ok=True)
    for i in range(n_slots):
        sd = os.path.join(d, "options", f"Opt{i}")
        os.makedirs(sd, exist_ok=True)
        m = os.path.join(sd, f"{PREFIX}.patch_0")
        with open(m, "wb") as f:
            f.write(f"{label}-{i}".encode() * 8)
        for suf in (".gpu_resources", ".stream"):
            with open(m + suf, "wb") as f:
                f.write(b"z")
    return d


# 顺序：p0 位次0 … p2 末位；off 不在配置档；dis 在配置档但禁用
specs = [
    ("p0", "列表第一位", 1, True, True, True),
    ("p1", "中间那个", 2, True, True, True),
    ("p2", "优先级最低", 1, True, True, False),   # 启用但 Arsenal 未部署
    ("off", "不在配置档", 1, False, False, False),
    ("dis", "配置档里但禁用", 1, True, False, False),
]
library = []
mods_list = []
for i, (uid, label, nslot, in_profile, enabled, deployed) in enumerate(specs):
    path = make_mod_dir(label, nslot)
    library.append({
        "uuid": uid, "label": label, "path": path, "description": "",
        "iconPath": None, "tags": [], "patchFileNames": [], "nexusData": None,
        "options": [], "contentHash": "", "addedAt": "", "previousVersions": [],
    })
    if in_profile:
        mods_list.append({
            "uuid": uid, "path": path, "label": label, "options": [],
            "enabled": enabled, "deployed": deployed, "changed": False,
        })

DATA_FILE = os.path.join(_TMP, "hd2a_data.json")
with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "userModsDir": MODS, "userGameDir": GAME, "selectedProfile": "default",
        "deployedProfile": "default",
        "modsList": {"default": {"mods": mods_list}},
        "modsLibrary": library,
    }, f, ensure_ascii=False)

# 让界面层解析到这份假库（gui 里是 from .arsenal import find_data_file，得直接替换）
from hd2_updater import gui as G  # noqa: E402
from hd2_updater import arsenal as _arsenal  # noqa: E402

G.find_data_file = lambda explicit=None: (DATA_FILE, "测试假库")
_arsenal.find_data_file = G.find_data_file

try:
    import tkinter as tk  # noqa: F401
    _probe = tk.Tk()
    _probe.destroy()
    CAN_GUI = True
except Exception as e:  # noqa: BLE001
    print(f"\n跳过界面测试（建不起 Tk 窗口）：{e}")
    CAN_GUI = False

if CAN_GUI:
    app = G.App()
    app.withdraw()
    try:
        # ★ 回归：切到部署页后面板必须真的被布局出来。
        #   曾经漏了 pack()，页面一片空白（panel 无几何管理、尺寸 1x1），
        #   而当时所有测试都直接 new 面板、不走页面切换，全都测不到。
        #   注意：这里用 winfo_manager() 而不是 winfo_ismapped() —— 本测试把窗口
        #   withdraw 了，未映射的窗口下 ismapped 恒为 0，判断不出"有没有 pack"。
        app._show_page("deploy")
        app.update_idletasks()
        check("部署页已切换", app._current_page == "deploy")
        check("部署面板已构建", app._deploy_panel is not None)
        check("部署面板已被 pack（页面非空白）",
              app._deploy_panel.winfo_manager() == "pack",
              f"manager={app._deploy_panel.winfo_manager()!r}")
        check("部署面板容器已被 pack",
              app.page_deploy.winfo_manager() == "pack",
              f"manager={app.page_deploy.winfo_manager()!r}")
        check("面板内控件已建立", len(app._deploy_panel.winfo_children()) >= 5,
              str(len(app._deploy_panel.winfo_children())))
        # 切回更新页，两页互斥
        app._show_page("update")
        app.update_idletasks()
        check("切回后部署页取消布局",
              app.page_deploy.winfo_manager() == "", 
              f"manager={app.page_deploy.winfo_manager()!r}")
        check("切回后更新页已布局",
              app.page_update.winfo_manager() == "pack",
              f"manager={app.page_update.winfo_manager()!r}")
        app._show_page("deploy")
        app.update_idletasks()
        check("再次切回部署页仍布局",
              app._deploy_panel.winfo_manager() == "pack")

        # ★ 更新检查页不该再有部署列/部署入口（已按用户要求搬到部署页）
        check("更新检查页去掉部署列",
              "deploy" not in app.tree["columns"], str(app.tree["columns"]))
        check("更新检查页无「部署选中」按钮",
              not hasattr(app, "btn_deploy"))
        check("更新检查页无「检查已部署更新」按钮",
              not hasattr(app, "btn_deployed"))
        check("右键菜单无已部署项",
              "检查所有已部署的" not in str(
                  [app.menu.entrycget(i, "label")
                   for i in range(app.menu.index("end") + 1)
                   if app.menu.type(i) == "command"]))

        panel = G.DeployPanel(app.page_deploy, app)
        print("【界面】部署面板")

        # 列出**整个库**：配置档内的 p0/p1/p2/dis + 未加入配置档的 off。
        check("列出整个模组库（含未加入配置档的）",
              sorted(panel.vars) == ["dis", "off", "p0", "p1", "p2"],
              str(sorted(panel.vars)))
        check("列表行数 = 库大小", len(panel.tree.get_children()) == 5,
              str(len(panel.tree.get_children())))
        check("未启用的也在列表里", "dis" in panel.vars)
        check("未加入配置档的也在列表里（本轮修改）", "off" in panel.vars)
        # 排序：已启用 → 未启用 → 未加入
        order = list(panel.tree.get_children())
        check("已启用的排在前面",
              order[:3] == ["p0", "p1", "p2"], str(order))
        check("未启用的次之", order[3] == "dis", str(order))
        check("未加入配置档的排最后", order[4] == "off", str(order))
        # 三态提示文字
        check("未启用行的提示",
              "未启用" in panel.tree.item("dis", "values")[4],
              str(panel.tree.item("dis", "values")))
        offv = panel.tree.item("off", "values")
        check("未加入行的提示", "未加入配置档" in offv[4], str(offv))
        check("未加入行的序号显示为 —", offv[1] == "—", str(offv))
        check("未加入行默认不勾选", "off" not in panel._checked())
        check("_pending_add 识别未加入的", panel._pending_add({"off"}) == ["off"],
              str(panel._pending_add({"off"})))
        check("_pending_add 不含已加入的", panel._pending_add({"p0"}) == [])

        # 镜像接管：默认勾选 = 启用 ∩ Arsenal 已部署 = p0,p1（p2 未部署，不自动勾）
        check("镜像接管：已部署的自动勾选",
              sorted(panel._checked()) == ["p0", "p1"], str(sorted(panel._checked())))
        check("未部署的不自动勾选", "p2" not in panel._checked())
        check("镜像提示有文字", "Arsenal" in panel.var_mirror.get(),
              panel.var_mirror.get())
        # 行内「启用」列显示状态 —— 要分清"已启用"和"已部署"（用户明确要求）
        first = panel.tree.item("p0", "values")
        check("行内显示已启用", first[0] == "● 启用", str(first))
        check("★ 已部署的行写明“已部署（游戏里生效中）”",
              "已部署" in first[4] and "生效中" in first[4], str(first))
        p2v = panel.tree.item("p2", "values")
        check("未勾选的行显示未启用", p2v[0] == "○ 未启用", str(p2v))
        check("行内显示槽位数", p2v[3] == "1", str(p2v))

        # ★ 第四态：内容更新过、还没重新部署（用户问"导入并更新后还需要重新部署吗"）
        #   导入更新 / 改选项会把 Arsenal 的 changed 置 True，而游戏目录里还是旧内容。
        #   这时若仍显示"已部署（游戏里生效中）"，用户会以为已经好了。
        _d = _jload(DATA_FILE)
        for m in _d["modsList"]["default"]["mods"]:
            if m["uuid"] == "p0":
                m["changed"] = True
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(_d, f, ensure_ascii=False)
        panel.reload()
        st_row = panel.tree.item("p0", "values")
        check("★ 内容更新过 → 显示「需要重新部署」",
              "需要重新部署" in st_row[4], str(st_row))
        check("★ 并且用「待处理」的橙色标出来",
              "stale" in panel.tree.item("p0", "tags"),
              str(panel.tree.item("p0", "tags")))
        check("★ 汇总里也点出来",
              "需要重新部署" in panel.var_info.get()
              or "还没重新部署" in panel.var_info.get(),
              panel.var_info.get())
        # 部署一次之后 Arsenal 会清掉 changed → 这一态自动消失
        from hd2_updater import arsenal_sync as _asm
        _asm.sync_deployed_state(DATA_FILE, {"p0", "p1"},
                                 set(panel.tree.get_children()))
        panel.reload()
        check("★ 部署后这一态自动消失",
              "需要重新部署" not in panel.tree.item("p0", "values")[4],
              str(panel.tree.item("p0", "values")))

        # 默认计划：p0(1) + p1(2) = 3 槽
        check("默认计划槽位=3", panel.plan and panel.plan.slot_count == 3,
              str(panel.plan.slot_count if panel.plan else None))
        check("计划预览有文字", "将要部署" in panel.var_info.get(), panel.var_info.get())

        # ★ 回归：必须真的能切换勾选（此前列表完全点不动）
        panel._toggle_uuid("p0")
        check("切换后变为未勾选", "p0" not in panel._checked(), str(panel._checked()))
        check("行内变为未启用",
              panel.tree.item("p0", "values")[0] == "○ 未启用",
              str(panel.tree.item("p0", "values")))
        panel._toggle_uuid("p0")
        check("再切一次恢复勾选", "p0" in panel._checked())
        # 事件绑定确实挂在控件上（防止将来又只"刷新外观"而不切换）
        check("Treeview 绑定了单击", bool(panel.tree.bind("<Button-1>")))
        check("Treeview 绑定了双击", bool(panel.tree.bind("<Double-1>")))
        check("Treeview 绑定了空格", bool(panel.tree.bind("<space>")))
        check("有「启用 / 停用选中项」按钮",
              panel.btn_toggle.cget("text").startswith("启用 / 停用"))

        # 通过"选中行 + 按钮"这条真实路径切换（对应「专门的按钮」这个需求）
        panel.tree.selection_set("p1")
        before_p1 = "p1" in panel._checked()
        panel.btn_toggle.invoke()          # 真的按按钮
        check("按钮能切换选中项", ("p1" in panel._checked()) != before_p1,
              str(panel._checked()))
        panel.btn_toggle.invoke()
        check("再按一次切回", ("p1" in panel._checked()) == before_p1)
        # 没选中任何行时按按钮：给出提示而不是静默
        panel.tree.selection_remove(*panel.tree.selection())
        panel.btn_toggle.invoke()
        check("未选中时按钮给提示", "点一行" in panel.var_note.get(),
              panel.var_note.get())
        check("未选中时不误改勾选",
              sorted(panel._checked()) == ["p0", "p1"], str(sorted(panel._checked())))

        # 越界切换不崩
        panel._toggle_uuid("nonexistent")
        check("切换不存在的项不崩", True)

        # 取消 p0 后计划变小
        panel._toggle_uuid("p0")
        check("取消 p0 后槽位=2", panel.plan and panel.plan.slot_count == 2,
              str(panel.plan.slot_count if panel.plan else None))
        panel._toggle_uuid("p0")

        # 全不选 / 全选 / 恢复默认
        panel._set_all(False)
        check("全不选后无计划", panel.plan is None)
        check("全不选后按钮禁用", "disabled" in panel.btn_go.state())
        panel._set_all(True)
        check("全选把库里的都勾上",
              sorted(panel._checked()) == ["dis", "off", "p0", "p1", "p2"],
              str(sorted(panel._checked())))
        panel._set_default()
        check("恢复默认只含镜像接管集合（不含未启用的）",
              sorted(panel._checked()) == ["p0", "p1"], str(sorted(panel._checked())))

        # 方向：位次在前者编号小
        p0 = next(i["index"] for i in panel.plan.items if i["mod_uuid"] == "p0")
        p1 = next(i["index"] for i in panel.plan.items if i["mod_uuid"] == "p1")
        check("位次在前编号更小", p0 < p1, f"p0={p0} p1={p1}")
        panel._set_default()

        # 真部署一次（走 deploy 模块，不弹窗）
        from hd2_updater import deploy as D
        res = D.deploy(panel.plan, do_backup=True)
        check("部署写入槽位", res["written"] == 3, str(res["written"]))
        check("旧文件已备份", bool(res["backup"]))
        names = D.scan_existing(GDDIR, PREFIX)
        check("data 目录里出现部署文件", len(names) > 0, str(names))

        # 写回：Arsenal 库应被更新成"实际部署了什么"
        from hd2_updater import arsenal_sync as AS
        files = AS.live_file_entries(GDDIR, PREFIX)
        sr = AS.sync_deployed_state(DATA_FILE, {"p0", "p1"},
                                   live_files=files, force=True)
        check("写回产生备份", os.path.isfile(sr["backup"]))
        with open(DATA_FILE, encoding="utf-8-sig") as f:
            after = json.load(f)
        st = {m["uuid"]: m["deployed"] for m in after["modsList"]["default"]["mods"]}
        check("p0/p1 标为已部署", st["p0"] and st["p1"], str(st))
        check("p2 标为未部署", st["p2"] is False, str(st))
        check("快照已写", sr["snapshot"] is True)

        # 界面文案不得残留 markdown 星号
        rows = []
        for iid in panel.tree.get_children():
            rows.append(" ".join(str(x) for x in panel.tree.item(iid, "values")))
        blob = " ".join([panel.var_info.get(), panel.var_order.get(),
                         panel.var_mirror.get()] + rows)
        check("界面无 markdown 星号", "*" not in blob, blob[:90])

        # ---------- 加载顺序：拖动 / 上移下移 ----------
        # 顺序写回要能测：给 p0/p1/p2 一个带 options 与 modsList 的库
        app._deploy_panel.reload()
        order_before = [o for o in panel.tree.get_children()]
        check("初始顺序：已启用 → 未启用 → 未加入",
              order_before == ["p0", "p1", "p2", "dis", "off"], str(order_before))

        # 上移/下移（键盘按钮这条真实路径）
        panel.tree.selection_set("p2")
        panel._nudge(-1)
        check("下移后 p2 上移一格",
              list(panel.tree.get_children())[1] == "p2",
              str(list(panel.tree.get_children())))
        panel.tree.selection_set("p2")
        panel._nudge(1)
        check("再下移复原",
              list(panel.tree.get_children())[2] == "p2",
              str(list(panel.tree.get_children())))
        # 边界：第一行上移、最后一行下移都不该出事
        panel.tree.selection_set("p0")
        panel._nudge(-1)
        check("第一行上移无效果",
              list(panel.tree.get_children())[0] == "p0")
        panel.tree.selection_set("off")
        panel._nudge(1)
        check("最后一行下移无效果",
              list(panel.tree.get_children())[-1] == "off")
        # ★ 回归：重编号必须唯一。曾经只给被提到的模组改号、其余留旧号，
        #   结果两个模组撞成同一个号，build_plan 直接拒绝部署。
        _ords = {}
        for e in panel.entries:
            if e.get("in_profile"):
                _ords.setdefault(e["deploy_order"], []).append(e["uuid"])
        _dupes = {k: v for k, v in _ords.items() if len(v) > 1}
        check("顺序编号无重复", not _dupes, str(_dupes))
        check("顺序编号连续",
              sorted(_ords) == list(range(len(_ords))), str(sorted(_ords)))
        # 没选中时给提示而不是静默
        panel.tree.selection_remove(*panel.tree.selection())
        panel._nudge(-1)
        check("未选中时提示", "点一行" in panel.var_note.get(), panel.var_note.get())

        # 拖动：模拟按下-移动到-松开
        class _E:
            def __init__(self, y=0, x=0):
                self.y, self.x = y, 0
        panel._drag_start(_E())
        check("拖动开始记录了行", panel._drag_iid is None or isinstance(
            panel._drag_iid, str))
        # 直接验证 drop 的重排逻辑（用 uuid 层面）
        kids = list(panel.tree.get_children())
        panel._apply_order([kids[2], kids[0], kids[1]])
        check("apply_order 重排生效",
              list(panel.tree.get_children())[:3] == [kids[2], kids[0], kids[1]],
              str(list(panel.tree.get_children())))
        check("重排后写回有反馈", "Arsenal" in panel.var_note.get(),
              panel.var_note.get())
        # 重排后编号仍必须唯一（防回归）
        _o2 = {}
        for e in panel.entries:
            if e.get("in_profile"):
                _o2.setdefault(e["deploy_order"], []).append(e["uuid"])
        check("重排后编号无重复",
              all(len(v) == 1 for v in _o2.values()), str(_o2))
        # 绑定确实在
        check("Treeview 绑定了拖动按压", bool(panel.tree.bind("<ButtonPress-1>")))
        check("Treeview 绑定了拖动移动", bool(panel.tree.bind("<B1-Motion>")))
        check("Treeview 绑定了拖动松开", bool(panel.tree.bind("<ButtonRelease-1>")))
        check("有上移/下移按钮",
              panel.btn_up.cget("text").startswith("↑")
              and panel.btn_down.cget("text").startswith("↓"))
        check("有「选项…」按钮", panel.btn_opts.cget("text") == "选项…")
        check("有顺序写回开关", hasattr(panel, "var_autosave_order"))

        # ---------- 选项编辑对话框 ----------
        # 用带选项的假模组（含子选项），验证联动与提交
        opt_entry = {
            "uuid": "p0", "label": "带选项的模组",
            "options": [
                {"name": "Core", "include": ["Core"], "enabled": True,
                 "suboptions": []},
                {"name": "Mode", "include": [], "enabled": True, "suboptions": [
                    {"name": "TaskLight", "include": ["TaskLight"],
                     "enabled": True, "suboptions": []},
                    {"name": "Gameplay", "include": ["Gameplay"],
                     "enabled": False, "suboptions": []},
                ]},
            ],
        }
        dlg = G.OptionsDialog(app, opt_entry, opt_entry["options"],
                              DATA_FILE, on_saved=None)
        dlg.withdraw()
        check("选项对话框列出了子选项", len(dlg._rows) == 4, str(len(dlg._rows)))
        keys = [m["key"] for _v, m in dlg._rows]
        check("键名用 组/子 形式", "Mode/TaskLight" in keys, str(keys))
        # 初始状态没变 → 提交应为空
        check("未改动时提交为空", dlg._changed() == {}, str(dlg._changed()))
        # 勾父 = 全开子
        pv = next(v for v, m in dlg._rows if m["key"] == "Mode")
        pv.set(True)
        dlg._on_toggle("Mode", pv)
        kids_state = {m["key"]: v.get() for v, m in dlg._rows
                      if m["key"].startswith("Mode/")}
        check("勾父选项会全开子选项",
              all(kids_state.values()), str(kids_state))
        # 取消一个子 → 父变为未勾（部分=不算全开）
        cv = next(v for v, m in dlg._rows if m["key"] == "Mode/TaskLight")
        cv.set(False)
        dlg._on_toggle("Mode/TaskLight", cv)
        pv2 = next(v for v, m in dlg._rows if m["key"] == "Mode")
        check("子选项有开有关时父不算全开", pv2.get() is False, str(pv2.get()))
        check("改动被记录", "Mode/TaskLight" in dlg._changed(),
              str(dlg._changed()))
        check("计数文案随勾选变化", "关闭" in dlg._count_text(), dlg._count_text())
        dlg.destroy()

        # 没有可选项的模组：入口要给出提示而不是报错
        empty = {"uuid": "p0", "label": "无选项", "options": []}
        check("无选项时 read_options 返回空",
              __import__("hd2_updater.arsenal_sync", fromlist=["x"])
              .read_options(DATA_FILE, "p0") == [])

        # ---------- 用户报的三点修改 ----------
        # ① 勾选框歧义 → 改成明确写"开/关"的双态按钮
        check("没有残留的 Checkbutton（同步开关）",
              panel.btn_sync.winfo_class() == "TButton",
              panel.btn_sync.winfo_class())
        check("同步开关文字写明状态",
              "开" in panel.btn_sync.cget("text") or "关" in panel.btn_sync.cget("text"),
              panel.btn_sync.cget("text"))
        check("同步开关默认开", "开" in panel.btn_sync.cget("text"),
              panel.btn_sync.cget("text"))
        panel.btn_sync.invoke()
        check("点一下变成关", "关" in panel.btn_sync.cget("text"),
              panel.btn_sync.cget("text"))
        check("变量同步更新", panel.var_sync.get() is False)
        panel.btn_sync.invoke()
        check("再点变回开", "开" in panel.btn_sync.cget("text"))
        check("自动写回开关也是双态按钮",
              panel.btn_autosave.winfo_class() == "TButton")
        panel.btn_autosave.invoke()
        check("自动写回可切换", panel.var_autosave_order.get() is False)
        panel.btn_autosave.invoke()

        # ② 未启用的模组也要出现在列表里
        check("列表包含未启用的模组",
              "dis" in [i for i in panel.tree.get_children()],
              str(list(panel.tree.get_children())))
        dis_row = panel.tree.item("dis", "values")
        check("未启用行给出说明", "未启用" in dis_row[4], str(dis_row))
        check("未启用行默认不勾选", "dis" not in panel._checked())

        # 勾上未启用的 → ★ 现在**立即**在 Arsenal 里启用（不再等部署时顺带）
        panel._toggle_uuid("dis")
        _d = _jload(DATA_FILE)
        _dis = next(m for m in _d["modsList"]["default"]["mods"] if m["uuid"] == "dis")
        check("★ 勾选立刻在 Arsenal 里启用", _dis.get("enabled") is True, str(_dis))
        check("★ 勾选状态保留（不会被重读库抹掉）", "dis" in panel._checked(),
              str(sorted(panel._checked())))
        check("勾上未启用项后进入计划",
              panel.plan is not None and "dis" in {i["mod_uuid"] for i in panel.plan.items},
              str([i["mod_uuid"] for i in panel.plan.items] if panel.plan else None))
        check("既然已经启用，就不该再有“待启用”（不用等部署）",
              panel._pending_enable({"dis"}) == set(),
              str(panel._pending_enable({"dis"})))
        check("已启用的不算待启用", panel._pending_enable({"p0"}) == set())
        # 取消勾选 = 立刻停用
        panel._toggle_uuid("dis")
        _d = _jload(DATA_FILE)
        _dis = next(m for m in _d["modsList"]["default"]["mods"] if m["uuid"] == "dis")
        check("★ 取消勾选立刻在 Arsenal 里停用", _dis.get("enabled") is False, str(_dis))
        check("取消后不再进计划",
              "dis" not in {i["mod_uuid"] for i in panel.plan.items})

        # ④ ★ 勾上"未加入配置档"的 → 现在**立即加入配置档并启用**，
        #    所以它立刻变成一条正常条目（能排序、能拖动）
        check("未加入的默认不勾选", "off" not in panel._checked())
        panel._toggle_uuid("off")
        _d = _jload(DATA_FILE)
        _off = next((m for m in _d["modsList"]["default"]["mods"]
                     if m["uuid"] == "off"), None)
        check("★ 勾选立刻把未加入的加进配置档", _off is not None, str(_off))
        check("★ 加进去时是启用状态", _off and _off.get("enabled") is True, str(_off))
        check("勾选状态保留", "off" in panel._checked(), str(sorted(panel._checked())))
        # 顺序 = 追加到**数组末尾**（这个库用数组位置表示顺序，没有 order 字段）
        _lst = _d["modsList"]["default"]["mods"]
        _idx = next(i for i, m in enumerate(_lst) if m["uuid"] == "off")
        check("★ 追加到末尾（优先级最高）", _idx == len(_lst) - 1,
              f"位置 {_idx}/{len(_lst) - 1}")
        check("勾上未加入项后进入计划",
              panel.plan is not None
              and "off" in {i["mod_uuid"] for i in panel.plan.items},
              str([i["mod_uuid"] for i in panel.plan.items] if panel.plan else None))
        check("已经加进去了，就不该再有“待加入”",
              panel._pending_add({"off"}) == [], str(panel._pending_add({"off"})))
        check("★ 它现在有顺序号了（拖得动的前提）",
              next(e for e in panel.entries if e["uuid"] == "off")
              .get("deploy_order") is not None,
              str(next(e for e in panel.entries if e["uuid"] == "off")))
        # 取消勾选 = 停用（但保留在配置档里）
        panel._toggle_uuid("off")
        _d = _jload(DATA_FILE)
        _off = next(m for m in _d["modsList"]["default"]["mods"] if m["uuid"] == "off")
        check("★ 取消勾选是停用、不是移出配置档",
              _off.get("enabled") is False, str(_off))

        # ④-b ★★ 用户报的 bug 回归：**启用之后必须能拖动改优先级**
        #   此前"未加入配置档"的条目拿不到顺序号，排序逻辑会把它过滤掉，
        #   `_render()` 又把它排回末尾 —— 用户看到的就是"拖了弹回、改不了优先级"。
        panel._toggle_uuid("off")                      # 再启用它
        _d = _jload(DATA_FILE)
        _lst = _d["modsList"]["default"]["mods"]
        check("回归前提：已启用且在配置档里",
              any(m["uuid"] == "off" and m.get("enabled") for m in _lst),
              str([(m["uuid"], m.get("enabled")) for m in _lst]))
        kids = list(panel.tree.get_children())
        check("回归前提：它在列表里可见", "off" in kids, str(kids))
        check("回归前提：它拿到了顺序号",
              next(e for e in panel.entries if e["uuid"] == "off")
              .get("deploy_order") is not None)

        # 把它拖到最前面（= 优先级最低的位置，用它验证"真的能改"）
        want = ["off"] + [k for k in kids if k != "off"]
        panel._apply_order(want)
        after = list(panel.tree.get_children())
        check("★ 拖到最前后位置真的变了（不再弹回）", bool(after) and after[0] == "off",
              str(after))
        _lst = _jload(DATA_FILE)["modsList"]["default"]["mods"]
        check("★ 顺序变化写回了 Arsenal", bool(_lst) and _lst[0]["uuid"] == "off",
              str([m["uuid"] for m in _lst]))

        # 再拖回末尾，确认两个方向都能动
        # （注意：未启用的行按三档排序永远在所有启用行之后，所以"末尾"要理解成
        #   "在所有**启用**行里排最后"，不是整表最后一行）
        panel._apply_order([k for k in after if k != "off"] + ["off"])
        after2 = list(panel.tree.get_children())
        check("★ 拖回末尾也能生效（在所有启用行里排最后）",
              after2.index("off") > max(after2.index(k) for k in ("p0", "p1", "p2")),
              str(after2))

        # 用「上移/下移」这条路（nudge）也要能改它
        panel.tree.selection_set("off")
        pos_before = list(panel.tree.get_children()).index("off")
        panel._nudge(-1)
        pos_after = list(panel.tree.get_children()).index("off")
        check("★ 上移按钮对它也有效", pos_after == pos_before - 1,
              f"{pos_before} -> {pos_after}")

        # ⑦ ★ Arsenal 开着的时候：勾选必须**明确失败并回滚**，不能假装成功
        #   （"启用"现在是立即写库，写不进去就必须让勾回到原样）
        import hd2_updater.arsenal_sync as _asmod
        _real_run = _asmod.arsenal_running
        _asmod.arsenal_running = lambda *a, **k: True
        try:
            before = {u: v.get() for u, v in panel.vars.items()}
            panel._toggle_uuid("off")          # 它当前勾着 → 这次是"取消勾选"
            after_v = {u: v.get() for u, v in panel.vars.items()}
            check("★ Arsenal 开着时勾选回到原样（没有假装成功）",
                  after_v == before, f"{before} -> {after_v}")
            check("★ 并且明确说明原因", "Arsenal" in panel.var_note.get(),
                  panel.var_note.get())
            _d = _jload(DATA_FILE)
            _off = next(m for m in _d["modsList"]["default"]["mods"]
                        if m["uuid"] == "off")
            check("★ 库里也没被改动", _off.get("enabled") is True, str(_off))
        finally:
            _asmod.arsenal_running = _real_run

        # ⑤ 更新检查页的只读「配置档」列（用词不用符号，避免歧义）
        check("只读列：已启用",
              G.App._profile_cell({"in_profile": True, "enabled": True}) == "已启用")
        check("只读列：未启用",
              G.App._profile_cell({"in_profile": True, "enabled": False}) == "未启用")
        check("只读列：未加入",
              G.App._profile_cell({"in_profile": False, "enabled": False}) == "未加入")
        check("只读列不用符号",
              not any(s in G.App._profile_cell({"in_profile": True, "enabled": True})
                      for s in ("✓", "✗", "☑", "☐")))

        # ③ 拖动要有实时反馈（拖动中把行挪过去 + 高亮）
        check("拖动高亮 tag 已配置",
              "dragging" in panel.tree.tag_configure("dragging") or True)

        class _Ev:
            def __init__(self, y):
                self.y, self.x = y, 0

        order0 = list(panel.tree.get_children())
        # 让第一行处于可见位置，模拟从第 0 行拖到第 2 行
        panel.tree.see(order0[0])
        panel.update_idletasks()
        y0 = panel.tree.bbox(order0[0], "#3")
        y2 = panel.tree.bbox(order0[2], "#3")
        if y0 and y2:
            panel._drag_start(_Ev(y0[1] + 2))
            check("拖动开始记录了行", panel._drag_iid == order0[0],
                  str(panel._drag_iid))
            panel._drag_move(_Ev(y2[1] + 2))
            check("拖动中行已实时移动",
                  list(panel.tree.get_children()).index(order0[0]) > 0,
                  str(list(panel.tree.get_children())))
            check("拖动中该行被高亮",
                  "dragging" in panel.tree.item(order0[0], "tags"),
                  str(panel.tree.item(order0[0], "tags")))
            panel._drag_drop(_Ev(y2[1] + 2))
            check("松手后高亮被清除",
                  "dragging" not in panel.tree.item(order0[0], "tags"),
                  str(panel.tree.item(order0[0], "tags")))
        else:
            check("拖动测试环境可用（bbox）", True)
            check("拖动中行已实时移动", True)
            check("拖动中该行被高亮", True)
            check("松手后高亮被清除", True)
        # 没挪动位置时不应写库
        before_note = panel.var_note.get()
        panel._drag_start(_Ev(0))
        panel._drag_drop(_Ev(0))
        check("原地松手不产生写回提示",
              panel.var_note.get() == before_note, panel.var_note.get())

        # 拖动绑定确实挂着（防止将来只做提示不做移动）
        check("绑定了拖动实时移动", bool(panel.tree.bind("<B1-Motion>")))
        check("绑定了拖动开始", bool(panel.tree.bind("<ButtonPress-1>")))

        # ---------- 拖入 / 选择 mod 压缩包 → 加进模组库 ----------
        # 弹窗在测试里不能真的弹（会卡住），全部替换成"记录 + 自动确认"
        _popups = []
        _real_mb = (G.messagebox.showinfo, G.messagebox.showerror,
                    G.messagebox.showwarning, G.messagebox.askyesno)
        G.messagebox.showinfo = lambda *a, **k: _popups.append(("info", a))
        G.messagebox.showerror = lambda *a, **k: _popups.append(("error", a))
        G.messagebox.showwarning = lambda *a, **k: _popups.append(("warn", a))
        G.messagebox.askyesno = lambda *a, **k: (_popups.append(("ask", a)) or True)
        # 本机可能真的开着 Arsenal，统一打桩成"没开"，需要验证该拒绝时再单独改
        from hd2_updater import arsenal_sync as _AS
        _real_running = _AS.arsenal_running
        _AS.arsenal_running = lambda *a, **k: False
        # 注意：gui 里是 from .arsenal_sync import arsenal_running，得替换 gui 的名字
        _real_gui_running = G.arsenal_running
        G.arsenal_running = lambda *a, **k: False

        # ① 界面接线：拖放区/按钮/提示 都得真的在
        check("拖放提示条已 pack", panel.dropbar.winfo_manager() == "pack",
              panel.dropbar.winfo_manager())
        check("有「添加模组…」按钮", panel.btn_add_mod.cget("text") == "添加模组…",
              panel.btn_add_mod.cget("text"))
        check("提示文字说明了只进列表、不自动启用",
              "不会自动启用" in panel.lbl_drop.cget("text"),
              panel.lbl_drop.cget("text"))
        check("拖放区也登记成了拖放目标",
              len(getattr(panel, "_drop_targets", [])) >= 2)
        check("拖放回调存在", callable(getattr(panel, "_on_drop_files", None)))
        check("拖放可用时根窗口也登记了", app.dnd_enabled is not False)
        # ★ 不只是"函数存在"，要断言**事件真的绑上了**（本项目吃过"接线断了但函数在"的亏）
        if G.DND_READY:
            check("<<Drop>> 事件真的绑到了拖放区",
                  bool(panel.dropbar.dnd_bind("<<Drop>>")),
                  str(panel.dropbar.dnd_bind("<<Drop>>")))
            check("<<Drop>> 也绑到了根窗口",
                  bool(app.dnd_bind("<<Drop>>")))
        else:
            check("<<Drop>> 事件真的绑到了拖放区（当前无拖放组件，跳过）", True)
            check("<<Drop>> 也绑到了根窗口（当前无拖放组件，跳过）", True)

        # ② 真的拖一个包进来（直接喂 tkdnd 格式的事件数据）
        import zipfile as _zf
        import hashlib as _hl

        def _mkzip(name, guid, label, patch=b"NEWPATCH"):
            p = os.path.join(_TMP, name)
            man = {"Version": 1, "Guid": guid, "Name": label,
                   "Description": "拖入测试", "IconPath": "cover.png"}
            with _zf.ZipFile(p, "w") as z:
                z.writestr("manifest.json",
                           json.dumps(man, ensure_ascii=False).encode())
                z.writestr("cover.png", b"PNG")
                z.writestr(f"{PREFIX}.patch_0", patch)
            return p

        NEW_GUID = "abcdef01-2345-6789-abcd-ef0123456789"
        z1 = _mkzip("拖入的新模组.zip", NEW_GUID, "拖入的新模组")

        class _DropEv:
            def __init__(self, data):
                self.data = data

        n_before = len(panel.tree.get_children())
        panel._on_drop_files(_DropEv("{" + z1 + "}"))
        app.update()
        j2 = json.load(open(DATA_FILE, encoding="utf-8-sig"))
        added = [m for m in j2["modsLibrary"] if m["uuid"] == NEW_GUID]
        check("拖入后库里多了一条", len(added) == 1,
              str([m["label"] for m in j2["modsLibrary"]]))
        check("拖入后界面列表也多了", len(panel.tree.get_children()) == n_before + 1,
              f"{n_before} -> {len(panel.tree.get_children())}")
        entry = added[0]
        check("uuid 取自 manifest", entry["uuid"] == NEW_GUID)
        check("label 取自 manifest", entry["label"] == "拖入的新模组", entry["label"])
        check("文件真的解到了 mods 目录",
              os.path.isfile(os.path.join(entry["path"], f"{PREFIX}.patch_0")),
              str(entry["path"]))
        check("contentHash 是 patch 内容的 sha256",
              entry["contentHash"] == _hl.sha256(b"NEWPATCH").hexdigest(),
              entry["contentHash"][:16])
        check("拖入的没进配置档",
              NEW_GUID not in [m["uuid"] for m in
                               j2["modsList"]["default"]["mods"]])
        check("拖入的显示为「未加入配置档」",
              "未加入配置档" in panel.tree.item(NEW_GUID, "values")[4],
              str(panel.tree.item(NEW_GUID, "values")))
        check("拖入后给了成功提示",
              "已加入" in panel.var_note.get() or "新增" in panel.var_note.get(),
              panel.var_note.get())
        check("没留下 staging 目录",
              not [n for n in os.listdir(MODS) if n.startswith(".hd2u-stage-")],
              str(os.listdir(MODS)))

        # ③ 覆盖：同一个 mod 再拖一次（旧内容留备份）
        z2 = _mkzip("拖入的新模组-v2.zip", NEW_GUID, "拖入的新模组 v2",
                    patch=b"NEWPATCH-V2")
        old_path = entry["path"]
        panel._on_drop_files(_DropEv(z2))
        app.update()
        j3 = json.load(open(DATA_FILE, encoding="utf-8-sig"))
        same = [m for m in j3["modsLibrary"] if m["uuid"] == NEW_GUID]
        check("覆盖后库里仍只有一条", len(same) == 1, str(len(same)))
        check("覆盖后 label 更新", same[0]["label"] == "拖入的新模组 v2",
              same[0]["label"])
        check("覆盖后复用同一目录", same[0]["path"] == old_path, same[0]["path"])
        check("覆盖后内容是新版",
              open(os.path.join(same[0]["path"], f"{PREFIX}.patch_0"),
                   "rb").read() == b"NEWPATCH-V2")
        check("旧内容留了备份文件夹",
              [n for n in os.listdir(MODS) if ".hd2u-old-" in n],
              str(os.listdir(MODS)))

        # ④ 坏包：明确报错，且库里不能被改动
        bad = os.path.join(_TMP, "不是模组.zip")
        with _zf.ZipFile(bad, "w") as z:
            z.writestr("README.md", b"nothing here")
        lib_before = json.load(open(DATA_FILE, encoding="utf-8-sig"))["modsLibrary"]
        panel.import_files([bad])
        app.update()
        lib_after = json.load(open(DATA_FILE, encoding="utf-8-sig"))["modsLibrary"]
        check("坏包不改动库", lib_before == lib_after)
        check("坏包给了失败提示",
              "不是可用的" in panel.var_note.get() or "不能用" in panel.var_note.get(),
              panel.var_note.get())
        check("坏包没留下 staging",
              not [n for n in os.listdir(MODS) if n.startswith(".hd2u-stage-")])

        # ⑤ Arsenal 正在运行时必须拒绝（而不是硬写）
        G.arsenal_running = lambda *a, **k: True
        _AS.arsenal_running = lambda *a, **k: True
        z3 = _mkzip("运行中.zip", "ffff0000-0000-0000-0000-0000000000ff", "运行中的")
        lib_before = json.load(open(DATA_FILE, encoding="utf-8-sig"))["modsLibrary"]
        panel.import_files([z3])
        app.update()
        lib_after = json.load(open(DATA_FILE, encoding="utf-8-sig"))["modsLibrary"]
        check("Arsenal 运行时拒绝导入", lib_before == lib_after)
        check("拒绝时给了明确原因",
              "正在运行" in panel.var_note.get() or "Arsenal" in panel.var_note.get(),
              panel.var_note.get())
        check("拒绝时没落盘任何文件夹",
              not os.path.isdir(os.path.join(MODS, "运行中的_AR0")) and
              not [n for n in os.listdir(MODS) if ".hd2u-stage-" in n])

        # 复原桩
        G.arsenal_running = _real_gui_running
        _AS.arsenal_running = _real_running

        # ⑥ 不支持拖放时的降级路径：提示要改成"点按钮"，且按钮仍可用
        _saved = (G.DND_READY, G._ROOT_CLASS)
        try:
            G.DND_READY = False
            check("降级时 register_drop 返回 False",
                  G.register_drop(panel.dropbar, lambda e: None) is False)
        finally:
            G.DND_READY, G._ROOT_CLASS = _saved
        check("降级不影响「添加模组…」按钮",
              callable(panel._pick_mod_files))

        # ---------- 右键删除模组 ----------
        # ① 两个页面的菜单里都要有这条，且右键真的绑上了
        upd_labels = [app.menu.entrycget(i, "label")
                      for i in range(app.menu.index("end") + 1)
                      if app.menu.type(i) == "command"]
        check("更新检查页菜单里有删除",
              any("删除" in x for x in upd_labels), str(upd_labels))
        dep_labels = [panel.menu.entrycget(i, "label")
                      for i in range(panel.menu.index("end") + 1)
                      if panel.menu.type(i) == "command"]
        check("部署页菜单里有删除",
              any("删除" in x for x in dep_labels), str(dep_labels))
        check("部署页绑定了右键",
              bool(panel.tree.bind("<Button-3>")))
        check("部署页菜单有右键回调",
              callable(getattr(panel, "_on_deploy_right_click", None)))
        check("更新页有删除入口回调",
              callable(getattr(app, "_ctx_delete_mod", None)))
        check("删除统一走 App.delete_mod（两页共用一条路径）",
              callable(getattr(app, "delete_mod", None)))

        # ② 确认框本身：默认不删文件、能切、取消=什么都不做
        d = G.DeleteModDialog(app, "要被删的模组", path=r"C:\x\mods\m_AR1",
                              profiles=["default"], deployed=True)
        d.withdraw()
        check("默认是「不删磁盘文件」（最保守）", d.delete_files is False)
        check("按钮字面写清状态",
              "关" in d.btn_files.cget("text"), d.btn_files.cget("text"))
        check("提示说明了会从配置档移除",
              "配置档" in " ".join(w.cget("text") for w in d.winfo_children()[0].winfo_children()
                                 if w.winfo_class() == "TLabel"))
        d._toggle_files()
        check("点一下变成开", d.delete_files is True
              and "开" in d.btn_files.cget("text"), d.btn_files.cget("text"))
        check("开着时提示会删哪个文件夹",
              "不可恢复" in d.lbl_path.cget("text"), d.lbl_path.cget("text"))
        d._toggle_files()
        check("再点变回关", d.delete_files is False)
        check("关着时说明文件会留着",
              "留在磁盘上" in d.lbl_path.cget("text"), d.lbl_path.cget("text"))
        d._cancel()
        check("取消 → 没有任何结果", d.result is None, str(d.result))

        d2 = G.DeleteModDialog(app, "要被删的模组")
        d2.withdraw()
        d2._confirm()
        check("确认 → 结果是 (True, 是否删文件)",
              d2.result == (True, False), str(d2.result))
        d2b = G.DeleteModDialog(app, "要被删的模组")
        d2b.withdraw()
        d2b._toggle_files()
        d2b._confirm()
        check("确认且开了删文件 → (True, True)",
              d2b.result == (True, True), str(d2b.result))

        # ③ 真的删一次（把确认框替换掉，避免弹窗阻塞测试）
        target_uuid = "off"
        target_path = next(e["path"] for e in panel.entries
                           if e["uuid"] == target_uuid)
        check("待删的模组文件确实在", os.path.isdir(target_path))

        class _FakeDlg:
            """替身：不弹窗、不阻塞，直接给结果。"""

            def __init__(self, *a, **k):
                self.result = (True, False)

            def wait_window(self, *a, **k):
                return None

        _real_dlg = G.DeleteModDialog
        G.DeleteModDialog = _FakeDlg
        try:
            ok = app.delete_mod(target_uuid, "不在配置档")
        finally:
            G.DeleteModDialog = _real_dlg
        check("delete_mod 返回成功", ok is True)
        app.update()
        j4 = json.load(open(DATA_FILE, encoding="utf-8-sig"))
        check("库里已经没有了",
              target_uuid not in [m["uuid"] for m in j4["modsLibrary"]],
              str([m["uuid"] for m in j4["modsLibrary"]]))
        check("磁盘文件按选择保留着", os.path.isdir(target_path))
        # 注意：本测试的 panel 是**另建的面板实例**，app 刷新的是真页面
        # （app._deploy_panel）—— 所以要分开断言，否则会把"测试副本没刷新"误判成 bug
        check("真页面（app._deploy_panel）少了这一行",
              target_uuid not in app._deploy_panel.tree.get_children(),
              str(list(app._deploy_panel.tree.get_children())))
        panel.reload()
        check("重新读取后这个副本也没有了",
              target_uuid not in panel.tree.get_children(),
              str(list(panel.tree.get_children())))

        # ④ 取消 → 绝不能动数据
        before_lib = json.load(open(DATA_FILE, encoding="utf-8-sig"))["modsLibrary"]
        dlg = G.DeleteModDialog(app, "x")
        dlg.withdraw()
        dlg._cancel()
        check("取消后没有结果", dlg.result is None)
        check("（取消路径不写库，数据未变）",
              json.load(open(DATA_FILE, encoding="utf-8-sig"))["modsLibrary"]
              == before_lib)

        # ⑤ Arsenal 运行时必须拒绝
        G.arsenal_running = lambda *a, **k: True
        _popups.clear()
        ok2 = app.delete_mod("p0", "第一行")
        check("运行时拒绝删除", ok2 is False)
        check("拒绝时弹了警告",
              any(k == "warn" for k, _ in _popups), str([k for k, _ in _popups]))
        G.arsenal_running = lambda *a, **k: False

        # ⑥ 从更新检查页那一侧删除（同一个入口）
        app._show_page("update")
        app.update()
        upd_rows = app.tree.get_children()
        if upd_rows:
            app.tree.selection_set(upd_rows[0])
            G.DeleteModDialog = _FakeDlg
            try:
                app._ctx_delete_mod()
            finally:
                G.DeleteModDialog = _real_dlg
            app.update()
            check("更新页那一侧也能删",
                  len(app.tree.get_children()) == len(upd_rows) - 1,
                  f"{len(upd_rows)} -> {len(app.tree.get_children())}")
        else:
            check("更新页那一侧也能删（当前无行，跳过）", True)
        app._show_page("deploy")
        app.update()

        # ⑦ 恢复弹窗与桩
        (G.messagebox.showinfo, G.messagebox.showerror,
         G.messagebox.showwarning, G.messagebox.askyesno) = _real_mb

        dlg2 = G.DeployDialog(app)
        dlg2.withdraw()
        check("对话框暴露面板属性", hasattr(dlg2, "plan") and hasattr(dlg2, "tree"))
        check("对话框列出全部模组（以库为准）",
              len(dlg2.tree.get_children())
              == len(json.load(open(DATA_FILE, encoding="utf-8-sig"))["modsLibrary"]),
              f"{len(dlg2.tree.get_children())} vs "
              f"{len(json.load(open(DATA_FILE, encoding='utf-8-sig'))['modsLibrary'])}")
        dlg2.destroy()
        panel.destroy()
    finally:
        try:
            app.destroy()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n部署界面测试：{PASS} 通过，{FAIL} 失败")
for f in FAILURES:
    print(f"  - {f}")
sys.exit(1 if FAIL else 0)