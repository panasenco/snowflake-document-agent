from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Protocol

import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import SnowflakeCursor
import yaml


class DocumentInfoProtocol(Protocol):
    """Protocol for document information objects."""

    modified_at_utc: datetime
    metadata: str
    local_path: Path | None


snowflake.connector.paramstyle = "numeric"

ALL_TABLES = ["document_metadata", "enhanced_metadata", "parsed_documents", "document_chunks"]


@dataclass
class DocumentInfo(DocumentInfoProtocol):
    modified_at_utc: datetime
    local_path: Path | None = None
    metadata: str = ""


def load_config(config_path: str = "snowflake.yml") -> dict[str, Any]:
    if not os.path.exists(config_path):
        raise RuntimeError(f"Error: Config file '{config_path}' not found.")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    return config.get("env", {})


def create_cursor(conn: SnowflakeConnection, config: dict[str, Any], /) -> SnowflakeCursor:
    cursor = conn.cursor()
    for attribute in ["role", "warehouse", "database", "schema"]:
        if attribute in config:
            cursor.execute(f"use {attribute} {config[attribute]}")
    return cursor


def get_snowflake_documents(
    conn: snowflake.connector.SnowflakeConnection, *, prefix: str, config: dict[str, Any]
) -> dict[str, DocumentInfo]:
    with create_cursor(conn, config) as cursor:
        cursor.execute(
            "select source_uri, modified_at_utc from document_metadata where startswith(source_uri, :1)",
            (f"{prefix}://",),
        )
        return {
            source_uri: DocumentInfo(modified_at_utc=modified_at_utc.replace(tzinfo=timezone.utc))
            for source_uri, modified_at_utc in cursor
        }


def create_temporary_updated_uris(conn: SnowflakeConnection, /, *, config: dict[str, Any]) -> None:
    with create_cursor(conn, config) as cursor:
        cursor.execute("create or replace temporary table updated_uris(source_uri string, stage_path string)")


def stage_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    local_path: Path,
    modified_at_utc: datetime,
    insert: bool,
    metadata: str = "",
) -> None:
    # Upload the document to Snowflake
    local_path_str = str(local_path.absolute()).replace("\\", "\\\\").replace("'", "\\'")
    stage_path = source_uri.split("://", 1)[-1]
    stage_parent = stage_path.rsplit("/", 1)[0].replace("'", "\\'") if "/" in stage_path else ""
    cursor.execute(
        f"put 'file://{local_path_str}' '@documents/{stage_parent}' auto_compress=false overwrite=true",
    )
    # Add to updated_uris
    cursor.execute("insert into updated_uris(source_uri, stage_path) values (:1, :2)", (source_uri, stage_path))
    # Update the modification timestamp and the metadata string
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


def parse_documents(cursor: SnowflakeCursor, *, prefix: str, insert: bool) -> None:
    """
    Parses all documents from the stage and inserts into parsed_documents.
    Raises RuntimeError if any document parsing fails.
    """
    select_stmt = """
        select
            source_uri,
            snowflake.cortex.parse_document(
                '@documents',
                stage_path,
                {'mode': 'OCR'}
            )::string as parsed_content
        from updated_uris
        """

    if insert:
        query = f"""
            insert into parsed_documents (source_uri, parsed_content)
            {select_stmt}
        """
    else:
        query = f"""
            update parsed_documents set
                parsed_content = updated_documents.parsed_content
            from ({select_stmt}) as updated_documents
            where parsed_documents.source_uri = updated_documents.source_uri
            """
    cursor.execute(query)

    # After insertion, check for parsing errors
    cursor.execute("""
        SELECT COUNT(*)
        FROM parsed_documents
        WHERE source_uri IN (SELECT source_uri FROM updated_uris)
        AND parsed_content LIKE '{"error%'
    """)

    error_count = cursor.fetchone()[0]

    if error_count > 0:
        raise RuntimeError(
            f"Document parsing failed for {error_count} documents. Check parsed_documents table for details."
        )


def generate_metadata(cursor: SnowflakeCursor, *, prefix: str, config: dict[str, Any], insert: bool) -> None:
    """
    Generates metadata for all documents in the stage
    """
    select_stmt = f"""
        select
            parsed_documents.source_uri,
            'Ground Truth Metadata:\\n'
            || document_metadata.metadata
            || '\\n\\nSynthetic Metadata:\\n'
            || snowflake.cortex.complete(
                '{config["metadata_model"]}', 
                '{config["metadata_prompt"].replace("\n", "\\n")}'
                || '\\n\\nDoc starts here:\\n' 
                || substr(parsed_content, 0, {config["metadata_first_chars"]}) 
                || '\\nDoc ends here\\n\\n'
            ) as enhanced_metadata
        from updated_uris
        inner join document_metadata 
            on updated_uris.source_uri = document_metadata.source_uri
        inner join parsed_documents 
            on updated_uris.source_uri = parsed_documents.source_uri
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
    cursor.execute(query)


def chunk_documents(cursor: SnowflakeCursor, *, prefix: str, config: dict[str, Any], insert: bool) -> None:
    """
    Splits documents into overlapping chunks for easier search
    """
    select_stmt = f"""
        select
            parsed_documents.source_uri,
            enhanced_metadata.enhanced_metadata
            || '\\n\\nDocument chunk:\\n'
            || chunks.value as contextualized_chunk
        from updated_uris
        join parsed_documents 
            on updated_uris.source_uri = parsed_documents.source_uri
        join enhanced_metadata 
            on updated_uris.source_uri = enhanced_metadata.source_uri,
        lateral flatten( input => snowflake.cortex.split_text_recursive_character(
            parsed_documents.parsed_content,
            'none',
            {config["chunk_size"]},
            {config["chunk_overlap"]}
        )) as chunks
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
    cursor.execute(query)


def clear_stage(cursor: SnowflakeCursor) -> None:
    cursor.execute("remove @documents")


def upload_documents(
    conn: SnowflakeConnection,
    *,
    sources: dict[str, DocumentInfo],
    prefix: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    with create_cursor(conn, config) as cursor:
        clear_stage(cursor)
        for source_uri, source_info in sources.items():
            stage_document(
                cursor,
                source_uri=source_uri,
                local_path=source_info.local_path,
                modified_at_utc=source_info.modified_at_utc,
                insert=insert,
                metadata=source_info.metadata,
            )
        parse_documents(cursor, prefix=prefix, insert=insert)
        generate_metadata(cursor, prefix=prefix, config=config, insert=insert)
        chunk_documents(cursor, prefix=prefix, config=config, insert=insert)


def delete_documents(conn: SnowflakeConnection, *, deleted_uris: set[str], config: dict[str, Any]) -> None:
    if not deleted_uris:
        return
    with create_cursor(conn, config) as cursor:
        # Create a string of placeholders: :1, :2, ..., :N
        placeholders = ", ".join([f":{i + 1}" for i in range(len(deleted_uris))])
        for table in ALL_TABLES:
            cursor.execute(
                f"delete from {table} where source_uri in ({placeholders})",
                tuple(deleted_uris),
            )


def refresh_search_services(conn: SnowflakeConnection, *, config: dict[str, Any]) -> None:
    with create_cursor(conn, config) as cursor:
        for search_service in ["search_metadata", "search_contents"]:
            cursor.execute(f"alter cortex search service if exists {search_service} refresh")


def process_documents(
    sources: dict[str, DocumentInfo],
    *,
    prefix: str,
    snowflake_connection_name: str = "default",
    conn: SnowflakeConnection | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    if config is None:
        config = load_config()
    if conn is None:
        conn = snowflake.connector.connect(connection_name=snowflake_connection_name)
    targets = get_snowflake_documents(conn, prefix=prefix, config=config)
    source_uris = set(sources)
    target_uris = set(targets)
    # Delete the removed documents
    deleted_uris = target_uris - source_uris
    for uri in deleted_uris:
        logging.info(f"Deleting document {uri} from Snowflake...")
    delete_documents(conn, deleted_uris=deleted_uris, config=config)
    # Create a temporary table that will hold all the updated URIs
    create_temporary_updated_uris(conn, config=config)
    # Insert the added documents
    added_uris = source_uris - target_uris
    for uri in added_uris:
        logging.info(f"Adding document {uri} to Snowflake...")
    if added_uris:
        upload_documents(
            conn,
            sources={uri: source for uri, source in sources.items() if uri in added_uris},
            prefix=prefix,
            config=config,
            insert=True,
        )
    # Update the modified documents
    common_uris = source_uris & target_uris
    modified_uris = {uri for uri in common_uris if sources[uri].modified_at_utc > targets[uri].modified_at_utc}
    for uri in modified_uris:
        logging.info(f"Updating modified document {uri} in Snowflake...")
    if modified_uris:
        upload_documents(
            conn,
            sources={uri: source for uri, source in sources.items() if uri in modified_uris},
            prefix=prefix,
            config=config,
            insert=False,
        )
    if deleted_uris or added_uris or modified_uris:
        refresh_search_services(conn, config=config)
