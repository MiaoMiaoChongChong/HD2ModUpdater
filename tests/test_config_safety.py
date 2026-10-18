# -*- coding: utf-8 -*-
"""config.json 的写入安全（2026-10-18 丢 key 事故之后补的回归测试）。

要钉住的：
  ① 合并写：写别的字段时，**已有的 key 必须还在**；
  ② 原子写：先写 .tmp 再替换，跑完不留半成品；
  ③ 磁盘上已有 config.json 但**读不出来**（另一个进程正在写、或被改坏）时，
     不能拿"空 + 本次改动"整份覆盖 —— 原文件要留档成 .bad-*，
     用户还能从里面把 key 找回来；
  ④ 文件不存在（第一次运行）时照常写。

这几条对应一次真实事故：测试进程与一个多余的界面实例同时写 config.json，
旧实现（直接 open("w") 截断 + 读失败当空）把用户填的 N 网 key 抹掉了。
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = os.path.join(HERE, "_tmp_cfg_data")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["HD2U_DATA_DIR"] = _TMP

from hd2_updater import arsenal  # noqa: E402

_fail: list[str] = []
_pass = 0


def check(name, cond, detail=""):
    global _pass
    if cond:
        _pass += 1
        print(f"  ✓ {name}")
    else:
        _fail.append(f"{name}　{detail}")
        print(f"  ✗ {name}　{detail}")


CFG = os.path.join(_TMP, "config.json")


def read_raw() -> dict:
    with open(CFG, encoding="utf-8-sig") as f:
        return json.load(f)


def write_raw(text: str) -> None:
    with open(CFG, "w", encoding="utf-8") as f:
        f.write(text)


print("【① 合并写：不能把别人的字段写没】")
check("write_config 返回 True（写成功了）",
      arsenal.write_config({"nexus_api_key": "KEY123",
                            "github_token": "GH456"}) is True)
check("没有留档（这次是正常写）", arsenal.config_backup_path() == "",
      arsenal.config_backup_path())
check("两个凭据写进去了",
      read_raw().get("nexus_api_key") == "KEY123"
      and read_raw().get("github_token") == "GH456", str(read_raw()))
arsenal.write_config({"version_overrides": {"u1": {"version": "2"}}})
check("之后写别的字段，key 还在",
      read_raw().get("nexus_api_key") == "KEY123"
      and read_raw().get("github_token") == "GH456", str(read_raw()))
check("新字段也进去了",
      read_raw().get("version_overrides", {}).get("u1", {}).get("version") == "2",
      str(read_raw()))

print("\n【② 原子写：不留半成品】")
check("没有 .tmp 残留", not os.path.exists(CFG + ".tmp"))
check("没有 .bad 残留（这次是正常写）",
      not glob.glob(CFG + ".bad-*"), str(glob.glob(CFG + ".bad-*")))

print("\n【③ 读不出来时：留档 + 不能抹掉原内容】")
write_raw('{"nexus_api_key": "OLDKEY", "github_token": "OLDGH",')   # 半截 JSON
check("准备：文件确实是坏的", arsenal.read_config() == {})
check("准备：读失败被记下来了", bool(arsenal._cfg_last_error),
      repr(arsenal._cfg_last_error))
arsenal.write_config({"log_enabled": True})
bads = glob.glob(CFG + ".bad-*")
check("坏文件被改名留档了", len(bads) == 1, str(bads))
kept = open(bads[0], encoding="utf-8").read() if bads else ""
check("留档里还能看到原来的 key", "OLDKEY" in kept, kept[:120])
check("本次改动照常写进去了", read_raw().get("log_enabled") is True,
      str(read_raw()))
check("旧实现会把 key 抹掉 —— 现在新文件里确实没有（但 .bad 里有，能找回）",
      "nexus_api_key" not in read_raw(), str(read_raw()))

print("\n【④ 文件不存在：第一次运行要能正常写】")
os.remove(CFG)
for p in glob.glob(CFG + ".bad-*"):
    os.remove(p)
arsenal.write_config({"nexus_api_key": "NEWKEY"})
check("新建成功", read_raw().get("nexus_api_key") == "NEWKEY", str(read_raw()))

print("\n【⑤ 顶层不是对象（被手工改坏）也算坏文件】")
write_raw("[1, 2, 3]")
arsenal.write_config({"log_enabled": False})
check("没有抛异常，改动写进去了", read_raw().get("log_enabled") is False,
      str(read_raw()))
check("又留了一份档", len(glob.glob(CFG + ".bad-*")) == 1,
      str(glob.glob(CFG + ".bad-*")))

print("\n【⑥ 并发写：谁也不能把别人的字段盖掉】")
# 这一条对应"两个窗口同时开着"的场景：原子写只保证读不到半个文件，
# 但"读→改→写"交错时后写的会盖掉先写的 —— 靠 _config_lock 串起来。
import threading  # noqa: E402

for p in glob.glob(CFG + ".bad-*"):
    os.remove(p)
os.remove(CFG)
errors: list = []


def worker(i: int) -> None:
    try:
        for _ in range(5):
            arsenal.write_config({f"k{i}": i})
    except Exception as e:  # noqa: BLE001
        errors.append(e)


ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
for t in ts:
    t.start()
for t in ts:
    t.join()
raw = read_raw()
missing = [i for i in range(8) if raw.get(f"k{i}") != i]
check("8 个线程各写各的，字段一个都没丢", not missing and not errors,
      f"missing={missing} errors={errors}")

print("\n" + "=" * 62)
if _fail:
    print(f"✗ {len(_fail)} 项未通过（通过 {_pass}）：")
    for x in _fail:
        print("   · " + x)
    sys.exit(1)
print(f"✓ 全部通过（{_pass} 项）")
