import re

from app.services.eh_tag_search import DEFAULT_NAMESPACE_ABBREVIATIONS


_STRUCTURED_TOKEN_PATTERN = re.compile(
    r'(^|\s)([a-zA-Z][a-zA-Z0-9_-]*):("(?:\\.|[^"\\])*"|[^\s]+)'
)
_ABBREVIATION_NAMESPACES = {
    abbreviation.casefold(): namespace
    for namespace, abbreviation in DEFAULT_NAMESPACE_ABBREVIATIONS.items()
}
_NHC_GENERIC_TAG_NAMESPACES = {
    "cosplayer",
    "female",
    "location",
    "male",
    "misc",
    "mixed",
    "other",
    "reclass",
}


def adapt_online_query(value, site):
    """Translate the shared full-namespace syntax for one online provider.

    The text stored by the UI and search history is left untouched. Only the
    request sent to a provider is rewritten, so copied legacy abbreviations
    remain compatible.
    """

    query = str(value or "").strip()
    site = str(site or "").casefold()
    if not query or site not in {"ehentai", "exhentai", "nhc", "nhn"}:
        return query

    def replace(match):
        whitespace, raw_namespace, raw_value = match.groups()
        namespace = raw_namespace.casefold()
        if site in {"nhc", "nhn"}:
            namespace = _ABBREVIATION_NAMESPACES.get(namespace, namespace)
            if site == "nhc" and namespace in _NHC_GENERIC_TAG_NAMESPACES:
                namespace = "tag"
            raw_value = _remove_eh_exact_suffix(raw_value)
        else:
            namespace = DEFAULT_NAMESPACE_ABBREVIATIONS.get(namespace, namespace)
        return f"{whitespace}{namespace}:{raw_value}"

    return _STRUCTURED_TOKEN_PATTERN.sub(replace, query)


def split_structured_query(value):
    """Return free text and decoded ``(namespace, value)`` query tokens."""

    tokens = []

    def collect(match):
        _whitespace, namespace, raw_value = match.groups()
        tokens.append((namespace.casefold(), _decode_query_value(raw_value)))
        return " "

    free_text = " ".join(_STRUCTURED_TOKEN_PATTERN.sub(collect, value).split())
    return free_text, tuple(tokens)


def online_tag_query_token(namespace, raw_tag, site):
    """Build a copyable exact tag query for the gallery's own source."""

    namespace = str(namespace or "").casefold().strip()
    raw_tag = str(raw_tag or "").strip()
    site = str(site or "").casefold()
    if not namespace or not raw_tag:
        return ""
    escaped = raw_tag.replace("\\", "\\\\").replace('"', '\\"')
    if site in {"nhc", "nhn"}:
        namespace = _ABBREVIATION_NAMESPACES.get(namespace, namespace)
        if site == "nhc" and namespace in _NHC_GENERIC_TAG_NAMESPACES:
            namespace = "tag"
        return f'{namespace}:"{escaped}"'
    return ""


def _remove_eh_exact_suffix(value):
    if value.startswith('"') and value.endswith('"'):
        content = value[1:-1]
        if content.endswith("$") and not content.endswith("\\$"):
            content = content[:-1]
        return f'"{content}"'
    return value[:-1] if value.endswith("$") else value


def _decode_query_value(value):
    value = _remove_eh_exact_suffix(str(value or ""))
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    decoded = []
    escaped = False
    for character in value:
        if escaped:
            decoded.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        else:
            decoded.append(character)
    if escaped:
        decoded.append("\\")
    return "".join(decoded).strip()
