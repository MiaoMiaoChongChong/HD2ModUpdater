# HD2 Mod Updater v1.0.2

中文界面的 **Helldivers 2 模组更新管理器** —— 给 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664) 补上它缺少的更新检测。

> 第一次用？看 [v1.0.0 的说明](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/releases/tag/v1.0.0) 了解功能和用法，本页只讲这次改了什么。

---

## 这次的主题：更快

这一版**没有新功能，也没有改任何界面** —— 只是让「检查更新」和「识别来源」明显更快了。你不需要学任何新东西，照常点按钮就行，只是等的时间短了。

### 「检查更新」更快了

- **同时查询的网站数从 4 个提到 8 个**：20 个模组原来要分 5 批查，现在 3 批就查完；
- **卡住的情况不再拖很久**：某个网站偶尔没响应时，原来会傻等 45 秒，现在 15 秒就换下一个（后台仍有重试兜底，不会因为这次超时就漏报更新）；
- **ayakamods 不再被"排队"**：如果好几个模组都在 ayakamods 上，原来它们会被强制一个接一个慢慢查，现在可以并行查（每个请求仍守着站点要求的安全间隔，不会猛打网站）。

### 「识别来源」更快了

- **联网搜索从"一个接一个"改成"一起搜"**：识别每个模组在 GitHub 上对应哪个仓库时，原来是逐个串行搜索，现在把这一批要搜的关键词一次性并发搜完。
- 实测：一批 22 组搜索词，串行要约 13~22 秒，现在**并发 3.7 秒**搜完，之后逐个模组评估时几乎**瞬间完成**。

### 已经识别过的模组不受影响

如果你之前已经识别过来源，那些来源都好好地存着 —— 这次只是让**还没识别的**、以及**以后新增的**模组识别得更快；「检查更新」对所有模组都更快。

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

---

## English summary

**v1.0.2 — a speed-up release. No new features, no UI changes.**

Everything works exactly as before; it just takes less time:

- **"Check for updates" is faster:** concurrent site queries raised from 4 to 8
  (20 mods now finish in ~3 batches instead of 5); a stuck request now times out at
  15s instead of 45s (retries still back it up, so no update is missed); and mods
  hosted on ayakamods.com are no longer forced through one global queue — they are
  checked in parallel (each request still respects the site's rate limit).
- **"Detect sources" is faster:** the per-mod GitHub repository searches used to run
  one after another. They are now batched and fetched concurrently. In testing, a
  batch of 22 search terms took ~3.7s (vs ~13–22s serially), and the per-mod
  evaluation afterwards is effectively instant.

Mods you already detected keep their saved sources; this only makes *new* detections
and every update check faster.

**Upgrade:** replace your existing `HD2ModUpdater.exe`. Your config and data live in
`%LOCALAPPDATA%\hd2-updater\` and are untouched. Unsigned, so SmartScreen may warn —
click "More info" → "Run anyway".

---

## 许可证

[MIT](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/blob/main/LICENSE)
