#!/usr/bin/env python3
import argparse
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.user_library_repository import UserLibraryRepository
from app.services.eh_tag_importer import parse_eh_tag_database


def build_parser():
    parser = argparse.ArgumentParser(
        description="将克隆的 EH 标签 Markdown 数据导入 RSViewer 自有 SQLite。"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "Database" / "database",
        help="Markdown 标签目录（默认：Database/database）",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "data" / "rsviewer.db",
        help="RSViewer 自有数据库路径",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    snapshot = parse_eh_tag_database(args.source)
    repository = UserLibraryRepository(args.database)
    repository.replace_eh_tags(snapshot.namespace_rows(), snapshot.tag_rows())

    counts = Counter(tag.namespace for tag in snapshot.tags)
    print(
        f"已导入 {len(snapshot.tags)} 个标签、"
        f"{len(snapshot.namespaces)} 个命名空间到 {repository.database_path}"
    )
    for namespace in snapshot.namespaces:
        print(
            f"  {namespace.key} ({namespace.abbreviation}): "
            f"{counts[namespace.key]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
