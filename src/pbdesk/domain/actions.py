"""Message contracts shared by every local interface; no provider side effects."""
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
import re
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    kind: Literal["note", "link", "sms"]
    device_id: str = Field(min_length=1,max_length=1024)
    recipient: str | None = Field(default=None,max_length=32)
    title: str = Field(default="",max_length=4096)
    body: str = Field(max_length=65536)
    url: str | None = Field(default=None,max_length=8192)
    transport: Literal["provider_plaintext"]

    @model_validator(mode="after")
    def validate_channel(self):
        if self.kind == "sms":
            if not self.recipient or not re.fullmatch(r"\+[1-9][0-9]{1,14}",self.recipient):
                raise ValueError("SMS requires an explicit E.164 recipient")
            if not self.body.strip():
                raise ValueError("SMS body cannot be empty")
            if self.url is not None or self.title:
                raise ValueError("SMS supports body and recipient only")
        else:
            if self.recipient is not None:
                raise ValueError("Push recipients are explicit device IDs")
            if self.kind == "link":
                try:
                    parsed=urlsplit(self.url or "")
                    valid=parsed.scheme in {"http","https"} and bool(parsed.hostname) and parsed.username is None and parsed.password is None
                except ValueError:
                    valid=False
                if not valid:
                    raise ValueError("Link requires an absolute HTTP(S) URL without credentials")
            elif self.url is not None:
                raise ValueError("Note cannot include a URL")
            if self.kind == "note" and not (self.title.strip() or self.body.strip()):
                raise ValueError("Note title or body must be nonempty")
        return self


class ActionRequest(Message):
    client_request_id: UUID
    expires_in_seconds: StrictInt = Field(default=3600,ge=1,le=86400)


class ActionEdit(Message):
    expected_revision: StrictInt = Field(ge=1)


class Approval(BaseModel):
    model_config=ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    allow_provider_plaintext: StrictBool


class Cancellation(BaseModel):
    model_config=ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)


class ActionConflict(RuntimeError):
    pass


class InvalidDestination(ValueError):
    pass
