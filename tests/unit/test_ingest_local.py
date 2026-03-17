import pytest
from pathlib import Path
from snowdoc.ingest_local import get_local_documents, local_downloader


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
    docs_gen = get_local_documents(d, "local")

    # Convert to list for verification - should return Iterator[tuple[str, str, None]] (URI, display_name, metadata)
    docs_list = list(docs_gen)
    assert len(docs_list) == 2

    # Find tuple that corresponds to our files
    file1_tuple = next(
        ((uri, display_name, metadata) for uri, display_name, metadata in docs_list if "file1.txt" in uri), None
    )
    file2_tuple = next(
        ((uri, display_name, metadata) for uri, display_name, metadata in docs_list if "subdir/file2.txt" in uri), None
    )

    assert file1_tuple is not None
    assert file2_tuple is not None

    file1_uri, file1_display_name, file1_metadata = file1_tuple
    file2_uri, file2_display_name, file2_metadata = file2_tuple

    assert file1_uri.startswith("file://local/")
    assert file2_uri.startswith("file://local/")
    assert "m=" in file1_uri  # Should have query parameter
    assert "m=" in file2_uri

    # Display names should be relative paths
    assert file1_display_name == "file1.txt"
    assert file2_display_name == "subdir/file2.txt"

    # Local documents should have None metadata
    assert file1_metadata is None
    assert file2_metadata is None


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
    docs_gen = get_local_documents(d, "local")

    # Convert to list for verification - should return Iterator[tuple[str, str, None]] (URI, display_name, metadata)
    docs_list = list(docs_gen)
    assert len(docs_list) == 1

    # Get the single tuple and verify it corresponds to visible.txt
    visible_uri, visible_display_name, visible_metadata = docs_list[0]
    assert "visible.txt" in visible_uri
    assert visible_uri.startswith("file://local/")
    assert "m=" in visible_uri

    # Verify no hidden files are included by checking all URIs
    for uri, display_name, metadata in docs_list:
        assert ".hidden" not in uri
        assert "file_in_hidden" not in uri

    # Verify the display name is correct
    assert visible_display_name == "visible.txt"

    # Local documents should have None metadata
    assert visible_metadata is None


def test_get_local_documents_non_existent_root():
    with pytest.raises(RuntimeError, match="does not exist"):
        # Generator functions don't execute until consumed, so we need to consume it
        list(get_local_documents(Path("/non/existent/path"), "local"))


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
    uri_with_params = f"{base_uri}?m=1234567890"

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
    docs_gen = get_local_documents(d, "local")
    docs_list = list(docs_gen)
    assert len(docs_list) == 2

    # Test that each URI from get_local_documents can be downloaded
    for source_uri, display_name, metadata in docs_list:
        downloaded_path = local_downloader(source_uri)
        assert downloaded_path.exists()
        assert downloaded_path.is_file()

        # Verify the display name matches the relative path
        expected_relative_path = downloaded_path.relative_to(d).as_posix()
        assert display_name == expected_relative_path

        # Local documents should have None metadata
        assert metadata is None
