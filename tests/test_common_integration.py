import pytest
from datetime import datetime, timezone
from snowflake_document_agent.common import stage_document, get_snowflake_documents


@pytest.fixture
def setup_staged_document(snowflake_conn, tmp_path, updated_uris):
    """
    Fixture that stages a document for other tests to use.
    Demonstrates best practice of using fixtures for resource dependencies.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    cursor = snowflake_conn.cursor()

    # Setup
    source_uri = "local://integration_tests/get_docs_test.txt"
    file_content = f"Content for get_snowflake_documents test {datetime.now().isoformat()}"
    local_file = tmp_path / "get_docs_test.txt"
    local_file.write_text(file_content)

    # We truncate microseconds because Snowflake timestamps might have different precision depending on type
    # but isoformat usually works.
    mod_time = datetime.now(timezone.utc)
    metadata_val = '{"type": "fixture"}'

    stage_document(
        cursor=cursor,
        source_uri=source_uri,
        local_path=local_file,
        modified_at_utc=mod_time,
        insert=True,
        metadata=metadata_val,
    )

    yield {"source_uri": source_uri, "mod_time": mod_time, "metadata": metadata_val, "local_path": local_file}

    # Cleanup
    stage_path = source_uri.split("://")[-1]
    try:
        cursor.execute(f"REMOVE @documents/{stage_path}")
    except Exception:
        pass

    try:
        cursor.execute(f"DELETE FROM document_metadata WHERE source_uri = '{source_uri}'")
    except Exception:
        pass
    cursor.close()


@pytest.mark.integration
def test_stage_document_integration(snowflake_conn, test_schema, tmp_path, updated_uris):
    """
    Verifies that stage_document uploads a file to the @documents stage
    and updates the document_metadata table.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    cursor = snowflake_conn.cursor()

    # 1. Setup - Create a dummy local file
    file_content = "Integration Test Content: " + datetime.now().isoformat()
    local_file = tmp_path / "integration_test_doc.txt"
    local_file.write_text(file_content)

    # Use a unique source URI to avoid collisions
    source_uri = "local://integration_tests/integration_test_doc.txt"
    mod_time = datetime.now(timezone.utc)
    metadata_val = '{"test": "true"}'

    try:
        # 2. Execute - Stage the document (insert mode)
        stage_document(
            cursor=cursor,
            source_uri=source_uri,
            local_path=local_file,
            modified_at_utc=mod_time,
            insert=True,
            metadata=metadata_val,
        )

        # 3. Verify - Check Stage
        # The path in stage will be determined by source_uri suffix after ://
        # source_uri: local://integration_tests/integration_test_doc.txt
        # expected stage path: @documents/integration_tests/integration_test_doc.txt
        stage_path = "integration_tests/integration_test_doc.txt"

        cursor.execute(f"LIST @documents/{stage_path}")
        stage_results = cursor.fetchall()
        assert len(stage_results) == 1, "File not found in stage @documents"

        # 4. Verify - Check Metadata Table
        cursor.execute(f"SELECT metadata FROM document_metadata WHERE source_uri = '{source_uri}'")
        row = cursor.fetchone()
        assert row is not None, "Metadata row not found"
        # Note: Depending on how :1 is bound and inserted, it might come back as a string or Variant.
        # common.py inserts it into a generic column?
        # Looking at setup_04_tables_stages.sql (implied), metadata is likely VARIANT or VARCHAR.
        # The input was a string.
        assert metadata_val in str(row[0])

    finally:
        # 5. Cleanup
        try:
            cursor.execute(f"REMOVE @documents/{stage_path}")
        except Exception:
            pass

        try:
            cursor.execute(f"DELETE FROM document_metadata WHERE source_uri = '{source_uri}'")
        except Exception:
            pass
        cursor.close()


@pytest.mark.integration
def test_get_snowflake_documents_integration(snowflake_conn, test_schema, setup_staged_document):
    """
    Verifies that get_snowflake_documents retrieves the metadata correctly.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    cursor = snowflake_conn.cursor()
    # Debug: Run the exact query used in get_snowflake_documents
    query = "select source_uri, modified_at_utc from document_metadata where startswith(source_uri, 'local://')"
    cursor.execute(query)
    debug_results = cursor.fetchall()
    print(f"\nDEBUG: Manual query results for '{query}': {debug_results}")

    # Action
    # Search with the prefix "local", as get_snowflake_documents appends "://"
    prefix = "local"
    docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config={})
    print(f"DEBUG: docs returned: {docs}")

    # Assert
    fixture_uri = setup_staged_document["source_uri"]
    assert fixture_uri in docs, "Staged document source URI not found in results"

    doc_info = docs[fixture_uri]

    # Verify timestamp (allowing for small differences due to DB precision)
    # Snowflake might truncate nanoseconds or similar
    assert abs((doc_info.modified_at_utc - setup_staged_document["mod_time"]).total_seconds()) < 1.0


@pytest.mark.integration
def test_delete_documents_integration(snowflake_conn, test_schema):
    """
    Verifies that delete_documents removes rows from all tables.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    cursor = snowflake_conn.cursor()
    source_uri = "local://integration_tests/delete_test.txt"

    from snowflake_document_agent.common import ALL_TABLES, delete_documents

    try:
        # 1. Setup - Insert dummy data into multiple tables
        for table in ALL_TABLES:
            if table == "document_metadata":
                cursor.execute(
                    f"insert into {table} (source_uri, modified_at_utc) values ('{source_uri}', current_timestamp())"
                )
            elif table == "document_chunks":
                cursor.execute(
                    f"insert into {table} (source_uri, contextualized_chunk) values ('{source_uri}', 'chunk')"
                )
            else:
                col = "enhanced_metadata" if table == "enhanced_metadata" else "parsed_content"
                cursor.execute(f"insert into {table} (source_uri, {col}) values ('{source_uri}', 'content')")

        # 2. Execute
        delete_documents(snowflake_conn, deleted_uris={source_uri}, config={})

        # 3. Verify
        for table in ALL_TABLES:
            cursor.execute(f"select count(*) from {table} where source_uri = '{source_uri}'")
            count = cursor.fetchone()[0]
            assert count == 0, f"Row still exists in {table}"

    finally:
        cursor.close()


@pytest.mark.integration
def test_process_documents_integration(snowflake_conn, test_schema, tmp_path, test_config):
    """
    Higher-level integration test for process_documents.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    from snowflake_document_agent.common import DocumentInfo, process_documents, get_snowflake_documents

    # 1. Setup - local files
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    f1 = docs_dir / "doc1.txt"
    f1.write_text("This is document 1 content. It is long enough to be parsed.")

    prefix = "process_test"
    source_uri = f"{prefix}://doc1.txt"
    mod_time = datetime.now(timezone.utc)

    sources = {source_uri: DocumentInfo(modified_at_utc=mod_time, local_path=f1)}

    try:
        # 2. Execute - Initial Ingestion
        process_documents(sources, prefix=prefix, conn=snowflake_conn, config=test_config)

        # 3. Verify Ingestion
        docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config=test_config)
        assert source_uri in docs

        cursor = snowflake_conn.cursor()

        # Debugging output
        cursor.execute("list @documents")
        print(f"\nDEBUG: Stage files: {cursor.fetchall()}")

        cursor.execute("select * from parsed_documents")
        print(f"DEBUG: parsed_documents content: {cursor.fetchall()}")

        from snowflake_document_agent.common import ALL_TABLES

        for table in ALL_TABLES:
            cursor.execute(f"select count(*) from {table}")
            count = cursor.fetchone()[0]
            assert count == 1, f"Row missing in {table} after ingestion. Expected 1, got {count}"

        # 3.1. Verify parsing
        cursor.execute("select parsed_content from parsed_documents")
        assert "error" not in cursor.fetchone()[0].lower(), "Parsing error in parsed_documents"

        # 4. Execute - Modification
        new_mod_time = datetime.now(timezone.utc)
        f1.write_text("Updated content for document 1. Still long enough.")
        sources[source_uri].modified_at_utc = new_mod_time

        process_documents(sources, prefix=prefix, conn=snowflake_conn, config=test_config)

        # 5. Verify Modification
        docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config=test_config)
        assert abs((docs[source_uri].modified_at_utc - new_mod_time).total_seconds()) < 2.0

        for table in ALL_TABLES:
            cursor.execute(f"select count(*) from {table} where source_uri = '{source_uri}'")
            count = cursor.fetchone()[0]
            assert count > 0, f"Row missing in {table} after modification"

        # 6. Execute - Deletion
        process_documents({}, prefix=prefix, conn=snowflake_conn, config=test_config)

        # 7. Verify Deletion
        docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config=test_config)
        assert source_uri not in docs

    finally:
        # Cleanup any leftover data if necessary
        # process_documents({}, prefix=prefix) should have cleaned up
        pass


@pytest.mark.integration
def test_process_documents_prefix_isolation(snowflake_conn, test_schema, tmp_path, test_config):
    """
    Verifies that process_documents only affects documents with the specified prefix.
    This test reproduces a bug where multiple prefixes might interfere with each other.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    from snowflake_document_agent.common import DocumentInfo, process_documents, get_snowflake_documents

    # 1. Setup - Two prefixes and documents
    prefix1 = "prefix1"
    prefix2 = "prefix2"

    docs_dir = tmp_path / "docs_isolation"
    docs_dir.mkdir()

    f1 = docs_dir / "doc1.txt"
    f1.write_text("Content for prefix 1")
    f2 = docs_dir / "doc2.txt"
    f2.write_text("Content for prefix 2")

    uri1 = f"{prefix1}://doc1.txt"
    uri2 = f"{prefix2}://doc2.txt"

    # We use fixed timestamps to avoid issues with fast execution
    t1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2025, 1, 1, tzinfo=timezone.utc)

    sources1 = {uri1: DocumentInfo(modified_at_utc=t1, local_path=f1)}
    sources2 = {uri2: DocumentInfo(modified_at_utc=t2, local_path=f2)}

    try:
        # 2. Ingest prefix 1
        process_documents(sources1, prefix=prefix1, conn=snowflake_conn, config=test_config)
        # 3. Ingest prefix 2
        process_documents(sources2, prefix=prefix2, conn=snowflake_conn, config=test_config)
        # 4. Verify both exist
        docs1 = get_snowflake_documents(snowflake_conn, prefix=prefix1, config=test_config)
        docs2 = get_snowflake_documents(snowflake_conn, prefix=prefix2, config=test_config)
        assert uri1 in docs1
        assert uri2 in docs2
    finally:
        # Final cleanup
        try:
            process_documents({}, prefix=prefix1, conn=snowflake_conn, config=test_config)
        except Exception:
            pass
        try:
            process_documents({}, prefix=prefix2, conn=snowflake_conn, config=test_config)
        except Exception:
            pass


@pytest.mark.integration
def test_parse_documents_fails_on_missing_files(snowflake_conn, test_schema, test_config):
    """
    Test that parse_documents() raises an exception when Cortex parsing fails
    due to missing files in the stage, instead of silently inserting error messages.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    from snowflake_document_agent.common import create_temporary_updated_uris, parse_documents

    with snowflake_conn.cursor() as cursor:
        # 1. Setup - Create updated_uris table with references to non-existent files
        create_temporary_updated_uris(snowflake_conn, config=test_config)

        # Insert fake stage paths that don't actually exist in the @documents stage
        cursor.execute(
            "INSERT INTO updated_uris (source_uri, stage_path) VALUES "
            "('test://missing_doc1.txt', 'missing_doc1.txt'), "
            "('test://missing_doc2.pdf', 'missing_doc2.pdf')"
        )

        # 2. Verify the @documents stage is empty for these paths
        cursor.execute("LIST @documents")
        stage_files = [row[0] for row in cursor.fetchall()]
        assert "missing_doc1.txt" not in str(stage_files), "Test setup error: file exists in stage"
        assert "missing_doc2.pdf" not in str(stage_files), "Test setup error: file exists in stage"

        # 3. Execute parse_documents - this should detect the parsing errors and raise an exception
        with pytest.raises(RuntimeError) as exc_info:
            parse_documents(cursor, prefix="test", insert=True)

        # 4. Verify the exception message mentions the parsing failure
        error_message = str(exc_info.value)
        assert "parsing failed" in error_message.lower(), f"Expected parsing error message, got: {error_message}"
        assert "2 documents" in error_message, f"Expected count of failed documents, got: {error_message}"

    # No cleanup - leave test data for inspection


@pytest.mark.integration
def test_stage_document_uses_local_filename(snowflake_conn, test_schema, test_config, tmp_path):
    """Test that stage_document uses the actual local filename rather than source_uri filename."""
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    from snowflake_document_agent.common import stage_document, create_temporary_updated_uris

    # Create a temp file with a different name than what source_uri implies
    local_file = tmp_path / "actual_temp_file_12345.pdf"
    local_file.write_text("Test content for stage filename test")

    # Source URI suggests a different filename
    source_uri = "test://expected_document_name.pdf"

    # Create temporary updated_uris table
    create_temporary_updated_uris(snowflake_conn, config=test_config)

    with snowflake_conn.cursor() as cursor:
        # Stage the document
        stage_document(
            cursor=cursor,
            source_uri=source_uri,
            local_path=local_file,
            modified_at_utc=datetime.now(timezone.utc),
            insert=True,
            metadata='{"test": "stage_filename"}',
        )

        # Check updated_uris table to see what stage_path was recorded
        cursor.execute("SELECT stage_path FROM updated_uris WHERE source_uri = :1", (source_uri,))
        result = cursor.fetchone()
        assert result is not None, "No entry found in updated_uris"

        recorded_stage_path = result[0]
        print(f"Recorded stage_path: {recorded_stage_path}")
        print(f"Local filename: {local_file.name}")

        # BUG: stage_path should reference the actual local filename, not the source_uri filename
        # This test documents the bug that needs to be fixed in common.py stage_document()
        assert local_file.name in recorded_stage_path, (
            f"stage_path should contain actual local filename '{local_file.name}', "
            f"but recorded: '{recorded_stage_path}'"
        )
