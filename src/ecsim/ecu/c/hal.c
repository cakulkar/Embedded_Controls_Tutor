/*
 * hal.c - host side of the ECSim virtual ECU ABI.
 *
 * All peripheral state lives in one exported struct, `ecu_io`, which the
 * Python simulator reads and writes between task activations (like registers).
 * Field order and types MUST match ecsim/ecu/io.py (checked via ecu_io_size()).
 */
#include "ecu_hal.h"

typedef struct
{
    uint32_t abi_version;
    uint32_t time_ms;
    uint32_t reset_reason;
    uint32_t sw_reset_request;
    uint32_t wdg_triggers;          /* incremented by Wdg_Trigger() */
    uint32_t dem_reports;           /* total reports, for change detection */
    uint32_t adc_bits;
    float    adc_vref;
    uint16_t adc[ECU_ADC_CHANNELS];
    uint16_t pwm[ECU_PWM_CHANNELS];
    uint8_t  dio_in[ECU_DIO_CHANNELS];
    uint8_t  dio_out[ECU_DIO_CHANNELS];
    uint8_t  dem_status[ECU_DEM_EVENTS];  /* 0xFF = never reported */
    uint32_t dem_seq[ECU_DEM_EVENTS];     /* report sequence of last report */
} ecu_io_t;

ecu_io_t ecu_io;

uint32_t ecu_io_size(void)
{
    return (uint32_t)sizeof(ecu_io_t);
}

uint16_t Adc_ReadChannel(uint8_t channel)
{
    return (channel < ECU_ADC_CHANNELS) ? ecu_io.adc[channel] : 0u;
}

uint8_t Adc_GetResolutionBits(void)
{
    return (uint8_t)ecu_io.adc_bits;
}

float Adc_GetReferenceVoltage(void)
{
    return ecu_io.adc_vref;
}

void Pwm_SetDutyCycle(uint8_t channel, uint16_t duty)
{
    if (channel < ECU_PWM_CHANNELS)
    {
        ecu_io.pwm[channel] = (duty > PWM_DUTY_MAX) ? (uint16_t)PWM_DUTY_MAX : duty;
    }
}

uint8_t Dio_ReadChannel(uint8_t channel)
{
    return (channel < ECU_DIO_CHANNELS) ? ecu_io.dio_in[channel] : 0u;
}

void Dio_WriteChannel(uint8_t channel, uint8_t level)
{
    if (channel < ECU_DIO_CHANNELS)
    {
        ecu_io.dio_out[channel] = (level != 0u) ? 1u : 0u;
    }
}

void Wdg_Trigger(void)
{
    ecu_io.wdg_triggers++;
}

void Dem_SetEventStatus(uint16_t event_id, uint8_t status)
{
    if (event_id < ECU_DEM_EVENTS)
    {
        ecu_io.dem_reports++;
        ecu_io.dem_status[event_id] = (status != 0u) ? 1u : 0u;
        ecu_io.dem_seq[event_id] = ecu_io.dem_reports;
    }
}

uint32_t Os_GetTimeMs(void)
{
    return ecu_io.time_ms;
}

Mcu_ResetType Mcu_GetResetReason(void)
{
    return (Mcu_ResetType)ecu_io.reset_reason;
}

void Mcu_PerformReset(void)
{
    ecu_io.sw_reset_request = 1u;
}
