import argparse
from datetime import datetime, timezone
from pathlib import Path

from .common import DocumentInfo, process_documents


def get_local_documents(root_path: Path, prefix: str) -> dict[str, DocumentInfo]:
    if not root_path.exists():
        raise RuntimeError(f"Error: Root directory '{root_path}' does not exist.")
    local_documents = {}

    # Use Path.walk (Python 3.12+) to efficiently prune directories
    for root, dirs, files in root_path.walk():
        # Exclude directories starting with a period, like .git, .venv, etc
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file_name in files:
            if file_name.startswith("."):
                continue
            local_path = root / file_name
            relative_path = local_path.relative_to(root_path)
            source_uri = f"{prefix}://{relative_path.as_posix()}"
            modified_timestamp = local_path.stat().st_mtime
            modified_at_utc = datetime.fromtimestamp(modified_timestamp, tz=timezone.utc)
            local_documents[source_uri] = DocumentInfo(modified_at_utc=modified_at_utc, local_path=local_path)
    return local_documents


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest local documents into Snowflake.")
    parser.add_argument("root_dir", help="Root directory containing documents")
    parser.add_argument("--prefix", default="local", help="URI scheme prefix for the documents (default: local)")
    args = parser.parse_args()
    root_path = Path(args.root_dir)
    local_documents = get_local_documents(root_path=root_path, prefix=args.prefix)
    process_documents(local_documents, prefix=args.prefix)


if __name__ == "__main__":
    main()
