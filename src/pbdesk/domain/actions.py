"""Message contracts shared by every local interface; no provider side effects."""
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
import re
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class Message(BaseModel):
    """Outbound message payload validated per channel before queueing.

    :ivar kind: Message channel kind (``note``, ``link``, or ``sms``).
    :type kind: Literal["note", "link", "sms"]
    :ivar device_id: Target Pushbullet device identifier.
    :type device_id: str
    :ivar recipient: Explicit E.164 recipient phone number for SMS messages.
    :type recipient: str | None
    :ivar title: Optional title used for note and link pushes.
    :type title: str
    :ivar body: Text body of the push or SMS message.
    :type body: str
    :ivar url: Target HTTP(S) URL for link pushes.
    :type url: str | None
    :ivar transport: Delivery transport mode permitted by the local workflow.
    :type transport: Literal["provider_plaintext"]
    """
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
        """Enforce channel-specific constraints for SMS, link, and note payloads.

        :return: Validated message model instance.
        :rtype: Message
        :raises ValueError: When fields violate channel requirements for SMS, link, or note.
        """
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
    """Pending outbound message action submission with client request ID and TTL.

    :ivar client_request_id: Client-generated idempotency UUID for the action request.
    :type client_request_id: uuid.UUID
    :ivar expires_in_seconds: Pending approval time-to-live in seconds (1 to 86400).
    :type expires_in_seconds: int
    """
    client_request_id: UUID
    expires_in_seconds: StrictInt = Field(default=3600,ge=1,le=86400)


class ActionEdit(Message):
    """Revision-checked update payload for an existing pending action.

    :ivar expected_revision: Optimistic concurrency revision number expected by the caller.
    :type expected_revision: int
    """
    expected_revision: StrictInt = Field(ge=1)


class Approval(BaseModel):
    """Desktop approval payload confirming revision, content fingerprint, and transport.

    :ivar revision: Expected action revision number being approved.
    :type revision: int
    :ivar fingerprint: SHA-256 hex digest of the exact action payload being approved.
    :type fingerprint: str
    :ivar allow_provider_plaintext: Explicit user confirmation of plaintext provider transmission.
    :type allow_provider_plaintext: bool
    """
    model_config=ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    allow_provider_plaintext: StrictBool


class Cancellation(BaseModel):
    """Revision-checked payload for cancelling a pending action.

    :ivar revision: Expected action revision number being cancelled.
    :type revision: int
    """
    model_config=ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)


class ActionConflict(RuntimeError):
    """Raised when an action state transition or revision check conflicts."""
    pass


class InvalidDestination(ValueError):
    """Raised when the target device ID is unknown or ineligible."""
    pass
