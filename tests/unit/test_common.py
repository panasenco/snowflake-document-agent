from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from snowdoc.common import (
    clean_html,
    doc_to_text,
    docx_to_html,
    excel_to_html,
    format_dict_changes,
    process_changed_documents,
)


def test_docx_to_html_basic():
    """
    Test that docx_to_html converts a DOCX file to HTML format.
    """
    # Use the fixture file
    fixture_docx = Path(__file__).parent.parent / "fixtures" / "mammoth-tables.docx"
    assert fixture_docx.exists(), f"Fixture file not found: {fixture_docx}"

    # Execute - Convert DOCX to HTML
    html_content = docx_to_html(fixture_docx)

    # Verify - Should return HTML content
    assert html_content is not None, "docx_to_html returned None"
    assert isinstance(html_content, str), f"Expected string, got {type(html_content)}"
    assert len(html_content.strip()) > 0, "HTML content is empty"

    # Check for HTML structure (basic validation) - mammoth produces HTML fragments, not full documents
    html_lower = html_content.lower()
    assert "<p>" in html_lower, f"Content doesn't appear to be HTML: {html_content[:100]}..."

    # Since the fixture is named "mammoth-tables", check for table elements
    assert "<table>" in html_lower, "Expected table elements in mammoth-tables fixture"
    assert "bottom right" in html_lower, "Expected 'Bottom right' content in mammoth-tables fixture"

    print(f"Successfully converted DOCX to {len(html_content)} characters of HTML")


def test_excel_to_html_basic():
    """
    Test that excel_to_html converts an XLSX file to HTML format.
    """
    # Use the fixture file
    fixture_xlsx = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"
    assert fixture_xlsx.exists(), f"Fixture file not found: {fixture_xlsx}"

    # Execute - Convert XLSX to HTML
    html_content = excel_to_html(fixture_xlsx)

    # Verify - Should return HTML content
    assert html_content is not None, "excel_to_html returned None"
    assert isinstance(html_content, str), f"Expected string, got {type(html_content)}"
    assert len(html_content.strip()) > 0, "HTML content is empty"

    # Check for HTML structure (basic validation)
    html_lower = html_content.lower()
    assert "<table" in html_lower, f"Content doesn't appear to be HTML with tables: {html_content[:100]}..."

    # Since the fixture is "multi-worksheet", expect content from both sheets
    assert "reset table" in html_lower, "Expected 'Reset Table' content from first sheet"
    assert "php-gold" in html_lower, "Expected 'PHP-GOLD' content from second sheet"

    # Check for multiple table structures (should have at least 2 for multi-worksheet)
    table_count = html_lower.count("<table")
    assert table_count >= 2, f"Expected at least 2 tables for multi-worksheet, got {table_count}"

    print(f"Successfully converted XLSX to {len(html_content)} characters of HTML with {table_count} tables")


def test_doc_to_text_basic():
    """
    Test that doc_to_text converts a .doc file to text format using antiword.
    """
    # Use the fixture file
    fixture_doc = Path(__file__).parent.parent / "fixtures" / "word97.doc"
    assert fixture_doc.exists(), f"Fixture file not found: {fixture_doc}"

    # Execute - Convert DOC to text
    text_content = doc_to_text(fixture_doc)

    # Verify - Should return text content
    assert text_content is not None, "doc_to_text returned None"
    assert isinstance(text_content, str), f"Expected string, got {type(text_content)}"
    assert len(text_content.strip()) > 0, "Text content is empty"

    # Check for expected content from the word97.doc fixture
    text_lower = text_content.lower()
    assert "heading!" in text_lower, "Expected 'Heading!' content in word97.doc fixture"
    assert "body" in text_lower, "Expected 'Body' content in word97.doc fixture"

    # Check for table content that should be extracted
    assert "table!" in text_lower, "Expected 'Table!' content from table in word97.doc fixture"
    assert "tabular" in text_lower, "Expected 'Tabular' content from table in word97.doc fixture"
    assert "data" in text_lower, "Expected 'data' content from table in word97.doc fixture"

    print(f"Successfully converted DOC to {len(text_content)} characters of text")


@pytest.mark.parametrize(
    "fixture_name",
    [
        "fluff.html",
        "fluff-utf16.html",
    ],
    ids=["utf8", "utf16"],
)
def test_html_to_text_basic(fixture_name):
    """
    Test that clean_html cleans HTML by removing MS Word fluff while preserving structure.
    Strips out styling, metadata, and other non-content elements but keeps table structure.
    Tests both UTF-8 and UTF-16 encoded files.
    """
    # Use the fixture file with lots of Microsoft Word HTML fluff
    fixture_html = Path(__file__).parent.parent / "fixtures" / fixture_name
    assert fixture_html.exists(), f"Fixture file not found: {fixture_html}"

    # Execute - Clean HTML by removing fluff
    cleaned_html = clean_html(fixture_html)

    # Verify - Should return cleaned HTML content
    assert cleaned_html is not None, "clean_html returned None"
    assert isinstance(cleaned_html, str), f"Expected string, got {type(cleaned_html)}"
    assert len(cleaned_html.strip()) > 0, "Cleaned content is empty"

    # Check that it's still HTML with structure preserved
    html_lower = cleaned_html.lower()
    assert "<table" in html_lower, "Expected table elements to be preserved"
    assert "<tr" in html_lower, "Expected table row elements to be preserved"
    assert "<td" in html_lower, "Expected table cell elements to be preserved"
    assert "<p>" in html_lower or "<p " in html_lower, "Expected paragraph elements to be preserved"

    # Check that meaningful content is preserved
    assert "service operations overview" in html_lower or "overview" in html_lower, (
        "Expected document title in cleaned content"
    )
    assert "table of contents" in html_lower, "Expected 'Table of Contents' in cleaned content"
    assert "important" in html_lower, "Expected important content sections"

    # Check that Microsoft Word fluff is removed
    assert "mso-" not in cleaned_html, "MS Office styling (mso-) should be stripped"
    assert "xmlns:" not in cleaned_html, "XML namespaces should be stripped"
    assert "<!--[if" not in cleaned_html, "Conditional comments should be stripped"
    assert "class=" not in cleaned_html, "CSS classes should be stripped"
    assert "style=" not in cleaned_html, "Inline styles should be stripped"

    # Check that empty tags are removed
    assert "<b></b>" not in cleaned_html, "Empty <b> tags should be removed"
    assert "<p></p>" not in cleaned_html, "Empty <p> tags should be removed"

    # Check that multiple spaces are collapsed
    assert "  " not in cleaned_html or cleaned_html.count("  ") < 5, "Multiple consecutive spaces should be collapsed"

    # The cleaned content should be significantly shorter than the original
    original_size = fixture_html.stat().st_size
    cleaned_size = len(cleaned_html)
    assert cleaned_size < original_size * 0.5, (
        f"Expected cleaned content to be much smaller (got {cleaned_size} bytes from {original_size} bytes)"
    )

    print(f"Successfully cleaned HTML to {len(cleaned_html)} characters from {original_size} bytes ({fixture_name})")


def test_process_changed_documents_handles_none_sentinel():
    """
    Test that process_changed_documents properly handles (None, None) sentinel values from generators.
    When a generator yields (None, None) to signal an error (instead of raising an exception),
    the failed counter should be incremented.
    """

    def error_yielding_generator():
        """Generator that yields some valid documents and then (None, None, None) to signal errors"""
        # First yield a successful document
        yield ("test://unit/success.txt", "Success Document", None)
        # Then yield (None, None, None) to signal an error occurred
        yield (None, None, None)
        # Then yield another successful document
        yield ("test://unit/success2.txt", "Success Document 2", None)
        # And another error sentinel
        yield (None, None, None)

    def mock_downloader(source_uri: str) -> Path:
        """Mock downloader that returns fake paths"""
        return Path("/fake/path") / source_uri.split("/")[-1]

    # Mock all the Snowflake operations
    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

    # Mock the supporting functions that interact with Snowflake
    with (
        patch("snowdoc.common.configure_connection", return_value=mock_connection),
        patch("snowdoc.common.clear_stage"),
        patch("snowdoc.common.process_document", return_value=("test://unit/success.txt", "new", "", "")),
        patch("snowdoc.common.refresh_search_services"),
    ):
        # Execute - Process the generator that includes error sentinels
        changes = process_changed_documents(
            error_yielding_generator(),
            connection=mock_connection,
            downloader=mock_downloader,
            prefix="test://unit/",
            config={
                "agent_name": "test",
                "chunk_size": 1000,
                "chunk_overlap": 100,
            },
            max_workers=1,
        )

    # Verify - Should have processed 2 successful docs; sentinels should not appear in changes
    assert len(changes) == 2, f"Expected 2 changes, got {len(changes)}"

    print("✅ Unit test passed - (None, None, None) yields should not appear in changes!")


def test_process_changed_documents_passes_metadata_to_process_document():
    """
    Test that process_changed_documents passes document_metadata_json from the source 3-tuple
    through to process_document for each document.
    """

    def source_generator():
        yield ("test://unit/with_meta.txt", "With Metadata", {"description": "A described document"})
        yield ("test://unit/no_meta.txt", "No Metadata", None)

    def mock_downloader(source_uri: str) -> Path:
        return Path("/fake/path") / source_uri.split("/")[-1]

    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

    with (
        patch("snowdoc.common.configure_connection", return_value=mock_connection),
        patch("snowdoc.common.clear_stage"),
        patch("snowdoc.common.process_document", return_value=("test://unit/doc", "new", "", "")) as mock_process_doc,
        patch("snowdoc.common.refresh_search_services"),
    ):
        process_changed_documents(
            source_generator(),
            connection=mock_connection,
            downloader=mock_downloader,
            prefix="test://unit/",
            config={
                "agent_name": "test",
                "chunk_size": 1000,
                "chunk_overlap": 100,
            },
            max_workers=1,
        )

    # Verify process_document was called twice
    assert mock_process_doc.call_count == 2

    # Verify the first call included metadata dict
    first_call_args = mock_process_doc.call_args_list[0]
    assert first_call_args[0][2] == "With Metadata"  # display_name
    # document_metadata_json should be passed as a JSON string
    assert '{"description": "A described document"}' in str(first_call_args) or "description" in str(first_call_args)

    # Verify the second call had None metadata
    second_call_args = mock_process_doc.call_args_list[1]
    assert second_call_args[0][2] == "No Metadata"  # display_name


# ============================================================
# Document changes return type tests (TDD red phase)
# ============================================================
# These tests define the expected interface for the planned change where
# process_changed_documents returns a list of (source_uri_base, state, core_changes, metadata_changes)
# tuples instead of a (processed, skipped, failed) count tuple.
# A single format_dict_changes helper diffs two dicts into a semicolon-separated string.


def _noop_downloader(uri):
    return Path("/fake") / uri.split("/")[-1]


def _default_config():
    return {"agent_name": "test", "chunk_size": 1000, "chunk_overlap": 100}


def test_format_dict_changes():
    """format_dict_changes diffs two dicts into a semicolon-separated string of quoted changes."""
    # Single changed key
    assert format_dict_changes({"v": "3"}, {"v": "4"}) == 'v: "3" -> "4"'

    # Multiple changes are semicolon-separated; unchanged keys excluded
    result = format_dict_changes({"v": "3", "ext": ".pdf", "foo": "x"}, {"v": "4", "ext": ".pdf", "foo": "y"})
    assert 'v: "3" -> "4"' in result
    assert 'foo: "x" -> "y"' in result
    assert "; " in result
    assert "ext" not in result

    # No changes → empty string
    assert format_dict_changes({"v": "3"}, {"v": "3"}) == ""
    assert format_dict_changes({}, {}) == ""

    # Added and removed keys are reported
    added = format_dict_changes({}, {"v": "1"})
    assert "v" in added and '"1"' in added
    removed = format_dict_changes({"v": "1"}, {})
    assert "v" in removed and '"1"' in removed

    # None treated as empty dict
    assert format_dict_changes(None, None) == ""
    result = format_dict_changes(None, {"description": "new"})
    assert "description" in result and '"new"' in result


def test_pcd_returns_change_tuples():
    """process_changed_documents returns a list of (source_uri_base, state, core_changes, metadata_changes) tuples.
    New, updated, skipped, sentinel, and duplicate sources are handled correctly."""

    def source_generator():
        yield (None, None, None)  # sentinel — should not appear
        yield ("test://new.txt?v=1", "New Doc", None)  # new
        yield ("test://updated.pdf?v=2", "Updated", {"description": "new"})  # updated
        yield ("test://skip.txt?v=1", "Skipped", None)  # unchanged → skipped
        yield ("test://new.txt?v=1", "Duplicate", None)  # duplicate → skipped

    # process_document returns a change tuple for new/updated, None for skipped
    side_effects = [
        ("test://new.txt", "new", "", ""),
        ("test://updated.pdf", "processed", 'v: "1" -> "2"', 'description: "old" -> "new"'),
        None,  # skipped
    ]

    with (
        patch("snowdoc.common.configure_connection", return_value=MagicMock()),
        patch("snowdoc.common.clear_stage"),
        patch("snowdoc.common.process_document", side_effect=side_effects),
        patch("snowdoc.common.refresh_search_services"),
    ):
        changes = process_changed_documents(
            source_generator(),
            connection=MagicMock(),
            downloader=_noop_downloader,
            prefix="test://",
            config=_default_config(),
            max_workers=1,
        )

    assert isinstance(changes, list)
    # Skipped, sentinel, and duplicate should not appear
    assert len(changes) == 2
    bases = {c[0] for c in changes}
    assert bases == {"test://new.txt", "test://updated.pdf"}
    # Check the updated entry carries diffs
    updated = [c for c in changes if c[1] == "processed"][0]
    assert 'v: "1" -> "2"' in updated[2]
    assert 'description: "old" -> "new"' in updated[3]


def test_pcd_deleted_and_broken_iterator():
    """Deleted documents appear with state='deleted'; broken iterator prevents deletions (safety)."""

    # --- Part 1: delete_missing=True with complete iteration ---
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    # Snowflake has one doc not in sources
    mock_cursor.fetchall.return_value = [("test://old.txt?v=1",)]

    with (
        patch("snowdoc.common.configure_connection", return_value=mock_conn),
        patch("snowdoc.common.clear_stage"),
        patch("snowdoc.common.delete_rows"),
        patch("snowdoc.common.refresh_search_services"),
    ):
        changes = process_changed_documents(
            [],  # empty sources — everything in Snowflake is "missing"
            connection=mock_conn,
            downloader=_noop_downloader,
            prefix="test://",
            config=_default_config(),
            max_workers=1,
            delete_missing=True,
        )

    assert len(changes) == 1
    assert changes[0] == ("test://old.txt", "deleted", "", "")

    # --- Part 2: broken iterator should suppress deletions ---
    class BrokenIterator:
        def __init__(self):
            self.done = False

        def __iter__(self):
            return self

        def __next__(self):
            if not self.done:
                self.done = True
                return ("test://ok.txt?v=1", "OK", None)
            raise RuntimeError("boom")

    mock_conn2 = MagicMock()
    mock_cursor2 = MagicMock()
    mock_conn2.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor2)
    mock_conn2.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_cursor2.fetchall.return_value = [("test://ok.txt?v=1",), ("test://other.txt?v=1",)]

    with (
        patch("snowdoc.common.configure_connection", return_value=mock_conn2),
        patch("snowdoc.common.clear_stage"),
        patch("snowdoc.common.process_document", return_value=("test://ok.txt", "new", "", "")),
        patch("snowdoc.common.delete_rows"),
        patch("snowdoc.common.refresh_search_services"),
    ):
        changes = process_changed_documents(
            BrokenIterator(),
            connection=mock_conn2,
            downloader=_noop_downloader,
            prefix="test://",
            config=_default_config(),
            max_workers=1,
            delete_missing=True,
        )

    deleted = [c for c in changes if c[1] == "deleted"]
    assert len(deleted) == 0
