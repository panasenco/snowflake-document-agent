## Running tests

Run unit tests with `uv run pytest`.

Run all tests with `uv run pytest --run-integration`.
Note that integration tests will attempt to auto-connect to your Snowflake environment, and will create a temporary testing schema there.
