# HD2 Mod Updater v1.0.3

中文界面的 **Helldivers 2 模组更新管理器** —— 给 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664) 补上它缺少的更新检测。

> 第一次用？看 [v1.0.0 的说明](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/releases/tag/v1.0.0) 了解功能和用法，本页只讲这次改了什么。

---

## 这次的主题：一眼看清"哪些在用"，只查"正在用的"

模组装多了以后，常有这样的情况：电脑里存了一大堆，但真正部署进游戏、天天在用的只有一部分。这一版加了两样东西，让你把精力花在"正在用"的那几个上。

### 新增「部署」列：一眼看出哪些模组正用在游戏里

主界面表格多了一列 **「部署」**：

| 显示 | 意思 |
|---|---|
| **✓已部署**（绿） | 这个模组当前部署在你的游戏里，正在生效 |
| **—未部署** | 存在电脑里，但没部署进游戏（没启用，或不在 Arsenal 的配置档里） |

这是**只读**地从 Arsenal 的当前配置档里读出来的，工具不会改动你的部署 —— 部署仍然完全由 Arsenal 自己负责。

### 新增「检查已部署更新」按钮：只查正在用的那几个

操作栏多了一个 **「检查已部署更新」** 按钮（右键菜单里也有「检查所有已部署的」）。

点它 = **只查「✓已部署」的模组有没有新版本**，存在电脑里但没部署的全部跳过。

- 什么时候点：平时快速过一遍"我在用的模组有没有要更新的"，比「检查更新」（查全部）更快、更聚焦；
- 想把**所有**模组（包括没部署的）都查一遍，照旧点 **「检查更新」**。

---

## 怎么升级

1. 下载下面的 **`HD2ModUpdater.exe`**，**替换掉你原来那个**（配置和数据都存在 `%LOCALAPPDATA%\hd2-updater\`，不受影响）；
2. 双击运行即可。

> **首次运行仍可能被 Windows SmartScreen 拦一下**（提示「Windows 已保护你的电脑」）。
> 原因还是没做代码签名（个人项目，签名证书要花钱）。点 **「更多信息」→「仍要运行」** 即可。
> 源码全部公开，不放心可以自己看或自己打包。

---

## 环境要求

- Windows 10 / 11（64 位）
- 用 EXE **不需要** Python
- 部署状态读取的是 HD2 Arsenal 的当前配置档，请确保你用的是 Arsenal 管理模组

---

## English summary

**v1.0.3 — see what's actually deployed, and check only those.**

When you have many mods sitting on disk, only some of them are actually deployed
into your game. This release adds two things to help you focus on those:

- **A new "Deployed" column** in the main table: **✓Deployed** (green) means the mod
  is currently deployed into your game and active; **—Not deployed** means it's only
  stored on disk (disabled, or not in the Arsenal profile). This is **read-only**
  from HD2 Arsenal's current profile — the tool never touches your deployment;
  deploying stays entirely up to Arsenal.
- **A new "Check deployed updates" button** (also in the right-click menu as
  "Check all deployed"): it checks **only the ✓Deployed mods** for new versions and
  skips everything that isn't deployed — faster and more focused than a full
  "Check for updates". Use the regular button when you want to check *all* mods.

**Upgrade:** replace your existing `HD2ModUpdater.exe`. Your config and data live in
`%LOCALAPPDATA%\hd2-updater\` and are untouched. Unsigned, so SmartScreen may warn —
click "More info" → "Run anyway".

---

## 许可证

[MIT](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/blob/main/LICENSE)
