"""Bound ASGI writes so stalled event consumers cannot occupy a stream forever."""
import asyncio
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

class BoundedStreamingResponse(StreamingResponse):
    send_timeout = 5.0

    async def __call__(self, scope, receive, send):
        async def bounded_send(message):
            try:
                await asyncio.wait_for(send(message), self.send_timeout)
            except TimeoutError:
                raise OSError("Event consumer stopped accepting data") from None
        try:
            await super().__call__(scope, receive, bounded_send)
        except ClientDisconnect:
            pass
