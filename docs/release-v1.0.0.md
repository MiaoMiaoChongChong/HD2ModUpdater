# HD2 Mod Updater v1.0.0

中文界面的 **Helldivers 2 模组更新管理器** —— 给 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664) 补上它缺少的更新检测。

Arsenal 只对「从 Nexus 装的模组」查更新。你从 **GitHub** 或 **ayakamods.com** 手动导入的模组，
它永远不知道有新版。这个工具就是补这一块。

---

## 下载哪个文件

| 你的情况 | 下载 |
|---|---|
| **不想装 Python**（绝大多数人） | **`HD2ModUpdater.exe`** ← 双击即用，约 11 MB |
| 想自己从源码跑 / 改代码 | `Source code (zip)` |

> **首次运行会被 Windows SmartScreen 拦一下**：提示「Windows 已保护你的电脑」。
> 这是因为没做代码签名（个人项目，签名证书要花钱）。点 **「更多信息」→「仍要运行」** 即可。
> 源码全部公开，不放心可以自己看或自己打包。

---

## 它能做什么

| 功能 | 说明 |
|---|---|
| **三站点统一检查** | GitHub / Nexus / ayakamods 一个列表看完 |
| **自动识别来源** | 自动判断每个模组发布在哪个网站，不用你手动登记 |
| **双击就能更新** | 双击一行：GitHub 的直接下载；另两个站点打开正确页面并**自动把文件收回来** |
| **「已下载」标记** | 下过的版本标出来，不会反复催你、也不会重复下载 |
| **变体识别** | 识别你装的是哪个变体（如 Megapack 的 Rows 版），**不会把 A 变体更新成 B 变体** |
| **中文界面** | 全程中文，含悬停说明 |
| **只读，零侵入** | 只读 Arsenal 的模组库，绝不修改；加载与部署仍由 Arsenal 负责 |

**实测效果**：20 个模组里 19 个能自动识别出来源，剩余 1 个右键手动填一次网址即可。

---

## 怎么用

1. 双击 `HD2ModUpdater.exe`
2. 程序会自动找到 Arsenal 的模组库（找不到才让你手动选）
3. 点 **「检查更新」**
4. 有更新的行会标出来 —— **双击它**即可

### 两个可选令牌（不填也能用）

在「设置…」里可以填，都是免费的、个人自用是官方允许的：

- **Nexus API key** —— 填了才能自动检查 N 网模组的更新
- **GitHub token** —— 查询限额从 60 次/小时提到 5000

设置里有「去获取」按钮，一键跳到官方页面。

---

## 两个常见疑问

### 为什么 N 网 / ayakamods 不能一键下载？

**这是站点设计决定的，不是工具偷懒。** 实测（用免费账号打 Nexus API）：

```
用 API key 直接取下载链接 → HTTP 403
  "You don't have permission to get download links from the API
   without visting nexusmods.com - this is for premium users only."
```

Nexus 官方 API 文档写得很直白：非会员**必须**访问网站才能通过 API 下载 ——
那个授权 key 是你点了网页按钮之后服务器才发给你的，程序自己造不出来（Premium 会员才可跳过）。

**所以本工具的做法**：双击那一行 → 打开**正好停在你那个文件上**的页面 →
你点一下 SLOW DOWNLOAD → **工具一直盯着下载文件夹，下完自动把文件收进下载夹**。
你只需要点那一下按钮，剩下的它处理。

ayakamods 同理（未登录时下载按钮只是个禁用的 `<span>`，站点根本不把链接发给匿名访客）。

> 本工具**不碰你的 cookie**、不保存任何站点密码 —— 你的登录态本来就在浏览器里。

### 一个模组有多个版本（变体）会不会下错？

**不会。** 有些模组会并行发布多个变体，它们共用一个页面、共用版本号序列。例如：

```
Vanilla-Plus-Megapack-v40.zip          ← 普通版
Vanilla-Plus-Megapack-Rows-v35.zip     ← Rows 版
```

本工具会从安装目录名认出你装的是哪个变体，**只在同一个变体里更新**。
万一作者停更了你用的变体，它**不会**偷偷换成另一个，而是直接写明：

> ⚠ 变体：「Vanilla Plus Megapack - Rows」停更在 v35（更新的发布里已经没有它了），
> 这是该变体的最后一版 —— 要不要换成普通版由你决定

### 一个模组在三个站点都有，会走哪一个？

**先看能不能直接下载，再看站点优先级**（这条优先于站点顺序）：

```
第 1 步：能不能直接下载？        GitHub 能直连；另两个站点要你点一下
第 2 步：同样条件下比站点优先级   GitHub > N网 > ayakamods
```

| 情形 | 走哪一个 |
|---|---|
| 三站都有更新，GitHub 有直链 | **GitHub**（能全自动下载） |
| 三站都有更新，都拿不到直链 | **GitHub**（规则一致） |
| GitHub 查询失败（限流/超时） | **N网** |
| GitHub + N网 都失败 | **ayakamods** |
| GitHub 已最新，但 N网 报有更新 | **N网** |
| 三站都已最新 | **GitHub**（只是显示"已最新"） |

> 三个站点是**一起查**的，上面的切换不需要你额外操作。
> **查询失败的来源永远不会被选中** —— 不会把一个失败结果当成"更新"报给你。

---

## 已知限制

1. **ayakamods 靠爬网页**（站点没开放 API，搜索页还对脚本返回 403），站点改版就可能失效
2. **N 网 / ayakamods 需要你点一下下载按钮**（见上）
3. **少数模组认不出来**，右键「指定来源…」贴一次网址即可；
   指定错了可以用右键「撤销来源并重新识别」退回来（重识别没找到新来源时，
   原来的记录会被**自动还原**，不会丢）
4. **日期与版本号不能互比**：ayakamods 上有些模组把「版本」栏填成日期（如 `2026-09-24`）。
   这种情况会显示「版本存疑」而**不会**误报有更新
5. 极少数模组天生没有版本号，右键可手动填一次（长期记住）
6. **ayakamods 的中文模组**：站点上约三分之一的模组是中文名，它们的页面地址里
   带中文（`…/mods/机甲部位血量HUD.4775/`）。这种地址**原样粘贴即可**，
   不需要自己转成 `%E6%9C...`

---

## 环境要求

- Windows 10 / 11（64 位）
- 用 EXE **不需要** Python

---

## English summary

**HD2 Mod Updater** — a Chinese-UI companion updater for **HD2 Arsenal** (Helldivers 2).

Arsenal only tracks updates for mods installed from Nexus. Mods you installed from
GitHub or ayakamods.com are invisible to it. This tool fills that gap:

- Detects your Arsenal library automatically (read-only, never modifies it)
- Figures out where each mod is published (GitHub / Nexus / ayakamods)
- Checks all three sources for updates in one list
- **Double-click a row to update**: GitHub downloads directly; the other two open the
  exact right page and automatically collect the downloaded file for you
- Recognises *which variant* you installed, so it never swaps e.g. a "Rows" build
  for a regular one

**Download `HD2ModUpdater.exe`** — standalone, no Python required.
The interface is Chinese, but the workflow is a single double-click.

**Known limits**: Nexus and ayakamods require login and a manual click on their
download button (site policy — non-premium API downloads are blocked server-side);
ayakamods support relies on page parsing since the site has no API.

MIT licensed. Not affiliated with Arrowhead Game Studios, Sony, Nexus Mods or ayakamods.

---

## 许可证

[MIT](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/blob/main/LICENSE) ——
自由使用、修改、分发。

与 Arrowhead Game Studios、Sony、Nexus Mods、ayakamods 均无关联。
本工具只做更新检查与下载，不修改游戏文件。
