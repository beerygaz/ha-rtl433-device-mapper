"""RTL-433 MQTT Discovery Bridge for Home Assistant — V2 Provisioning Only.

This integration subscribes to rtl_433 MQTT events, discovers 433 MHz devices,
and lets you approve which devices get provisioned as HA entities.

Architecture:
    rtl_433 → MQTT broker → this integration (subscribes via HA MQTT) →
    user approves device → we publish HA MQTT discovery configs directly
    to the broker (via paho) → HA's built-in MQTT integration creates entities.

Key design decisions:
  - We do NOT create any native HA sensor entities (sensor.py is gone).
  - We do NOT use mqtt.async_publish() — HA ignores its own client's discovery msgs.
  - We DO publish discovery configs via a separate paho-mqtt client with a distinct
    client_id so the broker relays them to HA's MQTT subscriber as external messages.

Config Entry Types:
    ENTRY_TYPE_HUB    — one per MQTT topic; handles MQTT subscription + discovery engine
    ENTRY_TYPE_DEVICE — one per approved device; records the approval (no entities owned)
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

import paho.mqtt.client as paho_mqtt

from homeassistant.components import mqtt
from homeassistant.components.mqtt import DOMAIN as MQTT_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant import config_entries

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_TOPIC_SUFFIX,
    CONF_DISCOVERY_PREFIX,
    CONF_EXPIRE_AFTER,
    CONF_FORCE_UPDATE,
    CONF_MODEL_BLOCKLIST,
    CONF_RETAIN,
    CONF_RTL_TOPIC,
    CONF_STALE_TIMEOUT,
    DEFAULT_DEVICE_TOPIC_SUFFIX,
    DEFAULT_DISCOVERY_PREFIX,
    DEFAULT_EXPIRE_AFTER,
    DEFAULT_FORCE_UPDATE,
    DEFAULT_MODEL_BLOCKLIST,
    DEFAULT_RETAIN,
    DEFAULT_STALE_TIMEOUT,
    DEVICE_STATE_APPROVED,
    DEVICE_STATE_IGNORED,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_HUB,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .discovery import RTL433DiscoveryEngine, DiscoveryPayload

_LOGGER = logging.getLogger(__name__)


# ─── Direct MQTT Publisher ────────────────────────────────────────────────────


def _get_mqtt_broker_config(hass: HomeAssistant) -> dict:
    """Extract broker connection details from HA's MQTT config entry."""
    try:
        mqtt_entries = hass.config_entries.async_entries(MQTT_DOMAIN)
        if not mqtt_entries:
            _LOGGER.warning("No MQTT config entry found — using defaults")
            return {"broker": "localhost", "port": 1883}

        entry = mqtt_entries[0]
        return {
            "broker": entry.data.get("broker", "localhost"),
            "port": int(entry.data.get("port", 1883)),
            "username": entry.data.get("username"),
            "password": entry.data.get("password"),
        }
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Failed to read MQTT broker config: %s — using defaults", err)
        return {"broker": "localhost", "port": 1883}


def _publish_payloads_direct(
    broker_config: dict,
    payloads: list[DiscoveryPayload],
    retain: bool = True,
) -> int:
    """Publish a list of DiscoveryPayload objects directly to the MQTT broker.

    Uses a fresh paho client per batch (connect → wait for CONNACK →
    publish all → disconnect). Always call via async_add_executor_job
    since this is blocking I/O.

    Returns:
        Number of messages successfully published.
    """
    if not payloads:
        return 0

    import threading
    import time
    import uuid

    broker = broker_config.get("broker", "localhost")
    port = int(broker_config.get("port", 1883))
    username = broker_config.get("username")
    password = broker_config.get("password")

    client_id = f"rtl433_mapper_pub_{uuid.uuid4().hex[:8]}"

    published_count = 0
    connected_event = threading.Event()
    connect_result: dict[str, Any] = {"reason_code": None}

    def _on_connect(client, userdata, flags, reason_code, properties):
        connect_result["reason_code"] = int(reason_code)
        connected_event.set()

    client: paho_mqtt.Client | None = None

    try:
        client = paho_mqtt.Client(
            paho_mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        client.on_connect = _on_connect

        if username:
            client.username_pw_set(username, password)

        client.connect(broker, port, keepalive=30)
        client.loop_start()

        # Wait up to 5 seconds for the connection to be established
        if not connected_event.wait(timeout=5.0):
            _LOGGER.error(
                "Could not connect to MQTT broker at %s:%d — "
                "is the broker running?",
                broker,
                port,
            )
            return 0

        reason_code = connect_result["reason_code"]
        if reason_code != 0:
            if reason_code in (4, 5):
                _LOGGER.error(
                    "MQTT authentication failed — check broker credentials "
                    "(%s:%d, CONNACK rc=%s)",
                    broker,
                    port,
                    reason_code,
                )
            else:
                _LOGGER.error(
                    "Could not connect to MQTT broker at %s:%d — "
                    "CONNACK rc=%s",
                    broker,
                    port,
                    reason_code,
                )
            return 0

        for dp in payloads:
            payload_str = dp.to_json() if dp.payload else ""
            result = client.publish(
                dp.config_topic,
                payload=payload_str,
                qos=1,
                retain=retain,
            )
            if result.rc == paho_mqtt.MQTT_ERR_SUCCESS:
                published_count += 1
                _LOGGER.debug(
                    "Published discovery config to %s (retain=%s)",
                    dp.config_topic,
                    retain,
                )
            else:
                _LOGGER.warning(
                    "Failed to publish to %s via MQTT broker %s:%d — rc=%d",
                    dp.config_topic,
                    broker,
                    port,
                    result.rc,
                )

        # Brief wait to let the outbound queue flush before disconnecting
        time.sleep(0.3)

    except ConnectionRefusedError:
        _LOGGER.error(
            "MQTT broker at %s:%d refused connection — "
            "is the broker running and accepting connections?",
            broker,
            port,
        )
    except TimeoutError:
        _LOGGER.error(
            "Connection to MQTT broker at %s:%d timed out — "
            "check network connectivity and broker status",
            broker,
            port,
        )
    except OSError as err:
        _LOGGER.error(
            "MQTT network error while publishing via %s:%d: %s",
            broker,
            port,
            err,
        )
    except paho_mqtt.MQTTException as err:
        _LOGGER.error(
            "MQTT client error while publishing via %s:%d: %s",
            broker,
            port,
            err,
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception(
            "Unexpected error publishing discovery payloads via broker %s:%d",
            broker,
            port,
        )
    finally:
        if client is not None:
            try:
                client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    return published_count


# ─── Setup ────────────────────────────────────────────────────────────────────


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Route setup based on entry type: hub or device."""
    entry_type = entry.data.get("entry_type", ENTRY_TYPE_HUB)

    if entry_type == ENTRY_TYPE_DEVICE:
        return await _async_setup_device_entry(hass, entry)
    else:
        return await _async_setup_hub_entry(hass, entry)


async def _async_setup_hub_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the hub config entry — MQTT subscription + discovery engine."""
    hass.data.setdefault(DOMAIN, {})

    # ── Verify MQTT integration is available ─────────────────────────────────
    mqtt_entries = hass.config_entries.async_entries(MQTT_DOMAIN)
    if not mqtt_entries:
        _LOGGER.error(
            "MQTT integration not configured — "
            "RTL-433 Device Mapper requires MQTT. "
            "Please set up the MQTT integration first."
        )
        return False

    # ── Load persistent state ────────────────────────────────────────────────
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    try:
        stored_data = await store.async_load() or {}
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Failed to load persistent RTL-433 state, "
            "continuing with empty state: %s",
            err,
        )
        stored_data = {}

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
        model_blocklist=entry.options.get(
            CONF_MODEL_BLOCKLIST, DEFAULT_MODEL_BLOCKLIST
        ),
    )

    # Restore persisted device states
    if stored_data:
        try:
            engine.load_state(stored_data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Failed to restore persistent RTL-433 state, "
                "continuing with empty state: %s",
                err,
            )

    retain = entry.options.get(CONF_RETAIN, DEFAULT_RETAIN)
    rtl_topic = entry.data.get(CONF_RTL_TOPIC, "rtl_433/+/events")

    # Read broker config once at startup
    broker_config = _get_mqtt_broker_config(hass)
    _LOGGER.info(
        "MQTT broker for direct publish: %s:%d",
        broker_config["broker"],
        broker_config["port"],
    )

    # Track device IDs already reported to config flow (to avoid re-triggering)
    already_reported: set[str] = set()
    # Pre-populate only approved/ignored devices — "discovered" ones still need UI cards
    for device_id, device in engine.devices.items():
        if device.state in (DEVICE_STATE_APPROVED, DEVICE_STATE_IGNORED):
            already_reported.add(device_id)

    # Store references for services, options flow, and MQTT callback
    entry_data = {
        "engine": engine,
        "store": store,
        "entry": entry,
        "retain": retain,
        "broker_config": broker_config,
        "unsub_mqtt": None,
        "already_reported": already_reported,
        "entry_type": ENTRY_TYPE_HUB,
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
        # Returns discovery payloads for approved devices (empty for new/ignored)
        payloads = engine.process_event(data, topic_prefix)

        # Publish discovery configs for approved devices
        # This runs on every event so field additions are picked up automatically
        if payloads:
            hass.async_add_executor_job(
                _publish_payloads_direct, broker_config, payloads, retain
            )

        # Fire HA events for new device discoveries
        new_devices = set(engine.devices.keys()) - known_before
        for device_id in new_devices:
            hass.bus.async_fire(
                f"{DOMAIN}_device_discovered", {"device_id": device_id}
            )

            # Trigger a discovery config flow for the new device
            if device_id not in already_reported:
                already_reported.add(device_id)
                device = engine.devices[device_id]
                _LOGGER.info(
                    "Triggering discovery flow for new device: %s (model=%s)",
                    device_id,
                    device.model,
                )
                discovery_flow.async_create_flow(
                    hass,
                    DOMAIN,
                    context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                    data={
                        "device_id": device_id,
                        "model": device.model,
                        "channel": device.channel,
                        "raw_id": device.raw_id,
                        "fields_seen": list(device.fields_seen.keys()),
                        "hub_entry_id": entry.entry_id,
                    },
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

    # ── Trigger discovery flows for existing unhandled devices ─────────────
    # Devices in "discovered" state that survived across restarts need discovery cards
    async def _trigger_startup_discoveries() -> None:
        for device_id, device in engine.discovered_devices.items():
            if device_id not in already_reported:
                already_reported.add(device_id)
                _LOGGER.info(
                    "Triggering startup discovery flow for: %s (model=%s)",
                    device_id,
                    device.model,
                )
                discovery_flow.async_create_flow(
                    hass,
                    DOMAIN,
                    context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                    data={
                        "device_id": device_id,
                        "model": device.model,
                        "channel": device.channel,
                        "raw_id": device.raw_id,
                        "fields_seen": list(device.fields_seen.keys()),
                        "hub_entry_id": entry.entry_id,
                    },
                )

    hass.async_create_task(_trigger_startup_discoveries())

    # ── Republish all approved devices on startup ────────────────────────────
    # Restores retained discovery configs in case the broker was wiped
    async def _startup_republish() -> None:
        count = await hass.async_add_executor_job(
            _republish_all_approved_blocking, hass, engine, broker_config, retain
        )
        if count:
            _LOGGER.info(
                "Startup: republished %d discovery configs for approved devices",
                count,
            )

    hass.async_create_task(_startup_republish())

    # ── Subscribe to rtl_433 events ──────────────────────────────────────────
    try:
        entry_data["unsub_mqtt"] = await mqtt.async_subscribe(
            hass, rtl_topic, _handle_mqtt_message, qos=0
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.error(
            "Failed to subscribe to %s — check MQTT connection: %s",
            rtl_topic,
            err,
        )
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return False

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
        "RTL-433 Discovery Bridge started (direct MQTT publish mode, "
        "subscribed to %s)",
        rtl_topic,
    )
    return True


async def _async_setup_device_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a device config entry.

    Device entries record that the user has approved a device. We publish
    MQTT discovery configs directly to the broker — HA's built-in MQTT
    integration creates the entities. We do NOT forward to any sensor platform.
    """
    hass.data.setdefault(DOMAIN, {})

    device_id = entry.data.get(CONF_DEVICE_ID)
    if not device_id:
        _LOGGER.error("Device entry %s has no device_id", entry.entry_id)
        return False

    _LOGGER.info(
        "Setting up device entry for %s (alias=%s)",
        device_id,
        entry.title,
    )

    # Find the hub entry and engine to update alias if needed
    hub_data = _get_hub_data(hass)
    if hub_data:
        engine = hub_data["engine"]
        store = hub_data["store"]
        broker_config = hub_data["broker_config"]
        retain = hub_data["retain"]

        # Update alias in engine if it changed in config entry
        alias = entry.title
        if device_id in engine.devices:
            device = engine.devices[device_id]
            if device.alias != alias:
                engine.set_alias(device_id, alias)
                engine.approve_device(device_id, alias)

        # Persist the updated engine state
        await store.async_save(engine.save_state())

        # Publish discovery configs for this device
        if device_id in engine.devices:
            device = engine.devices[device_id]
            payloads = engine.build_discovery_payloads(device)
            if payloads:
                count = await hass.async_add_executor_job(
                    _publish_payloads_direct, broker_config, payloads, retain
                )
                _LOGGER.info(
                    "Published %d discovery configs for device %s (%s)",
                    count,
                    device_id,
                    alias,
                )
            else:
                _LOGGER.warning(
                    "No discovery payloads for device %s — "
                    "device may not have received any MQTT data yet",
                    device_id,
                )
    else:
        _LOGGER.warning(
            "No hub entry found when setting up device %s — "
            "discovery configs will be published when hub is available",
            device_id,
        )

    hass.data[DOMAIN][entry.entry_id] = {
        "entry_type": ENTRY_TYPE_DEVICE,
        "device_id": device_id,
    }

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (hub or device)."""
    entry_type = entry.data.get("entry_type", ENTRY_TYPE_HUB)

    if entry_type == ENTRY_TYPE_DEVICE:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.info("Unloaded device entry: %s", entry.title)
        return True

    # Hub unload
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


# ─── Hub Data Helper ──────────────────────────────────────────────────────────


def _get_hub_data(hass: HomeAssistant) -> dict | None:
    """Find and return the hub entry's runtime data dict, or None."""
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if entry_data.get("entry_type", ENTRY_TYPE_HUB) == ENTRY_TYPE_HUB:
            return entry_data
    return None


# ─── Discovery Publish Helpers ────────────────────────────────────────────────


def _republish_all_approved_blocking(
    hass: HomeAssistant,
    engine: RTL433DiscoveryEngine,
    broker_config: dict,
    retain: bool,
) -> int:
    """Synchronously publish discovery configs for all approved devices.

    Intended to run in a thread pool (via async_add_executor_job).
    Returns total number of messages published.
    """
    all_payloads: list[DiscoveryPayload] = []
    for device in engine.approved_devices.values():
        all_payloads.extend(engine.build_discovery_payloads(device))

    if not all_payloads:
        return 0

    return _publish_payloads_direct(broker_config, all_payloads, retain)


def _publish_discovery(hass: HomeAssistant, payload: Any, retain: bool) -> None:
    """No-op compatibility stub — use _publish_payloads_direct instead."""
    pass


def _republish_all_approved(
    hass: HomeAssistant,
    engine: RTL433DiscoveryEngine,
    retain: bool,
) -> int:
    """Schedule republish of all approved devices (non-blocking).

    config_flow.py calls this after state changes. We fire-and-forget via
    the executor since paho publish is blocking I/O.
    """
    hub_data = _get_hub_data(hass)
    if not hub_data:
        return 0
    broker_config = hub_data.get("broker_config", {})

    hass.async_add_executor_job(
        _republish_all_approved_blocking, hass, engine, broker_config, retain
    )
    return len(engine.approved_devices)


def _remove_device_from_ha(
    hass: HomeAssistant,
    engine: RTL433DiscoveryEngine,
    device_id: str,
    retain: bool,
) -> int:
    """Publish empty payloads to remove a device's entities from HA.

    Publishing an empty string to a retained discovery topic removes the entity.
    """
    hub_data = _get_hub_data(hass)
    if not hub_data:
        return 0
    broker_config = hub_data.get("broker_config", {})

    device = engine.devices.get(device_id)
    if not device:
        return 0

    removal_payloads = engine.build_removal_payloads(device)
    if not removal_payloads:
        return 0

    hass.async_add_executor_job(
        _publish_payloads_direct, broker_config, removal_payloads, retain
    )
    return len(removal_payloads)


# ─── Service Registration ────────────────────────────────────────────────────


def _register_services(hass: HomeAssistant) -> None:
    """Register integration services for device management."""

    if hass.services.has_service(DOMAIN, "approve_device"):
        return  # Already registered

    async def handle_approve_device(call: ServiceCall) -> None:
        """Approve a device for HA entity creation, optionally setting an alias."""
        device_id = call.data["device_id"]
        alias = call.data.get("alias")
        hub_data = _get_hub_data(hass)
        if hub_data:
            engine: RTL433DiscoveryEngine = hub_data["engine"]
            store: Store = hub_data["store"]
            retain = hub_data["retain"]
            if engine.approve_device(device_id, alias):
                await store.async_save(engine.save_state())
                _republish_all_approved(hass, engine, retain)
                _LOGGER.info("Service: approved device %s (alias=%s)", device_id, alias)
                return
        _LOGGER.warning("Service: device %s not found", device_id)

    async def handle_ignore_device(call: ServiceCall) -> None:
        """Ignore/blocklist a device."""
        device_id = call.data["device_id"]
        hub_data = _get_hub_data(hass)
        if hub_data:
            engine: RTL433DiscoveryEngine = hub_data["engine"]
            store: Store = hub_data["store"]
            retain = hub_data["retain"]
            if engine.ignore_device(device_id):
                await store.async_save(engine.save_state())
                _remove_device_from_ha(hass, engine, device_id, retain)
                _LOGGER.info("Service: ignored device %s", device_id)
                return
        _LOGGER.warning("Service: device %s not found", device_id)

    async def handle_merge_device(call: ServiceCall) -> None:
        """Merge a new device into an existing approved device."""
        new_device_id = call.data["new_device_id"]
        old_device_id = call.data["old_device_id"]
        hub_data = _get_hub_data(hass)
        if hub_data:
            engine: RTL433DiscoveryEngine = hub_data["engine"]
            store: Store = hub_data["store"]
            retain = hub_data["retain"]

            # Remove old device entities before merge
            _remove_device_from_ha(hass, engine, old_device_id, retain)

            if engine.merge_device(new_device_id, old_device_id):
                await store.async_save(engine.save_state())
                _republish_all_approved(hass, engine, retain)
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
        hub_data = _get_hub_data(hass)
        if hub_data:
            engine: RTL433DiscoveryEngine = hub_data["engine"]
            store: Store = hub_data["store"]
            retain = hub_data["retain"]
            _remove_device_from_ha(hass, engine, device_id, retain)
            if engine.reset_device(device_id):
                await store.async_save(engine.save_state())
                _LOGGER.info("Service: reset device %s", device_id)
                return

    hass.services.async_register(DOMAIN, "approve_device", handle_approve_device)
    hass.services.async_register(DOMAIN, "ignore_device", handle_ignore_device)
    hass.services.async_register(DOMAIN, "merge_device", handle_merge_device)
    hass.services.async_register(DOMAIN, "reset_device", handle_reset_device)
