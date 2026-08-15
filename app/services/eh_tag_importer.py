import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_DIVIDER_RE = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True)
class EhTagNamespaceImport:
    key: str
    display_name: str
    abbreviation: str
    aliases: Tuple[str, ...]
    source_file: str

    def database_tuple(self) -> Tuple[str, str, str, str, str]:
        return (
            self.key,
            self.display_name,
            self.abbreviation,
            json.dumps(self.aliases, ensure_ascii=False),
            self.source_file,
        )


@dataclass(frozen=True)
class EhTagImport:
    namespace: str
    raw_tag: str
    translated_name: str
    description: str
    external_links: str
    source_file: str

    def database_tuple(self) -> Tuple[str, str, str, str, str, str]:
        return (
            self.namespace,
            self.raw_tag,
            self.translated_name,
            self.description,
            self.external_links,
            self.source_file,
        )


@dataclass(frozen=True)
class EhTagImportSnapshot:
    namespaces: Tuple[EhTagNamespaceImport, ...]
    tags: Tuple[EhTagImport, ...]

    def namespace_rows(self) -> List[Tuple[str, str, str, str, str]]:
        return [namespace.database_tuple() for namespace in self.namespaces]

    def tag_rows(self) -> List[Tuple[str, str, str, str, str, str]]:
        return [tag.database_tuple() for tag in self.tags]


def parse_eh_tag_database(database_directory: Path) -> EhTagImportSnapshot:
    """Parse the cloned EH tag translation Markdown database.

    Parsing completes before callers replace any database data, so malformed
    input cannot leave the existing imported snapshot half-written.
    """

    database_directory = Path(database_directory).resolve()
    if not database_directory.is_dir():
        raise FileNotFoundError(f"EH 标签目录不存在：{database_directory}")

    namespaces: List[EhTagNamespaceImport] = []
    tags_by_key: Dict[Tuple[str, str], EhTagImport] = {}
    markdown_files = sorted(database_directory.glob("*.md"))
    if not markdown_files:
        raise ValueError(f"EH 标签目录中没有 Markdown 文件：{database_directory}")

    for markdown_path in markdown_files:
        text = markdown_path.read_text(encoding="utf-8-sig")
        metadata, body_lines = _parse_front_matter(text, markdown_path)
        namespace = _clean_scalar(metadata.get("key", "")).casefold()
        if not namespace or namespace == "rows":
            continue
        abbreviation = _clean_scalar(metadata.get("abbr", "")).casefold()
        if not abbreviation:
            raise ValueError(f"{markdown_path.name} 缺少 abbr 命名空间缩写")
        display_name = _clean_display_text(metadata.get("name", "")) or namespace
        alias_values = metadata.get("aliases", ())
        if isinstance(alias_values, str):
            alias_values = (alias_values,)
        aliases = tuple(
            dict.fromkeys(
                cleaned_alias.casefold()
                for alias in alias_values
                if (cleaned_alias := _clean_scalar(alias))
            )
        )
        namespaces.append(
            EhTagNamespaceImport(
                key=namespace,
                display_name=display_name,
                abbreviation=abbreviation,
                aliases=aliases,
                source_file=markdown_path.name,
            )
        )

        for cells in _iter_markdown_table_rows(body_lines):
            raw_tag = _clean_tag_text(cells[0])
            if (
                not raw_tag
                or raw_tag == "原始标签"
                or _TABLE_DIVIDER_RE.fullmatch(raw_tag)
            ):
                continue
            translated_name = _clean_display_text(cells[1])
            tag = EhTagImport(
                namespace=namespace,
                raw_tag=raw_tag,
                translated_name=translated_name,
                description=cells[2].strip(),
                external_links=cells[3].strip(),
                source_file=markdown_path.name,
            )
            key = (namespace, raw_tag.casefold())
            previous = tags_by_key.get(key)
            if previous is not None and previous != tag:
                raise ValueError(
                    f"{markdown_path.name} 存在冲突的重复标签：{namespace}:{raw_tag}"
                )
            tags_by_key[key] = tag

    if not namespaces:
        raise ValueError("没有找到带 key/abbr 的 EH 标签命名空间")
    if not tags_by_key:
        raise ValueError("没有从 Markdown 表格中解析到 EH 标签")

    namespace_keys = {namespace.key for namespace in namespaces}
    if len(namespace_keys) != len(namespaces):
        raise ValueError("EH 标签 Markdown 中存在重复的命名空间 key")
    abbreviations = {namespace.abbreviation for namespace in namespaces}
    if len(abbreviations) != len(namespaces):
        raise ValueError("EH 标签 Markdown 中存在重复的命名空间 abbr")
    missing_namespaces = {
        tag.namespace
        for tag in tags_by_key.values()
        if tag.namespace not in namespace_keys
    }
    if missing_namespaces:
        raise ValueError(f"标签引用了未知命名空间：{sorted(missing_namespaces)}")

    return EhTagImportSnapshot(
        namespaces=tuple(sorted(namespaces, key=lambda value: value.key)),
        tags=tuple(
            sorted(
                tags_by_key.values(),
                key=lambda value: (value.namespace, value.raw_tag.casefold()),
            )
        ),
    )


def _parse_front_matter(text: str, source_path: Path):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{source_path.name} 缺少 Markdown front matter")
    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise ValueError(f"{source_path.name} 的 front matter 未闭合") from error

    metadata: Dict[str, object] = {}
    current_list_key = None
    for line in lines[1:closing_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if current_list_key and stripped.startswith("-"):
            metadata[current_list_key].append(_clean_scalar(stripped[1:]))
            continue
        current_list_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = _clean_scalar(value)
        if not value:
            metadata[key] = []
            current_list_key = key
        else:
            metadata[key] = value
    return metadata, lines[closing_index + 1 :]


def _iter_markdown_table_rows(lines: Iterable[str]):
    for line in lines:
        cells = _split_markdown_table_row(line)
        if cells is not None and len(cells) >= 4:
            yield cells[:4]


def _split_markdown_table_row(line: str):
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells = []
    current = []
    escaped = False
    for character in stripped[1:]:
        if escaped:
            if character == "|":
                current.append("|")
            else:
                current.extend(("\\", character))
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    if current or not stripped.endswith("|"):
        cells.append("".join(current).strip())
    return cells


def _clean_scalar(value) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    return text.strip()


def _clean_tag_text(value) -> str:
    return re.sub(r"\s+", " ", _clean_scalar(value).strip("` "))


def _clean_display_text(value) -> str:
    text = _MARKDOWN_IMAGE_RE.sub("", _clean_scalar(value))
    text = _HTML_TAG_RE.sub(" ", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()
