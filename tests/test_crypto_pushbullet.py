"""Official cross-language vectors plus encrypted recovery without real secrets."""
import asyncio
import base64
import json
from pathlib import Path
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pbdesk.config import Settings
from pbdesk.storage.database import Store
from pbdesk.pushbullet.crypto import derive_key,decrypt_bytes,decrypt_object,DecryptionError,EncryptionService
from pbdesk.pushbullet.normalize import normalize

KEY=base64.b64decode("1sW28zp7CWv5TtGjlQpDHHG4Cbr9v36fG5o4f74LsKg=")
CIPHERTEXT="MSfJxxY5YdjttlfUkCaKA57qU9SuCN8+ZhYg/xieI+lDnQ=="


def encrypt_object(value,key=KEY):
    iv=b"fixture-only"
    result=AESGCM(key).encrypt(iv,json.dumps(value).encode(),None)
    return base64.b64encode(b"1"+result[-16:]+iv+result[:-16]).decode()


def test_official_pbkdf2_and_aesgcm_vectors():
    assert derive_key("hunter2","up0snaKOsn")==KEY
    assert decrypt_bytes(KEY,CIPHERTEXT)==b"meow!"
    with pytest.raises(DecryptionError,match="JSON"):
        decrypt_object(KEY,CIPHERTEXT)
    assert derive_key(" hunter2 ","up0snaKOsn")!=KEY
    assert derive_key("é ","salt")!=derive_key("é","salt")


def test_authentication_before_parsing_and_strict_frames():
    frame=bytearray(base64.b64decode(CIPHERTEXT));frame[1]^=1
    for value in (base64.b64encode(frame).decode(),"not base64",base64.b64encode(b"2"+bytes(frame[1:])).decode(),"",None):
        with pytest.raises(DecryptionError): decrypt_object(KEY,value)
    with pytest.raises(DecryptionError): decrypt_bytes(b"x"*32,CIPHERTEXT)


def test_locked_payload_survives_restart_wrong_key_and_replay(tmp_path):
    async def run():
        settings=Settings(tmp_path)
        store=Store(settings)
        async def consumer(value):
            item=normalize(value)
            return store.ingest(item) if item else None
        encrypted={"type":"push","push":{"encrypted":True,"ciphertext":encrypt_object({"type":"mirror","source_device_iden":"fake-phone","package_name":"org.fake","notification_id":42,"title":"fake","body":"readable"})}}
        service=EncryptionService(store,consumer)
        await service.ingest(encrypted);await service.ingest(encrypted)
        assert store.encrypted_count()==1 and not store.list_items()
        store.close()
        store=Store(settings)
        service=EncryptionService(store,consumer)
        async def wrong(): return b"x"*32
        await service.reload(wrong)
        assert service.state=="locked" and store.encrypted_count()==1
        async def correct(): return KEY
        await service.reload(correct)
        assert service.state=="ready" and store.encrypted_count()==0
        assert len(store.list_items())==1
        await service.ingest(encrypted)
        assert len(store.list_items())==1 and store.event_bounds()==(1,1)
        assert store.encrypted_count()==0
        store.close()
    asyncio.run(run())


def test_authenticated_unsupported_or_invalid_json_is_retained(tmp_path):
    async def run():
        store=Store(Settings(tmp_path))
        async def consumer(value): pytest.fail("Unsupported content must not enter inbox")
        service=EncryptionService(store,consumer)
        async def loader(): return KEY
        await service.reload(loader)
        await service.ingest({"type":"push","push":{"encrypted":True,"ciphertext":encrypt_object({"type":[]})}})
        await service.ingest({"type":"push","push":{"encrypted":True,"ciphertext":CIPHERTEXT}})
        assert store.encrypted_count()==2
        statuses={row["status"] for row in store.encrypted_envelopes()}
        assert statuses=={"unsupported_or_malformed_content","authentication_or_format_error"}
        store.close()
    asyncio.run(run())


def test_shared_default_cannot_bypass_explicit_device_key_mapping(tmp_path):
    async def run():
        store=Store(Settings(tmp_path))
        async def consumer(value):
            pytest.fail('A shared key must not override a different device key selection')
        service=EncryptionService(store,consumer)
        envelope={'type':'push','push':{'encrypted':True,'ciphertext':encrypt_object({'type':'mirror','source_device_iden':'phone','package_name':'fake','notification_id':1,'body':'fixture'})}}
        await service.ingest(envelope)
        async def shared(): return KEY
        await service.reload(shared,{'phone':b'x'*32},{'phone':'key_ready'})
        assert store.encrypted_count()==1 and service.state=='locked'
        store.close()
    asyncio.run(run())


def test_authenticated_dismissal_is_retained_control_not_pending_or_fake_inbox(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));store.set_setting('pushbullet_account_id','fixture-account')
        async def consumer(value):pytest.fail('Dismissal controls must never create inbox messages')
        service=EncryptionService(store,consumer)
        control={'type':'push','push':{'encrypted':True,'ciphertext':encrypt_object({'type':'dismissal','source_device_iden':'phone','source_user_iden':'fixture-account','package_name':'fake.package','notification_id':5})}}
        await service.ingest(control)
        assert await service.pending_count()==1
        async def shared():return KEY
        states={'phone':'key_ready'}
        await service.reload(shared,{'phone':KEY},states)
        assert service.state=='ready' and states['phone']=='verified'
        assert await service.pending_count()==0 and not store.encrypted_envelopes() and not store.list_items()
        with store.engine.connect() as conn:
            row=conn.exec_driver_sql('SELECT payload,status FROM locked_envelopes').one()
            assert json.loads(row[0])==control and row[1]=='ignored_control'
        # Reload excludes the retained handled control from retry work.
        await service.reload(shared,{'phone':KEY},states)
        assert await service.pending_count()==0
        store.close()
    asyncio.run(run())


def test_invalid_or_wrong_association_dismissal_remains_recoverable_error(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));store.set_setting('pushbullet_account_id','fixture-account')
        async def consumer(value):pytest.fail('Invalid controls must not enter inbox')
        service=EncryptionService(store,consumer)
        async def loader():return KEY
        await service.reload(loader,{'phone':KEY},{})
        invalid=[{'type':'dismissal','source_device_iden':'phone','package_name':'fake','notification_id':True},
                 {'type':'dismissal','source_device_iden':'phone','source_user_iden':'other-account','package_name':'fake','notification_id':1},
                 {'type':'dismissal','source_device_iden':'other-phone','package_name':'fake','notification_id':1}]
        for value in invalid:
            await service.ingest({'type':'push','push':{'source_device_iden':'phone','encrypted':True,'ciphertext':encrypt_object(value)}})
        assert await service.pending_count()==3
        assert all(row['status']!='ignored_control' for row in store.encrypted_envelopes())
        assert not store.list_items()
        store.close()
    asyncio.run(run())


def test_handled_dismissal_does_not_mask_other_authentication_failures(tmp_path):
    async def run():
        store=Store(Settings(tmp_path))
        async def consumer(value):pytest.fail('Neither control nor invalid cipher is Inbox content')
        service=EncryptionService(store,consumer)
        async def loader():return KEY
        await service.reload(loader)
        await service.ingest({'type':'push','push':{'encrypted':True,'ciphertext':'bad-frame'}})
        control={'type':'dismissal','source_device_iden':'phone','package_name':'fake','notification_id':1}
        await service.ingest({'type':'push','push':{'encrypted':True,'ciphertext':encrypt_object(control)}})
        assert await service.pending_count()==1 and service.state=='locked'
        with store.engine.connect() as conn:
            assert conn.exec_driver_sql('SELECT COUNT(*) FROM locked_envelopes').scalar()==2
        store.close()
    asyncio.run(run())
