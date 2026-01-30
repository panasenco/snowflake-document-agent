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


def test_get_opentext_documents_returns_document_info_dict():
    """Test that get_opentext_documents returns dict mapping URIs to OpenTextDocumentInfo."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import get_opentext_documents, OpenTextClient, OpenTextDocumentInfo

    # Mock the client
    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
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

        # Mock the client.call method to return node info
        client.call = Mock()
        mock_node_response = Mock()
        mock_node_response.json.return_value = {
            "data": {
                "id": 12345,
                "name": "test_document.pdf",
                "modify_date": "2024-01-15T10:30:00Z",
                "type_name": "Document",
            }
        }
        client.call.return_value = mock_node_response

        # Should return a dictionary mapping URIs to OpenTextDocumentInfo objects
        result = get_opentext_documents(client, opentext_nodes=[12345], prefix="opentext")

        # Should be a dict with one entry
        assert isinstance(result, dict)
        assert len(result) == 1

        # Should have the expected URI key (like a file path after the scheme)
        expected_uri = "opentext://test_document.pdf"
        assert expected_uri in result

        # Should contain an OpenTextDocumentInfo object
        doc_info = result[expected_uri]
        assert isinstance(doc_info, OpenTextDocumentInfo)
        assert doc_info.opentext_id == 12345
        assert doc_info.opentext_name == "test_document.pdf"


def test_get_opentext_documents_handles_folder_with_documents():
    """Test that get_opentext_documents recursively processes folders to find documents."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import get_opentext_documents, OpenTextClient, OpenTextDocumentInfo

    # Mock the client
    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
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

        # Mock the client.call method to return different responses
        def mock_call_side_effect(method, path):
            mock_response = Mock()

            if path == "opentext/cloud/v1/nodes/67890":
                # Folder node info
                mock_response.json.return_value = {
                    "data": {
                        "id": 67890,
                        "name": "Documents Folder",
                        "modify_date": "2024-01-10T09:00:00Z",
                        "type_name": "Folder",
                    }
                }
            elif path == "opentext/cloud/v2/nodes/67890/nodes?limit=1000":
                # Children of the folder - two documents
                mock_response.json.return_value = {
                    "results": [
                        {
                            "data": {
                                "properties": {
                                    "id": 11111,
                                    "name": "doc1.pdf",
                                    "modify_date": "2024-01-11T10:00:00Z",
                                    "type_name": "Document",
                                }
                            }
                        },
                        {
                            "data": {
                                "properties": {
                                    "id": 22222,
                                    "name": "doc2.docx",
                                    "modify_date": "2024-01-12T11:00:00Z",
                                    "type_name": "Document",
                                }
                            }
                        },
                    ]
                }
            elif path == "opentext/cloud/v1/nodes/11111":
                # First document details
                mock_response.json.return_value = {
                    "data": {
                        "id": 11111,
                        "name": "doc1.pdf",
                        "modify_date": "2024-01-11T10:00:00Z",
                        "type_name": "Document",
                    }
                }
            elif path == "opentext/cloud/v1/nodes/22222":
                # Second document details
                mock_response.json.return_value = {
                    "data": {
                        "id": 22222,
                        "name": "doc2.docx",
                        "modify_date": "2024-01-12T11:00:00Z",
                        "type_name": "Document",
                    }
                }

            return mock_response

        client.call = Mock(side_effect=mock_call_side_effect)

        # Should return documents found within the folder
        result = get_opentext_documents(client, opentext_nodes=[67890], prefix="opentext")

        # Should be a dict with two entries (the documents within the folder)
        assert isinstance(result, dict)
        assert len(result) == 2

        # Should have both documents
        assert "opentext://doc1.pdf" in result
        assert "opentext://doc2.docx" in result

        # Verify the document info objects
        doc1 = result["opentext://doc1.pdf"]
        assert isinstance(doc1, OpenTextDocumentInfo)
        assert doc1.opentext_id == 11111
        assert doc1.opentext_name == "doc1.pdf"

        doc2 = result["opentext://doc2.docx"]
        assert isinstance(doc2, OpenTextDocumentInfo)
        assert doc2.opentext_id == 22222
        assert doc2.opentext_name == "doc2.docx"


def test_get_opentext_documents_handles_nested_folders():
    """Test that get_opentext_documents recursively processes nested folders."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import get_opentext_documents, OpenTextClient, OpenTextDocumentInfo

    # Mock the client
    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
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

        # Mock the client.call method for nested folder structure
        def mock_call_side_effect(method, path):
            mock_response = Mock()

            if path == "opentext/cloud/v1/nodes/100":
                # Root folder node info
                mock_response.json.return_value = {
                    "data": {
                        "id": 100,
                        "name": "Root Folder",
                        "modify_date": "2024-01-01T08:00:00Z",
                        "type_name": "Folder",
                    }
                }
            elif path == "opentext/cloud/v2/nodes/100/nodes?limit=1000":
                # Root folder contains a subfolder
                mock_response.json.return_value = {
                    "results": [
                        {
                            "data": {
                                "properties": {
                                    "id": 200,
                                    "name": "Subfolder",
                                    "modify_date": "2024-01-02T09:00:00Z",
                                    "type_name": "Folder",
                                }
                            }
                        }
                    ]
                }
            elif path == "opentext/cloud/v1/nodes/200":
                # Subfolder node info
                mock_response.json.return_value = {
                    "data": {
                        "id": 200,
                        "name": "Subfolder",
                        "modify_date": "2024-01-02T09:00:00Z",
                        "type_name": "Folder",
                    }
                }
            elif path == "opentext/cloud/v2/nodes/200/nodes?limit=1000":
                # Subfolder contains documents
                mock_response.json.return_value = {
                    "results": [
                        {
                            "data": {
                                "properties": {
                                    "id": 300,
                                    "name": "nested_doc1.pdf",
                                    "modify_date": "2024-01-03T10:00:00Z",
                                    "type_name": "Document",
                                }
                            }
                        },
                        {
                            "data": {
                                "properties": {
                                    "id": 400,
                                    "name": "nested_doc2.xlsx",
                                    "modify_date": "2024-01-04T11:00:00Z",
                                    "type_name": "Document",
                                }
                            }
                        },
                    ]
                }
            elif path == "opentext/cloud/v1/nodes/300":
                # First nested document
                mock_response.json.return_value = {
                    "data": {
                        "id": 300,
                        "name": "nested_doc1.pdf",
                        "modify_date": "2024-01-03T10:00:00Z",
                        "type_name": "Document",
                    }
                }
            elif path == "opentext/cloud/v1/nodes/400":
                # Second nested document
                mock_response.json.return_value = {
                    "data": {
                        "id": 400,
                        "name": "nested_doc2.xlsx",
                        "modify_date": "2024-01-04T11:00:00Z",
                        "type_name": "Document",
                    }
                }

            return mock_response

        client.call = Mock(side_effect=mock_call_side_effect)

        # Should return documents found deep within nested folders
        result = get_opentext_documents(client, opentext_nodes=[100], prefix="opentext")

        # Should be a dict with two entries (the documents from the nested subfolder)
        assert isinstance(result, dict)
        assert len(result) == 2

        # Should have both nested documents
        assert "opentext://nested_doc1.pdf" in result
        assert "opentext://nested_doc2.xlsx" in result

        # Verify the document info objects have correct details
        nested_doc1 = result["opentext://nested_doc1.pdf"]
        assert isinstance(nested_doc1, OpenTextDocumentInfo)
        assert nested_doc1.opentext_id == 300
        assert nested_doc1.opentext_name == "nested_doc1.pdf"

        nested_doc2 = result["opentext://nested_doc2.xlsx"]
        assert isinstance(nested_doc2, OpenTextDocumentInfo)
        assert nested_doc2.opentext_id == 400
        assert nested_doc2.opentext_name == "nested_doc2.xlsx"
