"""Foreground and service mode share this advisory kernel lock."""
import fcntl
import os
from pbdesk.credentials import private_dir

class AlreadyRunning(RuntimeError):
    """Error raised when another core instance already holds the state lock."""
    pass

class InstanceLock:
    """Advisory file-lock guard ensuring single-instance ownership of the state directory."""

    def __init__(self, settings):
        """Acquire an exclusive non-blocking advisory lock on the core lock file.

        :param settings: Application settings pointing to the state directory.
        :type settings: pbdesk.config.Settings
        :raises AlreadyRunning: If another pbdesk core already holds the state directory lock.
        """
        self.fd = os.open(private_dir(settings) / "core.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise AlreadyRunning("A pbdesk core already owns this state directory") from None

    def close(self):
        """Close and release the underlying lock file descriptor if still open."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
