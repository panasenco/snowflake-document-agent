import logging
import os
from typing import Any

from agenteval.targets import BaseTarget, TargetResponse
import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter, before_sleep_log

logger = logging.getLogger(__name__)


def _is_server_error(exc: BaseException) -> bool:
    """Return True if the exception is an HTTP 5xx server error."""
    return isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code >= 500


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

        # Initialize conversation thread state for multi-turn support
        self.thread_id = None
        self.parent_message_id = 0

    @retry(
        retry=retry_if_exception(_is_server_error),
        stop=stop_after_attempt(6),
        wait=wait_exponential_jitter(initial=2, max=60, jitter=5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def run(self, prompt: str, /) -> dict[str, Any]:
        """Run an agent and get its response from the Snowflake Cortex REST API."""
        # Build request payload with conversation threading support
        payload = {
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
        }

        # Add threading parameters if this is a continuing conversation
        if self.thread_id is not None:
            payload["thread_id"] = self.thread_id
            payload["parent_message_id"] = self.parent_message_id

        response = self.session.post(
            url=f"{self.agent_url}:run",
            json=payload,
        )
        response.raise_for_status()
        response_data = response.json()

        # Update conversation state for next turn
        if "thread_id" in response_data:
            self.thread_id = response_data["thread_id"]
        if "message_id" in response_data:
            self.parent_message_id = response_data["message_id"]

        return response_data

    def invoke(self, prompt: str) -> TargetResponse:
        response_payload = self.run(prompt)
        if "content" not in response_payload:
            raise RuntimeError(f"No 'content' field in {response_payload=}")
        response_parts = [item["text"] for item in response_payload["content"] if item.get("type") == "text"]
        response_text = "\n".join(response_parts).strip()
        return TargetResponse(response=response_text)
