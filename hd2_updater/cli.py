# -*- coding: utf-8 -*-
"""命令行模式：不开窗口也能检查更新（排查问题、或接任务计划用）。

用法：
    python run.py --check
    python run.py --check --all       # 连已最新的也列出来
"""
from __future__ import annotations

import sys

from .arsenal import find_data_file, load_library, read_config
from . import providers
from .engine import (
    ST_BETA,
    ST_DOWNLOADED,
    ST_ERROR,
    ST_NO_SOURCE,
    ST_OK,
    ST_UPDATE,
    Engine,
    apply_download_records,
    source_key,
)
from .util import friendly_error

W = 78
STATUS_TXT = {
    ST_UPDATE: "有更新",
    ST_DOWNLOADED: "已下载",
    ST_BETA: "仅测试版",
    ST_OK: "已最新",
    ST_ERROR: "查询失败",
    ST_NO_SOURCE: "未找到来源",
}


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    show_all = "--all" in argv

    # 只读自检：不联网，确认「能独立运行 + 版本 + 读得到部署状态」。
    # 给打包验证用（EXE 拷到空目录、屏蔽 Python 环境变量后跑这条）。
    if "--selftest" in argv:
        from . import __version__
        path, how = find_data_file()
        print(f"version={__version__}")
        if not path:
            print("selftest=FAIL 没找到模组库")
            return 2
        lib = load_library(path)
        dep = sum(1 for e in lib if e.get("deployed"))
        print(f"mods={len(lib)} deployed={dep} game_dir={bool(lib and lib[0].get('game_dir'))}")
        print("selftest=OK")
        return 0

    path, how = find_data_file()
    if not path:
        print("没找到 HD2 Arsenal 的模组库（hd2a_data.json）。", file=sys.stderr)
        print("请先运行一次 HD2 Arsenal，或用图形界面手动选择位置。", file=sys.stderr)
        return 2

    cfg = read_config()
    print("=" * W)
    print("  HD2 Mod 更新检查")
    print(f"  模组库：{path}（{how}）")
    print("=" * W)

    eng = Engine(
        path,
        api_key=cfg.get("nexus_api_key", ""),
        github_token=cfg.get("github_token", ""),
        use_search=True,
        log=lambda m: print(f"  · {m}"),
        progress=lambda d, t, m: None,
    )
    eng.load()
    eng.resolve_sources()
    results = eng.check()
    eng.persist_resolved()
    results = apply_download_records(results)

    order = {ST_UPDATE: 0, ST_DOWNLOADED: 1, ST_BETA: 2, ST_ERROR: 3,
             ST_NO_SOURCE: 4, ST_OK: 5}
    results = sorted(results, key=lambda r: order.get(r["status"], 9))

    print()
    n_upd = 0
    n_done = 0
    for r in results:
        st = r["status"]
        if st == ST_OK and not show_all:
            continue
        e = r["entry"]
        best = r.get("best") or {}
        tag = STATUS_TXT.get(st, "?")
        line = f"[{tag}] {e.get('label') or e.get('dirname')}"
        print(f"\n{line}")
        print(f"    本地 {r.get('installed') or '?'}"
              + (f"  →  线上 {best.get('version')}" if best.get("version") else ""))
        prov = best.get("provider")
        if prov:
            print(f"    来源 {prov}"
                  + (f"　{best.get('url')}" if best.get("url") else ""))
        if st == ST_DOWNLOADED:
            rec = r.get("downloaded") or {}
            print(f"    已下载 {rec.get('version')} → {rec.get('file')}")
        elif best.get("download_url"):
            print(f"    直链 {best['download_url']}")
            print(f"          （{best.get('asset_name')}）")
        elif best.get("file_url"):
            # 和图形界面右键「用浏览器取文件（接力）」打开的是同一个地址
            print(f"    取文件 {providers.handoff_url(best)}")
        if best.get("how"):
            print(f"    依据 {best['how'][:100]}")
        if st == ST_ERROR and best.get("error"):
            print(f"    错误 {friendly_error(best['error'])}")
        if st == ST_UPDATE:
            n_upd += 1
        if st == ST_DOWNLOADED:
            n_done += 1

    print()
    print("=" * W)
    print(f"  有更新：{n_upd} 个"
          + (f"　　已下载待导入：{n_done} 个" if n_done else ""))
    print("=" * W)
    return 1 if n_upd else 0
