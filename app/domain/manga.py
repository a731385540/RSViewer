from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple


@dataclass(frozen=True)
class MangaItem:
    """来自外部漫画数据源的只读本地漫画条目。"""

    gid: int
    english_title: str
    original_title: str
    category: int
    category_name: str
    primary_label: str
    multiple_labels: Tuple[str, ...]
    tags: Tuple[str, ...]
    folder: Path
    cover_path: Path
    thumbnail_path: Optional[Path]
    page_paths: Tuple[Path, ...]
    page_count: int
    added_time: int = 0
    progress_page_index: Optional[int] = None
    taxonomy_label_ids: Tuple[int, ...] = ()
    taxonomy_labels: Tuple[str, ...] = ()
    is_favorite: bool = False

    @property
    def cover_image_path(self) -> Path:
        """列表优先使用 EhViewer 的小缩略图，避免解码原始大图。"""
        return self.thumbnail_path or self.cover_path

    @property
    def display_title(self) -> str:
        return self.original_title or self.english_title or self.folder.name

    @property
    def secondary_title(self) -> str:
        if self.english_title and self.english_title != self.display_title:
            return self.english_title
        return ""

    @property
    def progress_page_number(self) -> Optional[int]:
        """Return the user-facing one-based reading page number."""
        if self.progress_page_index is None:
            return None
        return self.progress_page_index + 1

    @property
    def searchable_text(self) -> str:
        values = (
            self.english_title,
            self.original_title,
            self.primary_label,
            *self.multiple_labels,
            *self.taxonomy_labels,
            *self.tags,
        )
        return "\n".join(value.casefold() for value in values if value)

    def matches(self, query: str) -> bool:
        words = [word.casefold() for word in query.split() if word]
        return self.matches_terms(words)

    def matches_terms(self, terms: Sequence[str]) -> bool:
        searchable_text = self.searchable_text
        return all(str(term).casefold() in searchable_text for term in terms if term)
