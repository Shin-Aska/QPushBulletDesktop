"""Narrow MCP tools over the authenticated core; approval is desktop-only."""
from typing import Any, Literal
import uuid

import httpx
from mcp.server import MCPServer
from pbdesk.config import Settings
from pbdesk.credentials import service_token, health_headers, verify_health


class HistoryUnavailable(ValueError):
    pass


def create_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or Settings.from_env()
    token = service_token(settings, "mcp")
    server = MCPServer("pbdesk", log_level="WARNING", instructions="Read local inbox and devices, or request a pending message action. A person must review and approve the exact action in the desktop. No tool can approve, directly send, edit, cancel, retrieve secrets, or run arbitrary requests.")

    async def request(path: str, params=None, payload=None):
        async with httpx.AsyncClient(base_url=f"http://{settings.core_host}:{settings.core_port}", timeout=5, trust_env=False, follow_redirects=False) as client:
            try:
                proof = health_headers(settings, "mcp")
                health = await client.get("/api/v1/health", headers=proof)
                health.raise_for_status()
                if not verify_health(settings, "mcp", proof, health.json()):
                    raise ValueError("Unexpected private core identity")
                identity = health.json()
                if identity.get('history_encryption') != 1:
                    raise HistoryUnavailable('Restart the existing core from the desktop to enable mandatory encrypted history.')
                if not identity.get('ready') or not identity.get('history_ready'):
                    raise HistoryUnavailable('Unlock and protect local history in the desktop before using MCP.')
                if payload is None:
                    response = await client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
                else:
                    response = await client.post(path, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(20, connect=2))
                response.raise_for_status()
                return response.json()
            except HistoryUnavailable:
                raise
            except httpx.HTTPStatusError as exc:
                if payload is not None and (exc.response.status_code >= 500 or exc.response.status_code == 408):
                    raise ValueError("Action creation could not be confirmed. Review desktop Outbox or reconcile using the exact same client_request_id and original payload. Do not retry with a new ID.") from None
                if exc.response.status_code == 409:
                    raise ValueError("Request conflicts with an existing action. Reuse a request ID only with exactly its original contents; review the desktop Outbox.") from None
                raise ValueError("The core rejected the request. Check capabilities, explicit device, recipient, and content in the desktop. No approval was granted.") from None
            except (httpx.HTTPError, ValueError):
                message = "Private core request failed. Run pbdesk-diagnostics to check service status."
                if payload is not None:
                    message += " Creation may have completed: review desktop Outbox or reconcile with the exact same client_request_id and contents. Never retry with a new ID merely because the result is unknown. No sending or approval is retried."
                raise ValueError(message) from None

    @server.tool(structured_output=True)
    async def capabilities() -> dict[str, Any]:
        """Return supported features, provider/encryption state, and current send readiness."""
        return await request("/api/v1/capabilities")

    @server.tool(structured_output=True)
    async def search_notifications(query: str = "") -> dict[str, Any]:
        """Search retained local items. Empty query lists the recent inbox; SMS history is not implied."""
        if len(query) > 1000:
            raise ValueError("Search query is limited to 1000 characters.")
        return await request("/api/v1/items", {"q": query})

    @server.tool(structured_output=True)
    async def get_item(item_id: int) -> dict[str, Any]:
        """Retrieve one retained item by its numeric local ID."""
        if item_id < 1:
            raise ValueError("Item ID must be positive.")
        return await request(f"/api/v1/items/{item_id}")

    @server.tool(structured_output=True)
    async def list_devices() -> dict[str, Any]:
        """List cached device IDs and push/SMS capabilities. Active registration does not imply online connectivity."""
        return await request("/api/v1/devices")

    @server.tool(structured_output=True)
    async def request_action(client_request_id: str, kind: Literal["note", "link", "sms"], transport: Literal["provider_plaintext"], device_id: str, body: str, recipient: str | None = None, title: str = "", url: str | None = None, expires_in_seconds: int = 3600) -> dict[str, Any]:
        """Save a PENDING action for desktop review, never send or approve it. Supply a stable UUID and an explicit capable device. SMS also requires an explicit E.164 phone number, never inferred from a notification. Transport MUST be provider_plaintext: Pushbullet can process this content; SMS carrier delivery is not end-to-end encrypted. A stored E2E password does not encrypt outgoing messages. After an unknown result reconcile only with exactly the same UUID and payload."""
        try:
            request_id = str(uuid.UUID(client_request_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("client_request_id must be a UUID generated for this exact intent.") from None
        if not device_id.strip():
            raise ValueError("Select an explicit device ID from list_devices.")
        return await request("/api/v1/actions", payload={"client_request_id": request_id, "kind": kind, "transport": transport, "device_id": device_id, "recipient": recipient, "title": title, "body": body, "url": url, "expires_in_seconds": expires_in_seconds})

    @server.tool(structured_output=True)
    async def action_status(action_id: int) -> dict[str, Any]:
        """Inspect a pending/approved action. upstream_accepted means Pushbullet accepted it, not that it was delivered. uncertain must not be automatically retried."""
        if action_id < 1:
            raise ValueError("Action ID must be positive.")
        return await request(f"/api/v1/actions/{action_id}")

    return server


def main():
    create_server().run(transport="stdio")
