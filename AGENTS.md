# RSViewer 项目维护指南

本文档面向未来接手本仓库的 AI 助手和开发者。开始任何工作前，请先完整阅读本文、`README.md` 和 `CHANGELOG.md`，然后执行 `git status --short`。本文描述的是 2026-08-22 的工作区现状；若代码与本文冲突，以代码为准，并在本次修改中同步修正文档。

## 1. 项目背景与边界

RSViewer 是一个仅供个人、非商业使用的 Windows 桌面媒体管理与查看工具。目标是统一浏览本地磁盘和 NAS 中的漫画、图片集与视频，并提供媒体索引、封面、搜索、阅读/播放和进度保存能力。

当前项目来自 PyQt-Fluent-Widgets Gallery 示例骨架，但示例、演示资源和音乐播放器残留已经被有意删除。不要恢复 `examples/`、旧 Gallery 资源、旧音乐配置或约 26 万行的生成文件 `app/common/resource.py`，除非用户明确要求。

第三方 UI 依赖是 PySide6-Fluent-Widgets。项目虽为个人非商业用途，仍须遵守第三方组件许可证，不要删除 `README.md` 中的第三方说明。

### 当前成熟度

项目处于早期 MVP 阶段。由 RSViewer 自有 SQLite 驱动的本地漫画库已经可配置根目录、可分页浏览、搜索、筛选、打开详情并进入单页漫画阅读器；运行时不再依赖外部 `eh.db`。阅读器已有四向翻页、单张长图滚动、自动翻页、进度保存/恢复和即时同步设置。在线资源已按用户的 `eh_tool_refactored.py` 接入 EH/EX HTML 画廊列表搜索、翻页和封面加载，并具备分站点内存页缓存、并发封面加载、过期磁盘缓存、应用内详情、只读评论、缩略预览、按需在线阅读，以及兼容 EhViewer 目录和导出数据库的站点展示图/`fullimg` 原图断点下载。现有本地画廊可先把原图下载内容暂存到 `original/`，再以可恢复步骤替换根目录图片并保留 `history/del/` 基础图备份；原图下载按页持久记录 `original` / `base` 类型，单页没有 `fullimg` 时仅该页回退基础图，后续版本更新继续逐页优先请求原图。通用媒体扫描、双页/多图连续阅读和视频播放器仍未实现。

## 2. 技术栈与运行环境

- Python：建议 3.10+；2026-08-03 本机验证环境为 Python 3.9.2。
- GUI：PySide6，依赖范围见 `requirements.txt`。
- Fluent UI：PySide6-Fluent-Widgets。
- 主要平台：Windows 10/11；Mica 效果仅在符合条件的 Windows 11 环境启用。
- 当前持久化：QFluentWidgets 的 JSON 配置，以及单一 `rsviewer.db` 中的 EhViewer 兼容画廊索引/分类和 RSViewer 树状归类、收藏、本地浏览历史、漫画阅读进度、搜索历史、在线下载状态与评论。源码版和 PyInstaller 版都使用运行根目录下的 `data/config.json`、`data/rsviewer.db` 和 `data/cache/`；源码运行根目录是项目根目录，冻结运行根目录是 exe 所在目录。旧播放列表表仅为数据库兼容保留；旧 `eh.db` 仅通过脚本只读导入，需要时由设置页另行导出。
- 规划持久化：SQLite 媒体索引与文件系统缩略图缓存。
- 规划媒体能力：Qt Multimedia；在确认格式覆盖不足前不要过早引入 VLC/mpv 等额外运行时。

安装和启动：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

路径由 `app/common/app_paths.py` 统一解析。源码运行通过项目根目录得到稳定绝对路径；PyInstaller 运行时只读资源来自 `_MEIPASS`，配置、数据库和缓存写入 exe 同级的 `data`，不得写入临时 bundle。目标文件缺失时先迁移旧 `app/config`、`app/data` 或 `%LOCALAPPDATA%\RSViewer` 状态，再由配置层和 Repository 自动创建默认文件；已存在的目标文件不得被迁移覆盖。

## 3. 当前目录结构

```text
RSViewer/
├─ AGENTS.md                         # 本维护指南
├─ CHANGELOG.md                      # 面向版本/重大修改的变更记录
├─ README.md                         # 用户向项目简介与启动说明
├─ eh_tool_refactored.py             # 用户提供的 EH/EX HTML 列表页抓取与解析实现
├─ requirements.txt                 # 直接运行依赖
├─ RSViewer.spec                    # PyInstaller 单文件构建配置与 QSS 数据收集
├─ scripts/import_ehviewer_database.py # 只读导入旧 eh.db 到自有 SQLite
├─ scripts/import_eh_tags.py         # EH 标签翻译 Markdown 到自有 SQLite 的幂等导入脚本
├─ main.py                           # 唯一应用入口
└─ app/
   ├─ common/
   │  ├─ app_paths.py                # 源码/冻结环境的资源与可写状态路径
   │  ├─ config.py                   # 配置模型、稳定配置路径、JSON 加载
   │  └─ style_sheet.py              # 自定义 QSS 路径与主题注册
   ├─ domain/manga.py                # 本地漫画领域模型
   ├─ domain/online_gallery.py       # 在线画廊与翻页结果模型
   ├─ domain/online_download.py      # 在线下载任务状态模型
   ├─ domain/gallery_update.py       # 本地画廊版本更新任务模型
   ├─ domain/gallery_trash.py        # 本地画廊回收记录与持久状态
   ├─ domain/similar_gallery.py      # 最近一次选中文字相似查询记录
   ├─ repositories/user_library_repository.py # RSViewer 用户标签与阅读进度库
   ├─ repositories/ehviewer_schema.py # EhViewer v7 兼容表结构与校验工具
   ├─ repositories/ehviewer_download_repository.py # 自有库兼容表与下载目录写入
   ├─ repositories/gallery_update_state_repository.py # 目录 new.json 原子 checkpoint
   ├─ services/eh_tag_importer.py    # EH 标签 Markdown 快照解析与校验
   ├─ services/eh_tag_search.py      # 启动加载的内存标签检索表与本地查询解析
   ├─ services/search_history.py     # 本地/在线共享的持久化搜索历史服务
   ├─ services/manga_classification_index.py # 分类/归类到 GID 的内存倒排索引
   ├─ services/multi_window_coordinator.py # 多窗口事件总线、共享线程池与任务所有权
   ├─ services/gallery_page_download_scheduler.py # 活动画廊共用的公平页面下载调度器
   ├─ services/ehviewer_database_transfer.py # 旧库只读导入与兼容库原子导出
   ├─ services/library_organizer.py  # 未登记本地目录扫描、同步与回收站边界
   ├─ services/gallery_trash.py      # 画廊软删除、原位还原与永久清理事务
   ├─ services/online_download_builder.py # 从本地 sidecar 重建源站补齐请求
   ├─ services/online_query_syntax.py # EH/EXH/NHC/NHN 查询与复制 Tag 语法适配
   ├─ services/manga_title_similarity.py # 章节/卷号与元数据噪声剥离、标题相似度匹配
   ├─ services/online_thumbnail_cache.py # 在线封面分站点磁盘缓存与惰性过期
   ├─ services/online_gallery_memory_cache.py # 最近 20 个在线画廊 LRU 内存缓存
   ├─ sources/ehviewer_source.py     # EhViewer 只读查询、分类写入与惰性页面加载
   ├─ sources/eh_online_source.py    # EH/EX provider 接口、运行配置与用户爬虫适配器
   ├─ sources/nh_online_source.py    # NHC/NHN 列表、详情、预览、阅读与基础下载 provider
   ├─ workers/eh_online_worker.py    # 在线搜索、封面、详情、预览和阅读 Worker
   ├─ workers/ehviewer_database_worker.py # 设置页兼容数据库导出 Worker
   ├─ workers/online_gallery_download_worker.py # 在线画廊断点下载 Worker
   ├─ workers/original_gallery_worker.py # 原图替换与压缩图备份清理 Worker
   ├─ workers/library_organizer_worker.py # 整理页扫描、同步和回收站操作 Worker
   ├─ workers/gallery_trash_worker.py # 回收站串行批处理 Worker
   ├─ workers/gallery_update_worker.py # 本地画廊新版本可恢复更新 Worker
   ├─ workers/reading_progress_worker.py # 后台保存阅读进度
   ├─ workers/similar_manga_worker.py # 大型本地库标题相似搜索 Worker
   ├─ resource/qss/
   │  ├─ dark/                        # 设置页与阅读设置弹窗深色样式
   │  └─ light/                       # 设置页与阅读设置弹窗浅色样式
   └─ view/
      ├─ main_window.py              # Fluent 主窗口、导航、主题监听
      ├─ local_manga_interface.py    # 本地漫画分页、搜索、标签、排序与封面卡片
      ├─ custom_manga_sort_dialog.py # 分类/归类成员拖放与批量上下移动排序
      ├─ eh_tag_search_line_edit.py  # 支持多条件及光标替换的 EH 标签补全搜索框
      ├─ manga_history_interface.py  # 本地浏览历史与在线历史预留路由
      ├─ download_manager_interface.py # 未完成下载任务的集中管理页面
      ├─ update_manager_interface.py # 未完成画廊版本更新管理页面
      ├─ library_organizer_interface.py # 未登记本地资源整理页面
      ├─ recycle_bin_interface.py    # 可复选的画廊回收站卡片页
      ├─ manga_detail_interface.py   # 本地/在线共享详情、页面预览与评论区
      ├─ gallery_state_indicator.py  # 下载/阅读双状态点
      ├─ gallery_source_badge.py     # 在线/本地卡片来源标签
      ├─ similar_gallery_browser_window.py # 单例相似画廊浏览窗口
      ├─ manga_reader_interface.py   # 单页阅读、缩放、预读和全屏控制
      ├─ online_manga_interface.py   # 在线画廊搜索、翻页、封面和主题化结果页
      ├─ reader_setting_dialog.py    # 阅读页内即时同步设置面板
      ├─ media_interface.py          # 未实现媒体路由的轻量占位页面
      └─ setting_interface.py        # 设置页、数据源路径和配置绑定
```

`data/` 是运行时生成的用户状态目录，已被 `.gitignore` 忽略，不应提交。旧 `app/config/config.json`、`app/data/` 和 `app/cache/` 仅作为迁移来源继续忽略，不应删除用户现有文件。`.idea/`、`__pycache__/`、构建目录同样不应提交。

本机还可能存在被忽略的 `testData/`、`Database/` 和 `lib/`：前者是用户提供的外部数据库与漫画样例，`Database/` 是单独克隆的 EH 标签翻译仓库，后者是本地 Python 环境。它们都不是应用源码，不得提交或删除。

## 4. 核心模块与职责

### `main.py`

应用组合根。读取 DPI 和语言配置，创建 `QApplication`，安装 Fluent 翻译器，创建并运行 `MainWindow`。不要在模块导入时创建窗口或进入事件循环；继续保留 `main()` 和 `if __name__ == "__main__"` 保护。

### `app/common/config.py`

定义全局 `cfg`。当前配置项：

- `ehViewerMangaRoot`：漫画下载根目录，支持本地、映射盘或 UNC 路径；画廊索引固定使用自有数据库，不再配置外部数据库路径。
- `libraryFolders`：其他图片/视频使用的本地、映射盘或 NAS/UNC 媒体目录。
- `mangaPageSize` / `mangaSortOrder`：本地资源每页数量及时间倒序、时间升序或当前分类/归类的自定义顺序；默认时间倒序。
- `mangaPrimaryLabelFilter`：上次选择的分类；默认 `__none__`，即未分类。
- `mangaSearchHoverEnabled`：本地资源搜索按钮悬停自动展开开关；搜索词为空且鼠标离开搜索区域后自动收起。
- `searchHistoryLimit`：本地与在线资源共享搜索历史的保存上限，只允许 5/10/15/20，绝不超过 20；降低后即时裁剪自有数据库。
- `searchShortcut` / `tagSidebarShortcut` / `backShortcut`：展开本地资源搜索栏、切换标签栏和返回上一级的全局快捷键，均使用按键捕获设置。
- `onlineEhSite`：在线资源默认站点，支持 `ehentai`、`exhentai`、`nhc` 与 `nhn`，界面显示为 EH、EXH、NHC、NHN。
- `onlineEhCookie`：用户自行提供的完整 EH Cookie；裸 token 按 `igneous` 兼容。该值仅存于被忽略的本机配置 JSON，不得输出到日志或提交。
- `onlineNhcCookie` / `onlineNhnCookie`：NHC 与 NHN 各自的完整 Cookie，按来源原样注入独立 Session，不得套用 EH 裸 token 的 `igneous` 兼容，也不得输出到日志或提交。
- `onlineEhProxyMode` / `onlineEhManualProxy`：在线 provider 使用系统代理、直连或手动 HTTP(S) 代理；手动地址仅在 `manual` 模式消费。
- `onlineEhRequestTimeout`：传给在线 provider 的单次请求超时，支持 10/20/30/60 秒。
- `onlineEhViewMode`：在线结果的默认视图，支持 `card`、`list` 与 `extended`；`card`/`list` 共用站点 Compact 数据，只有切入或切出 `extended` 时才分别通过原列表页 `inline_set=dm_l` / `inline_set=dm_e` 同步远端账户模式并重取字段。
- `onlineEhThumbnailConcurrency`：在线封面专用线程池的最大并发请求数，支持 1/2/4/6/8/12，默认 6。
- `onlineEhDownloadConcurrency`：同时运行的画廊下载任务数，支持 1–3，默认 2；修改后即时更新下载线程池，运行时也必须硬限制为最多 3。
- `onlineEhDownloadThreads`：所有活动画廊共用的页面图片下载线程数，支持 1–6，默认 6；它不控制画廊任务数，修改后即时更新进程级页面调度器。
- `onlineEhDownloadLabel`：新建在线下载条目的默认 EhViewer 分类，空字符串表示未分类；设置页选项必须来自当前 `DOWNLOAD_LABELS`，已有 GID 继续下载时不得覆盖原 `LABEL`。
- `onlineEhMarkerTitleRules` / `onlineEhMarkerTagRules`：在线画廊深红边框标记规则。标题规则使用不区分大小写的包含匹配；Tag 规则对完整 `namespace:tag` 或裸 tag 名执行不区分大小写的精确匹配，空项与大小写重复项必须归一化去重。
- `onlineEhThumbnailCacheHours`：在线封面本地缓存有效期，支持 1 小时至 30 天，默认 7 天。
- `readerBackgroundColor`：阅读画布背景色。
- `readerPageDirection`：从左向右、从右向左、从上向下或从下向上的下一页按键方向。
- `readerImageLoadSize`：适应窗口、适应宽度或原始大小的初始显示模式。
- `readerScrollShortcut`：单张长图向前滚动一屏的快捷键，到底后进入下一页。
- `readerNextMangaShortcut`：分类/归类阅读序列中随时进入下一本的快捷键，默认 `Ctrl+PageDown`。
- `readerAutoPageEnabled` / `readerAutoPageInterval`：自动翻页开关与秒数间隔。
- `micaEnabled`：窗口 Mica 效果。
- `dpiScale`：Qt 缩放比例，需要重启。
- `language`：语言选择，需要重启；目前业务界面尚未真正国际化。
- `themeMode` 和 `themeColor`：继承自 QFluentWidgets 的 `QConfig`。

配置通过统一路径层加载运行根目录下的 `data/config.json`，首次启动会写出完整默认配置。业务数据库不能塞入此 JSON；媒体条目、进度、收藏和扫描状态以后应进入 SQLite。

### `app/repositories/user_library_repository.py`

RSViewer 自有 SQLite 使用 `PRAGMA user_version` 执行可重复迁移。版本 1 的复数标签表在界面上已演进为播放列表，版本 2 新增阅读进度，版本 3 保留历史分类覆盖兼容，版本 4 增加播放顺序与树状归类，版本 5 新增收藏与浏览历史，版本 6 新增 `eh_tag_namespaces` 与 `eh_tags` 保存 EH 标签翻译快照，版本 7 新增 `search_history(query, searched_at)` 保存本地/在线共享的最近搜索，版本 8 新增 `online_gallery_downloads` 与 `online_gallery_comments`，以唯一 GID 保存在线任务状态、额外元数据、评论快照和断点进度，版本 9 新增 `gallery_sync_records` 与 `gallery_sync_comments`，将普通本地画廊的源站元数据/评论同步与下载任务状态解耦。资源页分类、在线下载和本地详情“同步信息”与其他 RSViewer 状态都在同一数据库事务边界内。删除播放列表依靠外键清理成员；删除归类节点会级联删除子树和关联。`page_index` 始终是零基索引。

版本 10 新增 `gallery_update_tasks`，以源 GID 唯一保存画廊版本更新的目标、文件夹、checkpoint、页进度、状态和错误。它只用于快速列出管理任务，目录 `new.json` 和实际文件仍是崩溃恢复依据。更新完成的 GID 迁移必须在同一自有数据库事务内处理分类、播放列表、归类、收藏、历史和阅读进度，不得留下指向旧 GID 的孤立关联。

版本 11 为 `online_gallery_downloads` 新增 `download_mode`，区分 `standard`、`original_direct` 与 `original_local`；新增 `gallery_original_states` 持久化原图画廊属性、下载断点和 `staged`、`replacing_base`、`replacing_original`、`active`、`cleaning` 等文件操作阶段。原图属性不得只从目录猜测；应用启动须把中断的原图下载恢复为 paused，替换与清理则依据持久阶段和实际文件继续。

版本 12 新增 `gallery_trash`，保存软删除画廊的标题、目录、封面、页数、状态及 `DOWNLOADS`、`DOWNLOAD_DIRNAME`、`Gallery_Tags` 精确行快照；版本 13 曾追加删除时使用的数据库与漫画根目录绝对路径。版本 14 通过 `ehviewer_schema.py` 在自有库补齐 EhViewer v7 的 `DOWNLOADS`、`DOWNLOAD_DIRNAME`、`DOWNLOAD_LABELS`、`Gallery_Tags`、`BOOKMARKS`、`HISTORY`、`LOCAL_FAVORITES`、`QUICK_SEARCH`、`FILTER`、`Black_List`、`android_metadata` 全部兼容表；版本 15 把既有回收记录的还原目标统一迁移到当前自有数据库，禁止再写回旧 `eh.db`；版本 16 补齐 EhViewer 的 `IDX_Black_List_BADGAYNAME` 显式索引。`moving`、`trashed`、`restoring`、`deleting`、`failed` 每个阶段都必须先落库；启动时把中断阶段改为 failed，用户可重试还原或永久删除。软删除期间保留播放列表、归类、收藏、历史、进度及原图状态等自有关联以便还原，但计数和管理任务不得展示该 GID；永久删除文件成功后才事务清理全部自有关联。

版本 17 为 `gallery_original_states` 新增 `fallback_to_standard`，表示原图下载画廊中至少存在基础图回退页；版本 18 新增 `page_modes_json`，按页持久记录 `original` / `base` 类型。两者必须随 GID 晋升、回收站保留、多窗口刷新和中断恢复一起迁移，不能只写入任务错误或根据目录猜测；旧 v17 记录迁移时按已完成前缀兼容为基础图。版本 19 为下载评论和同步评论新增 `gallery_links_json`，持久保存评论正文中经严格校验的站内画廊 GID、token 和链接文字，使本地详情与重启后的评论仍能应用内跳转。

版本 20 为 `manga_reading_progress` 新增 `completed` 与 `cleared`：到达末页后 `completed` 永久保留，直到用户从实际右键画廊或详情操作栏显式清空；清空必须留下 `cleared` 墓碑，禁止旧 `.ehviewer` 页码再次导入。版本 21 新增单例 `latest_similar_search`，只保存最新一次详情标题选中文字、本次源 GID、结果 GID 顺序和时间；新查询完整覆盖旧查询。版本 22 为阅读进度新增 `started`，区分 `.ehviewer` 默认 `00000000` 起始页与用户实际在 RSViewer 打开第 1 页；迁移时未完成的零页索引旧记录标为未开始，第 2 页以后及已读完记录保持已开始。阅读状态与相似查询都必须随 GID 晋升和永久删除迁移，软删除期间保留。

版本 23 新增 `manga_custom_sort_rules` 与 `manga_custom_sort_entries`，每个分类或归类节点最多保存一套 GID 顺序；未分类使用分类作用域 `__none__`。删除分类/归类节点、永久删除画廊和 GID 版本晋升必须同步清理或迁移顺序。分类或归类成员真正离开对应集合时移除其顺序项；新加入或重新加入的成员不立刻改写规则，而是在已保存成员之后按 `added_time DESC, gid DESC` 展示。

版本 24 新增独立 `gallery_sources(local_gid, source, remote_id)`，来源只允许 `ehentai`、`exhentai`、`nhc`、`nhn`。兼容 `DOWNLOADS` 不承载该字段；历史记录优先读取同步记录、其次下载记录，无法判定时默认为 EXH。新增兼容下载行由触发器先登记 EXH，取得明确来源的同步或下载事务随后原位纠正。软删除保留来源，永久删除清理来源，GID 晋升同步迁移。

### `app/common/style_sheet.py`

把 RSViewer 自定义样式注册到 QFluentWidgets 的样式管理器。设置页、阅读设置弹窗、漫画详情标签、在线资源页和相似画廊窗口分别注册 `StyleSheet.SETTING_INTERFACE`、`StyleSheet.READER_SETTING_DIALOG`、`StyleSheet.MANGA_DETAIL_INTERFACE`、`StyleSheet.ONLINE_MANGA_INTERFACE`、`StyleSheet.SIMILAR_GALLERY_BROWSER_WINDOW`，`setTheme()` 会自动重新加载对应的 light/dark QSS。阅读设置弹窗和相似画廊浏览器都是独立窗口，不能依赖主窗口背景透传，必须分别定义浅色与深色实体背景；相似浏览器必须使用 Fluent 顶层窗口和标题栏，内部详情继续复用共享 `MangaDetailInterface`。在线资源滚动区、viewport、内容容器、结果卡片和封面占位也必须保持主题透明背景与对应明暗配色。

新增页面样式时，应：

1. 在 `StyleSheet` 枚举中增加名称。
2. 同时创建 `app/resource/qss/light/` 和 `dark/` 两份同名 QSS。
3. 在页面初始化、对象名设置完成后调用 `.apply(self)`。
4. 实际验证两种主题；避免用内联 QSS 固定文字颜色或背景色。

PyInstaller 构建必须使用仓库根目录的 `RSViewer.spec`。自定义样式保持文件形式并由 spec 将完整 `app/resource/qss` 目录复制到 bundle 内的同一相对位置，使 `StyleSheet.path()` 在源码运行与 `_MEIPASS` 解包环境中使用相同路径。新增 QSS 不需要逐文件登记，但不得把 spec 的 `datas` 恢复为空。`data` 及旧迁移来源 `app/data`、`app/config/config.json`、`app/cache` 都不得作为构建数据打入 exe；冻结环境的可写状态必须消费 `app_paths.py` 提供的 exe 同级 `data` 路径。新增任何可写资源时必须接入该路径层，禁止从业务模块的 `__file__` 或 `_MEIPASS` 派生。

### `app/view/main_window.py`

主 stacked widget 的整页位移动画保持关闭。媒体页已经常驻，切换时只更换当前 widget；不要恢复 Fluent 默认 300ms 动画，否则最大化或全屏窗口会连续重绘透明页面和媒体卡片并明显掉帧。

`SplashScreen` 在主窗口首次 `show()` 前必须显式 `resize(self.size())`，因为它是在主窗口初始 `resize()` 之后创建，不能依赖尚未发生的父窗口尺寸事件。退出时先隐藏窗口并取消下载；下载 provider 的流式响应必须支持由 Worker 主动关闭，未启动任务使用 `QThreadPool.clear()` 清理，不能只设置布尔标记后长时间等待网络超时。

主窗口基于 `FluentWindow`，负责窗口、导航、主题、数据源组合，以及本地资源/收藏/历史之间的共享数据同步。左侧导航不使用树状父子路由：底部“漫画”“视频”两个模式按钮按当前模式切换顶部扁平入口，“新窗口”动作紧邻“设置”上方；漫画模式显示本地资源、收藏、在线资源、历史记录、正在下载、更新管理、整理和回收站，视频模式当前只显示资源，切换模式分别进入本地资源或视频资源默认页。页面和路由对象保持常驻，只切换导航项可见性，不应因模式切换重新创建页面。可配置的搜索栏与标签栏快捷键使用应用级 `QShortcut`，会先切回漫画模式的本地资源再展开搜索或切换标签侧栏，并随配置即时更新。在线资源路由使用 `OnlineMangaInterface`，不得在主窗口或 GUI 线程直接执行网络请求。收藏与本地历史不得各自重新执行大型库加载，而应消费 `LocalMangaInterface.libraryLoaded` 的同一批元数据。打开详情和阅读时由主窗口即时更新历史顺序，并在单线程后台队列保存。

“新窗口”必须创建同一进程内的完整 `MainWindow`，并复用单一 `MultiWindowCoordinator`。协调器持有全局下载、画廊更新、原图文件操作、整理和回收站线程池，聚合各窗口活动任务与速度；下载总并发硬上限 3，更新总并发硬上限 1，同一 GID 的开始/暂停/删除必须路由到实际任务所有者。只有第一个窗口可执行启动中断恢复，后续窗口不得把正在运行的任务误标为暂停。收藏、历史、阅读进度、标签变更、自定义排序、资源重载、下载、更新、整理、回收站和数据源切换通过进程内事件总线同步；接收窗口更新 UI 或从共享数据库重载时不得再次回传同一事件形成循环。关闭一个窗口只能取消该窗口拥有的 Worker，禁止 `clear()` 共享线程池而影响仍打开的其他窗口。

资源浏览、画廊详情和阅读器是三个独立刷新层级。资源浏览页只管理当前查询集合、分页和卡片摘要：只有集合成员真正变化时才更新容器，新资源增量插入，下载/阅读摘要变化时只按 GID 更新现有卡片；下载页数、速度、详情预览和阅读图片不得触发资源页 `reload()`、重建当前页或改变滚动位置。下载管理页只有可见时才重建任务卡。窗口首次整库加载只初始化本窗口，禁止广播整库快照；跨窗口普通下载生命周期使用单 GID 事件，只有分类/归类集合变更、回收站增删或数据源切换等真正改变集合结构的操作才允许显式整库重载。

分类和树状归类的持久化事实是 SQLite 关系表；旧播放列表表及 Repository 方法仅为已有数据库兼容保留，不再进入 UI、搜索或阅读序列。本地资源加载后必须构建单一 `MangaClassificationIndex`，以“类型 -> 标签 -> GID 集合”保存分类和归类直接成员关系。“显示全部”通过同一索引的 `all_gids()` 获取全集，分类直接取对应集合，父归类在查询时合并全部后代节点集合。单本增量刷新或分类操作成功后必须同步 `upsert`/重建索引，界面筛选不得再分别扫描每个 `MangaItem` 的分类字段来维护另一套判断。

分类、未分类和每个归类节点各自最多保存一套自定义顺序；“显示全部”、收藏、历史等集合页不支持。没有规则时排序下拉框显示 `+`，新建对话框默认按 `added_time DESC, gid DESC` 罗列完整成员；保存后 `+` 替换为“自定”，且仅选中“自定”时显示编辑按钮。对话框支持整行拖动、多选后保持相对顺序整体上移/下移或移到首尾；列表级操作支持随机打乱、按显示标题不区分大小写且识别数字的自然升序和整表倒序。排序操作使用带中文 tooltip 的紧凑图标按钮，只修改对话框临时顺序，确认保存后才持久化。对话框的标题搜索只能定位，禁止过滤、删除或重排列表；输入时选中首个大小写不敏感的包含匹配，上/下图标按钮及搜索框内的 `Page Up` / `Page Down` 在匹配项间循环定位，并显示当前位置和总匹配数。父归类对合并全部后代的去重成员保存独立顺序；新增成员按时间倒序追加到已保存顺序末尾。卡片显示与分类/归类阅读序列必须调用同一排序实现，自定义顺序不消费搜索词或分页。详情标题只对用户实际选中的至少两个有效字符执行大小写不敏感字面量搜索，同时匹配本地英语标题和原标题并排除源 GID。结果按钮只在当前详情等于最近查询源 GID 时显示；进程内只能存在一个非模态相似画廊窗口，新查询必须替换窗口列表、详情页和返回栈。相似窗口中的本地详情必须转发“同步信息”操作，Worker 的进行中状态、失败提示和完成结果必须更新实际发起操作的详情组件，成功后增量更新相似结果行与本地资源项，禁止硬编码到主窗口详情或触发整库重载。详情标题还可把同一段选中文字去除嵌套双引号后作为带引号精确短语，直接在在线资源当前站点搜索。该跳转须独立保存来源详情、既有详情返回栈和分类/归类阅读序列；结果详情先返回结果列表，再由在线页返回来源详情。主动切换到无关路由时清除临时返回状态。

主窗口必须先初始化唯一的 `UserLibraryRepository`，再把其 `database_path` 传给本地数据源、下载、更新、整理、原图与回收站 Repository；禁止从配置或旧 JSON 键重新接入外部 `eh.db`。随后加载已导入的 EH 标签，构造一个全局共享的 `EhTagSearchIndex`，并创建单一 `SearchHistoryService` 供本地、收藏、本地历史和在线页面共享；不得让各页面重复读取四万多条标签或维护互相独立的搜索历史。标签仓库更新由 `scripts/import_eh_tags.py` 显式执行，主程序启动只加载 SQLite 快照，不扫描 Markdown。

`SystemThemeListener` 是持有资源的后台监听器，关闭窗口时必须 `terminate()` 和 `deleteLater()`。

### `app/view/media_interface.py`

仅为在线历史和视频等尚未实现路由提供轻量占位页。在线资源已由独立界面实现，收藏与本地历史使用 `LocalMangaInterface` 的集合模式。各页面通过稳定且唯一的 `objectName` 作为 Fluent 导航 route key。

### `app/sources/eh_online_source.py` 与 `app/view/online_manga_interface.py`

在线来源是 EH、EXH、NHC、NHN 四选一，并分别维护 `OnlineSiteState`。NHC/NHN 由 `nh_online_source.py` 使用独立 `requests.Session`，分别注入自身 Cookie；首页和 NHN 搜索直接请求服务端 HTML并由 BeautifulSoup+lxml 本地解析，NHC 有关键词时复用站点前端实际调用的同域数据请求并严格校验 JSON 结构。两站支持搜索、严格正整数页码、元数据详情、预览、在线阅读与基础图断点下载，仍禁止日期定位和画廊 URL 跳转；因为没有独立原图规格，不提供原图下载。NHC 结构化搜索必须把 `tag:`、`artist:`、`group:` 等名称通过对应集合端点解析为 ID 后再提交画廊筛选，不能把 namespace token 当普通标题；NHN 使用其 HTML 搜索的完整 namespace。EH/EXH 继续使用响应 URL 游标而不显示数字页码。在线与本地卡片右上方共享来源标签：EH/EXH 暗红、NHN 红、NHC 黄；NHC/NHN 必须用稳定本地 ID 与 `gallery_sources` 远程编号映射，不能因与 EH 相同的数值 ID 误显示或覆盖本地下载状态。

完整下载、单页补图、在线阅读、版本更新和 provider 页码校验必须统一消费 `OnlineGallery.preview_page_size`，不得各自固定按 20 张换算；只有旧记录未保存容量时才回退为 20。收齐全部全局 page token 后必须停止继续请求尾部预览页。

在线详情必须沿用当前列表页 provider 的同一 `requests.Session`，直接 GET 对应 `/g/{gid}/{token}/` HTML，并在后台解析完整元数据、标签、当前账户实际返回的 20/40 张缩略预览及 `.c1` 评论。详情页 `#gnd` 中同站 `/g/{gid}/{token}/` 链接表示当前画廊存在更新版本，是判断“旧父画廊”的唯一依据；`Parent` 字段只表示当前画廊的上游版本，不得单独据此反向判旧。评论正文的 `<a href>` 只有解析为精确 EH/EX HTTPS `/g/{gid}/{token}/` 地址时才转换为 `OnlineGalleryLink`，相对链接先基于当前画廊 URL 解析，外站、搜索页、单图页、缺 token、带 query/fragment 的目标都不能成为应用内按钮。后续预览分页直接请求画廊 `?p=N` HTML；EH/EX 的多个预览可能共享同一张横向精灵图，必须保留每个节点的 CSS `width`、`height` 和 `background-position`，下载共享图片后按各自区域裁剪，不能把整张精灵图直接交给预览控件。每个预览还必须保留 `/s/{page-token}/{gid}-{page}` 中的 page token，下载任务据此生成 EhViewer `VERSION2` sidecar。在线阅读和基础下载先请求该 `/s` 单图 HTML，再读取其中 `#img` 的站点展示图；原图下载从同一单图 HTML 提取 `fullimg` 链接。原图链接只允许当前 EH/EX 站点，并必须严格匹配当前 GID 与一基页码；合法单图页不存在链接时必须抛出专用 `OriginalImageUnavailableError`，供原图任务持久降级，网络、超时、无效页面或异域链接仍是普通失败，绝不能误判为可降级。所有链路均不调用 API。请求前严格校验当前站点、GID、token、page token、单图页页码及 EH/EX/ehgt/H@H 图片主机；当前评论区只读，不实现发表评论或投票。

`EhOnlineProvider` 是在线爬虫的稳定边界。UI 将 `OnlineGalleryQuery(keyword, seek_date, cursor, filters)` 交给 provider；基类先调用 `fetch_page()`，再调用可覆盖的 `filter_items()`，最后返回统一的 `OnlineGalleryPage`。EH/EX 列表没有稳定的数字页码，领域查询和在线列表 UI 均不得伪造“第 N 页”；“上一页”“下一页”按钮必须分别且只根据本次 `OnlineGalleryPage.previous_cursor` / `next_cursor` 是否存在来启用，点击后直接请求对应响应游标。尤其日期 Seek 结果不是第一页，若响应同时提供新旧两个方向就必须同时允许翻页。`seek_date` 为空或严格使用 `YYYY-MM-DD`，由 `eh_tool_refactored.py` 的原生 Seek 定位；同时存在关键词时必须先加载关键词列表建立含 `f_search` 的导航上下文，再执行 Seek。`create_eh_online_provider()` 按站点创建 provider：EH/EXH 返回只适配根目录 `eh_tool_refactored.py` 的 `RefactoredEhOnlineProvider`，NHC/NHN 返回 `NhentaiOnlineProvider`。EH 适配器沿用 `requests.Session`、HTML 列表页、`f_search`、页面生成的 next/prev URL 以及 BeautifulSoup+lxml 的多显示模式解析，不得擅自替换成其他 API 或站点接口。脚本输出的 gid/token、URL、标题、分类、封面、上传时间、页数、上传者、评分、源显示模式和分命名空间标签转换为领域模型；评分必须从文本或 EH 半星精灵图的 `background-position` 转换为 `0–5` 数值，绝不能把 CSS 样式传给 UI。缩略图仍通过同一会话下载。

登录 Cookie 通常不包含决定每页 25/50/100 条结果的 `hath_perks` 能力 Cookie；`RefactoredEhOnlineProvider` 必须在后台首次搜索或切换远端显示模式前用同一会话读取一次当前站点 `uconfig.php`，再请求列表，使服务器遵循账户的 Search Result Count。该初始化不得在 provider 构造函数或 GUI 线程执行，失败时应降级继续普通列表请求，不能阻断在线浏览；已有能力 Cookie 或同一 provider 后续请求不得重复初始化。禁止在客户端截取 25 条或拼接两次游标响应伪造 50 条，否则会破坏 prev/next 边界。

`EhOnlineSettings` 统一提供站点基址、规范化 Cookie、代理模式/映射和请求超时。EH Cookie 可粘贴完整 `ipb_member_id=...; ipb_pass_hash=...; igneous=...` 字符串，单独裸 token 按 `igneous` 兼容；NHC/NHN Cookie 仅清理可选的 `Cookie:` 前缀和换行后原样使用。所有 Cookie 都从 settings 的 `repr` 中排除。系统代理由标准库发现；Windows 把单一无 scheme 代理端点展开为同地址的 `http://` 与 `https://` 时，必须规范化为同一个 HTTP CONNECT 代理供 `requests` 使用。直连关闭 session 环境代理，手动模式验证并补全 HTTP(S) URL。源码中不得硬编码 Cookie 或本机代理；`eh_tool_refactored.py` 的全局默认凭据和代理必须保持为空，运行值仅由设置注入。列表翻页 URL 只允许当前来源主机，缩略图必须按 provider 限制到其可信 HTTPS 图片主机。

`OnlineMangaInterface` 为四个来源分别维护独立的 `OnlineSiteState` 内存容器，保存搜索词、当前游标、滚动位置及最近 64 个页面结果，EH/EXH 额外保存日期定位。切换站点先恢复其容器，容器为空才请求该站首页；工具栏“刷新”始终绕过页面内存缓存重取当前结果。搜索栏旁的日历按钮和画廊网址输入只对 EH/EXH 启用；网址必须通过统一的严格 EH/EX 画廊地址解析，取得 GID/token 后忽略输入地址所属站点，按当前 `_current_site` 重新生成目标 URL，并复用当前站点 provider 打开详情。结果支持 `card`、`list` 与 `extended` 三种视图，顶部图标按钮按该顺序循环且图标表示点击后的目标布局。默认 Card 使用 229px 固定宽度、367px 最小高度与 241px 封面高度，响应式列数必须复用同一尺寸常量；`list` 是与本地标题列表同为 116px 高的无封面行，只展示标题、分类、上传者和页数；Card/List 只重建当前内存结果，不得请求封面或切换远端模式。切入或切出 Extended 才由 `OnlineSearchWorker` 调用 `set_display_mode()`，EH/EXH 沿用 `eh_tool_refactored.py` 会话请求 `inline_set=dm_l/dm_e` 并重取当前关键词、日期或游标结果。当前结果已成功加载的封面必须跨卡片重建复用，不得重新读取磁盘或下载。EH/EXH 三类卡片右键菜单都提供“下载”，只允许鼠标左键触发详情；每张卡片通过 GID 判断本地是否已有画廊，命中时在左上角显示绿色下载图标。设置页画廊标记规则命中标题或列表元数据中已有 Tag 时，三类卡片均绘制深红边框；规则修改后所有窗口的当前卡片必须原地刷新，不得重新请求列表或封面。默认 Card 显示大封面、类型/评分、悬停滚动长标题、发布时间/上传者/页数；Extended 使用横向信息行和可换行标签，Minimal/Minimal+ 标签为空时显示缺省说明。类别色块统一使用 EH ct1–cta 渐变。列表请求使用独立搜索线程池，封面按单项任务提交到由 `onlineEhThumbnailConcurrency` 控制的专用线程池；`OnlineThumbnailCache` 按站点保存过期磁盘缓存。

在线列表点击下载后必须立即在内存中占用该 GID 并标绿；SQLite 任务写入、兼容表更新、目录创建、封面落盘和本地条目读取必须进入 `MultiWindowCoordinator` 持有的共享单线程预登记池，禁止在 GUI 线程执行。预登记成功后再获取完整详情并进入统一下载 Worker，详情请求和下载线程排队期间均须保留可恢复任务记录。

### `app/repositories/ehviewer_download_repository.py` 与 `app/workers/online_gallery_download_worker.py`

列表右键或详情点击下载时必须在提交详情 Worker/下载 Worker 前同步完成预登记：先保存当前已知的标题、GID、token、分类、页数等扩展任务和 EhViewer 兼容行，确定并创建目录，有可解码封面数据时原子写入 `.thumb`；不得为不完整页面 token 伪造 `.ehviewer`。在线绿色图标消费本地资源 GID 与全部未完成下载任务 GID 的并集，并通过 `downloads` 事件跨窗口同步，因此任务即使仍在排队或详情获取失败，也保持“已加入下载”的明确标记。后续 Worker 必须复用同一目录并以完整详情覆盖补齐元数据、评论、sidecar 和图片。

在线下载的画廊任务由所有窗口共享的专用 `QThreadPool` 执行，`onlineEhDownloadConcurrency` 的 1–3 只表示同时运行的画廊 Worker 数量，任何配置或旧值都不得让运行时超过 3；例如配置为 1 并加入 10 个画廊时，只允许 1 个画廊执行，另外 9 个必须排队。页面图片请求使用 `MultiWindowCoordinator` 持有的单一 `GalleryPageDownloadScheduler`，所有活动画廊公平共用 `onlineEhDownloadThreads` 配置的 1–6 个线程；不得给每个画廊各建一组页面线程，也不得把页面线程数误用为画廊任务数。只有一个活动画廊且页面线程配置为 6 时，该画廊应能同时下载最多 6 页。任务顺序固定为：自有库 EhViewer 兼容元数据、RSViewer 扩展任务和评论、封面、全部预览/page token、`.ehviewer`，最后并发下载缺失图片；逐页文件仍独立原子写入，数据库断点、进度信号和最终状态由画廊 Worker 串行汇总。下载管理页提供单项及“全部开始/全部暂停”，批量开始只提交当前未活动记录，批量暂停必须覆盖详情准备、sidecar 准备和图片下载阶段。Worker 汇总各活动页面线程的速度，任务卡片显示画廊总速度，标题区再汇总全部活动画廊速度；速度仅存内存，暂停、失败、完成或删除时清除，不得写入 SQLite。`EhViewerDownloadRepository` 只能事务 upsert 既有 `DOWNLOADS`、`DOWNLOAD_DIRNAME`、`Gallery_Tags` 内容，必须保留已有 `LABEL`、`TIME` 与 `ARCHIVE_URI`，并在写前校验所需表列；兼容表 DDL 只属于 `UserLibraryRepository` migration，业务 Repository 不得执行 DDL。新 GID 使用任务记录中的 `download_label` 写入 `LABEL`，非空值必须先确认存在于 `DOWNLOAD_LABELS`，无效分类应在创建目录前失败；已有 GID 无论默认设置如何都保留原 `LABEL`。本地详情的元数据同步只更新已有 GID 的兼容字段与 `Gallery_Tags`，必须保留 `STATE`、`LEGACY`、`LABEL`、`TIME`、`ARCHIVE_URI`，且不得创建下载目录或下载页面图片。目录优先复用 `DOWNLOAD_DIRNAME`，否则使用现有同 GID 前缀目录，再否则按 EhViewer 的 `gid-title` 规则清理 Windows 非法字符。

图片文件使用一基、八位十进制页码；`.ehviewer` 使用 `VERSION2`、gallery token、预览分页参数和每页 page token，不能把 page token 写入数据库。所有文件先写同目录临时文件再原子替换；只有新图片验证可解码并成功替换后才可删除同页旧后缀。开始或继续任务时必须校验现有图片，跳过有效页并重下缺失/损坏页；单页网络请求短暂失败最多重试三次，取消、失败和应用关闭均保留目录、自有任务和评论，下次从断点继续。逐页落盘事件只允许更新对应预览格、阅读缓存、下载页数和速度，不得重绘详情元数据或重建整个当前预览分页。若中断发生在兼容 `DOWNLOADS` 条目、下载目录或完整 `.ehviewer` 创建之前，继续任务必须从 RSViewer 自有下载记录恢复站点与 GID/token，后台重新请求详情，然后重新执行兼容元数据、目录和 sidecar 初始化，不能依赖本地资源列表先出现该 GID。兼容 `DOWNLOADS.STATE` 使用 EhViewer 的 downloading/finish/failed 数值，扩展任务表区分 queued/downloading/paused/failed/completed。预登记、外部注册、暂停、失败、完成和任务删除都应按单个 GID 读取并增量 upsert 本地条目，通过 `library_item` 事件同步其他窗口；正常路径不得整库 `reload()`，不得清空搜索、切到显示全部、改变当前分类/归类、页码或滚动位置。只有单条读取失败时可退回普通刷新。

原图下载继续复用同一下载线程池和逐页原子写入规则。全新在线画廊使用 `original_direct`，下载内容直接写根目录并在完成后进入 `active`；已经存在本地目录时必须使用 `original_local`，只写 `original/`，不得覆盖根目录基础图或重写 `.ehviewer`。完整暂存后详情提供标准/原图预览与阅读来源切换。每页先按原图接口请求；只有合法单图页明确不存在原图目标时，才先把该页模式持久化为 `base`，随后下载基础图。网络、超时、无效页面或异域链接必须继续按失败/重试处理，不能误判为基础图回退。断点恢复只跳过已校验且已有页模式的文件；未知模式文件必须重下，避免崩溃发生在文件落盘与页模式 checkpoint 之间时误认内容。`original_local` 的原图和基础回退页都暂存于 `original/`，完成后保持 `staged`，允许正常预览和原图替换。流式图片请求的连接与响应体无数据超时均不得超过 15 秒；超时必须关闭 response、清除陈旧速度并进入统一的最多三次重试，不能让任务只能依靠手动暂停恢复。列表和详情等非流式请求仍使用用户配置的请求超时。`OriginalGalleryFileWorker` 先校验 `original/` 的完整下载集合，再持久化 `replacing_base` 并把根目录数字页移到 `history/del/`，随后持久化 `replacing_original` 并把暂存内容提升到根目录，最终标记 `active`；每次恢复都以数据库阶段和实际文件共同判断。压缩图备份绝不自动删除，只有用户二次确认后进入 `cleaning` 并清理精确的 `history/del/`。非 active 的原图阶段不得启动画廊版本更新；active 原图下载画廊必须保留 `image_mode=original`。更新时按 page token 迁移已存在页的类型，新页面继续优先调用原图接口，缺失原图的单页回退基础图并写入目标 `target_page_modes` checkpoint。

下载 Worker 在兼容条目、目录和封面完成后必须上报本地注册事件，主窗口据此按 GID 增量 upsert 本地库并即时更新在线卡片标记；新条目不符合当前分类或搜索时不得重建当前卡片页。完整 `.ehviewer` 写入后还须单独上报 sidecar 就绪事件，让已打开的同 GID 本地详情重新读取总页数和 page token。续传或补齐任务每成功原子写入一页后，Worker 必须上报真实页索引和文件路径；当前本地详情把路径增量合并到 `MangaItem` 并只替换命中的预览格，已打开的本地阅读器同步更新对应全局页码。不能只更新进度数字或只在最终完成时重载资源列表，否则本地列表、在线标记、详情与阅读器会分别持有不同阶段的旧状态。图片响应流约每 1 秒上报一次速度并在页完成时补发末段速度；活动任务尚未取得首个有效值时显示“测速中”，取得后在页面切换和重试间隙保留最近一次有效速度，禁止因内部汇总短暂归零而反复切回“测速中”。暂停、失败和完成时再由主窗口清除速度。Worker 在兼容元数据初始化之前暂停或失败时，也必须更新已提前创建的 RSViewer 自有任务记录，不能让非活动任务残留为 queued/downloading。

### `app/workers/gallery_update_worker.py` 与更新管理

本地详情同步保存的 `newer_gallery_urls` 跨重启恢复“更新到最新”入口。用户触发后，RSViewer v10 `gallery_update_tasks` 作为更新管理的快速索引，画廊目录内原子写入的 `new.json` 作为文件系统 checkpoint。`status` 只是提示，每次恢复必须重新校验实际 sidecar、标记名和正常名图片；全局同时只允许一个更新 Worker，其他已开始任务必须保持 `queued`，并在当前任务完成、失败或暂停后按入队顺序自动提交。

更新顺序固定为：解析并固定最新版本、原子写 `new.ehviewer`；把旧图片改为八位页码加旧索引和十位 page token 的标记名；按新 sidecar 的 token 顺序重排，新版删除的页移入 `history/removed/{source-gid-token}`；仅用 `.part` 下载缺失页并校验后原子改名；验证完整集合后先记录 status 5，再幂等恢复标准页码名；最后归档旧 `.ehviewer`、晋升 `new.ehviewer`，并在同一个自有数据库事务中迁移 `DOWNLOADS`/`DOWNLOAD_DIRNAME`/`Gallery_Tags` GID 与分类、播放列表、收藏、历史、进度关联，同时把 `DOWNLOADS.TIME` 更新为完成时刻。page token 可能重复，标记、重排、验证和进度映射必须按出现次数处理，不能用 token 单值字典或首次索引；status 1–4 恢复时须幂等重跑旧页标识补齐与实际文件重排，不能因 checkpoint 已前进而只做校验。status 6 是完成事实，不能再被末尾进度信号写回运行态，启动恢复须把旧异常记录归一为 completed。完成本地原图替换时也刷新该排序时间。目标文件存在时绝不覆盖，冲突必须失败保留现场。更新管理允许删除运行中或非活动任务；运行中必须先交给实际所有者取消，删除只清理 `gallery_update_tasks` 索引，禁止删除目录图片、`new.json`、`new.ehviewer` 或其他恢复文件。

原画廊未完成时任务先进入 `waiting_download`，复用现有断点下载补齐后自动更新。任务未完成时详情的阅读、同步、整本/单页下载均禁用，阅读序列跳过该 GID。应用退出先 cancel provider 响应并把任务恢复为 paused；“更新管理”页单独展示进度、速度、checkpoint 和错误。

### `app/services/library_organizer.py` 与整理页

整理页只在用户点击右上角扫描按钮后工作，扫描和文件/数据库操作均使用专用单线程池，不得在 GUI 线程枚举 NAS 目录。扫描范围严格限制为 `ehViewerMangaRoot` 的直接实体子目录：排除 `DOWNLOADS` 已登记的 GID、`DOWNLOAD_DIRNAME` 已登记的目录名，以及自有 `gallery_trash` 中的 GID/目录名，不得把软删除画廊误报为孤儿；不递归进入画廊内部的 `original/`、`history/` 等目录。每个候选目录解析 `VERSION2` `.ehviewer` 的 GID、gallery token、总页数和完整 page token；缺失或损坏 sidecar 的条目可以展示和删除，但不得同步。

整理页使用与本地资源一致的响应式大封面卡片网格，卡片左上角常驻复选框并支持全选，右键“同步到数据库”和“删除本地资源”作用于当前选择集合。同步前必须再次验证目录仍是根目录直接子目录且不是符号链接，并拒绝已有 GID、目录名或不同路径残留映射冲突；允许在同一事务内修复 `DOWNLOADS` 已丢失但同 GID/同目录的 `DOWNLOAD_DIRNAME` 残行。同步不得创建、移动或覆盖图片目录，只按原目录名在自有库事务中插入 `DOWNLOADS`、`DOWNLOAD_DIRNAME`、`Gallery_Tags` 及 `online_gallery_downloads`/同步记录。同步阶段必须逐页验证文件可解码，完整有效页集合写 finish/completed，不完整或损坏集合写 failed/paused 以便继续补齐；任一写入失败必须整体回滚。删除必须二次确认并调用 Windows 回收站，禁止直接永久递归删除；所有操作结束后自动重扫，成功同步后刷新本地资源。

### `app/services/gallery_trash.py` 与回收站

本地资源、收藏和本地历史卡片右键“移入回收站”作用于当前复选集合。执行前必须拒绝正在下载、更新、原图替换、单页补齐或元数据同步的 GID。软删除不移动目录、不删除图片：先把三张兼容业务表的完整列名和值保存到 `gallery_trash`，再在同一个自有数据库事务内精确核对并删除这些行；不得仅保存当前已知列，也不得覆盖并发产生的新记录。还原要求原目录仍存在且 GID/目录名无冲突，以保存的原始列和值逐项插回自有数据库，无需确认。

回收站使用响应式封面卡片，支持常驻复选、全选、工具栏和右键“还原/彻底删除”。彻底删除前必须二次确认；确认后再次保证兼容登记不存在，只允许递归删除记录中漫画根目录的直接实体子目录，拒绝根目录本身、嵌套路径和符号链接，最后清理其余自有关系。任一步失败均保留目录或回收记录及错误，不能假装完成。回收站批处理全进程单线程，操作完成通过多窗口事件总线立即刷新其他窗口。

### `app/view/local_manga_interface.py`

本地资源页标签栏默认隐藏，通过工具栏“标签”按钮展开；其中“分类”“归类”两个面板互斥，各自带新增加号，顶部另有“显示全部漫画”。页面标题必须跟随当前筛选：分类显示所选名称，树状归类显示 `父级/子级/...` 完整路径，“显示全部漫画”显示“本地资源”；收藏和历史集合页继续保留各自标题。新增归类使用一个 Fluent 模态窗口同时填写名称和父级，不得连续弹出原生输入框；数据库写入后只增量刷新归类树及实际被分配的漫画，新增空节点不得重新查询全部漫画关系、重建全量条目或重绘未变化的卡片页。分隔条可拖动但标签栏最多占页面宽度 30%；使用 `FluentSplitterHandle` 提供 7 像素命中区和 1 像素主题色细线，透明度规则应与 `NavigationResizeHandle` 一致，不得恢复 Qt 默认实心手柄。展开、收起或拖动后必须等待 `QSplitter` 几何更新并主动调用卡片重排，不能依赖主窗口 `resizeEvent`。分类为单选并记忆选择，默认未分类；树状归类为多对多。两个树都提供右键删除并必须二次确认；未分类不可删除，分类删除时关联漫画先回到未分类，归类父节点删除会级联整个子树。

搜索栏支持按钮、全局快捷键和可配置的鼠标悬停展开。悬停模式不得主动抢占键盘焦点；鼠标离开搜索按钮与搜索输入区域后延迟检查，只有搜索词为空才自动收起，有内容必须保持。悬停临时展开后点击搜索按钮会切换为常驻并聚焦输入框，再点击一次只解除常驻，鼠标随后移出时恢复悬停收起逻辑；按钮从隐藏状态打开、全局快捷键和“搜索相似画廊”均属于显式常驻。关闭悬停配置后不得响应鼠标进入，显式常驻不受该配置影响。

本地资源、收藏、本地历史和在线资源的主搜索框使用 `EhTagSearchLineEdit`。内存索引同时按英文原始标签与中文译名做包含匹配，标签结果以 `namespace：tag` 和译名上下两行显示；补全插入完整 namespace，多词标签必须加引号，已有缩写输入继续兼容。补全只替换光标所在、引号外由空格分隔的当前条件，不得覆盖前面的条件。本地筛选要把 `o:"full color"` 等缩写还原为 `other:full color` 后匹配；在线搜索框和历史保留用户输入，provider 请求边界调用 `adapt_online_query()`：EH/EXH 把完整 namespace 转为缩写，NHN 把缩写还原为完整 namespace并保留其支持的 `female:`/`male:`，NHC 把缩写还原后将 EH 性别等细分 namespace 归入 `tag:`，两种 NH 来源均移除精确后缀 `$`。候选层先按最近顺序显示匹配的历史输入，再显示标签结果；只有搜索图标、Enter 或页面明确执行搜索时才写历史，不能把逐字输入的中间状态入库。候选层必须复用 QFluentWidgets 的 `CompleterMenu`，限制为最多 8 个可见项并允许滚动；不得直接调用原生 `QCompleter.complete()`，否则会与 `SearchLineEdit` 自带菜单叠加并破坏主题。菜单关闭或搜索框真正失焦时必须取消待显示任务，`PopupFocusReason` 造成的菜单焦点归还不得重新弹出候选或拦截页面其余操作。

网格和列表卡片的右键菜单只保留固定的“在资源管理器中打开”“清空阅读记录”“同步在线信息”“搜索相似画廊”“选择分类…”“选择归类…”和“移入回收站”入口，不得重新把大量标签展开为悬浮子菜单。“在资源管理器中打开”和“清空阅读记录”始终只使用实际右键卡片，即使复选模式已有多项选中也不得扩展为批量操作；本地详情和带本地下载标记的在线卡片提供同一打开目录入口。本地详情操作栏还提供“选择分类”，必须复用同一 `MangaLabelSelectionDialog.CATEGORY` 单选流程，替换当前分类或以“未分类”清除，不得再实现另一套分类写入。“搜索相似画廊”必须在后台针对完整本地库执行，按文件元数据括号、语言/数字版标记、章节号、卷数、话数及前后篇等规则提取作品主标题，再进行保守的长标题模糊匹配；修改搜索词或切换标签退出相似模式。标签入口打开主题化 `MangaLabelSelectionDialog`：提供搜索和可滚动树，分类单选并包含“未分类”，树状归类多选；批量目标成员状态不一致时显示半选，半选保持不变，用户明确勾选或取消后才批量写入。归类窗口保留“新建并添加…”入口。右键不需要先开启复选且不得触发详情。分类更新目标 `DOWNLOADS.LABEL`，归类写 RSViewer 自有库；数据库变更应在 Worker 中执行，多项选择变化应合并为单个后台任务。详情触发的分类成功后同样要增量更新分类索引、当前详情和各共享集合，并通过现有多窗口事件同步。

复选模式提供“全选/取消全选”按钮，作用于当前分类或归类及搜索条件共同形成的全部结果并跨越分页；切换筛选后选中集合必须收敛到新结果范围。卡片右键“同步在线信息”对全部选中项生效，列表批量同步最多同时运行 2 项；未加载 gallery token 的条目必须在同步 Worker 中只读 `.ehviewer` sidecar 补全，不能在 GUI 线程枚举页面。整批完成后只普通刷新一次本地库并保留当前筛选。

`LocalMangaInterface` 还提供收藏/历史集合模式：不启动 `MangaLoadWorker`，隐藏标签栏和添加时间排序，按 Repository 给出的 GID 顺序分页展示共享漫画对象。卡片右键菜单顶部提供收藏或取消收藏，复选时批量生效；收藏状态变更必须同步本地资源、收藏和历史三个视图。

`MangaLoadWorker` 在一次本地库加载中批量读取 RSViewer v8 下载记录和 v9 同步记录，把上传者、发布时间、评分、语言、文件大小、评分人数、可见性和更新版本链接等元数据合并回 `MangaItem`，不得按漫画逐条查询。下载完成刷新携带目标 GID；接受最新 Worker 结果后清除会遮住目标的旧搜索条件、切到显示全部并定位其分页，再通过 `libraryLoaded` 同步收藏、历史和在线下载标记。本地详情“同步信息”完成后只执行普通刷新，必须保留当前分类筛选，不得复用下载完成的目标定位路径。已取消或已被新任务替换的加载结果必须忽略。

同一次批量加载还必须合并原图状态与基础下载任务：`active` 原图下载画廊卡片绘制彩色渐变边框，其他原图阶段绘制暗黄色边框；包含任何 `base` 页的画廊仍使用 active 彩色边框，但必须在左上角以不会被封面子控件遮挡的置顶紫色圆点提示“部分页面没有原图，已使用基础图”。本地和在线卡片右上角统一竖排两个状态点：下载状态白/黄/绿分别表示未下载、下载不完整、下载完成，阅读状态白/黄绿/绿分别表示未读、阅读中、曾经读完；暂停和失败属于下载不完整，混合原图完成画廊仍是下载完成。仅有 `.ehviewer` 默认零页索引且没有 RSViewer 实际阅读记录时必须保持未读；用户实际进入阅读器后即使停留在第 1 页也属于阅读中。列表初始只消费数据库状态，打开详情后用实际文件校验结果纠正，不得启动时扫描整个 NAS。详情右上角对完整全原图画廊显示彩色 `ORIGINAL` 标签，对混合画廊显示金色 `X ORIGINAL` 和紫色 `Y BASE` 标签。绘制只消费内存中的 `MangaItem` 状态，不得让卡片逐条访问数据库或磁盘。

### `app/view/manga_history_interface.py`

历史页面包含“本地历史”和“在线历史”两个互斥入口。本地历史按 `viewed_at` 最近优先展示；在线历史当前只保留明确的占位页面和稳定 route，不得把本地记录混入其中。

### `app/view/manga_reader_interface.py`

漫画阅读器作为主窗口堆叠页运行，普通状态为窗口内阅读，按 `F11` 或工具栏按钮后由主窗口隐藏标题栏与导航并切换全屏，`Esc` 恢复窗口。当前实现单页模式、四向下一页按键、页码跳转、长图滚动和自动翻页。翻页操作行上方保留固定高度的进度条悬停区，滑块默认隐藏，鼠标进入整个底部导航区后只显示、离开后隐藏；只有按住鼠标左键拖动时才按一基页码实时调用现有 `showPage()`，无按键悬停移动不得改变滑块值或页码。页码框、滑块、本地补页和在线总页数必须始终同步，隐藏/显示不得改变阅读画布布局。从当前分类或归类打开画廊时，必须基于该标签完整成员和当前时间/自定模式建立去重阅读序列，不消费搜索或分页；父归类包含全部后代。末页“下一页”打开下一本首页，首页“上一页”打开上一本末页，序列两端正常停止；工具栏“下一本”和 `readerNextMangaShortcut` 可在任意页直接进入下一本。显示全部、收藏、历史、相似结果和直接详情不建立序列。窗口页码在顶部居中，全屏使用独立低透明度页码并默认隐藏控制区，仅上下 12 像素边缘触发对应栏。图片在 `QThreadPool` 后台解码并预读相邻页；加载器按 `GIF87a`/`GIF89a` 文件头识别扩展名错误的 GIF，预载阶段只缓存首帧，成为当前页后再使用 `QMovie` 播放并沿用现有缩放、翻页和全屏画布。点击图片取得焦点后仍须正常翻页。页码变化即时更新列表/详情并防抖保存。本地页必须按数字文件名映射全局页码，不能让中间缺页压缩后续页；具备完整 `.ehviewer` token 的本地画廊点击缺页时只下载该页到本地后展示，纯在线阅读仍只写内存缓存。工具栏显示当前画廊任务的已完成/总页数和实时速度。

在线阅读复用同一阅读器控件和当前 provider，会按当前页、后两页、前一页的顺序惰性解析单图 HTML 与下载展示图；不得一次解析整个画廊。在线页码不写入本地漫画进度表，返回时回到仍保留状态的在线详情页。

`ReaderSettingDialog` 是阅读页工具栏打开的非模态设置面板。它和全局 `SettingInterface` 绑定同一组 `cfg.reader*` 配置项，任一侧修改背景色、方向、图片载入大小、滚动快捷键或自动翻页设置后，另一侧与当前阅读器必须即时更新。弹窗通过 `StyleSheet.READER_SETTING_DIALOG` 注册 light/dark QSS，主题切换时背景必须与 Fluent 控件的文字和卡片风格同步刷新。

### `app/view/manga_detail_interface.py`

该页面由本地与在线画廊共享。本地模式每页显示 40 个预览格，并在按需读取 `.ehviewer` 后显示真实总页数、磁盘已有页数和完整状态；实际文件数少于 sidecar 页数时按 sidecar 总数创建预览格，已有页读取本地文件，缺失页只请求当前预览分页涉及的在线缩略图；实际文件数大于或等于 sidecar 页数时按实际文件数显示。只要 sidecar 含完整画廊/页面 token，就始终提供源站检查补齐，即使目标 `DOWNLOADS.STATE` 已为完成。本地“同步信息”只在后台请求当前详情 HTML，刷新标签/评论/额外元数据并显示 `#gnd` 版本检测结果，不得启动图片下载；已同步评论和版本状态跨重启恢复。EH/EXH 在线模式显示“开始在线阅读”、站点按当前账户设置实际返回的 20/40 张一页缩略预览和只读评论区，分页数量必须使用详情响应推导的实际容量，不能固定按 20 张计算。在线预览只加载当前分页，切页先查内存缓存，未命中才后台请求；点击缩略图必须从对应全局零基页索引进入阅读器。评论作者、时间、上传者标记、评分和正文均可选择复制；评论中的 `OnlineGalleryLink` 以应用内按钮显示，点击按当前在线源重建地址并进入目标详情。主窗口维护最多 32 项的详情内返回栈，连续打开关联画廊后返回应逐级恢复上一在线或本地详情；从列表卡片或网址输入开始新的详情导航时清空旧栈，最终从在线详情返回原在线资源列表。下载控件使用竖直组合：按钮位于进度条上方，下载/排队/暂停/失败时按钮直接显示已完成页数与总页数，下载中点击同一按钮暂停。

TagChip 的复制查询必须消费当前本地或在线画廊的 `source_site`：EH/EXH 使用带 namespace 缩写和 `$` 的精确格式，NHC/NHN 使用完整 namespace 且不带 `$`。NHC 详情按 category、language、tags、parodies、artists、authors、groups、characters、relationships、attributes 的真实 JSON 集合生成分组，普通彩色标签使用 `tag:`；NHN 详情只按 `/tag/`、`/artist/`、`/group/` 等实际链接路径判定 namespace，不得从标签文字臆测 male/female。NHC/NHN 详情支持预览、在线阅读和基础下载：NHC 只消费同源详情接口返回的 `thumbnail_url` / `source_url`，NHN 从详情页 `/g/{gid}/{page}/` 节点解析 `#image-container`，所有图片 URL 必须严格限制到对应 HTTPS CDN。两站没有独立原图规格，必须隐藏原图下载和评论控件；NH 下载不写 EH 专用 `.ehviewer`，总页数与断点从自有任务记录恢复，并用来源映射后的本地 ID 避免与 EH GID 冲突。

详情页仍在首次打开时后台枚举单本的全部页面路径，以供阅读器随机跳页；缩略预览不得据此一次创建全部控件或解码全部图片。预览固定每页 40 张，只创建并解码当前预览页，切页时取消上一批任务。每个预览块保留全局零基页索引，点击后必须进入对应的真实阅读页。详情标题、英文标题、元数据和标签文字均须支持鼠标/键盘选择及复制；标题选中文字的右键查询只接受至少两个有效字符，本地操作按大小写不敏感字面量匹配英语/原标题并排除当前 GID，在线操作自动组成带引号的精确短语并使用当前在线源。主信息区默认不展示归类、页数、阅读进度、已下载/完整状态、文件大小和可见性，这些低频字段统一放入默认收起且同样可复制的“查看详细”区域；其旁只在当前画廊存在最近查询时显示相似结果按钮。切换画廊时重新收起，同一画廊的异步信息刷新不得打断当前展开状态。主信息区还须显示 `language` 与 `artist` 关键标签；`group`、`parody` 等不纳入关键标签。本地和在线详情的关键标签及完整标签卡统一通过共享 `EhTagSearchIndex` 精确查询中文译名，有记录时仅显示中文、无记录时回退原始 tag；控件 tooltip 必须保留完整 `namespace:value`，`tagNamespace` 与 `rawTag` 属性分别保留原始组成，不得把翻译写回领域对象或数据库。详情标签使用独立卡片，按 EhViewer 命名空间分组并以主题化胶囊控件双栏自动换行；数据源为搜索同时生成的裸标签不得与 `namespace:value` 重复显示。样式由 `StyleSheet.MANGA_DETAIL_INTERFACE` 的 light/dark QSS 管理。

关键标签和完整标签卡中的每个 `TagChip` 都必须提供右键“复制 Tag”，剪贴板内容使用原始 namespace/tag 而非中文显示值，并序列化为 EH 精确查询语法，例如 `language:chinese` 复制为 `l:"chinese$"`。缩写优先来自共享 `EhTagSearchIndex`，没有导入记录时使用标准 namespace 缩写；引号与反斜杠必须转义。本地查询解析需去除该末尾精确标记后再匹配完整 `namespace:value`，在线查询保持原 token 提交。

### `app/view/setting_interface.py`

设置页基于 `ScrollArea`，使用 QFluentWidgets 设置卡片直接绑定 `cfg`。当前包含主题模式、主题色和媒体目录。主题变化链路为：

```text
OptionsSettingCard
  -> cfg.themeMode 更新
  -> cfg.themeChanged
  -> setTheme()
  -> QFluentWidgets 样式管理器刷新所有已注册 QSS
```

设置页提供漫画根目录选择器和“导出 EhViewer 数据库”卡片，不再提供外部数据库路径。导出选择目标文件后必须由 `EhViewerDatabaseExportWorker` 在后台从自有库的一致读取快照新建完整 EhViewer v7 schema，合并自有收藏/历史，执行 `PRAGMA integrity_check`，再原子替换目标；不得把 Cookie 或任何 RSViewer 私有表写入导出文件。在线资源分组提供站点、EH Cookie/Token、NHC Cookie、NHN Cookie、系统/直连/手动代理、手动 HTTP(S) 地址、10/20/30/60 秒超时、默认展示视图、封面并发数、1–3 个画廊下载并发数、默认下载分类、画廊标题/Tag 标记规则和封面缓存过期时间，手动地址只在对应模式启用；默认分类选项消费本地资源加载得到的同一批 `DOWNLOAD_LABELS`，不存在或已删除的配置回退为未分类。画廊标记打开单个 Fluent 对话框，上半区管理标题规则、下半区管理 Tag 规则，各区加号新增且每项减号删除。视图、标记和并发数修改后对应页面或线程池即时同步。快捷键使用点击后捕获一次按键的交互，组合键或单键按下即保存，`Esc` 取消；当前可配置搜索栏、标签栏、返回、阅读器滚动和下一本快捷键。界面设置提供搜索栏鼠标悬停自动展开开关并即时生效。全局设置新增漫画阅读器分组，与阅读页内设置面板共用配置并即时同步。通用 `libraryFolders` 仍未被扫描器消费。

### EhViewer 数据库导入/导出边界

用户本机的 `testData/db/eh.db` 是旧 Android 漫画应用产生的 SQLite 数据库，仅用于一次性迁移、兼容分析和开发测试，不是运行时数据源：

- SQLite `user_version=7`。
- 已有表包括 `BOOKMARKS`、`DOWNLOADS`、`DOWNLOAD_DIRNAME`、`DOWNLOAD_LABELS`、`Gallery_Tags`、`HISTORY`、`LOCAL_FAVORITES`、`FILTER`、`QUICK_SEARCH`、`Black_List` 和 `android_metadata`。
- `GID` 是下载信息、目录名、标签、收藏和历史之间的重要关联键。
- `scripts/import_ehviewer_database.py` 必须用 SQLite URI `mode=ro` 和显式读取事务取得一致快照，按列交集把兼容表合并到自有库；`--replace` 只清空自有库的兼容表，绝不修改源文件。
- `app/repositories/ehviewer_schema.py` 是兼容 DDL 的唯一来源；自有库 migration 可以创建这些表，业务 Repository 不得自行 DDL。
- 设置页导出必须新建独立临时数据库，完整复制兼容表，并用 RSViewer 的 `manga_favorites`/`manga_browsing_history` 覆盖本地画廊对应的 EhViewer 收藏/历史行；非本地兼容行应保留。
- 导出文件固定为 EhViewer `user_version=7`，默认 `android_metadata.locale=zh_CN`，完整性校验成功后才允许替换用户选择的目标。
- 外部 `eh.db` 永远不得由运行时浏览、分类、下载、更新、整理或回收站流程打开；导入完成后应用移走或删除原文件也应正常运行。

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

当前 EhViewer 兼容漫画流已经实现以下边界：

```text
RSViewer 自有 rsviewer.db + 配置中的本地/映射盘/UNC 漫画根目录
  -> Worker 后台执行 EhViewerDataSource
  -> 自有 SQLite 元数据查询 + 根目录单次枚举
  -> LocalMangaInterface 分页、搜索、分类/树状归类筛选和封面展示
  -> 显式分类操作更新自有库 DOWNLOADS.LABEL；归类写同一 SQLite
  -> 后台预读当前页及后续三页封面；无有效缩略图时回退第一页
  -> 打开详情时才枚举该漫画全部页面路径；缩略预览按每页 40 张后台生成
  -> 按需读取 .ehviewer 第二行；仅在无自有进度时导入 RSViewer SQLite
  -> 开始阅读后后台解码当前页并预读相邻页，可在窗口和全屏之间切换
  -> 翻页即时刷新列表/详情，防抖后由单线程 Worker 保存自有进度
  -> cfg.reader* 即时控制画布、方向、载入大小、滚动快捷键与自动翻页
  -> UserLibraryRepository 保存树状归类和阅读进度；旧播放列表接口仅兼容历史数据
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
  -> OnlineGalleryPage -> 当前站点最近 64 页内存缓存 -> Card/List/Extended 与日期/游标翻页 UI
  -> Card/List 本地重排；切入/切出 Extended -> 后台 inline_set -> 重取当前页
  -> 多个 OnlineCoverWorker 先查分站点磁盘缓存，未命中才使用同一 Session
  -> 专用 QThreadPool 按配置并发加载 EH/EX/ehgt 缩略图并原子写入缓存
  -> 点击卡片 -> OnlineDetailWorker 使用当前 provider GET 同站画廊 HTML
  -> OnlineGalleryDetail/Comment/Preview -> 最近 20 个画廊的 OnlineGalleryMemoryCache
  -> 共享 MangaDetailInterface 展示在线详情、20 张一页的预览与只读评论区
  -> 切换预览页 -> OnlinePreviewPageWorker 请求同站画廊 ?p=N HTML
  -> 点击预览或开始阅读 -> 共享 MangaReaderInterface
  -> OnlineReaderLoadWorker 惰性请求 /s 单图 HTML -> #img 展示图并预读相邻页
  -> 在线详情、本地详情或正在下载页发起 -> 可配置 1–3 并发的 OnlineGalleryDownloadWorker
  -> 同一自有 SQLite 事务写兼容元数据与扩展任务/评论/原图状态/额外元数据
  -> 收集全部画廊预览页的 page token -> 原子写 .thumb 与 VERSION2 .ehviewer
  -> 校验已有八位页码图片 -> 跳过有效页 -> /s HTML -> #img 展示图补齐缺失页
  -> 正在下载页可单项或一键全部开始/暂停未完成任务，也可删除任务记录；完成任务自动移出
  -> 完成后更新 DOWNLOADS.STATE 并增量 upsert 当前 GID；失败/暂停/重启后保留断点
```

在线资源只有用户进入尚无内存状态的站点、主动搜索/日期定位/刷新/翻页、切入或切出 Extended、点击未缓存的画廊详情、切换未缓存的预览分页、阅读或下载未缓存页面、封面磁盘缓存未命中时才产生网络请求；Card/List 互切只使用当前内存页，List 不启动封面任务。

列表阶段禁止递归或逐本枚举页面；21,389 部漫画的真实库验证为 0 个页面路径常驻。本地 `CoverLabel` 必须缓存按当前控件尺寸平滑缩放并裁切后的 pixmap，只在图片或尺寸变化时重算，滚动重绘不得再次缩放源图。通用媒体流仍按 `Source -> Worker -> Repository -> Service -> View` 目标继续实现。

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

截至 2026-08-17：

- 可启动的 PySide6 Fluent 桌面主窗口。
- 窗口居中、Splash、Fluent 图标和 Windows 系统主题监听。
- 浅色、深色、跟随系统和自定义主题色配置。
- 全局设置页和阅读设置弹窗的 light/dark QSS 已接入 Fluent 样式管理器；主题切换时背景、文字和卡片外观同步刷新。
- 应用默认直接进入本地漫画库；左侧导航底部使用“漫画 / 视频”模式按钮切换顶部扁平入口，不再保留虚拟父路由或空展示首页。
- 漫画根目录可在设置中选择，变更后自动刷新；所有本地画廊索引和业务状态使用单一 RSViewer 自有数据库。旧 EhViewer `eh.db` 可由脚本只读导入，设置页可后台原子导出新的 EhViewer v7 兼容数据库。
- 本地漫画支持封面/标题布局、分页、搜索，以及互斥的分类、树状归类视图；标签栏可拖动至最多 30%，分类记忆上次选择。分类和归类通过可搜索、可滚动的独立选择窗口支持单项或复选批量分配；从所选分类/归类进入阅读时支持自动跨漫画翻页和随时“下一本”。
- 漫画右键支持在后台搜索整个本地库的相似画廊：能剥离常见社团/语言元数据、章节号、卷数、话数及前后篇，优先找出同作品章节和重复条目，并对较长标题提供保守容错匹配；21,389 条真实库首次后台全量比较约 3.3 秒，标题指纹缓存后的再次比较约 1.1 秒，期间不阻塞 GUI。
- 搜索栏和标签栏均提供可即时配置的全局快捷键；搜索按钮支持可关闭的悬停自动展开，空搜索离开后自动收起，有搜索内容时保持显示。
- `Database/database` 标签翻译仓库可通过脚本幂等导入 RSViewer SQLite；启动后本地、收藏、历史和在线搜索共享 43,751 条内存标签索引，支持中英文片段、多词引号、空格分隔的多条件补全及两行候选显示。
- 本地与在线搜索共享 SQLite v7 最近历史，匹配历史优先于标签候选，设置中可即时配置 5/10/15/20 条上限。
- 漫画阅读器支持窗口内与沉浸式全屏模式、全屏上下边缘触发控制栏、独立透明页码、键盘/按钮翻页、页码跳转、适应窗口、原始尺寸、缩放、拖动和相邻页后台预读；GIF 按文件头而非后缀识别，扩展名错误时仍可播放动画。
- 阅读页内与全局阅读设置即时同步，支持画布背景、四向翻页、适应宽度长图滚动、滚动快捷键及自动翻页间隔。
- 阅读进度在 RSViewer 自有 SQLite 保存并恢复；兼容首次导入 `.ehviewer` 十六进制页索引，自有记录优先，列表卡片与详情页显示当前进度。到达末页后永久记录“曾经读完”，手动清空时保存墓碑，不能再次从旧 sidecar 静默恢复。
- 详情页只创建并解码当前预览页的至多 40 张缩略图，任意预览缩略图可点击并直接从对应页码进入阅读。
- 详情页按命名空间分组展示去重后的胶囊标签，支持浅色/深色主题配色和大量标签自动换行；标题、元数据和标签文字可选中复制。
- 收藏页和本地历史页复用大型库首次加载结果；收藏支持右键单项/批量切换，本地历史按最近打开详情或阅读的时间排列，并预留在线历史入口。
- 在线画廊卡片在应用内打开共享详情页；详情 Worker 复用当前 provider 会话直接请求同站画廊 HTML，展示完整元数据、标签、20 张一页的缩略预览与只读评论。点击预览或“开始在线阅读”后复用漫画阅读器，按需解析 `/s` 单图 HTML 并加载 `#img` 站点展示图，返回时恢复在线详情及列表状态。
- 在线详情页支持后台下载、暂停和断点继续：任务在自有 SQLite 中写兼容元数据、评论和额外信息，再收集全部 page token，按 EhViewer 规则生成目录、`.thumb`、`VERSION2` `.ehviewer` 和八位页码站点展示图；已有有效页会跳过，缺失或损坏页会补下，完成后自动刷新本地库。
- 在线详情、预览和阅读使用最近访问 20 个画廊的线程安全内存 LRU；详情、封面、已访问的预览分页与缩略图可直接复用，阅读图片每画廊最多缓存 5 页并受 128 MiB 总预算约束。
- 在线资源已把用户提供的 `eh_tool_refactored.py` 接入 provider 和后台 UI/Worker：支持关键词、日期 Seek、next/prev URL 翻页、多显示模式元数据解析和封面加载；两个站点各自保存关键词、日期、当前响应游标、滚动位置和最近 64 个结果。列表不显示虚构数字页码，上一页/下一页只消费当前响应对应的游标。在线结果支持 Card、116px 精简 List 与 Extended 三种本地视图；List 不加载封面，Card/List 互切不产生网络请求。封面使用可配置并发线程池及按站点隔离、可配置过期时间的磁盘缓存，浅色/深色三类结果卡片均纳入 Fluent QSS。
- 快捷键设置采用点击捕获交互，支持单键和 `Ctrl+S` 等组合键即时确认，`Esc` 取消。
- 大型库采用列表元数据与详情页面两级惰性加载；21,389 部真实漫画列表读取约 0.42 秒，加入自有进度批量读取约 0.483 秒，完整首屏约 1.33 秒。
- RSViewer 自有 SQLite v22 同时保存完整 EhViewer v7 兼容画廊索引、分类，以及树状归类、收藏、本地浏览历史、阅读进度/永久读完/清空墓碑、最近一次相似查询、导入的 EH 标签快照、共享搜索历史、在线下载状态、原图资源阶段、本地画廊同步记录、评论、评论内站点画廊引用和额外元数据；旧播放列表关系保留但不再由 UI 使用，运行时不依赖外部数据库。
- 本地/映射盘/NAS 路径的其他媒体目录配置入口。
- DPI、语言和 Mica 配置模型。
- 模板 Gallery、演示资源、音乐配置和无用生成资源已清理。
- `README.md`、`requirements.txt` 和 `.gitignore` 已建立。
- 已做过 `compileall`、`git diff --check`、provider 契约与配置自动化测试、真实大型库计时，以及默认页/详情/快速退出的 Qt offscreen 冒烟验证。

## 8. 正在开发的内容

EhViewer 兼容本地漫画浏览、收藏、本地历史和基础阅读链路已经可用；EH/EX 的 HTML 列表页抓取、配置、分页、封面、应用内详情、缩略预览、只读评论区、站点展示图在线阅读，以及站点展示图/`fullimg` 原图可续传下载已接入。漫画索引已纳入 RSViewer 自有 schema；下一阶段应补齐通用图片/视频媒体模型、通用本地/UNC 扫描服务，以及双页/多图连续滚动。发表评论、在线历史和视频仍未实现。

工作区当前包含本次惰性加载、数据源配置、默认导航和测试改动；`testData/`、`data/`、`app/config/config.json` 与 `app/data/` 是忽略的本机数据，不得提交或删除。

## 9. 已知问题与技术债

- `libraryFolders` 只保存其他媒体目录列表，尚无通用扫描、可达性检测、断线状态或重连机制；UNC/NAS 的大规模真实场景仍需专项验证。
- 当前 RSViewer SQLite 已有版本化的 EhViewer 兼容漫画索引、树状归类、收藏、本地浏览历史和漫画进度，并保留旧播放列表兼容表；尚无通用图片/视频媒体 schema、缩略图缓存及缓存失效策略。
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
2. 在现有版本化漫画索引/标签/进度表基础上定义通用图片与视频媒体领域模型和 schema。
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
- 旧 EhViewer 数据库只能通过迁移脚本以 `mode=ro` 打开，运行时业务代码不得引用外部路径；导出目标只能由完整 schema 的临时文件经完整性校验后原子生成，不能原地执行 RSViewer migration。
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
