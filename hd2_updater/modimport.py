# -*- coding: utf-8 -*-
"""把 mod 压缩包加进 Arsenal 的模组库（只进库，不进配置档）。

用途：用户把下载到的 mod ZIP 拖进界面 → 解包到 Arsenal 的模组目录 → 往
``modsLibrary`` 新增（或覆盖）一条条目。这样它就出现在"要维护更新的列表"里，
但**不会自动进配置档**，用户可以在部署页决定要不要启用。

【条目字段的来历（2026-10-11 对 21 条真实条目逐字段核对得出，不是猜的）】
· ``uuid``            ← mod 的 ``manifest.json`` 里的 ``Guid``（19/19 一致）。
                        有它就能天然去重：同一个 mod 再拖进来会覆盖同一条。
· ``label``           ← ``Name``；没有 manifest 时用压缩包文件名
· ``description``     ← ``Description``
· ``iconPath``        ← ``IconPath`` 解析成绝对路径；没有就为 None
· ``options``         ← ``Options`` 逐字段映射（见 :func:`_map_options`）
· ``tags``            ← ``["misc"]``（21/21 一致）
· ``patchFileNames``  ← ``[]``（21/21 全是空）
· ``previousVersions``← ``[]``
· ``addedAt``         ← 当前时间（ISO，带 Z）
· ``contentHash``     ← patch 文件内容的 sha256。**单槽位模组可精确复现**；
                        多选项模组 Arsenal 是按合并后的结果算的，算法未能解出，
                        这里退化为"全部 patch 按路径拼接"的近似值。
                        影响**仅限 Arsenal 自己的查重**（我们按 uuid 判重），
                        不影响模组能否加载与部署。
· ``nexusData``       ← 能从压缩包**文件名**里认出 N 网编号时（形如
                        ``名字 17165 2 2026-10-10T15-18Z QXOHm3L7i.zip``），
                        联网查一次 N 网把 ``fileId`` 补齐后写入；查不到就留 None。
                        **留 None 不影响识别** —— 文件夹名保留了那串 N 网命名，
                        "从安装目录名解析编号"那条路会接管。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import random
import re
import shutil
import uuid as _uuid

from . import arsenal_sync as As
from . import providers as Pv

# 模组文件的标志：所有 HD2 mod 的补丁文件都长这样
PATCH_MARK = ".patch_"
MANIFEST_NAME = "manifest.json"
STAGE_PREFIX = ".hd2u-stage-"
OLD_SUFFIX = ".hd2u-old-"
# Windows 文件名里不允许出现的字符
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# ★ N 网官方下载命名：
#   "<名字> <modId> <版本> <日期>T<时间>Z <随机slug>.zip"
#   例：G60-Lock-Line-V1.2.2 17165 2 2026-10-10T15-18Z QXOHm3L7i.zip
#   实测这条命名里的第 2 段 = Arsenal 库 nexusData.modId、第 3 段 = version
#   （在你库里 6 个真实样本上逐个核对一致）。
#   所以"从 ZIP 名解析 N 网编号"不是猜 —— 用的是 Arsenal 自己用的同一份证据。
NEXUS_NAME = re.compile(
    r"^(?P<base>.+?)\s+(?P<mod_id>\d{3,7})\s+"
    r"(?P<version>[0-9][0-9A-Za-z.\-]*)\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}-\d{2})Z"
    r"(?:\s+(?P<slug>\S+))?$")


class ModImportError(Exception):
    """拖入的压缩包有问题（或解包失败）时抛出，消息直接给用户看。"""


def parse_nexus_name(name: str) -> dict | None:
    """从 N 网命名的文件名里解析出 modId / 版本。解析不出返回 None。"""
    if not name:
        return None
    stem = os.path.splitext(os.path.basename(name.strip()))[0]
    m = NEXUS_NAME.match(stem)
    if not m:
        return None
    return {"mod_id": m.group("mod_id"), "version": m.group("version"),
            "date": m.group("date"), "slug": m.group("slug") or "",
            "base": m.group("base")}



def _sha256_concat(paths: list[str]) -> str:
    """按给定顺序把各文件内容拼起来算 sha256。"""
    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def safe_folder_name(name: str, fallback: str = "mod") -> str:
    """把名字变成安全的文件夹名（去掉非法字符、限长）。

    ★ 限长时**不能拦腰截断 N 网命名的尾巴** —— 尾巴里带着 modId/版本/时间戳，
    正是"从目录名解析 N 网编号"那条识别路径要用的。所以名字太长时，
    保留开头 + **完整的尾巴**（从 modId 那一段开始），中间用 … 省略。
    """
    n = _BAD_NAME.sub("_", (name or "").strip()).strip(" .")
    n = re.sub(r"\s+", " ", n)
    if not n:
        n = fallback
    limit = 110
    if len(n) <= limit:
        return n.strip(" .") or fallback
    m = NEXUS_NAME.match(n)
    if m:
        head = m.group("base")
        tail = n[m.start("mod_id"):]          # modId 往后全部保留
        room = limit - len(tail) - 3
        if room > 4:
            # 注意 "… " 后面要有空格 —— 识别用的正则要求编号前是空白分隔
            return (head[:room].strip(" .-") + "… " + tail).strip(" .") or fallback
        return tail.strip(" .") or fallback
    return n[:limit].strip(" .") or fallback



# ------------------------------------------------------------ 读压缩包
def _zip_root(names: list[str]) -> str:
    """判断压缩包内容是不是"多包了一层目录"，返回该层前缀（不带结尾斜杠）。

    实测两种都有：多数 ZIP 把 mod 内容直接放在根上（``manifest.json`` 在根），
    但也有的整个包在一个同名目录里。
    """
    tops = {n.split("/")[0] for n in names if n.strip()}
    if len(tops) == 1:
        only = tops.pop()
        # 只有一层且没有根级文件 → 认定是"套了一层目录"
        if not any(n.rstrip("/") == only for n in names):
            return only
    return ""


def inspect_zip(zip_path: str) -> dict:
    """只读地看一眼这个压缩包：够不够格当 mod、叫什么、有没有 manifest。

    不解包、不写任何东西 —— 先判断再动手。
    """
    import zipfile

    if not os.path.isfile(zip_path):
        raise ModImportError(f"找不到这个文件：{zip_path}")
    if not zip_path.lower().endswith(".zip"):
        raise ModImportError(
            "只支持 .zip 格式的模组压缩包。\n"
            f"（这个是 {os.path.splitext(zip_path)[1] or '无扩展名'}）")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise ModImportError(f"压缩包已损坏（{bad} 读取失败）。")
            infos = zf.infolist()
            names = [i.filename for i in infos]
    except ModImportError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ModImportError(f"打不开这个压缩包：{e}") from e

    # 防 zip-slip：不允许绝对路径或上跳
    for n in names:
        pure = n.replace("\\", "/")
        if pure.startswith("/") or ".." in pure.split("/") or re.match(r"^[A-Za-z]:", pure):
            raise ModImportError(f"压缩包里含不安全的路径，已拒绝：{n}")

    root = _zip_root(names)
    prefix = f"{root}/" if root else ""
    inner = [n[len(prefix):] for n in names if n.startswith(prefix)]
    has_patch = any(PATCH_MARK in n for n in inner)
    if not has_patch:
        raise ModImportError(
            "这个压缩包里没有找到模组文件（没有 .patch_ 之类的文件）。\n"
            "请确认它是 HD2 的模组包，而不是源码包或说明文档。")

    man = None
    mpath = f"{prefix}{MANIFEST_NAME}"
    for n in names:
        if n.replace("\\", "/") == mpath:
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    man = json.loads(zf.read(n).decode("utf-8-sig"))
            except Exception:  # noqa: BLE001
                man = None
            break

    man = man if isinstance(man, dict) else {}
    guid = man.get("Guid") or man.get("guid")
    if not (isinstance(guid, str) and re.fullmatch(
            r"[0-9a-fA-F-]{8,40}", guid.strip())):
        guid = None

    label = man.get("Name") or man.get("name")
    if not (isinstance(label, str) and label.strip()):
        label = os.path.splitext(os.path.basename(zip_path))[0]

    # ★ 文件夹名用**压缩包原名**（和 Arsenal 一样），而不是 manifest 里的显示名。
    #   原因：N 网下载的文件名里带着 "<modId> <版本> <日期>T<时间>Z <slug>"，
    #   而"从安装目录名解析 N 网编号"那条识别路径正靠它。
    #   早先版本用 manifest 的显示名当文件夹名，**把这串编号丢掉了** ——
    #   实测后果：从网页下载的 N 网 mod 拖进来后，来源完全识别不出
    #   （manifest 没有 Sources、nexusData 也没有，第五条路全断）。
    #   显示名（label）仍然用 manifest 的，用户看到的不变。
    folder_base = os.path.splitext(os.path.basename(zip_path))[0]

    return {
        "zip": zip_path,
        "manifest": man,
        "root": root,
        "patch_files": sorted(n for n in inner if PATCH_MARK in n),
        "uuid": guid or str(_uuid.uuid4()),
        "uuid_from_manifest": guid is not None,
        "label": str(label).strip(),
        "folder_base": folder_base,
        "nexus": parse_nexus_name(zip_path),
        "nexus_lookup_why": "",
        "description": str(man.get("Description") or man.get("description") or ""),
        "manifest_icon": man.get("IconPath") or man.get("iconPath"),
    }


# ------------------------------------------------------------ 构造条目
def _map_options(opts, icon_dir_ok=None) -> list[dict]:
    """把 manifest 的 ``Options`` 转成 Arsenal 库里 ``options`` 的形状。

    实测映射（19 个带 manifest 的模组全部对得上）：
      Name→name、Description→description、Include→include、
      SubOptions→**suboptions**（全小写）、Image→丢原始键但生成 iconPath、
      另外补 ``enabled: true``。``iconPath`` 有图时是完整路径，无图为 null。
    """
    out = []
    for o in opts or []:
        if not isinstance(o, dict):
            continue
        img = o.get("Image") or o.get("image")
        item = {
            "name": o.get("Name") or o.get("name") or "",
            "description": o.get("Description") or o.get("description") or "",
            "include": list(o.get("Include") or o.get("include") or []),
            "enabled": True,
            "iconPath": (icon_dir_ok(img) if (img and icon_dir_ok) else None),
            "suboptions": _map_options(o.get("SubOptions") or o.get("suboptions"),
                                       icon_dir_ok),
        }
        out.append(item)
    return out


def lookup_nexus_file(mod_id: str, version: str,
                      api_key: str = "") -> dict:
    """去 N 网把这个 mod 的 ``fileId``（和上传时间）查出来。

    为什么需要联网这一步：ZIP 名里只有 ``modId`` 和版本，**没有 fileId**
    （fileId 只出现在下载页 URL 的 ``file_id=`` 里）。而 Arsenal 的
    ``nexusData`` 里是带 ``fileId`` 的，所以要么查到、要么干脆不写。

    查不到就返回空 dict —— 调用方会退化成"不写 nexusData"。
    这时**识别仍然可用**：目录名里那串 N 网命名还在，工具的
    "从安装目录名解析 N 网编号"那条路能接管。
    """
    out: dict = {}
    mod_id = str(mod_id or "").strip()
    if not mod_id.isdigit():
        return out
    if not api_key:
        out["why"] = "没填 Nexus API key"
        return out
    try:
        data = Pv.http_get_json(
            f"{Pv.NEXUS_API}/games/helldivers2/mods/{mod_id}/files.json",
            headers=Pv.nexus_headers(api_key),
            # ★ 这是一次"锦上添花"的查询，不能让它拖住拖入操作：
            #   默认是 15s × 3 次重试（最坏 45s+），那样断网时界面会像卡死。
            #   这里压到 8s × 1 次 —— 查不到就走"靠目录名识别"那条路。
            timeout=8, retries=1,
        )
    except Exception as e:  # noqa: BLE001
        out["why"] = f"查询失败（{str(e)[:60]}）"
        return out
    files = (data or {}).get("files") if isinstance(data, dict) else None
    if not files:
        out["why"] = "该 mod 没有文件记录"
        return out

    ver = str(version or "").strip().lower()

    def hits(f):
        vals = [str(f.get("version") or "").lower(),
                str(f.get("name") or "").lower(),
                str(f.get("file_name") or "").lower()]
        return bool(ver) and any(ver == v or ver in v for v in vals)

    matched = [f for f in files if hits(f)]
    if not matched:
        out["why"] = f"这个 mod 的文件里没有版本「{version}」"
        return out
    # 同版本多个文件时取最新的那个
    best = sorted(matched, key=lambda f: int(f.get("uploaded_timestamp") or 0),
                  reverse=True)[0]
    return {
        "modId": mod_id,
        "fileId": str(best.get("file_id") or ""),
        "version": str(best.get("version") or version or ""),
        "updateTimestamp": int(best.get("uploaded_timestamp") or 0),
    }


def build_nexus_data(info: dict, api_key: str = "") -> dict:
    """尽力给这个压缩包配上 ``nexusData``（配不上就返回空 dict）。

    配不上**不算失败** —— 目录名里保留了 N 网命名，"从目录名解析编号"那条路
    仍然能把来源认出来。
    """
    nx = info.get("nexus") or {}
    if not nx.get("mod_id"):
        return {}
    got = lookup_nexus_file(nx["mod_id"], nx.get("version", ""), api_key)
    if not got or not got.get("fileId"):
        info["nexus_lookup_why"] = got.get("why", "没查到对应文件")
        # 查不到就不写 —— 宁可留空（工具靠目录名仍能识别），
        # 也不要写一个形状可疑的 nexusData 让 Arsenal 显示错的更新信息
        return {}
    info["nexus_lookup_why"] = ""
    return got


def build_entry(info: dict, target_dir: str,
                nexus_data: dict | None = None) -> dict:
    """按查实的规则造一条库条目（``target_dir`` 是解包后的模组目录）。"""
    man = info["manifest"]

    def resolve_icon(rel):
        if not rel:
            return None
        p = os.path.join(target_dir, str(rel).replace("/", os.sep))
        return p if os.path.isfile(p) else None

    icon = resolve_icon(info.get("manifest_icon"))
    if icon is None:
        # 没写 IconPath 时，Arsenal 会挑目录里的 png（实测 Resupply 就是这样）
        try:
            for n in sorted(os.listdir(target_dir)):
                if n.lower().endswith(".png") and os.path.isfile(
                        os.path.join(target_dir, n)):
                    icon = os.path.join(target_dir, n)
                    break
        except OSError:
            icon = None

    # contentHash：单槽位模组可精确复现（= 该槽 patch 文件按路径拼接的 sha256）
    patches = []
    for root, _dirs, files in os.walk(target_dir):
        for n in files:
            if PATCH_MARK in n:
                patches.append(os.path.join(root, n))
    patches.sort()
    try:
        chash = _sha256_concat(patches)
    except OSError:
        chash = ""

    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.") + f"{datetime.datetime.now().microsecond // 1000:03d}Z"

    return {
        "uuid": info["uuid"],
        "path": target_dir,
        "label": info["label"],
        "description": info.get("description", ""),
        "iconPath": icon,
        "tags": ["misc"],
        "patchFileNames": [],
        # nexusData：能从 ZIP 名解析出 N 网编号、并且联网查到了 fileId 时才有值。
        # 查不到就留 None —— 靠目录名里保留的 N 网命名，识别照样能走通。
        "nexusData": nexus_data or None,
        "options": _map_options(man.get("Options") or man.get("options"),
                                resolve_icon),
        "contentHash": chash,
        "addedAt": now,
        "previousVersions": [],
    }


# ------------------------------------------------------------ 解包 + 落库
def import_zip(data_file: str, zip_path: str, mods_dir: str | None = None,
               force: bool = False, api_key: str = "") -> dict:
    """把压缩包加进模组库。返回一句总结要用的信息。

    流程：先只读检查 → 解包到临时目录 → **换目录（重命名，不删原数据）** →
    再把条目写进库（含配置档副本同步）。任何一步失败都不留下半成品。
    """
    info = inspect_zip(zip_path)
    if mods_dir is None:
        data = As._load(data_file)
        mods_dir = As.mods_dir_of(data, data_file)
    mods_dir = os.path.normpath(mods_dir)
    if not os.path.isdir(mods_dir):
        raise ModImportError(f"Arsenal 的模组目录不存在：{mods_dir}")

    # 已在库里 → 复用它的目录（覆盖内容，不新建、不删旧目录）
    data = As._load(data_file)
    existing = next((m for m in (data.get("modsLibrary") or [])
                     if isinstance(m, dict) and m.get("uuid") == info["uuid"]),
                    None)
    reuse = None
    if existing:
        p = existing.get("path")
        if isinstance(p, str) and p.strip():
            cand = os.path.normpath(p)
            # 只复用"确实在我们管得到的 mods 目录里"的路径，避免误删别处东西
            if cand.lower().startswith(mods_dir.lower() + os.sep.lower()):
                reuse = cand

    if reuse:
        final_dir = reuse
    else:
        # 用压缩包原名（见 inspect_zip 里的说明：保住 N 网命名）
        base = safe_folder_name(info.get("folder_base") or info["label"])
        while True:
            cand = os.path.join(mods_dir, f"{base}_AR{random.randint(100000, 999999)}")
            if not os.path.exists(cand):
                final_dir = cand
                break

    stage = os.path.join(mods_dir, STAGE_PREFIX + _uuid.uuid4().hex[:8])
    old_dir = None
    created_new = False
    try:
        _extract(info, stage)

        # 换目录：先把旧的挪开（只重命名、不删数据），再就位
        if os.path.isdir(final_dir):
            suffix = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            old_dir = f"{final_dir}{OLD_SUFFIX}{suffix}"
            try:
                os.rename(final_dir, old_dir)
            except OSError as e:
                raise ModImportError(
                    "这个模组原来的文件夹正被占用，无法替换。\n"
                    f"请先关闭游戏和 HD2 Arsenal 再试。\n（{e}）") from e
        else:
            created_new = True
        try:
            os.rename(stage, final_dir)
        except OSError as e:
            if old_dir:                      # 换不成就把旧的挪回去
                try:
                    os.rename(old_dir, final_dir)
                except OSError:
                    pass
            raise ModImportError(f"解包好的内容没法就位：{e}") from e

        # nexusData：能从 ZIP 名认出 N 网编号时，顺手联网把 fileId 补上
        # （查不到就留空，不影响识别 —— 目录名里那串 N 网命名还在）
        nexus_data = build_nexus_data(info, api_key)

        # ★ 条目必须在**内容就位之后**构造 —— contentHash 要读真实文件，
        #   iconPath 也要确认真有这张图（早先在 staging 里算，两个都会落空）。
        entry = build_entry(info, final_dir, nexus_data)

        # 写库（备份 + 原子写 + 结构校验都在里面）
        try:
            res = As.upsert_library_mod(data_file, entry, force=force)
        except Exception:
            if created_new:
                # 全新导入又没写进库 = 留下一个"没人引用"的文件夹，
                # Arsenal 之后会把它清掉；这里主动收拾干净，别留垃圾
                shutil.rmtree(final_dir, ignore_errors=True)
            raise
        res.update({
            "label": info["label"],
            "path": final_dir,
            "old_dir": old_dir,
            "uuid_from_manifest": info["uuid_from_manifest"],
            "slot_count": _count_patch_slots(final_dir),
            "folder": os.path.basename(final_dir),
            "nexus": info.get("nexus") or {},
            "nexus_data": nexus_data or {},
            "nexus_why": info.get("nexus_lookup_why", "") if info.get("nexus") else "",
        })
        return res
    finally:
        if os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)


def _extract(info: dict, dest: str) -> None:
    """把压缩包里的 mod 内容解到 ``dest``（剥掉可能多出来的那层目录）。"""
    import zipfile

    os.makedirs(dest, exist_ok=True)
    prefix = f"{info['root']}/" if info["root"] else ""
    try:
        with zipfile.ZipFile(info["zip"]) as zf:
            for m in zf.infolist():
                name = m.filename.replace("\\", "/")
                if prefix and not name.startswith(prefix):
                    continue
                rel = name[len(prefix):]
                if not rel or rel.endswith("/"):
                    continue
                out = os.path.join(dest, rel.replace("/", os.sep))
                # 再确认一次没有跑到 dest 外面去
                if not os.path.normpath(out).lower().startswith(
                        os.path.normpath(dest).lower() + os.sep.lower()):
                    raise ModImportError(f"压缩包里有越界路径：{m.filename}")
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with zf.open(m) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except ModImportError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ModImportError(f"解包失败：{e}") from e


def _count_patch_slots(mod_dir: str) -> int:
    """数一下这个模组目录里有几个槽位（= 含 patch 文件的子目录 / 根目录占 1 个）。"""
    slots = 0
    for root, _dirs, files in os.walk(mod_dir):
        if any(PATCH_MARK in n for n in files):
            slots += 1
    return slots


def import_paths(data_file: str, paths: list[str],
                 mods_dir: str | None = None, force: bool = False,
                 api_key: str = "") -> dict:
    """批量导入（拖进来可能是好几个文件）。逐个来，一个失败不影响其它。"""
    done, failed = [], []
    for p in paths:
        try:
            r = import_zip(data_file, p, mods_dir=mods_dir, force=force,
                           api_key=api_key)
            done.append((p, r))
        except ModImportError as e:
            failed.append((p, str(e)))
        except As.SyncError as e:
            failed.append((p, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((p, f"意外错误：{e}"))
    return {"done": done, "failed": failed}


def split_drop_paths(raw: str) -> list[str]:
    """把拖放事件给的一串路径拆成列表。

    tkdnd 传进来的是 Tcl 列表：含空格的路径会用 ``{...}`` 包起来。
    """
    out, buf, in_brace = [], "", False
    for ch in raw or "":
        if ch == "{":
            in_brace = True
            continue
        if ch == "}":
            in_brace = False
            continue
        if ch == " " and not in_brace:
            if buf:
                out.append(buf)
            buf = ""
            continue
        buf += ch
    if buf:
        out.append(buf)
    return [p.strip() for p in out if p.strip()]
