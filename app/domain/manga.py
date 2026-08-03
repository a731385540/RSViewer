from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class MangaItem:
    """来自外部漫画数据源的只读本地漫画条目。"""

    gid: int
    english_title: str
    original_title: str
    category: int
    category_name: str
    tags: Tuple[str, ...]
    folder: Path
    cover_path: Path
    page_count: int

    @property
    def display_title(self) -> str:
        return self.original_title or self.english_title or self.folder.name

    @property
    def secondary_title(self) -> str:
        if self.english_title and self.english_title != self.display_title:
            return self.english_title
        return ""

    @property
    def searchable_text(self) -> str:
        values = (self.english_title, self.original_title, *self.tags)
        return "\n".join(value.casefold() for value in values if value)

    def matches(self, query: str) -> bool:
        words = [word.casefold() for word in query.split() if word]
        return all(word in self.searchable_text for word in words)
