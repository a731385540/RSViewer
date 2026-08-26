from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple


def merge_downloaded_page_path(page_paths, page_index, page_path):
    """Replace one numbered page path and keep the local reading order stable."""
    target_number = int(page_index) + 1
    resolved_path = Path(page_path)
    merged = []
    for path in page_paths:
        path = Path(path)
        if path.stem.isdigit() and int(path.stem) == target_number:
            continue
        merged.append(path)
    merged.append(resolved_path)

    def page_key(path):
        try:
            return 0, int(path.stem)
        except ValueError:
            return 1, path.name.casefold()

    return tuple(sorted(merged, key=page_key))


def local_page_path_map(page_paths):
    """Map zero-based page numbers to paths without collapsing EhViewer gaps."""
    paths = tuple(Path(path) for path in page_paths)
    numbered = {}
    for path in paths:
        if not path.stem.isdigit() or int(path.stem) <= 0:
            return {index: path for index, path in enumerate(paths)}
        page_index = int(path.stem) - 1
        if page_index in numbered:
            return {index: path for index, path in enumerate(paths)}
        numbered[page_index] = path
    return numbered


def local_page_slot_count(item):
    """Return local preview/reader slots, including sidecar-declared missing pages."""
    actual_count = len(item.page_paths)
    sidecar_count = int(item.page_count or 0)
    has_complete_sidecar = (
        sidecar_count > 0
        and len(item.page_tokens) == sidecar_count
        and all(item.page_tokens)
    )
    total = max(actual_count, sidecar_count if has_complete_sidecar else 0)
    cutoff = getattr(item, "ad_cleanup_cutoff_page_index", None)
    if cutoff is not None:
        total = min(total, max(0, int(cutoff)))
    return total


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
    downloaded_page_count: int = 0
    download_complete: Optional[bool] = None
    gallery_token: str = ""
    page_tokens: Tuple[str, ...] = ()
    source_site: str = ""
    source_id: str = ""
    posted: str = ""
    uploader: str = ""
    rating: Optional[float] = None
    language: str = ""
    file_size: str = ""
    rating_count: int = 0
    visible: str = ""
    favorited: str = ""
    parent_gallery: str = ""
    newer_gallery_urls: Tuple[str, ...] = ()
    metadata_synced: bool = False
    progress_page_index: Optional[int] = None
    reading_completed: bool = False
    taxonomy_label_ids: Tuple[int, ...] = ()
    taxonomy_labels: Tuple[str, ...] = ()
    is_favorite: bool = False
    original_mode: str = ""
    original_state: str = ""
    original_page_paths: Tuple[Path, ...] = ()
    original_completed_pages: int = 0
    original_fallback_to_standard: bool = False
    original_page_modes: Tuple[str, ...] = ()
    standard_download_pending: bool = False
    ad_cleanup_state: str = ""
    ad_cleanup_cutoff_page_index: Optional[int] = None
    ad_cleanup_pending_action: str = ""
    ad_cleanup_error: str = ""

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
