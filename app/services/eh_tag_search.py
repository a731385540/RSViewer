import heapq
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple


DEFAULT_NAMESPACE_ABBREVIATIONS = {
    "artist": "a",
    "character": "c",
    "cosplayer": "cos",
    "female": "f",
    "group": "g",
    "language": "l",
    "location": "loc",
    "male": "m",
    "misc": "misc",
    "mixed": "x",
    "other": "o",
    "parody": "p",
    "reclass": "r",
}


def exact_tag_query_token(namespace: str, raw_tag: str, abbreviation="") -> str:
    """Return an EH exact-tag token such as ``l:\"chinese$\"``."""
    namespace = str(namespace or "").casefold().strip()
    raw_tag = str(raw_tag or "").strip()
    if not namespace or not raw_tag:
        return ""
    prefix = str(abbreviation or "").casefold().strip()
    prefix = prefix or DEFAULT_NAMESPACE_ABBREVIATIONS.get(namespace, namespace)
    escaped_tag = raw_tag.replace("\\", "\\\\").replace('"', '\\"')
    return f'{prefix}:"{escaped_tag}$"'


@dataclass(frozen=True)
class EhTagSuggestion:
    namespace: str
    abbreviation: str
    raw_tag: str
    translated_name: str

    @property
    def display_text(self) -> str:
        base = f"{self.namespace}：{self.raw_tag}"
        if self.translated_name and _normalize(self.translated_name) != _normalize(
            self.raw_tag
        ):
            return f"{base}\n{self.translated_name}"
        return base

    @property
    def query_token(self) -> str:
        escaped_tag = self.raw_tag.replace("\\", "\\\\").replace('"', '\\"')
        if any(character.isspace() for character in escaped_tag):
            return f'{self.namespace}:"{escaped_tag}"'
        return f"{self.namespace}:{escaped_tag}"


@dataclass(frozen=True)
class _IndexedTag:
    suggestion: EhTagSuggestion
    normalized_raw_tag: str
    normalized_translation: str


class EhTagSearchIndex:
    """Immutable in-memory EH tag lookup shared by local and online search."""

    def __init__(self, rows: Sequence[Tuple[str, str, str, str, str, str]] = ()):
        entries: List[_IndexedTag] = []
        namespace_aliases: Dict[str, str] = {}
        for (
            namespace,
            abbreviation,
            aliases_json,
            raw_tag,
            translation,
            _name,
        ) in rows:
            namespace = namespace.casefold().strip()
            abbreviation = abbreviation.casefold().strip()
            if not namespace or not abbreviation or not raw_tag:
                continue
            aliases = _load_aliases(aliases_json)
            for alias in (namespace, abbreviation, *aliases):
                normalized_alias = _normalize(alias)
                if normalized_alias:
                    namespace_aliases[normalized_alias] = namespace
            suggestion = EhTagSuggestion(
                namespace=namespace,
                abbreviation=abbreviation,
                raw_tag=raw_tag,
                translated_name=translation,
            )
            entries.append(
                _IndexedTag(
                    suggestion=suggestion,
                    normalized_raw_tag=_normalize(raw_tag),
                    normalized_translation=_normalize(translation),
                )
            )
        self._entries = tuple(entries)
        self._namespace_aliases = namespace_aliases
        self._namespace_abbreviations = {
            entry.suggestion.namespace: entry.suggestion.abbreviation
            for entry in self._entries
        }
        self._translations = {
            (entry.suggestion.namespace, entry.normalized_raw_tag):
                entry.suggestion.translated_name
            for entry in self._entries
            if entry.suggestion.translated_name
        }
        gram_index: Dict[str, List[int]] = {}
        for entry_index, entry in enumerate(self._entries):
            grams = set()
            for value in (
                entry.normalized_raw_tag,
                entry.normalized_translation,
            ):
                grams.update(value)
                grams.update(
                    value[offset : offset + 2]
                    for offset in range(max(0, len(value) - 1))
                )
            for gram in grams:
                gram_index.setdefault(gram, []).append(entry_index)
        self._gram_index = {
            gram: tuple(entry_indexes) for gram, entry_indexes in gram_index.items()
        }

    @classmethod
    def from_repository(cls, repository):
        return cls(repository.load_eh_tags())

    def __len__(self):
        return len(self._entries)

    def translated_name(self, namespace: str, raw_tag: str) -> str:
        """Return the exact imported translation without running a fuzzy search."""
        canonical_namespace = self._namespace_aliases.get(
            _normalize(namespace), str(namespace).casefold().strip()
        )
        return self._translations.get(
            (canonical_namespace, _normalize(raw_tag)), ""
        )

    def exact_query_token(self, namespace: str, raw_tag: str) -> str:
        canonical_namespace = self._namespace_aliases.get(
            _normalize(namespace), str(namespace).casefold().strip()
        )
        return exact_tag_query_token(
            canonical_namespace,
            raw_tag,
            self._namespace_abbreviations.get(canonical_namespace, ""),
        )

    @property
    def is_empty(self) -> bool:
        return not self._entries

    def search(self, fragment: str, limit: int = 20) -> List[EhTagSuggestion]:
        namespace_filter, query = self._split_fragment(fragment)
        if not query:
            return []

        matches = []
        for entry_index in self._candidate_indexes(query):
            entry = self._entries[entry_index]
            suggestion = entry.suggestion
            if namespace_filter and suggestion.namespace != namespace_filter:
                continue
            rank = _match_rank(
                query,
                entry.normalized_raw_tag,
                entry.normalized_translation,
            )
            if rank is None:
                continue
            matches.append(
                (
                    rank,
                    len(entry.normalized_raw_tag),
                    suggestion.namespace,
                    entry.normalized_raw_tag,
                    suggestion,
                )
            )
        result_limit = max(1, int(limit))
        best_matches = heapq.nsmallest(
            result_limit,
            matches,
            key=lambda value: value[:-1],
        )
        return [value[-1] for value in best_matches]

    def _candidate_indexes(self, query: str):
        size = min(2, len(query))
        grams = {
            query[offset : offset + size]
            for offset in range(len(query) - size + 1)
        }
        postings = [self._gram_index.get(gram, ()) for gram in grams]
        if not postings or any(not posting for posting in postings):
            return ()
        postings.sort(key=len)
        candidates = set(postings[0])
        for posting in postings[1:]:
            candidates.intersection_update(posting)
            if not candidates:
                break
        return candidates

    def local_query_terms(self, query: str) -> Tuple[str, ...]:
        terms = []
        for token in _split_query_tokens(query):
            if ":" in token:
                prefix, value = token.split(":", 1)
                if value.endswith("$"):
                    value = value[:-1]
                namespace = self._namespace_aliases.get(_normalize(prefix))
                if namespace:
                    token = f"{namespace}:{value}"
                else:
                    token = f"{prefix}:{value}"
            normalized_token = _normalize(token)
            if normalized_token:
                terms.append(normalized_token)
        return tuple(terms)

    def _split_fragment(self, fragment: str):
        fragment = fragment.strip()
        namespace_filter = ""
        if ":" in fragment:
            possible_alias, fragment = fragment.split(":", 1)
            namespace_filter = self._namespace_aliases.get(
                _normalize(possible_alias), ""
            )
        fragment = fragment.strip()
        if fragment.startswith('"'):
            fragment = fragment[1:]
        if fragment.endswith('"'):
            fragment = fragment[:-1]
        return namespace_filter, _normalize(fragment)


def _load_aliases(value: str):
    try:
        aliases = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(aliases, list):
        return ()
    return tuple(str(alias) for alias in aliases if alias)


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _match_rank(query: str, raw_tag: str, translation: str):
    values = tuple(value for value in (translation, raw_tag) if value)
    if any(query == value for value in values):
        return 0
    if any(query in value.split() for value in values):
        return 1
    if any(value.startswith(query) for value in values):
        return 2
    if any(
        any(word.startswith(query) for word in value.split()) for value in values
    ):
        return 3
    if any(query in value for value in values):
        return 4
    return None


def _split_query_tokens(query: str):
    tokens = []
    current = []
    quoted = False
    escaped = False
    for character in query:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    if current:
        tokens.append("".join(current))
    return tokens
