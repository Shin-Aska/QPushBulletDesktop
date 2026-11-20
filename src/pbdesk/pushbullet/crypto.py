"""Official Pushbullet E2E framing, using maintained authenticated primitives."""
import asyncio
import base64
import binascii
import json
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from pbdesk.pushbullet.normalize import normalize


class DecryptionError(ValueError):
    """Error raised when encrypted Pushbullet payloads fail validation or authentication."""
    pass


def derive_key(password,user_id):
    """Derive a 32-byte encryption key from a password and user identity.

    :param password: User's end-to-end encryption password.
    :type password: str
    :param user_id: Pushbullet user account identifier used as the PBKDF2 salt.
    :type user_id: str
    :return: Derived 32-byte AES-GCM encryption key.
    :rtype: bytes
    :raises ValueError: If ``password`` or ``user_id`` is not a string, or ``user_id`` is empty.
    """
    if not isinstance(password,str) or not isinstance(user_id,str) or not user_id:
        raise ValueError("Encryption password and user identity are required")
    return PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=user_id.encode("utf-8"),iterations=30000).derive(password.encode("utf-8"))


def decrypt_bytes(key,ciphertext):
    """Decode and authenticate a versioned Pushbullet AES-GCM ciphertext string.

    :param key: 32-byte AES-GCM decryption key derived via :func:`derive_key`.
    :type key: bytes
    :param ciphertext: Base64-encoded versioned Pushbullet AES-GCM frame string.
    :type ciphertext: str
    :return: Authenticated plaintext bytes.
    :rtype: bytes
    :raises DecryptionError: If ``ciphertext`` is invalid or oversized, base64 decoding fails, the frame header is unsupported, or AES-GCM authentication fails.
    """
    if not isinstance(ciphertext,str) or len(ciphertext)>1400000:
        raise DecryptionError("Invalid encrypted payload")
    try:
        frame=base64.b64decode(ciphertext,validate=True)
    except (ValueError,binascii.Error):
        raise DecryptionError("Invalid encrypted payload") from None
    if len(frame)<29 or frame[:1]!=b"1":
        raise DecryptionError("Unsupported encrypted frame")
    try:
        # Official wire order: version, 16-byte tag, 12-byte IV, ciphertext.
        return AESGCM(key).decrypt(frame[17:29],frame[29:]+frame[1:17],None)
    except (InvalidTag,ValueError,TypeError):
        raise DecryptionError("Encrypted payload authentication failed") from None


def decrypt_object(key,ciphertext):
    """Decrypt and parse an authenticated JSON object payload.

    :param key: 32-byte AES-GCM decryption key derived via :func:`derive_key`.
    :type key: bytes
    :param ciphertext: Base64-encoded versioned Pushbullet AES-GCM frame string.
    :type ciphertext: str
    :return: Parsed JSON object dictionary decoded from authenticated plaintext.
    :rtype: dict[str, Any]
    :raises DecryptionError: If decryption fails or the authenticated plaintext does not decode to a JSON object.
    """
    plaintext=decrypt_bytes(key,ciphertext)  # Authentication happens before parsing.
    try:
        value=json.loads(plaintext)
    except (ValueError,UnicodeError,RecursionError):
        raise DecryptionError("Authenticated payload is not a supported JSON object") from None
    if not isinstance(value,dict):
        raise DecryptionError("Authenticated payload is not a supported JSON object")
    return value


def encrypted_push(payload):
    """Return the encrypted push dictionary from a payload, or None.

    :param payload: Raw stream message or push payload to inspect.
    :type payload: object
    :return: Encrypted push dictionary when ``payload`` holds an encrypted push, otherwise ``None``.
    :rtype: dict[str, Any] | None
    """
    if not isinstance(payload,dict):
        return None
    push=payload.get("push") if payload.get("type")=="push" else payload
    return push if isinstance(push,dict) and push.get("encrypted") is True else None


class EncryptionService:
    """Stateful manager for Pushbullet end-to-end encryption keys and pending envelopes."""

    def __init__(self,store,consumer):
        """Initialize the encryption service with a backing store and message consumer.

        :param store: Persistence store used to retain, query, and delete encrypted envelopes.
        :type store: pbdesk.storage.database.Store
        :param consumer: Asynchronous callable invoked with plaintext or decrypted push payloads.
        :type consumer: collections.abc.Callable[[dict[str, Any]], collections.abc.Awaitable[dict[str, Any] | None]]
        """
        self.store=store
        self.consumer=consumer
        self.key=None
        self.device_keys={}
        self.key_states={}
        self.state="unconfigured"
        self.lock=asyncio.Lock()

    async def ingest(self,payload):
        """Serialize and ingest a push payload through decryption if encrypted.

        :param payload: Incoming stream or stored push payload dictionary.
        :type payload: dict[str, Any]
        :return: Ingestion result returned by the consumer, or ``None`` if ignored, deferred, or invalid.
        :rtype: dict[str, Any] | None
        """
        async with self.lock:
            return await self._ingest(payload)

    async def _ingest(self,payload):
        """Decrypt, validate, and dispatch an encrypted push envelope or pass through plaintext.

        :param payload: Incoming push payload dictionary to decrypt or forward.
        :type payload: dict[str, Any]
        :return: Ingestion result returned by the consumer, or ``None`` if retained, ignored, or invalid.
        :rtype: dict[str, Any] | None
        """
        envelope=encrypted_push(payload)
        if envelope is None:
            return await self.consumer(payload)
        # Persist before any attempt so bad/missing keys survive restart.
        account=await asyncio.to_thread(self.store.account_key)
        envelope_id=await asyncio.to_thread(self.store.retain_envelope,payload,account)
        if envelope_id is None:
            self.state="error"
            return None
        candidates=[]
        visible=envelope.get('source_device_iden')
        if isinstance(visible,str) and visible in self.device_keys:
            candidates=[(visible,self.device_keys[visible])]
        else:
            candidates=list(self.device_keys.items())[:64]
            if self.key is not None:
                candidates.append((None,self.key))
        if not candidates:
            if self.state!="unconfigured":
                self.state="locked"
            return None
        decoded=None
        associated=None
        for device,key in candidates:
            try:
                value=decrypt_object(key,envelope.get("ciphertext"))
                source=value.get('source_device_iden')
                if visible is not None and source is not None and source!=visible:
                    continue
                if device is not None and source!=device:
                    continue
                if device is None and source in self.device_keys and self.device_keys[source]!=key:
                    continue
                decoded=value
                associated=device or source
                break
            except DecryptionError:
                continue
        if decoded is None:
            self.state="locked"
            await asyncio.to_thread(self.store.envelope_status,envelope_id,"authentication_or_format_error")
            return None
        if decoded.get('type')=='dismissal':
            # Authenticated dismissal is a notification control, not an Inbox
            # message or failed key. Preserve its original recovery ciphertext.
            notification=decoded.get('notification_id')
            valid=(isinstance(decoded.get('source_device_iden'),str) and bool(decoded['source_device_iden'])
                and isinstance(decoded.get('package_name'),str) and bool(decoded['package_name'])
                and not isinstance(notification,bool) and isinstance(notification,(str,int)) and notification!=''
                and decoded.get('source_user_iden',account)==account)
            if valid:
                await asyncio.to_thread(self.store.envelope_status,envelope_id,'ignored_control')
                await self._settle_state()
                if associated is not None:
                    self.key_states[associated]='verified'
                return None
        decoded_payload={"type":"push","push":decoded}
        if normalize(decoded_payload) is None and decoded.get("type")!="sms_changed":
            await asyncio.to_thread(self.store.envelope_status,envelope_id,"unsupported_or_malformed_content")
            self.state="error"
            return None
        result=await self.consumer(decoded_payload)
        if isinstance(result,dict) and result.get("item_id") is not None:
            await asyncio.to_thread(self.store.mark_decrypted,result["item_id"])
        # Supported decoded messages deduplicate by their stable source identity.
        await asyncio.to_thread(self.store.delete_envelope,envelope_id)
        await self._settle_state()
        if associated is not None:
            self.key_states[associated]="verified"
        return result

    async def _settle_state(self):
        """Recompute service state from remaining pending encrypted envelopes."""
        pending=await asyncio.to_thread(self.store.encrypted_envelopes,0,100)
        self.state=('error' if any(row['status']=='unsupported_or_malformed_content' for row in pending) else 'locked') if pending else 'ready'

    async def clear(self,state="unconfigured"):
        """Clear loaded encryption keys and set the service state.

        :param state: Service state assigned after clearing keys.
        :type state: str
        """
        async with self.lock:
            self.key=None
            self.device_keys={}
            self.key_states={}
            self.state=state

    async def reload(self,key_loader,device_keys=None,key_states=None):
        """Reload account and device keys and retry pending encrypted envelopes.

        :param key_loader: Asynchronous callable returning the account-wide 32-byte key or ``None``.
        :type key_loader: collections.abc.Callable[[], collections.abc.Awaitable[bytes | None]]
        :param device_keys: Optional mapping of device IDs to 32-byte derived encryption keys.
        :type device_keys: collections.abc.Mapping[str, bytes] | None
        :param key_states: Optional mutable mapping of per-device key verification states.
        :type key_states: dict[str, str] | None
        """
        async with self.lock:
            self.key=None
            self.device_keys=dict(device_keys or {})
            self.key_states=key_states if key_states is not None else {}
            try:
                self.key=await key_loader()
                self.state="key_ready" if self.key is not None or self.device_keys else "unconfigured"
            except Exception:
                self.state="key_ready" if self.device_keys else "locked"
        if self.key is not None or self.device_keys:
            await self.retry_pending()

    async def set_device_keys(self,keys,states):
        """Replace per-device encryption keys and retry pending envelopes.

        :param keys: Mapping of device IDs to 32-byte derived encryption keys.
        :type keys: collections.abc.Mapping[str, bytes]
        :param states: Mutable mapping of per-device key verification states.
        :type states: dict[str, str]
        """
        async with self.lock:
            self.device_keys=dict(keys)
            self.key_states=states
        await self.retry_pending()

    async def retry_pending(self):
        """Reprocess all stored encrypted envelopes in batches."""
        cursor=0
        while True:
            rows=await asyncio.to_thread(self.store.encrypted_envelopes,cursor,100)
            if not rows:
                return
            for row in rows:
                cursor=row["id"]
                async with self.lock:
                    await self._ingest(json.loads(row["payload"]))
            await asyncio.sleep(0)

    async def pending_count(self):
        """Return the count of retained encrypted envelopes awaiting decryption.

        :return: Number of retained non-ignored encrypted envelopes awaiting decryption.
        :rtype: int
        """
        return await asyncio.to_thread(self.store.encrypted_count)
