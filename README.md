# RSViewer

RSViewer 是一个面向个人使用的桌面媒体管理与查看工具，计划用于浏览本地目录和 NAS 中的漫画、图片与视频。

项目当前处于早期开发阶段，已具备基于 PySide6 和 PySide6-Fluent-Widgets 的桌面窗口、主题设置、外部 EhViewer 数据源配置、本地漫画封面分页、搜索、详情预览和漫画阅读器。默认收起的标签栏分为互斥的分类、播放列表和树状归类：分类单选并记忆上次位置，播放列表与归类均支持一本漫画加入多个节点，三类标签均可右键确认删除；播放列表可拖拽或使用按钮编排顺序、继续上次播放，并可在相邻漫画之间前后翻动。资源卡片支持单项右键标签操作，也可开启复选后批量操作；漫画详情会按作者、角色、语言等命名空间分组展示主题化胶囊标签。分类会更新所选 EhViewer 数据库的 `DOWNLOADS.LABEL`，播放列表、归类和播放状态保存在 RSViewer 自有数据库。大型漫画库采用惰性加载，缺失或损坏的 `.thumb` 自动回退到漫画第一页，详情预览固定每页加载 40 张。阅读器支持窗口内/全屏、四向翻页、长图按屏滚动、跳页、缩放、拖动、自动翻页、相邻页面预读和阅读进度恢复；全屏状态仅在鼠标到达上下边缘时显示对应控制栏。EhViewer `.ehviewer` 十六进制页索引可首次导入，后续进度保存在 RSViewer 独立数据库。视频播放功能仍待实现。

## 运行

建议使用 Python 3.10 或更高版本：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## 计划支持

- 本地目录、Windows 映射盘及 UNC 网络共享路径
- 漫画与图片集的封面浏览和阅读进度
- 常见视频格式的管理与播放
- EhViewer `eh.db` 兼容（浏览默认只读，显式分类操作更新 `DOWNLOADS.LABEL`）与独立的 RSViewer 播放列表、树状归类和阅读进度数据库
- SQLite 媒体索引和本地缩略图缓存

## 第三方组件

界面使用 [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)。本项目仅用于个人、非商业用途；使用和分发时仍需遵守相关第三方组件的许可证。
