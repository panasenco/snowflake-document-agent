# snowdoc
CLI to incrementally load documents into Snowflake for use with Snowflake Intelligence agents

Building on the foundation of Doneyli De Jesus' [snowflake-intelligent-rag-chatbot](https://github.com/sfc-gh-ddejesus/snowflake-intelligent-rag-chatbot).
Adding a more production-grade pipeline for ingesting new documents incrementally.

## Setup

### Your computer

First, clone this repository:
```sh
git clone https://github.com/panasenco/snowdoc.git
cd snowdoc
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
pyenv virtualenv snowdoc
~/.pyenv/versions/snowdoc/bin/pip install -e .
```

If you'd like to develop `snowdoc`, install the dev dependencies with `pip install -e .[dev]`.

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
In that case, rather than bothering with configuring `uv` to use the corporate registry, just do something like `/usr/local/bin/python3 -m pip install --group dev --editable /workspaces/snowdoc` and let the (hopefully sane) `pip` configuration in your corporate Docker image take care of things.

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
snowdoc ingest-local --snowflake-connection default --verbose path/to/your/documents
```

### Ingest OpenText files

OpenText ingestion requires the following environment variables to be set:
- 

Replace `12345678` in the below command with your real OpenText node ID that should be recursively traversed and
uploaded to Snowflake:

```sh
snowdoc ingest-opentext --snowflake-connection default --verbose 12345678
```

### Ingestion notes
- You'll need the program `antiword` installed to process .doc (Word 1997-2003) files.

## Using the agent

After an ingestion, your agent is ready to use!
The agent's name will be whatever is configured in your snowflake.yml.
Just ask the agent a question about anything within your documents and it should be able to answer!

## Automated evaluations

We currently only support the AWS-based [agent-evaluation](https://awslabs.github.io/agent-evaluation/evaluators/)
framework, though other frameworks may be added in the future.

### AWS agent-evaluation

We support the open-source AWS Labs framework [agent-evaluation](https://awslabs.github.io/agent-evaluation/evaluators/)
to run automated evaluations on the agent.

The advantage of agent-evaluation is it allows a direct apples-to-apples comparison with AWS Bedrock based agents.
The disadvantage is that you need to be signed into an AWS account with Bedrock access to run it.

To run automated evaluations, be sure to install the dev dependencies with either `pip install -e .[dev]` or `uv sync`.

Then create a subfolder `agenteval/mytests`, copying `agenteval/example/agenteval.yml` into the new folder.
Change the config parameters `agent_name` and `snowflake_account`.
You'll also need to export the environment variable `SNOWFLAKE_TOKEN` containing a Snowflake auth token for the account
(tested with PAT, but should work with PAT / SSH / OAuth).
Change the example step and expected result to be something actually meaningful for the agent you're testing.

Finally:
```sh
cd agenteval/mytests
agenteval run
```

This will run all the prompts and use an LLM judge to compare the outputs with the expected results.
The results will be saved in the file `agenteval_summary.md`.
Be sure to rename this file if you want to keep the results - otherwise the file will be overwritten on the next run!
