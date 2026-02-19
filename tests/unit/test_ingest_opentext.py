import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from snowflake_document_agent.ingest_opentext import get_opentext_documents, OpenTextDownloader


def test_get_opentext_documents_basic():
    """Test that get_opentext_documents returns dict mapping URIs to display_name strings."""
    mock_downloader = Mock()

    # Mock API response for a single document
    def mock_call_side_effect(method, path):
        mock_response = Mock()
        if path == "opentext/cloud/v1/nodes/12345":
            mock_response.json.return_value = {
                "data": {
                    "id": 12345,
                    "name": "test_document",
                    "type_name": "Document",
                }
            }
        elif path == "opentext/cloud/v1/nodes/12345/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "pdf", "version_number": 1}}
        return mock_response

    mock_downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute
    docs = get_opentext_documents(mock_downloader, opentext_nodes=[12345])

    # Verify - should return dict[str, str] (URI -> display_name)
    assert len(docs) == 1

    # Find the URI (should be opentext://12345.pdf?version_number=1)
    uris = list(docs.keys())
    assert len(uris) == 1
    uri = uris[0]
    assert uri.startswith("opentext://12345.pdf")
    assert "version_number=1" in uri

    # Should map to display name
    assert docs[uri] == "test_document.pdf"


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
                    "type_name": "Document",
                }
            }
        elif path == "opentext/cloud/v1/nodes/200/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "docx", "version_number": 2}}
        return mock_response

    mock_downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute
    docs = get_opentext_documents(mock_downloader, opentext_nodes=[100])

    # Should find the document inside the folder
    assert len(docs) == 1

    # Find the URI (should be opentext://200.docx?version_number=2)
    uris = list(docs.keys())
    uri = uris[0]
    assert uri.startswith("opentext://200.docx")
    assert "version_number=2" in uri

    # Should map to display name with parent folder
    assert docs[uri] == "Documents/child_doc.docx"


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
                    "type_name": "Shortcut",
                    "original_id": 600,
                }
            }
        elif path == "opentext/cloud/v1/nodes/500/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "pdf", "version_number": 3}}
        elif path == "opentext/cloud/v1/nodes/600":
            # Actual document (linked from shortcut)
            mock_response.json.return_value = {
                "data": {
                    "id": 600,
                    "name": "actual_doc",
                    "type_name": "Document",
                }
            }
        elif path == "opentext/cloud/v1/nodes/600/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "pdf", "version_number": 1}}
        return mock_response

    mock_downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute
    docs = get_opentext_documents(mock_downloader, opentext_nodes=[500])

    # Should have one document with shortcut's display name
    assert len(docs) == 1

    # Find the URI - should use actual document's node ID (600) from the recursive call
    uris = list(docs.keys())
    uri = uris[0]
    assert uri.startswith("opentext://600.pdf")
    assert "version_number=1" in uri

    # Should use shortcut name in display name (shortcut_name.pdf replaces actual_doc.pdf)
    assert docs[uri] == "shortcut_name.pdf"


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

            # Test downloading a document using new netloc format
            result = downloader("opentext://12345.pdf?version_number=1")

            # Should return a Path
            assert isinstance(result, Path)
            assert result.exists()
            assert result.suffix == ".pdf"
            assert "12345_" in result.name  # Should have node ID prefix

            # Should have made API call to download content
            mock_get.assert_called_once_with(
                "https://api.example.com/opentext/cloud/v1/nodes/12345/content", headers=downloader.headers
            )


def test_opentext_downloader_call_invalid_uri_format():
    """Test that OpenTextDownloader.__call__ errors on invalid URI format."""
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

        # Should error for URI without proper netloc format (no dot separator)
        with pytest.raises(ValueError):
            downloader("opentext://12345")

        # Should error for URI with empty netloc
        with pytest.raises(ValueError):
            downloader("opentext://")

        # Should error for URI without netloc (path-based)
        with pytest.raises(ValueError):
            downloader("opentext:///path/to/file.pdf")


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
