import pytest
from datetime import datetime, timezone

from snowflake_document_agent.common import (
    stage_document,
    update_document_metadata,
    update_document_text,
    parse_document,
    clear_stage,
)


@pytest.mark.deployment
def test_clear_stage_basic(snowflake_conn, test_schema, tmp_path):
    """
    Test that clear_stage properly removes all files from the @documents stage.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Setup - Create and stage some test files
        test_file1 = tmp_path / "test1.txt"
        test_file1.write_text("Test content 1")
        test_file2 = tmp_path / "test2.txt"
        test_file2.write_text("Test content 2")

        # Stage both files
        stage_document(cursor=cursor, source_uri="test://clear/test1.txt", local_path=test_file1)
        stage_document(cursor=cursor, source_uri="test://clear/test2.txt", local_path=test_file2)

        # Verify files exist in stage
        cursor.execute("LIST @documents")
        files_before = cursor.fetchall()
        assert len(files_before) >= 2, f"Expected at least 2 files in stage, found {len(files_before)}"

        # Execute - Clear the stage
        clear_stage(cursor)

        # Verify - Stage should be empty
        cursor.execute("LIST @documents")
        files_after = cursor.fetchall()
        assert len(files_after) == 0, f"Expected empty stage after clear, found {len(files_after)} files: {files_after}"


@pytest.mark.deployment
def test_stage_document_basic(snowflake_conn, test_schema, tmp_path):
    """
    Test that stage_document properly uploads a file to the @documents stage.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Setup - Create a test document
        test_file = tmp_path / "test_doc.txt"
        test_file.write_text("Test document content for stage_document test.")

        source_uri = "test://integration/test_doc.txt"

        # Execute - Stage the document
        stage_path = stage_document(
            cursor=cursor,
            source_uri=source_uri,
            local_path=test_file,
        )

        # Verify - Check that the stage path is returned correctly
        expected_stage_path = "integration/test_doc.txt"
        assert stage_path == expected_stage_path, f"Expected stage path '{expected_stage_path}', got '{stage_path}'"

        # Verify - Check that file exists in the @documents stage
        cursor.execute(f"LIST @documents/{expected_stage_path}")
        stage_results = cursor.fetchall()
        assert len(stage_results) == 1, f"Expected 1 file in stage, found {len(stage_results)}"


@pytest.mark.deployment
def test_update_document_metadata_basic(snowflake_conn, test_schema, tmp_path):
    """
    Test that update_document_metadata properly inserts/updates metadata in the document_metadata table.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Setup
        source_uri = "test://integration/metadata_test.txt"
        modified_time = datetime.now(timezone.utc)
        test_metadata = '{"test": "metadata"}'

        # Execute - Insert new metadata
        update_document_metadata(
            cursor=cursor,
            source_uri=source_uri,
            modified_at_utc=modified_time,
            insert=True,
            metadata=test_metadata,
        )

        # Verify - Check that metadata was inserted into document_metadata table
        cursor.execute("SELECT source_uri, metadata FROM document_metadata WHERE source_uri = :1", (source_uri,))
        metadata_result = cursor.fetchone()
        assert metadata_result is not None, "No metadata found in document_metadata table"
        assert metadata_result[0] == source_uri, f"Expected source_uri '{source_uri}', got '{metadata_result[0]}'"
        assert test_metadata in str(metadata_result[1]), f"Expected metadata to contain '{test_metadata}'"

        # Execute - Update existing metadata
        updated_metadata = '{"test": "updated_metadata"}'
        updated_time = datetime.now(timezone.utc)

        update_document_metadata(
            cursor=cursor,
            source_uri=source_uri,
            modified_at_utc=updated_time,
            insert=False,
            metadata=updated_metadata,
        )

        # Verify - Check that metadata was updated
        cursor.execute("SELECT source_uri, metadata FROM document_metadata WHERE source_uri = :1", (source_uri,))
        updated_result = cursor.fetchone()
        assert updated_result is not None, "No metadata found after update"
        assert updated_metadata in str(updated_result[1]), f"Expected metadata to contain '{updated_metadata}'"


@pytest.mark.deployment
def test_update_document_text_basic(snowflake_conn, test_schema, tmp_path):
    """
    Test that update_document_text properly inserts/updates text in the document_text table.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Setup
        source_uri = "test://integration/text_test.txt"
        original_text = "This is the original document text content."

        # Execute - Insert new text
        update_document_text(
            cursor=cursor,
            source_uri=source_uri,
            text=original_text,
            insert=True,
        )

        # Verify - Check that text was inserted into document_text table
        cursor.execute("SELECT source_uri, document_text FROM document_text WHERE source_uri = :1", (source_uri,))
        text_result = cursor.fetchone()
        assert text_result is not None, "No text found in document_text table"
        assert text_result[0] == source_uri, f"Expected source_uri '{source_uri}', got '{text_result[0]}'"
        assert text_result[1] == original_text, f"Expected text content '{original_text}', got '{text_result[1]}'"

        # Execute - Update existing text
        updated_text = "This is the updated document text content."

        update_document_text(
            cursor=cursor,
            source_uri=source_uri,
            text=updated_text,
            insert=False,
        )

        # Verify - Check that text was updated
        cursor.execute("SELECT source_uri, document_text FROM document_text WHERE source_uri = :1", (source_uri,))
        updated_result = cursor.fetchone()
        assert updated_result is not None, "No text found after update"
        assert updated_result[1] == updated_text, f"Expected updated text '{updated_text}', got '{updated_result[1]}'"


@pytest.mark.deployment
def test_parse_document_basic(snowflake_conn, test_schema, tmp_path):
    """
    Test that parse_document properly parses a staged PDF using Cortex and stores the result.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Setup - Use fixture PDF
        from pathlib import Path

        fixture_pdf = Path(__file__).parent.parent / "fixtures" / "cuad-sponsorship.pdf"
        assert fixture_pdf.exists(), f"Fixture file not found: {fixture_pdf}"

        source_uri = "test://parse/cuad-sponsorship.pdf"

        # Step 1 - Stage the document first (using our working stage_document function)
        stage_path = stage_document(
            cursor=cursor,
            source_uri=source_uri,
            local_path=fixture_pdf,
        )

        # Step 2 - Parse the staged document
        parse_document(
            cursor=cursor,
            source_uri=source_uri,
            stage_path=stage_path,
            insert=True,
        )

        # Verify - Check that parsed content was stored in document_text table
        cursor.execute("SELECT source_uri, document_text FROM document_text WHERE source_uri = :1", (source_uri,))
        parse_result = cursor.fetchone()
        assert parse_result is not None, "No parsed content found in document_text table"
        assert parse_result[0] == source_uri, f"Expected source_uri '{source_uri}', got '{parse_result[0]}'"

        # Verify content was actually parsed (not empty and no error messages)
        parsed_text = parse_result[1]
        assert parsed_text is not None and parsed_text.strip() != "", "Parsed content is empty"
        assert len(parsed_text.strip()) > 50, (
            f"Parsed content too short, got {len(parsed_text)} chars: {parsed_text[:100]}..."
        )

        print(f"Successfully parsed {len(parsed_text)} characters from PDF")


@pytest.mark.deployment
def test_parse_document_error_handling(snowflake_conn, test_schema, tmp_path):
    """
    Test that parse_document properly handles invalid files with descriptive error messages.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Test Case 1: Empty file
        empty_file = tmp_path / "empty.pdf"
        empty_file.write_bytes(b"")

        source_uri_empty = "test://error/empty.pdf"
        stage_path_empty = stage_document(
            cursor=cursor,
            source_uri=source_uri_empty,
            local_path=empty_file,
        )

        with pytest.raises(RuntimeError) as exc_info:
            parse_document(
                cursor=cursor,
                source_uri=source_uri_empty,
                stage_path=stage_path_empty,
                insert=True,
            )

        error_msg = str(exc_info.value)
        assert "parsing failed" in error_msg.lower(), f"Expected parsing error message, got: {error_msg}"
        assert source_uri_empty in error_msg, f"Expected source URI in error message, got: {error_msg}"

        # Test Case 2: Plain text file (not a PDF)
        text_file = tmp_path / "not_a_pdf.pdf"
        text_file.write_text("This is just plain text, not a PDF document at all.")

        source_uri_text = "test://error/not_a_pdf.pdf"
        stage_path_text = stage_document(
            cursor=cursor,
            source_uri=source_uri_text,
            local_path=text_file,
        )

        with pytest.raises(RuntimeError) as exc_info:
            parse_document(
                cursor=cursor,
                source_uri=source_uri_text,
                stage_path=stage_path_text,
                insert=True,
            )

        error_msg = str(exc_info.value)
        assert "parsing failed" in error_msg.lower(), f"Expected parsing error message, got: {error_msg}"
        assert source_uri_text in error_msg, f"Expected source URI in error message, got: {error_msg}"

        print("Error handling tests passed - invalid files properly rejected")
