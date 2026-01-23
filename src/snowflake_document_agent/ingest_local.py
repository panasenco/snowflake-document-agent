import argparse
import os
import sys
import yaml
import snowflake.connector
from pathlib import Path
from datetime import datetime, timezone

def load_config(config_path="snowflake.yml"):
    if not os.path.exists(config_path):
        print(f"Error: Config file '{config_path}' not found.")
        sys.exit(1)
    
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    return config.get("env", {})

def get_snowflake_connection(env_config):
    # Connect to Snowflake using environment variables for auth
    # and config file for context
    try:
        conn = snowflake.connector.connect(
            role=env_config.get("role"),
            warehouse=env_config.get("warehouse"),
            database=env_config.get("database"),
            schema=env_config.get("schema"),
        )
        return conn
    except Exception as e:
        print(f"Failed to connect to Snowflake: {e}")
        sys.exit(1)

def scan_local_files(root_dir, prefix):
    local_files = {}
    root_path = Path(root_dir)
    
    if not root_path.exists():
        print(f"Error: Directory '{root_dir}' does not exist.")
        sys.exit(1)

    for path in root_path.rglob("*"):
        if path.is_file():
            # Skip hidden files
            if path.name.startswith("."):
                continue
                
            relative_path = path.relative_to(root_path)
            # Ensure forward slashes for URI
            uri_path = str(relative_path).replace(os.sep, "/")
            source_uri = f"{prefix}{uri_path}"
            
            # Get modification time in UTC
            mtime = path.stat().st_mtime
            dt_utc = datetime.fromtimestamp(mtime, tz=timezone.utc)
            
            local_files[source_uri] = {
                "path": path,
                "mtime": dt_utc,
                "relative_path": str(relative_path) # native separator for file operations
            }
            
    return local_files

def scan_remote_files(conn, table_name, prefix):
    remote_files = {}
    cursor = conn.cursor()
    try:
        # Check if table exists first to avoid confusing errors? 
        # Assuming table exists based on instructions.
        
        # We filter by prefix to only manage files belonging to this "source"
        # Use parameter binding for safety
        query = f"SELECT source_uri, modified_at_utc FROM {table_name} WHERE source_uri LIKE %s"
        cursor.execute(query, (f"{prefix}%",))
        for row in cursor:
            source_uri = row[0]
            modified_at = row[1]
            if modified_at and modified_at.tzinfo is None:
                # Assume stored as UTC if naive
                modified_at = modified_at.replace(tzinfo=timezone.utc)
            remote_files[source_uri] = modified_at
    finally:
        cursor.close()
    return remote_files

def upload_file(conn, local_file_info, stage_name):
    file_path = local_file_info["path"]
    relative_path = local_file_info["relative_path"]
    
    # We want to maintain directory structure in the stage
    # PUT file:///path/to/file @stage/path/to/subdir
    
    # Extract directory from relative path for the target stage path
    target_dir = os.path.dirname(relative_path)
    if target_dir:
        # replace os sep with / for snowflake stage
        target_stage_path = f"@{stage_name}/{target_dir.replace(os.sep, '/')}"
    else:
        target_stage_path = f"@{stage_name}"

    cursor = conn.cursor()
    try:
        # Normalize file path for the SQL command
        abs_path = str(file_path.absolute()).replace("\\", "\\\\") # Windows safety
        
        print(f"Uploading {file_path} to {target_stage_path}...")
        # PUT command doesn't support standard binding for the file path/stage usually in the same way,
        # but the path is local.
        put_cmd = f"PUT 'file://{abs_path}' {target_stage_path} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
        cursor.execute(put_cmd)
    except Exception as e:
        print(f"Error uploading {file_path}: {e}")
        raise
    finally:
        cursor.close()

def update_metadata(conn, table_name, source_uri, mtime):
    cursor = conn.cursor()
    try:
        # Merge statement using bindings
        # mtime is a datetime object
        
        query = f"""
        MERGE INTO {table_name} AS target
        USING (SELECT %s AS source_uri, %s AS modified_at_utc) AS source
        ON target.source_uri = source.source_uri
        WHEN MATCHED THEN
            UPDATE SET modified_at_utc = source.modified_at_utc
        WHEN NOT MATCHED THEN
            INSERT (source_uri, modified_at_utc, metadata)
            VALUES (source.source_uri, source.modified_at_utc, NULL)
        """
        cursor.execute(query, (source_uri, mtime))
    finally:
        cursor.close()

def delete_file(conn, table_name, stage_name, source_uri, prefix):
    # Remove from metadata
    cursor = conn.cursor()
    try:
        print(f"Removing {source_uri}...")
        cursor.execute(f"DELETE FROM {table_name} WHERE source_uri = %s", (source_uri,))
        
        # Optional: Remove from stage.
        # Derived path from source_uri
        # source_uri = prefix + path/to/file
        # stage path = path/to/file
        if source_uri.startswith(prefix):
            rel_path = source_uri[len(prefix):]
            # Ensure no leading slash for stage removal
            if rel_path.startswith("/"):
                rel_path = rel_path[1:]
                
            # REMOVE command usually doesn't take bindings for the path literal efficiently if part of syntax
            # But we can try or just strictly sanitizing.
            # Ideally: REMOVE @stage/path
            
            # Simple sanitization to prevent injection if we insert into string
            # In Snowflake REMOVE is a command.
            safe_rel_path = rel_path.replace("'", "''")
            rm_cmd = f"REMOVE '@{stage_name}/{safe_rel_path}'"
            
            # We execute this best-effort
            try:
                cursor.execute(rm_cmd)
            except Exception as e:
                print(f"Warning: Failed to remove file from stage: {e}")

    finally:
        cursor.close()

def main():
    parser = argparse.ArgumentParser(description="Ingest local documents into Snowflake.")
    parser.add_argument("root_dir", help="Root directory containing documents")
    parser.add_argument("--prefix", default="local://", help="URI prefix for the documents (default: local://)")
    
    args = parser.parse_args()
    
    env_config = load_config()
    conn = get_snowflake_connection(env_config)
    
    stage_name = "documents"
    table_name = "document_metadata"
    
    print(f"Scanning local files in {args.root_dir}...")
    local_files = scan_local_files(args.root_dir, args.prefix)
    
    print(f"Fetching existing metadata from Snowflake ({table_name})...")
    remote_files = scan_remote_files(conn, table_name, args.prefix)
    
    to_add_or_update = []
    to_delete = []
    
    for uri, info in local_files.items():
        if uri not in remote_files:
            to_add_or_update.append(uri)
        else:
            # Compare timestamps
            # Use a small epsilon for float comparison safety if needed, 
            # but usually > is fine if remote is strictly older.
            if info["mtime"] > remote_files[uri]:
                 to_add_or_update.append(uri)
                 
    for uri in remote_files:
        if uri not in local_files:
            to_delete.append(uri)
            
    print(f"Found {len(to_add_or_update)} files to add/update and {len(to_delete)} files to delete.")
    
    for uri in to_add_or_update:
        info = local_files[uri]
        upload_file(conn, info, stage_name)
        update_metadata(conn, table_name, uri, info["mtime"])
        print(f"Updated {uri}")
        
    for uri in to_delete:
        delete_file(conn, table_name, stage_name, uri, args.prefix)
        print(f"Deleted {uri}")
        
    conn.close()
    print("Sync complete.")

if __name__ == "__main__":
    main()