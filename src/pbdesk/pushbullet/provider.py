"""One documented Pushbullet connection; outbound calls only via approved outbox."""
import asyncio
import json
import random
import math
import time
from email.utils import parsedate_to_datetime
from pbdesk.pushbullet.normalize import utc_timestamp
from pbdesk.pushbullet.crypto import derive_key
from pbdesk.credentials import e2e_password, device_e2e_password
import httpx
import websockets

class RateLimited(Exception):
    def __init__(self, delay):
        self.delay = delay

class ProviderUnauthorized(Exception):
    pass

class SubmissionError(RuntimeError):
    def __init__(self,state,code,message):
        super().__init__(message)
        self.state,self.code,self.message=state,code,message


class Provider:
    def __init__(self, token, store, ingest, settings=None, encryption=None):
        self._token = token
        self.store = store
        self.ingest = ingest
        self.state = "connecting"
        self.settings=settings
        self.encryption=encryption
        self.devices_lock=asyncio.Lock()
        self.registration_lock=asyncio.Lock()
        self.device_keys={}
        self.key_states={}
        self.account_id=None
        from pbdesk.pushbullet.sms import SmsHistory
        self.sms=SmsHistory(self)
        self.client = httpx.AsyncClient(base_url="https://api.pushbullet.com/v2", headers={"Access-Token": token}, timeout=20, trust_env=False, follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    @property
    def send_ready(self):
        return self.state == "connected"

    async def _read(self,path,params=None):
        response=await self.client.get(path,params=params)
        if response.status_code==401:
            raise ProviderUnauthorized()
        if response.status_code==429:
            try:
                delay=float(response.headers.get('Retry-After','60'))
            except (ValueError,OverflowError):
                delay=60
            if not math.isfinite(delay):
                delay=60
            raise RateLimited(min(max(delay,1),3600))
        response.raise_for_status()
        body=response.json()
        if not isinstance(body,dict):
            raise ValueError("Malformed provider object")
        return body

    async def refresh_devices(self):
        if self.settings is not None:
            await self.ensure_account()
        account=self.account_id if self.account_id is not None else await asyncio.to_thread(self.store.account_key)
        async with self.devices_lock:
            values=[]
            params={"limit":100}
            seen=set()
            for _ in range(100):
                body=await self._read("/devices",params)
                rows=body.get("devices")
                if not isinstance(rows,list):
                    raise ValueError("Malformed device list")
                for row in rows:
                    if not isinstance(row,dict) or not isinstance(row.get("iden"),str) or not row["iden"]:
                        continue
                    active=row.get("active") is True
                    def optional(name):
                        value=row.get(name)
                        return value[:1024] if isinstance(value,str) else None
                    values.append({"id":row["iden"],"nickname":optional("nickname") or optional("model") or row["iden"],
                        "type":optional("icon") or optional("type"),"manufacturer":optional("manufacturer"),"model":optional("model"),
                        "active":active,"can_push":active and row.get("pushable") is not False,
                        "can_sms":active and row.get("has_sms") is True})
                cursor=body.get("cursor")
                if not cursor:
                    await asyncio.to_thread(self.store.save_devices,values,account)
                    return
                if not isinstance(cursor,str) or cursor in seen:
                    raise ValueError("Malformed device pagination")
                seen.add(cursor)
                params["cursor"]=cursor
            raise ValueError("Device pagination exceeded safety bound")

    async def ensure_account(self):
        if self.account_id is None:
            user=await self._read('/users/me')
            account=user.get('iden')
            if not isinstance(account,str) or not account:
                raise ValueError('Provider user identity unavailable')
            # Publish the in-memory identity only after the durable partition
            # switch succeeds; failures must retry this transaction.
            await asyncio.to_thread(self.store.set_setting,'pushbullet_account_id',account)
            self.account_id=account
        return self.account_id

    async def reload_encryption(self):
        account=await self.ensure_account()
        previous_keys=self.device_keys
        previous_states=self.key_states
        self.device_keys={}
        self.key_states={}
        devices=await asyncio.to_thread(self.store.device_list)
        keys={}
        for device in devices:
            reference=await asyncio.to_thread(self.store.device_encryption,device['id'])
            entry=reference['entry']
            try:
                if entry not in keys:
                    password=await asyncio.to_thread(device_e2e_password,self.settings,entry)
                    keys[entry]=await asyncio.to_thread(derive_key,password,account) if password else None
                    password=None
                key=keys[entry]
                if key is not None:
                    self.device_keys[device['id']]=key
                    self.key_states[device['id']]='verified' if previous_keys.get(device['id'])==key and previous_states.get(device['id'])=='verified' else 'key_ready'
                else:
                    self.key_states[device['id']]='unconfigured'
            except Exception:
                self.key_states[device['id']]='locked'
        if self.encryption is not None:
            async def loader():
                password=await asyncio.to_thread(e2e_password,self.settings)
                return await asyncio.to_thread(derive_key,password,account) if password else None
            await self.encryption.reload(loader,self.device_keys,self.key_states)
        for device in devices:
            await self.sms.replay_cached(device['id'])

    async def request_sms_refresh(self,device_id=None):
        await self.sms.request(device_id)

    async def test_encryption(self,device_id):
        await self.reload_encryption()
        account=await self.ensure_account()
        # A key derivation is readiness, not proof. Authenticate retained ciphertext.
        verified=False
        rows=await asyncio.to_thread(self.store.sms_envelopes,account,device_id)
        for row in rows:
            try:
                self.sms.decode(account,device_id,json.loads(row['payload']))
                verified=True
                break
            except Exception:
                pass
        if self.key_states.get(device_id)=='verified':
            verified=True
        if any(d['id']==device_id and d['active'] and d['can_sms'] for d in await asyncio.to_thread(self.store.device_list)):
            await self.sms.request(device_id)
        return verified

    async def _device_mutation(self,method,path,payload=None):
        try:
            response=await self.client.request(method,path,json=payload)
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError:
            raise SubmissionError('uncertain','device_outcome_unknown','Device change outcome is unknown; refresh registrations before trying again') from None
        if response.status_code>=500 or 300<=response.status_code<400:
            raise SubmissionError('uncertain','device_outcome_unknown','Device change outcome is unknown; refresh registrations before trying again')
        if not 200<=response.status_code<300:
            raise SubmissionError('failed','device_change_rejected','Provider rejected the device change')
        try:
            body=response.json()
            if not isinstance(body,dict):
                raise ValueError()
            return body
        except ValueError:
            raise SubmissionError('uncertain','device_response_unknown','Provider response did not confirm the device change') from None

    async def register_device(self,nickname):
        await self.ensure_account()
        async with self.registration_lock,self.devices_lock:
            pending=await asyncio.to_thread(self.store.get_setting,'device_registration_pending')
            if pending:
                raise SubmissionError('uncertain','registration_pending','Previous registration outcome needs review; refresh devices rather than create a duplicate')
            await asyncio.to_thread(self.store.set_setting,'device_registration_pending',{'nickname':nickname,'state':'submitting','at':time.time()})
            try:
                body=await self._device_mutation('POST','/devices',{'nickname':nickname,'model':'pbdesk Linux','manufacturer':'pbdesk','icon':'desktop'})
                identity=body.get('iden')
                if not isinstance(identity,str) or not identity:
                    raise SubmissionError('uncertain','device_response_unknown','Registration response did not contain a device identity')
                await asyncio.to_thread(self.store.set_setting,'current_device_id',identity)
                rows=await asyncio.to_thread(self.store.device_list)
                rows=[row for row in rows if row['id']!=identity]
                rows.append({'id':identity,'nickname':nickname,'type':'desktop','model':'pbdesk Linux','manufacturer':'pbdesk','active':True,'can_push':True,'can_sms':False})
                await asyncio.to_thread(self.store.save_devices,rows,self.account_id)
                await asyncio.to_thread(self.store.set_setting,'device_registration_pending',None)
                return identity
            except SubmissionError as exc:
                if exc.state=='failed':
                    await asyncio.to_thread(self.store.set_setting,'device_registration_pending',None)
                else:
                    pending=await asyncio.to_thread(self.store.get_setting,'device_registration_pending')
                    await asyncio.to_thread(self.store.set_setting,'device_registration_pending',{**pending,'state':'uncertain'})
                raise

    async def resolve_registration(self,device_id,acknowledge):
        await self.ensure_account()
        from datetime import datetime
        async with self.registration_lock,self.devices_lock:
            pending=await asyncio.to_thread(self.store.get_setting,'device_registration_pending')
            if not pending:
                raise ValueError('No uncertain registration needs review')
            synced=await asyncio.to_thread(self.store.get_setting,'devices_synced_at')
            try:
                fresh=datetime.fromisoformat(synced).timestamp()>=pending['at']
            except (TypeError,ValueError,KeyError):
                fresh=False
            if acknowledge is not True or not fresh:
                raise ValueError('Refresh authoritative registrations and explicitly acknowledge the unknown outcome first')
            if device_id is not None:
                rows=await asyncio.to_thread(self.store.device_list)
                if not any(row['id']==device_id and row['active'] and row['model']=='pbdesk Linux' for row in rows):
                    raise ValueError('Select the exact registered pbdesk Linux device')
                await asyncio.to_thread(self.store.set_setting,'current_device_id',device_id)
            await asyncio.to_thread(self.store.set_setting,'device_registration_pending',None)

    async def rename_device(self,device_id,nickname):
        await self.ensure_account()
        from urllib.parse import quote
        async with self.devices_lock:
            await self._device_mutation('POST','/devices/'+quote(device_id,safe=''),{'nickname':nickname})
            # Keep the exact ID even if the next authoritative refresh is offline.
            rows=await asyncio.to_thread(self.store.device_list)
            for row in rows:
                if row['id']==device_id:
                    row['nickname']=nickname
            await asyncio.to_thread(self.store.save_devices,rows,self.account_id)

    async def remove_device(self,device_id):
        await self.ensure_account()
        from urllib.parse import quote
        async with self.devices_lock:
            await self._device_mutation('DELETE','/devices/'+quote(device_id,safe=''))
            await asyncio.to_thread(self.store.deactivate_device,device_id)

    async def send(self,action):
        # This method is called by the sole dispatcher only after durable claim.
        if action["transport"]!="provider_plaintext":
            raise SubmissionError("failed","unsupported_transport","No supported transport was approved")
        if action["kind"]=="sms":
            path="/texts"
            payload={"data":{"target_device_iden":action["device_id"],"addresses":[action["recipient"]],
                "message":action["body"],"guid":action["client_request_id"]}}
        else:
            path="/pushes"
            payload={"type":action["kind"],"device_iden":action["device_id"],"title":action["title"],"body":action["body"]}
            if action["kind"]=="link":
                payload["url"]=action["url"]
        try:
            response=await self.client.post(path,json=payload)
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError:
            raise SubmissionError("uncertain","network_outcome_unknown","Connection failed during submission; do not blindly retry") from None
        if response.status_code>=500:
            raise SubmissionError("uncertain","upstream_outcome_unknown","Provider failed during submission; acceptance is unknown")
        if response.status_code==429:
            raise SubmissionError("failed","rate_limited","Provider rate limit rejected submission; no automatic retry")
        if 400<=response.status_code<500:
            raise SubmissionError("failed","upstream_rejected","Provider rejected the approved submission")
        if not 200<=response.status_code<300:
            raise SubmissionError("uncertain","unexpected_response","Unexpected submission response; do not blindly retry")
        try:
            result=response.json()
            if not isinstance(result,dict):
                raise ValueError()
        except ValueError:
            raise SubmissionError("uncertain","malformed_response","Provider response could not confirm acceptance") from None
        upstream_id=result.get("iden")
        return upstream_id if isinstance(upstream_id,str) else None

    async def sync(self):
        previous=await asyncio.to_thread(self.store.get_setting,'push_sync',{})
        last_success=previous.get('last_success_at') if isinstance(previous,dict) else None
        await asyncio.to_thread(self.store.set_setting,'push_sync',{'state':'refreshing','last_success_at':last_success,'error':None})
        try:
            await self._sync_pushes()
        except asyncio.CancelledError:
            await asyncio.to_thread(self.store.set_setting,'push_sync',{'state':'idle','last_success_at':last_success,'error':None})
            raise
        except Exception as exc:
            state='rate_limited' if isinstance(exc,RateLimited) else 'error'
            await asyncio.to_thread(self.store.set_setting,'push_sync',{'state':state,'last_success_at':last_success,'error':'Push synchronization could not finish; cached messages retained'})
            raise
        await asyncio.to_thread(self.store.set_setting,'push_sync',{'state':'idle','last_success_at':time.time(),'error':None})

    async def _sync_pushes(self):
        cursor_key="pushbullet_modified:"+self.account_id if self.account_id else "pushbullet_modified"
        modified = await asyncio.to_thread(self.store.get_setting, cursor_key, 0)
        # A corrupted old setting must not poison every future synchronization.
        if utc_timestamp(modified) is None or modified > time.time() + 86400:
            modified = 0
        params = {"modified_after": str(modified), "limit": 100}
        latest = modified
        seen_cursors = set()
        for _ in range(1000):
            response = await self.client.get("/pushes", params=params)
            if response.status_code == 401:
                raise ProviderUnauthorized()
            if response.status_code == 429:
                try:
                    delay = float(response.headers.get("Retry-After", "60"))
                except (ValueError, OverflowError):
                    delay = 60
                if not math.isfinite(delay):
                    delay = 60
                raise RateLimited(min(max(delay, 1), 3600))
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("pushes"), list):
                raise ValueError("Malformed push list")
            # Server Date is validation context only, never the sync cursor.
            # A generous one-day skew allowance avoids accepting far-future poison.
            reference = time.time()
            try:
                server_date = parsedate_to_datetime(response.headers.get("Date", ""))
                if server_date.tzinfo is not None and utc_timestamp(server_date.timestamp()) is not None:
                    reference = server_date.timestamp()
            except (ValueError, TypeError, OverflowError):
                pass
            for push in body["pushes"]:
                if not isinstance(push, dict):
                    continue
                timestamp = utc_timestamp(push.get("modified"))
                if timestamp is None or timestamp > reference + 86400:
                    continue
                await self.ingest(push)
                # Tombstones/dismissals also advance the remote cursor, even
                # though retained-history normalization intentionally ignores them.
                latest = max(latest, timestamp)
            cursor = body.get("cursor")
            if not cursor:
                await asyncio.to_thread(self.store.set_setting, cursor_key, latest)
                return
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise ValueError("Invalid pagination cursor")
            seen_cursors.add(cursor)
            params["cursor"] = cursor
        raise ValueError("Push pagination exceeded safety bound")

    async def run(self):
        history_task=asyncio.create_task(self.sms.run())
        try:
            await self._run_connection()
        finally:
            history_task.cancel()
            await asyncio.gather(history_task,return_exceptions=True)

    async def _run_connection(self):
        delay = 1
        while True:
            try:
                await self.ensure_account()
                # Reconcile stored pushes each reconnect. Ephemeral mirrors cannot be recovered.
                try:
                    await asyncio.wait_for(self.refresh_devices(),timeout=20)
                except ProviderUnauthorized:
                    raise
                except Exception:
                    pass  # Retain stale devices; ordinary inbox sync may still work.
                await self.reload_encryption()
                await self.sync()
                await self.sms.request()
                async with websockets.connect("wss://stream.pushbullet.com/websocket/" + self._token,
                                              open_timeout=20, max_size=1048576, max_queue=16,
                                              ping_interval=20, ping_timeout=20) as socket:
                    self.state = "connected"
                    delay = 1
                    while True:
                        message = await asyncio.wait_for(socket.recv(), timeout=90)
                        try:
                            payload = json.loads(message)
                        except (ValueError, TypeError):
                            continue
                        if not isinstance(payload, dict):
                            continue
                        if payload.get("type") == "tickle" and payload.get("subtype") == "push":
                            await self.sync()
                        elif payload.get("type")=="tickle" and payload.get("subtype")=="device":
                            try:
                                await asyncio.wait_for(self.refresh_devices(),timeout=20)
                            except Exception:
                                pass
                        elif payload.get("type") == "push":
                            await self.ingest(payload)

            except asyncio.CancelledError:
                raise
            except ProviderUnauthorized:
                self.state = "unauthorized"
                await asyncio.sleep(60)
            except RateLimited as exc:
                self.state = "rate_limited"
                await asyncio.sleep(exc.delay)
            except Exception:
                # Never emit exception/URL: the documented stream URL contains the token.
                self.state = "reconnecting"
                await asyncio.sleep(delay + random.random())
                delay = min(delay * 2, 60)
