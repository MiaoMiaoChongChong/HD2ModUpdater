# HD2 Mod 更新管理器

给 **HD2 Arsenal** 补上它缺少的更新检测：**GitHub、Nexus、ayakamods** 三个来源统一检查，
中文界面，一键下载能自动化的部分。

Arsenal 只对「N 网下载的模组」查更新（详见下方原理），从 GitHub / ayakamods 手动导入的
ZIP 它永远不认识。这个工具就是补这一块——**它只读 Arsenal 的库，加载和部署仍然交给 Arsenal**。

> **English TL;DR** — A Chinese-UI companion updater for **HD2 Arsenal** (Helldivers 2).
> Arsenal only tracks updates for mods installed from Nexus; mods you grabbed from
> GitHub or ayakamods.com are invisible to it. This tool fills that gap: it auto-detects
> the Arsenal library, figures out where each mod is published, checks all three sites
> for updates, and downloads what it can. Read-only — it never modifies your Arsenal data.
> Windows 10/11, no Python needed (standalone EXE).

## 界面预览

![界面截图](docs/screenshot-main.png)

一行一个模组，一眼看清：**本地版本 / 线上版本 / 来源站点 / 该做什么**。

---

## 为什么需要它

如果你的模组全是从 Nexus 装的，**Arsenal 自己就够了，不用装这个**。

但只要你从别处装过模组 —— GitHub 的 release、ayakamods.com 的下载页 ——
Arsenal 就永远不知道它们有新版。这个工具专门补这一块。

| | Arsenal 自带 | 本工具 |
|---|---|---|
| Nexus 来源模组 | ✅ | ✅（需自填 API key） |
| GitHub 来源模组 | ❌ | ✅ |
| ayakamods 来源模组 | ❌ | ✅ |
| 自动识别模组来自哪个站点 | ❌ | ✅ |
| 一键下载 | 仅 N 网 | GitHub 全自动；另两站半自动 |

---

## 快速开始

### 方式一：下载 EXE（推荐，免装 Python）

到 [Releases](../../releases) 下载 `HD2ModUpdater.exe`，双击即可。

> 首次运行 Windows SmartScreen 可能提示"未知发布者"（未做代码签名），
> 点「更多信息」→「仍要运行」。

### 方式二：从源码运行

需要 Python 3.9+（tkinter 随 Python 自带）：

```powershell
python run.py           # 打开图形界面
python run.py --check   # 命令行检查一遍
```

1. 程序会自动找到 Arsenal 的模组库（找不到才让你手动选）
2. 点 **「检查更新」**
3. 有更新的会标出来；能自动下载的点 **「下载全部可自动下载的」**

### 第一次用（或换了台新电脑）

界面上会出现一条黄色提示：

> **第一次使用**：还没有识别过模组来源。点右边「开始识别来源」，
> 工具会自动找出这些模组各自在网上对应哪个页面。

点 **「开始识别来源」** 即可。它会为每个模组找出它发布在哪个网站
（GitHub / N网 / ayakamods），**结果会保存下来，以后不用重复找**。

识别完再点「检查更新」，就会去查每个网站上的最新版本。

> 「检查更新」其实也会顺带做识别 —— 但第一次用建议先单独点一次
> 「开始识别来源」，这样能清楚看到哪些模组认出来了、哪些要手动填。

### 两个按钮的分工

| 按钮 | 作用 | 什么时候点 |
|---|---|---|
| **识别来源** | 只找出"每个模组在网上对应哪个页面"，存起来 | 第一次用 / 加了新模组 / 有模组显示"未找到来源" |
| **检查更新** | 拿已知来源去各网站查最新版本 | 平时用 |

> 「重建站点索引」**平时不用点** —— 它是「识别来源」内部用的缓存机制
> （爬下 ayakamods 全部模组清单做本地匹配）。只有站点上架很多新模组、
> 导致识别不到时，才需要手动重建一次。鼠标悬停有详细说明。

### 填 Nexus API key（想要 N 网自动检查的话）

N 网要求带 key 才能查，**免费申请、个人自用是允许的**：

1. 在工具里点 **「设置…」**
2. 点 **「去获取 Nexus key」** 按钮 —— 会自动打开官方 API 页面
3. 页面上找到 **Personal API Key**，点复制
4. 粘回工具里，点 **「保存」**

> 那个按钮指向 `nexusmods.com/users/myaccount?tab=api`（Nexus 官方帮助文档里
> 指定的 API Access 页）。旁边还有「官方说明」按钮，指向其 API 使用政策。
> 没登录的话要先登录。

GitHub Token 同理 —— 设置里有两个按钮：

- **「新建 GitHub token」** → `github.com/settings/personal-access-tokens/new`
  （fine-grained，**GitHub 官方推荐**的类型）
- **「已建的令牌」** → 同一页面，用来复用已有的令牌

> 类型说明：GitHub 有 classic 与 fine-grained 两种令牌，两个页面都真实存在。
> 本工具用的是官方推荐的 fine-grained 页。权限**一个都不用勾**——
> 本工具只读公开仓库信息。

不填也能用 —— 只是 N 网模组会提示"需要设置 API key"，其余照常工作。

---

## 它能做什么

| 功能 | 说明 |
|---|---|
| **自动找到 Arsenal** | 探测 `%LOCALAPPDATA%\hd2arsenal\hd2a_data.json`，也会扫其他盘；找不到才让你选 |
| **三个站点统一检查** | GitHub / Nexus / ayakamods |
| **智能识别来源** | 替你判断每个 mod 在网上对应哪个页面，**实测算出 18/20** |
| **网站优先级** | 多平台发布时按 **GitHub > N网 > ayakamods** 取（能直连下载的优先） |
| **自动下载** | GitHub 的直链下载 + **sha256 校验** |
| **「已下载」标记** | 下过的版本标出来，不重复催、也不重复下 |
| **浏览器接力** | N网 / ayakamods 需要登录，工具开好**正确的下载页**并盯着下载夹，你点一下即可 |
| **换电脑不重填** | 数据位置可自定义到 U 盘/移动硬盘 —— 令牌、来源、索引、下载记录一起带走 |
| **手动指定来源** | 实在认不出来的，右键就能填 |

实测抓到过的真实更新：

```
Vanilla Plus Megapack         本地 35      →  线上 v40   （GitHub 直链）
HD2Runtime                    本地 0.28.1  →  线上 v0.30.0（GitHub 直链）
中文翻译包（简体 + 繁體）        本地 39.1    →  线上 40.1  （N网，接力取文件）
```

### 一个模组在三个站点都有，会走哪一个？

**先看能不能直接下载，再看站点优先级** ← 这条规则优先于站点顺序

```
第 1 步：能不能直接下载？        GitHub 能直连下载，另两个站点必须你点一下
第 2 步：同样条件下比站点优先级   GitHub(0) > N网(1) > ayakamods(2)
```

| 情形 | 走哪一个 | 为什么 |
|---|---|---|
| 三站都有更新，GitHub 有直链 | **GitHub** | 能全自动下载，最省事 |
| 三站都有更新，都拿不到直链 | **GitHub** | 规则一致，仍按站点优先级 |
| GitHub 查询失败（限流/超时） | **N网** | 失败的来源不会被选中 |
| GitHub + N网 都失败 | **ayakamods** | 继续往下降级 |
| GitHub 已最新，但 N网 报有更新 | **N网** | 谁报更新就按谁走 |
| 三站都已最新 | **GitHub** | 只是显示"已最新"，同样优先展示它 |

> 注：**查询失败的来源永远不会被选中**（否则会把一个失败结果当成"更新"给你）。
> 三个站点是**一起查**的，所以上面这些切换不需要你额外操作，也不会重复查两遍。
>
> 另：**变体识别会再叠加一层** —— 如果你装的是 GitHub 上的 Rows 版，
> 走 GitHub 时会挑 Rows；万一某个站点上你没有那个变体，工具会说明而不是随便换。

---

## 界面说明

```
┌─ Arsenal 模组库位置 ────────────────────────────────┐
│  C:\Users\...\hd2arsenal\hd2a_data.json  [重新探测] [手动选择…]
├─ 操作栏 ────────────────────────────────────────────┤
│  [检查更新] [下载全部可自动下载的] [识别来源] [只查未检查的] [设置…]
│                                            [智能搜索来源：开 ✓]
├─ 上手提示条（首次使用/有未识别时出现）──────────────┤
│  第一次使用：还没有识别过模组来源。点右边「开始识别来源」…  [开始识别来源]
├─ 结果表 ────────────────────────────────────────────┤
│  状态 │ 模组 │ 本地 │ 线上 │ 来源 │ 说明
│  有更新 │ Vanilla Plus Megapack Rows │ 35 │ v40 │ GitHub │ 可自动下载
│  已下载 │ HD2Runtime                 │ 0.28.1 │ v0.30.0 │ GitHub │ 已下好 v0.30.0 —— 去 Arsenal 导入即可
│  已最新 │ P-11 Self Heal             │ v8 │ v8  │ ayakamods │ 版本号已核对一致
│  未检查 │ Resupply Boxes Emissive    │ ?  │     │        │ 还没检查 —— 点上面的「检查更新」
└─────────────────────────────────────────────────────┘
```

**状态含义**：

| 状态 | 意思 |
|---|---|
| **有更新** | 线上有正式版更新 |
| **已下载** | 这一版已经下好了，文件就在下载夹里 —— 去 Arsenal 导入即可 |
| **仅测试版** | 只有预发布版可更（带 rc/beta/Test Build），自行决定 |
| **已最新** | 无需处理 |
| **未检查** | 刚打开、还没点「检查更新」 |
| **未找到来源** | 查过了，但没认出它对应哪个页面 —— 右键「指定来源…」填一次 |
| **查询失败** | 网络问题或需要填 key |
| **版本存疑** | 版本号格式无法比较（如站点把版本写成日期），不误报 |

**双击一行** = **直接干活**（不用先想该点哪个菜单）：

| 这一行的状态 | 双击的效果 |
|---|---|
| **有更新**（GitHub，有直链） | 直接下载 |
| **有更新**（N网 / ayakamods） | 打开对应的下载页 + **开始盯着下载夹**，你点完下载，文件自动被收进工具的下载夹 |
| **已下载** | 打开那个 ZIP |
| 已最新 / 查询失败 / 版本存疑 | 打开它的发布页 |
| 未找到来源 | 打开「指定来源…」对话框 |

**右键菜单**：

| 菜单项 | 干什么 |
|---|---|
| 只检查这一个 | 只重查选中的这一条 |
| **下载 / 打开下载页（= 双击这一行）** | 和双击完全一样，给习惯用右键的人 |
| 只在浏览器打开，不自动收文件 | 只想看看页面、不想让工具盯着下载夹时用 |
| 打开已下载的文件 | 用系统默认程序打开那个 ZIP，或定位到它所在文件夹 |
| 复制链接 | 复制下载直链（没有直链时给要打开的页面地址） |
| 指定来源… | 认不出来时手填一次 |
| 打开下载目录 | 打开工具的下载夹 |

**设置…** 里有 **「去获取 Nexus key」/「去获取 GitHub token」** 按钮，
一键跳到官方页面拿密钥，不用自己找入口。

---

## 「已下载」是怎么判断的

下载成功后会记一笔（`downloads.json`）：**哪个模组、哪个版本、文件在哪**。
列表里这一条就变成「已下载」，不会反复催你。

判断规则（三个条件同时满足才算已下载）：

| 情况 | 显示 | 为什么 |
|---|---|---|
| 记的版本 == 现在线上版本，且文件还在 | **已下载** | 就是刚下的那个 |
| 记的版本 ≠ 现在线上版本 | **有更新** | 线上又发新版了，不能让你以为新版本也下好了 |
| 版本对得上，但文件被移走/删了 | **有更新** + 附一句提示 | 你多半已经导入 Arsenal 了，不该继续挂着「已下载」骗人 |

「下载全部可自动下载的」也会跳过已下载的那些（按钮上会写「下载剩余 N 个」），
所以不会重复下一遍；哪天线上又更新了，它会自动重新出现。

---

## 智能识别是怎么工作的

按可信度从高到低：

1. **mod 自带 manifest 里作者写的来源** —— 最可信，直接采纳
2. **Arsenal 库里的 N 网编号** —— N 网下载的必然准确
3. **安装目录名里解析出的 N 网编号** —— 如 `Name 15298 0.2.2 2026-10-07T...`
4. **GitHub 搜索** —— 按名字搜，但要求能看出跟 HD2 有关
5. **ayakamods 本地索引** —— 爬下全站约 505 个 mod 建索引，模糊匹配

**最关键的消歧手段是版本号核对**：本地版本和线上版本能对上的候选，几乎必然是正确的。
例如 `Auto Reload` 会同时命中 `HD2 Auto Reload` 和 `Auto Reload Assist`，
但只有前者的版本号（0.6.4）跟本地一致，于是正确选中。

### 认不出来时怎么办

右键 → **指定来源…**，填：

| 站点 | 填什么 | 例子 |
|---|---|---|
| GitHub | 作者/仓库 | `SkyeShade/HD2Runtime` |
| Nexus | 数字 ID | `15298` |
| ayakamods | 页面地址或 slug | `https://ayakamods.com/mods/hd2runtime.4260/` |

填一次就记住了。

---

## 下载说明

| 来源 | 怎么下 | 为什么不能全自动 |
|---|---|---|
| **GitHub** | ✅ **全自动**，带 sha256 校验 —— **双击那一行** | —— |
| **Nexus** | 🟡 **双击那一行** → 页面已停在那个文件上 → 点 **SLOW DOWNLOAD** | 见下 |
| **ayakamods** | 🟡 **双击那一行** → 登录后点 **Download** | 见下 |

> 双击之后你不用管了：工具会**一直盯着下载文件夹**，文件下完自动被收进工具的下载夹，
> 那一行同时变成「已下载」。（同一时间只跑一个接力 —— 双击另一行会自动让前一个让位，
> 免得两个接力抢同一个下载夹里的文件。）

### 多个「变体」怎么办（重要）

有些 mod 会**并行发布多个变体**，它们共用一个页面、共用版本号序列，但内容不同。例如
Vanilla Plus Megapack 就有普通版和 **Rows 版**：

```
Vanilla-Plus-Megapack-v40.zip          ← 普通版
Vanilla-Plus-Megapack-Rows-v35.zip     ← Rows 版（你装的是这个）
```

这种 mod 只按"最新版"来挑文件会**挑错**。本工具的做法是：

1. **认出你装的是哪个变体** —— 从安装目录名还原原始文件名
   （`Vanilla Plus Megapack - Rows 16294 35 2026-09-29T19-48Z KHd6InFpy_AR…`
   → `Vanilla Plus Megapack - Rows`）
2. **只在同一个变体里更新** —— 装 Rows 就给你 Rows，不会换成普通版
3. **变体没了就明说** —— 万一作者停更了你用的变体（比如 Rows 实际上停在 v35），
   工具**不会**偷偷把普通版塞给你，而是在那一行写上：

   > ⚠ 变体：「Vanilla Plus Megapack - Rows」停更在 v35（更新的发布里已经没有它了），
   > 这是该变体的最后一版 —— 要不要换成普通版由你决定

   换不换由你决定，工具只负责把情况说清楚。

> 认不出变体时（比如手动导入的、目录名被改过的），工具会**明确说**"认不出你装的是哪个变体，
> 默认选了最大的那个，请确认"，而不是假装知道。

### 为什么 N 网不能一键下载

实测（用自己的免费账号打 API）确认，这不是我们没做，而是**站点设计上就不允许**：

| 做法 | 结果 |
|---|---|
| 用 API key 直接取下载链接 | **HTTP 403**：<br>`You don't have permission to get download links from the API without visting nexusmods.com - this is for premium users only.` |
| 用脚本抓网页 | **HTTP 403**（Cloudflare 直接拦） |
| 手动登录后点按钮 | ✅ 可以，但免费账号要过一次等待 |

Nexus 官方 API 文档里写得很直白：

> Non-premium members must provide the key and expiry from the .nxm link provided by the website.
> ……**This ensures that all non-premium members must access the website** to download through the API.

也就是说：那个 key 是**你点了网页按钮之后，服务器才发给你的**，程序自己造不出来。
VIP（Premium）会员才能跳过这一步。

**所以本工具的做法**：

1. **双击**那一行
2. 工具打开 **`.../mods/{id}?tab=files&file_id={文件id}&nmm=1`** —— 直接落在
   **要更新的那一个文件**上，不用自己在文件列表里翻
3. 你点一下 SLOW DOWNLOAD（免费账号要等一小会儿）
4. 工具一直在盯着下载文件夹，**你点完就不用管了** —— 文件下完会自动被收进工具的下载夹，
   列表里那一条同时变成「已下载」

关于 `&nmm=1`：这是 Nexus 自己给「交给管理器下载」按钮用的参数（Vortex 生成的链接就带它）。
带上它，页面上会出现一键交给管理器处理的按钮 —— 而你的机器上 `nxm://` 协议已经指向
**HD2 Arsenal**（装 Arsenal 时就注册好了），所以这条路能直接接到你现有的管理器上。

### 关于 ayakamods

同样实测：未登录时下载按钮只是一个禁用的 `<span>`（写着 "Log in or sign up to download"），
站点**根本不把下载链接发给匿名访客**——这不是"要不要 cookie"的问题，是服务器端就不给。

所以接力时打开的是**模组页**而不是 `/download`：未登录时 `/download` 会直接甩一个 403 错误页，
容易被误会成工具坏了；模组页则会好好地显示"登录后可下载"。

本工具**不碰你的 cookie**、不保存任何站点密码 —— 你的登录态本来就在浏览器里，
工具只负责打开正确的页面、然后盯着下载文件夹收文件。

下载好的 ZIP 在 **设置… → 下载位置 → ① 工具下载夹**（默认 `%LOCALAPPDATA%\hd2-updater\downloads\`）。
装法是：Arsenal 里 `Add mods / Import` 导入 → 旧的那份禁用后删除。

---

## 为什么 Arsenal 自己做不到

解包看过它的代码，机制是：只对 `hd2a_data.json` 里 `nexusData` 非空的模组调
`api.nexusmods.com/.../files.json?category=update`。手动导入的 ZIP 没有这个字段，
所以永远不会提示更新。

它也没有第三方插件接口（应用里那个 "extension" 是**浏览器扩展**，只服务 N 网下载按钮）。
所以正确做法就是从外部补——也就是这个工具。

---

## 安全与隐私

- **只读** Arsenal 的数据，绝不修改、不删除
- **不碰** 浏览器 cookie、不存任何站点密码
- API key 存在本机 `%LOCALAPPDATA%\hd2-updater\config.json`，只用于发查询请求
- 下载 GitHub 文件时**校验 sha256**（与官方发布的一致才保留）
- 对 ayakamods 做了请求节流（同站点间隔 3 秒），不给站点添麻烦

---

## 命令行模式

```powershell
python run.py --check          # 不开窗口检查一遍
python run.py --check --all    # 连已最新的也列出来
```

退出码：`0` = 没有更新，`1` = 有更新，`2` = 出错。

---

## 文件位置

| 内容 | 位置 |
|---|---|
| 配置（Arsenal 路径 / 令牌 / 版本修正 / 各类路径） | `%LOCALAPPDATA%\hd2-updater\config.json` |
| 已确认的来源 | `%LOCALAPPDATA%\hd2-updater\sources.json` |
| ayakamods 索引 | `%LOCALAPPDATA%\hd2-updater\ayaka_index.json` |
| **下载记录**（哪个模组下了哪一版、文件在哪） | `%LOCALAPPDATA%\hd2-updater\downloads.json` |
| 下载的 ZIP | `%LOCALAPPDATA%\hd2-updater\downloads\` |

上面这些**都可以换地方**（设置… → 数据位置 / 下载位置），想在别的机器上带着走很省事。
确认要重新识别所有来源：**设置… → 清空来源缓存**（不会动手工指定的那些）。

---

## 已知限制

1. **ayakamods 靠爬网页**（站点没开放 API，搜索页还对脚本返回 403），靠列表页建索引。
   站点改版就可能失效，出错信息会直接显示出来。
2. **N 网 / ayakamods 不能一键下载**（站点设计如此，Premium 会员才可以），
   走"浏览器接力"：工具开对页面 + 盯着下载夹，你点一下下载即可。
3. **N 网必须填 key** 才能自动检查，这是 Nexus 的限制。
4. **GitHub 未登录限额 60 次/小时**；可在设置里填 token 提到 5000。
   超出会自动退回 `releases.atom`（只有版本号，没有下载直链）。
5. **日期 vs 版本号**：ayakamods 上有些模组的"版本"栏填的是日期
   （如 `2026-09-24`）。这种情况版本无法比较，会显示"版本存疑"而**不会**误报有更新。
6. **少数 mod 认不出来**（实测 20 个里有 1~2 个），右键手动指定即可。
7. **「已下载」只是引用，不搬运**：你把下载夹里的 ZIP 移走/删掉后，
   那一行会退回「有更新」并附一句提示 —— 这是故意的，避免误以为新版已就绪。

---

## 环境要求

- Windows 10 / 11（64 位）
- **用 EXE 不需要 Python**；从源码跑需要 Python 3.9+（tkinter 随 Python 自带）

自己打包 EXE（用 PyInstaller）：

```powershell
pip install pyinstaller
python build_exe.py      # 产物在 dist\HD2ModUpdater.exe
```

---

## 开发

```
hd2_updater/
  util.py       HTTP（重试/节流）、版本比较、错误翻译、数据目录管理
  arsenal.py    定位并只读 Arsenal 库、猜版本、还原变体名、读 manifest 来源
  providers.py  github_check / nexus_check / ayaka_check / ayaka_crawl
  matcher.py    搜索词生成、相似度、来源识别、版本核对消歧
  engine.py     Engine：解析来源 → 检查 → 汇总；下载
  records.py    下载记录（哪个模组下了哪一版、文件在哪）
  gui.py        中文 tkinter 界面
  cli.py        命令行模式
tools/
  check_names.py          静态检查「用了但没定义」的名字
  check_display_text.py   静态检查用户可见文案里残留的 markdown `**`
run.py          入口
```

改动后自测：

```powershell
python run.py --check                    # 命令行跑一遍
python tools/check_names.py              # 静态检查
python tools/check_display_text.py       # 文案检查
```

欢迎提 issue / PR。改动请尽量保持「只读 Arsenal」这一条底线。

---

## 许可证

[MIT](LICENSE)

**免责声明**：与 Arrowhead Game Studios、Sony、Nexus Mods、ayakamods 均无关联。
本工具只做更新检查与下载，不修改游戏文件——部署仍由 HD2 Arsenal 完成。
请遵守各站点的服务条款；本工具不绕过登录、不抓取需要授权的下载。

*版本 1.0.0*
