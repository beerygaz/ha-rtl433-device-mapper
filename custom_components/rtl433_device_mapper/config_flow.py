"""Config flow for RTL-433 MQTT Discovery Bridge V2.

Provides:
  1. Initial setup flow — rtl_433 event topic configuration
  2. Options flow — device management (approve/ignore/alias/merge) + settings
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
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
)

_LOGGER = logging.getLogger(__name__)


class RTL433DiscoveryConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle initial configuration of the RTL-433 Discovery Bridge.

    V2 uses HA's built-in MQTT integration (no separate MQTT creds needed).
    We only need the rtl_433 event topic pattern.
    """

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step — rtl_433 topic configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            rtl_topic = user_input.get(CONF_RTL_TOPIC, DEFAULT_RTL_TOPIC)

            # Prevent duplicate entries
            await self.async_set_unique_id(f"rtl433_{rtl_topic}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"RTL-433 Discovery ({rtl_topic})",
                data=user_input,
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> RTL433DiscoveryOptionsFlow:
        """Return the options flow handler."""
        return RTL433DiscoveryOptionsFlow(config_entry)


class RTL433DiscoveryOptionsFlow(config_entries.OptionsFlow):
    """Options flow for managing devices, merges, and discovery settings.

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
        entry_data = self.hass.data.get(DOMAIN, {}).get(
            self._config_entry.entry_id
        )
        merge_count = 0
        if entry_data:
            engine = entry_data["engine"]
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
        entry_data = self.hass.data.get(DOMAIN, {}).get(
            self._config_entry.entry_id
        )
        if not entry_data:
            return self.async_abort(reason="integration_not_loaded")

        engine = entry_data["engine"]
        store = entry_data["store"]
        retain = entry_data["retain"]
        all_devices = engine.devices

        if user_input is not None:
            approved_ids = set(user_input.get("approved_devices", []))
            ignored_ids = set(user_input.get("ignored_devices", []))

            # Apply state changes
            for device_id, device in all_devices.items():
                if device_id in approved_ids:
                    # Check if user provided an alias
                    alias_key = f"alias_{device_id}"
                    alias = user_input.get(alias_key, device.alias)
                    engine.approve_device(device_id, alias)
                elif device_id in ignored_ids:
                    engine.ignore_device(device_id)
                else:
                    engine.reset_device(device_id)

            # Persist changes
            await store.async_save(engine.save_state())

            # Republish discovery for approved devices
            from . import _republish_all_approved, _remove_device_from_ha

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
        entry_data = self.hass.data.get(DOMAIN, {}).get(
            self._config_entry.entry_id
        )
        if not entry_data:
            return self.async_abort(reason="integration_not_loaded")

        engine = entry_data["engine"]
        store = entry_data["store"]
        retain = entry_data["retain"]
        suggestions = engine.get_merge_summary()

        if not suggestions:
            return self.async_abort(reason="no_merge_suggestions")

        if user_input is not None:
            from . import _republish_all_approved, _remove_device_from_ha

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


# Known sensor field names for display in device lists
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
