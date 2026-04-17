/*
 * T5838 AAD (Acoustic Activity Detection) driver for Omi
 *
 * Based on reference implementation from Brilliant Labs / IRNAS.
 *
 * T5838 AAD requires explicit register configuration via a proprietary
 * "fake I2C" protocol that uses THSEL as data line and PDM_CLK as clock.
 * Simply stopping the PDM clock does NOT enable AAD — registers must be
 * written first, then >2ms of clocking to enter sleep with AAD active.
 *
 * Protocol summary (from T5838 datasheet):
 *   1. Unlock AAD registers (5-register unlock sequence)
 *   2. Write AAD mode + threshold registers via THSEL/CLK bit-bang
 *   3. Clock >2ms to enter AAD sleep
 *   4. Stop clock → T5838 in AAD low-power mode (~15 µA)
 *   5. WAKE pin asserts when sound exceeds threshold
 *
 * Pin assignments (from board DTS nodelabels):
 *   pdm_en_pin    = P1.4   GPIO output — LDO enable + level shifter VCCB
 *   pdm_thsel_pin = P1.5   GPIO output — data line for fake I2C + threshold
 *   pdm_wake_pin  = P1.2   GPIO input  — WAKE interrupt from T5838
 *   PDM_CLK       = P1.1   clock line for fake I2C + PDM
 *
 * Verified from the board schematic:
 *   - MIC1 alone is connected to WAKE and THSEL through TXS0104E.
 *   - MIC2 shares only VDD/DATA/CLK and has WAKE/THSEL unconnected.
 *   - So any stuck-high WAKE behavior is from MIC1/TXS path, not MIC2.
 */

#include "t5838_aad.h"

#include <hal/nrf_gpio.h>
#include <hal/nrf_gpiote.h>
#include <hal/nrf_pdm.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/dt-bindings/gpio/nordic-nrf-gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "lib/core/mic.h"

LOG_MODULE_REGISTER(t5838_aad, CONFIG_LOG_DEFAULT_LEVEL);

/* ---- DTS-based GPIO specs ---- */
static const struct gpio_dt_spec pin_en = GPIO_DT_SPEC_GET_OR(DT_NODELABEL(pdm_en_pin), gpios, {0});
static const struct gpio_dt_spec pin_thsel = GPIO_DT_SPEC_GET_OR(DT_NODELABEL(pdm_thsel_pin), gpios, {0});
static const struct gpio_dt_spec pin_wake = GPIO_DT_SPEC_GET_OR(DT_NODELABEL(pdm_wake_pin), gpios, {0});

/*
 * CLK pin — use Zephyr GPIO API (not bare nrf_gpio) to ensure proper
 * pin ownership on nRF5340.  The PDM driver uses pinctrl for CLK;
 * only gpio_pin_configure() reliably takes the pin back from the
 * peripheral on this SoC.  Reference code does the same.
 */
static const struct device *gpio1_dev = DEVICE_DT_GET(DT_NODELABEL(gpio1));
#define T5838_PDM_CLK_PIN 1                          /* local pin number on port 1 */
#define T5838_PDM_CLK_ABS_PIN NRF_GPIO_PIN_MAP(1, 1) /* for PSEL only */
#define T5838_THSEL_PIN 5                            /* P1.5 — THSEL = fake I2C data */
#define T5838_WAKE_PIN 2                             /* P1.2 — WAKE input */
#define T5838_CLK_BIT (1UL << T5838_PDM_CLK_PIN)     /* bit mask for P1.1 */
#define T5838_THSEL_BIT (1UL << T5838_THSEL_PIN)     /* bit mask for P1.5 */
#define T5838_WAKE_BIT (1UL << T5838_WAKE_PIN)       /* bit mask for P1.2 */

/* Direct GPIO P1 register — OUTSET/OUTCLR with H0H1 bypasses Zephyr overhead */
#define T5838_P1 NRF_P1

/* nRF5340 PDM0 register base */
#define T5838_PDM_REG NRF_PDM0

/* ---- Fake I2C protocol constants (from T5838 datasheet) ---- */
#define FAKE2C_DEVICE_ADDRESS 0x53
#define FAKE2C_CLK_PERIOD_US 10 /* ~100 kHz clock */
#define FAKE2C_START_PILOT_CLKS 10
#define FAKE2C_ZERO_CLKS (1 * FAKE2C_START_PILOT_CLKS)  /* 10 clocks = bit 0 */
#define FAKE2C_ONE_CLKS (3 * FAKE2C_START_PILOT_CLKS)   /* 30 clocks = bit 1 */
#define FAKE2C_STOP_CLKS 130                            /* >128 clocks per datasheet */
#define FAKE2C_SPACE_CLKS (1 * FAKE2C_START_PILOT_CLKS) /* 10 clocks between bits */
#define FAKE2C_PRE_WRITE_CLKS 60                        /* >50 clocks before write */
#define FAKE2C_POST_WRITE_CLKS 60                       /* >50 clocks after write */

/* >2ms of clock is required before entering sleep with AAD */
#define AAD_SLEEP_CLOCK_US 5000    /* 5ms — generous margin over 2ms minimum */
#define AAD_SLEEP_CLK_PERIOD_US 10 /* ~100 kHz */

/* T5838 AAD register addresses */
#define REG_AAD_MODE 0x29
#define REG_AAD_D_FLOOR_HI 0x2A
#define REG_AAD_D_FLOOR_LO 0x2B
#define REG_AAD_D_MISC 0x2C
#define REG_AAD_D_REL_PULSE_LO 0x2E
#define REG_AAD_D_PULSE_SHARED 0x2F
#define REG_AAD_D_ABS_PULSE_LO 0x30
#define REG_AAD_D_ABS_THR_LO 0x31
#define REG_AAD_D_ABS_THR_HI 0x32
#define REG_AAD_D_REL_THR 0x33
#define REG_AAD_A_LPF 0x35
#define REG_AAD_A_THR 0x36

/* AAD mode values */
#define AAD_MODE_NONE 0x00
#define AAD_MODE_D1 0x01
#define AAD_MODE_A 0x08

/* AAD A mode LPF values */
#define AAD_A_LPF_4_4kHz 0x01 /* widest bandwidth */
#define AAD_A_LPF_2_0kHz 0x02
#define AAD_A_LPF_1_1kHz 0x07 /* narrowest bandwidth */

/* AAD A mode threshold values (from datasheet Table 17) */
#define AAD_A_THR_60dB 0x00
#define AAD_A_THR_65dB 0x02
#define AAD_A_THR_70dB 0x04
#define AAD_A_THR_75dB 0x06
#define AAD_A_THR_80dB 0x08
#define AAD_A_THR_85dB 0x0A

/* ---- State ---- */
static bool aad_initialized = false;
static volatile bool aad_sleeping = false;
static bool aad_unlocked = false;
static t5838_aad_wake_cb_t wake_callback = NULL;
static struct gpio_callback wake_cb_data;
static int64_t aad_armed_at_ms = 0;
static volatile uint32_t aad_isr_count = 0;

#define AAD_WAKE_GUARD_MS 300

static inline void wake_latch_clear(void)
{
    T5838_P1->LATCH = T5838_WAKE_BIT;
}

static inline int wake_latch_read_and_clear(void)
{
    int latched = (T5838_P1->LATCH & T5838_WAKE_BIT) ? 1 : 0;
    if (latched) {
        T5838_P1->LATCH = T5838_WAKE_BIT;
    }
    return latched;
}

static inline void wake_sense_low_enable(void)
{
    uint32_t cnf = T5838_P1->PIN_CNF[T5838_WAKE_PIN];
    cnf &= ~GPIO_PIN_CNF_SENSE_Msk;
    cnf |= (GPIO_PIN_CNF_SENSE_Low << GPIO_PIN_CNF_SENSE_Pos);
    T5838_P1->PIN_CNF[T5838_WAKE_PIN] = cnf;
}

static inline void wake_sense_high_enable(void)
{
    uint32_t cnf = T5838_P1->PIN_CNF[T5838_WAKE_PIN];
    cnf &= ~GPIO_PIN_CNF_SENSE_Msk;
    cnf |= (GPIO_PIN_CNF_SENSE_High << GPIO_PIN_CNF_SENSE_Pos);
    T5838_P1->PIN_CNF[T5838_WAKE_PIN] = cnf;
}

static inline void wake_sense_disable(void)
{
    uint32_t cnf = T5838_P1->PIN_CNF[T5838_WAKE_PIN];
    cnf &= ~GPIO_PIN_CNF_SENSE_Msk;
    cnf |= (GPIO_PIN_CNF_SENSE_Disabled << GPIO_PIN_CNF_SENSE_Pos);
    T5838_P1->PIN_CNF[T5838_WAKE_PIN] = cnf;
}

/*
 * GPIOTE diagnostic: which GPIOTE instance does Zephyr use for P1?
 * nRF5340 app core (secure): NRF_GPIOTE0_S at 0x5000D000
 * Each CONFIG[n] register:
 *   [1:0]  MODE   — 0=Disabled, 1=Event, 3=Task
 *   [12:8] PSEL   — pin number within port
 *   [13]   PORT   — 0=P0, 1=P1
 *   [17:16] POLARITY — 1=LoToHi, 2=HiToLo, 3=Toggle
 */
#define AAD_GPIOTE NRF_GPIOTE0_S

static void dump_gpiote_config(void)
{
    LOG_INF("T5838 GPIOTE0 dump (looking for P1.%d EVENT TOGGLE):", T5838_WAKE_PIN);
    int found = -1;
    for (int ch = 0; ch < 8; ch++) {
        uint32_t cfg = AAD_GPIOTE->CONFIG[ch];
        if (cfg == 0) {
            continue; /* disabled channel, skip */
        }
        int mode = cfg & 0x3;
        int psel = (cfg >> 8) & 0x1F;
        int port = (cfg >> 13) & 0x1;
        int pol = (cfg >> 16) & 0x3;
        LOG_INF("  GPIOTE CH%d: CONFIG=0x%08X MODE=%d PSEL=%d PORT=%d POL=%d", ch, cfg, mode, psel, port, pol);
        if (port == 1 && psel == T5838_WAKE_PIN && mode == 1) {
            found = ch;
        }
    }
    if (found >= 0) {
        LOG_INF("  -> P1.%d found on GPIOTE CH%d (EVENT mode) OK", T5838_WAKE_PIN, found);
        /* Check INTENSET for this channel */
        uint32_t inten = AAD_GPIOTE->INTENSET;
        LOG_INF("  GPIOTE INTENSET=0x%08X (CH%d bit=%d)", inten, found, (inten >> found) & 1);
    } else {
        LOG_WRN("  -> P1.%d NOT found in any GPIOTE channel! IRQ will never fire.", T5838_WAKE_PIN);
    }
}

/* ================================================================
 * Fake I2C bit-bang implementation
 * ================================================================
 * The T5838 uses a proprietary protocol on THSEL (data) and CLK pins.
 * Pulse-width encoding: short pulse = 0, long pulse = 1.
 * ================================================================ */

/**
 * @brief Bit-bang clock cycles on CLK pin via direct register OUTSET/OUTCLR.
 *
 * GPIO must be configured to H0H1 drive BEFORE calling this function.
 * Standard S0S1 drive (Zephyr default) is too weak to pass TXS0104E.
 */
static void clock_bitbang(uint16_t cycles, uint16_t period_us)
{
    for (int i = 0; i < cycles; i++) {
        T5838_P1->OUTSET = T5838_CLK_BIT;
        k_busy_wait(period_us / 2);
        T5838_P1->OUTCLR = T5838_CLK_BIT;
        k_busy_wait(period_us / 2);
    }
}

/**
 * @brief Write one register via fake I2C protocol.
 *
 * Frame: [device_addr<<1] [reg_addr] [data]
 * Each byte: 8 bits, MSB first, pulse-width encoded.
 */
static int fake2c_reg_write(uint8_t reg, uint8_t data)
{
    uint8_t wr_buf[3] = {FAKE2C_DEVICE_ADDRESS << 1, reg, data};

    /* CLK and THSEL must already be configured as H0H1 output */

    /*
     * Lock IRQs for the entire register write to prevent interrupt jitter
     * from corrupting pulse-width encoding.  Reference code uses k_spin_lock
     * for the same reason.  One write takes ~8ms (830 clocks * 10µs).
     */
    unsigned int irq_key = irq_lock();

    /* Start with THSEL LOW — direct register for H0H1 drive through TXS0104E */
    T5838_P1->OUTCLR = T5838_THSEL_BIT;

    /* Pre-write clocking */
    clock_bitbang(FAKE2C_PRE_WRITE_CLKS, FAKE2C_CLK_PERIOD_US);

    /* Start condition: THSEL HIGH + pilot clocks */
    T5838_P1->OUTSET = T5838_THSEL_BIT;
    clock_bitbang(FAKE2C_START_PILOT_CLKS, FAKE2C_CLK_PERIOD_US);

    /* First space before data */
    T5838_P1->OUTCLR = T5838_THSEL_BIT;
    clock_bitbang(FAKE2C_SPACE_CLKS, FAKE2C_CLK_PERIOD_US);

    /* Write 3 bytes: address, register, data */
    for (int i = 0; i < 3; i++) {
        for (int j = 0; j < 8; j++) {
            uint8_t bit_clks = (wr_buf[i] & BIT(7 - j)) ? FAKE2C_ONE_CLKS : FAKE2C_ZERO_CLKS;

            /* Data bit: THSEL HIGH + pulse-width clocks */
            T5838_P1->OUTSET = T5838_THSEL_BIT;
            clock_bitbang(bit_clks, FAKE2C_CLK_PERIOD_US);

            /* Space: THSEL LOW + space clocks */
            T5838_P1->OUTCLR = T5838_THSEL_BIT;
            clock_bitbang(FAKE2C_SPACE_CLKS, FAKE2C_CLK_PERIOD_US);
        }
    }

    /* Stop condition: THSEL HIGH + >128 clocks */
    T5838_P1->OUTSET = T5838_THSEL_BIT;
    clock_bitbang(FAKE2C_STOP_CLKS, FAKE2C_CLK_PERIOD_US);
    T5838_P1->OUTCLR = T5838_THSEL_BIT;

    /* Post-write clocking */
    clock_bitbang(FAKE2C_POST_WRITE_CLKS, FAKE2C_CLK_PERIOD_US);

    irq_unlock(irq_key);

    return 0;
}

/**
 * @brief Send the AAD unlock sequence (required before writing AAD registers).
 */
static int aad_unlock_sequence(void)
{
    struct {
        uint8_t addr;
        uint8_t data;
    } unlock_seq[] = {
        {0x5C, 0x00},
        {0x3E, 0x00},
        {0x6F, 0x00},
        {0x3B, 0x00},
        {0x4C, 0x00},
    };

    for (int i = 0; i < 5; i++) {
        int err = fake2c_reg_write(unlock_seq[i].addr, unlock_seq[i].data);
        if (err) {
            LOG_ERR("T5838: unlock seq[%d] failed: %d", i, err);
            return err;
        }
    }

    aad_unlocked = true;
    LOG_INF("T5838: AAD unlock sequence sent OK");
    return 0;
}

/**
 * @brief Clock T5838 for >2ms to enter AAD sleep mode.
 * Must be called after writing AAD registers.
 */
static void aad_enter_sleep_clocking(void)
{
    uint16_t cycles = AAD_SLEEP_CLOCK_US / AAD_SLEEP_CLK_PERIOD_US;
    clock_bitbang(cycles, AAD_SLEEP_CLK_PERIOD_US);
}

/* ---- ISR ---- */
static void wake_pin_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    ARG_UNUSED(dev);
    ARG_UNUSED(cb);
    ARG_UNUSED(pins);

    if (!aad_sleeping) {
        return;
    }

    aad_isr_count++;

    int64_t now_ms = k_uptime_get();
    if (aad_armed_at_ms > 0 && (now_ms - aad_armed_at_ms) < AAD_WAKE_GUARD_MS) {
        int wake_raw_guard = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
        int latch_guard = wake_latch_read_and_clear();
        LOG_INF("T5838: WAKE IRQ ignored in guard window (%lldms < %dms), raw=%d latch=%d",
                (long long) (now_ms - aad_armed_at_ms),
                AAD_WAKE_GUARD_MS,
                wake_raw_guard,
                latch_guard);
        return;
    }

    int wake_raw = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
    int latch = wake_latch_read_and_clear();

    /*
     * ANY interrupt while aad_sleeping → treat as sound detected.
     * The WAKE pin edge is the only source of interrupts while sleeping.
     */
    aad_sleeping = false;
    aad_armed_at_ms = 0;
    LOG_INF("T5838: WAKE IRQ → sound detected! raw=%d latch=%d", wake_raw, latch);
    if (wake_callback) {
        wake_callback();
    }
}

/* ---- Public API ---- */

int t5838_aad_init(void)
{
    int ret;

    if (!gpio_is_ready_dt(&pin_en)) {
        LOG_ERR("T5838: PDM_EN gpio not ready");
        return -ENODEV;
    }
    if (!gpio_is_ready_dt(&pin_thsel)) {
        LOG_ERR("T5838: THSEL gpio not ready");
        return -ENODEV;
    }
    if (!gpio_is_ready_dt(&pin_wake)) {
        LOG_ERR("T5838: WAKE gpio not ready");
        return -ENODEV;
    }

    /* PDM_EN HIGH — powers LDO + level shifter */
    ret = gpio_pin_configure_dt(&pin_en, GPIO_OUTPUT_HIGH);
    if (ret) {
        LOG_ERR("Failed to configure PDM_EN: %d", ret);
        return ret;
    }
    k_msleep(5);

    /* THSEL: output, default LOW */
    ret = gpio_pin_configure_dt(&pin_thsel, GPIO_OUTPUT_INACTIVE);
    if (ret) {
        LOG_ERR("Failed to configure THSEL: %d", ret);
        return ret;
    }

    /* WAKE: input with PULL-DOWN.
     * TXS0104E has internal pull-ups (~10kΩ) on both sides.
     * T5838 in AAD mode (~20µA total) CANNOT sink enough current
     * to pull WAKE LOW through TXS pull-up (needs ~180µA).
     * nRF pull-down (~13kΩ) creates voltage divider:
     *   V = VCCB × 13k/(10k+13k) ≈ 0.57×VCCB → below VIH threshold.
     * When T5838 detects sound and drives WAKE HIGH (full power),
     * it overcomes the pull-down → reads HIGH = rising edge. */
    ret = gpio_pin_configure_dt(&pin_wake, GPIO_INPUT | GPIO_PULL_DOWN);
    if (ret) {
        LOG_ERR("Failed to configure WAKE: %d", ret);
        return ret;
    }

    /* Disable WAKE IRQ initially */
    gpio_pin_interrupt_configure_dt(&pin_wake, GPIO_INT_DISABLE);
    wake_sense_disable();
    wake_latch_clear();

    /* Register WAKE ISR */
    gpio_init_callback(&wake_cb_data, wake_pin_isr, BIT(pin_wake.pin));
    ret = gpio_add_callback(pin_wake.port, &wake_cb_data);
    if (ret) {
        LOG_ERR("Failed to add WAKE callback: %d", ret);
        return ret;
    }

    aad_initialized = true;
    aad_sleeping = false;
    aad_unlocked = false;
    aad_armed_at_ms = 0;

    int wake_val = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
    LOG_INF("T5838 AAD init OK (EN=P1.%d THSEL=P1.%d WAKE=P1.%d CLK=P1.1)", pin_en.pin, pin_thsel.pin, pin_wake.pin);
    LOG_INF("T5838: initial WAKE=%d", wake_val);
    return 0;
}

int t5838_aad_enter_sleep(void)
{
    if (!aad_initialized || aad_sleeping) {
        return -EALREADY;
    }

    int wake_before = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
    LOG_INF("T5838: entering AAD sleep (WAKE before=%d)", wake_before);

    /* 1. Stop PDM peripheral */
    mic_pause();

    /* Wait for PDM to fully disable */
    int wait_count = 0;
    while (T5838_PDM_REG->ENABLE != 0 && wait_count < 100) {
        k_msleep(1);
        wait_count++;
    }
    LOG_INF("T5838: PDM disabled (waited %d ms)", wait_count);

    /* 2. Disconnect CLK and DIN from PDM peripheral */
    T5838_PDM_REG->PSEL.CLK = 0xFFFFFFFFUL;
    T5838_PDM_REG->PSEL.DIN = 0xFFFFFFFFUL;

    /* 3. No power-cycle needed — IRNAS reference doesn't power-cycle.
     * Power-cycling kills TXS0104E VCCB which disrupts signal path.
     * T5838 stays powered and accepts fake I2C from current state. */

    /* 4. Reclaim CLK/THSEL as GPIOs using Zephyr API, then switch to
     * direct OUTSET/OUTCLR bit-banging. This matches the reference driver
     * more closely than raw PIN_CNF writes. */
    int clk_cfg_ret = gpio_pin_configure(gpio1_dev, T5838_PDM_CLK_PIN, GPIO_OUTPUT_INACTIVE | NRF_GPIO_DRIVE_H0H1);
    if (clk_cfg_ret) {
        LOG_ERR("Failed to configure CLK GPIO: %d", clk_cfg_ret);
        goto cleanup_error;
    }
    int thsel_cfg_ret = gpio_pin_configure(pin_thsel.port, pin_thsel.pin, GPIO_OUTPUT_INACTIVE | NRF_GPIO_DRIVE_H0H1);
    if (thsel_cfg_ret) {
        LOG_ERR("Failed to configure THSEL GPIO: %d", thsel_cfg_ret);
        goto cleanup_error;
    }

    int wake_before_cfg = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
    LOG_INF("T5838: before AAD config, WAKE=%d", wake_before_cfg);

    /* 4. Unlock + write AAD registers — A-mode LPF1.1kHz THR85dB
     *
     * Maximum threshold (85dB) + narrowest LPF (1.1kHz) to minimize
     * false triggers from electronic/ambient noise through the board.
     */
    aad_unlocked = false;
    int err = aad_unlock_sequence();
    if (err) {
        LOG_ERR("T5838: AAD unlock failed, aborting");
        goto cleanup_error;
    }

    /* Write AAD config + sleep clocking in ONE continuous block */
    fake2c_reg_write(REG_AAD_MODE, AAD_MODE_NONE);
    fake2c_reg_write(REG_AAD_A_LPF, AAD_A_LPF_1_1kHz);
    fake2c_reg_write(REG_AAD_A_THR, AAD_A_THR_85dB);
    fake2c_reg_write(REG_AAD_MODE, AAD_MODE_A);
    /*
     * CRITICAL: Sleep clocking MUST be continuous (no IRQ gaps).
     * BLE/BT interrupts firing during sleep clocking create clock gaps
     * that prevent T5838 from completing AAD entry. Lock IRQs for
     * the entire 3ms sleep clocking period.
     */
    {
        unsigned int sleep_key = irq_lock();
        aad_enter_sleep_clocking();
        T5838_P1->OUTCLR = T5838_CLK_BIT;
        irq_unlock(sleep_key);
    }
    T5838_P1->OUTCLR = T5838_THSEL_BIT;

    /* µs-resolution WAKE sampling: 10 samples immediately (0-2ms),
     * then wait 20ms for AAD startup (6ms typical + margin),
     * then sample again to verify AAD is active and WAKE is LOW.
     *
     * With nRF pull-down on WAKE: idle should read 0 (pull-down overcomes TXS pull-up).
     * Without pull-down: idle reads 1 (TXS pull-up wins). */
    int wake_early[10];
    for (int i = 0; i < 10; i++) {
        wake_early[i] = (T5838_P1->IN & T5838_WAKE_BIT) ? 1 : 0;
        k_busy_wait(200);
    }
    LOG_INF("T5838: WAKE after clk stop (0-2ms):  %d%d%d%d%d%d%d%d%d%d",
            wake_early[0],
            wake_early[1],
            wake_early[2],
            wake_early[3],
            wake_early[4],
            wake_early[5],
            wake_early[6],
            wake_early[7],
            wake_early[8],
            wake_early[9]);

    /* Wait 20ms for AAD startup (6ms typ) + pull-down to settle */
    k_msleep(20);

    int wake_settled[10];
    for (int i = 0; i < 10; i++) {
        wake_settled[i] = (T5838_P1->IN & T5838_WAKE_BIT) ? 1 : 0;
        k_busy_wait(200);
    }
    LOG_INF("T5838: WAKE at 20-22ms (settled):     %d%d%d%d%d%d%d%d%d%d",
            wake_settled[0],
            wake_settled[1],
            wake_settled[2],
            wake_settled[3],
            wake_settled[4],
            wake_settled[5],
            wake_settled[6],
            wake_settled[7],
            wake_settled[8],
            wake_settled[9]);

    /* Keep CLK and THSEL driven LOW — match IRNAS reference.
     * Do NOT release to Hi-Z! TXS0104E pull-ups would pull CLK HIGH,
     * which T5838 could interpret as clock activity → exit AAD sleep.
     * CLK is already LOW from sleep clocking, THSEL already LOW. */
    LOG_INF("T5838: CLK=LOW THSEL=LOW (driven), WAKE=%d", (T5838_P1->IN & T5838_WAKE_BIT) ? 1 : 0);

    /*
     * WAKE: input with PULL-DOWN — critical for TXS0104E.
     * TXS pull-up (~10kΩ) holds WAKE HIGH when T5838 can't drive it in AAD.
     * nRF pull-down (~13kΩ) overcomes TXS pull-up → idle reads LOW.
     * When T5838 wakes (sound), it drives HIGH at full power → reads HIGH.
     */
    int wake_cfg_ret = gpio_pin_configure_dt(&pin_wake, GPIO_INPUT | GPIO_PULL_DOWN);
    if (wake_cfg_ret) {
        LOG_ERR("WAKE pin config failed: %d", wake_cfg_ret);
        err = wake_cfg_ret;
        goto cleanup_error;
    }
    k_msleep(1);
    int wake_idle = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
    bool wake_weak_high = false;
    LOG_INF("T5838: WAKE idle (pull-down)=%d", wake_idle);
    if (wake_idle == 1) {
        /* Diagnostic: briefly force the nRF side LOW in open-drain mode.
         * If the readback stays 1, something is actively driving HIGH.
         * If it drops to 0 and pops back to 1 when released, the line is
         * only being pulled up / weakly held high. */
        int wake_probe_cfg = gpio_pin_configure_dt(&pin_wake, GPIO_OUTPUT_INACTIVE | GPIO_OPEN_DRAIN);
        if (wake_probe_cfg == 0) {
            k_busy_wait(50);
            int wake_forced_low = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
            gpio_pin_configure_dt(&pin_wake, GPIO_INPUT | GPIO_PULL_DOWN);
            k_busy_wait(50);
            int wake_after_probe = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
            LOG_WRN("T5838: WAKE low-probe: forced_low=%d released=%d", wake_forced_low, wake_after_probe);
            wake_weak_high = (wake_forced_low == 0) && (wake_after_probe == 1);
        } else {
            LOG_WRN("T5838: WAKE low-probe config failed: %d", wake_probe_cfg);
        }
    }

    if (wake_weak_high) {
        /* WAKE is not actively driven HIGH; it is being weakly pulled up.
         * Probe whether that pull-up disappears when PDM_EN is disabled.
         * If yes, the source is on the T5838/TXS side of the level shifter.
         * Abort arming in this state because no future rising edge can occur. */
        gpio_pin_set_dt(&pin_en, 0);
        k_msleep(2);
        int wake_with_en_off = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
        gpio_pin_set_dt(&pin_en, 1);
        k_msleep(5);
        int wake_after_en_restore = gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
        LOG_ERR("T5838: aborting AAD arm, WAKE weak-high. PDM_EN off=%d restored=%d",
                wake_with_en_off,
                wake_after_en_restore);
        err = -EIO;
        goto cleanup_error;
    }

    /* Arm WAKE interrupt — RISING edge only.
     * Datasheet: WAKE LOW=idle, HIGH=sound detected.
     * With pull-down, idle=LOW. Sound=HIGH → rising edge. */
    /* --- DEBUG: verify pin states before arming --- */
    uint32_t thsel_cnf = T5838_P1->PIN_CNF[T5838_THSEL_PIN];
    uint32_t clk_cnf = T5838_P1->PIN_CNF[T5838_PDM_CLK_PIN];
    uint32_t wake_cnf = T5838_P1->PIN_CNF[T5838_WAKE_PIN];
    uint32_t p1_out = T5838_P1->OUT;
    uint32_t p1_in = T5838_P1->IN;
    LOG_INF("T5838 DBG: CLK_CNF=0x%08X THSEL_CNF=0x%08X WAKE_CNF=0x%08X", clk_cnf, thsel_cnf, wake_cnf);
    LOG_INF("T5838 DBG: P1_OUT=0x%04X P1_IN=0x%04X", (unsigned) (p1_out & 0xFFFF), (unsigned) (p1_in & 0xFFFF));

    /* Check WAKE state — with pull-down, should be LOW if AAD entered correctly */
    int wake_before_arm = (T5838_P1->IN & T5838_WAKE_BIT) ? 1 : 0;
    if (wake_before_arm) {
        LOG_WRN("T5838: WAKE=1 before arm — pull-down not overcoming TXS pull-up, or sound present");
    } else {
        LOG_INF("T5838: WAKE=0 before arm — pull-down working, AAD idle confirmed");
    }

    /* Set guard time BEFORE enabling interrupt to prevent race condition.
     * Previously aad_armed_at_ms was 0 when ISR fired → guard bypassed. */
    aad_isr_count = 0;
    aad_armed_at_ms = k_uptime_get();
    aad_sleeping = true;
    wake_latch_clear();

    int ret = gpio_pin_interrupt_configure_dt(&pin_wake, GPIO_INT_EDGE_RISING);
    if (ret) {
        LOG_ERR("WAKE IRQ arm failed: %d", ret);
        aad_sleeping = false;
        aad_armed_at_ms = 0;
        err = ret;
        goto cleanup_error;
    }

    /* Set SENSE_High after interrupt configure — Zephyr may overwrite SENSE. */
    wake_sense_high_enable();
    wake_latch_clear();
    uint32_t wake_cnf_after_sense = T5838_P1->PIN_CNF[T5838_WAKE_PIN];
    LOG_INF("T5838: WAKE_CNF after wake_sense_high_enable=0x%08X (SENSE=%d)",
            wake_cnf_after_sense,
            (wake_cnf_after_sense >> 16) & 0x3);

    /* Dump GPIOTE channels to verify edge interrupt is properly allocated */
    dump_gpiote_config();

    /* Verify WAKE_CNF after arming — SENSE should be set */
    uint32_t wake_cnf_armed = T5838_P1->PIN_CNF[T5838_WAKE_PIN];
    LOG_INF("T5838: WAKE_CNF after arm=0x%08X (SENSE=%d)", wake_cnf_armed, (wake_cnf_armed >> 16) & 0x3);

    LOG_INF("T5838: AAD armed — WAKE=%d isr=%u", gpio_pin_get_raw(pin_wake.port, pin_wake.pin), aad_isr_count);
    return 0;

cleanup_error:
    aad_sleeping = false;
    aad_armed_at_ms = 0;
    T5838_PDM_REG->PSEL.CLK = NRF_GPIO_PIN_MAP(1, 1);
    T5838_PDM_REG->PSEL.DIN = NRF_GPIO_PIN_MAP(1, 0);
    gpio_pin_configure(gpio1_dev, T5838_PDM_CLK_PIN, GPIO_DISCONNECTED);
    gpio_pin_configure_dt(&pin_thsel, GPIO_OUTPUT_INACTIVE);
    mic_resume();
    return err ? err : -EIO;
}

void t5838_aad_exit_sleep(void)
{
    if (!aad_initialized) {
        return;
    }

    aad_sleeping = false;
    aad_armed_at_ms = 0;

    /* Disable WAKE interrupt */
    gpio_pin_interrupt_configure_dt(&pin_wake, GPIO_INT_DISABLE);
    wake_sense_disable();

    /* Restore WAKE to input with pull-down when leaving AAD sleep */
    gpio_pin_configure_dt(&pin_wake, GPIO_INPUT | GPIO_PULL_DOWN);

    /* Restore PDM PSEL.CLK and PSEL.DIN so PDM peripheral can reclaim them */
    T5838_PDM_REG->PSEL.CLK = NRF_GPIO_PIN_MAP(1, 1);
    T5838_PDM_REG->PSEL.DIN = NRF_GPIO_PIN_MAP(1, 0);

    /* Release CLK pin — set to disconnected so PDM can reclaim via pinctrl */
    gpio_pin_configure(gpio1_dev, T5838_PDM_CLK_PIN, GPIO_DISCONNECTED);

    /* Restore THSEL LOW and reconfigure via Zephyr (undo H0H1 bit-bang mode) */
    gpio_pin_configure_dt(&pin_thsel, GPIO_OUTPUT_INACTIVE);

    /* Mark AAD as needing re-unlock on next sleep entry.
     * Resuming PDM resets the T5838 communication state. */
    aad_unlocked = false;

    /* Resume PDM */
    mic_resume();

    LOG_INF("T5838: AAD exit — mic resumed");
}

bool t5838_aad_is_sleeping(void)
{
    return aad_sleeping;
}

void t5838_aad_set_threshold(bool high_threshold)
{
    if (!aad_initialized) {
        return;
    }
    LOG_INF("T5838: threshold now set via AAD registers, not THSEL pin");
}

void t5838_aad_set_wake_callback(t5838_aad_wake_cb_t cb)
{
    wake_callback = cb;
}

int t5838_aad_read_wake_pin(void)
{
    if (!aad_initialized) {
        return -1;
    }
    return gpio_pin_get_dt(&pin_wake);
}

int t5838_aad_read_wake_pin_raw(void)
{
    if (!aad_initialized) {
        return -1;
    }
    return gpio_pin_get_raw(pin_wake.port, pin_wake.pin);
}

int t5838_aad_read_wake_latch(void)
{
    if (!aad_initialized) {
        return -1;
    }
    return wake_latch_read_and_clear();
}

uint32_t t5838_aad_read_p1_in(void)
{
    return T5838_P1->IN;
}

uint32_t t5838_aad_read_p1_out(void)
{
    return T5838_P1->OUT;
}

uint32_t t5838_aad_get_isr_count(void)
{
    return aad_isr_count;
}

void t5838_aad_selftest_wake_irq(void)
{
    if (!aad_initialized || !aad_sleeping) {
        return;
    }

    /*
     * Self-test: read current WAKE state and report.
     * Datasheet polarity: WAKE LOW = idle (no sound), WAKE HIGH = sound detected.
     *
     * In this build, WAKE=1 is only a raw electrical observation. It may mean:
     * - real sound above threshold,
     * - WAKE stuck high through level shifting, or
     * - AAD configuration did not stay latched.
     */
    int wake_now = (T5838_P1->IN & T5838_WAKE_BIT) ? 1 : 0;
    uint32_t isr_now = aad_isr_count;
    uint32_t wake_cnf = T5838_P1->PIN_CNF[T5838_WAKE_PIN];

    LOG_INF("T5838: self-test: WAKE=%d isr=%u WAKE_CNF=0x%08X (SENSE=%d)",
            wake_now,
            isr_now,
            wake_cnf,
            (wake_cnf >> 16) & 0x3);
    LOG_INF("T5838: self-test: WAKE=%d means %s",
            wake_now,
            wake_now ? "line is HIGH (sound or stuck-high / AAD-not-latched)" : "idle/quiet (AAD listening)");
}
