from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit


def get_local_documents(root_path: Path, source_name: str = "") -> Iterator[tuple[str, str, None]]:
    """Returns all files in the root folder and its subfolders.
    Returns a dictionary with source_uri's (absolute paths with timestamps) as the keys and
    display_name's (paths relative to the root directory) as the values.
    The source_name is the URI netloc, e.g. 'local' for 'file://local/absolute/path/to/file'.
    """
    root_path = root_path.absolute()
    if not root_path.exists():
        raise RuntimeError(f"Error: Root directory '{root_path}' does not exist.")
    # Use Path.walk (Python 3.12+) to efficiently prune directories
    for root, dirs, files in root_path.walk():
        # Exclude directories starting with a period, like .git, .venv, etc
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file_name in files:
            if file_name.startswith("."):
                continue
            file_path = (root / file_name).absolute()
            source_uri = urlunsplit(
                (
                    "file",
                    source_name,
                    urlsplit(file_path.as_uri()).path,
                    urlencode({"m": int(file_path.stat().st_mtime)}),
                    "",
                )
            )
            yield source_uri, file_path.relative_to(root_path).as_posix(), None


def local_downloader(source_uri: str) -> Path:
    """Retrieves a local file on the assumption that the source_uri contains an absolute filepath."""
    source_uri_path = urlsplit(source_uri).path
    # Strip leading slash on Windows
    if source_uri_path[2] == ":":
        source_uri_path = source_uri_path[1:]
    path = Path(source_uri_path)
    assert path.exists(), f"Path {path} for URI {source_uri} doesn't exist"
    assert path.is_file(), f"Path {path} for URI {source_uri} is not a file"
    return path
