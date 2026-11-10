import time
import cv2
import numpy as np
from ultralytics import YOLO

GREEN, ORANGE, RED = (80, 200, 80), (0, 165, 255), (60, 60, 230)  # BGR, not RGB


class CrowdEngine:
    def __init__(self, video_path, zones, imgsz=480, frame_skip=3, conf=0.3,
                 hold_s=2.0, dwell_limit_s=20.0, max_width=960):
        self.video_path, self.zones = video_path, zones
        self.imgsz, self.frame_skip, self.conf = imgsz, frame_skip, conf
        self.hold_s, self.dwell_limit_s, self.max_width = hold_s, dwell_limit_s, max_width
        self.model = YOLO("yolov8n.pt")
        self.show_heatmap, self.show_boxes = True, True
        self.last_pos, self.speed = {}, {}              # per track-ID memory
        self.entered = [dict() for _ in zones]          # per zone: {track_id: time entered}
        self.alert_state = {}                           # (zone_idx, type) -> state
        self.heat = None

    # ---------- main loop ----------
    def run(self):
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {self.video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_idx, polys = 0, None
        while True:
            if not cap.grab():                          # grab = cheap skip, no decoding
                break
            frame_idx += 1
            if frame_idx % self.frame_skip:
                continue
            ok, frame = cap.retrieve()                  # decode only the frames we use
            if not ok:
                break
            t0 = time.time()
            h0, w0 = frame.shape[:2]
            if w0 > self.max_width:
                frame = cv2.resize(frame, (self.max_width, int(h0 * self.max_width / w0)))
            h, w = frame.shape[:2]
            if polys is None:                           # zones are stored 0-1, convert to pixels
                polys = [np.array([[x * w, y * h] for x, y in z["points"]], np.int32)
                         for z in self.zones]
                self.heat = np.zeros((h // 4, w // 4), np.float32)
            t = frame_idx / fps                         # video time in seconds
            people = self._detect(frame, t)
            stats = self._analyse(people, polys, t)
            self._draw(frame, people, polys, stats, w, h)
            _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            stats["proc_fps"] = round(1 / max(time.time() - t0, 1e-6), 1)
            stats["progress"] = round(100 * frame_idx / max(cap.get(cv2.CAP_PROP_FRAME_COUNT), 1))
            yield jpg.tobytes(), stats
        cap.release()

    # ---------- detection + tracking + velocity ----------
    def _detect(self, frame, t):
        r = self.model.track(frame, persist=True, classes=[0], conf=self.conf,
                             imgsz=self.imgsz, tracker="bytetrack.yaml", verbose=False)[0]
        people = []
        b = r.boxes
        if b is None or len(b) == 0:
            return people
        xyxy, confs = b.xyxy.cpu().numpy(), b.conf.cpu().numpy()
        ids = b.id.int().cpu().tolist() if b.id is not None else [-(i + 1) for i in range(len(b))]
        for box, c, tid in zip(xyxy, confs, ids):
            x1, y1, x2, y2 = box
            foot = (int((x1 + x2) / 2), int(y2))        # bottom-centre = where they stand
            spd, prev = 0.0, self.last_pos.get(tid)
            if prev:
                dt = t - prev[2]
                if dt > 0:
                    inst = np.hypot(foot[0] - prev[0], foot[1] - prev[1]) / dt
                    spd = 0.6 * self.speed.get(tid, inst) + 0.4 * inst   # smoothing
            self.last_pos[tid], self.speed[tid] = (foot[0], foot[1], t), spd
            people.append({"id": tid, "box": (int(x1), int(y1), int(x2), int(y2)),
                           "foot": foot, "conf": float(c), "speed": spd})
        return people

    # ---------- zones, dwell, heatmap, alerts ----------
    def _analyse(self, people, polys, t):
        self.heat *= 0.985                              # old heat fades
        H, W = self.heat.shape
        for p in people:
            iy = int(np.clip(p["foot"][1] // 4, 0, H - 1))
            ix = int(np.clip(p["foot"][0] // 4, 0, W - 1))
            self.heat[iy, ix] += 1
        events, zone_stats = [], []
        for i, (z, poly) in enumerate(zip(self.zones, polys)):
            inside = [p for p in people if cv2.pointPolygonTest(poly, p["foot"], False) >= 0]
            ids, ent = {p["id"] for p in inside}, self.entered[i]
            for p in inside:
                ent.setdefault(p["id"], t)              # remember first time seen in zone
            for tid in [k for k in ent if k not in ids]:
                del ent[tid]                            # left the zone
            dwell = [t - ent[p["id"]] for p in inside]
            avg_dwell = float(np.mean(dwell)) if dwell else 0.0
            avg_speed = float(np.mean([p["speed"] for p in inside])) if inside else 0.0
            count, cap = len(inside), z["capacity"]
            over = self._edge((i, "over"), count > cap, count < cap, t, self.hold_s)
            slow = self._edge((i, "dwell"), avg_dwell > self.dwell_limit_s,
                              avg_dwell < 0.8 * self.dwell_limit_s or count == 0, t, 0)
            for kind, res, sev in (("over_capacity", over, "critical"), ("long_dwell", slow, "warning")):
                if res:
                    events.append({"zone": z["name"], "type": kind, "state": res,
                                   "severity": sev, "count": count, "capacity": cap,
                                   "video_time": round(t, 1)})
            if self.alert_state.get((i, "over"), {}).get("active"):
                status = "critical"
            elif self.alert_state.get((i, "dwell"), {}).get("active") or count >= 0.8 * cap:
                status = "warning"
            else:
                status = "normal"
            zone_stats.append({"name": z["name"], "count": count, "capacity": cap,
                               "occupancy_pct": round(100 * count / cap),
                               "avg_dwell_s": round(avg_dwell, 1),
                               "max_dwell_s": round(max(dwell, default=0.0), 1),
                               "avg_speed_px_s": round(avg_speed), "status": status})
        return {"video_time": round(t, 1), "total": len(people),
                "zones": zone_stats, "events": events}

    def _edge(self, key, trigger, release, t, hold):
        """Fires 'raised' ONCE when trigger holds for `hold` seconds, 'cleared' ONCE on release."""
        st = self.alert_state.setdefault(key, {"active": False, "since": None})
        if not st["active"]:
            if trigger:
                st["since"] = t if st["since"] is None else st["since"]
                if t - st["since"] >= hold:
                    st["active"] = True
                    return "raised"
            else:
                st["since"] = None
        elif release:
            st["active"], st["since"] = False, None
            return "cleared"
        return None

    # ---------- drawing ----------
    def _draw(self, frame, people, polys, stats, w, h):
        if self.show_heatmap and self.heat.max() > 1e-3:
            blur = cv2.GaussianBlur(self.heat, (0, 0), 4)
            norm = blur / max(blur.max(), 1e-6)         # relative heat: hottest spot = 1.0
            color = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_JET)
            color = cv2.resize(color, (w, h))
            mask = cv2.resize(norm, (w, h)) > 0.15
            frame[mask] = cv2.addWeighted(frame, 0.45, color, 0.55, 0)[mask]
        for poly, zs in zip(polys, stats["zones"]):
            col = {"normal": GREEN, "warning": ORANGE, "critical": RED}[zs["status"]]
            cv2.polylines(frame, [poly], True, col, 2)
            cv2.putText(frame, f'{zs["name"]} {zs["count"]}/{zs["capacity"]}',
                        tuple(poly[0] + [4, 18]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        if self.show_boxes:
            for p in people:
                x1, y1, x2, y2 = p["box"]
                cv2.rectangle(frame, (x1, y1), (x2, y2), GREEN, 1)
                cv2.putText(frame, f'#{p["id"]} {p["conf"]:.2f}', (x1, max(y1 - 4, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, GREEN, 1)