"""Virtual ECU: runs a compiled application on a simulated target, 1 ms per tick.

Execution model (documented so learners know exactly what is and isn't simulated):

* **Functional:** task code runs at its release instant (logical-execution-time
  style). Peripherals behave like registers sampled/updated between task calls.
* **Timing:** a fixed-priority *preemptive* scheduler is simulated in microseconds
  using each task's modelled WCET (from the target profile, or a fault injection).
  If a task is still running when its next activation arrives, that activation is
  dropped (OSEK-style activation limit of 1) and an OS deadline miss is recorded.
  A dropped activation's code is not executed.
* **Not simulated:** instruction-level timing of the learner's code, caches,
  interrupts preempting tasks mid-function, real memory protection.
* **Reset:** the application library is reloaded from a fresh copy, so every
  static/global variable starts from its C initial value, as after a real reset.
  Diagnostic memory (DEM) is kept, like non-volatile memory.
"""
from __future__ import annotations

import ctypes
import itertools
import os
import random
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ecsim.ecu.io import ABI_VERSION, ADC_CHANNELS, DEM_EVENTS, DEM_NEVER, DIO_CHANNELS, EcuIo, PWM_DUTY_MAX
from ecsim.ecu.profiles import TargetProfile

HANG_US = 10**12
RESET_POWER_ON, RESET_WATCHDOG, RESET_SOFTWARE = 0, 1, 2
_COPY_IDS = itertools.count(1)  # unique file per load: Windows locks loaded DLLs
RESET_NAMES = {RESET_POWER_ON: "power-on", RESET_WATCHDOG: "watchdog", RESET_SOFTWARE: "software"}


@dataclass
class AdcFault:
    mode: str            # offset | stuck | open | short_supply | gain
    value: float = 0.0

    def apply(self, volts: float, vref: float) -> float:
        if self.mode == "offset":
            return volts + self.value
        if self.mode == "gain":
            return volts * self.value
        if self.mode == "stuck":
            return self.value
        if self.mode == "open":
            return 0.0
        if self.mode == "short_supply":
            return vref
        raise ValueError(f"unknown ADC fault mode {self.mode}")


@dataclass
class ResetEvent:
    time_ms: int
    reason: int
    detail: str

    @property
    def reason_name(self) -> str:
        return RESET_NAMES.get(self.reason, str(self.reason))


@dataclass
class DemRecord:
    time_ms: int
    event_id: int
    status: int          # 0 passed, 1 failed


@dataclass
class DeadlineMiss:
    time_ms: int
    task: str


@dataclass
class _TaskState:
    name: str
    period_ms: int
    wcet_us: int
    priority: int
    fn: Callable[[], None] | None = None
    remaining_us: int = 0
    activations: int = 0
    dropped: int = 0


def _unload(lib: ctypes.CDLL) -> None:
    import _ctypes  # CPython implementation detail; available on all CPython builds
    handle = lib._handle
    if sys.platform.startswith("win"):
        _ctypes.FreeLibrary(handle)
    else:
        _ctypes.dlclose(handle)


class VirtualEcu:
    def __init__(self, lib_path: Path, profile: TargetProfile, seed: int = 0) -> None:
        self.lib_path = Path(lib_path)
        self.profile = profile
        self.rng = random.Random(seed)
        self.time_ms = 0
        self.analog_in = [0.0] * ADC_CHANNELS
        self.adc_noise_v = [0.0] * ADC_CHANNELS
        self.adc_faults: dict[int, AdcFault] = {}
        self.dio_in = [0] * DIO_CHANNELS
        self.wcet_override: dict[str, int] = {}
        self.clear_overrides_on_reset = True
        self.resets: list[ResetEvent] = []
        self.dem_log: list[DemRecord] = []
        self.dem_status: dict[int, int] = {}
        self.deadline_misses: list[DeadlineMiss] = []
        self.busy_us = 0
        self.elapsed_us = 0
        self.wdg_services = 0
        self._lib: ctypes.CDLL | None = None
        self.io: EcuIo | None = None
        self._generation = 0
        self._startup_left = 0
        self._last_wdg_count = 0
        self._last_service_ms = 0
        self._last_dem_reports = 0
        self._dem_seq_seen = [0] * DEM_EVENTS
        self.tasks = [_TaskState(t.name, t.period_ms, t.wcet_us, t.priority) for t in profile.tasks]
        self.tasks.sort(key=lambda t: -t.priority)
        self._boot(RESET_POWER_ON)

    # ---- library lifecycle ----------------------------------------------
    def _load(self) -> None:
        self._generation += 1
        copy = self.lib_path.with_name(
            f"{self.lib_path.stem}_{os.getpid()}_{next(_COPY_IDS)}{self.lib_path.suffix}")
        shutil.copyfile(self.lib_path, copy)
        lib = ctypes.CDLL(str(copy))
        self._lib_copy = copy
        lib.ecu_io_size.restype = ctypes.c_uint32
        if lib.ecu_io_size() != ctypes.sizeof(EcuIo):
            raise RuntimeError("HAL ABI mismatch between hal.c and io.py")
        self._lib = lib
        self.io = EcuIo.in_dll(lib, "ecu_io")
        try:
            self._app_init = lib.app_init
        except AttributeError:
            raise RuntimeError("application does not define app_init()") from None
        self._app_init.restype = None
        for t in self.tasks:
            try:
                fn = getattr(lib, t.name)
                fn.restype = None
                t.fn = fn
            except AttributeError:
                t.fn = None

    def close(self) -> None:
        if self._lib is not None:
            self.io = None
            for t in self.tasks:
                t.fn = None
            self._app_init = None
            _unload(self._lib)
            self._lib = None
            try:
                self._lib_copy.unlink()
            except OSError:
                pass  # best effort; the temporary directory is removed later anyway

    def __enter__(self) -> "VirtualEcu":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _boot(self, reason: int) -> None:
        self._load()
        io = self.io
        io.abi_version = ABI_VERSION
        io.time_ms = self.time_ms
        io.reset_reason = reason
        io.adc_bits = self.profile.adc_bits
        io.adc_vref = self.profile.adc_vref
        for i in range(DEM_EVENTS):
            io.dem_status[i] = DEM_NEVER
        self._last_wdg_count = 0
        self._last_dem_reports = 0
        self._dem_seq_seen = [0] * DEM_EVENTS
        for t in self.tasks:
            t.remaining_us = 0
        self._sample_inputs()
        self._app_init()
        self._collect_dem()
        self._last_service_ms = self.time_ms
        self._first_service = True   # the window does not apply to the first service after boot

    def _reset(self, reason: int, detail: str) -> None:
        self.resets.append(ResetEvent(self.time_ms, reason, detail))
        self.close()
        if self.clear_overrides_on_reset:
            self.wcet_override.clear()
        self._pending_reason = reason
        self._startup_left = self.profile.reset_startup_ms

    # ---- inputs / outputs --------------------------------------------------
    @property
    def in_reset(self) -> bool:
        return self._lib is None

    def pwm(self, ch: int) -> int:
        return 0 if self.io is None else int(self.io.pwm[ch])

    def duty(self, ch: int) -> float:
        return self.pwm(ch) / PWM_DUTY_MAX

    def dio_out(self, ch: int) -> int:
        return 0 if self.io is None else int(self.io.dio_out[ch])

    def pin_volts(self, ch: int) -> float:
        """Voltage at the ADC pin after injected faults, without noise (for traces)."""
        v = self.analog_in[ch]
        f = self.adc_faults.get(ch)
        return f.apply(v, self.profile.adc_vref) if f else v

    def adc_volts(self, ch: int) -> float:
        """Voltage seen by the converter: pin voltage plus noise."""
        v = self.pin_volts(ch)
        if self.adc_noise_v[ch] > 0:
            v += self.rng.gauss(0.0, self.adc_noise_v[ch])
        return v

    def _sample_inputs(self) -> None:
        io = self.io
        full = 1 << self.profile.adc_bits
        vref = self.profile.adc_vref
        for ch in range(ADC_CHANNELS):
            v = min(max(self.adc_volts(ch), 0.0), vref)
            io.adc[ch] = min(int(round(v / vref * full)), full - 1)
        for ch in range(DIO_CHANNELS):
            io.dio_in[ch] = 1 if self.dio_in[ch] else 0
        io.time_ms = self.time_ms

    # ---- diagnostics -------------------------------------------------------
    def _collect_dem(self) -> None:
        io = self.io
        if io.dem_reports == self._last_dem_reports:
            return
        new = []
        for eid in range(DEM_EVENTS):
            seq = io.dem_seq[eid]
            if seq != self._dem_seq_seen[eid]:
                self._dem_seq_seen[eid] = seq
                new.append((seq, eid, int(io.dem_status[eid])))
        for _, eid, st in sorted(new):
            if self.dem_status.get(eid) != st:
                self.dem_log.append(DemRecord(self.time_ms, eid, st))
            self.dem_status[eid] = st
        self._last_dem_reports = io.dem_reports

    def dem_first_failed(self, event_id: int, after_ms: int = 0) -> int | None:
        for r in self.dem_log:
            if r.event_id == event_id and r.status == 1 and r.time_ms >= after_ms:
                return r.time_ms
        return None

    # ---- main step ---------------------------------------------------------
    def tick(self) -> None:
        """Advance simulated time by 1 ms."""
        self.time_ms += 1
        self.elapsed_us += 1000
        if self._lib is None:
            self._startup_left -= 1
            if self._startup_left <= 0:
                self._boot(self._pending_reason)
            return
        self._sample_inputs()

        # Timing model: releases, activation limit, preemptive CPU budget.
        to_run: list[_TaskState] = []
        for t in self.tasks:
            if t.fn is None or self.time_ms % t.period_ms != 0:
                continue
            if t.remaining_us > 0:
                t.dropped += 1
                self.deadline_misses.append(DeadlineMiss(self.time_ms, t.name))
                continue
            t.activations += 1
            t.remaining_us = self.wcet_override.get(t.name, t.wcet_us)
            to_run.append(t)
        budget = 1000
        for t in self.tasks:  # highest priority first
            if budget <= 0:
                break
            if t.remaining_us > 0:
                used = min(t.remaining_us, budget)
                t.remaining_us -= used
                budget -= used
                self.busy_us += used

        # Functional execution of accepted activations, priority order.
        for t in to_run:
            t.fn()
            if self.io.sw_reset_request:
                break
        self._collect_dem()

        # Watchdog supervision.
        wd = self.profile.watchdog
        count = self.io.wdg_triggers
        new = count - self._last_wdg_count
        if new > 0:
            self._last_wdg_count = count
            for i in range(new):
                interval = self.time_ms - self._last_service_ms if i == 0 else 0
                if wd.window_min_ms and interval < wd.window_min_ms and not self._first_service:
                    self._reset(RESET_WATCHDOG,
                                f"{wd.name}: serviced {interval} ms after previous service "
                                f"(window opens at {wd.window_min_ms} ms)")
                    return
                self.wdg_services += 1
                self._first_service = False
                self._last_service_ms = self.time_ms
        if self.time_ms - self._last_service_ms >= wd.timeout_ms:
            self._reset(RESET_WATCHDOG, f"{wd.name}: not serviced for {wd.timeout_ms} ms")
            return
        if self.io.sw_reset_request:
            self._reset(RESET_SOFTWARE, "Mcu_PerformReset() called")

    @property
    def cpu_load(self) -> float:
        return self.busy_us / self.elapsed_us if self.elapsed_us else 0.0

    def task_stats(self) -> list[dict]:
        return [{"task": t.name, "period_ms": t.period_ms, "wcet_us": t.wcet_us,
                 "present": t.fn is not None or t.activations > 0,
                 "activations": t.activations, "dropped": t.dropped} for t in self.tasks]
