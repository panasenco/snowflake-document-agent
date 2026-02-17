import pytest
from datetime import datetime, timezone
from pathlib import Path
from snowflake_document_agent.ingest_local import get_local_documents, get_local_downloader


def test_get_local_documents_basic(tmp_path):
    # Setup
    d = tmp_path / "documents"
    d.mkdir()
    p1 = d / "file1.txt"
    p1.write_text("content1")

    sub = d / "subdir"
    sub.mkdir()
    p2 = sub / "file2.txt"
    p2.write_text("content2")

    # Execute
    docs = get_local_documents(d, "local")

    # Verify - should return dict[str, tuple[datetime, str]]
    assert len(docs) == 2
    assert "local://file1.txt" in docs
    assert "local://subdir/file2.txt" in docs

    # Each document should be a tuple: (modified_at_utc, metadata)
    assert isinstance(docs["local://file1.txt"], tuple)
    assert len(docs["local://file1.txt"]) == 2

    modified_time, metadata = docs["local://file1.txt"]
    assert isinstance(modified_time, datetime)
    assert modified_time.tzinfo == timezone.utc
    assert isinstance(metadata, str)


def test_get_local_documents_ignores_hidden(tmp_path):
    # Setup
    d = tmp_path / "documents"
    d.mkdir()

    # Visible file
    (d / "visible.txt").write_text("visible")

    # Hidden file
    (d / ".hidden.txt").write_text("hidden")

    # Hidden directory
    hidden_dir = d / ".hidden_dir"
    hidden_dir.mkdir()
    (hidden_dir / "file_in_hidden.txt").write_text("content")

    # Execute
    docs = get_local_documents(d, "local")

    # Verify - should return dict[str, tuple[datetime, str]]
    assert len(docs) == 1
    assert "local://visible.txt" in docs
    assert "local://.hidden.txt" not in docs
    assert "local://.hidden_dir/file_in_hidden.txt" not in docs

    # Verify the structure of the returned data
    modified_time, metadata = docs["local://visible.txt"]
    assert isinstance(modified_time, datetime)
    assert modified_time.tzinfo == timezone.utc
    assert isinstance(metadata, str)


def test_get_local_documents_non_existent_root():
    with pytest.raises(RuntimeError, match="does not exist"):
        get_local_documents(Path("/non/existent/path"), "local")


def test_get_local_downloader_basic(tmp_path):
    """Test that get_local_downloader returns a working downloader function."""
    # Setup
    d = tmp_path / "documents"
    d.mkdir()
    p1 = d / "file1.txt"
    p1.write_text("content1")

    sub = d / "subdir"
    sub.mkdir()
    p2 = sub / "file2.txt"
    p2.write_text("content2")

    # Execute - Get the downloader function
    downloader = get_local_downloader(d, "local")

    # Verify - The downloader function should be callable
    assert callable(downloader)

    # Test downloading existing files
    result1 = downloader("local://file1.txt")
    assert result1 == p1
    assert result1.exists()

    result2 = downloader("local://subdir/file2.txt")
    assert result2 == p2
    assert result2.exists()


def test_get_local_downloader_invalid_prefix(tmp_path):
    """Test that downloader function errors on invalid URI prefix."""
    d = tmp_path / "documents"
    d.mkdir()
    (d / "file1.txt").write_text("content")

    downloader = get_local_downloader(d, "local")

    # Should error for wrong prefix
    with pytest.raises(AssertionError, match="required prefix"):
        downloader("remote://file1.txt")

    # Should error for no prefix at all
    with pytest.raises(AssertionError, match="required prefix"):
        downloader("file1.txt")


def test_get_local_downloader_nonexistent_file(tmp_path):
    """Test that downloader function errors when file doesn't exist."""
    d = tmp_path / "documents"
    d.mkdir()

    downloader = get_local_downloader(d, "local")

    # Should error for nonexistent file
    with pytest.raises(AssertionError, match="doesn't exist"):
        downloader("local://nonexistent.txt")
