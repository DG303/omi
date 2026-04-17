#ifndef MIC_H
#define MIC_H

#include <stdbool.h>
#include <stdint.h>

typedef void (*mix_handler)(int16_t *);

typedef enum {
    MIC_MODE_STEREO = 0,
    MIC_MODE_MONO_LEFT = 1,
} mic_mode_t;

/**
 * @brief Initialize the Microphone
 *
 * Initializes the Microphone
 *
 * @return 0 if successful, negative errno code if error
 */
int mic_start();
void set_mic_callback(mix_handler _callback);

void mic_off();
void mic_on();
void mic_pause();
void mic_resume();
int mic_set_mode(mic_mode_t mode);
mic_mode_t mic_get_mode(void);
void mic_set_gain(uint8_t gain_level);
#endif
