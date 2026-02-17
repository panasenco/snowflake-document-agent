import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Callable

from .common import process_changed_documents


def get_local_documents(root_path: Path, prefix: str) -> dict[str, tuple[datetime, str]]:
    """Returns a dictionary with source_uris as the keys and (modified_at_utc, "") tuples as the values for all files
    in the root folder and its subfolders.
    """
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
            local_documents[source_uri] = (modified_at_utc, "")
    return local_documents


def get_local_downloader(root_path: Path, prefix: str) -> Callable[[str], Path]:
    """Given a root folder and a prefix, returns a downloader function that returns paths for source_uri's.
    The downloader function accepts only source_uri's that start with "{prefix}://" and errors otherwise.
    The downloader function appends the part after the prefix to the root path and returns that path.
    If the path doesn't exist, the downloader function errors.
    This function doesn't actually "download" anything obviously but is named this way for consistency with others.
    """

    def local_downloader(source_uri: str) -> Path:
        assert source_uri.startswith(f"{prefix}://"), f"URI {source_uri} doesn't begin with required prefix {prefix}"
        path = root_path / source_uri.removeprefix(f"{prefix}://")
        assert path.exists(), f"Path {path} for URI {source_uri} doesn't exist"
        assert path.is_file(), f"Path {path} for URI {source_uri} is not a file"
        return path

    return local_downloader


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest local documents into Snowflake.")
    parser.add_argument(
        "root_dir",
        help="Root directory containing documents",
    )
    parser.add_argument(
        "-c", "--snowflake-connection", default="default", help="Name of Snowflake connection to use (default: default)"
    )
    parser.add_argument("-p", "--prefix", default="local", help="URI scheme prefix for the documents (default: local)")
    parser.add_argument(
        "-v",
        "--verbose",
        help="Be verbose. Include once for INFO output, twice for DEBUG output.",
        action="count",
        default=0,
    )
    args = parser.parse_args()
    LOGGING_LEVELS = [logging.WARNING, logging.INFO, logging.DEBUG]
    logging.basicConfig(level=LOGGING_LEVELS[min(args.verbose, len(LOGGING_LEVELS) - 1)])  # cap to last level index
    args = parser.parse_args()
    root_path = Path(args.root_dir)
    local_documents = get_local_documents(root_path=root_path, prefix=args.prefix)
    local_downloader = get_local_downloader(root_path=root_path, prefix=args.prefix)
    process_changed_documents(
        local_documents, connection=args.snowflake_connection, downloader=local_downloader, prefix=args.prefix
    )


if __name__ == "__main__":
    main()
