from pathlib import Path

from qfluentwidgets import (
    BodyLabel,
    ColorSettingCard,
    ComboBox,
    CustomColorSettingCard,
    ExpandLayout,
    FolderListSettingCard,
    ListWidget,
    MessageBoxBase,
    OptionsSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SwitchSettingCard,
    SubtitleLabel,
    ToolButton,
    setTheme,
    setThemeColor,
    PushButton,
    LineEdit,
    PasswordLineEdit,
)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar
from PySide6.QtCore import QEvent, QSize, QStandardPaths, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.common.config import cfg
from app.common.style_sheet import StyleSheet
from app.services.gallery_marker import normalize_gallery_marker_rules


class DataPathSettingCard(SettingCard):
    """为单个数据库文件或目录提供明确的路径选择。"""

    pathChanged = Signal(str)

    def __init__(self, config_item, icon, title, content, select_file, parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem = config_item
        self.selectFile = select_file
        self.chooseButton = PushButton(self.tr("选择…"), self)
        self.hBoxLayout.addWidget(self.chooseButton)
        self.hBoxLayout.addSpacing(16)
        self.chooseButton.clicked.connect(self._choosePath)
        self._updateContent(cfg.get(config_item))

    def _choosePath(self):
        current = cfg.get(self.configItem)
        if self.selectFile:
            path, _selected_filter = QFileDialog.getOpenFileName(
                self,
                self.tr("选择 EhViewer 数据库"),
                current,
                self.tr("SQLite 数据库 (*.db *.sqlite *.sqlite3);;所有文件 (*)"),
            )
        else:
            path = QFileDialog.getExistingDirectory(
                self,
                self.tr("选择本地漫画根目录"),
                current,
            )
        if not path:
            return
        cfg.set(self.configItem, path)
        self._updateContent(path)
        self.pathChanged.emit(path)

    def _updateContent(self, path: str):
        self.setContent(path or self.tr("尚未配置"))


class DatabaseExportSettingCard(SettingCard):
    exportRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(
            FIF.SAVE,
            self.tr("导出 EhViewer 数据库"),
            self.tr("从 RSViewer 自有数据生成可供 EhViewer 使用的新 eh.db"),
            parent,
        )
        self.exportButton = PushButton(self.tr("导出…"), self)
        self.exportButton.clicked.connect(self._chooseDestination)
        self.hBoxLayout.addWidget(self.exportButton)
        self.hBoxLayout.addSpacing(16)

    def _chooseDestination(self):
        documents = QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation
        )
        default_path = str(Path(documents or ".") / "eh.db")
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("导出 EhViewer 数据库"),
            default_path,
            self.tr("EhViewer 数据库 (eh.db);;SQLite 数据库 (*.db);;所有文件 (*)"),
        )
        if path:
            self.exportRequested.emit(path)

    def setExporting(self, exporting):
        self.exportButton.setEnabled(not exporting)
        self.exportButton.setText(
            self.tr("正在导出…") if exporting else self.tr("导出…")
        )


class TextConfigSettingCard(SettingCard):
    """A compact text editor bound to a QFluentWidgets config item."""

    def __init__(
        self,
        config_item,
        icon,
        title,
        content,
        parent=None,
        password=False,
        placeholder="",
    ):
        super().__init__(icon, title, content, parent)
        self.configItem = config_item
        editor_class = PasswordLineEdit if password else LineEdit
        self.lineEdit = editor_class(self)
        self.lineEdit.setMinimumWidth(280)
        self.lineEdit.setMaximumWidth(380)
        self.lineEdit.setPlaceholderText(placeholder)
        self.lineEdit.setText(str(cfg.get(config_item) or ""))
        self.hBoxLayout.addWidget(self.lineEdit)
        self.hBoxLayout.addSpacing(16)
        self.lineEdit.editingFinished.connect(self._saveValue)
        config_item.valueChanged.connect(self._syncValue)

    def _saveValue(self):
        cfg.set(self.configItem, self.lineEdit.text().strip())

    def _syncValue(self, value):
        value = str(value or "")
        if self.lineEdit.text() != value:
            self.lineEdit.setText(value)


class OnlineDownloadLabelSettingCard(SettingCard):
    """Select the primary EhViewer label assigned to new online downloads."""

    def __init__(self, parent=None):
        super().__init__(
            FIF.TAG,
            self.tr("默认下载分类"),
            self.tr("新下载的在线画廊写入此分类；已有画廊保留原分类"),
            parent,
        )
        self.configItem = cfg.onlineEhDownloadLabel
        self.comboBox = ComboBox(self)
        self.comboBox.setMinimumWidth(180)
        self.comboBox.setMaximumWidth(280)
        self.hBoxLayout.addWidget(self.comboBox)
        self.hBoxLayout.addSpacing(16)
        self.comboBox.currentIndexChanged.connect(self._saveCurrentLabel)
        self.configItem.valueChanged.connect(self._syncCurrentLabel)
        current = str(cfg.get(self.configItem) or "")
        self._populate((current,) if current else (), current)

    def setLabels(self, labels):
        labels = tuple(
            dict.fromkeys(
                str(label).strip() for label in labels if str(label).strip()
            )
        )
        current = str(cfg.get(self.configItem) or "")
        if current and current not in labels:
            current = ""
            cfg.set(self.configItem, current)
        self._populate(labels, current)

    def _populate(self, labels, current):
        self.comboBox.blockSignals(True)
        self.comboBox.clear()
        self.comboBox.addItem(self.tr("未分类"), userData="")
        for label in labels:
            self.comboBox.addItem(label, userData=label)
        index = self.comboBox.findData(current)
        self.comboBox.setCurrentIndex(max(0, index))
        self.comboBox.blockSignals(False)

    def _saveCurrentLabel(self, _index):
        cfg.set(self.configItem, str(self.comboBox.currentData() or ""))

    def _syncCurrentLabel(self, value):
        index = self.comboBox.findData(str(value or ""))
        if index >= 0 and index != self.comboBox.currentIndex():
            self.comboBox.setCurrentIndex(index)


class GalleryMarkerRuleRow(QWidget):
    removeRequested = Signal(str)

    def __init__(self, rule, parent=None):
        super().__init__(parent)
        self.rule = str(rule)
        self.ruleLabel = BodyLabel(self.rule, self)
        self.ruleLabel.setToolTip(self.rule)
        self.removeButton = ToolButton(FIF.REMOVE, self)
        self.removeButton.setFixedSize(28, 28)
        self.removeButton.setToolTip(self.tr("删除此标记项"))
        self.removeButton.clicked.connect(
            lambda: self.removeRequested.emit(self.rule)
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 4, 3)
        layout.setSpacing(8)
        layout.addWidget(self.ruleLabel, 1)
        layout.addWidget(self.removeButton)


class GalleryMarkerRuleSection(QWidget):
    def __init__(self, title, placeholder, rules=(), parent=None):
        super().__init__(parent)
        self.titleLabel = BodyLabel(title, self)
        self.inputEdit = LineEdit(self)
        self.inputEdit.setPlaceholderText(placeholder)
        self.addButton = ToolButton(FIF.ADD, self)
        self.addButton.setFixedSize(32, 32)
        self.addButton.setToolTip(self.tr("添加标记项"))
        self.addButton.setEnabled(False)
        self.listWidget = ListWidget(self)
        self.listWidget.setFixedHeight(142)
        self.listWidget.setSpacing(2)

        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(8)
        input_layout.addWidget(self.inputEdit, 1)
        input_layout.addWidget(self.addButton)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        layout.addWidget(self.titleLabel)
        layout.addLayout(input_layout)
        layout.addWidget(self.listWidget)

        self.inputEdit.textChanged.connect(
            lambda text: self.addButton.setEnabled(bool(text.strip()))
        )
        self.inputEdit.returnPressed.connect(self.addCurrentRule)
        self.addButton.clicked.connect(self.addCurrentRule)
        self.setRules(rules)

    def rules(self):
        return tuple(
            str(self.listWidget.item(index).data(Qt.UserRole))
            for index in range(self.listWidget.count())
        )

    def setRules(self, rules):
        self.listWidget.clear()
        for rule in normalize_gallery_marker_rules(rules):
            self._appendRule(rule)

    def addCurrentRule(self):
        rule = self.inputEdit.text().strip()
        if not rule:
            return
        existing = {value.casefold() for value in self.rules()}
        if rule.casefold() not in existing:
            self._appendRule(rule)
        self.inputEdit.clear()
        self.inputEdit.setFocus()

    def _appendRule(self, rule):
        item = QListWidgetItem(self.listWidget)
        item.setData(Qt.UserRole, rule)
        item.setSizeHint(QSize(0, 38))
        row = GalleryMarkerRuleRow(rule, self.listWidget)
        row.removeRequested.connect(self.removeRule)
        self.listWidget.setItemWidget(item, row)

    def removeRule(self, rule):
        target = str(rule).casefold()
        for index in range(self.listWidget.count()):
            item = self.listWidget.item(index)
            if str(item.data(Qt.UserRole)).casefold() == target:
                row = self.listWidget.itemWidget(item)
                self.listWidget.takeItem(index)
                if row is not None:
                    row.deleteLater()
                return


class GalleryMarkerRulesDialog(MessageBoxBase):
    def __init__(self, title_rules=(), tag_rules=(), parent=None):
        super().__init__(parent)
        self.widget.setMinimumWidth(620)
        self.titleLabel = SubtitleLabel(self.tr("画廊标记"), self.widget)
        self.titleSection = GalleryMarkerRuleSection(
            self.tr("标题包含"),
            self.tr("输入标题中需要包含的文字"),
            title_rules,
            self.widget,
        )
        self.tagSection = GalleryMarkerRuleSection(
            self.tr("Tag 匹配"),
            self.tr("输入 artist:name 或仅输入 name"),
            tag_rules,
            self.widget,
        )
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.titleSection)
        self.viewLayout.addWidget(self.tagSection)
        self.yesButton.setText(self.tr("保存"))
        self.cancelButton.setText(self.tr("取消"))

    def titleRules(self):
        return self.titleSection.rules()

    def tagRules(self):
        return self.tagSection.rules()


class GalleryMarkerSettingCard(SettingCard):
    def __init__(self, parent=None):
        super().__init__(FIF.FLAG, self.tr("画廊标记"), " ", parent)
        self.configureButton = PushButton(self.tr("配置…"), self)
        self.configureButton.clicked.connect(self._openDialog)
        self.hBoxLayout.addWidget(self.configureButton)
        self.hBoxLayout.addSpacing(16)
        cfg.onlineEhMarkerTitleRules.valueChanged.connect(self._updateContent)
        cfg.onlineEhMarkerTagRules.valueChanged.connect(self._updateContent)
        self._updateContent()

    def _openDialog(self):
        dialog = GalleryMarkerRulesDialog(
            cfg.get(cfg.onlineEhMarkerTitleRules),
            cfg.get(cfg.onlineEhMarkerTagRules),
            self.window(),
        )
        if not dialog.exec():
            return
        cfg.set(cfg.onlineEhMarkerTitleRules, list(dialog.titleRules()))
        cfg.set(cfg.onlineEhMarkerTagRules, list(dialog.tagRules()))

    def _updateContent(self, _value=None):
        title_count = len(
            normalize_gallery_marker_rules(cfg.get(cfg.onlineEhMarkerTitleRules))
        )
        tag_count = len(
            normalize_gallery_marker_rules(cfg.get(cfg.onlineEhMarkerTagRules))
        )
        self.setContent(
            self.tr("标题 {} 项 · Tag {} 项").format(title_count, tag_count)
        )


class ShortcutCaptureButton(PushButton):
    """点击后捕获一次键盘输入，组合键按下即确认。"""

    sequenceCaptured = Signal(str)

    def __init__(self, sequence: str, parent=None):
        self._sequence = sequence
        self._capturing = False
        super().__init__(parent)
        self.setProperty("capturesShortcut", True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(150)
        self.setMaximumWidth(190)
        self.clicked.connect(self.beginCapture)
        self._updateText()

    @property
    def sequence(self) -> str:
        return self._sequence

    def setSequence(self, sequence: str):
        self._sequence = sequence
        if not self._capturing:
            self._updateText()

    def beginCapture(self):
        self._capturing = True
        self.setText(self.tr("请按下快捷键…"))
        self.setFocus(Qt.MouseFocusReason)
        self.grabKeyboard()

    def cancelCapture(self):
        if not self._capturing:
            return
        self._capturing = False
        self.releaseKeyboard()
        self._updateText()

    def event(self, event):
        if self._capturing and event.type() == QEvent.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self._capturing:
            return super().keyPressEvent(event)
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.cancelCapture()
            event.accept()
            return
        if event.key() in {
            Qt.Key_Control,
            Qt.Key_Shift,
            Qt.Key_Alt,
            Qt.Key_Meta,
        }:
            event.accept()
            return

        sequence = QKeySequence(event.keyCombination()).toString(
            QKeySequence.PortableText
        )
        if sequence:
            self._sequence = sequence
            self._capturing = False
            self.releaseKeyboard()
            self._updateText()
            self.sequenceCaptured.emit(sequence)
        event.accept()

    def focusOutEvent(self, event):
        self.cancelCapture()
        super().focusOutEvent(event)

    def _updateText(self):
        self.setText(self._sequence or self.tr("点击后按键"))


class ShortcutSettingCard(SettingCard):
    """使用按键捕获按钮绑定 QFluentWidgets 配置项。"""

    def __init__(self, config_item, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem = config_item
        self.captureButton = ShortcutCaptureButton(cfg.get(config_item), self)
        self.hBoxLayout.addWidget(self.captureButton)
        self.hBoxLayout.addSpacing(16)
        self.captureButton.sequenceCaptured.connect(self._saveShortcut)
        config_item.valueChanged.connect(self.captureButton.setSequence)

    def _saveShortcut(self, shortcut: str):
        cfg.set(self.configItem, shortcut)


class SettingInterface(ScrollArea):
    """ Setting interface """

    dataSourceChanged = Signal()
    ehViewerExportRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.settingLabel = QLabel(self.tr("设置"), self)

        self.dataSourceGroup = SettingCardGroup(
            self.tr("本地漫画数据源"), self.scrollWidget)
        self.personalGroup = SettingCardGroup(
            self.tr("界面设置"), self.scrollWidget)
        self.shortcutGroup = SettingCardGroup(
            self.tr("快捷键"), self.scrollWidget)
        self.readerGroup = SettingCardGroup(
            self.tr("漫画阅读器"), self.scrollWidget)
        self.onlineGroup = SettingCardGroup(
            self.tr("在线资源"), self.scrollWidget)
        self.themeCard = OptionsSettingCard(
            cfg.themeMode,
            FIF.BRUSH,
            self.tr("应用主题"),
            self.tr("更改应用的明暗外观"),
            texts=[
                self.tr("浅色"), self.tr("深色"),
                self.tr("跟随系统")
            ],
            parent=self.personalGroup
        )
        self.themeColorCard = CustomColorSettingCard(
            cfg.themeColor,
            FIF.PALETTE,
            self.tr("主题色"),
            self.tr("更改应用的强调色"),
            self.personalGroup
        )
        self.ehViewerDatabaseCard = DatabaseExportSettingCard(
            self.dataSourceGroup
        )
        self.ehViewerMangaRootCard = DataPathSettingCard(
            cfg.ehViewerMangaRoot,
            FIF.FOLDER,
            self.tr("本地漫画根目录"),
            self.tr("EhViewer 下载目录，可选择本地盘、映射盘或 UNC 路径"),
            False,
            self.dataSourceGroup,
        )
        self.libraryFoldersCard = FolderListSettingCard(
            cfg.libraryFolders,
            self.tr("其他媒体目录"),
            self.tr("添加图片或视频使用的本地、映射盘或 NAS 路径"),
            parent=self.personalGroup,
        )
        self.onlineSiteCard = OptionsSettingCard(
            cfg.onlineEhSite,
            FIF.GLOBE,
            self.tr("默认站点"),
            self.tr("在线资源页可随时在 E-Hentai 与 ExHentai 之间切换"),
            texts=["E-Hentai", "ExHentai"],
            parent=self.onlineGroup,
        )
        self.onlineCookieCard = TextConfigSettingCard(
            cfg.onlineEhCookie,
            FIF.FINGERPRINT,
            self.tr("EH Token / Cookie"),
            self.tr("建议粘贴完整 Cookie；内容仅保存在本机配置，不会写入导出的 eh.db"),
            self.onlineGroup,
            password=True,
            placeholder="ipb_member_id=...; ipb_pass_hash=...; igneous=...",
        )
        self.onlineProxyModeCard = OptionsSettingCard(
            cfg.onlineEhProxyMode,
            FIF.GLOBE,
            self.tr("网络代理"),
            self.tr("跟随 Windows/环境代理、完全直连或使用手动 HTTP(S) 代理"),
            texts=[self.tr("跟随系统"), self.tr("直连"), self.tr("手动设置")],
            parent=self.onlineGroup,
        )
        self.onlineManualProxyCard = TextConfigSettingCard(
            cfg.onlineEhManualProxy,
            FIF.WIFI,
            self.tr("手动代理地址"),
            self.tr("例如 http://127.0.0.1:7890；仅在手动设置模式下使用"),
            self.onlineGroup,
            placeholder="http://127.0.0.1:7890",
        )
        self.onlineTimeoutCard = OptionsSettingCard(
            cfg.onlineEhRequestTimeout,
            FIF.SPEED_HIGH,
            self.tr("请求超时"),
            self.tr("提供给在线 provider 的单次请求超时时间"),
            texts=[
                self.tr("10 秒"),
                self.tr("20 秒"),
                self.tr("30 秒"),
                self.tr("60 秒"),
            ],
            parent=self.onlineGroup,
        )
        self.onlineViewModeCard = OptionsSettingCard(
            cfg.onlineEhViewMode,
            FIF.VIEW,
            self.tr("默认展示视图"),
            self.tr("在线资源页切换视图时会同时更新此默认设置"),
            texts=[self.tr("卡片"), self.tr("精简列表"), "Extended"],
            parent=self.onlineGroup,
        )
        self.onlineThumbnailConcurrencyCard = OptionsSettingCard(
            cfg.onlineEhThumbnailConcurrency,
            FIF.DOWNLOAD,
            self.tr("封面并发请求数"),
            self.tr("同时加载在线画廊封面的后台任务数量"),
            texts=[
                self.tr("1 个"),
                self.tr("2 个"),
                self.tr("4 个"),
                self.tr("6 个"),
                self.tr("8 个"),
                self.tr("12 个"),
            ],
            parent=self.onlineGroup,
        )
        self.onlineDownloadConcurrencyCard = OptionsSettingCard(
            cfg.onlineEhDownloadConcurrency,
            FIF.DOWNLOAD,
            self.tr("画廊下载并发数"),
            self.tr("同时下载画廊的后台任务数量，范围 1–3"),
            texts=[
                self.tr("1 个"),
                self.tr("2 个"),
                self.tr("3 个"),
            ],
            parent=self.onlineGroup,
        )
        self.onlineDownloadThreadsCard = OptionsSettingCard(
            cfg.onlineEhDownloadThreads,
            FIF.DOWNLOAD,
            self.tr("图片下载线程数"),
            self.tr("所有正在下载的画廊共用这些线程，范围 1–6"),
            texts=[
                self.tr("1 个"),
                self.tr("2 个"),
                self.tr("3 个"),
                self.tr("4 个"),
                self.tr("5 个"),
                self.tr("6 个"),
            ],
            parent=self.onlineGroup,
        )
        self.onlineDownloadLabelCard = OnlineDownloadLabelSettingCard(
            self.onlineGroup
        )
        self.onlineGalleryMarkerCard = GalleryMarkerSettingCard(self.onlineGroup)
        self.onlineThumbnailCacheHoursCard = OptionsSettingCard(
            cfg.onlineEhThumbnailCacheHours,
            FIF.HISTORY,
            self.tr("封面缓存过期时间"),
            self.tr("过期后再次显示封面时会重新下载并更新本地缓存"),
            texts=[
                self.tr("1 小时"),
                self.tr("6 小时"),
                self.tr("12 小时"),
                self.tr("1 天"),
                self.tr("3 天"),
                self.tr("7 天"),
                self.tr("30 天"),
            ],
            parent=self.onlineGroup,
        )
        self._updateManualProxyEnabled(cfg.get(cfg.onlineEhProxyMode))
        self.searchShortcutCard = ShortcutSettingCard(
            cfg.searchShortcut,
            FIF.SEARCH,
            self.tr("展开漫画搜索栏"),
            self.tr("切换到本地资源并展开、聚焦搜索栏"),
            self.shortcutGroup,
        )
        self.tagSidebarShortcutCard = ShortcutSettingCard(
            cfg.tagSidebarShortcut,
            FIF.TAG,
            self.tr("展开或收起漫画标签栏"),
            self.tr("切换到本地资源并切换分类、播放列表和归类侧栏"),
            self.shortcutGroup,
        )
        self.backShortcutCard = ShortcutSettingCard(
            cfg.backShortcut,
            FIF.RETURN,
            self.tr("返回上一级"),
            self.tr("详情页等下一级页面的通用返回快捷键"),
            self.shortcutGroup,
        )
        self.mangaSearchHoverCard = SwitchSettingCard(
            FIF.SEARCH,
            self.tr("鼠标悬停展开搜索栏"),
            self.tr("移到搜索按钮时自动展开；搜索词为空且鼠标离开后自动收起"),
            cfg.mangaSearchHoverEnabled,
            self.personalGroup,
        )
        self.searchHistoryLimitCard = OptionsSettingCard(
            cfg.searchHistoryLimit,
            FIF.HISTORY,
            self.tr("搜索历史记录上限"),
            self.tr("本地与在线资源共用；新搜索会保留在最近记录中"),
            texts=[
                self.tr("5 条"),
                self.tr("10 条"),
                self.tr("15 条"),
                self.tr("20 条"),
            ],
            parent=self.personalGroup,
        )
        self.readerBackgroundCard = ColorSettingCard(
            cfg.readerBackgroundColor,
            FIF.PALETTE,
            self.tr("阅读背景颜色"),
            self.tr("设置漫画图片周围的画布颜色"),
            self.readerGroup,
        )
        self.readerDirectionCard = OptionsSettingCard(
            cfg.readerPageDirection,
            FIF.MOVE,
            self.tr("翻页方向"),
            self.tr("括号中的方向键用于翻到下一页"),
            texts=[
                self.tr("从左向右（←）"),
                self.tr("从右向左（→）"),
                self.tr("从上向下（↑）"),
                self.tr("从下向上（↓）"),
            ],
            parent=self.readerGroup,
        )
        self.readerImageLoadSizeCard = OptionsSettingCard(
            cfg.readerImageLoadSize,
            FIF.FIT_PAGE,
            self.tr("图片载入大小"),
            self.tr("控制图片首次显示时的缩放方式"),
            texts=[
                self.tr("适应窗口"),
                self.tr("适应宽度（长图）"),
                self.tr("原始大小"),
            ],
            parent=self.readerGroup,
        )
        self.readerScrollShortcutCard = ShortcutSettingCard(
            cfg.readerScrollShortcut,
            FIF.SCROLL,
            self.tr("向前滚动"),
            self.tr("长图模式下滚动一屏，到底后进入下一页"),
            self.readerGroup,
        )
        self.readerNextMangaShortcutCard = ShortcutSettingCard(
            cfg.readerNextMangaShortcut,
            FIF.PAGE_RIGHT,
            self.tr("下一本漫画"),
            self.tr("阅读分类或归类时，直接进入序列中的下一本"),
            self.readerGroup,
        )
        self.readerAutoPageCard = SwitchSettingCard(
            FIF.PLAY,
            self.tr("自动翻页"),
            self.tr("按设定间隔自动进入下一页"),
            cfg.readerAutoPageEnabled,
            self.readerGroup,
        )
        self.readerAutoPageIntervalCard = OptionsSettingCard(
            cfg.readerAutoPageInterval,
            FIF.SPEED_HIGH,
            self.tr("自动翻页间隔"),
            self.tr("每次自动翻页等待的时间"),
            texts=[
                self.tr("2 秒"),
                self.tr("3 秒"),
                self.tr("5 秒"),
                self.tr("8 秒"),
                self.tr("10 秒"),
                self.tr("15 秒"),
                self.tr("30 秒"),
            ],
            parent=self.readerGroup,
        )

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName('settingInterface')

        self.scrollWidget.setObjectName('scrollWidget')
        self.settingLabel.setObjectName('settingLabel')
        StyleSheet.SETTING_INTERFACE.apply(self)
        self.__initLayout()
        self.__connectSignalToSlot()

    # 定义布局
    def __initLayout(self):
        self.settingLabel.move(36, 30)

        self.dataSourceGroup.addSettingCard(self.ehViewerDatabaseCard)
        self.dataSourceGroup.addSettingCard(self.ehViewerMangaRootCard)
        self.onlineGroup.addSettingCard(self.onlineSiteCard)
        self.onlineGroup.addSettingCard(self.onlineCookieCard)
        self.onlineGroup.addSettingCard(self.onlineProxyModeCard)
        self.onlineGroup.addSettingCard(self.onlineManualProxyCard)
        self.onlineGroup.addSettingCard(self.onlineTimeoutCard)
        self.onlineGroup.addSettingCard(self.onlineViewModeCard)
        self.onlineGroup.addSettingCard(self.onlineThumbnailConcurrencyCard)
        self.onlineGroup.addSettingCard(self.onlineDownloadConcurrencyCard)
        self.onlineGroup.addSettingCard(self.onlineDownloadThreadsCard)
        self.onlineGroup.addSettingCard(self.onlineDownloadLabelCard)
        self.onlineGroup.addSettingCard(self.onlineGalleryMarkerCard)
        self.onlineGroup.addSettingCard(self.onlineThumbnailCacheHoursCard)
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.libraryFoldersCard)
        self.personalGroup.addSettingCard(self.mangaSearchHoverCard)
        self.personalGroup.addSettingCard(self.searchHistoryLimitCard)
        self.readerGroup.addSettingCard(self.readerBackgroundCard)
        self.readerGroup.addSettingCard(self.readerDirectionCard)
        self.readerGroup.addSettingCard(self.readerImageLoadSizeCard)
        self.readerGroup.addSettingCard(self.readerScrollShortcutCard)
        self.readerGroup.addSettingCard(self.readerNextMangaShortcutCard)
        self.readerGroup.addSettingCard(self.readerAutoPageCard)
        self.readerGroup.addSettingCard(self.readerAutoPageIntervalCard)
        self.shortcutGroup.addSettingCard(self.searchShortcutCard)
        self.shortcutGroup.addSettingCard(self.tagSidebarShortcutCard)
        self.shortcutGroup.addSettingCard(self.backShortcutCard)
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.expandLayout.addWidget(self.dataSourceGroup)
        self.expandLayout.addWidget(self.onlineGroup)
        self.expandLayout.addWidget(self.personalGroup)
        self.expandLayout.addWidget(self.readerGroup)
        self.expandLayout.addWidget(self.shortcutGroup)

    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            self.tr("更新成功"),
            self.tr("配置将在重启后生效"),
            duration=1500,
            parent=self
        )

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.appRestartSig.connect(self.__showRestartTooltip)

        cfg.themeChanged.connect(setTheme)
        self.themeColorCard.colorChanged.connect(lambda c: setThemeColor(c))
        self.ehViewerDatabaseCard.exportRequested.connect(
            self.ehViewerExportRequested
        )
        self.ehViewerMangaRootCard.pathChanged.connect(self.dataSourceChanged)
        cfg.onlineEhProxyMode.valueChanged.connect(self._updateManualProxyEnabled)

    def _updateManualProxyEnabled(self, mode):
        self.onlineManualProxyCard.setEnabled(mode == "manual")

    def setOnlineDownloadLabels(self, labels):
        self.onlineDownloadLabelCard.setLabels(labels)
