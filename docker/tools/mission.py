#!/usr/bin/env python3
"""Send one GNSS_ONLY mission and follow it to the end.

Publishes the same message the dashboard sends (MissionList on
/mission_list). Steps: set the datum (unless --no-datum), wait for
mission_master's subscription, publish one MissionList, then follow
/mission_status: RUNNING -> arrival -> COMPLETED.

  python3 /opt/mr2/mission.py   (make mission)
      [--lat 38.406766] [--lon -110.791394] [--no-datum]
      [--timeout 300] [--id 1]

Prints JSON lines; exits 0 only on COMPLETED.
"""
import argparse
import json
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from mr2_action_interface.msg import MissionList, MissionSpec, MissionStatus
from robot_localization.srv import SetDatum

DATUM = (38.406739, -110.791394)
STATES = {0: "IDLE", 1: "RUNNING", 2: "PAUSED", 3: "COMPLETED", 4: "FAILED"}


def log(event, **kw):
    print(json.dumps({"t": round(time.time(), 3), "event": event, **kw}),
          flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=38.406766)
    ap.add_argument("--lon", type=float, default=-110.791394)
    ap.add_argument("--no-datum", action="store_true")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--id", type=int, default=1)
    a = ap.parse_args()

    rclpy.init()
    n = Node("mr2_mission_probe")
    qos = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                     history=HistoryPolicy.KEEP_LAST, depth=10)
    seen = []

    def on_status(msg):
        st = STATES.get(msg.state, str(msg.state))
        key = (st, msg.arrival, msg.active_mission.mission_id)
        if not seen or seen[-1] != key:
            seen.append(key)
            log("status", state=st, arrival=msg.arrival,
                mission_id=msg.active_mission.mission_id, detail=msg.detail,
                stamp=msg.stamp.sec + msg.stamp.nanosec * 1e-9)

    n.create_subscription(MissionStatus, "/mission_status", on_status, qos)
    pub = n.create_publisher(MissionList, "/mission_list", qos)

    def spin(sec):
        end = time.time() + sec
        while time.time() < end:
            rclpy.spin_once(n, timeout_sec=0.1)

    rc = 1
    try:
        if not a.no_datum:
            cli = n.create_client(SetDatum, "/navsat_transform/datum")
            if not cli.wait_for_service(timeout_sec=20):
                log("error", msg="datum service not found")
                return 1
            req = SetDatum.Request()
            req.geo_pose.position.latitude = DATUM[0]
            req.geo_pose.position.longitude = DATUM[1]
            req.geo_pose.orientation.w = 1.0
            fut = cli.call_async(req)
            end = time.time() + 10
            while not fut.done() and time.time() < end:
                rclpy.spin_once(n, timeout_sec=0.1)
            log("datum", ok=fut.done(), lat=DATUM[0], lon=DATUM[1])
            spin(3.0)
        else:
            log("datum", skipped=True)

        end = time.time() + 30
        while pub.get_subscription_count() == 0 and time.time() < end:
            spin(0.2)
        subs = pub.get_subscription_count()
        log("mission_list_subscribers", count=subs)
        if subs == 0:
            return 1

        msg = MissionList()
        msg.stamp.sec = int(time.time())
        spec = MissionSpec()
        spec.mission_id = a.id
        spec.mission_type = 1
        spec.detection_method = 0
        spec.object_type = 0
        spec.target_latitude = a.lat
        spec.target_longitude = a.lon
        spec.target_radius = 0.0
        spec.waypoint_count = 0
        msg.missions = [spec]
        pub.publish(msg)
        log("sent", lat=a.lat, lon=a.lon, mission_id=a.id)

        end = time.time() + a.timeout
        while time.time() < end:
            spin(0.2)
            # mission_master reports mission_id 0 once a mission ends,
            # so end on a terminal state after our mission was RUNNING.
            ran = any(s[0] == "RUNNING" and s[2] == a.id for s in seen)
            if ran and seen[-1][0] in ("COMPLETED", "FAILED"):
                break
        arrived = any(s[1] for s in seen if s[2] == a.id)
        final = seen[-1][0] if seen else None
        ok = final == "COMPLETED" and arrived
        log("result", ok=ok, final=final, arrival_seen=arrived,
            transitions=[s[0] + ("+arrival" if s[1] else "") for s in seen])
        rc = 0 if ok else 1
    finally:
        n.destroy_node()
        rclpy.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
