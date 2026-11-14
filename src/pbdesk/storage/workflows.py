"""Durable devices, encrypted recovery and approval-bound outbox transactions."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
from sqlalchemy import Table, Column, Integer, Text, MetaData, ForeignKey, select, insert, update, func
from pbdesk.domain.actions import ActionConflict, InvalidDestination

metadata=MetaData()
devices=Table("devices",metadata,Column("account_id",Text,primary_key=True),Column("id",Text,primary_key=True),Column("nickname",Text),Column("type",Text),
    Column("manufacturer",Text),Column("model",Text),Column("active",Integer),Column("can_push",Integer),Column("can_sms",Integer))
actions=Table("actions",metadata,Column("account_id",Text),Column("id",Integer,primary_key=True),Column("client_request_id",Text),
    Column("request_fingerprint",Text),Column("requested_by",Text),Column("approved_revision",Integer),Column("approved_fingerprint",Text),
    Column("revision",Integer),Column("fingerprint",Text),Column("kind",Text),Column("device_id",Text),Column("recipient",Text),
    Column("title",Text),Column("body",Text),Column("url",Text),Column("transport",Text),Column("state",Text),
    Column("created_at",Text),Column("expires_at",Text),Column("updated_at",Text),Column("upstream_id",Text),
    Column("error_code",Text),Column("error_message",Text))
audit=Table("action_audit",metadata,Column("id",Integer,primary_key=True),Column("action_id",ForeignKey("actions.id")),
    Column("revision",Integer),Column("fingerprint",Text),Column("state",Text),Column("actor",Text),Column("at",Text),Column("error_code",Text))
locked=Table("locked_envelopes",metadata,Column("account_id",Text),Column("id",Integer,primary_key=True),Column("digest",Text),
    Column("payload",Text),Column("created_at",Text),Column("status",Text))
MESSAGE_FIELDS=("kind","device_id","recipient","title","body","url","transport")
ACTION_FIELDS=("id","revision","fingerprint",*MESSAGE_FIELDS,"requested_by","state","created_at","expires_at","updated_at","upstream_id","error_code","error_message")


def utcnow():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def fingerprint(row):
    return digest({**{key:row[key] for key in MESSAGE_FIELDS},"expires_at":row["expires_at"]})


class WorkflowStore:
    @contextmanager
    def writing(self):
        with self.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except ActionConflict:
                # Conflict checks may have performed a legitimate expiry transition.
                # No requested edit/approval is written before these checks pass.
                conn.commit()
                raise
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def action_public(row):
        return {key:row[key] for key in ACTION_FIELDS}

    @staticmethod
    def _audit(conn,row,actor,error_code=None):
        conn.execute(insert(audit).values(action_id=row["id"],revision=row["revision"],fingerprint=row["fingerprint"],state=row["state"],actor=actor,at=row["updated_at"],error_code=error_code))

    def _row(self,conn,action_id):
        return conn.execute(select(actions).where(actions.c.id==action_id,actions.c.account_id==self.account_key())).mappings().first()

    def device_list(self):
        with self.engine.connect() as conn:
            return [{**{key:value for key,value in row.items() if key!="account_id"},**{key:bool(row[key]) for key in ("active","can_push","can_sms")}} for row in conn.execute(select(devices).where(devices.c.account_id==self.account_key()).order_by(devices.c.nickname,devices.c.id)).mappings()]

    def save_devices(self,values,account=None):
        from sqlalchemy.dialects.sqlite import insert as upsert
        account=self.account_key() if account is None else account
        with self.writing() as conn:
            if self._account_at(conn)!=account:
                raise ValueError('Account changed before device refresh completed')
            conn.execute(update(devices).where(devices.c.account_id==account).values(active=0,can_push=0,can_sms=0))
            for value in values:
                statement=upsert(devices).values(**value,account_id=account)
                conn.execute(statement.on_conflict_do_update(index_elements=["account_id","id"],set_={key:statement.excluded[key] for key in value if key!="id"}))
            # Keep the cache and its sync timestamp atomic.
            from pbdesk.storage.database import settings_table
            statement=upsert(settings_table).values(key=self._setting_key("devices_synced_at"),value=json.dumps(utcnow().isoformat()))
            conn.execute(statement.on_conflict_do_update(index_elements=["key"],set_={"value":statement.excluded.value}))

    def _validate_device(self,conn,message):
        device=conn.execute(select(devices).where(devices.c.id==message["device_id"],devices.c.account_id==self.account_key())).mappings().first()
        flag="can_sms" if message["kind"]=="sms" else "can_push"
        if not device or not device["active"] or not device[flag]:
            raise InvalidDestination("Selected device is inactive or does not support this channel")

    def validate_device(self,message):
        with self.engine.connect() as conn:
            self._validate_device(conn,message)

    def create_action(self,request,actor):
        data=request.model_dump(mode="json")
        request_hash=digest(data)
        request_id=self.scoped_identity(self.account_key(),str(data.pop("client_request_id")))
        lifetime=data.pop("expires_in_seconds")
        now=utcnow()
        with self.writing() as conn:
            previous=conn.execute(select(actions).where(actions.c.client_request_id==request_id,actions.c.account_id==self.account_key())).mappings().first()
            if previous:
                if previous["request_fingerprint"]!=request_hash:
                    raise ActionConflict("Request UUID already belongs to different content")
                return self.action_public(previous)
            self._validate_device(conn,data)
            values={**data,"account_id":self.account_key(),"client_request_id":request_id,"request_fingerprint":request_hash,"requested_by":actor,
                "revision":1,"state":"pending_approval","created_at":now.isoformat(),"updated_at":now.isoformat(),
                "expires_at":(now+timedelta(seconds=lifetime)).isoformat(),"upstream_id":None,"error_code":None,"error_message":None,
                "approved_revision":None,"approved_fingerprint":None}
            values["fingerprint"]=fingerprint(values)
            action_id=conn.execute(insert(actions).values(**values)).inserted_primary_key[0]
            values["id"]=action_id
            self._audit(conn,values,actor)
            return self.action_public(values)

    def _expire(self,conn,row):
        if row["state"] in {"pending_approval","ready"} and datetime.fromisoformat(row["expires_at"])<=utcnow():
            row={**row,"state":"expired","updated_at":utcnow().isoformat(),"error_code":"expired","error_message":"Action expired before submission"}
            conn.execute(update(actions).where(actions.c.id==row["id"]).values(state=row["state"],updated_at=row["updated_at"],error_code=row["error_code"],error_message=row["error_message"]))
            self._audit(conn,row,"core","expired")
        return row

    def action(self,action_id):
        with self.writing() as conn:
            row=self._row(conn,action_id)
            return self.action_public(self._expire(conn,row)) if row else None

    def action_list(self):
        return self.actions_snapshot()['actions']

    def actions_snapshot(self):
        self.expire_actions()
        with self.engine.connect() as conn:
            conn.exec_driver_sql('BEGIN')
            account=self._account_at(conn)
            rows=conn.execute(select(actions).where(actions.c.account_id==account).order_by(actions.c.id.desc()).limit(200)).mappings()
            return {'account_id':account or None,'actions':[self.action_public(row) for row in rows]}

    def edit_action(self,action_id,edit,actor):
        data=edit.model_dump(mode="json")
        expected=data.pop("expected_revision")
        with self.writing() as conn:
            row=self._row(conn,action_id)
            if not row:
                return None
            row=self._expire(conn,row)
            if row["state"] not in {"pending_approval","ready"} or row["revision"]!=expected:
                raise ActionConflict("Action state or revision changed")
            self._validate_device(conn,data)
            values={**data,"revision":expected+1,"state":"pending_approval","updated_at":utcnow().isoformat(),
                "approved_revision":None,"approved_fingerprint":None,"error_code":None,"error_message":None}
            values["fingerprint"]=fingerprint({**row,**values})
            conn.execute(update(actions).where(actions.c.id==action_id).values(**values))
            updated={**row,**values}
            self._audit(conn,updated,actor)
            return self.action_public(updated)

    def approve_action(self,action_id,approval,actor):
        with self.writing() as conn:
            row=self._row(conn,action_id)
            if not row:
                return None
            row=self._expire(conn,row)
            if row["state"]!="pending_approval" or row["revision"]!=approval.revision or row["fingerprint"]!=approval.fingerprint:
                raise ActionConflict("Approval no longer matches the pending message")
            if approval.allow_provider_plaintext is not True:
                raise ActionConflict("Explicit provider plaintext approval is required")
            self._validate_device(conn,row)
            values={"state":"ready","updated_at":utcnow().isoformat(),"approved_revision":row["revision"],"approved_fingerprint":row["fingerprint"]}
            conn.execute(update(actions).where(actions.c.id==action_id).values(**values))
            updated={**row,**values}
            self._audit(conn,updated,actor)
            return self.action_public(updated)

    def cancel_action(self,action_id,revision,actor):
        with self.writing() as conn:
            row=self._row(conn,action_id)
            if not row:
                return None
            row=self._expire(conn,row)
            if row["state"] not in {"pending_approval","ready"} or row["revision"]!=revision:
                raise ActionConflict("Action state or revision changed")
            values={"state":"cancelled","updated_at":utcnow().isoformat()}
            conn.execute(update(actions).where(actions.c.id==action_id).values(**values))
            updated={**row,**values}
            self._audit(conn,updated,actor)
            return self.action_public(updated)

    def expire_actions(self):
        with self.writing() as conn:
            for row in conn.execute(select(actions).where(actions.c.state.in_(["pending_approval","ready"]),actions.c.account_id==self.account_key())).mappings():
                self._expire(conn,row)

    def ready_actions(self):
        self.expire_actions()
        with self.engine.connect() as conn:
            return [row[0] for row in conn.execute(select(actions.c.id).where(actions.c.state=="ready",actions.c.account_id==self.account_key()).order_by(actions.c.id).limit(20))]

    def claim_action(self,action_id):
        with self.writing() as conn:
            row=self._row(conn,action_id)
            if not row:
                return None
            row=self._expire(conn,row)
            if row["state"]!="ready":
                return None
            if row["approved_revision"]!=row["revision"] or row["approved_fingerprint"]!=fingerprint(row) or row["fingerprint"]!=fingerprint(row):
                self._finish_in(conn,row,"failed","approval_mismatch","Stored approval no longer matches content")
                return None
            try:
                self._validate_device(conn,row)
            except InvalidDestination:
                self._finish_in(conn,row,"failed","device_unavailable","Selected device no longer supports this channel")
                return None
            values={"state":"submitting","updated_at":utcnow().isoformat()}
            result=conn.execute(update(actions).where(actions.c.id==action_id,actions.c.state=="ready",actions.c.revision==row["revision"],actions.c.fingerprint==row["fingerprint"]).values(**values))
            if result.rowcount!=1:
                return None
            claimed={**row,**values}
            self._audit(conn,claimed,"core")
            return {**dict(claimed),"client_request_id":self.unscoped_identity(claimed["client_request_id"])}

    def _finish_in(self,conn,row,state,code,message,upstream_id=None):
        values={"state":state,"error_code":code,"error_message":message,"upstream_id":upstream_id,"updated_at":utcnow().isoformat()}
        conn.execute(update(actions).where(actions.c.id==row["id"]).values(**values))
        self._audit(conn,{**row,**values},"core",code)

    def finish_action(self,action_id,state,code=None,message=None,upstream_id=None):
        if state not in {"failed","uncertain","upstream_accepted"}:
            raise ValueError("Invalid submission outcome")
        with self.writing() as conn:
            row=self._row(conn,action_id)
            if row and row["state"]=="submitting":
                self._finish_in(conn,row,state,code,message,upstream_id)

    def recover_actions(self):
        with self.writing() as conn:
            for row in conn.execute(select(actions).where(actions.c.state=="submitting")).mappings():
                self._finish_in(conn,row,"uncertain","restart_during_submission","Core stopped during submission; do not blindly retry")
        self.expire_actions()

    def action_audit(self,action_id):
        with self.engine.connect() as conn:
            if self._row(conn,action_id) is None:
                return []
            return [dict(row) for row in conn.execute(select(audit).where(audit.c.action_id==action_id).order_by(audit.c.id)).mappings()]

    def retain_envelope(self,payload,account=None):
        account=self.account_key() if account is None else account
        serialized=json.dumps(payload,sort_keys=True,separators=(",",":"))
        if len(serialized.encode())>1048576:
            return None
        inner=payload.get("push") if payload.get("type")=="push" else payload
        ciphertext=inner.get("ciphertext") if isinstance(inner,dict) else None
        identity=digest({"ciphertext":ciphertext}) if isinstance(ciphertext,str) else digest(payload)
        identity=self.scoped_identity(account,identity)
        with self.writing() as conn:
            existing=conn.execute(select(locked.c.id).where(locked.c.digest==identity)).scalar_one_or_none()
            if existing is not None:
                return existing
            result=conn.execute(insert(locked).values(account_id=account,digest=identity,payload=serialized,created_at=utcnow().isoformat(),status="locked"))
            return result.inserted_primary_key[0]

    def encrypted_envelopes(self,after_id=0,limit=100):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(locked).where(locked.c.id>after_id,locked.c.account_id==self.account_key(),locked.c.status!="ignored_control").order_by(locked.c.id).limit(min(limit,100))).mappings()]

    def encrypted_count(self):
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(locked).where(locked.c.account_id==self.account_key(),locked.c.status!="ignored_control")).scalar()

    def envelope_status(self,envelope_id,status):
        with self.writing() as conn:
            conn.execute(update(locked).where(locked.c.id==envelope_id,locked.c.account_id==self.account_key()).values(status=status))

    def delete_envelope(self,envelope_id):
        with self.writing() as conn:
            conn.execute(locked.delete().where(locked.c.id==envelope_id,locked.c.account_id==self.account_key()))
