"""Deployment tests for OpenText document ingestion functionality."""

from itertools import islice

import pytest

from snowdoc.ingest_opentext import OpenTextDownloader
from snowdoc.common import process_changed_documents


def test_opentext_conn_fixture_deployment(opentext_conn):
    """Test that opentext_conn fixture works properly for deployment tests."""
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


def test_opentext_document_content_download(opentext_conn, opentext_node_id):
    """Test downloading actual document content from OpenText using provided node ID."""
    try:
        # Call get_opentext_documents with the provided node ID
        documents_gen = opentext_conn.get_opentext_documents(opentext_nodes=[opentext_node_id])

        # Get the first document for content testing
        try:
            first_uri, first_display_name, first_metadata = next(documents_gen)
        except StopIteration:
            pytest.fail(
                f"No documents found in OpenText node {opentext_node_id}. Expected to find a document for content testing."
            )

        print(f"✅ Found document from OpenText node {opentext_node_id}")

        # Test downloading content from the first document

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
            print(f"❌ Access denied for content in node {opentext_node_id} (expected in enterprise environments)")
            print("🔒 This proves authentication works, but we don't have content access to this specific node")
            pytest.fail(f"Access denied for OpenText node {opentext_node_id} content: {e}")
        elif "404" in error_msg or "not found" in error_msg:
            print("✅ OpenText API deployment successful!")
            print(f"❌ Document content not found for node {opentext_node_id} (may have been deleted or doesn't exist)")
            pytest.fail(f"OpenText document content not found for node {opentext_node_id}: {e}")
        else:
            # Re-raise unexpected errors
            raise


def test_get_opentext_documents_deployment(opentext_conn, opentext_node_id):
    """Test get_opentext_documents with real OpenText API using provided node ID."""
    try:
        # Call get_opentext_documents with the provided node ID
        documents_gen = opentext_conn.get_opentext_documents(opentext_nodes=[opentext_node_id])

        # Get the first document for testing
        try:
            first_uri, first_display_name, first_metadata = next(documents_gen)
        except StopIteration:
            pytest.fail(
                f"No documents found in OpenText node {opentext_node_id}. Expected to find a document for testing."
            )

        # Verify structure of the returned document
        # URI should start with the expected prefix
        assert first_uri.startswith("opentext://")

        # Should be a string display name
        assert isinstance(first_display_name, str)

        # Should have metadata dict
        assert isinstance(first_metadata, dict), f"Expected metadata dict, got {type(first_metadata)}"

        print(f"✅ Successfully discovered document from OpenText node {opentext_node_id}")
        print(f"  - {first_uri} -> {first_display_name}")
        print(f"  - Metadata: {first_metadata}")

        # Check if document has a valid file extension in the display name
        if "." in first_display_name and first_display_name.split(".")[-1]:
            print(f"    ✅ Has extension: .{first_display_name.split('.')[-1]}")
        else:
            print("    ❌ Missing file extension")
            pytest.fail(
                f"Document missing file extension: URI: {first_uri}. "
                f"Document should have proper file extension for processing."
            )

    except Exception as e:
        error_msg = str(e).lower()
        # Provide helpful diagnostic messages but still fail the test
        if "401" in error_msg or "unauthorized" in error_msg:
            print("✅ OpenText API deployment successful!")
            print(f"❌ Access denied for node {opentext_node_id} (expected in enterprise environments)")
            print("🔒 This proves authentication works, but we don't have access to this specific node")
            pytest.fail(f"Access denied for OpenText node {opentext_node_id}: {e}")
        elif "404" in error_msg or "not found" in error_msg:
            print("✅ OpenText API deployment successful!")
            print(f"❌ Node {opentext_node_id} not found (may have been deleted or doesn't exist)")
            pytest.fail(f"OpenText node {opentext_node_id} not found: {e}")
        else:
            # Re-raise unexpected errors
            raise


def test_full_opentext_to_snowflake_pipeline(opentext_conn, opentext_node_id, snowflake_conn, test_config):
    """Test complete pipeline: OpenText document discovery -> Snowflake data loading.

    This test requires both OpenText and Snowflake to be configured:
    - OpenText: environment variables must be set
    - Snowflake: --snowflake-connection-name must be provided
    - Node ID: --opentext-node-id must be provided
    """
    print("🚀 Starting full OpenText -> Snowflake deployment pipeline test")
    print(f"📁 OpenText Node ID: {opentext_node_id}")

    # Step 1: Use Snowflake configuration from fixture
    print(f"📋 Loaded Snowflake config for schema: {test_config['schema']}")

    # Step 2: Discover OpenText documents
    print(f"🔍 Discovering document from OpenText node {opentext_node_id}...")
    documents_gen = opentext_conn.get_opentext_documents(opentext_nodes=[opentext_node_id])

    print("✅ Got OpenText documents generator")

    # Step 3: Process documents into Snowflake (tables already truncated by fixture)
    print("📤 Processing documents into Snowflake...")

    process_changed_documents(
        islice(documents_gen, 1),  # Only process one OpenText document
        connection=snowflake_conn,
        downloader=opentext_conn,
        prefix="opentext://",
        config=test_config,
    )

    print("✅ Processed document into Snowflake")

    # Step 4: Verify data was loaded
    print("🔍 Verifying data was loaded into Snowflake...")
    with snowflake_conn.cursor() as cursor:
        # Check the actual document tables for loaded records
        cursor.execute("SELECT COUNT(*) FROM test_document_text")
        text_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM test_document_chunks")
        chunk_count = cursor.fetchone()[0]

        print(f"📊 Document text: {text_count} records")
        print(f"📊 Document chunks: {chunk_count} records")

        # Verify we have exactly one document processed
        assert text_count == 1, f"Expected exactly 1 document text record, but got {text_count}"
        assert chunk_count >= 1, f"Expected at least 1 chunk record, but got {chunk_count}"

    print("🎉 Full OpenText -> Snowflake deployment pipeline test completed successfully!")
    print(f"📈 Summary: 1 OpenText document -> {chunk_count} DB chunks")
