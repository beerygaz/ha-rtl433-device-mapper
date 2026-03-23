# RTL-433 Device Mapper for Home Assistant

A Home Assistant custom integration that discovers 433 MHz devices via [rtl_433](https://github.com/merbanan/rtl_433) MQTT output and lets you **choose which devices to add to HA** — keeping transient devices (neighbours' tyre sensors, passing car remotes) out of your dashboard.

## Why?

The standard `rtl_433_mqtt_hass.py` auto-discovery script publishes **every device** rtl_433 hears as a Home Assistant entity. In practice, a 433 MHz radio picks up dozens of transient devices — TPMS sensors from passing cars, neighbours' weather stations, random remotes — polluting HA with hundreds of junk entities.

This integration puts you in control:

- **Discover** — all devices rtl_433 hears are logged
- **Approve** — only devices you explicitly approve get HA entities
- **Ignore** — blocklist devices so they don't keep appearing
- **Merge** — when a device changes its rolling ID (common with Acurite sensors after battery replacement), merge the new ID into your existing device — HA entities keep working, history preserved

## Architecture

This integration is a **provisioning layer only** — it never republishes telemetry data.

```
rtl_433 → MQTT broker → raw device topics (e.g. rtl_433/.../temperature_C)
                              ↑
                              | HA entities subscribe directly to raw topics
                              |
This integration → publishes HA MQTT discovery configs only
                   (homeassistant/sensor/.../config)
```

If this integration goes down, your HA entities **keep receiving data** — they subscribe directly to rtl_433's raw MQTT topics. The integration only needs to be running to:
- Discover new devices
- Approve/ignore devices
- Handle ID changes (merge)

## Prerequisites

### 1. rtl_433 running and publishing to MQTT

rtl_433 must be configured to publish both **events** and **device topics** to your MQTT broker. This integration subscribes to the event stream to discover devices, and configures HA entities to subscribe to the per-field device topics for state updates.

### 2. MQTT Broker

An MQTT broker accessible by both rtl_433 and Home Assistant. The [Mosquitto add-on](https://github.com/home-assistant/addons/tree/master/mosquitto) is the standard choice.

### 3. HA MQTT Integration

The [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) must be configured in Home Assistant and connected to the same broker.

---

## rtl_433 Configuration

### Required Settings

rtl_433 must publish to MQTT with both **events** and **device** output enabled. The exact configuration depends on how you run rtl_433 (container, service, CLI).

#### Key flags / config options

| Setting | Value | Why |
|---|---|---|
| `-F mqtt://broker:1883` | MQTT output | Enable MQTT publishing |
| `-M time:utc` | UTC timestamps | Consistent time across devices |
| `-C si` | SI units (recommended) | Metric output — °C, km/h, mm, km |
| `-R 0` | No raw output | Reduces MQTT noise |

#### Example: rtl_433 command line
```bash
rtl_433 -C si -M time:utc -F mqtt://mqtt-broker:1883,retain=1,devices=rtl_433/[ID]/devices[/type][/model][/subtype][/channel][/id]
```

#### Example: rtl_433.conf
```conf
# Output
output mqtt://mqtt-broker:1883,retain=1
output mqtt://mqtt-broker:1883,events=rtl_433/[ID]/events,devices=rtl_433/[ID]/devices[/type][/model][/subtype][/channel][/id]

# Units
convert si

# Timestamps
report_meta time:utc
```

#### Example: Docker container (docker-compose.yml)
```yaml
services:
  rtl433:
    image: hertzg/rtl_433
    restart: unless-stopped
    devices:
      - /dev/bus/usb  # USB SDR dongle
    command: >
      -C si
      -M time:utc
      -F mqtt://mqtt-broker:1883,retain=1,devices=rtl_433/[ID]/devices[/type][/model][/subtype][/channel][/id]
```

### MQTT Topic Structure

rtl_433 publishes to two types of topics:

#### Event topics (this integration subscribes to these)
```
rtl_433/<bridge_id>/events
```
Contains the full JSON payload for each received message:
```json
{
  "time": "2026-03-23T10:39:20Z",
  "model": "Acurite-6045M",
  "id": 92,
  "channel": "A",
  "battery_ok": 1,
  "temperature_C": 23.1,
  "humidity": 73,
  "strike_count": 46,
  "storm_dist": 27,
  "active": 1,
  "snr": 25.99
}
```

#### Device topics (HA entities subscribe to these)
```
rtl_433/<bridge_id>/devices/<model>/<channel>/<id>/<field>
```
Each field is published as a separate topic with just the value:
```
rtl_433/5d78552d9bf7/devices/Acurite-6045M/A/92/temperature_C → 23.1
rtl_433/5d78552d9bf7/devices/Acurite-6045M/A/92/humidity → 73
rtl_433/5d78552d9bf7/devices/Acurite-6045M/A/92/strike_count → 46
```

### Verifying rtl_433 MQTT Output

To check that rtl_433 is publishing correctly, subscribe to the event topic:

```bash
# Using mosquitto_sub (from the mosquitto-clients package)
mosquitto_sub -h mqtt-broker -t 'rtl_433/+/events' -v

# Or with authentication
mosquitto_sub -h mqtt-broker -u username -P password -t 'rtl_433/+/events' -v
```

You should see JSON payloads appearing as devices transmit. If nothing appears:
1. Check rtl_433 is running: `docker logs rtl433` or `systemctl status rtl_433`
2. Check the SDR dongle is detected: `lsusb | grep -i rtl`
3. Check MQTT connectivity: `mosquitto_pub -h mqtt-broker -t test -m hello`

---

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three dots (⋮) → **Custom repositories**
3. Add: `https://github.com/beerygaz/ha-rtl433-device-mapper`
4. Category: **Integration**
5. Click **Download**
6. Restart Home Assistant

### Manual

1. Copy `custom_components/rtl433_device_mapper/` to your HA `config/custom_components/` directory
2. Restart Home Assistant

### Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **RTL-433 Device Mapper**
3. Configure:
   - **Event topic pattern**: `rtl_433/+/events` (default — matches all rtl_433 bridges)
   - **Unit system**: SI (default) or Customary — must match your rtl_433 `-C` flag
4. The integration starts listening for devices immediately

---

## Usage

### Discovering Devices

Once configured, the integration subscribes to rtl_433 events and logs every device it sees. Go to **Settings → Devices & Services → RTL-433 Device Mapper → Configure** to see discovered devices.

Each device shows:
- Model name and ID
- Fields available (temperature, humidity, wind, etc.)
- Message count and last seen time
- Signal quality (0-5, derived from SNR)

### Approving Devices

Select a discovered device and click **Approve** to create HA entities. You can set a friendly name (alias) — e.g. "Weather Station" instead of "Acurite-5n1-C-448".

Approved devices get MQTT auto-discovery topics published, and HA creates entities automatically.

### Ignoring Devices

Click **Ignore** on devices you don't want (neighbour's sensors, passing cars). Ignored devices are blocklisted and won't appear in the discovered list again.

### Handling ID Changes (Merge)

Acurite and other 433 MHz sensors sometimes change their rolling ID after a battery replacement or randomly. When this happens:

1. The old device stops updating (goes stale)
2. A new device appears with the same model and channel but a different ID
3. The integration detects this and suggests a merge
4. Confirm the merge → HA entities seamlessly switch to the new ID
5. No history loss, no dashboard changes, no manual reconfiguration

### Services

| Service | Description |
|---|---|
| `rtl433_device_mapper.approve_device` | Approve a discovered device |
| `rtl433_device_mapper.ignore_device` | Ignore (blocklist) a device |
| `rtl433_device_mapper.merge_device` | Merge a new device ID into an existing approved device |
| `rtl433_device_mapper.reset_device` | Reset a device back to discovered state |

---

## Supported Devices

This integration works with **any device rtl_433 can decode** — over 200 device types. Field mappings are included for:

- **Weather stations** — temperature, humidity, wind speed/direction, rain, pressure, UV
- **Lightning detectors** — strike count, distance, storm active
- **Soil/moisture sensors** — moisture, temperature
- **Power meters** — energy, power, current, voltage
- **Door/window sensors** — open/close, tamper, alarm
- **TPMS** — tyre pressure (if you actually want it)
- **Doorbells** — button press triggers

### Signal Quality

Any device reporting SNR (signal-to-noise ratio) automatically gets a **Signal Quality** sensor (0–5 scale):

| Score | Label | SNR |
|---|---|---|
| 0 | Unusable | < 5 dB |
| 1 | Poor | 5–10 dB |
| 2 | Weak | 10–15 dB |
| 3 | Fair | 15–20 dB |
| 4 | Good | 20–30 dB |
| 5 | Excellent | > 30 dB |

---

## CLI Test Tool

A standalone test tool is included for debugging without Home Assistant:

```bash
# Listen for 60 seconds and show discovered devices
python3 test_discover.py -H mqtt-broker -u user -P pass --timeout 60

# Show raw MQTT messages
python3 test_discover.py -H mqtt-broker -u user -P pass --dump

# Auto-approve all devices and show what HA entities would be created
python3 test_discover.py -H mqtt-broker -u user -P pass --approve --timeout 30
```

Requires: `pip install paho-mqtt`

---

## Configuration Options

| Option | Default | Description |
|---|---|---|
| Event topic | `rtl_433/+/events` | MQTT topic pattern for rtl_433 events |
| Discovery prefix | `homeassistant` | HA MQTT discovery topic prefix |
| Unit system | `si` | `si` (metric) or `customary` (imperial) — must match rtl_433 `-C` flag |
| Expire after | `0` (disabled) | Seconds with no update before entity goes unavailable |
| Stale timeout | `3600` (1 hour) | Seconds before a device is considered stale (for merge detection) |
| Force update | `false` | Publish state even when value hasn't changed |

---

## License

MIT
