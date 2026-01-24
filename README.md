# snowflake-document-agent
Snowflake agent that enables you to chat with your documents

Building on the foundation of Doneyli De Jesus' [snowflake-intelligent-rag-chatbot](https://github.com/sfc-gh-ddejesus/snowflake-intelligent-rag-chatbot).
Adding a more production-grade pipeline for ingesting new documents incrementally.

## Setup

### Your computer

First, clone this repository:
```sh
git clone https://github.com/panasenco/snowflake-document-agent.git
cd snowflake-document-agent
```

Then follow one of the below instructions:

<details>

<summary>Manual install</summary>

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync
```

</details>

<details>

<summary>Dev Container</summary>

In Visual Studio Code: Follow [this guide](https://code.visualstudio.com/docs/devcontainers/containers) to learn how to use development containers in Visual Studio Code.
The TL;DR is that if you have Docker installed on your system, a prompt with a button to reopen the folder in a dev container should just appear when you open the folder in VS Code.
You can also click the `><` icon in the bottom left and choose "Reopen in Container".

The Dev Container will attempt to automatically mount your `~/.snowflake` directory.
Note that if you use private keys, there's some pain around making absolute paths work with `snowflake-connector-python`.
See [this issue](https://github.com/snowflakedb/snowflake-connector-python/issues/2746).

</details>

<details>

<summary>NixOS</summary>

Run to start a development environment:

```sh
nix develop
```

</details>

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

## Running the pipeline

### Ingest local files

Run this command to upload some documents to Snowflake and to see what changes are being synchronized:

```sh
ingest-local path/to/your/documents --verbose
```
