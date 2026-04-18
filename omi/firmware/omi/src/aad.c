/*
 * T5838 AAD + VAD gate for Omi (software-only mode)
 *
 * Owns all voice-activity detection (VAD) state.  The main loop calls
 * aad_process_audio() from the mic callback; it decides whether
 * a frame should be forwarded to the codec (voice) or discarded (sleep).
 *
 * During VAD sleep the mic is switched to mono-left low-power mode and
 * the SD card may be suspended to save current.  A dedicated handler
 * thread manages SD lifecycle and WAKE pin signaling.
 *
 * Pin assignment (board DTS):
 *   pdm_wake_pin = P1.2   GPIO input with pull-down, rising-edge IRQ
 */

#include "aad.h"

#include <string.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>

#include "lib/core/codec.h"
#include "lib/core/config.h"
#include "lib/core/mic.h"
#include "lib/core/sd_card.h"

LOG_MODULE_REGISTER(aad, CONFIG_LOG_DEFAULT_LEVEL);

extern bool is_connected;

/* ---- DTS GPIO spec for WAKE pin ---- */
static const struct gpio_dt_spec pin_wake = GPIO_DT_SPEC_GET_OR(DT_NODELABEL(pdm_wake_pin), gpios, {0});
static struct gpio_callback wake_cb_data;

/* ---- Thread plumbing ---- */
#define AAD_THREAD_STACK_SIZE 1024
#define AAD_THREAD_PRIORITY 5

static K_THREAD_STACK_DEFINE(aad_stack, AAD_THREAD_STACK_SIZE);
static struct k_thread aad_thread_data;
static k_tid_t aad_tid;

static K_SEM_DEFINE(aad_sem, 0, 1);

/* ---- Atomic flags (ISR / cross-thread safe) ---- */
static atomic_t wake_pending = ATOMIC_INIT(0);
static atomic_t wake_consumed = ATOMIC_INIT(0);
static atomic_t sd_resume_req = ATOMIC_INIT(0);
static atomic_t sd_suspend_req = ATOMIC_INIT(0);
static atomic_t sd_suspended = ATOMIC_INIT(0);

/* ---- VAD state (only from mic callback context) ---- */
static bool vad_is_recording = false;
static bool mic_low_power_mode = false;
static uint16_t vad_voice_streak = 0;
static int64_t vad_last_voice_ms = 0;
static int64_t vad_next_status_log_ms = 0;
static uint8_t mic_low_power_skip_frames = 0;
static uint8_t mic_low_power_wake_history = 0;

/* ---- VAD pre-roll ring buffer ---- */
#define VAD_PREROLL_FRAMES 3
static int16_t vad_preroll_buffer[VAD_PREROLL_FRAMES][MIC_BUFFER_SAMPLES];
static uint8_t vad_preroll_write_index = 0;
static uint8_t vad_preroll_count = 0;

/* ---- VAD tuning ---- */
#define VAD_STATUS_LOG_INTERVAL_MS 2000
#define MIC_LOW_POWER_SKIP_FRAMES_COUNT 10
#define MIC_LOW_POWER_WAKE_THRESHOLD 180
#define MIC_LOW_POWER_WAKE_DEBOUNCE_FRAMES 2
#define MIC_LOW_POWER_WAKE_WINDOW_FRAMES 3

/* ---- VAD helpers ---- */

static uint8_t count_bits_u8(uint8_t value)
{
    uint8_t count = 0;
    while (value) {
        count += value & 0x1u;
        value >>= 1;
    }
    return count;
}

static void vad_preroll_reset(void)
{
    vad_preroll_write_index = 0;
    vad_preroll_count = 0;
}

static void vad_preroll_store(const int16_t *buffer)
{
    memcpy(vad_preroll_buffer[vad_preroll_write_index], buffer, sizeof(vad_preroll_buffer[0]));
    vad_preroll_write_index = (vad_preroll_write_index + 1) % VAD_PREROLL_FRAMES;
    if (vad_preroll_count < VAD_PREROLL_FRAMES) {
        vad_preroll_count++;
    }
}

static void vad_preroll_flush(void)
{
    if (vad_preroll_count == 0) {
        return;
    }

    uint8_t start = (vad_preroll_write_index + VAD_PREROLL_FRAMES - vad_preroll_count) % VAD_PREROLL_FRAMES;
    for (uint8_t i = 0; i < vad_preroll_count; ++i) {
        uint8_t idx = (start + i) % VAD_PREROLL_FRAMES;
        int err = codec_receive_pcm(vad_preroll_buffer[idx], MIC_BUFFER_SAMPLES);
        if (err) {
            LOG_ERR("VAD preroll flush frame %u failed: %d", i, err);
            break;
        }
    }
    LOG_INF("VAD: flushed %u pre-roll frame(s)", vad_preroll_count);
    vad_preroll_reset();
}

static uint32_t vad_average_abs_amplitude(const int16_t *buffer, size_t n)
{
    if (n == 0) {
        return 0;
    }
    uint64_t sum = 0;
    for (size_t i = 0; i < n; i++) {
        int32_t s = buffer[i];
        sum += (uint32_t) (s < 0 ? -s : s);
    }
    return (uint32_t) (sum / n);
}

/* ---- WAKE pin ISR ---- */

static void wake_pin_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    ARG_UNUSED(dev);
    ARG_UNUSED(cb);
    ARG_UNUSED(pins);

    atomic_set(&wake_pending, 1);
    k_sem_give(&aad_sem);
}

/* ---- Handler thread (SD suspend/resume) ---- */

static void aad_thread_fn(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    LOG_INF("AAD handler thread running");

    while (1) {
        k_sem_take(&aad_sem, K_MSEC(100));

        /* WAKE event from ISR */
        if (atomic_cas(&wake_pending, 1, 0)) {
            atomic_set(&wake_consumed, 1);
            LOG_INF("AAD: WAKE detected");
        }

        /* SD resume */
        if (atomic_cas(&sd_resume_req, 1, 0)) {
            if (atomic_get(&sd_suspended)) {
                int ret = app_sd_init();
                if (ret == 0) {
                    atomic_set(&sd_suspended, 0);
                    LOG_INF("AAD: SD card resumed");
                } else {
                    LOG_ERR("AAD: SD resume failed (%d)", ret);
                }
            }
        }

        /* SD suspend */
        if (atomic_cas(&sd_suspend_req, 1, 0)) {
            if (!is_connected && !atomic_get(&sd_suspended)) {
                int ret = app_sd_off();
                if (ret == 0) {
                    atomic_set(&sd_suspended, 1);
                    LOG_INF("AAD: SD card suspended");
                } else {
                    LOG_WRN("AAD: SD suspend failed (%d)", ret);
                }
            }
        }

        /* Auto-resume SD when BLE connects */
        if (is_connected && atomic_get(&sd_suspended)) {
            int ret = app_sd_init();
            if (ret == 0) {
                atomic_set(&sd_suspended, 0);
                LOG_INF("AAD: SD resumed (BLE connected)");
            } else {
                LOG_ERR("AAD: SD resume on connect failed (%d)", ret);
            }
        }
    }
}

/* ================================================================
 * Public API
 * ================================================================ */

bool aad_process_audio(int16_t *buffer, size_t sample_count)
{
    /* Check for WAKE event from ISR -> reset VAD debounce */
    if (atomic_cas(&wake_consumed, 1, 0)) {
        vad_voice_streak = 0;
        vad_last_voice_ms = k_uptime_get();
        vad_is_recording = false;
        LOG_INF("AAD: WAKE, VAD debounce reset");
    }

    uint32_t avg_abs = vad_average_abs_amplitude(buffer, sample_count);
    int64_t now_ms = k_uptime_get();
    uint32_t active_threshold = CONFIG_OMI_VAD_ABS_THRESHOLD;
    uint16_t active_debounce_frames = CONFIG_OMI_VAD_DEBOUNCE_FRAMES;

    if (mic_low_power_mode) {
        active_threshold = MIC_LOW_POWER_WAKE_THRESHOLD;
        active_debounce_frames = MIC_LOW_POWER_WAKE_DEBOUNCE_FRAMES;
    }

    /* ---- Detect voice ---- */
    bool has_voice = false;
    if (mic_low_power_mode) {
        if (mic_low_power_skip_frames > 0) {
            mic_low_power_skip_frames--;
            vad_preroll_store(buffer);
            return false;
        }
        bool wake_candidate = avg_abs >= active_threshold;
        mic_low_power_wake_history = ((mic_low_power_wake_history << 1) | (wake_candidate ? 1u : 0u)) &
                                     ((1u << MIC_LOW_POWER_WAKE_WINDOW_FRAMES) - 1u);
        has_voice = count_bits_u8(mic_low_power_wake_history) >= MIC_LOW_POWER_WAKE_DEBOUNCE_FRAMES;
    } else {
        has_voice = avg_abs >= active_threshold;
    }

    /* ---- State machine ---- */
    if (has_voice) {
        vad_last_voice_ms = now_ms;
        if (!vad_is_recording) {
            if (mic_low_power_mode) {
#ifdef CONFIG_OMI_ENABLE_OFFLINE_STORAGE
                if (atomic_get(&sd_suspended)) {
                    atomic_set(&sd_resume_req, 1);
                    k_sem_give(&aad_sem);
                }
#endif
                int mic_ret = mic_set_mode(MIC_MODE_STEREO);
                if (mic_ret == 0) {
                    mic_low_power_mode = false;
                    mic_low_power_skip_frames = 0;
                    mic_low_power_wake_history = 0;
                    LOG_INF("VAD: wake, stereo restored");
                } else {
                    LOG_ERR("VAD: stereo restore failed (%d)", mic_ret);
                }
                vad_preroll_flush();
                vad_is_recording = true;
                LOG_INF("VAD: RECORDING (avg=%u, debounce=%d)", avg_abs, active_debounce_frames);
            } else {
                vad_voice_streak++;
                if (vad_voice_streak >= active_debounce_frames) {
                    vad_preroll_flush();
                    vad_is_recording = true;
                    LOG_INF("VAD: RECORDING (avg=%u, debounce=%d)", avg_abs, active_debounce_frames);
                }
            }
        }
    } else {
        vad_voice_streak = 0;
        if (vad_is_recording) {
            int64_t silent_ms = now_ms - vad_last_voice_ms;
            if (silent_ms >= CONFIG_OMI_VAD_HOLD_MS) {
                vad_is_recording = false;
                LOG_INF("VAD: SLEEP (silent %lld ms)", silent_ms);
                if (!mic_low_power_mode) {
                    int mic_ret = mic_set_mode(MIC_MODE_MONO_LEFT);
                    if (mic_ret == 0) {
                        mic_low_power_mode = true;
                        mic_low_power_skip_frames = MIC_LOW_POWER_SKIP_FRAMES_COUNT;
                        mic_low_power_wake_history = 0;
#ifdef CONFIG_OMI_ENABLE_OFFLINE_STORAGE
                        if (!is_connected && !atomic_get(&sd_suspended)) {
                            atomic_set(&sd_suspend_req, 1);
                            k_sem_give(&aad_sem);
                        }
#endif
                        vad_preroll_reset();
                        LOG_INF("VAD: low-power (MIC1 only)");
                    } else {
                        vad_is_recording = true;
                        vad_last_voice_ms = now_ms;
                        LOG_ERR("VAD: mono switch failed (%d)", mic_ret);
                    }
                }
            }
        }
    }

    /* Periodic status log */
    if (now_ms >= vad_next_status_log_ms) {
        LOG_INF("VAD: %s (avg=%u thr=%u deb=%u hold=%d)",
                vad_is_recording ? "REC" : "SLEEP",
                avg_abs,
                active_threshold,
                active_debounce_frames,
                CONFIG_OMI_VAD_HOLD_MS);
        vad_next_status_log_ms = now_ms + VAD_STATUS_LOG_INTERVAL_MS;
    }

    if (!vad_is_recording) {
        vad_preroll_store(buffer);
        return false;
    }
    return true;
}

int aad_start(void)
{
    int ret;

    if (!gpio_is_ready_dt(&pin_wake)) {
        LOG_ERR("AAD: WAKE gpio not ready");
        return -ENODEV;
    }

    ret = gpio_pin_configure_dt(&pin_wake, GPIO_INPUT | GPIO_PULL_DOWN);
    if (ret) {
        LOG_ERR("AAD: WAKE pin config failed (%d)", ret);
        return ret;
    }

    gpio_init_callback(&wake_cb_data, wake_pin_isr, BIT(pin_wake.pin));
    ret = gpio_add_callback(pin_wake.port, &wake_cb_data);
    if (ret) {
        LOG_ERR("AAD: WAKE callback add failed (%d)", ret);
        return ret;
    }

    ret = gpio_pin_interrupt_configure_dt(&pin_wake, GPIO_INT_EDGE_RISING);
    if (ret) {
        LOG_ERR("AAD: WAKE IRQ config failed (%d)", ret);
        return ret;
    }

    aad_tid = k_thread_create(&aad_thread_data,
                              aad_stack,
                              K_THREAD_STACK_SIZEOF(aad_stack),
                              aad_thread_fn,
                              NULL,
                              NULL,
                              NULL,
                              AAD_THREAD_PRIORITY,
                              0,
                              K_NO_WAIT);
    k_thread_name_set(aad_tid, "aad");

    LOG_INF("AAD: started (WAKE=P1.%d, VAD thr=%d deb=%d hold=%d)",
            pin_wake.pin,
            CONFIG_OMI_VAD_ABS_THRESHOLD,
            CONFIG_OMI_VAD_DEBOUNCE_FRAMES,
            CONFIG_OMI_VAD_HOLD_MS);
    return 0;
}