# Changelog

## [0.1.2] — 2026-03-23

### Fixed
- Added icon.png and logo.png to custom_components directory for HA native brand display
- Brand assets now available in both `brand/` (HACS) and `custom_components/` (HA 2026.3+)

## [0.1.1] — 2026-03-23

### Fixed
- Added missing `services.yaml` — resolves "Failed to load services.yaml for integration" error on HA startup
- Signal arc spacing in brand icon/logo to fit within circle boundary

## [0.1.0] — 2026-03-22

### Added
- Initial release
- MQTT-based RTL-433 device discovery and provisioning
- Device approval, ignore, merge, and reset services
- Persistent device state with automatic periodic saves
- Stale device detection
- Rolling-code device merge suggestions
- HACS-compatible packaging with brand assets
