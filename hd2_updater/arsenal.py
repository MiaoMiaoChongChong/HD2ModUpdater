# -*- coding: utf-8 -*-
"""读取 HD2 Arsenal 的模组库，并做「智能填入」定位。

Arsenal 的数据位置（按优先级）：
  1. 用户手动指定过的位置（存本工具配置）
  2. %LOCALAPPDATA%\\hd2arsenal\\hd2a_data.json（默认）
  3. 同盘/其他常见位置的最后手段扫描

只读，绝不修改 Arsenal 的任何文件。
"""
from __future__ import annotations

import json
import os
import re
import glob

from .util import app_data_dir

ARSENAL_DIRNAME = "hd2arsenal"
DATA_FILENAME = "hd2a_data.json"


def candidate_data_files() -> list[str]:
    """列出所有可能的 hd2a_data.json 位置（越靠前越可信）。"""
    out: list[str] = []

    # 1) 上次用过的（本工具配置）
    cfg = os.path.join(app_data_dir(), "config.json")
    try:
        with open(cfg, encoding="utf-8-sig") as f:
            saved = (json.load(f) or {}).get("arsenal_data_path")
        if saved:
            out.append(saved)
            # 也把它的父目录/同类位置一起纳入
            out.append(os.path.join(os.path.dirname(os.path.dirname(saved)),
                                    ARSENAL_DIRNAME, DATA_FILENAME))
    except Exception:  # noqa: BLE001
        pass

    # 2) 默认：%LOCALAPPDATA%\hd2arsenal\hd2a_data.json
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, ARSENAL_DIRNAME, DATA_FILENAME))
        # 有些版本可能放在 Roaming
    roaming = os.environ.get("APPDATA")
    if roaming:
        out.append(os.path.join(roaming, ARSENAL_DIRNAME, DATA_FILENAME))

    # 3) 备用盘符扫描（用户可能把数据挪到别的盘）
    for drive in _other_drives():
        for rel in (
            (ARSENAL_DIRNAME, DATA_FILENAME),
            ("Games", ARSENAL_DIRNAME, DATA_FILENAME),
            ("game", ARSENAL_DIRNAME, DATA_FILENAME),
        ):
            out.append(os.path.join(drive + os.sep, *rel))

    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _other_drives() -> list[str]:
    """Windows 上除系统盘外的可用盘符。"""
    drives = []
    try:
        import string
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.isdir(root):
                drives.append(f"{letter}:")
    except Exception:  # noqa: BLE001
        pass
    sysdrive = (os.environ.get("SystemDrive") or "C:").upper()
    return [d for d in drives if d.upper() != sysdrive]


def read_config() -> dict:
    cfg = os.path.join(app_data_dir(), "config.json")
    try:
        with open(cfg, encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def write_config(patch: dict) -> None:
    cfg = os.path.join(app_data_dir(), "config.json")
    cur = read_config()
    cur.update(patch)
    try:
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


def find_data_file(explicit: str | None = None) -> tuple[str | None, str]:
    """找 Arsenal 数据文件。

    返回 (路径 或 None, 说明)。explicit 是用户手动指定的
    （可以是 hd2a_data.json 本身，也可以是 hd2arsenal 目录，或它的父目录）。
    """
    if explicit:
        p = _resolve_explicit(explicit)
        if p:
            return p, "用户指定"
        return None, f"指定位置里没找到 {DATA_FILENAME}：{explicit}"

    for p in candidate_data_files():
        if os.path.isfile(p):
            return p, "自动探测"
    return None, "自动探测没找到"


def _resolve_explicit(path: str) -> str | None:
    """把用户给的路径解析成 hd2a_data.json 的完整路径。"""
    p = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
    if os.path.isfile(p):
        return p if os.path.basename(p).lower() == DATA_FILENAME.lower() else None
    if os.path.isdir(p):
        for cand in (
            os.path.join(p, DATA_FILENAME),
            os.path.join(p, ARSENAL_DIRNAME, DATA_FILENAME),
            os.path.join(p, "AppData", "Local", ARSENAL_DIRNAME, DATA_FILENAME),
        ):
            if os.path.isfile(cand):
                return cand
        # 目录里搜一层
        hits = glob.glob(os.path.join(p, "*", DATA_FILENAME))
        if hits:
            return hits[0]
    return None


def load_library(data_file: str) -> list[dict]:
    """读 modsLibrary，返回统一结构的列表。"""
    with open(data_file, encoding="utf-8-sig") as f:
        data = json.load(f)

    # 布局信息也读出来（mods 目录可能在别处）
    mods_dir = data.get("userModsDir") or ""
    lib: list[dict] = []
    for m in data.get("modsLibrary") or []:
        path = m.get("path") or ""
        nd = m.get("nexusData") or {}
        dirname = os.path.basename(path.rstrip("\\/")) if path else ""
        entry = {
            "uuid": m.get("uuid") or "",
            "label": (m.get("label") or "").strip(),
            "description": (m.get("description") or "").strip(),
            "path": path,
            "dirname": dirname,
            "mods_dir": mods_dir,
            "nexus_mod_id": str(nd.get("modId")) if nd.get("modId") else "",
            "nexus_file_id": str(nd.get("fileId")) if nd.get("fileId") else "",
            "nexus_version": str(nd.get("version")) if nd.get("version") else "",
            "added_at": m.get("addedAt") or "",
            "enabled": bool(m.get("enabled")),
            "deployed": bool(m.get("deployed")),
        }
        entry["installed_version"] = (
            entry["nexus_version"]
            or guess_version(entry)
            or read_version_from_metadata(entry)
            or ""
        )
        lib.append(entry)
    return lib


def guess_version(entry: dict) -> str:
    """从目录名/清单里猜本地版本（尽力而为）。

    注意：manifest 里的 "Version" 通常是**清单格式版本**（几乎恒为 1），
    不是 mod 版本，所以只在目录名完全解析不出时才用它，且值为 1 时忽略。
    """
    d = entry.get("dirname") or ""
    # Nexus 命名: "Name <modId> <version> <date>T<time>Z <slug>_AR..."
    m = re.search(
        r"\s\d{3,7}\s+([0-9][0-9A-Za-z\.\-]*)\s+\d{4}-\d{2}-\d{2}T", d
    )
    if m:
        return m.group(1)
    # 目录名里显式的 "vX.Y" / "V1.11.1" / "-v8" / " 0.6.4"
    m = re.search(r"(?:^|[\s\-_])[vV](\d+(?:\.\d+)+)", d)
    if m:
        return m.group(1)
    m = re.search(r"(?:^|[\s\-_])v(\d+)(?![0-9])", d)   # 形如 -v4
    if m:
        return m.group(1)
    m = re.search(r"(\d+\.\d+(?:\.\d+)+)", d)           # 形如 1.11.1
    if m:
        return m.group(1)
    m = re.search(r"(\d+\.\d+)", d)                      # 形如 0.6
    if m:
        return m.group(1)
    # 形如 "-R41-" / "_R41_" / " r41"（作者的修订号，如 DiversBestFriend-R41-）
    m = re.search(r"(?:^|[\s\-_])[rR](\d{1,4})(?=[\s\-_]|$)", d)
    if m:
        return f"r{m.group(1)}"
    # 形如 "V1.11.1" 已在上方处理；这里兜底纯数字修订号 "-v4"
    m = re.search(r"(?:^|[\s\-_])[vV](\d{1,4})(?=[\s\-_]|$)", d)
    if m:
        return m.group(1)
    # 最后才看清单，且忽略恒为 1 的格式版本
    mf = os.path.join(entry.get("path") or "", "manifest.json")
    try:
        with open(mf, encoding="utf-8-sig") as f:
            man = json.load(f)
        v = str(man.get("Version") or "").strip()
        if v and v not in ("1", "2"):
            return v
    except Exception:  # noqa: BLE001
        pass
    return ""


def read_version_from_metadata(entry: dict) -> str:
    """从 mod 目录里的构建元数据文件挖版本号（手动导入的 mod 常用）。

    会看 *build*.json / *-build.json / hd2runtime.json / build-info.json
    里的 version / revision / mod_version 等字段。
    """
    path = entry.get("path") or ""
    if not path or not os.path.isdir(path):
        return ""
    names = []
    try:
        for n in os.listdir(path):
            low = n.lower()
            if not low.endswith(".json"):
                continue
            if ("build" in low or "runtime" in low or "info" in low
                    or "provenance" in low):
                names.append(n)
    except OSError:
        return ""
    for n in names[:12]:
        try:
            with open(os.path.join(path, n), encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        for key in ("version", "Version", "mod_version", "modVersion",
                    "revision", "Revision", "release", "tag", "tag_name"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                s = v.strip()
                # 排除明显是格式/内部版本的值
                if s.lower() in ("1", "v1", "read_only", "readonly"):
                    continue
                return s
    return ""


def read_manifest_sources(entry: dict) -> list[dict]:
    """读 mod 自带 manifest 里的来源声明（HD2MM 的 Sources / NexusData）。

    这是「智能搜索」最高优先级的依据 —— 作者自己写的，必然准确。
    """
    out: list[dict] = []
    mf = os.path.join(entry.get("path") or "", "manifest.json")
    if not os.path.isfile(mf):
        return out
    try:
        with open(mf, encoding="utf-8-sig") as f:
            man = json.load(f)
    except Exception:  # noqa: BLE001
        return out
    for s in man.get("Sources") or []:
        if not isinstance(s, dict):
            continue
        prov = str(s.get("Provider") or "").strip().lower()
        if prov:
            out.append(
                {
                    "provider": prov,
                    "id": str(s.get("Id") or ""),
                    "url": str(s.get("Url") or ""),
                    "version": str(s.get("Version") or ""),
                    "confidence": "high",
                    "how": "mod 自带的 manifest 里写着",
                }
            )
    nd = man.get("NexusData") or {}
    if nd.get("ModId"):
        out.append(
            {
                "provider": "nexus",
                "id": str(nd.get("ModId")),
                "url": "",
                "version": str(nd.get("Version") or ""),
                "confidence": "high",
                "how": "mod 自带的 manifest 里写着（旧格式 NexusData）",
            }
        )
    return out


def default_downloads_dir() -> str:
    """系统「下载」目录（问注册表，失败退回 ~/Downloads）。"""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            val, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
        val = os.path.expandvars(val)
        if val and os.path.isdir(val):
            return val
    except Exception:  # noqa: BLE001
        pass
    guess = os.path.join(os.path.expanduser("~"), "Downloads")
    return guess if os.path.isdir(guess) else os.path.abspath(os.getcwd())


def tool_downloads_dir() -> str:
    """本工具存放已下载 ZIP 的目录。"""
    d = os.path.join(app_data_dir(), "downloads")
    os.makedirs(d, exist_ok=True)
    return d
