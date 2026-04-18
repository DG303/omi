/*
 * T5838 AAD + VAD gate for Omi (software-only mode)
 *
 * Monitors WAKE pin (P1.2) via GPIO ISR, runs all VAD state machine
 * logic, and manages SD card suspend/resume in a background thread.
 */

#ifndef AAD_H
#define AAD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/**
 * @brief Start AAD handler: configure WAKE pin ISR and spawn thread.
 *
 * Call once after mic_start().
 *
 * @return 0 on success, negative errno on failure
 */
int aad_start(void);

/**
 * @brief Process a mic buffer through the VAD gate.
 *
 * Called from the mic callback.  Handles debounce, low-power mode
 * switching, pre-roll buffering, and SD suspend/resume.
 *
 * @param buffer       Raw PCM samples from the microphone
 * @param sample_count Number of samples in @p buffer
 * @return true  if the frame contains voice — caller should forward to codec
 * @return false if in VAD sleep — frame stored in pre-roll, skip codec
 */
bool aad_process_audio(int16_t *buffer, size_t sample_count);

#endif /* AAD_H */