"""Account-scoped immutable summary output and independently retained history."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from sqlalchemy import text


class SummaryConflict(ValueError):
    """A revision changed since the caller captured it."""


class SummaryNotFound(ValueError):
    """No object with that identity belongs to this account."""


def _json(value):
    """Serialize a value to canonical compact JSON.

    :param value: JSON-serializable object to encode.
    :type value: object
    :return: Canonical compact JSON string with sorted keys.
    :rtype: str
    """
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _hash(value):
    """Return the SHA-256 hex digest of a JSON-serialized value.

    :param value: JSON-serializable object to digest.
    :type value: object
    :return: Lowercase SHA-256 hexadecimal digest string.
    :rtype: str
    """
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now():
    """Return the current UTC timestamp in ISO 8601 format.

    :return: Current UTC timestamp formatted as an ISO 8601 string.
    :rtype: str
    """
    return datetime.now(timezone.utc).isoformat()


def _string(value, name, maximum=256):
    """Validate and return a non-empty bounded string without null bytes.

    :param value: Candidate string value to validate.
    :type value: object
    :param name: Field name included in validation error messages.
    :type name: str
    :param maximum: Maximum permitted string length.
    :type maximum: int
    :return: Validated non-empty string.
    :rtype: str
    :raises ValueError: If ``value`` is not a non-empty string within ``maximum`` characters or contains null bytes.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise ValueError(f'Invalid {name}')
    return value


def _account(value):
    """Validate and return an account identifier string.

    :param value: Candidate account identifier value to validate.
    :type value: object
    :return: Validated non-empty account identifier string.
    :rtype: str
    """
    return _string(value, 'account_id')


def _date(value, name, nullable=False):
    """Parse and normalize a timezone-aware ISO 8601 timestamp.

    :param value: Candidate ISO 8601 timestamp string, or ``None`` if nullable.
    :type value: object
    :param name: Field name included in validation error messages.
    :type name: str
    :param nullable: Whether ``None`` is accepted and returned unchanged.
    :type nullable: bool
    :return: Normalized UTC ISO 8601 timestamp string, or ``None``.
    :rtype: str | None
    :raises ValueError: If ``value`` is not a valid timezone-aware ISO 8601 timestamp within 64 characters.
    """
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f'Invalid {name}')
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
        return date.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise ValueError(f'Invalid {name}') from None


def _int(value, name, maximum=2**63-1):
    """Validate and return a bounded non-negative integer.

    :param value: Candidate integer value to validate.
    :type value: object
    :param name: Field name included in validation error messages.
    :type name: str
    :param maximum: Inclusive upper bound for the integer value.
    :type maximum: int
    :return: Validated non-negative integer.
    :rtype: int
    :raises ValueError: If ``value`` is not a strict ``int`` in ``[0, maximum]``.
    """
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f'Invalid {name}')
    return value


def _uuid(value):
    """Validate and return a canonical UUID string.

    :param value: Candidate UUID string to parse and normalize.
    :type value: object
    :return: Canonical lowercase hyphenated UUID string.
    :rtype: str
    :raises ValueError: If ``value`` cannot be parsed as a valid UUID.
    """
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid UUID') from None


def normalize_config(config):
    """Validate and normalize a summary preset configuration dictionary.

    :param config: Raw preset configuration mapping to validate and normalize.
    :type config: dict[str, object]
    :return: Normalized summary preset configuration dictionary.
    :rtype: dict[str, object]
    :raises ValueError: If preset fields, filter counts, window hours, or conversation rules are invalid.
    """
    if not isinstance(config, dict):
        raise ValueError('Invalid preset')
    name = _string(config.get('name'), 'name', 100).strip()
    sources = config.get('sources')
    if not isinstance(sources, list) or not sources or any(s not in ('notifications', 'sms') for s in sources):
        raise ValueError('Invalid sources')
    devices = config.get('device_ids', [])
    pairs = config.get('conversations', [])
    if not isinstance(devices, list) or len(devices) > 100 or not isinstance(pairs, list) or len(pairs) > 100:
        raise ValueError('Too many device/conversation filters')
    devices = sorted(set(_string(x, 'device_id') for x in devices))
    normalized = []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {'device_id', 'conversation_id'}:
            raise ValueError('Invalid conversation')
        normalized.append((_string(pair['device_id'], 'device_id'), _string(pair['conversation_id'], 'conversation_id')))
    pairs = [{'device_id': d, 'conversation_id': c} for d, c in sorted(set(normalized))]
    mode = config.get('conversation_filter', 'selected' if pairs else 'all')
    if mode not in ('all', 'selected') or (mode == 'selected' and (not pairs or 'sms' not in sources)) or (mode == 'all' and pairs):
        raise ValueError('Selected conversations require a nonempty SMS selection')
    hours = config.get('window_hours', 24)
    if type(hours) is not int or hours not in (1, 6, 24, 72, 168):
        raise ValueError('Invalid window_hours')
    model = config.get('model_override')
    if model is not None:
        model = _string(model, 'model_override', 200)
    return dict(name=name, sources=sorted(set(sources)), device_ids=devices, conversations=pairs,
                conversation_filter=mode, window_hours=hours, model_override=model)


class SummaryRepository:
    """Use only the core-owned Store engine; never resolves an implicit account."""

    def __init__(self, store):
        """Initialize the repository with a core-owned Store instance.

        :param store: Core-owned database store instance.
        :type store: pbdesk.storage.database.Store
        """
        self.store = store

    @staticmethod
    def canonical_config_hash(config):
        """Return the canonical filter hash for a preset configuration.

        :param config: Preset configuration dictionary to hash.
        :type config: dict[str, object]
        :return: SHA-256 hex digest of normalized filter fields excluding display name and model override.
        :rtype: str
        """
        value = normalize_config(config)
        return _hash({k: v for k, v in value.items() if k not in ('name', 'model_override')})

    @staticmethod
    def _one(conn, sql, **values):
        """Execute a query and return the first row mapping as a dict or None.

        :param conn: Active SQLAlchemy database connection.
        :type conn: sqlalchemy.Connection
        :param sql: Parameterized SQL query string.
        :type sql: str
        :param values: Named SQL bind parameters forwarded to the query.
        :type values: object
        :return: First matching row as a dictionary, or ``None`` if absent.
        :rtype: dict[str, object] | None
        """
        row = conn.execute(text(sql), values).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _preset(row):
        """Hydrate a preset database row with parsed config and boolean archived flag.

        :param row: Raw ``summary_presets`` row mapping or ``None``.
        :type row: dict[str, object] | None
        :return: Hydrated preset dictionary, or ``None`` if ``row`` is falsy.
        :rtype: dict[str, object] | None
        """
        return {**{k: v for k, v in row.items() if k != 'config'}, **json.loads(row['config']), 'archived': bool(row['archived'])} if row else None

    def _seed(self, conn, account):
        """Seed default summary presets and state for a new account.

        :param conn: Active writable SQLAlchemy connection.
        :type conn: sqlalchemy.Connection
        :param account: Validated Pushbullet account identifier.
        :type account: str
        """
        if self._one(conn, 'SELECT * FROM summary_state WHERE account_id=:a', a=account):
            return
        stamp = _now()
        first = None
        for name, sources in [('All activity', ['notifications', 'sms']), ('Notifications only', ['notifications'])]:
            pid = str(uuid.uuid4())
            first = first or pid
            config = normalize_config(dict(name=name, sources=sources))
            conn.execute(text('INSERT INTO summary_presets VALUES (:a,:id,1,0,:config,:at,:at)'), dict(a=account, id=pid, config=_json(config), at=stamp))
        conn.execute(text('INSERT INTO summary_state VALUES (:a,:id,1,30,1)'), dict(a=account, id=first))

    def list_presets(self, account_id, include_archived=False):
        """Return summary presets for an account, optionally including archived ones.

        :param account_id: Account identifier owning the presets.
        :type account_id: str
        :param include_archived: Whether to include archived presets in the returned list.
        :type include_archived: bool
        :return: Ordered list of hydrated summary preset dictionaries.
        :rtype: list[dict[str, object]]
        """
        account_id = _account(account_id)
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            rows = conn.execute(text('SELECT * FROM summary_presets WHERE account_id=:a' + ('' if include_archived else ' AND archived=0') + ' ORDER BY created_at,id'), dict(a=account_id)).mappings()
            return [self._preset(dict(row)) for row in rows]

    def save_preset(self, account_id, config, expected_revision=0, preset_id=None):
        """Create or update a summary preset with optimistic revision checking.

        :param account_id: Account identifier owning the preset.
        :type account_id: str
        :param config: Preset configuration mapping to validate and persist.
        :type config: dict[str, object]
        :param expected_revision: Expected current preset revision, or ``0`` for a new preset.
        :type expected_revision: int
        :param preset_id: Existing preset UUID to update, or ``None`` to generate a new UUID.
        :type preset_id: str | None
        :return: Saved and hydrated summary preset dictionary.
        :rtype: dict[str, object]
        :raises SummaryConflict: If the target preset is archived or its revision changed.
        """
        account_id = _account(account_id)
        config = normalize_config(config)
        expected_revision = _int(expected_revision, 'expected_revision')
        pid = _uuid(preset_id) if preset_id else str(uuid.uuid4())
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            row = self._one(conn, 'SELECT * FROM summary_presets WHERE account_id=:a AND id=:id', a=account_id, id=pid)
            if row and row['archived']:
                raise SummaryConflict('Archived presets cannot be edited')
            if (row['revision'] if row else 0) != expected_revision:
                raise SummaryConflict('Preset revision changed')
            stamp = _now()
            if row:
                conn.execute(text('UPDATE summary_presets SET config=:config,revision=revision+1,updated_at=:at WHERE account_id=:a AND id=:id'), dict(config=_json(config), at=stamp, a=account_id, id=pid))
            else:
                conn.execute(text('INSERT INTO summary_presets VALUES (:a,:id,1,0,:config,:at,:at)'), dict(a=account_id, id=pid, config=_json(config), at=stamp))
            return self._preset(self._one(conn, 'SELECT * FROM summary_presets WHERE account_id=:a AND id=:id', a=account_id, id=pid))

    def _active(self, conn, account):
        """Return the active preset and active revision for an account.

        :param conn: Active SQLAlchemy database connection.
        :type conn: sqlalchemy.Connection
        :param account: Validated Pushbullet account identifier.
        :type account: str
        :return: Dictionary containing ``'preset'`` and ``'revision'`` for the active preset.
        :rtype: dict[str, object]
        """
        state = self._one(conn, 'SELECT * FROM summary_state WHERE account_id=:a', a=account)
        preset = self._preset(self._one(conn, 'SELECT * FROM summary_presets WHERE account_id=:a AND id=:id AND archived=0', a=account, id=state['active_preset_id']))
        return dict(preset=preset, revision=state['active_revision'])

    def get_active_preset(self, account_id):
        """Return the active summary preset and revision for an account.

        :param account_id: Account identifier to query.
        :type account_id: str
        :return: Dictionary containing the active ``'preset'`` dictionary and ``'revision'``.
        :rtype: dict[str, object]
        """
        account_id = _account(account_id)
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            return self._active(conn, account_id)

    def set_active_preset(self, account_id, preset_id, expected_revision):
        """Set the active summary preset for an account with revision checking.

        :param account_id: Account identifier owning the summary state.
        :type account_id: str
        :param preset_id: UUID of the non-archived preset to activate.
        :type preset_id: str
        :param expected_revision: Expected current ``active_revision`` counter.
        :type expected_revision: int
        :return: Updated dictionary containing the active ``'preset'`` and ``'revision'``.
        :rtype: dict[str, object]
        :raises SummaryConflict: If the active preset revision differs from ``expected_revision``.
        :raises SummaryNotFound: If no active non-archived preset matches ``preset_id``.
        """
        account_id, preset_id = _account(account_id), _uuid(preset_id)
        _int(expected_revision, 'expected_revision')
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            active = self._active(conn, account_id)
            if active['revision'] != expected_revision:
                raise SummaryConflict('Active preset changed')
            if not self._one(conn, 'SELECT id FROM summary_presets WHERE account_id=:a AND id=:id AND archived=0', a=account_id, id=preset_id):
                raise SummaryNotFound('Preset not found')
            if active['preset']['id'] != preset_id:
                conn.execute(text('UPDATE summary_state SET active_preset_id=:id,active_revision=active_revision+1 WHERE account_id=:a'), dict(id=preset_id, a=account_id))
            return self._active(conn, account_id)

    def archive_preset(self, account_id, preset_id, expected_revision):
        """Archive a preset and fall back to another active preset if needed.

        :param account_id: Account identifier owning the preset.
        :type account_id: str
        :param preset_id: UUID of the preset to archive.
        :type preset_id: str
        :param expected_revision: Expected preset revision for optimistic concurrency checking.
        :type expected_revision: int
        :return: Archived and hydrated preset dictionary.
        :rtype: dict[str, object]
        :raises SummaryNotFound: If no preset with ``preset_id`` exists for the account.
        :raises SummaryConflict: If the stored preset revision differs from ``expected_revision``.
        """
        account_id, preset_id = _account(account_id), _uuid(preset_id)
        _int(expected_revision, 'expected_revision')
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            row = self._one(conn, 'SELECT * FROM summary_presets WHERE account_id=:a AND id=:id', a=account_id, id=preset_id)
            if not row:
                raise SummaryNotFound('Preset not found')
            if row['revision'] != expected_revision:
                raise SummaryConflict('Preset revision changed')
            if not row['archived']:
                conn.execute(text('UPDATE summary_presets SET archived=1,revision=revision+1,updated_at=:at WHERE account_id=:a AND id=:id'), dict(a=account_id, id=preset_id, at=_now()))
                active = self._active(conn, account_id)
                if active['preset'] is None:
                    fallback = self._one(conn, 'SELECT id FROM summary_presets WHERE account_id=:a AND archived=0 ORDER BY created_at,id LIMIT 1', a=account_id)
                    if fallback is None:
                        config = normalize_config(dict(name='All activity', sources=['notifications', 'sms']))
                        fallback = {'id': str(uuid.uuid4())}
                        conn.execute(text('INSERT INTO summary_presets VALUES (:a,:id,1,0,:config,:at,:at)'), dict(a=account_id, id=fallback['id'], config=_json(config), at=_now()))
                    conn.execute(text('UPDATE summary_state SET active_preset_id=:id,active_revision=active_revision+1 WHERE account_id=:a'), dict(a=account_id, id=fallback['id']))
            return self._preset(self._one(conn, 'SELECT * FROM summary_presets WHERE account_id=:a AND id=:id', a=account_id, id=preset_id))

    def version(self, account_id):
        """Return active preset and retention policy revision counters for an account.

        :param account_id: Account identifier to query.
        :type account_id: str
        :return: Dictionary with ``'active_revision'`` and ``'retention_revision'`` counts.
        :rtype: dict[str, int]
        """
        account_id = _account(account_id)
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            state = self._one(conn, 'SELECT * FROM summary_state WHERE account_id=:a', a=account_id)
            return {k: state[k] for k in ('active_revision', 'retention_revision')}

    def lookup_generation(self, account_id, canonical_config_hash, resolved_model, prompt_version, input_fingerprint):
        """Look up a cached summary generation matching the given input key.

        :param account_id: Account identifier owning the generation cache.
        :type account_id: str
        :param canonical_config_hash: Canonical filter hash of the summary configuration.
        :type canonical_config_hash: str
        :param resolved_model: Resolved model identifier used for generation.
        :type resolved_model: str
        :param prompt_version: Prompt template version string.
        :type prompt_version: str
        :param input_fingerprint: Hash of the summarized input snapshot.
        :type input_fingerprint: str
        :return: Matching ``summary_generations`` row dictionary, or ``None`` if absent.
        :rtype: dict[str, object] | None
        """
        values = dict(a=_account(account_id), c=_string(canonical_config_hash, 'canonical_config_hash'), m=_string(resolved_model, 'resolved_model'), p=_string(prompt_version, 'prompt_version'), f=_string(input_fingerprint, 'input_fingerprint'))
        with self.store.engine.connect() as conn:
            return self._one(conn, 'SELECT * FROM summary_generations WHERE account_id=:a AND canonical_config_hash=:c AND resolved_model=:m AND prompt_version=:p AND input_fingerprint=:f', **values)

    @staticmethod
    def _generation(data):
        """Validate and normalize summary generation metadata and metrics.

        :param data: Raw generation payload dictionary to validate.
        :type data: dict[str, object]
        :return: Normalized generation column dictionary ready for persistence.
        :rtype: dict[str, object]
        :raises ValueError: If ``data`` is not a dict, ``truncated`` is invalid, or date bounds are inverted.
        """
        if not isinstance(data, dict):
            raise ValueError('Invalid generation')
        result = {key: _string(data.get(key), key, 16000 if key == 'markdown' else 256) for key in ('canonical_config_hash', 'resolved_model', 'prompt_version', 'input_fingerprint', 'markdown')}
        result['generated_at'] = _date(data.get('generated_at'), 'generated_at')
        result['actual_response_model'] = _string(data['actual_response_model'], 'actual_response_model') if data.get('actual_response_model') is not None else None
        for key in ('window_start', 'window_end', 'covered_oldest', 'covered_newest'):
            result[key] = _date(data.get(key), key, nullable=True)
        for key in ('notification_count', 'sms_count', 'conversation_count', 'latency_ms', 'input_tokens', 'output_tokens', 'cached_input_tokens'):
            result[key] = _int(data[key], key) if data.get(key) is not None else None
        if data.get('truncated') is not None and type(data['truncated']) is not bool:
            raise ValueError('Invalid truncated')
        result['truncated'] = int(data['truncated']) if data.get('truncated') is not None else None
        for start, end in [('window_start', 'window_end'), ('covered_oldest', 'covered_newest')]:
            if result[start] and result[end] and result[start] > result[end]:
                raise ValueError(f'Invalid {start}/{end} range')
        return result

    def save_generation(self, account_id, data, preset_snapshot=None, saved_at=None):
        """Save a generated summary and its captured preset snapshot into history.

        :param account_id: Account identifier owning the summary history.
        :type account_id: str
        :param data: Generation metadata, metrics, and markdown text.
        :type data: dict[str, object]
        :param preset_snapshot: Captured preset dictionary associated with the generation.
        :type preset_snapshot: dict[str, object] | None
        :param saved_at: Optional ISO 8601 timestamp overriding the saved timestamp.
        :type saved_at: str | None
        :return: Hydrated summary history entry dictionary including its generation record.
        :rtype: dict[str, object]
        """
        return self._save_generation(account_id, data, preset_snapshot, saved_at, 'generated')

    def _save_generation(self, account_id, data, preset_snapshot, saved_at, origin):
        """Persist a summary generation and entry row for generated or legacy origin.

        :param account_id: Account identifier owning the summary history.
        :type account_id: str
        :param data: Generation metadata, metrics, and markdown text.
        :type data: dict[str, object]
        :param preset_snapshot: Captured preset snapshot, required unless ``origin`` is ``'legacy'``.
        :type preset_snapshot: dict[str, object] | None
        :param saved_at: Optional ISO 8601 timestamp overriding the saved timestamp.
        :type saved_at: str | None
        :param origin: Entry origin label (``'generated'`` or ``'legacy'``).
        :type origin: str
        :return: Hydrated summary history entry dictionary including its generation record.
        :rtype: dict[str, object]
        :raises ValueError: If the captured preset snapshot is missing, malformed, or fails validation.
        :raises SummaryNotFound: If the referenced captured preset does not exist for the account.
        """
        account_id = _account(account_id)
        values = self._generation(data)
        saved_at = _date(saved_at or _now(), 'saved_at')
        if preset_snapshot is None and origin != 'legacy':
            raise ValueError('Captured preset snapshot required')
        snapshot = None
        if preset_snapshot is not None:
            snapshot = normalize_config(preset_snapshot)
            snapshot.update(id=_uuid(preset_snapshot.get('id')), revision=_int(preset_snapshot.get('revision'), 'preset revision'), account_id=account_id)
            for key in ('created_at', 'updated_at'):
                if preset_snapshot.get(key) is not None:
                    snapshot[key] = _date(preset_snapshot[key], key)
            if 'archived' in preset_snapshot:
                if type(preset_snapshot['archived']) is not bool:
                    raise ValueError('Invalid captured archive flag')
                snapshot['archived'] = preset_snapshot['archived']
            if snapshot['revision'] < 1:
                raise ValueError('Invalid preset revision')
            if preset_snapshot.get('account_id', account_id) != account_id:
                raise ValueError('Snapshot account mismatch')
            if self.canonical_config_hash(snapshot) != values['canonical_config_hash']:
                raise ValueError('Snapshot filter hash mismatch')
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            if snapshot:
                preset = self._one(conn, 'SELECT * FROM summary_presets WHERE account_id=:a AND id=:id', a=account_id, id=snapshot['id'])
                if not preset:
                    raise SummaryNotFound('Captured preset not found')
                if snapshot['revision'] > preset['revision']:
                    raise ValueError('Invalid captured preset revision')
            gid = str(uuid.uuid4())
            fields = ['account_id', 'id', *values]
            payload = dict(account_id=account_id, id=gid, **values)
            conn.execute(text('INSERT OR IGNORE INTO summary_generations (' + ','.join(fields) + ') VALUES (' + ','.join(':'+k for k in fields) + ')'), payload)
            generation = self._one(conn, 'SELECT * FROM summary_generations WHERE account_id=:account_id AND canonical_config_hash=:canonical_config_hash AND resolved_model=:resolved_model AND prompt_version=:prompt_version AND input_fingerprint=:input_fingerprint', **payload)
            key = _hash([snapshot['id'], snapshot['revision'], generation['id']]) if snapshot else _hash(['legacy', generation['id']])
            conn.execute(text('INSERT OR IGNORE INTO summary_entries VALUES (:a,:id,:key,:g,:p,:r,:snapshot,:at,:origin,0,0,NULL)'), dict(a=account_id, id=str(uuid.uuid4()), key=key, g=generation['id'], p=snapshot['id'] if snapshot else None, r=snapshot['revision'] if snapshot else None, snapshot=_json(snapshot) if snapshot else None, at=saved_at, origin=origin))
            row = self._one(conn, 'SELECT id FROM summary_entries WHERE account_id=:a AND entry_key=:key', a=account_id, key=key)
            return self._entry(conn, account_id, row['id'])

    def import_legacy(self, account_id, data):
        """Import a legacy cached summary digest into account summary history.

        :param account_id: Account identifier receiving the imported legacy digest.
        :type account_id: str
        :param data: Legacy digest payload containing ``'markdown'``, ``'generated_at'``, and ``'resolved_model'``.
        :type data: dict[str, object]
        :return: Hydrated summary history entry dictionary created for the legacy digest.
        :rtype: dict[str, object]
        """
        value = dict(data)
        value['canonical_config_hash'] = _hash({'origin': 'legacy', 'unknown_context': True})
        value['prompt_version'] = 'legacy-cache-v1'
        value['input_fingerprint'] = _hash({k: data.get(k) for k in ('markdown', 'generated_at', 'resolved_model')})
        return self._save_generation(account_id, value, None, data.get('generated_at'), 'legacy')

    def _entry(self, conn, account, entry_id):
        """Fetch and hydrate a summary history entry joined with its generation.

        :param conn: Active SQLAlchemy database connection.
        :type conn: sqlalchemy.Connection
        :param account: Validated Pushbullet account identifier.
        :type account: str
        :param entry_id: UUID of the summary history entry.
        :type entry_id: str
        :return: Hydrated summary history entry dictionary merged with generation fields, or ``None``.
        :rtype: dict[str, object] | None
        """
        row = self._one(conn, 'SELECT * FROM summary_entries WHERE account_id=:a AND id=:id', a=account, id=entry_id)
        if not row:
            return None
        generation = self._one(conn, 'SELECT * FROM summary_generations WHERE account_id=:a AND id=:id', a=account, id=row['generation_id'])
        generation['truncated'] = bool(generation['truncated']) if generation['truncated'] is not None else None
        row['preset_snapshot'] = json.loads(row['preset_snapshot']) if row['preset_snapshot'] else None
        row['pinned'] = bool(row['pinned'])
        row.pop('entry_key')
        return {**generation, **row, 'generation': generation}

    def get_history(self, account_id, entry_id):
        """Return a single summary history entry for an account.

        :param account_id: Account identifier owning the history entry.
        :type account_id: str
        :param entry_id: UUID of the summary history entry.
        :type entry_id: str
        :return: Hydrated summary history entry dictionary, or ``None`` if not found.
        :rtype: dict[str, object] | None
        """
        with self.store.engine.connect() as conn:
            return self._entry(conn, _account(account_id), _uuid(entry_id))

    def list_history(self, account_id, query=None, preset_id=None, pinned=None, date_from=None, date_to=None, limit=50, offset=0):
        """Return paginated and filtered summary history entries for an account.

        :param account_id: Account identifier owning the summary history.
        :type account_id: str
        :param query: Optional substring filter matched against markdown and preset name.
        :type query: str | None
        :param preset_id: Optional preset UUID filter.
        :type preset_id: str | None
        :param pinned: Optional pin state filter.
        :type pinned: bool | None
        :param date_from: Optional inclusive lower-bound ISO 8601 timestamp on ``saved_at``.
        :type date_from: str | None
        :param date_to: Optional exclusive upper-bound ISO 8601 timestamp on ``saved_at``.
        :type date_to: str | None
        :param limit: Maximum number of entries to return (1 to 100).
        :type limit: int
        :param offset: Zero-based pagination offset into matching entries.
        :type offset: int
        :return: Paginated dictionary containing ``'items'``, ``'total'``, ``'limit'``, and ``'offset'``.
        :rtype: dict[str, object]
        :raises ValueError: If ``limit``, ``query``, ``pinned``, or the date range is invalid.
        """
        values = dict(a=_account(account_id), limit=_int(limit, 'limit', 100), offset=_int(offset, 'offset', 100000))
        if not limit:
            raise ValueError('Invalid limit')
        where = ['e.account_id=:a']
        if query is not None:
            if not isinstance(query, str) or len(query) > 256 or '\x00' in query:
                raise ValueError('Invalid query')
            values['query'] = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            where.append("(g.markdown LIKE :query ESCAPE '\\' OR json_extract(e.preset_snapshot,'$.name') LIKE :query ESCAPE '\\')")
        if preset_id is not None:
            values['preset'] = _uuid(preset_id)
            where.append('e.preset_id=:preset')
        if pinned is not None:
            if type(pinned) is not bool:
                raise ValueError('Invalid pinned')
            values['pinned'] = int(pinned)
            where.append('e.pinned=:pinned')
        for key, date, operator in [('date_from', date_from, '>='), ('date_to', date_to, '<')]:
            if date is not None:
                values[key] = _date(date, key)
                where.append(f'e.saved_at{operator}:{key}')
        if date_from and date_to and values['date_from'] > values['date_to']:
            raise ValueError('Invalid date range')
        base = ' FROM summary_entries e JOIN summary_generations g ON g.account_id=e.account_id AND g.id=e.generation_id WHERE ' + ' AND '.join(where)
        with self.store.engine.connect() as conn:
            conn.exec_driver_sql('BEGIN')
            total = conn.execute(text('SELECT count(*)' + base), values).scalar()
            ids = conn.execute(text('SELECT e.id' + base + ' ORDER BY e.saved_at DESC,e.id DESC LIMIT :limit OFFSET :offset'), values).scalars().all()
            return dict(items=[self._entry(conn, account_id, eid) for eid in ids], total=total, limit=limit, offset=offset)

    def _mutate_entry(self, account_id, entry_id, sql, **values):
        """Apply an update statement to an existing summary entry and return it.

        :param account_id: Account identifier owning the history entry.
        :type account_id: str
        :param entry_id: UUID of the summary history entry.
        :type entry_id: str
        :param sql: SQL ``UPDATE summary_entries SET ...`` clause prefix before ``WHERE``.
        :type sql: str
        :param values: Named SQL bind parameters for the update statement.
        :type values: object
        :return: Updated and hydrated summary history entry dictionary.
        :rtype: dict[str, object]
        :raises SummaryNotFound: If no history entry with ``entry_id`` exists for the account.
        """
        account_id, entry_id = _account(account_id), _uuid(entry_id)
        with self.store.writing() as conn:
            if not self._entry(conn, account_id, entry_id):
                raise SummaryNotFound('History entry not found')
            conn.execute(text(sql + ' WHERE account_id=:a AND id=:id'), dict(a=account_id, id=entry_id, **values))
            return self._entry(conn, account_id, entry_id)

    def set_pin(self, account_id, entry_id, pinned):
        """Pin or unpin a summary history entry.

        :param account_id: Account identifier owning the history entry.
        :type account_id: str
        :param entry_id: UUID of the summary history entry.
        :type entry_id: str
        :param pinned: Target pin flag (``True`` to pin, ``False`` to unpin).
        :type pinned: bool
        :return: Updated and hydrated summary history entry dictionary.
        :rtype: dict[str, object]
        :raises ValueError: If ``pinned`` is not a strict ``bool``.
        """
        if type(pinned) is not bool:
            raise ValueError('Invalid pinned')
        return self._mutate_entry(account_id, entry_id, 'UPDATE summary_entries SET pinned=:pinned', pinned=int(pinned))

    def record_reuse(self, account_id, entry_id):
        """Increment the reuse counter and timestamp for a summary history entry.

        :param account_id: Account identifier owning the history entry.
        :type account_id: str
        :param entry_id: UUID of the summary history entry.
        :type entry_id: str
        :return: Updated and hydrated summary history entry dictionary.
        :rtype: dict[str, object]
        """
        return self._mutate_entry(account_id, entry_id, 'UPDATE summary_entries SET reuse_count=reuse_count+1,last_reused_at=:at', at=_now())

    def get_retention(self, account_id):
        """Return the retention period and revision for an account.

        :param account_id: Account identifier to query.
        :type account_id: str
        :return: Dictionary containing ``'retention_days'`` and ``'revision'``.
        :rtype: dict[str, int]
        """
        account_id = _account(account_id)
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            state = self._one(conn, 'SELECT * FROM summary_state WHERE account_id=:a', a=account_id)
            return dict(retention_days=state['retention_days'], revision=state['retention_revision'])

    def _policy(self, conn, account, days, revision):
        """Validate retention policy inputs and check optimistic revision.

        :param conn: Active writable SQLAlchemy connection.
        :type conn: sqlalchemy.Connection
        :param account: Validated Pushbullet account identifier.
        :type account: str
        :param days: Retention window in days (``7``, ``30``, ``90``, or ``365``).
        :type days: int
        :param revision: Expected ``retention_revision`` counter.
        :type revision: int
        :return: Current ``summary_state`` row dictionary for the account.
        :rtype: dict[str, object]
        :raises ValueError: If ``days`` is not a strict ``int`` in ``(7, 30, 90, 365)``.
        :raises SummaryConflict: If the stored ``retention_revision`` differs from ``revision``.
        """
        if type(days) is not int or days not in (7, 30, 90, 365):
            raise ValueError('Invalid retention_days')
        _int(revision, 'expected_revision')
        self._seed(conn, account)
        state = self._one(conn, 'SELECT * FROM summary_state WHERE account_id=:a', a=account)
        if state['retention_revision'] != revision:
            raise SummaryConflict('Retention policy changed')
        return state

    @staticmethod
    def _cutoff(days, now):
        """Compute the ISO 8601 retention cutoff timestamp.

        :param days: Retention duration in days to subtract from ``now``.
        :type days: int
        :param now: Optional ISO 8601 reference timestamp; defaults to the current UTC time.
        :type now: str | None
        :return: UTC ISO 8601 cutoff timestamp string.
        :rtype: str
        """
        stamp = _date(now or _now(), 'now')
        return (datetime.fromisoformat(stamp) - timedelta(days=days)).isoformat()

    def preview_retention(self, account_id, retention_days, expected_revision, now=None):
        """Count unpinned history entries that would be removed by a retention policy.

        :param account_id: Account identifier to evaluate.
        :type account_id: str
        :param retention_days: Candidate retention duration in days (``7``, ``30``, ``90``, or ``365``).
        :type retention_days: int
        :param expected_revision: Expected ``retention_revision`` counter.
        :type expected_revision: int
        :param now: Optional ISO 8601 reference timestamp overriding the current UTC time.
        :type now: str | None
        :return: Dictionary containing ``'retention_days'``, ``'revision'``, and ``'affected_count'``.
        :rtype: dict[str, int]
        """
        account_id = _account(account_id)
        with self.store.writing() as conn:
            self._policy(conn, account_id, retention_days, expected_revision)
            count = conn.execute(text('SELECT count(*) FROM summary_entries WHERE account_id=:a AND pinned=0 AND saved_at<:cutoff'), dict(a=account_id, cutoff=self._cutoff(retention_days, now))).scalar()
            return dict(retention_days=retention_days, revision=expected_revision, affected_count=count)

    def _prune(self, conn, account, days, now):
        """Delete expired unpinned summary entries and unreferenced generations.

        :param conn: Active writable SQLAlchemy connection.
        :type conn: sqlalchemy.Connection
        :param account: Validated Pushbullet account identifier.
        :type account: str
        :param days: Retention duration in days.
        :type days: int
        :param now: Optional ISO 8601 reference timestamp overriding the current UTC time.
        :type now: str | None
        :return: Number of expired unpinned summary entries deleted.
        :rtype: int
        """
        result = conn.execute(text('DELETE FROM summary_entries WHERE account_id=:a AND pinned=0 AND saved_at<:cutoff'), dict(a=account, cutoff=self._cutoff(days, now)))
        conn.execute(text('DELETE FROM summary_generations WHERE account_id=:a AND NOT EXISTS (SELECT 1 FROM summary_entries e WHERE e.account_id=summary_generations.account_id AND e.generation_id=summary_generations.id)'), dict(a=account))
        return result.rowcount

    def set_retention(self, account_id, retention_days, expected_revision, confirmed_retention_days, now=None):
        """Update an account's retention policy and prune expired entries.

        :param account_id: Account identifier owning the retention policy.
        :type account_id: str
        :param retention_days: Target retention duration in days (``7``, ``30``, ``90``, or ``365``).
        :type retention_days: int
        :param expected_revision: Expected ``retention_revision`` counter.
        :type expected_revision: int
        :param confirmed_retention_days: Explicit confirmation integer matching ``retention_days``.
        :type confirmed_retention_days: int
        :param now: Optional ISO 8601 reference timestamp overriding the current UTC time.
        :type now: str | None
        :return: Dictionary containing ``'retention_days'``, updated ``'revision'``, and ``'removed_count'``.
        :rtype: dict[str, int]
        :raises ValueError: If ``confirmed_retention_days`` is not a strict ``int`` matching ``retention_days``.
        """
        account_id = _account(account_id)
        if type(confirmed_retention_days) is not int or confirmed_retention_days != retention_days:
            raise ValueError('Explicit retention confirmation required')
        with self.store.writing() as conn:
            state = self._policy(conn, account_id, retention_days, expected_revision)
            changed = state['retention_days'] != retention_days
            conn.execute(text('UPDATE summary_state SET retention_days=:days,retention_revision=retention_revision+:changed WHERE account_id=:a'), dict(days=retention_days, changed=int(changed), a=account_id))
            removed = self._prune(conn, account_id, retention_days, now)
            return dict(retention_days=retention_days, revision=expected_revision+int(changed), removed_count=removed)

    def prune(self, account_id, now=None):
        """Prune expired unpinned summary history according to the account policy.

        :param account_id: Account identifier whose summary history should be pruned.
        :type account_id: str
        :param now: Optional ISO 8601 reference timestamp overriding the current UTC time.
        :type now: str | None
        :return: Number of expired unpinned summary entries deleted.
        :rtype: int
        """
        account_id = _account(account_id)
        with self.store.writing() as conn:
            self._seed(conn, account_id)
            state = self._one(conn, 'SELECT * FROM summary_state WHERE account_id=:a', a=account_id)
            return self._prune(conn, account_id, state['retention_days'], now)
