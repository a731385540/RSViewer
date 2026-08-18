from collections import defaultdict


class MangaClassificationIndex:
    """In-memory inverted index for local gallery classifications."""

    ALL = "all"
    CATEGORY = "category"
    PLAYLIST = "playlist"
    TAXONOMY = "taxonomy"
    UNCLASSIFIED = "__none__"

    def __init__(self):
        self._all_gids = set()
        self._memberships = {
            self.CATEGORY: defaultdict(set),
            self.PLAYLIST: defaultdict(set),
            self.TAXONOMY: defaultdict(set),
        }
        self._gid_memberships = {}
        self._taxonomy_children = defaultdict(set)

    def rebuild(self, items, taxonomy_labels=()):
        self._all_gids.clear()
        for groups in self._memberships.values():
            groups.clear()
        self._gid_memberships.clear()
        self.set_taxonomy_labels(taxonomy_labels)
        for item in items:
            self.upsert(item)

    def set_taxonomy_labels(self, taxonomy_labels):
        self._taxonomy_children.clear()
        for label_id, parent_id, _name, _count in taxonomy_labels:
            if parent_id is not None:
                self._taxonomy_children[int(parent_id)].add(int(label_id))

    def upsert(self, item):
        gid = int(item.gid)
        self.remove(gid)
        memberships = {
            self.CATEGORY: (str(item.primary_label or self.UNCLASSIFIED),),
            self.PLAYLIST: tuple(
                dict.fromkeys(str(name) for name in item.multiple_labels if name)
            ),
            self.TAXONOMY: tuple(
                dict.fromkeys(int(label_id) for label_id in item.taxonomy_label_ids)
            ),
        }
        self._all_gids.add(gid)
        self._gid_memberships[gid] = memberships
        for kind, labels in memberships.items():
            for label in labels:
                self._memberships[kind][label].add(gid)

    def remove(self, gid):
        gid = int(gid)
        memberships = self._gid_memberships.pop(gid, None)
        self._all_gids.discard(gid)
        if memberships is None:
            return
        for kind, labels in memberships.items():
            groups = self._memberships[kind]
            for label in labels:
                gids = groups.get(label)
                if gids is None:
                    continue
                gids.discard(gid)
                if not gids:
                    groups.pop(label, None)

    def all_gids(self):
        return frozenset(self._all_gids)

    def gids_for(self, kind, label=None):
        kind = str(kind)
        if kind == self.ALL:
            return self.all_gids()
        if kind not in self._memberships or label is None:
            return frozenset()
        if kind != self.TAXONOMY:
            return frozenset(self._memberships[kind].get(label, ()))
        gids = set()
        for label_id in self.taxonomy_label_ids(int(label)):
            gids.update(self._memberships[self.TAXONOMY].get(label_id, ()))
        return frozenset(gids)

    def taxonomy_label_ids(self, label_id):
        result = {int(label_id)}
        pending = [int(label_id)]
        while pending:
            parent_id = pending.pop()
            for child_id in self._taxonomy_children.get(parent_id, ()):
                if child_id not in result:
                    result.add(child_id)
                    pending.append(child_id)
        return frozenset(result)
