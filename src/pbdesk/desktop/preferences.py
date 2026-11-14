"""Small, shared local UI preferences; never store account credentials here."""
import json
import os
from pathlib import Path
import tempfile
import threading

from pbdesk.config import Settings


_lock = threading.RLock()


def preference_path():
    return Settings.from_env().state_dir / 'ui-preferences.json'


def read_preferences():
    """Return a fresh mapping, tolerating absent or malformed preference files."""
    with _lock:
        try:
            value = json.loads(preference_path().read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}


def update_preferences(changes):
    """Merge a write atomically, preserving unrelated and future UI fields."""
    with _lock:
        value = read_preferences()
        value.update(changes)
        path = preference_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=path.parent, prefix='.ui-preferences-',
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(value) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return value
