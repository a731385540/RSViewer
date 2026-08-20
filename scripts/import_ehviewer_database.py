import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.user_library_repository import UserLibraryRepository
from app.services.ehviewer_database_transfer import import_ehviewer_database


def parse_args():
    parser = argparse.ArgumentParser(
        description="Import an EhViewer eh.db into RSViewer's own database."
    )
    parser.add_argument("source", type=Path, help="path to the source eh.db")
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "data" / "rsviewer.db",
        help="RSViewer database path",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="clear previously imported EhViewer compatibility rows first",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    repository = UserLibraryRepository(args.database)
    result = import_ehviewer_database(args.source, repository, args.replace)
    print(
        f"Imported {result.gallery_count} galleries from {result.path} "
        f"into {repository.database_path}"
    )


if __name__ == "__main__":
    main()
