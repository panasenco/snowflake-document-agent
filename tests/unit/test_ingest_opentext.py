import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from requests.exceptions import HTTPError
from snowdoc.ingest_opentext import OpenTextDownloader


def test_get_opentext_documents_basic():
    """Test that get_opentext_documents returns generator of (source_uri, display_name) tuples."""
    # Create actual OpenTextDownloader instance with mocked authentication
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

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

    # Should yield exactly one tuple (source_uri, display_name, metadata)
    source_uri, display_name, metadata = next(docs_generator)

    # Should be no more items in the generator
    with pytest.raises(StopIteration):
        next(docs_generator)
    assert source_uri.startswith("opentext://12345")
    assert "v=1" in source_uri
    assert "ext=.pdf" in source_uri

    # Should have correct display name
    assert display_name == "test_document.pdf"

    # Should have metadata dict (with no description since none was in the node data)
    assert isinstance(metadata, dict)


def test_get_opentext_documents_handles_folder():
    """Test that get_opentext_documents recursively processes folders."""
    # Create actual OpenTextDownloader instance with mocked authentication
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

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

    # Should yield exactly one tuple (source_uri, display_name, metadata) from the folder
    source_uri, display_name, metadata = next(docs_generator)

    # Should be no more items in the generator
    with pytest.raises(StopIteration):
        next(docs_generator)
    assert source_uri.startswith("opentext://200")
    assert "v=2" in source_uri
    assert "ext=.docx" in source_uri

    # Should have display name with parent folder
    assert display_name == "Documents/child_doc.docx"

    # Should have metadata dict from the child document node
    assert isinstance(metadata, dict)


def test_get_opentext_documents_handles_shortcut():
    """Test that get_opentext_documents resolves shortcuts to actual documents."""
    # Create actual OpenTextDownloader instance with mocked authentication
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

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
    source_uri, display_name, metadata = next(docs_generator)

    # Should be no more items in the generator
    with pytest.raises(StopIteration):
        next(docs_generator)
    # URI should use actual document's node ID (600) from the recursive call
    assert source_uri.startswith("opentext://600")
    assert "v=1" in source_uri
    assert "ext=.pdf" in source_uri

    # Should use shortcut name in display name (shortcut_name.pdf replaces actual_doc.pdf)
    assert display_name == "shortcut_name.pdf"

    # Should have metadata dict from the actual document (not the shortcut)
    assert isinstance(metadata, dict)


def test_get_opentext_documents_handles_http_errors():
    """Test that get_opentext_documents handles 404/401 errors gracefully across all API calls and continues processing."""
    # Create actual OpenTextDownloader instance with mocked authentication
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

        downloader = OpenTextDownloader(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

    # Mock API responses with various error scenarios
    def mock_call_side_effect(method, path):
        mock_response = Mock()

        # Node info call errors
        if path == "opentext/cloud/v1/nodes/404":
            mock_response.status_code = 404
            error = HTTPError("404 Client Error: Not Found")
            error.response = mock_response
            raise error
        elif path == "opentext/cloud/v1/nodes/401":
            mock_response.status_code = 401
            error = HTTPError("401 Client Error: Unauthorized")
            error.response = mock_response
            raise error

        # Folder with children list error (404)
        elif path == "opentext/cloud/v1/nodes/100":
            mock_response.json.return_value = {
                "data": {"id": 100, "name": "folder_404_children", "type_name": "Folder"}
            }
        elif path == "opentext/cloud/v2/nodes/100/nodes?limit=1000":
            mock_response.status_code = 404
            error = HTTPError("404 Client Error: Not Found")
            error.response = mock_response
            raise error

        # Folder with children list error (401)
        elif path == "opentext/cloud/v1/nodes/101":
            mock_response.json.return_value = {
                "data": {"id": 101, "name": "folder_401_children", "type_name": "Folder"}
            }
        elif path == "opentext/cloud/v2/nodes/101/nodes?limit=1000":
            mock_response.status_code = 401
            error = HTTPError("401 Client Error: Unauthorized")
            error.response = mock_response
            raise error

        # Document with version error (404)
        elif path == "opentext/cloud/v1/nodes/200":
            mock_response.json.return_value = {"data": {"id": 200, "name": "doc_404_version", "type_name": "Document"}}
        elif path == "opentext/cloud/v1/nodes/200/versions/0":
            mock_response.status_code = 404
            error = HTTPError("404 Client Error: Not Found")
            error.response = mock_response
            raise error

        # Document with version error (401)
        elif path == "opentext/cloud/v1/nodes/201":
            mock_response.json.return_value = {"data": {"id": 201, "name": "doc_401_version", "type_name": "Document"}}
        elif path == "opentext/cloud/v1/nodes/201/versions/0":
            mock_response.status_code = 401
            error = HTTPError("401 Client Error: Unauthorized")
            error.response = mock_response
            raise error

        # Successful document
        elif path == "opentext/cloud/v1/nodes/300":
            mock_response.json.return_value = {"data": {"id": 300, "name": "successful_doc", "type_name": "Document"}}
        elif path == "opentext/cloud/v1/nodes/300/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "txt", "version_number": 1}}

        return mock_response

    downloader.call = Mock(side_effect=mock_call_side_effect)

    # Mock the logger on the downloader instance
    mock_logger = Mock()
    downloader.logger = mock_logger

    # Execute - test all error scenarios: 404 node, 401 node, folder errors, document errors, success
    docs_generator = downloader.get_opentext_documents(opentext_nodes=[404, 401, 100, 101, 200, 201, 300])
    docs = list(docs_generator)  # Convert generator to list

    # Should log multiple errors (6 total: 404 node, 401 node, 404 children, 401 children, 404 version, 401 version)
    assert mock_logger.exception.call_count == 6

    # Verify exception messages contain relevant information
    error_calls = [call[0][0] for call in mock_logger.exception.call_args_list]

    # Check that we have errors for all the expected scenarios
    assert any("404" in msg and "node" in msg.lower() for msg in error_calls), "Should have node 404 error"
    assert any("401" in msg and "node" in msg.lower() for msg in error_calls), "Should have node 401 error"
    assert any("404" in msg and ("children" in msg.lower() or "folder" in msg.lower()) for msg in error_calls), (
        "Should have children 404 error"
    )
    assert any("401" in msg and ("children" in msg.lower() or "folder" in msg.lower()) for msg in error_calls), (
        "Should have children 401 error"
    )
    assert any("404" in msg and ("version" in msg.lower() or "document" in msg.lower()) for msg in error_calls), (
        "Should have version 404 error"
    )
    assert any("401" in msg and ("version" in msg.lower() or "document" in msg.lower()) for msg in error_calls), (
        "Should have version 401 error"
    )

    # Should continue processing and return (None, None, None) sentinels for errors plus successful node (300)
    assert len(docs) == 7, f"Expected 7 results (6 error sentinels + 1 success), got {len(docs)}"

    # Count error sentinels and get successful documents
    error_sentinel_count = sum(1 for uri, name, meta in docs if uri is None and name is None)
    successful_docs = [(uri, name, meta) for uri, name, meta in docs if uri is not None and name is not None]

    assert error_sentinel_count == 6, f"Expected 6 error sentinels, got {error_sentinel_count}"
    assert len(successful_docs) == 1, f"Expected 1 successful document, got {len(successful_docs)}"

    # Verify the successful document
    source_uri, display_name, metadata = successful_docs[0]
    assert source_uri.startswith("opentext://300")
    assert "v=1" in source_uri
    assert "ext=.txt" in source_uri
    assert display_name == "successful_doc.txt"
    assert isinstance(metadata, dict)


def test_opentext_downloader_call_basic():
    """Test that OpenTextDownloader.__call__ downloads documents properly."""
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}

        # Mock content response
        mock_content_response = Mock()
        mock_content_response.content = b"fake document content"

        # Return different responses based on call order
        mock_request.side_effect = [mock_auth_response, mock_content_response]

        downloader = OpenTextDownloader(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Test downloading a document using new URI format
        result = downloader("opentext://12345?v=1&ext=.pdf")

        # Should return a Path
        assert isinstance(result, Path)
        assert result.exists()
        assert result.suffix == ".pdf"
        assert "12345_" in result.name  # Should have node ID prefix

        # Should have made 2 requests: auth + content download
        assert mock_request.call_count == 2


def test_opentext_downloader_call_uri_format():
    """Test that OpenTextDownloader.__call__ errors on invalid URI format."""
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

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
            downloader("opentext://12345?v=1")

        # Should error for blank node id
        with pytest.raises(ValueError):
            downloader("opentext://?ext=.pdf")

        # Should error for non-numeric node id
        with pytest.raises(ValueError):
            downloader("opentext://path/to/file?ext=.pdf")

        path = downloader("opentext://12345?ext=.pdf")
        downloader.call.assert_called_with("GET", "opentext/cloud/v1/nodes/12345/content")
        assert path.suffix == ".pdf"

        downloader.call.reset_mock()

        path = downloader("https://opentext.example.com/my/api/path/6789?ext=.docx")
        downloader.call.assert_called_with("GET", "opentext/cloud/v1/nodes/6789/content")
        assert path.suffix == ".docx"


def test_opentext_downloader_initialization():
    """Test OpenTextDownloader initialization with credentials."""
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        # Mock auth response
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

        downloader = OpenTextDownloader(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

        # Should have made auth request
        mock_request.assert_called_once()
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
        patch("snowdoc.ingest_opentext.requests.request") as mock_request,
    ):
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

        downloader = OpenTextDownloader()

        assert downloader.client_id == "env_client"
        assert downloader.client_secret == "env_secret"
        assert downloader.api_prefix == "https://env.api.com"


def test_get_opentext_documents_includes_description_metadata():
    """Test that get_opentext_documents extracts description from node data and includes it in metadata."""
    # Create actual OpenTextDownloader instance with mocked authentication
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

        downloader = OpenTextDownloader(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

    def mock_call_side_effect(method, path):
        mock_response = Mock()
        if path == "opentext/cloud/v1/nodes/12345":
            mock_response.json.return_value = {
                "data": {
                    "id": 12345,
                    "name": "documented_file",
                    "type_name": "Document",
                    "description": "This is an important compliance document for Q4 review",
                }
            }
        elif path == "opentext/cloud/v1/nodes/12345/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "pdf", "version_number": 1}}
        return mock_response

    downloader.call = Mock(side_effect=mock_call_side_effect)

    docs_generator = downloader.get_opentext_documents(opentext_nodes=[12345])
    source_uri, display_name, metadata = next(docs_generator)

    # Metadata should contain the description from node_data
    assert metadata is not None
    assert isinstance(metadata, dict)
    assert "description" in metadata
    assert metadata["description"] == "This is an important compliance document for Q4 review"


def test_get_opentext_documents_metadata_without_description():
    """Test that metadata dict is returned even when the node has no description."""
    with patch("snowdoc.ingest_opentext.requests.request") as mock_request:
        mock_auth_response = Mock()
        mock_auth_response.json.return_value = {"access_token": "fake_token"}
        mock_request.return_value = mock_auth_response

        downloader = OpenTextDownloader(
            client_id="test_client",
            client_secret="test_secret",
            api_prefix="https://api.example.com",
            app_client_id="app_client",
            app_client_secret="app_secret",
        )

    def mock_call_side_effect(method, path):
        mock_response = Mock()
        if path == "opentext/cloud/v1/nodes/99999":
            mock_response.json.return_value = {
                "data": {
                    "id": 99999,
                    "name": "no_description_file",
                    "type_name": "Document",
                    # No "description" key at all
                }
            }
        elif path == "opentext/cloud/v1/nodes/99999/versions/0":
            mock_response.json.return_value = {"data": {"file_type": "txt", "version_number": 1}}
        return mock_response

    downloader.call = Mock(side_effect=mock_call_side_effect)

    docs_generator = downloader.get_opentext_documents(opentext_nodes=[99999])
    source_uri, display_name, metadata = next(docs_generator)

    # Should still return a metadata dict, just without the description key
    assert metadata is not None
    assert isinstance(metadata, dict)
    assert "description" not in metadata
