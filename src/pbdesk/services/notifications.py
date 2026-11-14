"""The only desktop notifier. Optional and independent of Qt."""
import asyncio
import shutil
import html

class Notifier:
    def __init__(self, enabled=False):
        self.configure(enabled)

    def configure(self, enabled):
        self.requested = bool(enabled)
        self.command = shutil.which("notify-send")
        self.available = self.command is not None
        self.enabled = self.requested and self.available
        self.outcome = "disabled" if not self.requested else "unavailable" if not self.available else "ready"

    async def notify(self, item):
        if not self.enabled or not self.command:
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                self.command, "--app-name=pbdesk", "--", html.escape(item["title"] or "Pushbullet"), html.escape(item["body"][:1000]),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(process.wait(), timeout=5)
            self.outcome = "submitted" if process.returncode == 0 else "failed"
            return process.returncode == 0
        except (OSError, TimeoutError):
            if 'process' in locals() and process.returncode is None:
                process.kill()
                await process.wait()
            self.outcome = "failed"
            return False
