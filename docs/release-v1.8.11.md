# HD2 Mod Updater v1.8.11

中文界面的 **Helldivers 2 模组更新管理器** —— 给 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664) 补上它缺少的更新检测。

> 第一次用？看 [v1.0.0 的说明](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/releases/tag/v1.0.0) 了解功能和用法。
> 本页写给**已经在用 v1.0.3 及更早版本**的人，以及刚拿到这个工具的新用户。

---

## 先说最要紧的：这一版为什么跳了这么多号

**公开 release 上一个版本是 v1.0.3，这次直接到 v1.8.11。** 中间 v1.1 ~ v1.8 的改动
其实早就做好了，只是作者一直用在自己的机器上、没往公开仓库推（这是本工具一贯的
"先本地自用验证，确认没问题再发"）。所以这一版**不是小修小补，而是攒了十几个版本的
新功能一次交出来**。

如果你从 v1.0.3 直接跳过来，**下面这些全是新的**：

| 新增 | 一句话 |
|---|---|
| **「部署模组」页** | 自己挑要部署哪些模组、调加载顺序、改每个模组的选项 —— Arsenal 只能整包部署，这个能只放一部分 |
| **「下载 / 导入」页** | 下载和安装接起来：GitHub 直链下完直接导入，不用自己去翻文件夹 |
| **「备份」页** | 部署前自动备份游戏文件，并管着别让备份无限增长 |
| **「反馈 / 帮助」页** | 出问题一键生成诊断信息（含日志），不用你手打一堆环境信息 |
| **拖入压缩包就能加模组** | 把 mod 的 `.zip` 拖到窗口上，自动解包并加进列表 |
| **右键删除模组** | 更新检查页和部署页都能删，删前写清影响 |
| **「来源总览」** | 一个 mod 在三个站点各查到什么、各自什么版本，想用哪个点哪个 |
| **只查"正在用的"** | 只检查已经部署进游戏的那些，跳过存着没用的 |

**升级方式**：直接下载下面的 `HD2ModUpdater.exe` **替换掉你原来那个**即可。
配置和数据都存在 `%LOCALAPPDATA%\hd2-updater\`，不受影响，**不用重新填令牌**。

---

## 这次（v1.8.11）修的是什么

> 你报的：「我用 Diver's Best Friend 这个 mod 尝试，来源总览里重新查找只是显示
> 已经重新查找，**结果是什么也没显示，明明 GitHub 有这个模组也还是没找到**」

**是我的 bug，而且上一个版本（v1.8.10）只修了一半。** 真正的原因有两条，
都在**数据**这一层，不在界面显示：

1. **重查结果根本没写进磁盘。** 来源表的保存规则是"盘上已有的模组一律跳过"
   （本意是保护你手工填的来源），而你重查的那条 mod **本来就在盘上**（它已经有
   a 站来源）—— 于是新认出来的 GitHub 被这条规则挡掉了。日志里明明写着
   "查到 1 个候选"，磁盘上却还是原来那一条。
   → 现在：**显式重查过的条目照写不误**（同一站点用新结果替换，其它站点原样保留，
   你手工指定的记录仍然受保护）。
2. **同一个来源被拆成了两行。** 盘上的记录没有版本字段，而查询结果有 ——
   旧代码按「站点 + 版本」去重，于是同一条 GitHub 显示成
   「找到（还没查版本）」**和**「找到 r42」两行，看着就像没找到。
   → 现在按「站点 + 地址」归组：同一条只出现一次，并且**带上查到的版本**；
   GitHub 真有正式版 + 测试版两个时，它们仍然各自成行、各有一个下载按钮。

顺手还改了两处：

- **重查时顺手把版本也查了** —— 以前点完只显示"找到（还没查版本）"，
  你还是不知道线上是什么版本。
- **完成提示如实报结果**，不再只说"已重新查找"：采用了就说新条数；
  **查到了但可信度不够、没敢替你采用时会明说**（并指路「手工指定来源…」）；
  一个候选都没有才说"这次没找到"。

⚠ 一条红线没放宽：**可信度不够的匹配（medium/low）依然绝不写进来源表** ——
否则会固化成错误的"已确认来源"，下次拿它去比版本会把你引到别的 mod 页面上。

### 界面预览

![更新检查页](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/raw/main/docs/screenshot-main.png)

![部署模组页](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/raw/main/docs/screenshot-deploy.png)

（截图里是**虚构的示例模组**，不是作者自己的模组库。）

---

## 怎么升级

1. 下载下面的 **`HD2ModUpdater.exe`**，**替换掉你原来那个**；
2. 双击运行即可。

> **首次运行仍可能被 Windows SmartScreen 拦一下**（提示「Windows 已保护你的电脑」）。
> 原因还是没做代码签名（个人项目，签名证书要花钱）。点 **「更多信息」→「仍要运行」** 即可。
> 源码全部公开，不放心可以自己看或自己打包。

---

## 环境要求

- Windows 10 / 11（64 位）
- 用 EXE **不需要** Python
- **需要先安装 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664)**（Nexus mod 4664）：
  本工具不自己维护模组清单，「你装了哪些 mod、哪些启用、加载顺序」都读自 Arsenal

---

## English summary

**v1.8.11 — a big catch-up release, plus a fix for "re-check found nothing".**

The previous public release was v1.0.3; everything from v1.1 through v1.8 was developed
and used locally but never pushed. So this release delivers a dozen versions of new
features at once:

- **Deploy page** — choose which mods to deploy, reorder load priority, toggle each
  mod's options. Arsenal can only deploy everything as one bundle; this lets you pick.
- **Download / Import page** — download and install in one flow.
- **Backups page** — automatic backups of game files before deploying, with rotation.
- **Feedback / Help page** — one-click diagnostic report (logs included).
- **Drag & drop a mod .zip** to add it to your library.
- **Delete a mod** from either page, with a clear warning of what it does.
- **"Source overview"** — see what each of the three sites found, and pick which to use.
- **Check only deployed mods** — skip the ones sitting unused on disk.

**The fix in this version:** re-searching sources in the "Source overview" dialog
appeared to do nothing. Two root causes, both in the *data* layer: (1) the re-search
result was never written to disk, because the source table's "skip existing entries"
rule (meant to protect hand-entered sources) also blocked the mod you had just
re-searched; (2) the same source was split into two rows because saved entries carry
no version while fresh results do. Both are fixed — re-searched entries are now saved,
grouped by site + URL, and the completion message honestly reports what was found.

**Upgrade:** replace your existing `HD2ModUpdater.exe`. Config and data live in
`%LOCALAPPDATA%\hd2-updater\` and are untouched. Unsigned, so SmartScreen may warn —
click "More info" → "Run anyway".

**Requires [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664)** to be installed.

---

## 许可证

[MIT](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/blob/main/LICENSE)
