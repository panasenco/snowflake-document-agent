import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from snowflake_document_agent.ingest_opentext import OpenTextDownloader


def test_get_opentext_documents_basic():
    """Test that get_opentext_documents returns generator of (source_uri, display_name) tuples."""
    # Create actual OpenTextDownloader instance with mocked authentication
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

    # Mock API responses for document discovery
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

    downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute - call as method on the downloader instance
    docs_generator = downloader.get_opentext_documents(opentext_nodes=[12345])

    # Should yield exactly one tuple (source_uri, display_name)
    source_uri, display_name = next(docs_generator)

    # Should be no more items in the generator
    with pytest.raises(StopIteration):
        next(docs_generator)
    assert source_uri.startswith("opentext://12345")
    assert "version_number=1" in source_uri
    assert "extension=.pdf" in source_uri

    # Should have correct display name
    assert display_name == "test_document.pdf"


def test_get_opentext_documents_handles_folder():
    """Test that get_opentext_documents recursively processes folders."""
    # Create actual OpenTextDownloader instance with mocked authentication
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

    downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute - call as method on the downloader instance
    docs_generator = downloader.get_opentext_documents(opentext_nodes=[100])

    # Should yield exactly one tuple (source_uri, display_name) from the folder
    source_uri, display_name = next(docs_generator)

    # Should be no more items in the generator
    with pytest.raises(StopIteration):
        next(docs_generator)
    assert source_uri.startswith("opentext://200")
    assert "version_number=2" in source_uri
    assert "extension=.docx" in source_uri

    # Should have display name with parent folder
    assert display_name == "Documents/child_doc.docx"


def test_get_opentext_documents_handles_shortcut():
    """Test that get_opentext_documents resolves shortcuts to actual documents."""
    # Create actual OpenTextDownloader instance with mocked authentication
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

    downloader.call = Mock(side_effect=mock_call_side_effect)

    # Execute - call as method on the downloader instance
    docs_generator = downloader.get_opentext_documents(opentext_nodes=[500])

    # Should yield exactly one tuple with shortcut's display name
    source_uri, display_name = next(docs_generator)

    # Should be no more items in the generator
    with pytest.raises(StopIteration):
        next(docs_generator)
    # URI should use actual document's node ID (600) from the recursive call
    assert source_uri.startswith("opentext://600")
    assert "version_number=1" in source_uri
    assert "extension=.pdf" in source_uri

    # Should use shortcut name in display name (shortcut_name.pdf replaces actual_doc.pdf)
    assert display_name == "shortcut_name.pdf"


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

            # Test downloading a document using new URI format
            result = downloader("opentext://12345?version_number=1&extension=.pdf")

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

        # Mock the call method to avoid real HTTP requests
        mock_response = Mock()
        mock_response.content = b"fake content"
        downloader.call = Mock(return_value=mock_response)

        # Should error for URI missing extension parameter
        with pytest.raises(KeyError):
            downloader("opentext://12345?version_number=1")

        # Should succeed for URI with empty netloc (but creates invalid API path)
        # This exposes that the implementation doesn't validate netloc properly
        result = downloader("opentext://?extension=.pdf&version_number=1")
        assert isinstance(result, Path)
        # Should have made a call with empty node ID
        downloader.call.assert_called_with("GET", "opentext/cloud/v1/nodes//content")

        # Reset mock for next test
        downloader.call.reset_mock()

        # Should succeed for URI without netloc (path-based)
        # This also exposes that netloc validation is missing
        result = downloader("opentext:///path/to/file.pdf?extension=.pdf&version_number=1")
        assert isinstance(result, Path)
        # Should have made a call with empty node ID (netloc is empty when path is used)
        downloader.call.assert_called_with("GET", "opentext/cloud/v1/nodes//content")


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
