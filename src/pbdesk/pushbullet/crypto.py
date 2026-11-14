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
    pass


def derive_key(password,user_id):
    if not isinstance(password,str) or not isinstance(user_id,str) or not user_id:
        raise ValueError("Encryption password and user identity are required")
    return PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=user_id.encode("utf-8"),iterations=30000).derive(password.encode("utf-8"))


def decrypt_bytes(key,ciphertext):
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
    plaintext=decrypt_bytes(key,ciphertext)  # Authentication happens before parsing.
    try:
        value=json.loads(plaintext)
    except (ValueError,UnicodeError,RecursionError):
        raise DecryptionError("Authenticated payload is not a supported JSON object") from None
    if not isinstance(value,dict):
        raise DecryptionError("Authenticated payload is not a supported JSON object")
    return value


def encrypted_push(payload):
    if not isinstance(payload,dict):
        return None
    push=payload.get("push") if payload.get("type")=="push" else payload
    return push if isinstance(push,dict) and push.get("encrypted") is True else None


class EncryptionService:
    def __init__(self,store,consumer):
        self.store=store
        self.consumer=consumer
        self.key=None
        self.device_keys={}
        self.key_states={}
        self.state="unconfigured"
        self.lock=asyncio.Lock()

    async def ingest(self,payload):
        async with self.lock:
            return await self._ingest(payload)

    async def _ingest(self,payload):
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
        pending=await asyncio.to_thread(self.store.encrypted_envelopes,0,100)
        self.state=('error' if any(row['status']=='unsupported_or_malformed_content' for row in pending) else 'locked') if pending else 'ready'

    async def clear(self,state="unconfigured"):
        async with self.lock:
            self.key=None
            self.device_keys={}
            self.key_states={}
            self.state=state

    async def reload(self,key_loader,device_keys=None,key_states=None):
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
        async with self.lock:
            self.device_keys=dict(keys)
            self.key_states=states
        await self.retry_pending()

    async def retry_pending(self):
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
        return await asyncio.to_thread(self.store.encrypted_count)
