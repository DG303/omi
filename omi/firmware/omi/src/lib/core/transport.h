#ifndef TRANSPORT_H
#define TRANSPORT_H

#include <zephyr/drivers/sensor.h>
#ifdef CONFIG_OMI_ENABLE_BATTERY
extern uint8_t battery_percentage;
#endif
/**
 * @brief Initialize the BLE transport logic
 *
 * Initializes the BLE Logic
 *
 * @return 0 if successful, negative errno code if error
 */
int transport_start();

/**
 * @brief Turn off the BLE transport
 *
 * @return 0 if successful, negative errno code if error
 */
int transport_off();

/**
 * @brief Broadcast audio packets over BLE
 *
 * @param buffer Buffer containing audio data
 * @param size Size of the audio data
 * @return 0 if successful, negative errno code if error
 */
int broadcast_audio_packets(uint8_t *buffer, size_t size);

/**
 * @brief Get the current BLE connection
 *
 * @return Pointer to current connection, or NULL if not connected
 */
struct bt_conn *get_current_connection();

/**
 * @brief Switch BLE advertising to slow interval for low-power mode.
 *
 * Uses ~1 s interval instead of the default fast interval.
 * Only effective when not connected (advertising only).
 *
 * @return 0 if successful, negative errno code if error
 */
int transport_set_adv_slow(void);

/**
 * @brief Switch BLE advertising back to normal fast interval.
 *
 * @return 0 if successful, negative errno code if error
 */
int transport_set_adv_fast(void);

#endif // TRANSPORT_H
