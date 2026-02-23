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

<summary>Manual install using uv</summary>

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync
```

</details>

<details>

<summary>Manual install using pyenv</summary>

If you're in an environment where `uv` doesn't work well, you can use `pyenv` instead:

```sh
pyenv virtualenv snowflake-document-agent
~/.pyenv/versions/snowflake-document-agent/bin/pip install -e .
```

If you'd like to develop `snowflake-document-agent`, install the dev dependencies with `pip install -e .[dev]`.

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

#### Alternative Python package registries

If your corporate environment blocks the Python Package index `pypi.org`, you won't be able to take advantage of the lock file `uv.lock`.
In that case, rather than bothering with configuring `uv` to use the corporate registry, just do something like `/usr/local/bin/python3 -m pip install --group dev --editable /workspaces/snowflake-document-agent` and let the (hopefully sane) `pip` configuration in your corporate Docker image take care of things.

#### Line ending issues

If you're running the development container on a Windows machine, you might run into line ending issues.
For this reason, the repository is configured to always use LF for line endings in `.gitattributes` and `.vscode/settings.json`, even on Windows.
Then you can switch between Windows and the Linux development container without either system freaking out about file line endings.
For more information and history, see the blog post [Mind the End of Your Line](https://adaptivepatchwork.com/2012/03/01/mind-the-end-of-your-line/).

</details>

<details>

<summary>NixOS</summary>

Run to start a development environment:

```sh
nix develop
```

</details>

### Snowflake
1.  Configure the file [~/.snowflake/connections.toml](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect#connecting-using-the-connections-toml-file) which will work for both snowflake-cli and snowflake-connector-python.
2.  Copy the file `snowflake.example.yml` to `snowflake.yml` and update the values for your use case.
3.  Inspect each setup script in `./scripts/snowflake-cli/setup_*`.
    Only run each script if it makes sense to you and conforms to your organization's policies!
    Run each script with a command like:
    ```sh
    snow sql -c [connection] -f scripts/snowflake-cli/setup_00_example.sql
    ```

## Running the pipeline

### Ingest local files

Run this command to upload some documents to Snowflake and to see what changes are being synchronized:

```sh
ingest-local --snowflake-connection default --verbose path/to/your/documents
```

### Ingest OpenText files

OpenText ingestion requires the following environment variables to be set:
- 

Replace `12345678` in the below command with your real OpenText node ID that should be recursively traversed and
uploaded to Snowflake:

```sh
ingest-local --snowflake-connection default --verbose 12345678
```

## Using the agent

After an ingestion, your agent is ready to use!
The agent's name will be whatever is configured in your snowflake.yml.
Just ask the agent a question about anything within your documents and it should be able to answer!
