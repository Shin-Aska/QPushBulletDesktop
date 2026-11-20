"""Retained SMS snapshots: failures and remote removal never erase readable rows."""
import json
import time
from sqlalchemy import Table, Column, Text, Integer, Float, MetaData, select, func, update
from sqlalchemy.dialects.sqlite import insert as upsert

metadata=MetaData()
messages=Table('sms_messages',metadata,Column('id',Integer,primary_key=True),
    *(Column(k,Text) for k in ('account_id','device_id','conversation_id','message_id','direction','body')),
    Column('timestamp',Float),Column('addresses',Text),Column('name',Text),Column('decrypted',Integer),Column('image_urls',Text),Column('recipients',Text))
sync=Table('sms_sync',metadata,Column('account_id',Text,primary_key=True),Column('device_id',Text,primary_key=True),
    Column('state',Text),Column('last_success_at',Float),Column('error',Text),Column('has_more',Integer))
threads=Table('sms_threads',metadata,Column('account_id',Text,primary_key=True),Column('device_id',Text,primary_key=True),
    Column('conversation_id',Text,primary_key=True),Column('fingerprint',Text))
envelopes=Table('sms_envelopes',metadata,Column('account_id',Text,primary_key=True),Column('device_id',Text,primary_key=True),
    Column('permanent_key',Text,primary_key=True),Column('payload',Text))
encryption=Table('device_encryption',metadata,Column('account_id',Text,primary_key=True),Column('device_id',Text,primary_key=True),Column('mode',Text),Column('entry',Text))


class SmsStore:
    """Mixin providing account-scoped SMS history, sync state, and device encryption storage."""

    def sms_account(self):
        """Return the active Pushbullet account identifier from settings.

        :return: Active Pushbullet account identifier, or ``None`` if unset.
        :rtype: str | None
        """
        return self.get_setting('pushbullet_account_id')

    def sms_snapshot(self,device_id,direction=None):
        """Return retained SMS messages, sync status, and unavailable thread count for a device.

        :param device_id: Target Pushbullet device identifier.
        :type device_id: str
        :param direction: Optional message direction filter (``'incoming'`` or ``'outgoing'``).
        :type direction: str | None
        :return: Dictionary containing ``'account_id'``, ``'messages'``, and ``'sync'`` metadata.
        :rtype: dict[str, object]
        """
        account=self.sms_account()
        with self.engine.connect() as conn:
            statement=select(messages).where(messages.c.account_id==account,messages.c.device_id==device_id)
            if direction:
                statement=statement.where(messages.c.direction==direction)
            rows=[]
            for row in conn.execute(statement.order_by(messages.c.timestamp,messages.c.id)).mappings():
                rows.append({**dict(row),'addresses':json.loads(row['addresses']),'decrypted':bool(row['decrypted']),'image_urls':json.loads(row['image_urls'] or '[]'),'recipients':json.loads(row['recipients'] or '[]')})
            # Thumbnails describe contacts, not each historical message. Expose
            # each conversation's latest copy once, with a snapshot-wide budget.
            seen = set()
            thumbnail_budget = 512 * 1024
            for message in reversed(rows):
                conversation = message['conversation_id']
                first = conversation not in seen
                seen.add(conversation)
                for recipient in message['recipients']:
                    thumbnail = recipient.get('thumbnail')
                    if thumbnail and first and len(thumbnail) <= thumbnail_budget:
                        thumbnail_budget -= len(thumbnail)
                    else:
                        recipient.pop('thumbnail', None)
            state=conn.execute(select(sync).where(sync.c.account_id==account,sync.c.device_id==device_id)).mappings().first()
            public={k:state[k] for k in ('state','last_success_at','error','has_more')} if state else dict(state='idle',last_success_at=None,error=None,has_more=False)
            public['has_more']=bool(public['has_more'])
            from pbdesk.storage.database import settings_table
            key=self.sms_unavailable_key(account,device_id)
            value=conn.execute(select(settings_table.c.value).where(settings_table.c.key==key)).scalar_one_or_none()
            missing=json.loads(value) if value else {}
            public['unavailable_count']=len(missing.get('conversation_ids',[]))
            return {'account_id':account,'messages':rows,'sync':public}

    def sms_message(self, message_id):
        """Resolve a retained row only inside the active provider account.

        :param message_id: Primary-key identifier of the retained SMS message row.
        :type message_id: int
        :return: Decoded SMS message dictionary if found in the active account, else ``None``.
        :rtype: dict[str, object] | None
        """
        with self.engine.connect() as conn:
            row = conn.execute(select(messages).where(messages.c.id == message_id,
                messages.c.account_id == self.account_key())).mappings().first()
            return {**dict(row), 'image_urls': json.loads(row['image_urls'] or '[]'),
                'recipients': json.loads(row['recipients'] or '[]')} if row else None

    @staticmethod
    def sms_unavailable_key(account,device):
        """Return the settings key tracking unavailable SMS conversations for a device.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :return: Canonical settings key string for unavailable conversation metadata.
        :rtype: str
        """
        return 'sms_unavailable:'+json.dumps([account,device],separators=(',',':'))

    def sms_unavailable(self,account,device):
        """Return stored metadata for unavailable SMS conversations on a device.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :return: Stored metadata dictionary of check time and unavailable conversation IDs.
        :rtype: dict[str, object]
        """
        return self.get_setting(self.sms_unavailable_key(account,device),{})

    def record_sms_unavailable(self,account,device,conversations):
        """Persist the sorted set of unavailable SMS conversation identifiers.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :param conversations: Unavailable conversation identifiers to record.
        :type conversations: collections.abc.Iterable[str]
        """
        # Retain provider identities locally; the API exposes only their count.
        self.set_setting(self.sms_unavailable_key(account,device),{'checked_at':time.time(),'conversation_ids':sorted(set(conversations))})

    def sms_status(self,account,device,state,error=None,success=False):
        """Upsert SMS synchronization status and optional success timestamp for a device.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :param state: Synchronization state string (such as ``'idle'``, ``'queued'``, or ``'refreshing'``).
        :type state: str
        :param error: Optional status detail or error message.
        :type error: str | None
        :param success: Whether to record the current timestamp as ``last_success_at``.
        :type success: bool
        """
        values=dict(account_id=account,device_id=device,state=state,error=error,has_more=0)
        if success:
            values['last_success_at']=time.time()
        with self.writing() as conn:
            statement=upsert(sync).values(**values)
            conn.execute(statement.on_conflict_do_update(index_elements=['account_id','device_id'],set_={k:statement.excluded[k] for k in values if k not in ('account_id','device_id')}))

    def recover_sms_sync(self):
        """Reset interrupted refreshing or queued SMS sync states back to idle."""
        with self.writing() as conn:
            conn.execute(update(sync).where(sync.c.state.in_(['refreshing','queued'])).values(state='idle',error=None))

    def sms_thread_fingerprint(self,account,device,conversation):
        """Return the stored fingerprint for an SMS conversation thread.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :param conversation: SMS conversation identifier.
        :type conversation: str
        :return: Stored thread fingerprint string, or ``None`` if absent.
        :rtype: str | None
        """
        with self.engine.connect() as conn:
            return conn.execute(select(threads.c.fingerprint).where(threads.c.account_id==account,threads.c.device_id==device,threads.c.conversation_id==conversation)).scalar_one_or_none()

    def merge_sms_thread(self,account,device,conversation,values,fingerprint):
        """Upsert SMS messages and update the thread fingerprint atomically.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :param conversation: SMS conversation identifier.
        :type conversation: str
        :param values: Normalized SMS message dictionaries to upsert.
        :type values: collections.abc.Iterable[dict[str, object]]
        :param fingerprint: Thread fingerprint string for the conversation snapshot.
        :type fingerprint: str
        """
        with self.writing() as conn:
            for value in values:
                statement=upsert(messages).values(
                    **{**value,'account_id':account,'device_id':device,'conversation_id':conversation,'addresses':json.dumps(value['addresses']),'image_urls':json.dumps(value.get('image_urls',[])),'recipients':json.dumps(value.get('recipients',[]))})
                conn.execute(statement.on_conflict_do_update(index_elements=['account_id','device_id','conversation_id','message_id','direction'],
                    set_={k:statement.excluded[k] for k in ('body','timestamp','addresses','name','decrypted','image_urls','recipients')}))
            statement=upsert(threads).values(account_id=account,device_id=device,conversation_id=conversation,fingerprint=fingerprint)
            conn.execute(statement.on_conflict_do_update(index_elements=['account_id','device_id','conversation_id'],set_={'fingerprint':fingerprint}))

    def retain_sms_envelope(self,account,device,key,payload):
        """Persist an encrypted SMS envelope payload keyed by permanent key.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :param key: Permanent object key identifying the encrypted envelope.
        :type key: str
        :param payload: Raw encrypted SMS envelope JSON payload.
        :type payload: dict[str, object]
        """
        with self.writing() as conn:
            statement=upsert(envelopes).values(account_id=account,device_id=device,permanent_key=key,payload=json.dumps(payload))
            conn.execute(statement.on_conflict_do_update(index_elements=['account_id','device_id','permanent_key'],set_={'payload':statement.excluded.payload}))

    def delete_sms_envelope(self,account,device,key):
        """Delete a retained encrypted SMS envelope by permanent key.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :param key: Permanent object key identifying the envelope to delete.
        :type key: str
        """
        with self.writing() as conn:
            conn.execute(envelopes.delete().where(envelopes.c.account_id==account,envelopes.c.device_id==device,envelopes.c.permanent_key==key))

    def sms_envelopes(self,account,device):
        """Return all retained encrypted SMS envelopes for an account and device.

        :param account: Pushbullet account identifier.
        :type account: str
        :param device: Pushbullet device identifier.
        :type device: str
        :return: List of retained encrypted SMS envelope row dictionaries.
        :rtype: list[dict[str, object]]
        """
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(envelopes).where(envelopes.c.account_id==account,envelopes.c.device_id==device)).mappings()]

    def device_encryption(self,device):
        """Return the encryption mode and key entry configured for a device.

        :param device: Pushbullet device identifier.
        :type device: str
        :return: Dictionary containing ``'mode'`` and ``'entry'`` key reference settings.
        :rtype: dict[str, str]
        """
        with self.engine.connect() as conn:
            row=conn.execute(select(encryption).where(encryption.c.account_id==(self.sms_account() or ''),encryption.c.device_id==device)).mappings().first()
            return {k:row[k] for k in ('mode','entry')} if row else {'mode':'shared','entry':'e2e-password'}

    def set_device_encryption(self,device,mode,entry,account=None):
        """Save device encryption settings while verifying the active account.

        :param device: Pushbullet device identifier.
        :type device: str
        :param mode: Key reference mode (such as ``'shared'`` or ``'dedicated'``).
        :type mode: str
        :param entry: Secret entry identifier storing the device key.
        :type entry: str
        :param account: Expected Pushbullet account identifier, defaulting to the active account.
        :type account: str | None
        :raises ValueError: If the active account changed before the write transaction executed.
        """
        account=self.account_key() if account is None else account
        with self.writing() as conn:
            if self._account_at(conn)!=account:
                raise ValueError('Account changed while choosing the local key; select the device again')
            statement=upsert(encryption).values(account_id=account,device_id=device,mode=mode,entry=entry)
            conn.execute(statement.on_conflict_do_update(index_elements=['account_id','device_id'],set_={'mode':mode,'entry':entry}))

    def sms_history_metadata(self,device):
        """Return retained SMS message count and last successful sync time for a device.

        :param device: Pushbullet device identifier.
        :type device: str
        :return: Dictionary containing retained message ``'count'`` and ``'last_success_at'``.
        :rtype: dict[str, object]
        """
        account=self.sms_account()
        with self.engine.connect() as conn:
            count=conn.execute(select(func.count()).select_from(messages).where(messages.c.account_id==account,messages.c.device_id==device)).scalar()
            last=conn.execute(select(sync.c.last_success_at).where(sync.c.account_id==account,sync.c.device_id==device)).scalar_one_or_none()
        return {'count':count,'last_success_at':last}

    def devices_snapshot(self):
        """Return a consistent snapshot of devices, encryption settings, and SMS history metadata.

        :return: Dictionary containing ``'account_id'``, ``'devices'``, and sync settings.
        :rtype: dict[str, object]
        """
        from pbdesk.storage.workflows import devices
        from pbdesk.storage.database import settings_table
        with self.engine.connect() as conn:
            conn.exec_driver_sql('BEGIN')
            account=self._account_at(conn)
            result=[]
            for row in conn.execute(select(devices).where(devices.c.account_id==account).order_by(devices.c.nickname,devices.c.id)).mappings():
                device={key:value for key,value in row.items() if key!='account_id'}
                for flag in ('active','can_push','can_sms'):
                    device[flag]=bool(device[flag])
                reference=conn.execute(select(encryption).where(encryption.c.account_id==account,encryption.c.device_id==device['id'])).mappings().first()
                device['encryption']={key:reference[key] for key in ('mode','entry')} if reference else {'mode':'shared','entry':'e2e-password'}
                count=conn.execute(select(func.count()).select_from(messages).where(messages.c.account_id==account,messages.c.device_id==device['id'])).scalar()
                last=conn.execute(select(sync.c.last_success_at).where(sync.c.account_id==account,sync.c.device_id==device['id'])).scalar_one_or_none()
                device['history']={'count':count,'last_success_at':last}
                result.append(device)
            output={'account_id':account or None,'devices':result}
            for key in ('current_device_id','device_registration_pending','devices_synced_at'):
                name=key+':'+account if account else key
                value=conn.execute(select(settings_table.c.value).where(settings_table.c.key==name)).scalar_one_or_none()
                output[{'device_registration_pending':'registration_pending','devices_synced_at':'synced_at'}.get(key,key)]=json.loads(value) if value else None
            return output

    def deactivate_device(self,device):
        """Mark a device inactive and disable push and SMS capabilities.

        :param device: Pushbullet device identifier to deactivate.
        :type device: str
        """
        from pbdesk.storage.workflows import devices
        with self.writing() as conn:
            conn.execute(update(devices).where(devices.c.id==device,devices.c.account_id==self.account_key()).values(active=0,can_push=0,can_sms=0))
