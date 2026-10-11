# -*- coding: utf-8 -*-
"""重新生成 README 用的界面预览截图（**全部虚构数据**，不含任何真实模组名/路径）。

    python tools/make_screenshots.py

产出：
    docs/screenshot-main.png    更新检查页
    docs/screenshot-deploy.png  部署模组页

为什么必须用虚构数据而不是直接抓真实窗口：真实窗口里是用户自己的模组列表
（含个人安装路径），发到公开仓库就是泄漏。这里手工喂一组**虚构**的库与检查结果，
让界面按**真实渲染逻辑**画出来 —— 图片是程序自己画的，不是拼的。

为什么要有这个脚本（别再手抓）：
  · 标题栏/侧边栏会显示 `__init__.__version__`，所以**改版本号必须连带重生成截图**，
    否则 README 里的图还标着旧版本号，用户会以为下错了（v1.0.0→v1.0.1 时踩过）。
  · 手抓容易抓错窗口：2026-10-11 有一次把 **Windows 壁纸**当成了界面截图存进
    `docs/screenshot-main.png`，差点随 v1.8.11 一起发出去。脚本化 + 下面那几条
    自检断言（尺寸/非壁纸/版本号）就是为了让这类错误不可能溜过去。

安全：全程只写临时目录（`HD2U_DATA_DIR` + `HD2U_READONLY_GUARD`），
绝不读写真机的 Arsenal 库。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

# ---------------------------------------------------------------- 隔离
_TMP = os.path.join(tempfile.gettempdir(), "hd2u_shot")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["HD2U_DATA_DIR"] = _TMP
# 护栏：任何"写真实 Arsenal 库"的尝试都会被拒绝（同测试套件）
os.environ["HD2U_READONLY_GUARD"] = "1"

# 假路径前缀：一眼能看出是示例，不含任何真实盘符/用户名
FAKE_ROOT = r"C:\Games\HD2Arsenal"
FAKE_GAME = r"C:\Games\Helldivers2"
# 16 位十六进制前缀（部署文件名的前缀）。故意用明显假的值。
PREFIX = "0123456789abcdef"

# ---------------------------------------------------------------- 虚构模组
# 覆盖部署页要展示的全部状态，让截图能把功能讲全。
# (uuid, 显示名, 启用, 已部署, 内容变过, 在配置档, 槽位数)
FAKE_MODS = [
    ("m01", "Example Megapack - Rows", True, True, False, True, 3),
    ("m02", "Sample HUD Overlay", True, True, False, True, 1),
    ("m03", "Demo Quick Actions", True, True, True, True, 2),   # 内容更新过 → 需重新部署
    ("m04", "Sample Loader", True, False, False, True, 1),      # 已启用还没部署
    ("m05", "Example Translation Pack", True, True, False, True, 4),
    ("m06", "Demo Armor Pack", False, False, False, True, 1),   # Arsenal 里未启用
    ("m07", "Mystery Mod", False, False, False, False, 1),      # 未加入配置档
    ("m08", "Example Credits Tracker", False, False, False, False, 1),
]


def _build_fake_tree() -> str:
    """造出假模组目录（含 patch 文件）+ 假游戏 data 目录，返回库文件路径。

    必须真的建出 patch 文件：部署页的「槽位」列和"将部署 N 个槽位"是
    `collect_slots()` 实际扫目录数出来的 —— 不建文件的话截图里全是 0，
    就没法把"一个选项一个文件"这件事讲清楚。
    """
    for _u, label, _en, _dep, _ch, _inp, n_slots in FAKE_MODS:
        mod_dir = os.path.join(FAKE_ROOT, "mods", label)
        for i in range(n_slots):
            # 单槽位模组直接放根目录；多槽位的放进子目录（= 一个选项一个槽位）
            slot = mod_dir if n_slots == 1 else os.path.join(mod_dir, f"Option {i + 1}")
            os.makedirs(slot, exist_ok=True)
            for suf in ("", ".gpu_resources", ".stream"):
                with open(os.path.join(slot, f"{PREFIX}.patch_0{suf}"), "wb") as f:
                    # 给点体积，让"约 X MB"是个像样的数
                    f.write(b"\0" * (2 * 1024 * 1024 if not suf else 1024))
    # 游戏 data 目录：部署目标（build_plan 会检查它存在）
    os.makedirs(os.path.join(FAKE_GAME, "data"), exist_ok=True)

    lib = {
        "userModsDir": os.path.join(FAKE_ROOT, "mods"),
        "userGameDir": FAKE_GAME,
        "selectedProfile": "default",
        "modsLibrary": [
            {"uuid": u, "label": lb,
             "path": os.path.join(FAKE_ROOT, "mods", lb),
             "enabled": en}
            for u, lb, en, _d, _c, _i, _n in FAKE_MODS
        ],
        "modsList": {
            "default": {
                "label": "Default Profile",
                # 顺序即加载优先级（数组越靠后优先级越高）
                "mods": [
                    {"uuid": u, "enabled": en, "deployed": dep, "changed": ch}
                    for u, _lb, en, dep, ch, inp, _n in FAKE_MODS if inp
                ],
            }
        },
    }
    p = os.path.join(_TMP, "hd2a_data.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=1)
    return p


FAKE_LIB = _build_fake_tree()

from hd2_updater import gui as G                                    # noqa: E402
from hd2_updater import __version__                                 # noqa: E402
from hd2_updater.engine import (                                    # noqa: E402
    ST_BETA, ST_DOWNLOADED, ST_ERROR, ST_NO_SOURCE, ST_OK, ST_UNKNOWN, ST_UPDATE,
)

# 别让它去探测真机 / 别后台重建站点索引（那会联网且拖慢）
G.find_data_file = lambda *a, **k: (FAKE_LIB, "示例数据")   # type: ignore[assignment]
G.matcher.index_is_stale = lambda *a, **k: False            # type: ignore[assignment]
G.messagebox.showinfo = lambda *a, **k: None                # type: ignore[assignment]
G.messagebox.showwarning = lambda *a, **k: None             # type: ignore[assignment]


# ---------------------------------------------------------------- 抓窗口
def grab(hwnd_root: int) -> tuple[int, int, bytes]:
    """用 PrintWindow 抓窗口内容（含标题栏），返回 (宽, 高, BGRA 像素)。"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    PW_RENDERFULLCONTENT = 0x00000002

    r = wintypes.RECT()
    user32.GetWindowRect(hwnd_root, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetWindowDC(hwnd_root)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    ok = user32.PrintWindow(hwnd_root, memdc, PW_RENDERFULLCONTENT)
    if not ok:
        raise RuntimeError("PrintWindow 失败（窗口可能还没画出来）")

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BMI(ctypes.Structure):
        _fields_ = [("bmiHeader", BMIH), ("bmiColors", wintypes.DWORD * 3)]

    bi = BMI()
    bi.bmiHeader.biSize = ctypes.sizeof(BMIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h          # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    raw = bytearray(buf.raw)
    for i in range(0, len(raw), 4):     # BGRA -> RGBA
        raw[i], raw[i + 2] = raw[i + 2], raw[i]
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd_root, hdc)
    return w, h, bytes(raw)


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    rows = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride]
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def save(path: str, w: int, h: int, rgba: bytes) -> None:
    with open(path, "wb") as f:
        f.write(png_bytes(w, h, rgba))
    print(f"  已写入 {os.path.relpath(path, REPO)}  ({os.path.getsize(path) / 1024:.0f} KB, {w}x{h})")


# ---------------------------------------------------------------- 更新检查页
def row(app, label, status, installed, best):
    entry = next(e for e in app.library if e["label"] == label)
    return {"entry": entry, "status": status, "installed": installed,
            "best": best, "checks": []}


def main() -> int:
    app = G.App()
    app.cancel_startup_detect()
    app.data_file = FAKE_LIB
    app.library = G.load_library(FAKE_LIB)
    # 显示成模板化路径，不露真实用户名
    app.var_arsenal.set(r"C:\Users\<你的用户名>\AppData\Local\hd2arsenal\hd2a_data.json")
    app.var_nexus_key.set("")
    app.var_github_token.set("")

    results = [
        row(app, "Example Megapack - Rows", ST_UPDATE, "35",
            {"provider": "github", "version": "v40", "download_url": "https://example.invalid/a.zip",
             "asset_name": "Example-Megapack-Rows-v40.zip",
             "pick_reason": "匹配到同变体「Rows」"}),
        row(app, "Sample HUD Overlay", ST_UPDATE, "1.2.0",
            {"provider": "nexus", "version": "1.4.0",
             "url": "https://www.nexusmods.com/helldivers2/mods/1234",
             "file_url": "https://www.nexusmods.com/helldivers2/mods/1234?tab=files&file_id=999&nmm=1"}),
        row(app, "Demo Quick Actions", ST_UPDATE, "0.6.4",
            {"provider": "ayaka", "version": "0.7.0",
             "url": "https://ayakamods.com/mods/demo-quick-actions.4001/"}),
        row(app, "Sample Loader", ST_BETA, "r41",
            {"provider": "ayaka", "version": "42", "prerelease": True,
             "url": "https://ayakamods.com/mods/sample-loader.4002/"}),
        row(app, "Example Credits Tracker", ST_ERROR, "0.9.1",
            {"provider": "github", "error": "站点暂时不可达（超时）——过一会儿再试"}),
        row(app, "Demo Armor Pack", ST_UNKNOWN, "2026-09-24",
            {"provider": "ayaka", "version": "2026-10-04", "unknown_ver": True,
             "error": "两边格式不同，没法比大小：本地「2026-09-24」vs 线上「2026-10-04」。"
                      "大多是站点把「版本」栏填成了日期。"}),
        row(app, "Mystery Mod", ST_NO_SOURCE, "", None),
        row(app, "Example Translation Pack", ST_OK, "39.1",
            {"provider": "nexus", "version": "40.1", "how": "Arsenal 库里的 N 网编号",
             "url": "https://www.nexusmods.com/helldivers2/mods/5678"}),
    ]
    app.results = results
    app._populate(results)
    app.var_status.set("检查完成：3 个有更新")
    app.update()
    app.update_idletasks()

    user32 = ctypes.windll.user32
    top = user32.GetAncestor(app.winfo_id(), 2)     # GA_ROOT
    n = user32.GetWindowTextLengthW(top)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(top, buf, n + 1)
    print(f"  窗口标题 = {buf.value!r}")
    if f"v{__version__}" not in buf.value:
        raise SystemExit(f"[X] 标题栏没有当前版本号 v{__version__} —— 截图会误导用户")
    w, h, rgba = grab(top)
    save(os.path.join(REPO, "docs", "screenshot-main.png"), w, h, rgba)

    # ------------------------------------------------------------ 部署页
    app._show_page("deploy")
    for _ in range(8):
        app.update()
        app.update_idletasks()
    w2, h2, rgba2 = grab(top)
    save(os.path.join(REPO, "docs", "screenshot-deploy.png"), w2, h2, rgba2)

    app.destroy()
    print("\n完成。**记得肉眼看一眼两张图**（脚本能保证尺寸与版本号，"
          "但「图里画的到底是不是应用界面」最终还得人确认）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
