#!/usr/bin/env python3
"""Read-only discovery and parameter probe for RobStride private CAN."""

import argparse
import json
from pathlib import Path
import select
import socket
import struct
import time


FRAME = struct.Struct("=IB3x8s")
EFF = socket.CAN_EFF_FLAG
MASK = 0x1FFFFFFF


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iface", default="can0")
    parser.add_argument("--host-id", type=int, default=253)
    parser.add_argument("--motor-id", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 0 <= args.host_id <= 255:
        parser.error("host-id must be 0..255")
    if args.motor_id is not None and not 1 <= args.motor_id <= 127:
        parser.error("motor-id must be 1..127")
    result = {"iface": args.iface, "host_id": args.host_id,
              "motor_ids": [], "frames": [], "parameters": {}}
    with socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as bus:
        bus.bind((args.iface,))
        bus.setblocking(False)

        def receive(seconds):
            end = time.monotonic() + seconds
            received = []
            while time.monotonic() < end:
                ready, _, _ = select.select([bus], [], [], max(0, end - time.monotonic()))
                if not ready:
                    break
                raw = bus.recv(FRAME.size)
                cid, dlc, data = FRAME.unpack(raw)
                entry = {"time": time.monotonic(), "can_id": f"{cid:08X}",
                         "dlc": dlc, "data": data[:dlc].hex()}
                result["frames"].append(entry)
                if cid & 0xE0000000 != EFF or dlc != 8:
                    continue
                received.append((cid & MASK, data))
            return received

        def send(kind, motor, payload=b"\0" * 8):
            # Only non-motion Type 0 (identity) and Type 17 (read) are supported.
            assert kind in (0, 17)
            cid = EFF | kind << 24 | args.host_id << 8 | motor
            bus.send(FRAME.pack(cid, 8, payload))

        passive = receive(0.4)
        result["passive_extended_ids"] = sorted({f"{cid:08X}" for cid, _ in passive})
        found = set()
        identities = {}
        targets = [args.motor_id] if args.motor_id is not None else range(1, 128)
        for motor in targets:
            send(0, motor)
            for cid, data in receive(0.015):
                if cid >> 24 == 0 and cid & 255 == 0xFE:
                    address = (cid >> 8) & 0xFFFF
                    if 1 <= address <= 127:
                        found.add(address)
                        identities[str(address)] = data.hex()
        for cid, data in receive(0.2):
            if cid >> 24 == 0 and cid & 255 == 0xFE:
                address = (cid >> 8) & 0xFFFF
                if 1 <= address <= 127:
                    found.add(address)
                    identities[str(address)] = data.hex()
        result["motor_ids"] = sorted(found)
        result["mcu_identifiers"] = identities
        # Keep reads addressed: never broadcast a mode change or enable.
        if args.motor_id is not None or len(found) == 1:
            motor = args.motor_id if args.motor_id is not None else next(iter(found))
            definitions = {0x7005: ("run_mode", "B"), 0x700A: ("spd_ref", "f"),
                           0x7018: ("limit_cur", "f"), 0x7019: ("mechPos", "f"),
                           0x701A: ("iqf", "f"), 0x701B: ("mechVel", "f"),
                           0x701C: ("VBUS", "f"), 0x7022: ("acc_rad", "f"),
                           0x7028: ("canTimeout", "I")}
            for index, (name, fmt) in definitions.items():
                send(17, motor, struct.pack("<H6x", index))
                replies = receive(0.12)
                matches = [data for cid, data in replies
                           if cid == (17 << 24 | motor << 8 | args.host_id)
                           and struct.unpack_from("<H", data)[0] == index]
                result["parameters"][name] = (
                    struct.unpack_from("<" + fmt, matches[-1], 4)[0] if matches else None)
            result["probed_motor_id"] = motor
    encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
