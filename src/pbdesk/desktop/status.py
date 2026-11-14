"""Connection presentation, independent of widgets and polling frequency."""
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class StatusView:
    label: str
    tone: str
    diagnostics: str
    next_update: float | None = None


def merge_capabilities(previous, incoming):
    """Partial snapshots must not erase known sync, feature or encryption state."""
    merged = dict(previous)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


class DesktopStatus:
    def __init__(self, clock=time.monotonic, delay=3.0):
        self.clock, self.delay = clock, delay
        self.reset()

    def reset(self):
        self.capabilities = {}
        self.core = 'starting'
        self.core_detail = 'Starting private core'
        self.transients = {}
        self.stable = ('Checking updates', 'neutral')

    def snapshot(self, capabilities):
        self.capabilities = merge_capabilities(self.capabilities, capabilities)
        self.core, self.core_detail = 'available', 'Available'

    def connection(self, message):
        lowered = message.casefold()
        self.core_detail = message
        if 'disconnected' in lowered or 'unavailable' in lowered:
            self.core = 'unavailable'
        elif lowered.startswith('connected'):
            self.core = 'available'
        elif lowered.startswith('starting'):
            self.core = 'starting'

    def failed(self, message):
        # CoreWorker.failed is reserved for startup/identity/snapshot failure;
        # ordinary command errors arrive through result and never call this.
        self.core, self.core_detail = 'unavailable', message

    def view(self, format_stamp=str):
        now, cap = self.clock(), self.capabilities
        provider = str(cap.get('provider_state', 'unknown'))
        encryption = str(cap.get('encryption_state', 'unknown'))
        try:
            pending = max(0, int(cap.get('encrypted_pending', 0) or 0))
        except (TypeError, ValueError):
            pending = 0
        sync = cap.get('push_sync') or {}
        sync_state = str(sync.get('state', 'unknown'))
        detail = (f'Local core: {self.core_detail}\n'
                  f'Provider stream: {provider}\n'
                  f'Push synchronization: {sync_state}\n'
                  f'Last push synchronization: {format_stamp(sync.get("last_success_at"))}\n'
                  f'Incoming encryption / key readiness: {encryption}\n'
                  f'{pending} encrypted items waiting')
        if sync.get('error'):
            detail += '\nPush synchronization detail: ' + str(sync['error'])
        active = set()
        if provider in {'connecting', 'reconnecting', 'refreshing', 'device_refresh_failed'}:
            active.add('provider')
        if sync_state in {'refreshing', 'queued', 'syncing'}:
            active.add('sync')
        self.transients = {key: value for key, value in self.transients.items() if key in active}
        for key in active:
            self.transients.setdefault(key, now)
        deadline = min((start + self.delay for start in self.transients.values() if start + self.delay > now), default=None)
        if self.core == 'unavailable':
            label, tone = 'Core unavailable', 'error'
        elif self.core == 'starting':
            label, tone = 'Starting core', 'neutral'
        elif provider in {'unauthorized', 'locked', 'unconfigured'}:
            label, tone = 'Credentials needed', 'error'
        elif provider == 'rate_limited' or sync_state == 'rate_limited':
            label, tone = 'Rate limited', 'warn'
        elif encryption == 'error':
            label, tone = 'Decryption error', 'error'
        elif pending and encryption not in {'ready', 'key_ready', 'verified'}:
            label, tone = 'Encryption locked', 'warn'
        elif pending:
            label, tone = 'Encrypted backlog', 'warn'
        elif sync_state == 'error' or sync.get('error'):
            label, tone = 'Sync needs attention', 'warn'
        elif active:
            matured = {key for key, start in self.transients.items() if now - start >= self.delay}
            if 'provider' in matured:
                label, tone = 'Reconnecting…', 'warn'
            elif 'sync' in matured:
                label, tone = 'Refreshing…', 'neutral'
            else:
                label, tone = self.stable
        elif provider == 'connected':
            label, tone = 'Live updates', 'positive'
            self.stable = (label, tone)
        elif provider == 'demo':
            label, tone = 'Demo data', 'neutral'
            self.stable = (label, tone)
        elif provider in {'error', 'failed'}:
            label, tone = 'Updates unavailable', 'error'
        else:
            label, tone = 'Checking updates', 'neutral'
            self.stable = (label, tone)
        return StatusView(label, tone, detail, deadline)
