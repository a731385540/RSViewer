# RSViewer

RSViewer 是一个面向个人使用的桌面媒体管理与查看工具，计划用于浏览本地目录和 NAS 中的漫画、图片与视频。

项目当前处于早期开发阶段，已具备外部 EhViewer 本地漫画库、分页封面、搜索、详情预览、收藏、本地浏览历史和漫画阅读器。在线资源已预留 `EhOnlineProvider` 接口、后台 Worker、搜索/游标翻页 UI 和 E-Hentai / ExHentai 配置；设置中可填写自己的 EH Cookie/Token，选择系统代理、直连或手动 HTTP(S) 代理并设置超时。默认 provider 不访问网络，具体页面获取与过滤由用户自行实现。默认收起的标签栏分为分类、播放列表和树状归类，支持新增、分配、移至未分类和右键删除；资源卡片右键通过独立的可搜索选择窗口分配分类、播放列表和树状归类，避免大量标签子菜单越界或显示不全，同时支持单项和复选批量操作。资源卡片也可批量收藏，收藏页与历史页复用本地资源的首次加载结果，不会重复扫描大型漫画库。历史页按最近访问展示本地漫画，并预留独立在线历史入口。播放列表支持编排、继续播放和相邻漫画前后翻动；详情按命名空间显示主题化胶囊标签。大型漫画库采用惰性加载，详情预览固定每页 40 张。阅读器支持窗口内/全屏、四向翻页、长图滚动、跳页、缩放、拖动、自动翻页、预读和进度恢复。分类修改目标 EhViewer 既有数据，播放列表、归类、收藏、历史和进度保存在 RSViewer 独立数据库。视频播放功能仍待实现。

## 运行

建议使用 Python 3.10 或更高版本：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## 接入在线爬虫

在线资源 UI 不包含具体站点抓取代码。实现 [EhOnlineProvider](app/sources/eh_online_source.py) 后，在同文件的 `create_eh_online_provider()` 中返回你的实现即可：

```python
class MyEhProvider(EhOnlineProvider):
    def fetch_page(self, query: OnlineGalleryQuery) -> OnlineGalleryPage:
        # 使用 self.settings 中的站点、Cookie、代理和超时获取一页数据
        ...

    def filter_items(self, items, query):
        # query.filters 是为自定义过滤器预留的字典
        return items

    def load_thumbnail(self, url: str) -> bytes:
        # 可选；不实现时在线卡片显示“封面不可用”
        return b""
```

`fetch_page()` 返回的 `OnlineGalleryPage.next_cursor`/`previous_cursor` 可以是站点游标、URL 或你的内部分页令牌，UI 不解释其内容。具体网络客户端、解析方式和过滤规则均由 provider 自己决定。

## 计划支持

- 本地目录、Windows 映射盘及 UNC 网络共享路径
- 漫画与图片集的封面浏览和阅读进度
- 常见视频格式的管理与播放
- EhViewer `eh.db` 兼容（浏览默认只读，显式分类操作更新 `DOWNLOADS.LABEL`）与独立的 RSViewer 播放列表、树状归类和阅读进度数据库
- SQLite 媒体索引和本地缩略图缓存

## 第三方组件

界面使用 [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)。本项目仅用于个人、非商业用途；使用和分发时仍需遵守相关第三方组件的许可证。
