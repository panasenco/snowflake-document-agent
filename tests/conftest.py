from pathlib import Path
from time import time

import pytest
import snowflake.connector
import yaml

from snowflake_document_agent.common import create_temporary_updated_uris

def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False, help="run integration tests")
    parser.addoption("--connection-name", action="store", default=None, help="Snowflake connection name from config")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as integration test")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(reason="need --run-integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


@pytest.fixture(scope="session")
def snowflake_conn(pytestconfig):
    """
    Establishes a connection to Snowflake.
    Allows overriding the connection name via --connection-name.
    """
    if not pytestconfig.getoption("--run-integration"):
        yield None
        return
    conn_name = pytestconfig.getoption("--connection-name")
    try:
        conn = snowflake.connector.connect(**({"connection_name": conn_name} if conn_name else {}))
        yield conn
        conn.close()
    except Exception as e:
        pytest.fail(f"Failed to connect to Snowflake: {e}")

@pytest.fixture(scope="session")
def test_schema(snowflake_conn):
    """
    Creates a temporary schema for the test session and runs the DDL setup.
    Teardown drops the schema.
    """
    schema_name = f"TEST_DOCUMENT_AGENT_{int(time())}"
    cursor = snowflake_conn.cursor()
    try:
        cursor.execute(f"CREATE SCHEMA {schema_name}")
        cursor.execute(f"USE SCHEMA {schema_name}")

        # Read and apply DDL
        # We need to find the DDL file relative to the project root
        # Assuming tests are run from root
        ddl_path = Path("scripts/snowflake-cli/setup_04_tables_stages.sql")
        if ddl_path.exists():
            content = ddl_path.read_text()
            # simple split by semicolon, filtering out 'use' commands which might refer to bad roles
            # Remove comments (lines starting with --) before processing
            lines = [line for line in content.splitlines() if not line.strip().startswith("--")]
            clean_content = "\n".join(lines)
            statements = clean_content.split(";")
            for stmt in statements:
                stmt = stmt.strip()
                if not stmt:
                    continue
                # Skip templated 'use' commands or specific 'use role' commands
                if stmt.lower().startswith("use role") or stmt.lower().startswith("use schema"):
                    continue

                cursor.execute(stmt)
        else:
            pytest.fail(f"DDL file not found at {ddl_path}")

        yield schema_name
    finally:
        # Teardown
        try:
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema_name}")
        except Exception as e:
            print(f"Warning: Failed to drop test schema {schema_name}: {e}")
        cursor.close()


@pytest.fixture(scope="session")
def test_config(test_schema):
    """
    Returns a config dict for the tests
    """
    with open(Path(__file__).parent.parent / "snowflake.example.yml", "r") as config_file:
        config = yaml.safe_load(config_file)["env"]
    # Don't override role, warehouse, or database in the connection
    for attribute in ["role", "warehouse", "database"]:
        if attribute in config:
            del config[attribute]
    # Override the schema
    config["schema"] = test_schema
    yield config


@pytest.fixture()
def updated_uris(snowflake_conn, test_config):
    """
    Create the temporary table `updated_uris`
    Teardown drops the table.
    """
    create_temporary_updated_uris(snowflake_conn, config=test_config)
    table_identifier = f"{test_config['schema']}.updated_uris"
    yield table_identifier
    with snowflake_conn.cursor() as cursor:
        cursor.execute(f"drop table {table_identifier}")
