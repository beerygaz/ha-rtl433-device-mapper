# Architecture — RTL-433 MQTT Discovery Bridge V2

## Overview

A Home Assistant custom integration that bridges rtl_433 433 MHz device data
(published via MQTT) into HA with user-controlled device selection, aliasing,
and rolling-ID merge detection.

**V2 is a provisioning layer only — it NEVER republishes telemetry.**

```
┌──────────────┐       ┌──────────────┐       ┌────────────────────────┐
│  rtl_433     │──────▶│  Mosquitto   │──────▶│  rtl433_discovery      │
│  (rfgw host) │ MQTT  │  (HA add-on) │ MQTT  │  (this integration)    │
└──────────────┘       └──────────────┘       │                        │
                                              │  Subscribes to events  │
                                              │  Publishes discovery   │
                                              │  configs ONLY          │
                                              └────────────┬───────────┘
                                                           │
                                              ┌────────────▼───────────┐
                                              │  HA MQTT Integration   │
                                              │  Picks up configs →    │
                                              │  creates entities →    │
                                              │  entities subscribe    │
                                              │  DIRECTLY to rtl_433   │
                                              │  raw device topics     │
                                              └────────────────────────┘
```

## Data Flow

```
rtl_433 publishes to:
  rtl_433/{instance}/events                    ← JSON event (full device payload)
  rtl_433/{instance}/devices/{model}/{ch}/{id}/{field}  ← individual field values

Our integration:
  SUBSCRIBES to: rtl_433/+/events              ← discover devices from events
  PUBLISHES to:  homeassistant/{type}/{alias}/{alias}-{suffix}/config
                                               ← HA auto-discovery configs

Discovery config payload contains:
  state_topic: rtl_433/{instance}/devices/{model}/{ch}/{id}/{field}
              ↑ points at the RAW rtl_433 topic — we never touch the data

Result:
  HA entities subscribe directly to rtl_433 device topics
  Our integration can go down — entities keep working
```

## Components

### 1. `const.py` — Constants and Field Mappings (no HA imports)

- All config keys, defaults, device states
- Complete field-to-HA-entity mapping table (ported from rtl_433_mqtt_hass.py)
- 60+ field mappings covering weather, air quality, energy, binary sensors, etc.
- Must be loadable standalone by the CLI test harness

### 2. `discovery.py` — Core Discovery Engine (no HA imports)

The heart of the integration. Pure Python, no I/O dependencies.

**`RTL433DiscoveryEngine`**:
- Processes rtl_433 JSON events
- Manages in-memory device registry with three states (discovered/approved/ignored)
- Builds HA discovery config payloads with `state_topic` pointing at raw rtl_433 topics
- Tracks device aliases (friendly names) that survive merges
- Detects rolling-ID changes by matching model+channel against stale approved devices
- Provides merge suggestions for UI presentation
- Serializable state for persistence

**`DiscoveredDevice`**:
- Data class with alias support, staleness detection, identity key for merge matching
- Serializable to/from JSON

**`MergeSuggestion`**:
- Tracks pending merge candidates (new device → stale approved device)
- Created automatically when a new device matches an approved device's model+channel

**`DiscoveryPayload`**:
- Ready-to-publish HA discovery config with the critical `state_topic` pointing at raw rtl_433

### 3. `__init__.py` — HA Integration Glue

- Uses HA's native MQTT integration (no separate paho-mqtt connection)
- Subscribes to rtl_433 events via `mqtt.async_subscribe()`
- Publishes discovery configs via `mqtt.async_publish()`
- Loads/saves engine state via HA's `helpers.storage.Store`
- Registers services: `approve_device`, `ignore_device`, `merge_device`, `reset_device`
- Fires HA events for new discoveries and merge suggestions
- Periodic persistence every 5 minutes
- Republishes all approved device configs on startup

### 4. `config_flow.py` — Configuration UI

**Setup flow (V2 simplified):**
- Only needs the rtl_433 event topic pattern (uses HA's MQTT for connectivity)

**Options flow (4 steps):**
1. **Menu** — Manage Devices / Merge Suggestions / Settings
2. **Manage Devices** — multi-select approve/ignore with stale indicators
3. **Merge Devices** — review and accept rolling-ID merge suggestions
4. **Settings** — discovery prefix, topic suffix, stale timeout, expire_after, etc.

### 5. `test_discover.py` — Standalone CLI Test Harness

Works WITHOUT Home Assistant:
- Loads `const.py` and `discovery.py` via importlib (bypasses `__init__.py`)
- Connects to MQTT via paho-mqtt
- Shows real-time device discoveries in a colored terminal table
- `--approve` mode auto-approves devices and shows actual HA discovery payloads
- `--dump` mode shows raw MQTT messages
- Demonstrates that state_topic points at raw rtl_433 topics (no republishing)

## Device Lifecycle

```
┌──────────────┐
│  DISCOVERED  │ ◀── initial state when first seen on air
└──────┬───────┘
       │
   ┌───┴───┐
   ▼       ▼
┌──────┐ ┌───────┐
│APPROV│ │IGNORED│
│  ED  │ │       │
└──┬───┘ └──┬────┘
   │        │
   └───┬────┘
       ▼
   (can reset back to DISCOVERED)
```

## Merge Flow (Rolling ID)

```
1. Device "Acurite-5n1-C-448" is APPROVED with alias "Weather Station"
2. Battery dies / sensor reboots / random ID change
3. "Acurite-5n1-C-448" goes stale (no events for >1 hour)
4. "Acurite-5n1-C-512" appears (same model, same channel, new ID)
5. Engine detects: same identity_key (Acurite-5n1-C) + stale approved match
6. Creates MergeSuggestion: "Acurite-5n1-C-512 → Weather Station?"
7. User accepts merge via UI or service call
8. Engine:
   a. Publishes empty configs for old device topics (removes old entities)
   b. Transfers alias + approved state to new device
   c. Publishes new configs with state_topic pointing at new device's topics
   d. unique_id stays the same → HA sees it as the same entity
   e. History preserved, dashboards unaffected
```

## Key Design Decisions

### Why provisioning only (no telemetry republishing)?

V1 considered republishing but it was unnecessary overhead:
- rtl_433 already publishes per-field topics (`devices/.../temperature_C`)
- HA's MQTT discovery supports pointing `state_topic` at any topic
- Removing the middleman means zero latency, zero data loss if integration restarts
- Simpler code, fewer failure modes

### Why HA's native MQTT instead of paho-mqtt?

V1 used a standalone paho-mqtt connection. V2 uses `homeassistant.components.mqtt`:
- No duplicate MQTT credentials to configure
- Automatic reconnection handled by HA
- Works with HA's MQTT add-on out of the box
- Less code, fewer moving parts
- `async_subscribe` / `async_publish` integrate with HA's event loop

### Why alias-based unique_ids?

When a device is merged (rolling ID change), the `unique_id` must stay the same
for HA to treat it as the same entity. Using the alias slug (`weather_station`)
instead of the device_id (`Acurite-5n1-C-448`) means:
- Merge = update state_topic, same unique_id → same entity → history preserved
- User sees "Weather Station Temperature" not "Acurite-5n1-C-448 Temperature"

### Why track all devices (even ignored)?

Ignored devices still update `last_seen` and `fields_seen`. This allows:
- Changing your mind (reset back to discovered)
- Accurate device count in the UI
- No "device keeps reappearing" if we deleted it completely

### Staleness detection

Configurable timeout (default 1 hour). Used for:
- Marking devices as stale in the UI (visual indicator)
- Merge detection: only stale approved devices are merge candidates
- Could be extended for availability tracking in future versions

## Storage

- Engine state persisted via HA's `helpers.storage.Store`
- Location: `.storage/rtl433_discovery_registry` in HA config dir
- Saved every 5 minutes and on shutdown
- Loaded on startup — device states, aliases, and merge history survive restarts
- Storage version 2 (upgraded from V1 format, backwards compatible)

## Services

| Service | Fields | Description |
|---|---|---|
| `approve_device` | `device_id`, `alias` (optional) | Approve + optionally alias |
| `ignore_device` | `device_id` | Blocklist a device |
| `merge_device` | `new_device_id`, `old_device_id` | Merge rolling ID |
| `reset_device` | `device_id` | Reset to discovered |

## Events

| Event | Data | Trigger |
|---|---|---|
| `rtl433_discovery_device_discovered` | `device_id` | New device first seen |
| `rtl433_discovery_merge_suggested` | `new_device_id`, `old_device_id`, `alias`, `model` | Merge candidate detected |
