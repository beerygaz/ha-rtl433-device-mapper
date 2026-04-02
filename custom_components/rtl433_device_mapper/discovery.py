"""Core discovery engine — V2 provisioning-only architecture.

This module is the heart of the integration. It:
  1. Parses incoming rtl_433 JSON events
  2. Extracts device identity (model + id + channel)
  3. Manages a device registry with approve/ignore/merge lifecycle
  4. Builds HA MQTT auto-discovery config payloads pointing at RAW rtl_433 topics
  5. Detects stale devices and suggests merges for rolling-ID sensors
  6. NEVER republishes telemetry — discovery configs only

**No Home Assistant imports** — this module must be loadable standalone
by the CLI test harness (test_discover.py).

Ported from rtl_433_mqtt_hass.py with the following V2 improvements:
  - Device aliasing (friendly names for approved devices)
  - Merge detection for rolling-ID sensors (Acurite battery changes, etc.)
  - Staleness tracking with configurable timeout
  - Discovery configs point at raw rtl_433 topics (zero republishing)
  - If integration goes down, entities keep receiving data
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .const import (
    DEFAULT_STALE_TIMEOUT,
    DEFAULT_UNIT_SYSTEM,
    DEVICE_STATE_APPROVED,
    DEVICE_STATE_DISCOVERED,
    DEVICE_STATE_IGNORED,
    FIELD_MAPPINGS,
    MANUFACTURER,
    MERGE_STATE_DISMISSED,
    MERGE_STATE_PENDING,
    SECRET_KNOCK_MAPPINGS,
    SKIP_KEYS,
    SYNTHETIC_MAPPINGS,
    UNIT_AWARE_FIELDS,
)

_LOGGER = logging.getLogger(__name__)

# Regex for parsing the rtl_433 device topic suffix template
# e.g. "devices[/type][/model][/subtype][/channel][/id]"
TOPIC_PARSE_RE = re.compile(
    r"\[(?P<slash>/?)(?P<token>[^\]:]+):?(?P<default>[^\]:]*)\]"
)


def sanitize(text: str) -> str:
    """Sanitize a name for MQTT topic / HA entity use."""
    return (
        text.replace(" ", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace("&", "")
    )


def slugify(text: str) -> str:
    """Convert text to a slug suitable for unique_id and topic segments."""
    return (
        text.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace("&", "")
        .replace("'", "")
        .replace('"', "")
        .replace("(", "")
        .replace(")", "")
    )


def normalize_blocklist_pattern(pattern: str) -> str:
    """Normalize a user-provided model blocklist pattern for matching.

    Matching is case-insensitive. Bare prefixes get an implicit trailing `*`
    so `Digitech` matches `Digitech-XC0346`.
    """
    normalized = pattern.strip().lower()
    if not normalized:
        return ""
    if not any(char in normalized for char in "*?[]"):
        normalized = f"{normalized}*"
    return normalized


def match_blocked_model(model: str, patterns: list[str]) -> str | None:
    """Return the matching pattern if the model is blocked, else None."""
    normalized_model = model.strip().lower()
    for pattern in patterns:
        normalized_pattern = normalize_blocklist_pattern(pattern)
        if normalized_pattern and fnmatch.fnmatch(normalized_model, normalized_pattern):
            return pattern
    return None


# ─── Data Classes ────────────────────────────────────────────────────────────


@dataclass
class DiscoveredDevice:
    """Represents a single rtl_433 device seen on the air.

    Attributes:
        model: Sanitized model name from rtl_433 (e.g. "Acurite-5n1").
        device_id: Composite ID built from topic template (e.g. "Acurite-5n1-C-448").
        channel: Channel identifier (may be None for single-channel devices).
        raw_id: The 'id' field from the JSON payload (int or string).
        alias: User-assigned friendly name (e.g. "Weather Station"). None if unset.
        first_seen: Unix timestamp of first event.
        last_seen: Unix timestamp of most recent event.
        message_count: Total events received.
        state: One of DEVICE_STATE_DISCOVERED / APPROVED / IGNORED.
        fields_seen: Maps field_name → last sample value.
        base_topic: The raw rtl_433 device topic prefix for this device.
    """

    model: str
    device_id: str
    channel: str | None = None
    raw_id: str | int | None = None
    alias: str | None = None
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    message_count: int = 0
    state: str = DEVICE_STATE_DISCOVERED
    fields_seen: dict[str, Any] = field(default_factory=dict)
    base_topic: str = ""

    @property
    def display_name(self) -> str:
        """Human-readable name — alias if set, otherwise device_id."""
        return self.alias or self.device_id

    @property
    def unique_key(self) -> str:
        """Stable key for storing/looking up this device."""
        return self.device_id

    @property
    def identity_key(self) -> str:
        """Key for merge detection: model + channel (without the rolling ID)."""
        parts = [self.model]
        if self.channel:
            parts.append(str(self.channel))
        return "-".join(parts)

    def is_stale(self, timeout: float = DEFAULT_STALE_TIMEOUT) -> bool:
        """Return True if device hasn't been seen within the timeout window."""
        return (time.time() - self.last_seen) > timeout

    def to_dict(self) -> dict:
        """Serialize for JSON storage."""
        return {
            "model": self.model,
            "device_id": self.device_id,
            "channel": self.channel,
            "raw_id": self.raw_id,
            "alias": self.alias,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "message_count": self.message_count,
            "state": self.state,
            "fields_seen": self.fields_seen,
            "base_topic": self.base_topic,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DiscoveredDevice:
        """Deserialize from JSON storage."""
        return cls(
            model=data["model"],
            device_id=data["device_id"],
            channel=data.get("channel"),
            raw_id=data.get("raw_id"),
            alias=data.get("alias"),
            first_seen=data.get("first_seen", 0),
            last_seen=data.get("last_seen", 0),
            message_count=data.get("message_count", 0),
            state=data.get("state", DEVICE_STATE_DISCOVERED),
            fields_seen=data.get("fields_seen", {}),
            base_topic=data.get("base_topic", ""),
        )


@dataclass
class MergeSuggestion:
    """A pending merge suggestion — new device that looks like a stale approved one.

    When a 433 MHz sensor changes its rolling ID (battery swap, etc.), a new
    device_id appears with the same model and channel. This dataclass tracks
    the suggestion so the UI can prompt the user.
    """

    new_device_id: str
    old_device_id: str
    alias: str  # The alias of the old (approved) device
    model: str
    channel: str | None
    state: str = MERGE_STATE_PENDING  # pending / accepted / dismissed
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "new_device_id": self.new_device_id,
            "old_device_id": self.old_device_id,
            "alias": self.alias,
            "model": self.model,
            "channel": self.channel,
            "state": self.state,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MergeSuggestion:
        return cls(
            new_device_id=data["new_device_id"],
            old_device_id=data["old_device_id"],
            alias=data.get("alias", ""),
            model=data.get("model", ""),
            channel=data.get("channel"),
            state=data.get("state", MERGE_STATE_PENDING),
            created_at=data.get("created_at", 0),
        )


@dataclass
class DiscoveryPayload:
    """A single HA MQTT auto-discovery config message ready to publish.

    The state_topic in the payload points at a RAW rtl_433 device topic —
    the integration never republishes telemetry data.
    """

    config_topic: str  # e.g. "homeassistant/sensor/weather_station/weather_station-T/config"
    payload: dict[str, Any]
    retain: bool = True

    def to_json(self) -> str:
        """Serialize payload to compact JSON string."""
        return json.dumps(self.payload, separators=(",", ":"))


# ─── Discovery Engine ────────────────────────────────────────────────────────


class RTL433DiscoveryEngine:
    """Core engine that processes rtl_433 events and manages device discovery.

    This is the V2 provisioning-only engine. It:
    - Tracks all devices seen on the air
    - Manages approve/ignore/merge lifecycle
    - Builds HA MQTT auto-discovery config payloads
    - Discovery configs point state_topic at RAW rtl_433 device topics
    - NEVER republishes telemetry data
    - Detects potential merge candidates when rolling IDs change
    """

    def __init__(
        self,
        discovery_prefix: str = "homeassistant",
        device_topic_suffix: str = "devices[/type][/model][/subtype][/channel][/id]",
        expire_after: int = 0,
        force_update: bool = False,
        stale_timeout: int = DEFAULT_STALE_TIMEOUT,
        unit_system: str = DEFAULT_UNIT_SYSTEM,
        model_blocklist: list[str] | None = None,
    ) -> None:
        self.discovery_prefix = discovery_prefix
        self.device_topic_suffix = device_topic_suffix
        self.expire_after = expire_after
        self.force_update = force_update
        self.stale_timeout = stale_timeout
        self.unit_system = unit_system
        self.model_blocklist = [
            pattern.strip() for pattern in (model_blocklist or []) if pattern.strip()
        ]

        # Device registry: device_id → DiscoveredDevice
        self._devices: dict[str, DiscoveredDevice] = {}

        # Merge suggestions: keyed by new_device_id
        self._merge_suggestions: dict[str, MergeSuggestion] = {}

    # ── Device Registry ──────────────────────────────────────────────────────

    @property
    def devices(self) -> dict[str, DiscoveredDevice]:
        """Return the full device registry (copy)."""
        return dict(self._devices)

    @property
    def discovered_devices(self) -> dict[str, DiscoveredDevice]:
        """Devices in 'discovered' state (pending user decision)."""
        return {
            k: v for k, v in self._devices.items()
            if v.state == DEVICE_STATE_DISCOVERED
        }

    @property
    def approved_devices(self) -> dict[str, DiscoveredDevice]:
        """Devices the user has approved for HA entity creation."""
        return {
            k: v for k, v in self._devices.items()
            if v.state == DEVICE_STATE_APPROVED
        }

    @property
    def ignored_devices(self) -> dict[str, DiscoveredDevice]:
        """Devices the user has chosen to ignore (blocklist)."""
        return {
            k: v for k, v in self._devices.items()
            if v.state == DEVICE_STATE_IGNORED
        }

    @property
    def stale_devices(self) -> dict[str, DiscoveredDevice]:
        """Devices that haven't been seen within the stale timeout."""
        return {
            k: v for k, v in self._devices.items()
            if v.is_stale(self.stale_timeout)
        }

    @property
    def merge_suggestions(self) -> dict[str, MergeSuggestion]:
        """All pending merge suggestions."""
        return {
            k: v for k, v in self._merge_suggestions.items()
            if v.state == MERGE_STATE_PENDING
        }

    def approve_device(self, device_id: str, alias: str | None = None) -> bool:
        """Mark a device as approved for HA discovery.

        Args:
            device_id: The device identifier.
            alias: Optional friendly name. If not provided and device already
                   has an alias, the existing alias is preserved.

        Returns:
            True if device was found and approved.
        """
        if device_id not in self._devices:
            return False
        device = self._devices[device_id]
        device.state = DEVICE_STATE_APPROVED
        if alias is not None:
            device.alias = alias
        elif device.alias is None:
            # Default alias to device_id if none set
            device.alias = device.device_id
        _LOGGER.info("Approved device: %s (alias=%s)", device_id, device.alias)
        return True

    def ignore_device(self, device_id: str) -> bool:
        """Mark a device as ignored (blocklisted)."""
        if device_id not in self._devices:
            return False
        self._devices[device_id].state = DEVICE_STATE_IGNORED
        _LOGGER.info("Ignored device: %s", device_id)
        return True

    def reset_device(self, device_id: str) -> bool:
        """Reset a device back to 'discovered' state."""
        if device_id not in self._devices:
            return False
        self._devices[device_id].state = DEVICE_STATE_DISCOVERED
        _LOGGER.info("Reset device to discovered: %s", device_id)
        return True

    def remove_device(self, device_id: str) -> bool:
        """Completely remove a device from the registry."""
        if device_id not in self._devices:
            return False
        del self._devices[device_id]
        _LOGGER.info("Removed device: %s", device_id)
        return True

    def set_alias(self, device_id: str, alias: str) -> bool:
        """Set or update the friendly alias for a device."""
        if device_id not in self._devices:
            return False
        self._devices[device_id].alias = alias
        _LOGGER.info("Set alias for %s: %s", device_id, alias)
        return True

    def merge_device(self, new_device_id: str, old_device_id: str) -> bool:
        """Merge a new device into an existing approved device.

        The old device's alias and unique_id are preserved. The new device's
        raw topic becomes the state_topic source. The old device entry is
        removed from the registry, and the new device takes over.

        Args:
            new_device_id: The newly-appeared device (with new rolling ID).
            old_device_id: The existing approved device to merge into.

        Returns:
            True if the merge was successful.
        """
        if new_device_id not in self._devices:
            _LOGGER.warning("Merge failed: new device %s not found", new_device_id)
            return False
        if old_device_id not in self._devices:
            _LOGGER.warning("Merge failed: old device %s not found", old_device_id)
            return False

        old_device = self._devices[old_device_id]
        new_device = self._devices[new_device_id]

        if old_device.state != DEVICE_STATE_APPROVED:
            _LOGGER.warning(
                "Merge failed: old device %s is not approved (state=%s)",
                old_device_id,
                old_device.state,
            )
            return False

        # Transfer the alias and approved state to the new device
        new_device.alias = old_device.alias
        new_device.state = DEVICE_STATE_APPROVED
        # Preserve first_seen from the original device for history continuity
        new_device.first_seen = old_device.first_seen

        # Remove the old device
        del self._devices[old_device_id]

        # Clear merge suggestion if one exists
        if new_device_id in self._merge_suggestions:
            self._merge_suggestions[new_device_id].state = "accepted"

        _LOGGER.info(
            "Merged device: %s → %s (alias=%s)",
            old_device_id,
            new_device_id,
            new_device.alias,
        )
        return True

    def dismiss_merge(self, new_device_id: str) -> bool:
        """Dismiss a merge suggestion."""
        if new_device_id in self._merge_suggestions:
            self._merge_suggestions[new_device_id].state = MERGE_STATE_DISMISSED
            return True
        return False

    # ── Persistence ──────────────────────────────────────────────────────────

    def load_state(self, data: dict) -> None:
        """Load full engine state from persisted dict.

        Expected format:
            {
                "devices": { device_id: {...}, ... },
                "merge_suggestions": { new_device_id: {...}, ... }
            }
        """
        self._devices = {}
        self._merge_suggestions = {}

        devices_data = data.get("devices", data)  # Backwards compat with v1
        loaded_devices = 0
        device_errors = 0

        for key, device_data in devices_data.items():
            try:
                self._devices[key] = DiscoveredDevice.from_dict(device_data)
                loaded_devices += 1
            except (KeyError, TypeError) as err:
                device_errors += 1
                _LOGGER.warning("Failed to load device %s: %s", key, err)

        for key, merge_data in data.get("merge_suggestions", {}).items():
            try:
                self._merge_suggestions[key] = MergeSuggestion.from_dict(merge_data)
            except (KeyError, TypeError) as err:
                _LOGGER.warning("Failed to load merge suggestion %s: %s", key, err)

        _LOGGER.info(
            "Loaded %d/%d devices (%d errors)",
            loaded_devices,
            len(devices_data),
            device_errors,
        )

    def save_state(self) -> dict:
        """Export full engine state as a dict suitable for JSON serialization."""
        return {
            "devices": {k: v.to_dict() for k, v in self._devices.items()},
            "merge_suggestions": {
                k: v.to_dict() for k, v in self._merge_suggestions.items()
            },
        }

    # Backwards compat aliases
    def load_devices(self, data: dict) -> None:
        """Load device registry from persisted dict (V1 compat)."""
        self.load_state(data)

    def save_devices(self) -> dict:
        """Export device registry (V1 compat — returns full state)."""
        return self.save_state()

    # ── Topic Parsing ────────────────────────────────────────────────────────

    def _resolve_device_topic(
        self, data: dict[str, Any], topic_prefix: str
    ) -> tuple[str, str]:
        """Build the rtl_433 device topic and composite device_id from event data.

        Returns:
            (base_topic, device_id) — e.g.
            ("rtl_433/abc123/devices/Acurite-5n1/C/448", "Acurite-5n1-C-448")
        """
        path_elements: list[str] = []
        id_elements: list[str] = []
        last_end = 0

        for match in re.finditer(TOPIC_PARSE_RE, self.device_topic_suffix):
            path_elements.append(self.device_topic_suffix[last_end : match.start()])
            key = match.group("token")
            if key in data:
                if match.group("slash"):
                    path_elements.append("/")
                element = sanitize(str(data[key]))
                path_elements.append(element)
                id_elements.append(element)
            elif match.group("default"):
                path_elements.append(match.group("default"))
            last_end = match.end()

        path = "".join(filter(None, path_elements))
        device_id = "-".join(id_elements)
        return f"{topic_prefix}/{path}", device_id

    # ── Merge Detection ──────────────────────────────────────────────────────

    def _check_merge_candidates(self, new_device: DiscoveredDevice) -> None:
        """Check if a newly discovered device might be a rolling-ID replacement.

        Looks for approved devices with the same model+channel that have gone
        stale. If found, creates a MergeSuggestion.
        """
        if new_device.state != DEVICE_STATE_DISCOVERED:
            return

        # Already has a merge suggestion?
        if new_device.device_id in self._merge_suggestions:
            return

        identity = new_device.identity_key
        for existing in self._devices.values():
            if existing.device_id == new_device.device_id:
                continue
            if existing.state != DEVICE_STATE_APPROVED:
                continue
            if existing.identity_key != identity:
                continue
            if not existing.is_stale(self.stale_timeout):
                continue

            # Found a stale approved device with same model+channel
            suggestion = MergeSuggestion(
                new_device_id=new_device.device_id,
                old_device_id=existing.device_id,
                alias=existing.alias or existing.device_id,
                model=new_device.model,
                channel=new_device.channel,
            )
            self._merge_suggestions[new_device.device_id] = suggestion
            _LOGGER.info(
                "Merge suggestion: new device %s looks like %s (%s) — "
                "same model=%s, channel=%s",
                new_device.device_id,
                existing.device_id,
                existing.alias,
                new_device.model,
                new_device.channel,
            )
            break  # One suggestion per new device

    # ── Discovery Payload Building ───────────────────────────────────────────

    def _get_alias_slug(self, device: DiscoveredDevice) -> str:
        """Get a slug from the device alias for use in topics and unique_ids."""
        return slugify(device.display_name)

    def build_discovery_payloads(
        self, device: DiscoveredDevice
    ) -> list[DiscoveryPayload]:
        """Build all HA MQTT auto-discovery payloads for an approved device.

        Each payload's state_topic points at the RAW rtl_433 device topic —
        the integration never republishes telemetry.

        Returns:
            List of DiscoveryPayload objects (may be empty if no mapped fields).
        """
        if device.state != DEVICE_STATE_APPROVED:
            return []

        alias_slug = self._get_alias_slug(device)
        payloads: list[DiscoveryPayload] = []

        for field_key in device.fields_seen:
            try:
                if field_key in FIELD_MAPPINGS:
                    mapping = FIELD_MAPPINGS[field_key]
                    payload = self._build_single_config(
                        device, alias_slug, mapping, field_key
                    )
                    if payload:
                        payloads.append(payload)

                # Handle secret_knock multi-mapping
                if field_key == "secret_knock":
                    for mapping in SECRET_KNOCK_MAPPINGS:
                        try:
                            payload = self._build_single_config(
                                device, alias_slug, mapping, "secret_knock"
                            )
                            if payload:
                                payloads.append(payload)
                        except (KeyError, TypeError, ValueError) as err:
                            _LOGGER.warning(
                                "Failed to build secret_knock config for device=%s: %s",
                                device.device_id,
                                err,
                            )

                # Handle synthetic (computed) mappings
                if field_key in SYNTHETIC_MAPPINGS:
                    for mapping in SYNTHETIC_MAPPINGS[field_key]:
                        try:
                            payload = self._build_single_config(
                                device, alias_slug, mapping, field_key
                            )
                            if payload:
                                payloads.append(payload)
                        except (KeyError, TypeError, ValueError) as err:
                            _LOGGER.warning(
                                "Failed to build synthetic config for device=%s field=%s: %s",
                                device.device_id,
                                field_key,
                                err,
                            )
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning(
                    "Failed to build discovery config for device=%s field=%s: %s",
                    device.device_id,
                    field_key,
                    err,
                )

        return payloads

    def _build_single_config(
        self,
        device: DiscoveredDevice,
        alias_slug: str,
        mapping: dict,
        field_key: str,
    ) -> DiscoveryPayload | None:
        """Build a single HA MQTT discovery config payload.

        The state_topic points at the raw rtl_433 device topic for this field.
        The unique_id uses the alias slug so it survives device merges.
        """
        device_type = mapping["device_type"]
        object_suffix = mapping["object_suffix"]
        object_name = f"{alias_slug}-{object_suffix}"

        # Config topic: homeassistant/{type}/{alias_slug}/{alias_slug}-{suffix}/config
        config_topic = "/".join([
            self.discovery_prefix,
            device_type,
            alias_slug,
            object_name,
            "config",
        ])

        # State topic points at the RAW rtl_433 device topic
        state_topic = f"{device.base_topic}/{field_key}"

        config = mapping["config"].copy()

        # ── Unit-aware field overrides ───────────────────────────────────────
        # For fields where the unit is ambiguous (depends on rtl_433's -C flag),
        # apply overrides based on the configured unit system.
        # Fields with explicit units in their name (temperature_C, rain_mm, etc.)
        # are already correct in the static mappings.
        if field_key in UNIT_AWARE_FIELDS:
            unit_overrides = UNIT_AWARE_FIELDS[field_key].get(self.unit_system, {})
            config.update(unit_overrides)

        if device_type == "device_automation":
            config["topic"] = state_topic
            config["platform"] = "mqtt"
        else:
            readable_name = config.get("name", field_key or object_suffix)
            config["state_topic"] = state_topic
            config["unique_id"] = object_name
            config["name"] = f"{device.display_name} {readable_name}"

        # HA device registry info — uses alias for user-friendly naming
        config["device"] = {
            "identifiers": [alias_slug],
            "name": device.display_name,
            "model": device.model,
            "manufacturer": MANUFACTURER,
        }

        if self.force_update and device_type != "device_automation":
            config["force_update"] = "true"

        if self.expire_after and self.expire_after > 0 and device_type != "device_automation":
            config["expire_after"] = self.expire_after

        return DiscoveryPayload(
            config_topic=config_topic,
            payload=config,
        )

    def build_removal_payloads(self, device: DiscoveredDevice) -> list[DiscoveryPayload]:
        """Build empty-payload discovery topics to remove a device from HA.

        Publishing an empty string to a discovery config topic removes the entity.
        """
        alias_slug = self._get_alias_slug(device)
        payloads: list[DiscoveryPayload] = []

        for field_key in device.fields_seen:
            if field_key in FIELD_MAPPINGS:
                mapping = FIELD_MAPPINGS[field_key]
                object_name = f"{alias_slug}-{mapping['object_suffix']}"
                config_topic = "/".join([
                    self.discovery_prefix,
                    mapping["device_type"],
                    alias_slug,
                    object_name,
                    "config",
                ])
                payloads.append(
                    DiscoveryPayload(config_topic=config_topic, payload={})
                )

        return payloads

    # ── Main Event Processing ────────────────────────────────────────────────

    def process_event(
        self, data: dict[str, Any], topic_prefix: str
    ) -> list[DiscoveryPayload]:
        """Process a single rtl_433 event message.

        1. Checks model against blocklist
        2. Extracts device identity
        3. Updates the device registry (fields, last_seen, etc.)
        4. Checks for merge candidates (rolling-ID detection)
        5. Returns discovery payloads ONLY for approved devices

        The caller decides whether to actually publish.

        Returns:
            List of DiscoveryPayload objects ready to publish (may be empty).
        """
        if "model" not in data:
            _LOGGER.debug("Event has no 'model' field, skipping")
            return []

        raw_model = str(data["model"])

        # ── Check model blocklist before any processing ──────────────────────
        matched_pattern = match_blocked_model(raw_model, self.model_blocklist)
        if matched_pattern:
            _LOGGER.debug(
                "Blocked device model=%s (matched pattern=%s)",
                raw_model,
                matched_pattern,
            )
            return []

        model = sanitize(raw_model)

        try:
            base_topic, device_id = self._resolve_device_topic(data, topic_prefix)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Failed to resolve device topic for model=%s: %s",
                raw_model,
                err,
            )
            return []

        if not device_id:
            _LOGGER.warning("No suitable identifier for model=%s", model)
            return []

        # ── Update device registry ───────────────────────────────────────────
        now = time.time()
        is_new = device_id not in self._devices

        if is_new:
            device = DiscoveredDevice(
                model=model,
                device_id=device_id,
                channel=str(data.get("channel", "")),
                raw_id=data.get("id"),
                first_seen=now,
                last_seen=now,
                message_count=1,
                base_topic=base_topic,
            )
            self._devices[device_id] = device
            _LOGGER.info(
                "New device discovered: %s (model=%s, id=%s, channel=%s)",
                device_id,
                model,
                data.get("id"),
                data.get("channel"),
            )
        else:
            device = self._devices[device_id]
            device.last_seen = now
            device.message_count += 1
            device.base_topic = base_topic

        # Track sample field values (for all devices, even ignored)
        for key, value in data.items():
            if key not in SKIP_KEYS:
                device.fields_seen[key] = value

        # ── Check merge candidates for new devices ───────────────────────────
        if is_new and device.state == DEVICE_STATE_DISCOVERED:
            self._check_merge_candidates(device)

        # ── Handle ignored devices ───────────────────────────────────────────
        if device.state == DEVICE_STATE_IGNORED:
            _LOGGER.debug("Device %s is ignored, skipping", device_id)
            return []

        # ── Return discovery payloads for approved devices ───────────────────
        if device.state == DEVICE_STATE_APPROVED:
            return self.build_discovery_payloads(device)

        # Device is in 'discovered' state — don't publish
        return []

    # ── Summary / UI Helpers ─────────────────────────────────────────────────

    def get_device_summary(self) -> list[dict[str, Any]]:
        """Get a summary of all discovered devices for UI display."""
        summary = []
        for device in self._devices.values():
            mapped_fields = [
                k for k in device.fields_seen if k in FIELD_MAPPINGS
            ]
            summary.append({
                "device_id": device.device_id,
                "alias": device.alias,
                "model": device.model,
                "channel": device.channel,
                "raw_id": device.raw_id,
                "state": device.state,
                "first_seen": device.first_seen,
                "last_seen": device.last_seen,
                "message_count": device.message_count,
                "stale": device.is_stale(self.stale_timeout),
                "mapped_fields": mapped_fields,
                "unmapped_fields": [
                    k for k in device.fields_seen
                    if k not in FIELD_MAPPINGS and k not in SKIP_KEYS
                ],
                "sample_values": device.fields_seen,
            })
        return sorted(summary, key=lambda d: d["last_seen"], reverse=True)

    def get_merge_summary(self) -> list[dict[str, Any]]:
        """Get pending merge suggestions for UI display."""
        return [
            s.to_dict()
            for s in self._merge_suggestions.values()
            if s.state == MERGE_STATE_PENDING
        ]
