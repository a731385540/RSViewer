# RSViewer

RSViewer 是一个面向个人使用的桌面媒体管理与查看工具，计划用于浏览本地目录和 NAS 中的漫画、图片与视频。

项目当前处于早期开发阶段，已具备基于 PySide6 和 PySide6-Fluent-Widgets 的桌面窗口、主题设置、媒体目录配置，以及漫画和视频的导航骨架。漫画导航包含本地资源、收藏、在线资源预留入口和历史记录；媒体扫描、封面库、图片阅读和视频播放功能仍待实现。

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
- SQLite 媒体索引和本地缩略图缓存

## 第三方组件

界面使用 [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)。本项目仅用于个人、非商业用途；使用和分发时仍需遵守相关第三方组件的许可证。
