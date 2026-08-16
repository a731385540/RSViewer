# RSViewer 项目维护指南

本文档面向未来接手本仓库的 AI 助手和开发者。开始任何工作前，请先完整阅读本文、`README.md` 和 `CHANGELOG.md`，然后执行 `git status --short`。本文描述的是 2026-08-15 的工作区现状；若代码与本文冲突，以代码为准，并在本次修改中同步修正文档。

## 1. 项目背景与边界

RSViewer 是一个仅供个人、非商业使用的 Windows 桌面媒体管理与查看工具。目标是统一浏览本地磁盘和 NAS 中的漫画、图片集与视频，并提供媒体索引、封面、搜索、阅读/播放和进度保存能力。

当前项目来自 PyQt-Fluent-Widgets Gallery 示例骨架，但示例、演示资源和音乐播放器残留已经被有意删除。不要恢复 `examples/`、旧 Gallery 资源、旧音乐配置或约 26 万行的生成文件 `app/common/resource.py`，除非用户明确要求。

第三方 UI 依赖是 PySide6-Fluent-Widgets。项目虽为个人非商业用途，仍须遵守第三方组件许可证，不要删除 `README.md` 中的第三方说明。

### 当前成熟度

项目处于早期 MVP 阶段。EhViewer 本地漫画库已经可配置、可分页浏览、搜索、筛选、打开详情并进入单页漫画阅读器；阅读器已有四向翻页、单张长图滚动、自动翻页、进度保存/恢复和即时同步设置。在线资源已按用户的 `eh_tool_refactored.py` 接入 EH/EX HTML 画廊列表搜索、翻页和封面加载，并具备分站点内存页缓存、并发封面加载、过期磁盘缓存、应用内详情、只读评论、缩略预览、按需在线阅读，以及兼容 EhViewer 目录和数据库的站点展示图/`fullimg` 原图断点下载。现有本地画廊可先把原图暂存到 `original/`，再以可恢复步骤替换根目录图片并保留 `history/del/` 基础图备份；原图画廊后续版本更新仍使用原图。通用媒体扫描、自有完整媒体索引、双页/多图连续阅读和视频播放器仍未实现。

## 2. 技术栈与运行环境

- Python：建议 3.10+；2026-08-03 本机验证环境为 Python 3.9.2。
- GUI：PySide6，依赖范围见 `requirements.txt`。
- Fluent UI：PySide6-Fluent-Widgets。
- 主要平台：Windows 10/11；Mica 效果仅在符合条件的 Windows 11 环境启用。
- 当前持久化：QFluentWidgets 的 JSON 配置、目标 EhViewer 既有业务表中的分类与在线下载元数据，以及独立 SQLite 中的 RSViewer 播放列表、树状归类、收藏、本地浏览历史、漫画阅读进度、搜索历史、在线下载状态和评论。
- 规划持久化：SQLite 媒体索引与文件系统缩略图缓存。
- 规划媒体能力：Qt Multimedia；在确认格式覆盖不足前不要过早引入 VLC/mpv 等额外运行时。

安装和启动：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

配置文件通过项目根目录解析为稳定绝对路径，因此从其他当前工作目录启动也能读取同一份开发配置。打包前仍应迁移到 `QStandardPaths.AppConfigLocation`。

## 3. 当前目录结构

```text
RSViewer/
├─ AGENTS.md                         # 本维护指南
├─ CHANGELOG.md                      # 面向版本/重大修改的变更记录
├─ README.md                         # 用户向项目简介与启动说明
├─ eh_tool_refactored.py             # 用户提供的 EH/EX HTML 列表页抓取与解析实现
├─ requirements.txt                 # 直接运行依赖
├─ scripts/import_eh_tags.py         # EH 标签翻译 Markdown 到自有 SQLite 的幂等导入脚本
├─ main.py                           # 唯一应用入口
└─ app/
   ├─ common/
   │  ├─ config.py                   # 配置模型、稳定配置路径、JSON 加载
   │  └─ style_sheet.py              # 自定义 QSS 路径与主题注册
   ├─ domain/manga.py                # 本地漫画领域模型
   ├─ domain/online_gallery.py       # 在线画廊与翻页结果模型
   ├─ domain/online_download.py      # 在线下载任务状态模型
   ├─ domain/gallery_update.py       # 本地画廊版本更新任务模型
   ├─ repositories/user_library_repository.py # RSViewer 用户标签与阅读进度库
   ├─ repositories/ehviewer_download_repository.py # 外部 eh.db 与下载目录兼容写入
   ├─ repositories/gallery_update_state_repository.py # 目录 new.json 原子 checkpoint
   ├─ services/eh_tag_importer.py    # EH 标签 Markdown 快照解析与校验
   ├─ services/eh_tag_search.py      # 启动加载的内存标签检索表与本地查询解析
   ├─ services/search_history.py     # 本地/在线共享的持久化搜索历史服务
   ├─ services/library_organizer.py  # 未登记本地目录扫描、双库恢复与回收站边界
   ├─ services/online_download_builder.py # 从本地 sidecar 重建源站补齐请求
   ├─ services/manga_title_similarity.py # 章节/卷号与元数据噪声剥离、标题相似度匹配
   ├─ services/online_thumbnail_cache.py # 在线封面分站点磁盘缓存与惰性过期
   ├─ services/online_gallery_memory_cache.py # 最近 20 个在线画廊 LRU 内存缓存
   ├─ sources/ehviewer_source.py     # EhViewer 只读查询、分类写入与惰性页面加载
   ├─ sources/eh_online_source.py    # EH/EX provider 接口、运行配置与用户爬虫适配器
   ├─ workers/eh_online_worker.py    # 在线搜索、封面、详情、预览和阅读 Worker
   ├─ workers/online_gallery_download_worker.py # 在线画廊断点下载 Worker
   ├─ workers/original_gallery_worker.py # 原图替换与压缩图备份清理 Worker
   ├─ workers/library_organizer_worker.py # 整理页扫描、同步和回收站操作 Worker
   ├─ workers/gallery_update_worker.py # 本地画廊新版本可恢复更新 Worker
   ├─ workers/reading_progress_worker.py # 后台保存阅读进度
   ├─ workers/similar_manga_worker.py # 大型本地库标题相似搜索 Worker
   ├─ resource/qss/
   │  ├─ dark/                        # 设置页与阅读设置弹窗深色样式
   │  └─ light/                       # 设置页与阅读设置弹窗浅色样式
   └─ view/
      ├─ main_window.py              # Fluent 主窗口、导航、主题监听
      ├─ local_manga_interface.py    # 本地漫画分页、搜索、标签与封面卡片
      ├─ eh_tag_search_line_edit.py  # 支持多条件及光标替换的 EH 标签补全搜索框
      ├─ manga_history_interface.py  # 本地浏览历史与在线历史预留路由
      ├─ download_manager_interface.py # 未完成下载任务的集中管理页面
      ├─ update_manager_interface.py # 未完成画廊版本更新管理页面
      ├─ library_organizer_interface.py # 未登记本地资源整理页面
      ├─ manga_detail_interface.py   # 本地/在线共享详情、页面预览与评论区
      ├─ manga_reader_interface.py   # 单页阅读、缩放、预读和全屏控制
      ├─ online_manga_interface.py   # 在线画廊搜索、翻页、封面和主题化结果页
      ├─ reader_setting_dialog.py    # 阅读页内即时同步设置面板
      ├─ media_interface.py          # 未实现媒体路由的轻量占位页面
      └─ setting_interface.py        # 设置页、数据源路径和配置绑定
```

`app/config/config.json` 是运行时生成的用户配置，已被 `.gitignore` 忽略，不应提交。`.idea/`、`__pycache__/`、构建目录同样不应提交。

本机还可能存在被忽略的 `testData/`、`Database/` 和 `lib/`：前者是用户提供的外部数据库与漫画样例，`Database/` 是单独克隆的 EH 标签翻译仓库，后者是本地 Python 环境。它们都不是应用源码，不得提交或删除。

## 4. 核心模块与职责

### `main.py`

应用组合根。读取 DPI 和语言配置，创建 `QApplication`，安装 Fluent 翻译器，创建并运行 `MainWindow`。不要在模块导入时创建窗口或进入事件循环；继续保留 `main()` 和 `if __name__ == "__main__"` 保护。

### `app/common/config.py`

定义全局 `cfg`。当前配置项：

- `ehViewerDatabase`：目标 EhViewer SQLite 文件；常规浏览只读，显式分类操作需要写权限。
- `ehViewerMangaRoot`：与外部库对应的漫画下载根目录，支持本地、映射盘或 UNC 路径。
- `libraryFolders`：其他图片/视频使用的本地、映射盘或 NAS/UNC 媒体目录。
- `mangaPageSize` / `mangaSortOrder`：本地资源每页数量及按 EhViewer 添加时间升序/降序；默认降序（最新优先）。
- `mangaPrimaryLabelFilter`：上次选择的分类；默认 `__none__`，即未分类。
- `mangaSearchHoverEnabled`：本地资源搜索按钮悬停自动展开开关；搜索词为空且鼠标离开搜索区域后自动收起。
- `searchHistoryLimit`：本地与在线资源共享搜索历史的保存上限，只允许 5/10/15/20，绝不超过 20；降低后即时裁剪自有数据库。
- `searchShortcut` / `tagSidebarShortcut` / `backShortcut`：展开本地资源搜索栏、切换标签栏和返回上一级的全局快捷键，均使用按键捕获设置。
- `onlineEhSite`：在线资源默认站点，支持 `ehentai` 与 `exhentai`。
- `onlineEhCookie`：用户自行提供的完整 EH Cookie；裸 token 按 `igneous` 兼容。该值仅存于被忽略的本机配置 JSON，不得输出到日志或提交。
- `onlineEhProxyMode` / `onlineEhManualProxy`：在线 provider 使用系统代理、直连或手动 HTTP(S) 代理；手动地址仅在 `manual` 模式消费。
- `onlineEhRequestTimeout`：传给在线 provider 的单次请求超时，支持 10/20/30/60 秒。
- `onlineEhViewMode`：在线结果的默认视图，支持 `card` 与 `extended`；页面切换时即时保存，并分别通过原列表页 `inline_set=dm_l` / `inline_set=dm_e` 同步远端账户的 Compact / Extended 默认模式。
- `onlineEhThumbnailConcurrency`：在线封面专用线程池的最大并发请求数，支持 1/2/4/6/8/12，默认 6。
- `onlineEhDownloadConcurrency`：同时运行的画廊下载任务数，支持 1–3，默认 2；修改后即时更新下载线程池，运行时也必须硬限制为最多 3。
- `onlineEhDownloadLabel`：新建在线下载条目的默认 EhViewer 分类，空字符串表示未分类；设置页选项必须来自当前 `DOWNLOAD_LABELS`，已有 GID 继续下载时不得覆盖原 `LABEL`。
- `onlineEhThumbnailCacheHours`：在线封面本地缓存有效期，支持 1 小时至 30 天，默认 7 天。
- `readerBackgroundColor`：阅读画布背景色。
- `readerPageDirection`：从左向右、从右向左、从上向下或从下向上的下一页按键方向。
- `readerImageLoadSize`：适应窗口、适应宽度或原始大小的初始显示模式。
- `readerScrollShortcut`：单张长图向前滚动一屏的快捷键，到底后进入下一页。
- `readerAutoPageEnabled` / `readerAutoPageInterval`：自动翻页开关与秒数间隔。
- `micaEnabled`：窗口 Mica 效果。
- `dpiScale`：Qt 缩放比例，需要重启。
- `language`：语言选择，需要重启；目前业务界面尚未真正国际化。
- `themeMode` 和 `themeColor`：继承自 QFluentWidgets 的 `QConfig`。

配置通过基于 `config.py` 所在项目根目录解析的绝对路径加载 `app/config/config.json`。业务数据库不能塞入此 JSON；媒体条目、进度、收藏和扫描状态以后应进入 SQLite。

### `app/repositories/user_library_repository.py`

RSViewer 独立 SQLite 使用 `PRAGMA user_version` 执行可重复迁移。版本 1 的复数标签表在界面上已演进为播放列表，版本 2 新增阅读进度，版本 3 保留历史分类覆盖兼容，版本 4 增加播放顺序与树状归类，版本 5 新增收藏与浏览历史，版本 6 新增 `eh_tag_namespaces` 与 `eh_tags` 保存 EH 标签翻译快照，版本 7 新增 `search_history(query, searched_at)` 保存本地/在线共享的最近搜索，版本 8 新增 `online_gallery_downloads` 与 `online_gallery_comments`，以唯一 GID 保存在线任务状态、额外元数据、评论快照和断点进度，版本 9 新增 `gallery_sync_records` 与 `gallery_sync_comments`，将普通本地画廊的源站元数据/评论同步与下载任务状态解耦。当前资源页的分类、用户触发的在线下载及本地详情“同步信息”会事务更新目标 EhViewer 既有业务表，但绝不修改外部库 schema。删除播放列表依靠外键清理成员；删除归类节点会级联删除子树和关联。`page_index` 始终是零基索引；播放列表、归类、收藏、历史、进度、EH 标签快照、搜索历史，以及 EhViewer 无列可容纳的在线下载/同步数据只写 RSViewer 自有数据库。

版本 10 新增 `gallery_update_tasks`，以源 GID 唯一保存画廊版本更新的目标、文件夹、checkpoint、页进度、状态和错误。它只用于快速列出管理任务，目录 `new.json` 和实际文件仍是崩溃恢复依据。更新完成的 GID 迁移必须在同一自有数据库事务内处理分类、播放列表、归类、收藏、历史和阅读进度，不得留下指向旧 GID 的孤立关联。

版本 11 为 `online_gallery_downloads` 新增 `download_mode`，区分 `standard`、`original_direct` 与 `original_local`；新增 `gallery_original_states` 持久化原图画廊属性、下载断点和 `staged`、`replacing_base`、`replacing_original`、`active`、`cleaning` 等文件操作阶段。原图属性不得只从目录猜测；应用启动须把中断的原图下载恢复为 paused，替换与清理则依据持久阶段和实际文件继续。

### `app/common/style_sheet.py`

把 RSViewer 自定义样式注册到 QFluentWidgets 的样式管理器。设置页、阅读设置弹窗、漫画详情标签和在线资源页分别注册 `StyleSheet.SETTING_INTERFACE`、`StyleSheet.READER_SETTING_DIALOG`、`StyleSheet.MANGA_DETAIL_INTERFACE`、`StyleSheet.ONLINE_MANGA_INTERFACE`，`setTheme()` 会自动重新加载对应的 light/dark QSS。阅读设置弹窗是独立窗口，不能依赖主窗口背景透传，必须分别定义浅色与深色实体背景；在线资源滚动区、viewport、内容容器、结果卡片和封面占位也必须保持主题透明背景与对应明暗配色。

新增页面样式时，应：

1. 在 `StyleSheet` 枚举中增加名称。
2. 同时创建 `app/resource/qss/light/` 和 `dark/` 两份同名 QSS。
3. 在页面初始化、对象名设置完成后调用 `.apply(self)`。
4. 实际验证两种主题；避免用内联 QSS 固定文字颜色或背景色。

### `app/view/main_window.py`

主 stacked widget 的整页位移动画保持关闭。媒体页已经常驻，切换时只更换当前 widget；不要恢复 Fluent 默认 300ms 动画，否则最大化或全屏窗口会连续重绘透明页面和媒体卡片并明显掉帧。

`SplashScreen` 在主窗口首次 `show()` 前必须显式 `resize(self.size())`，因为它是在主窗口初始 `resize()` 之后创建，不能依赖尚未发生的父窗口尺寸事件。退出时先隐藏窗口并取消下载；下载 provider 的流式响应必须支持由 Worker 主动关闭，未启动任务使用 `QThreadPool.clear()` 清理，不能只设置布尔标记后长时间等待网络超时。

主窗口基于 `FluentWindow`，负责窗口、导航、主题、数据源组合，以及本地资源/收藏/历史之间的共享数据同步。左侧导航不使用树状父子路由：底部“漫画”“视频”两个模式按钮位于“设置”上方，按当前模式切换顶部扁平入口；漫画模式显示本地资源、收藏、在线资源、历史记录、正在下载、更新管理和整理，视频模式当前只显示资源，切换模式分别进入本地资源或视频资源默认页。页面和路由对象保持常驻，只切换导航项可见性，不应因模式切换重新创建页面。可配置的搜索栏与标签栏快捷键使用应用级 `QShortcut`，会先切回漫画模式的本地资源再展开搜索或切换标签侧栏，并随配置即时更新。在线资源路由使用 `OnlineMangaInterface`，不得在主窗口或 GUI 线程直接执行网络请求。收藏与本地历史不得各自重新执行大型库加载，而应消费 `LocalMangaInterface.libraryLoaded` 的同一批元数据。打开详情和阅读时由主窗口即时更新历史顺序，并在单线程后台队列保存。

主窗口创建自有 Repository 后会立即加载已导入的 EH 标签，构造一个全局共享的 `EhTagSearchIndex`，并创建单一 `SearchHistoryService` 供本地、收藏、本地历史和在线页面共享；不得让各页面重复读取四万多条标签或维护互相独立的搜索历史。标签仓库更新由 `scripts/import_eh_tags.py` 显式执行，主程序启动只加载 SQLite 快照，不扫描 Markdown。

`SystemThemeListener` 是持有资源的后台监听器，关闭窗口时必须 `terminate()` 和 `deleteLater()`。

### `app/view/media_interface.py`

仅为在线历史和视频等尚未实现路由提供轻量占位页。在线资源已由独立界面实现，收藏与本地历史使用 `LocalMangaInterface` 的集合模式。各页面通过稳定且唯一的 `objectName` 作为 Fluent 导航 route key。

### `app/sources/eh_online_source.py` 与 `app/view/online_manga_interface.py`

在线详情必须沿用当前列表页 provider 的同一 `requests.Session`，直接 GET 对应 `/g/{gid}/{token}/` HTML，并在后台解析完整元数据、标签、20 张缩略预览及 `.c1` 评论。详情页 `#gnd` 中同站 `/g/{gid}/{token}/` 链接表示当前画廊存在更新版本，是判断“旧父画廊”的唯一依据；`Parent` 字段只表示当前画廊的上游版本，不得单独据此反向判旧。后续预览分页直接请求画廊 `?p=N` HTML；EH/EX 的多个预览可能共享同一张横向精灵图，必须保留每个节点的 CSS `width`、`height` 和 `background-position`，下载共享图片后按各自区域裁剪，不能把整张精灵图直接交给预览控件。每个预览还必须保留 `/s/{page-token}/{gid}-{page}` 中的 page token，下载任务据此生成 EhViewer `VERSION2` sidecar。在线阅读和基础下载先请求该 `/s` 单图 HTML，再读取其中 `#img` 的站点展示图；原图下载从同一单图 HTML 提取 `fullimg` 链接。原图链接只允许当前 EH/EX 站点，并必须严格匹配当前 GID 与一基页码；不存在链接时应明确提示账户权限或原图额度问题。所有链路均不调用 API。请求前严格校验当前站点、GID、token、page token、单图页页码及 EH/EX/ehgt/H@H 图片主机；当前评论区只读，不实现发表评论或投票。

`EhOnlineProvider` 是在线爬虫的稳定边界。UI 将 `OnlineGalleryQuery(keyword, cursor, page_number, filters)` 交给 provider；基类先调用 `fetch_page()`，再调用可覆盖的 `filter_items()`，最后返回统一的 `OnlineGalleryPage`。`create_eh_online_provider()` 当前返回 `RefactoredEhOnlineProvider`，它只适配用户提供的根目录 `eh_tool_refactored.py`：沿用 `requests.Session`、EH/EX HTML 列表页、`f_search`、页面生成的 next/prev URL 以及 BeautifulSoup+lxml 的多显示模式解析，不得擅自替换成其他 API 或站点接口。脚本输出的 gid/token、URL、标题、分类、封面、上传时间、页数、上传者、评分、源显示模式和分命名空间标签转换为领域模型；评分必须从文本或 EH 半星精灵图的 `background-position` 转换为 `0–5` 数值，绝不能把 CSS 样式传给 UI。缩略图仍通过同一会话下载。

`EhOnlineSettings` 统一提供站点基址、规范化 Cookie、代理模式/映射和请求超时。Cookie 可粘贴完整 `ipb_member_id=...; ipb_pass_hash=...; igneous=...` 字符串，单独裸 token 按 `igneous` 兼容，并从 settings 的 `repr` 中排除。系统代理由标准库发现；Windows 把单一无 scheme 代理端点展开为同地址的 `http://` 与 `https://` 时，必须规范化为同一个 HTTP CONNECT 代理供 `requests` 使用。直连关闭 session 环境代理，手动模式验证并补全 HTTP(S) URL。源码中不得硬编码 Cookie 或本机代理；`eh_tool_refactored.py` 的全局默认凭据和代理必须保持为空，运行值仅由设置注入。列表翻页 URL 只允许当前 EH/EX 主机，缩略图只允许 EH/EX 与 `ehgt.org` HTTPS 主机。

`OnlineMangaInterface` 为 `ehentai` 与 `exhentai` 分别维护独立的 `OnlineSiteState` 内存容器，保存搜索词、当前页、翻页游标历史、滚动位置及最近 64 个页面结果。切换站点先恢复其容器，容器为空才请求该站首页；工具栏“刷新”始终绕过页面内存缓存重取当前页。结果支持 `card` 与 `extended` 两种视图，顶部使用一个图标按钮在两种布局间切换，按钮图标表示点击后的目标布局。切换先重建当前内存页卡片并更新 `onlineEhViewMode`，再由 `OnlineSearchWorker` 在后台调用 provider 的 `set_display_mode()`，沿用 `eh_tool_refactored.py` 会话请求当前页面生成的 `inline_set=dm_l/dm_e`，随后重取当前搜索/游标页以获得对应完整字段；当前页已经成功加载的封面必须跨两次卡片重建复用，不得重新读取磁盘或下载，封面控件还必须保留源图并在布局几何变化时重新按比例缩放，不能沿用构造阶段的临时 `QLabel` 尺寸。Card/Extended 卡片右键菜单提供“下载”，无需进入详情；卡片只允许鼠标左键触发详情，右键释放必须被拦截并交给上下文菜单，不能沿 `CardWidget.clicked` 进入详情。主窗口必须先用列表 GID/token 和设置中的目标分类登记 queued 任务，再后台获取完整详情并进入统一下载 Worker，确保详情请求期间退出也可恢复。主窗口将 `LocalMangaInterface.libraryLoaded` 的同一批本地元数据提取为 GID 集合交给在线页面，Card/Extended 的每张卡片通过 GID 判断本地是否已有画廊；命中时在左上角显示绿色下载图标，未命中时不创建额外查询或磁盘访问。默认卡片显示大封面、类型/评分、悬停滚动长标题、发布时间/上传者/页数；除类别色块外这些字段都使用无边框纯文本，上传者不得增加字段前缀并与页数紧凑堆叠。类别色块在 Card/Extended 中统一使用 EH ct1–cta 渐变：Misc 灰、Doujinshi 红、Manga 橙、Artist CG 黄、Game CG 绿、Image Set 蓝、Cosplay 紫、Asian Porn 粉、Non-H 青、Western 亮绿。Extended 使用横向信息行和可换行标签，源页面为 Minimal/Minimal+ 且标签为空时显示缺省说明。列表请求使用独立搜索线程池，封面按单项任务提交到由 `onlineEhThumbnailConcurrency` 控制的专用线程池。`OnlineThumbnailCache` 将编码后的有效图片按站点写入被忽略的 `app/cache/online_thumbnails/`，使用文件修改时间和 `onlineEhThumbnailCacheHours` 惰性判定过期；损坏缓存应删除并重新请求，不得写入配置 JSON 或外部 `eh.db`。

### `app/repositories/ehviewer_download_repository.py` 与 `app/workers/online_gallery_download_worker.py`

在线下载由所有任务共享的专用 `QThreadPool` 执行，`onlineEhDownloadConcurrency` 的 1–3 只表示同时运行的画廊 Worker 数量，任何配置或旧值都不得让运行时超过 3；每个 `OnlineGalleryDownloadWorker` 内部仍按页面单线程顺序请求，不能误解或改成每个任务各自创建 N 个线程。任务顺序固定为：外部 `eh.db` 兼容元数据、RSViewer 自有任务和评论、封面、全部预览/page token、`.ehviewer`，最后逐页图片。下载管理页提供单项及“全部开始/全部暂停”，批量开始只提交当前未活动记录，批量暂停必须覆盖详情准备、sidecar 准备和图片下载阶段。Worker 在每张图片请求结束后用图片字节数/请求耗时计算瞬时速度并做指数平滑，任务卡片显示各自速度，标题区汇总活动任务速度；速度仅存内存，暂停、失败、完成或删除时清除，不得写入 SQLite。`EhViewerDownloadRepository` 只能事务 upsert 既有 `DOWNLOADS`、`DOWNLOAD_DIRNAME`、`Gallery_Tags` 内容，必须保留已有 `LABEL`、`TIME` 与 `ARCHIVE_URI`，并在写前校验所需表列；不得执行任何 DDL。新 GID 使用任务记录中的 `download_label` 写入 `LABEL`，非空值必须先确认存在于 `DOWNLOAD_LABELS`，无效分类应在创建目录前失败；已有 GID 无论默认设置如何都保留原 `LABEL`。本地详情的元数据同步只更新已有 GID 的兼容字段与 `Gallery_Tags`，必须保留 `STATE`、`LEGACY`、`LABEL`、`TIME`、`ARCHIVE_URI`，且不得创建下载目录或下载页面图片。目录优先复用 `DOWNLOAD_DIRNAME`，否则使用现有同 GID 前缀目录，再否则按 EhViewer 的 `gid-title` 规则清理 Windows 非法字符。

图片文件使用一基、八位十进制页码；`.ehviewer` 使用 `VERSION2`、gallery token、预览分页参数和每页 page token，不能把 page token 写入数据库。所有文件先写同目录临时文件再原子替换；只有新图片验证可解码并成功替换后才可删除同页旧后缀。开始或继续任务时必须校验现有图片，跳过有效页并重下缺失/损坏页；单页网络请求短暂失败最多重试三次，取消、失败和应用关闭均保留目录、自有任务和评论，下次从断点继续。逐页落盘事件只允许更新对应预览格、阅读缓存、下载页数和速度，不得重绘详情元数据或重建整个当前预览分页。若中断发生在外部 `eh.db` 条目、下载目录或完整 `.ehviewer` 创建之前，继续任务必须从 RSViewer 自有下载记录恢复站点与 GID/token，后台重新请求详情，然后重新执行外部元数据、目录和 sidecar 初始化，不能依赖本地资源列表先出现该 GID。外部 `DOWNLOADS.STATE` 使用 EhViewer 的 downloading/finish/failed 数值，RSViewer 自有库区分 queued/downloading/paused/failed/completed。完成后必须携带目标 GID 重新加载本地库、收藏/历史共享元数据和在线下载标记；本地页应清除会遮住目标的搜索条件、切到显示全部并定位目标分页，过期的后台加载结果不得覆盖本次刷新。

原图下载继续复用同一下载线程池和逐页原子写入规则。全新在线画廊使用 `original_direct`，原图直接写根目录并在完成后进入 `active`；已经存在本地目录时必须使用 `original_local`，只写 `original/`，不得覆盖根目录基础图或重写 `.ehviewer`。完整暂存后详情提供标准/原图预览与阅读来源切换。流式图片请求的连接与响应体无数据超时均不得超过 15 秒；超时必须关闭 response、清除陈旧速度并进入统一的最多三次重试，不能让任务只能依靠手动暂停恢复。列表和详情等非流式请求仍使用用户配置的请求超时。`OriginalGalleryFileWorker` 必须先校验完整原图，再持久化 `replacing_base` 并把根目录数字页移到 `history/del/`，随后持久化 `replacing_original` 并把 `original/` 数字页提升到根目录，最终标记 `active`；每次恢复都以数据库阶段和实际文件共同判断。压缩图备份绝不自动删除，只有用户二次确认后进入 `cleaning` 并清理精确的 `history/del/`。非 active 的原图阶段不得启动画廊版本更新；active 画廊的更新任务必须在 metadata 中保留 `image_mode=original`，新增页调用原图下载接口。

下载 Worker 在外部条目、目录和封面完成后必须上报本地注册事件，主窗口据此普通刷新本地库并即时更新在线卡片的本地 GID 标记；该刷新必须保留当前分类/播放列表/归类模式、显示全部状态、搜索条件、页码、仍有效的复选集合和封面内存缓存。完整 `.ehviewer` 写入后还须单独上报 sidecar 就绪事件，让已打开的同 GID 本地详情重新读取总页数和 page token。续传或补齐任务每成功原子写入一页后，Worker 必须上报真实页索引和文件路径；当前本地详情把路径增量合并到 `MangaItem` 并只替换命中的预览格，已打开的本地阅读器同步更新对应全局页码。不能只更新进度数字或只在最终完成时重载资源列表，否则本地列表、在线标记、详情与阅读器会分别持有不同阶段的旧状态。Worker 在外部初始化之前暂停或失败时，也必须更新已提前创建的 RSViewer 自有任务记录，不能让非活动任务残留为 queued/downloading。

### `app/workers/gallery_update_worker.py` 与更新管理

本地详情同步保存的 `newer_gallery_urls` 跨重启恢复“更新到最新”入口。用户触发后，RSViewer v10 `gallery_update_tasks` 作为更新管理的快速索引，画廊目录内原子写入的 `new.json` 作为文件系统 checkpoint。`status` 只是提示，每次恢复必须重新校验实际 sidecar、标记名和正常名图片；全局同时只允许一个更新 Worker，其他已开始任务必须保持 `queued`，并在当前任务完成、失败或暂停后按入队顺序自动提交。

更新顺序固定为：解析并固定最新版本、原子写 `new.ehviewer`；把旧图片改为八位页码加旧索引和十位 page token 的标记名；按新 sidecar 的 token 顺序重排，新版删除的页移入 `history/removed/{source-gid-token}`；仅用 `.part` 下载缺失页并校验后原子改名；验证完整集合后先记录 status 5，再幂等恢复标准页码名；最后归档旧 `.ehviewer`、晋升 `new.ehviewer`，并事务迁移外部 `DOWNLOADS`/`DOWNLOAD_DIRNAME`/`Gallery_Tags` GID 与 RSViewer 分类、播放列表、收藏、历史、进度关联。目标文件存在时绝不覆盖，冲突必须失败保留现场。

原画廊未完成时任务先进入 `waiting_download`，复用现有断点下载补齐后自动更新。任务未完成时详情的阅读、同步、整本/单页下载均禁用，播放列表跳过该 GID。应用退出先 cancel provider 响应并把任务恢复为 paused；“更新管理”页单独展示进度、速度、checkpoint 和错误。

### `app/services/library_organizer.py` 与整理页

整理页只在用户点击右上角扫描按钮后工作，扫描和文件/数据库操作均使用专用单线程池，不得在 GUI 线程枚举 NAS 目录。扫描范围严格限制为 `ehViewerMangaRoot` 的直接实体子目录：排除 `DOWNLOADS` 已登记的 GID 和 `DOWNLOAD_DIRNAME` 已登记的目录名，不递归进入画廊内部的 `original/`、`history/` 等目录。每个候选目录解析 `VERSION2` `.ehviewer` 的 GID、gallery token、总页数和完整 page token；缺失或损坏 sidecar 的条目可以展示和删除，但不得同步。

整理页卡片常驻复选框并支持全选，右键“同步到数据库”和“删除本地资源”作用于当前选择集合。同步前必须再次验证目录仍是根目录直接子目录且不是符号链接，并拒绝已有 GID、目录名或不同路径残留映射冲突；允许在同一事务内修复 `DOWNLOADS` 已丢失但同 GID/同目录的 `DOWNLOAD_DIRNAME` 残行。同步不得创建、移动或覆盖图片目录，只按原目录名事务插入外部既有 `DOWNLOADS`、`DOWNLOAD_DIRNAME`、`Gallery_Tags`，随后保存 RSViewer `online_gallery_downloads`/同步记录；外部表不得执行 DDL。同步阶段必须逐页验证文件可解码，完整有效页集合写 finish/completed，不完整或损坏集合写 failed/paused 以便继续补齐。自有库写入失败时必须回滚本次精确匹配的外部插入。删除必须二次确认并调用 Windows 回收站，禁止直接永久递归删除；所有操作结束后自动重扫，成功同步后刷新本地资源。

### `app/view/local_manga_interface.py`

本地资源页标签栏默认隐藏，通过工具栏“标签”按钮展开；其中“分类”“播放列表”“归类”三个面板互斥，各自带新增加号，顶部另有“显示全部漫画”。分隔条可拖动但标签栏最多占页面宽度 30%；使用 `FluentSplitterHandle` 提供 7 像素命中区和 1 像素主题色细线，透明度规则应与 `NavigationResizeHandle` 一致，不得恢复 Qt 默认实心手柄。展开、收起或拖动后必须等待 `QSplitter` 几何更新并主动调用卡片重排，不能依赖主窗口 `resizeEvent`。分类为单选并记忆选择，默认未分类；播放列表和树状归类为多对多。三个树都提供右键删除并必须二次确认；未分类不可删除，分类删除时关联漫画先回到未分类，归类父节点删除会级联整个子树。播放列表按持久顺序展示，提供播放、继续上一次，以及拖拽、上下移动、置顶/置底编排；编排保存必须绑定打开窗口时的播放列表 ID。

搜索栏支持按钮、全局快捷键和可配置的鼠标悬停展开。悬停模式不得主动抢占键盘焦点；鼠标离开搜索按钮与搜索输入区域后延迟检查，只有搜索词为空才自动收起，有内容必须保持。手动按钮与快捷键仍可显式控制搜索栏，关闭悬停配置后不得响应鼠标进入。

本地资源、收藏、本地历史和在线资源的主搜索框使用 `EhTagSearchLineEdit`。内存索引同时按英文原始标签与中文译名做包含匹配，标签结果以 `namespace：tag` 和译名上下两行显示；插入时使用 Markdown 中声明的缩写，多词标签必须加引号。补全只替换光标所在、引号外由空格分隔的当前条件，不得覆盖前面的条件。本地筛选要把 `o:"full color"` 等缩写还原为 `other:full color` 后匹配，在线搜索保留 EH 查询语法原样提交。候选层先按最近顺序显示匹配的历史输入，再显示标签结果；只有搜索图标、Enter 或页面明确执行搜索时才写历史，不能把逐字输入的中间状态入库。候选层必须复用 QFluentWidgets 的 `CompleterMenu`，限制为最多 8 个可见项并允许滚动；不得直接调用原生 `QCompleter.complete()`，否则会与 `SearchLineEdit` 自带菜单叠加并破坏主题。菜单关闭或搜索框真正失焦时必须取消待显示任务，`PopupFocusReason` 造成的菜单焦点归还不得重新弹出候选或拦截页面其余操作。

网格和列表卡片的右键菜单只保留固定的“同步在线信息”“搜索相似画廊”“选择分类…”“选择播放列表…”“选择归类…”入口，不得重新把大量标签展开为悬浮子菜单；正在查看具体播放列表时额外显示“从当前播放列表移除”，支持单本和复选批量操作。“搜索相似画廊”必须在后台针对完整本地库执行，按文件元数据括号、语言/数字版标记、章节号、卷数、话数及前后篇等规则提取作品主标题，再进行保守的长标题模糊匹配；修改搜索词或切换标签退出相似模式。标签入口打开主题化 `MangaLabelSelectionDialog`：提供搜索和可滚动树，分类单选并包含“未分类”，播放列表与树状归类多选；批量目标成员状态不一致时显示半选，半选保持不变，用户明确勾选或取消后才批量写入。播放列表/归类窗口保留“新建并添加…”入口。右键不需要先开启复选且不得触发详情。分类更新目标 `DOWNLOADS.LABEL`，播放列表和归类写 RSViewer 自有库；数据库变更应在 Worker 中执行，多项选择变化应合并为单个后台任务。

复选模式提供“全选/取消全选”按钮，作用于当前分类、播放列表或归类及搜索条件共同形成的全部结果并跨越分页；切换筛选后选中集合必须收敛到新结果范围。卡片右键“同步在线信息”对全部选中项生效，列表批量同步最多同时运行 2 项；未加载 gallery token 的条目必须在同步 Worker 中只读 `.ehviewer` sidecar 补全，不能在 GUI 线程枚举页面。整批完成后只普通刷新一次本地库并保留当前筛选。

`LocalMangaInterface` 还提供收藏/历史集合模式：不启动 `MangaLoadWorker`，隐藏标签栏和添加时间排序，按 Repository 给出的 GID 顺序分页展示共享漫画对象。卡片右键菜单顶部提供收藏或取消收藏，复选时批量生效；收藏状态变更必须同步本地资源、收藏和历史三个视图。

`MangaLoadWorker` 在一次本地库加载中批量读取 RSViewer v8 下载记录和 v9 同步记录，把上传者、发布时间、评分、语言、文件大小、评分人数、可见性和更新版本链接等元数据合并回 `MangaItem`，不得按漫画逐条查询。下载完成刷新携带目标 GID；接受最新 Worker 结果后清除会遮住目标的旧搜索条件、切到显示全部并定位其分页，再通过 `libraryLoaded` 同步收藏、历史和在线下载标记。本地详情“同步信息”完成后只执行普通刷新，必须保留当前分类筛选，不得复用下载完成的目标定位路径。已取消或已被新任务替换的加载结果必须忽略。

同一次批量加载还必须合并 v11 原图状态与基础下载任务：`active` 原图画廊卡片绘制彩色渐变边框，其他原图阶段绘制暗黄色边框；未完成的 `standard` 下载在右上角绘制黄点。绘制只消费内存中的 `MangaItem` 状态，不得让卡片逐条访问数据库或磁盘。

### `app/view/manga_history_interface.py`

历史页面包含“本地历史”和“在线历史”两个互斥入口。本地历史按 `viewed_at` 最近优先展示；在线历史当前只保留明确的占位页面和稳定 route，不得把本地记录混入其中。

### `app/view/manga_reader_interface.py`

漫画阅读器作为主窗口堆叠页运行，普通状态为窗口内阅读，按 `F11` 或工具栏按钮后由主窗口隐藏标题栏与导航并切换全屏，`Esc` 恢复窗口。当前实现单页模式、四向下一页按键、页码跳转、长图滚动和自动翻页；从播放列表进入时，末页“下一页”打开下一本首页，首页“上一页”打开上一本末页，列表两端则正常停止。窗口页码在顶部居中，全屏使用独立低透明度页码并默认隐藏控制区，仅上下 12 像素边缘触发对应栏。图片在 `QThreadPool` 后台解码并预读相邻页；加载器按 `GIF87a`/`GIF89a` 文件头识别扩展名错误的 GIF，预载阶段只缓存首帧，成为当前页后再使用 `QMovie` 播放并沿用现有缩放、翻页和全屏画布。点击图片取得焦点后仍须正常翻页。页码变化即时更新列表/详情并防抖保存。本地页必须按数字文件名映射全局页码，不能让中间缺页压缩后续页；具备完整 `.ehviewer` token 的本地画廊点击缺页时只下载该页到本地后展示，纯在线阅读仍只写内存缓存。工具栏显示当前画廊任务的已完成/总页数和实时速度。

在线阅读复用同一阅读器控件和当前 provider，会按当前页、后两页、前一页的顺序惰性解析单图 HTML 与下载展示图；不得一次解析整个画廊。在线页码不写入本地漫画进度表，返回时回到仍保留状态的在线详情页。

`ReaderSettingDialog` 是阅读页工具栏打开的非模态设置面板。它和全局 `SettingInterface` 绑定同一组 `cfg.reader*` 配置项，任一侧修改背景色、方向、图片载入大小、滚动快捷键或自动翻页设置后，另一侧与当前阅读器必须即时更新。弹窗通过 `StyleSheet.READER_SETTING_DIALOG` 注册 light/dark QSS，主题切换时背景必须与 Fluent 控件的文字和卡片风格同步刷新。

### `app/view/manga_detail_interface.py`

该页面由本地与在线画廊共享。本地模式每页显示 40 个预览格，并在按需读取 `.ehviewer` 后显示真实总页数、磁盘已有页数和完整状态；实际文件数少于 sidecar 页数时按 sidecar 总数创建预览格，已有页读取本地文件，缺失页只请求当前预览分页涉及的在线缩略图；实际文件数大于或等于 sidecar 页数时按实际文件数显示。只要 sidecar 含完整画廊/页面 token，就始终提供源站检查补齐，即使目标 `DOWNLOADS.STATE` 已为完成。本地“同步信息”只在后台请求当前详情 HTML，刷新标签/评论/额外元数据并显示 `#gnd` 版本检测结果，不得启动图片下载；已同步评论和版本状态跨重启恢复。在线模式显示“开始在线阅读”、站点原生每页 20 张的缩略预览和只读评论区。在线预览只加载当前分页，切页先查内存缓存，未命中才后台请求；点击缩略图必须从对应全局零基页索引进入阅读器。评论作者、时间、上传者标记、评分和正文均可选择复制；在线详情返回时必须回到原在线资源列表，而不是本地资源。下载控件使用竖直组合：按钮位于进度条上方，下载/排队/暂停/失败时按钮直接显示已完成页数与总页数，下载中点击同一按钮暂停。

详情页仍在首次打开时后台枚举单本的全部页面路径，以供阅读器随机跳页；缩略预览不得据此一次创建全部控件或解码全部图片。预览固定每页 40 张，只创建并解码当前预览页，切页时取消上一批任务。每个预览块保留全局零基页索引，点击后必须进入对应的真实阅读页。详情标题、英文标题、元数据和标签文字均须支持鼠标/键盘选择及复制。主信息区默认不展示播放列表、归类、页数、阅读进度、已下载/完整状态、文件大小和可见性，这些低频字段统一放入默认收起且同样可复制的“查看详细”区域；切换画廊时重新收起，同一画廊的异步信息刷新不得打断当前展开状态。详情标签使用独立卡片，按 EhViewer 命名空间分组并以主题化胶囊控件双栏自动换行；数据源为搜索同时生成的裸标签不得与 `namespace:value` 重复显示。样式由 `StyleSheet.MANGA_DETAIL_INTERFACE` 的 light/dark QSS 管理。

### `app/view/setting_interface.py`

设置页基于 `ScrollArea`，使用 QFluentWidgets 设置卡片直接绑定 `cfg`。当前包含主题模式、主题色和媒体目录。主题变化链路为：

```text
OptionsSettingCard
  -> cfg.themeMode 更新
  -> cfg.themeChanged
  -> setTheme()
  -> QFluentWidgets 样式管理器刷新所有已注册 QSS
```

设置页还提供 EhViewer 数据库文件与漫画根目录选择器，变更后立即重建数据源并后台刷新本地漫画；常规读取使用只读连接，分类操作按用户指令另开写事务。在线资源分组提供站点、Cookie/Token、系统/直连/手动代理、手动 HTTP(S) 地址、10/20/30/60 秒超时、默认展示视图、封面并发数、1–3 个画廊下载并发数、默认下载分类和封面缓存过期时间，手动地址只在对应模式启用；默认分类选项消费本地资源加载得到的同一批 `DOWNLOAD_LABELS`，不存在或已删除的配置回退为未分类。视图和并发数修改后对应页面或线程池即时同步。快捷键使用点击后捕获一次按键的交互，组合键或单键按下即保存，`Esc` 取消；当前可配置搜索栏、标签栏、返回和阅读器滚动快捷键。界面设置提供搜索栏鼠标悬停自动展开开关并即时生效。全局设置新增漫画阅读器分组，与阅读页内设置面板共用配置并即时同步。通用 `libraryFolders` 仍未被扫描器消费。

### 外部 EhViewer 数据源约束

用户本机的 `testData/db/eh.db` 是旧 Android 漫画应用产生的 SQLite 数据库，仅用于兼容分析和开发测试：

- SQLite `user_version=7`。
- 已有表包括 `BOOKMARKS`、`DOWNLOADS`、`DOWNLOAD_DIRNAME`、`DOWNLOAD_LABELS`、`Gallery_Tags`、`HISTORY`、`LOCAL_FAVORITES`、`FILTER`、`QUICK_SEARCH`、`Black_List` 和 `android_metadata`。
- `GID` 是下载信息、目录名、标签、收藏和历史之间的重要关联键。
- 浏览、搜索、封面和元数据读取默认以 SQLite 只读模式打开。功能需要且用户明确触发时可以事务修改既有表中的业务内容；当前已实现更新所选 GID 的 `DOWNLOADS.LABEL`，以及在线下载时 upsert `DOWNLOADS`、`DOWNLOAD_DIRNAME`、`Gallery_Tags`。
- 不允许在 `eh.db` 中新增 RSViewer 表，也不允许对它执行 RSViewer migration。
- 外部数据库的表、索引、触发器、列和 `user_version` 结构属于不可变边界；允许修改内容不等于允许执行 DDL。
- RSViewer 自有的媒体索引、路径映射、阅读进度、视频数据和设置扩展必须写入另一份独立 SQLite 文件。

`ehViewerMangaRoot` 指向对应下载根目录。典型结构是一个下载目录包含 `.ehviewer` sidecar、`.thumb` 缩略图和按页码命名的图片。列表只枚举一次根目录；当前页和后续三页在后台优先读取 `.thumb`，缩略图缺失或损坏时只对相应漫画枚举并使用自然排序后的第一页；详情页才枚举单本的全部页面。扫描和阅读逻辑必须自然排序现有页面并容忍缺页，不能假设编号连续。

`.ehviewer` 读取兼容规则：第二行是十六进制的零基页索引，例如 `0000008f` 表示索引 143、界面第 144 页；`VERSION2` 的 GID、画廊 token、总页数和逐页 token 只在打开详情或继续任务时按需解析，用于判断磁盘实际完整性并重建源站 `/s/...` 补齐地址，绝不能以 `DOWNLOADS.STATE` 代替文件校验。启动阶段禁止逐本打开两万多个 sidecar；仅在当前列表页/后续预载页或打开详情时后台按需读取。自有数据库无记录且 sidecar 有效时导入一条；两边都有时自有记录优先；后续阅读只写 RSViewer 数据库。在线下载是唯一会新建或补全 `.ehviewer` 的链路，写入时保留合法的已有阅读页索引。

## 5. 当前系统数据流

### 启动流

```text
导入 cfg并加载 JSON
  -> 读取 DPI/语言
  -> 创建 QApplication
  -> 安装 FluentTranslator
  -> 创建 MainWindow
  -> 初始化 RSViewer 自有 SQLite 并加载 EH 标签内存检索表与搜索历史
  -> 创建 SettingInterface
  -> 注册 light/dark QSS
  -> 进入 Qt 事件循环
```

### 设置流

```text
用户操作设置卡片
  -> QConfig 校验器验证
  -> cfg 更新并保存 JSON
  -> 对应 Qt 信号触发
  -> 主题/主题色立即刷新，DPI/语言提示重启
```

### 媒体流

当前 EhViewer 漫画流已经实现以下边界：

```text
配置中的 eh.db + 本地/映射盘/UNC 漫画根目录
  -> Worker 后台执行 EhViewerDataSource
  -> SQLite 只读元数据查询 + 根目录单次枚举
  -> LocalMangaInterface 分页、搜索、分类/播放列表/树状归类筛选和封面展示
  -> 显式分类操作更新目标 DOWNLOADS.LABEL；播放列表与归类写自有 SQLite
  -> 后台预读当前页及后续三页封面；无有效缩略图时回退第一页
  -> 打开详情时才枚举该漫画全部页面路径；缩略预览按每页 40 张后台生成
  -> 按需读取 .ehviewer 第二行；仅在无自有进度时导入 RSViewer SQLite
  -> 开始阅读后后台解码当前页并预读相邻页，可在窗口和全屏之间切换
  -> 翻页即时刷新列表/详情，防抖后由单线程 Worker 保存自有进度
  -> cfg.reader* 即时控制画布、方向、载入大小、滚动快捷键与自动翻页
  -> UserLibraryRepository 保存播放列表顺序/位置、树状归类和阅读进度
  -> 打开详情/阅读时后台更新本地历史；右键收藏后刷新共享集合视图
```

在线资源流：

```text
cfg.onlineEh* -> EhOnlineSettings
  -> OnlineMangaInterface 构造 OnlineGalleryQuery
  -> 按 EH/EX 切换 OnlineSiteState；命中内存页缓存则直接恢复
  -> OnlineSearchWorker 调用 EhOnlineProvider.search
  -> RefactoredEhOnlineProvider 调用 eh_tool_refactored.EhData
  -> 同站 HTML 列表页 GET/f_search/next_url -> BeautifulSoup+lxml 解析
  -> provider.fetch_page -> provider.filter_items
  -> OnlineGalleryPage -> 当前站点最近 64 页内存缓存 -> Card/Extended 视图与游标翻页 UI
  -> 视图切换 -> 后台 inline_set 同步站点默认模式 -> 重取当前页并更新缓存
  -> 多个 OnlineCoverWorker 先查分站点磁盘缓存，未命中才使用同一 Session
  -> 专用 QThreadPool 按配置并发加载 EH/EX/ehgt 缩略图并原子写入缓存
  -> 点击卡片 -> OnlineDetailWorker 使用当前 provider GET 同站画廊 HTML
  -> OnlineGalleryDetail/Comment/Preview -> 最近 20 个画廊的 OnlineGalleryMemoryCache
  -> 共享 MangaDetailInterface 展示在线详情、20 张一页的预览与只读评论区
  -> 切换预览页 -> OnlinePreviewPageWorker 请求同站画廊 ?p=N HTML
  -> 点击预览或开始阅读 -> 共享 MangaReaderInterface
  -> OnlineReaderLoadWorker 惰性请求 /s 单图 HTML -> #img 展示图并预读相邻页
  -> 在线详情、本地详情或正在下载页发起 -> 可配置 1–3 并发的 OnlineGalleryDownloadWorker
  -> 事务写既有 eh.db 元数据 -> 自有 SQLite v11 任务/评论/原图状态/额外元数据
  -> 收集全部画廊预览页的 page token -> 原子写 .thumb 与 VERSION2 .ehviewer
  -> 校验已有八位页码图片 -> 跳过有效页 -> /s HTML -> #img 展示图补齐缺失页
  -> 正在下载页可单项或一键全部开始/暂停未完成任务，也可删除任务记录；完成任务自动移出
  -> 完成后更新 DOWNLOADS.STATE 并刷新本地库；失败/暂停/重启后保留断点
```

在线资源只有用户进入尚无内存状态的站点、主动搜索/刷新/翻页/切换 Card/Extended 视图、点击未缓存的画廊详情、切换未缓存的预览分页、阅读或下载未缓存页面、封面磁盘缓存未命中时才产生网络请求；仅创建页面和 provider 本身不访问网络。

列表阶段禁止递归或逐本枚举页面；21,389 部漫画的真实库验证为 0 个页面路径常驻。通用媒体流仍按 `Source -> Worker -> Repository -> Service -> View` 目标继续实现。

网络和磁盘 I/O 不得阻塞 Qt GUI 线程。

## 6. 目标架构（尚未实现）

后续业务代码优先按以下职责分层；不要把扫描、数据库或媒体解析逻辑直接写进 QWidget：

```text
app/
├─ domain/          # MediaItem、Library、Progress 等纯数据模型
├─ repositories/    # SQLite schema、迁移和数据访问
├─ sources/         # Local/UNC，未来可扩展 SMB/WebDAV
├─ services/        # 扫描编排、查询、缩略图、元数据、进度
├─ workers/         # QThreadPool/QRunnable 后台任务
└─ view/
   ├─ library/      # 封面墙、搜索、筛选、详情
   ├─ reader/       # 图片/漫画阅读器
   ├─ player/       # 视频播放器
   └─ settings/     # 设置界面
```

依赖方向应为 `view -> services -> repositories/sources -> domain`。`domain` 不应依赖 Qt Widget；Repository 不应反向引用 View。跨层通信优先用明确的服务接口和 Qt 信号，避免重新引入一个无边界的全局 signal bus。

NAS 第一阶段按普通文件系统路径处理，包括已挂载盘符和可访问的 UNC 路径。不要在 MVP 阶段同时实现原生 SMB/WebDAV 凭据系统。

## 7. 已完成内容

截至 2026-08-15：

- 可启动的 PySide6 Fluent 桌面主窗口。
- 窗口居中、Splash、Fluent 图标和 Windows 系统主题监听。
- 浅色、深色、跟随系统和自定义主题色配置。
- 全局设置页和阅读设置弹窗的 light/dark QSS 已接入 Fluent 样式管理器；主题切换时背景、文字和卡片外观同步刷新。
- 应用默认直接进入本地漫画库；左侧导航底部使用“漫画 / 视频”模式按钮切换顶部扁平入口，不再保留虚拟父路由或空展示首页。
- EhViewer 外部 DB 与漫画根目录可在设置中选择，变更后自动刷新；外部 DB 除用户显式分类操作和在线下载更新既有表内容外均只读，schema 始终不可变。
- 本地漫画支持封面/标题布局、分页、搜索，以及互斥的分类、播放列表、树状归类视图；标签栏可拖动至最多 30%，分类记忆上次选择，播放列表可编排/续播/跨漫画翻页。三类标签通过可搜索、可滚动的独立选择窗口支持单项或复选批量分配，不再使用无界右键子菜单。
- 漫画右键支持在后台搜索整个本地库的相似画廊：能剥离常见社团/语言元数据、章节号、卷数、话数及前后篇，优先找出同作品章节和重复条目，并对较长标题提供保守容错匹配；21,389 条真实库首次后台全量比较约 3.3 秒，标题指纹缓存后的再次比较约 1.1 秒，期间不阻塞 GUI。
- 搜索栏和标签栏均提供可即时配置的全局快捷键；搜索按钮支持可关闭的悬停自动展开，空搜索离开后自动收起，有搜索内容时保持显示。
- `Database/database` 标签翻译仓库可通过脚本幂等导入 RSViewer SQLite；启动后本地、收藏、历史和在线搜索共享 43,751 条内存标签索引，支持中英文片段、多词引号、空格分隔的多条件补全及两行候选显示。
- 本地与在线搜索共享 SQLite v7 最近历史，匹配历史优先于标签候选，设置中可即时配置 5/10/15/20 条上限。
- 漫画阅读器支持窗口内与沉浸式全屏模式、全屏上下边缘触发控制栏、独立透明页码、键盘/按钮翻页、页码跳转、适应窗口、原始尺寸、缩放、拖动和相邻页后台预读；GIF 按文件头而非后缀识别，扩展名错误时仍可播放动画。
- 阅读页内与全局阅读设置即时同步，支持画布背景、四向翻页、适应宽度长图滚动、滚动快捷键及自动翻页间隔。
- 阅读进度在 RSViewer 独立 SQLite 保存并恢复；兼容首次导入 `.ehviewer` 十六进制页索引，自有记录优先，列表卡片与详情页显示当前进度。
- 详情页只创建并解码当前预览页的至多 40 张缩略图，任意预览缩略图可点击并直接从对应页码进入阅读。
- 详情页按命名空间分组展示去重后的胶囊标签，支持浅色/深色主题配色和大量标签自动换行；标题、元数据和标签文字可选中复制。
- 收藏页和本地历史页复用大型库首次加载结果；收藏支持右键单项/批量切换，本地历史按最近打开详情或阅读的时间排列，并预留在线历史入口。
- 在线画廊卡片在应用内打开共享详情页；详情 Worker 复用当前 provider 会话直接请求同站画廊 HTML，展示完整元数据、标签、20 张一页的缩略预览与只读评论。点击预览或“开始在线阅读”后复用漫画阅读器，按需解析 `/s` 单图 HTML 并加载 `#img` 站点展示图，返回时恢复在线详情及列表状态。
- 在线详情页支持后台下载、暂停和断点继续：任务先写外部 `eh.db` 兼容元数据与自有 SQLite 评论/额外元数据，再收集全部 page token，按 EhViewer 规则生成目录、`.thumb`、`VERSION2` `.ehviewer` 和八位页码站点展示图；已有有效页会跳过，缺失或损坏页会补下，完成后自动刷新本地库。
- 在线详情、预览和阅读使用最近访问 20 个画廊的线程安全内存 LRU；详情、封面、已访问的预览分页与缩略图可直接复用，阅读图片每画廊最多缓存 5 页并受 128 MiB 总预算约束。
- 在线资源已把用户提供的 `eh_tool_refactored.py` 接入 provider 和后台 UI/Worker：按原实现请求 E-Hentai / ExHentai HTML 列表页，支持关键词、next/prev URL 翻页、多显示模式元数据解析和封面加载；两个站点各自保存当前页、翻页历史、滚动位置和最近 64 页内存结果，切站可即时恢复，空容器自动加载首页，刷新按钮可强制重取当前页。在线结果支持记忆默认的 Card/Extended 本地视图，评分以 `0–5` 数值展示，Minimal 页面缺少标签时会明确说明。封面使用可配置并发线程池及按站点隔离、可配置过期时间的磁盘缓存。设置页提供隐藏显示的 Cookie/Token、系统/直连/手动代理、手动 HTTP(S) 地址、请求超时和默认视图。浅色/深色在线页滚动区、两类结果卡片与封面占位已纳入 Fluent QSS。
- 快捷键设置采用点击捕获交互，支持单键和 `Ctrl+S` 等组合键即时确认，`Esc` 取消。
- 大型库采用列表元数据与详情页面两级惰性加载；21,389 部真实漫画列表读取约 0.42 秒，加入自有进度批量读取约 0.483 秒，完整首屏约 1.33 秒。
- RSViewer 独立 SQLite v11 保存播放列表、树状归类、收藏、本地浏览历史、阅读进度、导入的 EH 标签快照、共享搜索历史，以及在线下载状态、原图资源阶段、本地画廊同步记录、评论和额外元数据；目标 EhViewer 数据库只在显式分类、在线下载或本地详情同步时更新既有业务表，schema 永不变更。
- 本地/映射盘/NAS 路径的其他媒体目录配置入口。
- DPI、语言和 Mica 配置模型。
- 模板 Gallery、演示资源、音乐配置和无用生成资源已清理。
- `README.md`、`requirements.txt` 和 `.gitignore` 已建立。
- 已做过 `compileall`、`git diff --check`、provider 契约与配置自动化测试、真实大型库计时，以及默认页/详情/快速退出的 Qt offscreen 冒烟验证。

## 8. 正在开发的内容

EhViewer 本地漫画浏览、收藏、本地历史和基础阅读链路已经可用；EH/EX 的 HTML 列表页抓取、配置、分页、封面、应用内详情、缩略预览、只读评论区、站点展示图在线阅读，以及站点展示图/`fullimg` 原图可续传下载已接入。下一阶段应补齐 RSViewer 完整媒体 schema/migration、通用本地/UNC 扫描服务，以及双页/多图连续滚动。发表评论、在线历史和视频仍未实现。

工作区当前包含本次惰性加载、数据源配置、默认导航和测试改动；`testData/`、`app/config/config.json` 与 `app/data/` 是忽略的本机数据，不得提交或删除。

## 9. 已知问题与技术债

- 开发配置路径已不依赖当前工作目录，但打包前仍应迁移到 `QStandardPaths.AppConfigLocation`；RSViewer 自有数据库和缓存也应迁移到应用数据目录。
- `libraryFolders` 只保存其他媒体目录列表，尚无通用扫描、可达性检测、断线状态或重连机制；UNC/NAS 的大规模真实场景仍需专项验证。
- 当前 RSViewer SQLite 已有版本化的播放列表、树状归类、收藏、本地浏览历史、漫画进度和历史兼容分类覆盖表，尚无完整媒体 schema、缩略图缓存及缓存失效策略。
- 为保证大型库首屏速度，漫画卡片在列表阶段不显示精确页数；页数在打开单本详情并完成按需枚举后可用。
- 阅读器当前只有单页模式；单张长图可按屏滚动，但尚未把多张图片拼接为连续长图，也未建立磁盘级解码缓存。
- 在线资源当前已支持 HTML 列表页与画廊详情抓取、缩略预览、只读评论、站点展示图阅读、基础/原图断点下载、过滤扩展点、URL 主机校验、分站点页状态、并发缩略图、过期磁盘封面缓存，以及最近 20 个画廊且受图片预算约束的内存 LRU；尚无手动清理缓存入口，发表评论和在线历史仍未实现。原图请求可能消耗站点额度，失败时保留任务供用户继续。鉴权 Cookie 目前保存在被忽略的本机 JSON，打包前应迁移到 Windows 凭据存储。
- 数据源已有小型自动化测试，但空库、数据库损坏、UNC 断线、取消中的慢速 NAS 和超长路径仍需覆盖。
- 语言枚举和 Fluent 翻译器已存在，但 RSViewer 自身的中文文案是硬编码，切换英语不会完整翻译。
- 依赖只声明范围，没有锁定可复现版本；本机验证版本是 PySide6 6.10.1 和 PySide6-Fluent-Widgets 1.10.5。
- 尚无打包和发布流程。
- 代码仍有少量格式和命名可统一，例如私有初始化方法命名、字符串引号和空行；功能开发时顺手改善，避免纯格式大改掩盖业务 diff。

## 10. 下一步计划

按优先级推进：

1. 将配置、RSViewer 自有 SQLite 和缓存从开发目录迁移到 `QStandardPaths` 应用数据目录，并设计兼容迁移。
2. 在现有版本化标签/进度表基础上定义 RSViewer 完整媒体领域模型和独立 SQLite schema。
3. 实现本地目录、映射盘和 UNC 通用扫描；必须后台执行、可取消、可报告进度和错误。
4. 提取图片/视频基础元数据，建立磁盘缩略图缓存。
5. 在现有单页/单张长图阅读器上补充双页和多图连续滚动。
6. 实现 Qt Multimedia 视频播放器和播放进度。
7. 扩充自动化测试，覆盖损坏 DB、空目录、目录不可达、UNC 断线、中文/特殊字符、超长路径和大目录取消。
8. 在现有 EH/EX HTML 列表、详情、预览、站点展示图阅读与基础/原图断点下载 provider 上完善发表评论与在线历史接口，以及打包、更新和发布流程。

## 11. 开发规范

- 修改前先读 `AGENTS.md`、`CHANGELOG.md`，执行 `git status --short`，保护用户已有修改。
- 保持 UI、服务、数据访问、文件来源分层；禁止在 GUI 线程递归扫描 NAS 或生成大量缩略图。
- 文件路径使用 `pathlib.Path`；对 UNC、长路径、无权限、断线和文件消失做显式错误处理。
- 媒体扫描应幂等：重复扫描不能制造重复条目；文件删除、移动和修改需要可追踪。
- 数据库 schema 每次变化必须有迁移，不允许靠删除用户数据库解决升级。
- 外部 EhViewer 数据库的 schema 永远不可修改：不得新增/删除/重命名表、列、索引或触发器，不得执行 RSViewer migration。功能需要且用户明确触发时，可在事务中更新既有表的业务内容；RSViewer 自有表和不属于 EhViewer 原结构的数据仍只能进入独立数据库。
- 所有 QSS 必须同时维护 light/dark 版本，并验证主题即时切换。不要用内联 QSS 固定主题相关颜色。
- 配置只放轻量用户偏好；媒体元数据和进度进入 SQLite；缩略图进入缓存目录。
- 新依赖加入 `requirements.txt` 前说明用途和运行时成本，优先使用 Python 标准库与 PySide6 已包含模块。
- 不提交用户 NAS 路径、凭据、数据库、缓存、日志、IDE 文件或测试媒体。
- 不提交大体积示例媒体；测试需要媒体时使用小型、明确许可的 fixtures。
- 对个人非商业边界和第三方许可保持说明，不擅自宣称整个项目采用某许可证。
- 用户只要求诊断时不要顺带实现；用户要求修改时完成与风险相称的验证。

## 12. 验证清单

每次 Python 修改至少执行：

```powershell
python -m compileall -q main.py app eh_tool_refactored.py
git diff --check
```

涉及 UI 时还应：

- 从仓库根目录运行 `python main.py`。
- 检查浅色、深色、跟随系统三种模式。
- 检查窗口缩放和最小宽度。
- 确认关闭窗口后进程退出，主题监听器没有遗留。

涉及扫描/数据库时还应增加自动化测试，覆盖空目录、重复扫描、损坏文件、目录不可达、UNC 断线、中文/特殊字符路径和大目录取消。

## 13. 文档和变更记录维护规则

每次重大修改必须在同一批改动中同步更新：

- `AGENTS.md`：更新架构、模块职责、数据流、当前完成内容、正在开发内容、已知问题和下一步计划。
- `CHANGELOG.md`：在 `[Unreleased]` 下按 Added/Changed/Fixed/Removed/Security 记录用户可见或维护上重要的变化。

“重大修改”包括但不限于：新增系统/页面/服务、改变目录或依赖方向、数据库 schema 变化、新增第三方依赖、配置格式变化、NAS 访问策略变化、重要 Bug 修复、删除功能或改变发布方式。

完成一项计划后，把它从“正在开发/下一步”移动到“已完成”，并记录验证方式。不要只追加历史而让当前状态失真。
