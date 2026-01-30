"""Tests for OpenText document ingestion functionality."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from snowflake_document_agent.ingest_opentext import OpenTextDocumentInfo, OpenTextClient, get_opentext_documents


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
    from snowflake_document_agent.ingest_opentext import HttpMethod

    mock_client.call.assert_called_once_with(HttpMethod.GET, "opentext/cloud/v1/nodes/12345/content")

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
        mock_auth_response.raise_for_status.return_value = None
        mock_post.return_value = mock_auth_response

        client = OpenTextClient(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Should have made auth request during init (now includes headers due to call() method)
        mock_post.assert_called_once_with(
            "https://api.example.com/opentext/cloud/v1/auth",
            headers={},
            data={"grant_type": "client_credentials", "client_id": "test_client", "client_secret": "test_secret"},
        )

        # Should store the headers with token
        assert client.headers["authorization"] == "Bearer fake_token"
        assert client.headers["app-client-id"] == "app_client"
        assert client.headers["app-client-secret"] == "app_secret"


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
        from snowflake_document_agent.ingest_opentext import HttpMethod

        result = client.call(HttpMethod.GET, "opentext/cloud/v1/nodes/12345/content")

        # Should NOT have made another auth request
        mock_post.assert_not_called()

        # Should have made the API request with Bearer token
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args[0][0] == "https://api.example.com/opentext/cloud/v1/nodes/12345/content"

        # Check that headers include the token
        headers = call_args[1]["headers"]
        assert headers["authorization"] == "Bearer fake_token"
        assert headers["app-client-id"] == "app_client"
        assert headers["app-client-secret"] == "app_secret"

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


def test_get_opentext_documents_handles_shortcut():
    """Test that get_opentext_documents handles Shortcut nodes by following original_id."""
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

        # Mock the client.call method for shortcut scenario
        def mock_call_side_effect(method, path):
            mock_response = Mock()

            if path == "opentext/cloud/v1/nodes/500":
                # Shortcut node info
                mock_response.json.return_value = {
                    "data": {
                        "id": 500,
                        "name": "shortcut_to_doc.pdf",
                        "modify_date": "2024-01-15T12:00:00Z",
                        "type_name": "Shortcut",
                        "original_id": 600,  # Points to the actual document
                    }
                }
            elif path == "opentext/cloud/v1/nodes/600":
                # Actual document that the shortcut points to
                mock_response.json.return_value = {
                    "data": {
                        "id": 600,
                        "name": "actual_document.pdf",
                        "modify_date": "2024-01-15T12:00:00Z",
                        "type_name": "Document",
                    }
                }

            return mock_response

        client.call = Mock(side_effect=mock_call_side_effect)

        # Should follow the shortcut and return the actual document
        result = get_opentext_documents(client, opentext_nodes=[500], prefix="opentext")

        # Should be a dict with one entry for the actual document (using shortcut name)
        assert isinstance(result, dict)
        assert len(result) == 1

        # Should have the document accessible via shortcut name
        expected_uri = "opentext://shortcut_to_doc.pdf"
        assert expected_uri in result

        # Document info should point to the actual document but preserve shortcut name
        doc_info = result[expected_uri]
        assert isinstance(doc_info, OpenTextDocumentInfo)
        assert doc_info.opentext_id == 600  # Actual document ID
        assert doc_info.opentext_name == "shortcut_to_doc.pdf"  # Shortcut name preserved


def test_get_opentext_documents_handles_multiple_shortcuts():
    """Test that get_opentext_documents handles multiple shortcuts correctly."""
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

        # Mock multiple shortcuts pointing to different documents
        def mock_call_side_effect(method, path):
            mock_response = Mock()

            if path == "opentext/cloud/v1/nodes/700":
                # First shortcut
                mock_response.json.return_value = {
                    "data": {
                        "id": 700,
                        "name": "link_to_report.docx",
                        "modify_date": "2024-01-15T12:00:00Z",
                        "type_name": "Shortcut",
                        "original_id": 710,
                    }
                }
            elif path == "opentext/cloud/v1/nodes/800":
                # Second shortcut
                mock_response.json.return_value = {
                    "data": {
                        "id": 800,
                        "name": "link_to_spreadsheet.xlsx",
                        "modify_date": "2024-01-16T12:00:00Z",
                        "type_name": "Shortcut",
                        "original_id": 810,
                    }
                }
            elif path == "opentext/cloud/v1/nodes/710":
                # First actual document
                mock_response.json.return_value = {
                    "data": {
                        "id": 710,
                        "name": "quarterly_report.docx",
                        "modify_date": "2024-01-15T12:00:00Z",
                        "type_name": "Document",
                    }
                }
            elif path == "opentext/cloud/v1/nodes/810":
                # Second actual document
                mock_response.json.return_value = {
                    "data": {
                        "id": 810,
                        "name": "budget_data.xlsx",
                        "modify_date": "2024-01-16T12:00:00Z",
                        "type_name": "Document",
                    }
                }

            return mock_response

        client.call = Mock(side_effect=mock_call_side_effect)

        # Should handle multiple shortcuts
        result = get_opentext_documents(client, opentext_nodes=[700, 800], prefix="opentext")

        # Should be a dict with two entries
        assert isinstance(result, dict)
        assert len(result) == 2

        # Should have both shortcuts resolved
        assert "opentext://link_to_report.docx" in result
        assert "opentext://link_to_spreadsheet.xlsx" in result

        # Verify first shortcut resolution
        doc1 = result["opentext://link_to_report.docx"]
        assert isinstance(doc1, OpenTextDocumentInfo)
        assert doc1.opentext_id == 710  # Actual document ID
        assert doc1.opentext_name == "link_to_report.docx"  # Shortcut name

        # Verify second shortcut resolution
        doc2 = result["opentext://link_to_spreadsheet.xlsx"]
        assert isinstance(doc2, OpenTextDocumentInfo)
        assert doc2.opentext_id == 810  # Actual document ID
        assert doc2.opentext_name == "link_to_spreadsheet.xlsx"  # Shortcut name


def test_opentext_client_retries_on_server_errors():
    """Test that OpenTextClient.call() retries on server errors (500, 502, etc) but not 404."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import OpenTextClient
    import requests

    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
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

        with patch("snowflake_document_agent.ingest_opentext.requests.get") as mock_get:
            # First call fails with 500, second call succeeds
            mock_error_response = Mock()
            mock_error_response.status_code = 500
            mock_error_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=mock_error_response
            )

            mock_success_response = Mock()
            mock_success_response.status_code = 200
            mock_success_response.content = b"success content"
            mock_success_response.raise_for_status.return_value = None

            mock_get.side_effect = [mock_error_response, mock_success_response]

            # Should eventually succeed after retry
            from snowflake_document_agent.ingest_opentext import HttpMethod

            result = client.call(HttpMethod.GET, "opentext/cloud/v1/nodes/12345")

            # Should have made 2 calls (first failed, second succeeded)
            assert mock_get.call_count == 2
            assert result == mock_success_response


def test_opentext_client_does_not_retry_404_errors():
    """Test that OpenTextClient.call() does not retry 404 errors."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import OpenTextClient
    import requests

    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
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

        with patch("snowflake_document_agent.ingest_opentext.requests.get") as mock_get:
            # Mock 404 error response
            mock_404_response = Mock()
            mock_404_response.status_code = 404
            mock_404_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_404_response)
            mock_get.return_value = mock_404_response

            # Should raise HTTPError immediately without retries
            from snowflake_document_agent.ingest_opentext import HttpMethod

            with pytest.raises(requests.exceptions.HTTPError):
                client.call(HttpMethod.GET, "opentext/cloud/v1/nodes/99999")

            # Should have made only 1 call (no retries for 404)
            assert mock_get.call_count == 1


def test_opentext_client_does_not_retry_401_errors():
    """Test that OpenTextClient.call() does not retry 401 auth errors."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import OpenTextClient, HttpMethod
    import requests

    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
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

        with patch("snowflake_document_agent.ingest_opentext.requests.get") as mock_get:
            # Mock 401 error response (auth failure)
            mock_401_response = Mock()
            mock_401_response.status_code = 401
            mock_401_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_401_response)
            mock_get.return_value = mock_401_response

            # Should raise HTTPError immediately without retries
            with pytest.raises(requests.exceptions.HTTPError):
                client.call(HttpMethod.GET, "opentext/cloud/v1/nodes/12345")

            # Should have made only 1 call (no retries for 401)
            assert mock_get.call_count == 1


def test_opentext_client_retries_auth_on_server_errors():
    """Test that OpenTextClient.__init__() retries auth on server errors."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import OpenTextClient
    import requests

    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
        # First auth call fails with 500, second succeeds
        mock_error_response = Mock()
        mock_error_response.status_code = 500
        mock_error_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_error_response)

        mock_success_response = Mock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {"access_token": "fake_token"}
        mock_success_response.raise_for_status.return_value = None

        mock_post.side_effect = [mock_error_response, mock_success_response]

        # Should eventually succeed after retry
        client = OpenTextClient(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Should have made 2 auth calls (first failed, second succeeded)
        assert mock_post.call_count == 2
        assert client.headers["authorization"] == "Bearer fake_token"


def test_opentext_client_does_not_retry_auth_on_401_errors():
    """Test that OpenTextClient.__init__() does not retry auth on 401 errors."""
    from unittest.mock import patch
    from snowflake_document_agent.ingest_opentext import OpenTextClient
    import requests

    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
        # Mock 401 error response (bad credentials)
        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_401_response)
        mock_post.return_value = mock_401_response

        # Should raise HTTPError immediately without retries
        with pytest.raises(requests.exceptions.HTTPError):
            OpenTextClient(
                client_id="bad_client",
                client_secret="bad_secret",
                api_prefix="https://api.example.com",
                app_client_id="app_client",
                app_client_secret="app_secret",
            )

        # Should have made only 1 auth call (no retries for 401)
        assert mock_post.call_count == 1


def test_opentext_client_reads_from_environment_variables():
    """Test that OpenTextClient reads parameters from environment variables when not provided."""
    # Mock environment variables
    env_vars = {
        "OPENTEXT_CLIENT_ID": "env_client_id",
        "OPENTEXT_CLIENT_SECRET": "env_client_secret",
        "OPENTEXT_API_PREFIX": "https://env.api.example.com",
        "OPENTEXT_APP_CLIENT_ID": "env_app_client",
        "OPENTEXT_APP_CLIENT_SECRET": "env_app_secret",
    }

    with (
        patch.dict("os.environ", env_vars, clear=False),
        patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post,
    ):
        # Mock auth response for init
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        # Create client without explicit parameters - should read from env vars
        client = OpenTextClient()

        # Should have read values from environment variables
        assert client.client_id == "env_client_id"
        assert client.client_secret == "env_client_secret"
        assert client.api_prefix == "https://env.api.example.com"
        assert client.app_client_id == "env_app_client"
        assert client.app_client_secret == "env_app_secret"

        # Should have made auth request with env var values
        mock_post.assert_called_once_with(
            "https://env.api.example.com/opentext/cloud/v1/auth",
            headers={},
            data={
                "grant_type": "client_credentials",
                "client_id": "env_client_id",
                "client_secret": "env_client_secret",
            },
        )


def test_opentext_client_explicit_params_override_environment():
    """Test that explicit parameters override environment variables."""
    # Set environment variables
    env_vars = {
        "OPENTEXT_CLIENT_ID": "env_client_id",
        "OPENTEXT_CLIENT_SECRET": "env_client_secret",
        "OPENTEXT_API_PREFIX": "https://env.api.example.com",
        "OPENTEXT_APP_CLIENT_ID": "env_app_client",
        "OPENTEXT_APP_CLIENT_SECRET": "env_app_secret",
    }

    with (
        patch.dict("os.environ", env_vars, clear=False),
        patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post,
    ):
        # Mock auth response for init
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        # Create client with explicit parameters - should use these instead of env vars
        client = OpenTextClient(
            client_id="explicit_client_id",
            client_secret="explicit_client_secret",
            api_prefix="https://explicit.api.example.com",
            app_client_id="explicit_app_client",
            app_client_secret="explicit_app_secret",
        )

        # Should use explicit values, not environment variables
        assert client.client_id == "explicit_client_id"
        assert client.client_secret == "explicit_client_secret"
        assert client.api_prefix == "https://explicit.api.example.com"
        assert client.app_client_id == "explicit_app_client"
        assert client.app_client_secret == "explicit_app_secret"

        # Should have made auth request with explicit values
        mock_post.assert_called_once_with(
            "https://explicit.api.example.com/opentext/cloud/v1/auth",
            headers={},
            data={
                "grant_type": "client_credentials",
                "client_id": "explicit_client_id",
                "client_secret": "explicit_client_secret",
            },
        )


def test_opentext_client_raises_error_when_missing_parameters():
    """Test that OpenTextClient raises error when required parameters are missing."""
    # Clear any existing environment variables and don't provide parameters
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            OpenTextClient()

        error_msg = str(exc_info.value)
        assert "Missing required OpenText parameters" in error_msg
        assert "OPENTEXT_CLIENT_ID" in error_msg
        assert "OPENTEXT_CLIENT_SECRET" in error_msg
        assert "OPENTEXT_API_PREFIX" in error_msg
        assert "OPENTEXT_APP_CLIENT_ID" in error_msg
        assert "OPENTEXT_APP_CLIENT_SECRET" in error_msg


@pytest.mark.integration
def test_opentext_conn_fixture_integration(opentext_conn):
    """Test that opentext_conn fixture works properly for integration tests."""
    if opentext_conn is None:
        pytest.skip("Integration tests not enabled (use --run-integration)")

    # Should have an OpenTextClient instance
    assert isinstance(opentext_conn, OpenTextClient)

    # Should have all required attributes populated from environment
    assert opentext_conn.client_id
    assert opentext_conn.client_secret
    assert opentext_conn.api_prefix
    assert opentext_conn.app_client_id
    assert opentext_conn.app_client_secret

    # Should have authentication headers set
    assert opentext_conn.headers
    assert "authorization" in opentext_conn.headers


@pytest.mark.integration
def test_get_opentext_documents_integration(opentext_conn, pytestconfig):
    """Test get_opentext_documents with real OpenText API using provided node ID."""
    if opentext_conn is None:
        pytest.skip("Integration tests not enabled (use --run-integration)")

    node_id = pytestconfig.getoption("--opentext-node-id")
    if node_id is None:
        pytest.skip("OpenText node ID not provided (use --opentext-node-id)")

    try:
        # Call get_opentext_documents with the provided node ID
        documents = get_opentext_documents(client=opentext_conn, opentext_nodes=[node_id], prefix="opentext")

        # Should return a non-empty dictionary - we expect documents for the provided node
        assert isinstance(documents, dict)
        assert len(documents) > 0, (
            f"No documents found in OpenText node {node_id}. Expected to find documents for testing."
        )

        # Verify structure of returned documents
        for uri, doc_info in documents.items():
            # URI should start with the expected prefix
            assert uri.startswith("opentext://")

            # Should be an OpenTextDocumentInfo instance
            assert isinstance(doc_info, OpenTextDocumentInfo)

            # Should have required attributes
            assert doc_info.opentext_id
            assert doc_info.opentext_name
            assert doc_info.modified_at_utc
            assert doc_info.opentext_api_client is opentext_conn

        print(f"✅ Successfully discovered {len(documents)} documents from OpenText node {node_id}")
        for uri in documents.keys():
            print(f"  - {uri}")

    except Exception as e:
        error_msg = str(e).lower()
        # Provide helpful diagnostic messages but still fail the test
        if "401" in error_msg or "unauthorized" in error_msg:
            print("✅ OpenText API integration successful!")
            print(f"❌ Access denied for node {node_id} (expected in enterprise environments)")
            print("🔒 This proves authentication works, but we don't have access to this specific node")
            pytest.fail(f"Access denied for OpenText node {node_id}: {e}")
        elif "404" in error_msg or "not found" in error_msg:
            print("✅ OpenText API integration successful!")
            print(f"❌ Node {node_id} not found (may have been deleted or doesn't exist)")
            pytest.fail(f"OpenText node {node_id} not found: {e}")
        else:
            # Re-raise unexpected errors
            raise


@pytest.mark.integration
def test_full_opentext_to_snowflake_pipeline(opentext_conn, snowflake_conn, test_schema, pytestconfig):
    """Test complete pipeline: OpenText document discovery -> Snowflake data loading.

    This test requires both OpenText and Snowflake to be configured:
    - OpenText: environment variables must be set
    - Snowflake: --snowflake-connection-name must be provided
    - Node ID: --opentext-node-id must be provided
    """
    # Skip if integration tests not enabled
    if opentext_conn is None or snowflake_conn is None:
        pytest.skip("Full integration test requires both OpenText and Snowflake (use --run-integration)")

    node_id = pytestconfig.getoption("--opentext-node-id")
    if node_id is None:
        pytest.skip("Full integration test requires OpenText node ID (use --opentext-node-id)")

    from pathlib import Path
    import yaml
    from snowflake_document_agent.common import process_documents

    print("🚀 Starting full OpenText -> Snowflake pipeline test")
    print(f"📁 OpenText Node ID: {node_id}")
    print(f"❄️  Snowflake Schema: {test_schema}")

    # Step 1: Read Snowflake configuration
    config_path = Path(__file__).parent.parent / "snowflake.example.yml"
    with open(config_path, "r") as config_file:
        full_config = yaml.safe_load(config_file)
        snowflake_config = full_config["env"]

    # Override with test schema
    snowflake_config["schema"] = test_schema

    print(f"📋 Loaded Snowflake config for schema: {snowflake_config['schema']}")

    # Step 2: Discover OpenText documents
    print(f"🔍 Discovering documents from OpenText node {node_id}...")
    documents = get_opentext_documents(client=opentext_conn, opentext_nodes=[node_id], prefix="opentext")

    print(f"✅ Discovered {len(documents)} documents from OpenText")
    assert len(documents) > 0, f"Expected to find documents in OpenText node {node_id} for full pipeline test"

    # Log discovered documents
    for i, uri in enumerate(list(documents.keys())[:5], 1):  # Show first 5
        print(f"  {i}. {uri}")
    if len(documents) > 5:
        print(f"  ... and {len(documents) - 5} more documents")

    # Step 3: Truncate tables (prepare for clean test)
    print(f"🧹 Truncating tables in schema {test_schema}...")
    with snowflake_conn.cursor() as cursor:
        # Get list of tables in the test schema
        cursor.execute(f"SHOW TABLES IN SCHEMA {test_schema}")
        tables = cursor.fetchall()

        for table_row in tables:
            table_name = table_row[1]  # Table name is in second column
            print(f"  Truncating {table_name}...")
            cursor.execute(f"TRUNCATE TABLE {test_schema}.{table_name}")

    print("✅ Tables truncated successfully")

    # Step 4: Process documents into Snowflake
    print("📤 Processing documents into Snowflake...")

    # Use the common process_documents function
    results = process_documents(document_info_dict=documents, config=snowflake_config, conn=snowflake_conn)

    print(f"✅ Processed {len(results)} documents into Snowflake")

    # Step 5: Verify data was loaded
    print("🔍 Verifying data was loaded into Snowflake...")
    with snowflake_conn.cursor() as cursor:
        # Check the documents table for loaded records
        cursor.execute(f"SELECT COUNT(*) FROM {test_schema}.documents")
        doc_count = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM {test_schema}.chunks")
        chunk_count = cursor.fetchone()[0]

        print(f"📊 Documents table: {doc_count} records")
        print(f"📊 Chunks table: {chunk_count} records")

        # Verify we have data
        assert doc_count > 0, "Expected documents to be loaded, but documents table is empty"
        assert chunk_count > 0, "Expected chunks to be loaded, but chunks table is empty"

        # Show sample data
        cursor.execute(f"SELECT source_uri, relative_path FROM {test_schema}.documents LIMIT 3")
        sample_docs = cursor.fetchall()
        print("📄 Sample loaded documents:")
        for doc in sample_docs:
            print(f"  - {doc[0]} -> {doc[1]}")

    print("🎉 Full OpenText -> Snowflake pipeline test completed successfully!")
    print(f"📈 Summary: {len(documents)} OpenText documents -> {doc_count} DB documents -> {chunk_count} DB chunks")
