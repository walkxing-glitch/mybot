"""Neural-Twin tool — call the digital-twin service for predictions and analysis."""

from __future__ import annotations

import json
from typing import Any

import httpx

from mybot.tools.base import BaseTool, ToolResult

DEFAULT_BASE_URL = "http://localhost:8004"
DEFAULT_TIMEOUT = 30.0

_ALLOWED_OPS = (
    "predict_decision",
    "analyze_habits",
    "detect_anomaly",
    "get_day_forecast",
)


class NeuralTwinTool(BaseTool):
    """HTTP client for the Neural-Twin (Keras digital twin) service."""

    name = "neural_twin"
    description = (
        "Interact with the Neural-Twin digital twin: predict a decision for a "
        "described scenario, analyze consumption habits, detect anomalies in "
        "new transaction data, or get today's day-level forecast."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(_ALLOWED_OPS),
                "description": "Which Neural-Twin operation to run.",
            },
            "scenario": {
                "type": "string",
                "description": "Free-text scenario description (for predict_decision).",
            },
            "data": {
                "type": "object",
                "description": "Payload object (e.g. transaction dict for detect_anomaly, or scenario features).",
            },
        },
        "required": ["operation"],
    }

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    # ---------------------------------------------------------------- helpers

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ToolResult:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(method, url, json=json_body, params=params)
        except httpx.ConnectError as exc:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Cannot reach Neural-Twin service at {self.base_url}: {exc}. "
                    "Is the Neural-Twin HTTP service running?"
                ),
            )
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                output="",
                error=f"Neural-Twin timed out after {self.timeout:.0f}s.",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"HTTP error: {exc}")

        if resp.status_code >= 400:
            return ToolResult(
                success=False,
                output="",
                error=f"Neural-Twin returned HTTP {resp.status_code}: {resp.text[:500]}",
            )

        try:
            data = resp.json()
            pretty = json.dumps(data, ensure_ascii=False, indent=2)
        except ValueError:
            pretty = resp.text
        return ToolResult(success=True, output=pretty)

    # -------------------------------------------------------------- operations

    async def execute(self, **params) -> ToolResult:
        op = params.get("operation")
        if op not in _ALLOWED_OPS:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown operation {op!r}. Allowed: {', '.join(_ALLOWED_OPS)}.",
            )

        try:
            if op == "predict_decision":
                scenario = params.get("scenario")
                extra = params.get("data") or {}
                if not scenario and not extra:
                    return ToolResult(
                        success=False,
                        output="",
                        error="'scenario' (string) or 'data' (object) is required for predict_decision.",
                    )
                payload: dict[str, Any] = {}
                if scenario:
                    payload["scenario"] = scenario
                if isinstance(extra, dict):
                    payload.update(extra)
                return await self._request("POST", "/predict", json_body=payload)

            if op == "analyze_habits":
                return await self._request("GET", "/habits/analyze")

            if op == "detect_anomaly":
                data = params.get("data")
                if not isinstance(data, dict):
                    return ToolResult(
                        success=False,
                        output="",
                        error="'data' (object) is required for detect_anomaly.",
                    )
                return await self._request("POST", "/anomaly/detect", json_body=data)

            if op == "get_day_forecast":
                return await self._request("GET", "/forecast/today")

        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Unhandled error: {exc}")

        return ToolResult(success=False, output="", error="Unreachable branch.")


tools = [NeuralTwinTool()]
