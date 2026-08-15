from PySide6.QtCore import QModelIndex, QTimer, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QCompleter
from qfluentwidgets import SearchLineEdit
from qfluentwidgets.components.widgets.line_edit import CompleterMenu

from app.services.eh_tag_search import EhTagSearchIndex


_FULL_TEXT_ROLE = Qt.UserRole + 1
_CURSOR_POSITION_ROLE = Qt.UserRole + 2
_ENTRY_KIND_ROLE = Qt.UserRole + 3


class _EhTagCompleter(QCompleter):
    def pathFromIndex(self, index):
        full_text = index.data(_FULL_TEXT_ROLE)
        if full_text is not None:
            return str(full_text)
        return super().pathFromIndex(index)


class EhTagSearchLineEdit(SearchLineEdit):
    """Search input with cursor-aware, multi-token EH tag completion."""

    def __init__(
        self,
        tag_search_index=None,
        parent=None,
        search_history_service=None,
    ):
        super().__init__(parent)
        self._tag_search_index = tag_search_index or EhTagSearchIndex()
        self._search_history_service = search_history_service
        self._suggestions = {}
        self._completion_model = QStandardItemModel(self)
        self._tag_completer = _EhTagCompleter(self._completion_model, self)
        self._tag_completer.setCompletionMode(QCompleter.PopupCompletion)
        self._tag_completer.setCompletionRole(Qt.DisplayRole)
        self._tag_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._tag_completer.setWrapAround(False)
        self._tag_completer.setMaxVisibleItems(8)
        self._suggestion_popup_requested = False
        self._suggestion_timer = QTimer(self)
        self._suggestion_timer.setSingleShot(True)
        self._suggestion_timer.timeout.connect(self._showCompleterMenu)
        self.setCompleter(self._tag_completer)
        self.textEdited.connect(self._onTextEdited)
        self.searchSignal.connect(self.recordCurrentSearch)
        self.returnPressed.connect(self.recordCurrentSearch)
        self._tag_completer.activated[QModelIndex].connect(
            self._onSuggestionActivated
        )
        if self._search_history_service is not None:
            self._search_history_service.historyChanged.connect(
                self._onSearchHistoryChanged
            )

    def setTagSearchIndex(self, tag_search_index):
        self._tag_search_index = tag_search_index or EhTagSearchIndex()
        self._hideSuggestions()

    def _onTextEdited(self, *_args):
        self._suggestion_popup_requested = True
        self.refreshTagSuggestions()

    def refreshTagSuggestions(self, *_args):
        text = self.text()
        cursor_position = self.cursorPosition()
        start, end = current_tag_token_span(text, cursor_position)
        fragment = text[start:cursor_position]
        history_items = self._matchingHistory(text, fragment)
        suggestions = self._tag_search_index.search(fragment, limit=20)
        if not history_items and not suggestions:
            self._hideSuggestions()
            return

        self._completion_model.clear()
        self._suggestions.clear()
        for history_query in history_items:
            item = QStandardItem(history_query)
            item.setData(history_query, _FULL_TEXT_ROLE)
            item.setData(len(history_query), _CURSOR_POSITION_ROLE)
            item.setData("history", _ENTRY_KIND_ROLE)
            self._completion_model.appendRow(item)
        for suggestion in suggestions:
            display_text = suggestion.display_text
            replacement = suggestion.query_token
            full_text = f"{text[:start]}{replacement}{text[end:]}"
            item = QStandardItem(display_text)
            item.setData(full_text, _FULL_TEXT_ROLE)
            item.setData(start + len(replacement), _CURSOR_POSITION_ROLE)
            item.setData("tag", _ENTRY_KIND_ROLE)
            self._completion_model.appendRow(item)
            self._suggestions[display_text] = suggestion
        self._tag_completer.setCompletionPrefix("")
        self._suggestion_timer.start(0)

    def recordCurrentSearch(self, text=None):
        if self._search_history_service is None:
            return
        query = text if isinstance(text, str) else self.text()
        self._search_history_service.record(query)
        self._hideSuggestions()

    def _matchingHistory(self, text, fragment):
        if self._search_history_service is None:
            return []
        matches = self._search_history_service.search(text.strip())
        if matches or fragment.strip() == text.strip():
            return matches
        fragment_matches = self._search_history_service.search(fragment)
        return fragment_matches

    def _onSearchHistoryChanged(self, _items):
        if self.hasFocus():
            self.refreshTagSuggestions()

    def _showCompleterMenu(self):
        """Show QFluentWidgets' themed menu instead of Qt's native popup."""

        menu_visible = bool(self._completerMenu and self._completerMenu.isVisible())
        if not self._suggestion_popup_requested or (
            not self.hasFocus() and not menu_visible
        ):
            return
        if not self._completion_model.rowCount():
            self._hideSuggestions()
            return
        if not self._completerMenu:
            self.setCompleterMenu(CompleterMenu(self))
            self._completerMenu.setItemHeight(48)
            self._completerMenu.aboutToHide.connect(
                self._onCompleterMenuAboutToHide
            )
        changed = self._completerMenu.setCompletion(self._completion_model, 0)
        self._completerMenu.setMaxVisibleItems(
            self._tag_completer.maxVisibleItems()
        )
        if changed:
            self._completerMenu.popup()

    def activateTagSuggestion(self, display_text: str) -> bool:
        """Activate a visible completion; also provides a stable test hook."""

        for row in range(self._completion_model.rowCount()):
            index = self._completion_model.index(row, 0)
            if index.data(Qt.DisplayRole) == display_text:
                self._applyCompletionIndex(index)
                return True
        return False

    def suggestionTexts(self):
        return [
            self._completion_model.index(row, 0).data(Qt.DisplayRole)
            for row in range(self._completion_model.rowCount())
        ]

    def suggestionKinds(self):
        return [
            self._completion_model.index(row, 0).data(_ENTRY_KIND_ROLE)
            for row in range(self._completion_model.rowCount())
        ]

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if event.reason() == Qt.PopupFocusReason:
            return
        if self._search_history_service is not None:
            self._suggestion_popup_requested = True
            self.refreshTagSuggestions()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if event.reason() != Qt.PopupFocusReason:
            self._hideSuggestions()

    def _onCompleterMenuAboutToHide(self):
        self._suggestion_popup_requested = False
        self._suggestion_timer.stop()

    def _onSuggestionActivated(self, index):
        self._applyCompletionIndex(index)

    def _applyCompletionIndex(self, index):
        if not index.isValid():
            return
        full_text = index.data(_FULL_TEXT_ROLE)
        cursor_position = index.data(_CURSOR_POSITION_ROLE)
        if full_text is None:
            return
        self.setText(str(full_text))
        self.setCursorPosition(int(cursor_position))
        self._hideSuggestions()

    def _hideSuggestions(self):
        self._suggestion_popup_requested = False
        self._suggestion_timer.stop()
        if self._completerMenu:
            self._completerMenu.close()
        self._completion_model.clear()
        self._suggestions.clear()


def current_tag_token_span(text: str, cursor_position: int):
    """Return the token around the cursor, treating quoted spaces as content."""

    cursor_position = max(0, min(len(text), int(cursor_position)))
    start = 0
    quoted = False
    escaped = False
    for index, character in enumerate(text[:cursor_position]):
        if escaped:
            escaped = False
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character.isspace() and not quoted:
            start = index + 1

    end = cursor_position
    escaped = False
    for index, character in enumerate(text[cursor_position:], start=cursor_position):
        if escaped:
            escaped = False
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character.isspace() and not quoted:
            break
        end = index + 1
    return start, end
