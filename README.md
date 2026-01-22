# snowflake-document-agent
Snowflake agent that enables you to chat with your documents

## Setup
1.  [Install and configure snowflake-cli](https://docs.snowflake.com/en/developer-guide/snowflake-cli/installation/installation).
2.  Copy the file `snowflake.example.yml` to `snowflake.yml` and change the values.
3.  Inspect each setup script in `./scripts/snowflake-cli/setup_*`.
    Only run each script if it makes sense to you and aligns with your organization's best practices!
    Run each script with a command like:
    ```sh
    snow sql -f scripts/snowflake-cli/script_name.sql
    ```
