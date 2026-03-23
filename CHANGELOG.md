# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-03-23

### Added
- Initial release
- Core discovery engine — subscribes to rtl_433 MQTT events, catalogs devices
- Device lifecycle: discover → approve/ignore workflow
- Rolling ID change detection and merge flow
- HA MQTT auto-discovery provisioning (provisioning-only — never republishes telemetry)
- Device aliasing — user-friendly names for approved devices
- Staleness detection (configurable, default 1 hour)
- Unit-aware field mappings — global config (SI/customary), explicit units in field names always win
- Signal Quality synthetic sensor (0–5 scale derived from SNR)
- 60+ field mappings: weather, lightning, soil, power, TPMS, doorbells, etc.
- Acurite 6045M lightning sensor: strike count, distance (km), storm active binary sensor
- Config flow for MQTT topic settings + unit system
- Options flow for device management (approve/ignore/merge)
- HA services: `approve_device`, `ignore_device`, `merge_device`, `reset_device`
- HACS-compatible packaging
- CLI test tool (`test_discover.py`) — standalone, no HA required
- Full README with rtl_433 MQTT configuration guide

[Unreleased]: https://github.com/beerygaz/ha-rtl433-device-mapper/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/beerygaz/ha-rtl433-device-mapper/releases/tag/v0.1.0
