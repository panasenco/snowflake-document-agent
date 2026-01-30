"""OpenText document ingestion for Snowflake Document Agent.

This module provides functionality to discover and ingest documents from OpenText
into the Snowflake Document Agent pipeline, with on-demand downloading capabilities.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import tempfile
from typing import Any

import requests

from .common import DocumentInfoProtocol


class OpenTextClient:
    """Simple HTTP client for OpenText API with authentication."""

    def __init__(self, client_id: str, client_secret: str, api_prefix: str, app_client_id: str, app_client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_prefix = api_prefix
        self.app_client_id = app_client_id
        self.app_client_secret = app_client_secret

        # Authenticate and get access token
        auth_url = f"{self.api_prefix}/opentext/cloud/v1/auth"
        auth_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        auth_response = requests.post(auth_url, data=auth_data)
        token_dict = auth_response.json()
        access_token = token_dict["access_token"]

        # Store headers for reuse
        self.headers = {
            "authorization": f"Bearer {access_token}",
            f"{self.app_client_id}": self.app_client_secret,  # App credentials as headers
        }

    def call(self, method: str, path: str) -> Any:
        """Make an authenticated API call to OpenText."""
        api_url = f"{self.api_prefix}/{path}"

        if method.upper() == "GET":
            return requests.get(api_url, headers=self.headers)
        elif method.upper() == "POST":
            return requests.post(api_url, headers=self.headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")


@dataclass
class OpenTextDocumentInfo(DocumentInfoProtocol):
    """DocumentInfo for OpenText documents with on-demand downloading."""

    modified_at_utc: datetime
    opentext_id: int
    opentext_name: str
    opentext_api_client: OpenTextClient
    metadata: str = ""

    @property
    def local_path(self) -> Path:
        """Download document from OpenText on-demand and return local path."""
        # Call OpenText API to get content
        response = self.opentext_api_client.call("GET", f"opentext/cloud/v1/nodes/{self.opentext_id}/content")

        # Extract file extension from opentext_name
        file_suffix = Path(self.opentext_name).suffix

        # Write content to temporary file with opentext_id prefix and correct extension
        temp_file = tempfile.NamedTemporaryFile(delete=False, prefix=f"{self.opentext_id}_", suffix=file_suffix)
        temp_file.write(response.content)
        temp_file.close()

        return Path(temp_file.name)


def get_opentext_documents(
    client: OpenTextClient, *, opentext_nodes: list[int], prefix: str
) -> dict[str, OpenTextDocumentInfo]:
    """Discover OpenText documents from specified node IDs.

    Args:
        client: OpenTextClient for making API calls
        opentext_nodes: List of OpenText node IDs to process
        prefix: URI scheme prefix for the documents (e.g., 'opentext')

    Returns:
        Dictionary mapping source URIs to OpenTextDocumentInfo objects
    """
    result = {}

    for node_id in opentext_nodes:
        # Get node info from OpenText API
        response = client.call("GET", f"opentext/cloud/v1/nodes/{node_id}")
        node_data = response.json()["data"]

        node_type = node_data["type_name"]

        if node_type == "Document":
            # Handle individual document
            name = node_data["name"]
            modify_date_str = node_data["modify_date"]
            modify_date = datetime.fromisoformat(modify_date_str.replace("Z", "+00:00"))

            # Create source URI
            source_uri = f"{prefix}://{name}"

            # Create OpenTextDocumentInfo object
            doc_info = OpenTextDocumentInfo(
                modified_at_utc=modify_date, opentext_id=node_id, opentext_name=name, opentext_api_client=client
            )

            result[source_uri] = doc_info

        elif node_type == "Folder":
            # Handle folder - get children and process recursively
            children_response = client.call("GET", f"opentext/cloud/v2/nodes/{node_id}/nodes?limit=1000")
            children_data = children_response.json()["results"]

            # Extract child IDs
            child_ids = [child["data"]["properties"]["id"] for child in children_data]

            # Recursively process children
            child_documents = get_opentext_documents(client, opentext_nodes=child_ids, prefix=prefix)
            result.update(child_documents)

    return result


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
    LOGGING_LEVELS = [logging.WARNING, logging.INFO, logging.DEBUG]
    logging.basicConfig(level=LOGGING_LEVELS[min(args.verbose, len(LOGGING_LEVELS) - 1)])

    # TODO: Implement main processing logic
    raise NotImplementedError("OpenText ingestion pipeline not yet implemented")


if __name__ == "__main__":
    main()
