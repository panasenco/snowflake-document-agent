## Formatting and checking with ruff

```sh
ruff format .
ruff check --fix .
```

## Running tests

Run just the unit tests with `pytest`.

Run all tests with `pytest --run-integration`.
Note that integration tests will attempt to auto-connect to your Snowflake environment, and will create a temporary testing schema there.
