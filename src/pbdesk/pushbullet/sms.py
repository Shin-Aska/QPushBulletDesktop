"""Official client's permanent-object SMS history protocol; no sending endpoints."""
import asyncio
import hashlib
import json
import math
import time
import httpx
from urllib.parse import quote
from pbdesk.pushbullet.crypto import decrypt_object, DecryptionError
from pbdesk.pushbullet.normalize import utc_timestamp, media_recipients, media_urls

NORMALIZER_VERSION = 2


def thread_fingerprint(thread):
    """Return a versioned SHA-256 digest representing an SMS thread summary.

    :param thread: Raw Pushbullet SMS thread summary dictionary from the conversation index.
    :type thread: dict[str, Any]
    :return: Hexadecimal SHA-256 digest incorporating ``NORMALIZER_VERSION`` and ``thread``.
    :rtype: str
    """
    return hashlib.sha256(json.dumps([NORMALIZER_VERSION, thread], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SmsHistory:
    """Synchronizer and decoder for Pushbullet permanent-object SMS history."""

    def __init__(self,provider,pace=.35,interval=300):
        """Initialize SMS history sync state, pacing, and coordination primitives.

        :param provider: Parent Pushbullet provider used for authenticated reads, key lookup, and storage.
        :type provider: pbdesk.pushbullet.provider.Provider
        :param pace: Minimum interval in seconds between permanent-object HTTP reads.
        :type pace: float
        :param interval: Periodic background synchronization interval in seconds.
        :type interval: float
        """
        self.provider=provider
        self.store=provider.store
        self.pace=pace
        self.interval=interval
        self.pending=set()
        self.wake=asyncio.Event()
        self.lock=asyncio.Lock()
        self._last_read=0

    async def request(self,device_id=None):
        """Queue SMS history synchronization for a specific device or all SMS devices.

        :param device_id: Optional SMS-capable device ID to queue; queues all active SMS devices when ``None``.
        :type device_id: str | None
        :raises ValueError: If ``device_id`` is provided but is inactive or does not support SMS.
        """
        devices=await asyncio.to_thread(self.store.device_list)
        ids={row['id'] for row in devices if row['active'] and row['can_sms']}
        if device_id is not None:
            if device_id not in ids:
                raise ValueError('Selected device is inactive or does not support SMS')
            ids={device_id}
        self.pending.update(ids)
        account=await asyncio.to_thread(self.store.sms_account)
        if account:
            for device in ids:
                await asyncio.to_thread(self.store.sms_status,account,device,'queued')
        self.wake.set()

    async def _read_permanent(self,account,device,key):
        """Fetch a permanent SMS object with request pacing and optional decryption.

        :param account: Active Pushbullet account identifier.
        :type account: str
        :param device: Target Pushbullet device identifier.
        :type device: str
        :param key: Permanent object key such as ``<device>_threads`` or ``<device>_thread_<id>``.
        :type key: str
        :return: Tuple of ``(payload, decrypted)`` where ``payload`` is the decoded dictionary and ``decrypted`` indicates whether E2E decryption was applied.
        :rtype: tuple[dict[str, Any], bool]
        """
        wait=self.pace-(time.monotonic()-self._last_read)
        if wait>0:
            await asyncio.sleep(wait)
        self._last_read=time.monotonic()
        payload=await self.provider._read('/permanents/'+quote(key,safe=''))
        if payload.get('encrypted') is True:
            await asyncio.to_thread(self.store.retain_sms_envelope,account,device,key,payload)
            payload=self.decode(account,device,payload)
            decrypted=True
        else:
            decrypted=False
        return payload,decrypted

    def decode(self,account,device,payload):
        """Decrypt an encrypted permanent SMS object and verify device and account bindings.

        :param account: Expected Pushbullet account identifier bound to the permanent object.
        :type account: str
        :param device: Expected Pushbullet device identifier bound to the permanent object.
        :type device: str
        :param payload: Encrypted permanent-object envelope dictionary containing ``ciphertext``.
        :type payload: dict[str, Any]
        :return: Authenticated and decrypted JSON dictionary.
        :rtype: dict[str, Any]
        :raises DecryptionError: If the device key is unavailable, ciphertext decryption fails, or decoded account/device fields mismatch.
        """
        key=self.provider.device_keys.get(device)
        if key is None:
            self.provider.key_states[device]='locked'
            raise DecryptionError('Local encryption key unavailable')
        try:
            value=decrypt_object(key,payload.get('ciphertext'))
        except DecryptionError:
            self.provider.key_states[device]='locked'
            raise
        # Permanent key binds account/device. Explicit decoded routing must agree.
        for field,expected in (('source_device_iden',device),('device_iden',device),('user_iden',account)):
            if value.get(field) is not None and value[field]!=expected:
                self.provider.key_states[device]='locked'
                raise DecryptionError('Encrypted history association mismatch')
        self.provider.key_states[device]='verified'
        return value

    @staticmethod
    def normalize_thread(thread,rows,decrypted):
        """Validate and normalize raw SMS thread recipients and messages.

        :param thread: Thread metadata dictionary containing the ``recipients`` list.
        :type thread: dict[str, Any]
        :param rows: Raw SMS message dictionaries belonging to the conversation.
        :type rows: list[dict[str, Any]]
        :param decrypted: Whether the thread payload was end-to-end encrypted and decrypted locally.
        :type decrypted: bool
        :return: List of normalized SMS message dictionaries ready for storage.
        :rtype: list[dict[str, Any]]
        :raises ValueError: If recipients, message records, identities, directions, timestamps, or bodies are malformed or out of range.
        """
        recipients=thread.get('recipients') or []
        if not isinstance(recipients,list):
            raise ValueError('Malformed SMS recipients')
        clean_recipients=media_recipients(recipients)
        addresses=[r.get('address') or r.get('number') for r in clean_recipients]
        addresses=[a for a in addresses if isinstance(a,str) and a]
        names=[r.get('name') for r in clean_recipients if isinstance(r.get('name'),str)]
        result=[]
        for row in rows:
            if not isinstance(row,dict):
                raise ValueError('Malformed SMS message')
            direction=row.get('direction')
            identity=row.get('id',row.get('guid'))
            timestamp=row.get('timestamp')
            body=row.get('body')
            if direction not in ('incoming','outgoing') or isinstance(identity,bool) or not isinstance(identity,(str,int)) or str(identity)=='':
                raise ValueError('SMS identity or direction missing')
            if isinstance(timestamp,bool) or not isinstance(timestamp,(float,int)) or not math.isfinite(timestamp) or timestamp<0:
                raise ValueError('SMS timestamp missing')
            # Android timestamps may be milliseconds; API consistently exposes seconds.
            if timestamp>100000000000:
                timestamp/=1000
            timestamp=utc_timestamp(timestamp)
            if timestamp is None:
                raise ValueError('SMS timestamp is outside the supported date range')
            if not isinstance(body,str):
                if body is None and isinstance(row.get('image_urls'),list):
                    body=''
                else:
                    raise ValueError('SMS body malformed')
            result.append(dict(message_id=str(identity),direction=direction,body=body,timestamp=float(timestamp),
                addresses=addresses,name=', '.join(names) or None,decrypted=int(decrypted),
                recipients=clean_recipients,image_urls=media_urls(row.get('image_urls'))))
        return result

    async def sync_device(self,device):
        """Synchronize SMS threads and messages for a single Pushbullet device.

        :param device: Pushbullet device identifier to synchronize SMS history for.
        :type device: str
        :return: ``True`` if synchronization completed (including partial 404 results), or ``False`` on handled failure.
        :rtype: bool
        :raises pbdesk.pushbullet.provider.RateLimited: When Pushbullet rate limits permanent-object reads.
        :raises pbdesk.pushbullet.provider.ProviderUnauthorized: When Pushbullet rejects the access token.
        """
        async with self.lock:
            account=await asyncio.to_thread(self.store.sms_account)
            try:
                account=await self.provider.ensure_account()
                await asyncio.to_thread(self.store.sms_status,account,device,'refreshing')
                index,_=await self._read_permanent(account,device,device+'_threads')
                threads=index.get('threads')
                if not isinstance(threads,list):
                    raise ValueError('Malformed SMS conversation index')
                unavailable=[]
                previous_unavailable=await asyncio.to_thread(self.store.sms_unavailable,account,device)
                previous_missing=set(previous_unavailable.get('conversation_ids',[]))
                for thread in threads:
                    if not isinstance(thread,dict) or isinstance(thread.get('id'),bool) or not isinstance(thread.get('id'),(str,int)):
                        raise ValueError('Malformed SMS conversation identity')
                    conversation=str(thread['id'])
                    # A missing latest identity must cause a re-read, never a stale skip.
                    latest=thread.get('latest')
                    has_latest=isinstance(latest,dict) and ('id' in latest or 'guid' in latest) and 'timestamp' in latest
                    fingerprint=thread_fingerprint(thread)
                    previous=await asyncio.to_thread(self.store.sms_thread_fingerprint,account,device,conversation)
                    if has_latest and previous==fingerprint and conversation not in previous_missing:
                        continue
                    try:
                        body,decrypted=await self._read_permanent(account,device,device+'_thread_'+conversation)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code!=404:
                            raise
                        # A stale index can name a missing permanent. Continue all
                        # other conversations and revisit this exact key next pass.
                        unavailable.append(conversation)
                        previous_missing.add(conversation)
                        await asyncio.to_thread(self.store.record_sms_unavailable,account,device,previous_missing)
                        continue
                    rows=body.get('thread')
                    if not isinstance(rows,list):
                        raise ValueError('Malformed SMS conversation history')
                    messages=self.normalize_thread(thread,rows,decrypted)
                    await asyncio.to_thread(self.store.merge_sms_thread,account,device,conversation,messages,fingerprint)
                    await asyncio.to_thread(self.store.delete_sms_envelope,account,device,device+'_thread_'+conversation)
                await asyncio.to_thread(self.store.delete_sms_envelope,account,device,device+'_threads')
                await asyncio.to_thread(self.store.record_sms_unavailable,account,device,unavailable)
                detail=f'{len(unavailable)} listed conversations are currently unavailable; cached history kept' if unavailable else None
                await asyncio.to_thread(self.store.sms_status,account,device,'partial' if unavailable else 'idle',detail,success=True)
            except asyncio.CancelledError:
                if account:
                    await asyncio.to_thread(self.store.sms_status,account,device,'idle')
                raise
            except Exception as exc:
                from pbdesk.pushbullet.provider import RateLimited,ProviderUnauthorized
                state='locked' if isinstance(exc,DecryptionError) else 'rate_limited' if isinstance(exc,RateLimited) else 'error'
                error='Local encryption key could not decrypt history' if state=='locked' else 'SMS synchronization could not finish; cached messages retained'
                if account:
                    await asyncio.to_thread(self.store.sms_status,account,device,state,error)
                if isinstance(exc,(RateLimited,ProviderUnauthorized)):
                    raise
                return False
            return True

    async def replay_cached(self,device):
        """Recover locally retained history without requiring connectivity.

        :param device: Pushbullet device identifier whose retained SMS envelopes should be replayed.
        :type device: str
        """
        account=await asyncio.to_thread(self.store.sms_account)
        rows=await asyncio.to_thread(self.store.sms_envelopes,account,device)
        payloads={row['permanent_key']:json.loads(row['payload']) for row in rows}
        index=payloads.get(device+'_threads')
        if index is None:
            return
        try:
            index=self.decode(account,device,index)
            threads=index.get('threads')
            if not isinstance(threads,list):
                return
            for thread in threads:
                if not isinstance(thread,dict) or not isinstance(thread.get('id'),(str,int)):
                    continue
                conversation=str(thread['id'])
                key=device+'_thread_'+conversation
                if key not in payloads:
                    continue
                decoded=self.decode(account,device,payloads[key])
                if not isinstance(decoded.get('thread'),list):
                    continue
                messages=self.normalize_thread(thread,decoded['thread'],True)
                fingerprint=thread_fingerprint(thread)
                await asyncio.to_thread(self.store.merge_sms_thread,account,device,conversation,messages,fingerprint)
                await asyncio.to_thread(self.store.delete_sms_envelope,account,device,key)
        except (DecryptionError,ValueError,TypeError):
            return  # Preserve recoverable ciphertext and any previously readable rows.

    async def run(self):
        """Run the periodic and on-demand SMS history synchronization loop."""
        while True:
            device=None
            try:
                if not self.pending:
                    try:
                        await asyncio.wait_for(self.wake.wait(),self.interval)
                    except TimeoutError:
                        await self.request()
                self.wake.clear()
                while self.pending:
                    device=sorted(self.pending)[0]
                    self.pending.remove(device)
                    await self.sync_device(device)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                from pbdesk.pushbullet.provider import RateLimited
                if isinstance(exc,RateLimited) and device is not None:
                    self.pending.add(device)
                await asyncio.sleep(exc.delay if isinstance(exc,RateLimited) else 30)
