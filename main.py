import json, shutil, sqlite3, threading, time, uuid
from collections import deque
from pathlib import Path

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import CrowdEngine

DB = "crowdsense.db"
UPLOADS, SAMPLES, FRONTEND = Path("uploads"), Path("samples"), Path("frontend")
for d in (UPLOADS, SAMPLES, FRONTEND):
    d.mkdir(exist_ok=True)

app = FastAPI(title="CrowdSense API")
app.mount("/static", StaticFiles(directory="frontend"), name="static")


# ---------------- SQLite ----------------
def q(sql, args=(), fetch=False):
    """Open a short-lived connection per call: simple and thread-safe."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()] if fetch else None
        con.commit()
        return rows
    finally:
        con.close()


q("""CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY, name TEXT, started_at TEXT, config TEXT)""")
q("""CREATE TABLE IF NOT EXISTS samples(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
        video_time REAL, total INTEGER, zones_json TEXT)""")
q("""CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, zone TEXT,
        type TEXT, state TEXT, severity TEXT, count INTEGER, capacity INTEGER,
        video_time REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")


# ---------------- request models (validation) ----------------
class Zone(BaseModel):
    name: str
    capacity: int = Field(gt=0)
    points: list[list[float]] = Field(min_length=3)    # fractions 0-1, at least a triangle


class StartReq(BaseModel):
    zones: list[Zone] = Field(min_length=1)
    dwell_limit_s: float = 20.0
    hold_s: float = 2.0
    conf: float = 0.3


class ToggleReq(BaseModel):
    heatmap: bool = True
    boxes: bool = True


# ---------------- background runner ----------------
class Runner:
    """Runs the engine in a thread and shares the latest frame + messages with web clients."""

    def __init__(self, sid, path, req: StartReq):
        self.sid = sid
        self.engine = CrowdEngine(str(path), [z.model_dump() for z in req.zones],
                                  conf=req.conf, hold_s=req.hold_s,
                                  dwell_limit_s=req.dwell_limit_s)
        self.cond = threading.Condition()
        self.jpg, self.frame_seq = None, 0
        self.msgs, self.msg_seq = deque(maxlen=500), 0
        self.state, self.error, self.stop_flag = "running", None, False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        wall0, last_sec = time.time(), -1
        try:
            for jpg, stats in self.engine.run():
                if self.stop_flag:
                    break
                lag = stats["video_time"] - (time.time() - wall0)
                if lag > 0:
                    time.sleep(min(lag, 1.0))         # play at real-time speed, not faster
                for e in stats["events"]:
                    q("""INSERT INTO alerts(session_id,zone,type,state,severity,count,capacity,video_time)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (self.sid, e["zone"], e["type"], e["state"], e["severity"],
                       e["count"], e["capacity"], e["video_time"]))
                if int(stats["video_time"]) != last_sec:   # one DB row per video second
                    last_sec = int(stats["video_time"])
                    q("INSERT INTO samples(session_id,video_time,total,zones_json) VALUES(?,?,?,?)",
                      (self.sid, stats["video_time"], stats["total"], json.dumps(stats["zones"])))
                with self.cond:
                    self.jpg, self.frame_seq = jpg, self.frame_seq + 1
                    self.msg_seq += 1
                    self.msgs.append({**stats, "seq": self.msg_seq})
                    self.cond.notify_all()
        except Exception as ex:
            self.error = str(ex)
        with self.cond:
            self.state = "error" if self.error else "finished"
            self.cond.notify_all()


RUNNERS: dict[str, Runner] = {}
VIDEOS: dict[str, dict] = {}


def get_video(sid):
    if sid not in VIDEOS:
        raise HTTPException(404, "Unknown session")
    return VIDEOS[sid]


def get_runner(sid):
    if sid not in RUNNERS:
        raise HTTPException(404, "Session not started")
    return RUNNERS[sid]


# ---------------- REST endpoints ----------------
@app.get("/")
def index():
    f = FRONTEND / "index.html"
    return FileResponse(f) if f.exists() else {"message": "API running. Open /docs"}


@app.get("/api/samples")
def list_samples():
    return sorted(p.name for p in SAMPLES.glob("*") if p.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"})


@app.post("/api/sessions")
async def create_session(file: UploadFile | None = File(None), sample: str | None = Form(None)):
    """Register a video (uploaded or from samples/) and return its id + size."""
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in {".mp4", ".avi", ".mov", ".mkv"}:
            raise HTTPException(400, "Unsupported file type. Use mp4, avi, mov or mkv.")
        sid = uuid.uuid4().hex[:8]
        path = UPLOADS / f"{sid}{ext}"
        with open(path, "wb") as out:
            shutil.copyfileobj(file.file, out)
        name = file.filename
    elif sample:
        path = SAMPLES / Path(sample).name            # .name blocks path tricks like ../..
        if not path.exists():
            raise HTTPException(404, "Sample not found")
        sid, name = uuid.uuid4().hex[:8], path.name
    else:
        raise HTTPException(400, "Provide a file or a sample name")

    cap = cv2.VideoCapture(str(path))
    ok, w, h = cap.isOpened(), cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    fps, n = cap.get(cv2.CAP_PROP_FPS) or 25, cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if not ok or w == 0:
        if path.parent == UPLOADS:
            path.unlink(missing_ok=True)
        raise HTTPException(400, "Could not read this video file")
    VIDEOS[sid] = {"path": path, "name": name}
    return {"session_id": sid, "name": name, "width": int(w), "height": int(h),
            "duration_s": round(n / fps, 1)}


@app.get("/api/sessions/{sid}/preview")
def preview(sid: str):
    """First frame, so the dashboard can draw zones on it."""
    cap = cv2.VideoCapture(str(get_video(sid)["path"]))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(400, "Cannot read frame")
    return Response(cv2.imencode(".jpg", frame)[1].tobytes(), media_type="image/jpeg")


@app.post("/api/sessions/{sid}/start")
def start(sid: str, req: StartReq):
    v = get_video(sid)
    for r in RUNNERS.values():                         # only one analysis at a time (CPU)
        r.stop_flag = True
    RUNNERS[sid] = Runner(sid, v["path"], req)
    q("INSERT OR REPLACE INTO sessions VALUES(?,?,datetime('now'),?)",
      (sid, v["name"], req.model_dump_json()))
    return {"status": "started", "session_id": sid}


@app.post("/api/sessions/{sid}/stop")
def stop(sid: str):
    get_runner(sid).stop_flag = True
    return {"status": "stopping"}


@app.post("/api/sessions/{sid}/toggle")
def toggle(sid: str, t: ToggleReq):
    r = get_runner(sid)
    r.engine.show_heatmap, r.engine.show_boxes = t.heatmap, t.boxes
    return {"heatmap": t.heatmap, "boxes": t.boxes}


@app.get("/api/sessions/{sid}/timeseries")
def timeseries(sid: str):
    rows = q("SELECT video_time,total,zones_json FROM samples WHERE session_id=? ORDER BY id", (sid,), True)
    return [{"t": r["video_time"], "total": r["total"], "zones": json.loads(r["zones_json"])} for r in rows]


@app.get("/api/alerts")
def alerts(session_id: str | None = None, limit: int = 100):
    if session_id:
        return q("SELECT * FROM alerts WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit), True)
    return q("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,), True)


@app.get("/api/history")
def history():
    return q("SELECT * FROM sessions ORDER BY started_at DESC LIMIT 50", fetch=True)


# ---------------- live streams ----------------
def mjpeg_gen(r: Runner):
    seq = 0
    while True:
        with r.cond:
            r.cond.wait_for(lambda: r.frame_seq != seq or r.state != "running", timeout=5)
            if r.frame_seq == seq:
                if r.state != "running":
                    return
                continue
            jpg, seq = r.jpg, r.frame_seq
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"


@app.get("/api/sessions/{sid}/stream")
def stream(sid: str):
    return StreamingResponse(mjpeg_gen(get_runner(sid)),
                             media_type="multipart/x-mixed-replace; boundary=frame")


def sse_gen(r: Runner):
    last = 0
    while True:
        with r.cond:
            r.cond.wait_for(lambda: r.msg_seq > last or r.state != "running", timeout=15)
            new = [m for m in r.msgs if m["seq"] > last]
            done = r.state != "running" and not new
        for m in new:
            last = m["seq"]
            yield f"data: {json.dumps(m)}\n\n"
        if done:
            yield "event: done\ndata: {}\n\n"
            return
        if not new:
            yield ": keepalive\n\n"


@app.get("/api/sessions/{sid}/events")
def events(sid: str):
    return StreamingResponse(sse_gen(get_runner(sid)), media_type="text/event-stream")