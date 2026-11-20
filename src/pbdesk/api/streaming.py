"""Bound ASGI writes so stalled event consumers cannot occupy a stream forever."""
import asyncio
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

class BoundedStreamingResponse(StreamingResponse):
    """StreamingResponse subclass that enforces a write timeout per ASGI message."""

    send_timeout = 5.0

    async def __call__(self, scope, receive, send):
        """Stream response chunks while handling timeouts and client disconnects.

        :param scope: ASGI connection scope mapping.
        :type scope: starlette.types.Scope
        :param receive: ASGI receive callable yielding incoming events.
        :type receive: starlette.types.Receive
        :param send: ASGI send callable accepting outgoing response messages.
        :type send: starlette.types.Send
        """
        async def bounded_send(message):
            """Send a single ASGI message within the configured timeout.

            :param message: Outgoing ASGI event mapping to transmit.
            :type message: collections.abc.MutableMapping[str, object]
            :raises OSError: If sending the ASGI message exceeds ``self.send_timeout``.
            """
            try:
                await asyncio.wait_for(send(message), self.send_timeout)
            except TimeoutError:
                raise OSError("Event consumer stopped accepting data") from None
        try:
            await super().__call__(scope, receive, bounded_send)
        except ClientDisconnect:
            pass
