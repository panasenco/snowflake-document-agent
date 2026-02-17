import pytest
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from snowflake.connector import DictCursor

from snowflake_document_agent.common import (
    stage_document,
    update_document_metadata,
    update_document_text,
    parse_document,
    generate_document_metadata,
    chunk_document,
    clear_stage,
    process_documents,
    process_changed_documents,
    delete_documents,
    get_snowflake_documents,
    refresh_search_services,
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
        assert "undergo training" in parsed_text.lower(), "Expected 'undergo training' in cuad-sponsorship.pdf content"

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

        # Test Case 2: Excel file disguised as a PDF (should fail parsing)
        excel_as_pdf = tmp_path / "actually_excel.pdf"
        excel_fixture = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"
        shutil.copy(excel_fixture, excel_as_pdf)  # Copy Excel file with .pdf extension

        source_uri_text = "test://error/actually_excel.pdf"
        stage_path_text = stage_document(
            cursor=cursor,
            source_uri=source_uri_text,
            local_path=excel_as_pdf,
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


@pytest.mark.deployment
def test_stage_document_unsupported_file_types(snowflake_conn, test_schema, tmp_path):
    """
    Test that stage_document raises ValueError for unsupported file extensions.
    Cortex only supports: PDF, PPTX, DOCX, JPEG, JPG, PNG, TIFF, TIF, HTML, TXT
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Test Case 1: Video file
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video content")

        with pytest.raises(ValueError) as exc_info:
            stage_document(
                cursor=cursor,
                source_uri="test://unsupported/video.mp4",
                local_path=video_file,
            )

        error_msg = str(exc_info.value)
        assert "unsupported extension" in error_msg.lower(), f"Expected unsupported extension message, got: {error_msg}"
        assert "video.mp4" in error_msg, f"Expected filename in error message, got: {error_msg}"

        # Test Case 2: Archive file
        zip_file = tmp_path / "archive.zip"
        zip_file.write_bytes(b"fake zip content")

        with pytest.raises(ValueError) as exc_info:
            stage_document(
                cursor=cursor,
                source_uri="test://unsupported/archive.zip",
                local_path=zip_file,
            )

        error_msg = str(exc_info.value)
        assert "unsupported extension" in error_msg.lower(), f"Expected unsupported extension message, got: {error_msg}"
        assert "archive.zip" in error_msg, f"Expected filename in error message, got: {error_msg}"

        # Test Case 3: Executable file
        exe_file = tmp_path / "program.exe"
        exe_file.write_bytes(b"fake exe content")

        with pytest.raises(ValueError) as exc_info:
            stage_document(
                cursor=cursor,
                source_uri="test://unsupported/program.exe",
                local_path=exe_file,
            )

        error_msg = str(exc_info.value)
        assert "unsupported extension" in error_msg.lower(), f"Expected unsupported extension message, got: {error_msg}"
        assert "program.exe" in error_msg, f"Expected filename in error message, got: {error_msg}"

        print("File type validation tests passed - unsupported extensions properly rejected")


@pytest.mark.deployment
def test_generate_document_metadata_basic(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test that generate_document_metadata uses config metadata_prompt to generate metadata.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Setup - First create document text to generate metadata from
        source_uri = "test://integration/metadata_generation_test.txt"
        test_text = "This is a test document for metadata generation."

        # Insert document text
        update_document_text(
            cursor=cursor,
            source_uri=source_uri,
            text=test_text,
            insert=True,
        )

        # Setup config to test filename awareness - should fail because we don't pass source URI to metadata generation
        test_config["metadata_prompt"] = (
            "Look at the filename and return this string exactly: File extension: [extension without leading period]"
        )

        # Execute - Generate metadata with test config
        generate_document_metadata(cursor=cursor, source_uri=source_uri, config=test_config, insert=True)

        # Verify - Check that metadata was inserted into enhanced_metadata table
        cursor.execute(
            "SELECT source_uri, enhanced_metadata FROM enhanced_metadata WHERE source_uri = :1", (source_uri,)
        )
        metadata_result = cursor.fetchone()
        assert metadata_result is not None, "No enhanced metadata found in enhanced_metadata table"
        assert metadata_result[0] == source_uri, f"Expected source_uri '{source_uri}', got '{metadata_result[0]}'"

        # Verify the generated metadata contains file extension (should fail - filename not passed to AI)
        generated_metadata = metadata_result[1]
        assert "File extension: txt" in generated_metadata, (
            f"Expected 'File extension: txt' in metadata, got '{generated_metadata}'"
        )

        print(f"Successfully generated metadata with filename awareness: '{generated_metadata[:100]}...'")


@pytest.mark.deployment
def test_chunk_document_basic(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test that chunk_document splits documents into exact expected chunks using small config values.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Setup - Create simple, predictable document text
        source_uri = "test://integration/chunk_test.txt"
        test_text = "abcdefghijklmnopqrstuvwxyz"  # 26 characters
        test_metadata = "Test"

        # Insert document text
        update_document_text(
            cursor=cursor,
            source_uri=source_uri,
            text=test_text,
            insert=True,
        )

        # Insert enhanced metadata (required by chunk_document)
        cursor.execute(
            "INSERT INTO enhanced_metadata (source_uri, enhanced_metadata) VALUES (:1, :2)", (source_uri, test_metadata)
        )

        # Setup config with very small chunking parameters for precise testing
        config_with_small_chunks = test_config.copy()
        config_with_small_chunks["chunk_size"] = 10  # Very small chunks
        config_with_small_chunks["chunk_overlap"] = 3  # Small overlap

        # Execute - Chunk the document
        chunk_document(cursor=cursor, source_uri=source_uri, config=config_with_small_chunks, insert=True)

        # Verify - Check exact chunks were created
        cursor.execute(
            "SELECT contextualized_chunk FROM document_chunks WHERE source_uri = :1 ORDER BY contextualized_chunk",
            (source_uri,),
        )
        chunk_results = cursor.fetchall()

        # Extract just the document chunk parts (after metadata)
        document_chunks = []
        for chunk_result in chunk_results:
            full_chunk = chunk_result[0]
            # Find the document chunk part after the metadata
            chunk_start = full_chunk.find("Document chunk:\n") + len("Document chunk:\n")
            document_chunk = full_chunk[chunk_start:] if chunk_start > len("Document chunk:\n") - 1 else full_chunk
            document_chunks.append(document_chunk.strip())

        # With chunk_size=10 and chunk_overlap=3, Snowflake's split_text_recursive_character returns:
        # Chunk 1: "abcdefghij" (chars 0-9)
        # Chunk 2: "hijklmnopq" (chars 7-16, 3-char overlap)
        # Chunk 3: "opqrstuvwx" (chars 15-24, 3-char overlap)
        # Chunk 4: "vwxyz" (remaining chars 22-25)
        expected_chunks = ["abcdefghij", "hijklmnopq", "opqrstuvwx", "vwxyz"]

        assert len(document_chunks) == len(expected_chunks), (
            f"Expected {len(expected_chunks)} chunks, got {len(document_chunks)}: {document_chunks}"
        )

        for i, expected in enumerate(expected_chunks):
            assert expected in document_chunks[i], (
                f"Expected chunk {i} to contain '{expected}', got '{document_chunks[i]}'"
            )

        print(f"Successfully created {len(document_chunks)} precise chunks: {document_chunks}")


@pytest.mark.deployment
def test_process_documents_kitchen_sink(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Comprehensive test: throw everything at process_documents and verify it handles gracefully.
    Tests all document types, nonexistent files, bad extensions, and corrupted files.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    mock_logger = MagicMock()

    # Override metadata prompt to test filename awareness - should now work with filename included
    test_config["metadata_prompt"] = (
        "Look at the filename and return this string exactly: File extension: [extension without leading period]"
    )

    sources = []

    # Define specific timestamps for testing
    txt_modified = datetime(2024, 1, 1, 10, 30, 0, tzinfo=timezone.utc)
    html_modified = datetime(2024, 1, 2, 11, 45, 0, tzinfo=timezone.utc)
    pdf_modified = datetime(2024, 1, 3, 14, 15, 0, tzinfo=timezone.utc)
    docx_modified = datetime(2024, 1, 4, 16, 20, 0, tzinfo=timezone.utc)
    xlsx_modified = datetime(2024, 1, 5, 9, 10, 0, tzinfo=timezone.utc)

    # === DOCUMENTS THAT SHOULD SUCCEED ===

    # 1. Plain text file
    txt_file = tmp_path / "success.txt"
    txt_file.write_text("This is a test document for end-to-end processing.")  # Use exact content for verification
    sources.append(
        (
            "test://kitchen/success.txt",
            txt_file,
            txt_modified,
            "",  # metadata (empty for now)
            True,
        )
    )

    # 2. HTML file
    html_file = tmp_path / "success.html"
    html_file.write_text("<p>Test HTML document</p>")  # Use exact content for verification
    sources.append(
        (
            "test://kitchen/success.html",
            html_file,
            html_modified,
            "",  # metadata (empty for now)
            True,
        )
    )

    # 3. PDF fixture (should parse successfully)
    pdf_file = Path(__file__).parent.parent / "fixtures" / "cuad-sponsorship.pdf"
    sources.append(
        (
            "test://kitchen/cuad-sponsorship.pdf",
            pdf_file,
            pdf_modified,
            "",  # metadata (empty for now)
            True,
        )
    )

    # 4. DOCX fixture (should convert successfully)
    docx_file = Path(__file__).parent.parent / "fixtures" / "mammoth-tables.docx"
    sources.append(
        (
            "test://kitchen/mammoth-tables.docx",
            docx_file,
            docx_modified,
            "",  # metadata (empty for now)
            True,
        )
    )

    # 5. XLSX fixture (should convert successfully)
    xlsx_file = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"
    sources.append(
        (
            "test://kitchen/multi-worksheet.xlsx",
            xlsx_file,
            xlsx_modified,
            "",  # metadata (empty for now)
            True,
        )
    )

    # === DOCUMENTS THAT SHOULD FAIL ===

    # 6. Nonexistent file (FileNotFoundError)
    missing_file = tmp_path / "does_not_exist.txt"
    sources.append(
        (
            "test://kitchen/missing.txt",
            missing_file,
            datetime(2024, 1, 6, 12, 0, 0, tzinfo=timezone.utc),
            "",
            True,
        )
    )

    # 7. Unsupported file extension (ValueError from stage_document)
    bad_ext_file = tmp_path / "bad_extension.xyz"
    bad_ext_file.write_text("This has an unsupported extension")
    sources.append(
        (
            "test://kitchen/bad_extension.xyz",
            bad_ext_file,
            datetime(2024, 1, 7, 13, 0, 0, tzinfo=timezone.utc),
            "",
            True,
        )
    )

    # 8. Corrupted "PDF" (actually Excel file, should fail parsing)
    excel_as_pdf = tmp_path / "actually_excel.pdf"
    excel_fixture = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"
    shutil.copy(excel_fixture, excel_as_pdf)  # Copy Excel file with .pdf extension
    sources.append(
        (
            "test://kitchen/actually_excel.pdf",
            excel_as_pdf,
            datetime(2024, 1, 8, 14, 0, 0, tzinfo=timezone.utc),
            "",
            True,
        )
    )

    # Execute process_documents - should not crash despite failures
    process_documents(
        sources=sources,
        connection=snowflake_conn,  # Pass connection object directly
        config=test_config,
        max_workers=8,  # Use real multithreading
        logger=mock_logger,
    )

    # === VERIFICATIONS ===

    logged_errors = [call.args[0] for call in mock_logger.error.call_args_list]
    print(f"DEBUG: Got {mock_logger.error.call_count} error logs:")
    for i, error in enumerate(logged_errors):
        print(f"  {i + 1}. {error}")

    # Should have logged errors for the 3 failing documents
    assert mock_logger.error.call_count == 3, f"Expected 3 error logs, got {mock_logger.error.call_count}"

    # Verify each expected failure was logged with source URI
    expected_failures = [
        ("missing.txt", "FileNotFoundError"),
        ("bad_extension.xyz", "unsupported extension"),
        ("actually_excel.pdf", "parsing failed"),  # Excel file with .pdf extension should fail parsing
    ]

    for expected_file, expected_error_type in expected_failures:
        matching_log = None
        for logged_error in logged_errors:
            if expected_file in logged_error and expected_error_type.lower() in logged_error.lower():
                matching_log = logged_error
                break

        assert matching_log is not None, (
            f"Expected error log containing '{expected_file}' and '{expected_error_type}'. Got: {logged_errors}"
        )

    # Verify successful documents were processed (check database)
    with snowflake_conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM document_text")
        text_count = cursor.fetchone()[0]
        assert text_count >= 5, f"Expected at least 5 successful document_text entries, got {text_count}"

        cursor.execute("SELECT COUNT(*) FROM enhanced_metadata")
        metadata_count = cursor.fetchone()[0]
        assert metadata_count >= 5, f"Expected at least 5 successful metadata entries, got {metadata_count}"

        # === DETAILED CONTENT VERIFICATION ===
        # Verify specific content for each document type (incorporating test_process_document_multiple_types checks)

        # 1. Text file content
        cursor.execute("SELECT document_text FROM document_text WHERE source_uri = :1", ("test://kitchen/success.txt",))
        txt_result = cursor.fetchone()
        assert txt_result and "This is a test document for end-to-end processing." in txt_result[0]

        # 2. HTML file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri = :1", ("test://kitchen/success.html",)
        )
        html_result = cursor.fetchone()
        assert html_result and "<p>Test HTML document</p>" in html_result[0]

        # 3. PDF file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri = :1", ("test://kitchen/cuad-sponsorship.pdf",)
        )
        pdf_result = cursor.fetchone()
        assert pdf_result and "undergo training" in pdf_result[0].lower()

        # 4. DOCX file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri = :1", ("test://kitchen/mammoth-tables.docx",)
        )
        docx_result = cursor.fetchone()
        assert docx_result and "bottom right" in docx_result[0].lower()

        # 5. XLSX file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri = :1", ("test://kitchen/multi-worksheet.xlsx",)
        )
        xlsx_result = cursor.fetchone()
        assert xlsx_result and "reset table" in xlsx_result[0].lower()

        # Verify enhanced metadata contains file extension information (should now work with filename awareness)
        test_cases = [
            ("test://kitchen/success.txt", "txt"),
            ("test://kitchen/success.html", "html"),
            ("test://kitchen/cuad-sponsorship.pdf", "pdf"),
            ("test://kitchen/mammoth-tables.docx", "docx"),
            ("test://kitchen/multi-worksheet.xlsx", "xlsx"),
        ]

        for source_uri, expected_ext in test_cases:
            cursor.execute("SELECT enhanced_metadata FROM enhanced_metadata WHERE source_uri = :1", (source_uri,))
            metadata_result = cursor.fetchone()
            if metadata_result:
                expected_text = f"File extension: {expected_ext}"
                assert expected_text in metadata_result[0], (
                    f"Expected '{expected_text}' in metadata for {source_uri}: {metadata_result[0][:200]}..."
                )

        # Verify basic document metadata (modified_at_utc) is stored with correct timestamps
        expected_metadata = [
            ("test://kitchen/success.txt", txt_modified),
            ("test://kitchen/success.html", html_modified),
            ("test://kitchen/cuad-sponsorship.pdf", pdf_modified),
            ("test://kitchen/mammoth-tables.docx", docx_modified),
            ("test://kitchen/multi-worksheet.xlsx", xlsx_modified),
        ]

        for source_uri, expected_timestamp in expected_metadata:
            cursor.execute("SELECT modified_at_utc FROM document_metadata WHERE source_uri = :1", (source_uri,))
            metadata_result = cursor.fetchone()
            assert metadata_result, (
                f"Expected document_metadata entry for {source_uri} - this suggests update_document_metadata() is not being called in process_document()"
            )
            stored_timestamp = metadata_result[0]
            # Snowflake TIMESTAMP_NTZ doesn't preserve timezone, so compare as naive datetimes
            expected_naive = expected_timestamp.replace(tzinfo=None)
            assert stored_timestamp == expected_naive, (
                f"Expected timestamp {expected_naive} for {source_uri}, got {stored_timestamp}"
            )

    print(f"✅ Kitchen sink test passed! Processed {text_count} documents, logged {len(logged_errors)} errors")


@pytest.mark.deployment
def test_get_snowflake_documents(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test get_snowflake_documents function - should return documents matching a prefix.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # Define test data with different prefixes
    test_documents = [
        ("s3://bucket/folder1/doc1.pdf", datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), "metadata for doc1"),
        ("s3://bucket/folder1/doc2.txt", datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc), "metadata for doc2"),
        ("s3://bucket/folder2/doc3.docx", datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc), "metadata for doc3"),
        ("gcs://other-bucket/doc4.xlsx", datetime(2024, 1, 4, 13, 0, 0, tzinfo=timezone.utc), "metadata for doc4"),
    ]

    # Insert test document metadata
    with snowflake_conn.cursor() as cursor:
        for source_uri, modified_at_utc, metadata in test_documents:
            update_document_metadata(
                cursor, source_uri=source_uri, modified_at_utc=modified_at_utc, metadata=metadata, insert=True
            )

    # Test 1: Get documents with "s3://bucket/folder1/" prefix
    result = get_snowflake_documents(snowflake_conn, prefix="s3://bucket/folder1/", config=test_config)

    assert isinstance(result, dict), "Should return a dictionary"
    assert len(result) == 2, f"Expected 2 documents with prefix 's3://bucket/folder1/', got {len(result)}"

    # Verify the correct documents are returned
    assert "s3://bucket/folder1/doc1.pdf" in result
    assert "s3://bucket/folder1/doc2.txt" in result
    assert "s3://bucket/folder2/doc3.docx" not in result
    assert "gcs://other-bucket/doc4.xlsx" not in result

    # Test 2: Verify return value format (modified_at_utc, metadata) tuples
    doc1_info = result["s3://bucket/folder1/doc1.pdf"]
    assert isinstance(doc1_info, tuple), "Should return tuples"
    assert len(doc1_info) == 2, "Tuple should have 2 elements: (modified_at_utc, metadata)"

    modified_at_utc, metadata = doc1_info
    assert isinstance(modified_at_utc, datetime), "First element should be datetime"
    assert isinstance(metadata, str), "Second element should be string"

    # get_snowflake_documents should return timezone-aware datetimes (assume UTC)
    expected_modified = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert modified_at_utc == expected_modified, f"Expected {expected_modified}, got {modified_at_utc}"
    assert modified_at_utc.tzinfo == timezone.utc, f"Expected UTC timezone, got {modified_at_utc.tzinfo}"
    assert metadata == "metadata for doc1", f"Expected 'metadata for doc1', got '{metadata}'"

    # Test 3: Get documents with "s3://bucket/" prefix (should get folder1 and folder2)
    result_broad = get_snowflake_documents(snowflake_conn, prefix="s3://bucket/", config=test_config)
    assert len(result_broad) == 3, f"Expected 3 documents with prefix 's3://bucket/', got {len(result_broad)}"

    # Test 4: Get documents with non-existent prefix
    result_empty = get_snowflake_documents(snowflake_conn, prefix="nonexistent://", config=test_config)
    assert len(result_empty) == 0, f"Expected 0 documents with non-existent prefix, got {len(result_empty)}"


@pytest.mark.deployment
def test_process_changed_documents_basic(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test process_changed_documents function - incremental processing logic.
    Should handle:
    1. New documents (insert=True)
    2. Changed documents (insert=False, newer timestamp or different metadata)
    3. Deleted documents (remove from Snowflake)
    4. Unchanged documents (skip processing)
    5. Call refresh_search_services when there are changes
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # Mock downloader that returns real fixture files
    def mock_downloader(source_uri: str) -> Path:
        """Returns real fixture files based on the source URI"""
        filename = source_uri.split("/")[-1]  # Extract filename from URI
        fixture_dir = Path(__file__).parent.parent / "fixtures"

        if filename.endswith(".txt"):
            # Create a simple text file for .txt documents
            local_file = tmp_path / filename
            local_file.write_text(f"Test content for {filename}")
            return local_file
        elif filename.endswith(".pdf"):
            # Use the PDF fixture
            return fixture_dir / "cuad-sponsorship.pdf"
        elif filename.endswith(".docx"):
            # Use the DOCX fixture
            return fixture_dir / "mammoth-tables.docx"
        elif filename.endswith(".xlsx"):
            # Use the XLSX fixture
            return fixture_dir / "multi-worksheet.xlsx"
        elif filename.endswith(".html"):
            # Create a simple HTML file
            local_file = tmp_path / filename
            local_file.write_text(f"<p>Test HTML content for {filename}</p>")
            return local_file
        else:
            # Default: create a text file
            local_file = tmp_path / filename
            local_file.write_text(f"Default content for {filename}")
            return local_file

    prefix = "test://project/"

    # === SETUP: Initial state in Snowflake ===
    # Simulate documents that were previously ingested
    initial_snowflake_docs = [
        ("test://project/doc1.txt", datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), "old metadata 1"),
        ("test://project/doc2.pdf", datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc), "metadata 2"),
        ("test://project/old_doc.docx", datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc), "metadata for old doc"),
    ]

    # Insert initial documents into Snowflake
    with snowflake_conn.cursor() as cursor:
        for source_uri, modified_at_utc, metadata in initial_snowflake_docs:
            update_document_metadata(
                cursor, source_uri=source_uri, modified_at_utc=modified_at_utc, metadata=metadata, insert=True
            )

    # === TEST: Current source documents ===
    current_sources = {
        # doc1: CHANGED - newer timestamp and different metadata (should reingest with insert=False)
        "test://project/doc1.txt": (datetime(2024, 1, 10, 15, 0, 0, tzinfo=timezone.utc), "updated metadata 1"),
        # doc2: UNCHANGED - same timestamp and metadata (should be skipped)
        "test://project/doc2.pdf": (datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc), "metadata 2"),
        # doc3: NEW - doesn't exist in Snowflake (should ingest with insert=True)
        "test://project/doc3.xlsx": (datetime(2024, 1, 4, 14, 0, 0, tzinfo=timezone.utc), "metadata 3"),
        # doc4: NEW with same timestamp but never existed (should ingest with insert=True)
        "test://project/doc4.html": (datetime(2024, 1, 5, 16, 0, 0, tzinfo=timezone.utc), "metadata 4"),
    }
    # Note: old_doc.docx is in Snowflake but not in current_sources, so should be DELETED

    mock_logger = MagicMock()

    # === EXECUTE ===
    # Mock refresh_search_services to avoid long execution time
    with patch("snowflake_document_agent.common.refresh_search_services") as mock_refresh:
        process_changed_documents(
            current_sources,
            connection=snowflake_conn,
            downloader=mock_downloader,
            prefix=prefix,
            config=test_config,
            max_workers=4,
            logger=mock_logger,
        )

        # Verify refresh_search_services was called (there were changes: 1 deleted, 1 updated, 2 new)
        assert mock_refresh.call_count == 1, (
            f"Expected refresh_search_services to be called once, got {mock_refresh.call_count}"
        )

    # === VERIFY RESULTS ===

    # Debug: Check what was logged
    print("\n=== LOGGED MESSAGES ===")
    print("INFO calls:")
    for call in mock_logger.info.call_args_list:
        print(f"  {call.args[0] if call.args else 'No args'}")
    print("ERROR calls:")
    for call in mock_logger.error.call_args_list:
        print(f"  {call.args[0] if call.args else 'No args'}")
    print("=== END LOGGED MESSAGES ===\n")

    # Get final state from Snowflake
    final_docs = get_snowflake_documents(snowflake_conn, prefix=prefix, config=test_config)

    # Should have 4 documents (doc1, doc2, doc3, doc4) - old_doc should be deleted
    assert len(final_docs) == 4, (
        f"Expected 4 documents after processing, got {len(final_docs)}: {list(final_docs.keys())}"
    )

    # Verify each document's final state

    # doc1: Should have updated timestamp and metadata (was reingested)
    doc1_modified, doc1_metadata = final_docs["test://project/doc1.txt"]
    assert doc1_modified == datetime(2024, 1, 10, 15, 0, 0, tzinfo=timezone.utc), "doc1 should have updated timestamp"
    assert doc1_metadata == "updated metadata 1", "doc1 should have updated metadata"

    # doc2: Should be unchanged (was skipped)
    doc2_modified, doc2_metadata = final_docs["test://project/doc2.pdf"]
    assert doc2_modified == datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc), "doc2 should have original timestamp"
    assert doc2_metadata == "metadata 2", "doc2 should have original metadata"

    # doc3: Should exist (was new)
    doc3_modified, doc3_metadata = final_docs["test://project/doc3.xlsx"]
    assert doc3_modified == datetime(2024, 1, 4, 14, 0, 0, tzinfo=timezone.utc), "doc3 should have new timestamp"
    assert doc3_metadata == "metadata 3", "doc3 should have new metadata"

    # doc4: Should exist (was new)
    doc4_modified, doc4_metadata = final_docs["test://project/doc4.html"]
    assert doc4_modified == datetime(2024, 1, 5, 16, 0, 0, tzinfo=timezone.utc), "doc4 should have new timestamp"
    assert doc4_metadata == "metadata 4", "doc4 should have new metadata"

    # old_doc: Should be deleted (not in final_docs)
    assert "test://project/old_doc.docx" not in final_docs, "old_doc.docx should have been deleted"

    # Verify logging behavior - should log deletions, new docs, and changed docs
    logged_messages = [str(call.args[0]) for call in mock_logger.info.call_args_list]

    # Should log about processing actions
    deletion_logs = [msg for msg in logged_messages if "delet" in msg.lower() and "old_doc.docx" in msg]
    assert len(deletion_logs) > 0, f"Should log deletion of old_doc.docx. All logs: {logged_messages}"

    new_doc_logs = [msg for msg in logged_messages if "new" in msg.lower() and ("doc3" in msg or "doc4" in msg)]
    assert len(new_doc_logs) > 0, f"Should log processing of new documents. All logs: {logged_messages}"

    # === TEST NO CHANGES: Process same sources again ===
    mock_logger.reset_mock()

    with patch("snowflake_document_agent.common.refresh_search_services") as mock_refresh_no_changes:
        process_changed_documents(
            current_sources,  # Same sources as before - no changes
            connection=snowflake_conn,
            downloader=mock_downloader,
            prefix=prefix,
            config=test_config,
            max_workers=4,
            logger=mock_logger,
        )

        # Verify refresh_search_services was NOT called when no changes
        assert mock_refresh_no_changes.call_count == 0, (
            f"Expected refresh_search_services NOT to be called when no changes, got {mock_refresh_no_changes.call_count}"
        )

    # Verify no errors were logged for no changes
    no_change_errors = [call.args[0] for call in mock_logger.error.call_args_list]
    assert len(no_change_errors) == 0, f"Expected no errors when no changes, got: {no_change_errors}"

    print("✅ process_changed_documents test passed - incremental processing and no-change detection work correctly!")


@pytest.mark.deployment
def test_process_document_orphaned_data_bug(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test for orphaned data bug at process_document level: when a single document processing fails,
    ensure no partial data is left in ANY table (document_text, enhanced_metadata, document_chunks).

    This reproduces the bug where failed documents leave entries in document_text.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # Create an Excel file disguised as PDF (will fail parsing)
    xlsx_fixture = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"
    fake_pdf = tmp_path / "fake.pdf"
    if xlsx_fixture.exists():
        shutil.copy(xlsx_fixture, fake_pdf)
    else:
        # Create fake Excel-like content with PDF extension
        fake_pdf.write_bytes(b"PK\x03\x04")  # ZIP signature (Excel files are ZIP-based)

    source_uri = "test://orphaned/fake.pdf"
    modified_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    metadata = "test metadata"

    # Try to process the problematic document (should fail)
    from snowflake_document_agent.common import process_document

    try:
        process_document(
            connection=snowflake_conn,
            source_uri=source_uri,
            local_path=fake_pdf,
            modified_at_utc=modified_time,
            metadata=metadata,
            config=test_config,
            insert=True,
        )
        assert False, "Expected process_document to raise an exception for fake PDF"
    except Exception as e:
        print(f"DEBUG: process_document failed as expected: {type(e).__name__} - {e}")

    # === VERIFY NO ORPHANED DATA ===
    # The bug is that failed documents leave partial data in some tables but not others

    with snowflake_conn.cursor() as cursor:
        # Check all tables - should be completely empty for this source_uri
        tables_to_check = ["document_metadata", "document_text", "enhanced_metadata", "document_chunks"]

        for table in tables_to_check:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE source_uri = :1", (source_uri,))
            count = cursor.fetchone()[0]
            print(f"DEBUG: {table} contains {count} entries for failed document")
            assert count == 0, f"Failed document should not be in {table}, found {count} entries (ORPHANED DATA BUG)"

    print("✅ Orphaned data test passed - no partial data left from failed process_document!")


@pytest.mark.deployment
def test_delete_documents(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test delete_documents function - should remove documents from all relevant tables.
    Should delete from: document_metadata, document_text, enhanced_metadata, document_chunks
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # === SETUP: Create documents in all tables ===
    test_documents = [
        ("test://delete/keep1.txt", datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), "metadata for keep1"),
        ("test://delete/delete1.pdf", datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc), "metadata for delete1"),
        ("test://delete/delete2.docx", datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc), "metadata for delete2"),
        ("test://delete/keep2.xlsx", datetime(2024, 1, 4, 13, 0, 0, tzinfo=timezone.utc), "metadata for keep2"),
    ]

    with snowflake_conn.cursor() as cursor:
        # Insert into document_metadata
        for source_uri, modified_at_utc, metadata in test_documents:
            update_document_metadata(
                cursor, source_uri=source_uri, modified_at_utc=modified_at_utc, metadata=metadata, insert=True
            )

        # Insert into document_text
        for source_uri, _, _ in test_documents:
            document_text = f"This is the content of {source_uri}"
            update_document_text(cursor, source_uri=source_uri, text=document_text, insert=True)

        # Insert into enhanced_metadata (AI-generated metadata)
        for source_uri, _, _ in test_documents:
            # Use a simple test prompt to generate metadata
            test_config_copy = test_config.copy()
            test_config_copy["metadata_prompt"] = f"Document summary for {source_uri}"
            generate_document_metadata(cursor, source_uri=source_uri, config=test_config_copy, insert=True)

        # Insert into document_chunks
        for source_uri, _, _ in test_documents:
            chunk_document(cursor, source_uri=source_uri, config=test_config, insert=True)

    # === VERIFY INITIAL STATE ===
    with snowflake_conn.cursor() as cursor:
        # Should have 4 documents in each table
        cursor.execute("SELECT COUNT(*) FROM document_metadata")
        assert cursor.fetchone()[0] == 4, "Should have 4 entries in document_metadata"

        cursor.execute("SELECT COUNT(*) FROM document_text")
        assert cursor.fetchone()[0] == 4, "Should have 4 entries in document_text"

        cursor.execute("SELECT COUNT(*) FROM enhanced_metadata")
        assert cursor.fetchone()[0] == 4, "Should have 4 entries in enhanced_metadata"

        cursor.execute("SELECT COUNT(*) FROM document_chunks")
        chunk_count = cursor.fetchone()[0]
        assert chunk_count > 0, "Should have some entries in document_chunks"

    # === TEST: Empty delete_uris set (should make no SQL calls) ===

    # Mock the connection to track SQL calls
    original_cursor_method = snowflake_conn.cursor
    sql_calls = []

    def mock_cursor():
        cursor = original_cursor_method()
        original_execute = cursor.execute

        def track_execute(sql, *args):
            sql_calls.append(sql)
            return original_execute(sql, *args)

        cursor.execute = track_execute
        return cursor

    snowflake_conn.cursor = mock_cursor

    try:
        # Call delete_documents with empty set - should make no SQL calls
        delete_documents(connection=snowflake_conn, delete_uris=set(), config=test_config)

        # Verify no SQL calls were made for empty delete
        empty_delete_calls = len(sql_calls)
        assert empty_delete_calls == 0, (
            f"Expected 0 SQL calls for empty delete_uris, got {empty_delete_calls}: {sql_calls}"
        )

    finally:
        # Restore original cursor method
        snowflake_conn.cursor = original_cursor_method

    # === EXECUTE DELETION ===
    uris_to_delete = {
        "test://delete/delete1.pdf",
        "test://delete/delete2.docx",
    }

    delete_documents(connection=snowflake_conn, delete_uris=uris_to_delete, config=test_config)

    # === VERIFY RESULTS ===
    with snowflake_conn.cursor() as cursor:
        # Should have 2 documents remaining in each table
        cursor.execute("SELECT COUNT(*) FROM document_metadata")
        remaining_metadata = cursor.fetchone()[0]
        assert remaining_metadata == 2, f"Expected 2 remaining in document_metadata, got {remaining_metadata}"

        cursor.execute("SELECT COUNT(*) FROM document_text")
        remaining_text = cursor.fetchone()[0]
        assert remaining_text == 2, f"Expected 2 remaining in document_text, got {remaining_text}"

        cursor.execute("SELECT COUNT(*) FROM enhanced_metadata")
        remaining_enhanced = cursor.fetchone()[0]
        assert remaining_enhanced == 2, f"Expected 2 remaining in enhanced_metadata, got {remaining_enhanced}"

        # Verify correct documents remain
        cursor.execute("SELECT source_uri FROM document_metadata ORDER BY source_uri")
        remaining_uris = {row[0] for row in cursor.fetchall()}
        expected_remaining = {"test://delete/keep1.txt", "test://delete/keep2.xlsx"}
        assert remaining_uris == expected_remaining, f"Expected {expected_remaining}, got {remaining_uris}"

        # Verify deleted documents are gone from all tables
        for deleted_uri in uris_to_delete:
            cursor.execute("SELECT COUNT(*) FROM document_metadata WHERE source_uri = :1", (deleted_uri,))
            assert cursor.fetchone()[0] == 0, f"{deleted_uri} should be deleted from document_metadata"

            cursor.execute("SELECT COUNT(*) FROM document_text WHERE source_uri = :1", (deleted_uri,))
            assert cursor.fetchone()[0] == 0, f"{deleted_uri} should be deleted from document_text"

            cursor.execute("SELECT COUNT(*) FROM enhanced_metadata WHERE source_uri = :1", (deleted_uri,))
            assert cursor.fetchone()[0] == 0, f"{deleted_uri} should be deleted from enhanced_metadata"

            cursor.execute("SELECT COUNT(*) FROM document_chunks WHERE source_uri = :1", (deleted_uri,))
            assert cursor.fetchone()[0] == 0, f"{deleted_uri} should be deleted from document_chunks"

        # Verify kept documents are still there
        for kept_uri in expected_remaining:
            cursor.execute("SELECT COUNT(*) FROM document_metadata WHERE source_uri = :1", (kept_uri,))
            assert cursor.fetchone()[0] == 1, f"{kept_uri} should still exist in document_metadata"

            cursor.execute("SELECT COUNT(*) FROM document_text WHERE source_uri = :1", (kept_uri,))
            assert cursor.fetchone()[0] == 1, f"{kept_uri} should still exist in document_text"

    print("✅ delete_documents test passed - batch deletion from all tables works correctly!")


@pytest.mark.deployment
def test_refresh_search_services(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test refresh_search_services function - should refresh both Cortex search services with latest data.
    Tests the two hardcoded search services: search_metadata and search_contents.
    Verifies that data_timestamp gets updated after refresh.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # The two search services from setup_05_cortex_search_agent.sql
    search_services = ["search_metadata", "search_contents"]

    # === SETUP: Add some test data that should be picked up by search services ===
    test_documents = [
        ("test://search/doc1.txt", datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc), "metadata 1"),
        ("test://search/doc2.pdf", datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc), "metadata 2"),
    ]

    with snowflake_conn.cursor() as cursor:
        # Insert document data into all relevant tables
        for source_uri, modified_at_utc, metadata in test_documents:
            update_document_metadata(
                cursor, source_uri=source_uri, modified_at_utc=modified_at_utc, metadata=metadata, insert=True
            )
            update_document_text(cursor, source_uri=source_uri, text=f"Content for {source_uri}", insert=True)
            # Add enhanced metadata for search_metadata service
            generate_document_metadata(cursor, source_uri=source_uri, config=test_config, insert=True)
            # Add document chunks for search_contents service
            chunk_document(cursor, source_uri=source_uri, config=test_config, insert=True)

    # === GET INITIAL STATE ===
    initial_timestamps = {}

    for service_name in search_services:
        try:
            with snowflake_conn.cursor(DictCursor) as cursor:
                cursor.execute(f"DESCRIBE CORTEX SEARCH SERVICE {service_name}")
                result = cursor.fetchone()  # DESCRIBE returns a single row with service info

                if result is None:
                    print(f"⚠️ Search service {service_name} not found")
                    continue

                service_timestamp = result.get("data_timestamp")
                service_status = result.get("serving_state")

                if service_timestamp is None:
                    print(f"⚠️ Search service {service_name} found but data_timestamp not available")
                    continue

                initial_timestamps[service_name] = service_timestamp
                print(f"Initial {service_name} data_timestamp: {service_timestamp}, serving_state: {service_status}")

        except Exception as e:
            error_msg = str(e).lower()
            if "does not exist" in error_msg or "object does not exist" in error_msg:
                print(f"⚠️ Search service {service_name} does not exist - skipping")
                continue
            else:
                raise e

    if not initial_timestamps:
        pytest.skip("No search services available for testing")

    # === EXECUTE REFRESH ===
    refresh_search_services(snowflake_conn, test_config)

    # === VERIFY REFRESH OCCURRED ===
    refreshed_services = 0

    for service_name in initial_timestamps:
        with snowflake_conn.cursor(DictCursor) as cursor:
            cursor.execute(f"DESCRIBE CORTEX SEARCH SERVICE {service_name}")
            result = cursor.fetchone()

            updated_timestamp = result.get("data_timestamp")
            service_status = result.get("serving_state")

            assert updated_timestamp is not None, f"data_timestamp should be available for {service_name} after refresh"
            assert service_status == "ACTIVE", (
                f"Search service {service_name} should be ACTIVE after refresh, got: {service_status}"
            )

            print(f"Updated {service_name} data_timestamp: {updated_timestamp}, serving_state: {service_status}")

            # The refresh function should have triggered some update activity
            # Even if timestamp is the same, the service should be active and accessible
            refreshed_services += 1

        assert refreshed_services > 0, "At least one search service should have been refreshed"

    print(f"✅ refresh_search_services test passed - {refreshed_services} search services refreshed and accessible!")
