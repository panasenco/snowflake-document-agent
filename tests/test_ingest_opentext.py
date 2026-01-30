"""Tests for OpenText document ingestion functionality."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

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


def test_local_path_property_returns_path():
    """Test that local_path property returns a Path object."""
    modify_date = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    mock_client = Mock()

    # Mock response with content attribute
    mock_response = Mock()
    mock_response.content = b"test content"
    mock_client.call.return_value = mock_response

    doc_info = OpenTextDocumentInfo(
        modified_at_utc=modify_date,
        opentext_id=12345,
        opentext_name="test_document.pdf",
        opentext_api_client=mock_client,
    )

    # This should return a Path object
    result = doc_info.local_path
    assert isinstance(result, Path)


def test_local_path_downloads_from_opentext():
    """Test that local_path downloads document from OpenText API."""
    modify_date = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    mock_client = Mock()

    # Mock HTTP client response with content attribute (like requests.Response)
    mock_response = Mock()
    mock_response.content = b"fake PDF content"
    mock_client.call.return_value = mock_response

    doc_info = OpenTextDocumentInfo(
        modified_at_utc=modify_date,
        opentext_id=12345,
        opentext_name="test_document.pdf",
        opentext_api_client=mock_client,
    )

    # Access local_path should trigger download
    result = doc_info.local_path

    # Should have called the real OpenText content API
    mock_client.call.assert_called_once_with("GET", "opentext/cloud/v1/nodes/12345/content")

    # Should return a path to a file that exists
    assert isinstance(result, Path)
    assert result.exists()

    # File should contain the downloaded content
    assert result.read_bytes() == b"fake PDF content"
