import pytest
from datetime import datetime, timezone
from snowflake_document_agent.common import (
    stage_document,
    get_snowflake_documents,
    process_documents,
    DocumentInfo,
    create_cursor,
)


@pytest.mark.integration
def test_stage_document_with_spaces_integration(snowflake_conn, test_schema, tmp_path):
    """
    Integration test for filenames with spaces.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with create_cursor(snowflake_conn, {}) as cursor:
        # Setup
        filename = "test file with spaces.txt"
        local_file = tmp_path / filename
        local_file.write_text("Content for spaces test")

        source_uri = f"local://integration_tests/{filename}"
        mod_time = datetime.now(timezone.utc)

        try:
            # Execute
            stage_document(
                cursor=cursor, source_uri=source_uri, local_path=local_file, modified_at_utc=mod_time, insert=True
            )

            # Verify Stage
            stage_path = f"integration_tests/{filename}"
            cursor.execute(f"LIST '@documents/{stage_path}'")
            results = cursor.fetchall()
            assert len(results) == 1, "File with spaces not found in stage"

            # Verify Metadata
            cursor.execute(f"SELECT source_uri FROM document_metadata WHERE source_uri = '{source_uri}'")
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == source_uri

        finally:
            # Cleanup
            try:
                cursor.execute(f"REMOVE '@documents/integration_tests/{filename}'")
            except Exception:
                pass
            try:
                cursor.execute(f"DELETE FROM document_metadata WHERE source_uri = '{source_uri}'")
            except Exception:
                pass


@pytest.mark.integration
def test_stage_document_with_single_quotes_integration(snowflake_conn, test_schema, tmp_path):
    """
    Integration test for filenames with single quotes.
    This is a critical failure point for SQL injection or syntax errors.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with create_cursor(snowflake_conn, {}) as cursor:
        # Setup
        filename = "test'file'with'quotes.txt"
        local_file = tmp_path / filename
        local_file.write_text("Content for quotes test")

        source_uri = f"local://integration_tests/{filename}"
        mod_time = datetime.now(timezone.utc)

        try:
            # Execute
            stage_document(
                cursor=cursor, source_uri=source_uri, local_path=local_file, modified_at_utc=mod_time, insert=True
            )

            # Verify Stage
            stage_path = f"integration_tests/{filename}"
            # We need to escape the single quote in the SQL string literal for the LIST command itself
            stage_path_escaped = stage_path.replace("'", "''")
            cursor.execute(f"LIST '@documents/{stage_path_escaped}'")
            results = cursor.fetchall()
            assert len(results) == 1, "File with quotes not found in stage"

            # Verify Metadata
            source_uri_escaped = source_uri.replace("'", "''")
            cursor.execute(f"SELECT source_uri FROM document_metadata WHERE source_uri = '{source_uri_escaped}'")
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == source_uri

        finally:
            # Cleanup
            try:
                stage_path_escaped = f"integration_tests/{filename}".replace("'", "''")
                cursor.execute(f"REMOVE '@documents/{stage_path_escaped}'")
            except Exception:
                pass
            try:
                source_uri_escaped = source_uri.replace("'", "''")
                cursor.execute(f"DELETE FROM document_metadata WHERE source_uri = '{source_uri_escaped}'")
            except Exception:
                pass


@pytest.mark.integration
def test_process_documents_with_tricky_filenames(snowflake_conn, test_schema, tmp_path):
    """
    Test the higher level process_documents function with a mix of tricky filenames
    to ensure the end-to-end flow works (upload, parse, metadata, chunks).
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # Setup
    docs_dir = tmp_path / "tricky_docs"
    docs_dir.mkdir()

    # 1. Spaces
    f1 = docs_dir / "doc with spaces.txt"
    f1.write_text("Content 1")

    # 2. Single Quotes
    f2 = docs_dir / "doc's_file.txt"
    f2.write_text("Content 2")

    prefix = "tricky_test"

    sources = {}
    mod_time = datetime.now(timezone.utc)

    uri1 = f"{prefix}://doc with spaces.txt"
    sources[uri1] = DocumentInfo(modified_at_utc=mod_time, local_path=f1)

    uri2 = f"{prefix}://doc's_file.txt"
    sources[uri2] = DocumentInfo(modified_at_utc=mod_time, local_path=f2)

    try:
        # Execute
        process_documents(sources, prefix=prefix, conn=snowflake_conn)

        # Verify
        docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config={})

        assert uri1 in docs
        assert uri2 in docs

    finally:
        # Cleanup
        from snowflake_document_agent.common import delete_documents

        # We need to manually clean up because we want to test that ingestion worked
        # process_documents normally handles deletes if we passed empty sources, but let's be explicit
        delete_documents(snowflake_conn, deleted_uris={uri1, uri2}, config={})

        with create_cursor(snowflake_conn, {}) as cursor:
            try:
                cursor.execute("REMOVE '@documents/doc with spaces.txt'")
                cursor.execute("REMOVE '@documents/doc''s_file.txt'")
            except Exception:
                pass
