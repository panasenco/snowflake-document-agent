from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from snowflake_document_agent.common import word_doc_to_html, excel_to_html, process_document, DocumentInfo


def test_word_doc_to_html_basic():
    """
    Test that word_doc_to_html converts a DOCX file to HTML format.
    """
    # Use the fixture file
    fixture_docx = Path(__file__).parent.parent / "fixtures" / "mammoth-tables.docx"
    assert fixture_docx.exists(), f"Fixture file not found: {fixture_docx}"

    # Execute - Convert DOCX to HTML
    html_content = word_doc_to_html(fixture_docx)

    # Verify - Should return HTML content
    assert html_content is not None, "word_doc_to_html returned None"
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


@patch("snowflake_document_agent.common.snowflake.connector.connect")
def test_process_document_error(mock_connect, tmp_path):
    """
    Test that process_document returns exception objects instead of raising them.
    """
    # Use a nonexistent file path to trigger FileNotFoundError in the txt processing path
    nonexistent_file = tmp_path / "does_not_exist.txt"
    # Explicitly don't create the file - this will cause FileNotFoundError when process_document tries to read it

    result = process_document(
        connection=mock_connect,
        source_uri="test://error/does_not_exist.txt",
        source_info=DocumentInfo(
            modified_at_utc=datetime.now(timezone.utc),
            local_path=nonexistent_file,  # This file doesn't exist
            metadata='{"type": "error_test"}',
        ),
        prefix="test://error/",
        config={"test": "config"},
        insert=True,
    )

    # Verify that an exception object is returned instead of raised
    assert isinstance(result, Exception), f"Expected exception object, got {type(result)}: {result}"
    assert isinstance(result, FileNotFoundError), f"Expected FileNotFoundError, got {type(result)}"
