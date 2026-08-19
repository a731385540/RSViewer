def normalize_gallery_marker_rules(rules):
    """Return non-empty, case-insensitively deduplicated marker rules."""

    if isinstance(rules, str):
        rules = (rules,)
    try:
        values = tuple(rules or ())
    except TypeError:
        values = ()

    normalized = []
    seen = set()
    for value in values:
        if value is None:
            continue
        rule = str(value).strip()
        key = rule.casefold()
        if not rule or key in seen:
            continue
        seen.add(key)
        normalized.append(rule)
    return tuple(normalized)


def gallery_matches_marker(gallery, title_rules=(), tag_rules=()):
    """Match title substrings or exact full/bare EH tags."""

    title = str(getattr(gallery, "title", "") or "").casefold()
    if any(
        rule.casefold() in title
        for rule in normalize_gallery_marker_rules(title_rules)
    ):
        return True

    full_tags = set()
    bare_tags = set()
    for value in getattr(gallery, "tags", ()) or ():
        tag = str(value).strip().casefold()
        if not tag:
            continue
        full_tags.add(tag)
        bare_tags.add(tag.split(":", 1)[-1].strip())

    for rule in normalize_gallery_marker_rules(tag_rules):
        marker = rule.casefold()
        if ":" in marker:
            if marker in full_tags:
                return True
        elif marker in bare_tags or marker in full_tags:
            return True
    return False
