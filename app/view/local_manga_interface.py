import math
import sqlite3
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QEvent, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFontMetrics,
    QImage,
    QImageReader,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QDialog,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    SegmentedWidget,
    SegmentedToolWidget,
    SimpleCardWidget,
    SpinBox,
    SubtitleLabel,
    ToolButton,
    TreeWidget,
    TitleLabel,
    TransparentPushButton,
    isDarkTheme,
    themeColor,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import cfg
from app.domain.manga import MangaItem
from app.domain.online_download import (
    DOWNLOAD_MODE_STANDARD,
    ONLINE_DOWNLOAD_COMPLETED,
    ORIGINAL_STATE_ACTIVE,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.manga_classification_index import MangaClassificationIndex
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.eh_tag_search_line_edit import EhTagSearchLineEdit
from app.view.gallery_state_indicator import (
    DOWNLOAD_COMPLETE,
    DOWNLOAD_INCOMPLETE,
    GalleryStateIndicator,
    READING_COMPLETE,
    READING_NONE,
    READING_PARTIAL,
)
from app.workers.similar_manga_worker import SimilarMangaWorker


ORIGINAL_PENDING_BORDER_COLOR = "#B8860B"
ORIGINAL_FALLBACK_BADGE_COLOR = "#A855F7"


class FluentSplitterHandle(QSplitterHandle):
    """Wide hit target with the same subtle line used by navigation resize."""

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._hovered = False
        self._dragging = False
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAutoFillBackground(False)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        color = themeColor()
        color.setAlpha(190 if self._hovered or self._dragging else 45)
        painter = QPainter(self)
        painter.setPen(QPen(color, 1))
        if self.orientation() == Qt.Horizontal:
            x = self.width() // 2
            painter.drawLine(x, 0, x, self.height())
        else:
            y = self.height() // 2
            painter.drawLine(0, y, self.width(), y)


class FluentSplitter(QSplitter):
    def createHandle(self):
        return FluentSplitterHandle(self.orientation(), self)


class FadeTextLabel(QLabel):
    """在宽度不足时让文本末端渐隐，完整内容保留在工具提示中。"""

    def __init__(self, text="", muted=False, parent=None):
        super().__init__(text, parent)
        self.muted = muted
        self.setFixedHeight(22 if not muted else 18)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setToolTip(text)

    def setText(self, text: str):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        text = self.text()
        if not text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(self.font())
        rect = self.contentsRect()
        color = QColor(255, 255, 255) if isDarkTheme() else QColor(0, 0, 0)
        if self.muted:
            color.setAlpha(155)

        text_width = QFontMetrics(self.font()).horizontalAdvance(text)
        if text_width <= rect.width():
            painter.setPen(color)
        else:
            fade_width = min(52, max(24, rect.width() // 4))
            fade_start = max(0.0, (rect.width() - fade_width) / max(1, rect.width()))
            transparent = QColor(color)
            transparent.setAlpha(0)
            gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
            gradient.setColorAt(0.0, color)
            gradient.setColorAt(fade_start, color)
            gradient.setColorAt(1.0, transparent)
            painter.setPen(QPen(QBrush(gradient), 1))

        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine, text)


class CoverLabel(QWidget):
    """按封面比例裁切图片并绘制圆角。"""

    def __init__(
        self,
        image_path: Path,
        image=None,
        defer_load=False,
        parent=None,
    ):
        super().__init__(parent)
        if image is not None and not image.isNull():
            self._pixmap = QPixmap.fromImage(image)
        elif defer_load:
            self._pixmap = QPixmap()
        else:
            self._pixmap = QPixmap(str(image_path))
        self._display_pixmap = QPixmap()
        self._loading = defer_load and image is None
        self.setMinimumSize(72, 96)
        self._rebuildDisplayPixmap()

    def setImage(self, image):
        self._loading = False
        self._pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        self._rebuildDisplayPixmap()
        self.update()

    def resizeEvent(self, event):
        self._rebuildDisplayPixmap()
        super().resizeEvent(event)

    def _rebuildDisplayPixmap(self):
        if self._pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            self._display_pixmap = QPixmap()
            return
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        source_x = max(0, (scaled.width() - self.width()) // 2)
        source_y = max(0, (scaled.height() - self.height()) // 2)
        self._display_pixmap = scaled.copy(
            source_x,
            source_y,
            self.width(),
            self.height(),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        path = QPainterPath()
        path.addRoundedRect(self.rect(), 8, 8)
        painter.setClipPath(path)

        if self._display_pixmap.isNull():
            placeholder = self.palette().color(QPalette.AlternateBase)
            painter.fillRect(self.rect(), placeholder)
            painter.setPen(self.palette().color(QPalette.PlaceholderText))
            text = self.tr("加载中…") if self._loading else self.tr("无封面")
            painter.drawText(self.rect(), Qt.AlignCenter, text)
            return

        painter.drawPixmap(0, 0, self._display_pixmap)


def visible_tags(item: MangaItem) -> str:
    plain_tags = [tag for tag in item.tags if ":" not in tag]
    return " · ".join(plain_tags[:4])


def manga_metadata_text(item: MangaItem, translate) -> str:
    parts = [item.category_name]
    if item.page_count:
        parts.append(translate("{} 页").format(item.page_count))
    if item.progress_page_number is not None:
        if item.page_count:
            parts.append(
                translate("进度 {}/{}").format(
                    min(item.progress_page_number, item.page_count),
                    item.page_count,
                )
            )
        else:
            parts.append(translate("进度第 {} 页").format(item.progress_page_number))
    return " · ".join(parts)


def paint_manga_download_state(card):
    item = card.item
    painter = QPainter(card)
    painter.setRenderHint(QPainter.Antialiasing)
    rect = card.rect().adjusted(1, 1, -1, -1)
    if item.original_state == ORIGINAL_STATE_ACTIVE:
        gradient = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        gradient.setColorAt(0.0, QColor("#f1c453"))
        gradient.setColorAt(0.33, QColor("#d85dc7"))
        gradient.setColorAt(0.66, QColor("#4abed1"))
        gradient.setColorAt(1.0, QColor("#f1c453"))
        painter.setPen(QPen(QBrush(gradient), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 7, 7)
    elif item.original_state:
        painter.setPen(QPen(QColor(ORIGINAL_PENDING_BORDER_COLOR), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 7, 7)

def local_gallery_states(item):
    download_state = (
        DOWNLOAD_COMPLETE
        if getattr(item, "download_complete", None) is True
        and not bool(getattr(item, "standard_download_pending", False))
        else DOWNLOAD_INCOMPLETE
    )
    if bool(getattr(item, "reading_completed", False)):
        reading_state = READING_COMPLETE
    elif getattr(item, "progress_page_index", None) is not None:
        reading_state = READING_PARTIAL
    else:
        reading_state = READING_NONE
    return download_state, reading_state



class OriginalFallbackBadge(QWidget):
    """Paint above card children so the source limitation marker stays visible."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(15, 15)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#F3E8FF"), 1.5))
        painter.setBrush(QColor(ORIGINAL_FALLBACK_BADGE_COLOR))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


def update_original_fallback_badge(card):
    visible = bool(card.item.original_fallback_to_standard)
    card.originalFallbackBadge.setVisible(visible)
    if not visible:
        return
    left = 40 if card.selectionMode else 10
    card.originalFallbackBadge.move(left, 10)
    card.originalFallbackBadge.setToolTip(
        card.tr("部分页面没有原图；这些页面已使用基础图")
    )
    card.originalFallbackBadge.raise_()


class MangaGridCard(CardWidget):
    """大封面漫画卡片。"""

    def __init__(
        self,
        item: MangaItem,
        open_callback=None,
        label_menu_callback=None,
        selection_callback=None,
        selection_mode=False,
        selected=False,
        cover_image=None,
        parent=None,
    ):
        super().__init__(parent)
        self.item = item
        self.openCallback = open_callback
        self.labelMenuCallback = label_menu_callback
        self.selectionCallback = selection_callback
        self.selectionMode = bool(selection_mode)
        self.clicked.connect(self._handleCardClick)
        self.selectionCheckBox = CheckBox(self)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.setVisible(self.selectionMode)
        self.selectionCheckBox.clicked.connect(self._handleSelectionClick)
        self.selectionCheckBox.move(14, 14)
        self.selectionCheckBox.raise_()
        self._updateDownloadStateToolTip()
        self.coverLabel = CoverLabel(
            item.cover_image_path,
            image=cover_image,
            defer_load=True,
            parent=self,
        )
        self.titleLabel = FadeTextLabel(item.display_title, parent=self)
        self.englishTitleLabel = FadeTextLabel(
            item.secondary_title,
            muted=True,
            parent=self,
        )
        self.metaLabel = CaptionLabel(
            manga_metadata_text(item, self.tr),
            self,
        )

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(5)
        self.layout.addWidget(self.coverLabel)
        self.layout.addWidget(self.titleLabel)
        self.layout.addWidget(self.englishTitleLabel)
        self.layout.addWidget(self.metaLabel)
        self.selectionCheckBox.raise_()
        self.originalFallbackBadge = OriginalFallbackBadge(self)
        self.stateIndicator = GalleryStateIndicator(self)
        self._updateDownloadStateBadge()

    def setItem(self, item: MangaItem):
        self.item = item
        self.titleLabel.setText(item.display_title)
        self.englishTitleLabel.setText(item.secondary_title)
        self.metaLabel.setText(manga_metadata_text(item, self.tr))
        self._updateDownloadStateToolTip()
        self._updateDownloadStateBadge()
        self.update()

    def _updateDownloadStateToolTip(self):
        self.setToolTip(
            self.tr("部分页面没有原图；这些页面已使用基础图")
            if self.item.original_fallback_to_standard
            else ""
        )

    def _updateDownloadStateBadge(self):
        update_original_fallback_badge(self)
        self.stateIndicator.setStates(*local_gallery_states(self.item))
        self.stateIndicator.move(self.width() - 26, 10)
        self.stateIndicator.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._updateDownloadStateBadge()

    def paintEvent(self, event):
        super().paintEvent(event)
        paint_manga_download_state(self)

    def setSelectionState(self, selection_mode: bool, selected: bool):
        self.selectionMode = bool(selection_mode)
        self.selectionCheckBox.blockSignals(True)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.blockSignals(False)
        self.selectionCheckBox.setVisible(self.selectionMode)
        self.selectionCheckBox.raise_()
        self._updateDownloadStateToolTip()
        self._updateDownloadStateBadge()

    def _handleCardClick(self):
        if self.selectionMode and self.selectionCallback is not None:
            self.selectionCallback(
                self.item.gid, not self.selectionCheckBox.isChecked()
            )
        elif self.openCallback is not None:
            self.openCallback(self.item)

    def _handleSelectionClick(self, checked: bool):
        if self.selectionCallback is not None:
            self.selectionCallback(self.item.gid, checked)

    def setCardWidth(self, width: int):
        width = max(150, width)
        cover_width = width - 20
        cover_height = round(cover_width * 1.36)
        self.setFixedWidth(width)
        self.coverLabel.setFixedSize(cover_width, cover_height)
        self.setFixedHeight(cover_height + 92)

    def contextMenuEvent(self, event):
        if self.labelMenuCallback is not None:
            self.labelMenuCallback(self.item, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MangaListCard(CardWidget):
    """一行一个条目的标题布局卡片。"""

    def __init__(
        self,
        item: MangaItem,
        open_callback=None,
        label_menu_callback=None,
        selection_callback=None,
        selection_mode=False,
        selected=False,
        cover_image=None,
        parent=None,
    ):
        super().__init__(parent)
        self.item = item
        self.openCallback = open_callback
        self.labelMenuCallback = label_menu_callback
        self.selectionCallback = selection_callback
        self.selectionMode = bool(selection_mode)
        self.clicked.connect(self._handleCardClick)
        self.selectionCheckBox = CheckBox(self)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.setVisible(self.selectionMode)
        self.selectionCheckBox.clicked.connect(self._handleSelectionClick)
        self.selectionCheckBox.move(14, 14)
        self.selectionCheckBox.raise_()
        self._updateDownloadStateToolTip()
        self.setFixedHeight(116)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.coverLabel = CoverLabel(
            item.cover_image_path,
            image=cover_image,
            defer_load=True,
            parent=self,
        )
        self.coverLabel.setFixedSize(72, 96)
        self.titleLabel = FadeTextLabel(item.display_title, parent=self)
        self.englishTitleLabel = FadeTextLabel(
            item.secondary_title,
            muted=True,
            parent=self,
        )
        self.metaLabel = CaptionLabel(
            manga_metadata_text(item, self.tr),
            self,
        )
        self.tagsLabel = FadeTextLabel(visible_tags(item), muted=True, parent=self)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 4, 4, 4)
        text_layout.setSpacing(3)
        text_layout.addWidget(self.titleLabel)
        text_layout.addWidget(self.englishTitleLabel)
        text_layout.addWidget(self.metaLabel)
        text_layout.addWidget(self.tagsLabel)
        text_layout.addStretch(1)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(14)
        self.layout.addWidget(self.coverLabel)
        self.layout.addLayout(text_layout, 1)
        self.selectionCheckBox.raise_()
        self.originalFallbackBadge = OriginalFallbackBadge(self)
        self.stateIndicator = GalleryStateIndicator(self)
        self._updateDownloadStateBadge()

    def setItem(self, item: MangaItem):
        self.item = item
        self.titleLabel.setText(item.display_title)
        self.englishTitleLabel.setText(item.secondary_title)
        self.metaLabel.setText(manga_metadata_text(item, self.tr))
        self.tagsLabel.setText(visible_tags(item))
        self._updateDownloadStateToolTip()
        self._updateDownloadStateBadge()
        self.update()

    def _updateDownloadStateToolTip(self):
        self.setToolTip(
            self.tr("部分页面没有原图；这些页面已使用基础图")
            if self.item.original_fallback_to_standard
            else ""
        )

    def _updateDownloadStateBadge(self):
        update_original_fallback_badge(self)
        self.stateIndicator.setStates(*local_gallery_states(self.item))
        self.stateIndicator.move(self.width() - 26, 10)
        self.stateIndicator.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._updateDownloadStateBadge()

    def paintEvent(self, event):
        super().paintEvent(event)
        paint_manga_download_state(self)

    def setSelectionState(self, selection_mode: bool, selected: bool):
        self.selectionMode = bool(selection_mode)
        self.selectionCheckBox.blockSignals(True)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.blockSignals(False)
        self.selectionCheckBox.setVisible(self.selectionMode)
        self.selectionCheckBox.raise_()
        self._updateDownloadStateBadge()

    def _handleCardClick(self):
        if self.selectionMode and self.selectionCallback is not None:
            self.selectionCallback(
                self.item.gid, not self.selectionCheckBox.isChecked()
            )
        elif self.openCallback is not None:
            self.openCallback(self.item)

    def _handleSelectionClick(self, checked: bool):
        if self.selectionCallback is not None:
            self.selectionCallback(self.item.gid, checked)

    def contextMenuEvent(self, event):
        if self.labelMenuCallback is not None:
            self.labelMenuCallback(self.item, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CoverPreloadSignals(QObject):
    imageReady = Signal(int, object)
    progressReady = Signal(int, int)
    finished = Signal()


class CoverPreloadWorker(QRunnable):
    """后台预读当前页和后续页封面，并为缺失缩略图的漫画寻找第一页。"""

    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
        items,
    ):
        super().__init__()
        self.source = source
        self.user_repository = user_repository
        self.items = tuple(items)
        self.cancelled = False
        self.signals = CoverPreloadSignals()

    def run(self):
        for item in self.items:
            if self.cancelled:
                return
            if item.progress_page_index is None:
                try:
                    progress = self.user_repository.resolve_progress(
                        item.gid,
                        self.source.read_ehviewer_progress(item),
                    )
                    if progress is not None:
                        self.signals.progressReady.emit(item.gid, progress)
                except (OSError, RuntimeError, sqlite3.Error):
                    pass
            image = QImage()
            try:
                cover_path = self.source.find_cover_path(item)
                image = self._readImage(cover_path)
                if image.isNull():
                    first_page = self.source.find_first_page_path(item)
                    if first_page != cover_path:
                        image = self._readImage(first_page)
            except (OSError, RuntimeError):
                image = QImage()
            if self.cancelled:
                return
            try:
                self.signals.imageReady.emit(item.gid, image)
            except RuntimeError:
                return
        try:
            self.signals.finished.emit()
        except RuntimeError:
            pass

    @staticmethod
    def _readImage(path):
        if path is None:
            return QImage()
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid():
            source_size.scale(QSize(180, 245), Qt.KeepAspectRatio)
            reader.setScaledSize(source_size)
        return reader.read()


class MangaLoadSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class MangaLoadWorker(QRunnable):
    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
    ):
        super().__init__()
        self.source = source
        self.user_repository = user_repository
        self.signals = MangaLoadSignals()
        self.cancelled = False

    def run(self):
        try:
            items = self.source.list_local_manga()
            items = self.enrichItems(self.user_repository, items)
            if not self.cancelled:
                try:
                    self.signals.loaded.emit(
                        (
                            items,
                            self.source.list_primary_labels(),
                            self.user_repository.list_taxonomy_labels(),
                        )
                    )
                except RuntimeError:
                    pass
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass

    @classmethod
    def enrichItems(cls, user_repository, items):
        items = list(items)
        gids = [item.gid for item in items]
        taxonomy_assignments = user_repository.taxonomy_for_mangas(gids)
        reading_states = user_repository.reading_states_for_mangas(gids)
        online_downloads = user_repository.online_gallery_downloads_for_mangas(gids)
        original_states = user_repository.gallery_original_states_for_mangas(gids)
        sync_records = user_repository.gallery_sync_records_for_mangas(gids)
        favorite_gids = set(user_repository.favorite_gids(gids))
        return [
            cls._mergeUserState(
                item,
                online_downloads.get(item.gid),
                original_states.get(item.gid),
                sync_records.get(item.gid),
                progress_page_index=(
                    reading_states[item.gid][0]
                    if item.gid in reading_states else None
                ),
                reading_completed=(
                    reading_states[item.gid][1]
                    if item.gid in reading_states else False
                ),
                is_favorite=item.gid in favorite_gids,
                taxonomy_label_ids=tuple(
                    label_id
                    for label_id, _name in taxonomy_assignments.get(item.gid, ())
                ),
                taxonomy_labels=tuple(
                    name
                    for _label_id, name in taxonomy_assignments.get(item.gid, ())
                ),
            )
            for item in items
        ]

    @classmethod
    def loadItem(cls, source, user_repository, gid, folder=None):
        item = source.load_local_manga(gid, folder)
        if item is None:
            return None
        return cls.enrichItems(user_repository, (item,))[0]

    @staticmethod
    def _onlineMetadata(item, record, sync_record=None):
        if record is None and sync_record is None:
            return {}
        metadata = dict(record.metadata or {}) if record is not None else {}
        if sync_record is not None:
            metadata.update(dict(sync_record.metadata or {}))
        values = {
            "source_site": str(
                (sync_record.site if sync_record else "")
                or (record.site if record else "")
            ),
            "gallery_token": str(
                (sync_record.token if sync_record else "")
                or (record.token if record else "")
                or item.gallery_token
            ),
            "posted": str(metadata.get("posted") or item.posted),
            "uploader": str(metadata.get("uploader") or item.uploader),
            "language": str(metadata.get("language") or item.language),
            "file_size": str(metadata.get("file_size") or ""),
            "rating_count": max(0, int(metadata.get("rating_count") or 0)),
            "visible": str(metadata.get("visible") or ""),
            "favorited": str(metadata.get("favorited") or ""),
            "parent_gallery": str(metadata.get("parent_gallery") or ""),
            "newer_gallery_urls": tuple(metadata.get("newer_gallery_urls") or ()),
            "metadata_synced": sync_record is not None,
        }
        rating = metadata.get("rating")
        if rating is not None:
            values["rating"] = float(rating)
        if record is not None and int(record.page_count) > 0:
            values["page_count"] = max(0, int(record.page_count))
        return values

    @classmethod
    def _mergeUserState(
        cls,
        item,
        download_record,
        original_state,
        sync_record,
        **values,
    ):
        values.update(cls._onlineMetadata(item, download_record, sync_record))
        if (
            download_record is not None
            and download_record.download_mode == DOWNLOAD_MODE_STANDARD
        ):
            total = max(0, int(download_record.page_count))
            completed = max(0, int(download_record.completed_pages))
            values.update(
                {
                    "downloaded_page_count": completed,
                    "download_complete": bool(
                        total > 0
                        and completed >= total
                        and download_record.state == ONLINE_DOWNLOAD_COMPLETED
                    ),
                }
            )
        values.update(
            {
                "original_mode": original_state.mode if original_state else "",
                "original_state": original_state.state if original_state else "",
                "original_completed_pages": (
                    original_state.completed_pages if original_state else 0
                ),
                "original_fallback_to_standard": bool(
                    original_state is not None
                    and original_state.fallback_to_standard
                ),
                "original_page_modes": (
                    original_state.page_modes if original_state is not None else ()
                ),
                "standard_download_pending": bool(
                    download_record is not None
                    and download_record.download_mode == DOWNLOAD_MODE_STANDARD
                    and download_record.state != ONLINE_DOWNLOAD_COMPLETED
                ),
            }
        )
        return replace(item, **values)


class LabelMutationSignals(QObject):
    succeeded = Signal()
    failed = Signal(str)


class LabelMutationWorker(QRunnable):
    """Run an explicit label database mutation outside the GUI thread."""

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = LabelMutationSignals()

    def run(self):
        try:
            self.operation()
        except Exception as error:
            try:
                self.signals.failed.emit(str(error))
            except RuntimeError:
                pass
            return
        try:
            self.signals.succeeded.emit()
        except RuntimeError:
            pass


class TaxonomyCreateDialog(MessageBoxBase):
    """Create one taxonomy node without chaining native input dialogs."""

    def __init__(self, parent_entries, parent=None):
        super().__init__(parent)
        self.widget.setMinimumWidth(480)

        self.titleLabel = SubtitleLabel(self.tr("新增归类"), self.widget)
        self.nameEdit = LineEdit(self.widget)
        self.nameEdit.setPlaceholderText(self.tr("归类名称"))
        self.parentLabel = BodyLabel(self.tr("父级"), self.widget)
        self.parentCombo = ComboBox(self.widget)
        for text, label_id in parent_entries:
            self.parentCombo.addItem(str(text), userData=label_id)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.nameEdit)
        self.viewLayout.addWidget(self.parentLabel)
        self.viewLayout.addWidget(self.parentCombo)
        self.yesButton.setText(self.tr("创建"))
        self.cancelButton.setText(self.tr("取消"))
        self.yesButton.setEnabled(False)
        self.nameEdit.textChanged.connect(
            lambda text: self.yesButton.setEnabled(bool(text.strip()))
        )
        self.nameEdit.setFocus()

    def name(self):
        return self.nameEdit.text().strip()

    def parentId(self):
        value = self.parentCombo.currentData()
        return int(value) if value is not None else None


class MangaLabelSelectionDialog(MessageBoxBase):
    """Searchable modal selector replacing unbounded context submenus."""

    CATEGORY = "category"
    TAXONOMY = "taxonomy"
    CREATE_REQUESTED = 2

    def __init__(
        self,
        mode,
        target_items,
        primary_labels=(),
        taxonomy_labels=(),
        parent=None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.targetItems = tuple(target_items)
        self._treeItems = []
        self._initialStates = {}
        self._categoryValue = None
        self.widget.setMinimumSize(600, 590)

        titles = {
            self.CATEGORY: self.tr("选择分类"),
            self.TAXONOMY: self.tr("选择归类"),
        }
        descriptions = {
            self.CATEGORY: self.tr("分类为单选；选择“未分类”可取消现有分类。"),
            self.TAXONOMY: self.tr("归类按树状层级显示，可独立选择任意父节点或子节点。"),
        }
        self.titleLabel = SubtitleLabel(titles[mode], self.widget)
        self.descriptionLabel = BodyLabel(descriptions[mode], self.widget)
        self.descriptionLabel.setWordWrap(True)
        self.searchEdit = SearchLineEdit(self.widget)
        self.searchEdit.setPlaceholderText(self.tr("搜索标签"))
        self.tree = TreeWidget(self.widget)
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(390)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.descriptionLabel)
        self.viewLayout.addWidget(self.searchEdit)
        self.viewLayout.addWidget(self.tree, 1)
        self.yesButton.setText(self.tr("应用"))
        self.cancelButton.setText(self.tr("取消"))

        self.createButton = None
        if mode == self.TAXONOMY:
            self.createButton = PushButton(self.tr("新建并添加…"), self.buttonGroup)
            self.buttonLayout.insertWidget(0, self.createButton, 1, Qt.AlignVCenter)
            self.createButton.clicked.connect(
                lambda: self.done(self.CREATE_REQUESTED)
            )

        if mode == self.CATEGORY:
            self._populateCategories(primary_labels)
            self.tree.currentItemChanged.connect(self._onCategoryChanged)
        else:
            self._populateTaxonomy(taxonomy_labels)

        self.searchEdit.textChanged.connect(self._applySearch)
        self.tree.expandAll()

    def _populateCategories(self, labels):
        current_labels = {item.primary_label for item in self.targetItems}
        selected_value = next(iter(current_labels)) if len(current_labels) == 1 else None
        entries = [("", self.tr("未分类"))] + [
            (name, name) for name in labels
        ]
        for value, text in entries:
            tree_item = QTreeWidgetItem([text])
            tree_item.setData(0, Qt.UserRole, value)
            self.tree.addTopLevelItem(tree_item)
            self._treeItems.append(tree_item)
            if selected_value == value:
                self.tree.setCurrentItem(tree_item)
                self._categoryValue = value
        self.yesButton.setEnabled(self._categoryValue is not None)

    def _populateTaxonomy(self, labels):
        total = len(self.targetItems)
        by_parent = {}
        for label_id, parent_id, name, _count in labels:
            by_parent.setdefault(parent_id, []).append((int(label_id), name))

        def add_children(parent_item, parent_id):
            for label_id, name in by_parent.get(parent_id, ()):
                membership = sum(
                    label_id in item.taxonomy_label_ids
                    for item in self.targetItems
                )
                state = self._membershipState(membership, total)
                tree_item = self._checkableItem(name, label_id, state)
                if parent_item is None:
                    self.tree.addTopLevelItem(tree_item)
                else:
                    parent_item.addChild(tree_item)
                add_children(tree_item, label_id)

        add_children(None, None)

    @staticmethod
    def _membershipState(membership, total):
        if total and membership == total:
            return Qt.Checked
        if membership:
            return Qt.PartiallyChecked
        return Qt.Unchecked

    def _checkableItem(self, text, value, state):
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.UserRole, value)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, state)
        self._initialStates[value] = state
        self._treeItems.append(item)
        return item

    def _onCategoryChanged(self, current, _previous):
        if current is None:
            self._categoryValue = None
            self.yesButton.setEnabled(False)
            return
        self._categoryValue = current.data(0, Qt.UserRole)
        self.yesButton.setEnabled(True)

    def _applySearch(self, text):
        query = text.strip().casefold()
        if self.mode != self.TAXONOMY:
            for item in self._treeItems:
                item.setHidden(bool(query) and query not in item.text(0).casefold())
            return

        def update_visibility(item):
            child_visible = False
            for index in range(item.childCount()):
                child_visible = update_visibility(item.child(index)) or child_visible
            matches = not query or query in item.text(0).casefold()
            visible = matches or child_visible
            item.setHidden(not visible)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            update_visibility(self.tree.topLevelItem(index))
        if query:
            self.tree.expandAll()

    def selectedCategory(self):
        return self._categoryValue

    def validate(self):
        return self.mode != self.CATEGORY or self._categoryValue is not None

    def selectionChanges(self):
        changes = {}
        for item in self._treeItems:
            value = item.data(0, Qt.UserRole)
            state = item.checkState(0)
            if state == Qt.PartiallyChecked:
                continue
            if state != self._initialStates[value]:
                changes[int(value)] = state == Qt.Checked
        return changes


class LocalMangaInterface(QWidget):
    """EhViewer 本地下载漫画的搜索、分类与布局视图。"""

    GRID_MODE = "grid"
    LIST_MODE = "list"
    TAG_CATEGORY = "category"
    TAG_TAXONOMY = "taxonomy"
    mangaActivated = Signal(object)
    readingSequenceMangaActivated = Signal(object, object, int)
    libraryLoaded = Signal(object)
    libraryMutated = Signal()
    favoriteChanged = Signal(object, bool)
    metadataSyncRequested = Signal(object)
    trashRequested = Signal(object)
    folderOpenRequested = Signal(object)
    readingRecordClearRequested = Signal(int)
    categoryChanged = Signal(object)

    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
        parent=None,
        collection_kind=None,
        object_name=None,
        tag_search_index=None,
        search_history_service=None,
    ):
        super().__init__(parent=parent)
        self._collection_kind = collection_kind
        self._collection_order = ()
        self.setObjectName(object_name or "localMangaInterface")
        self.source = source
        self.userRepository = user_repository
        self.tagSearchIndex = tag_search_index
        self.searchHistoryService = search_history_service
        self._all_items: List[MangaItem] = []
        self._filtered_items: List[MangaItem] = []
        self._classification_index = MangaClassificationIndex()
        self._pending_item_upserts: Dict[int, MangaItem] = {}
        self._cards: List[QWidget] = []
        self._empty_label: Optional[BodyLabel] = None
        self._layout_mode = self.GRID_MODE
        self._tag_mode = self.TAG_CATEGORY
        self._show_all_manga = False
        self._primary_label_filter = str(
            cfg.get(cfg.mangaPrimaryLabelFilter) or "__none__"
        )
        self._tag_sidebar_width = 230
        self._taxonomy_filter_id: Optional[int] = None
        self._primary_labels: List[str] = []
        self._taxonomy_labels = []
        self._selection_mode = False
        self._selected_gids: Set[int] = set()
        self._label_workers = set()
        self._sort_order = cfg.get(cfg.mangaSortOrder)
        self._page = 1
        self._page_size = cfg.get(cfg.mangaPageSize)
        self._last_columns = 0
        self._load_worker = None
        self._pending_reveal_gid = None
        self._cover_worker = None
        self._cover_cache = OrderedDict()
        self._similar_search_worker: Optional[SimilarMangaWorker] = None
        self._similar_result_gids: Optional[tuple] = None
        self._similar_reference_title = ""
        self._search_hover_widgets = set()
        self._search_opened_by_hover = False
        self._search_pinned = False

        title = {
            "favorites": self.tr("收藏"),
            "history": self.tr("本地历史"),
        }.get(collection_kind, self.tr("本地资源"))
        self.titleLabel = TitleLabel(title, self)
        self.searchEdit = EhTagSearchLineEdit(
            tag_search_index,
            self,
            search_history_service,
        )
        self.searchEdit.setPlaceholderText(self.tr("搜索英语标题、原标题或标签"))
        self.searchEdit.setMinimumWidth(260)
        self.searchPanel = QWidget(self)
        search_layout = QHBoxLayout(self.searchPanel)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.addWidget(self.searchEdit)
        self.searchPanel.hide()
        self.searchHoverTimer = QTimer(self)
        self.searchHoverTimer.setSingleShot(True)
        self.searchHoverTimer.setInterval(160)
        self.searchHoverTimer.timeout.connect(self._finishSearchHover)

        self.searchButton = TransparentPushButton(
            FIF.SEARCH,
            self.tr("搜索"),
            self,
        )
        self.searchButton.clicked.connect(self.toggleSearch)
        for hover_widget in (self.searchButton, self.searchPanel, self.searchEdit):
            hover_widget.setAttribute(Qt.WA_Hover, True)
            hover_widget.installEventFilter(self)
        cfg.mangaSearchHoverEnabled.valueChanged.connect(
            self._onSearchHoverSettingChanged
        )
        self.tagButton = TransparentPushButton(
            FIF.TAG,
            self.tr("标签"),
            self,
        )
        self.tagButton.clicked.connect(self.toggleClassification)

        self.sortCombo = ComboBox(self)
        self.sortCombo.addItem(self.tr("添加时间：最新优先"), userData="desc")
        self.sortCombo.addItem(self.tr("添加时间：最早优先"), userData="asc")
        self.sortCombo.setCurrentIndex(0 if self._sort_order == "desc" else 1)
        self.sortCombo.setFixedWidth(176)

        self.multiSelectCheckBox = CheckBox(self)
        self.multiSelectCheckBox.setText(self.tr("复选"))
        self.selectionCountLabel = CaptionLabel(self.tr("已选 0 项"), self)
        self.selectionCountLabel.hide()
        self.selectAllButton = ToolButton(FIF.CHECKBOX, self)
        self.selectAllButton.setFixedSize(36, 36)
        self.selectAllButton.setToolTip(self.tr("全选当前筛选结果"))
        self.selectAllButton.setAccessibleName(self.tr("全选当前筛选结果"))
        self.selectAllButton.hide()
        self.layoutSwitch = SegmentedToolWidget(self)
        self.layoutSwitch.addItem(
            self.GRID_MODE,
            FIF.TILES,
            lambda: self.setLayoutMode(self.GRID_MODE),
        )
        self.layoutSwitch.addItem(
            self.LIST_MODE,
            FIF.MENU,
            lambda: self.setLayoutMode(self.LIST_MODE),
        )
        self.layoutSwitch.setCurrentItem(self.GRID_MODE)
        self.layoutSwitch.setToolTip(self.tr("切换封面或标题布局"))

        self.resultLabel = BodyLabel(self.tr("正在读取本地漫画…"), self)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        header_layout.addWidget(self.titleLabel)
        header_layout.addStretch(1)
        header_layout.addWidget(self.selectionCountLabel)
        header_layout.addWidget(self.selectAllButton)
        header_layout.addWidget(self.multiSelectCheckBox)
        header_layout.addWidget(self.sortCombo)
        header_layout.addWidget(self.layoutSwitch)
        header_layout.addWidget(self.tagButton)
        header_layout.addWidget(self.searchButton)

        self.classificationCard = SimpleCardWidget(self)
        self.classificationCard.setMinimumWidth(190)
        classification_layout = QVBoxLayout(self.classificationCard)
        classification_layout.setContentsMargins(12, 16, 12, 16)
        classification_layout.setSpacing(10)

        self.tagModeSwitch = SegmentedWidget(self.classificationCard)
        self.tagModeSwitch.addItem(
            self.TAG_CATEGORY,
            self.tr("分类"),
            lambda: self._setTagMode(self.TAG_CATEGORY),
        )
        self.tagModeSwitch.addItem(
            self.TAG_TAXONOMY,
            self.tr("归类"),
            lambda: self._setTagMode(self.TAG_TAXONOMY),
        )
        self.tagModeSwitch.setCurrentItem(self.TAG_CATEGORY)
        classification_layout.addWidget(self.tagModeSwitch)
        self.showAllMangaButton = PushButton(
            FIF.APPLICATION, self.tr("显示全部漫画"), self.classificationCard
        )
        classification_layout.addWidget(self.showAllMangaButton)

        self.tagStack = QStackedWidget(self.classificationCard)
        classification_layout.addWidget(self.tagStack, 1)

        self.categoryPanel = QWidget(self.tagStack)
        category_layout = QVBoxLayout(self.categoryPanel)
        category_layout.setContentsMargins(0, 0, 0, 0)
        category_header = QHBoxLayout()
        category_header.addWidget(BodyLabel(self.tr("分类"), self.categoryPanel))
        category_header.addStretch(1)
        self.addCategoryButton = ToolButton(FIF.ADD, self.categoryPanel)
        self.addCategoryButton.setToolTip(self.tr("新增分类"))
        category_header.addWidget(self.addCategoryButton)
        category_layout.addLayout(category_header)
        self.primaryLabelTree = TreeWidget(self.categoryPanel)
        self.primaryLabelTree.setHeaderHidden(True)
        self.primaryLabelTree.setContextMenuPolicy(Qt.CustomContextMenu)
        category_layout.addWidget(self.primaryLabelTree, 1)
        self.tagStack.addWidget(self.categoryPanel)

        self.taxonomyPanel = QWidget(self.tagStack)
        taxonomy_layout = QVBoxLayout(self.taxonomyPanel)
        taxonomy_layout.setContentsMargins(0, 0, 0, 0)
        taxonomy_header = QHBoxLayout()
        taxonomy_header.addWidget(BodyLabel(self.tr("归类"), self.taxonomyPanel))
        taxonomy_header.addStretch(1)
        self.addTaxonomyButton = ToolButton(FIF.ADD, self.taxonomyPanel)
        self.addTaxonomyButton.setToolTip(self.tr("新增归类节点"))
        taxonomy_header.addWidget(self.addTaxonomyButton)
        taxonomy_layout.addLayout(taxonomy_header)
        self.taxonomyTree = TreeWidget(self.taxonomyPanel)
        self.taxonomyTree.setHeaderHidden(True)
        self.taxonomyTree.setContextMenuPolicy(Qt.CustomContextMenu)
        taxonomy_layout.addWidget(self.taxonomyTree, 1)
        self.tagStack.addWidget(self.taxonomyPanel)

        self._taxonomy_items: Dict[int, QTreeWidgetItem] = {}

        self.scrollWidget = QWidget()
        self.scrollWidget.setObjectName("localMangaScrollWidget")
        self.contentLayout = QGridLayout(self.scrollWidget)
        self.contentLayout.setContentsMargins(0, 0, 0, 24)
        self.contentLayout.setHorizontalSpacing(16)
        self.contentLayout.setVerticalSpacing(16)
        self.contentLayout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollArea.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#localMangaScrollWidget { background: transparent; }"
        )

        self.pageSizeCombo = ComboBox(self)
        for size in (20, 40, 60, 100):
            self.pageSizeCombo.addItem(str(size), userData=size)
        page_size_index = next(
            (
                index
                for index in range(self.pageSizeCombo.count())
                if self.pageSizeCombo.itemData(index) == self._page_size
            ),
            1,
        )
        self.pageSizeCombo.setCurrentIndex(page_size_index)
        self.pageSizeCombo.setFixedWidth(76)

        self.firstPageButton = ToolButton(FIF.PAGE_LEFT, self)
        self.previousPageButton = ToolButton(FIF.LEFT_ARROW, self)
        self.nextPageButton = ToolButton(FIF.RIGHT_ARROW, self)
        self.lastPageButton = ToolButton(FIF.PAGE_RIGHT, self)
        self.pageSpinBox = SpinBox(self)
        self.pageSpinBox.setRange(1, 1)
        self.pageSpinBox.setFixedWidth(82)
        self.pageCountLabel = BodyLabel(self.tr("/ 1 页"), self)

        pagination_layout = QHBoxLayout()
        pagination_layout.setContentsMargins(0, 0, 0, 0)
        pagination_layout.setSpacing(8)
        pagination_layout.addWidget(BodyLabel(self.tr("每页"), self))
        pagination_layout.addWidget(self.pageSizeCombo)
        pagination_layout.addWidget(BodyLabel(self.tr("部"), self))
        pagination_layout.addStretch(1)
        pagination_layout.addWidget(self.firstPageButton)
        pagination_layout.addWidget(self.previousPageButton)
        pagination_layout.addWidget(self.pageSpinBox)
        pagination_layout.addWidget(self.pageCountLabel)
        pagination_layout.addWidget(self.nextPageButton)
        pagination_layout.addWidget(self.lastPageButton)

        self.contentPanel = QWidget(self)
        content_layout = QVBoxLayout(self.contentPanel)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addLayout(header_layout)
        content_layout.addWidget(self.searchPanel)
        content_layout.addWidget(self.resultLabel)
        content_layout.addWidget(self.scrollArea, 1)
        content_layout.addLayout(pagination_layout)

        self.tagSplitter = FluentSplitter(Qt.Horizontal, self)
        self.tagSplitter.setChildrenCollapsible(False)
        self.tagSplitter.setHandleWidth(7)
        self.tagSplitter.addWidget(self.classificationCard)
        self.tagSplitter.addWidget(self.contentPanel)
        self.tagSplitter.setStretchFactor(0, 0)
        self.tagSplitter.setStretchFactor(1, 1)
        self.tagSplitter.setSizes([self._tag_sidebar_width, 730])
        self.tagSplitter.splitterMoved.connect(
            lambda _position, _index: self._scheduleCardRelayout()
        )

        self.mainLayout = QHBoxLayout(self)
        self.mainLayout.setContentsMargins(36, 32, 36, 24)
        self.mainLayout.setSpacing(0)
        self.mainLayout.addWidget(self.tagSplitter)
        self.classificationCard.hide()

        self.searchTimer = QTimer(self)
        self.searchTimer.setSingleShot(True)
        self.searchTimer.setInterval(180)
        self.searchTimer.timeout.connect(self.applyFilters)
        self.searchEdit.textChanged.connect(self._onSearchTextChanged)
        self.primaryLabelTree.currentItemChanged.connect(self._onPrimaryLabelChanged)
        self.taxonomyTree.currentItemChanged.connect(self._onTaxonomyChanged)
        self.primaryLabelTree.customContextMenuRequested.connect(
            lambda position: self._showTagTreeMenu(
                self.TAG_CATEGORY, self.primaryLabelTree, position
            )
        )
        self.taxonomyTree.customContextMenuRequested.connect(
            lambda position: self._showTagTreeMenu(
                self.TAG_TAXONOMY, self.taxonomyTree, position
            )
        )
        self.showAllMangaButton.clicked.connect(self._showAllManga)
        self.addCategoryButton.clicked.connect(self._createPrimaryLabel)
        self.addTaxonomyButton.clicked.connect(self._createTaxonomyLabel)
        self.multiSelectCheckBox.toggled.connect(self._onSelectionModeChanged)
        self.selectAllButton.clicked.connect(self._toggleSelectAll)
        self.sortCombo.currentIndexChanged.connect(self._onSortOrderChanged)
        self.pageSizeCombo.currentIndexChanged.connect(self._onPageSizeChanged)
        self.pageSpinBox.valueChanged.connect(self._onPageChanged)
        self.firstPageButton.clicked.connect(lambda: self.setPage(1))
        self.previousPageButton.clicked.connect(lambda: self.setPage(self._page - 1))
        self.nextPageButton.clicked.connect(lambda: self.setPage(self._page + 1))
        self.lastPageButton.clicked.connect(lambda: self.setPage(self.pageCount()))
        if self._collection_kind:
            self._show_all_manga = True
            self.tagButton.hide()
            self.sortCombo.hide()
            self.resultLabel.setText(self.tr("暂无内容"))
            self._renderCards()
        else:
            self.reload()

    def setSource(self, source: EhViewerDataSource):
        self.source = source
        if not self._collection_kind:
            self._pending_item_upserts.clear()
            self._cover_cache.clear()
            self.reload()

    def setCollectionItems(self, items, ordered_gids):
        self.cancelLoad()
        by_gid = {item.gid: item for item in items}
        self._collection_order = tuple(
            gid for gid in ordered_gids if gid in by_gid
        )
        self._all_items = [by_gid[gid] for gid in self._collection_order]
        self._classification_index.rebuild(
            self._all_items,
            self._taxonomy_labels,
        )
        self._selected_gids.intersection_update(self._collection_order)
        self._updateSelectionState()
        self.applyFilters(reset_page=True)

    def allItems(self):
        return tuple(self._all_items)

    def upsertItem(self, item: MangaItem) -> bool:
        """Update one card in place, rebuilding only when it is newly visible."""

        gid = int(item.gid)
        if self._collection_kind and gid not in self._collection_order:
            return False
        if self._load_worker is not None:
            self._pending_item_upserts[gid] = item
        existing_index = next(
            (
                index
                for index, current in enumerate(self._all_items)
                if int(current.gid) == gid
            ),
            None,
        )
        if existing_index is None:
            self._all_items.append(item)
            self._classification_index.upsert(item)
            if not self._itemMatchesCurrentFilters(item):
                if self._collection_kind:
                    self.resultLabel.setText(
                        self.tr("显示 {} 部漫画").format(
                            len(self._filtered_items)
                        )
                    )
                else:
                    self.resultLabel.setText(
                        self.tr("显示 {} / {} 部漫画").format(
                            len(self._filtered_items),
                            len(self._all_items),
                        )
                    )
                self._updatePagination()
                return True
            scroll_position = self.scrollArea.verticalScrollBar().value()
            self.applyFilters(reset_page=False)
            QTimer.singleShot(
                0,
                lambda value=scroll_position: (
                    self.scrollArea.verticalScrollBar().setValue(value)
                ),
            )
            return True

        previous = self._all_items[existing_index]
        self._all_items[existing_index] = item
        self._classification_index.upsert(item)
        filter_identity = lambda value: (
            value.english_title,
            value.original_title,
            value.tags,
            value.primary_label,
            value.taxonomy_label_ids,
            value.added_time,
        )
        if filter_identity(previous) != filter_identity(item):
            self.applyFilters(reset_page=False)
            return True

        for index, current in enumerate(self._filtered_items):
            if int(current.gid) == gid:
                self._filtered_items[index] = item
                break
        for card in self._cards:
            if int(card.item.gid) == gid:
                card.setItem(item)
                break
        return True

    def _itemMatchesCurrentFilters(self, item: MangaItem) -> bool:
        if self._similar_result_gids is not None:
            return item.gid in self._similar_result_gids
        query = self.searchEdit.text().strip()
        query_terms = (
            self.tagSearchIndex.local_query_terms(query)
            if self.tagSearchIndex is not None
            else tuple(word.casefold() for word in query.split() if word)
        )
        if not item.matches_terms(query_terms):
            return False
        if self._collection_kind:
            return True
        return int(item.gid) in self._activeTagGids()

    def tagMetadata(self):
        return (
            tuple(self._primary_labels),
            tuple(self._taxonomy_labels),
        )

    def setTagMetadata(self, primary_labels, taxonomy_labels):
        self._primary_labels = list(primary_labels)
        self._taxonomy_labels = list(taxonomy_labels)
        self._classification_index.set_taxonomy_labels(self._taxonomy_labels)
        self._populatePrimaryLabels(self._primary_labels)
        self._populateTaxonomy(self._taxonomy_labels)

    def cancelLoad(self):
        if self._load_worker is not None:
            self._load_worker.cancelled = True
            self._load_worker = None
        self._cancelCoverPreload()
        self._cancelSimilarSearchWorker()

    def _cancelCoverPreload(self):
        if self._cover_worker is not None:
            self._cover_worker.cancelled = True
            self._cover_worker = None

    def reload(self, reveal_gid=None):
        self.cancelLoad()
        self._pending_reveal_gid = (
            int(reveal_gid) if reveal_gid is not None else None
        )
        self._clearSimilarSearch(clear_query=True)
        if reveal_gid is not None:
            self._selected_gids.clear()
        self._updateSelectionState()
        self.resultLabel.setText(self.tr("正在读取本地漫画…"))
        worker = MangaLoadWorker(self.source, self.userRepository)
        worker.signals.loaded.connect(
            lambda payload: self._onLoaded(payload, worker)
        )
        worker.signals.failed.connect(
            lambda message: self._onLoadFailed(message, worker)
        )
        self._load_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _onLoaded(self, payload, worker=None):
        if worker is not None and self._load_worker is not worker:
            return
        self._load_worker = None
        reveal_gid = self._pending_reveal_gid
        self._pending_reveal_gid = None
        self._all_items, primary_labels, taxonomy_labels = payload
        if self._pending_item_upserts:
            pending = dict(self._pending_item_upserts)
            self._pending_item_upserts.clear()
            loaded_gids = {int(item.gid) for item in self._all_items}
            self._all_items = [
                pending.get(int(item.gid), item) for item in self._all_items
            ]
            self._all_items.extend(
                item for gid, item in pending.items() if gid not in loaded_gids
            )
        tag_mode = self._tag_mode
        show_all_manga = self._show_all_manga
        valid_gids = {item.gid for item in self._all_items}
        self._selected_gids.intersection_update(valid_gids)
        for gid in tuple(self._cover_cache):
            if gid not in valid_gids:
                self._cover_cache.pop(gid, None)
        self._primary_labels = list(
            dict.fromkeys(
                [
                    *primary_labels,
                    *(item.primary_label for item in self._all_items if item.primary_label),
                ]
            )
        )
        self._taxonomy_labels = list(taxonomy_labels)
        self._classification_index.rebuild(
            self._all_items,
            self._taxonomy_labels,
        )
        self._populatePrimaryLabels(self._primary_labels)
        self._populateTaxonomy(self._taxonomy_labels)
        self._setTagMode(tag_mode, reset_page=False)
        if reveal_gid is not None and any(
            item.gid == reveal_gid for item in self._all_items
        ):
            self.searchTimer.stop()
            self.searchEdit.blockSignals(True)
            self.searchEdit.clear()
            self.searchEdit.blockSignals(False)
            self._show_all_manga = True
        else:
            self._show_all_manga = show_all_manga
        self.applyFilters(reset_page=reveal_gid is not None)
        if reveal_gid is not None:
            for index, item in enumerate(self._filtered_items):
                if item.gid == reveal_gid:
                    self.setPage(index // self._page_size + 1)
                    break
        self.libraryLoaded.emit(tuple(self._all_items))

    def _onLoadFailed(self, message: str, worker=None):
        if worker is not None and self._load_worker is not worker:
            return
        self._load_worker = None
        self._pending_reveal_gid = None
        self._all_items = []
        self._filtered_items = []
        self._classification_index.rebuild(())
        self.resultLabel.setText(self.tr("读取失败：{}").format(message))
        self._renderCards()

    def _populatePrimaryLabels(self, labels: List[str]):
        self.primaryLabelTree.blockSignals(True)
        self.primaryLabelTree.clear()
        entries = [
            (self.tr("未分类"), "__none__"),
            *((label, label) for label in labels),
        ]
        valid_values = {value for _text, value in entries}
        if self._primary_label_filter not in valid_values:
            self._primary_label_filter = "__none__"
        selected_item = None
        for text, value in entries:
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.UserRole, value)
            self.primaryLabelTree.addTopLevelItem(item)
            if value == self._primary_label_filter:
                selected_item = item
        self.primaryLabelTree.setCurrentItem(
            selected_item or self.primaryLabelTree.topLevelItem(0)
        )
        self.primaryLabelTree.blockSignals(False)
        cfg.set(cfg.mangaPrimaryLabelFilter, self._primary_label_filter)

    def _populateTaxonomy(self, labels):
        selected_id = self._taxonomy_filter_id
        self.taxonomyTree.blockSignals(True)
        self.taxonomyTree.clear()
        self._taxonomy_items = {}
        selected_item = None
        for label_id, _parent_id, name, count in labels:
            item = QTreeWidgetItem([f"{name} ({count})"])
            item.setData(0, Qt.UserRole, int(label_id))
            item.setData(0, Qt.UserRole + 1, name)
            self._taxonomy_items[int(label_id)] = item
        for label_id, parent_id, _name, _count in labels:
            item = self._taxonomy_items[int(label_id)]
            parent_item = self._taxonomy_items.get(parent_id)
            if parent_item is None:
                self.taxonomyTree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            if int(label_id) == selected_id:
                selected_item = item
        if not labels:
            empty_item = QTreeWidgetItem([self.tr("尚未创建归类")])
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemIsSelectable)
            self.taxonomyTree.addTopLevelItem(empty_item)
        elif selected_item is None:
            selected_item = self.taxonomyTree.topLevelItem(0)
        self.taxonomyTree.expandAll()
        self.taxonomyTree.setCurrentItem(selected_item)
        self.taxonomyTree.blockSignals(False)
        if selected_item is not None:
            self._taxonomy_filter_id = int(selected_item.data(0, Qt.UserRole))

    def _onPrimaryLabelChanged(self, current, previous=None):
        if current is None:
            return
        value = current.data(0, Qt.UserRole)
        if value is None:
            return
        self._primary_label_filter = value
        self._clearSimilarSearch(clear_query=True)
        self._show_all_manga = False
        cfg.set(cfg.mangaPrimaryLabelFilter, value)
        self.applyFilters(reset_page=True)

    def _onTaxonomyChanged(self, current, previous=None):
        if current is None or current.data(0, Qt.UserRole) is None:
            return
        self._taxonomy_filter_id = int(current.data(0, Qt.UserRole))
        self._clearSimilarSearch(clear_query=True)
        self._show_all_manga = False
        if self._tag_mode == self.TAG_TAXONOMY:
            self.applyFilters(reset_page=True)

    def _setTagMode(self, mode: str, reset_page=True):
        if mode not in (self.TAG_CATEGORY, self.TAG_TAXONOMY):
            return
        self._clearSimilarSearch(clear_query=True)
        self._tag_mode = mode
        self._show_all_manga = False
        self.tagModeSwitch.setCurrentItem(mode)
        self.tagStack.setCurrentWidget({
            self.TAG_CATEGORY: self.categoryPanel,
            self.TAG_TAXONOMY: self.taxonomyPanel,
        }[mode])
        self.sortCombo.setEnabled(True)
        self._updateTitleLabel()
        if self._all_items:
            self.applyFilters(reset_page=reset_page)

    def _showAllManga(self):
        self._clearSimilarSearch(clear_query=True)
        self._show_all_manga = True
        self.sortCombo.setEnabled(True)
        self.applyFilters(reset_page=True)

    def _createPrimaryLabel(self):
        name, accepted = QInputDialog.getText(
            self, self.tr("新增分类"), self.tr("分类名称")
        )
        normalized = name.strip()
        if not accepted or not normalized:
            return
        self._startLabelMutation(
            lambda: self.source.create_primary_label(normalized),
            lambda: self._finishCreatePrimaryLabel(normalized),
        )

    def _finishCreatePrimaryLabel(self, name: str):
        if name not in self._primary_labels:
            self._primary_labels.append(name)
        self._populatePrimaryLabels(self._primary_labels)

    def _createTaxonomyLabel(self, assign_to_gids=None):
        parent_entries = [(self.tr("根节点"), None)] + self._taxonomyPathEntries()
        dialog = TaxonomyCreateDialog(parent_entries, self.window())
        if dialog.exec() != QDialog.Accepted:
            return
        normalized = dialog.name()
        if not normalized:
            return
        parent_id = dialog.parentId()
        target_gids = tuple(
            dict.fromkeys(int(gid) for gid in (assign_to_gids or ()))
        )
        result = {}

        def operation():
            result["id"] = self.userRepository.create_taxonomy_label(
                normalized, parent_id
            )
            if target_gids:
                self.userRepository.assign_taxonomy_to_mangas(
                    target_gids, result["id"]
                )
            result["labels"] = self.userRepository.list_taxonomy_labels()
            result["assignments"] = (
                self.userRepository.taxonomy_for_mangas(target_gids)
                if target_gids else {}
            )

        self._startLabelMutation(
            operation,
            lambda: self._finishCreateTaxonomyLabel(result, target_gids),
        )

    def _finishCreateTaxonomyLabel(self, result, target_gids):
        previous_filter_id = self._taxonomy_filter_id
        self._taxonomy_labels = list(result["labels"])
        self._classification_index.set_taxonomy_labels(self._taxonomy_labels)

        if target_gids:
            target_gid_set = set(target_gids)
            assignments = result["assignments"]

            def update(item):
                if item.gid not in target_gid_set:
                    return item
                values = assignments.get(item.gid, ())
                return replace(
                    item,
                    taxonomy_label_ids=tuple(
                        label_id for label_id, _name in values
                    ),
                    taxonomy_labels=tuple(name for _label_id, name in values),
                )

            self._all_items = [update(item) for item in self._all_items]
            for item in self._all_items:
                if item.gid in target_gid_set:
                    self._classification_index.upsert(item)

        self._populateTaxonomy(self._taxonomy_labels)
        filter_changed = previous_filter_id != self._taxonomy_filter_id
        if target_gids or (
            self._tag_mode == self.TAG_TAXONOMY and filter_changed
        ):
            self.applyFilters(reset_page=False)
        else:
            self._updateTitleLabel()

    def _taxonomyPathEntries(self):
        return sorted(
            (
                (self._taxonomyPath(label_id, " / "), int(label_id))
                for label_id, _parent_id, _name, _count in self._taxonomy_labels
            ),
            key=lambda pair: pair[0].casefold(),
        )

    def _taxonomyPath(self, label_id, separator="/"):
        by_id = {
            int(current_id): (parent_id, name)
            for current_id, parent_id, name, _count in self._taxonomy_labels
        }
        values = []
        seen = set()
        label_id = int(label_id) if label_id is not None else None
        while label_id in by_id and label_id not in seen:
            seen.add(label_id)
            parent_id, name = by_id[label_id]
            values.append(str(name))
            label_id = int(parent_id) if parent_id is not None else None
        return separator.join(reversed(values))

    def _showTagTreeMenu(self, tag_mode, tree, position):
        item = tree.itemAt(position)
        if item is None:
            return
        menu = self._buildTagTreeMenu(tag_mode, item)
        if menu is not None:
            menu.exec(tree.viewport().mapToGlobal(position))

    def _buildTagTreeMenu(self, tag_mode, item):
        value = item.data(0, Qt.UserRole)
        if value is None:
            return None
        name = (
            str(value)
            if tag_mode == self.TAG_CATEGORY
            else str(item.data(0, Qt.UserRole + 1) or item.text(0))
        )
        menu = RoundMenu(name, self)
        if tag_mode == self.TAG_CATEGORY and value == "__none__":
            return None
        delete_action = QAction(self.tr("删除"), menu)
        delete_action.triggered.connect(
            lambda: self._deleteTag(tag_mode, value, name)
        )
        menu.addAction(delete_action)
        return menu

    def _deleteTag(self, tag_mode, value, name):
        descriptions = {
            self.TAG_CATEGORY: self.tr(
                "使用该分类的漫画会移到“未分类”，分类本身将从目标数据库删除。"
            ),
            self.TAG_TAXONOMY: self.tr(
                "该归类、全部子归类及其漫画关联将被删除。"
            ),
        }
        if not self._confirmDeleteTag(name, descriptions[tag_mode]):
            return
        if tag_mode == self.TAG_CATEGORY:
            self._startLabelMutation(
                lambda: self.source.delete_primary_label(name),
                lambda: self._finishDeletePrimaryLabel(name),
            )
        else:
            self._startLabelMutation(
                lambda: self.userRepository.delete_taxonomy_label(int(value)),
                self._refreshTagData,
            )

    def _confirmDeleteTag(self, name: str, description: str) -> bool:
        dialog = MessageBox(
            self.tr("删除“{}”？").format(name),
            description,
            self.window(),
        )
        dialog.yesButton.setText(self.tr("删除"))
        dialog.cancelButton.setText(self.tr("取消"))
        return bool(dialog.exec())

    def _finishDeletePrimaryLabel(self, name: str):
        target = name.casefold()
        self._primary_labels = [
            label for label in self._primary_labels
            if label.casefold() != target
        ]
        self._all_items = [
            replace(item, primary_label="")
            if item.primary_label.casefold() == target else item
            for item in self._all_items
        ]
        self._classification_index.rebuild(
            self._all_items,
            self._taxonomy_labels,
        )
        if self._primary_label_filter.casefold() == target:
            self._primary_label_filter = "__none__"
            cfg.set(cfg.mangaPrimaryLabelFilter, "__none__")
        self._populatePrimaryLabels(self._primary_labels)
        self.applyFilters(reset_page=True)

    def _refreshTagData(self):
        self._taxonomy_labels = self.userRepository.list_taxonomy_labels()
        taxonomy = self.userRepository.taxonomy_for_mangas(
            [item.gid for item in self._all_items]
        )
        self._all_items = [
            replace(
                item,
                taxonomy_label_ids=tuple(
                    label_id for label_id, _name in taxonomy.get(item.gid, ())
                ),
                taxonomy_labels=tuple(
                    name for _label_id, name in taxonomy.get(item.gid, ())
                ),
            )
            for item in self._all_items
        ]
        self._classification_index.rebuild(
            self._all_items,
            self._taxonomy_labels,
        )
        self._populateTaxonomy(self._taxonomy_labels)
        self.applyFilters(reset_page=False)


    def _showLabelMenu(self, item: MangaItem, global_position):
        if self._selection_mode:
            if item.gid not in self._selected_gids:
                self._selected_gids = {item.gid}
                self._updateSelectionState()
            target_gids = tuple(sorted(self._selected_gids))
        else:
            target_gids = (item.gid,)
        menu = self._buildLabelMenu(item, target_gids)
        menu.exec(global_position)

    def _buildLabelMenu(self, item: MangaItem, target_gids=None):
        target_gids = tuple(dict.fromkeys(target_gids or (item.gid,)))
        target_gid_set = set(target_gids)
        target_items = [
            current for current in self._all_items
            if current.gid in target_gid_set
        ]
        title = self.tr("漫画标签")
        if len(target_gids) > 1:
            title = self.tr("批量操作（{} 项）").format(len(target_gids))
        menu = RoundMenu(title, self)
        favorite = not (
            bool(target_items) and all(item.is_favorite for item in target_items)
        )
        favorite_action = QAction(
            self.tr("添加到收藏") if favorite else self.tr("取消收藏"),
            menu,
        )
        favorite_action.triggered.connect(
            lambda: self._setMangaFavorite(target_gids, favorite)
        )
        menu.addAction(favorite_action)
        open_folder_action = QAction(
            FIF.FOLDER.icon(), self.tr("在资源管理器中打开"), menu
        )
        open_folder_action.triggered.connect(
            lambda _checked=False, current=item: self.folderOpenRequested.emit(current)
        )
        menu.addAction(open_folder_action)
        if self._collection_kind is None:
            sync_action = QAction(self.tr("同步在线信息"), menu)
            sync_action.triggered.connect(
                lambda _checked=False, items=tuple(target_items): (
                    self.metadataSyncRequested.emit(items)
                )
            )
            menu.addAction(sync_action)
            similar_action = QAction(self.tr("搜索相似画廊"), menu)
            similar_action.triggered.connect(
                lambda _checked=False, reference=item: self._searchSimilarGalleries(
                    reference
                )
            )
            menu.addAction(similar_action)
        if item.progress_page_index is not None or item.reading_completed:
            clear_progress_action = QAction(
                self.tr("清空阅读记录"), menu
            )
            clear_progress_action.triggered.connect(
                lambda _checked=False, gid=int(item.gid): (
                    self.readingRecordClearRequested.emit(gid)
                )
            )
            menu.addAction(clear_progress_action)
        menu.addSeparator()
        for mode, text in (
            (self.TAG_CATEGORY, self.tr("选择分类…")),
            (self.TAG_TAXONOMY, self.tr("选择归类…")),
        ):
            action = QAction(text, menu)
            action.triggered.connect(
                lambda _checked=False, current_mode=mode: (
                    self._openLabelSelection(
                        current_mode, target_gids, target_items
                    )
                )
            )
            menu.addAction(action)
        menu.addSeparator()
        trash_action = QAction(
            FIF.DELETE.icon(), self.tr("移入回收站"), menu
        )
        trash_action.triggered.connect(
            lambda _checked=False, items=tuple(target_items): (
                self.trashRequested.emit(items)
            )
        )
        menu.addAction(trash_action)
        return menu

    def _openLabelSelection(self, mode, target_gids, target_items):
        dialog = MangaLabelSelectionDialog(
            mode,
            target_items,
            primary_labels=self._primary_labels,
            taxonomy_labels=self._taxonomy_labels,
            parent=self.window(),
        )
        result = dialog.exec()
        if result == MangaLabelSelectionDialog.CREATE_REQUESTED:
            if mode == self.TAG_TAXONOMY:
                self._createTaxonomyLabel(target_gids)
            return
        if result != QDialog.Accepted:
            return
        if mode == self.TAG_CATEGORY:
            self._setMangaPrimaryLabel(target_gids, dialog.selectedCategory())
        else:
            self._applyTaxonomySelection(target_gids, dialog.selectionChanges())

    def openCategorySelection(self, item):
        current = next(
            (
                candidate
                for candidate in self._all_items
                if int(candidate.gid) == int(item.gid)
            ),
            item,
        )
        self._openLabelSelection(
            self.TAG_CATEGORY,
            (int(current.gid),),
            (current,),
        )

    def _applyTaxonomySelection(self, gids, changes):
        if not changes:
            return
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))

        def operation():
            for label_id, checked in changes.items():
                if checked:
                    self.userRepository.assign_taxonomy_to_mangas(
                        target_gids, label_id
                    )
                else:
                    self.userRepository.unassign_taxonomy_from_mangas(
                        target_gids, label_id
                    )

        self._startLabelMutation(operation, self._refreshTagData)

    def _setMangaPrimaryLabel(self, gids, label_name: str):
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        normalized_label = label_name.strip()

        def update_items():
            target_gid_set = set(target_gids)
            self._all_items = [
                replace(item, primary_label=normalized_label)
                if item.gid in target_gid_set else item
                for item in self._all_items
            ]
            for item in self._all_items:
                if item.gid in target_gid_set:
                    self._classification_index.upsert(item)
            self.applyFilters(reset_page=False)
            for item in self._all_items:
                if item.gid in target_gid_set:
                    self.categoryChanged.emit(item)

        if normalized_label:
            operation = lambda: self.source.set_primary_label(
                target_gids, normalized_label
            )
        else:
            operation = lambda: self.source.clear_primary_label(target_gids)
        self._startLabelMutation(
            operation,
            update_items,
        )

    def _setMangaMultipleLabel(
        self,
        gids,
        label_id: int,
        label_name: str,
        checked: bool,
    ):
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if checked:
            operation = lambda: self.userRepository.assign_label_to_mangas(
                target_gids, label_id
            )
        else:
            operation = lambda: self.userRepository.unassign_label_from_mangas(
                target_gids, label_id
            )
        self._startLabelMutation(
            operation,
            self._refreshTagData,
        )

    def _setMangaTaxonomyLabel(self, gids, label_id: int, checked: bool):
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if checked:
            operation = lambda: self.userRepository.assign_taxonomy_to_mangas(
                target_gids, label_id
            )
        else:
            operation = lambda: self.userRepository.unassign_taxonomy_from_mangas(
                target_gids, label_id
            )
        self._startLabelMutation(operation, self._refreshTagData)

    def _setMangaFavorite(self, gids, favorite: bool):
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))

        def finish():
            self.setFavoriteState(target_gids, favorite)
            self.favoriteChanged.emit(target_gids, favorite)

        self._startLabelMutation(
            lambda: self.userRepository.set_favorite(target_gids, favorite),
            finish,
        )

    def setFavoriteState(self, gids, favorite: bool):
        target_gids = set(int(gid) for gid in gids)

        def update(item):
            if item.gid not in target_gids:
                return item
            return replace(item, is_favorite=bool(favorite))

        self._all_items = [update(item) for item in self._all_items]
        self._filtered_items = [update(item) for item in self._filtered_items]
        for card in self._cards:
            if card.item.gid in target_gids:
                card.setItem(update(card.item))

    def _startLabelMutation(self, operation, on_success):
        worker = LabelMutationWorker(operation)
        worker.signals.succeeded.connect(
            lambda: self._finishLabelMutation(worker, on_success)
        )
        worker.signals.failed.connect(
            lambda message: self._failLabelMutation(worker, message)
        )
        self._label_workers.add(worker)
        QThreadPool.globalInstance().start(worker)

    def _finishLabelMutation(self, worker, on_success):
        self._label_workers.discard(worker)
        on_success()
        self.libraryMutated.emit()

    def _failLabelMutation(self, worker, message: str):
        self._label_workers.discard(worker)
        InfoBar.error(
            title=self.tr("标签操作失败"),
            content=message,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000,
            parent=self,
        )

    def _onSelectionModeChanged(self, enabled: bool):
        self._selection_mode = bool(enabled)
        if not self._selection_mode:
            self._selected_gids.clear()
        self._updateSelectionState()

    def _setMangaSelected(self, gid: int, selected: bool):
        if selected:
            self._selected_gids.add(int(gid))
        else:
            self._selected_gids.discard(int(gid))
        self._updateSelectionState()

    def _toggleSelectAll(self):
        scope_gids = {item.gid for item in self._filtered_items}
        if not scope_gids:
            return
        if scope_gids.issubset(self._selected_gids):
            self._selected_gids.difference_update(scope_gids)
        else:
            self._selected_gids.update(scope_gids)
        self._updateSelectionState()

    def _updateSelectionState(self):
        self.selectionCountLabel.setVisible(self._selection_mode)
        self.selectionCountLabel.setText(
            self.tr("已选 {} 项").format(len(self._selected_gids))
        )
        scope_gids = {item.gid for item in self._filtered_items}
        all_selected = bool(scope_gids) and scope_gids.issubset(
            self._selected_gids
        )
        self.selectAllButton.setVisible(self._selection_mode)
        self.selectAllButton.setEnabled(bool(scope_gids))
        self.selectAllButton.setIcon(
            FIF.CLEAR_SELECTION if all_selected else FIF.CHECKBOX
        )
        tooltip = (
            self.tr("取消全选")
            if all_selected
            else self.tr("全选当前筛选结果")
        )
        self.selectAllButton.setToolTip(tooltip)
        self.selectAllButton.setAccessibleName(tooltip)
        for card in self._cards:
            card.setSelectionState(
                self._selection_mode,
                card.item.gid in self._selected_gids,
            )

    def _scheduleSearch(self):
        self.searchTimer.start()

    def _onSearchTextChanged(self):
        self._clearSimilarSearch()
        self._scheduleSearch()
        if (
            cfg.get(cfg.mangaSearchHoverEnabled)
            and not self._search_pinned
            and not self.searchEdit.text().strip()
            and not self._isPointerInSearchArea()
        ):
            self.searchHoverTimer.start()

    def _cancelSimilarSearchWorker(self):
        if self._similar_search_worker is not None:
            self._similar_search_worker.cancelled = True
            self._similar_search_worker = None

    def _clearSimilarSearch(self, clear_query=False):
        was_active = bool(
            self._similar_search_worker is not None
            or self._similar_result_gids is not None
            or self._similar_reference_title
        )
        self._cancelSimilarSearchWorker()
        self._similar_result_gids = None
        self._similar_reference_title = ""
        if clear_query and was_active:
            self.searchEdit.blockSignals(True)
            self.searchEdit.clear()
            self.searchEdit.blockSignals(False)
        if self._collection_kind is None:
            self.sortCombo.setEnabled(True)

    def _searchSimilarGalleries(self, item: MangaItem):
        self._cancelSimilarSearchWorker()
        self.searchTimer.stop()
        self._similar_result_gids = None
        self._similar_reference_title = item.display_title
        self._show_all_manga = True
        self.sortCombo.setEnabled(False)
        self.openSearch()
        self.searchEdit.blockSignals(True)
        self.searchEdit.setText(item.display_title)
        self.searchEdit.blockSignals(False)
        self.searchEdit.selectAll()
        self.resultLabel.setText(
            self.tr("正在分析《{}》的主标题并搜索相似画廊…").format(
                item.display_title
            )
        )
        worker = SimilarMangaWorker(item, self._all_items)
        worker.signals.found.connect(
            lambda matches: self._finishSimilarSearch(worker, matches)
        )
        worker.signals.failed.connect(
            lambda message: self._failSimilarSearch(worker, message)
        )
        self._similar_search_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _finishSimilarSearch(self, worker, matches):
        if self._similar_search_worker is not worker:
            return
        self._similar_search_worker = None
        self._similar_result_gids = tuple(item.gid for item in matches)
        self.applyFilters(reset_page=True)

    def _failSimilarSearch(self, worker, message: str):
        if self._similar_search_worker is not worker:
            return
        self._clearSimilarSearch(clear_query=True)
        InfoBar.error(
            title=self.tr("相似画廊搜索失败"),
            content=message,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000,
            parent=self,
        )
        self.applyFilters(reset_page=True)

    def _onSortOrderChanged(self):
        order = self.sortCombo.currentData()
        if order not in ("desc", "asc"):
            return
        self._sort_order = order
        cfg.set(cfg.mangaSortOrder, order)
        self.applyFilters(reset_page=True)

    def toggleSearch(self):
        if self._search_pinned:
            self._search_pinned = False
            if cfg.get(cfg.mangaSearchHoverEnabled):
                self._search_opened_by_hover = True
                if (
                    not self._isPointerInSearchArea()
                    and not self.searchEdit.text().strip()
                ):
                    self.searchHoverTimer.start()
            else:
                self._hideSearchPanel()
        else:
            self.openSearch()

    def eventFilter(self, watched, event):
        if watched in (self.searchButton, self.searchPanel, self.searchEdit):
            if event.type() in (QEvent.Enter, QEvent.HoverEnter):
                self.searchHoverTimer.stop()
                if (
                    cfg.get(cfg.mangaSearchHoverEnabled)
                    and not self.searchPanel.isVisible()
                ):
                    self._showSearchPanel(focus=False, opened_by_hover=True)
            elif event.type() in (QEvent.Leave, QEvent.HoverLeave):
                if (
                    cfg.get(cfg.mangaSearchHoverEnabled)
                    and not self._search_pinned
                ):
                    self.searchHoverTimer.start()
        return super().eventFilter(watched, event)

    def _isPointerInSearchArea(self):
        return any(
            widget.isVisible() and widget.underMouse()
            for widget in (self.searchButton, self.searchPanel, self.searchEdit)
        )

    def _finishSearchHover(self):
        if (
            not cfg.get(cfg.mangaSearchHoverEnabled)
            or self._search_pinned
            or self._isPointerInSearchArea()
            or self.searchEdit.text().strip()
        ):
            return
        self._hideSearchPanel()

    def _onSearchHoverSettingChanged(self, enabled):
        if enabled:
            return
        self.searchHoverTimer.stop()
        if (
            not self._search_pinned
            and self._search_opened_by_hover
            and not self.searchEdit.text().strip()
        ):
            self._hideSearchPanel()

    def toggleClassification(self):
        show_sidebar = self.classificationCard.isHidden()
        total_width = max(self.tagSplitter.width(), sum(self.tagSplitter.sizes()), 1)
        if show_sidebar:
            self.classificationCard.show()
            maximum_width = max(190, int(self.width() * 0.3))
            sidebar_width = min(
                maximum_width,
                max(190, self._tag_sidebar_width),
            )
            self.tagSplitter.setSizes(
                [sidebar_width, max(1, total_width - sidebar_width)]
            )
        else:
            sizes = self.tagSplitter.sizes()
            if sizes and sizes[0] > 0:
                self._tag_sidebar_width = sizes[0]
            self.classificationCard.hide()
            self.tagSplitter.setSizes([0, total_width])
        self.tagButton.setIcon(
            FIF.CARE_LEFT_SOLID if show_sidebar else FIF.TAG
        )
        self._scheduleCardRelayout()
        QTimer.singleShot(40, self._scheduleCardRelayout)

    def _scheduleCardRelayout(self):
        if self._layout_mode != self.GRID_MODE:
            return
        self.contentPanel.updateGeometry()
        self.scrollArea.updateGeometry()
        QTimer.singleShot(0, self._relayoutCards)

    def openSearch(self):
        self._showSearchPanel(focus=True, opened_by_hover=False)

    def _showSearchPanel(self, focus: bool, opened_by_hover: bool):
        self.searchPanel.show()
        self.searchButton.setIcon(FIF.UP)
        self._search_opened_by_hover = bool(opened_by_hover)
        self._search_pinned = not opened_by_hover
        if focus:
            self.searchEdit.setFocus(Qt.ShortcutFocusReason)
            self.searchEdit.selectAll()

    def _hideSearchPanel(self):
        self.searchHoverTimer.stop()
        self.searchPanel.hide()
        self.searchButton.setIcon(FIF.SEARCH)
        self._search_opened_by_hover = False
        self._search_pinned = False

    def applyFilters(self, reset_page=False):
        self._updateTitleLabel()
        query = self.searchEdit.text().strip()
        query_terms = (
            self.tagSearchIndex.local_query_terms(query)
            if self.tagSearchIndex is not None
            else tuple(word.casefold() for word in query.split() if word)
        )
        if self._similar_result_gids is not None:
            by_gid = {item.gid: item for item in self._all_items}
            self._filtered_items = [
                by_gid[gid] for gid in self._similar_result_gids if gid in by_gid
            ]
            if reset_page:
                self._page = 1
            self._page = min(max(1, self._page), self.pageCount())
            self.resultLabel.setText(
                self.tr(
                    "找到 {} 个与《{}》相关的画廊（含当前画廊）；修改搜索词可退出"
                ).format(
                    len(self._filtered_items), self._similar_reference_title
                )
            )
            self._updatePagination()
            self._renderCards()
            return
        if self._collection_kind:
            self._filtered_items = [
                item for item in self._all_items if item.matches_terms(query_terms)
            ]
            order = {
                gid: position for position, gid in enumerate(self._collection_order)
            }
            self._filtered_items.sort(
                key=lambda item: order.get(item.gid, len(order))
            )
            if reset_page:
                self._page = 1
            self._page = min(max(1, self._page), self.pageCount())
            self.resultLabel.setText(
                self.tr("显示 {} 部漫画").format(len(self._filtered_items))
            )
            self._updatePagination()
            self._renderCards()
            return
        active_gids = self._activeTagGids()
        self._filtered_items = [
            item
            for item in self._all_items
            if int(item.gid) in active_gids
            and item.matches_terms(query_terms)
        ]
        self._filtered_items.sort(
            key=lambda item: (item.added_time, item.gid),
            reverse=self._sort_order == "desc",
        )
        if reset_page:
            self._page = 1
        self._page = min(max(1, self._page), self.pageCount())
        self.resultLabel.setText(
            self.tr("显示 {} / {} 部漫画").format(
                len(self._filtered_items),
                len(self._all_items),
            )
        )
        self._updatePagination()
        self._renderCards()

    def _updateTitleLabel(self):
        if self._collection_kind is not None:
            return
        title = self.tr("本地资源")
        if not self._show_all_manga:
            if self._tag_mode == self.TAG_CATEGORY:
                title = (
                    self.tr("未分类")
                    if self._primary_label_filter == "__none__"
                    else str(self._primary_label_filter)
                )
            elif self._tag_mode == self.TAG_TAXONOMY:
                title = self._taxonomyPath(self._taxonomy_filter_id) or title
        self.titleLabel.setText(title)
        self.titleLabel.setToolTip(title)

    def _matchesActiveTag(self, item: MangaItem, taxonomy_label_ids=None) -> bool:
        return int(item.gid) in self._activeTagGids()

    def _activeTagGids(self):
        if self._show_all_manga:
            return self._classification_index.gids_for(
                MangaClassificationIndex.ALL
            )
        if self._tag_mode == self.TAG_CATEGORY:
            return self._classification_index.gids_for(
                MangaClassificationIndex.CATEGORY,
                self._primary_label_filter,
            )
        return self._classification_index.gids_for(
            MangaClassificationIndex.TAXONOMY,
            self._taxonomy_filter_id,
        )

    def _activeTaxonomyLabelIds(self):
        if self._taxonomy_filter_id is None:
            return set()
        return set(
            self._classification_index.taxonomy_label_ids(
                self._taxonomy_filter_id
            )
        )

    def pageCount(self) -> int:
        return max(1, math.ceil(len(self._filtered_items) / self._page_size))

    def setPage(self, page: int):
        page = min(max(1, page), self.pageCount())
        if page == self._page:
            return
        self._page = page
        self._updatePagination()
        self._renderCards()
        self.scrollArea.verticalScrollBar().setValue(0)

    def _onPageChanged(self, page: int):
        self.setPage(page)

    def _onPageSizeChanged(self):
        page_size = self.pageSizeCombo.currentData()
        if not page_size:
            return
        self._page_size = int(page_size)
        cfg.set(cfg.mangaPageSize, self._page_size)
        self._page = 1
        self._updatePagination()
        self._renderCards()

    def _updatePagination(self):
        page_count = self.pageCount()
        self.pageSpinBox.blockSignals(True)
        self.pageSpinBox.setRange(1, page_count)
        self.pageSpinBox.setValue(self._page)
        self.pageSpinBox.blockSignals(False)
        self.pageCountLabel.setText(self.tr("/ {} 页").format(page_count))
        self.firstPageButton.setEnabled(self._page > 1)
        self.previousPageButton.setEnabled(self._page > 1)
        self.nextPageButton.setEnabled(self._page < page_count)
        self.lastPageButton.setEnabled(self._page < page_count)

    def setLayoutMode(self, mode: str):
        if mode not in (self.GRID_MODE, self.LIST_MODE) or mode == self._layout_mode:
            return
        self._layout_mode = mode
        self.layoutSwitch.setCurrentItem(mode)
        self._renderCards()

    def _renderCards(self):
        self._selected_gids.intersection_update(
            item.gid for item in self._filtered_items
        )
        self._updateSelectionState()
        self._clearContentLayout()
        self._cards = []
        self._empty_label = None

        if not self._filtered_items:
            self._cancelCoverPreload()
            message = self.tr("没有找到符合条件的本地漫画")
            if self._collection_kind == "favorites":
                message = self.tr("还没有收藏漫画")
            elif self._collection_kind == "history":
                message = self.tr("还没有本地浏览历史")
            if not self._all_items and self._load_worker is None:
                if not self._collection_kind:
                    message = self.tr("当前数据源中没有可用的本地漫画")
            self._empty_label = BodyLabel(message, self.scrollWidget)
            self.contentLayout.addWidget(self._empty_label, 0, 0)
            return

        start = (self._page - 1) * self._page_size
        page_items = self._filtered_items[start:start + self._page_size]
        card_class = MangaGridCard if self._layout_mode == self.GRID_MODE else MangaListCard
        self._cards = [
            card_class(
                item=item,
                open_callback=self._activateManga,
                label_menu_callback=self._showLabelMenu,
                selection_callback=self._setMangaSelected,
                selection_mode=self._selection_mode,
                selected=item.gid in self._selected_gids,
                cover_image=self._cover_cache.get(item.gid),
                parent=self.scrollWidget,
            )
            for item in page_items
        ]
        self._relayoutCards()
        self._preloadCovers()

    def _orderedReadingSequenceItems(self):
        if self._collection_kind or self._show_all_manga:
            return []
        active_gids = self._activeTagGids()
        return sorted(
            (item for item in self._all_items if item.gid in active_gids),
            key=lambda item: (item.added_time, item.gid),
            reverse=True,
        )

    def _activateManga(self, item: MangaItem):
        if self._tag_mode in (self.TAG_CATEGORY, self.TAG_TAXONOMY):
            items = self._orderedReadingSequenceItems()
            try:
                position = next(
                    index for index, current in enumerate(items)
                    if current.gid == item.gid
                )
            except StopIteration:
                self.mangaActivated.emit(item)
                return
            self.readingSequenceMangaActivated.emit(item, tuple(items), position)
            return
        self.mangaActivated.emit(item)

    def _preloadCovers(self):
        self._cancelCoverPreload()
        start = (self._page - 1) * self._page_size
        end = min(len(self._filtered_items), start + self._page_size * 4)
        items = [
            item
            for item in self._filtered_items[start:end]
            if item.gid not in self._cover_cache
        ]
        if not items:
            return
        worker = CoverPreloadWorker(self.source, self.userRepository, items)
        worker.signals.imageReady.connect(
            lambda gid, image: self._onCoverReady(worker, gid, image)
        )
        worker.signals.progressReady.connect(
            lambda gid, page_index: self._onProgressReady(
                worker, gid, page_index
            )
        )
        worker.signals.finished.connect(lambda: self._finishCoverPreload(worker))
        self._cover_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _onCoverReady(self, worker, gid: int, image):
        if self._cover_worker is not worker:
            return
        self._cover_cache[gid] = image
        self._cover_cache.move_to_end(gid)
        cache_limit = max(160, self._page_size * 4)
        while len(self._cover_cache) > cache_limit:
            self._cover_cache.popitem(last=False)
        for card in self._cards:
            if card.item.gid == gid:
                card.coverLabel.setImage(image)
                break

    def _finishCoverPreload(self, worker):
        if self._cover_worker is worker:
            self._cover_worker = None

    def _onProgressReady(self, worker, gid: int, page_index: int):
        if self._cover_worker is not worker:
            return
        self.updateReadingProgress(gid, page_index)

    def updateReadingProgress(
        self, gid: int, page_index: int, page_count=0, completed=False
    ):
        def update(item):
            if item.gid != gid:
                return item
            return replace(
                item,
                progress_page_index=max(0, int(page_index)),
                reading_completed=item.reading_completed or bool(completed),
                page_count=max(item.page_count, int(page_count or 0)),
            )

        self._all_items = [update(item) for item in self._all_items]
        self._filtered_items = [update(item) for item in self._filtered_items]
        for card in self._cards:
            if card.item.gid == gid:
                card.setItem(update(card.item))
                break

    def clearReadingProgress(self, gid: int):
        gid = int(gid)

        def update(item):
            if int(item.gid) != gid:
                return item
            return replace(
                item,
                progress_page_index=None,
                reading_completed=False,
            )

        self._all_items = [update(item) for item in self._all_items]
        self._filtered_items = [update(item) for item in self._filtered_items]
        for card in self._cards:
            if int(card.item.gid) == gid:
                card.setItem(update(card.item))
                break

    def _clearContentLayout(self):
        while self.contentLayout.count():
            layout_item = self.contentLayout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for column in range(self._last_columns):
            self.contentLayout.setColumnStretch(column, 0)
        self._last_columns = 0

    def _relayoutCards(self):
        if not self._cards:
            return

        while self.contentLayout.count():
            self.contentLayout.takeAt(0)
        for column in range(self._last_columns):
            self.contentLayout.setColumnStretch(column, 0)

        viewport_width = max(1, self.scrollArea.viewport().width())
        if self._layout_mode == self.LIST_MODE:
            self._last_columns = 1
            self.contentLayout.setColumnStretch(0, 1)
            for row, card in enumerate(self._cards):
                self.contentLayout.addWidget(card, row, 0)
            return

        spacing = self.contentLayout.horizontalSpacing()
        minimum_card_width = 188
        columns = max(1, (viewport_width + spacing) // (minimum_card_width + spacing))
        card_width = max(
            minimum_card_width,
            (viewport_width - spacing * (columns - 1)) // columns,
        )
        self._last_columns = columns
        for column in range(columns):
            self.contentLayout.setColumnStretch(column, 1)

        for index, card in enumerate(self._cards):
            card.setCardWidth(card_width)
            self.contentLayout.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        maximum_sidebar_width = max(190, int(self.width() * 0.3))
        self.classificationCard.setMaximumWidth(maximum_sidebar_width)
        sizes = self.tagSplitter.sizes()
        if len(sizes) == 2 and sizes[0] > maximum_sidebar_width:
            self.tagSplitter.setSizes(
                [maximum_sidebar_width, max(1, sum(sizes) - maximum_sidebar_width)]
            )
        if self._layout_mode == self.GRID_MODE:
            self._scheduleCardRelayout()
