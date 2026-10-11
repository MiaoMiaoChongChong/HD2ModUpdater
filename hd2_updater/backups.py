# -*- coding: utf-8 -*-
"""备份轮转：给"写前必留一份"补上"留多少、什么时候清"。

为什么需要这个模块：
    原来的备份策略只写了"动手前先备份"，**没写上限、也不清理**，于是只增不减。
    实测（2026-10-11，用户机器）：
      · 游戏 `data\\hd2u_backup\\<时间戳>\\` —— 7 份、**1.63 GB**（每份约 230 MB，
        每次部署都会再复制一份当前整套 patch）
      · 模组目录里的 `<名字>.hd2u-old-<时间戳>` —— 5 份、78.7 MB
      · Arsenal 库备份 `hd2a_data.json.hd2u-<时间戳>.bak` —— 29 份、2.81 MB
    用户问"有没有上限"，答案是没有。所以补上这个模块。

**三条安全约束（一条都不能省）**：
  ① 只删**我们自己命名规则**的产物（`.hd2u-` 前缀），别的文件一概不碰 ——
     尤其**不能删 Arsenal 自己写的 `hd2a_data_<ISO>.json.bak`**；
  ② 只在**我们自己的目录**里动手（游戏 data 下的 hd2u_backup / 模组目录 /
     数据目录），删前逐个校验绝对路径确实落在该目录内；
  ③ **永远保留最早的那一份库备份**（"锚点"）—— 它是用户库的原始状态，
     只按"留最近 N 份"轮转的话它早晚会被清掉（用户明确把它当安全网）。
"""
from __future__ import annotations

import os
import re
import shutil

# ---- 命名规则（只认这些，别的一概不动）----
BAK_SUFFIX = re.compile(r"^.+\.hd2u-\d{8}-\d{6}\.bak$")
OLD_DIR = re.compile(r"^(?P<base>.+)\.hd2u-old-(?P<stamp>\d{8}-\d{6})$")
STAMP_DIR = re.compile(r"^\d{8}-\d{6}$")
BACKUP_DIR_NAME = "hd2u_backup"

# ---- 默认保留份数（用户可在设置里改）----
DEFAULTS = {
    "keep_game_backups": 3,      # 游戏目录 hd2u_backup/<时间戳>/ 保留份数
    "keep_old_mod_dirs": 1,      # 模组目录里每个模组的 .hd2u-old-* 保留份数
    "keep_data_baks": 10,        # 库/配置 .hd2u-*.bak 保留份数（最早那份永不删）
}
# 设置里允许的范围（防止填 0 把安全网清光，或填个天文数字）
LIMITS = {
    "keep_game_backups": (1, 50),
    "keep_old_mod_dirs": (0, 20),
    "keep_data_baks": (1, 200),
}
CONFIG_KEYS = tuple(DEFAULTS)


def retention() -> dict:
    """读用户的保留设置（没设过就用默认）。"""
    from .arsenal import read_config
    try:
        cfg = read_config() or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    out = {}
    for k, dflt in DEFAULTS.items():
        lo, hi = LIMITS[k]
        try:
            v = int(cfg.get(k, dflt))
        except (TypeError, ValueError):
            v = dflt
        out[k] = max(lo, min(hi, v))
    return out


def save_retention(values: dict) -> dict:
    """把保留设置写回配置（顺带夹到合法范围）。"""
    from .arsenal import write_config
    patch = {}
    for k in CONFIG_KEYS:
        if k not in values:
            continue
        lo, hi = LIMITS[k]
        try:
            v = int(values[k])
        except (TypeError, ValueError):
            v = DEFAULTS[k]
        patch[k] = max(lo, min(hi, v))
    if patch:
        write_config(patch)
    return retention()


# ---------------------------------------------------------------- 扫描
def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _inside(child: str, parent: str) -> bool:
    """child 是否真的在 parent 里面（防"删到别处"）。"""
    try:
        c = os.path.normcase(os.path.abspath(child))
        p = os.path.normcase(os.path.abspath(parent))
    except Exception:  # noqa: BLE001
        return False
    return c == p or c.startswith(p + os.sep)


def _game_backup_root(game_dir: str) -> str:
    return os.path.join(game_dir or "", "data", BACKUP_DIR_NAME)


def _sorted_stamp_dirs(root: str) -> list[str]:
    """root 下形如 <时间戳> 的子目录，**从新到旧**。"""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = [n for n in names
           if STAMP_DIR.match(n) and os.path.isdir(os.path.join(root, n))]
    return [os.path.join(root, n) for n in sorted(out, reverse=True)]


def _sorted_old_dirs(mods_dir: str) -> dict[str, list[str]]:
    """模组目录里 `<名字>.hd2u-old-<时间戳>`，按 base 分组，各自从新到旧。"""
    try:
        names = os.listdir(mods_dir)
    except OSError:
        return {}
    groups: dict[str, list[tuple[str, str]]] = {}
    for n in names:
        m = OLD_DIR.match(n)
        if not m:
            continue
        p = os.path.join(mods_dir, n)
        if not os.path.isdir(p):
            continue
        groups.setdefault(m.group("base"), []).append((m.group("stamp"), p))
    return {b: [p for _s, p in sorted(v, reverse=True)] for b, v in groups.items()}


def _sorted_baks(data_file: str) -> list[str]:
    """`<data_file>.hd2u-<时间戳>.bak`，**从新到旧**。"""
    d = os.path.dirname(data_file or "")
    base = os.path.basename(data_file or "")
    if not d or not base:
        return []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    hits = []
    for n in names:
        if not BAK_SUFFIX.match(n) or not n.startswith(base + ".hd2u-"):
            continue
        p = os.path.join(d, n)
        if os.path.isfile(p):
            hits.append((n, p))
    return [p for _n, p in sorted(hits, reverse=True)]


def scan(game_dir: str = "", mods_dir: str = "", data_file: str = "",
         keep: dict | None = None) -> dict:
    """看看现在有哪些备份、按当前设置能释放多少（**只读，不删东西**）。

    返回的 ``freeable`` 是"删掉超出的份数能省下的字节数"，给界面显示用。
    """
    keep = keep or retention()
    out = {"game": [], "old_mods": [], "data_baks": [],
           "total_bytes": 0, "freeable_bytes": 0, "freeable_items": 0}

    root = _game_backup_root(game_dir)
    dirs = _sorted_stamp_dirs(root)
    for i, p in enumerate(dirs):
        size = _dir_size(p)
        drop = i >= keep["keep_game_backups"]
        out["game"].append({"path": p, "bytes": size, "drop": drop})
        out["total_bytes"] += size
        if drop:
            out["freeable_bytes"] += size
            out["freeable_items"] += 1

    for base, paths in sorted(_sorted_old_dirs(mods_dir).items()):
        for i, p in enumerate(paths):
            size = _dir_size(p)
            drop = i >= keep["keep_old_mod_dirs"]
            out["old_mods"].append({"path": p, "base": base, "bytes": size,
                                    "drop": drop})
            out["total_bytes"] += size
            if drop:
                out["freeable_bytes"] += size
                out["freeable_items"] += 1

    baks = _sorted_baks(data_file)
    for i, p in enumerate(baks):
        size = _file_size(p)
        # ★ 最后一份（= 最早的）**永不删**：它是库的原始状态，用户的安全网
        drop = i >= keep["keep_data_baks"] and i < len(baks) - 1
        out["data_baks"].append({"path": p, "bytes": size, "drop": drop})
        out["total_bytes"] += size
        if drop:
            out["freeable_bytes"] += size
            out["freeable_items"] += 1
    return out


# ---------------------------------------------------------------- 清理
def prune(game_dir: str = "", mods_dir: str = "", data_file: str = "",
          keep: dict | None = None, dry_run: bool = False,
          skip_mods: bool = False) -> dict:
    """按保留设置清掉多余的备份。返回删了哪些、省了多少。

    ``skip_mods=True`` 时跳过模组目录那部分（Arsenal 正开着时用 ——
    那个目录属于它，运行中不动它更稳）。``dry_run=True`` 只算不删。
    """
    keep = keep or retention()
    report = scan(game_dir, mods_dir, data_file, keep)
    done = {"deleted": [], "freed_bytes": 0, "failed": [], "dry_run": dry_run}
    game_root = _game_backup_root(game_dir)
    data_root = os.path.dirname(data_file) if data_file else ""

    def do(path: str, is_dir: bool) -> None:
        # 双保险：真要删的东西必须落在我们预期的目录里（不在就跳过并记账）
        ok_place = (_inside(path, game_root)
                    or (bool(mods_dir) and _inside(path, mods_dir))
                    or (bool(data_root) and _inside(path, data_root)))
        if not ok_place:
            done["failed"].append((path, "不在预期目录内，已跳过"))
            return
        if not os.path.exists(path):
            return
        if dry_run:
            done["deleted"].append(path)
            return
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                os.remove(path)
            done["deleted"].append(path)
        except OSError as e:
            done["failed"].append((path, str(e)))

    for item in report["game"]:
        if item["drop"]:
            do(item["path"], True)
            done["freed_bytes"] += item["bytes"]
    if not skip_mods:
        for item in report["old_mods"]:
            if item["drop"]:
                do(item["path"], True)
                done["freed_bytes"] += item["bytes"]
    for item in report["data_baks"]:
        if item["drop"]:
            do(item["path"], False)
            done["freed_bytes"] += item["bytes"]

    return done


def prune_selected(paths: list[str], ctx: dict | None = None,
                   dry_run: bool = False) -> dict:
    """删掉用户**手选**的那些备份（「备份」页的「删除选中」）。

    与 :func:`prune` 共用同一套安全约束，只是"删哪些"由用户指定：
      · 路径必须落在我们预期的三个目录里（游戏 hd2u_backup / 模组目录 / 数据目录）；
      · 名字必须符合我们自己的 `.hd2u-` 命名规则 —— 用户即使误选了别的文件也删不掉；
      · 目录只删我们那种 `<时间戳>` / `<名字>.hd2u-old-<时间戳>` 形态的。
    返回 ``{deleted, freed_bytes, failed, dry_run}``。
    """
    ctx = ctx or {}
    game_dir = ctx.get("game_dir", "")
    mods_dir = ctx.get("mods_dir", "")
    data_file = ctx.get("data_file", "")
    game_root = _game_backup_root(game_dir)
    data_root = os.path.dirname(data_file) if data_file else ""

    done = {"deleted": [], "freed_bytes": 0, "failed": [], "dry_run": dry_run}
    for path in paths or []:
        if not path or not os.path.exists(path):
            continue
        name = os.path.basename(path)
        is_dir = os.path.isdir(path)
        # ① 必须在预期目录内
        in_game = _inside(path, game_root)
        in_mods = bool(mods_dir) and _inside(path, mods_dir)
        in_data = bool(data_root) and _inside(path, data_root)
        if not (in_game or in_mods or in_data):
            done["failed"].append((path, "不在备份目录内，已跳过"))
            continue
        # ② 名字必须符合我们自己的命名规则
        ok_name = (STAMP_DIR.match(name) if in_game else
                   (OLD_DIR.match(name) if in_mods else BAK_SUFFIX.match(name)))
        if not ok_name:
            done["failed"].append((path, "不是本工具的备份产物，已跳过"))
            continue
        size = _dir_size(path) if is_dir else _file_size(path)
        if dry_run:
            done["deleted"].append(path)
            done["freed_bytes"] += size
            continue
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                os.remove(path)
            done["deleted"].append(path)
            done["freed_bytes"] += size
        except OSError as e:
            done["failed"].append((path, str(e)))
    return done


def prune_for_context(data_file: str, game_dir: str = "",
                      mods_dir: str = "", arsenal_running: bool = False,
                      dry_run: bool = False) -> dict:
    """按当前上下文自动清理（自动调用时的入口）。

    Arsenal 开着就跳过模组目录那部分 —— 那个目录属于它，运行中不动更稳
    （与本项目"对方程序开着就拒绝写它的数据"同一条规矩）。
    """
    return prune(game_dir=game_dir, mods_dir=mods_dir, data_file=data_file,
                 dry_run=dry_run, skip_mods=bool(arsenal_running))
