# -*- coding: utf-8 -*-
"""读取 HD2 Arsenal 的模组库，并做「智能填入」定位。

Arsenal 的数据位置（按优先级）：
  1. 用户手动指定过的位置（存本工具配置）
  2. %LOCALAPPDATA%\\hd2arsenal\\hd2a_data.json（默认）
  3. 同盘/其他常见位置的最后手段扫描

只读，绝不修改 Arsenal 的任何文件。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import glob
import time

from .util import app_data_dir

ARSENAL_DIRNAME = "hd2arsenal"
DATA_FILENAME = "hd2a_data.json"

# 最近一次读 config.json 的失败原因（空 = 读成功 / 文件不存在）。
# write_config 靠它判断"这次读到的是空壳，不能拿去覆盖磁盘"。
_cfg_last_error = ""

# 上一次 write_config 把旧配置留档到了哪里（空 = 没留档）
_cfg_backed_up = ""


@contextlib.contextmanager
def _config_lock(timeout: float = 3.0):
    """跨进程互斥：同一时刻只允许一个进程对 config.json 做"读-改-写"。

    为什么需要（2026-10-18 事故的最后一环）：**原子写只解决"读到半个文件"**，
    不解决"后写的盖掉先写的" —— 两个实例同时读、各自改、再各自写，
    后写的那个就把前一个刚存的字段盖没了（两个窗口同时开着就够触发）。
    这里用 Windows 的文件锁把这段串成队；进程退出时系统自动释放锁，
    不会像 `O_EXCL` 那种自造锁一样留下"死锁文件"。

    拿不到锁（超时 3 秒）就**照常写**：宁可偶尔丢一次更新，
    也不能让保存按钮卡住 —— 但绝不会再出现"读空 → 整份覆盖"。
    """
    f = None
    locked = False
    try:
        f = open(os.path.join(app_data_dir(), "config.lock"), "a+b")
        try:
            import msvcrt
        except ImportError:      # 非 Windows：没有 msvcrt，退化为不锁
            msvcrt = None
        if msvcrt is not None:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
        yield
    finally:
        if f is not None:
            if locked:
                try:
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:  # noqa: BLE001
                    pass
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass

# .patch_ 是所有 HD2 mod 的补丁文件标志（modimport 里也用同一个）
_PATCH_MARK = ".patch_"


# ==========================================================================
# 「手动填的本地版本」——内容签名机制
# ==========================================================================
# ★ 背景（2026-10-12，GitHub issue #1）：
#   用户手动填过本地版本后，这个值被**永久**记在 config.json 里，
#   并且优先级最高（压过 nexusData.version / 目录名 / 元数据）。
#   结果：模组更新之后，这个旧值仍然压着新的真实版本，于是工具**永远**
#   显示「有更新」，用户重装多少次都没用（实测反馈者 30 个模组里 17 个
#   被冻住、其中 14 个是"装完下个版本就误报"的定时炸弹）。
#
# ★ 修法（用户提案「版本号设计成不冻结的」）：
#   记录格式从 `"1.1"` 改成 `{"version": "1.1", "sig": "<内容签名>"}`，
#   **sig = 填这个版本时，模组内容的签名**。
#   于是：
#     · 内容没变（sig 一致）→ 模组没被动过 → 手动值仍然成立（冻结是安全的）
#     · 内容变了（sig 不一致）→ 模组被更新过 → **手动值自动失效**，改回自动探测
#   这就是"不冻结"：**冻结只在内容确实没变期间成立**，模组一更新就自动解冻。
#
# ★ 为什么签名要自己算，而不是复用 Arsenal 的 contentHash / mtime：
#   · Arsenal 的 contentHash：我们**只能复现 16/21**（多选项模组它的合并算法
#     没解出，见 modimport.build_entry 的注释），拿它当签名会误判。
#   · patch 文件的 mtime：实测 **8/21 是 1980-01-01**（解包没带时间戳），不可用。
#   · 只记文件大小：实测"内容全换但大小不变"时签名不变 → 会漏判更新。
#   所以只能用「相对路径 + 内容」一起哈希。实测 21/21 稳定、成本 0.21 秒。
_sig_cache: dict[str, tuple[tuple, str]] = {}


def content_sig(path: str, fresh: bool = False) -> str:
    """算模组目录的**内容签名**（读 ``.patch_`` 文件）。

    同一份内容永远得到同一个签名，内容变了一定不同。

    为什么只看 ``.patch_``：那才是 mod 的**实际内容**；同目录下的
    manifest / png / txt 是元数据，作者改个描述不该被当成"更新了"。

    ★ ``fresh=True`` 表示**不许用缓存**，必须重算 —— 用于"内容变了吗"这种
      判断。它必须这么做：手动版本会不会失效，完全取决于这个判断对不对；
      而缓存的判据只有 (清单, 大小, mtime)，两次快速改写**可能**落在同一个
      时间刻度里（实测 Windows 上 st_mtime 有 100ns 精度，但同一 tick 内
      连写两次仍会撞上），那样就会漏判"已更新" → 手动值永远不失效，
      又回到 issue #1 那个坑。
      代价实测很小：21 个模组 / 260MB 全量重算只要 0.21 秒。
    """
    if not path:
        return ""
    try:
        real = os.path.normcase(os.path.abspath(path))
    except (OSError, ValueError):
        return ""
    if not os.path.isdir(path):
        return ""
    files: list[tuple[str, int, int]] = []
    try:
        for root, _dirs, names in os.walk(path):
            for n in names:
                if _PATCH_MARK in n:
                    fp = os.path.join(root, n)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    files.append((os.path.relpath(fp, path), st.st_size,
                                  int(st.st_mtime_ns)))
    except OSError:
        return ""
    if not files:
        return ""
    files.sort(key=lambda t: t[0].lower())
    cached = _sig_cache.get(real)
    if not fresh and cached and cached[0] == tuple(files):
        return cached[1]
    h = hashlib.sha256()
    for rel, _sz, _mt in files:
        h.update(rel.replace("\\", "/").encode("utf-8", "replace") + b"\0")
        try:
            with open(os.path.join(path, rel), "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
    sig = h.hexdigest()
    _sig_cache[real] = (tuple(files), sig)
    return sig


def _override_parts(rec) -> tuple[str, str]:
    """把一条 override 记录拆成 ``(版本, 内容签名)``。

    兼容两种格式：
      · 新格式 ``{"version": "1.1", "sig": "abc…"}``
      · 旧格式 ``"1.1"``（v1.8.11 及以前写下的，没有签名）
    """
    if isinstance(rec, dict):
        return str(rec.get("version") or "").strip(), str(rec.get("sig") or "").strip()
    return str(rec or "").strip(), ""


def _resolve_override(rec, path: str) -> tuple[str, str, str]:
    """判断这条 override 还能不能用。

    返回 ``(可用版本, 内容签名, 原因)``：
      · ``("",  sig, 原因)`` → **不可用**（已失效），调用方改用自动探测
      · ``(版本, sig, 原因)`` → 可用

    判定规则（就是「不冻结」那套）：
      · 旧格式（没有签名）→ **仍然可用**。不能因为"缺签名"就丢弃用户填的值
        —— 那等于"修 bug 把好数据删了"。这类交给界面提示 + 「恢复自动」。
      · 有签名且和当前内容一致 → 可用（模组没被动过）。
      · 有签名但和当前内容不一致 → **失效**（模组被更新过了）。
    """
    ver, sig = _override_parts(rec)
    if not ver:
        return "", sig, "空记录"
    if not sig:
        return ver, sig, "旧格式，没有内容签名 —— 先沿用，界面上标出来待核对"
    # ★ fresh=True：这里是在判断"内容到底变没变"，**不能**用缓存 ——
    #   判错的后果就是"手动值永远不失效"，等于没修（issue #1 的坑）。
    now = content_sig(path, fresh=True)
    if not now:
        # 目录读不到（不存在/无权限）—— 判不了，按"仍然可用"处理，
        # 宁可暂时用旧值，也不要在读不到目录时把用户填的值丢掉。
        return ver, sig, "目录读不到，暂时沿用手动值"
    if now == sig:
        return ver, sig, "内容没变，手动值仍然成立"
    return "", sig, "内容变了（模组被更新过），手动值已自动失效"


def read_version_overrides() -> dict:
    """读 config.json 里的 version_overrides（原始记录，未解析）。"""
    return (read_config().get("version_overrides") or {})


def _merge_nexus_and_imported(nexus_version: str, imported: str) -> str:
    """把「N 网记的版本」和「导入时记的版本」合并成一个本地版本。

    ★ 为什么需要这一层（2026-10-19 用户实测：
      「这个 mod 我更新以后还是显示未更新…名字也都成 V20 了本地版本还是 V19」）：

      这两个值来自**两条互不相通的路径**，谁也不总是准：

        · ``nexus_version`` = Arsenal 库里那条 ``nexusData.version``。
          只有**从 N 网下载安装**时才会被写成新版本；用户**手动导入一个
          zip**（比如从 GitHub 下的）时，Arsenal 根本不知道版本变了，
          这个值就**永远停在旧值**（实测：Bingus Shared Loader 库里记 19，
          实际装的是 v20）。

        · ``imported`` = 本工具导入那一刻记下的版本，且带内容签名核对
          （见 :func:`_resolve_installed`）—— 它说的是"**现在磁盘上**装的
          到底是哪个版本"，是更硬的证据。

      之前的代码是 ``nexus_version or _pick_local_version(...)`` ——
      ``or`` 是**短路**的：``nexus_version`` 只要非空就赢，后面的导入记录
      根本没机会被读到。于是这类模组会**永远**显示旧版本、永远报「有更新」，
      用户重装多少次都没用。

    ★ 规则（与 :func:`_pick_local_version` 里"目录名 vs 元数据"的处理同源）：
      · 两边都比得上大小 → **取更新的那个**（陈旧的那个让位）；
      · 比不了（格式不同 / 缺一方）→ **保留 N 网记录**（它是 Arsenal 自己
        写的、语义上更"官方"，而且这条路径不引入新行为）；
      · 只有导入记录 → 用导入记录；只有 N 网记录 → 用 N 网记录。

    ★ 为什么不"导入记录无条件优先"：
      导入记录虽然更贴近磁盘，但它记的是**压缩包名字里的版本**，
      解析偶尔会错（作者把版本写在别处、或 zip 名里没有版本号）。
      "取更新的那个"在这种情况下也不会更差 —— 因为如果它更旧，
      就会自动让位给 N 网记录；只有它确实更新时才被采用。
    """
    from .util import compare as _cmp

    nv = str(nexus_version or "").strip()
    im = str(imported or "").strip()
    if not nv:
        return im
    if not im:
        return nv
    if nv == im:
        return nv
    try:
        # compare(a, b) == "newer" 的意思是「**b 比 a 新**」。
        # 这里 a=nv（可能陈旧）、b=im（导入时记的）：
        #   "newer" → 导入记录更新 → 用导入记录
        #   "older" → N 网记录更新 → 用 N 网记录
        rel = _cmp(nv, im)
    except Exception:  # noqa: BLE001
        rel = "unknown"
    if rel == "newer":
        return im
    return nv


def _pick_local_version(entry: dict, imported: str) -> str:
    """在没有导入记录时，从「目录名」和「元数据」两处证据里挑一个。

    ★ 为什么需要挑（issue #1，用户 2026-10-12 实测）：
      导入更新版会**复用旧目录**，文件夹名里那个版本号是永久旧值
      （"Vanilla-Plus-Megapack-v40_AR205686" 里装着 v41 的内容），
      而 `guess_version` 原本优先读目录名 → 永远报「有更新」。
      用户的实测：HD2Runtime（目录名 0.30.3 / 元数据 0.31.3）、
      Vanilla Plus Megapack（目录名 40 / 元数据 megapack-v41）都这样。

    ★ 为什么不能简单改成"元数据优先"（实测 21 个模组后的结论）：
      两种证据各有准的时候 —— 有的 mod 元数据反而更旧（实测 5 个，例如
      HD2 C4 Quick Actions 目录名 1.13.1 反而比元数据的 2.2 更可信）。
      所以规则是：**能比大小就取更新的那个；比不了（格式不同 / 缺一方）
      就退回目录名**（它至少是 Arsenal 自己装的、能对上 N 网命名）。

    顺序：导入时记下的版本（最硬） > 这一段的挑选 > 目录名 > 元数据。
    """
    if imported:
        return imported
    from .util import compare as _cmp

    by_dir = guess_version(entry) or ""
    by_meta = read_version_from_metadata(entry) or ""
    if by_dir and by_meta and by_dir != by_meta:
        try:
            # compare(a, b) == "newer" 的意思是「**b 比 a 新**」。
            # 这里 a=by_dir、b=by_meta，所以：
            #   "newer" → 元数据更新 → 用元数据（目录名是旧目录留下的）
            #   "older" → 目录名更新 → 用目录名
            _rel = _cmp(by_dir, by_meta)
            # ★ 防呆：目录名里被截出来的**单段**数字（如 `1`）经常只是
            #   版本号的第一段，而不是真的版本号（实测「机甲部位血量HUD v1.11.1」
            #   的目录名只解析出 `1`，而 label/元数据都指向 1.11.1）。
            #   拿 "1" 和 "1.11.1" 比大小会判"元数据更新"，但这里真正的版本号
            #   是 `1.11.1`。**出现这种情况就信多段的那个**（更完整）。
            _d_parts = by_dir.replace(".", "").isdigit() and len(by_dir.split("."))
            _m_parts = by_meta.replace(".", "").isdigit() and len(by_meta.split("."))
            if _d_parts == 1 and _m_parts and _m_parts > 1:
                return by_meta
            if _rel == "newer":
                return by_meta
            if _rel == "older":
                return by_dir
        except Exception:  # noqa: BLE001
            pass
    return by_dir or by_meta


def read_installed_versions() -> dict:
    """读「导入时记下的本地版本」。

    ★ 这份记录解决的是另一个问题（issue #1 用户 2026-12-12 实测发现）：
      导入更新版时 `import_zip` 会**复用旧目录**，所以文件夹名里那个
      版本号永远停在旧值（"Vanilla-Plus-Megapack-v40" 里装着 v41 的内容），
      而 `guess_version` 又优先读目录名 → 于是永远报「有更新」。
      **导入那一刻我们确切知道装的是哪个版本**，把它连同内容签名记下来，
      比事后从目录名/元数据里猜可靠得多。
    """
    return (read_config().get("installed_versions") or {})


def _resolve_installed(rec, path: str) -> str:
    """「导入时记下的版本」现在还成不成立？成立就返回它，否则返回空。

    和手动版本同一套判据（按内容签名核对）：内容又变了 → 这条也失效。
    """
    if rec is None:
        return ""
    ver, sig = _override_parts(rec)
    if not ver or not sig:
        return ver
    now = content_sig(path, fresh=True)
    return ver if (now and now == sig) else ""


def set_installed_version(uuid: str, version: str, sig: str) -> None:
    """记下「这个 uuid 当前装的是 <version>」（导入时调用）。

    带内容签名 —— 内容再变一次（用户又更新了）这条记录自动失效，
    避免变成第二个"永久冻结"。
    """
    if not uuid or not version:
        return
    cfg = read_config()
    rec = cfg.get("installed_versions") or {}
    rec[uuid] = {"version": str(version), "sig": str(sig or "")}
    write_config({"installed_versions": rec})


def get_installed_version(uuid: str) -> str:
    return _override_parts(read_installed_versions().get(uuid or ""))[0]


def get_version_override(uuid: str) -> str:
    """读这个模组**手动填的**本地版本（没有记录返回空串）。

    注意它返回的是"记下来的那个值"，**不管它现在还有效没有** ——
    "当前实际采用的本地版本"请用 ``load_library`` 给的
    ``installed_version``（那里已经按内容签名核对过了）。
    """
    return _override_parts(read_version_overrides().get(uuid or ""))[0]


def set_version_override(uuid: str, version: str, sig: str = "",
                         path: str = "") -> None:
    """记一条手动填的本地版本（**带上当前内容签名**）。

    传空 ``version`` = 清除这条记录。

    ``sig`` 是"填这个版本时该模组的内容签名"，**必须带上**，否则这条记录
    会退化成"旧格式"：`_resolve_override` 对没有签名的记录一律「先沿用」，
    将来模组更新了它也**不会自动失效** —— 那正是 issue #1 的坑。

    为了少一个踩坑点：``sig`` 留空但给了 ``path`` 时**现算一次**；
    两者都空才会真的写成旧格式记录（只用于测试 / 历史数据迁移场景）。

    ★ 2026-10-12 修正：原实现的 docstring 写着"留空就现场算一次"，
    但代码只是 ``sig or ""`` —— 文档与行为不符，等于留了个"静默造出
    永久冻结记录"的口子（新加的测试正是这样把它照出来的）。
    """
    cfg = read_config()
    ov = cfg.get("version_overrides") or {}
    if uuid:
        if version:
            if not sig and path:
                try:
                    sig = content_sig(path, fresh=True)
                except Exception:  # noqa: BLE001
                    sig = ""
            ov[uuid] = {"version": str(version), "sig": sig or ""}
        else:
            ov.pop(uuid, None)
        write_config({"version_overrides": ov})


def clear_version_override(uuid: str) -> bool:
    """清掉这一条手动版本（= 用户主动选择"恢复自动探测"）。返回是否真清掉了。"""
    cfg = read_config()
    ov = cfg.get("version_overrides") or {}
    if uuid in ov:
        ov.pop(uuid, None)
        write_config({"version_overrides": ov})
        return True
    return False


def heal_legacy_overrides(entries) -> int:
    """存量自愈：清掉「旧格式、且当前不产生任何影响」的手动版本记录。

    为什么需要（issue #1 评估，2026-10-12）：
      v1.8.11 及更早写下的手动版本**没有内容签名**，而 `_resolve_override`
      对没有签名的记录是「先沿用」（有意保留用户数据）—— 于是"不冻结"机制
      对它们**完全不生效**。后果：升级到修复版之后，这些记录照旧永久生效，
      反馈者那 16 条里 2 条现在就在误报、其余 14 条一更新模组就变成误报。
      而让用户自己逐条右键去清，16 条 ≈ 48 次点击，且 GUI 里没有批量入口。

    本函数只清**可证明无副作用**的那一类：``手动值 == 自动探测值``。
      有它没它，工具采用的本地版本**完全一样**，判定也一样 ——
      所以清掉它不改变任何结论，只是把一条"看起来是用户设的、
      实际什么都不影响"的噪音记录删掉（这类正是「指定来源」当年无条件
      写入 override 留下的历史包袱）。

    ★ 刻意**不**清的两种情况（都得由用户自己决定）：
      · 手动值 != 自动探测值 → 有实际影响，界面标出来让用户核对；
      · 自动探测值为空 → 清掉会把「有版本可比」变成「版本存疑」，那是有害的
        （实测反馈者的 Mine Flags (AiO) 就是这种：目录名解析不出版本，
         那条手动值 `1.0` 反而是**唯一**能比大小的依据）。

    返回清掉几条。调用方负责记日志 / 提示用户。
    """
    doomed: list[str] = []
    for e in entries or ():
        if not e.get("override_legacy"):
            continue
        manual = str(e.get("manual_version") or "").strip()
        auto = str(e.get("auto_version") or "").strip()
        # ★ 两个条件缺一不可：值相同 **且** 自动值非空。
        if manual and auto and manual == auto:
            u = str(e.get("uuid") or "")
            if u:
                doomed.append(u)
    if not doomed:
        return 0
    cfg = read_config()
    ov = cfg.get("version_overrides") or {}
    cleaned = 0
    for u in doomed:
        if u in ov:
            ov.pop(u, None)
            cleaned += 1
    if cleaned:
        write_config({"version_overrides": ov})
    return cleaned


def prune_version_overrides(valid_uuids) -> int:
    """删掉**已经不在库里**的孤儿记录（模组被删了 / 换了 uuid）。

    没有这个清理，孤儿记录会永远躺在 config.json 里：用户早就忘了填过，
    模组也不在了，配置却还在慢慢变大。返回删掉几条。
    """
    valid = set(valid_uuids or ())
    cfg = read_config()
    ov = cfg.get("version_overrides") or {}
    if not ov:
        return 0
    gone = [k for k in ov if k not in valid]
    for k in gone:
        ov.pop(k, None)
    if gone:
        write_config({"version_overrides": ov})
    return len(gone)


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
    """读工具自己的 config.json。

    · 文件**不存在** → 返回 {}（第一次运行，正常情况）
    · 文件存在但**读不出来**（另一个进程正在写 / 被手工改坏）→ 重试 3 次，
      仍失败就返回 {}，并把原因记进 ``_cfg_last_error``。

    ★ 为什么要这个全局标记（2026-10-18 的事故）：旧版读失败直接返回 {}，
      然后 :func:`write_config` 拿"空 + 本次改动"整份写回磁盘 ——
      用户填过的 N 网 key / GitHub token 就这样被**静默抹掉**了。
      现在写盘前会先看这个标记：读到的是"空壳"时**不拿它覆盖**。
    """
    global _cfg_last_error
    cfg = os.path.join(app_data_dir(), "config.json")
    if not os.path.isfile(cfg):
        _cfg_last_error = ""
        return {}
    last = ""
    for attempt in range(3):
        try:
            with open(cfg, encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _cfg_last_error = ""
                return data
            last = f"顶层不是对象（{type(data).__name__}）"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if attempt < 2:
            time.sleep(0.05 * (attempt + 1))
    _cfg_last_error = last
    return {}


def write_config(patch: dict) -> bool:
    """把 ``patch`` 合并进 config.json —— **原子写 + 跨进程串行**，返回是否写成功。

    三条保护（2026-10-18 事故之后加的，别删）：

    ① **原子替换**：先写 ``config.json.tmp`` 再 ``os.replace`` ——
       别的进程永远看不到写了一半的文件。旧版直接 ``open("w")`` 截断再写，
       另一个进程恰好在这瞬间读，就会读到半个 JSON → 解析失败 → 当空 →
       再整份写回，把别人的配置全抹掉（那次就是这么丢掉用户的 key 的）。
    ② **读到空壳时不覆盖**：磁盘上已有 config.json 但这次读不出来时，
       先把坏文件改名留档（``config.json.bad-<时间>``，路径可用
       :func:`config_backup_path` 取到），再只写这次的改动 ——
       即使用户的东西已经被别的原因弄丢，也能从那个 .bad 里找回来。
    ③ **跨进程互斥**：两个实例同时"读→改→写"仍会互相盖掉对方的字段
       （原子写只解决"读到半个文件"，不解决"后写的盖掉先写的"）。
       这里用文件锁把这段串起来，见 :func:`_config_lock`。
    """
    global _cfg_backed_up
    cfg = os.path.join(app_data_dir(), "config.json")
    _cfg_backed_up = ""
    with _config_lock():
        cur = read_config()
        if _cfg_last_error and os.path.isfile(cfg):
            bad = f"{cfg}.bad-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                os.replace(cfg, bad)
                _cfg_backed_up = bad
            except OSError:
                pass
        cur.update(patch)
        tmp = cfg + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cfg)
            return True
        except Exception:  # noqa: BLE001
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False


def config_backup_path() -> str:
    """上一次 :func:`write_config` 把旧配置留档到了哪里（没留档就是空串）。

    给界面用：留过档就说明"磁盘上那份配置当时读不出来"，
    必须告诉用户一声，而不是让他以后自己发现设置怎么没了。
    """
    return _cfg_backed_up


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
    # 用户手动修正过的本地版本（按 uuid 记）。
    # ★ 不是"无条件优先" —— 会先按内容签名核对，见 _resolve_override。
    overrides = (read_config().get("version_overrides") or {})
    # 「导入时记下的本地版本」—— 导入那一刻确切知道的版本 + 当时的内容签名。
    # ★ 排在目录名之前：导入新版会复用旧目录，目录名里那个版本号是永久旧值
    #   （issue #1 用户实测：目录名 "Vanilla-Plus-Megapack-v40" 装着 v41 的内容）。
    installed_recs = (read_config().get("installed_versions") or {})
    lib: list[dict] = []
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
        _imported = _resolve_installed(installed_recs.get(entry["uuid"]),
                                      entry.get("path") or "")
        entry["imported_version"] = _imported
        # ★ 2026-10-19 修正（用户实测：「更新以后还是显示未更新…名字成 V20 了
        #   本地版本还是 V19」）：
        #   原来是 `entry["nexus_version"] or _pick_local_version(...)` ——
        #   `or` 短路，N 网那条**陈旧**的 nexusData.version 只要非空就赢，
        #   导入时记下的确切版本（"20"）根本没机会被读到 → 永远报「有更新」。
        #   现在先把两个来源合并（冲突时取更新的那个），再往下走。
        _merged = _merge_nexus_and_imported(entry["nexus_version"], _imported)
        auto = (
            _merged
            or _pick_local_version(entry, _imported)
            or ""
        )
        entry["auto_version"] = auto
        # 「装的是哪个变体」—— 多变体 mod（如 Megapack 的 Rows 版）靠它挑对文件
        entry["variant"] = local_variant(entry)
        # 「手动填的版本」—— ★ 不再无条件优先，见 _resolve_override 的说明：
        #   内容没变就沿用；内容变了（模组被更新过）就自动失效、回到 auto。
        rec = overrides.get(entry["uuid"])
        if rec is not None:
            use, sig, why = _resolve_override(rec, entry.get("path") or "")
            entry["manual_version"] = _override_parts(rec)[0]
            entry["manual_sig"] = sig
            entry["manual_state"] = why
            # 「旧格式记录」= 没有内容签名的那种（v1.8.11 及更早写的）。
            #   ★ 标出来是给存量自愈看的（`heal_legacy_overrides`）：这类记录
            #   绕过了"不冻结"机制，只有它能被安全清理；新格式一律不动。
            entry["override_legacy"] = not sig
            if use:
                entry["installed_version"] = use
                entry["version_is_manual"] = True
                entry["override_why"] = why
                entry["override_stale"] = False
            else:
                # 自动失效：本地版本改用自动探测值，并把"你填的旧值"留在旁边，
                # 供界面如实说明"你填的 X 已失效，现在按自动探测的 Y 判断"。
                entry["installed_version"] = auto
                entry["version_is_manual"] = False
                entry["override_why"] = why
                entry["override_stale"] = True
        else:
            entry["installed_version"] = auto
            entry["version_is_manual"] = False
            entry["manual_version"] = ""
            entry["manual_sig"] = ""
            entry["manual_state"] = ""
            entry["override_why"] = ""
            entry["override_stale"] = False
            entry["override_legacy"] = False
        lib.append(entry)
    return lib


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
