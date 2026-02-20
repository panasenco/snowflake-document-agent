from collections.abc import Iterable
from concurrent.futures import wait, ThreadPoolExecutor, FIRST_COMPLETED
import logging
from logging import getLogger, Logger
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, unquote_plus

import mammoth
import pandas as pd
import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import SnowflakeCursor
import yaml

snowflake.connector.paramstyle = "numeric"

ALL_TABLES = ["document_metadata", "document_text", "document_chunks"]
# See https://docs.snowflake.com/en/user-guide/snowflake-cortex/parse-document#input-requirements
CORTEX_DOCUMENT_EXTENSIONS = {"pdf", "pptx", "docx", "jpeg", "jpg", "png", "tiff", "tif", "html", "html", "txt"}


def get_console_logger(verbosity: int) -> Logger:
    """Returns a logger object set to the provided level of verbosity (0 for warn, 1 for info, 2 for debug)."""
    LOGGING_LEVELS = [logging.WARNING, logging.INFO, logging.DEBUG]
    logging_level = LOGGING_LEVELS[min(verbosity, len(LOGGING_LEVELS) - 1)]  # cap to last level index
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging_level)
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] %(message)s")
    console_handler.setFormatter(formatter)
    logger = getLogger("snowdoc")
    logger.setLevel(logging_level)
    logger.addHandler(console_handler)
    return logger


def load_config(config_path: str = "snowflake.yml") -> dict[str, Any]:
    """Load configuration from YAML file."""
    if not os.path.exists(config_path):
        raise RuntimeError(f"Error: Config file '{config_path}' not found.")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    return config.get("env", {})


def configure_connection(connection: SnowflakeConnection | str, config: dict[str, Any], /) -> SnowflakeConnection:
    """Returns a properly configured Snowflake connection object.
    If the connection is a string, treats it as a Snowflake connection_name and creates a SnowflakeConnection object.
    Either way, updates the role/warehouse/database/schema in the Snowflake connection from the config dict."""
    if isinstance(connection, str):
        connection = snowflake.connector.connect(connection_name=connection)
    # Set the config attributes
    with connection.cursor() as cursor:
        for attribute in ["role", "warehouse", "database", "schema"]:
            if attribute in config:
                cursor.execute(f"use {attribute} {config[attribute]}")
    return connection


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
    source_uri_parts = urlsplit(source_uri)
    stage_parent = (
        (unquote_plus(source_uri_parts.netloc.strip("/")) + "/" + unquote_plus(source_uri_parts.path).strip("/"))
        .strip("/")
        .encode("ascii", errors="ignore")
        .decode()
    )
    stage_parent_sanitized = stage_parent.replace("\\", "\\\\").replace("'", "\\'")
    cursor.execute(
        f"put 'file://{local_path_str}' '@documents/{stage_parent_sanitized}' auto_compress=false overwrite=true",
    )
    return (stage_parent + "/" + local_path.name) if stage_parent else local_path.name


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


def set_document_text(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    display_name: str,
    text: str,
) -> None:
    """Uploads a document's text directly to document_text for documents that don't need to be parsed."""
    cursor.execute(
        """
        insert into document_text (source_uri, display_name, document_text)
        select :1 as source_uri, :2 as display_name, :3 as document_text
        """,
        (source_uri, display_name, text),
    )


def parse_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    display_name: str,
    stage_path: str,
) -> None:
    """Parses a staged document and inserts into document_text.
    Raises RuntimeError if the parsing fails.
    """
    # Use ai_parse_document to generate the parsed text
    cursor.execute(
        """
        insert into document_text (source_uri, display_name, document_text)
        select
            :1 as source_uri,
            :2 as display_name,
            ai_parse_document(
                to_file('@documents', :3),
                {'mode': 'OCR'}
            )::string as document_text
        """,
        (source_uri, display_name, stage_path),
    )
    # Verify the content was inserted/updated successfully
    cursor.execute("SELECT substring(document_text, 1, 100) FROM document_text WHERE source_uri = :1", (source_uri,))
    result = cursor.fetchone()
    if result is None:
        raise RuntimeError(f"Document parsing for {source_uri} failed - no content found.")
    parsed_text = result[0]
    if parsed_text is None or len(parsed_text.strip()) == 0:
        raise RuntimeError(f"Document parsing for {source_uri} failed - empty content.")
    if parsed_text.strip().startswith('{"error'):
        raise RuntimeError(f"Document parsing for {source_uri} failed with error: {parsed_text}")


def generate_document_metadata(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    display_name: str,
    config: dict[str, Any],
) -> None:
    """Generates synthetic metadata for a parsed document."""
    cursor.execute(
        """
        insert into document_metadata (source_uri, display_name, generated_metadata)
        select
            :1 as source_uri,
            :2 as display_name,
            snowflake.cortex.complete(
                :3,
                'Document name: ' || :2 || chr(10) ||
                'Document URI: ' || :1 || chr(10) || chr(10) ||
                :4 || chr(10) || chr(10) ||
                '=== Document excerpt starts here ===' || chr(10)
                || substr(document_text, 1, :5) || chr(10) ||
                '=== Document excerpt ends here ==='
            ) as generated_metadata
        from document_text
        where source_uri = :1
        """,
        (
            source_uri,
            display_name,
            config["metadata_model"],
            config["metadata_prompt"],
            config["metadata_first_chars"],
        ),
    )


def chunk_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    display_name: str,
    config: dict[str, Any],
) -> None:
    """Splits documents into overlapping chunks for easier search."""
    cursor.execute(
        """
        insert into document_chunks (source_uri, display_name, contextualized_chunk)
        select
            :1 as source_uri,
            :2 as display_name,
            document_metadata.generated_metadata
            || chr(10) || chr(10) || 'Document chunk:' || chr(10)
            || chunks.value as contextualized_chunk
        from document_text
        inner join document_metadata
            on document_text.source_uri = document_metadata.source_uri,
        lateral flatten( input => snowflake.cortex.split_text_recursive_character(
            document_text.document_text,
            'none',
            :3,
            :4
        )) as chunks
        where document_text.source_uri = :1
        """,
        (source_uri, display_name, config["chunk_size"], config["chunk_overlap"]),
    )


def commit_document(
    cursor: SnowflakeCursor,
    source_uri: str,
    /,
) -> None:
    """Commits a document addition.
    Accepts an already-configured (role, warehouse, schema set) connection object.
    Deletes all previous versions of the document's rows from all tables.
    Note that previous versions are considered as sharing the locator before the query character '?'.
    E.g. "ot://my/file?v=34" and "ot://my/file?v=35" are considered different versions of the same document.
    """
    source_parts = source_uri.split("?")
    assert len(source_parts) == 2, f"Source URI {source_uri} did not have exactly one '?' character, aborting..."
    for table in ALL_TABLES:
        cursor.execute(
            f"delete from {table} where source_uri like :1 and source_uri <> :2",
            (source_parts[0] + "%", source_uri),
        )


def process_document(
    configured_connection: SnowflakeConnection,
    source_uri: str,
    display_name: str,
    downloader: Callable[[str], Path],
    config: dict[str, Any],
    logger: Logger = getLogger(),
) -> None:
    """Process a single document end-to-end."""
    local_path = downloader(source_uri)
    with configured_connection.cursor() as cursor:
        document_type = local_path.suffix.removeprefix(".").lower()
        match document_type:
            case "htm" | "html" | "txt":
                # Upload the contents directly
                logger.info(f"Uploading contents of {source_uri} directly...")
                set_document_text(cursor, source_uri=source_uri, display_name=display_name, text=local_path.read_text())
            case "xls" | "xlsx":
                # Convert Excel to HTML
                logger.info(f"Converting Excel document {source_uri} to HTML...")
                document_html = excel_to_html(local_path)
                logger.info(f"Uploading converted HTML contents of {source_uri} directly...")
                set_document_text(cursor, source_uri=source_uri, display_name=display_name, text=document_html)
            case "doc" | "docx":
                # Convert Word to HTML
                logger.info(f"Converting Word document {source_uri} to HTML...")
                document_html = word_doc_to_html(local_path)
                logger.info(f"Uploading converted HTML contents of {source_uri} directly...")
                set_document_text(cursor, source_uri=source_uri, display_name=display_name, text=document_html)
            case _:
                # Parse in Snowflake
                logger.info(f"Uploading document {source_uri} to Snowflake...")
                stage_path = stage_document(cursor, source_uri=source_uri, local_path=local_path)
                logger.info(f"Parsing document {source_uri} in Snowflake...")
                parse_document(cursor, source_uri=source_uri, display_name=display_name, stage_path=stage_path)
        # Generate synthetic metadata
        logger.info(f"Generating synthetic metadata for document {source_uri}...")
        generate_document_metadata(cursor, source_uri=source_uri, display_name=display_name, config=config)
        # Split the document into chunks
        logger.info(f"Chunking document {source_uri}...")
        chunk_document(cursor, source_uri=source_uri, display_name=display_name, config=config)
        # Commit
        logger.info(f"Processed {source_uri} - Deleting previous versions of file")
        commit_document(cursor, source_uri)


def clear_stage(configured_connection: SnowflakeConnection, /) -> None:
    """Clear the documents stage before ingesting new documents."""
    with configured_connection.cursor() as cursor:
        cursor.execute("REMOVE @documents")


def delete_document(
    configured_connection: SnowflakeConnection,
    source_uri: str,
    /,
) -> None:
    """Deletes a particular source_uri from all tables without affecting the document's other versions.
    Accepts an already-configured (role, warehouse, schema set) connection object.
    """
    with configured_connection.cursor() as cursor:
        for table in ALL_TABLES:
            cursor.execute(f"delete from {table} where source_uri = :1", (source_uri,))


def get_snowflake_documents(
    configured_connection: SnowflakeConnection,
    prefix: str,
) -> dict[str, str]:
    """Get information about the documents currently in Snowflake.
    Accepts an already-configured (role, warehouse, schema set) connection object.
    Returns a dictionary with source_uri's as the keys and display_name's as the values.
    Filter on source_uri prefixes, e.g. "local://" for URIs like "local://path/to/the/file".
    The prefix can also be used to filter for subfolders, e.g. "local://path/to".
    """
    with configured_connection.cursor() as cursor:
        # Query document_metadata table for documents matching the prefix
        cursor.execute(
            "select source_uri, display_name from document_metadata where source_uri like :1",
            (prefix + "%",),
        )
        return {row[0]: row[1] for row in cursor}


def refresh_search_services(configured_connection: SnowflakeConnection) -> None:
    """Make sure the snowflake-document-agent Cortex search services have the latest data.
    Accepts an already-configured (role, warehouse, schema set) connection object.
    """
    with configured_connection.cursor() as cursor:
        for search_service in ["search_metadata", "search_contents"]:
            cursor.execute(f"alter cortex search service if exists {search_service} refresh")


def update_display_name(
    configured_connection: SnowflakeConnection,
    source_uri: str,
    display_name: str,
    /,
) -> None:
    """Changes the display_name for a source_uri in all tables.
    Accepts an already-configured (role, warehouse, schema set) connection object.
    """
    with configured_connection.cursor() as cursor:
        for table in ALL_TABLES:
            cursor.execute(f"update {table} set display_name = :2 where source_uri = :1", (source_uri, display_name))


def process_changed_documents(
    sources: Iterable[tuple[str, str]],
    *,
    connection: SnowflakeConnection | str = "default",
    downloader: Callable[[str], Path],
    prefix: str,
    config: dict[str, Any] | None = None,
    max_workers: int = 8,
    logger: Logger = getLogger(),
) -> None:
    """Process just the documents that have changed since the last ingestion into Snowflake.
    Accepts an iterable (list or generator) of (source_uri, display_name) tuples.
    Requires the downloader callable, which accepts a source_uri and returns a local path to the corresponding document.
    Accepts a Snowflake connection as either a connection name (string) or a connection object.
    Deletes the documents matching the prefix that are only in Snowflake and no longer in the source.
    Ingests new or updated documents matching the prefix into Snowflake.
    Deletes old versions of successfully ingested documents, if any.
    """
    sources_iterator = iter(sources)
    if config is None:
        config = load_config()
    configured_connection = configure_connection(connection, config)
    logger.info(f"Getting Snowflake documents with {prefix=}...")
    targets = get_snowflake_documents(configured_connection, prefix)
    source_uris = set()
    any_processed = False
    delete_missing_uris = False
    clear_stage(configured_connection)
    with ThreadPoolExecutor(max_workers) as executor:
        future_uris = {}
        sources_remaining = True
        while sources_remaining or len(future_uris) > 0:
            if sources_remaining and len(future_uris) < max_workers:
                try:
                    logger.debug(f"Getting next source. {sources_remaining=}, {len(future_uris)=}, {max_workers=}")
                    source_uri, display_name = next(sources_iterator)
                    source_uris.add(source_uri)
                except StopIteration:
                    sources_remaining = False
                    logger.debug(f"All sources present, will delete missing. {len(source_uris)=}, {len(targets)=}")
                    delete_missing_uris = True
                except Exception as err:
                    logger.error(f"Error fetching the next source in iterator - aborting. {type(err).__name__}: {err}")
                    sources_remaining = False
                if sources_remaining:
                    if source_uri in targets:
                        logger.debug(f"Source {source_uri} already present in Snowflake - skipping processing...")
                        if targets[source_uri] != display_name:
                            logger.info(
                                f"Updating display name for {source_uri} from {targets[source_uri]} to {display_name}..."
                            )
                            update_display_name(configured_connection, source_uri, display_name)
                    else:
                        logger.info(f"Source {source_uri} not in Snowflake - submitting for processing...")
                        future = executor.submit(
                            process_document,
                            configured_connection,
                            source_uri,
                            display_name,
                            downloader,
                            config,
                            logger,
                        )
                        future_uris[future] = source_uri
            elif len(future_uris) > 0:
                logger.debug(f"Waiting for next future. {len(future_uris)=}")
                done, _ = wait(future_uris, return_when=FIRST_COMPLETED)
                for future in done:
                    source_uri = future_uris[future]
                    logger.debug(f"Future for {source_uri=} completed, deleting from future_uris and getting result.")
                    del future_uris[future]
                    try:
                        future.result()
                        any_processed = True
                    except Exception as err:
                        logger.error(
                            f"Error processing {source_uri} - deleting from database. {type(err).__name__}: {err}"
                        )
                        delete_document(configured_connection, source_uri)

    # Delete the removed documents
    deleted_uris = (set(targets) - source_uris) if delete_missing_uris else set()
    for source_uri in deleted_uris:
        logger.info(f"Deleting removed document {source_uri} from Snowflake...")
        delete_document(configured_connection, source_uri)
    # Make sure Cortex search services reflect the changes
    if deleted_uris or any_processed:
        logger.info("Finished processing documents - refreshing search services...")
        refresh_search_services(configured_connection)
        logger.info("Search services refreshed - all done!")
    else:
        logger.info("No documents processed or deleted.")
