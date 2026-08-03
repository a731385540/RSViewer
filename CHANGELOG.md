# Changelog

本项目的重要变更记录在此文件中。格式参考 Keep a Changelog；项目尚未建立正式版本发布流程，因此当前变更先记录在 `Unreleased`。

## [Unreleased]

### Added

- 建立 RSViewer 桌面应用入口、Fluent 主窗口和设置界面。
- 增加左侧“漫画”导航树，包含本地资源、收藏、在线资源预留入口和历史记录。
- 增加独立的“视频”导航入口。
- 增加媒体功能占位页，为后续服务和视图实现提供稳定路由。
- 增加媒体目录配置，面向本地目录、Windows 映射盘和 UNC/NAS 路径。
- 增加浅色与深色设置页 QSS，并注册到 Fluent 样式管理器。
- 增加 `README.md`、`requirements.txt`、`.gitignore` 和项目维护指南 `AGENTS.md`。

### Changed

- 将本地 `testData/db/eh.db` 明确为只读外部漫画数据源；RSViewer 自有数据必须使用另一份独立 SQLite 数据库。
- 忽略本地测试数据目录和本地 `lib/` Python 环境，避免提交用户数据库、漫画样例和环境文件。
- 将项目定位从 PyQt-Fluent-Widgets 示例骨架明确为个人非商业用途的漫画、图片与视频管理工具。
- 将旧音乐/下载目录配置替换为 `Library/Folders` 媒体库配置。
- 重构 `main.py`，使用显式 `main()` 入口并保留翻译器生命周期。
- 主窗口标题和图标改为 RSViewer 自身标识，不再依赖 Gallery 资源。
- 精简自定义样式枚举，只保留项目实际使用的设置页样式。

### Fixed

- 修复切换深色主题时设置内容区仍为浅色、标题颜色错误以及出现明显 Qt 默认边框的问题。
- 修复媒体目录设置卡片将父组件误传为目录参数的问题。

### Removed

- 删除 `examples/` 下的 PyQt-Fluent-Widgets Gallery 和控件演示副本。
- 删除旧 Gallery 文档、安装脚本、示例图片/音频、翻译、图标和无用公共模块。
- 删除约 26 万行的旧 Qt Gallery 生成资源 `app/common/resource.py`。
- 删除音乐播放器模板中的 `musicFolders`、`downloadFolder` 和第三方示例常量。

### Validation

- `python -m compileall -q main.py app`
- `git diff --check`
- Qt offscreen 主窗口创建与退出冒烟测试
- 浅色/深色主题切换渲染检查
- 漫画子导航、视频路由和全部占位页切换检查
- 外部 `eh.db` 校验哈希前后一致性检查
