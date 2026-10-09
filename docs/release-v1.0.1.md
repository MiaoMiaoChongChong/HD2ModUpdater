# HD2 Mod Updater v1.0.1

中文界面的 **Helldivers 2 模组更新管理器** —— 给 [HD2 Arsenal](https://www.nexusmods.com/helldivers2/mods/4664) 补上它缺少的更新检测。

> 第一次用？看 [v1.0.0 的说明](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/releases/tag/v1.0.0) 了解功能和用法，本页只讲这次改了什么。

---

## 这次修了什么

### 1. ayakamods 上「中文名 / 带特殊符号」的模组现在能认出来了

之前这类模组**整个识别不出来**：它们会一直显示「未找到来源」，你手动把网址贴进去也会被告知「认不出这是哪个站点的链接」—— 而那个网址明明是好的。

原因是站点上**约 12% 的模组是中文名**（尤其中文社区的汉化、功能模组），它们的页面地址里带中文，实际传输时是 `%E6%9C%BA%E7%94%B2...` 这样的编码。旧版本只认英文字母和数字，把这一整类模组漏掉了（本地索引里一条中文模组都没有）。

现在：

- **自动识别**能把中文名模组认出来了；
- 手动指定来源时，**中文地址直接原样粘贴即可**，不需要你自己转成 `%E6%9C%BA...` 那种乱码；
- 界面上显示的都是**中文名字**，不会再给你看 `%E6%9C%BA%E7%94%B2` 这类编码串。

### 2. 手动指定来源后，可以「撤销并重新识别」了

右键菜单新增 **「撤销来源并重新识别」**。

之前有个死角：手动指定的来源是**受保护**的（自动识别不会覆盖它，「清空来源缓存」也只清自动的），所以一旦填错、或站点那边改了名，你就**退不回自动识别**了，只能一直用着那条错的记录。

现在右键它就能撤销并立刻重新识别这一条。**并且不会让你丢数据**：如果重新识别什么也没找到（比如你关掉了「智能搜索来源」、或当前离线、索引还没建好），**原来的来源会被自动放回去**，并明确告诉你「没找到新来源，已还原」。想强行换一个，用「指定来源…」直接填新地址就行。

### 3. 另外修掉的问题（你可能会碰到）

| 问题 | 现象 | 现在 |
|---|---|---|
| 索引只爬前 30 页 | 排在后面的模组一直识别不出来 | 上限提到 80 页（站点当前 44 页），并保证按名字排在后面的模组也能进索引 |
| 索引抓一半就存盘 | 网络抖一下，之后 7 天都在用一个残缺索引 | 抓取明显不完整时**保留原索引**，不覆盖 |
| 老索引一直不失效 | 有时改了匹配逻辑，但它对你这里「没生效」 | 索引带格式版本，格式不对就自动重建 |
| 相似度显示虚高 | 明明不像，却显示「相似度 99%」 | 报的是**真实名字相似度**；版本号只有**完全一致**才算「已核对」 |
| 界面偶发假死 | 极少数情况下窗口还在但点什么都没反应 | 消息处理加了异常保护，单条出错不影响整体 |

---

## 怎么升级

1. 下载下面的 **`HD2ModUpdater.exe`**，**替换掉你原来那个**（配置和数据都存在 `%LOCALAPPDATA%\hd2-updater\`，不受影响）；
2. 双击运行即可。

> **首次运行仍可能被 Windows SmartScreen 拦一下**（提示「Windows 已保护你的电脑」）。
> 原因还是没做代码签名（个人项目，签名证书要花钱）。点 **「更多信息」→「仍要运行」** 即可。
> 源码全部公开，不放心可以自己看或自己打包。

**建议第一次打开后点一下「识别来源」**：索引格式这次变了，会自动重建一次（约 2 分钟，只此一次），之后中文名模组就能认出来了。

---

## 环境要求

- Windows 10 / 11（64 位）
- 用 EXE **不需要** Python

---

## English summary

**v1.0.1 — two fixes.**

1. **Chinese-named / special-character mods on ayakamods.com are now detected.** About 12% of
   mods on that site have Chinese names, and their page URLs are percent-encoded. The previous
   release only matched ASCII slugs, so those mods were silently skipped — both in automatic
   detection and when you pasted their URL in manually. Now they work, and you can paste the
   Chinese URL as-is (no need to encode it yourself).

2. **You can now undo a manually-specified source** via the right-click menu
   ("撤销来源并重新识别" / undo source and re-detect). Previously a manual source was
   protected and could never be rolled back. If re-detection finds nothing (search disabled,
   offline, index not ready), **your original source is restored** instead of being lost.

Also fixed: the site index only crawled the first 30 pages (now 80; the site has 44);
a partially-failed crawl no longer overwrites a good index; the index now carries a format
version so matching-logic fixes actually take effect; similarity percentages are no longer
inflated by version bonuses; and a rare case where the window could stop responding.

**Upgrade:** replace your existing `HD2ModUpdater.exe`. Your config and data live in
`%LOCALAPPDATA%\hd2-updater\` and are untouched. Unsigned, so SmartScreen may warn — click
"More info" → "Run anyway".

---

## 许可证

[MIT](https://github.com/MiaoMiaoChongChong/HD2ModUpdater/blob/main/LICENSE)
