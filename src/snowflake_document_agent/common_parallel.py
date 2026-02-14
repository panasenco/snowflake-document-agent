class DocumentInfoProtocol(Protocol):
    """Protocol for document information objects."""

    modified_at_utc: datetime
    metadata: str
    local_path: Path | None

def clear_stage(cursor: SnowflakeCursor) -> None:
    """Clear the documents stage before ingesting new documents."""

def delete_documents(
    conn: SnowflakeConnection,
    *,
    deleted_uris: set[str],
    config: dict[str, Any],
) -> None:
    """ Batch delete documents.
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
    """ Stages a document from a local filepath into the Snowflake stage @documents.
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
    """ Parses a staged document and inserts into parsed_documents.
    Raises RuntimeError if the parsing fails.
    """

def generate_document_metadata(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """ Generates synthetic metadata for a parsed document. """

def chunk_document(
    cursor: SnowflakeCursor,
    *,
    source_uri: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """ Splits documents into overlapping chunks for easier search. """

def process_document(
    conn: SnowflakeConnection,
    *,
    source_uri: str,
    source_info: DocumentInfo,
    prefix: str,
    config: dict[str, Any],
    insert: bool,
) -> None:
    """ Process a single document end-to-end. """

def process_documents(
    sources: dict[str, DocumentInfo],
    *,
    prefix: str,
    snowflake_connection: SnowflakeConnection | str = "default",
    config: dict[str, Any] | None = None,
) -> None:
    """ Process documents end-to-end in parallel.
    Accepts a SnowflakeConnection object or Snowflake connection name (if string).
    Displays a progress bar.
    Doesn't crash when a document fails to process, but displays detailed information about the error.
    """