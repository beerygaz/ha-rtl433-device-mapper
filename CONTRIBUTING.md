# Contributing to RTL-433 Device Mapper

Thanks for your interest in contributing! This project is actively developed and welcomes bug reports, feature requests, and pull requests.

## Reporting Bugs

1. Check [existing issues](https://github.com/beerygaz/ha-rtl433-device-mapper/issues) first
2. Include:
   - Your rtl_433 configuration (command line flags or config file)
   - The device model and a sample MQTT event payload (from `test_discover.py --dump`)
   - Home Assistant version
   - What you expected vs what happened
   - Logs from HA (Settings → System → Logs, filter for `rtl433_device_mapper`)

## Adding Support for New Devices

rtl_433 supports 200+ device types. If your device's fields aren't mapped:

1. Run `test_discover.py --dump` and capture a few event payloads
2. Check `const.py` → `FIELD_MAPPINGS` — most common fields are already mapped
3. If a field is missing, add it to `FIELD_MAPPINGS` following the existing pattern:
   ```python
   "new_field_name": {
       "device_type": "sensor",           # or "binary_sensor" or "device_automation"
       "object_suffix": "NF",             # short unique suffix for the entity
       "config": {
           "device_class": "...",          # HA device class (temperature, humidity, etc.)
           "name": "Human Readable Name",
           "unit_of_measurement": "...",   # if applicable
           "value_template": "{{ value|float }}",
           "state_class": "measurement",   # or "total_increasing" for counters
       },
   },
   ```
4. For unit-ambiguous fields (no unit in field name), add to `UNIT_AWARE_FIELDS`
5. Run the test tool to verify: `python3 test_discover.py --approve --timeout 30`
6. Submit a PR

## Development Setup

```bash
git clone https://github.com/beerygaz/ha-rtl433-device-mapper.git
cd ha-rtl433-device-mapper
pip install paho-mqtt  # for the test tool

# Test without HA
python3 test_discover.py --help

# Test against a live MQTT broker
python3 test_discover.py -H broker -u user -P pass --dump --timeout 60
```

### Code Structure

| File | Purpose | HA imports? |
|---|---|---|
| `const.py` | Constants, field mappings, config keys | ❌ No |
| `discovery.py` | Core engine (registry, payloads, merge logic) | ❌ No |
| `__init__.py` | HA integration setup, MQTT wiring, services | ✅ Yes |
| `config_flow.py` | Setup + options UI flows | ✅ Yes |
| `test_discover.py` | CLI test wrapper | ❌ No |

`const.py` and `discovery.py` must remain free of Home Assistant imports so the CLI test tool works standalone.

## Versioning

This project follows [Semantic Versioning](https://semver.org/):
- **PATCH** (0.1.x): Bug fixes, new field mappings
- **MINOR** (0.x.0): New features (e.g. new UI capabilities, new synthetic sensors)
- **MAJOR** (x.0.0): Breaking changes (config schema changes, etc.)

## Release Process

1. Update `CHANGELOG.md` — move items from `[Unreleased]` to the new version section
2. Update `manifest.json` → `version` field
3. Commit: `git commit -m "release: vX.Y.Z"`
4. Tag: `git tag vX.Y.Z`
5. Push: `git push origin main --tags`
6. Create a GitHub release from the tag (HACS uses GitHub releases for version detection)

## Code Style

- Python 3.11+ (match HA Core requirements)
- Type hints on all public functions
- Docstrings on classes and public methods
- Keep `discovery.py` pure — no I/O, no HA imports, testable in isolation
