"""Settings and filesystem layout. Everything lives under ECSIM_HOME (offline, local)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
CONTENT_DIR = PACKAGE_DIR / "content"
MIGRATIONS_DIR = PACKAGE_DIR / "db" / "migrations"


def default_home() -> Path:
    env = os.environ.get("ECSIM_HOME")
    return Path(env).expanduser() if env else Path.home() / ".ecsim"


@dataclass
class Settings:
    home: Path
    c_compiler: str = "gcc"
    c_timeout_s: float = 5.0
    content_dir: Path = field(default=CONTENT_DIR)

    @property
    def db_path(self) -> Path:
        return self.home / "ecsim.db"

    @property
    def workspace_dir(self) -> Path:
        return self.home / "workspace"

    @property
    def settings_file(self) -> Path:
        return self.home / "settings.json"

    @classmethod
    def load(cls, home: Path | None = None) -> "Settings":
        home = Path(home) if home else default_home()
        home.mkdir(parents=True, exist_ok=True)
        s = cls(home=home)
        if s.settings_file.exists():
            data = json.loads(s.settings_file.read_text(encoding="utf-8"))
            for key in ("c_compiler", "c_timeout_s"):
                if key in data:
                    setattr(s, key, data[key])
            if "content_dir" in data:
                s.content_dir = Path(data["content_dir"])
        s.workspace_dir.mkdir(parents=True, exist_ok=True)
        return s

    def save(self) -> None:
        data = asdict(self)
        data.pop("home")
        data["content_dir"] = str(self.content_dir)
        self.settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
