"""Foreground and service mode share this advisory kernel lock."""
import fcntl
import os
from pbdesk.credentials import private_dir

class AlreadyRunning(RuntimeError):
    pass

class InstanceLock:
    def __init__(self, settings):
        self.fd = os.open(private_dir(settings) / "core.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise AlreadyRunning("A pbdesk core already owns this state directory") from None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
