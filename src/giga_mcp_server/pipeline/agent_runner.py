from __future__ import annotations

import json
import re
from typing import Any

import anthropic
import jsonschema
import structlog

from giga_mcp_server.pipeline.agent_prompts import AGENT_REGISTRY

logger = structlog.get_logger()

_PIPELINE_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 8192
_MAX_PARSE_RETRIES = 3


class AgentValidationError(Exception):
    """Raised when agent input or output fails schema validation."""


class AgentRunner:
    """Calls Claude for a named pipeline agent, validating I/O against schemas."""

    def __init__(self, api_key: str, model: str = _PIPELINE_MODEL) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def run(self, agent_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """Run a named agent, returning validated output.

        Validates input against the agent's input_schema, calls Claude,
        then validates the parsed JSON output against the agent's output_schema.
        Retries up to _MAX_PARSE_RETRIES times on parse/validation failures.
        """
        if agent_name not in AGENT_REGISTRY:
            raise ValueError(f"Unknown agent: {agent_name!r}. "
                             f"Available: {list(AGENT_REGISTRY)}")

        config = AGENT_REGISTRY[agent_name]
        self._validate_schema(input_data, config["input_schema"], context=f"{agent_name}.input")

        user_message = json.dumps(input_data, indent=2)
        messages: list[dict[str, str]] = [{"role": "user", "content": user_message}]

        last_error: Exception | None = None
        for attempt in range(1, _MAX_PARSE_RETRIES + 1):
            try:
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=_MAX_TOKENS,
                    system=config["system_prompt"],
                    messages=messages,
                )
                raw_text = response.content[0].text.strip()
                parsed = self._parse_json(raw_text)
                self._validate_schema(
                    parsed, config["output_schema"], context=f"{agent_name}.output"
                )
                logger.info(
                    "agent_run_ok",
                    agent=agent_name,
                    attempt=attempt,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
                return parsed
            except (json.JSONDecodeError, jsonschema.ValidationError, AgentValidationError) as e:
                last_error = e
                logger.warning(
                    "agent_parse_retry",
                    agent=agent_name,
                    attempt=attempt,
                    error=str(e),
                )
                # Ask Claude to fix the output on retry
                if attempt < _MAX_PARSE_RETRIES:
                    messages = [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": raw_text},  # type: ignore[possibly-undefined]
                        {
                            "role": "user",
                            "content": (
                                f"Your previous response failed validation: {e}\n"
                                "Please return ONLY valid JSON matching the required schema."
                            ),
                        },
                    ]

        raise AgentValidationError(
            f"Agent {agent_name!r} failed after {_MAX_PARSE_RETRIES} attempts: {last_error}"
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Strip markdown fences and parse JSON."""
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    @staticmethod
    def _validate_schema(
        data: dict[str, Any], schema: dict[str, Any], context: str
    ) -> None:
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            raise AgentValidationError(
                f"Schema validation failed for {context}: {e.message}"
            ) from e
