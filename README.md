# snowflake-document-agent
Snowflake agent that enables you to chat with your documents

Building on the foundation of Doneyli De Jesus' [snowflake-intelligent-rag-chatbot](https://github.com/sfc-gh-ddejesus/snowflake-intelligent-rag-chatbot).
Adding a more production-grade pipeline for ingesting new documents incrementally.

## Setup

### Development environment
```sh
git clone https://github.com/panasenco/snowflake-document-agent.git
cd snowflake-document-agent
```

On most systems: Install [snowflake-cli](https://docs.snowflake.com/en/developer-guide/snowflake-cli/installation/installation) and [uv](https://docs.astral.sh/uv/getting-started/installation/), then `uv sync`.
On NixOS: `nix develop`.

### Snowflake
1.  [Install snowflake-cli](https://docs.snowflake.com/en/developer-guide/snowflake-cli/installation/installation).
2.  Configure the file [connections.toml](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect#connecting-using-the-connections-toml-file) which will work for both snowflake-cli and snowflake-connector-python.
3.  Copy the file `snowflake.example.yml` to `snowflake.yml` and change the values.
4.  Inspect each setup script in `./scripts/snowflake-cli/setup_*`.
    Only run each script if it makes sense to you and aligns with your organization's best practices!
    Run each script with a command like:
    ```sh
    snow sql -f scripts/snowflake-cli/script_name.sql
    ```
