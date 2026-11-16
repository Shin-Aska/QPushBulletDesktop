"""Existing API tests start with preprovisioned encrypted history, not setup UI."""
import time
from fastapi.testclient import TestClient


def wait_for_encrypted_test_client(monkeypatch):
    enter = TestClient.__enter__
    def ready(client):
        result = enter(client)
        state = getattr(client.app, 'state', None)
        if state is not None and hasattr(state, 'history_ready'):
            deadline = time.monotonic() + 10
            while not state.history_ready:
                if time.monotonic() >= deadline:
                    raise AssertionError('Preprovisioned encrypted test history did not become ready')
                time.sleep(.01)
        return result
    monkeypatch.setattr(TestClient, '__enter__', ready)


async def wait_for_encrypted_history(app):
    import asyncio
    for _ in range(1000):
        if app.state.history_ready:
            return
        await asyncio.sleep(.01)
    raise AssertionError('Preprovisioned encrypted test history did not become ready')
