# -*- coding: utf-8 -*-
"""下载记录：记住「哪个模组的哪个版本，我已经下过了、文件在哪」。

为什么需要这个：
    下载完 ZIP 之后，用户还得去 Arsenal 里导入，中间隔着一段时间。
    这期间列表若仍写着「有更新」，用户会以为刚才那一下没成功、又点一遍。

所以单独记一笔，界面上把这一条标成「已下载」，并带上文件路径，方便直接打开。

存 <数据目录>/downloads.json：
{
  "<模组 key>": {
      "version": "v40",                      # 下的是哪个版本
      "file": "D:\\...\\xxx-v40.zip",         # 落地位置
      "at": "2026-10-08 22:51",              # 下载时间
      "provider": "github",
      "label": "Vanilla Plus Megapack"
  }
}
"""
from __future__ import annotations

import json
import os

from .util import app_data_dir, now_str


def path() -> str:
    return os.path.join(app_data_dir(), "downloads.json")


def load() -> dict:
    try:
        with open(path(), encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save(data: dict) -> None:
    """原子写（先写 .tmp 再 replace），避免写一半断电留下坏文件。"""
    p = path()
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data or {}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except OSError:
        # 记不下来不该影响下载本身
        try:
            os.remove(tmp)
        except OSError:
            pass


def mark(key: str, version: str, file_path: str,
         provider: str = "", label: str = "") -> None:
    """记一笔「这个模组的这个版本已经下过」。

    新下载会把 ``applied_at`` 清空 —— 换了新文件，上次"已导入"的结论不再适用。
    """
    if not key:
        return
    data = load()
    data[key] = {
        "version": str(version or ""),
        "file": str(file_path or ""),
        "at": now_str(),
        "provider": str(provider or ""),
        "label": str(label or ""),
        "applied_at": "",
    }
    save(data)


def mark_applied(key: str) -> None:
    """把这个下载记成「已经导入过了」。

    为什么必须记：ZIP 导入完**文件还在原地**，光看"文件在不在"永远分不清
    这个下载到底应用了没有 —— 用户会看到「已下载」一直挂着，以为刚才那一下
    没成功。记一笔就能把「待导入」和「已导入」分开。
    """
    if not key:
        return
    data = load()
    rec = data.get(key)
    if not isinstance(rec, dict):
        return
    rec["applied_at"] = now_str()
    data[key] = rec
    save(data)


def applied(key: str) -> bool:
    """这个下载是不是已经导入过了。"""
    rec = get(key)
    return bool(rec and str(rec.get("applied_at") or "").strip())


def state(key: str) -> str:
    """给「下载」页用的三态：``pending`` / ``applied`` / ``missing``。

    · ``missing`` —— 文件已不在原处（被移走/删掉），别继续装作能用
    · ``applied`` —— 已经导入过
    · ``pending`` —— 文件在、还没导入，这是**要用户处理**的那些
    """
    rec = get(key)
    if not rec:
        return ""
    fp = str(rec.get("file") or "")
    if not fp or not os.path.isfile(fp):
        return "missing"
    return "applied" if str(rec.get("applied_at") or "").strip() else "pending"


def all_records() -> list[tuple[str, dict]]:
    """全部下载记录（给「下载」页列出来）。新的排前面。"""
    items = [(k, v) for k, v in load().items() if isinstance(v, dict)]
    items.sort(key=lambda kv: str(kv[1].get("at") or ""), reverse=True)
    return items


def get(key: str) -> dict | None:
    """拿这个模组的下载记录（不管文件还在不在）。"""
    rec = load().get(key or "")
    return rec if isinstance(rec, dict) else None


def forget(key: str) -> None:
    data = load()
    if key in data:
        data.pop(key, None)
        save(data)


def match(key: str, version: str) -> dict | None:
    """只有当「记的版本 == 现在线上这个版本」**且文件还在、且还没导入过**时才算已下载。

    - 版本对不上（线上又更新了）→ 返回 None，界面继续显示「有更新」，正确。
    - 文件被移走/删掉 → 返回 None —— 用户多半已经导入 Arsenal 了，
      不该继续挂着「已下载」骗人。
    - **已经导入过了 → 也返回 None**：这时的状态应该是按装好的版本去比
      （通常是「已最新」），而不是继续显示「已下载」让用户以为还没处理。
    """
    ver = str(version or "").strip()
    if not key or not ver:
        return None
    rec = get(key)
    if not rec:
        return None
    if str(rec.get("applied_at") or "").strip():
        return None
    if str(rec.get("version") or "").strip() != ver:
        return None
    fp = str(rec.get("file") or "")
    if not fp or not os.path.isfile(fp):
        return None
    return rec


def file_missing(key: str, version: str) -> dict | None:
    """版本对得上、但文件不在了 —— 用来提示「上次下的文件已不在原处」。"""
    ver = str(version or "").strip()
    if not key or not ver:
        return None
    rec = get(key)
    if not rec or str(rec.get("version") or "").strip() != ver:
        return None
    fp = str(rec.get("file") or "")
    if fp and not os.path.isfile(fp):
        return rec
    return None
