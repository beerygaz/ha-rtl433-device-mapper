#!/usr/bin/env python3
"""CLI test wrapper — connects to MQTT broker, discovers rtl_433 devices, shows what it finds.

This script works WITHOUT Home Assistant installed. It loads const.py and
discovery.py directly via importlib, bypassing __init__.py (which requires HA).

Usage:
  python3 test_discover.py -H <mqtt_host> -u <user> -P <pass>
  python3 test_discover.py -H ha.local -u mqtt_user -P mqtt_pass --timeout 60
  python3 test_discover.py -H ha.local -u mqtt_user -P mqtt_pass --dump

Environment variables:
  MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import sys
import time
from datetime import datetime

# ── Load const.py and discovery.py directly (no HA dependencies) ─────────────

_components_path = os.path.join(
    os.path.dirname(__file__), "custom_components", "rtl433_device_mapper"
)


def _load_module(name: str, filepath: str):
    """Load a Python module from file path, registering it in sys.modules."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_const = _load_module(
    "rtl433_device_mapper.const", os.path.join(_components_path, "const.py")
)
_discovery = _load_module(
    "rtl433_device_mapper.discovery", os.path.join(_components_path, "discovery.py")
)

import paho.mqtt.client as mqtt

RTL433DiscoveryEngine = _discovery.RTL433DiscoveryEngine

# ── Terminal Colors ──────────────────────────────────────────────────────────

GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


class RTL433TestHarness:
    """Connects to MQTT, feeds events to the discovery engine, prints results.

    This is the V2 test harness. Key differences from V1:
    - Shows alias support in device output
    - Demonstrates merge detection for rolling-ID sensors
    - Discovery payloads use state_topic pointing at raw rtl_433 topics
    - Supports --approve to auto-approve devices for payload preview
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.engine = RTL433DiscoveryEngine(
            discovery_prefix=args.discovery_prefix,
            expire_after=args.expire_after,
            force_update=args.force_update,
            stale_timeout=args.stale_timeout,
        )
        self.event_count = 0
        self.start_time = time.time()
        self._running = True

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata,
        flags: dict,
        rc: int,
    ) -> None:
        """Handle MQTT connection."""
        if rc != 0:
            print(f"{RED}✗ MQTT connect failed: {mqtt.connack_string(rc)}{RESET}")
            sys.exit(1)

        topic = self.args.rtl_topic
        print(f"{GREEN}✓ Connected to {self.args.host}:{self.args.port}{RESET}")
        print(f"  Subscribing to: {CYAN}{topic}{RESET}")
        if self.args.approve:
            print(f"  {YELLOW}Auto-approve mode:{RESET} all devices will be approved")
        print(f"  Waiting for rtl_433 events... (Ctrl+C to stop)\n")
        client.subscribe(topic)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata,
        rc: int,
    ) -> None:
        """Handle MQTT disconnection."""
        if self._running:
            print(f"\n{YELLOW}⚠ Disconnected: {mqtt.connack_string(rc)}{RESET}")

    def _on_message(
        self,
        client: mqtt.Client,
        userdata,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Handle incoming rtl_433 event messages."""
        try:
            data = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            return

        self.event_count += 1
        topic_prefix = "/".join(msg.topic.split("/", 2)[:2])

        if self.args.dump:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"{DIM}[{ts}] {msg.topic}{RESET}")
            print(f"  {json.dumps(data, indent=2)}\n")

        # Track new discoveries
        known_before = set(self.engine.devices.keys())

        # Auto-approve if requested
        if self.args.approve:
            # Pre-approve: after processing, approve any new devices
            pass

        # Feed to discovery engine
        payloads = self.engine.process_event(data, topic_prefix)

        # Detect new devices
        new_devices = set(self.engine.devices.keys()) - known_before
        for device_id in new_devices:
            dev = self.engine.devices[device_id]
            mapped = [
                k for k in dev.fields_seen if k in _const.FIELD_MAPPINGS
            ]
            print(
                f"  {GREEN}★ New device:{RESET} {BOLD}{device_id}{RESET} "
                f"({dev.model})"
            )
            if mapped:
                samples = ", ".join(
                    f"{k}={dev.fields_seen[k]}" for k in mapped[:5]
                )
                print(f"    Fields: {samples}")
            print()

            # Auto-approve new devices if --approve flag set
            if self.args.approve:
                alias = self.args.alias or device_id
                self.engine.approve_device(device_id, alias)
                print(
                    f"    {YELLOW}→ Auto-approved as '{alias}'{RESET}\n"
                )

        # Show merge suggestions
        for suggestion in self.engine.get_merge_summary():
            if suggestion["new_device_id"] in new_devices:
                print(
                    f"  {YELLOW}⚡ Merge suggestion:{RESET} "
                    f"{suggestion['new_device_id']} looks like "
                    f"{suggestion['alias']} (was {suggestion['old_device_id']})"
                )
                print(
                    f"    Same model={suggestion['model']}, "
                    f"channel={suggestion['channel']}\n"
                )

    def print_summary(self) -> None:
        """Print a table of all discovered devices and sample payloads."""
        elapsed = time.time() - self.start_time
        summary = self.engine.get_device_summary()

        print(f"\n{'─' * 90}")
        print(
            f"{BOLD}Discovery Summary{RESET} "
            f"({self.event_count} events in {elapsed:.0f}s)"
        )
        print(f"{'─' * 90}")

        if not summary:
            print(f"\n  {YELLOW}No devices discovered.{RESET}")
            print(
                f"  Check that rtl_433 is running and publishing to MQTT."
            )
            print(f"  Expected topic: {self.args.rtl_topic}\n")
            return

        # Device table
        print(
            f"\n  {'Device ID':<30} {'Alias':<20} {'Model':<18} "
            f"{'Msgs':>5}  {'State':<10}  {'Fields'}"
        )
        print(
            f"  {'─' * 30} {'─' * 20} {'─' * 18} "
            f"{'─' * 5}  {'─' * 10}  {'─' * 20}"
        )

        state_icons = {
            "discovered": f"{YELLOW}○{RESET}",
            "approved": f"{GREEN}●{RESET}",
            "ignored": f"{DIM}✗{RESET}",
        }

        for d in summary:
            icon = state_icons.get(d["state"], "?")
            alias = d.get("alias") or "—"
            stale = " ⏸" if d.get("stale") else ""
            fields = ", ".join(d["mapped_fields"][:4])
            if len(d["mapped_fields"]) > 4:
                fields += f" +{len(d['mapped_fields']) - 4}"

            state_color = {
                "discovered": YELLOW,
                "approved": GREEN,
                "ignored": DIM,
            }.get(d["state"], "")

            print(
                f"  {icon} {state_color}{d['device_id']:<29}{RESET} "
                f"{alias:<20} {d['model']:<18} "
                f"{d['message_count']:>5}  {d['state']:<10}{stale}  {fields}"
            )

        # Merge suggestions
        merges = self.engine.get_merge_summary()
        if merges:
            print(f"\n  {BOLD}Pending Merge Suggestions:{RESET}")
            for m in merges:
                print(
                    f"    {YELLOW}⚡{RESET} {m['new_device_id']} → "
                    f"{m['alias']} (was {m['old_device_id']})"
                )

        print()

        # Show HA discovery payloads for approved (or first) device
        target_device = None
        for device in self.engine.approved_devices.values():
            target_device = device
            break

        if not target_device and summary:
            # Temporarily approve the first device to show example payloads
            first_id = summary[0]["device_id"]
            dev = self.engine.devices.get(first_id)
            if dev:
                original_state = dev.state
                original_alias = dev.alias
                dev.state = "approved"
                dev.alias = dev.alias or dev.device_id
                payloads = self.engine.build_discovery_payloads(dev)
                dev.state = original_state
                dev.alias = original_alias
                if payloads:
                    self._print_example_payloads(first_id, payloads, simulated=True)
                return

        if target_device:
            payloads = self.engine.build_discovery_payloads(target_device)
            if payloads:
                self._print_example_payloads(
                    target_device.device_id, payloads, simulated=False
                )

    def _print_example_payloads(
        self,
        device_id: str,
        payloads: list,
        simulated: bool = False,
    ) -> None:
        """Print example HA discovery payloads."""
        label = "Simulated" if simulated else "Actual"
        print(
            f"  {BOLD}{label} HA discovery payloads for {device_id}:{RESET}\n"
        )
        for p in payloads[:5]:  # Limit to 5 to avoid overwhelming output
            print(f"  {CYAN}Topic:{RESET} {p.config_topic}")

            # Highlight the state_topic — it should point at raw rtl_433
            payload = p.payload
            state_topic = payload.get("state_topic", payload.get("topic", ""))
            if state_topic:
                print(
                    f"  {CYAN}state_topic:{RESET} {GREEN}{state_topic}{RESET} "
                    f"← raw rtl_433 topic (no republishing!)"
                )

            print(f"  {CYAN}Payload:{RESET}")
            print(f"  {json.dumps(payload, indent=4)}\n")

        if len(payloads) > 5:
            print(
                f"  {DIM}... and {len(payloads) - 5} more payloads{RESET}\n"
            )

    def run(self) -> None:
        """Connect to MQTT and listen for events."""
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1
            )
        else:
            client = mqtt.Client()

        if self.args.user:
            client.username_pw_set(self.args.user, self.args.password)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        print(f"\n{BOLD}RTL-433 Discovery Test Harness (V2 — Provisioning Only){RESET}")
        print(f"  Broker: {self.args.host}:{self.args.port}")
        print(f"  Topic:  {self.args.rtl_topic}")
        if self.args.timeout:
            print(f"  Timeout: {self.args.timeout}s")
        print(f"  Connecting...\n")

        try:
            client.connect(self.args.host, self.args.port, 60)
        except Exception as e:
            print(f"{RED}✗ Connection failed: {e}{RESET}")
            sys.exit(1)

        # Handle Ctrl+C gracefully
        def handle_signal(sig, frame):
            self._running = False
            client.disconnect()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        client.loop_start()

        try:
            if self.args.timeout:
                time.sleep(self.args.timeout)
                self._running = False
                client.disconnect()
            else:
                while self._running:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            client.loop_stop()
            self.print_summary()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Test harness for RTL-433 → HA discovery engine (V2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Architecture (V2 — provisioning only):
  This integration NEVER republishes telemetry. Discovery config payloads
  set state_topic pointing at the RAW rtl_433 device topics. If the
  integration goes down, HA entities keep receiving data.

Examples:
  %(prog)s -H ha.local -u user -P pass
  %(prog)s -H ha.local -u user -P pass --timeout 30
  %(prog)s -H ha.local -u user -P pass --dump
  %(prog)s -H ha.local -u user -P pass --approve --alias "Weather Station"
        """,
    )
    parser.add_argument(
        "-H", "--host",
        default=os.environ.get("MQTT_HOST", "127.0.0.1"),
        help="MQTT broker host (default: $MQTT_HOST or 127.0.0.1)",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=int(os.environ.get("MQTT_PORT", "1883")),
        help="MQTT broker port (default: $MQTT_PORT or 1883)",
    )
    parser.add_argument(
        "-u", "--user",
        default=os.environ.get("MQTT_USERNAME"),
        help="MQTT username (default: $MQTT_USERNAME)",
    )
    parser.add_argument(
        "-P", "--password",
        default=os.environ.get("MQTT_PASSWORD"),
        help="MQTT password (default: $MQTT_PASSWORD)",
    )
    parser.add_argument(
        "-R", "--rtl-topic",
        dest="rtl_topic",
        default="rtl_433/+/events",
        help="rtl_433 event topic (default: rtl_433/+/events)",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=None,
        help="Listen for N seconds then show summary (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Dump raw MQTT messages",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Auto-approve all discovered devices (for payload preview)",
    )
    parser.add_argument(
        "--alias",
        default=None,
        help="Alias for auto-approved devices (use with --approve)",
    )
    parser.add_argument(
        "--discovery-prefix",
        dest="discovery_prefix",
        default="homeassistant",
        help="HA discovery topic prefix (default: homeassistant)",
    )
    parser.add_argument(
        "--expire-after",
        dest="expire_after",
        type=int,
        default=0,
        help="Seconds before sensor becomes unavailable (default: 0 = disabled)",
    )
    parser.add_argument(
        "--stale-timeout",
        dest="stale_timeout",
        type=int,
        default=3600,
        help="Seconds before a device is considered stale (default: 3600 = 1h)",
    )
    parser.add_argument(
        "--force-update",
        dest="force_update",
        action="store_true",
        help="Set force_update on all entities",
    )

    args = parser.parse_args()

    if not args.user:
        print(
            f"{YELLOW}⚠ No MQTT username set. "
            f"Use -u/--user or MQTT_USERNAME env var.{RESET}"
        )

    harness = RTL433TestHarness(args)
    harness.run()


if __name__ == "__main__":
    main()
