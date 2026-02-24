from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from snowflake_document_agent.common import (
    docx_to_html,
    excel_to_html,
    doc_to_text,
    html_to_text,
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
    Test that html_to_text extracts meaningful content from HTML with MS Word fluff.
    Uses inscriptis to strip out styling, metadata, and other non-content elements.
    Tests both UTF-8 and UTF-16 encoded files.
    """
    # Use the fixture file with lots of Microsoft Word HTML fluff
    fixture_html = Path(__file__).parent.parent / "fixtures" / fixture_name
    assert fixture_html.exists(), f"Fixture file not found: {fixture_html}"

    # Execute - Extract clean content from fluff HTML
    clean_content = html_to_text(fixture_html)

    # Verify - Should return cleaned text content
    assert clean_content is not None, "html_to_text returned None"
    assert isinstance(clean_content, str), f"Expected string, got {type(clean_content)}"
    assert len(clean_content.strip()) > 0, "Extracted content is empty"

    # Check that meaningful content is preserved
    content_lower = clean_content.lower()
    assert "service operations overview" in content_lower or "overview" in content_lower, (
        "Expected document title in extracted content"
    )
    assert "table of contents" in content_lower, "Expected 'Table of Contents' in extracted content"
    assert "account setup" in content_lower or "appointments" in content_lower, (
        "Expected section headers in extracted content"
    )

    # Check that Microsoft Word fluff is removed
    assert "mso-" not in clean_content, "MS Office styling (mso-) should be stripped"
    assert "xmlns:" not in clean_content, "XML namespaces should be stripped"
    assert "<!--[if" not in clean_content, "Conditional comments should be stripped"
    assert "WordSection" not in clean_content, "Word-specific classes should be stripped"

    # The cleaned content should be significantly shorter than the original
    original_size = fixture_html.stat().st_size
    cleaned_size = len(clean_content)
    assert cleaned_size < original_size * 0.5, (
        f"Expected cleaned content to be much smaller (got {cleaned_size} bytes from {original_size} bytes)"
    )

    print(f"Successfully extracted {len(clean_content)} characters of clean content from HTML fluff ({fixture_name})")


def test_process_changed_documents_handles_none_sentinel():
    """
    Test that process_changed_documents properly handles (None, None) sentinel values from generators.
    When a generator yields (None, None) to signal an error (instead of raising an exception),
    the failed counter should be incremented.
    """

    def error_yielding_generator():
        """Generator that yields some valid documents and then (None, None) to signal errors"""
        # First yield a successful document
        yield ("test://unit/success.txt", "Success Document")
        # Then yield (None, None) to signal an error occurred
        yield (None, None)
        # Then yield another successful document
        yield ("test://unit/success2.txt", "Success Document 2")
        # And another error sentinel
        yield (None, None)

    def mock_downloader(source_uri: str) -> Path:
        """Mock downloader that returns fake paths"""
        return Path("/fake/path") / source_uri.split("/")[-1]

    # Mock all the Snowflake operations
    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

    # Mock the supporting functions that interact with Snowflake
    with (
        patch("snowflake_document_agent.common.configure_connection", return_value=mock_connection),
        patch("snowflake_document_agent.common.clear_stage"),
        patch("snowflake_document_agent.common.process_document", return_value=(1, 0, 0)),
        patch("snowflake_document_agent.common.refresh_search_services"),
    ):
        # Execute - Process the generator that includes error sentinels
        processed, skipped, failed = process_changed_documents(
            error_yielding_generator(),
            connection=mock_connection,
            downloader=mock_downloader,
            prefix="test://unit/",
            config={
                "agent_name": "test",
                "metadata_model": "test",
                "metadata_prompt": "test",
                "metadata_first_chars": 100,
                "chunk_size": 1000,
                "chunk_overlap": 100,
            },
            max_workers=1,
        )

    # Verify - Should have processed 2 successful docs and failed on 2 error sentinels
    assert processed == 2, f"Expected 2 processed documents, got {processed}"
    assert skipped == 0, f"Expected 0 skipped documents, got {skipped}"
    assert failed == 2, f"Expected 2 failed (from None sentinels), got {failed}"

    print("✅ Unit test passed - (None, None) yields should increment failed counter!")
