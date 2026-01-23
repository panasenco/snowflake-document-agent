import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from snowflake_document_agent import common

def scan_local_files(root_dir, prefix):
    local_files = {}
    root_path = Path(root_dir)
    
    if not root_path.exists():
        print(f"Error: Directory '{root_dir}' does not exist.")
        sys.exit(1)

    # Use Path.walk (Python 3.12+) to efficiently prune directories
    for root, dirs, files in root_path.walk():
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        
        for file_name in files:
            if file_name.startswith("."):
                continue
            
            file_path = root / file_name
            relative_path = file_path.relative_to(root_path)
            
            # Ensure forward slashes for URI
            uri_path = str(relative_path).replace(os.sep, "/")
            source_uri = f"{prefix}://{uri_path}"
            
            # Get modification time in UTC
            mtime = file_path.stat().st_mtime
            dt_utc = datetime.fromtimestamp(mtime, tz=timezone.utc)
            
            local_files[source_uri] = {
                "path": file_path,
                "mtime": dt_utc,
                "relative_path": str(relative_path) # native separator for file operations
            }
            
    return local_files

def scan_remote_files(conn, table_name, prefix):
    remote_files = {}
    cursor = conn.cursor()
    try:
        # We filter by prefix
        query = f"SELECT source_uri, modified_at_utc FROM {table_name} WHERE source_uri LIKE %s"
        cursor.execute(query, (f"{prefix}://%",))
        for row in cursor:
            source_uri = row[0]
            modified_at = row[1]
            if modified_at and modified_at.tzinfo is None:
                modified_at = modified_at.replace(tzinfo=timezone.utc)
            remote_files[source_uri] = modified_at
    finally:
        cursor.close()
    return remote_files

def upload_file(conn, local_file_info, stage_name):
    file_path = local_file_info["path"]
    relative_path = local_file_info["relative_path"]
    
    # Target path in stage
    target_dir = os.path.dirname(relative_path)
    if target_dir:
        target_stage_path = f"@{stage_name}/{target_dir.replace(os.sep, '/')}"
    else:
        target_stage_path = f"@{stage_name}"

    cursor = conn.cursor()
    try:
        abs_path = str(file_path.absolute()).replace("\\", "\\\\")
        print(f"Uploading {file_path} to {target_stage_path}...")
        put_cmd = f"PUT 'file://{abs_path}' {target_stage_path} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
        cursor.execute(put_cmd)
        
        # Return the relative path of the file on the stage for downstream processing
        # PUT uploads to the directory. The filename remains the same.
        # So stage relative path is relative_path (with forward slashes)
        return relative_path.replace(os.sep, "/")
    except Exception as e:
        print(f"Error uploading {file_path}: {e}")
        raise
    finally:
        cursor.close()

def main():
    parser = argparse.ArgumentParser(description="Ingest local documents into Snowflake.")
    parser.add_argument("root_dir", help="Root directory containing documents")
    parser.add_argument("--prefix", default="local", help="URI scheme prefix for the documents (default: local)")
    
    args = parser.parse_args()
    prefix = args.prefix
    
    env_config = common.load_config()
    conn = common.get_snowflake_connection(env_config)
    
    stage_name = "documents"
    table_name = "document_metadata"
    
    print(f"Scanning local files in {args.root_dir}...")
    local_files = scan_local_files(args.root_dir, prefix)
    
    print(f"Fetching existing metadata from Snowflake ({table_name})...")
    remote_files = scan_remote_files(conn, table_name, prefix)
    
    to_add_or_update = []
    to_delete = []
    
    for uri, info in local_files.items():
        if uri not in remote_files:
            to_add_or_update.append(uri)
        else:
            if info["mtime"] > remote_files[uri]:
                 to_add_or_update.append(uri)
                 
    for uri in remote_files:
        if uri not in local_files:
            to_delete.append(uri)
            
    print(f"Found {len(to_add_or_update)} files to add/update and {len(to_delete)} files to delete.")
    
    # Process additions/updates
    cursor = conn.cursor()
    for uri in to_add_or_update:
        try:
            info = local_files[uri]
            
            # 1. Upload
            stage_rel_path = upload_file(conn, info, stage_name)
            
            # 2. Parse
            common.parse_document(cursor, stage_name, stage_rel_path, uri)
            
            # 3. Generate Metadata (Synthetic + Ground Truth)
            # For local ingestion, we don't have explicit sidecar metadata yet, 
            # so we pass minimal info or just empty string for ground truth part
            # effectively just using path/filename as context if needed, but the prompt handles the content.
            # We could pass "Filename: {stage_rel_path}" as ground truth.
            ground_truth = f"Filename: {stage_rel_path}"
            common.generate_metadata(cursor, uri, ground_truth)
            
            # 4. Chunk
            common.chunk_document(cursor, uri)
            
            # 5. Update Metadata Timestamp
            common.update_metadata_timestamp(cursor, table_name, uri, info["mtime"])
            
            # 6. Cleanup Stage
            common.cleanup_stage_file(cursor, stage_name, stage_rel_path)
            
            print(f"Successfully processed {uri}")
            
        except Exception as e:
            print(f"Error processing {uri}: {e}")
            # Continue to next file? or abort?
            # For a batch tool, better to continue and report errors.
            continue
            
    # Process deletions
    for uri in to_delete:
        try:
            common.delete_document_all(cursor, uri)
            print(f"Deleted {uri}")
        except Exception as e:
            print(f"Error deleting {uri}: {e}")

    cursor.close()
    conn.close()
    print("Sync complete.")

if __name__ == "__main__":
    main()