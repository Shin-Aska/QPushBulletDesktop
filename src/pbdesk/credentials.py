"""Private local service credentials and explicitly verified provider keyring."""
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile

ROLES = {"desktop", "web", "mcp"}


def private_dir(settings):
    path = settings.state_dir
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise RuntimeError("State directory must be owned by the current user")
    path.chmod(0o700)
    return path


def _tokens(settings, role=None, revoke=False):
    root = private_dir(settings)
    lock = os.open(root / "credentials.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "service-credentials.json"
        data = {}
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise RuntimeError("Service credential file must be private (0600)")
                data = json.load(stream)
        except FileNotFoundError:
            pass
        changed = False
        for name in sorted(ROLES):
            if name not in data or (revoke and name == role):
                data[name] = secrets.token_urlsafe(48)
                changed = True
        if changed:
            fd, temporary = tempfile.mkstemp(dir=root, prefix=".credentials-")
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(data, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return data
    finally:
        os.close(lock)


def service_token(settings, role: str) -> str:
    if role not in ROLES:
        raise ValueError("Unknown service role")
    return _tokens(settings)[role]


def revoke_service_token(settings, role: str):
    if role not in ROLES:
        raise ValueError("Unknown service role")
    _tokens(settings, role, revoke=True)


def identify_service(settings, supplied):
    for role, token in _tokens(settings).items():
        if role in ROLES and secrets.compare_digest(token, supplied):
            return role
    return None


class CredentialLocked(RuntimeError):
    pass


def secure_backend():
    """Only Secret Service and KWallet backends; reject plaintext/chained fallbacks."""
    try:
        import keyring
        backend = keyring.get_keyring()
        name = type(backend).__module__
        if name not in {"keyring.backends.SecretService", "keyring.backends.kwallet"}:
            raise CredentialLocked("Secure Secret Service/KWallet backend unavailable")
        return backend
    except CredentialLocked:
        raise
    except Exception:
        raise CredentialLocked("Credential store unavailable or locked") from None


def provider_credential_source(settings=None):
    """Return the established SQLite reference without opening a wallet."""
    from pbdesk.config import Settings
    from pbdesk.configuration import get, provider_reference
    settings = settings or Settings.from_env()
    try:
        return provider_reference(get(settings, 'provider', {'backend': 'keyring'}))
    except Exception:
        raise CredentialLocked("Provider credential reference is invalid or inaccessible") from None


def _save_provider_source(settings, reference):
    from pbdesk.configuration import save_provider
    save_provider(settings, reference)


def use_kwallet(settings, folder="Pushbullet", entry="api-key", wallet=None):
    """Select an existing entry, persisting its location rather than its secret."""
    from pbdesk.services.kwallet import read_password, KWalletUnavailable
    if not all(isinstance(value, str) and value and len(value) <= 1024 for value in (folder, entry)):
        raise ValueError("KWallet folder and entry must be nonempty names")
    if wallet is not None and (not isinstance(wallet, str) or not wallet or len(wallet) > 1024):
        raise ValueError("KWallet name must be nonempty")
    if folder == 'Pushbullet' and entry == 'ai-openai-api-key':
        raise ValueError('The AI credential entry is reserved for OpenAI')
    try:
        token = read_password(folder, entry, wallet)
        if not token or not token.strip():
            raise CredentialLocked("The selected KWallet password entry is missing or empty")
    except KWalletUnavailable:
        raise CredentialLocked("KWallet is unavailable or locked; unlock it and allow pbdesk access") from None
    _save_provider_source(settings, {"backend": "kwallet", "wallet": wallet, "folder": folder, "entry": entry})


def provider_token(settings=None):
    try:
        reference = provider_credential_source(settings)
        if reference["backend"] == "kwallet":
            if reference.get('folder') == 'Pushbullet' and reference.get('entry') == 'ai-openai-api-key':
                raise CredentialLocked('The AI credential entry is reserved for OpenAI')
            from pbdesk.services.kwallet import read_password
            token = read_password(reference["folder"], reference["entry"], reference.get("wallet"))
            if not token or not token.strip():
                raise CredentialLocked("Selected KWallet entry is missing or empty")
            return token.strip()
        token = secure_backend().get_password("pbdesk", "pushbullet")
        return token or None
    except Exception:
        raise CredentialLocked("Credential store unavailable or locked") from None


def store_provider_token(token, settings=None):
    try:
        backend = secure_backend()
        backend.set_password("pbdesk", "pushbullet", token)
        if not secrets.compare_digest(backend.get_password("pbdesk", "pushbullet") or "", token):
            raise CredentialLocked("Credential verification failed")
        from pbdesk.config import Settings
        _save_provider_source(settings or Settings.from_env(), {"backend": "keyring"})
    except Exception:
        raise CredentialLocked("Could not securely save and verify provider credential") from None


def _health_mac(token, text):
    import hashlib
    import hmac
    return hmac.new(token.encode(), text.encode(), hashlib.sha256).hexdigest()


def health_headers(settings, role):
    nonce = secrets.token_hex(32)
    proof = _health_mac(service_token(settings, role), "pbdesk-health-request-v1\n" + role + "\n" + nonce)
    return {"X-PBDesk-Role": role, "X-PBDesk-Nonce": nonce, "X-PBDesk-Proof": proof}


def health_response(settings, headers, instance_id, ready):
    import re
    role = headers.get("x-pbdesk-role", headers.get("X-PBDesk-Role", ""))
    nonce = headers.get("x-pbdesk-nonce", headers.get("X-PBDesk-Nonce", ""))
    proof = headers.get("x-pbdesk-proof", headers.get("X-PBDesk-Proof", ""))
    if role not in ROLES or not re.fullmatch("[0-9a-f]{64}", nonce):
        return None
    token = service_token(settings, role)
    expected = _health_mac(token, "pbdesk-health-request-v1\n" + role + "\n" + nonce)
    if not secrets.compare_digest(expected, proof):
        return None
    body = {"application": "pbdesk-core", "api_version": 1, "instance_id": instance_id, "ready": ready}
    body["proof"] = _health_mac(token, "pbdesk-health-response-v1\n" + role + "\n" + nonce + "\n" + instance_id + "\n" + ("1" if ready else "0"))
    return body


def verify_health(settings, role, headers, body):
    import re
    import uuid
    if role not in ROLES or not isinstance(body, dict):
        return False
    if body.get("application") != "pbdesk-core" or type(body.get("api_version")) is not int or body["api_version"] != 1 or type(body.get("ready")) is not bool:
        return False
    instance_id = body.get("instance_id")
    try:
        if not isinstance(instance_id, str) or str(uuid.UUID(instance_id)) != instance_id:
            return False
    except ValueError:
        return False
    nonce = headers.get("X-PBDesk-Nonce", headers.get("x-pbdesk-nonce", ""))
    if not re.fullmatch("[0-9a-f]{64}", nonce):
        return False
    if headers.get("X-PBDesk-Role", headers.get("x-pbdesk-role")) != role:
        return False
    expected = _health_mac(service_token(settings, role), "pbdesk-health-response-v1\n" + role + "\n" + nonce + "\n" + instance_id + "\n" + ("1" if body["ready"] else "0"))
    proof = body.get("proof")
    return isinstance(proof, str) and secrets.compare_digest(expected, proof)


def e2e_password(settings=None):
    """Optional exact Unicode password, from the selected verified store only."""
    try:
        reference = provider_credential_source(settings)
        if reference["backend"] == "kwallet":
            from pbdesk.services.kwallet import read_password
            return read_password(reference["folder"], "e2e-password", reference.get("wallet"))
        return secure_backend().get_password("pbdesk", "pushbullet-e2e")
    except Exception:
        raise CredentialLocked("End-to-end credential store unavailable or locked") from None


def device_e2e_password(settings, entry='e2e-password'):
    """Local incoming-key reference in the established wallet/folder only."""
    if entry == 'ai-openai-api-key':
        raise CredentialLocked('The AI credential entry cannot be used for device encryption')
    from pbdesk.services.kwallet import read_password
    try:
        reference=provider_credential_source(settings)
        wallet=reference.get('wallet') or 'kdewallet'
        return read_password('Pushbullet',entry,wallet)
    except Exception:
        raise CredentialLocked('Local encryption credential is missing or KWallet is locked') from None


def create_device_e2e_password(settings,entry,password):
    from pbdesk.services.kwallet import write_new_password
    if not isinstance(entry,str) or not entry.strip() or len(entry)>256 or entry in {'api-key','e2e-password','ai-openai-api-key'}:
        raise ValueError('Choose a new device-specific KWallet entry name')
    if not isinstance(password,str) or not password or len(password)>16384:
        raise ValueError('An encryption password is required')
    try:
        reference=provider_credential_source(settings)
        write_new_password('Pushbullet',entry,password,reference.get('wallet') or 'kdewallet')
    except Exception:
        raise CredentialLocked('Could not create a new KWallet entry; existing entries are never overwritten') from None
