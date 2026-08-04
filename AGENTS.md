# RSViewer 项目维护指南

本文档面向未来接手本仓库的 AI 助手和开发者。开始任何工作前，请先完整阅读本文、`README.md` 和 `CHANGELOG.md`，然后执行 `git status --short`。本文描述的是 2026-08-04 的工作区现状；若代码与本文冲突，以代码为准，并在本次修改中同步修正文档。

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
- 当前持久化：QFluentWidgets 的 JSON 配置，以及独立 SQLite 中的 RSViewer 分类标签覆盖、用户复数标签和漫画阅读进度。
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
   ├─ domain/manga.py                # 漫画领域模型
   ├─ repositories/user_library_repository.py # RSViewer 用户标签与阅读进度库
   ├─ sources/ehviewer_source.py     # EhViewer DB/sidecar 只读适配与惰性页面加载
   ├─ workers/reading_progress_worker.py # 后台保存阅读进度
   ├─ resource/qss/
   │  ├─ dark/                        # 设置页与阅读设置弹窗深色样式
   │  └─ light/                       # 设置页与阅读设置弹窗浅色样式
   └─ view/
      ├─ main_window.py              # Fluent 主窗口、导航、主题监听
      ├─ local_manga_interface.py    # 本地漫画分页、搜索、标签与封面卡片
      ├─ manga_detail_interface.py   # 单本详情与按需页面预览
      ├─ manga_reader_interface.py   # 单页阅读、缩放、预读和全屏控制
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

- `ehViewerDatabase`：只读外部 EhViewer SQLite 文件。
- `ehViewerMangaRoot`：与外部库对应的漫画下载根目录，支持本地、映射盘或 UNC 路径。
- `libraryFolders`：其他图片/视频使用的本地、映射盘或 NAS/UNC 媒体目录。
- `mangaPageSize` / `mangaSortOrder`：本地资源每页数量及按 EhViewer 添加时间升序/降序；默认降序（最新优先）。
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

RSViewer 独立 SQLite 使用 `PRAGMA user_version` 执行可重复迁移。版本 1 是复数标签表，版本 2 新增 `manga_reading_progress(gid, page_index, updated_at)`，版本 3 新增只存于 RSViewer 的 `manga_primary_labels` 分类覆盖表；`page_index` 始终是零基索引。`resolve_progress()` 只在自有记录不存在时导入 `.ehviewer` 进度，两边都有时必须返回 RSViewer 自有记录。分类覆盖和复数标签都不能写回外部 `eh.db`。

### `app/common/style_sheet.py`

把 RSViewer 自定义样式注册到 QFluentWidgets 的样式管理器。`StyleSheet.SETTING_INTERFACE` 和 `StyleSheet.READER_SETTING_DIALOG` 注册后，`setTheme()` 会自动重新加载对应的 light/dark QSS。阅读设置弹窗是独立窗口，不能依赖主窗口背景透传，必须分别定义浅色与深色实体背景。

新增页面样式时，应：

1. 在 `StyleSheet` 枚举中增加名称。
2. 同时创建 `app/resource/qss/light/` 和 `dark/` 两份同名 QSS。
3. 在页面初始化、对象名设置完成后调用 `.apply(self)`。
4. 实际验证两种主题；避免用内联 QSS 固定文字颜色或背景色。

### `app/view/main_window.py`

主窗口基于 `FluentWindow`，负责窗口大小、图标、Splash、侧边导航、系统主题监听和 Mica 刷新。当前保留独立的虚拟“漫画”父路由，其下包含“本地资源”、收藏、在线资源和历史记录；父路由与启动默认页都打开 `LocalMangaInterface`，以后可替换父路由首页而不删除“本地资源”子项。另有独立“视频”和底部“设置”。数据源配置变化时由主窗口重建 Source 并通知列表与详情页。

`SystemThemeListener` 是持有资源的后台监听器，关闭窗口时必须 `terminate()` 和 `deleteLater()`。

### `app/view/media_interface.py`

仅为收藏、历史、在线资源和视频等尚未实现路由提供轻量占位页。本地漫画已经由 `LocalMangaInterface` 实现，应用默认进入该页，不再保留无内容的漫画展示首页。各页面通过稳定且唯一的 `objectName` 作为 Fluent 导航 route key。

### `app/view/manga_reader_interface.py`

漫画阅读器作为主窗口堆叠页运行，普通状态为窗口内阅读，按 `F11` 或工具栏按钮后由主窗口隐藏标题栏与导航并切换全屏，`Esc` 恢复窗口。当前实现单页模式、四向下一页按键、页码跳转、适应窗口/宽度、原始大小、缩放、拖动、长图按屏滚动和自动翻页；窗口模式的“第 N / 总页数 页”在顶部工具栏下方居中突出显示，底部仅保留翻页与跳转控件。全屏模式使用独立的低透明度页码浮层并默认隐藏所有控制区；鼠标进入屏幕最上方 12 像素区域时只显示标题/操作栏，进入最下方 12 像素区域时只显示翻页栏，离开对应控件区域即隐藏。普通鼠标活动、点击、滚轮和键盘翻页都不得唤出控制栏。图片在 `QThreadPool` 后台解码，优先当前页并预读后两页和前一页，缓存最多五页。图片视图自身通过事件过滤器接管阅读按键，点击图片取得焦点后仍必须正常翻页。页码改变后由主窗口即时更新列表/详情状态，并通过单线程后台队列防抖保存到 RSViewer SQLite。离开或关闭时必须停止自动翻页、落盘待保存进度并取消仍在运行的解码任务。

`ReaderSettingDialog` 是阅读页工具栏打开的非模态设置面板。它和全局 `SettingInterface` 绑定同一组 `cfg.reader*` 配置项，任一侧修改背景色、方向、图片载入大小、滚动快捷键或自动翻页设置后，另一侧与当前阅读器必须即时更新。弹窗通过 `StyleSheet.READER_SETTING_DIALOG` 注册 light/dark QSS，主题切换时背景必须与 Fluent 控件的文字和卡片风格同步刷新。

### `app/view/manga_detail_interface.py`

详情页仍在首次打开时后台枚举单本的全部页面路径，以供阅读器随机跳页；缩略预览不得据此一次创建全部控件或解码全部图片。预览固定每页 40 张，只创建并解码当前预览页，切页时取消上一批任务。每个预览块保留全局零基页索引，点击后必须进入对应的真实阅读页。

### `app/view/setting_interface.py`

设置页基于 `ScrollArea`，使用 QFluentWidgets 设置卡片直接绑定 `cfg`。当前包含主题模式、主题色和媒体目录。主题变化链路为：

```text
OptionsSettingCard
  -> cfg.themeMode 更新
  -> cfg.themeChanged
  -> setTheme()
  -> QFluentWidgets 样式管理器刷新所有已注册 QSS
```

设置页还提供 EhViewer 数据库文件与漫画根目录选择器，变更后立即重建只读数据源并后台刷新本地漫画；快捷键使用点击后捕获一次按键的交互，组合键或单键按下即保存，`Esc` 取消。全局设置新增漫画阅读器分组，与阅读页内设置面板共用配置并即时同步。通用 `libraryFolders` 仍未被扫描器消费。

### 外部 EhViewer 数据源约束

用户本机的 `testData/db/eh.db` 是旧 Android 漫画应用产生的 SQLite 数据库，仅用于兼容分析和开发测试：

- SQLite `user_version=7`。
- 已有表包括 `BOOKMARKS`、`DOWNLOADS`、`DOWNLOAD_DIRNAME`、`DOWNLOAD_LABELS`、`Gallery_Tags`、`HISTORY`、`LOCAL_FAVORITES`、`FILTER`、`QUICK_SEARCH`、`Black_List` 和 `android_metadata`。
- `GID` 是下载信息、目录名、标签、收藏和历史之间的重要关联键。
- 既有表、索引、触发器及数据必须保持原样；访问适配器默认以 SQLite 只读模式打开该库。
- 不允许在 `eh.db` 中新增 RSViewer 表，也不允许对它执行 RSViewer migration。
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
  -> LocalMangaInterface 分页、搜索、筛选和封面展示
  -> 后台预读当前页及后续三页封面；无有效缩略图时回退第一页
  -> 打开详情时才枚举该漫画全部页面路径；缩略预览按每页 40 张后台生成
  -> 按需读取 .ehviewer 第二行；仅在无自有进度时导入 RSViewer SQLite
  -> 开始阅读后后台解码当前页并预读相邻页，可在窗口和全屏之间切换
  -> 翻页即时刷新列表/详情，防抖后由单线程 Worker 保存自有进度
  -> cfg.reader* 即时控制画布、方向、载入大小、滚动快捷键与自动翻页
  -> UserLibraryRepository 在独立 SQLite 保存复数标签
```

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
- EhViewer 外部 DB 与漫画根目录可在设置中选择，变更后自动刷新；外部 DB 全程只读。
- 本地漫画支持封面/标题布局、分页、搜索、主标签/复数标签筛选、按添加时间升降序、两级右键标签分配、详情与每页 40 张的预览分页；当前列表页会预读后续三页封面，缩略图不可用时回退漫画第一页。
- 漫画阅读器支持窗口内与沉浸式全屏模式、全屏上下边缘触发控制栏、独立透明页码、键盘/按钮翻页、页码跳转、适应窗口、原始尺寸、缩放、拖动和相邻页后台预读。
- 阅读页内与全局阅读设置即时同步，支持画布背景、四向翻页、适应宽度长图滚动、滚动快捷键及自动翻页间隔。
- 阅读进度在 RSViewer 独立 SQLite 保存并恢复；兼容首次导入 `.ehviewer` 十六进制页索引，自有记录优先，列表卡片与详情页显示当前进度。
- 详情页只创建并解码当前预览页的至多 40 张缩略图，任意预览缩略图可点击并直接从对应页码进入阅读。
- 快捷键设置采用点击捕获交互，支持单键和 `Ctrl+S` 等组合键即时确认，`Esc` 取消。
- 大型库采用列表元数据与详情页面两级惰性加载；21,389 部真实漫画列表读取约 0.42 秒，加入自有进度批量读取约 0.483 秒，完整首屏约 1.33 秒。
- RSViewer 独立 SQLite 保存分类标签覆盖、用户复数标签和阅读进度，连接均显式提交、回滚和关闭。
- 本地/映射盘/NAS 路径的其他媒体目录配置入口。
- DPI、语言和 Mica 配置模型。
- 模板 Gallery、演示资源、音乐配置和无用生成资源已清理。
- `README.md`、`requirements.txt` 和 `.gitignore` 已建立。
- 已做过 `compileall`、`git diff --check`、自动化数据源测试、真实大型库计时，以及默认页/详情/快速退出的 Qt offscreen 冒烟验证。

## 8. 正在开发的内容

EhViewer 本地漫画浏览和基础阅读链路已经可用，下一阶段应补齐 RSViewer 完整媒体 schema/migration、通用本地/UNC 扫描服务，以及双页/多图连续滚动。收藏、历史、在线资源和视频仍是占位页。

工作区当前包含本次惰性加载、数据源配置、默认导航和测试改动；`testData/`、`app/config/config.json` 与 `app/data/` 是忽略的本机数据，不得提交或删除。

## 9. 已知问题与技术债

- 开发配置路径已不依赖当前工作目录，但打包前仍应迁移到 `QStandardPaths.AppConfigLocation`；RSViewer 自有数据库和缓存也应迁移到应用数据目录。
- `libraryFolders` 只保存其他媒体目录列表，尚无通用扫描、可达性检测、断线状态或重连机制；UNC/NAS 的大规模真实场景仍需专项验证。
- 当前 RSViewer SQLite 已有版本化的分类覆盖、复数标签与漫画进度表，尚无完整媒体 schema、缩略图缓存及缓存失效策略。
- 为保证大型库首屏速度，漫画卡片在列表阶段不显示精确页数；页数在打开单本详情并完成按需枚举后可用。
- 阅读器当前只有单页模式；单张长图可按屏滚动，但尚未把多张图片拼接为连续长图，也未建立磁盘级解码缓存。
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
6. 将收藏与历史占位页接入查询服务和封面网格。
7. 实现 Qt Multimedia 视频播放器和播放进度。
8. 扩充自动化测试，覆盖损坏 DB、空目录、目录不可达、UNC 断线、中文/特殊字符、超长路径和大目录取消。
9. 最后再完善在线资源接口、打包、更新和发布流程。

## 11. 开发规范

- 修改前先读 `AGENTS.md`、`CHANGELOG.md`，执行 `git status --short`，保护用户已有修改。
- 保持 UI、服务、数据访问、文件来源分层；禁止在 GUI 线程递归扫描 NAS 或生成大量缩略图。
- 文件路径使用 `pathlib.Path`；对 UNC、长路径、无权限、断线和文件消失做显式错误处理。
- 媒体扫描应幂等：重复扫描不能制造重复条目；文件删除、移动和修改需要可追踪。
- 数据库 schema 每次变化必须有迁移，不允许靠删除用户数据库解决升级。
- 外部 EhViewer 数据库始终视为只读数据源；不得修改既有 schema，不得向其中添加 RSViewer 自有表。所有新增表只能存在于 RSViewer 独立数据库。
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
