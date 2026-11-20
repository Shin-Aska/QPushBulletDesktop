"""The only desktop notifier. Optional and independent of Qt."""
import asyncio
import shutil
import html
from pbdesk.runtime import system_environment

class Notifier:
    """Desktop notification dispatcher backed by the notify-send command."""

    def __init__(self, enabled=False):
        """Initialize the notifier with the requested enabled state.

        :param enabled: Whether desktop notifications should initially be enabled.
        :type enabled: bool
        """
        self.configure(enabled)

    def configure(self, enabled):
        """Update the requested state and refresh notify-send command availability.

        :param enabled: Whether desktop notifications are requested.
        :type enabled: bool
        """
        self.requested = bool(enabled)
        self.command = shutil.which("notify-send")
        self.available = self.command is not None
        self.enabled = self.requested and self.available
        self.outcome = "disabled" if not self.requested else "unavailable" if not self.available else "ready"

    async def notify(self, item):
        """Send an escaped desktop notification for the given item if enabled.

        :param item: Notification item mapping containing ``title`` and ``body`` fields.
        :type item: collections.abc.Mapping[str, Any]
        :return: ``True`` if ``notify-send`` exited with status 0, otherwise ``False``.
        :rtype: bool
        """
        if not self.enabled or not self.command:
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                self.command, "--app-name=pbdesk", "--", html.escape(item["title"] or "Pushbullet"), html.escape(item["body"][:1000]),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=system_environment())
            await asyncio.wait_for(process.wait(), timeout=5)
            self.outcome = "submitted" if process.returncode == 0 else "failed"
            return process.returncode == 0
        except (OSError, TimeoutError):
            if 'process' in locals() and process.returncode is None:
                process.kill()
                await process.wait()
            self.outcome = "failed"
            return False
