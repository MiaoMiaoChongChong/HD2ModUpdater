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

        # ------------------------------------------------------------------
        # issue #1「我明明更新过了，它还一直说有更新」的核心判定（2026-10-12）
        #
        # 为什么放在 selftest 里而不是只放在 tests/：这里跑的是**打包后的 EXE**
        # 也会走的同一条代码路径。以前"本地版本"被永久冻住、导入还会抹掉
        # Arsenal 的 nexusData —— 这类"改了但测不出来"的东西，只有在真程序里
        # 验一遍才算数。
        try:
            import json as _js
            import os as _os2
            import tempfile as _tf2
            from . import arsenal as _ar
            from .engine import evaluate as _ev
            from .gui import App as _App

            _t = _tf2.mkdtemp(prefix="hd2u_selftest_v_")
            _md = _os2.path.join(_t, "mods")
            _uu = "eeee1111-2222-3333-4444-555566667777"
            _d0 = _os2.path.join(_md, "Some Mod 16493 10 2026-01-01T00-00Z x_AR1")
            _os2.makedirs(_d0)
            for _i in range(3):
                with open(_os2.path.join(_d0, f"slot{_i}.patch_0"), "wb") as _fh:
                    _fh.write(b"content-%d-v10" % _i)
            _ent = {"uuid": _uu, "label": "Some Mod", "path": _d0,
                    "description": "", "iconPath": None, "tags": ["misc"],
                    "patchFileNames": [], "options": [], "contentHash": "",
                    "addedAt": "2026-01-01T00:00:00.000Z",
                    "previousVersions": [],
                    "nexusData": {"modId": "16493", "fileId": "555",
                                  "version": "12", "updateTimestamp": 1}}
            _pr = dict(_ent)
            _pr.update({"enabled": True, "deployed": True, "changed": False,
                        "new": False, "copiedFiles": [], "renamedFiles": []})
            _df2 = _os2.path.join(_t, "hd2a_data.json")
            with open(_df2, "w", encoding="utf-8") as _f:
                _js.dump({"modsLibrary": [_ent],
                          "modsList": {"Default Profile": {"mods": [_pr]}},
                          "selectedProfile": "Default Profile",
                          "userModsDir": _md, "userGameDir": "D:\\g",
                          "setTopPriority": False}, _f, ensure_ascii=False)
            # 内容签名：算得出 + 稳定
            _s1 = _ar.content_sig(_d0)
            _same = (_ar.content_sig(_d0) == _s1 and bool(_s1))
            # 记下手动值（带签名）→ 应生效
            _ar.set_version_override(_uu, "10", _ar.content_sig(_d0, fresh=True))
            _e = _ar.load_library(_df2)[0]
            _manual_ok = (_e["installed_version"] == "10"
                          and _e["version_is_manual"])
            # ★ 内容变了 → 手动值必须自动失效
            _d1 = _os2.path.join(_md, "Some Mod 16493 12 2026-10-11T11-52Z y_AR2")
            _os2.rename(_d0, _d1)
            for _i in range(3):
                with open(_os2.path.join(_d1, f"slot{_i}.patch_0"), "wb") as _fh:
                    _fh.write(b"content-%d-v12-NEW" % _i)
            with open(_df2, encoding="utf-8") as _f:
                _d2 = _js.load(_f)
            _d2["modsLibrary"][0]["path"] = _d1
            with open(_df2, "w", encoding="utf-8") as _f:
                _js.dump(_d2, _f, ensure_ascii=False)
            _e2 = _ar.load_library(_df2)[0]
            _unfrozen = (not _e2["version_is_manual"]
                         and _e2["override_stale"])
            _followed = (_e2["installed_version"] == "12")
            _st = _ev(_e2, [{"provider": "nexus", "ok": True,
                             "version": "12", "url": "x",
                             "download_url": ""}])["status"]
            _clear = _ar.clear_version_override(_uu)
            import shutil as _sh2
            _sh2.rmtree(_t, ignore_errors=True)
            if not (_same and _manual_ok and _unfrozen and _followed
                    and _st == "ok" and _clear):
                print(f"version=FAIL sig={_same} manual={_manual_ok} "
                      f"unfrozen={_unfrozen} followed={_followed} "
                      f"status={_st} clear={_clear}")
                print("selftest=FAIL 本地版本「不冻结」判定不对")
                return 3
            print(f"version=OK 签名稳定={_same} 手动生效={_manual_ok} "
                  f"自动解冻={_unfrozen} 跟上新版={_followed} 判定={_st} "
                  f"可清除={_clear}")

            # ★★ 第二个根因（用户 2026-10-12 实测）：导入更新版会**复用旧目录**，
            #   文件夹名里那个版本号是永久旧值（"…-v40" 里装着 v41 的内容），
            #   而本地版本原本优先读文件夹名 → 永远报「有更新」。
            #   这里照用户实测的那两个 mod 的真实目录名走一遍导入。
            _zip_ok = True
            try:
                import zipfile as _zf
                _t2 = _tf2.mkdtemp(prefix="hd2u_selftest_i_")
                _md2 = _os2.path.join(_t2, "mods")
                _os2.makedirs(_md2)
                for _uu2, _dir2, _zn, _want in (
                        ("c0000000-0000-0000-0000-000000000001",
                         "HD2Runtime-0.30.3-runtime_AR172840",
                         "HD2Runtime-0.31.3-runtime.zip", "0.31.3"),
                        ("c0000000-0000-0000-0000-000000000002",
                         "Vanilla-Plus-Megapack-v40_AR205686",
                         "Vanilla-Plus-Megapack-v41.zip", "41")):
                    _dd = _os2.path.join(_md2, _dir2)
                    _os2.makedirs(_dd)
                    with open(_os2.path.join(_dd, "a.patch_0"), "wb") as _fh:
                        _fh.write(b"old content")
                    _e3 = {"uuid": _uu2, "label": _os2.path.splitext(_zn)[0],
                           "path": _dd, "description": "", "iconPath": None,
                           "tags": ["misc"], "patchFileNames": [],
                           "options": [], "contentHash": "",
                           "addedAt": "", "previousVersions": [],
                           "nexusData": None}
                    _p3 = dict(_e3)
                    _p3.update({"enabled": True, "deployed": True,
                                "changed": False, "new": False,
                                "copiedFiles": [], "renamedFiles": []})
                    _df3 = _os2.path.join(_t2, "hd2a_data.json")
                    with open(_df3, "w", encoding="utf-8") as _f:
                        _js.dump({"modsLibrary": [_e3],
                                  "modsList": {"P": {"mods": [_p3]}},
                                  "selectedProfile": "P",
                                  "userModsDir": _md2,
                                  "userGameDir": "D:\\g",
                                  "setTopPriority": False}, _f,
                                 ensure_ascii=False)
                    _zp = _os2.path.join(_t2, _zn)
                    with _zf.ZipFile(_zp, "w") as _z:
                        _z.writestr("manifest.json", _js.dumps(
                            {"Name": _os2.path.splitext(_zn)[0], "Guid": _uu2}))
                        _z.writestr("a.patch_0",
                                    ("brand new " + _want).encode())
                    from . import modimport as _MI
                    _MI.import_zip(_df3, _zp, mods_dir=_md2, api_key="")
                    _row = next(x for x in _ar.load_library(_df3)
                                if x["uuid"] == _uu2)
                    if _row["installed_version"] != _want:
                        _zip_ok = False
                        break
                import shutil as _sh3
                _sh3.rmtree(_t2, ignore_errors=True)
            except Exception as e:  # noqa: BLE001
                _zip_ok = False
                print(f"   import 校验异常：{e}")
            if not _zip_ok:
                print("version2=FAIL 导入新版后本地版本没跟上"
                      "（文件夹名停在旧版本号 → 会永远报有更新）")
                print("selftest=FAIL 导入后版本识别不对")
                return 3
            print("version2=OK 导入新版后本地版本跟上"
                  "（复用旧目录时也能认对）")

            # ★★ 存量自愈（2026-10-12，issue #1 评估查出的缺口）：
            #   老用户升级上来时，v1.8.11 写的旧格式记录（没有内容签名）
            #   会被永久沿用 —— "不冻结"机制对它完全不生效，于是他仍然看到
            #   「更新了还说有更新」。这里清掉**可证明无副作用**的那一类
            #   （手动值 == 自动探测值），有实际影响 / 自动值探测不出的留着。
            try:
                _th = _tf2.mkdtemp(prefix="hd2u_selftest_h_")
                _mh = _os2.path.join(_th, "mods")
                _uh = "hhhh1111-2222-3333-4444-555566667777"
                _dh = _os2.path.join(_mh, "Heal 16493 5 2026-01-01T00-00Z h_AR1")
                _os2.makedirs(_dh)
                with open(_os2.path.join(_dh, "a.patch_0"), "wb") as _fh:
                    _fh.write(b"heal content")
                _eh = {"uuid": _uh, "label": "Heal", "path": _dh, "description": "",
                       "iconPath": None, "tags": ["misc"], "patchFileNames": [],
                       "options": [], "contentHash": "", "addedAt": "",
                       "previousVersions": [], "nexusData": None}
                _ph = dict(_eh)
                _ph.update({"enabled": True, "deployed": True, "changed": False,
                            "new": False, "copiedFiles": [], "renamedFiles": []})
                _dfh = _os2.path.join(_th, "hd2a_data.json")
                with open(_dfh, "w", encoding="utf-8") as _f:
                    _js.dump({"modsLibrary": [_eh],
                              "modsList": {"P": {"mods": [_ph]}},
                              "selectedProfile": "P", "userModsDir": _mh,
                              "userGameDir": "D:\\g", "setTopPriority": False},
                             _f, ensure_ascii=False)
                # v1.8.11 的旧格式记录：手动值 5 == 自动探测 5 → 应被清
                import hd2_updater.util as _u2
                _cfg_h = _os2.path.join(_u2.app_data_dir(), "config.json")
                with open(_cfg_h, "w", encoding="utf-8") as _f:
                    _js.dump({"version_overrides": {_uh: "5"}}, _f)
                _rows_h = _ar.load_library(_dfh)
                _legacy_ok = bool(_rows_h[0].get("override_legacy"))
                _n_h = _ar.heal_legacy_overrides(_rows_h)
                _cleared = (_uh not in (_ar.read_version_overrides() or {}))
                _still = _ar.load_library(_dfh)[0]["installed_version"] == "5"
                import shutil as _sh4
                _sh4.rmtree(_th, ignore_errors=True)
                if not (_legacy_ok and _n_h == 1 and _cleared and _still):
                    print(f"heal=FAIL legacy={_legacy_ok} n={_n_h} "
                          f"cleared={_cleared} value_kept={_still}")
                    print("selftest=FAIL 存量自愈不对"
                          "（老用户升级后旧记录不会被清）")
                    return 3
                print(f"heal=OK 认出旧格式={_legacy_ok} 清掉={_n_h} "
                      f"记录已删={_cleared} 结论不变={_still}")
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print(f"heal=FAIL {e}")
                print("selftest=FAIL 存量自愈模块不可用")
                return 3

            # ★ 错误分类（2026-10-12：「已删除的 mod 被说成没填 key」）
            #   核心：分类靠结构化标签、不靠文案子串；mod 级优先于凭据级。
            from .providers import kind_of_http as _koh
            from .gui import App as _App2

            _gone = _koh(403, '{"message":"Mod not available: 2"}')
            _noperm = _koh(403, "Forbidden")
            _nf = _koh(404, '{"message":"No Mod Found: 9"}')
            _note = _App2._error_note("这个 mod 被隐藏/删除，或你的 key 无权访问")
            _ok_note = "API key" not in _note
            if not (_gone == "gone" and _noperm == "noperm"
                    and _nf == "gone" and _ok_note):
                print(f"errkind=FAIL 403删={_gone} 403权限={_noperm} "
                      f"404={_nf} 已删除提示不提key={_ok_note}")
                print("selftest=FAIL 错误分类不对（删除会被说成没填 key）")
                return 3
            print(f"errkind=OK 403已删除={_gone} 403无权限={_noperm} "
                  f"404={_nf} 「已删除」提示不再提 key={_ok_note}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"version=FAIL {e}")
            print("selftest=FAIL 本地版本模块不可用")
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
