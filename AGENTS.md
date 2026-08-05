# RSViewer 项目维护指南

本文档面向未来接手本仓库的 AI 助手和开发者。开始任何工作前，请先完整阅读本文、`README.md` 和 `CHANGELOG.md`，然后执行 `git status --short`。本文描述的是 2026-08-05 的工作区现状；若代码与本文冲突，以代码为准，并在本次修改中同步修正文档。

## 1. 项目背景与边界

RSViewer 是一个仅供个人、非商业使用的 Windows 桌面媒体管理与查看工具。目标是统一浏览本地磁盘和 NAS 中的漫画、图片集与视频，并提供媒体索引、封面、搜索、阅读/播放和进度保存能力。

当前项目来自 PyQt-Fluent-Widgets Gallery 示例骨架，但示例、演示资源和音乐播放器残留已经被有意删除。不要恢复 `examples/`、旧 Gallery 资源、旧音乐配置或约 26 万行的生成文件 `app/common/resource.py`，除非用户明确要求。

第三方 UI 依赖是 PySide6-Fluent-Widgets。项目虽为个人非商业用途，仍须遵守第三方组件许可证，不要删除 `README.md` 中的第三方说明。

### 当前成熟度

项目处于早期 MVP 阶段。EhViewer 本地漫画库已经可配置、可分页浏览、搜索、筛选、打开详情并进入单页漫画阅读器；阅读器已有四向翻页、单张长图滚动、自动翻页、进度保存/恢复和即时同步设置。通用媒体扫描、自有完整媒体索引、双页/多图连续阅读和视频播放器仍未实现。

## 2. 技术栈与运行环境

- Python：建议 3.10+；2026-08-03 本机验证环境为 Python 3.9.2。
- GUI：PySide6，依赖范围见 `requirements.txt`。
- Fluent UI：PySide6-Fluent-Widgets。
- 主要平台：Windows 10/11；Mica 效果仅在符合条件的 Windows 11 环境启用。
- 当前持久化：QFluentWidgets 的 JSON 配置、目标 EhViewer `DOWNLOADS.LABEL` 中的分类，以及独立 SQLite 中的 RSViewer 播放列表、树状归类、收藏、本地浏览历史和漫画阅读进度。
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
├─ requirements.txt                 # 直接运行依赖
├─ main.py                           # 唯一应用入口
└─ app/
   ├─ common/
   │  ├─ config.py                   # 配置模型、稳定配置路径、JSON 加载
   │  └─ style_sheet.py              # 自定义 QSS 路径与主题注册
   ├─ domain/manga.py                # 本地漫画领域模型
   ├─ domain/online_gallery.py       # 在线画廊与翻页结果模型
   ├─ repositories/user_library_repository.py # RSViewer 用户标签与阅读进度库
   ├─ sources/ehviewer_source.py     # EhViewer 只读查询、分类写入与惰性页面加载
   ├─ sources/eh_online_source.py    # EH/EX provider 接口、运行配置与默认空实现
   ├─ workers/eh_online_worker.py    # 在线搜索与封面下载 Worker
   ├─ workers/reading_progress_worker.py # 后台保存阅读进度
   ├─ resource/qss/
   │  ├─ dark/                        # 设置页与阅读设置弹窗深色样式
   │  └─ light/                       # 设置页与阅读设置弹窗浅色样式
   └─ view/
      ├─ main_window.py              # Fluent 主窗口、导航、主题监听
      ├─ local_manga_interface.py    # 本地漫画分页、搜索、标签与封面卡片
      ├─ manga_history_interface.py  # 本地浏览历史与在线历史预留路由
      ├─ manga_detail_interface.py   # 单本详情与按需页面预览
      ├─ manga_reader_interface.py   # 单页阅读、缩放、预读和全屏控制
      ├─ online_manga_interface.py   # 在线 provider 的搜索、翻页与封面 UI 壳
      ├─ reader_setting_dialog.py    # 阅读页内即时同步设置面板
      ├─ media_interface.py          # 未实现媒体路由的轻量占位页面
      └─ setting_interface.py        # 设置页、数据源路径和配置绑定
```

`app/config/config.json` 是运行时生成的用户配置，已被 `.gitignore` 忽略，不应提交。`.idea/`、`__pycache__/`、构建目录同样不应提交。

本机还可能存在被忽略的 `testData/` 和 `lib/`：前者是用户提供的外部数据库与漫画样例，后者是本地 Python 环境。二者都不是应用源码，不得提交或删除。

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
- `onlineEhSite`：在线资源默认站点，支持 `ehentai` 与 `exhentai`。
- `onlineEhCookie`：用户自行提供的完整 EH Cookie；裸 token 按 `igneous` 兼容。该值仅存于被忽略的本机配置 JSON，不得输出到日志或提交。
- `onlineEhProxyMode` / `onlineEhManualProxy`：在线 provider 使用系统代理、直连或手动 HTTP(S) 代理；手动地址仅在 `manual` 模式消费。
- `onlineEhRequestTimeout`：传给在线 provider 的单次请求超时，支持 10/20/30/60 秒。
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

RSViewer 独立 SQLite 使用 `PRAGMA user_version` 执行可重复迁移。版本 1 的复数标签表在界面上已演进为播放列表，版本 2 新增阅读进度，版本 3 保留历史分类覆盖兼容，版本 4 增加播放顺序与树状归类，版本 5 新增 `manga_favorites(gid, created_at)` 和 `manga_browsing_history(gid, viewed_at)`。当前资源页的分类直接更新目标 EhViewer `DOWNLOADS.LABEL`，但绝不修改外部库 schema。删除播放列表依靠外键清理成员；删除归类节点会级联删除子树和关联。`page_index` 始终是零基索引；播放列表、归类、收藏、历史和进度只写 RSViewer 自有数据库。

### `app/common/style_sheet.py`

把 RSViewer 自定义样式注册到 QFluentWidgets 的样式管理器。设置页、阅读设置弹窗和漫画详情标签分别注册 `StyleSheet.SETTING_INTERFACE`、`StyleSheet.READER_SETTING_DIALOG`、`StyleSheet.MANGA_DETAIL_INTERFACE`，`setTheme()` 会自动重新加载对应的 light/dark QSS。阅读设置弹窗是独立窗口，不能依赖主窗口背景透传，必须分别定义浅色与深色实体背景。

新增页面样式时，应：

1. 在 `StyleSheet` 枚举中增加名称。
2. 同时创建 `app/resource/qss/light/` 和 `dark/` 两份同名 QSS。
3. 在页面初始化、对象名设置完成后调用 `.apply(self)`。
4. 实际验证两种主题；避免用内联 QSS 固定文字颜色或背景色。

### `app/view/main_window.py`

主窗口基于 `FluentWindow`，负责窗口、导航、主题、数据源组合，以及本地资源/收藏/历史之间的共享数据同步。漫画父路由下包含本地资源、收藏、在线资源和历史记录；父路由与启动默认页打开本地资源。在线资源路由使用 `OnlineMangaInterface`，不得在主窗口或 GUI 线程直接执行网络请求。收藏与本地历史不得各自重新执行大型库加载，而应消费 `LocalMangaInterface.libraryLoaded` 的同一批元数据。打开详情和阅读时由主窗口即时更新历史顺序，并在单线程后台队列保存。

`SystemThemeListener` 是持有资源的后台监听器，关闭窗口时必须 `terminate()` 和 `deleteLater()`。

### `app/view/media_interface.py`

仅为在线历史和视频等尚未实现路由提供轻量占位页。在线资源已由独立界面实现，收藏与本地历史使用 `LocalMangaInterface` 的集合模式。各页面通过稳定且唯一的 `objectName` 作为 Fluent 导航 route key。

### `app/sources/eh_online_source.py` 与 `app/view/online_manga_interface.py`

`EhOnlineProvider` 是用户自行实现爬虫的稳定扩展点。UI 将 `OnlineGalleryQuery(keyword, cursor, page_number, filters)` 交给 provider；基类先调用抽象的 `fetch_page()`，再调用可覆盖的 `filter_items()`，最后返回统一的 `OnlineGalleryPage`。远程封面可选择实现 `load_thumbnail()`。`create_eh_online_provider()` 是组合入口，当前返回完全不访问网络的 `UnimplementedEhOnlineProvider`；接入实际爬虫时只替换这里或向 `OnlineMangaInterface` 注入 factory，不得把抓取/解析写进 QWidget 或 Worker。

`EhOnlineSettings` 统一提供站点基址、规范化 Cookie、代理模式/映射和请求超时。Cookie 可粘贴完整 `ipb_member_id=...; ipb_pass_hash=...; igneous=...` 字符串，单独裸 token 按 `igneous` 兼容，并从 settings 的 `repr` 中排除。系统代理由标准库发现，直连返回空映射，手动模式验证并补全 HTTP(S) URL。具体 HTTP 客户端、站点请求、HTML/API 解析、URL 白名单和结果过滤均由用户的 provider 实现。

### `app/view/local_manga_interface.py`

本地资源页标签栏默认隐藏，通过工具栏“标签”按钮展开；其中“分类”“播放列表”“归类”三个面板互斥，各自带新增加号，顶部另有“显示全部漫画”。分隔条可拖动但标签栏最多占页面宽度 30%；使用 `FluentSplitterHandle` 提供 7 像素命中区和 1 像素主题色细线，透明度规则应与 `NavigationResizeHandle` 一致，不得恢复 Qt 默认实心手柄。展开、收起或拖动后必须等待 `QSplitter` 几何更新并主动调用卡片重排，不能依赖主窗口 `resizeEvent`。分类为单选并记忆选择，默认未分类；播放列表和树状归类为多对多。三个树都提供右键删除并必须二次确认；未分类不可删除，分类删除时关联漫画先回到未分类，归类父节点删除会级联整个子树。播放列表按持久顺序展示，提供播放、继续上一次，以及拖拽、上下移动、置顶/置底编排；编排保存必须绑定打开窗口时的播放列表 ID。

网格和列表卡片的右键菜单只保留固定的“选择分类…”“选择播放列表…”“选择归类…”入口，不得重新把大量标签展开为悬浮子菜单。入口打开主题化 `MangaLabelSelectionDialog`：提供搜索和可滚动树，分类单选并包含“未分类”，播放列表与树状归类多选；批量目标成员状态不一致时显示半选，半选保持不变，用户明确勾选或取消后才批量写入。播放列表/归类窗口保留“新建并添加…”入口。右键不需要先开启复选且不得触发详情。分类更新目标 `DOWNLOADS.LABEL`，播放列表和归类写 RSViewer 自有库；数据库变更应在 Worker 中执行，多项选择变化应合并为单个后台任务。

`LocalMangaInterface` 还提供收藏/历史集合模式：不启动 `MangaLoadWorker`，隐藏标签栏和添加时间排序，按 Repository 给出的 GID 顺序分页展示共享漫画对象。卡片右键菜单顶部提供收藏或取消收藏，复选时批量生效；收藏状态变更必须同步本地资源、收藏和历史三个视图。

### `app/view/manga_history_interface.py`

历史页面包含“本地历史”和“在线历史”两个互斥入口。本地历史按 `viewed_at` 最近优先展示；在线历史当前只保留明确的占位页面和稳定 route，不得把本地记录混入其中。

### `app/view/manga_reader_interface.py`

漫画阅读器作为主窗口堆叠页运行，普通状态为窗口内阅读，按 `F11` 或工具栏按钮后由主窗口隐藏标题栏与导航并切换全屏，`Esc` 恢复窗口。当前实现单页模式、四向下一页按键、页码跳转、长图滚动和自动翻页；从播放列表进入时，末页“下一页”打开下一本首页，首页“上一页”打开上一本末页，列表两端则正常停止。窗口页码在顶部居中，全屏使用独立低透明度页码并默认隐藏控制区，仅上下 12 像素边缘触发对应栏。图片在 `QThreadPool` 后台解码并预读相邻页；点击图片取得焦点后仍须正常翻页。页码变化即时更新列表/详情并防抖保存。

`ReaderSettingDialog` 是阅读页工具栏打开的非模态设置面板。它和全局 `SettingInterface` 绑定同一组 `cfg.reader*` 配置项，任一侧修改背景色、方向、图片载入大小、滚动快捷键或自动翻页设置后，另一侧与当前阅读器必须即时更新。弹窗通过 `StyleSheet.READER_SETTING_DIALOG` 注册 light/dark QSS，主题切换时背景必须与 Fluent 控件的文字和卡片风格同步刷新。

### `app/view/manga_detail_interface.py`

详情页仍在首次打开时后台枚举单本的全部页面路径，以供阅读器随机跳页；缩略预览不得据此一次创建全部控件或解码全部图片。预览固定每页 40 张，只创建并解码当前预览页，切页时取消上一批任务。每个预览块保留全局零基页索引，点击后必须进入对应的真实阅读页。详情标签使用独立卡片，按 EhViewer 命名空间分组并以主题化胶囊控件双栏自动换行；数据源为搜索同时生成的裸标签不得与 `namespace:value` 重复显示。样式由 `StyleSheet.MANGA_DETAIL_INTERFACE` 的 light/dark QSS 管理。

### `app/view/setting_interface.py`

设置页基于 `ScrollArea`，使用 QFluentWidgets 设置卡片直接绑定 `cfg`。当前包含主题模式、主题色和媒体目录。主题变化链路为：

```text
OptionsSettingCard
  -> cfg.themeMode 更新
  -> cfg.themeChanged
  -> setTheme()
  -> QFluentWidgets 样式管理器刷新所有已注册 QSS
```

设置页还提供 EhViewer 数据库文件与漫画根目录选择器，变更后立即重建数据源并后台刷新本地漫画；常规读取使用只读连接，分类操作按用户指令另开写事务。在线资源分组提供站点、Cookie/Token、系统/直连/手动代理、手动 HTTP(S) 地址和 10/20/30/60 秒超时，手动地址只在对应模式启用。快捷键使用点击后捕获一次按键的交互，组合键或单键按下即保存，`Esc` 取消。全局设置新增漫画阅读器分组，与阅读页内设置面板共用配置并即时同步。通用 `libraryFolders` 仍未被扫描器消费。

### 外部 EhViewer 数据源约束

用户本机的 `testData/db/eh.db` 是旧 Android 漫画应用产生的 SQLite 数据库，仅用于兼容分析和开发测试：

- SQLite `user_version=7`。
- 已有表包括 `BOOKMARKS`、`DOWNLOADS`、`DOWNLOAD_DIRNAME`、`DOWNLOAD_LABELS`、`Gallery_Tags`、`HISTORY`、`LOCAL_FAVORITES`、`FILTER`、`QUICK_SEARCH`、`Black_List` 和 `android_metadata`。
- `GID` 是下载信息、目录名、标签、收藏和历史之间的重要关联键。
- 浏览、搜索、封面和元数据读取默认以 SQLite 只读模式打开。功能需要且用户明确触发时可以事务修改既有表中的业务内容；当前已实现的是更新所选 GID 的 `DOWNLOADS.LABEL`。
- 不允许在 `eh.db` 中新增 RSViewer 表，也不允许对它执行 RSViewer migration。
- 外部数据库的表、索引、触发器、列和 `user_version` 结构属于不可变边界；允许修改内容不等于允许执行 DDL。
- RSViewer 自有的媒体索引、路径映射、阅读进度、视频数据和设置扩展必须写入另一份独立 SQLite 文件。

`ehViewerMangaRoot` 指向对应下载根目录。典型结构是一个下载目录包含 `.ehviewer` sidecar、`.thumb` 缩略图和按页码命名的图片。列表只枚举一次根目录；当前页和后续三页在后台优先读取 `.thumb`，缩略图缺失或损坏时只对相应漫画枚举并使用自然排序后的第一页；详情页才枚举单本的全部页面。扫描和阅读逻辑必须自然排序现有页面并容忍缺页，不能假设编号连续。

`.ehviewer` 只读兼容规则：第二行是十六进制的零基页索引，例如 `0000008f` 表示索引 143、界面第 144 页。启动阶段禁止逐本打开两万多个 sidecar；仅在当前列表页/后续预载页或打开详情时后台按需读取。自有数据库无记录且 sidecar 有效时导入一条；两边都有时自有记录优先；后续阅读只写 RSViewer 数据库，不修改 `.ehviewer`。

## 5. 当前系统数据流

### 启动流

```text
导入 cfg并加载 JSON
  -> 读取 DPI/语言
  -> 创建 QApplication
  -> 安装 FluentTranslator
  -> 创建 MainWindow
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

在线资源预留流：

```text
cfg.onlineEh* -> EhOnlineSettings
  -> OnlineMangaInterface 构造 OnlineGalleryQuery
  -> OnlineSearchWorker 调用 EhOnlineProvider.search
  -> provider.fetch_page -> provider.filter_items
  -> OnlineGalleryPage -> 通用卡片/游标翻页 UI
  -> 可选 OnlineCoverWorker 调用 provider.load_thumbnail
```

默认 `create_eh_online_provider()` 返回空实现，以上流程在用户接入实际 provider 前不会产生网络请求。

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

截至 2026-08-04：

- 可启动的 PySide6 Fluent 桌面主窗口。
- 窗口居中、Splash、Fluent 图标和 Windows 系统主题监听。
- 浅色、深色、跟随系统和自定义主题色配置。
- 全局设置页和阅读设置弹窗的 light/dark QSS 已接入 Fluent 样式管理器；主题切换时背景、文字和卡片外观同步刷新。
- 应用默认直接进入本地漫画库；保留虚拟“漫画”父路由和独立“本地资源”子项，不再创建空展示首页。
- EhViewer 外部 DB 与漫画根目录可在设置中选择，变更后自动刷新；外部 DB 除用户显式新增分类或分配分类时更新既有表内容外均只读，schema 始终不可变。
- 本地漫画支持封面/标题布局、分页、搜索，以及互斥的分类、播放列表、树状归类视图；标签栏可拖动至最多 30%，分类记忆上次选择，播放列表可编排/续播/跨漫画翻页。三类标签通过可搜索、可滚动的独立选择窗口支持单项或复选批量分配，不再使用无界右键子菜单。
- 漫画阅读器支持窗口内与沉浸式全屏模式、全屏上下边缘触发控制栏、独立透明页码、键盘/按钮翻页、页码跳转、适应窗口、原始尺寸、缩放、拖动和相邻页后台预读。
- 阅读页内与全局阅读设置即时同步，支持画布背景、四向翻页、适应宽度长图滚动、滚动快捷键及自动翻页间隔。
- 阅读进度在 RSViewer 独立 SQLite 保存并恢复；兼容首次导入 `.ehviewer` 十六进制页索引，自有记录优先，列表卡片与详情页显示当前进度。
- 详情页只创建并解码当前预览页的至多 40 张缩略图，任意预览缩略图可点击并直接从对应页码进入阅读。
- 详情页按命名空间分组展示去重后的胶囊标签，支持浅色/深色主题配色和大量标签自动换行。
- 收藏页和本地历史页复用大型库首次加载结果；收藏支持右键单项/批量切换，本地历史按最近打开详情或阅读的时间排列，并预留在线历史入口。
- 在线资源已预留 provider 接口和通用后台 UI/Worker：支持 E-Hentai / ExHentai 查询上下文、可扩展过滤参数、游标翻页和可选封面回传；设置页提供隐藏显示的 Cookie/Token、系统/直连/手动代理、手动 HTTP(S) 地址和请求超时。默认 provider 不访问网络。
- 快捷键设置采用点击捕获交互，支持单键和 `Ctrl+S` 等组合键即时确认，`Esc` 取消。
- 大型库采用列表元数据与详情页面两级惰性加载；21,389 部真实漫画列表读取约 0.42 秒，加入自有进度批量读取约 0.483 秒，完整首屏约 1.33 秒。
- RSViewer 独立 SQLite v5 保存播放列表、树状归类、收藏、本地浏览历史和阅读进度；目标 EhViewer 数据库只在显式分类操作或新增分类时更新既有业务表，schema 永不变更。
- 本地/映射盘/NAS 路径的其他媒体目录配置入口。
- DPI、语言和 Mica 配置模型。
- 模板 Gallery、演示资源、音乐配置和无用生成资源已清理。
- `README.md`、`requirements.txt` 和 `.gitignore` 已建立。
- 已做过 `compileall`、`git diff --check`、provider 契约与配置自动化测试、真实大型库计时，以及默认页/详情/快速退出的 Qt offscreen 冒烟验证。

## 8. 正在开发的内容

EhViewer 本地漫画浏览、收藏、本地历史和基础阅读链路已经可用；EH/EX 的 provider 接口、配置与 UI 壳已就绪，但具体抓取和过滤由用户另行实现。下一阶段应补齐 RSViewer 完整媒体 schema/migration、通用本地/UNC 扫描服务，以及双页/多图连续滚动。在线抓取/阅读/下载、在线历史和视频仍未实现。

工作区当前包含本次惰性加载、数据源配置、默认导航和测试改动；`testData/`、`app/config/config.json` 与 `app/data/` 是忽略的本机数据，不得提交或删除。

## 9. 已知问题与技术债

- 开发配置路径已不依赖当前工作目录，但打包前仍应迁移到 `QStandardPaths.AppConfigLocation`；RSViewer 自有数据库和缓存也应迁移到应用数据目录。
- `libraryFolders` 只保存其他媒体目录列表，尚无通用扫描、可达性检测、断线状态或重连机制；UNC/NAS 的大规模真实场景仍需专项验证。
- 当前 RSViewer SQLite 已有版本化的播放列表、树状归类、收藏、本地浏览历史、漫画进度和历史兼容分类覆盖表，尚无完整媒体 schema、缩略图缓存及缓存失效策略。
- 为保证大型库首屏速度，漫画卡片在列表阶段不显示精确页数；页数在打开单本详情并完成按需枚举后可用。
- 阅读器当前只有单页模式；单张长图可按屏滚动，但尚未把多张图片拼接为连续长图，也未建立磁盘级解码缓存。
- 在线资源当前只有 provider 契约、后台 Worker 和 UI 壳，默认空实现不会访问网络；具体抓取、过滤、URL 安全校验、在线详情、原图阅读/下载、磁盘封面缓存和在线历史尚未实现。鉴权 Cookie 目前保存在被忽略的本机 JSON，打包前应迁移到 Windows 凭据存储。
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
8. 用户接入 EH/EX provider 后，再完善在线详情、原图阅读/下载与在线历史接口，以及打包、更新和发布流程。

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
python -m compileall -q main.py app
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
