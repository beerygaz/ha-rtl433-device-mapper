# Changelog

## [0.2.0](https://github.com/beerygaz/ha-rtl433-device-mapper/compare/rtl433-device-mapper-v0.1.3...rtl433-device-mapper-v0.2.0) (2026-04-02)


### ⚠ BREAKING CHANGES

* Entities created by previous versions will need to be re-approved after upgrade.

### Features

* brand assets, HACS publishing compliance ([3ec7935](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/3ec7935319da28d068c1a3a4c0f23cabcb8177b2))
* HA-native device discovery UI for approve/ignore workflow ([8376d57](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/8376d57d50fea1d2c4bf745a00f665ec3630718a))
* MQTT direct publish architecture with release-please ([44f3c49](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/44f3c491c0dc785eeb82d990405d8f30e2c1598c))
* native HA sensor entities instead of MQTT discovery ([44e097d](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/44e097dc7b77c7e54f3c59def36f3f451a5c0c17))
* proper brand assets (SVG source + PNG renders) ([929214e](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/929214e339db7b76940d029c71b4e6cf0a57d497))
* RTL-433 Device Mapper for Home Assistant (v0.1.0) ([48f5680](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/48f5680438ccbf1dd273da450a52e52ee272df4e))


### Bug Fixes

* add brand/ subdir inside custom_components for HA 2026.3+ icon display ([12e3e53](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/12e3e537c3ed7191c3d5deb35cec947075268366))
* add icon/logo PNGs to custom_components for HA integration display ([406e355](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/406e35576e4e8b56e244919ddbd35cdcb7999173))
* add services.yaml to resolve HA service loading error ([d39ff9f](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/d39ff9f16c3be651312a832e09dbb89e9d11143f))
* re-render PNGs with rsvg-convert (gradient fix) ([e7ec93f](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/e7ec93f86040c731fd24d55e5c1c80bbd0ed446a))
* signal arcs contained within circle, larger radius ([30d6889](https://github.com/beerygaz/ha-rtl433-device-mapper/commit/30d6889f39c90b85ac4befb8e626dacb1dc09fec))

## [0.1.3] — 2026-03-23

### Fixed
- Brand icons now in `custom_components/<domain>/brand/` subdirectory (HA 2026.3+ native brand proxy)
- Icon displays correctly on integrations page without external brands repo submission

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
