"""Configuration; deliberately standard-library-only and safe for launchers."""
from dataclasses import dataclass
import os
import sys
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    state_dir: Path
    core_host: str = "127.0.0.1"
    core_port: int = 8765
    web_host: str = "127.0.0.1"
    web_port: int = 8766
    application_dir: Path | None = None

    def __post_init__(self):
        object.__setattr__(self, "state_dir", Path(self.state_dir))
        # Explicit constructors remain isolated (notably tests and embedders).
        object.__setattr__(self, "application_dir", Path(self.application_dir or self.state_dir).resolve())
        if self.core_host != "127.0.0.1" or self.web_host != "127.0.0.1":
            raise ValueError("This slice supports loopback hosting only")
        for port in (self.core_port, self.web_port):
            if not 1 <= port <= 65535:
                raise ValueError("Ports must be between 1 and 65535")
        if self.core_port == self.web_port:
            raise ValueError("Core and web ports must differ")

    @classmethod
    def from_env(cls):
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        return cls(Path(os.environ.get("PBDESK_STATE_DIR", root / "pbdesk")),
                   core_port=int(os.environ.get("PBDESK_CORE_PORT", "8765")),
                   web_port=int(os.environ.get("PBDESK_WEB_PORT", "8766")),
                   application_dir=application_directory())

    @property
    def core_url(self):
        return f"http://{self.core_host}:{self.core_port}"


def application_directory():
    """Directory of our launcher, never the system Python executable directory."""
    configured = os.environ.get("PBDESK_APPLICATION_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys,"frozen",False):
        return Path(sys.executable).resolve().parent
    launcher = Path(sys.argv[0]).expanduser()
    if launcher.name.startswith("pbdesk") and launcher.is_file():
        return launcher.absolute().parent
    # Source launch via python -m / IDE: the development launcher is this project.
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").is_file():
        return project
    raise ValueError("Set PBDESK_APPLICATION_DIR to the pbdesk application directory")
