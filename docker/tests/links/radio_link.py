#!/usr/bin/env python3
"""Radio link check, both directions, through the real gateway.

Runs in the autonomy container (ROS + tornado). Opens the gateway's
/xbee-ws like the dashboard does, sends dashboard-format heartbeats and
drive commands (+0.4 m/s) for a few seconds, and checks:
  base -> rover: /base/cmd_vel carries x ~ 0.4 (via TCP radio, XBee bridge)
  rover -> base: gateway reports link_status.connected = true (rover
                 heartbeats came back over the radio)
Prints JSON; exit 0 when both pass.
"""
import asyncio
import json
import os
import sys
import threading
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from tornado.websocket import websocket_connect

URL = os.environ.get("GATEWAY_WS", "ws://gateway:8081/xbee-ws")


def main():
    rclpy.init()
    n = Node("mr2_radio_probe")
    cmd = []
    n.create_subscription(Twist, "/base/cmd_vel",
                          lambda m: cmd.append(m.linear.x), 10)
    th = threading.Thread(target=rclpy.spin, args=(n,), daemon=True)
    th.start()
    time.sleep(2.0)
    link = []

    async def run():
        ws = await websocket_connect(URL)

        async def reader():
            while True:
                msg = await ws.read_message()
                if msg is None:
                    return
                try:
                    d = json.loads(msg)
                except ValueError:
                    continue
                if d.get("type") == "link_status":
                    link.append(bool(d.get("connected")))

        task = asyncio.ensure_future(reader())
        drive = {"type": "cmd_drive", "linear_x_m_s": 0.4,
                 "linear_y_m_s": 0, "angular_z_rad_s": 0}
        for i in range(40):
            if i % 5 == 0:
                await ws.write_message(json.dumps({"type": "heartbeat"}))
            await ws.write_message(json.dumps(drive))
            await asyncio.sleep(0.1)
        stop = dict(drive, linear_x_m_s=0.0)
        for _ in range(10):
            await ws.write_message(json.dumps(stop))
            await asyncio.sleep(0.1)
        task.cancel()
        ws.close()

    asyncio.run(run())
    time.sleep(0.5)
    peak = max(cmd) if cmd else None
    res = {
        "base_to_rover": {"ok": bool(cmd) and peak > 0.3,
                          "samples": len(cmd), "peak_x": peak},
        "rover_to_base": {"ok": any(link), "link_status": link[-5:]},
    }
    res["ok"] = res["base_to_rover"]["ok"] and res["rover_to_base"]["ok"]
    print(json.dumps(res), flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    # Exit hard: rclpy's spin thread still alive at interpreter exit makes
    # the C++ runtime abort ("terminate called without an active exception").
    os._exit(main())
