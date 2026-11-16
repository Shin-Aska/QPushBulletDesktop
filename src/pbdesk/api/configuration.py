"""Desktop setup and preferences; secrets go only to the established wallet."""
import asyncio
import json
from typing import Literal
import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from pbdesk.configuration import PREFIX, provider_reference, save_provider
from pbdesk.credentials import CredentialLocked, secure_backend
from pbdesk.storage.database import settings_table


class ProviderSetup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    backend: Literal['kwallet', 'keyring'] = 'kwallet'
    wallet: str = Field(default='kdewallet', min_length=1, max_length=1024)
    folder: str = Field(default='Pushbullet', min_length=1, max_length=1024)
    entry: str = Field(default='api-key', min_length=1, max_length=1024)
    token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    expected_revision: StrictInt = Field(ge=0)


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    main_page: Literal['notifications', 'sms', 'summary'] | None = None
    color_scheme: Literal['system', 'light', 'dark'] | None = None


async def verify_token(token):
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=12) as client:
            response = await client.get('https://api.pushbullet.com/v2/users/me', headers={'Access-Token': token})
            if response.status_code == 401:
                raise HTTPException(422, 'Pushbullet rejected this access token.')
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict) or not isinstance(value.get('iden'), str) or not value['iden']:
                raise ValueError()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Could not verify the access token with Pushbullet. Check the network and retry.') from None


def snapshot(store, demo=False):
    with store.engine.connect() as conn:
        values = {key.removeprefix(PREFIX): json.loads(value) for key, value in conn.execute(
            select(settings_table.c.key, settings_table.c.value).where(settings_table.c.key.in_(
                [PREFIX + name for name in ('provider', 'setup', 'provider_revision', 'ui')])))}
    reference = values.get('provider')
    established = reference is not None or values.get('setup', {}).get('completed') is True
    error = None
    try:
        reference = provider_reference(reference) if reference is not None else None
    except ValueError:
        reference = None
        error = 'Saved connection settings are invalid. Select the connection again in Account settings.'
    return {'storage': 'sqlite', 'setup': {'required': not established and not demo,
        'completed': established, 'enabled': not demo,
        'source': reference, 'error': error,
        'revision': values.get('provider_revision', 0)}, 'preferences': values.get('ui', {})}


def update_preferences(store, changes):
    with store.writing() as conn:
        raw = conn.execute(select(settings_table.c.value).where(
            settings_table.c.key == PREFIX + 'ui')).scalar_one_or_none()
        value = json.loads(raw) if raw is not None else {}
        value.update(changes)
        statement = insert(settings_table).values(key=PREFIX + 'ui', value=json.dumps(value))
        conn.execute(statement.on_conflict_do_update(index_elements=['key'], set_={'value': statement.excluded.value}))
    return {'preferences': value}


def install_configuration_routes(app, authorization, settings, guard_mutation, reload_provider, demo=False):
    async def desktop(role=Depends(authorization)):
        if role != 'desktop':
            raise HTTPException(403, 'Configuration requires the local desktop')

    @app.get('/api/v1/configuration', dependencies=[Depends(desktop)])
    async def read():
        return await asyncio.to_thread(snapshot, app.state.store, demo)

    @app.patch('/api/v1/configuration/preferences', dependencies=[Depends(desktop)])
    @guard_mutation
    async def preferences(value: Preferences):
        return await asyncio.to_thread(update_preferences, app.state.store, value.model_dump(exclude_none=True))

    @app.post('/api/v1/configuration/provider', dependencies=[Depends(desktop)])
    @guard_mutation
    async def setup(value: ProviderSetup):
        if demo:
            raise HTTPException(409, 'Connection setup is disabled in demo mode.')
        reference = ({'backend': 'kwallet', 'wallet': value.wallet, 'folder': value.folder, 'entry': value.entry}
            if value.backend == 'kwallet' else {'backend': 'keyring'})
        if value.backend == 'kwallet' and (value.entry in {'e2e-password', 'ai-openai-api-key'} or not all(
                field.strip() for field in (value.wallet, value.folder, value.entry))):
            raise HTTPException(422, 'Choose a dedicated provider wallet entry.')
        from pbdesk.services.kwallet import read_password, write_provider_password, KWalletUnavailable
        async with app.state.configuration_lock:
            current = await asyncio.to_thread(snapshot, app.state.store)
            if current['setup']['revision'] != value.expected_revision:
                raise HTTPException(409, 'Connection settings changed. Reload and try again.')
            try:
                token = value.token
                if token is None:
                    if value.backend == 'kwallet':
                        token = await asyncio.to_thread(read_password, value.folder, value.entry, value.wallet, interactive=True)
                    else:
                        token = await asyncio.to_thread(lambda: secure_backend().get_password('pbdesk', 'pushbullet'))
                if not isinstance(token, str) or not token or any(char.isspace() for char in token):
                    raise HTTPException(422, 'The selected entry has no valid access token. Unlock KWallet or choose another entry.')
                await verify_token(token)
                if value.token is not None:
                    if value.backend == 'kwallet':
                        await asyncio.to_thread(write_provider_password, value.folder, value.entry, token, value.wallet)
                    else:
                        def write_keyring():
                            try:
                                backend = secure_backend()
                                backend.set_password('pbdesk', 'pushbullet', token)
                                if backend.get_password('pbdesk', 'pushbullet') != token:
                                    raise CredentialLocked('Credential verification failed')
                            except Exception:
                                raise CredentialLocked('Credential store unavailable') from None
                        await asyncio.to_thread(write_keyring)
                await asyncio.to_thread(save_provider, settings, reference, value.expected_revision)
            except KWalletUnavailable:
                raise HTTPException(409, 'KWallet could not save or read this entry. Unlock the selected wallet; existing entries are never replaced.') from None
            except CredentialLocked:
                raise HTTPException(409, 'The selected secure credential store is locked or unavailable.') from None
            except ValueError:
                raise HTTPException(409, 'Connection settings changed. Reload and try again.') from None
            # Only the explicit Save and connect action replaces the provider.
            await reload_provider()
            return await asyncio.to_thread(snapshot, app.state.store)
