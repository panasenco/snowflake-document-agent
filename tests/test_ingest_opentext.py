"""Tests for OpenText document ingestion functionality."""

import pytest
from datetime import datetime, timezone

from snowflake_document_agent.ingest_opentext import OpenTextDocumentInfo


def test_opentext_document_info_creation():
    """Test basic creation of OpenTextDocumentInfo instance."""
    modify_date = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    doc_info = OpenTextDocumentInfo(
        modified_at_utc=modify_date, opentext_id=12345, opentext_name="test_document.pdf", opentext_api_client=None
    )

    assert doc_info.opentext_id == 12345
    assert doc_info.opentext_name == "test_document.pdf"
    assert doc_info.modified_at_utc == modify_date


def test_opentext_document_info_requires_opentext_fields():
    """Test that OpenText fields are mandatory."""
    modify_date = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    # Should fail without opentext_id
    with pytest.raises(TypeError):
        OpenTextDocumentInfo(modified_at_utc=modify_date, opentext_name="test_document.pdf", opentext_api_client=None)
