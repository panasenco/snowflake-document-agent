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


def test_local_path_uses_correct_file_extension():
    """Test that local_path uses the file extension from opentext_name."""
    modify_date = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    mock_client = Mock()

    # Mock HTTP client response
    mock_response = Mock()
    mock_response.content = b"fake document content"
    mock_client.call.return_value = mock_response

    doc_info = OpenTextDocumentInfo(
        modified_at_utc=modify_date,
        opentext_id=12345,
        opentext_name="test_document.docx",  # Note: .docx, not .pdf
        opentext_api_client=mock_client,
    )

    # Access local_path should create temp file with correct extension
    result = doc_info.local_path

    # Should return a path with the correct extension
    assert isinstance(result, Path)
    assert result.suffix == ".docx"
    assert result.exists()


def test_local_path_uses_opentext_id_prefix():
    """Test that local_path creates temp file names starting with opentext_id."""
    modify_date = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    mock_client = Mock()

    # Mock HTTP client response
    mock_response = Mock()
    mock_response.content = b"fake document content"
    mock_client.call.return_value = mock_response

    doc_info = OpenTextDocumentInfo(
        modified_at_utc=modify_date,
        opentext_id=98765,
        opentext_name="important_doc.xlsx",
        opentext_api_client=mock_client,
    )

    # Access local_path should create temp file with opentext_id prefix
    result = doc_info.local_path

    # Should return a path with filename starting with opentext_id
    assert isinstance(result, Path)
    assert result.name.startswith("98765_")
    assert result.suffix == ".xlsx"
    assert result.exists()


def test_opentext_client_creation():
    """Test basic creation of OpenTextClient instance."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import OpenTextClient

    # Mock the auth request since it happens in __init__
    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        client = OpenTextClient(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Should store auth details
        assert client.client_id == "test_client"
        assert client.client_secret == "test_secret"
        assert client.api_prefix == "https://api.example.com"


def test_opentext_client_authenticates_at_init():
    """Test that client authenticates once during initialization."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import OpenTextClient

    # Mock the auth request
    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        client = OpenTextClient(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Should have made auth request during init
        mock_post.assert_called_once_with(
            "https://api.example.com/opentext/cloud/v1/auth",
            data={"grant_type": "client_credentials", "client_id": "test_client", "client_secret": "test_secret"},
        )

        # Should store the headers with token
        assert client.headers["authorization"] == "Bearer fake_token"
        assert "app_client" in client.headers
        assert client.headers["app_client"] == "app_secret"


def test_opentext_client_call_method():
    """Test that call method uses stored token for API requests."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import OpenTextClient

    # Mock the requests calls
    with (
        patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post,
        patch("snowflake_document_agent.ingest_opentext.requests.get") as mock_get,
    ):
        # Mock auth response for init
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        client = OpenTextClient(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Reset mock to check only the call() behavior
        mock_post.reset_mock()

        # Mock API response
        mock_api_response = Mock()
        mock_api_response.content = b"test content"
        mock_get.return_value = mock_api_response

        # Make API call
        result = client.call("GET", "opentext/cloud/v1/nodes/12345/content")

        # Should NOT have made another auth request
        mock_post.assert_not_called()

        # Should have made the API request with Bearer token
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args[0][0] == "https://api.example.com/opentext/cloud/v1/nodes/12345/content"

        # Check that headers include the token
        headers = call_args[1]["headers"]
        assert headers["authorization"] == "Bearer fake_token"
        assert headers["app_client"] == "app_secret"

        # Should return the response
        assert result == mock_api_response
