"""Target profiles. One virtual-ECU engine, several microcontroller flavours.

Numbers are representative of each device class, not a datasheet of a specific
part number. Features marked `planned` are declared in the profile so the UI can
show them, but are not simulated yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaskConfig:
    name: str            # C symbol, e.g. "task_10ms"
    period_ms: int
    wcet_us: int         # modelled worst-case execution time on this target
    priority: int        # larger = higher (rate-monotonic by default)


@dataclass(frozen=True)
class WatchdogConfig:
    timeout_ms: int                # must be serviced at least this often
    window_min_ms: int = 0         # servicing sooner than this since last service -> reset
    name: str = "watchdog"


@dataclass(frozen=True)
class TargetProfile:
    code: str
    title: str
    family: str
    cpu_mhz: int
    cores: int
    cores_simulated: int
    flash_kb: int
    ram_kb: int
    adc_bits: int
    adc_vref: float
    watchdog: WatchdogConfig
    tasks: tuple[TaskConfig, ...]
    reset_startup_ms: int = 5
    notes: str = ""
    planned: tuple[str, ...] = field(default_factory=tuple)


_DEFAULT_TASKS_CM = (
    TaskConfig("task_1ms", 1, 120, 3),
    TaskConfig("task_10ms", 10, 900, 2),
    TaskConfig("task_100ms", 100, 2500, 1),
)

# A 300 MHz TriCore runs the same work faster than a 168 MHz Cortex-M4.
_DEFAULT_TASKS_TC = (
    TaskConfig("task_1ms", 1, 70, 3),
    TaskConfig("task_10ms", 10, 520, 2),
    TaskConfig("task_100ms", 100, 1400, 1),
)

PROFILES: dict[str, TargetProfile] = {
    "CM4_GENERIC": TargetProfile(
        code="CM4_GENERIC",
        title="Generic Cortex-M4F (STM32F4-class)",
        family="cortex_m",
        cpu_mhz=168, cores=1, cores_simulated=1,
        flash_kb=1024, ram_kb=192,
        adc_bits=12, adc_vref=5.0,
        watchdog=WatchdogConfig(timeout_ms=100, window_min_ms=0, name="independent watchdog"),
        tasks=_DEFAULT_TASKS_CM,
        notes="Single core, simple timeout watchdog. Typical body/actuator ECU class.",
    ),
    "TC3XX_LIKE": TargetProfile(
        code="TC3XX_LIKE",
        title="AURIX TC3xx-style (multi-core TriCore class)",
        family="aurix_tc3xx",
        cpu_mhz=300, cores=3, cores_simulated=1,
        flash_kb=6144, ram_kb=1024,
        adc_bits=12, adc_vref=5.0,
        watchdog=WatchdogConfig(timeout_ms=100, window_min_ms=5, name="windowed safety watchdog"),
        tasks=_DEFAULT_TASKS_TC,
        notes=("Windowed safety watchdog: servicing too early is a fault, not just too late. "
               "The application runs on core 0 only in this phase."),
        planned=("multi-core execution and inter-core communication",
                 "per-core CPU watchdogs",
                 "lockstep checker-core fault model",
                 "memory protection between cores"),
    ),
}


def get_profile(code: str) -> TargetProfile:
    try:
        return PROFILES[code]
    except KeyError:
        raise KeyError(f"unknown target profile {code!r}; available: {', '.join(PROFILES)}") from None
