# -*- coding: utf-8 -*-
"""命令行模式：不开窗口也能检查更新（排查问题、或接任务计划用）。

用法：
    python run.py --check
    python run.py --check --all       # 连已最新的也列出来
"""
from __future__ import annotations

import io
import sys

from .arsenal import find_data_file, load_library, read_config
from . import diag
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


class _Tee:
    """把 print 的输出**同时**写进日志。

    为什么需要：命令行模式（`--check` / `--selftest`）以前只往控制台说话，
    而打包成 `--windowed` 的 EXE 后**根本没有控制台** —— 于是"打包自检
    到底验出了什么"事后一行都查不到，只能看退出码。现在每行都进日志。
    """

    def __init__(self, real=None) -> None:
        self._real = real
        self._buf = ""

    def write(self, s) -> int:
        if self._real is not None:
            try:
                self._real.write(s)
            except Exception:  # noqa: BLE001
                pass
        self._buf += str(s)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                diag.log(line)
        return len(str(s))

    def flush(self) -> None:
        if self._real is not None:
            try:
                self._real.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self) -> bool:
        return False


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    show_all = "--all" in argv

    # 打包成 --windowed 的 EXE 时没有控制台，sys.stdout / stderr 可能是 None ——
    # 那时 print 会直接抛 AttributeError，把"命令行自检"整条路弄崩。
    # 兜一个空流让它照常跑完（结果同时写进日志，见 diag）。
    try:
        if sys.stdout is None:
            sys.stdout = io.StringIO()
        if sys.stderr is None:
            sys.stderr = io.StringIO()
    except Exception:  # noqa: BLE001
        pass

    # 命令行模式也留日志：以前 --check / --selftest 的输出只存在控制台里，
    # 用户（或我在打包验证时）回头什么都查不到。
    diag.setup()
    diag.event("命令行模式", " ".join(argv) if argv else "（无参数）")
    try:
        sys.stdout = _Tee(sys.stdout)
        sys.stderr = _Tee(sys.stderr)
    except Exception:  # noqa: BLE001
        pass

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

        # 必须真的跑一遍部署模块的核心逻辑 —— 否则打进 EXE 的 deploy 模块
        # 缺了也照样 selftest=OK，等于没验（新功能就这么漏过）。
        try:
            from .deploy import build_plan, collect_slots
            enabled = {e["uuid"] for e in lib if e.get("enabled")}
            game_dir = next((e.get("game_dir") for e in lib if e.get("game_dir")), "")
            if not enabled or not game_dir:
                print("deploy=SKIP 没有已启用模组或缺游戏目录")
            else:
                from .arsenal import first_mod_priority
                fmp = first_mod_priority(path)
                plan = build_plan(lib, enabled, game_dir, first_mod_priority=fmp)
                idxs = sorted(i["index"] for i in plan.items)
                contiguous = idxs == list(range(plan.slot_count))
                # 方向校验：位次在前者编号必须更小（默认设置下越靠后越赢）。
                # 这是曾经实现反了的地方，所以放进打包自检里盯住。
                by_mod: dict[str, list[int]] = {}
                for it in plan.items:
                    by_mod.setdefault(it["mod_uuid"], []).append(it["index"])
                ordered = sorted(
                    (e for e in lib if e.get("uuid") in enabled
                     and e.get("deploy_order") is not None),
                    key=lambda e: e["deploy_order"])
                dir_ok = True
                prev = -1
                for e in ordered:
                    ix = sorted(by_mod.get(e["uuid"], []))
                    if not ix:
                        continue
                    if ix[0] <= prev:
                        dir_ok = False
                        break
                    prev = ix[-1]
                print(f"deploy=OK prefix={plan.prefix} slots={plan.slot_count} "
                      f"contiguous={contiguous} dir_ok={dir_ok} "
                      f"first_mod_priority={fmp}")
                if not contiguous:
                    print("selftest=FAIL 部署编号不连续")
                    return 3
                if not dir_ok:
                    print("selftest=FAIL 加载顺序方向不对（编号没有随 Arsenal 位次递增）")
                    return 3
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"deploy=FAIL {e}")
            print("selftest=FAIL 部署模块不可用")
            return 3

        # 同步模块也要真跑一遍：镜像接管能读出「Arsenal 已部署的」，
        # 且写回前的冲突检测可用 —— 缺了这段，模块没打进包也照样 OK。
        try:
            from .arsenal_sync import arsenal_running, deployed_uuids
            mirror = deployed_uuids(path)
            running = arsenal_running()
            print(f"sync=OK mirror={len(mirror)} arsenal_running={running}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"sync=FAIL {e}")
            print("selftest=FAIL 同步模块不可用")
            return 3

        # 拖入导入模块也要真跑一遍：**光 import 不够** —— 真正会漏的是
        # tkinterdnd2 的平台二进制（tkdnd）没被打进包，那时 import 照样成功、
        # 但拖放能力没了。所以这里真去试建一个支持拖放的根窗口。
        try:
            from .modimport import safe_folder_name, split_drop_paths
            from .gui import DND_READY
            probe = "n/a"
            if DND_READY:
                import tkinterdnd2 as _t
                r = _t.TkinterDnD.Tk()
                r.withdraw()
                probe = str(r.tk.call("package", "require", "tkdnd"))
                r.destroy()
            nm = safe_folder_name("a/b:c*d")
            dp = split_drop_paths("{C:\\x y\\a.zip} D:\\b.zip")
            if len(dp) != 2:
                raise RuntimeError(f"拖放路径解析不对：{dp}")
            print(f"import=OK dnd={DND_READY} tkdnd={probe} "
                  f"safename={nm} droppaths={len(dp)}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"import=FAIL {e}")
            print("selftest=FAIL 导入模块不可用")
            return 3

        # 「下载 / 导入」页也要真验一下：这一页依赖 records 的新接口
        # （state / all_records / mark_applied）。**只 import 模块不够** ——
        # 要确认接口真的在（打包漏文件/漏更新时，界面会在用户点下去时才炸）。
        try:
            import os as _os
            import tempfile as _tf
            from . import records as _r
            from .gui import DownloadsPanel as _DP
            old = _os.environ.get("HD2U_DATA_DIR")
            _os.environ["HD2U_DATA_DIR"] = _tf.mkdtemp(prefix="hd2u_st_")
            try:
                z = _os.path.join(_os.environ["HD2U_DATA_DIR"], "t.zip")
                open(z, "wb").write(b"PK")
                _r.mark("k", "v1", z, "github", "自检")
                s1 = _r.state("k")
                _r.mark_applied("k")
                s2 = _r.state("k")
                n = len(_r.all_records())
            finally:
                if old is None:
                    _os.environ.pop("HD2U_DATA_DIR", None)
                else:
                    _os.environ["HD2U_DATA_DIR"] = old
            if (s1, s2, n) != ("pending", "applied", 1):
                raise RuntimeError(f"下载记录状态不对：{s1}/{s2}/{n}")
            if not hasattr(_DP, "_import_selected"):
                raise RuntimeError("下载页缺少导入入口")
            print(f"downloads=OK 三态={s1}->{s2} 页面={_DP.__name__}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"downloads=FAIL {e}")
            print("selftest=FAIL 下载页不可用")
            return 3

        # 诊断 / 反馈模块也要真跑一遍（v1.8.0 新增）：日志能写、脱敏生效、
        # 报告能生成。**只 import 不够** —— 脱敏一旦失效，用户的 key 就会
        # 出现在他要发出去的那段文字里，这种错必须在打包阶段就拦住。
        try:
            import json as _json
            import os as _os
            import tempfile as _tf
            from . import diag as _dg
            old = _os.environ.get("HD2U_DATA_DIR")
            _os.environ["HD2U_DATA_DIR"] = _tf.mkdtemp(prefix="hd2u_st_diag_")
            try:
                lp = _dg.setup()
                _dg.event("自检", "打包自检写入的一行")
                tail = _dg.log_tail(20)
                cfg_path = _os.path.join(_os.environ["HD2U_DATA_DIR"], "config.json")
                with open(cfg_path, "w", encoding="utf-8") as f:
                    _json.dump({"nexus_api_key": "SELFTESTKEY123456"}, f)
                _dg.refresh_secrets()
                masked = _dg.redact("SELFTESTKEY123456")
                rp = _dg.report(desc="打包自检", include_library=False)
            finally:
                if old is None:
                    _os.environ.pop("HD2U_DATA_DIR", None)
                else:
                    _os.environ["HD2U_DATA_DIR"] = old
                _dg.setup()          # 恢复成真实数据目录
            if not lp:
                raise RuntimeError("日志文件没建起来")
            if "自检" not in tail:
                raise RuntimeError("日志没写进去")
            if "SELFTESTKEY123456" in masked:
                raise RuntimeError("脱敏失效：key 没被抹掉")
            if "SELFTESTKEY123456" in rp:
                raise RuntimeError("脱敏失效：诊断报告里出现了 key")
            print(f"diag=OK log={bool(lp)} report_lines={len(rp.splitlines())}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"diag=FAIL {e}")
            print("selftest=FAIL 诊断模块不可用")
            return 3

        # 备份轮转也要真验一下：这一页会**删文件**，所以关键在"只删该删的"。
        try:
            import os as _os
            import tempfile as _tf
            from . import backups as _bk
            from .gui import BackupsPanel as _BP
            _t = _tf.mkdtemp(prefix="hd2u_st_bk_")
            _gd = _os.path.join(_t, "game", "data")
            _os.makedirs(_os.path.join(_gd, _bk.BACKUP_DIR_NAME, "20260101-000000"))
            _os.makedirs(_os.path.join(_gd, _bk.BACKUP_DIR_NAME, "20260102-000000"))
            _md = _os.path.join(_t, "mods")
            _os.makedirs(_md)
            _df = _os.path.join(_t, "hd2a_data.json")
            open(_df, "w").write("{}")
            _rep = _bk.scan(_os.path.join(_t, "game"), _md, _df,
                            {"keep_game_backups": 1, "keep_old_mod_dirs": 1,
                             "keep_data_baks": 1})
            if _rep["freeable_items"] != 1:
                raise RuntimeError(f"保留份数判定不对：{_rep['freeable_items']}")
            # 手选删除必须挡住"名字不合规"的东西
            _bad = _os.path.join(_md, "正常模组目录")
            _os.makedirs(_bad)
            _r2 = _bk.prune_selected([_bad], {"game_dir": _os.path.join(_t, "game"),
                                              "mods_dir": _md, "data_file": _df})
            if not _os.path.isdir(_bad):
                raise RuntimeError("删掉了不该删的目录！")
            import shutil as _sh
            _sh.rmtree(_t, ignore_errors=True)
            print(f"backups=OK 可清理={_rep['freeable_items']} "
                  f"拦下误删={len(_r2['failed'])} 页面={_BP.__name__}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"backups=FAIL {e}")
            print("selftest=FAIL 备份页不可用")
            return 3

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

    def _log(m: str) -> None:
        """命令行模式的每条过程日志：控制台 + 日志文件各一份。"""
        diag.log(m)
        print(f"  · {m}")

    eng = Engine(
        path,
        api_key=cfg.get("nexus_api_key", ""),
        github_token=cfg.get("github_token", ""),
        use_search=True,
        log=_log,
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
    diag.event("命令行检查", f"完成：共 {len(results)} 个，有更新 {n_upd}，"
                            f"已下载待导入 {n_done}")
    return 1 if n_upd else 0
