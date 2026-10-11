# -*- coding: utf-8 -*-
"""与 Arsenal 库同步：镜像接管 + 安全写回 + 冲突检测。

**为什么需要这个模块**：本工具的部署和 Arsenal 的部署写的是同一批文件
（游戏 data 目录里的 ``<16hex>.patch_N``），两者相遇是"谁后部署谁生效"。
实测 Arsenal 部署时会 `Restoring game to pristine state...` 后按 ``.patch_``
通配删除全部 patch 文件（日志实证 `Removed 136 mod file(s)`），再写自
己的全量集合 —— 所以我们的部署会被它整体清掉，反之亦然。

因此本模块做三件事：
1. **镜像接管**（:func:`deployed_uuids`）：把 Arsenal 当前已部署的模组读出来，
   部署时默认纳入，避免"只勾一部分"把它们从游戏里抹掉。
2. **冲突检测**（:func:`arsenal_running`）：Arsenal 运行时**禁止写回** ——
   它把库读进内存，退出/保存时会覆盖我们的修改。
3. **安全写回**（:func:`sync_deployed_state`）：备份 + 原子替换 + 校验，
   让 Arsenal 界面上的"已部署"与实际一致。

⚠️ **写回不会让两者共存**。它只保证 Arsenal 的*显示*正确；只要用户在 Arsenal
里再点一次 Deploy，游戏目录仍会被它重写。界面必须如实告知这一点。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SNAPSHOT_NAME = "deployment_snapshot.json"
# 进程名（不带 .exe，比较时统一小写）
ARSENAL_PROCS = ("hd2arsenal",)
# arsenal_running 的结果缓存（见该函数说明：查进程要 ~360ms，不能每次操作都查）
_RUNNING_CACHE: dict = {"t": 0.0, "v": None}


class SyncError(Exception):
    """同步过程中的可预期错误，消息直接面向用户。"""


# ---------------------------------------------------------------- 冲突检测

def arsenal_running(max_age: float = 1.5) -> bool:
    """Arsenal 是否正在运行。

    Arsenal 把模组库读进内存，运行期间改它的库会在它保存时被覆盖 —— 所以
    写回前必须确认它没开。检测失败（拿不到进程列表）时**保守返回 True**：
    宁可拒绝写回，也不要冒覆盖用户数据的风险。

    ``max_age``：结果缓存多少秒。查进程要 spawn 一个 ``tasklist``，实测**每次约
    360ms** —— 而"改顺序"这类操作一次用户动作里会连着问好几遍（预览、确认、
    写库各一次），不缓存的话每次操作都卡三分之一秒，手感很差。
    缓存只在**同一次用户动作**的时间尺度内生效，用户看到的仍是刚刚探测的结果；
    传 ``max_age=0`` 可强制重新探测。
    """
    now = time.monotonic()
    if max_age > 0 and _RUNNING_CACHE["v"] is not None \
            and now - _RUNNING_CACHE["t"] <= max_age:
        return _RUNNING_CACHE["v"]

    result = _probe_arsenal()
    _RUNNING_CACHE["t"] = now
    _RUNNING_CACHE["v"] = result
    return result


def _probe_arsenal() -> bool:
    """真正去查一次进程表（不复用缓存）。

    优先用系统 API 直接枚举进程（``CreateToolhelp32Snapshot``，实测约 1ms）；
    起 ``tasklist`` 子进程要 ~360ms，每次改顺序都卡一下，手感很差。
    API 方式失败时退回 tasklist；两者都拿不到结果就**保守返回 True**（当作在运行）。
    """
    if not sys.platform.startswith("win"):
        return False
    got = _probe_by_snapshot()
    if got is not None:
        return got
    return _probe_by_tasklist()


def _probe_by_snapshot() -> bool | None:
    """用 Toolhelp32 快照枚举进程名。拿不到结果返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID = ctypes.c_void_p(-1).value

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32First.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(PROCESSENTRY32)]
        k32.Process32Next.argtypes = [wintypes.HANDLE,
                                      ctypes.POINTER(PROCESSENTRY32)]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID:
            return None
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if not k32.Process32First(snap, ctypes.byref(entry)):
                return None
            names = set()
            while True:
                names.add(entry.szExeFile.decode("mbcs", "replace").lower())
                if not k32.Process32Next(snap, ctypes.byref(entry)):
                    break
        finally:
            k32.CloseHandle(snap)
        return any(f"{p}.exe" in names for p in ARSENAL_PROCS)
    except Exception:  # noqa: BLE001
        return None


def _probe_by_tasklist() -> bool:
    """退回方案：起 tasklist 子进程（慢，约 360ms）。"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=flags,
        ).stdout or ""
    except Exception:  # noqa: BLE001
        # 拿不到进程列表 = 无法确认安全 → 当作在运行，不写
        return True
    low = out.lower()
    return any(f"{p}.exe" in low or f'"{p}.exe"' in low for p in ARSENAL_PROCS)


def running_warning() -> str:
    return ("HD2 Arsenal 正在运行。\n\n"
            "Arsenal 会把模组库读进内存，如果现在修改它的库文件，"
            "Arsenal 退出时会用内存里的内容覆盖掉我们的修改（甚至写坏）。\n\n"
            "请先完全关闭 Arsenal（包括右下角托盘图标），再回来操作。")


# ---------------------------------------------------------------- 镜像接管

def _load(data_file: str) -> dict:
    try:
        with open(data_file, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        raise SyncError(f"读取 Arsenal 模组库失败：{e}") from e


def current_profile(data: dict) -> str:
    """当前配置档名。selectedProfile 失效时退回第一个（与 arsenal.py 一致）。"""
    ml = data.get("modsList")
    if not isinstance(ml, dict) or not ml:
        return ""
    p = data.get("selectedProfile")
    return p if p in ml else next(iter(ml))


def deployed_uuids(data_file: str) -> list[str]:
    """Arsenal 当前标记为「已部署」的模组 uuid（镜像接管的输入）。

    返回**按加载顺序排列**的列表，方便调用方直接并入部署集合。
    """
    data = _load(data_file)
    prof = current_profile(data)
    if not prof:
        return []
    node = data.get("modsList", {}).get(prof)
    mods = node.get("mods") if isinstance(node, dict) else None
    if not isinstance(mods, list):
        return []
    return [m["uuid"] for m in mods
            if isinstance(m, dict) and m.get("uuid") and m.get("deployed")]


def deployed_labels(data_file: str) -> dict[str, str]:
    """{uuid: label} —— 只含 Arsenal 标记为已部署的。"""
    data = _load(data_file)
    prof = current_profile(data)
    node = data.get("modsList", {}).get(prof)
    mods = node.get("mods") if isinstance(node, dict) else None
    if not isinstance(mods, list):
        return {}
    return {m["uuid"]: (m.get("label") or "") for m in mods
            if isinstance(m, dict) and m.get("uuid") and m.get("deployed")}


# ---------------------------------------------------------------- 安全写回

def _backup(path: str) -> str:
    """把将要改写的文件复制一份（带时间戳），返回备份路径。

    ★ 时间戳只到秒，所以**同一秒内的连续写只留第一份备份** —— 这是必须的：
    部署页现在"勾选即启用"，用户连点十几下就会连写十几次，不这样的话会在
    用户目录里攒出几十个几乎一样的 .bak。
    保留**这一串改动开始之前**的那一份才是最有用的（真要还原就回到动手前）。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.hd2u-{stamp}.bak"
    if os.path.exists(dst):
        return dst
    shutil.copy2(path, dst)
    return dst


def _guard_against_real_write(path: str) -> None:
    """测试模式下的护栏：禁止写到**真实的** Arsenal 库。

    设了环境变量 ``HD2U_READONLY_GUARD=1`` 时（测试套件会设），如果目标路径不在
    临时目录里，就直接拒绝。

    为什么需要它：本次开发中曾有一次 ad-hoc 脚本在**未打桩**的情况下跑了真实库，
    把用户模组库的顺序改掉了（靠自动备份才发现并还原）。光靠"测试记得打桩"不可靠，
    所以加一道结构性护栏，让"测试意外写到真实库"变成不可能。
    """
    if not os.environ.get("HD2U_READONLY_GUARD"):
        return
    try:
        real = os.path.realpath(path)
    except OSError:
        real = os.path.abspath(path)
    tmp_roots = [os.path.realpath(tempfile.gettempdir())]
    for key in ("TEMP", "TMP", "TMPDIR"):
        v = os.environ.get(key)
        if v:
            tmp_roots.append(os.path.realpath(v))
    data_root = os.environ.get("HD2U_DATA_DIR")
    if data_root:
        tmp_roots.append(os.path.realpath(data_root))
    for root in tmp_roots:
        if root and os.path.realpath(real).lower().startswith(root.lower()):
            return
    raise SyncError(
        "测试护栏拦下了一次对真实 Arsenal 库的写入：\n  " + real +
        "\n\n（设了 HD2U_READONLY_GUARD=1，只允许写临时目录。）\n"
        "如果你确实想改真实库，请关掉该环境变量再操作。")


def _atomic_write_json(path: str, data) -> None:
    """原子写：先写临时文件再替换，避免写到一半崩掉留下半个 JSON。

    Arsenal 的库文件损坏会让它整个打不开 —— 这个代价必须避免。
    """
    _guard_against_real_write(path)
    # 调试用：设 HD2U_TRACE_WRITES=<文件路径> 可把"谁写了我"记下来
    _trace = os.environ.get("HD2U_TRACE_WRITES")
    if _trace:
        import traceback
        try:
            with open(_trace, "a", encoding="utf-8") as f:
                f.write(f"\n=== WRITE {path} @ {time.strftime('%H:%M:%S')} ===\n")
                f.write("".join(traceback.format_stack()[-6:]))
        except Exception:  # noqa: BLE001
            pass
    tmp = f"{path}.hd2u-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    # os.replace 在同盘上是原子操作
    os.replace(tmp, path)


def sync_deployed_state(data_file: str, deployed: set[str],
                        live_files: list[dict] | None = None,
                        force: bool = False) -> dict:
    """把「实际部署了哪些」写回 Arsenal 库，让它的显示与事实一致。

    :param deployed: 本次实际部署进游戏的 mod uuid 集合
    :param live_files: 游戏 data 目录里现存的部署文件 [{"name","size","modified"}]
                       —— 用于同步 deployment_snapshot.json，不传则跳过快照
    :param force: 跳过"Arsenal 正在运行"检查（仅供测试；正式流程不要用）

    只改 ``deployed`` / ``changed`` 两个标志和 ``deployment_snapshot.json``，
    **不动 enabled、顺序、路径等任何其它字段** —— 那些是用户的配置。

    ``enabled`` 保持原样是有意的：用户没勾的模组在 Arsenal 里仍是"启用但未部署"，
    正是 Arsenal 本来就有的状态（本机就有 4 个模组处在这个状态）。
    """
    if not force and arsenal_running():
        raise SyncError(running_warning())
    if not os.path.isfile(data_file):
        raise SyncError(f"找不到 Arsenal 模组库：{data_file}")

    backup = _backup(data_file)
    data = _load(data_file)
    prof = current_profile(data)
    if not prof:
        raise SyncError("Arsenal 库里没有可用的配置档，无法写回。")
    node = data.get("modsList", {}).get(prof)
    mods = node.get("mods") if isinstance(node, dict) else None
    if not isinstance(mods, list):
        raise SyncError("Arsenal 配置档结构异常，为安全起见没有写回。")

    changed_n = 0
    for m in mods:
        if not isinstance(m, dict):
            continue
        want = m.get("uuid") in deployed
        if bool(m.get("deployed")) != want:
            m["deployed"] = want
            changed_n += 1
        # 刚部署过 = 与当前状态一致，不再标"需要重新部署"
        if m.get("changed"):
            m["changed"] = False

    # 顶层记录当前生效配置档（Arsenal 自己也维护这个键）
    data["deployedProfile"] = prof

    # 校验：写之前先确认改完仍是合法 JSON 且结构完整
    if not isinstance(data.get("modsList"), dict):
        raise SyncError("写回前的结构校验未通过，已放弃（原文件未改动）。")

    _atomic_write_json(data_file, data)

    snap_path = os.path.join(os.path.dirname(data_file), SNAPSHOT_NAME)
    snap_written = False
    if live_files is not None:
        try:
            snap = {
                "timestamp": int(time.time() * 1000),
                "deployedMods": [
                    {"uuid": m.get("uuid") or "", "label": m.get("label") or "",
                     "patchFiles": []}
                    for m in mods if isinstance(m, dict) and m.get("uuid") in deployed
                ],
                "deployedFiles": live_files,
            }
            _atomic_write_json(snap_path, snap)
            snap_written = True
        except Exception:  # noqa: BLE001
            # 快照只是给 Arsenal 展示用，写失败不该让整个部署算失败
            snap_written = False

    return {"backup": backup, "changed": changed_n,
            "snapshot": snap_written, "profile": prof}


# ---------------------------------------------------------------- 选项编辑

def _walk_options(opts, fn) -> None:
    """递归遍历选项（含 suboptions），对每个 dict 调用 fn。"""
    for o in opts or []:
        if isinstance(o, dict):
            fn(o)
            _walk_options(o.get("suboptions"), fn)


def read_options(data_file: str, uuid: str) -> list[dict]:
    """读某个模组的选项树（含 suboptions），供界面勾选。

    返回 ``[{"name","description","enabled","suboptions":[...]}]``。
    读不到返回空列表 —— 界面上表现为"这个模组没有可选项"，不是错误。
    """
    data = _load(data_file)
    for m in data.get("modsLibrary") or []:
        if isinstance(m, dict) and m.get("uuid") == uuid:
            out: list[dict] = []

            def conv(o: dict) -> dict:
                sub = o.get("suboptions")
                return {
                    "name": o.get("name") or "",
                    "description": o.get("description") or "",
                    "enabled": bool(o.get("enabled", True)),
                    "include": o.get("include"),
                    "suboptions": [conv(s) for s in sub or []
                                   if isinstance(s, dict)],
                }

            for o in m.get("options") or []:
                if isinstance(o, dict):
                    out.append(conv(o))
            return out
    return []


def set_options(data_file: str, uuid: str, enabled_map: dict,
                force: bool = False) -> dict:
    """把选项开关写回 Arsenal 库。

    :param enabled_map: ``{"选项名": bool}``；带 ``suboptions`` 的组用
        ``"组名/子选项名"`` 作键。**只改键里出现的那些**，其余选项保持原样
        —— 不能因为界面没显示就把它们重置。

    与 :func:`sync_deployed_state` 一样：备份 + 原子写 + 只改该改的字段，
    并且**把改了选项的模组标记为 ``changed=True``** —— Arsenal 用这个标志提示
    "变了，需要重新部署"，我们照做，否则用户会看到"已部署"但选的还是旧选项。
    """
    if not force and arsenal_running():
        raise SyncError(running_warning())
    if not os.path.isfile(data_file):
        raise SyncError(f"找不到 Arsenal 模组库：{data_file}")
    if not enabled_map:
        raise SyncError("没有要修改的选项。")

    backup = _backup(data_file)
    data = _load(data_file)

    lib = data.get("modsLibrary") or []
    entry = next((m for m in lib
                  if isinstance(m, dict) and m.get("uuid") == uuid), None)
    if entry is None:
        raise SyncError(f"在 Arsenal 库里找不到这个模组（{uuid}），没有改动。")

    applied = 0
    missing: list[str] = []

    def apply(o: dict, prefix: str) -> None:
        nonlocal applied
        key = prefix + (o.get("name") or "")
        if key in enabled_map:
            want = bool(enabled_map[key])
            if bool(o.get("enabled", True)) != want:
                o["enabled"] = want
                applied += 1
        elif key not in enabled_map and prefix:
            # 子选项：整组没被提到时不动它
            pass
        for s in o.get("suboptions") or []:
            if isinstance(s, dict):
                apply(s, key + "/")

    for o in entry.get("options") or []:
        if isinstance(o, dict):
            apply(o, "")

    # 检查有没有写了但树上不存在的键（拼错/已改名的选项）
    known = set()

    def collect(o: dict, prefix: str) -> None:
        k = prefix + (o.get("name") or "")
        known.add(k)
        for s in o.get("suboptions") or []:
            if isinstance(s, dict):
                collect(s, k + "/")
    for o in entry.get("options") or []:
        if isinstance(o, dict):
            collect(o, "")
    missing = [k for k in enabled_map if k not in known]

    if applied:
        # 选项变了 → Arsenal 那边应显示"需要重新部署"
        prof = current_profile(data)
        node = data.get("modsList", {}).get(prof)
        mods = node.get("mods") if isinstance(node, dict) else None
        if isinstance(mods, list):
            for m in mods:
                if isinstance(m, dict) and m.get("uuid") == uuid:
                    m["changed"] = True
        _atomic_write_json(data_file, data)

    return {"backup": backup, "applied": applied, "missing": missing,
            "changed_file": bool(applied)}


def option_summary(data_file: str, uuid: str) -> str:
    """一句话描述选项状态，如「21 个选项，全部启用」。"""
    opts = read_options(data_file, uuid)
    if not opts:
        return "没有可选项"
    total = 0
    off = 0

    def walk(items) -> None:
        nonlocal total, off
        for o in items:
            total += 1
            if not o.get("enabled"):
                off += 1
            walk(o.get("suboptions") or [])
    walk(opts)
    if off == 0:
        return f"{total} 个选项，全部启用"
    return f"{total} 个选项，{off} 个已关闭"


def add_to_profile(data_file: str, uuid: str, enabled: bool = True,
                   force: bool = False) -> dict:
    """把模组库里的一个模组**加入当前配置档**（追加到列表末尾）。

    用途：用户库里有模组"还没加入配置档"（HD2 Arsenal 里表现为库里有、当前档里没有），
    在部署页勾选它们时要把它们放进去，否则部署不了（配置档里没有 = 没有加载顺序）。

    **怎么构造条目**：实测本机 18 个配置档条目**恰好 = 库条目 + 固定 6 个字段**
    （``enabled`` / ``deployed`` / ``changed`` / ``new`` / ``copiedFiles`` /
    ``renamedFiles``），且共享字段的值与库条目**逐字段完全一致**。所以做法是
    **照抄库条目 + 补这 6 个默认值**，不猜字段、不丢字段。

    **位置**：追加到末尾。Arsenal 默认设置下越靠后优先级越高，所以新加入的会拿到
    最高优先级 —— 这与 Arsenal 自己"从库添加"的行为一致，用户可在部署页拖动调整。

    **只做"加入"，不做"移除"** —— 移除是删条目、可能牵动它的部署记录，风险更大，
    需要时请回 Arsenal 操作。
    """
    if not force and arsenal_running():
        raise SyncError(running_warning())
    if not os.path.isfile(data_file):
        raise SyncError(f"找不到 Arsenal 模组库：{data_file}")

    backup = _backup(data_file)
    data = _load(data_file)
    prof = current_profile(data)
    node = data.get("modsList", {}).get(prof)
    mods = node.get("mods") if isinstance(node, dict) else None
    if not isinstance(mods, list):
        raise SyncError("Arsenal 配置档结构异常，为安全起见没有改动。")

    if any(isinstance(m, dict) and m.get("uuid") == uuid for m in mods):
        return {"backup": backup, "added": False, "reason": "已在配置档里"}

    lib = data.get("modsLibrary") or []
    entry = next((m for m in lib
                  if isinstance(m, dict) and m.get("uuid") == uuid), None)
    if entry is None:
        raise SyncError(f"模组库里没有这个模组（{uuid}），无法加入配置档。")

    # 以现有配置档条目的键顺序为模板 —— 保证键名与顺序都和 Arsenal 自己写的一致
    template = next((m for m in mods if isinstance(m, dict)), None)
    extra = {"enabled": bool(enabled), "deployed": False, "changed": False,
             "new": False, "copiedFiles": [], "renamedFiles": []}
    if template:
        new_entry = {}
        for k in template:
            if k in entry:
                new_entry[k] = entry[k]
            elif k in extra:
                new_entry[k] = extra[k]
        # 模板里没出现的库字段也要保留，不能丢
        for k in entry:
            if k not in new_entry:
                new_entry[k] = entry[k]
    else:
        new_entry = dict(entry)
        new_entry.update(extra)

    # 校验：必须覆盖库条目的全部字段，且那 6 个字段都在
    missing = [k for k in entry if k not in new_entry]
    if missing:
        raise SyncError(f"构造配置档条目时丢了字段 {missing}，已放弃（原文件未改动）。")
    for k in extra:
        if k not in new_entry:
            new_entry[k] = extra[k]

    mods.append(new_entry)
    if len(mods) != len(data["modsList"][prof]["mods"]):
        raise SyncError("加入后数量校验未通过，已放弃（原文件未改动）。")

    _atomic_write_json(data_file, data)
    return {"backup": backup, "added": True, "order": len(mods) - 1,
            "enabled": bool(enabled)}


def set_enabled(data_file: str, uuid: str, enabled: bool,
                force: bool = False) -> dict:
    """把某个模组在配置档里的启用开关写回 Arsenal。

    部署页要能直接启用"没启用的模组"（用户明确要求），否则他还得回 Arsenal 里点一次。
    只改这一个模组的 ``enabled``；启用时顺带把 ``changed`` 标为 True（Arsenal 据此
    提示需要重新部署）。
    """
    if not force and arsenal_running():
        raise SyncError(running_warning())
    if not os.path.isfile(data_file):
        raise SyncError(f"找不到 Arsenal 模组库：{data_file}")

    backup = _backup(data_file)
    data = _load(data_file)
    prof = current_profile(data)
    node = data.get("modsList", {}).get(prof)
    mods = node.get("mods") if isinstance(node, dict) else None
    if not isinstance(mods, list):
        raise SyncError("Arsenal 配置档结构异常，为安全起见没有改动。")

    target = next((m for m in mods
                   if isinstance(m, dict) and m.get("uuid") == uuid), None)
    if target is None:
        raise SyncError("这个模组不在 Arsenal 当前配置档里，无法在这里启用。\n"
                        "请先在 Arsenal 中把它加入配置档。")

    changed = bool(target.get("enabled")) != bool(enabled)
    if changed:
        target["enabled"] = bool(enabled)
        if enabled:
            target["changed"] = True
        _atomic_write_json(data_file, data)
    return {"backup": backup, "changed": changed, "enabled": bool(enabled)}


# ---------------------------------------------------------------- 加载顺序

def read_order(data_file: str) -> list[dict]:
    """当前配置档的加载顺序：``[{"uuid","label","enabled","deployed"}]``，
    数组顺序即 Arsenal 的排列顺序（默认设置下**越靠后优先级越高**）。"""
    data = _load(data_file)
    prof = current_profile(data)
    node = data.get("modsList", {}).get(prof)
    mods = node.get("mods") if isinstance(node, dict) else None
    if not isinstance(mods, list):
        return []
    return [{"uuid": m.get("uuid") or "", "label": m.get("label") or "",
             "enabled": bool(m.get("enabled")),
             "deployed": bool(m.get("deployed"))}
            for m in mods if isinstance(m, dict) and m.get("uuid")]


def set_order(data_file: str, ordered_uuids: list[str],
              force: bool = False) -> dict:
    """按给定顺序重排当前配置档的模组（默认设置下**越靠后优先级越高**）。

    只重排 ``modsList.<profile>.mods`` 数组，**不动数组里每个元素的内容**。

    ★ 关键语义：**只在被提到模组原本占据的槽位之间做置换**，其余模组**原地不动**。

    早期实现是"未提到的排前面 + 被提到的按给定顺序排后面" —— 那等于**擅自改了
    未提到的模组的优先级**。实测踩过：部署页当时只列出已启用的模组，用户拖动排序后
    那两个未启用的模组被挪到了列表最前面，用户的库被无声改动（靠备份才发现）。
    现在的做法：取出被提到模组的槽位下标（升序），把新顺序依次填进去，别的都不碰。
    全覆盖时（界面总是传全部行）结果与整体重排完全一致。
    """
    if not force and arsenal_running():
        raise SyncError(running_warning())
    if not os.path.isfile(data_file):
        raise SyncError(f"找不到 Arsenal 模组库：{data_file}")
    if not ordered_uuids:
        raise SyncError("没有新的顺序，未做改动。")

    backup = _backup(data_file)
    data = _load(data_file)
    prof = current_profile(data)
    node = data.get("modsList", {}).get(prof)
    mods = node.get("mods") if isinstance(node, dict) else None
    if not isinstance(mods, list):
        raise SyncError("Arsenal 配置档结构异常，为安全起见没有改动顺序。")

    by_uuid = {m.get("uuid"): i for i, m in enumerate(mods)
               if isinstance(m, dict) and m.get("uuid")}
    want = [u for u in ordered_uuids if u in by_uuid]
    if not want:
        raise SyncError("给的新顺序里没有一个 uuid 能在配置档里找到，未做改动。")

    # 被提到模组原本占用的槽位（升序）—— 只在这些位置之间重排
    slots = sorted(by_uuid[u] for u in want)
    new_mods = list(mods)
    for slot, uuid in zip(slots, want):
        new_mods[slot] = mods[by_uuid[uuid]]

    # 元素总数必须一致 —— 少了就是写丢，宁可放弃
    if len(new_mods) != len(mods):
        raise SyncError("重排后的模组数量与原来不一致，已放弃（原文件未改动）。")
    # 再核一遍：内容集合必须完全相同（防止上面的映射写错）
    if sorted(id(x) for x in new_mods) != sorted(id(x) for x in mods):
        raise SyncError("重排结果与原始内容不一致，已放弃（原文件未改动）。")

    node["mods"] = new_mods
    _atomic_write_json(data_file, data)
    return {"backup": backup, "count": len(new_mods), "moved": len(want)}


def live_file_entries(data_dir: str, prefix: str) -> list[dict]:
    """扫描游戏 data 目录，生成 Arsenal 快照格式的文件清单。"""
    out: list[dict] = []
    try:
        names = os.listdir(data_dir)
    except OSError:
        return out
    for n in sorted(names):
        if not n.startswith(f"{prefix}.patch_"):
            continue
        p = os.path.join(data_dir, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"name": n, "size": st.st_size,
                    "modified": int(st.st_mtime * 1000)})
    return out


def describe_sync(result: dict) -> str:
    """把写回结果整理成一句人话。"""
    parts = [f"已在 Arsenal 里更新 {result.get('changed', 0)} 个模组的部署状态"]
    if result.get("snapshot"):
        parts.append("部署快照已同步")
    if result.get("backup"):
        parts.append(f"原文件已备份为 {os.path.basename(result['backup'])}")
    return "；".join(parts) + "。"


# ------------------------------------------------- 模组库（modsLibrary）的新增/替换
# 库条目实测有 12 个字段（21/21 完全统一）：
#   uuid / path / label / description / iconPath / tags / patchFileNames /
#   nexusData / options / contentHash / addedAt / previousVersions
LIBRARY_FIELDS = ("uuid", "path", "label", "description", "iconPath", "tags",
                  "patchFileNames", "nexusData", "options", "contentHash",
                  "addedAt", "previousVersions")
# 配置档条目 = 库条目 + 这 6 个字段（18/18 实测统一）
PROFILE_EXTRA_FIELDS = ("enabled", "deployed", "changed", "new",
                        "copiedFiles", "renamedFiles")


def mods_dir_of(data: dict, data_file: str) -> str:
    """从 Arsenal 数据里取"模组安装目录"（``userModsDir``）。

    这是权威来源 —— 不要猜它一定在数据文件旁边。
    """
    d = data.get("userModsDir")
    if isinstance(d, str) and d.strip():
        return os.path.normpath(d)
    return os.path.join(os.path.dirname(os.path.abspath(data_file)), "mods")


def upsert_library_mod(data_file: str, entry: dict,
                       force: bool = False) -> dict:
    """把一条库条目**新增或替换**进 ``modsLibrary``；若该模组也在配置档里，同步那份副本。

    这是本工具**唯一**会动 ``modsLibrary`` 的函数，用于"把拖进来的 mod 加入列表"。

    ★ 三个安全要点：
    ① **只增/替换目标那一条**，其余条目一个字节都不碰；
    ② **键名与顺序照抄现有条目**（模板法）—— 保证和 Arsenal 自己写的形状一致；
    ③ 配置档里那份是**独立的副本**（实测：配置档条目 = 库条目 + 6 个字段，值逐字段相同），
       所以改了库还必须同步改配置档，否则配置档会指向**旧路径**、部署时找不到文件。
       同步时那 6 个"配置档专属字段"原样保留，只把 ``changed`` 置 True（提示需要重新部署）。

    ``entry`` 必须含 :data:`LIBRARY_FIELDS` 全部字段（由 modimport 构造并校验）。
    """
    if not force and arsenal_running():
        raise SyncError(running_warning())
    if not os.path.isfile(data_file):
        raise SyncError(f"找不到 Arsenal 模组库：{data_file}")

    missing = [k for k in LIBRARY_FIELDS if k not in entry]
    if missing:
        raise SyncError(f"要写入的库条目缺少字段 {missing}，已放弃（原文件未改动）。")
    uuid = entry.get("uuid")
    if not uuid:
        raise SyncError("要写入的库条目没有 uuid，已放弃（原文件未改动）。")

    backup = _backup(data_file)
    data = _load(data_file)
    lib = data.get("modsLibrary")
    if not isinstance(lib, list):
        raise SyncError("Arsenal 模组库结构异常，为安全起见没有改动。")

    # 以现有条目的键顺序为模板，保证形状与 Arsenal 自己写的一致
    template = next((m for m in lib if isinstance(m, dict)), None)
    if template:
        shaped = {}
        for k in template:
            if k in entry:
                shaped[k] = entry[k]
        for k in LIBRARY_FIELDS:            # 模板里没有的也不能丢
            if k in entry and k not in shaped:
                shaped[k] = entry[k]
    else:
        shaped = {k: entry.get(k) for k in LIBRARY_FIELDS}
    still = [k for k in LIBRARY_FIELDS if k not in shaped]
    if still:
        raise SyncError(f"构造库条目时丢了字段 {still}，已放弃（原文件未改动）。")

    idx = next((i for i, m in enumerate(lib)
                if isinstance(m, dict) and m.get("uuid") == uuid), None)
    added = idx is None
    before_count = len(lib)
    if added:
        lib.append(shaped)
    else:
        lib[idx] = shaped                    # 覆盖旧的（Arsenal 自己也是直接覆盖）
    if len(lib) != before_count + (1 if added else 0):
        raise SyncError("库条目数量校验未通过，已放弃（原文件未改动）。")

    # 同步配置档里的那份副本
    prof = current_profile(data)
    node = data.get("modsList", {}).get(prof)
    mods = node.get("mods") if isinstance(node, dict) else None
    profile_updated = False
    if isinstance(mods, list):
        pidx = next((i for i, m in enumerate(mods)
                     if isinstance(m, dict) and m.get("uuid") == uuid), None)
        if pidx is not None:
            old = mods[pidx]
            extra = {k: old.get(k, True if k == "enabled" else
                                (False if k in ("deployed", "changed", "new") else []))
                     for k in PROFILE_EXTRA_FIELDS}
            extra["changed"] = True          # 内容变了 → 需要重新部署
            ptmpl = next((m for m in mods if isinstance(m, dict)), None)
            if ptmpl:
                newp = {}
                for k in ptmpl:
                    if k in shaped:
                        newp[k] = shaped[k]
                    elif k in extra:
                        newp[k] = extra[k]
                for k in list(shaped) + list(extra):
                    if k not in newp:
                        newp[k] = shaped.get(k, extra.get(k))
            else:
                newp = dict(shaped)
                newp.update(extra)
            pmiss = [k for k in list(shaped) + list(extra) if k not in newp]
            if pmiss:
                raise SyncError(f"同步配置档时丢了字段 {pmiss}，已放弃"
                                "（原文件未改动）。")
            if len(newp) != len(old):
                raise SyncError("同步配置档时字段数变化，已放弃（原文件未改动）。")
            mods[pidx] = newp
            profile_updated = True

    _atomic_write_json(data_file, data)
    return {"backup": backup, "added": added, "replaced": not added,
            "profile_updated": profile_updated, "uuid": uuid}


def library_uuids(data_file: str) -> list[str]:
    """当前模组库里所有模组的 uuid（用于判断"拖进来的这个是不是已经在列表里"）。"""
    if not os.path.isfile(data_file):
        return []
    data = _load(data_file)
    return [m.get("uuid") for m in (data.get("modsLibrary") or [])
            if isinstance(m, dict) and m.get("uuid")]


def profile_names_with(data_file: str, uuid: str) -> list[str]:
    """哪些配置档里有这个模组（删除前拿它给用户交代清楚）。"""
    if not os.path.isfile(data_file):
        return []
    data = _load(data_file)
    out = []
    for prof, node in (data.get("modsList") or {}).items():
        mods = node.get("mods") if isinstance(node, dict) else None
        if isinstance(mods, list) and any(
                isinstance(m, dict) and m.get("uuid") == uuid for m in mods):
            out.append(prof)
    return out


def remove_library_mod(data_file: str, uuid: str, delete_files: bool = False,
                       force: bool = False) -> dict:
    """把一个模组从模组库里删掉（可选连磁盘文件一起删）。

    ★ **必须同时删配置档里那份副本**：实测配置档条目 = 库条目 + 6 个字段的**独立副本**
    （不是引用），里面带着自己的 ``path``/``options``。只删库的话，配置档里会留下一条
    "僵尸条目" —— 它仍然指向磁盘上那个文件夹，仍然可能被部署进游戏，用户会觉得
    "删了但没删掉"。所以这里把**所有配置档**里同 uuid 的条目一并移除。

    对照 Arsenal 自己的实现：它的 ``removeFromLibrary(uuid, b)`` 里 ``b`` 就是
    "要不要连文件夹一起删"（默认 false），但它只 splice 库、不管配置档 ——
    所以我们比它多做了配置档这一步，是为了让"删除"名副其实。

    ``delete_files`` 为真时才删磁盘文件夹，且**只允许删 mods 目录里面的**，
    其它路径一律拒绝（避免手滑删掉别处的东西）。
    """
    if not force and arsenal_running():
        raise SyncError(running_warning())
    if not os.path.isfile(data_file):
        raise SyncError(f"找不到 Arsenal 模组库：{data_file}")

    data = _load(data_file)
    lib = data.get("modsLibrary")
    if not isinstance(lib, list):
        raise SyncError("Arsenal 模组库结构异常，为安全起见没有改动。")

    idx = next((i for i, m in enumerate(lib)
                if isinstance(m, dict) and m.get("uuid") == uuid), None)
    if idx is None:
        raise SyncError("这个模组不在 Arsenal 的模组库里，可能已经被删掉了。")

    entry = lib[idx]
    label = entry.get("label") or uuid
    path = entry.get("path") or ""

    # 先在内存里把该删的都删干净，确认结构没问题再落盘
    del lib[idx]

    removed_profiles = []
    for prof, node in (data.get("modsList") or {}).items():
        mods = node.get("mods") if isinstance(node, dict) else None
        if not isinstance(mods, list):
            continue
        keep = [m for m in mods
                if not (isinstance(m, dict) and m.get("uuid") == uuid)]
        if len(keep) != len(mods):
            node["mods"] = keep
            removed_profiles.append(prof)

    # 删磁盘文件：只认 mods 目录里面的路径
    deleted_folder = ""
    if delete_files and path:
        mods_dir = os.path.normpath(mods_dir_of(data, data_file))
        target = os.path.normpath(path)
        inside = target.lower().startswith(mods_dir.lower() + os.sep.lower())
        if not inside:
            raise SyncError(
                "这个模组的位置不在 Arsenal 的模组目录里，为安全起见没有删它的文件：\n"
                f"  {target}\n（列表里的条目仍会被移除。）")
        if os.path.isdir(target):
            try:
                shutil.rmtree(target)
                deleted_folder = target
            except OSError as e:
                raise SyncError(
                    f"没能删掉模组文件夹（可能被游戏或 Arsenal 占用）：\n{e}") from e

    # 备份放在真正落盘之前 —— 上面任何一步拒绝（比如路径不在 mods 目录里）
    # 都不该在工作目录里留下一个没用的 .bak
    backup = _backup(data_file)
    _atomic_write_json(data_file, data)
    return {"backup": backup, "label": label, "path": path,
            "removed_profiles": removed_profiles,
            "deleted_folder": deleted_folder, "uuid": uuid,
            "was_in_profile": bool(removed_profiles)}

