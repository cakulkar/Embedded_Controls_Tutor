"""Service registry + composition root."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecsim.app.config import Settings
from ecsim.app.events import EventBus
from ecsim.db import migrate, open_db
from ecsim.ecu.lab import EcuLab
from ecsim.learning.competency import CompetencyService
from ecsim.learning.content import Catalog, load_catalog, sync_catalog
from ecsim.learning.engine import LearningEngine
from ecsim.learning.graders import default_graders


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    def register(self, name: str, service: Any) -> None:
        if name in self._services:
            raise KeyError(f"service already registered: {name}")
        self._services[name] = service

    def get(self, name: str) -> Any:
        return self._services[name]

    def names(self) -> list[str]:
        return sorted(self._services)


@dataclass
class AppContext:
    settings: Settings
    conn: sqlite3.Connection
    bus: EventBus
    catalog: Catalog
    learning: LearningEngine
    competency: CompetencyService
    registry: ServiceRegistry
    ecu_lab: EcuLab

    def close(self) -> None:
        self.conn.close()


def bootstrap(home: Path | str | None = None, db_path: str | None = None) -> AppContext:
    """Build the whole application. db_path=':memory:' for tests."""
    settings = Settings.load(Path(home) if home else None)
    conn = open_db(db_path or settings.db_path)
    migrate(conn)
    catalog = load_catalog(settings.content_dir)
    sync_catalog(conn, catalog)
    bus = EventBus()
    graders = default_graders(settings.c_compiler, settings.c_timeout_s)
    learning = LearningEngine(conn, catalog, graders, bus)
    competency = CompetencyService(conn, catalog)
    ecu_lab = EcuLab(conn, settings.c_compiler)
    reg = ServiceRegistry()
    for name, svc in (("settings", settings), ("bus", bus), ("catalog", catalog),
                      ("learning", learning), ("competency", competency), ("ecu_lab", ecu_lab)):
        reg.register(name, svc)
    return AppContext(settings, conn, bus, catalog, learning, competency, reg, ecu_lab)
