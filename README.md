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

<summary>Development Container</summary>

[Development Containers](https://containers.dev/) allow you to develop in Docker containers.

#### Starting the Development Container

In Visual Studio Code: Follow [this guide](https://code.visualstudio.com/docs/devcontainers/containers) to learn how to use development containers in Visual Studio Code.
The TL;DR is that you need Docker installed on your system and the Development Container extension installed in VS Code.
Then a prompt with a button to reopen the folder in a dev container should just appear when you open the folder in VS Code.
You can also click the `><` icon in the bottom left and choose "Reopen in Container".

#### Snowflake connection/configuration

The process will attempt to automatically mount your `~/.snowflake` directory.
Note that if you use private keys, there's some pain around making absolute paths work with `snowflake-connector-python`.
See [this issue](https://github.com/snowflakedb/snowflake-connector-python/issues/2746).

#### Alternative Development Container

If you're in a corporate environment that blocks Microsoft's container registry `mcr.microsoft.com`, you'll have to create your own development container configuration file in `.devcontainer/private/devcontainer.json`.
Then when you select "Reopen in Container", VS Code will ask you which configuration file you want to use.

Note that `uv` installs packages in the folder `.venv` in your workspace by default, but on a Windows host your workspace is stored in the Windows filesystem, and the interface between the two filesystems is painfully slow. To improve performance, set `UV_PROJECT_ENVIRONMENT` to a location elsewhere in the container.

#### Line ending issues

If you're running the development container on a Windows machine, you might run into line ending issues.
For this reason, the repository is configured to always use LF for line endings in `.gitattributes` and `.vscode/settings.json`, even on Windows.
Then you can switch between Windows and the Linux development container without either system freaking out about file line endings.
For more information and history, see the blog post [Mind the End of Your Line](https://adaptivepatchwork.com/2012/03/01/mind-the-end-of-your-line/).

#### Alternative Python package registries

If your corporate environment blocks the Python Package index `pypi.org`, you will have to configure `uv` to use the corporate package index by default instead.
Create a `uv.toml` file in the root of the repo:

```toml
[[index]]
name = "my-private-index"
url = "https://private.index.mycorp.com/index/api/pypi/simple"
default = true
```

You will also need to set the environment variable `UV_LOCKED` to `true` in your dev container definition so as to not overwrite the URLs in `uv.lock` with your corporate package index URLs.

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
