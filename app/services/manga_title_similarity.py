import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Callable, Iterable, Tuple


_EDGE_GROUP_RE = re.compile(r"^\s*(?:\[[^\]]{1,120}\]|\([^)]{1,120}\))\s*")
_TRAILING_GROUP_RE = re.compile(r"\s*(?:\[([^\]]{1,80})\]|\(([^)]{1,80})\))\s*$")
_TRAILING_NOISE_RE = re.compile(
    r"\b(?:english|chinese|japanese|korean|translated|translation|digital|"
    r"scan|color(?:ed)?|full\s*color|uncensored|decensored|ongoing|complete|"
    r"rewrite|variant|web|mobile|無修正|无修正|翻訳|翻译|中国翻訳|中文|漢化|汉化)\b",
    re.IGNORECASE,
)
_NUMBER = (
    r"(?:\d+(?:\.\d+)?|[ivxlcdm]+|[一二三四五六七八九十百零〇两兩]+|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
)
_CHAPTER_SUFFIX_RE = re.compile(
    rf"""
    (?:[\s\-_:|,，.。]*)(?:
        (?:ch(?:apter)?|chap|episode|ep|vol(?:ume)?|part|book|act|season)
            \.?\s*\#?\s*{_NUMBER}(?:\s*[-~～至]\s*{_NUMBER})?
        | 第\s*{_NUMBER}\s*(?:話|话|章|卷|巻|集|部|回|篇|册|冊)
        | {_NUMBER}\s*(?:話|话|章|巻|集|回)
        | (?:前|中|後|后)(?:編|篇)
        | (?:上|中|下)(?:巻|卷|册|冊)
    )\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_PLAIN_NUMBER_SUFFIX_RE = re.compile(rf"^(.*\S)\s+[#＃]?{_NUMBER}\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class TitleFingerprint:
    forms: Tuple[str, ...]


def _normalize_words(value: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(value or "")).casefold()
    result = []
    previous_space = True
    for character in value:
        if unicodedata.category(character)[0] in ("L", "N"):
            result.append(character)
            previous_space = False
        elif not previous_space:
            result.append(" ")
            previous_space = True
    return "".join(result).strip()


def _strip_trailing_noise_groups(value: str) -> str:
    while True:
        match = _TRAILING_GROUP_RE.search(value)
        if match is None:
            return value.strip()
        content = match.group(1) or match.group(2) or ""
        is_chapter_group = bool(
            _CHAPTER_SUFFIX_RE.fullmatch(content.strip())
            or re.fullmatch(_NUMBER, content.strip(), re.IGNORECASE)
        )
        if _TRAILING_NOISE_RE.search(content) is None and not is_chapter_group:
            return value.strip()
        value = value[: match.start()]


def _strip_chapter_suffix(value: str) -> str:
    value = _strip_trailing_noise_groups(value)
    previous = None
    while value and value != previous:
        previous = value
        value = _CHAPTER_SUFFIX_RE.sub("", value).strip()
        value = _strip_trailing_noise_groups(value)
    plain_number = _PLAIN_NUMBER_SUFFIX_RE.match(value)
    if plain_number is not None:
        prefix = _normalize_words(plain_number.group(1))
        compact = prefix.replace(" ", "")
        has_multiple_words = len(prefix.split()) >= 2
        has_long_cjk_title = len(compact) >= 4 and any(
            "CJK" in unicodedata.name(character, "") for character in compact
        )
        if has_multiple_words or has_long_cjk_title:
            value = plain_number.group(1).strip()
    return value


@lru_cache(maxsize=50_000)
def title_fingerprint(title: str) -> TitleFingerprint:
    """Build comparable full-title and work-title forms from a noisy title."""
    raw = unicodedata.normalize("NFKC", html.unescape(title or "")).strip()
    candidates = [raw]
    stripped = raw
    while True:
        updated = _EDGE_GROUP_RE.sub("", stripped, count=1)
        if updated == stripped:
            break
        stripped = updated.strip()
        if stripped:
            candidates.append(stripped)

    forms = []
    for candidate in candidates:
        for form in (candidate, _strip_chapter_suffix(candidate)):
            normalized = _normalize_words(form)
            if normalized and normalized not in forms:
                forms.append(normalized)
    return TitleFingerprint(tuple(forms))


def _compact(value: str) -> str:
    return value.replace(" ", "")


def _is_usable(value: str) -> bool:
    compact = _compact(value)
    if len(compact) >= 5:
        return True
    cjk_count = sum("CJK" in unicodedata.name(character, "") for character in compact)
    return cjk_count >= 3


@lru_cache(maxsize=256)
def _character_trigrams(value: str) -> frozenset:
    compact = _compact(value)
    if len(compact) < 3:
        return frozenset((compact,))
    return frozenset(compact[index:index + 3] for index in range(len(compact) - 2))


def fingerprint_similarity(left: TitleFingerprint, right: TitleFingerprint) -> float:
    best = 0.0
    left_forms = tuple(
        (_compact(form), _character_trigrams(form))
        for form in left.forms
        if _is_usable(form)
    )
    right_forms = tuple(
        (_compact(form), _character_trigrams(form))
        for form in right.forms
        if _is_usable(form)
    )
    for left_compact, left_trigrams in left_forms:
        for right_compact, right_trigrams in right_forms:
            if left_compact == right_compact:
                return 1.0
            shorter = min(len(left_compact), len(right_compact))
            longer = max(len(left_compact), len(right_compact))
            if shorter < 5:
                continue
            contains = left_compact in right_compact or right_compact in left_compact
            ratio = 0.0
            if contains:
                coverage = shorter / longer
                if coverage >= 0.72:
                    ratio = max(ratio, 0.86 + 0.1 * coverage)
            overlap = 2 * len(left_trigrams.intersection(right_trigrams)) / max(
                1, len(left_trigrams) + len(right_trigrams)
            )
            if not contains and overlap < 0.36:
                continue
            ratio = max(
                ratio,
                SequenceMatcher(None, left_compact, right_compact).ratio(),
            )
            threshold = 0.84 if shorter >= 12 else 0.89 if shorter >= 8 else 0.94
            if ratio >= threshold:
                best = max(best, ratio)
    return best


def _item_titles(item) -> Tuple[str, ...]:
    titles = tuple(
        dict.fromkeys(
            title.strip()
            for title in (
                getattr(item, "original_title", ""),
                getattr(item, "english_title", ""),
            )
            if title and title.strip()
        )
    )
    if titles:
        return titles
    folder = getattr(item, "folder", None)
    return (str(getattr(folder, "name", "")).strip(),) if folder else ()


def _item_fingerprints(item) -> Tuple[TitleFingerprint, ...]:
    return tuple(title_fingerprint(title) for title in _item_titles(item))


def _fingerprint_set_similarity(left, right) -> float:
    return max(
        (
            fingerprint_similarity(left_fingerprint, right_fingerprint)
            for left_fingerprint in left
            for right_fingerprint in right
        ),
        default=0.0,
    )


def manga_similarity_score(reference, candidate) -> float:
    if int(reference.gid) == int(candidate.gid):
        return 2.0
    return _fingerprint_set_similarity(
        _item_fingerprints(reference), _item_fingerprints(candidate)
    )


def find_similar_manga(
    reference,
    items: Iterable,
    should_cancel: Callable[[], bool] = lambda: False,
) -> Tuple:
    """Return the reference and confidently similar manga in score order."""
    matches = []
    reference_fingerprints = _item_fingerprints(reference)
    for item in items:
        if should_cancel():
            return ()
        score = (
            2.0
            if int(reference.gid) == int(item.gid)
            else _fingerprint_set_similarity(
                reference_fingerprints, _item_fingerprints(item)
            )
        )
        if score >= 0.84:
            matches.append((score, int(getattr(item, "added_time", 0)), int(item.gid), item))
    matches.sort(key=lambda entry: (-entry[0], -entry[1], -entry[2]))
    return tuple(entry[3] for entry in matches)
