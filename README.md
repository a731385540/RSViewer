# RSViewer

RSViewer 是一个面向个人使用的桌面媒体管理与查看工具，计划用于浏览本地目录和 NAS 中的漫画、图片与视频。

项目当前处于早期开发阶段，已具备基于 PySide6 和 PySide6-Fluent-Widgets 的桌面窗口、主题设置、外部 EhViewer 数据源配置、本地漫画封面分页、搜索、标签筛选、详情预览和漫画阅读器。大型漫画库采用惰性加载：资源列表只读取数据库元数据和根目录，当前页会在后台预读后续三页封面；缺失或损坏的 `.thumb` 自动回退到漫画第一页，打开单本详情时才枚举全部页面。阅读器支持窗口内阅读、全屏、翻页、跳页、缩放、拖动和相邻页面预读；视频播放功能仍待实现。

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
- EhViewer `eh.db` 只读兼容与独立的 RSViewer 用户标签数据库
- SQLite 媒体索引和本地缩略图缓存

## 第三方组件

界面使用 [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)。本项目仅用于个人、非商业用途；使用和分发时仍需遵守相关第三方组件的许可证。
