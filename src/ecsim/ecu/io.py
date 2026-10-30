"""ctypes mirror of `ecu_io_t` in c/hal.c. Layout verified at load time."""
from __future__ import annotations

import ctypes

ABI_VERSION = 1
ADC_CHANNELS = 8
PWM_CHANNELS = 4
DIO_CHANNELS = 16
DEM_EVENTS = 64
PWM_DUTY_MAX = 10000
DEM_NEVER = 0xFF


class EcuIo(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("time_ms", ctypes.c_uint32),
        ("reset_reason", ctypes.c_uint32),
        ("sw_reset_request", ctypes.c_uint32),
        ("wdg_triggers", ctypes.c_uint32),
        ("dem_reports", ctypes.c_uint32),
        ("adc_bits", ctypes.c_uint32),
        ("adc_vref", ctypes.c_float),
        ("adc", ctypes.c_uint16 * ADC_CHANNELS),
        ("pwm", ctypes.c_uint16 * PWM_CHANNELS),
        ("dio_in", ctypes.c_uint8 * DIO_CHANNELS),
        ("dio_out", ctypes.c_uint8 * DIO_CHANNELS),
        ("dem_status", ctypes.c_uint8 * DEM_EVENTS),
        ("dem_seq", ctypes.c_uint32 * DEM_EVENTS),
    ]
