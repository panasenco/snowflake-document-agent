import pytest
from pathlib import Path
from datetime import datetime, timezone
from snowflake_document_agent.common import stage_document


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
