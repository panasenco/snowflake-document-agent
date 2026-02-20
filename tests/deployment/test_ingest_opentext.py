"""Deployment tests for OpenText document ingestion functionality."""

import pytest

from snowflake_document_agent.ingest_opentext import OpenTextDownloader
from snowflake_document_agent.common import process_changed_documents


@pytest.mark.deployment
def test_opentext_conn_fixture_deployment(opentext_conn):
    """Test that opentext_conn fixture works properly for deployment tests."""
    if opentext_conn is None:
        pytest.skip("Deployment tests not enabled (use --run-deployment)")

    # Should have an OpenTextDownloader instance
    assert isinstance(opentext_conn, OpenTextDownloader)

    # Should have all required attributes populated from environment
    assert opentext_conn.client_id
    assert opentext_conn.client_secret
    assert opentext_conn.api_prefix
    assert opentext_conn.app_client_id
    assert opentext_conn.app_client_secret

    # Should have authentication headers set
    assert opentext_conn.headers
    assert "authorization" in opentext_conn.headers


@pytest.mark.deployment
def test_opentext_document_content_download(opentext_conn, pytestconfig):
    """Test downloading actual document content from OpenText using provided node ID."""
    if opentext_conn is None:
        pytest.skip("Deployment tests not enabled (use --run-deployment)")

    node_id = pytestconfig.getoption("--opentext-node-id")
    if node_id is None:
        pytest.skip("OpenText node ID not provided (use --opentext-node-id)")

    try:
        # Call get_opentext_documents with the provided node ID
        documents = opentext_conn.get_opentext_documents(opentext_nodes=[node_id])

        # Should return a non-empty dictionary
        assert isinstance(documents, dict)
        assert len(documents) > 0, (
            f"No documents found in OpenText node {node_id}. Expected to find documents for content testing."
        )

        print(f"✅ Discovered {len(documents)} documents from OpenText node {node_id}")

        # Test downloading content from the first document
        first_uri = next(iter(documents.keys()))
        first_display_name = documents[first_uri]

        print(f"🔍 Testing document content download for: {first_uri}")
        print(f"  - Display name: {first_display_name}")

        # Test the downloader directly (it's callable)
        # This should trigger the download
        local_path = opentext_conn(first_uri)

        # Verify the file was downloaded and exists
        assert local_path is not None, f"local_path should not be None for document {first_uri}"
        assert local_path.exists(), f"Downloaded file should exist at {local_path}"
        assert local_path.is_file(), f"Downloaded path should be a file: {local_path}"

        # Check that the file has some content
        file_size = local_path.stat().st_size
        assert file_size > 0, f"Downloaded file should not be empty. Size: {file_size} bytes"

        print("✅ Successfully downloaded document content:")
        print(f"  - Local path: {local_path}")
        print(f"  - File size: {file_size} bytes")
        print(f"  - File extension: {local_path.suffix}")

    except Exception as e:
        error_msg = str(e).lower()
        # Provide helpful diagnostic messages but still fail the test
        if "401" in error_msg or "unauthorized" in error_msg:
            print("✅ OpenText API deployment successful!")
            print(f"❌ Access denied for content in node {node_id} (expected in enterprise environments)")
            print("🔒 This proves authentication works, but we don't have content access to this specific node")
            pytest.fail(f"Access denied for OpenText node {node_id} content: {e}")
        elif "404" in error_msg or "not found" in error_msg:
            print("✅ OpenText API deployment successful!")
            print(f"❌ Document content not found for node {node_id} (may have been deleted or doesn't exist)")
            pytest.fail(f"OpenText document content not found for node {node_id}: {e}")
        else:
            # Re-raise unexpected errors
            raise


@pytest.mark.deployment
def test_get_opentext_documents_deployment(opentext_conn, pytestconfig):
    """Test get_opentext_documents with real OpenText API using provided node ID."""
    if opentext_conn is None:
        pytest.skip("Deployment tests not enabled (use --run-deployment)")

    node_id = pytestconfig.getoption("--opentext-node-id")
    if node_id is None:
        pytest.skip("OpenText node ID not provided (use --opentext-node-id)")

    try:
        # Call get_opentext_documents with the provided node ID
        documents = opentext_conn.get_opentext_documents(opentext_nodes=[node_id])

        # Should return a non-empty dictionary - we expect documents for the provided node
        assert isinstance(documents, dict)
        assert len(documents) > 0, (
            f"No documents found in OpenText node {node_id}. Expected to find documents for testing."
        )

        # Verify structure of returned documents
        for uri, display_name in documents.items():
            # URI should start with the expected prefix
            assert uri.startswith("opentext://")

            # Should be a string display name
            assert isinstance(display_name, str)

        print(f"✅ Successfully discovered {len(documents)} documents from OpenText node {node_id}")

        # Check document names and extensions in one pass
        missing_ext_docs = []

        for uri, display_name in documents.items():
            print(f"  - {uri} -> {display_name}")

            # Check if document has a valid file extension in the display name
            if "." in display_name and display_name.split(".")[-1]:
                print(f"    ✅ Has extension: .{display_name.split('.')[-1]}")
            else:
                print("    ❌ Missing file extension")
                missing_ext_docs.append(f"URI: {uri}")

        # Fail if any documents are missing extensions
        if missing_ext_docs:
            pytest.fail(
                f"Documents missing file extensions: {', '.join(missing_ext_docs)}. "
                f"All documents should have proper file extensions for processing."
            )

    except Exception as e:
        error_msg = str(e).lower()
        # Provide helpful diagnostic messages but still fail the test
        if "401" in error_msg or "unauthorized" in error_msg:
            print("✅ OpenText API deployment successful!")
            print(f"❌ Access denied for node {node_id} (expected in enterprise environments)")
            print("🔒 This proves authentication works, but we don't have access to this specific node")
            pytest.fail(f"Access denied for OpenText node {node_id}: {e}")
        elif "404" in error_msg or "not found" in error_msg:
            print("✅ OpenText API deployment successful!")
            print(f"❌ Node {node_id} not found (may have been deleted or doesn't exist)")
            pytest.fail(f"OpenText node {node_id} not found: {e}")
        else:
            # Re-raise unexpected errors
            raise


@pytest.mark.deployment
def test_full_opentext_to_snowflake_pipeline(opentext_conn, snowflake_conn, test_schema, test_config, pytestconfig):
    """Test complete pipeline: OpenText document discovery -> Snowflake data loading.

    This test requires both OpenText and Snowflake to be configured:
    - OpenText: environment variables must be set
    - Snowflake: --snowflake-connection-name must be provided
    - Node ID: --opentext-node-id must be provided
    """
    # Skip if deployment tests not enabled
    if opentext_conn is None or snowflake_conn is None:
        pytest.skip("Full deployment test requires both OpenText and Snowflake (use --run-deployment)")

    node_id = pytestconfig.getoption("--opentext-node-id")
    if node_id is None:
        pytest.skip("Full deployment test requires OpenText node ID (use --opentext-node-id)")

    print("🚀 Starting full OpenText -> Snowflake deployment pipeline test")
    print(f"📁 OpenText Node ID: {node_id}")
    print(f"❄️  Snowflake Schema: {test_schema}")

    # Step 1: Use Snowflake configuration from fixture
    print(f"📋 Loaded Snowflake config for schema: {test_config['schema']}")

    # Step 2: Discover OpenText documents
    print(f"🔍 Discovering documents from OpenText node {node_id}...")
    documents = opentext_conn.get_opentext_documents(opentext_nodes=[node_id])

    print(f"✅ Discovered {len(documents)} documents from OpenText")
    assert len(documents) > 0, f"Expected to find documents in OpenText node {node_id} for full pipeline test"

    # Log discovered documents
    for i, uri in enumerate(list(documents.keys())[:5], 1):  # Show first 5
        print(f"  {i}. {uri}")
    if len(documents) > 5:
        print(f"  ... and {len(documents) - 5} more documents")

    # Step 3: Process documents into Snowflake (tables already truncated by fixture)
    print("📤 Processing documents into Snowflake...")

    process_changed_documents(
        documents,
        connection=snowflake_conn,
        downloader=opentext_conn,
        prefix="opentext://",
        config=test_config,
    )

    print(f"✅ Processed {len(documents)} documents into Snowflake")

    # Step 4: Verify data was loaded
    print("🔍 Verifying data was loaded into Snowflake...")
    with snowflake_conn.cursor() as cursor:
        # Check the actual document tables for loaded records
        cursor.execute(f"SELECT COUNT(*) FROM {test_schema}.document_metadata")
        metadata_count = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM {test_schema}.document_text")
        text_count = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM {test_schema}.document_chunks")
        chunk_count = cursor.fetchone()[0]

        print(f"📊 Document metadata: {metadata_count} records")
        print(f"📊 Document text: {text_count} records")
        print(f"📊 Document chunks: {chunk_count} records")

        # Verify we have data
        assert metadata_count > 0, "Expected document metadata to be loaded, but document_metadata table is empty"
        assert text_count > 0, "Expected document text to be loaded, but document_text table is empty"
        assert chunk_count > 0, "Expected chunks to be loaded, but document_chunks table is empty"

        # Show sample data
        cursor.execute(f"SELECT source_uri, LEFT(generated_metadata, 50) FROM {test_schema}.document_metadata LIMIT 3")
        sample_docs = cursor.fetchall()
        print("📄 Sample loaded documents:")
        for doc in sample_docs:
            print(f"  - {doc[0]} -> {doc[1]}...")

    print("🎉 Full OpenText -> Snowflake deployment pipeline test completed successfully!")
    print(f"📈 Summary: {len(documents)} OpenText documents -> {metadata_count} DB metadata -> {chunk_count} DB chunks")
