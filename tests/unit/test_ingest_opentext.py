import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from snowflake_document_agent.ingest_opentext import get_opentext_documents, OpenTextDownloader


def test_get_opentext_documents_basic():
    """Test that get_opentext_documents returns dict mapping URIs to (datetime, str) tuples."""
    mock_downloader = Mock()

    # Mock API response for a single document
    def mock_call_side_effect(method, path):
        mock_response = Mock()
        if path == "opentext/cloud/v1/nodes/12345":
            mock_response.json.return_value = {
                "data": {
                    "id": 12345,
                    "name": "test_document",
                    "modify_date": "2024-01-15T10:30:00Z",
                    "type_name": "Document",
                }
            }
        elif path == "opentext/cloud/v1/nodes/12345/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "pdf"}}
        return mock_response

    mock_downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute
    docs = get_opentext_documents(mock_downloader, opentext_nodes=[12345], prefix="opentext")

    # Verify - should return dict[str, tuple[datetime, str]]
    assert len(docs) == 1
    assert "opentext://test_document.pdf" in docs

    # Each document should be a tuple: (modified_at_utc, metadata)
    assert isinstance(docs["opentext://test_document.pdf"], tuple)
    assert len(docs["opentext://test_document.pdf"]) == 2

    modified_time, metadata = docs["opentext://test_document.pdf"]
    assert isinstance(modified_time, datetime)
    assert modified_time.tzinfo == timezone.utc
    assert isinstance(metadata, str)
    assert metadata == ""  # Empty metadata by default


def test_get_opentext_documents_handles_folder():
    """Test that get_opentext_documents recursively processes folders."""
    mock_downloader = Mock()

    def mock_call_side_effect(method, path):
        mock_response = Mock()
        if path == "opentext/cloud/v1/nodes/100":
            # Folder node
            mock_response.json.return_value = {
                "data": {
                    "id": 100,
                    "name": "Documents",
                    "modify_date": "2024-01-01T08:00:00Z",
                    "type_name": "Folder",
                }
            }
        elif path == "opentext/cloud/v2/nodes/100/nodes?limit=1000":
            # Child nodes
            mock_response.json.return_value = {
                "results": [
                    {
                        "data": {
                            "properties": {
                                "id": 200,
                                "name": "child_doc",
                                "modify_date": "2024-01-02T09:00:00Z",
                                "type_name": "Document",
                            }
                        }
                    }
                ]
            }
        elif path == "opentext/cloud/v1/nodes/200":
            # Child document
            mock_response.json.return_value = {
                "data": {
                    "id": 200,
                    "name": "child_doc",
                    "modify_date": "2024-01-02T09:00:00Z",
                    "type_name": "Document",
                }
            }
        elif path == "opentext/cloud/v1/nodes/200/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "docx"}}
        return mock_response

    mock_downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute
    docs = get_opentext_documents(mock_downloader, opentext_nodes=[100], prefix="opentext")

    # Should find the document inside the folder
    assert len(docs) == 1
    assert "opentext://Documents/child_doc.docx" in docs

    modified_time, metadata = docs["opentext://Documents/child_doc.docx"]
    assert isinstance(modified_time, datetime)
    assert isinstance(metadata, str)


def test_get_opentext_documents_handles_shortcut():
    """Test that get_opentext_documents resolves shortcuts to actual documents."""
    mock_downloader = Mock()

    def mock_call_side_effect(method, path):
        mock_response = Mock()
        if path == "opentext/cloud/v1/nodes/500":
            # Shortcut node
            mock_response.json.return_value = {
                "data": {
                    "id": 500,
                    "name": "shortcut_name",
                    "modify_date": "2024-01-15T12:00:00Z",
                    "type_name": "Shortcut",
                    "original_id": 600,
                }
            }
        elif path == "opentext/cloud/v1/nodes/500/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "pdf"}}
        elif path == "opentext/cloud/v1/nodes/600":
            # Actual document
            mock_response.json.return_value = {
                "data": {
                    "id": 600,
                    "name": "actual_doc",
                    "modify_date": "2024-01-10T11:00:00Z",
                    "type_name": "Document",
                }
            }
        return mock_response

    mock_downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute
    docs = get_opentext_documents(mock_downloader, opentext_nodes=[500], prefix="opentext")

    # Should use shortcut name but resolve to actual document data
    assert len(docs) == 1
    assert "opentext://shortcut_name.pdf" in docs

    modified_time, metadata = docs["opentext://shortcut_name.pdf"]
    # Should use actual document's modification time
    expected_time = datetime(2024, 1, 10, 11, 0, 0, tzinfo=timezone.utc)
    assert modified_time == expected_time


def test_opentext_downloader_call_basic():
    """Test that OpenTextDownloader.__call__ downloads documents properly."""
    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        downloader = OpenTextDownloader(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Mock the content download
        with patch("snowflake_document_agent.ingest_opentext.requests.get") as mock_get:
            mock_content_response = Mock()
            mock_content_response.content = b"fake document content"
            mock_get.return_value = mock_content_response

            # Test downloading a document
            result = downloader("opentext://12345/test_document.pdf")

            # Should return a Path
            assert isinstance(result, Path)
            assert result.exists()
            assert result.suffix == ".pdf"

            # Should have made API call to download content
            mock_get.assert_called_once()


def test_opentext_downloader_call_invalid_prefix():
    """Test that OpenTextDownloader.__call__ errors on invalid URI prefix."""
    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        downloader = OpenTextDownloader(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Should error for wrong prefix
        with pytest.raises(AssertionError, match="required prefix"):
            downloader("sharepoint://file1.txt")

        # Should error for no prefix at all
        with pytest.raises(AssertionError, match="required prefix"):
            downloader("file1.txt")


def test_opentext_downloader_initialization():
    """Test OpenTextDownloader initialization with credentials."""
    with patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        downloader = OpenTextDownloader(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Should have made auth request
        mock_post.assert_called_once()
        assert downloader.headers["authorization"] == "Bearer fake_token"


def test_opentext_downloader_missing_credentials():
    """Test that OpenTextDownloader raises error when credentials are missing."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="Missing required OpenText parameters"):
            OpenTextDownloader()


def test_opentext_downloader_env_vars():
    """Test that OpenTextDownloader reads from environment variables."""
    env_vars = {
        "OPENTEXT_CLIENT_ID": "env_client",
        "OPENTEXT_CLIENT_SECRET": "env_secret",
        "OPENTEXT_API_PREFIX": "https://env.api.com",
        "OPENTEXT_APP_CLIENT_ID": "env_app_client",
        "OPENTEXT_APP_CLIENT_SECRET": "env_app_secret",
    }

    with (
        patch.dict("os.environ", env_vars),
        patch("snowflake_document_agent.ingest_opentext.requests.post") as mock_post,
    ):
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_post.return_value = mock_auth_response

        downloader = OpenTextDownloader()

        assert downloader.client_id == "env_client"
        assert downloader.client_secret == "env_secret"
        assert downloader.api_prefix == "https://env.api.com"
