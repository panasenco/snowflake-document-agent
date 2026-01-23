import pytest
from datetime import datetime, timezone
from snowflake_document_agent.common import stage_document, get_snowflake_documents


@pytest.fixture
def setup_staged_document(snowflake_conn, tmp_path):
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
def test_stage_document_integration(snowflake_conn, test_schema, tmp_path):
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
def test_process_documents_integration(snowflake_conn, test_schema, tmp_path, config):
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
        process_documents(sources, prefix=prefix, conn=snowflake_conn, config=config)

        # 3. Verify Ingestion
        docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config=config)
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

        process_documents(sources, prefix=prefix, conn=snowflake_conn, config=config)

        # 5. Verify Modification
        docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config=config)
        assert abs((docs[source_uri].modified_at_utc - new_mod_time).total_seconds()) < 2.0

        for table in ALL_TABLES:
            cursor.execute(f"select count(*) from {table} where source_uri = '{source_uri}'")
            count = cursor.fetchone()[0]
            assert count > 0, f"Row missing in {table} after modification"

        # 6. Execute - Deletion
        process_documents({}, prefix=prefix, conn=snowflake_conn, config=config)

        # 7. Verify Deletion
        docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config=config)
        assert source_uri not in docs

    finally:
        # Cleanup any leftover data if necessary
        # process_documents({}, prefix=prefix) should have cleaned up
        pass
