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
    """Request payload for configuring and verifying a Pushbullet credential source.

    :ivar backend: Secret storage backend selected for the Pushbullet token.
    :type backend: Literal['kwallet', 'keyring']
    :ivar wallet: KWallet wallet name when using the ``kwallet`` backend.
    :type wallet: str
    :ivar folder: KWallet folder name storing the Pushbullet entry.
    :type folder: str
    :ivar entry: KWallet entry key name storing the Pushbullet token.
    :type entry: str
    :ivar token: Optional Pushbullet access token to verify and write to the secret backend.
    :type token: str | None
    :ivar expected_revision: Optimistic concurrency revision expected for provider configuration updates.
    :type expected_revision: int
    """

    model_config = ConfigDict(extra='forbid')
    backend: Literal['kwallet', 'keyring'] = 'kwallet'
    wallet: str = Field(default='kdewallet', min_length=1, max_length=1024)
    folder: str = Field(default='Pushbullet', min_length=1, max_length=1024)
    entry: str = Field(default='api-key', min_length=1, max_length=1024)
    token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    expected_revision: StrictInt = Field(ge=0)


class Preferences(BaseModel):
    """Request payload for updating desktop UI preferences.

    :ivar main_page: Optional preferred primary navigation page in the desktop UI.
    :type main_page: Literal['notifications', 'sms', 'summary'] | None
    :ivar color_scheme: Optional UI color scheme preference.
    :type color_scheme: Literal['system', 'light', 'dark'] | None
    """

    model_config = ConfigDict(extra='forbid')
    main_page: Literal['notifications', 'sms', 'summary'] | None = None
    color_scheme: Literal['system', 'light', 'dark'] | None = None


async def verify_token(token):
    """Verify a Pushbullet access token against the Pushbullet users API.

    :param token: Pushbullet access token to validate against ``/v2/users/me``.
    :type token: str
    :raises fastapi.HTTPException: If Pushbullet rejects the token with HTTP 401 (HTTP 422) or if an HTTP or payload validation error prevents verification (HTTP 502).
    """
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
    """Build the current provider setup and UI preferences snapshot.

    :param store: Opened local SQLite store instance.
    :type store: pbdesk.storage.database.Store
    :param demo: Whether the core is running in demo or fixture mode.
    :type demo: bool
    :return: Configuration snapshot containing storage type, setup status, and UI preferences.
    :rtype: dict[str, object]
    """
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
    """Persist partial UI preference updates into the SQLite settings table.

    :param store: Opened local SQLite store instance.
    :type store: pbdesk.storage.database.Store
    :param changes: Partial dictionary of UI preference fields to merge and persist.
    :type changes: dict[str, object]
    :return: Dictionary containing the updated ``preferences`` mapping.
    :rtype: dict[str, dict[str, object]]
    """
    with store.writing() as conn:
        raw = conn.execute(select(settings_table.c.value).where(
            settings_table.c.key == PREFIX + 'ui')).scalar_one_or_none()
        value = json.loads(raw) if raw is not None else {}
        value.update(changes)
        statement = insert(settings_table).values(key=PREFIX + 'ui', value=json.dumps(value))
        conn.execute(statement.on_conflict_do_update(index_elements=['key'], set_={'value': statement.excluded.value}))
    return {'preferences': value}


def install_configuration_routes(app, authorization, settings, guard_mutation, reload_provider, demo=False):
    """Register desktop-only configuration and provider setup routes.

    :param app: FastAPI application instance to register routes on.
    :type app: fastapi.FastAPI
    :param authorization: FastAPI dependency verifying the bearer service role.
    :type authorization: collections.abc.Callable[..., collections.abc.Awaitable[str]]
    :param settings: Application configuration settings instance.
    :type settings: pbdesk.config.Settings
    :param guard_mutation: Decorator serializing mutating endpoints under the core operation gate.
    :type guard_mutation: collections.abc.Callable[[collections.abc.Callable[..., object]], collections.abc.Callable[..., object]]
    :param reload_provider: Async callback reloading the Pushbullet provider and capabilities after setup changes.
    :type reload_provider: collections.abc.Callable[[], collections.abc.Awaitable[dict[str, object]]]
    :param demo: Whether demo or fixture mode disables live provider setup.
    :type demo: bool
    """
    async def desktop(role=Depends(authorization)):
        """Enforce local desktop authorization for configuration endpoints.

        :param role: Authenticated caller role resolved by the authorization dependency.
        :type role: str
        :raises fastapi.HTTPException: If the caller role is not ``'desktop'`` (HTTP 403).
        """
        if role != 'desktop':
            raise HTTPException(403, 'Configuration requires the local desktop')

    @app.get('/api/v1/configuration', dependencies=[Depends(desktop)])
    async def read():
        """Return the current configuration and UI preferences snapshot.

        :return: Current configuration and UI preferences snapshot dictionary.
        :rtype: dict[str, object]
        """
        return await asyncio.to_thread(snapshot, app.state.store, demo)

    @app.patch('/api/v1/configuration/preferences', dependencies=[Depends(desktop)])
    @guard_mutation
    async def preferences(value: Preferences):
        """Update and return persisted UI preferences.

        :param value: Validated partial UI preference update payload.
        :type value: Preferences
        :return: Dictionary containing the persisted ``preferences`` mapping.
        :rtype: dict[str, dict[str, object]]
        """
        return await asyncio.to_thread(update_preferences, app.state.store, value.model_dump(exclude_none=True))

    @app.post('/api/v1/configuration/provider', dependencies=[Depends(desktop)])
    @guard_mutation
    async def setup(value: ProviderSetup):
        """Validate, optionally store, and activate the Pushbullet provider configuration.

        :param value: Validated provider credential backend, entry, and optional token payload.
        :type value: ProviderSetup
        :return: Updated configuration snapshot dictionary after saving and reloading the provider.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If demo mode is active or the revision or credential store state conflicts (HTTP 409), or if the selected entry or token is invalid (HTTP 422).
        """
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
                            """Store the Pushbullet token in the keyring backend and verify persistence.

                            :raises CredentialLocked: If storing or verifying the Pushbullet token in the keyring fails.
                            """
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
