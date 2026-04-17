/*
 * T5838 AAD (Acoustic Activity Detection) driver for Omi
 *
 * Controls the T5838 MEMS microphone AAD mode:
 *   - enter_sleep(): stops PDM, forces CLK LOW, arms WAKE IRQ
 *   - exit_sleep(): releases CLK, restarts PDM, disarms WAKE IRQ
 *   - set_threshold(): THSEL pin (LOW = more sensitive)
 *
 * IMPORTANT: This driver explicitly drives PDM_EN (P1.4) HIGH so that
 * the SGM2036S LDO stays on (mic needs VDD for AAD) and the TXS0104
 * level shifter has VCCB power (needed for WAKE to reach the SoC).
 *
 * Hardware note from the schematic:
 *   - Only MIC1 is wired to WAKE and THSEL.
 *   - MIC2 only shares VDD, DATA, and CLK; its WAKE/THSEL pins are NC.
 *   - Therefore all AAD configuration and wake behavior in this driver
 *     applies to MIC1 only.
 *
 * Pin assignments (from board DTS nodelabels):
 *   pdm_en_pin    = P1.4   LDO enable + level shifter VCCB
 *   pdm_thsel_pin = P1.5   AAD threshold select
 *   pdm_wake_pin  = P1.2   WAKE interrupt (rising edge)
 *   PDM_CLK       = P1.1   forced LOW during sleep
 */

#ifndef T5838_AAD_H
#define T5838_AAD_H

#include <stdbool.h>
#include <stdint.h>

/**
 * @brief Initialize T5838 AAD hardware
 *
 * Configures PDM_EN HIGH, THSEL, WAKE input, and WAKE IRQ.
 * Must be called after mic_start().
 * @return 0 on success, negative error code on failure
 */
int t5838_aad_init(void);

/**
 * @brief Enter AAD low-power mode
 *
 * Pauses PDM, forces CLK LOW, arms WAKE rising-edge interrupt.
 * PDM_EN stays HIGH (mic needs VDD, level shifter needs VCCB).
 * @return 0 if AAD armed successfully, negative errno if arming failed
 */
int t5838_aad_enter_sleep(void);

/**
 * @brief Exit AAD mode and resume normal PDM recording
 */
void t5838_aad_exit_sleep(void);

/**
 * @brief Check if currently in AAD sleep mode
 */
bool t5838_aad_is_sleeping(void);

/**
 * @brief Set AAD threshold sensitivity
 * @param high_threshold true = less sensitive, false = more sensitive
 */
void t5838_aad_set_threshold(bool high_threshold);

/**
 * @brief Register callback for AAD wake event (called from ISR context)
 */
typedef void (*t5838_aad_wake_cb_t)(void);
void t5838_aad_set_wake_callback(t5838_aad_wake_cb_t cb);

/**
 * @brief Read WAKE pin logical value (debug)
 * @return 1 if HIGH, 0 if LOW, negative on error
 */
int t5838_aad_read_wake_pin(void);

/**
 * @brief Read WAKE pin raw physical level (debug)
 */
int t5838_aad_read_wake_pin_raw(void);

/**
 * @brief Read and clear WAKE low-level latch (nRF GPIO SENSE)
 * @return 1 if WAKE went LOW since last clear, 0 if not, negative on error
 */
int t5838_aad_read_wake_latch(void);

/** @brief Read raw P1->IN register (all port 1 input values) */
uint32_t t5838_aad_read_p1_in(void);

/** @brief Read raw P1->OUT register (all port 1 output drive values) */
uint32_t t5838_aad_read_p1_out(void);

/** @brief Get ISR fire count since last arm (debug) */
uint32_t t5838_aad_get_isr_count(void);

/** @brief Self-test: briefly drive WAKE LOW from nRF to verify ISR path */
void t5838_aad_selftest_wake_irq(void);

#endif /* T5838_AAD_H */
