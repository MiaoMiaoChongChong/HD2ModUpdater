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


def first_mod_priority(data_file: str) -> bool:
    """Arsenal 的「第一个模组优先」设置（``setTopPriority``）。

    影响部署时的编号方向：
    - ``False``（Arsenal 默认）：列表越靠后优先级越高 —— 实测位次 0→patch_0、
      位次 17→patch_44，且 Bingus loader 的 README 写明「默认设置下放最后 = 赢」。
    - ``True``：反过来，列表第一个优先（README 对应说法是「把 loader 放最前」）。

    读不到或结构异常时返回 False（Arsenal 默认值），绝不抛异常。
    """
    try:
        with open(data_file, encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return False
    return bool(data.get("setTopPriority"))


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


def _deployment_map(data: dict) -> dict:
    """从 hd2a_data.json 里抽出「当前配置档」的部署状态，按 uuid 索引。

    返回 {uuid: {"enabled":bool, "deployed":bool, "changed":bool, "order":int}}。
    找不到配置档时返回空 dict（调用方据此把所有模组标成「不在配置档里」）。
    对损坏结构（不是 dict / mods 不是列表）一律当空，绝不抛异常 —— 库文件可能被
    手工编辑坏，不能让整个加载崩掉。
    """
    mods_list = data.get("modsList")
    if not isinstance(mods_list, dict) or not mods_list:
        return {}
    profile = data.get("selectedProfile")
    if profile not in mods_list:
        # 退一步用第一个配置档（用户切换过、selectedProfile 没跟上等情况）
        profile = next(iter(mods_list))
    node = mods_list.get(profile)
    mods = node.get("mods") if isinstance(node, dict) else None
    if not isinstance(mods, list):
        return {}
    out: dict = {}
    for i, m in enumerate(mods):
        if not isinstance(m, dict):
            continue
        u = m.get("uuid")
        if not u:
            continue
        out[u] = {
            "enabled": bool(m.get("enabled")),
            "deployed": bool(m.get("deployed")),
            "changed": bool(m.get("changed")),
            "order": i,
        }
    return out


def load_library(data_file: str) -> list[dict]:
    """读 modsLibrary，返回统一结构的列表。"""
    with open(data_file, encoding="utf-8-sig") as f:
        data = json.load(f)

    # 布局信息也读出来（mods 目录 / 游戏目录 可能在别处）
    mods_dir = data.get("userModsDir") or ""
    game_dir = data.get("userGameDir") or ""

    # 部署状态以**当前配置档**（modsList.<selectedProfile>.mods）为准：
    # 那里每个模组带 enabled/deployed/changed 布尔标志，数组顺序就是加载优先级。
    # （modsLibrary 是"库"，没有这些标志 —— 早期版本误从它读，导致 enabled/deployed
    #   恒为 False。这里改成按 uuid 从配置档反查。）
    deploy = _deployment_map(data)
    # 用户手动修正过的本地版本（按 uuid 记），优先于自动推断
    overrides = (read_config().get("version_overrides") or {})
    lib: list[dict] = []
    for m in data.get("modsLibrary") or []:
        path = m.get("path") or ""
        nd = m.get("nexusData") or {}
        dirname = os.path.basename(path.rstrip("\\/")) if path else ""
        uuid = m.get("uuid") or ""
        dstate = deploy.get(uuid) or {}
        entry = {
            "uuid": uuid,
            "label": (m.get("label") or "").strip(),
            "description": (m.get("description") or "").strip(),
            "path": path,
            "dirname": dirname,
            "mods_dir": mods_dir,
            "game_dir": game_dir,
            "nexus_mod_id": str(nd.get("modId")) if nd.get("modId") else "",
            "nexus_file_id": str(nd.get("fileId")) if nd.get("fileId") else "",
            "nexus_version": str(nd.get("version")) if nd.get("version") else "",
            "added_at": m.get("addedAt") or "",
            "enabled": bool(dstate.get("enabled")),
            "deployed": bool(dstate.get("deployed")),
            "deploy_changed": bool(dstate.get("changed")),
            "deploy_order": dstate.get("order"),
            "in_profile": bool(dstate),
            # 选项配置：部署时要用它过滤掉用户禁用的选项（include 名 = 子目录名）
            "options": m.get("options") or [],
        }
        auto = (
            entry["nexus_version"]
            or guess_version(entry)
            or read_version_from_metadata(entry)
            or ""
        )
        entry["auto_version"] = auto
        # 「装的是哪个变体」—— 多变体 mod（如 Megapack 的 Rows 版）靠它挑对文件
        entry["variant"] = local_variant(entry)
        if entry["uuid"] in overrides:
            entry["installed_version"] = str(overrides[entry["uuid"]])
            entry["version_is_manual"] = True
        else:
            entry["installed_version"] = auto
            entry["version_is_manual"] = False
        lib.append(entry)
    return lib


def set_version_override(uuid: str, version: str) -> None:
    cfg = read_config()
    ov = cfg.get("version_overrides") or {}
    if version:
        ov[uuid] = version
    else:
        ov.pop(uuid, None)
    write_config({"version_overrides": ov})


def get_version_override(uuid: str) -> str:
    return str((read_config().get("version_overrides") or {}).get(uuid) or "")


# Nexus 给下载目录起的名字末尾固定是
#   " <modId> <version> <YYYY-MM-DDTHH-MMZ> <8位随机码>"
# 去掉这一串（以及 Arsenal 自己加的 _AR######），剩下的就是**原始文件名的主体**。
# 实测：加回 ".zip" 后与 Nexus API 的 file_name 精确一致（9 个里命中 7 个）。
NEXUS_TAIL = re.compile(r"\s\d{3,7}\s+[0-9][0-9A-Za-z\.\-]*"
                        r"\s+\d{4}-\d{2}-\d{2}T[0-9\-]+Z\s+\S+$")
_AR_SUFFIX = re.compile(r"_AR\d+$")


def local_variant(entry: dict) -> str:
    """猜「用户装的这一个文件叫什么名字」——用于在多变体里挑对那一个。

    为什么需要：同一个 mod 常有多个并行变体，比如 Vanilla Plus Megapack 有
    普通版和 Rows 版（`Vanilla-Plus-Megapack-v40.zip` vs
    `Vanilla-Plus-Megapack-Rows-v35.zip`）。它们**共用一个 mod 页面、
    共用版本号序列**，只靠"版本号 + MAIN 分类"根本区分不出来 ——
    实测就踩了：用户装的 Rows 版，工具却下成普通版给人家。

    Nexus 下载的目录名里带着**原始文件名**，所以能还原出变体：
        "Vanilla Plus Megapack - Rows 16294 35 2026-09-29T19-48Z KHd6InFpy_AR123"
        → "Vanilla Plus Megapack - Rows"

    返回空串表示"猜不出"（手动导入的、名字被改过等）——此时调用方
    **不应该**替用户猜变体，而应当照旧走默认策略。
    """
    d = entry.get("dirname") or ""
    if not d:
        return ""
    d = _AR_SUFFIX.sub("", d)
    # 只有 Nexus 那套命名才敢还原（末尾有 编号+版本+时间戳+随机码）
    if not NEXUS_TAIL.search(d):
        return ""
    base = NEXUS_TAIL.sub("", d).strip()
    return base


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
    / provenance.json 里的 version / revision / mod_version 等字段，
    以及 INSTALL.txt / README 里形如 "v1.2.3" 或 "(build 12345678)" 的痕迹。
    """
    path = entry.get("path") or ""
    if not path or not os.path.isdir(path):
        return ""
    names = []
    try:
        for n in os.listdir(path):
            low = n.lower()
            if not low.endswith((".json", ".txt", ".md")):
                continue
            if ("build" in low or "runtime" in low or "info" in low
                    or "provenance" in low or "install" in low
                    or "readme" in low or "manifest" in low):
                names.append(n)
    except OSError:
        return ""

    # 1) 先看结构化字段（更可靠）
    for n in names:
        if not n.lower().endswith(".json"):
            continue
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
                if s.lower() in ("1", "v1", "read_only", "readonly", "2", "v2"):
                    continue
                return s

    # 2) 退一步：从文本里找 "v1.2.3" / "Revision 41" / "build 25327279"
    import re
    pats = [
        r"\bv(\d+(?:\.\d+)+)\b",                       # v1.2.3
        r"\brevision[:\s]+([rR]?\d+(?:\.\d+)*)\b",     # Revision R41 / revision 41
        r"\bbuild[s]?[:\s]+(\d{5,})\b",                # build 25327279
        r"\b(\d+\.\d+(?:\.\d+)+)\b",                   # 1.2.3
    ]
    for n in names:
        if n.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(path, n), encoding="utf-8-sig",
                      errors="replace") as f:
                txt = f.read(20000)
        except Exception:  # noqa: BLE001
            continue
        for p in pats:
            m = re.search(p, txt, re.I)
            if m:
                return m.group(1)
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
    """本工具存放已下载 ZIP 的目录（可自定义，存 config.json）。"""
    custom = str(read_config().get("tool_downloads_dir") or "").strip()
    if custom:
        d = os.path.abspath(os.path.expandvars(os.path.expanduser(custom)))
    else:
        d = os.path.join(app_data_dir(), "downloads")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        # 自定义路径不可用时退回默认，保证不崩
        d = os.path.join(app_data_dir(), "downloads")
        os.makedirs(d, exist_ok=True)
    return d


def is_custom_tool_downloads() -> bool:
    return bool(str(read_config().get("tool_downloads_dir") or "").strip())


def browser_watch_dir() -> str:
    """浏览器接力时**实际监视**的文件夹。

    默认问注册表拿系统「下载」目录；用户可在设置里覆盖
    （浏览器改过下载位置时必须能改，否则监视会落空）。
    """
    custom = str(read_config().get("browser_watch_dir") or "").strip()
    if custom:
        d = os.path.abspath(os.path.expandvars(os.path.expanduser(custom)))
        if os.path.isdir(d):
            return d
    return default_downloads_dir()


def is_custom_browser_watch() -> bool:
    return bool(str(read_config().get("browser_watch_dir") or "").strip())


def guess_browser_download_dirs() -> list[str]:
    """常见浏览器的实际下载目录候选（供用户挑选，免去自己找）。

    注意：浏览器把下载位置存在各自的 Preferences 里，编码/结构各版本不同，
    这里只做**保守的已知位置推断**，不作为唯一来源 —— 用户仍可手动指定。
    """
    out: list[str] = []

    def add(p: str) -> None:
        if p and os.path.isdir(p) and os.path.normcase(p) not in {
            os.path.normcase(x) for x in out
        }:
            out.append(p)

    add(default_downloads_dir())

    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or ""
    # 各浏览器配置目录里的默认下载位置（多数用户的默认就是系统的 Downloads，
    # 这里补充几种常见的自定义习惯位置）
    for rel in ("Downloads", "下载", os.path.join("Desktop", "下载")):
        add(os.path.join(home, rel))
        if local:
            add(os.path.join(local, rel))
    for drive in ("D:", "E:", "F:"):
        for rel in ("Downloads", "下载", os.path.join("下载", "HD2")):
            add(os.path.join(drive + os.sep, rel))
    return out
