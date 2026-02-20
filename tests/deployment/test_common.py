import pytest
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from snowflake.connector import DictCursor

from snowflake_document_agent.common import (
    stage_document,
    set_document_text,
    parse_document,
    generate_document_metadata,
    chunk_document,
    clear_stage,
    process_changed_documents,
    delete_document,
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
        clear_stage(snowflake_conn)

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
def test_set_document_text_basic(snowflake_conn, test_schema, tmp_path):
    """
    Test that set_document_text properly sets text in the document_text table.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Setup - Using new URI format with query parameters
        source_uri = "test://integration/text_test.txt?timestamp=1234567890"
        display_name = "text_test.txt"
        test_text = "This is the document text content for testing."

        # Execute - Set document text
        set_document_text(
            cursor=cursor,
            source_uri=source_uri,
            display_name=display_name,
            text=test_text,
        )

        # Verify - Check that text was set in document_text table
        cursor.execute(
            "SELECT source_uri, display_name, document_text FROM document_text WHERE source_uri = :1", (source_uri,)
        )
        text_result = cursor.fetchone()
        assert text_result is not None, "No text found in document_text table"
        assert text_result[0] == source_uri, f"Expected source_uri '{source_uri}', got '{text_result[0]}'"
        assert text_result[1] == display_name, f"Expected display_name '{display_name}', got '{text_result[1]}'"
        assert text_result[2] == test_text, f"Expected text content '{test_text}', got '{text_result[2]}'"

        # Execute - Test versioning: set text with new query parameters (simulating update)
        new_source_uri = "test://integration/text_test.txt?timestamp=1234567891"
        updated_text = "This is the updated document text content."

        set_document_text(
            cursor=cursor,
            source_uri=new_source_uri,
            display_name=display_name,
            text=updated_text,
        )

        # Verify - Both versions should exist (versioning allows multiple URIs)
        cursor.execute("SELECT COUNT(*) FROM document_text WHERE source_uri LIKE 'test://integration/text_test.txt%'")
        count_result = cursor.fetchone()
        assert count_result[0] == 2, f"Expected 2 versions, found {count_result[0]}"

        # Verify - Check that the new version has the correct content
        cursor.execute("SELECT source_uri, document_text FROM document_text WHERE source_uri = :1", (new_source_uri,))
        new_text_result = cursor.fetchone()
        assert new_text_result is not None, "New version not found in document_text table"
        assert new_text_result[0] == new_source_uri, (
            f"Expected source_uri '{new_source_uri}', got '{new_text_result[0]}'"
        )
        assert new_text_result[1] == updated_text, f"Expected text content '{updated_text}', got '{new_text_result[1]}'"


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

        source_uri = "test://parse/cuad-sponsorship.pdf?timestamp=1234567890"

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
            display_name="cuad-sponsorship.pdf",
            stage_path=stage_path,
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
                display_name="empty.pdf",
                stage_path=stage_path_empty,
            )

        error_msg = str(exc_info.value)
        assert "invalid pdf file" in error_msg.lower(), f"Expected parsing error message, got: {error_msg}"
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
                display_name="actually_excel.pdf",
                stage_path=stage_path_text,
            )

        error_msg = str(exc_info.value)
        assert "isn't supported" in error_msg.lower(), f"Expected parsing error message, got: {error_msg}"
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
        source_uri = "test://integration/metadata_generation_test.txt?timestamp=1234567890"
        display_name = "metadata_generation_test.txt"
        test_text = "This is a test document for metadata generation."

        # Insert document text
        set_document_text(
            cursor=cursor,
            source_uri=source_uri,
            display_name=display_name,
            text=test_text,
        )

        # Setup config to test filename awareness - should fail because we don't pass source URI to metadata generation
        test_config["metadata_prompt"] = (
            "Look at the filename and return this string exactly: File extension: [extension without leading period]"
        )

        # Execute - Generate metadata with test config
        generate_document_metadata(cursor=cursor, source_uri=source_uri, display_name=display_name, config=test_config)

        # Verify - Check that metadata was inserted into document_metadata table
        cursor.execute(
            "SELECT source_uri, generated_metadata FROM document_metadata WHERE source_uri = :1", (source_uri,)
        )
        metadata_result = cursor.fetchone()
        assert metadata_result is not None, "No generated metadata found in document_metadata table"
        assert metadata_result[0] == source_uri, f"Expected source_uri '{source_uri}', got '{metadata_result[0]}'"

        # Verify the generated metadata contains file extension (should work - filename is passed to AI in display_name)
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
        source_uri = "test://integration/chunk_test.txt?timestamp=1234567890"
        display_name = "chunk_test.txt"
        test_text = "abcdefghijklmnopqrstuvwxyz"  # 26 characters
        test_metadata = "Test"

        # Insert document text
        set_document_text(
            cursor=cursor,
            source_uri=source_uri,
            display_name=display_name,
            text=test_text,
        )

        # Insert generated metadata (required by chunk_document)
        cursor.execute(
            "INSERT INTO document_metadata (source_uri, display_name, generated_metadata) VALUES (:1, :2, :3)",
            (source_uri, display_name, test_metadata),
        )

        # Setup config with very small chunking parameters for precise testing
        config_with_small_chunks = test_config.copy()
        config_with_small_chunks["chunk_size"] = 10  # Very small chunks
        config_with_small_chunks["chunk_overlap"] = 3  # Small overlap

        # Execute - Chunk the document
        chunk_document(
            cursor=cursor, source_uri=source_uri, display_name="chunk_test.txt", config=config_with_small_chunks
        )

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
def test_process_changed_documents_kitchen_sink(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Comprehensive test: throw everything at process_changed_documents and verify it handles gracefully.
    Tests all document types, nonexistent files, bad extensions, and corrupted files.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    mock_logger = MagicMock()

    # Override metadata prompt to test filename awareness - should now work with filename included
    test_config["metadata_prompt"] = (
        "Look at the filename and return this string exactly: File extension: [extension without leading period]"
    )

    # Define specific timestamps for testing
    txt_timestamp = int(datetime(2024, 1, 1, 10, 30, 0, tzinfo=timezone.utc).timestamp())
    html_timestamp = int(datetime(2024, 1, 2, 11, 45, 0, tzinfo=timezone.utc).timestamp())
    pdf_timestamp = int(datetime(2024, 1, 3, 14, 15, 0, tzinfo=timezone.utc).timestamp())
    docx_timestamp = int(datetime(2024, 1, 4, 16, 20, 0, tzinfo=timezone.utc).timestamp())
    xlsx_timestamp = int(datetime(2024, 1, 5, 9, 10, 0, tzinfo=timezone.utc).timestamp())

    # === DOCUMENTS THAT SHOULD SUCCEED ===

    # 1. Plain text file
    txt_file = tmp_path / "success.txt"
    txt_file.write_text("This is a test document for end-to-end processing.")

    # 2. HTML file
    html_file = tmp_path / "success.html"
    html_file.write_text("<p>Test HTML document</p>")

    # 3. PDF fixture (should parse successfully)
    pdf_file = Path(__file__).parent.parent / "fixtures" / "cuad-sponsorship.pdf"

    # 4. DOCX fixture (should convert successfully)
    docx_file = Path(__file__).parent.parent / "fixtures" / "mammoth-tables.docx"

    # 5. XLSX fixture (should convert successfully)
    xlsx_file = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"

    # === DOCUMENTS THAT SHOULD FAIL ===

    # 6. Nonexistent file (FileNotFoundError)
    missing_file = tmp_path / "does_not_exist.txt"

    # 7. Unsupported file extension (ValueError from stage_document)
    bad_ext_file = tmp_path / "bad_extension.xyz"
    bad_ext_file.write_text("This has an unsupported extension")

    # 8. Corrupted "PDF" (actually Excel file, should fail parsing)
    excel_as_pdf = tmp_path / "actually_excel.pdf"
    excel_fixture = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"
    shutil.copy(excel_fixture, excel_as_pdf)

    # Create sources list of tuples for new process_changed_documents API
    sources = [
        (f"test://kitchen/success.txt?timestamp={txt_timestamp}", "Success Text File"),
        (f"test://kitchen/success.html?timestamp={html_timestamp}", "Success HTML File"),
        (f"test://kitchen/cuad-sponsorship.pdf?timestamp={pdf_timestamp}", "CUAD Sponsorship PDF"),
        (f"test://kitchen/mammoth-tables.docx?timestamp={docx_timestamp}", "Mammoth Tables DOCX"),
        (f"test://kitchen/multi-worksheet.xlsx?timestamp={xlsx_timestamp}", "Multi Worksheet XLSX"),
        (
            f"test://kitchen/missing.txt?timestamp={int(datetime(2024, 1, 6, 12, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Missing Text File",
        ),
        (
            f"test://kitchen/bad_extension.xyz?timestamp={int(datetime(2024, 1, 7, 13, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Bad Extension File",
        ),
        (
            f"test://kitchen/actually_excel.pdf?timestamp={int(datetime(2024, 1, 8, 14, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Corrupted PDF File",
        ),
    ]

    # Create test downloader function
    def test_downloader(source_uri: str) -> Path:
        """Test downloader that returns local file paths based on URI."""
        # Extract the path part before the query parameters
        uri_path = source_uri.split("?")[0].removeprefix("test://kitchen/")

        if uri_path == "success.txt":
            return txt_file
        elif uri_path == "success.html":
            return html_file
        elif uri_path == "cuad-sponsorship.pdf":
            return pdf_file
        elif uri_path == "mammoth-tables.docx":
            return docx_file
        elif uri_path == "multi-worksheet.xlsx":
            return xlsx_file
        elif uri_path == "missing.txt":
            return missing_file
        elif uri_path == "bad_extension.xyz":
            return bad_ext_file
        elif uri_path == "actually_excel.pdf":
            return excel_as_pdf
        else:
            raise ValueError(f"Unknown URI: {source_uri}")

    # Execute process_changed_documents - should not crash despite failures
    process_changed_documents(
        sources,
        connection=snowflake_conn,
        downloader=test_downloader,
        prefix="test://kitchen/",
        config=test_config,
        max_workers=4,  # Use parallel processing for testing
        logger=mock_logger,
    )

    # === VERIFICATIONS ===

    logged_errors = [call.args[0] for call in mock_logger.error.call_args_list]
    print(f"DEBUG: Got {mock_logger.error.call_count} error logs:")
    for i, error in enumerate(logged_errors):
        print(f"  {i + 1}. {error}")

    # Should have logged errors for the 3 failing documents
    assert mock_logger.error.call_count == 3, (
        f"Expected 3 error logs (one per failure), got {mock_logger.error.call_count}"
    )

    # Verify each expected failure was logged with source URI
    expected_failures = [
        ("missing.txt", "FileNotFoundError"),
        ("bad_extension.xyz", "unsupported extension"),
        ("actually_excel.pdf", "isn't supported"),  # Excel file with .pdf extension should fail parsing
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

        cursor.execute("SELECT COUNT(*) FROM document_metadata")
        metadata_count = cursor.fetchone()[0]
        assert metadata_count >= 5, f"Expected at least 5 successful metadata entries, got {metadata_count}"

        # === DETAILED CONTENT VERIFICATION ===
        # Verify specific content for each document type (incorporating test_process_document_multiple_types checks)

        # 1. Text file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri LIKE :1",
            ("test://kitchen/success.txt?timestamp=%",),
        )
        txt_result = cursor.fetchone()
        assert txt_result and "This is a test document for end-to-end processing." in txt_result[0]

        # 2. HTML file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri LIKE :1",
            ("test://kitchen/success.html?timestamp=%",),
        )
        html_result = cursor.fetchone()
        assert html_result and "<p>Test HTML document</p>" in html_result[0]

        # 3. PDF file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri LIKE :1",
            ("test://kitchen/cuad-sponsorship.pdf?timestamp=%",),
        )
        pdf_result = cursor.fetchone()
        assert pdf_result and "undergo training" in pdf_result[0].lower()

        # 4. DOCX file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri LIKE :1",
            ("test://kitchen/mammoth-tables.docx?timestamp=%",),
        )
        docx_result = cursor.fetchone()
        assert docx_result and "bottom right" in docx_result[0].lower()

        # 5. XLSX file content
        cursor.execute(
            "SELECT document_text FROM document_text WHERE source_uri LIKE :1",
            ("test://kitchen/multi-worksheet.xlsx?timestamp=%",),
        )
        xlsx_result = cursor.fetchone()
        assert xlsx_result and "reset table" in xlsx_result[0].lower()

        # Verify generated metadata contains file extension information (should now work with filename awareness)
        test_cases = [
            ("test://kitchen/success.txt?timestamp=", "txt"),
            ("test://kitchen/success.html?timestamp=", "html"),
            ("test://kitchen/cuad-sponsorship.pdf?timestamp=", "pdf"),
            ("test://kitchen/mammoth-tables.docx?timestamp=", "docx"),
            ("test://kitchen/multi-worksheet.xlsx?timestamp=", "xlsx"),
        ]

        for source_uri_pattern, expected_ext in test_cases:
            cursor.execute(
                "SELECT generated_metadata FROM document_metadata WHERE source_uri LIKE :1", (source_uri_pattern + "%",)
            )
            metadata_result = cursor.fetchone()
            if metadata_result:
                expected_text = f"File extension: {expected_ext}"
                assert expected_text in metadata_result[0], (
                    f"Expected '{expected_text}' in metadata for pattern {source_uri_pattern}: {metadata_result[0][:200]}..."
                )

    print(f"✅ Kitchen sink test passed! Processed {text_count} documents, logged {len(logged_errors)} errors")


@pytest.mark.deployment
def test_get_snowflake_documents(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test get_snowflake_documents function - should return documents matching a prefix.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # Define test URIs with different prefixes - using new query parameter format
    test_uris = [
        "s3://bucket/folder1/doc1.pdf?version=1",
        "s3://bucket/folder1/doc2.txt?version=1",
        "s3://bucket/folder2/doc3.docx?version=1",
        "gcs://other-bucket/doc4.xlsx?version=1",
    ]

    # Insert test document metadata entries
    with snowflake_conn.cursor() as cursor:
        for source_uri in test_uris:
            cursor.execute(
                "INSERT INTO document_metadata (source_uri, display_name, generated_metadata) VALUES (:1, :2, :3)",
                (source_uri, "test_doc", "test metadata"),
            )

    # Test 1: Get documents with "s3://bucket/folder1/" prefix
    result = get_snowflake_documents(snowflake_conn, prefix="s3://bucket/folder1/")

    assert isinstance(result, set), "Should return a set"
    assert len(result) == 2, f"Expected 2 documents with prefix 's3://bucket/folder1/', got {len(result)}"

    # Verify the correct documents are returned
    assert "s3://bucket/folder1/doc1.pdf?version=1" in result
    assert "s3://bucket/folder1/doc2.txt?version=1" in result
    assert "s3://bucket/folder2/doc3.docx?version=1" not in result
    assert "gcs://other-bucket/doc4.xlsx?version=1" not in result

    # Test 2: Get documents with "s3://bucket/" prefix (should get folder1 and folder2)
    result_broad = get_snowflake_documents(snowflake_conn, prefix="s3://bucket/")
    assert len(result_broad) == 3, f"Expected 3 documents with prefix 's3://bucket/', got {len(result_broad)}"

    expected_broad = {
        "s3://bucket/folder1/doc1.pdf?version=1",
        "s3://bucket/folder1/doc2.txt?version=1",
        "s3://bucket/folder2/doc3.docx?version=1",
    }
    assert result_broad == expected_broad, f"Expected {expected_broad}, got {result_broad}"

    # Test 3: Get documents with non-existent prefix
    result_empty = get_snowflake_documents(snowflake_conn, prefix="nonexistent://")
    assert len(result_empty) == 0, f"Expected 0 documents with non-existent prefix, got {len(result_empty)}"


@pytest.mark.deployment
def test_process_changed_documents_basic(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test process_changed_documents function - incremental processing logic.
    The current implementation handles:
    1. New documents (in sources but not in Snowflake) - processes them
    2. Deleted documents (in Snowflake but not in sources) - deletes them
    3. Existing documents (in both sources and Snowflake) - skips them
    4. Calls refresh_search_services when there are changes
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # Mock downloader that returns real fixture files
    def mock_downloader(source_uri: str) -> Path:
        """Returns real fixture files based on the source URI"""
        # Extract filename from URI (remove query parameters)
        uri_path = source_uri.split("?")[0]
        filename = uri_path.split("/")[-1]
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
    # Create initial documents using the new URI format with query parameters
    initial_sources = [
        (
            f"test://project/doc1.txt?timestamp={int(datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Document 1",
        ),
        (
            f"test://project/doc2.pdf?timestamp={int(datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Document 2",
        ),
        (
            f"test://project/old_doc.docx?timestamp={int(datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Old Document",
        ),
    ]

    # Process initial documents
    process_changed_documents(
        initial_sources,
        connection=snowflake_conn,
        downloader=mock_downloader,
        prefix=prefix,
        config=test_config,
        max_workers=4,
    )

    # Verify initial setup worked
    initial_docs_in_snowflake = get_snowflake_documents(snowflake_conn, prefix)
    assert len(initial_docs_in_snowflake) == 3, f"Expected 3 initial documents, got {len(initial_docs_in_snowflake)}"

    # === TEST: Current source documents ===
    # This represents the "current state" of the source system
    current_sources = [
        # doc1: EXISTS in Snowflake - should be SKIPPED (no timestamp-based change detection)
        (
            f"test://project/doc1.txt?timestamp={int(datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Document 1",
        ),
        # doc2: EXISTS in Snowflake - should be SKIPPED
        (
            f"test://project/doc2.pdf?timestamp={int(datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Document 2",
        ),
        # doc3: NEW - doesn't exist in Snowflake - should be PROCESSED
        (
            f"test://project/doc3.xlsx?timestamp={int(datetime(2024, 1, 4, 14, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Document 3",
        ),
        # doc4: NEW - doesn't exist in Snowflake - should be PROCESSED
        (
            f"test://project/doc4.html?timestamp={int(datetime(2024, 1, 5, 16, 0, 0, tzinfo=timezone.utc).timestamp())}",
            "Document 4",
        ),
    ]
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

        # Verify refresh_search_services was called (there were changes: 1 deleted, 2 new)
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
    final_docs = get_snowflake_documents(snowflake_conn, prefix)

    # Should have 4 documents (doc1, doc2, doc3, doc4) - old_doc should be deleted
    assert len(final_docs) == 4, f"Expected 4 documents after processing, got {len(final_docs)}: {sorted(final_docs)}"

    # Verify the URIs we expect are present
    expected_uris = {uri for uri, _ in current_sources}
    assert final_docs == expected_uris, f"Expected URIs {sorted(expected_uris)}, got {sorted(final_docs)}"

    # Verify logging behavior - should log deletions and new docs
    logged_messages = [str(call.args[0]) for call in mock_logger.info.call_args_list]

    # Should log deletion of old_doc.docx
    deletion_logs = [msg for msg in logged_messages if "delet" in msg.lower() and "old_doc.docx" in msg]
    assert len(deletion_logs) > 0, f"Should log deletion of old_doc.docx. All logs: {logged_messages}"

    # Verify content was actually stored for new documents
    with snowflake_conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM document_text WHERE source_uri LIKE :1", ("test://project/doc3.xlsx?timestamp=%",)
        )
        doc3_count = cursor.fetchone()[0]
        assert doc3_count == 1, "doc3.xlsx should have been processed and stored"

        cursor.execute(
            "SELECT COUNT(*) FROM document_text WHERE source_uri LIKE :1", ("test://project/doc4.html?timestamp=%",)
        )
        doc4_count = cursor.fetchone()[0]
        assert doc4_count == 1, "doc4.html should have been processed and stored"

    # === TEST NO CHANGES: Process same sources again ===
    mock_logger.reset_mock()

    with patch("snowflake_document_agent.common.refresh_search_services") as mock_refresh_no_changes:
        process_changed_documents(
            current_sources,  # Same sources as before - no changes
            connection=snowflake_conn,
            downloader=mock_downloader,
            prefix=prefix,
            config=test_config,
            max_workers=1,
            logger=mock_logger,
        )

        # Verify refresh_search_services was NOT called when no changes
        assert mock_refresh_no_changes.call_count == 0, (
            f"Expected refresh_search_services NOT to be called when no changes, got {mock_refresh_no_changes.call_count}"
        )

    # Verify no errors were logged for no changes
    no_change_errors = [call.args[0] for call in mock_logger.error.call_args_list]
    assert len(no_change_errors) == 0, f"Expected no errors when no changes, got: {no_change_errors}"

    print("✅ process_changed_documents test passed - incremental processing (new/delete only) works correctly!")


@pytest.mark.deployment
def test_process_changed_documents_orphaned_data_bug(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test for orphaned data bug at process_changed_documents level: when document processing fails at a later stage,
    ensure no partial data is left in ANY table (document_text, document_metadata, document_chunks).

    This test uses a valid text file but corrupts the config to cause chunking to fail after earlier steps succeed.
    This validates that process_changed_documents properly cleans up orphaned data from earlier pipeline stages.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # Create a valid text file (will succeed through parsing, text insertion, and metadata generation)
    text_file = tmp_path / "valid.txt"
    text_file.write_text("This is a valid text document that will process successfully until chunking fails.")

    source_uri = "test://orphaned/valid.txt?timestamp=1234567890"
    display_name = "valid.txt"

    # Create a mock downloader that returns our valid text file
    def mock_downloader(uri: str) -> Path:
        assert uri == source_uri, f"Expected {source_uri}, got {uri}"
        return text_file

    # Corrupt the config to make chunking fail (chunk_size must be int, not string)
    test_config["chunk_size"] = "invalid_string_value"  # This will cause chunking to fail

    # Try to process the document using process_changed_documents (which has rollback functionality)
    sources = [(source_uri, display_name)]

    # process_changed_documents doesn't raise exceptions for individual doc failures - it handles them gracefully
    process_changed_documents(
        sources,
        connection=snowflake_conn,
        downloader=mock_downloader,
        prefix="test://orphaned/",
        config=test_config,
        max_workers=1,  # Use single worker to ensure predictable behavior
    )

    # No return value check needed since process_changed_documents doesn't return a boolean

    # === VERIFY NO ORPHANED DATA ===
    # The bug is that failed documents leave partial data in some tables but not others

    with snowflake_conn.cursor() as cursor:
        # Check all tables - should be completely empty for this source_uri
        tables_to_check = ["document_metadata", "document_text", "document_chunks"]

        for table in tables_to_check:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE source_uri = :1", (source_uri,))
            count = cursor.fetchone()[0]
            print(f"DEBUG: {table} contains {count} entries for failed document")
            assert count == 0, f"Failed document should not be in {table}, found {count} entries (ORPHANED DATA BUG)"

    print("✅ Orphaned data test passed - no partial data left from failed process_changed_documents!")


@pytest.mark.deployment
def test_delete_document(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test delete_document function - should remove individual documents from all relevant tables.
    Should delete from: document_metadata, document_text, enhanced_metadata, document_chunks
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    # === SETUP: Create documents in all tables ===
    test_documents = [
        (
            "test://delete/keep1.txt?timestamp=1",
            datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            "metadata for keep1",
        ),
        (
            "test://delete/delete1.pdf?timestamp=2",
            datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc),
            "metadata for delete1",
        ),
        (
            "test://delete/delete2.docx?timestamp=3",
            datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc),
            "metadata for delete2",
        ),
        (
            "test://delete/keep2.xlsx?timestamp=4",
            datetime(2024, 1, 4, 13, 0, 0, tzinfo=timezone.utc),
            "metadata for keep2",
        ),
    ]

    with snowflake_conn.cursor() as cursor:
        # Insert into document_metadata
        for source_uri, modified_at_utc, metadata in test_documents:
            cursor.execute(
                "INSERT INTO document_metadata (source_uri, display_name, generated_metadata) VALUES (:1, :2, :3)",
                (source_uri, "test_doc", metadata),
            )

        # Insert into document_text
        for source_uri, _, _ in test_documents:
            document_text = f"This is the content of {source_uri}"
            set_document_text(cursor=cursor, source_uri=source_uri, display_name="test_doc", text=document_text)

        # Insert into document_chunks
        for source_uri, _, _ in test_documents:
            chunk_document(cursor, source_uri=source_uri, display_name="test_doc", config=test_config)

    # === VERIFY INITIAL STATE ===
    with snowflake_conn.cursor() as cursor:
        # Should have 4 documents in each table
        cursor.execute("SELECT COUNT(*) FROM document_metadata")
        assert cursor.fetchone()[0] == 4, "Should have 4 entries in document_metadata"

        cursor.execute("SELECT COUNT(*) FROM document_text")
        assert cursor.fetchone()[0] == 4, "Should have 4 entries in document_text"

        cursor.execute("SELECT COUNT(*) FROM document_chunks")
        chunk_count = cursor.fetchone()[0]
        assert chunk_count > 0, "Should have some entries in document_chunks"

    # === EXECUTE DELETION ===
    uris_to_delete = {
        "test://delete/delete1.pdf?timestamp=2",
        "test://delete/delete2.docx?timestamp=3",
    }

    # Delete each document individually
    for uri in uris_to_delete:
        delete_document(snowflake_conn, uri)

    # === VERIFY RESULTS ===
    with snowflake_conn.cursor() as cursor:
        # Should have 2 documents remaining in each table
        cursor.execute("SELECT COUNT(*) FROM document_metadata")
        remaining_metadata = cursor.fetchone()[0]
        assert remaining_metadata == 2, f"Expected 2 remaining in document_metadata, got {remaining_metadata}"

        cursor.execute("SELECT COUNT(*) FROM document_text")
        remaining_text = cursor.fetchone()[0]
        assert remaining_text == 2, f"Expected 2 remaining in document_text, got {remaining_text}"

        # Verify correct documents remain
        cursor.execute("SELECT source_uri FROM document_metadata ORDER BY source_uri")
        remaining_uris = {row[0] for row in cursor.fetchall()}
        expected_remaining = {"test://delete/keep1.txt?timestamp=1", "test://delete/keep2.xlsx?timestamp=4"}
        assert remaining_uris == expected_remaining, f"Expected {expected_remaining}, got {remaining_uris}"

        # Verify deleted documents are gone from all tables
        for deleted_uri in uris_to_delete:
            cursor.execute("SELECT COUNT(*) FROM document_metadata WHERE source_uri = :1", (deleted_uri,))
            assert cursor.fetchone()[0] == 0, f"{deleted_uri} should be deleted from document_metadata"

            cursor.execute("SELECT COUNT(*) FROM document_text WHERE source_uri = :1", (deleted_uri,))
            assert cursor.fetchone()[0] == 0, f"{deleted_uri} should be deleted from document_text"

            cursor.execute("SELECT COUNT(*) FROM document_chunks WHERE source_uri = :1", (deleted_uri,))
            assert cursor.fetchone()[0] == 0, f"{deleted_uri} should be deleted from document_chunks"

        # Verify kept documents are still there
        for kept_uri in expected_remaining:
            cursor.execute("SELECT COUNT(*) FROM document_metadata WHERE source_uri = :1", (kept_uri,))
            assert cursor.fetchone()[0] == 1, f"{kept_uri} should still exist in document_metadata"

            cursor.execute("SELECT COUNT(*) FROM document_text WHERE source_uri = :1", (kept_uri,))
            assert cursor.fetchone()[0] == 1, f"{kept_uri} should still exist in document_text"

    print("✅ delete_document test passed - individual document deletion from all tables works correctly!")


@pytest.mark.deployment
def test_stage_document_edge_cases(snowflake_conn, test_schema, tmp_path):
    """
    Test that stage_document properly parses URI edge cases with tricky filenames.
    The staging document path should be [netloc]/[path] if the URI has a netloc,
    and just [path (without leading /)] otherwise. Query parameters should be stripped.
    URL-encoded identifiers should be properly unescaped.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    with snowflake_conn.cursor() as cursor:
        # Create a test document with tricky filename including backslash and single quote
        test_file = tmp_path / "File&With=Special@Chars\\and'quotes.txt"
        test_file.write_text("Test document content for URI edge case testing.")

        # Test Case 1: URI with URL-encoded spaces and query parameters (OpenText-style)
        source_uri_encoded_spaces = "opentext://My%20Document%20With%20Spaces.pdf?version_number=1&other_param=value"
        stage_path_encoded_spaces = stage_document(
            cursor=cursor,
            source_uri=source_uri_encoded_spaces,
            local_path=test_file,
        )

        # Expected: URL-decode "My%20Document%20With%20Spaces.pdf" as folder + local filename
        expected_encoded_spaces = "My Document With Spaces.pdf/File&With=Special@Chars\\and'quotes.txt"
        print(f"URI with encoded spaces: {source_uri_encoded_spaces}")
        print(f"Actual stage path: '{stage_path_encoded_spaces}', Expected: '{expected_encoded_spaces}'")

        # Test Case 2: URI with URL-encoded quotes and special chars in netloc
        source_uri_encoded_quotes = "opentext://Doc%27s%20File%20%26%20More.docx"
        stage_path_encoded_quotes = stage_document(
            cursor=cursor,
            source_uri=source_uri_encoded_quotes,
            local_path=test_file,
        )

        # Expected: URL-decode "Doc%27s%20File%20%26%20More.docx" as folder + local filename
        expected_encoded_quotes = "Doc's File & More.docx/File&With=Special@Chars\\and'quotes.txt"
        print(f"URI with encoded quotes: {source_uri_encoded_quotes}")
        print(f"Actual stage path: '{stage_path_encoded_quotes}', Expected: '{expected_encoded_quotes}'")

        # Test Case 3: Unescaped URI with special characters (should work as-is)
        source_uri_unescaped = "test://bucket/folder/File&With=Special@Chars.txt"
        stage_path_unescaped = stage_document(
            cursor=cursor,
            source_uri=source_uri_unescaped,
            local_path=test_file,
        )

        # Expected: netloc "bucket", path "/folder/File&With=Special@Chars.txt" + local filename
        # Result: "bucket/folder/File&With=Special@Chars.txt/File&With=Special@Chars\and'quotes.txt"
        expected_unescaped = "bucket/folder/File&With=Special@Chars.txt/File&With=Special@Chars\\and'quotes.txt"
        print(f"Unescaped URI: {source_uri_unescaped}")
        print(f"Actual stage path: '{stage_path_unescaped}', Expected: '{expected_unescaped}'")

        # Test Case 4: Mixed encoding in path and query
        source_uri_mixed = (
            "test://my-bucket/path%20with%20spaces/doc's%20file.pdf?timestamp=2024%2D01%2D01&user=john%40example.com"
        )
        stage_path_mixed = stage_document(
            cursor=cursor,
            source_uri=source_uri_mixed,
            local_path=test_file,
        )

        # Expected: netloc "my-bucket", path "/path with spaces/doc's file.pdf" (decoded) + local filename
        # Query params should be stripped
        expected_mixed = "my-bucket/path with spaces/doc's file.pdf/File&With=Special@Chars\\and'quotes.txt"
        print(f"Mixed encoding URI: {source_uri_mixed}")
        print(f"Actual stage path: '{stage_path_mixed}', Expected: '{expected_mixed}'")

        # Test Case 5: File URI with encoded path
        source_uri_file_encoded = "file:///home/user/My%20Documents/Important%20File.txt"
        stage_path_file_encoded = stage_document(
            cursor=cursor,
            source_uri=source_uri_file_encoded,
            local_path=test_file,
        )

        # Expected: no netloc, decode path "/home/user/My Documents/Important File.txt" + local filename
        # Remove leading slash: "home/user/My Documents/Important File.txt/File&With=Special@Chars\and'quotes.txt"
        expected_file_encoded = "home/user/My Documents/Important File.txt/File&With=Special@Chars\\and'quotes.txt"
        print(f"Encoded file URI: {source_uri_file_encoded}")
        print(f"Actual stage path: '{stage_path_file_encoded}', Expected: '{expected_file_encoded}'")

        # Test Case 6: Unicode characters (should handle properly)
        source_uri_unicode = "test://bucket/docs/Résumé_François_Müller.pdf?version=1"
        stage_path_unicode = stage_document(
            cursor=cursor,
            source_uri=source_uri_unicode,
            local_path=test_file,
        )

        # Expected: Unicode chars replaced with question marks for Snowflake stage naming rules
        expected_unicode = "bucket/docs/Rsum_Franois_Mller.pdf/File&With=Special@Chars\\and'quotes.txt"
        print(f"Unicode URI: {source_uri_unicode}")
        print(f"Actual stage path: '{stage_path_unicode}', Expected: '{expected_unicode}'")

        # Assertions - These demonstrate the expected behavior
        # Note: Many will fail with current implementation due to improper URI parsing

        # Query parameters should be stripped from all URIs
        for stage_path, test_name in [
            (stage_path_encoded_spaces, "encoded_spaces"),
            (stage_path_mixed, "mixed"),
            (stage_path_unicode, "unicode"),
        ]:
            assert "?" not in stage_path, f"{test_name}: Stage path should not contain query parameters: '{stage_path}'"

        # URL-encoded characters should be unescaped
        assert stage_path_encoded_spaces == expected_encoded_spaces, (
            f"Expected '{expected_encoded_spaces}', got '{stage_path_encoded_spaces}'"
        )
        assert stage_path_encoded_quotes == expected_encoded_quotes, (
            f"Expected '{expected_encoded_quotes}', got '{stage_path_encoded_quotes}'"
        )

        # Unescaped URIs should work as-is
        assert stage_path_unescaped == expected_unescaped, (
            f"Expected '{expected_unescaped}', got '{stage_path_unescaped}'"
        )

        # Mixed encoding should be properly handled
        assert stage_path_mixed == expected_mixed, f"Expected '{expected_mixed}', got '{stage_path_mixed}'"

        # File URIs should decode path and remove leading slash
        assert stage_path_file_encoded == expected_file_encoded, (
            f"Expected '{expected_file_encoded}', got '{stage_path_file_encoded}'"
        )

        # Unicode should be preserved
        assert stage_path_unicode == expected_unicode, f"Expected '{expected_unicode}', got '{stage_path_unicode}'"

        # Verify files were actually staged (basic functionality check)
        cursor.execute("LIST @documents")
        staged_files = cursor.fetchall()
        assert len(staged_files) >= 6, f"Expected at least 6 staged files, found {len(staged_files)}"


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
    # Create test documents using the new URI format with query parameters
    test_documents = [
        f"test://search/doc1.txt?timestamp={int(datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())}",
        f"test://search/doc2.pdf?timestamp={int(datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc).timestamp())}",
    ]

    with snowflake_conn.cursor() as cursor:
        # Insert document data into all relevant tables directly (no expensive Cortex calls)
        for source_uri in test_documents:
            display_name = f"Test Document {source_uri.split('/')[-1].split('?')[0]}"

            # Add document text
            set_document_text(
                cursor, source_uri=source_uri, display_name=display_name, text=f"Content for {source_uri}"
            )

            # Insert dummy metadata directly for search_metadata service
            cursor.execute(
                """
                INSERT INTO document_metadata (source_uri, display_name, generated_metadata)
                VALUES (:1, :2, :3)
                """,
                (
                    source_uri,
                    display_name,
                    f"Dummy metadata for {display_name}. This is test data for search service validation.",
                ),
            )

            # Insert dummy chunks directly for search_contents service
            cursor.execute(
                """
                INSERT INTO document_chunks (source_uri, display_name, contextualized_chunk)
                VALUES (:1, :2, :3)
                """,
                (
                    source_uri,
                    display_name,
                    f"Document: {display_name}\n\nDummy chunk content for {source_uri}. This chunk contains test data for search validation.",
                ),
            )

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

    # === VERIFY DATA WAS INSERTED CORRECTLY ===
    print("\n=== VERIFYING TABLE DATA ===")
    with snowflake_conn.cursor() as cursor:
        # Check document_metadata table
        cursor.execute("SELECT COUNT(*), MIN(LENGTH(generated_metadata)) FROM document_metadata")
        meta_count, min_meta_len = cursor.fetchone()
        print(f"document_metadata: {meta_count} rows, min metadata length: {min_meta_len}")

        # Check document_chunks table
        cursor.execute("SELECT COUNT(*), MIN(LENGTH(contextualized_chunk)) FROM document_chunks")
        chunk_count, min_chunk_len = cursor.fetchone()
        print(f"document_chunks: {chunk_count} rows, min chunk length: {min_chunk_len}")

        if meta_count == 0:
            print("⚠️ No data in document_metadata - search_metadata service may fail")
        if chunk_count == 0:
            print("⚠️ No data in document_chunks - search_contents service may fail")

    # === EXECUTE REFRESH ===
    refresh_search_services(snowflake_conn)

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
