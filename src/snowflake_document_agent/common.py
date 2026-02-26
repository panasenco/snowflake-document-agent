from argparse import ArgumentParser, Namespace
from collections.abc import Iterable
from concurrent.futures import wait, ThreadPoolExecutor, FIRST_COMPLETED
from hashlib import sha1
import json
import logging
from logging import getLogger, Logger
from operator import add
import os
from pathlib import Path
import re
import subprocess
from traceback import print_exception
from typing import Any, Callable
from urllib.parse import urlsplit, unquote_plus

from charset_normalizer import from_path
import lxml.html
import lxml.html.clean
import mammoth
import pandas as pd
import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import SnowflakeCursor
import yaml

snowflake.connector.paramstyle = "numeric"

ALL_TABLES = ["document_metadata", "document_text", "document_chunks"]
# See https://docs.snowflake.com/en/user-guide/snowflake-cortex/parse-document#input-requirements
CORTEX_DOCUMENT_EXTENSIONS = {"pdf", "pptx", "docx", "jpeg", "jpg", "png", "tiff", "tif", "htm", "html", "txt"}


class ErrorCaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[tuple[str, Any]] = []

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.records.append((record.msg, record.exc_info))


def get_console_logger(verbosity: int) -> Logger:
    """Returns a logger object set to the provided level of verbosity (0 for warn, 1 for info, 2 for debug)."""
    LOGGING_LEVELS = [logging.WARNING, logging.INFO, logging.DEBUG]
    logging_level = LOGGING_LEVELS[min(verbosity, len(LOGGING_LEVELS) - 1)]  # cap to last level index
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging_level)
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] %(message)s")
    console_handler.setFormatter(formatter)
    logger = getLogger("snowflake-document-agent")
    logger.setLevel(logging_level)
    logger.addHandler(console_handler)
    logger.addHandler(ErrorCaptureHandler())
    return logger


def parse_common_options(parser: ArgumentParser) -> tuple[Namespace, dict[str, Any], Logger]:
    """Convenience function for common options and outputs to a parser object.
    1. Adds common arguments to the parser.
    2. Parses the arguments.
    3. Returns a tuple (parser_arguments, process_changed_documents_params, logger)
    NOTE: Do not use -c, -d, -u, or -v in ingester-specific arguments!
    """
    parser.add_argument(
        "-c", "--snowflake-connection", default="default", help="Name of Snowflake connection to use (default: default)"
    )
    parser.add_argument(
        "-d",
        "--delete-missing",
        action="store_true",
        help="Set to delete Snowflake documents matching the prerix that are not in the source. The default behavior is to add and update only.",
    )
    parser.add_argument(
        "-u",
        "--update-display-names",
        action="store_true",
        help="Set to update the display names of already-present documents. The default behavior is to not update the display names.",
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
    process_changed_documents_params = {
        "connection": args.snowflake_connection,
        "delete_missing": args.delete_missing,
        "update_display_names": args.update_display_names,
        "logger": logger,
    }
    return args, process_changed_documents_params, logger


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
    table_prefix: str,
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
        f"put 'file://{local_path_str}' '@{table_prefix}documents/{stage_parent_sanitized}' auto_compress=false overwrite=true",
    )
    return (stage_parent + "/" + local_path.name) if stage_parent else local_path.name


def doc_to_text(doc_path: Path) -> str:
    """Parse a .doc file to text using antiword.
    Accepts the path to the .doc (Word 1997-2003) file.
    Returns extracted text content
    """
    result = subprocess.run(["antiword", "-f", str(doc_path)], capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        error_msg = f"antiword failed with return code {result.returncode}"
        if result.stderr:
            error_msg += f"\nstderr: {result.stderr}\n"
        if result.stdout:
            error_msg += f"\nstdout: {result.stdout}"
        raise RuntimeError(error_msg)

    return result.stdout


def docx_to_html(local_path: Path) -> str:
    """Gets the contents a .docx (NOT .doc) Word document in HTML format."""
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


def clean_html(local_path: Path) -> str:
    """Cleans the contents of an HTML file and extracts just the meaningful content HTML.
    Automatically detects encoding (handles UTF-8, UTF-16, etc.)."""
    # Detect encoding automatically
    detected = from_path(local_path).best()
    if detected is None:
        raise RuntimeError(f"Could not detect best encoding for {local_path}")
    html_content = str(detected)

    html_doc = lxml.html.fromstring(html_content)
    html_cleaner = lxml.html.clean.Cleaner(
        scripts=True,
        javascript=True,
        comments=True,
        style=True,
        inline_style=True,
        links=True,
        meta=True,
        page_structure=True,
        processing_instructions=True,
        embedded=True,
        frames=True,
        forms=True,
        annoying_tags=True,
        remove_tags=["a", "span"],
        kill_tags=["img", "script", "style"],
        safe_attrs_only=True,
        safe_attrs=["colspan", "rowspan", "headers", "scope"],
    )
    cleaned_doc = html_cleaner.clean_html(html_doc)

    # Remove empty tags (e.g., <b></b>, <p></p>, <p> </p>)
    # Collect elements to remove first to avoid iterator issues
    while True:
        empty_elements = []
        for element in cleaned_doc.iter():
            # Check if element is empty or contains only whitespace
            has_text = element.text is not None and element.text.strip()
            has_tail = element.tail is not None and element.tail.strip()
            has_children = len(element) > 0
            if not has_text and not has_tail and not has_children:
                empty_elements.append(element)
        if not empty_elements:
            break  # No more empty elements found
        for element in empty_elements:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)

    cleaned_html = lxml.html.tostring(cleaned_doc, encoding="unicode", pretty_print=True)
    # Strip out carriage returns, repeating newlines, and multiple spaces
    cleaned_html = cleaned_html.replace("\r\n", "\n")
    cleaned_html = re.sub(r" {2,}", " ", cleaned_html)
    cleaned_html = re.sub(r"\n{2,}", "\n", cleaned_html)
    return cleaned_html


def set_document_text(
    cursor: SnowflakeCursor,
    *,
    table_prefix: str,
    source_uri: str,
    text: str,
) -> None:
    """Uploads a document's text directly to document_text for documents that don't need to be parsed."""
    cursor.execute(
        f"""
        insert into {table_prefix}document_text (source_uri, document_text)
        select :1 as source_uri, :2 as document_text
        """,
        (source_uri, text),
    )


def parse_document(
    cursor: SnowflakeCursor,
    *,
    table_prefix: str,
    source_uri: str,
    stage_path: str,
) -> None:
    """Parses a staged document and inserts into document_text.
    Raises RuntimeError if the parsing fails.
    """
    # Use ai_parse_document to generate the parsed text
    cursor.execute(
        f"""
        insert into {table_prefix}document_text (source_uri, document_text)
        with parsed_document as (
            select
                ai_parse_document(
                    to_file('@{table_prefix}documents', :2),
                    {{'mode': 'OCR'}}
                )::string as parsed_text
        )
        select
            :1 as source_uri,
            case
                when startswith(parsed_text, '{{"content":"') then json_extract_path_text(parsed_text, 'content')
                else parsed_text
            end as document_text
        from parsed_document
        """,
        (source_uri, stage_path),
    )
    # Verify the content was inserted/updated successfully
    cursor.execute(
        f"SELECT substring(document_text, 1, 1000) FROM {table_prefix}document_text WHERE source_uri = :1",
        (source_uri,),
    )
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
    table_prefix: str,
    source_uri: str,
    display_name: str,
    metadata_config_hash: str,
    metadata_model: str,
    metadata_prompt: str,
    metadata_first_chars: int,
) -> None:
    """Generates synthetic metadata for a parsed document."""
    cursor.execute(
        f"""
        insert into {table_prefix}document_metadata (source_uri, display_name, metadata_config_hash, generated_metadata)
        select
            :1 as source_uri,
            :2 as display_name,
            :3 as metadata_config_hash,
            ai_complete(
                model => :4,
                prompt => :5 || chr(10) || chr(10) ||
                '=== Document name: ' || :2 || chr(10) ||
                '=== Document URI: ' || :1 || chr(10) || chr(10) ||
                '=== Document excerpt starts here ===' || chr(10)
                || substr(document_text, 1, :6) || chr(10) ||
                '=== Document excerpt ends here ===',
                model_parameters => {{
                    'temperature': 0,
                    'max_tokens': 1024
                }},
                show_details => false
            ) as generated_metadata
        from {table_prefix}document_text
        where source_uri = :1
        """,
        (
            source_uri,
            display_name,
            metadata_config_hash,
            metadata_model,
            metadata_prompt,
            metadata_first_chars,
        ),
    )


def chunk_document(
    cursor: SnowflakeCursor,
    *,
    table_prefix: str,
    source_uri: str,
    display_name: str,
    chunk_config_hash: str,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    """Splits documents into overlapping chunks for easier search."""
    cursor.execute(
        f"""
        insert into {table_prefix}document_chunks (
            source_uri,
            display_name,
            chunk_config_hash,
            chunk_index,
            document_chunk
        )
        select
            :1 as source_uri,
            :2 as display_name,
            :3 as chunk_config_hash,
            chunks.index as chunk_index,
            chunks.value as document_chunk
        from {table_prefix}document_text as document_text,
        lateral flatten( input => snowflake.cortex.split_text_recursive_character(
            document_text.document_text,
            'none',
            :4,
            :5
        )) as chunks
        where document_text.source_uri = :1
        """,
        (source_uri, display_name, chunk_config_hash, chunk_size, chunk_overlap),
    )


def delete_rows(
    cursor: SnowflakeCursor,
    table_filters: dict[str, tuple[Any]],
    /,
    *,
    table_prefix: str,
) -> None:
    """Deletes rows from tables matching provided filters and params.
    The first element in the tuple is the filter string, and the remaining elements are the params.
    """
    for table, params in table_filters.items():
        cursor.execute(
            f"delete from {table_prefix}{table} where {params[0]}",
            params[1:],
        )


def get_source_pattern(source_uri: str, /) -> str:
    """Returns a Snowflake like-pattern matching just the immutable part of a source URI."""
    source_parts = source_uri.split("?")
    assert len(source_parts) == 2, f"Source URI {source_uri} did not have exactly one '?' character, aborting..."
    return source_parts[0] + "%"


def dict_hash(d: dict[str, Any], /) -> str:
    """Compute the first 7 characters of the SHA1 hash of the json form of a Python dict."""
    return sha1(json.dumps(d).encode()).hexdigest()[:7]


def delete_document(
    cursor: SnowflakeCursor,
    source_uri: str,
    /,
    *,
    table_prefix: str,
    source_uri_new: bool = False,
    metadata_config_hash: str | None = None,
    chunk_config_hash: str | None = None,
) -> None:
    if source_uri_new:
        delete_rows(
            cursor,
            {"document_text": ("source_uri = :1", source_uri)},
            table_prefix=table_prefix,
        )
    if metadata_config_hash:
        delete_rows(
            cursor,
            {
                "document_metadata": (
                    "source_uri = :1 and metadata_config_hash = :2",
                    source_uri,
                    metadata_config_hash,
                ),
            },
            table_prefix=table_prefix,
        )
    if chunk_config_hash:
        delete_rows(
            cursor,
            {"document_chunks": ("source_uri = :1 and chunk_config_hash = :2", source_uri, chunk_config_hash)},
            table_prefix=table_prefix,
        )


def process_document(
    configured_connection: SnowflakeConnection,
    source_uri: str,
    display_name: str,
    downloader: Callable[[str], Path],
    table_prefix: str,
    metadata_config: dict[str, Any],
    metadata_config_hash: str,
    chunk_config: dict[str, Any],
    chunk_config_hash: str,
    update_display_name: bool = False,
    logger: Logger = getLogger(),
) -> tuple[int, int, int]:
    """Process a single document end-to-end. Returns a (processed, skipped, failed) count tuple."""
    name = f"{source_uri} ({display_name})"  # Convenient shorthand for document name
    processed = False
    local_path = downloader(source_uri)
    source_uri_new = False
    metadata_config_new = False
    chunk_config_new = False
    with configured_connection.cursor() as cursor:
        # Only reload/reparse if the uri is not already present in the document_text table
        cursor.execute(f"select count(*) from {table_prefix}document_text where source_uri = :1", (source_uri,))
        if cursor.fetchone()[0] == 0:
            source_uri_new = True
            try:
                document_type = local_path.suffix.removeprefix(".").lower()
                match document_type:
                    case "txt":
                        # Upload the contents directly
                        logger.info(f"Uploading contents of {name} directly...")
                        set_document_text(
                            cursor,
                            table_prefix=table_prefix,
                            source_uri=source_uri,
                            text=str(from_path(local_path).best()),
                        )
                    case "htm" | "html":
                        # The HTML files are often Word exports with a bunch of fluff, so clean before uploading
                        logger.info(f"Cleaning and uploading contents of {name}...")
                        set_document_text(
                            cursor,
                            table_prefix=table_prefix,
                            source_uri=source_uri,
                            text=clean_html(local_path),
                        )
                    case "xls" | "xlsx" | "xlsm":
                        # Convert Excel to HTML
                        logger.info(f"Converting Excel document {name} to HTML...")
                        document_html = excel_to_html(local_path)
                        logger.info(f"Uploading converted HTML contents of {name} directly...")
                        set_document_text(
                            cursor,
                            table_prefix=table_prefix,
                            source_uri=source_uri,
                            text=document_html,
                        )
                    case "doc":
                        # Convert doc format (Word 1997) to text
                        logger.info(f"Converting doc (Word 1997) document {name} to text...")
                        document_text = doc_to_text(local_path)
                        logger.info(f"Uploading converted text contents of {name} directly...")
                        set_document_text(
                            cursor,
                            table_prefix=table_prefix,
                            source_uri=source_uri,
                            text=document_text,
                        )
                    case "docx":
                        # Convert docx format (Word 2007 onward) to HTML
                        logger.info(f"Converting docx document {name} to HTML...")
                        document_html = docx_to_html(local_path)
                        logger.info(f"Uploading converted HTML contents of {name} directly...")
                        set_document_text(
                            cursor,
                            table_prefix=table_prefix,
                            source_uri=source_uri,
                            text=document_html,
                        )
                    case _:
                        # Parse in Snowflake
                        logger.info(f"Uploading document {name} to Snowflake...")
                        stage_path = stage_document(
                            cursor, table_prefix=table_prefix, source_uri=source_uri, local_path=local_path
                        )
                        logger.info(f"Parsing document {name} in Snowflake...")
                        parse_document(
                            cursor,
                            table_prefix=table_prefix,
                            source_uri=source_uri,
                            stage_path=stage_path,
                        )
                processed = True
            except Exception:
                logger.exception(f"Error uploading or parsing {name} - removing version.")
                delete_document(
                    cursor,
                    source_uri,
                    table_prefix=table_prefix,
                    source_uri_new=True,
                )
                return (0, 0, 1)
        # Only recompute the metadata if the uri+config hash is not already present in the document_metadata table
        cursor.execute(
            f"""
            select display_name from {table_prefix}document_metadata
            where source_uri = :1 and metadata_config_hash = :2
            """,
            (source_uri, metadata_config_hash),
        )
        current_metadata_row = cursor.fetchone()
        if current_metadata_row is None:
            metadata_config_new = True
            # Generate synthetic metadata
            try:
                logger.info(f"Generating synthetic metadata for document {name}...")
                generate_document_metadata(
                    cursor,
                    table_prefix=table_prefix,
                    source_uri=source_uri,
                    display_name=display_name,
                    metadata_config_hash=metadata_config_hash,
                    **metadata_config,
                )
                processed = True
            except Exception:
                logger.exception(f"Error generating metadata for {name} - removing version.")
                delete_document(
                    cursor,
                    source_uri,
                    table_prefix=table_prefix,
                    source_uri_new=source_uri_new,
                    metadata_config_hash=metadata_config_hash if metadata_config_new else None,
                )
                return (0, 0, 1)
        elif update_display_name and current_metadata_row[0] != display_name:
            # Update just the display name
            logger.info(f"Updating the display name of {source_uri}: {current_metadata_row[0]} -> {display_name}...")
            for table in ["document_metadata", "document_chunks"]:
                cursor.execute(
                    f"update {table_prefix}{table} set display_name = :2 where source_uri = :1",
                    (source_uri, display_name),
                )
            processed = True
        # Only recompute the chunks if the uri+config hash is not already present in the document_chunks table
        cursor.execute(
            f"""
            select count(*) from {table_prefix}document_chunks
            where source_uri = :1 and chunk_config_hash = :2
            """,
            (source_uri, chunk_config_hash),
        )
        if cursor.fetchone()[0] == 0:
            chunk_config_new = True
            # Split the document into chunks
            try:
                logger.info(f"Chunking document {name}...")
                chunk_document(
                    cursor,
                    table_prefix=table_prefix,
                    source_uri=source_uri,
                    display_name=display_name,
                    chunk_config_hash=chunk_config_hash,
                    **chunk_config,
                )
                processed = True
            except Exception:
                logger.exception(f"Error generating chunks for {name} - removing version.")
                delete_document(
                    cursor,
                    source_uri,
                    table_prefix=table_prefix,
                    source_uri_new=source_uri_new,
                    metadata_config_hash=metadata_config_hash if metadata_config_new else None,
                    chunk_config_hash=chunk_config_hash if chunk_config_new else None,
                )
                return (0, 0, 1)
        if processed:
            # Commit the changes
            logger.info(f"Processed {name} - Deleting previous versions of text/metadata/chunks")
            source_pattern = get_source_pattern(source_uri)
            delete_rows(
                cursor,
                {
                    "document_text": ("source_uri like :1 and not (source_uri = :2)", source_pattern, source_uri),
                    "document_metadata": (
                        "source_uri like :1 and not (source_uri = :2 and metadata_config_hash = :3)",
                        source_pattern,
                        source_uri,
                        metadata_config_hash,
                    ),
                    "document_chunks": (
                        "source_uri like :1 and not (source_uri = :2 and chunk_config_hash = :3)",
                        source_pattern,
                        source_uri,
                        chunk_config_hash,
                    ),
                },
                table_prefix=table_prefix,
            )
            return (1, 0, 0)
    logger.debug(f"No changes detected in {name} - skipped processing...")
    return (0, 1, 0)


def clear_stage(configured_connection: SnowflakeConnection, /, *, table_prefix: str) -> None:
    """Clear the documents stage before ingesting new documents."""
    with configured_connection.cursor() as cursor:
        cursor.execute(f"REMOVE @{table_prefix}documents")


def refresh_search_services(configured_connection: SnowflakeConnection, /, *, table_prefix: str) -> None:
    """Make sure the snowflake-document-agent Cortex search services have the latest data.
    Accepts an already-configured (role, warehouse, schema set) connection object.
    """
    search_services = ["search_metadata", "search_contents"]
    with ThreadPoolExecutor(len(search_services)) as executor:
        with configured_connection.cursor() as cursor:
            executor.map(
                cursor.execute,
                [
                    f"alter cortex search service if exists {table_prefix}{search_service} refresh"
                    for search_service in search_services
                ],
            )


def process_changed_documents(
    sources: Iterable[tuple[str, str]],
    *,
    connection: SnowflakeConnection | str = "default",
    downloader: Callable[[str], Path],
    prefix: str,
    config: dict[str, Any] | None = None,
    max_workers: int = 8,
    delete_missing: bool = False,
    update_display_names: bool = False,
    logger: Logger = getLogger(),
) -> tuple[int, int, int]:
    """Process just the documents that have changed since the last ingestion into Snowflake.
    Accepts an iterable (list or generator) of (source_uri, display_name) tuples.
    Requires the downloader callable, which accepts a source_uri and returns a local path to the corresponding document.
    Accepts a Snowflake connection as either a connection name (string) or a connection object.
    Ingests new or updated documents matching the prefix into Snowflake.
    Deletes old versions of successfully ingested documents, if any.
    If delete_missing is set, deletes matching documents that are only in Snowflake and are no longer in the source.
    If update_display_names is set, updates the display names of otherwise unchanged documents.
    Returns a (processed, skipped, failed) count tuple.
    """
    sources_iterator = iter(sources)
    if config is None:
        config = load_config()
    table_prefix = config["agent_name"].lower() + "_"
    # Collect the metadata config and compute its hash
    metadata_config = {key: config[key] for key in ["metadata_model", "metadata_prompt", "metadata_first_chars"]}
    metadata_config_hash = dict_hash(metadata_config)
    # Collect the chunk config and compute its hash
    chunk_config = {key: config[key] for key in ["chunk_size", "chunk_overlap"]}
    chunk_config_hash = dict_hash(chunk_config)
    # Ensure the Snowflake connection has the correct database/schema/warehouse
    configured_connection = configure_connection(connection, config)
    source_uris = set()
    n_processed, n_skipped, n_failed = 0, 0, 0
    process_sources = True
    all_sources_fetched = False
    clear_stage(configured_connection, table_prefix=table_prefix)
    with ThreadPoolExecutor(max_workers) as executor:
        future_uris = {}
        while process_sources or len(future_uris) > 0:
            if process_sources and len(future_uris) < max_workers:
                try:
                    logger.debug(
                        f"Getting next source. {process_sources=}, {list(future_uris.values())=}, {max_workers=}"
                    )
                    source_uri, display_name = next(sources_iterator)
                except StopIteration:
                    process_sources = False
                    logger.debug(f"All sources fetched. {len(source_uris)=}")
                    all_sources_fetched = True
                except Exception:
                    logger.exception("Unexpected error fetching the next source in the sources iterator - aborting.")
                    n_failed += 1
                    process_sources = False
                if process_sources:
                    if source_uri is None:
                        logger.info("Failed to process a source, incrementing the failed counter")
                        n_failed += 1
                    elif source_uri in source_uris:
                        logger.warning(
                            f"The URI of source {source_uri} ({display_name}) was already encountered - skipping!"
                        )
                        n_skipped += 1
                    else:
                        source_uris.add(source_uri)
                        logger.info(f"Submitting {source_uri} for processing...")
                        future = executor.submit(
                            process_document,
                            configured_connection,
                            source_uri,
                            display_name,
                            downloader,
                            table_prefix,
                            metadata_config,
                            metadata_config_hash,
                            chunk_config,
                            chunk_config_hash,
                            update_display_names,
                            logger,
                        )
                        future_uris[future] = source_uri
            elif len(future_uris) > 0:
                logger.debug(f"Waiting for next future. {list(future_uris.values())=}")
                done, _ = wait(future_uris, return_when=FIRST_COMPLETED)
                for future in done:
                    source_uri = future_uris[future]
                    logger.debug(f"Future for {source_uri=} completed, deleting from future_uris and getting result.")
                    del future_uris[future]
                    try:
                        result = future.result()
                        n_processed, n_skipped, n_failed = tuple(map(add, (n_processed, n_skipped, n_failed), result))
                    except Exception:
                        n_failed += 1
                        logger.exception(
                            f"Unexpected error processing {source_uri} - duplicates might be left in database."
                        )

    # Delete the removed documents
    deleted_uris = set()
    if delete_missing and all_sources_fetched:
        with configured_connection.cursor() as cursor:
            # Get all URIs in Snowflake
            cursor.execute(
                " union ".join(f"select source_uri from {table_prefix}{table_name}" for table_name in ALL_TABLES)
            )
            target_uris = {row[0] for row in cursor.fetchall()}
            deleted_uris = target_uris - source_uris
            for source_uri in deleted_uris:
                logger.info(f"Deleting removed document {source_uri} from Snowflake...")
                source_pattern = get_source_pattern(source_uri)
                delete_rows(
                    cursor,
                    {table_name: ("source_uri like :1", source_pattern) for table_name in ALL_TABLES},
                    table_prefix=table_prefix,
                )
    # Make sure Cortex search services reflect the changes
    if deleted_uris or n_processed > 0:
        logger.info("Finished processing documents - refreshing search services...")
        refresh_search_services(configured_connection, table_prefix=table_prefix)
        logger.info("Search services refreshed - all done!")
    else:
        logger.info("No documents processed or deleted.")
    # Summarize
    error_capture_handlers = [handler for handler in logger.handlers if isinstance(handler, ErrorCaptureHandler)]
    if len(error_capture_handlers) > 0:
        print("=== ERRORS ===")
        for logger_message, exc_info in error_capture_handlers[0].records:
            if exc_info:
                print(f"{logger_message}. {exc_info[0].__name__}: {exc_info[1]}")
                if logger.level <= logging.INFO:
                    print_exception(*exc_info)
                print("------")
            else:
                print(logger_message)
                print("------")
    print("=== SUMMARY ===")
    print(f"Processed: {n_processed}; Skipped: {n_skipped}; Failed: {n_failed}.")
    return (n_processed, n_skipped, n_failed)
