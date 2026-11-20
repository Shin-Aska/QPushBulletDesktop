"""Narrow MCP tools over the authenticated core; approval is desktop-only."""
from typing import Any, Literal
import uuid

import httpx
from mcp.server import MCPServer
from pbdesk.config import Settings
from pbdesk.credentials import service_token, health_headers, verify_health


class HistoryUnavailable(ValueError):
    """Error raised when encrypted local history is unavailable or locked."""
    pass


def create_server(settings: Settings | None = None) -> MCPServer:
    """Create and configure the pbdesk MCP server instance.

    :param settings: Optional application settings; defaults to ``Settings.from_env()``.
    :type settings: Settings | None
    :return: Configured MCP server instance with registered pbdesk tools.
    :rtype: MCPServer
    """
    settings = settings or Settings.from_env()
    token = service_token(settings, "mcp")
    server = MCPServer("pbdesk", log_level="WARNING", instructions="Read local inbox and devices, or request a pending message action. A person must review and approve the exact action in the desktop. No tool can approve, directly send, edit, cancel, retrieve secrets, or run arbitrary requests.")

    async def request(path: str, params=None, payload=None):
        """Verify core readiness and perform an authenticated core API request.

        :param path: Relative core API endpoint path to request.
        :type path: str
        :param params: Optional query parameters for GET requests.
        :type params: dict[str, Any] | None
        :param payload: Optional JSON payload for POST requests; uses GET when None.
        :type payload: dict[str, Any] | None
        :return: Parsed JSON response dictionary from the private core API.
        :rtype: dict[str, Any]
        :raises HistoryUnavailable: If core lacks mandatory history encryption or encrypted history is locked.
        :raises ValueError: If core identity verification fails or the core rejects or fails the request.
        """
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
        """Return supported features, provider/encryption state, and current send readiness.

        :return: Core capabilities, provider connection status, encryption state, and send readiness.
        :rtype: dict[str, Any]
        """
        return await request("/api/v1/capabilities")

    @server.tool(structured_output=True)
    async def search_notifications(query: str = "") -> dict[str, Any]:
        """Search retained local items. Empty query lists the recent inbox; SMS history is not implied.

        :param query: Search query text, or empty string to list recent inbox items.
        :type query: str
        :return: Matching retained items payload from the core API.
        :rtype: dict[str, Any]
        :raises ValueError: If ``query`` exceeds 1000 characters.
        """
        if len(query) > 1000:
            raise ValueError("Search query is limited to 1000 characters.")
        return await request("/api/v1/items", {"q": query})

    @server.tool(structured_output=True)
    async def get_item(item_id: int) -> dict[str, Any]:
        """Retrieve one retained item by its numeric local ID.

        :param item_id: Positive numeric local ID of the retained item.
        :type item_id: int
        :return: Retained item details dictionary from the core API.
        :rtype: dict[str, Any]
        :raises ValueError: If ``item_id`` is less than 1.
        """
        if item_id < 1:
            raise ValueError("Item ID must be positive.")
        return await request(f"/api/v1/items/{item_id}")

    @server.tool(structured_output=True)
    async def list_devices() -> dict[str, Any]:
        """List cached device IDs and push/SMS capabilities. Active registration does not imply online connectivity.

        :return: Cached devices and capability metadata dictionary from the core API.
        :rtype: dict[str, Any]
        """
        return await request("/api/v1/devices")

    @server.tool(structured_output=True)
    async def request_action(client_request_id: str, kind: Literal["note", "link", "sms"], transport: Literal["provider_plaintext"], device_id: str, body: str, recipient: str | None = None, title: str = "", url: str | None = None, expires_in_seconds: int = 3600) -> dict[str, Any]:
        """Save a PENDING action for desktop review, never send or approve it. Supply a stable UUID and an explicit capable device. SMS also requires an explicit E.164 phone number, never inferred from a notification. Transport MUST be provider_plaintext: Pushbullet can process this content; SMS carrier delivery is not end-to-end encrypted. A stored E2E password does not encrypt outgoing messages. After an unknown result reconcile only with exactly the same UUID and payload.

        :param client_request_id: Stable UUID string generated for this exact action intent.
        :type client_request_id: str
        :param kind: Action kind (``note``, ``link``, or ``sms``).
        :type kind: Literal["note", "link", "sms"]
        :param transport: Explicit transport mode; must be ``provider_plaintext``.
        :type transport: Literal["provider_plaintext"]
        :param device_id: Target device identifier selected from ``list_devices``.
        :type device_id: str
        :param body: Message body or note content for the pending action.
        :type body: str
        :param recipient: Explicit E.164 phone number required when ``kind`` is ``sms``.
        :type recipient: str | None
        :param title: Optional title for note or link actions.
        :type title: str
        :param url: Target URL when ``kind`` is ``link``.
        :type url: str | None
        :param expires_in_seconds: Pending action expiration window in seconds.
        :type expires_in_seconds: int
        :return: Created or reconciled pending action payload from the core API.
        :rtype: dict[str, Any]
        :raises ValueError: If ``client_request_id`` is not a valid UUID or ``device_id`` is blank.
        """
        try:
            request_id = str(uuid.UUID(client_request_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("client_request_id must be a UUID generated for this exact intent.") from None
        if not device_id.strip():
            raise ValueError("Select an explicit device ID from list_devices.")
        return await request("/api/v1/actions", payload={"client_request_id": request_id, "kind": kind, "transport": transport, "device_id": device_id, "recipient": recipient, "title": title, "body": body, "url": url, "expires_in_seconds": expires_in_seconds})

    @server.tool(structured_output=True)
    async def action_status(action_id: int) -> dict[str, Any]:
        """Inspect a pending/approved action. upstream_accepted means Pushbullet accepted it, not that it was delivered. uncertain must not be automatically retried.

        :param action_id: Positive numeric identifier of the action to inspect.
        :type action_id: int
        :return: Action status dictionary from the core API.
        :rtype: dict[str, Any]
        :raises ValueError: If ``action_id`` is less than 1.
        """
        if action_id < 1:
            raise ValueError("Action ID must be positive.")
        return await request(f"/api/v1/actions/{action_id}")

    return server


def main():
    """Run the pbdesk MCP server over stdio."""
    create_server().run(transport="stdio")
