# RSViewer 项目维护指南

本文档面向未来接手本仓库的 AI 助手和开发者。开始任何工作前，请先完整阅读本文、`README.md` 和 `CHANGELOG.md`，然后执行 `git status --short`。本文描述的是 2026-08-03 的工作区现状；若代码与本文冲突，以代码为准，并在本次修改中同步修正文档。

## 1. 项目背景与边界

RSViewer 是一个仅供个人、非商业使用的 Windows 桌面媒体管理与查看工具。目标是统一浏览本地磁盘和 NAS 中的漫画、图片集与视频，并提供媒体索引、封面、搜索、阅读/播放和进度保存能力。

当前项目来自 PyQt-Fluent-Widgets Gallery 示例骨架，但示例、演示资源和音乐播放器残留已经被有意删除。不要恢复 `examples/`、旧 Gallery 资源、旧音乐配置或约 26 万行的生成文件 `app/common/resource.py`，除非用户明确要求。

第三方 UI 依赖是 PySide6-Fluent-Widgets。项目虽为个人非商业用途，仍须遵守第三方组件许可证，不要删除 `README.md` 中的第三方说明。

### 当前成熟度

项目处于早期基础设施阶段。目前是可启动的桌面应用壳，不是已经可用的媒体管理器。媒体扫描、数据库、封面库、图片阅读器和视频播放器均尚未实现。

## 2. 技术栈与运行环境

- Python：建议 3.10+；2026-08-03 本机验证环境为 Python 3.9.2。
- GUI：PySide6，依赖范围见 `requirements.txt`。
- Fluent UI：PySide6-Fluent-Widgets。
- 主要平台：Windows 10/11；Mica 效果仅在符合条件的 Windows 11 环境启用。
- 当前持久化：QFluentWidgets 的 JSON 配置。
- 规划持久化：SQLite 媒体索引与文件系统缩略图缓存。
- 规划媒体能力：Qt Multimedia；在确认格式覆盖不足前不要过早引入 VLC/mpv 等额外运行时。

安装和启动：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

应用必须从仓库根目录启动，因为当前配置路径 `app/config/config.json` 是相对当前工作目录解析的。这是已知技术债。

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
   │  ├─ config.py                   # 配置模型、验证器、JSON 加载
   │  └─ style_sheet.py              # 自定义 QSS 路径与主题注册
   ├─ resource/qss/
   │  ├─ dark/setting_interface.qss  # 设置页深色样式
   │  └─ light/setting_interface.qss # 设置页浅色样式
   └─ view/
      ├─ main_window.py              # Fluent 主窗口、导航、主题监听
      ├─ media_interface.py          # 漫画/视频路由的轻量占位页面
      └─ setting_interface.py        # 设置页和配置绑定
```

`app/config/config.json` 是运行时生成的用户配置，已被 `.gitignore` 忽略，不应提交。`.idea/`、`__pycache__/`、构建目录同样不应提交。

本机还可能存在被忽略的 `testData/` 和 `lib/`：前者是用户提供的外部数据库与漫画样例，后者是本地 Python 环境。二者都不是应用源码，不得提交或删除。

## 4. 核心模块与职责

### `main.py`

应用组合根。读取 DPI 和语言配置，创建 `QApplication`，安装 Fluent 翻译器，创建并运行 `MainWindow`。不要在模块导入时创建窗口或进入事件循环；继续保留 `main()` 和 `if __name__ == "__main__"` 保护。

### `app/common/config.py`

定义全局 `cfg`。当前配置项：

- `libraryFolders`：用户选择的本地、映射盘或 NAS/UNC 媒体目录。
- `micaEnabled`：窗口 Mica 效果。
- `dpiScale`：Qt 缩放比例，需要重启。
- `language`：语言选择，需要重启；目前业务界面尚未真正国际化。
- `themeMode` 和 `themeColor`：继承自 QFluentWidgets 的 `QConfig`。

配置通过 `qconfig.load("app/config/config.json", cfg)` 加载和保存。业务数据库不能塞入此 JSON；媒体条目、进度、收藏和扫描状态以后应进入 SQLite。

### `app/common/style_sheet.py`

把 RSViewer 自定义样式注册到 QFluentWidgets 的样式管理器。`StyleSheet.SETTING_INTERFACE.apply(widget)` 注册后，`setTheme()` 会自动重新加载对应的 light/dark QSS。

新增页面样式时，应：

1. 在 `StyleSheet` 枚举中增加名称。
2. 同时创建 `app/resource/qss/light/` 和 `dark/` 两份同名 QSS。
3. 在页面初始化、对象名设置完成后调用 `.apply(self)`。
4. 实际验证两种主题；避免用内联 QSS 固定文字颜色或背景色。

### `app/view/main_window.py`

主窗口基于 `FluentWindow`，负责窗口大小、图标、Splash、侧边导航、系统主题监听和 Mica 刷新。当前主导航包含可展开的“漫画”、独立“视频”和底部“设置”。“漫画”子路由包括本地资源、收藏、在线资源和历史记录；在线资源只是预留入口。

`SystemThemeListener` 是持有资源的后台监听器，关闭窗口时必须 `terminate()` 和 `deleteLater()`。

### `app/view/media_interface.py`

当前为漫画和视频路由提供统一的轻量占位页面。它只负责标题与说明，不包含扫描、数据库或媒体解析逻辑。各页面通过稳定且唯一的 `objectName` 作为 Fluent 导航 route key；修改名称可能影响路由状态，非必要不要变更。

### `app/view/setting_interface.py`

设置页基于 `ScrollArea`，使用 QFluentWidgets 设置卡片直接绑定 `cfg`。当前包含主题模式、主题色和媒体目录。主题变化链路为：

```text
OptionsSettingCard
  -> cfg.themeMode 更新
  -> cfg.themeChanged
  -> setTheme()
  -> QFluentWidgets 样式管理器刷新所有已注册 QSS
```

媒体目录目前只被写入配置，还没有扫描器消费它。

### 外部 EhViewer 数据源约束

用户本机的 `testData/db/eh.db` 是旧 Android 漫画应用产生的 SQLite 数据库，仅用于兼容分析和开发测试：

- SQLite `user_version=7`。
- 已有表包括 `BOOKMARKS`、`DOWNLOADS`、`DOWNLOAD_DIRNAME`、`DOWNLOAD_LABELS`、`Gallery_Tags`、`HISTORY`、`LOCAL_FAVORITES`、`FILTER`、`QUICK_SEARCH`、`Black_List` 和 `android_metadata`。
- `GID` 是下载信息、目录名、标签、收藏和历史之间的重要关联键。
- 既有表、索引、触发器及数据必须保持原样；访问适配器默认以 SQLite 只读模式打开该库。
- 不允许在 `eh.db` 中新增 RSViewer 表，也不允许对它执行 RSViewer migration。
- RSViewer 自有的媒体索引、路径映射、阅读进度、视频数据和设置扩展必须写入另一份独立 SQLite 文件。

`testData/manga/` 是对应的本地漫画样例。典型结构是一个下载目录包含 `.ehviewer` sidecar 和按页码命名的 WebP 图片。当前样例页码范围为 1–39，但第 19 页缺失，说明下载可能不完整；扫描和阅读逻辑必须自然排序现有页面并容忍缺页，不能假设编号连续。

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

尚未实现。目标数据流应保持以下边界：

```text
本地目录/映射盘/UNC 路径
  -> Source 统一文件访问
  -> Worker 后台扫描与元数据提取
  -> Repository 写入 SQLite
  -> Service 查询、排序、搜索和生成缩略图
  -> View 展示封面库
  -> Reader/Player 打开媒体
  -> Repository 保存阅读或播放进度
```

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

截至 2026-08-03：

- 可启动的 PySide6 Fluent 桌面主窗口。
- 窗口居中、Splash、Fluent 图标和 Windows 系统主题监听。
- 浅色、深色、跟随系统和自定义主题色配置。
- 设置页 light/dark QSS 已接入 Fluent 样式管理器；修复过主题切换时内容区变白、标题颜色错误和明显默认边框的问题。
- 已加入“漫画”导航树（本地资源、收藏、在线资源预留、历史记录）、独立“视频”入口及对应占位页。
- 本地/映射盘/NAS 路径的媒体目录配置入口。
- DPI、语言和 Mica 配置模型。
- 模板 Gallery、演示资源、音乐配置和无用生成资源已清理。
- `README.md`、`requirements.txt` 和 `.gitignore` 已建立。
- 已做过 `compileall`、`git diff --check` 以及无界面窗口/双主题渲染验证。

## 8. 正在开发的内容

漫画和视频导航已经建立，但所有媒体页面仍是占位页。正在推进的是 MVP 媒体库设计，下一项应从“EhViewer 只读数据源适配器 + RSViewer 自有媒体数据模型/SQLite schema + 本地/UNC 扫描服务”开始，而不是继续堆叠界面。

工作区提示：模板清理、设置页重构和主题修复目前仍是未提交改动。`git status` 会显示约 353 个有意删除的跟踪文件以及新的文档/QSS；不要误判为意外丢失后恢复它们。

## 9. 已知问题与技术债

- 配置路径依赖当前工作目录；从其他目录执行 `python path/to/main.py` 可能读写错误位置。应迁移到 `QStandardPaths.AppConfigLocation` 或基于项目/可执行文件的稳定路径。
- `libraryFolders` 只保存目录列表，尚无可达性检测、扫描、断线状态或重连机制；UNC/NAS 实际场景尚未验证。
- 没有媒体数据库、迁移机制、缩略图缓存及缓存失效策略。
- 尚未实现 EhViewer SQLite 只读适配器，当前只是分析并记录了 schema 兼容约束。
- 没有自动化测试，仅有人工/脚本化冒烟验证。
- 语言枚举和 Fluent 翻译器已存在，但 RSViewer 自身的中文文案是硬编码，切换英语不会完整翻译。
- 依赖只声明范围，没有锁定可复现版本；本机验证版本是 PySide6 6.10.1 和 PySide6-Fluent-Widgets 1.10.5。
- 尚无打包和发布流程。
- 代码仍有少量格式和命名可统一，例如私有初始化方法命名、字符串引号和空行；功能开发时顺手改善，避免纯格式大改掩盖业务 diff。

## 10. 下一步计划

按优先级推进：

1. 稳定运行时目录：配置、RSViewer 自有 SQLite 和缓存放到明确的应用数据目录。
2. 实现 EhViewer `eh.db` 只读数据源适配器，保持既有 schema 完全不变。
3. 定义 RSViewer 自有媒体领域模型和独立 SQLite schema，并加入可重复执行的迁移机制。
4. 实现本地目录、映射盘和 UNC 路径扫描；扫描必须后台执行、可取消、可报告进度和错误。
5. 提取图片/视频基础元数据，建立磁盘缩略图缓存。
6. 将漫画本地资源、收藏、历史记录占位页接入查询服务和封面网格。
7. 实现图片/漫画阅读器：自然排序、单页/双页/长图、缩放、翻页和进度。
8. 实现 Qt Multimedia 视频播放器和播放进度。
9. 增加收藏、最近浏览、标签，以及针对数据层和扫描器的自动化测试。
10. 最后再完善在线资源接口、打包、更新和发布流程。

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
