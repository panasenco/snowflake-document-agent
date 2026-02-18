import pytest
from pathlib import Path
import shutil
import time
from unittest.mock import MagicMock, patch

from snowflake_document_agent.ingest_local import get_local_documents, get_local_downloader
from snowflake_document_agent.common import process_changed_documents


@pytest.mark.deployment
def test_process_local_documents_kitchen_sink(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Comprehensive test: process local documents through the full pipeline with changes and error handling.
    Tests adding, updating, deleting files, plus various document types and error conditions.
    Combines both happy and unhappy paths like the test_process_documents_kitchen_sink test.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    mock_logger = MagicMock()

    # Override metadata prompt to test filename awareness
    test_config["metadata_prompt"] = (
        "Look at the filename and return this string exactly: File extension: [extension without leading period]"
    )

    # Create a local documents directory
    docs_dir = tmp_path / "local_kitchen_sink"
    docs_dir.mkdir()

    # Create subdirectory for organizing files
    subdir = docs_dir / "documents"
    subdir.mkdir()

    # === ROUND 1: INITIAL DOCUMENTS (SOME SUCCESS, SOME FAILURE) ===

    # 1. Plain text file (should succeed)
    txt_file = docs_dir / "success.txt"
    txt_file.write_text("This is a test document for end-to-end local processing.")

    # 2. HTML file (should succeed)
    html_file = docs_dir / "success.html"
    html_file.write_text("<p>Test HTML document for local processing</p>")

    # 3. PDF fixture (should succeed if available)
    pdf_fixture = Path(__file__).parent.parent / "fixtures" / "cuad-sponsorship.pdf"
    pdf_file = None
    if pdf_fixture.exists():
        pdf_file = docs_dir / "cuad-sponsorship.pdf"
        shutil.copy(pdf_fixture, pdf_file)

    # 4. DOCX fixture (should succeed if available)
    docx_fixture = Path(__file__).parent.parent / "fixtures" / "mammoth-tables.docx"
    docx_file = None
    if docx_fixture.exists():
        docx_file = subdir / "mammoth-tables.docx"
        shutil.copy(docx_fixture, docx_file)

    # 5. Unsupported file extension (should fail)
    bad_ext_file = docs_dir / "bad_extension.xyz"
    bad_ext_file.write_text("This has an unsupported extension")

    # 6. Excel file disguised as PDF (should fail parsing)
    xlsx_fixture = Path(__file__).parent.parent / "fixtures" / "multi-worksheet.xlsx"
    fake_pdf = docs_dir / "actually_excel.pdf"
    if xlsx_fixture.exists():
        shutil.copy(xlsx_fixture, fake_pdf)
    else:
        # Create fake Excel-like content with PDF extension
        fake_pdf.write_bytes(b"PK\x03\x04")  # ZIP signature (Excel files are ZIP-based)

    # === PROCESS INITIAL DOCUMENTS ===

    initial_sources = get_local_documents(docs_dir, "local")
    downloader = get_local_downloader(docs_dir, "local")

    # Verify we found all files
    expected_count = 4 + (1 if pdf_file else 0) + (1 if docx_file else 0)
    assert len(initial_sources) == expected_count, f"Expected {expected_count} files, got {len(initial_sources)}"

    # Mock refresh_search_services to avoid long execution time
    with patch("snowflake_document_agent.common.refresh_search_services") as mock_refresh:
        # Process initial documents
        process_changed_documents(
            sources=initial_sources,
            connection=snowflake_conn,
            downloader=downloader,
            prefix="local",
            config=test_config,
            max_workers=1,  # SSO requires sequential processing
            logger=mock_logger,
        )

        # Check initial results
        logged_errors = [call.args[0] for call in mock_logger.error.call_args_list]
        print(f"DEBUG: Round 1 - Got {mock_logger.error.call_count} error logs:")
        for i, error in enumerate(logged_errors):
            print(f"  {i + 1}. {error}")

        # Should have errors for bad extension and fake PDF (2 documents × 2 messages each = 4 total)
        # Each failed document logs both rollback notice + detailed error
        expected_initial_errors = 4
        assert mock_logger.error.call_count == expected_initial_errors, (
            f"Expected {expected_initial_errors} error logs in round 1, got {mock_logger.error.call_count}"
        )

        # Verify refresh_search_services was called in round 1 (there were changes)
        assert mock_refresh.call_count == 1, (
            f"Expected refresh_search_services to be called once in round 1, got {mock_refresh.call_count}"
        )

    # === ROUND 2: FILESYSTEM CHANGES ===

    # Reset mocks for round 2
    mock_logger.reset_mock()

    # Wait a bit to ensure different timestamps
    time.sleep(0.1)

    # 1. Update existing file
    txt_file.write_text("This is UPDATED content for the local processing test.")

    # 2. Delete a successful file
    html_file.unlink()

    # 3. Delete a problematic file (bad extension)
    bad_ext_file.unlink()

    # 4. Add new successful file
    new_file = docs_dir / "new_document.txt"
    new_file.write_text("This is a brand new document added in round 2.")

    # 5. Add new problematic file (simulate race condition - file disappears after discovery)
    race_condition_file = docs_dir / "race_condition.txt"
    race_condition_file.write_text("This will disappear before processing")

    # 6. Add XLSX file (should succeed if fixture available)
    new_xlsx = None
    if xlsx_fixture.exists():
        new_xlsx = subdir / "new_spreadsheet.xlsx"
        shutil.copy(xlsx_fixture, new_xlsx)

    # Get updated sources BEFORE deleting race condition file
    updated_sources = get_local_documents(docs_dir, "local")
    updated_downloader = get_local_downloader(docs_dir, "local")

    # Now delete the race condition file to simulate it disappearing
    race_condition_file.unlink()

    # === PROCESS CHANGED DOCUMENTS ===

    # Mock refresh_search_services to avoid long execution time
    with patch("snowflake_document_agent.common.refresh_search_services") as mock_refresh:
        process_changed_documents(
            sources=updated_sources,
            connection=snowflake_conn,
            downloader=updated_downloader,
            prefix="local",
            config=test_config,
            max_workers=1,  # SSO requires sequential processing
            logger=mock_logger,
        )

        # Check round 2 results
        logged_errors_round2 = [call.args[0] for call in mock_logger.error.call_args_list]
        print(f"DEBUG: Round 2 - Got {mock_logger.error.call_count} error logs:")
        for i, error in enumerate(logged_errors_round2):
            print(f"  {i + 1}. {error}")

        # Should have errors for fake PDF (still there) and missing race condition file
        # Race condition: 1 download failure message
        # Fake PDF: 2 processing failure messages (rollback + detailed error)
        expected_round2_errors = 3
        assert mock_logger.error.call_count == expected_round2_errors, (
            f"Expected {expected_round2_errors} error logs in round 2, got {mock_logger.error.call_count}"
        )

        # Verify one error is for the race condition file (download failure)
        race_condition_errors = [error for error in logged_errors_round2 if "race_condition.txt" in error]
        assert len(race_condition_errors) == 1, f"Expected 1 race condition error, got: {race_condition_errors}"

        # Verify refresh_search_services was called in round 2 (there were changes)
        assert mock_refresh.call_count == 1, (
            f"Expected refresh_search_services to be called once in round 2, got {mock_refresh.call_count}"
        )

    # === VERIFY FINAL STATE ===

    with snowflake_conn.cursor() as cursor:
        # Check document_metadata table
        cursor.execute("SELECT source_uri FROM document_metadata WHERE source_uri LIKE 'local://%' ORDER BY source_uri")
        db_uris = [row[0] for row in cursor.fetchall()]

        print(f"DEBUG: Final database URIs: {db_uris}")

        # Expected successful documents
        expected_uris = [
            "local://success.txt",  # Updated
            "local://new_document.txt",  # Added in round 2
        ]

        # Add conditional files if fixtures exist
        if pdf_file:
            expected_uris.append("local://cuad-sponsorship.pdf")
        if docx_file:
            expected_uris.append("local://documents/mammoth-tables.docx")
        if new_xlsx:
            expected_uris.append("local://documents/new_spreadsheet.xlsx")

        # Verify expected files are present
        for expected_uri in expected_uris:
            assert expected_uri in db_uris, f"Expected URI {expected_uri} not found in database"

        # Verify deleted files are gone
        assert "local://success.html" not in db_uris, "Deleted HTML file should not be in database"
        assert "local://bad_extension.xyz" not in db_uris, "Deleted bad extension file should not be in database"

        # Verify problematic files were not processed
        assert "local://actually_excel.pdf" not in db_uris, "Fake PDF should not be processed successfully"
        assert "local://race_condition.txt" not in db_uris, "Race condition file should not be processed"

        # Check document_text table has content for successful files only
        cursor.execute("SELECT source_uri FROM document_text WHERE source_uri LIKE 'local://%' ORDER BY source_uri")
        text_uris = [row[0] for row in cursor.fetchall()]
        print(f"DEBUG: document_text URIs: {text_uris}")

        text_count = len(text_uris)
        expected_text_count = len(expected_uris)
        assert text_count == expected_text_count, (
            f"Expected {expected_text_count} documents in document_text, got {text_count}. "
            f"Expected: {sorted(expected_uris)}, Got: {sorted(text_uris)}"
        )

        # Verify updated content
        cursor.execute("SELECT document_text FROM document_text WHERE source_uri = 'local://success.txt'")
        updated_content = cursor.fetchone()[0]
        assert "UPDATED content" in updated_content, "Updated file content not found in database"

        # Verify metadata generation worked (filename awareness)
        cursor.execute("SELECT enhanced_metadata FROM enhanced_metadata WHERE source_uri = 'local://success.txt'")
        metadata_result = cursor.fetchone()
        if metadata_result:
            metadata_content = metadata_result[0]
            assert "File extension: txt" in metadata_content, (
                f"Expected filename awareness in metadata, got: {metadata_content[:200]}..."
            )

    print("Kitchen sink test completed successfully - verified adds, updates, deletes, and error handling!")


@pytest.mark.deployment
def test_process_changed_documents_no_changes(snowflake_conn, test_schema, test_config, tmp_path):
    """
    Test that refresh_search_services is NOT called when there are no changes to process.
    """
    if not snowflake_conn:
        pytest.skip("No Snowflake connection")

    mock_logger = MagicMock()

    # Mock refresh_search_services to verify it's not called
    with patch("snowflake_document_agent.common.refresh_search_services") as mock_refresh:
        # Create a local documents directory with one file
        docs_dir = tmp_path / "no_changes_test"
        docs_dir.mkdir()

        txt_file = docs_dir / "unchanged.txt"
        txt_file.write_text("This file will not change")

        # === FIRST PROCESSING: Add initial document ===

        initial_sources = get_local_documents(docs_dir, "local")
        downloader = get_local_downloader(docs_dir, "local")

        process_changed_documents(
            sources=initial_sources,
            connection=snowflake_conn,
            downloader=downloader,
            prefix="local",
            config=test_config,
            max_workers=1,  # SSO requires sequential processing
            logger=mock_logger,
        )

        # Verify refresh was called for initial processing
        assert mock_refresh.call_count == 1, "Expected refresh_search_services to be called for initial processing"

        # === SECOND PROCESSING: No changes ===

        mock_refresh.reset_mock()
        mock_logger.reset_mock()

        # Get same sources (no changes)
        same_sources = get_local_documents(docs_dir, "local")
        same_downloader = get_local_downloader(docs_dir, "local")

        process_changed_documents(
            sources=same_sources,
            connection=snowflake_conn,
            downloader=same_downloader,
            prefix="local",
            config=test_config,
            max_workers=1,  # SSO requires sequential processing
            logger=mock_logger,
        )

        # Verify refresh was NOT called when there are no changes
        assert mock_refresh.call_count == 0, "Expected refresh_search_services NOT to be called when no changes"

        # Verify no errors were logged
        logged_errors = [call.args[0] for call in mock_logger.error.call_args_list]
        assert len(logged_errors) == 0, f"Expected no errors when no changes, got: {logged_errors}"

        print("No changes test completed - verified refresh_search_services is not called when no changes!")
