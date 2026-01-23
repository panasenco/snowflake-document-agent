import pytest
from pathlib import Path
from snowflake_document_agent.ingest_local import get_local_documents
from snowflake_document_agent.common import DocumentInfo


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

    # Verify
    assert len(docs) == 2
    assert "local://file1.txt" in docs
    assert "local://subdir/file2.txt" in docs

    assert isinstance(docs["local://file1.txt"], DocumentInfo)
    assert docs["local://file1.txt"].local_path == p1
    assert docs["local://subdir/file2.txt"].local_path == p2


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

    # Verify
    assert len(docs) == 1
    assert "local://visible.txt" in docs
    assert "local://.hidden.txt" not in docs
    assert "local://.hidden_dir/file_in_hidden.txt" not in docs


def test_get_local_documents_non_existent_root():
    with pytest.raises(RuntimeError, match="does not exist"):
        get_local_documents(Path("/non/existent/path"), "local")
