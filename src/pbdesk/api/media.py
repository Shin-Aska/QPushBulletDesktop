"""Desktop-only, retained-reference media loading; never an arbitrary URL proxy."""
import asyncio
import base64
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, HTTPException

MAX_IMAGE_BYTES = 4 * 1024 * 1024
# Exact origins in Pushbullet's public client, documented image examples,
# and retained provider contact metadata (the numbered dl3 host).
# See docs/sms-history-protocol.md and its pinned public-client source.
MEDIA_HOSTS = frozenset({'dl.pushbulletusercontent.com', 'dl3.pushbulletusercontent.com', 'static.pushbullet.com',
    'pushbullet.imgix.net', *(f'lh{n}.googleusercontent.com' for n in range(1, 7))})
RASTER_TYPES = frozenset({'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp'})


def safe_media_url(value):
    """Return whether the value is an allowed HTTPS Pushbullet media URL.

    :param value: Candidate URL value to validate against allowed Pushbullet media origins.
    :type value: object
    :return: True if ``value`` is a well-formed HTTPS URL on an allowed media host without credentials, fragments, or control characters; otherwise False.
    :rtype: bool
    """
    if not isinstance(value, str) or not 0 < len(value) <= 4096:
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and url.hostname in MEDIA_HOSTS and
                url.port in (None, 443) and not url.username and not url.password and
                not url.fragment and not any(ord(c) < 33 or ord(c) == 127 for c in value))
    except ValueError:
        return False


async def fetch_image(url, transport=None):
    """Use a credential-free, environment-independent client for remote bytes.

    :param url: Validated HTTPS media URL to fetch.
    :type url: str
    :param transport: Optional custom HTTPX transport used for testing or dependency injection.
    :type transport: httpx.AsyncBaseTransport | None
    :return: Dictionary containing base64-encoded image ``data`` and detected ``content_type``.
    :rtype: dict[str, str]
    :raises fastapi.HTTPException: If the URL is disallowed (HTTP 422), upstream returns an auth/not-found error (HTTP 410) or unexpected status (HTTP 502), content type/encoding is unsupported or empty (HTTP 415), payload exceeds ``MAX_IMAGE_BYTES`` (HTTP 413), or an HTTP transport error occurs (HTTP 502).
    """
    if not safe_media_url(url):
        raise HTTPException(422, 'This image source cannot be loaded safely')
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(10, connect=3), transport=transport) as client:
            async with client.stream('GET', url, headers={'Accept': ', '.join(sorted(RASTER_TYPES))}) as response:
                if response.status_code in (401, 403, 404, 410):
                    raise HTTPException(410, 'Image unavailable or expired; refresh history and try again')
                if response.status_code != 200:
                    raise HTTPException(502, 'Image could not be loaded; try again later')
                kind = response.headers.get('content-type', '').split(';', 1)[0].lower().strip()
                if kind not in RASTER_TYPES or response.headers.get('content-encoding', 'identity') not in ('', 'identity'):
                    raise HTTPException(415, 'Image format is unavailable')
                length = response.headers.get('content-length')
                if length and (not length.isdecimal() or int(length) > MAX_IMAGE_BYTES):
                    raise HTTPException(413, 'Image exceeds the preview limit')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                        raise HTTPException(413, 'Image exceeds the preview limit')
                    data.extend(chunk)
                if not data:
                    raise HTTPException(415, 'Image is empty')
                return {'data': base64.b64encode(data).decode('ascii'), 'content_type': kind}
    except httpx.HTTPError:
        raise HTTPException(502, 'Image could not be loaded; try again later') from None


def install_media_routes(app, authorization, settings):
    """Install after the core defines its authorization dependency.

    :param app: FastAPI application instance to register routes on.
    :type app: fastapi.FastAPI
    :param authorization: FastAPI dependency verifying the bearer service role.
    :type authorization: collections.abc.Callable[..., collections.abc.Awaitable[str]]
    :param settings: Application configuration settings instance.
    :type settings: pbdesk.config.Settings
    """
    slots = asyncio.Semaphore(2)

    @app.get('/api/v1/media/image')
    async def image(account_id: str, kind: str, item_id: int | None = None,
            message_id: int | None = None, index: int = 0, role: str = Depends(authorization)):
        """Fetch and return a base64-encoded retained attachment or contact image.

        :param account_id: Expected active Pushbullet account identifier.
        :type account_id: str
        :param kind: Retained image source kind (``'item'``, ``'sms'``, or ``'contact'``).
        :type kind: str
        :param item_id: Database item ID when ``kind`` is ``'item'``.
        :type item_id: int | None
        :param message_id: Database SMS message ID when ``kind`` is ``'sms'`` or ``'contact'``.
        :type message_id: int | None
        :param index: Zero-based attachment URL index within the retained record.
        :type index: int
        :param role: Authenticated caller role resolved by the authorization dependency.
        :type role: str
        :return: Dictionary containing base64-encoded ``data``, ``content_type``, and ``account_id``.
        :rtype: dict[str, str]
        :raises fastapi.HTTPException: If the caller is not ``'desktop'`` (HTTP 403), the active account does not match or changes mid-fetch (HTTP 409), parameters do not select a valid retained source (HTTP 422), the requested image or index does not exist (HTTP 404), or fetching times out (HTTP 504).
        """
        if role != 'desktop':
            raise HTTPException(403, 'Only the local desktop can load images')
        store = app.state.store
        if not account_id or account_id != await asyncio.to_thread(store.sms_account):
            raise HTTPException(409, 'Account changed; select the image again')
        if index < 0 or index >= 16:
            raise HTTPException(404, 'Image not found')
        if kind == 'item' and item_id is not None:
            row = await asyncio.to_thread(store.item, item_id)
            urls = [row.get('file_url')] if row and str(row.get('file_type') or '').lower() in RASTER_TYPES else []
        elif kind in ('sms', 'contact') and message_id is not None:
            row = await asyncio.to_thread(store.sms_message, message_id)
            if kind == 'sms':
                urls = row.get('image_urls', []) if row else []
            else:
                recipients = row.get('recipients', []) if row else []
                urls = [recipients[0].get('image_url')] if len(recipients) == 1 else []
        else:
            raise HTTPException(422, 'Choose a retained image attachment')
        if not row or index >= len(urls):
            raise HTTPException(404, 'Image not found')
        # A queued request must not cross accounts while waiting for a fetch slot.
        async with slots:
            if account_id != await asyncio.to_thread(store.sms_account):
                raise HTTPException(409, 'Account changed; select the image again')
            try:
                result = await asyncio.wait_for(fetch_image(urls[index]), timeout=12)
            except TimeoutError:
                raise HTTPException(504, 'Image load timed out; try again later') from None
            if account_id != await asyncio.to_thread(store.sms_account):
                raise HTTPException(409, 'Account changed; select the image again')
        return {**result, 'account_id': account_id}
