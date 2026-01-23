from datetime import datetime
from pathlib import Path
import os

import snowflake.connector
from snowflake.connector.cursor import SnowflakeCursor
import yaml

ALL_TABLES = ["document_metadata", "enhanced_metadata", "parsed_documents", "document_chunks"]

def load_config(config_path: str = "snowflake.yml") -> Dict[str, Any]:
    if not os.path.exists(config_path):
        raise RuntimeError(f"Error: Config file '{config_path}' not found.")
    
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    return config.get("env", {})

def get_snowflake_connection(env_config: Dict[str, Any]) -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        role=env_config.get("role"),
        warehouse=env_config.get("warehouse"),
        database=env_config.get("database"),
        schema=env_config.get("schema"),
    )

def stage_document(
        cursor: SnowflakeCursor,
        stage_name: str,
        prefix: str,
        source_path_str: str,
        local_path: Path,
        modified_at: datetime,
        metadata: str = "",
    ) -> None:
    # Upload the document to Snowflake
    local_path_str = str(local_path.absolute()).replace("\\", "\\\\")
    cursor.execute(f"""
        put 'file://{local_path_str}' @{stage_name}/{source_path_str}
            auto_compress=false overwrite=true
        """
    )
    # Update the modification timestamp and the metadata string
    query = f"""
    with updated_metadata as (
        select
            '{prefix}://{source_path_str}' as source_uri,
            '{modified_at.isoformat()}'::timestamp_ntz as modified_at_utc,
            %s as metadata
    )
    merge into document_metadata
        using updated_metadata
        on document_metadata.source_uri = updated_metadata.source_uri
        when matched then update set
            document_metadata.modified_at_utc = updated_metadata.modified_at_utc,
            document_metadata.metadata = updated_metadata.metadata
        when not matched then insert (source_uri, modified_at_utc, metadata) values (
            updated_metadata.source_uri, updated_metadata.modified_at_utc, updated_metadata.metadata
        )
    """
    cursor.execute(query, (metadata,))
        

def parse_documents(cursor: SnowflakeCursor, stage_name: str, prefix: str) -> None:
    """
    Parses all documents from the stage and inserts into parsed_documents.
    """
    # Refresh the stage for directory() to be up-to-date
    cursor.execute(f"alter stage {stage_name} refresh")
    query = f"""
    with updated_documents as (
        select
            '{prefix}://' || relative_path as source_uri,
            snowflake.cortex.parse_document(
                '@{stage_name}',
                relative_path,
                {{'mode': 'OCR'}}
            )::string as parsed_content
        from directory(@{stage_name})
    )
    merge into parsed_documents
        using updated_documents
        on parsed_documents.source_uri = updated_documents.source_uri
        when matched then update set parsed_documents.parsed_content = updated_documents.parsed_content
        when not matched then insert (source_uri, parsed_content) values (
            updated_documents.source_uri, updated_documents.parsed_content
        )
    """
    cursor.execute(query)


def generate_metadata(cursor: SnowflakeCursor, stage_name: str, prefix: str, config: dict[str, Any]) -> None:
    """
    Generates metadata for all documents in the stage
    """
    query = f"""
    with updated_uris as (
        select
            '{prefix}://' || relative_path as source_uri
        from directory(@{stage_name})
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
    merge into enhanced_metadata
        using updated_metadata
        on enhanced_metadata.source_uri = updated_metadata.source_uri
        when matched then update set enhanced_metadata.parsed_content = updated_metadata.parsed_content
        when not matched then insert (source_uri, parsed_content) values (
            updated_metadata.source_uri, updated_metadata.parsed_content
        )
    """
    cursor.execute(query)


def chunk_documents(cursor: SnowflakeCursor, stage_name: str, prefix: str, config: dict[str, Any]) -> None:
    """
    Splits documents into overlapping chunks for easier search
    """
    query = f"""
    with updated_uris as (
        select
            '{prefix}://' || relative_path as source_uri
        from directory(@{stage_name})
    ), updated_chunks as (
        select
            parsed_documents.source_uri,
            enchanced_metadata.enchanced_metadata
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
    merge into document_chunks
        using updated_chunks
        on document_chunks.source_uri = updated_chunks.source_uri
        when matched then update set document_chunks.contextualized_chunk = updated_chunks.contextualized_chunk
        when not matched then insert (source_uri, contextualized_chunk) values (
            updated_chunks.source_uri, updated_chunks.contextualized_chunk
        )
    """
    cursor.execute(query)

def delete_document(cursor: SnowflakeCursor, source_uri: str) -> None:
    for table in ALL_TABLES:
        cursor.execute(f"delete from {table} where source_uri = %s", (source_uri,))

def clear_stage(cursor: SnowflakeCursor, stage_name: str) -> None
    cursor.execute(f"remove stage @{stage_name}")

