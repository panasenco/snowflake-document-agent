from pathlib import Path
from time import time

import pytest
import snowflake.connector
import yaml

from snowflake_document_agent.common import ALL_TABLES, load_config, clear_stage
from snowflake_document_agent.ingest_opentext import OpenTextClient


def pytest_addoption(parser):
    parser.addoption("--run-deployment", action="store_true", default=False, help="run deployment tests")
    parser.addoption(
        "--snowflake-connection-name", action="store", default=None, help="Snowflake connection name from config"
    )
    parser.addoption(
        "--opentext-node-id", action="store", type=int, default=None, help="OpenText node ID for integration tests"
    )
    parser.addoption(
        "--danger-use-existing-schema",
        action="store_true",
        default=False,
        help="Use existing schema and truncate test tables instead of creating new schema (DANGEROUS for production!)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "deployment: mark test as deployment test")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-deployment"):
        return
    skip_deployment = pytest.mark.skip(reason="need --run-deployment option to run")
    for item in items:
        if "deployment" in item.keywords:
            item.add_marker(skip_deployment)


@pytest.fixture(scope="session")
def snowflake_conn(pytestconfig):
    """
    Establishes a connection to Snowflake.
    Requires --snowflake-connection-name to be provided when --run-deployment is used.
    Skips gracefully if Snowflake connection name is not provided.
    """
    if not pytestconfig.getoption("--run-deployment"):
        yield None
        return

    conn_name = pytestconfig.getoption("--snowflake-connection-name")
    if not conn_name:
        pytest.skip("Snowflake integration tests skipped: --snowflake-connection-name not provided")
        return

    try:
        conn = snowflake.connector.connect(connection_name=conn_name)
        yield conn
        conn.close()
    except Exception as e:
        pytest.fail(f"Failed to connect to Snowflake: {e}")


@pytest.fixture(scope="session")
def temp_schema(snowflake_conn, pytestconfig):
    """
    Creates a temporary schema for the test session and runs the DDL setup.
    Child fixture - use test_schema instead.
    """
    # Skip if snowflake_conn is None (integration tests not enabled)
    if snowflake_conn is None:
        yield None
        return

    # Skip if using existing schema
    if pytestconfig.getoption("--danger-use-existing-schema"):
        yield None
        return

    cursor = snowflake_conn.cursor()
    schema_name = f"TEST_DOCUMENT_AGENT_{int(time())}"
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
        # Teardown: drop the temporary schema
        try:
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema_name}")
        except Exception as e:
            print(f"Warning: Failed to drop test schema {schema_name}: {e}")
        cursor.close()


@pytest.fixture(scope="function")
def existing_schema(snowflake_conn, pytestconfig):
    """
    Uses existing schema from real config and truncates test tables.
    Child fixture - use test_schema instead.
    """
    # Only available with --danger-use-existing-schema flag
    if not pytestconfig.getoption("--danger-use-existing-schema"):
        yield None
        return

    # Skip if snowflake_conn is None (integration tests not enabled)
    if snowflake_conn is None:
        yield None
        return

    # Load real config to get schema name and role
    real_config = load_config()
    schema_name = real_config.get("schema")
    if not schema_name:
        pytest.fail("Schema not specified in snowflake.yml configuration")

    cursor = snowflake_conn.cursor()

    # Use regular role from config for truncation permissions
    if real_config.get("role"):
        cursor.execute(f"USE ROLE {real_config['role']}")
        print(f"🧹 Using existing schema {schema_name} with role: {real_config['role']}")

    # Truncate the test tables and clear stage to prepare for clean testing
    print(f"🧹 Truncating test tables in {schema_name}...")
    for table in ALL_TABLES:
        cursor.execute(f"TRUNCATE TABLE IF EXISTS {table}")
        print(f"  Truncated {table}")

    clear_stage(cursor)
    print("  Cleared @documents stage")

    yield schema_name

    # No cleanup - leave data for inspection
    cursor.close()


@pytest.fixture(scope="function")
def test_schema(temp_schema, existing_schema, pytestconfig):
    """
    Unified test schema fixture that delegates to appropriate child fixture.
    Returns schema name for tests to use.
    """
    if pytestconfig.getoption("--danger-use-existing-schema"):
        return existing_schema
    else:
        return temp_schema


@pytest.fixture(scope="session")
def opentext_conn(pytestconfig):
    """
    Creates an OpenText client for integration tests.
    Only available when --run-deployment is set to true.
    Reads configuration from environment variables.
    Skips gracefully if OpenText credentials are not available.
    """
    if not pytestconfig.getoption("--run-deployment"):
        yield None
        return

    try:
        # Create OpenText client using environment variables
        client = OpenTextClient()
        yield client
    except ValueError as e:
        # Skip if OpenText credentials are missing
        if "Missing required OpenText parameters" in str(e):
            pytest.skip(f"OpenText integration tests skipped: {e}")
        else:
            pytest.fail(f"Failed to create OpenText client: {e}")
    except Exception as e:
        pytest.fail(f"Failed to create OpenText client: {e}")


@pytest.fixture(scope="session")
def example_config(temp_schema):
    """
    Returns config dict for temporary schema tests using snowflake.example.yml.
    Child fixture - use test_config instead.
    """
    if temp_schema is None:
        yield None
        return

    with open(Path(__file__).parent.parent / "snowflake.example.yml", "r") as f:
        config = yaml.safe_load(f)["env"]

    # Don't override role, warehouse, or database in the connection for temp schema
    for attribute in ["role", "warehouse", "database"]:
        if attribute in config:
            del config[attribute]

    # Override the schema with temp schema name
    config["schema"] = temp_schema
    yield config


@pytest.fixture(scope="function")
def real_config(existing_schema):
    """
    Returns config dict for existing schema tests using snowflake.yml.
    Child fixture - use test_config instead.
    """
    if existing_schema is None:
        yield None
        return

    with open(Path(__file__).parent.parent / "snowflake.yml", "r") as f:
        config = yaml.safe_load(f)["env"]

    # Override the schema with existing schema name (should be the same, but just to be sure)
    config["schema"] = existing_schema
    yield config


@pytest.fixture(scope="function")
def test_config(example_config, real_config, pytestconfig):
    """
    Unified test config fixture that delegates to appropriate child fixture.
    Returns config dict for tests to use.
    """
    if pytestconfig.getoption("--danger-use-existing-schema"):
        return real_config
    else:
        return example_config
