from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import os

import snowflake.connector
from snowflake.connector.cursor import SnowflakeCursor
import yaml

snowflake.connector.paramstyle = "numeric"

ALL_TABLES = ["document_metadata", "enhanced_metadata", "parsed_documents", "document_chunks"]


@dataclass
class DocumentInfo:
    modified_at_utc: datetime
    local_path: Path | None = None
    metadata: str = ""


def load_config(config_path: str = "snowflake.yml") -> dict[str, Any]:
    if not os.path.exists(config_path):
        raise RuntimeError(f"Error: Config file '{config_path}' not found.")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    return config.get("env", {})


def get_snowflake_connection(env_config: dict[str, Any]) -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        role=env_config.get("role"),
        warehouse=env_config.get("warehouse"),
        database=env_config.get("database"),
        schema=env_config.get("schema"),
    )


def get_snowflake_documents(conn: snowflake.connector.SnowflakeConnection, *, prefix: str) -> dict[str, DocumentInfo]:
    with conn.cursor() as cursor:
        cursor.execute(
            f"select source_uri, modified_at_utc from document_metadata where startswith(source_uri, '{prefix}://')"
        )
    return {source_uri: DocumentInfo(modified_at_utc=modified_at_utc) for source_uri, modified_at_utc in cursor}


def stage_document(
    cursor: SnowflakeCursor,
    source_uri: str,
    local_path: Path,
    modified_at_utc: datetime,
    insert: bool,
    metadata: str = "",
) -> None:
    # Upload the document to Snowflake
    local_path_str = str(local_path.absolute()).replace("\\", "\\\\")
    source_path_str = source_uri.split("://", 1)[-1]
    cursor.execute(f"""
        put 'file://{local_path_str}' @documents/{source_path_str}
            auto_compress=false overwrite=true
        """)
    # Update the modification timestamp and the metadata string
    cte_inner = f"""
        select
            '{source_uri}' as source_uri,
            '{modified_at_utc.isoformat()}'::timestamp_ntz as modified_at_utc,
            :1 as metadata
    """
    if insert:
        query = f"""
            insert into document_metadata (source_uri, modified_at_utc, metadata)
            with updated_metadata as ({cte_inner})
            select * from updated_metadata
            """
    else:
        query = f"""
            with updated_metadata as ({cte_inner})
            update document_metadata set
                modified_at_utc = updated_metadata.modified_at_utc,
                metadata = updated_metadata.metadata
            from updated_metadata
            where document_metadata.source_uri = updated_metadata.source_uri
            """
    cursor.execute(query, (metadata,))


def parse_documents(cursor: SnowflakeCursor, prefix: str, insert: bool) -> None:
    """
    Parses all documents from the stage and inserts into parsed_documents.
    """
    # Refresh the stage for directory() to be up-to-date
    cursor.execute("alter stage documents refresh")
    cte = f"""
    with updated_documents as (
        select
            '{prefix}://' || relative_path as source_uri,
            snowflake.cortex.parse_document(
                '@documents',
                relative_path,
                {{'mode': 'OCR'}}
            )::string as parsed_content
        from directory(@documents)
    )
    """
    if insert:
        query = (
            cte
            + """
            insert into parsed_documents (source_uri, parsed_content)
            select * from updated_documents
            """
        )
    else:
        query = (
            cte
            + """
            update parsed_documents set
                parsed_content = updated_documents.parsed_content
            from updated_documents
            where parsed_documents.source_uri = updated_documents.source_uri
            """
        )
    cursor.execute(query)


def generate_metadata(cursor: SnowflakeCursor, prefix: str, config: dict[str, Any], insert: bool) -> None:
    """
    Generates metadata for all documents in the stage
    """
    ctes = f"""
    with updated_uris as (
        select
            '{prefix}://' || relative_path as source_uri
        from directory(@documents)
    ), updated_metadata as (
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
        inner join document_metadata on updated_uris.source_uri = document_metadata.source_uri
        inner join parsed_documents on updated_uris.source_uri = parsed_documents.source_uri
    )
    """
    if insert:
        query = (
            ctes
            + """
            insert into enhanced_metadata (source_uri, enhanced_metadata)
            select * from updated_metadata
            """
        )
    else:
        query = (
            ctes
            + """
            update enhanced_metadata set
                enhanced_metadata = updated_metadata.enhanced_metadata
            from updated_metadata
            where enhanced_metadata.source_uri = updated_metadata.source_uri
            """
        )
    cursor.execute(query)


def chunk_documents(cursor: SnowflakeCursor, prefix: str, config: dict[str, Any], insert: bool) -> None:
    """
    Splits documents into overlapping chunks for easier search
    """
    ctes = f"""
    with updated_uris as (
        select
            '{prefix}://' || relative_path as source_uri
        from directory(@documents)
    ), updated_chunks as (
        select
            parsed_documents.source_uri,
            enhanced_metadata.enhanced_metadata
            || '\\n\\nDocument chunk:\\n'
            || chunks.value as contextualized_chunk
        from updated_uris
        inner join parsed_documents on updated_uris.source_uri = parsed_documents.source_uri
        inner join enhanced_metadata on updated_uris.source_uri = enhanced_metadata.source_uri
        lateral flatten( input => snowflake.cortex.split_text_recursive_character(
            parsed_documents.parsed_content,
            'none',
            {config["chunk_size"]},
            {config["chunk_overlap"]}
        )) as chunks
    )
    """
    if insert:
        query = (
            ctes
            + """
            insert into document_chunks (source_uri, contextualized_chunks)
            select * from updated_chunks
            """
        )
    else:
        query = (
            ctes
            + """
            update document_chunks set
                contextualized_chunks = updated_chunks.contextualized_chunks
            from updated_chunks
            where document_chunks.source_uri = updated_chunks.source_uri
            """
        )
    cursor.execute(query)


def clear_stage(cursor: SnowflakeCursor) -> None:
    cursor.execute("remove stage @documents")


def upload_documents(
    conn: snowflake.connector.SnowflakeConnection,
    sources: dict[str, DocumentInfo],
    prefix: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    with conn.cursor() as cursor:
        clear_stage(cursor)
        for source_uri, source_info in sources.items():
            stage_document(
                cursor,
                source_uri=source_uri,
                local_path=source_info.local_path,
                modified_at_utc=source_info.modified_at_utc,
                metadata=source_info.metadata,
            )
        parse_documents(cursor, prefix=prefix, insert=insert)
        generate_metadata(cursor, prefix=prefix, config=config, insert=insert)
        chunk_documents(cursor, prefix=prefix, config=config, insert=insert)
        clear_stage(cursor)


def delete_documents(conn: snowflake.connector.SnowflakeConnection, deleted_uris: set[str]) -> None:
    with conn.cursor() as cursor:
        for table in ALL_TABLES:
            cursor.execute(f"delete from {table} where source_uri in :1", (deleted_uris,))


def process_documents(sources: dict[str, DocumentInfo], prefix: str) -> None:
    config = load_config()
    conn = get_snowflake_connection(config)
    targets = get_snowflake_documents(conn, prefix=prefix)
    source_uris = set(sources)
    target_uris = set(targets)
    # Delete the removed documents
    delete_documents(conn, target_uris - source_uris)
    # Insert the added documents
    added_uris = source_uris - target_uris
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
    upload_documents(
        conn,
        sources={uri: source for uri, source in sources.items() if uri in modified_uris},
        prefix=prefix,
        config=config,
        insert=False,
    )
