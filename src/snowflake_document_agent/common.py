from dataclasses import dataclass
from datetime import datetime
from logging import Logger, getLogger
from concurrent.futures import as_completed, ThreadPoolExecutor
import os
from pathlib import Path
from typing import Any, Protocol

import mammoth
import pandas as pd
import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import SnowflakeCursor
import yaml

snowflake.connector.paramstyle = "numeric"

ALL_TABLES = ["document_metadata", "enhanced_metadata", "document_text", "document_chunks"]
# See https://docs.snowflake.com/en/user-guide/snowflake-cortex/parse-document#input-requirements
CORTEX_DOCUMENT_EXTENSIONS = {"pdf", "pptx", "docx", "jpeg", "jpg", "png", "tiff", "tif", "html", "txt"}


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
    cursor.execute("REMOVE @documents")


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
) -> str:
    """Stages a document from a local filepath into the Snowflake stage @documents.
    Returns the path to the document within the Snowflake stage.
    Raises ValueError if the file extension is not supported by Snowflake Cortex
    """
    if local_path.suffix.removeprefix(".").lower() not in CORTEX_DOCUMENT_EXTENSIONS:
        raise ValueError(
            f"File {local_path} has an unsupported extension. Cortex allows one of: {CORTEX_DOCUMENT_EXTENSIONS}"
        )
    local_path_str = str(local_path.absolute()).replace("\\", "\\\\").replace("'", "\\'")
    source_path = source_uri.split("://", 1)[-1]
    stage_parent = source_path.rsplit("/", 1)[0] if "/" in source_path else ""
    stage_parent_sanitized = stage_parent.replace("'", "\\'")
    cursor.execute(
        f"put 'file://{local_path_str}' '@documents/{stage_parent_sanitized}' auto_compress=false overwrite=true",
    )
    return (stage_parent + "/" + local_path.name) if stage_parent else local_path.name


def update_document_metadata(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    modified_at_utc: datetime,
    metadata: str = "",
    insert: bool,
) -> None:
    """Add (if insert=True) or update (if insert=False) a document's metadata in the table document_metadata."""
    select_stmt = """
        select
            :1 as source_uri,
            :2::timestamp_ntz as modified_at_utc,
            :3 as metadata
        """
    if insert:
        query = f"""
            insert into document_metadata (source_uri, modified_at_utc, metadata)
            {select_stmt}
            """
    else:
        query = f"""
            update document_metadata set
                modified_at_utc = updated_metadata.modified_at_utc,
                metadata = updated_metadata.metadata
            from ({select_stmt}) as updated_metadata
            where document_metadata.source_uri = updated_metadata.source_uri
            """
    cursor.execute(query, (source_uri, modified_at_utc.isoformat(), metadata))


def word_doc_to_html(local_path: Path) -> str:
    """Gets the contents a Word document (.doc/.docx) in HTML format."""
    with open(local_path, "rb") as docx_file:
        result = mammoth.convert_to_html(docx_file)
        return result.value


def excel_to_html(local_path: Path) -> str:
    """Gets the contents an Excel document (.xls/.xlsx) in HTML format."""
    # Read all worksheets
    all_sheets = pd.read_excel(local_path, sheet_name=None)

    html_content = ""
    for sheet_name, df in all_sheets.items():
        # Convert each sheet to HTML table
        sheet_html = df.to_html(table_id=f"sheet_{sheet_name}", index=False)
        html_content += sheet_html + "\n"

    return html_content


def update_document_text(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    text: str,
    insert: bool,
) -> None:
    """Uploads a document's text directly to document_text for documents that don't need to be parsed."""
    select_stmt = """
        select
            :1 as source_uri,
            :2 as document_text
        """
    if insert:
        query = f"""
            insert into document_text (source_uri, document_text)
            {select_stmt}
            """
    else:
        query = f"""
            update document_text set
                document_text = updated_text.document_text
            from ({select_stmt}) as updated_text
            where document_text.source_uri = updated_text.source_uri
            """
    cursor.execute(query, (source_uri, text))


def parse_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    stage_path: str,
    insert: bool,
) -> None:
    """Parses a staged document and inserts into document_text.
    Raises RuntimeError if the parsing fails.
    """
    select_stmt = """
        select
            :1 as source_uri,
            AI_PARSE_DOCUMENT(
                TO_FILE('@documents', :2),
                {'mode': 'OCR'}
            )::string as document_text
        """

    if insert:
        query = f"""
            insert into document_text (source_uri, document_text)
            {select_stmt}
        """
    else:
        query = f"""
            update document_text set
                document_text = updated_text.document_text
            from ({select_stmt}) as updated_text
            where document_text.source_uri = updated_text.source_uri
            """

    cursor.execute(query, (source_uri, stage_path))

    # Verify the content was inserted/updated successfully
    cursor.execute("SELECT document_text FROM document_text WHERE source_uri = :1", (source_uri,))
    result = cursor.fetchone()

    if result is None:
        raise RuntimeError(f"Document parsing failed - no content found for {source_uri}")

    parsed_text = result[0]
    if parsed_text is None or len(parsed_text.strip()) == 0:
        raise RuntimeError(f"Document parsing failed - empty content for {source_uri}")

    # Check if AI_PARSE_DOCUMENT returned an error message in JSON format
    if parsed_text.strip().startswith('{"error'):
        raise RuntimeError(f"Document parsing failed for {source_uri}: {parsed_text}")


def generate_document_metadata(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """Generates synthetic metadata for a parsed document."""
    select_stmt = f"""
        select
            :1 as source_uri,
            snowflake.cortex.complete(
                '{config["metadata_model"]}',
                '{config["metadata_prompt"].replace("'", "''").replace(chr(10), chr(92) + "n")}'
                || chr(10) || chr(10) || 'Doc starts here:' || chr(10)
                || substr(document_text, 1, {config["metadata_first_chars"]})
                || chr(10) || 'Doc ends here' || chr(10) || chr(10)
            ) as enhanced_metadata
        from document_text
        where source_uri = :1
        """

    if insert:
        query = f"""
            insert into enhanced_metadata (source_uri, enhanced_metadata)
            {select_stmt}
        """
    else:
        query = f"""
            update enhanced_metadata set
                enhanced_metadata = updated_metadata.enhanced_metadata
            from ({select_stmt}) as updated_metadata
            where enhanced_metadata.source_uri = updated_metadata.source_uri
        """
    cursor.execute(query, (source_uri,))


def chunk_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """Splits documents into overlapping chunks for easier search."""
    select_stmt = f"""
        select
            :1 as source_uri,
            enhanced_metadata.enhanced_metadata
            || chr(10) || chr(10) || 'Document chunk:' || chr(10)
            || chunks.value as contextualized_chunk
        from document_text
        join enhanced_metadata
            on document_text.source_uri = enhanced_metadata.source_uri,
        lateral flatten( input => snowflake.cortex.split_text_recursive_character(
            document_text.document_text,
            'none',
            {config["chunk_size"]},
            {config["chunk_overlap"]}
        )) as chunks
        where document_text.source_uri = :1
        """

    if insert:
        query = f"""
            insert into document_chunks (source_uri, contextualized_chunk)
            {select_stmt}
            """
    else:
        query = f"""
            update document_chunks set
                contextualized_chunk = updated_chunks.contextualized_chunk
            from ({select_stmt}) as updated_chunks
            where document_chunks.source_uri = updated_chunks.source_uri
            """
    cursor.execute(query, (source_uri,))


def process_document(
    connection: SnowflakeConnection,
    source_uri: str,
    source_info: DocumentInfo,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """Process a single document end-to-end."""
    with connection.cursor() as cursor:
        document_type = source_info.local_path.suffix.removeprefix(".").lower()
        match document_type:
            case "html" | "txt":
                # Upload the contents directly
                update_document_text(
                    cursor, source_uri=source_uri, text=source_info.local_path.read_text(), insert=insert
                )
            case "xls" | "xlsx":
                # Convert Excel to HTML
                document_html = excel_to_html(source_info.local_path)
                update_document_text(cursor, source_uri=source_uri, text=document_html, insert=insert)
            case "doc" | "docx":
                # Convert Word to HTML
                document_html = word_doc_to_html(source_info.local_path)
                update_document_text(cursor, source_uri=source_uri, text=document_html, insert=insert)
            case _:
                # Parse in Snowflake
                stage_path = stage_document(cursor, source_uri=source_uri, local_path=source_info.local_path)
                parse_document(cursor, source_uri=source_uri, stage_path=stage_path, insert=insert)
        generate_document_metadata(cursor, source_uri=source_uri, config=config, insert=insert)
        chunk_document(cursor, source_uri=source_uri, config=config, insert=insert)


def process_documents(
    sources: list[tuple[str, DocumentInfoProtocol, bool]],
    *,
    connection: SnowflakeConnection,
    config: dict[str, Any] | None = None,
    max_workers: int = 8,
    logger: Logger = getLogger(),
) -> None:
    """Process documents end-to-end in parallel.
    Requires a list of (source_uri, DocumentInfo, insert) tuples.
    Accepts a SnowflakeConnection object.
    Uses multithreading to reuse the connection object.
    Without multithreading, restrictive corporate environments that only allow browser based auth would open an SSO
    browser tab for each document processed.
    Displays a progress bar.
    Doesn't crash when a document fails to process, but displays detailed information about the error.
    """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_document, connection, source_uri, source_info, config, insert): source_uri
            for (source_uri, source_info, insert) in sources
        }
    for future in as_completed(futures):
        source_uri = futures[future]
        try:
            future.result()
        except Exception as err:
            logger.error(f"Error processing {source_uri}: {type(err).__name__} - {err}")
