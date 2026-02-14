from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Protocol

from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import SnowflakeCursor
import yaml


class DocumentInfoProtocol(Protocol):
    """Protocol for document information objects."""

    modified_at_utc: datetime
    metadata: str
    local_path: Path | None


@dataclass
class DocumentInfo(DocumentInfoProtocol):
    modified_at_utc: datetime
    local_path: Path | None = None
    metadata: str = ""


# Constants
ALL_TABLES = ["document_metadata", "enhanced_metadata", "parsed_documents", "document_chunks"]


def load_config(config_path: str = "snowflake.yml") -> dict[str, Any]:
    """Load configuration from YAML file."""
    if not os.path.exists(config_path):
        raise RuntimeError(f"Error: Config file '{config_path}' not found.")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    return config.get("env", {})


def create_cursor(conn: SnowflakeConnection, config: dict[str, Any], /) -> SnowflakeCursor:
    """Create a Snowflake cursor with the configured context set."""
    cursor = conn.cursor()
    for attribute in ["role", "warehouse", "database", "schema"]:
        if attribute in config:
            cursor.execute(f"use {attribute} {config[attribute]}")
    return cursor


def clear_stage(cursor: SnowflakeCursor) -> None:
    """Clear the documents stage before ingesting new documents."""


def delete_documents(
    conn: SnowflakeConnection,
    *,
    deleted_uris: set[str],
    config: dict[str, Any],
) -> None:
    """Batch delete documents.
    Note that document deletion doesn't need to be parallelized.
    """


def stage_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    local_path: Path,
    modified_at_utc: datetime,
    insert: bool,
    metadata: str = "",
) -> str:
    """Stages a document from a local filepath into the Snowflake stage @documents.
    Writes the metadata into the table document_metadata.
    Returns the path to the document within the Snowflake stage.
    """


def parse_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    stage_path: str,
    insert: bool,
) -> None:
    """Parses a staged document and inserts into parsed_documents.
    Raises RuntimeError if the parsing fails.
    """


def generate_document_metadata(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """Generates synthetic metadata for a parsed document."""


def chunk_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """Splits documents into overlapping chunks for easier search."""


def process_document(
    conn: SnowflakeConnection,
    *,
    source_uri: str,
    source_info: DocumentInfo,
    prefix: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """Process a single document end-to-end."""


def process_documents(
    sources: dict[str, DocumentInfoProtocol],
    *,
    prefix: str,
    snowflake_connection: SnowflakeConnection | str = "default",
    config: dict[str, Any] | None = None,
) -> None:
    """Process documents end-to-end in parallel.
    Accepts a SnowflakeConnection object or Snowflake connection name (if string).
    Displays a progress bar.
    Doesn't crash when a document fails to process, but displays detailed information about the error.
    """
