import pytest
from pathlib import Path
from snowflake_document_agent.ingest_local import get_local_documents, local_downloader


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

    # Verify - should return dict[str, str] (URI -> display_name)
    assert len(docs) == 2

    # URIs should now contain absolute paths with query parameters
    uris = list(docs.keys())
    assert len(uris) == 2

    # Find URIs that correspond to our files
    file1_uri = next((uri for uri in uris if "file1.txt" in uri), None)
    file2_uri = next((uri for uri in uris if "subdir/file2.txt" in uri), None)

    assert file1_uri is not None
    assert file2_uri is not None
    assert file1_uri.startswith("file://local/")
    assert file2_uri.startswith("file://local/")
    assert "mtime=" in file1_uri  # Should have query parameter
    assert "mtime=" in file2_uri

    # Values should be display names (relative paths)
    assert docs[file1_uri] == "file1.txt"
    assert docs[file2_uri] == "subdir/file2.txt"


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

    # Verify - should return dict[str, str] (URI -> display_name)
    assert len(docs) == 1

    # Get the single URI and verify it corresponds to visible.txt
    visible_uri = next(iter(docs.keys()))
    assert "visible.txt" in visible_uri
    assert visible_uri.startswith("file://local/")
    assert "mtime=" in visible_uri

    # Verify no hidden files are included by checking all URIs
    for uri in docs.keys():
        assert ".hidden" not in uri
        assert "file_in_hidden" not in uri

    # Verify the display name is correct
    assert docs[visible_uri] == "visible.txt"


def test_get_local_documents_non_existent_root():
    with pytest.raises(RuntimeError, match="does not exist"):
        get_local_documents(Path("/non/existent/path"), "local")


def test_local_downloader_basic(tmp_path):
    """Test that local_downloader can download files from URIs with absolute paths."""
    # Setup
    d = tmp_path / "documents"
    d.mkdir()
    p1 = d / "file1.txt"
    p1.write_text("content1")

    sub = d / "subdir"
    sub.mkdir()
    p2 = sub / "file2.txt"
    p2.write_text("content2")

    # Create URIs in the new format (file://netloc/absolute/path)
    uri1 = p1.as_uri().replace("file://", "file://local")
    uri2 = p2.as_uri().replace("file://", "file://local")

    # Test downloading existing files
    result1 = local_downloader(uri1)
    assert result1 == p1
    assert result1.exists()

    result2 = local_downloader(uri2)
    assert result2 == p2
    assert result2.exists()


def test_local_downloader_nonexistent_file(tmp_path):
    """Test that local_downloader errors when file doesn't exist."""
    d = tmp_path / "documents"
    d.mkdir()

    # Create a URI for a non-existent file
    nonexistent_path = d / "nonexistent.txt"
    uri = nonexistent_path.as_uri().replace("file://", "file://local")

    # Should error for nonexistent file
    with pytest.raises(AssertionError, match="doesn't exist"):
        local_downloader(uri)


def test_local_downloader_directory_not_file(tmp_path):
    """Test that local_downloader errors when URI points to a directory."""
    d = tmp_path / "documents"
    d.mkdir()

    # Create a URI pointing to a directory
    uri = d.as_uri().replace("file://", "file://local")

    # Should error because path is not a file
    with pytest.raises(AssertionError, match="is not a file"):
        local_downloader(uri)


def test_local_downloader_with_query_parameters(tmp_path):
    """Test that local_downloader ignores query parameters and works with realistic URIs."""
    # Setup
    d = tmp_path / "documents"
    d.mkdir()
    p1 = d / "file1.txt"
    p1.write_text("content1")

    # Create URI with query parameters (like what get_local_documents produces)
    base_uri = p1.as_uri().replace("file://", "file://local")
    uri_with_params = f"{base_uri}?mtime=1234567890.123"

    # Test downloading - should ignore query parameters
    result = local_downloader(uri_with_params)
    assert result == p1
    assert result.exists()
    assert result.read_text() == "content1"


def test_integration_get_documents_and_downloader(tmp_path):
    """Test that get_local_documents and local_downloader work together."""
    # Setup
    d = tmp_path / "documents"
    d.mkdir()
    p1 = d / "file1.txt"
    p1.write_text("content1")

    sub = d / "subdir"
    sub.mkdir()
    p2 = sub / "file2.txt"
    p2.write_text("content2")

    # Get documents
    docs = get_local_documents(d, "local")
    assert len(docs) == 2

    # Test that each URI from get_local_documents can be downloaded
    for source_uri, display_name in docs.items():
        downloaded_path = local_downloader(source_uri)
        assert downloaded_path.exists()
        assert downloaded_path.is_file()

        # Verify the display name matches the relative path
        expected_relative_path = downloaded_path.relative_to(d).as_posix()
        assert display_name == expected_relative_path
