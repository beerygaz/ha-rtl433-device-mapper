"""Constants for the RTL-433 MQTT Discovery integration (V2 — provisioning only).

This module has NO Home Assistant imports — it must be loadable standalone
by the CLI test harness.
"""

DOMAIN = "rtl433_device_mapper"
MANUFACTURER = "rtl_433"

# ── Config keys ──────────────────────────────────────────────────────────────

CONF_RTL_TOPIC = "rtl_topic"
CONF_DISCOVERY_PREFIX = "discovery_prefix"
CONF_DEVICE_TOPIC_SUFFIX = "device_topic_suffix"
CONF_EXPIRE_AFTER = "expire_after"
CONF_FORCE_UPDATE = "force_update"
CONF_RETAIN = "retain"
CONF_STALE_TIMEOUT = "stale_timeout"
CONF_UNIT_SYSTEM = "unit_system"

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_RTL_TOPIC = "rtl_433/+/events"
DEFAULT_DISCOVERY_PREFIX = "homeassistant"
DEFAULT_DEVICE_TOPIC_SUFFIX = "devices[/type][/model][/subtype][/channel][/id]"
DEFAULT_EXPIRE_AFTER = 0  # 0 = disabled
DEFAULT_FORCE_UPDATE = False
DEFAULT_RETAIN = True
DEFAULT_STALE_TIMEOUT = 3600  # 1 hour in seconds

# ── Unit system ──────────────────────────────────────────────────────────────
UNIT_SYSTEM_SI = "si"
UNIT_SYSTEM_CUSTOMARY = "customary"
DEFAULT_UNIT_SYSTEM = UNIT_SYSTEM_SI

# Fields whose unit is ambiguous (depends on rtl_433 -C flag).
# When the field name doesn't encode the unit (unlike temperature_C / temperature_F),
# we use the global unit_system config to determine the correct unit.
# Format: field_name → { "si": {overrides}, "customary": {overrides} }
UNIT_AWARE_FIELDS = {
    "storm_dist": {
        UNIT_SYSTEM_SI: {"unit_of_measurement": "km"},
        UNIT_SYSTEM_CUSTOMARY: {"unit_of_measurement": "mi"},
    },
    "strike_distance": {
        UNIT_SYSTEM_SI: {"unit_of_measurement": "km"},
        UNIT_SYSTEM_CUSTOMARY: {"unit_of_measurement": "mi"},
    },
    "depth_cm": {
        UNIT_SYSTEM_SI: {"unit_of_measurement": "cm"},
        UNIT_SYSTEM_CUSTOMARY: {"unit_of_measurement": "in"},
    },
}

# ── Storage ──────────────────────────────────────────────────────────────────

STORAGE_KEY = f"{DOMAIN}_registry"
STORAGE_VERSION = 2

# ── Device States ────────────────────────────────────────────────────────────

DEVICE_STATE_DISCOVERED = "discovered"
DEVICE_STATE_APPROVED = "approved"
DEVICE_STATE_IGNORED = "ignored"

# ── Fields to skip when parsing rtl_433 events (metadata, not sensor data) ──

SKIP_KEYS = [
    "type", "model", "subtype", "channel", "id", "mic", "mod",
    "freq", "sequence_num", "message_type", "exception", "raw_msg",
]

# ── Merge Suggestion States ─────────────────────────────────────────────────

MERGE_STATE_PENDING = "pending"
MERGE_STATE_ACCEPTED = "accepted"
MERGE_STATE_DISMISSED = "dismissed"


# ─── Field Mappings ───────────────────────────────────────────────────────────
# Global mapping of rtl_433 field names to Home Assistant discovery metadata.
# Ported directly from rtl_433_mqtt_hass.py — proven in production.
#
# Each entry maps an rtl_433 JSON field to:
#   device_type   - HA component type (sensor, binary_sensor, device_automation)
#   object_suffix - suffix for the unique object_id
#   config        - HA MQTT discovery config payload fields
# ──────────────────────────────────────────────────────────────────────────────

FIELD_MAPPINGS: dict[str, dict] = {
    # ── Temperature ──────────────────────────────────────────────────────────
    "temperature_C": {
        "device_type": "sensor",
        "object_suffix": "T",
        "config": {
            "device_class": "temperature",
            "name": "Temperature",
            "unit_of_measurement": "°C",
            "value_template": "{{ value|float|round(1) }}",
            "state_class": "measurement",
        },
    },
    "temperature_1_C": {
        "device_type": "sensor",
        "object_suffix": "T1",
        "config": {
            "device_class": "temperature",
            "name": "Temperature 1",
            "unit_of_measurement": "°C",
            "value_template": "{{ value|float|round(1) }}",
            "state_class": "measurement",
        },
    },
    "temperature_2_C": {
        "device_type": "sensor",
        "object_suffix": "T2",
        "config": {
            "device_class": "temperature",
            "name": "Temperature 2",
            "unit_of_measurement": "°C",
            "value_template": "{{ value|float|round(1) }}",
            "state_class": "measurement",
        },
    },
    "temperature_3_C": {
        "device_type": "sensor",
        "object_suffix": "T3",
        "config": {
            "device_class": "temperature",
            "name": "Temperature 3",
            "unit_of_measurement": "°C",
            "value_template": "{{ value|float|round(1) }}",
            "state_class": "measurement",
        },
    },
    "temperature_4_C": {
        "device_type": "sensor",
        "object_suffix": "T4",
        "config": {
            "device_class": "temperature",
            "name": "Temperature 4",
            "unit_of_measurement": "°C",
            "value_template": "{{ value|float|round(1) }}",
            "state_class": "measurement",
        },
    },
    "temperature_F": {
        "device_type": "sensor",
        "object_suffix": "F",
        "config": {
            "device_class": "temperature",
            "name": "Temperature",
            "unit_of_measurement": "°F",
            "value_template": "{{ value|float|round(1) }}",
            "state_class": "measurement",
        },
    },
    # ── Timestamp ────────────────────────────────────────────────────────────
    "time": {
        "device_type": "sensor",
        "object_suffix": "UTC",
        "config": {
            "device_class": "timestamp",
            "name": "Timestamp",
            "entity_category": "diagnostic",
            "enabled_by_default": False,
            "icon": "mdi:clock-in",
        },
    },
    # ── Battery ──────────────────────────────────────────────────────────────
    "battery_ok": {
        "device_type": "sensor",
        "object_suffix": "B",
        "config": {
            "device_class": "battery",
            "name": "Battery",
            "unit_of_measurement": "%",
            "value_template": "{{ ((float(value) * 99)|round(0)) + 1 }}",
            "state_class": "measurement",
            "entity_category": "diagnostic",
        },
    },
    "battery_mV": {
        "device_type": "sensor",
        "object_suffix": "mV",
        "config": {
            "device_class": "voltage",
            "name": "Battery mV",
            "unit_of_measurement": "mV",
            "value_template": "{{ float(value) }}",
            "state_class": "measurement",
            "entity_category": "diagnostic",
        },
    },
    "supercap_V": {
        "device_type": "sensor",
        "object_suffix": "V",
        "config": {
            "device_class": "voltage",
            "name": "Supercap V",
            "unit_of_measurement": "V",
            "value_template": "{{ float(value) }}",
            "state_class": "measurement",
            "entity_category": "diagnostic",
        },
    },
    # ── Humidity ─────────────────────────────────────────────────────────────
    "humidity": {
        "device_type": "sensor",
        "object_suffix": "H",
        "config": {
            "device_class": "humidity",
            "name": "Humidity",
            "unit_of_measurement": "%",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "humidity_1": {
        "device_type": "sensor",
        "object_suffix": "H1",
        "config": {
            "device_class": "humidity",
            "name": "Humidity 1",
            "unit_of_measurement": "%",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "humidity_2": {
        "device_type": "sensor",
        "object_suffix": "H2",
        "config": {
            "device_class": "humidity",
            "name": "Humidity 2",
            "unit_of_measurement": "%",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    # ── Moisture ─────────────────────────────────────────────────────────────
    "moisture": {
        "device_type": "sensor",
        "object_suffix": "M",
        "config": {
            "device_class": "moisture",
            "name": "Moisture",
            "unit_of_measurement": "%",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "detect_wet": {
        "device_type": "binary_sensor",
        "object_suffix": "moisture",
        "config": {
            "name": "Water Sensor",
            "device_class": "moisture",
            "force_update": "true",
            "payload_on": "1",
            "payload_off": "0",
        },
    },
    # ── Pressure ─────────────────────────────────────────────────────────────
    "pressure_hPa": {
        "device_type": "sensor",
        "object_suffix": "P",
        "config": {
            "device_class": "pressure",
            "name": "Pressure",
            "unit_of_measurement": "hPa",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "pressure_kPa": {
        "device_type": "sensor",
        "object_suffix": "P",
        "config": {
            "device_class": "pressure",
            "name": "Pressure",
            "unit_of_measurement": "kPa",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    # ── Wind ─────────────────────────────────────────────────────────────────
    "wind_speed_km_h": {
        "device_type": "sensor",
        "object_suffix": "WS",
        "config": {
            "device_class": "wind_speed",
            "name": "Wind Speed",
            "unit_of_measurement": "km/h",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "wind_avg_km_h": {
        "device_type": "sensor",
        "object_suffix": "WS",
        "config": {
            "device_class": "wind_speed",
            "name": "Wind Speed",
            "unit_of_measurement": "km/h",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "wind_avg_mi_h": {
        "device_type": "sensor",
        "object_suffix": "WS",
        "config": {
            "device_class": "wind_speed",
            "name": "Wind Speed",
            "unit_of_measurement": "mi/h",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "wind_avg_m_s": {
        "device_type": "sensor",
        "object_suffix": "WS",
        "config": {
            "device_class": "wind_speed",
            "name": "Wind Average",
            "unit_of_measurement": "km/h",
            "value_template": "{{ (float(value|float) * 3.6) | round(2) }}",
            "state_class": "measurement",
        },
    },
    "wind_speed_m_s": {
        "device_type": "sensor",
        "object_suffix": "WS",
        "config": {
            "device_class": "wind_speed",
            "name": "Wind Speed",
            "unit_of_measurement": "km/h",
            "value_template": "{{ float(value|float) * 3.6 }}",
            "state_class": "measurement",
        },
    },
    "gust_speed_km_h": {
        "device_type": "sensor",
        "object_suffix": "GS",
        "config": {
            "device_class": "wind_speed",
            "name": "Gust Speed",
            "unit_of_measurement": "km/h",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "wind_max_km_h": {
        "device_type": "sensor",
        "object_suffix": "GS",
        "config": {
            "device_class": "wind_speed",
            "name": "Wind max speed",
            "unit_of_measurement": "km/h",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "wind_max_m_s": {
        "device_type": "sensor",
        "object_suffix": "GS",
        "config": {
            "device_class": "wind_speed",
            "name": "Wind max",
            "unit_of_measurement": "km/h",
            "value_template": "{{ (float(value|float) * 3.6) | round(2) }}",
            "state_class": "measurement",
        },
    },
    "gust_speed_m_s": {
        "device_type": "sensor",
        "object_suffix": "GS",
        "config": {
            "device_class": "wind_speed",
            "name": "Gust Speed",
            "unit_of_measurement": "km/h",
            "value_template": "{{ float(value|float) * 3.6 }}",
            "state_class": "measurement",
        },
    },
    "wind_dir_deg": {
        "device_type": "sensor",
        "object_suffix": "WD",
        "config": {
            "name": "Wind Direction",
            "unit_of_measurement": "°",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    # ── Rain ─────────────────────────────────────────────────────────────────
    "rain_mm": {
        "device_type": "sensor",
        "object_suffix": "RT",
        "config": {
            "device_class": "precipitation",
            "name": "Rain Total",
            "unit_of_measurement": "mm",
            "value_template": "{{ value|float|round(2) }}",
            "state_class": "total_increasing",
        },
    },
    "rain_rate_mm_h": {
        "device_type": "sensor",
        "object_suffix": "RR",
        "config": {
            "device_class": "precipitation_intensity",
            "name": "Rain Rate",
            "unit_of_measurement": "mm/h",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "rain_in": {
        "device_type": "sensor",
        "object_suffix": "RT",
        "config": {
            "device_class": "precipitation",
            "name": "Rain Total",
            "unit_of_measurement": "in",
            "value_template": "{{ value|float|round(2) }}",
            "state_class": "total_increasing",
        },
    },
    "rain_rate_in_h": {
        "device_type": "sensor",
        "object_suffix": "RR",
        "config": {
            "device_class": "precipitation_intensity",
            "name": "Rain Rate",
            "unit_of_measurement": "in/h",
            "value_template": "{{ value|float|round(2) }}",
            "state_class": "measurement",
        },
    },
    # ── Binary Sensors ───────────────────────────────────────────────────────
    "reed_open": {
        "device_type": "binary_sensor",
        "object_suffix": "reed_open",
        "config": {
            "device_class": "safety",
            "force_update": "true",
            "payload_on": "1",
            "payload_off": "0",
            "entity_category": "diagnostic",
        },
    },
    "contact_open": {
        "device_type": "binary_sensor",
        "object_suffix": "contact_open",
        "config": {
            "device_class": "safety",
            "force_update": "true",
            "payload_on": "1",
            "payload_off": "0",
            "entity_category": "diagnostic",
        },
    },
    "tamper": {
        "device_type": "binary_sensor",
        "object_suffix": "tamper",
        "config": {
            "device_class": "safety",
            "force_update": "true",
            "payload_on": "1",
            "payload_off": "0",
            "entity_category": "diagnostic",
        },
    },
    "alarm": {
        "device_type": "binary_sensor",
        "object_suffix": "alarm",
        "config": {
            "device_class": "safety",
            "force_update": "true",
            "payload_on": "1",
            "payload_off": "0",
            "entity_category": "diagnostic",
        },
    },
    # ── Signal Quality ───────────────────────────────────────────────────────
    "rssi": {
        "device_type": "sensor",
        "object_suffix": "rssi",
        "config": {
            "device_class": "signal_strength",
            "unit_of_measurement": "dB",
            "value_template": "{{ value|float|round(2) }}",
            "state_class": "measurement",
            "entity_category": "diagnostic",
        },
    },
    "snr": {
        "device_type": "sensor",
        "object_suffix": "snr",
        "config": {
            "device_class": "signal_strength",
            "unit_of_measurement": "dB",
            "value_template": "{{ value|float|round(2) }}",
            "state_class": "measurement",
            "entity_category": "diagnostic",
        },
    },
    "noise": {
        "device_type": "sensor",
        "object_suffix": "noise",
        "config": {
            "device_class": "signal_strength",
            "unit_of_measurement": "dB",
            "value_template": "{{ value|float|round(2) }}",
            "state_class": "measurement",
            "entity_category": "diagnostic",
        },
    },
    # ── Depth ────────────────────────────────────────────────────────────────
    "depth_cm": {
        "device_type": "sensor",
        "object_suffix": "D",
        "config": {
            "name": "Depth",
            "unit_of_measurement": "cm",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    # ── Power / Energy ───────────────────────────────────────────────────────
    "power_W": {
        "device_type": "sensor",
        "object_suffix": "watts",
        "config": {
            "device_class": "power",
            "name": "Power",
            "unit_of_measurement": "W",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "energy_kWh": {
        "device_type": "sensor",
        "object_suffix": "kwh",
        "config": {
            "device_class": "energy",
            "name": "Energy",
            "unit_of_measurement": "kWh",
            "value_template": "{{ value|float }}",
            "state_class": "total_increasing",
        },
    },
    "current_A": {
        "device_type": "sensor",
        "object_suffix": "A",
        "config": {
            "device_class": "current",
            "name": "Current",
            "unit_of_measurement": "A",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "voltage_V": {
        "device_type": "sensor",
        "object_suffix": "V",
        "config": {
            "device_class": "voltage",
            "name": "Voltage",
            "unit_of_measurement": "V",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    # ── Light / UV ───────────────────────────────────────────────────────────
    "light_lux": {
        "device_type": "sensor",
        "object_suffix": "lux",
        "config": {
            "device_class": "illuminance",
            "name": "Outside Luminance",
            "unit_of_measurement": "lx",
            "value_template": "{{ value|int }}",
            "state_class": "measurement",
        },
    },
    "lux": {
        "device_type": "sensor",
        "object_suffix": "lux",
        "config": {
            "device_class": "illuminance",
            "name": "Outside Luminance",
            "unit_of_measurement": "lx",
            "value_template": "{{ value|int }}",
            "state_class": "measurement",
        },
    },
    "uv": {
        "device_type": "sensor",
        "object_suffix": "uv",
        "config": {
            "name": "UV Index",
            "unit_of_measurement": "UV Index",
            "value_template": "{{ value|float|round(1) }}",
            "state_class": "measurement",
        },
    },
    "uvi": {
        "device_type": "sensor",
        "object_suffix": "uvi",
        "config": {
            "name": "UV Index",
            "unit_of_measurement": "UV Index",
            "value_template": "{{ value|float|round(1) }}",
            "state_class": "measurement",
        },
    },
    # ── Lightning ────────────────────────────────────────────────────────────
    "storm_dist_km": {
        "device_type": "sensor",
        "object_suffix": "stdist",
        "config": {
            "name": "Lightning Distance",
            "unit_of_measurement": "km",
            "value_template": "{{ value|int }}",
            "state_class": "measurement",
        },
    },
    "storm_dist": {
        "device_type": "sensor",
        "object_suffix": "stdist",
        "config": {
            "name": "Lightning Distance",
            "unit_of_measurement": "km",
            "value_template": "{{ value|int }}",
            "state_class": "measurement",
        },
    },
    "strike_distance": {
        "device_type": "sensor",
        "object_suffix": "stdist",
        "config": {
            "name": "Lightning Distance",
            "unit_of_measurement": "mi",
            "value_template": "{{ value|int }}",
            "state_class": "measurement",
        },
    },
    "strike_count": {
        "device_type": "sensor",
        "object_suffix": "strcnt",
        "config": {
            "name": "Lightning Strike Count",
            "value_template": "{{ value|int }}",
            "state_class": "total_increasing",
        },
    },
    "active": {
        "device_type": "binary_sensor",
        "object_suffix": "storm_active",
        "config": {
            "name": "Storm Active",
            "device_class": "safety",
            "payload_on": "1",
            "payload_off": "0",
            "icon": "mdi:weather-lightning",
        },
    },
    # ── Consumption (SCM meters) ─────────────────────────────────────────────
    "consumption_data": {
        "device_type": "sensor",
        "object_suffix": "consumption",
        "config": {
            "name": "SCM Consumption Value",
            "value_template": "{{ value|int }}",
            "state_class": "total_increasing",
        },
    },
    "consumption": {
        "device_type": "sensor",
        "object_suffix": "consumption",
        "config": {
            "name": "SCMplus Consumption Value",
            "value_template": "{{ value|int }}",
            "state_class": "total_increasing",
        },
    },
    # ── Device Automations ───────────────────────────────────────────────────
    "channel": {
        "device_type": "device_automation",
        "object_suffix": "CH",
        "config": {
            "automation_type": "trigger",
            "type": "button_short_release",
            "subtype": "button_1",
        },
    },
    "button": {
        "device_type": "device_automation",
        "object_suffix": "BTN",
        "config": {
            "automation_type": "trigger",
            "type": "button_short_release",
            "subtype": "button_2",
        },
    },
    # ── Air Quality ──────────────────────────────────────────────────────────
    "pm2_5_ug_m3": {
        "device_type": "sensor",
        "object_suffix": "PM25",
        "config": {
            "device_class": "pm25",
            "name": "PM 2.5 Concentration",
            "unit_of_measurement": "µg/m³",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "pm10_ug_m3": {
        "device_type": "sensor",
        "object_suffix": "PM10",
        "config": {
            "device_class": "pm10",
            "name": "PM 10 Concentration",
            "unit_of_measurement": "µg/m³",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "estimated_pm10_0_ug_m3": {
        "device_type": "sensor",
        "object_suffix": "PM10",
        "config": {
            "device_class": "pm10",
            "name": "Estimated PM 10 Concentration",
            "unit_of_measurement": "µg/m³",
            "value_template": "{{ value|float }}",
            "state_class": "measurement",
        },
    },
    "co2_ppm": {
        "device_type": "sensor",
        "object_suffix": "CO2",
        "config": {
            "device_class": "carbon_dioxide",
            "name": "CO2 Concentration",
            "unit_of_measurement": "ppm",
            "value_template": "{{ value|int }}",
            "state_class": "measurement",
        },
    },
    # ── External Power ───────────────────────────────────────────────────────
    "ext_power": {
        "device_type": "binary_sensor",
        "object_suffix": "extpwr",
        "config": {
            "device_class": "power",
            "name": "External Power",
            "payload_on": "1",
            "payload_off": "0",
            "entity_category": "diagnostic",
        },
    },
}

# ── Synthetic (computed) mappings ─────────────────────────────────────────────
# These produce additional entities derived from a source field.
# Format: source_field → list of extra mappings (each gets its own entity).
# The entity subscribes to the same raw topic as the source field.
SYNTHETIC_MAPPINGS: dict[str, list[dict]] = {
    "snr": [
        {
            "device_type": "sensor",
            "object_suffix": "SQ",
            "config": {
                "name": "Signal Quality",
                "icon": "mdi:signal",
                "value_template": (
                    "{% set s = value|float %}"
                    "{% if s > 30 %}5"
                    "{% elif s > 20 %}4"
                    "{% elif s > 15 %}3"
                    "{% elif s > 10 %}2"
                    "{% elif s > 5 %}1"
                    "{% else %}0{% endif %}"
                ),
                "json_attributes_template": (
                    '{% set s = value|float %}'
                    '{% set labels = {0: "Unusable", 1: "Poor", 2: "Weak", 3: "Fair", 4: "Good", 5: "Excellent"} %}'
                    '{% set score = 5 if s > 30 else (4 if s > 20 else (3 if s > 15 else (2 if s > 10 else (1 if s > 5 else 0)))) %}'
                    '{{ {"snr_db": s|round(1), "quality_label": labels[score]} | tojson }}'
                ),
                "state_class": "measurement",
            },
        },
    ],
}

# Special multi-mapping for Honeywell ActivLink doorbell secret_knock field.
# Produces two device_automation triggers from a single field.
SECRET_KNOCK_MAPPINGS: list[dict] = [
    {
        "device_type": "device_automation",
        "object_suffix": "Knock",
        "config": {
            "automation_type": "trigger",
            "type": "button_short_release",
            "subtype": "button_1",
            "payload": 0,
        },
    },
    {
        "device_type": "device_automation",
        "object_suffix": "Secret-Knock",
        "config": {
            "automation_type": "trigger",
            "type": "button_triple_press",
            "subtype": "button_1",
            "payload": 1,
        },
    },
]
