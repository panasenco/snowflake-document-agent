import os
from typing import Any

from agenteval.targets import BaseTarget, TargetResponse
import requests


class HostnameAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that verifies TLS against a different hostname."""

    def __init__(self, *args: Any, assert_hostname: Any, **kwargs: Any) -> None:
        self.assert_hostname = assert_hostname
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["assert_hostname"] = self.assert_hostname
        return super().init_poolmanager(*args, **kwargs)


class SnowflakeAgentTarget(BaseTarget):
    def __init__(
        self,
        agent_name: str,
        database: str = "SNOWFLAKE_INTELLIGENCE",
        schema: str = "AGENTS",
        snowflake_account: str | None = None,
        auth_token: str | None = None,
        assert_hostname: str | None = None,
    ) -> None:
        """Initializes a Snowflake Intelligence agent.
        Grabs the authentication token from the environment variable $SNOWFLAKE_TOKEN if not provided.
        Some Snowflake account identifiers have undescores, which breaks Python's hostname checking.
        If you get SSLError "Hostname mismatch", try passing assert_hostname="snowflakecomputing.com".
        """
        snowflake_account = snowflake_account or os.environ.get("SNOWFLAKE_ACCOUNT", None)
        if not snowflake_account:
            raise ValueError("snowflake_account must be provided or set in the environment variable SNOWFLAKE_ACCOUNT")
        auth_token = auth_token or os.environ.get("SNOWFLAKE_TOKEN", None)
        if not auth_token:
            raise ValueError("auth_token must be provided or set in the environment variable SNOWFLAKE_TOKEN")
        self.agent_url = (
            f"https://{snowflake_account}.snowflakecomputing.com/api/v2/"
            f"databases/{database}/schemas/{schema}/agents/{agent_name}"
        )
        self.session = requests.Session()
        self.session.headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if assert_hostname:
            self.session.mount("https://", HostnameAdapter(assert_hostname=assert_hostname))

    def run(self, prompt: str, /) -> dict[str, Any]:
        """Run an agent and get its response from the Snowflake Cortex REST API."""
        response = self.session.post(
            url=f"{self.agent_url}:run",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    },
                ],
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json()

    def invoke(self, prompt: str) -> TargetResponse:
        response_payload = self.run(prompt)
        if "content" not in response_payload:
            raise RuntimeError(f"No 'content' field in {response_payload=}")
        response_parts = [item["text"] for item in response_payload["content"] if item.get("type") == "text"]
        response_text = "\n".join(response_parts).strip()
        return TargetResponse(response=response_text)
