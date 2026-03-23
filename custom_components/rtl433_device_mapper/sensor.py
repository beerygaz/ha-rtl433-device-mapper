"""Sensor platform for RTL-433 Device Mapper.

Creates native HA sensor entities for each approved rtl_433 device.
Each entity subscribes directly to its specific rtl_433 MQTT topic.

This replaces the MQTT auto-discovery approach (which doesn't work when
publishing from within HA's own MQTT client).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    CONF_DEVICE_ID,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    FIELD_MAPPINGS,
    MANUFACTURER,
)

_LOGGER = logging.getLogger(__name__)

# Map HA device_class string names → SensorDeviceClass enum values
_SENSOR_DEVICE_CLASS_MAP: dict[str, SensorDeviceClass] = {
    "battery": SensorDeviceClass.BATTERY,
    "carbon_dioxide": SensorDeviceClass.CO2,
    "current": SensorDeviceClass.CURRENT,
    "energy": SensorDeviceClass.ENERGY,
    "humidity": SensorDeviceClass.HUMIDITY,
    "illuminance": SensorDeviceClass.ILLUMINANCE,
    "moisture": SensorDeviceClass.MOISTURE,
    "pm10": SensorDeviceClass.PM10,
    "pm25": SensorDeviceClass.PM25,
    "power": SensorDeviceClass.POWER,
    "precipitation": SensorDeviceClass.PRECIPITATION,
    "precipitation_intensity": SensorDeviceClass.PRECIPITATION_INTENSITY,
    "pressure": SensorDeviceClass.PRESSURE,
    "signal_strength": SensorDeviceClass.SIGNAL_STRENGTH,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "timestamp": SensorDeviceClass.TIMESTAMP,
    "voltage": SensorDeviceClass.VOLTAGE,
    "wind_speed": SensorDeviceClass.WIND_SPEED,
}

_BINARY_DEVICE_CLASS_MAP: dict[str, BinarySensorDeviceClass] = {
    "moisture": BinarySensorDeviceClass.MOISTURE,
    "power": BinarySensorDeviceClass.POWER,
    "safety": BinarySensorDeviceClass.SAFETY,
}

_STATE_CLASS_MAP: dict[str, SensorStateClass] = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
    "total": SensorStateClass.TOTAL,
}

_ENTITY_CATEGORY_MAP: dict[str, EntityCategory] = {
    "diagnostic": EntityCategory.DIAGNOSTIC,
    "config": EntityCategory.CONFIG,
}


def _coerce_value(raw: str, config: dict) -> Any:
    """Apply basic type coercion based on the field config.

    The FIELD_MAPPINGS use Jinja2 value_templates — we translate the
    common patterns here into native Python operations. For fields where
    the template is just a float/int cast we do that; for battery_ok's
    special formula we handle it explicitly.
    """
    template = config.get("value_template", "")
    try:
        if "battery_ok" in template or "float(value) * 99" in template:
            # battery_ok: 0 → 1%, 1 → 100%
            return round(float(raw) * 99) + 1

        if "|int" in template or "value|int" in template:
            return int(float(raw))

        if "|float" in template or "float(value" in template:
            # Check for rounding
            if "round(1)" in template:
                return round(float(raw), 1)
            if "round(2)" in template:
                return round(float(raw), 2)
            if "* 3.6" in template:
                # m/s → km/h
                if "round(2)" in template:
                    return round(float(raw) * 3.6, 2)
                return float(raw) * 3.6
            return float(raw)
    except (ValueError, TypeError):
        pass

    # Fall through — return raw string
    return raw


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for an approved rtl_433 device entry."""
    device_id = entry.data.get(CONF_DEVICE_ID)
    if not device_id:
        _LOGGER.error("Device entry %s missing device_id", entry.entry_id)
        return

    # Find the hub engine to get device details
    from . import _get_hub_data

    hub_data = _get_hub_data(hass)
    if not hub_data:
        _LOGGER.warning(
            "No hub found when setting up sensors for %s — skipping",
            device_id,
        )
        return

    engine = hub_data["engine"]
    device = engine.devices.get(device_id)

    if not device:
        _LOGGER.warning(
            "Device %s not found in engine when setting up sensors", device_id
        )
        return

    alias = entry.title  # The user-visible name (alias)

    device_info = DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name=alias,
        model=device.model,
        manufacturer=MANUFACTURER,
    )

    entities: list[SensorEntity | BinarySensorEntity] = []

    for field_key, field_value in device.fields_seen.items():
        if field_key not in FIELD_MAPPINGS:
            continue

        mapping = FIELD_MAPPINGS[field_key]
        device_type = mapping.get("device_type", "sensor")

        # Skip device_automation — no entity type for those
        if device_type == "device_automation":
            continue

        mqtt_topic = f"{device.base_topic}/{field_key}"
        config = mapping["config"]

        if device_type == "binary_sensor":
            entity = RTL433BinarySensor(
                device_id=device_id,
                field_key=field_key,
                mqtt_topic=mqtt_topic,
                config=config,
                device_info=device_info,
                alias=alias,
            )
        else:
            entity = RTL433Sensor(
                device_id=device_id,
                field_key=field_key,
                mqtt_topic=mqtt_topic,
                config=config,
                device_info=device_info,
                alias=alias,
            )

        entities.append(entity)
        _LOGGER.debug(
            "Created %s entity for %s/%s → %s",
            device_type,
            device_id,
            field_key,
            mqtt_topic,
        )

    if entities:
        async_add_entities(entities)
        _LOGGER.info(
            "Added %d sensor entities for device %s (%s)",
            len(entities),
            device_id,
            alias,
        )
    else:
        _LOGGER.warning(
            "No mappable fields found for device %s (fields_seen=%s)",
            device_id,
            list(device.fields_seen.keys()),
        )


class RTL433Sensor(SensorEntity):
    """A native HA sensor entity for a single rtl_433 device field.

    Subscribes directly to the rtl_433 MQTT topic for this field.
    Updates its state whenever a new value arrives.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        device_id: str,
        field_key: str,
        mqtt_topic: str,
        config: dict,
        device_info: DeviceInfo,
        alias: str,
    ) -> None:
        self._device_id = device_id
        self._field_key = field_key
        self._mqtt_topic = mqtt_topic
        self._config = config
        self._unsub_mqtt: Any = None

        # Unique ID: stable per device+field combo
        self._attr_unique_id = f"rtl433_{device_id}_{field_key}"
        self._attr_device_info = device_info

        # Entity name = the sensor's friendly name (e.g. "Temperature")
        self._attr_name = config.get("name", field_key)

        # Device class
        dc_str = config.get("device_class")
        self._attr_device_class = _SENSOR_DEVICE_CLASS_MAP.get(dc_str) if dc_str else None

        # Unit of measurement
        self._attr_native_unit_of_measurement = config.get("unit_of_measurement")

        # State class
        sc_str = config.get("state_class")
        self._attr_state_class = _STATE_CLASS_MAP.get(sc_str) if sc_str else None

        # Entity category
        ec_str = config.get("entity_category")
        self._attr_entity_category = _ENTITY_CATEGORY_MAP.get(ec_str) if ec_str else None

        # Enabled by default
        enabled = config.get("enabled_by_default", True)
        if isinstance(enabled, bool):
            self._attr_entity_registry_enabled_default = enabled

        # Icon
        icon = config.get("icon")
        if icon:
            self._attr_icon = icon

        # Initial state unknown
        self._attr_native_value = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to the MQTT topic when entity is added to HA."""
        self._unsub_mqtt = await mqtt.async_subscribe(
            self.hass,
            self._mqtt_topic,
            self._message_received,
            qos=0,
        )
        _LOGGER.debug(
            "Subscribed to %s for entity %s", self._mqtt_topic, self.unique_id
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from MQTT when entity is removed."""
        if self._unsub_mqtt:
            self._unsub_mqtt()
            self._unsub_mqtt = None

    @callback
    def _message_received(self, msg: mqtt.ReceiveMessage) -> None:
        """Handle incoming MQTT message — update entity state."""
        try:
            raw = msg.payload
            if raw is None or raw == "":
                return
            coerced = _coerce_value(str(raw), self._config)
            self._attr_native_value = coerced
            self.async_write_ha_state()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Error processing MQTT message for %s: %s", self.unique_id, err
            )


class RTL433BinarySensor(BinarySensorEntity):
    """A native HA binary sensor entity for a single rtl_433 device field.

    Subscribes directly to the rtl_433 MQTT topic for this field.
    payload_on / payload_off in the config determine the ON/OFF mapping.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        device_id: str,
        field_key: str,
        mqtt_topic: str,
        config: dict,
        device_info: DeviceInfo,
        alias: str,
    ) -> None:
        self._device_id = device_id
        self._field_key = field_key
        self._mqtt_topic = mqtt_topic
        self._config = config
        self._unsub_mqtt: Any = None

        self._attr_unique_id = f"rtl433_{device_id}_{field_key}"
        self._attr_device_info = device_info
        self._attr_name = config.get("name", field_key)

        dc_str = config.get("device_class")
        self._attr_device_class = _BINARY_DEVICE_CLASS_MAP.get(dc_str) if dc_str else None

        ec_str = config.get("entity_category")
        self._attr_entity_category = _ENTITY_CATEGORY_MAP.get(ec_str) if ec_str else None

        self._payload_on = str(config.get("payload_on", "1"))
        self._payload_off = str(config.get("payload_off", "0"))

        self._attr_is_on = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to the MQTT topic when entity is added to HA."""
        self._unsub_mqtt = await mqtt.async_subscribe(
            self.hass,
            self._mqtt_topic,
            self._message_received,
            qos=0,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from MQTT when entity is removed."""
        if self._unsub_mqtt:
            self._unsub_mqtt()
            self._unsub_mqtt = None

    @callback
    def _message_received(self, msg: mqtt.ReceiveMessage) -> None:
        """Handle incoming MQTT message — update entity state."""
        try:
            raw = str(msg.payload).strip()
            if raw == self._payload_on:
                self._attr_is_on = True
            elif raw == self._payload_off:
                self._attr_is_on = False
            else:
                # Try numeric coercion — non-zero = on
                try:
                    self._attr_is_on = float(raw) != 0
                except ValueError:
                    self._attr_is_on = raw.lower() not in ("false", "no", "off", "0", "")
            self.async_write_ha_state()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Error processing MQTT message for %s: %s", self.unique_id, err
            )
