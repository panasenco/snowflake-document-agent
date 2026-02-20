import argparse
import logging
from mimetypes import guess_extension
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests
from tenacity import before_sleep_log, retry, retry_if_exception, stop_after_attempt, wait_random_exponential

from .common import get_console_logger, process_changed_documents

logger = logging.getLogger(__name__)


def is_retriable_requests_error(e: Exception) -> bool:
    """Determine if a requests error should be retried."""
    if not isinstance(e, requests.exceptions.HTTPError):
        return False
    if e.response.status_code in [401, 404]:
        return False
    return True


class OpenTextDownloader:
    """Simple HTTP client for OpenText API with authentication."""

    def __init__(
        self,
        client_id: str = None,
        client_secret: str = None,
        api_prefix: str = None,
        app_client_id: str = None,
        app_client_secret: str = None,
    ):
        self.client_id = client_id or os.environ.get("OPENTEXT_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("OPENTEXT_CLIENT_SECRET")
        self.api_prefix = api_prefix or os.environ.get("OPENTEXT_API_PREFIX")
        self.app_client_id = app_client_id or os.environ.get("OPENTEXT_APP_CLIENT_ID")
        self.app_client_secret = app_client_secret or os.environ.get("OPENTEXT_APP_CLIENT_SECRET")

        # Validate that all required parameters are available
        required_params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "api_prefix": self.api_prefix,
            "app_client_id": self.app_client_id,
            "app_client_secret": self.app_client_secret,
        }

        missing_params = [name for name, value in required_params.items() if not value]
        if missing_params:
            raise ValueError(
                f"Missing required OpenText parameters: {', '.join(missing_params)}. "
                f"Provide them as arguments or set environment variables: "
                f"OPENTEXT_CLIENT_ID, OPENTEXT_CLIENT_SECRET, OPENTEXT_API_PREFIX, "
                f"OPENTEXT_APP_CLIENT_ID, OPENTEXT_APP_CLIENT_SECRET"
            )

        # Initialize headers as empty dict first
        self.headers = {}

        # Authenticate and get access token using call() with retry logic
        auth_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        auth_response = self.call("POST", "opentext/cloud/v1/auth", headers={}, data=auth_data)
        token_dict = auth_response.json()
        access_token = token_dict["access_token"]

        # Store headers for reuse
        self.headers = {
            "authorization": f"Bearer {access_token}",
            "app-client-id": self.app_client_id,  # App credentials as standard headers
            "app-client-secret": self.app_client_secret,
        }

    @retry(
        retry=retry_if_exception(is_retriable_requests_error),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
        before_sleep=before_sleep_log(logger, log_level=logging.INFO, exc_info=True),
    )
    def call(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Make an API call to OpenText with optional custom headers and data."""
        api_url = f"{self.api_prefix}/{path}"
        request_headers = headers if headers is not None else self.headers

        if method == "GET":
            response = requests.get(api_url, headers=request_headers)
        elif method == "POST":
            response = requests.post(api_url, headers=request_headers, data=data)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        return response

    def get_document_extension_version(self, node_id: int, /) -> tuple[str, str]:
        """Gets a (extension, version_number) tuple for a document."""
        version = self.call("GET", f"opentext/cloud/v1/nodes/{node_id}/versions/0").json()
        if version["data"]["file_type"]:
            extension = "." + version["data"]["file_type"].lower()
        elif version["data"]["mime_type"]:
            extension = guess_extension(version["data"]["mime_type"])
        else:
            raise ValueError(f"Unable to determine extension of OpenText node {node_id}.")
        return extension, version["data"]["version_number"]

    def get_opentext_documents(
        self,
        opentext_nodes: list[int],
        *,
        prefix: str = "opentext",
        parents: list[str] = [],
    ) -> dict[str, str]:
        """Discover OpenText documents from specified node IDs.

        Args:
            opentext_nodes: List of OpenText node IDs to process
            parents: List of parent folders to construct a display_name containing all the parent folders

        Returns:
            Dictionary mapping source URIs to display_name strings
        """
        result = {}

        for node_id in opentext_nodes:
            # Get node info from OpenText API
            response = self.call("GET", f"opentext/cloud/v1/nodes/{node_id}")
            node_data = response.json()["data"]
            node_type = node_data["type_name"]
            match node_type:
                case "Folder":
                    # Get child IDs
                    children_response = self.call("GET", f"opentext/cloud/v2/nodes/{node_id}/nodes?limit=1000")
                    children_data = children_response.json()["results"]
                    child_ids = [child["data"]["properties"]["id"] for child in children_data]
                    # Recursively process children
                    child_documents = self.get_opentext_documents(child_ids, parents=[*parents, node_data["name"]])
                    result.update(child_documents)
                case "Document":
                    extension, version_number = self.get_document_extension_version(node_id)
                    # Create source URI with extension
                    source_uri = urlunsplit(
                        (
                            prefix,
                            str(node_id),
                            "",
                            urlencode({"version_number": version_number, "extension": extension}),
                            "",
                        )
                    )
                    result[source_uri] = "/".join([*parents, f"{node_data['name']}.{extension}"])
                case "Shortcut":
                    # Handle shortcut - follow original_id but preserve shortcut name
                    original_id = node_data["original_id"]
                    # Get the linked document(s)
                    original_documents = self.get_opentext_documents([original_id])
                    # Update the display name with shortcut name instead of original name
                    for original_uri, original_display_name in original_documents.items():
                        # Replace the first part of the original display name with the shortcut name
                        # If linked to a document, this will replace the full document name
                        # If linked to a folder, this will replace the top folder name
                        display_name_parts = original_display_name.split("/")
                        if "." in display_name_parts[0]:
                            # Preserve the extension from the original display name
                            display_name_parts[0] = node_data["name"] + "." + display_name_parts[0].split(".", 1)[1]
                        else:
                            display_name_parts[0] = node_data["name"]
                        # Save the new display name
                        result[original_uri] = "/".join(parents + display_name_parts)

        return result

    def __call__(self, source_uri: str) -> Path:
        """Downloads an OpenText document and returns its local path."""
        # The 'netloc' part of the URI is the OpenText node ID
        source_parts = urlsplit(source_uri)
        opentext_id = source_parts.netloc
        # The extension is stored in the query part of the URI
        extension = parse_qs(source_parts.query)["extension"][0]
        # Call OpenText API to get content
        response = self.call("GET", f"opentext/cloud/v1/nodes/{opentext_id}/content")
        # Write content to temporary file with opentext_id prefix and correct extension
        temp_file = tempfile.NamedTemporaryFile(delete=False, prefix=f"{opentext_id}_", suffix=extension)
        temp_file.write(response.content)
        temp_file.close()

        return Path(temp_file.name)


def main() -> None:
    """Main entry point for OpenText document ingestion CLI."""
    parser = argparse.ArgumentParser(description="Ingest OpenText documents into Snowflake.")
    parser.add_argument("node_ids", nargs="+", type=int, help="OpenText node IDs to process")
    parser.add_argument(
        "-c", "--snowflake-connection", default="default", help="Name of Snowflake connection to use (default: default)"
    )
    parser.add_argument(
        "-p", "--prefix", default="opentext", help="URI scheme prefix for the documents (default: opentext)"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Be verbose. Include once for INFO output, twice for DEBUG output.",
        action="count",
        default=0,
    )

    args = parser.parse_args()
    logger = get_console_logger(args.verbose)
    opentext_downloader = OpenTextDownloader()
    logger.info(f"Getting OpenText documents in root nodes {args.node_ids}...")
    opentext_documents = opentext_downloader.get_opentext_documents(args.node_ids, prefix=args.prefix)
    process_changed_documents(
        opentext_documents,
        connection=args.snowflake_connection,
        downloader=opentext_downloader,
        prefix=f"{args.prefix}://",
        logger=logger,
    )


if __name__ == "__main__":
    main()
