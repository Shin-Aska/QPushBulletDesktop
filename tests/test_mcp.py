"""Official SDK client round trip; fake HTTP transport never opens a socket."""
import asyncio
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_official_stdio_client(tmp_path):
    script = tmp_path / "fake_adapter.py"
    script.write_text('''
import httpx
import json
from pbdesk.mcp_server import server
from pbdesk.config import Settings
from pbdesk.credentials import health_response
settings = Settings.from_env()
actual = httpx.AsyncClient

def respond(request):
    if request.url.path.endswith('health'):
        assert 'Authorization' not in request.headers
        return httpx.Response(200, json=health_response(settings, request.headers, '11111111-1111-1111-1111-111111111111', True))
    assert request.headers['Authorization'].startswith('Bearer ')
    if request.url.path.endswith('capabilities'):
        return httpx.Response(200, json={'features': {'send': False, 'inbox': True}, 'provider_state': 'fixture'})
    if request.url.path.endswith('/devices'):
        return httpx.Response(200, json={'devices': [{'id': 'fake-phone', 'can_sms': True, 'active': True}], 'synced_at': None})
    if request.url.path.endswith('/actions'):
        assert request.method == 'POST'
        assert request.extensions['timeout']['read'] >= 20
        body = json.loads(request.content)
        assert body['transport'] == 'provider_plaintext'
        assert 'allow_provider_plaintext' not in body and 'confirmed' not in body
        return httpx.Response(200, json=dict(body, id=7, revision=1, state='pending_approval', requested_by='mcp'))
    if '/actions/' in request.url.path:
        assert request.method == 'GET'
        return httpx.Response(200, json={'id': 7, 'state': 'pending_approval', 'requested_by': 'mcp'})
    item = {'id': 1, 'title': 'Sanitized note', 'body': 'fixture'}
    if request.url.path.endswith('/items'):
        assert request.url.params.get('q') == 'note'
        return httpx.Response(200, json={'items': [item], 'latest_sequence': 1, 'instance_id': 'fake'})
    return httpx.Response(200, json=item)
server.httpx.AsyncClient = lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(respond))
server.main()
''')
    async def run():
        env = dict(os.environ, PBDESK_STATE_DIR=str(tmp_path / "state"))
        params = StdioServerParameters(command=sys.executable, args=[str(script)], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {"capabilities", "search_notifications", "get_item", "list_devices", "request_action", "action_status"}
                result = await session.call_tool("capabilities", {})
                assert not result.is_error
                assert result.structured_content["features"]["send"] is False
                found = await session.call_tool("search_notifications", {"query": "note"})
                assert not found.is_error
                assert found.structured_content["items"][0]["title"] == "Sanitized note"
                item = await session.call_tool("get_item", {"item_id": 1})
                assert not item.is_error
                assert item.structured_content["id"] == 1
                invalid = await session.call_tool("get_item", {"item_id": -1})
                assert invalid.is_error
                devices = await session.call_tool("list_devices", {})
                assert devices.structured_content["devices"][0]["id"] == "fake-phone"
                action_args = {"client_request_id": "22222222-2222-2222-2222-222222222222", "kind": "sms", "transport": "provider_plaintext", "device_id": "fake-phone", "recipient": "+639171234567", "body": "Sanitized fake message"}
                created = await session.call_tool("request_action", action_args)
                assert not created.is_error
                assert created.structured_content["state"] == "pending_approval"
                assert created.structured_content["requested_by"] == "mcp"
                repeated = await session.call_tool("request_action", action_args)
                assert repeated.structured_content["client_request_id"] == created.structured_content["client_request_id"]
                state = await session.call_tool("action_status", {"action_id": 7})
                assert state.structured_content["state"] == "pending_approval"
                missing_transport = dict(action_args)
                missing_transport.pop("transport")
                assert (await session.call_tool("request_action", missing_transport)).is_error
                schema = next(tool for tool in tools.tools if tool.name == "request_action")
                assert "transport" in schema.input_schema["required"]
                assert "confirmed" not in schema.input_schema["properties"]
                assert "allow_provider_plaintext" not in schema.input_schema["properties"]
                assert "Pushbullet can process" in schema.description
    asyncio.run(run())
