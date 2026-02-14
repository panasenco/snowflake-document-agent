from snowflake_document_agent.common import doc_to_html


def test_doc_to_html_basic():
    """
    Test that doc_to_html converts a DOCX file to HTML format.
    """
    from pathlib import Path

    # Use the fixture file
    fixture_docx = Path(__file__).parent.parent / "fixtures" / "mammoth-tables.docx"
    assert fixture_docx.exists(), f"Fixture file not found: {fixture_docx}"

    # Execute - Convert DOCX to HTML
    html_content = doc_to_html(fixture_docx)

    # Verify - Should return HTML content
    assert html_content is not None, "doc_to_html returned None"
    assert isinstance(html_content, str), f"Expected string, got {type(html_content)}"
    assert len(html_content.strip()) > 0, "HTML content is empty"

    # Check for HTML structure (basic validation) - mammoth produces HTML fragments, not full documents
    html_lower = html_content.lower()
    assert "<p>" in html_lower, f"Content doesn't appear to be HTML: {html_content[:100]}..."

    # Since the fixture is named "mammoth-tables", check for table elements
    assert "<table>" in html_lower, "Expected table elements in mammoth-tables fixture"

    print(f"Successfully converted DOCX to {len(html_content)} characters of HTML")
