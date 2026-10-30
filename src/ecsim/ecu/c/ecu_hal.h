/*
 * ecu_hal.h - ECSim virtual ECU hardware abstraction (ABI v1)
 *
 * API names are *inspired by* AUTOSAR MCAL/BSW modules (Adc, Pwm, Dio, Wdg, Dem,
 * Mcu) so they feel familiar on the job. They are NOT AUTOSAR-conformant APIs.
 *
 * Application entry points the virtual ECU calls (define the ones you need):
 *   void app_init(void);      required; runs after every reset
 *   void task_1ms(void);      optional; highest priority
 *   void task_10ms(void);     optional
 *   void task_100ms(void);    optional; lowest priority
 */
#ifndef ECU_HAL_H
#define ECU_HAL_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define ECU_HAL_ABI_VERSION   1u
#define ECU_ADC_CHANNELS      8u
#define ECU_PWM_CHANNELS      4u
#define ECU_DIO_CHANNELS      16u
#define ECU_DEM_EVENTS        64u

#define PWM_DUTY_MAX          10000u   /* 10000 = 100.00 % */

#define DEM_EVENT_STATUS_PASSED  0u
#define DEM_EVENT_STATUS_FAILED  1u

typedef enum
{
    MCU_RESET_POWER_ON = 0,
    MCU_RESET_WATCHDOG = 1,
    MCU_RESET_SOFTWARE = 2
} Mcu_ResetType;

/* Raw conversion result in counts (resolution: Adc_GetResolutionBits()). */
uint16_t Adc_ReadChannel(uint8_t channel);
uint8_t  Adc_GetResolutionBits(void);
float    Adc_GetReferenceVoltage(void);

/* duty in 0..PWM_DUTY_MAX; larger values saturate. */
void     Pwm_SetDutyCycle(uint8_t channel, uint16_t duty);

uint8_t  Dio_ReadChannel(uint8_t channel);
void     Dio_WriteChannel(uint8_t channel, uint8_t level);

/* Service the (possibly windowed) watchdog. */
void     Wdg_Trigger(void);

/* Report a diagnostic event result; event_id < ECU_DEM_EVENTS. */
void     Dem_SetEventStatus(uint16_t event_id, uint8_t status);

uint32_t      Os_GetTimeMs(void);
Mcu_ResetType Mcu_GetResetReason(void);
void          Mcu_PerformReset(void);

#endif /* ECU_HAL_H */
