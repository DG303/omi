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
#include "aad.h"
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
    monitor_inc_mic_buffer();
#endif

#ifdef CONFIG_OMI_ENABLE_T5838_AAD
    if (!aad_process_audio(buffer, MIC_BUFFER_SAMPLES)) {
        return;
    }
#endif

    int err = codec_receive_pcm(buffer, MIC_BUFFER_SAMPLES);
    if (err) {
        LOG_ERR("Failed to process PCM data: %d", err);
    }
}

void set_led_state()
{
    if (is_off) {
        led_off();
        return;
    }

#ifdef CONFIG_OMI_ENABLE_OFFLINE_STORAGE
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

    print_reset_reason();

    ret = watchdog_init();
    if (ret) {
        LOG_WRN("Watchdog init failed (err %d), continuing without watchdog", ret);
    }

#ifdef CONFIG_OMI_ENABLE_HAPTIC
    ret = haptic_init();
    if (ret) {
        LOG_ERR("Failed to initialize Haptic driver (err %d)", ret);
        error_haptic();
    } else {
        LOG_INF("Haptic driver initialized");
        LOG_WRN("Skipping boot haptic pulse for stability");
    }
#endif

    LOG_INF("Initializing LEDs...\n");
    ret = led_start();
    if (ret) {
        LOG_ERR("Failed to initialize LEDs (err %d)", ret);
        error_led_driver();
        return ret;
    }

    LOG_PRINTK("\n");
    LOG_INF("Suspending unused modules...\n");
    ret = suspend_unused_modules();
    if (ret) {
        LOG_ERR("Failed to suspend unused modules (err %d)", ret);
        ret = 0;
    }

    LOG_INF("Initializing settings...\n");
    int setting_ret = app_settings_init();
    if (setting_ret) {
        LOG_ERR("Failed to initialize settings (err %d)", setting_ret);
    }

    init_rtc();
    if (!rtc_is_valid()) {
        LOG_WRN("UTC time not synchronized yet");
    }

    (void) lsm6dsl_time_boot_adjust_rtc();

#ifdef CONFIG_OMI_ENABLE_MONITOR
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

#ifdef CONFIG_OMI_ENABLE_BATTERY
    ret = battery_init();
    if (ret) {
        LOG_ERR("Battery init failed (err %d)", ret);
        error_battery_init();
        return ret;
    }

    ret = battery_charge_start();
    if (ret) {
        LOG_ERR("Battery failed to start (err %d)", ret);
        error_battery_charge();
        return ret;
    }
    LOG_INF("Battery initialized");
#endif

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

    ret = app_sd_init();
    if (ret) {
        LOG_ERR("Failed to initialize SD Card (err %d)", ret);
        error_sd_card();
        return ret;
    }

#ifdef CONFIG_OMI_ENABLE_OFFLINE_STORAGE
    ret = storage_init();
    if (ret) {
        LOG_ERR("Failed to initialize storage service (err %d)", ret);
        error_storage();
    } else {
        LOG_INF("Storage service initialized");
    }
#endif

    LOG_PRINTK("\n");
    LOG_INF("Initializing transport...\n");

    int transportErr = transport_start();
    if (transportErr) {
        LOG_ERR("Failed to start transport (err %d), continuing for offline recording", transportErr);
        error_transport();
    }

    LOG_INF("Initializing codec...\n");
    set_codec_callback(codec_handler);
    ret = codec_start();
    if (ret) {
        LOG_ERR("Failed to start codec: %d", ret);
        error_codec();
        return ret;
    }

    LOG_INF("Initializing microphone...\n");
    set_mic_callback(mic_handler);
    ret = mic_start();
    if (ret) {
        LOG_ERR("Failed to start microphone: %d", ret);
        error_microphone();
        return ret;
    }

#ifdef CONFIG_OMI_ENABLE_T5838_AAD
    ret = aad_start();
    if (ret) {
        LOG_ERR("AAD start failed (%d)", ret);
    }
#endif

    LOG_INF("Device initialized successfully\n");

    while (1) {
        watchdog_feed();
#ifdef CONFIG_OMI_ENABLE_MONITOR
        monitor_log_metrics();
#endif
        set_led_state();
        k_msleep(1000);
    }

    printk("Exiting omi...");
    return 0;
}