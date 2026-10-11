# -*- coding: utf-8 -*-
"""把选中的模组部署到游戏 data 目录 —— 纯文件复制，不做二进制合并。

机制（全部来自对本机 Arsenal 部署产物的实测，见项目记忆）：
  1. 所有模组的 patch 文件前缀都是同一个 ``<16hex>``，靠后缀序号决定加载先后，
     后加载者赢。
  2. 模组目录里每个**子目录**是一个槽位（``options\\<选项名>\\<16hex>.patch_0`` 三件套），
     目录名对应配置里的 ``options[].include``。
  3. ``modsList`` 数组顺序 = 加载优先级，**默认设置下越靠后优先级越高**（后加载者覆盖先加载者）。

所以部署 = 清掉 data 目录里的 ``<16hex>.patch_*`` → 按优先级**倒序**分配序号后逐字节复制
→ 写一份清单。序号倒序是关键：若按列表顺序递增编号，加载优先级会反过来，
Bingus 这类 loader 会被排到最前，游戏会崩。

⚠️ 本模块会写游戏目录。调用前必须确认已备份。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

# <16 进制>.patch_<序号> 及其伴生文件（.gpu_resources / .stream）
_PATCH_RE = re.compile(r"^([0-9a-fA-F]{16})\.patch_(\d+)((?:\.[A-Za-z0-9_]+)?)$")
MANIFEST_NAME = ".hd2u-deployed.json"
BACKUP_DIR_NAME = "hd2u_backup"

# 伴生后缀：复制主文件时若源目录缺哪个，就写一个空占位，保证序号连续
COMPANION_SUFFIXES = (".gpu_resources", ".stream")


class DeployError(Exception):
    """部署过程中的可预期错误，消息直接面向用户。"""


class DeployPlan:
    """一次部署的完整计划 —— 只描述要做什么，不碰磁盘。"""

    def __init__(self, prefix: str, items: list[dict], game_dir: str):
        self.prefix = prefix
        self.items = items          # [{index, mod_label, src_dir, files:{后缀:源文件或None}}]
        self.game_dir = game_dir

    @property
    def slot_count(self) -> int:
        return len(self.items)

    @property
    def total_bytes(self) -> int:
        n = 0
        for it in self.items:
            for src in it["files"].values():
                if src:
                    try:
                        n += os.path.getsize(src)
                    except OSError:
                        pass
        return n

    def target_name(self, index: int, suffix: str = "") -> str:
        return f"{self.prefix}.patch_{index}{suffix}"

    def summary(self) -> str:
        mods = len({it["mod_label"] for it in self.items})
        return f"{mods} 个模组、{self.slot_count} 个槽位、约 {self.total_bytes / 1048576:.1f} MB"


def prefixes_in_tree(root: str) -> set[str]:
    """扫一棵目录树，返回其中所有 patch 文件的 16 位前缀（小写）。"""
    found: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if m := _PATCH_RE.match(fn):
                found.add(m.group(1).lower())
    return found


def find_prefix(game_dir: str, mods: list[str] | None = None) -> str:
    """推断部署文件前缀。

    优先用**模组目录自己**的前缀 —— 那是即将写入的内容，且在从未部署过时也存在；
    没有选中模组时才退回看游戏 data 目录里现存的部署产物。两者都拿不到、或出现
    多个前缀（别的工具也在部署），一律报错，不猜。
    """
    if mods:
        found: set[str] = set()
        for p in mods:
            if p and os.path.isdir(p):
                found |= prefixes_in_tree(p)
        if len(found) == 1:
            return found.pop()
        if len(found) > 1:
            raise DeployError(
                "选中的模组使用了不同的文件前缀，无法部署到同一组文件：\n  "
                + "\n  ".join(sorted(found))
                + "\n它们可能来自不同的模组格式或不同的游戏版本。"
            )
    try:
        names = os.listdir(game_dir)
    except OSError as exc:
        raise DeployError(f"读不到游戏目录：{exc}") from exc
    prefixes = {m.group(1).lower() for n in names if (m := _PATCH_RE.match(n))}
    if not prefixes:
        raise DeployError(
            "无法确定模组部署文件的前缀：选中的模组里没有可识别的部署文件，"
            "游戏 data 目录里也没有。\n请确认游戏路径正确，且模组包完整。"
        )
    if len(prefixes) > 1:
        # 多个前缀 = 有别的工具也在部署。不猜，直接交给用户决定。
        raise DeployError(
            "游戏 data 目录里存在多个不同的模组文件前缀：\n  "
            + "\n  ".join(sorted(prefixes))
            + "\n可能有别的模组管理器也在部署。为避免互相覆盖，已停止。"
        )
    return prefixes.pop()


def game_data_dir(game_dir: str) -> str:
    """模组部署目标是游戏根目录下的 data 子目录。"""
    d = os.path.join(game_dir, "data")
    if not os.path.isdir(d):
        raise DeployError(f"游戏目录里没有 data 子目录：{d}")
    return d


def option_include_names(entry: dict, enabled_only: bool = True) -> list[str]:
    """模组配置里各选项（含子选项）的 include 名，即模组目录里的子目录名。

    必须**递归进 suboptions**：实测 Helmet Headlamp 的 ``include`` 是空的，
    真正的目录名写在它的子选项里（``Default Mode - Task Light`` /
    ``Default Mode - Gameplay``），且这两者是**单选组** —— 一个启用一个禁用。
    只读顶层 include 会把整个 mod 的槽位判成 0，进而误删它的全部内容。

    ``include`` 可能是字符串或字符串数组（实测两种都有），统一成列表。
    """
    out: list[str] = []

    def walk(opts) -> None:
        for o in opts or []:
            if not isinstance(o, dict):
                continue
            if enabled_only and not o.get("enabled", True):
                continue
            inc = o.get("include")
            if isinstance(inc, str):
                out.append(inc)
            elif isinstance(inc, (list, tuple)):
                out.extend(str(x) for x in inc)
            walk(o.get("suboptions"))

    walk(entry.get("options"))
    return [x for x in out if x]


def collect_slots(mod_path: str, entry: dict | None = None) -> list[str]:
    """列出该模组目录下所有含 patch 文件的子目录（每个 = 一个槽位）。

    按子目录分组而非按文件名 —— 同一模组内可能有多个子目录装着**同名同前缀**的
    patch_0，只有子目录能区分它们。

    给了 ``entry`` 时按它的选项开关过滤：**用户在 Arsenal 里禁用的选项不会被部署**
    （否则"禁用了却仍装进游戏"，与 Arsenal 行为不符）。实测本机所有选项都是启用状态，
    所以这条分支平时不改变结果，但禁用选项是 Arsenal 支持的正常用法，必须照做。
    """
    if not mod_path or not os.path.isdir(mod_path):
        return []
    groups: dict[str, str] = {}  # 目录绝对路径 -> 主文件名
    for dirpath, _dirnames, filenames in os.walk(mod_path):
        for fn in filenames:
            if not _PATCH_RE.match(fn):
                continue
            if fn.endswith(COMPANION_SUFFIXES):
                continue
            groups[dirpath] = fn

    if entry is not None:
        incs = option_include_names(entry, enabled_only=True)
        if incs:
            # 只保留路径里出现某个启用选项名的槽位（大小写不敏感、按路径段匹配）
            def keep(p: str) -> bool:
                rel = os.path.relpath(p, mod_path).lower()
                parts = set(rel.split(os.sep))
                return any(i.lower() in parts or i.lower() in rel for i in incs)
            filtered = {p: f for p, f in groups.items() if keep(p)}
            # 过滤后若一个都不剩，宁可报出全部（说明 include 命名与目录对不上），
            # 也不要静默部署出空集合 —— 那会让用户的模组莫名其妙消失。
            if filtered:
                groups = filtered

    # 按相对路径排序，保证同一输入产出同一部署顺序
    return sorted(groups, key=lambda p: p.lower())


def build_plan(library: list[dict], selected_uuids: set[str], game_dir: str,
               prefix: str | None = None, first_mod_priority: bool = False) -> DeployPlan:
    """按加载优先级分配槽位编号，生成部署计划。

    ``library`` 需是 :func:`arsenal.load_library` 的输出（含 deploy_order/enabled）。

    **编号方向（实测锚定，别想当然）**：Arsenal 的 ``modsList`` 数组位次 0 对应它自己
    写出的 ``patch_0``，即**顺序编号**；游戏按编号递增加载、后加载者覆盖先加载者，
    所以**列表越靠后 = 编号越大 = 越后加载 = 优先级越高**。
    实测反查：位次 0→patch_0、位次 13→patch_19、位次 17(Bingus loader)→patch_44，
    且 Bingus 的 README 写明「默认设置下把 loader 放最后 = winning priority」。
    这也正是 DDMM 的做法（profile 列表顺序递增重编号）。

    ``first_mod_priority`` 对应 Arsenal 的 ``setTopPriority``：若用户开了
    「第一个模组优先」，则方向反过来（位次 0 拿最大编号），与 mod 作者 README 里
    「若启用 first-mod priority，把 loader 放最前」的说法一致。
    """
    data_dir = game_data_dir(game_dir)

    chosen = []
    for e in library:
        if e.get("uuid") not in selected_uuids:
            continue
        if not e.get("enabled"):
            raise DeployError(
                f"「{e.get('label') or e.get('uuid')}」在 Arsenal 里是未启用状态。\n"
                "请先在 Arsenal 中启用它，再回来部署。"
            )
        order = e.get("deploy_order")
        if order is None:
            raise DeployError(
                f"「{e.get('label') or e.get('uuid')}」不在 Arsenal 当前配置档里，"
                "没有加载顺序。请先在 Arsenal 中把它加入配置档。"
            )
        chosen.append((order, e))
    if not chosen:
        raise DeployError("没有选中任何模组。")
    # 重复顺序号 = 数据异常，宁可停下也不猜（消息里带上是谁，便于定位）
    orders = [o for o, _ in chosen]
    if len(set(orders)) != len(orders):
        seen: dict = {}
        dupes = []
        for o, e in chosen:
            if o in seen:
                dupes.append(f"「{seen[o]}」和「{e.get('label') or e.get('uuid')}」"
                             f"都是顺序 {o}")
            else:
                seen[o] = e.get("label") or e.get("uuid")
        raise DeployError(
            "这些模组的加载顺序重复，无法确定部署次序，已停止：\n  "
            + "\n  ".join(dupes[:5]))

    chosen.sort(key=lambda t: t[0])
    # 前缀要从选中模组自己的文件推断 —— data 目录可能从未部署过、什么都没有
    pfx = prefix or find_prefix(data_dir, [e["path"] for _o, e in chosen])
    # 槽位按选项开关过滤 —— 用户禁用的选项不能装进游戏
    slots_of = {e["uuid"]: collect_slots(e["path"], e) for _o, e in chosen}
    total_slots = sum(len(v) for v in slots_of.values())

    items: list[dict] = []
    # 默认（Arsenal 的 setTopPriority=False）：顺序编号 —— 列表越靠后编号越大、越后加载、
    # 优先级越高。开了「第一个模组优先」才反过来。
    seq = list(enumerate(chosen))
    if first_mod_priority:
        seq = list(reversed(seq))
    # 每个槽位单独占一个编号：同一模组内也要依次递增，不能共用
    idx = 0
    for _pos, (_order, e) in seq:
        for slot_dir in slots_of.get(e["uuid"], []):
            main = None
            for fn in os.listdir(slot_dir):
                if _PATCH_RE.match(fn) and not fn.endswith(COMPANION_SUFFIXES):
                    main = fn
                    break
            if main is None:
                continue
            files = {"": os.path.join(slot_dir, main)}
            for suf in COMPANION_SUFFIXES:
                p = os.path.join(slot_dir, main + suf)
                files[suf] = p if os.path.isfile(p) else None
            items.append({
                "index": idx,
                "mod_label": e.get("label") or "",
                "mod_uuid": e.get("uuid") or "",
                "src_dir": slot_dir,
                "files": files,
            })
            idx += 1
    return DeployPlan(pfx, items, data_dir)


def scan_existing(data_dir: str, prefix: str) -> list[str]:
    """data 目录里现存的、属于该前缀的 patch 文件名。"""
    try:
        names = os.listdir(data_dir)
    except OSError:
        return []
    return sorted(
        n for n in names
        if (m := _PATCH_RE.match(n)) and m.group(1).lower() == prefix.lower()
    )


def backup_dir(data_dir: str) -> str:
    return os.path.join(data_dir, BACKUP_DIR_NAME)


def backup_existing(data_dir: str, prefix: str) -> str | None:
    """把将被清掉的 patch 文件复制一份到 data\\hd2u_backup\\<时间戳>\\。

    返回备份目录；没有可备份的文件时返回 None。游戏本体有校验，这些文件删掉
    通常能被重新部署回来，但备份一次几乎不花时间。
    """
    existing = scan_existing(data_dir, prefix)
    if not existing:
        return None
    dst = os.path.join(backup_dir(data_dir), time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dst, exist_ok=True)
    for n in existing:
        shutil.copy2(os.path.join(data_dir, n), os.path.join(dst, n))
    return dst


def _copy_slot(plan: DeployPlan, item: dict) -> None:
    for suffix, src in item["files"].items():
        target = os.path.join(plan.game_dir, plan.target_name(item["index"], suffix))
        if src is None:
            # 缺失的伴生文件写空占位，保证同序号的文件成套存在
            with open(target, "wb"):
                pass
            continue
        shutil.copyfile(src, target)  # 逐字节复制，不改文件头


def deploy(plan: DeployPlan, do_backup: bool = True) -> dict:
    """执行部署。会清空 data 目录里该前缀的全部 patch 文件，请确保已确认。"""
    gd = plan.game_dir
    backup = backup_dir(gd) if do_backup else None
    made = backup_existing(gd, plan.prefix) if do_backup else None

    removed = 0
    for name in scan_existing(gd, plan.prefix):
        try:
            os.remove(os.path.join(gd, name))
            removed += 1
        except OSError as exc:
            raise DeployError(
                f"删除旧部署文件「{name}」失败：{exc}\n"
                "请关闭游戏（或游戏加速器/同步盘）后重试。"
            ) from exc

    written = 0
    for item in plan.items:
        try:
            _copy_slot(plan, item)
        except OSError as exc:
            raise DeployError(f"复制槽位 {item['index']} 失败：{exc}") from exc
        written += 1

    manifest = {
        "tool": "HD2 Mod Updater",
        "timestamp": int(time.time() * 1000),
        "prefix": plan.prefix,
        "gameDataDir": gd,
        "backupDir": made or "",
        "mods": [
            {"uuid": it["mod_uuid"], "label": it["mod_label"], "index": it["index"]}
            for it in plan.items
        ],
    }
    with open(os.path.join(gd, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {
        "removed": removed,
        "written": written,
        "backup": made,
        "backup_root": backup,
        "manifest": os.path.join(gd, MANIFEST_NAME),
    }


def last_manifest(data_dir: str) -> dict | None:
    """读取本工具上次部署的清单；没有或损坏返回 None。"""
    p = os.path.join(data_dir, MANIFEST_NAME)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) else None


def describe_state(data_dir: str, prefix: str) -> str:
    """一句话描述 data 目录当前的部署状态，供界面显示。"""
    m = last_manifest(data_dir)
    files = scan_existing(data_dir, prefix)
    if m:
        return f"由本工具部署：{len(m.get('mods') or [])} 个槽位，{time.strftime('%Y-%m-%d %H:%M', time.localtime(m.get('timestamp', 0) / 1000))}"
    if files:
        return f"由其他工具部署：{len(files)} 个文件"
    return "未部署"