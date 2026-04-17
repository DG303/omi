#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/pm/device_runtime.h>
#include <zephyr/shell/shell.h>

#include "lib/core/button.h"
#include "lib/core/codec.h"
#include "lib/core/config.h"
#include "lib/core/feedback.h"
#include "lib/core/haptic.h"
#include "lib/core/led.h"
#include "lib/core/lib/battery/battery.h"
#include "lib/core/mic.h"
#ifdef CONFIG_OMI_ENABLE_MONITOR
#include "lib/core/monitor.h"
#endif
#include "lib/core/settings.h"
#include "lib/core/transport.h"
#ifdef CONFIG_OMI_ENABLE_OFFLINE_STORAGE
#include "lib/core/storage.h"
#endif
#include <hal/nrf_reset.h>

#include "imu.h"
#include "lib/core/sd_card.h"
#include "rtc.h"
#include "spi_flash.h"
#include "wdog_facade.h"
#ifdef CONFIG_OMI_ENABLE_T5838_AAD
#include "t5838_aad.h"
#endif

LOG_MODULE_REGISTER(main, CONFIG_LOG_DEFAULT_LEVEL);

#ifdef CONFIG_OMI_ENABLE_BATTERY
#define BATTERY_FULL_THRESHOLD_PERCENT 98 // 98%
extern uint8_t battery_percentage;
#endif
bool is_connected = false;
bool is_charging = false;
bool is_off = false;
bool blink_toggle = false;

#ifdef CONFIG_OMI_ENABLE_VAD_GATE
static bool vad_is_recording = false;
static bool mic_low_power_mode = false;
static uint16_t vad_voice_streak = 0; /* consecutive voice frames for debounce */
static int64_t vad_last_voice_ms = 0; /* uptime of last voice frame */
static int64_t vad_next_status_log_ms = 0;
static uint8_t mic_low_power_skip_frames = 0;
static uint8_t mic_low_power_wake_history = 0;
static uint8_t vad_preroll_write_index = 0;
static uint8_t vad_preroll_count = 0;

#define VAD_PREROLL_FRAMES 3
static int16_t vad_preroll_buffer[VAD_PREROLL_FRAMES][MIC_BUFFER_SAMPLES];

#ifdef CONFIG_OMI_ENABLE_T5838_AAD
/* Flag set by WAKE ISR to signal main loop to resume mic */
static volatile bool aad_wake_pending = false;
static int64_t aad_next_debug_log_ms = 0;
static bool aad_hw_ready = false;
static int64_t aad_sleep_started_ms = 0;
static bool aad_selftest_done = false;

#define AAD_SELFTEST_DELAY_MS 5000
#define AAD_GUARD_MS 1000    /* ignore polls for 1s after arming */
#define AAD_TIMEOUT_MS 60000 /* force resume after 60s with no wake */

static void aad_wake_callback(void)
{
    /* Called from GPIO ISR — keep minimal */
    aad_wake_pending = true;
}
#endif /* CONFIG_OMI_ENABLE_T5838_AAD */

#define VAD_STATUS_LOG_INTERVAL_MS 2000
#define MIC_LOW_POWER_SKIP_FRAMES_COUNT 10
#define MIC_LOW_POWER_WAKE_THRESHOLD 180
#define MIC_LOW_POWER_WAKE_DEBOUNCE_FRAMES 2
#define MIC_LOW_POWER_WAKE_WINDOW_FRAMES 3

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

    uint8_t start_index = (vad_preroll_write_index + VAD_PREROLL_FRAMES - vad_preroll_count) % VAD_PREROLL_FRAMES;
    for (uint8_t i = 0; i < vad_preroll_count; ++i) {
        uint8_t index = (start_index + i) % VAD_PREROLL_FRAMES;
        int err = codec_receive_pcm(vad_preroll_buffer[index], MIC_BUFFER_SAMPLES);
        if (err) {
            LOG_ERR("Failed to flush VAD pre-roll frame %u: %d", i, err);
            break;
        }
    }

    LOG_INF("VAD: flushed %u pre-roll frame(s)", vad_preroll_count);
    vad_preroll_reset();
}

static uint32_t vad_average_abs_amplitude(const int16_t *buffer, size_t sample_count)
{
    if (sample_count == 0) {
        return 0;
    }

    uint64_t sum_abs = 0;
    for (size_t i = 0; i < sample_count; i++) {
        int32_t sample = buffer[i];
        if (sample < 0) {
            sample = -sample;
        }
        sum_abs += (uint32_t) sample;
    }

    return (uint32_t) (sum_abs / sample_count);
}
#endif

static void print_reset_reason(void)
{
    uint32_t reas;

#if defined(NRF_RESET)
    reas = nrf_reset_resetreas_get(NRF_RESET);
    nrf_reset_resetreas_clear(NRF_RESET, reas);
#elif defined(NRF_RESET_S)
    reas = nrf_reset_resetreas_get(NRF_RESET_S);
    nrf_reset_resetreas_clear(NRF_RESET_S, reas);
#elif defined(NRF_RESET_NS)
    reas = nrf_reset_resetreas_get(NRF_RESET_NS);
    nrf_reset_resetreas_clear(NRF_RESET_NS, reas);
#else
    printk("Reset reason unavailable (no RESET peripheral symbol)\n");
    return;
#endif

    if (reas & NRF_RESET_RESETREAS_DOG0_MASK) {
        printk("Reset by WATCHDOG\n");
    } else if (reas & NRF_RESET_RESETREAS_NFC_MASK) {
        printk("Wake up by NFC field detect\n");
    } else if (reas & NRF_RESET_RESETREAS_RESETPIN_MASK) {
        printk("Reset by pin-reset\n");
    } else if (reas & NRF_RESET_RESETREAS_SREQ_MASK) {
        printk("Reset by soft-reset\n");
    } else if (reas & NRF_RESET_RESETREAS_LOCKUP_MASK) {
        printk("Reset by CPU LOCKUP\n");
    } else if (reas) {
        printk("Reset by a different source (0x%08X)\n", reas);
    } else {
        printk("Power-on-reset\n");
    }
}

static void codec_handler(uint8_t *data, size_t len)
{
#ifdef CONFIG_OMI_ENABLE_MONITOR
    monitor_inc_broadcast_audio();
#endif
    int err = broadcast_audio_packets(data, len);
    if (err) {
#ifdef CONFIG_OMI_ENABLE_MONITOR
        monitor_inc_broadcast_audio_failed();
#endif
    }
}

static void mic_handler(int16_t *buffer)
{
#ifdef CONFIG_OMI_ENABLE_MONITOR
    // Track total bytes processed (each sample is 2 bytes)
    monitor_inc_mic_buffer();
#endif

#ifdef CONFIG_OMI_ENABLE_VAD_GATE
    uint32_t avg_abs = vad_average_abs_amplitude(buffer, MIC_BUFFER_SAMPLES);
    int64_t now_ms = k_uptime_get();
    uint32_t active_threshold = CONFIG_OMI_VAD_ABS_THRESHOLD;
    uint16_t active_debounce_frames = CONFIG_OMI_VAD_DEBOUNCE_FRAMES;

    if (mic_low_power_mode) {
        active_threshold = MIC_LOW_POWER_WAKE_THRESHOLD;
        active_debounce_frames = MIC_LOW_POWER_WAKE_DEBOUNCE_FRAMES;
    }

    bool has_voice = false;
    if (mic_low_power_mode) {
        if (mic_low_power_skip_frames > 0) {
            mic_low_power_skip_frames--;
            vad_preroll_store(buffer);
            return;
        }
        bool wake_candidate = avg_abs >= active_threshold;
        mic_low_power_wake_history = ((mic_low_power_wake_history << 1) | (wake_candidate ? 1u : 0u)) &
                                     ((1u << MIC_LOW_POWER_WAKE_WINDOW_FRAMES) - 1u);
        has_voice = count_bits_u8(mic_low_power_wake_history) >= MIC_LOW_POWER_WAKE_DEBOUNCE_FRAMES;
    } else {
        has_voice = avg_abs >= active_threshold;
    }

    if (has_voice) {
        vad_last_voice_ms = now_ms;
        if (!vad_is_recording) {
            if (mic_low_power_mode) {
                int mic_ret = mic_set_mode(MIC_MODE_STEREO);
                if (mic_ret == 0) {
                    mic_low_power_mode = false;
                    mic_low_power_skip_frames = 0;
                    mic_low_power_wake_history = 0;
                    LOG_INF("VAD: wake detected, stereo microphones restored");
                } else {
                    LOG_ERR("VAD: failed to restore stereo microphones (%d)", mic_ret);
                }
                vad_preroll_flush();
                vad_is_recording = true;
                LOG_INF("VAD: RECORDING (avg_abs=%u, debounce=%d frames)", avg_abs, active_debounce_frames);
            } else {
                vad_voice_streak++;
                if (vad_voice_streak >= active_debounce_frames) {
                    vad_preroll_flush();
                    vad_is_recording = true;
                    LOG_INF("VAD: RECORDING (avg_abs=%u, debounce=%d frames)", avg_abs, active_debounce_frames);
                }
            }
        }
    } else {
        vad_voice_streak = 0;
        if (vad_is_recording) {
            int64_t silent_ms = now_ms - vad_last_voice_ms;
            if (silent_ms >= CONFIG_OMI_VAD_HOLD_MS) {
                vad_is_recording = false;
                LOG_INF("VAD: SLEEP (silent %lld ms, hold=%d ms)", silent_ms, CONFIG_OMI_VAD_HOLD_MS);
#ifdef CONFIG_OMI_ENABLE_T5838_AAD
                aad_sleep_started_ms = 0;
                aad_selftest_done = false;
#endif
                if (!mic_low_power_mode) {
                    int mic_ret = mic_set_mode(MIC_MODE_MONO_LEFT);
                    if (mic_ret == 0) {
                        mic_low_power_mode = true;
                        mic_low_power_skip_frames = MIC_LOW_POWER_SKIP_FRAMES_COUNT;
                        mic_low_power_wake_history = 0;
                        vad_preroll_reset();
                        LOG_INF("VAD: software sleep active (MIC1 only, waiting for voice)");
                    } else {
                        vad_is_recording = true;
                        vad_last_voice_ms = now_ms;
                        LOG_ERR("VAD: failed to switch to MIC1-only mode (%d)", mic_ret);
                    }
                }
            }
        }
    }

    if (now_ms >= vad_next_status_log_ms) {
        LOG_INF("VAD: STATE=%s (avg_abs=%u, threshold=%u, debounce=%u, hold=%d ms)",
                vad_is_recording ? "RECORDING" : "SLEEP",
                avg_abs,
                active_threshold,
                active_debounce_frames,
                CONFIG_OMI_VAD_HOLD_MS);
        vad_next_status_log_ms = now_ms + VAD_STATUS_LOG_INTERVAL_MS;
    }

    if (!vad_is_recording) {
        vad_preroll_store(buffer);
        return;
    }
#endif

    int err = codec_receive_pcm(buffer, MIC_BUFFER_SAMPLES);
    if (err) {
        LOG_ERR("Failed to process PCM data: %d", err);
    }
}

static void boot_led_sequence(void)
{
    // Quick blue pulse = "I'm alive, booting..."
    set_led_blue(true);
    k_msleep(300);
    led_off();
}

static void boot_ready_sequence(void)
{
    const int steps = 50;
    const int delay_ms = 10;

    // Smooth green fade in/out 2 times = "Ready!"
    for (int cycle = 0; cycle < 2; cycle++) {
        // Fade in: ease-in-out
        for (int i = 0; i <= steps; i++) {
            float t = (float) i / steps;
            // Ease-in-out quadratic
            float eased = t < 0.5f ? 2.0f * t * t : 1.0f - 2.0f * (1.0f - t) * (1.0f - t);
            uint8_t level = (uint8_t) (eased * 50.0f);
            set_led_pwm(LED_GREEN, level);
            k_msleep(delay_ms);
        }

        // Fade out: ease-in-out
        for (int i = 0; i <= steps; i++) {
            float t = (float) i / steps;
            float eased = t < 0.5f ? 2.0f * t * t : 1.0f - 2.0f * (1.0f - t) * (1.0f - t);
            uint8_t level = (uint8_t) ((1.0f - eased) * 70.0f);
            set_led_pwm(LED_GREEN, level);
            k_msleep(delay_ms);
        }
    }
    k_msleep(10);
    led_off();
    k_msleep(10);
}

void set_led_state()
{
    // If device is off, turn off all LEDs immediately
    if (is_off) {
        led_off();
        return;
    }

#ifdef CONFIG_OMI_ENABLE_OFFLINE_STORAGE
    // If RTC not synced, blink red to warn user to connect phone app
    if (!rtc_is_valid()) {
        set_led_green(is_charging);
        set_led_blue(!blink_toggle && is_connected);
        set_led_red(blink_toggle);
        blink_toggle = !blink_toggle;
        return;
    }
#endif

    bool green = false;
    bool blue = false;
    bool red = false;

    if (is_charging) {
#ifdef CONFIG_OMI_ENABLE_BATTERY
        // Solid green if battery is full (>= BATTERY_FULL_THRESHOLD_PERCENT)
        if (battery_percentage >= BATTERY_FULL_THRESHOLD_PERCENT) {
            green = true;
        } else
#endif
        {
            green = blink_toggle;
            blue = !blink_toggle && is_connected;
            red = !blink_toggle && !is_connected;
            blink_toggle = !blink_toggle;
        }
    } else {
        blue = is_connected;
        red = !is_connected;
    }

    set_led_green(green);
    set_led_blue(blue);
    set_led_red(red);
}

static int suspend_unused_modules(void)
{
    LOG_WRN("Skipping early SPI flash suspend for boot stability");
    return 0;
}

int main(void)
{
    int ret;
    printk("Starting omi ...\n");

    // print reset reason at startup
    print_reset_reason();

    // Initialize watchdog first to catch any early freezes
    ret = watchdog_init();
    if (ret) {
        LOG_WRN("Watchdog init failed (err %d), continuing without watchdog", ret);
    }

    // Initialize Haptic driver first; this is building up for future of omi turn on sequence - long press to turn on
    // instead of short press
#ifdef CONFIG_OMI_ENABLE_HAPTIC
    ret = haptic_init();
    if (ret) {
        LOG_ERR("Failed to initialize Haptic driver (err %d)", ret);
        error_haptic();
        // Non-critical, continue boot
    } else {
        LOG_INF("Haptic driver initialized");
        LOG_WRN("Skipping boot haptic pulse for stability (will keep BLE haptic commands)");
    }
#endif

    // Initialize LEDs
    LOG_INF("Initializing LEDs...\n");

    ret = led_start();
    if (ret) {
        LOG_ERR("Failed to initialize LEDs (err %d)", ret);
        error_led_driver();
        return ret;
    }

    // Suspend unused modules
    LOG_PRINTK("\n");
    LOG_INF("Suspending unused modules...\n");
    ret = suspend_unused_modules();
    if (ret) {
        LOG_ERR("Failed to suspend unused modules (err %d)", ret);
        ret = 0;
    }

    // Initialize settings
    LOG_INF("Initializing settings...\n");
    int setting_ret = app_settings_init();
    if (setting_ret) {
        LOG_ERR("Failed to initialize settings (err %d)", setting_ret);
    }

    // Initialize RTC from saved epoch
    LOG_INF("Boot stage: init_rtc start");
    init_rtc();
    LOG_INF("Boot stage: init_rtc done");
    if (!rtc_is_valid()) {
        LOG_WRN("UTC time not synchronized yet");
    }

    LOG_INF("Boot stage: lsm6dsl_time_boot_adjust_rtc start");
    (void) lsm6dsl_time_boot_adjust_rtc();
    LOG_INF("Boot stage: lsm6dsl_time_boot_adjust_rtc done");

#ifdef CONFIG_OMI_ENABLE_MONITOR
    // Initialize monitoring system
    LOG_INF("Initializing monitoring system...\n");
    ret = monitor_init();
    if (ret) {
        LOG_ERR("Failed to initialize monitoring system (err %d)", ret);
    }
#endif

    if (setting_ret) {
        error_settings();
        app_settings_save_dim_ratio(30);
    }

    // Initialize battery
#ifdef CONFIG_OMI_ENABLE_BATTERY
    LOG_INF("Boot stage: battery_init start");
    ret = battery_init();
    if (ret) {
        LOG_ERR("Battery init failed (err %d)", ret);
        error_battery_init();
        return ret;
    }

    LOG_INF("Boot stage: battery_charge_start start");
    ret = battery_charge_start();
    if (ret) {
        LOG_ERR("Battery failed to start (err %d)", ret);
        error_battery_charge();
        return ret;
    }
    LOG_INF("Battery initialized");
#endif

    // Initialize button
#ifdef CONFIG_OMI_ENABLE_BUTTON
    ret = button_init();
    if (ret) {
        LOG_ERR("Failed to initialize Button (err %d)", ret);
        error_button();
        return ret;
    }
    LOG_INF("Button initialized");
    activate_button_work();
#endif

    // SD Card
    ret = app_sd_init();
    if (ret) {
        LOG_ERR("Failed to initialize SD Card (err %d)", ret);
        error_sd_card();
        return ret;
    }

#ifdef CONFIG_OMI_ENABLE_OFFLINE_STORAGE
    // Initialize storage service for offline audio
    ret = storage_init();
    if (ret) {
        LOG_ERR("Failed to initialize storage service (err %d)", ret);
        error_storage();
        // Non-critical, continue boot
    } else {
        LOG_INF("Storage service initialized");
    }
#endif

    // Indicate transport initialization
    LOG_PRINTK("\n");
    LOG_INF("Initializing transport...\n");

    // Start transport
    int transportErr;
    transportErr = transport_start();
    if (transportErr) {
        LOG_ERR("Failed to start transport (err %d), continuing with mic/codec for offline recording", transportErr);
        error_transport();
        // Non-fatal: mic and codec must still start for VAD/offline recording
    }

    // Initialize codec
    LOG_INF("Initializing codec...\n");

    // Set codec callback
    set_codec_callback(codec_handler);
    ret = codec_start();
    if (ret) {
        LOG_ERR("Failed to start codec: %d", ret);
        error_codec();
        return ret;
    }

    // Initialize microphone
    LOG_INF("Initializing microphone...\n");
    set_mic_callback(mic_handler);
    ret = mic_start();
    if (ret) {
        LOG_ERR("Failed to start microphone: %d", ret);
        error_microphone();
        return ret;
    }
#ifdef CONFIG_OMI_ENABLE_VAD_GATE
    LOG_INF("VAD gate enabled (threshold=%d, debounce=%d frames, hold=%d ms)",
            CONFIG_OMI_VAD_ABS_THRESHOLD,
            CONFIG_OMI_VAD_DEBOUNCE_FRAMES,
            CONFIG_OMI_VAD_HOLD_MS);
#ifdef CONFIG_OMI_ENABLE_T5838_AAD
    aad_hw_ready = false;
    LOG_INF("T5838 hardware AAD bypassed; using MIC1 software wake");
#endif
#else
    LOG_INF("VAD gate disabled (always recording)");
#endif
    LOG_INF("Device initialized successfully\n");

    while (1) {
        watchdog_feed();
#ifdef CONFIG_OMI_ENABLE_MONITOR
        monitor_log_metrics();
#endif

#if defined(CONFIG_OMI_ENABLE_VAD_GATE) && defined(CONFIG_OMI_ENABLE_T5838_AAD)
        /* Check if T5838 WAKE pin fired while mic was paused */
        if (aad_wake_pending) {
            aad_wake_pending = false;
            t5838_aad_exit_sleep();
            aad_sleep_started_ms = 0;
            aad_selftest_done = false;
            /* Reset VAD state so debounce starts fresh */
            vad_voice_streak = 0;
            vad_last_voice_ms = k_uptime_get();
            vad_is_recording = false;
            LOG_INF("AAD: WAKE fired → mic resumed, VAD debounce reset");
        }

        /* Debug: poll WAKE at 500 ms while AAD sleeping */
        if (t5838_aad_is_sleeping()) {
            int64_t now_ms = k_uptime_get();

            /* ---- POLLING-BASED WAKE DETECTION ----
             * Datasheet: WAKE HIGH = sound detected, WAKE LOW = idle.
             * Only trigger on LATCH (LOW→HIGH transition), NOT on raw level.
             * Raw WAKE=1 at arm time just means TXS pull-up or T5838 not in
             * AAD yet — not a real sound event.
             *
             * Guard time: ignore first 1s after arming to let T5838 settle.
             * Timeout: force resume after 60s as safety.
             */
            int64_t aad_elapsed_ms = now_ms - aad_sleep_started_ms;

            if (aad_elapsed_ms >= AAD_GUARD_MS) {
                int latch_poll = t5838_aad_read_wake_latch();
                if (latch_poll == 1) {
                    int wake_poll = t5838_aad_read_wake_pin_raw();
                    LOG_INF("AAD POLL: LATCH fired! wake=%d latch=1 → resuming mic", wake_poll);
                    t5838_aad_exit_sleep();
                    aad_sleep_started_ms = 0;
                    aad_selftest_done = false;
                    vad_voice_streak = 0;
                    vad_last_voice_ms = k_uptime_get();
                    vad_is_recording = false;
                }
            }

            /* Timeout: force resume after 60s with no wake event */
            if (t5838_aad_is_sleeping() && aad_elapsed_ms >= AAD_TIMEOUT_MS) {
                LOG_WRN("AAD TIMEOUT: no wake after %llds, forcing resume", (long long) (aad_elapsed_ms / 1000));
                t5838_aad_exit_sleep();
                aad_sleep_started_ms = 0;
                aad_selftest_done = false;
                vad_voice_streak = 0;
                vad_last_voice_ms = k_uptime_get();
                vad_is_recording = false;
            }

            if (t5838_aad_is_sleeping() && now_ms >= aad_next_debug_log_ms) {
                int wake_r = t5838_aad_read_wake_pin_raw();
                int wake_lat = t5838_aad_read_wake_latch();
                uint32_t p1_in = t5838_aad_read_p1_in();
                uint32_t isr_cnt = t5838_aad_get_isr_count();
                LOG_INF("AAD DBG: WAKE=%d latch=%d isr=%u | P1_IN=0x%04X",
                        wake_r,
                        wake_lat,
                        isr_cnt,
                        (unsigned) (p1_in & 0xFFFF));
                aad_next_debug_log_ms = now_ms + 200;
            }

            /* Self-test: after 5s, check WAKE state and report AAD status */
            if (!aad_selftest_done && aad_sleep_started_ms > 0 &&
                (now_ms - aad_sleep_started_ms) >= AAD_SELFTEST_DELAY_MS) {
                aad_selftest_done = true;
                LOG_INF("AAD: running self-test (checking WAKE state)...");
                t5838_aad_selftest_wake_irq();
            }
        }
#endif

        set_led_state();
        k_msleep(100); /* 100ms for fast T5838 AAD wake response */
    }

    printk("Exiting omi...");
    return 0;
}
