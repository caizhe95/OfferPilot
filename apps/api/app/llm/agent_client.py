"""Agent client: HTTP client for calling the TS pi-mono Agent service."""

import json
import httpx
from typing import AsyncIterator, Optional
from app.core.config import settings


class AgentClient:
    """HTTP client for the pi-mono Agent service (TypeScript Express server)."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or f"http://localhost:{settings.agent_port}").rstrip("/")

    async def health(self) -> dict:
        """Check agent service health."""
        async with httpx.AsyncClient(timeout=1) as client:
            resp = await client.get(f"{self.base_url}/health")
            resp.raise_for_status()
            return resp.json()

    async def run(
        self,
        input_text: str,
        session_id: Optional[str] = None,
    ) -> dict:
        """Call agent /agent/run (non-streaming JSON response)."""
        async with httpx.AsyncClient(timeout=1) as client:
            body = {"input": input_text}
            if session_id:
                body["session_id"] = session_id
            resp = await client.post(
                f"{self.base_url}/agent/run",
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    async def run_stream(
        self,
        input_text: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Call agent /agent/run-stream and yield SSE events.

        Yields parsed JSON event dicts.
        """
        async with httpx.AsyncClient(timeout=300) as client:
            body = {"input": input_text}
            if session_id:
                body["session_id"] = session_id

            async with client.stream(
                "POST",
                f"{self.base_url}/agent/run-stream",
                json=body,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            event = json.loads(data_str)
                            yield event
                        except json.JSONDecodeError:
                            continue

    async def list_tools(self) -> dict:
        """List available tools from agent service."""
        async with httpx.AsyncClient(timeout=1) as client:
            resp = await client.get(f"{self.base_url}/agent/tools")
            resp.raise_for_status()
            return resp.json()


# Singleton
agent_client = AgentClient()
