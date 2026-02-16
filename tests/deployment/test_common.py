import pytest
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from snowflake_document_agent.common import (
    stage_document,
    update_document_metadata,
    update_document_text,
    parse_document,
    generate_document_metadata,
    chunk_document,
    clear_stage,
    process_documents,
    DocumentInfo,
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

    # === DOCUMENTS THAT SHOULD SUCCEED ===

    # 1. Plain text file
    txt_file = tmp_path / "success.txt"
    txt_file.write_text("This is a test document for end-to-end processing.")  # Use exact content for verification
    sources.append(
        (
            "test://kitchen/success.txt",
            DocumentInfo(modified_at_utc=datetime.now(timezone.utc), local_path=txt_file),
            True,
        )
    )

    # 2. HTML file
    html_file = tmp_path / "success.html"
    html_file.write_text("<p>Test HTML document</p>")  # Use exact content for verification
    sources.append(
        (
            "test://kitchen/success.html",
            DocumentInfo(modified_at_utc=datetime.now(timezone.utc), local_path=html_file),
            True,
        )
    )

    # 3. PDF fixture (should parse successfully)
    pdf_file = Path(__file__).parent.parent / "fixtures" / "cuad-sponsorship.pdf"
    sources.append(
        (
            "test://kitchen/cuad-sponsorship.pdf",
            DocumentInfo(modified_at_utc=datetime.now(timezone.utc), local_path=pdf_file),
            True,
        )
    )

    # 4. DOCX fixture (should convert successfully)
    docx_file = Path(__file__).parent.parent / "fixtures" / "mammoth-tables.docx"
    sources.append(
        (
            "test://kitchen/mammoth-tables.docx",
            DocumentInfo(modified_at_utc=datetime.now(timezone.utc), local_path=docx_file),
            True,
        )
    )

    # 5. XLSX fixture (should convert successfully)
    xlsx_file = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"
    sources.append(
        (
            "test://kitchen/multi-worksheet.xlsx",
            DocumentInfo(modified_at_utc=datetime.now(timezone.utc), local_path=xlsx_file),
            True,
        )
    )

    # === DOCUMENTS THAT SHOULD FAIL ===

    # 6. Nonexistent file (FileNotFoundError)
    missing_file = tmp_path / "does_not_exist.txt"
    sources.append(
        (
            "test://kitchen/missing.txt",
            DocumentInfo(modified_at_utc=datetime.now(timezone.utc), local_path=missing_file),
            True,
        )
    )

    # 7. Unsupported file extension (ValueError from stage_document)
    bad_ext_file = tmp_path / "bad_extension.xyz"
    bad_ext_file.write_text("This has an unsupported extension")
    sources.append(
        (
            "test://kitchen/bad_extension.xyz",
            DocumentInfo(modified_at_utc=datetime.now(timezone.utc), local_path=bad_ext_file),
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
            DocumentInfo(modified_at_utc=datetime.now(timezone.utc), local_path=excel_as_pdf),
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

    print(f"✅ Kitchen sink test passed! Processed {text_count} documents, logged {len(logged_errors)} errors")
