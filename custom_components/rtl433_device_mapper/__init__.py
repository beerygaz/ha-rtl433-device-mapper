"""RTL-433 MQTT Discovery Bridge for Home Assistant — V2 Provisioning Only.

This integration subscribes to rtl_433 MQTT events, discovers 433 MHz devices,
and lets you approve which devices get provisioned as HA entities via MQTT
auto-discovery. It is a **provisioning layer only** — it NEVER republishes
telemetry data.

Architecture:
    rtl_433 → MQTT → this integration (subscribes to events) →
    publishes HA discovery configs with state_topic pointing at
    raw rtl_433 device topics → HA entities subscribe directly
    to rtl_433's data topics.

    If this integration goes down, entities keep receiving data.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

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
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .discovery import DiscoveryPayload, RTL433DiscoveryEngine

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RTL-433 Discovery from a config entry.

    Uses HA's native MQTT integration for subscriptions and publishing —
    no separate paho-mqtt connection needed.
    """
    hass.data.setdefault(DOMAIN, {})

    # ── Load persistent state ────────────────────────────────────────────────
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored_data = await store.async_load() or {}

    # ── Create discovery engine ──────────────────────────────────────────────
    engine = RTL433DiscoveryEngine(
        discovery_prefix=entry.options.get(
            CONF_DISCOVERY_PREFIX, DEFAULT_DISCOVERY_PREFIX
        ),
        device_topic_suffix=entry.options.get(
            CONF_DEVICE_TOPIC_SUFFIX, DEFAULT_DEVICE_TOPIC_SUFFIX
        ),
        expire_after=entry.options.get(CONF_EXPIRE_AFTER, DEFAULT_EXPIRE_AFTER),
        force_update=entry.options.get(CONF_FORCE_UPDATE, DEFAULT_FORCE_UPDATE),
        stale_timeout=entry.options.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT),
    )

    # Restore persisted device states
    if stored_data:
        engine.load_state(stored_data)
        _LOGGER.info(
            "Restored %d devices from persistent storage",
            len(engine.devices),
        )

    retain = entry.options.get(CONF_RETAIN, DEFAULT_RETAIN)
    rtl_topic = entry.data.get(CONF_RTL_TOPIC, "rtl_433/+/events")

    # Store references for services, options flow, and MQTT callback
    entry_data = {
        "engine": engine,
        "store": store,
        "entry": entry,
        "retain": retain,
        "unsub_mqtt": None,
    }
    hass.data[DOMAIN][entry.entry_id] = entry_data

    # ── MQTT message handler ─────────────────────────────────────────────────

    @callback
    def _handle_mqtt_message(msg: mqtt.ReceiveMessage) -> None:
        """Handle incoming rtl_433 event messages via HA's MQTT integration."""
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            _LOGGER.error("Failed to decode MQTT message: %s", err)
            return

        # Extract topic prefix (first two segments, e.g. "rtl_433/hostname")
        topic_prefix = "/".join(msg.topic.split("/", 2)[:2])

        # Track new discoveries for event firing
        known_before = set(engine.devices.keys())

        # Process through discovery engine
        payloads = engine.process_event(data, topic_prefix)

        # Fire HA events for new device discoveries
        new_devices = set(engine.devices.keys()) - known_before
        for device_id in new_devices:
            hass.bus.async_fire(
                f"{DOMAIN}_device_discovered", {"device_id": device_id}
            )

        # Fire event for merge suggestions
        for suggestion in engine.get_merge_summary():
            if suggestion["new_device_id"] in new_devices:
                hass.bus.async_fire(
                    f"{DOMAIN}_merge_suggested",
                    {
                        "new_device_id": suggestion["new_device_id"],
                        "old_device_id": suggestion["old_device_id"],
                        "alias": suggestion["alias"],
                        "model": suggestion["model"],
                    },
                )

        # Publish discovery configs for approved devices
        for payload in payloads:
            _publish_discovery(hass, payload, retain)

    # ── Subscribe to rtl_433 events ──────────────────────────────────────────
    entry_data["unsub_mqtt"] = await mqtt.async_subscribe(
        hass, rtl_topic, _handle_mqtt_message, qos=0
    )

    # ── Republish all approved devices on startup ────────────────────────────
    _republish_all_approved(hass, engine, retain)

    # ── Periodic persistence (every 5 minutes) ──────────────────────────────

    async def _periodic_save(_now: Any = None) -> None:
        data = engine.save_state()
        await store.async_save(data)

    entry.async_on_unload(
        async_track_time_interval(hass, _periodic_save, timedelta(seconds=300))
    )

    # ── Register services ────────────────────────────────────────────────────
    _register_services(hass)

    # ── Listen for options updates ───────────────────────────────────────────
    entry.async_on_unload(entry.add_update_listener(async_options_updated))

    _LOGGER.info(
        "RTL-433 Discovery Bridge V2 started (provisioning-only mode, "
        "subscribed to %s)",
        rtl_topic,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data:
        engine: RTL433DiscoveryEngine = data["engine"]
        store: Store = data["store"]

        # Persist state before shutdown
        save_data = engine.save_state()
        await store.async_save(save_data)

        # Unsubscribe from MQTT
        if data.get("unsub_mqtt"):
            data["unsub_mqtt"]()

    _LOGGER.info("RTL-433 Discovery Bridge stopped")
    return True


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — restart the integration with new settings."""
    _LOGGER.info("Options updated, reloading integration")
    await hass.config_entries.async_reload(entry.entry_id)


# ─── MQTT Publishing ─────────────────────────────────────────────────────────


def _publish_discovery(
    hass: HomeAssistant, payload: DiscoveryPayload, retain: bool
) -> None:
    """Publish a single HA discovery config topic via HA's MQTT integration."""
    json_payload = payload.to_json() if payload.payload else ""
    mqtt.async_publish(
        hass,
        payload.config_topic,
        json_payload,
        qos=0,
        retain=retain,
    )
    _LOGGER.debug("Published discovery: %s", payload.config_topic)


def _republish_all_approved(
    hass: HomeAssistant,
    engine: RTL433DiscoveryEngine,
    retain: bool,
) -> int:
    """Republish discovery configs for all approved devices.

    Called on startup and after merges to ensure HA entities are up-to-date.
    """
    count = 0
    for device in engine.approved_devices.values():
        payloads = engine.build_discovery_payloads(device)
        for payload in payloads:
            _publish_discovery(hass, payload, retain)
            count += 1
    if count:
        _LOGGER.info("Republished %d discovery configs for approved devices", count)
    return count


def _remove_device_from_ha(
    hass: HomeAssistant,
    engine: RTL433DiscoveryEngine,
    device_id: str,
    retain: bool,
) -> int:
    """Publish empty configs to remove a device's entities from HA."""
    device = engine.devices.get(device_id)
    if not device:
        return 0
    payloads = engine.build_removal_payloads(device)
    for payload in payloads:
        mqtt.async_publish(hass, payload.config_topic, "", qos=0, retain=retain)
    _LOGGER.info(
        "Published %d removal configs for device %s", len(payloads), device_id
    )
    return len(payloads)


# ─── Service Registration ────────────────────────────────────────────────────


def _register_services(hass: HomeAssistant) -> None:
    """Register integration services for device management."""

    if hass.services.has_service(DOMAIN, "approve_device"):
        return  # Already registered

    async def handle_approve_device(call: ServiceCall) -> None:
        """Approve a device for HA discovery, optionally setting an alias."""
        device_id = call.data["device_id"]
        alias = call.data.get("alias")
        for entry_data in hass.data[DOMAIN].values():
            engine: RTL433DiscoveryEngine = entry_data["engine"]
            retain: bool = entry_data["retain"]
            if engine.approve_device(device_id, alias):
                _republish_all_approved(hass, engine, retain)
                store: Store = entry_data["store"]
                await store.async_save(engine.save_state())
                _LOGGER.info("Service: approved device %s (alias=%s)", device_id, alias)
                return
        _LOGGER.warning("Service: device %s not found", device_id)

    async def handle_ignore_device(call: ServiceCall) -> None:
        """Ignore/blocklist a device."""
        device_id = call.data["device_id"]
        for entry_data in hass.data[DOMAIN].values():
            engine: RTL433DiscoveryEngine = entry_data["engine"]
            retain: bool = entry_data["retain"]
            if engine.ignore_device(device_id):
                _remove_device_from_ha(hass, engine, device_id, retain)
                store: Store = entry_data["store"]
                await store.async_save(engine.save_state())
                _LOGGER.info("Service: ignored device %s", device_id)
                return
        _LOGGER.warning("Service: device %s not found", device_id)

    async def handle_merge_device(call: ServiceCall) -> None:
        """Merge a new device into an existing approved device."""
        new_device_id = call.data["new_device_id"]
        old_device_id = call.data["old_device_id"]
        for entry_data in hass.data[DOMAIN].values():
            engine: RTL433DiscoveryEngine = entry_data["engine"]
            retain: bool = entry_data["retain"]

            # Remove old device's HA entities first
            _remove_device_from_ha(hass, engine, old_device_id, retain)

            if engine.merge_device(new_device_id, old_device_id):
                # Republish with new state_topic pointing at new device
                _republish_all_approved(hass, engine, retain)
                store: Store = entry_data["store"]
                await store.async_save(engine.save_state())
                _LOGGER.info(
                    "Service: merged %s → %s", old_device_id, new_device_id
                )
                return
        _LOGGER.warning(
            "Service: merge failed for %s → %s", old_device_id, new_device_id
        )

    async def handle_reset_device(call: ServiceCall) -> None:
        """Reset a device back to 'discovered' state."""
        device_id = call.data["device_id"]
        for entry_data in hass.data[DOMAIN].values():
            engine: RTL433DiscoveryEngine = entry_data["engine"]
            retain: bool = entry_data["retain"]
            if engine.reset_device(device_id):
                _remove_device_from_ha(hass, engine, device_id, retain)
                store: Store = entry_data["store"]
                await store.async_save(engine.save_state())
                _LOGGER.info("Service: reset device %s", device_id)
                return

    hass.services.async_register(DOMAIN, "approve_device", handle_approve_device)
    hass.services.async_register(DOMAIN, "ignore_device", handle_ignore_device)
    hass.services.async_register(DOMAIN, "merge_device", handle_merge_device)
    hass.services.async_register(DOMAIN, "reset_device", handle_reset_device)
