# HD2 Mod Updater v1.8.17

中文界面的 **Helldivers 2 模组更新管理器** —— 给 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664) 补上它缺少的更新检测。

**下载下面的 `HD2ModUpdater.exe` 双击即可**，不需要装 Python。

---

## 它是干什么的

如果你**所有**模组都是从 Nexus 装的，那 Arsenal 自己就够了，不用装这个。

但只要你有模组来自别处 —— GitHub 的 release、ayakamods.com 的下载页 ——
**Arsenal 就永远不知道它们有新版**（它只查 Nexus 来源的模组）。这个工具专门补这一块：

| | Arsenal 自带 | 本工具 |
|---|---|---|
| Nexus 来源模组 | ✅ | ✅（需自填 API key） |
| GitHub 来源模组 | ❌ | ✅ |
| ayakamods 来源模组 | ❌ | ✅ |
| 自动识别模组来自哪个站点 | ❌ | ✅ |
| 一键下载 | 仅 N 网 | GitHub 全自动；另两站半自动 |

> ⚠ **前置条件：需要先装 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664)（Nexus mod 4664）。**
> 本工具不自己维护模组清单 —— 「你装了哪些 mod、哪些启用、加载顺序」都读自 Arsenal。

## 它能做什么

| 功能 | 说明 |
|---|---|
| **三个站点统一检查** | GitHub / Nexus / ayakamods，一次查完 |
| **智能识别来源** | 替你判断每个 mod 在网上对应哪个页面，认不出的右键手动填 |
| **网站优先级** | 多平台发布时按 **GitHub > N网 > ayakamods** 取（能直连下载的优先） |
| **自动下载** | GitHub 直链直接下；另两站走"浏览器接力"，你点一下即可 |
| **部署到游戏** | 自己挑要部署哪些模组 —— Arsenal 只能整包部署，这个能只放一部分 |
| **改加载顺序** | 拖动行调优先级，写回 Arsenal（写前自动备份） |
| **编辑模组选项** | 像 Arsenal 一样勾选每个模组的功能开关（如 Megapack 的 21 个） |
| **拖入 ZIP 就能加模组** | 把 mod 压缩包拖到窗口上，自动解包并加进列表 |
| **删除模组** | 右键即可，删前写清影响；要不要连磁盘文件一起删由你决定 |
| **屏蔽某个模组的更新与查找** | 右键那一行就能静音：以后检查更新、识别来源都跳过它；已屏蔽的右键项变成「取消屏蔽」 |
| **来源总览** | 一个 mod 在三个站点各查到什么、什么版本，想用哪个点哪个 |
| **备份** | 部署前自动备份游戏文件，并管着别让备份无限增长 |
| **出问题好反馈** | 一键生成诊断信息（含日志），不用你手打一堆环境信息 |

### 怎么用（三步）

1. 打开工具 —— 它会**自动找到** Arsenal 的模组库（找不到才让你手动选）
2. 第一次用，点 **「识别来源」**：它会给每个模组找出网上的发布页面，**结果会存下来，以后不用重复找**
3. 之后平时就点 **「检查更新」** —— 有更新的会标出来，能自动下载的点「下载全部可自动下载的」

> N 网要填一次 **API key**（免费申请，个人自用是允许的）才能自动查。
> 工具里点「设置…」，里面有「去获取 Nexus key」按钮会直接带你去官方页面。
> 不填也能用，只是 N 网模组会提示需要 key，其余照常。

### 界面预览

![更新检查页](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/raw/main/docs/screenshot-main.png)

一行一个模组，一眼看清：**本地版本 / 线上版本 / 来源站点 / 该做什么**。

![部署模组页](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/raw/main/docs/screenshot-deploy.png)

（截图里是**虚构的示例模组**，不是作者自己的模组库。）

> 更完整的说明（每个页面怎么用、部署注意事项、已知限制、出问题怎么办）
> 都在 **[README](https://github.com/MiaoMiaoChongChong/HD2ModUpdater#readme)** 里。

---

## 怎么升级

**下载下面的 `HD2ModUpdater.exe`，替换掉你原来那个**，双击运行即可。
配置和数据都存在 `%LOCALAPPDATA%\hd2-updater\`，不受影响，**不用重新填令牌**。

> **首次运行可能被 Windows SmartScreen 拦一下**（提示「Windows 已保护你的电脑」）。
> 原因是没有做代码签名（个人项目，签名证书要花钱）。点 **「更多信息」→「仍要运行」** 即可。
> 源码全部公开，不放心可以自己看或自己打包。

## 环境要求

- Windows 10 / 11（64 位）
- 用 EXE **不需要** Python
- **需要先安装 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664)**（Nexus mod 4664）

---

## 这一版改了什么

**修复：手动导入新版之后，本地版本不再停在旧号。**

如果你遇到过这种情况，这一版就是给你的 —— 你更新了模组，模组名已经变成新版
（比如「…- v20」），但「**本地**」那一列还是旧号（比如 19），
于是**永远**显示「有更新」，重装多少次都没用。

### 为什么会这样

本地版本其实有两个来源，它们**各有一半的道理**，冲突时之前**旧的那条赢了**：

| 来源 | 它说的是 | 什么时候会过期 |
|---|---|---|
| Arsenal 库里的「N 网版本」 | 这个模组在 N 网上对应的是哪个版本 | **只有从 N 网下载安装时才会更新**。你手动导入一个 zip（比如从 GitHub 下的），Arsenal 并不知道版本变了 → 这个号一直停在旧值 |
| 本工具导入时记下的版本 | 导入那一刻装的**确实是**哪个版本（带内容校验，最准） | 内容再变一次就自动失效（不会变成"永久冻结"） |

之前取值的顺序让第一条**无条件优先**（代码里是个短路写法），
于是第二条根本没机会被读到 —— 明明它是对的。

### 这次怎么改的

两个来源冲突时**取更新的那个**。

- 你手动导入 v20、Arsenal 还记着 19 → 取 **20**，不再报「有更新」
- 反过来（N 网记录更新）→ 仍然取 N 网那个，不会把真更新压掉
- 两个比不了大小（比如一个是日期、一个是版本号）→ 保持原样，不乱动
- **没有导入记录的存量用户行为完全不变**（你库里其余 21 个模组实测一个字节都没变）

> 顺带一提：打包后的 EXE 自检里加了一条对应检查（`stale=OK`），
> 以后这类回归会在打包环节就被挡住，而不是等用户撞上。

<details>
<summary>点开看完整更新日志（新用户不必读）</summary>

> 按版本倒序，完整历史见
> [README 的「更新日志」一节](https://github.com/MiaoMiaoChongChong/HD2ModUpdater#更新日志)。

**v1.8.17** — 修「手动导入新版后本地版本仍停在旧号、永远说有更新」：N 网那条记录与导入记录冲突时取更新的那个

**v1.8.16** — 新增「屏蔽这个模组的更新与查找」：右键即可让某个模组不参与检查更新与来源识别，
已屏蔽时同一项变成「取消屏蔽」；两个页面都能做，诊断里也会标出来

**v1.8.15** — 让「以前填过版本号」的老用户真正好起来：启动时自动清掉无影响的旧记录；
弹窗安全动作设为默认；写明该按哪个键

**v1.8.14** — 修「N 网已删除的 mod 被说成『你没填 API key』」：失败分类不再靠匹配文案，改由产生处打标签

**v1.8.13** — 修「更新并导入了模组，仍然一直说有更新」：导入时记下确切版本；目录名与模组自带文件不一致时取更新的

**v1.8.12** — 修「手工填的版本被永久记住、优先级最高」：改成跟着内容走，内容一变自动失效；
「本地」列标出来源；导入不再抹掉 Arsenal 的 nexusData

**v1.8.11** — 修「来源总览重查查到了却不显示」

**v1.8.10** — 修来源总览「上面说没找到、下面显示找到了」（只修了显示同步，没修到根子上）

**v1.8.9** — 来源总览改成「三个站点固定三行 + 单独查找」，删掉一个空白窗

**v1.8.8** — 新增「来源总览」：一个 mod 的全部来源，想用哪个点哪个

**v1.8.7** — 修 GitHub 预发布版看不到（改读 `/releases` 列表）

**v1.8.6** — 把「为什么这一行显示的是这个来源」摊开给你看

**v1.8.5** — 修 GitHub 上有仓库却认不出来（搜索词少了"紧凑形式"）

**v1.8.4** — 修网络自检报假警 + 代理说明

**v1.8.3** — 把「浏览器能开、工具却被挡」讲清楚

**v1.8.2** — 修设置页"下半截滚不动" + 重建索引"看起来没生效"

**v1.8.1** — 设置页能滚动了 + 「查不到」时给出路

**v1.8.0** — 新增「反馈 / 帮助」页（一键诊断）

**v1.7.x** — 没装 Arsenal 时把话说清楚；新增「备份」页

**v1.6.x** — 新增「下载 / 导入」页，把下载和安装接起来

**v1.5.x** — 右键删除模组；修从 N 网网页直接下载的 mod 认不出来源

**v1.4.0** — 把 mod 压缩包拖进界面就能加入列表

**v1.3.0** — 部署页列出整个模组库（含未启用、未加入配置档的）

**v1.2.x** — 选项编辑 + 拖动排序；部署页交互改进

</details>

---

## English summary

**HD2 Mod Updater** — a Chinese-UI companion updater for **HD2 Arsenal** (Helldivers 2).

Arsenal only tracks updates for mods installed from Nexus; mods you grabbed from GitHub
or ayakamods.com are invisible to it. This tool fills that gap.

**What it does**

- Checks all three sites (GitHub / Nexus / ayakamods) for updates in one go
- Auto-detects where each mod is published (manual override available)
- Site priority: GitHub > Nexus > ayakamods (prefers sources with direct downloads)
- Downloads what it can (GitHub fully automatic; the other two via a browser hand-off)
- **Deploys mods to your game** — pick which mods to deploy, reorder load priority,
  toggle each mod's options. Arsenal can only deploy everything as one bundle.
- Drag & drop a mod `.zip` to add it to your library; right-click to delete
- **Mute individual mods**: right-click a row → "Block this mod's update checks and source
  lookup". Blocked mods are skipped by update checks and source detection (no requests
  sent), shown as "blocked" and sorted last; right-click again to unblock.
- Automatic backups of game files before deploying

**Getting started**

1. Open it — it auto-detects your Arsenal library
2. First run: click **"Identify sources"** (results are saved, no need to redo)
3. Day to day: click **"Check for updates"**

**Requires [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664) to be installed.**
Windows 10/11, no Python needed (standalone EXE).

**Upgrade:** replace your existing `HD2ModUpdater.exe`. Config and data live in
`%LOCALAPPDATA%\hd2-updater\` and are untouched. Unsigned, so SmartScreen may warn —
click "More info" → "Run anyway".

**This version fixes** a case where the local version stayed on the old number after you
imported a newer build by hand — the mod's name updated but the "local" column kept the
old value, so it reported "update available" forever. The local version now takes the
newer of the two available sources.

---

## 许可证

[MIT](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/blob/main/LICENSE)
