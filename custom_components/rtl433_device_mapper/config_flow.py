"""Config flow for RTL-433 MQTT Discovery Bridge V2.

Provides:
  1. Initial setup flow — rtl_433 event topic configuration
  2. Integration discovery flow — one per discovered 433 MHz device
     Shows as "Discovered: RTL-433 Device Mapper — Acurite-5n1 (C-448)"
     on the Integrations page. User can confirm (with alias) or ignore.
  3. Options flow — device management (approve/ignore/alias/merge) + settings
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_TOPIC_SUFFIX,
    CONF_DISCOVERY_PREFIX,
    CONF_EXPIRE_AFTER,
    CONF_FORCE_UPDATE,
    CONF_RETAIN,
    CONF_RTL_TOPIC,
    CONF_STALE_TIMEOUT,
    DEFAULT_DEVICE_TOPIC_SUFFIX,
    DEFAULT_DISCOVERY_PREFIX,
    DEFAULT_EXPIRE_AFTER,
    DEFAULT_FORCE_UPDATE,
    DEFAULT_RETAIN,
    DEFAULT_RTL_TOPIC,
    DEFAULT_STALE_TIMEOUT,
    DEVICE_STATE_APPROVED,
    DEVICE_STATE_DISCOVERED,
    DEVICE_STATE_IGNORED,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_HUB,
)

_LOGGER = logging.getLogger(__name__)

# Fields worth showing to user as sensor capabilities
_KNOWN_SENSOR_FIELDS = {
    "temperature_C", "temperature_F", "temperature_1_C", "temperature_2_C",
    "temperature_3_C", "temperature_4_C", "humidity", "humidity_1", "humidity_2",
    "pressure_hPa", "pressure_kPa", "wind_speed_km_h", "wind_avg_km_h",
    "wind_avg_m_s", "wind_speed_m_s", "gust_speed_km_h", "wind_max_km_h",
    "wind_dir_deg", "rain_mm", "rain_rate_mm_h", "rain_in", "rain_rate_in_h",
    "battery_ok", "battery_mV", "rssi", "snr", "light_lux", "lux", "uv", "uvi",
    "pm2_5_ug_m3", "pm10_ug_m3", "co2_ppm", "moisture", "power_W", "energy_kWh",
    "voltage_V", "current_A", "depth_cm",
}

_FIELD_LABELS = {
    "temperature_C": "Temperature (°C)",
    "temperature_F": "Temperature (°F)",
    "humidity": "Humidity (%)",
    "pressure_hPa": "Pressure (hPa)",
    "wind_speed_km_h": "Wind Speed",
    "wind_avg_km_h": "Wind Average",
    "wind_dir_deg": "Wind Direction",
    "rain_mm": "Rain Total",
    "rain_rate_mm_h": "Rain Rate",
    "battery_ok": "Battery",
    "rssi": "Signal Strength",
    "snr": "Signal/Noise",
    "light_lux": "Light",
    "uv": "UV Index",
}


class RTL433DiscoveryConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle configuration of the RTL-433 Discovery Bridge.

    V2 uses HA's built-in MQTT integration (no separate MQTT creds needed).
    We only need the rtl_433 event topic pattern.
    """

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step — rtl_433 topic configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            rtl_topic = user_input.get(CONF_RTL_TOPIC, DEFAULT_RTL_TOPIC)

            # Prevent duplicate hub entries
            await self.async_set_unique_id(f"rtl433_{rtl_topic}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"RTL-433 Discovery ({rtl_topic})",
                data={
                    **user_input,
                    "entry_type": ENTRY_TYPE_HUB,
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_RTL_TOPIC, default=DEFAULT_RTL_TOPIC
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> FlowResult:
        """Handle discovery of a new rtl_433 device.

        This is called when the MQTT handler spots a new device.
        Shows a "Discovered" card on the Integrations page.
        """
        device_id = discovery_info["device_id"]
        model = discovery_info.get("model", device_id)
        channel = discovery_info.get("channel", "")
        raw_id = discovery_info.get("raw_id", "")

        _LOGGER.info(
            "Integration discovery flow started for device: %s (model=%s)",
            device_id,
            model,
        )

        # Each device gets a unique config entry — abort if already configured
        await self.async_set_unique_id(f"rtl433_device_{device_id}")
        self._abort_if_unique_id_configured()

        # Store discovery info for the confirm step
        self._discovery_info = discovery_info

        # Set the context title shown in the "Discovered" card
        self.context["title_placeholders"] = {
            "model": model,
            "device_id": device_id,
        }

        return await self.async_step_confirm_device()

    async def async_step_confirm_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show device confirmation form — set alias and confirm approval."""
        device_id = self._discovery_info.get("device_id", "")
        model = self._discovery_info.get("model", device_id)
        channel = self._discovery_info.get("channel", "")
        raw_id = self._discovery_info.get("raw_id", "")
        hub_entry_id = self._discovery_info.get("hub_entry_id", "")
        fields_seen = self._discovery_info.get("fields_seen", [])

        # Build readable field list for description
        known_fields = [
            _FIELD_LABELS.get(f, f)
            for f in fields_seen
            if f in _KNOWN_SENSOR_FIELDS
        ]
        fields_str = ", ".join(known_fields[:6]) if known_fields else "Unknown"

        if user_input is not None:
            alias = user_input.get("alias", "").strip() or device_id

            # Find the hub data and approve the device in the engine
            from . import _get_hub_data, _republish_all_approved
            hub_data = _get_hub_data(self.hass)

            if hub_data:
                engine = hub_data["engine"]
                retain = hub_data["retain"]
                store = hub_data["store"]

                engine.approve_device(device_id, alias)
                _republish_all_approved(self.hass, engine, retain)
                await store.async_save(engine.save_state())
                _LOGGER.info(
                    "Device approved via discovery flow: %s → %s", device_id, alias
                )

            # Create a device config entry to persist this approval
            return self.async_create_entry(
                title=alias,
                data={
                    "entry_type": ENTRY_TYPE_DEVICE,
                    CONF_DEVICE_ID: device_id,
                    "model": model,
                    "hub_entry_id": hub_entry_id,
                },
            )

        # Build default alias from model + channel/id
        if channel:
            default_alias = f"{model} {channel}"
        elif raw_id:
            default_alias = f"{model} {raw_id}"
        else:
            default_alias = model

        return self.async_show_form(
            step_id="confirm_device",
            data_schema=vol.Schema(
                {
                    vol.Optional("alias", default=default_alias): str,
                }
            ),
            description_placeholders={
                "device_id": device_id,
                "model": model,
                "channel": str(channel) if channel else "—",
                "raw_id": str(raw_id) if raw_id else "—",
                "fields": fields_str,
            },
        )

    async def async_step_ignore(self, user_input: dict) -> FlowResult:
        """Handle user ignoring this device discovery.

        HA calls this when user clicks 'Ignore' on the discovery card.
        We override to also update the engine's device state.
        """
        device_id = self._discovery_info.get("device_id", "")
        if device_id:
            from . import _get_hub_data
            hub_data = _get_hub_data(self.hass)
            if hub_data:
                engine = hub_data["engine"]
                store = hub_data["store"]
                if engine.ignore_device(device_id):
                    _LOGGER.info("Device ignored via UI: %s", device_id)
                    self.hass.async_create_task(
                        store.async_save(engine.save_state())
                    )

        # Delegate to HA's standard ignore flow (creates SOURCE_IGNORE config entry)
        return await super().async_step_ignore(user_input)

    def is_matching(self, other_flow: RTL433DiscoveryConfigFlow) -> bool:
        """Prevent duplicate in-progress flows for the same device."""
        return (
            self._discovery_info.get("device_id")
            == other_flow._discovery_info.get("device_id")
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler appropriate for the entry type."""
        if config_entry.data.get("entry_type") == ENTRY_TYPE_DEVICE:
            return RTL433DeviceOptionsFlow(config_entry)
        return RTL433DiscoveryOptionsFlow(config_entry)


class RTL433DeviceOptionsFlow(config_entries.OptionsFlow):
    """Options flow for a single approved device.

    Allows renaming the device or resetting it back to discovered state.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Device options — rename or reset."""
        device_id = self._config_entry.data.get(CONF_DEVICE_ID, "")
        current_alias = self._config_entry.title

        if user_input is not None:
            action = user_input.get("action", "rename")

            if action == "reset":
                # Reset device back to discovered state
                from . import _get_hub_data, _remove_device_from_ha
                hub_data = _get_hub_data(self.hass)
                if hub_data:
                    engine = hub_data["engine"]
                    retain = hub_data["retain"]
                    store = hub_data["store"]
                    engine.reset_device(device_id)
                    _remove_device_from_ha(self.hass, engine, device_id, retain)
                    await store.async_save(engine.save_state())
                    _LOGGER.info(
                        "Device reset via options flow: %s", device_id
                    )

                # Remove the device config entry
                self.hass.async_create_task(
                    self.hass.config_entries.async_remove(
                        self._config_entry.entry_id
                    )
                )
                return self.async_create_entry(title="", data={})

            # Rename
            new_alias = user_input.get("alias", current_alias).strip() or current_alias
            from . import _get_hub_data, _republish_all_approved
            hub_data = _get_hub_data(self.hass)
            if hub_data:
                engine = hub_data["engine"]
                retain = hub_data["retain"]
                store = hub_data["store"]
                engine.set_alias(device_id, new_alias)
                _republish_all_approved(self.hass, engine, retain)
                await store.async_save(engine.save_state())

            # Update the config entry title
            self.hass.config_entries.async_update_entry(
                self._config_entry, title=new_alias
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("alias", default=current_alias): str,
                    vol.Required("action", default="rename"): vol.In(
                        {
                            "rename": "Rename device",
                            "reset": "Reset to discovered state (removes entities)",
                        }
                    ),
                }
            ),
            description_placeholders={
                "device_id": device_id,
                "current_name": current_alias,
            },
        )


class RTL433DiscoveryOptionsFlow(config_entries.OptionsFlow):
    """Options flow for the hub config entry.

    Flow steps:
      1. init — Main menu
      2. manage_devices — Approve/ignore devices with alias support
      3. merge_devices — Handle merge suggestions for rolling-ID sensors
      4. settings — Discovery prefix, stale timeout, etc.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Options menu — choose what to configure."""
        if user_input is not None:
            next_step = user_input.get("next_step", "manage_devices")
            if next_step == "settings":
                return await self.async_step_settings()
            if next_step == "merge_devices":
                return await self.async_step_merge_devices()
            return await self.async_step_manage_devices()

        # Check for merge suggestions to show in menu
        from . import _get_hub_data
        hub_data = _get_hub_data(self.hass)
        merge_count = 0
        if hub_data:
            engine = hub_data["engine"]
            merge_count = len(engine.get_merge_summary())

        menu_options = {
            "manage_devices": "Manage Devices (approve/ignore/alias)",
            "settings": "Discovery Settings",
        }
        if merge_count > 0:
            menu_options["merge_devices"] = (
                f"Merge Suggestions ({merge_count} pending)"
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("next_step", default="manage_devices"): vol.In(
                        menu_options
                    ),
                }
            ),
        )

    async def async_step_manage_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Device management — approve/ignore discovered devices with aliases."""
        from . import _get_hub_data, _republish_all_approved, _remove_device_from_ha
        hub_data = _get_hub_data(self.hass)
        if not hub_data:
            return self.async_abort(reason="integration_not_loaded")

        engine = hub_data["engine"]
        store = hub_data["store"]
        retain = hub_data["retain"]
        all_devices = engine.devices

        if user_input is not None:
            approved_ids = set(user_input.get("approved_devices", []))
            ignored_ids = set(user_input.get("ignored_devices", []))

            # Apply state changes
            for device_id, device in all_devices.items():
                if device_id in approved_ids:
                    alias_key = f"alias_{device_id}"
                    alias = user_input.get(alias_key, device.alias)
                    engine.approve_device(device_id, alias)
                elif device_id in ignored_ids:
                    engine.ignore_device(device_id)
                else:
                    engine.reset_device(device_id)

            # Persist changes
            await store.async_save(engine.save_state())

            _republish_all_approved(self.hass, engine, retain)

            # Remove HA entities for newly ignored devices
            for device_id in ignored_ids:
                _remove_device_from_ha(self.hass, engine, device_id, retain)

            return self.async_create_entry(
                title="", data=self._config_entry.options
            )

        # Build device selection lists
        if not all_devices:
            return self.async_abort(reason="no_devices_discovered")

        device_options = {}
        for device_id, device in all_devices.items():
            fields = [
                k for k in device.fields_seen
                if k in _KNOWN_SENSOR_FIELDS
            ]
            field_str = ", ".join(fields[:5])
            if len(fields) > 5:
                field_str += f" (+{len(fields) - 5} more)"

            stale_marker = " ⏸ STALE" if device.is_stale(engine.stale_timeout) else ""
            alias_str = f" → {device.alias}" if device.alias else ""
            label = (
                f"{device.model} [{device_id}]{alias_str} — "
                f"msgs: {device.message_count}{stale_marker}"
            )
            if field_str:
                label += f" — {field_str}"
            device_options[device_id] = label

        current_approved = [
            did for did, d in all_devices.items()
            if d.state == DEVICE_STATE_APPROVED
        ]
        current_ignored = [
            did for did, d in all_devices.items()
            if d.state == DEVICE_STATE_IGNORED
        ]

        return self.async_show_form(
            step_id="manage_devices",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "approved_devices",
                        default=current_approved,
                    ): vol.All(
                        vol.Coerce(list),
                        [vol.In(device_options)],
                    ),
                    vol.Optional(
                        "ignored_devices",
                        default=current_ignored,
                    ): vol.All(
                        vol.Coerce(list),
                        [vol.In(device_options)],
                    ),
                }
            ),
            description_placeholders={
                "device_count": str(len(all_devices)),
                "approved_count": str(len(current_approved)),
                "ignored_count": str(len(current_ignored)),
            },
        )

    async def async_step_merge_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle merge suggestions for rolling-ID sensors."""
        from . import _get_hub_data, _republish_all_approved, _remove_device_from_ha
        hub_data = _get_hub_data(self.hass)
        if not hub_data:
            return self.async_abort(reason="integration_not_loaded")

        engine = hub_data["engine"]
        store = hub_data["store"]
        retain = hub_data["retain"]
        suggestions = engine.get_merge_summary()

        if not suggestions:
            return self.async_abort(reason="no_merge_suggestions")

        if user_input is not None:
            accepted = set(user_input.get("accept_merges", []))
            for suggestion in suggestions:
                new_id = suggestion["new_device_id"]
                old_id = suggestion["old_device_id"]
                if new_id in accepted:
                    _remove_device_from_ha(self.hass, engine, old_id, retain)
                    engine.merge_device(new_id, old_id)
                else:
                    engine.dismiss_merge(new_id)

            _republish_all_approved(self.hass, engine, retain)
            await store.async_save(engine.save_state())
            return self.async_create_entry(
                title="", data=self._config_entry.options
            )

        # Build merge suggestion options
        merge_options = {}
        for s in suggestions:
            label = (
                f"{s['new_device_id']} → {s['alias']} "
                f"(was {s['old_device_id']}, model={s['model']})"
            )
            merge_options[s["new_device_id"]] = label

        return self.async_show_form(
            step_id="merge_devices",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "accept_merges",
                        default=[],
                    ): vol.All(
                        vol.Coerce(list),
                        [vol.In(merge_options)],
                    ),
                }
            ),
            description_placeholders={
                "merge_count": str(len(suggestions)),
            },
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Discovery tuning parameters."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DISCOVERY_PREFIX,
                        default=current.get(
                            CONF_DISCOVERY_PREFIX, DEFAULT_DISCOVERY_PREFIX
                        ),
                    ): str,
                    vol.Optional(
                        CONF_DEVICE_TOPIC_SUFFIX,
                        default=current.get(
                            CONF_DEVICE_TOPIC_SUFFIX, DEFAULT_DEVICE_TOPIC_SUFFIX
                        ),
                    ): str,
                    vol.Optional(
                        CONF_EXPIRE_AFTER,
                        default=current.get(
                            CONF_EXPIRE_AFTER, DEFAULT_EXPIRE_AFTER
                        ),
                    ): int,
                    vol.Optional(
                        CONF_STALE_TIMEOUT,
                        default=current.get(
                            CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT
                        ),
                    ): int,
                    vol.Optional(
                        CONF_FORCE_UPDATE,
                        default=current.get(
                            CONF_FORCE_UPDATE, DEFAULT_FORCE_UPDATE
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_RETAIN,
                        default=current.get(CONF_RETAIN, DEFAULT_RETAIN),
                    ): bool,
                }
            ),
        )
