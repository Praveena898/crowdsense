import { useEffect, useRef, useState } from "react";
import { ComposedChart, Area, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, ReferenceLine } from "recharts";

const ZC = ["#7c8cff", "#2dd4bf", "#fbbf24", "#f472b6", "#60a5fa", "#a3e635"];   // zone identity colours
const ST = { normal: "var(--ok)", warning: "var(--warn)", critical: "var(--bad)" };
const LABEL = { normal: "Normal", warning: "Near limit", critical: "Over capacity" };

const call = async (u, o) => {
  const r = await fetch(u, o);
  if (!r.ok) { let d = r.statusText; try { const j = await r.json(); d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {} throw new Error(d); }
  return r;
};
const post = (u, b) => call(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });

export default function App() {
  const [phase, setPhase] = useState("setup");
  const [session, setSession] = useState(null);
  const [zones, setZones] = useState([]);
  const [rules, setRules] = useState({ dwell_limit_s: 10, hold_s: 2, conf: 0.3 });
  const [err, setErr] = useState("");
  const [runState, setRunState] = useState("Idle");

  const start = async () => {
    try { await post(`/api/sessions/${session.id}/start`, { zones, ...rules }); setPhase("live"); }
    catch (e) { setErr(e.message); }
  };
  const back = () => { if (session) call(`/api/sessions/${session.id}/stop`, { method: "POST" }).catch(() => {}); setPhase("setup"); setRunState("Idle"); };

  return (<>
    <header className="top">
      <div className="brand">
        <svg viewBox="0 0 32 32" width="30" height="30"><rect width="32" height="32" rx="9" fill="#7c8cff"/><circle cx="11" cy="12" r="3" fill="#0a0e1a"/><circle cx="21" cy="12" r="3" fill="#0a0e1a"/><path d="M5 25c0-4 3-6 6-6s6 2 6 6M15 25c0-4 3-6 6-6s6 2 6 6" stroke="#0a0e1a" strokeWidth="2.2" fill="none" strokeLinecap="round"/></svg>
        <div><b>CrowdSense</b><span>Crowd density and bottleneck analytics</span></div>
      </div>
      <div className="top-r">
        {session && <span className="file">{session.name}</span>}
        <span className={"pill " + (runState === "Analysing" ? "live" : "")}><i />{runState}</span>
        {phase === "live" && <button className="btn ghost" onClick={back}>New analysis</button>}
      </div>
    </header>
    {err && <div className="toast" role="alert" onClick={() => setErr("")}>{err}<small>Click to dismiss</small></div>}
    <main>
      {phase === "setup"
        ? <Setup {...{ session, setSession, zones, setZones, rules, setRules, onStart: start, onErr: setErr }} />
        : <Live key={session.id} session={session} zones={zones} onState={setRunState} />}
    </main>
  </>);
}

/* ---------------- Setup: source, zones, rules ---------------- */
function Setup({ session, setSession, zones, setZones, rules, setRules, onStart, onErr }) {
  const [samples, setSamples] = useState([]), [pick, setPick] = useState(""), [file, setFile] = useState(null), [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({ name: "Zone A", cap: 5 });
  useEffect(() => { fetch("/api/samples").then(r => r.json()).then(l => { setSamples(l); setPick(l[0] || ""); }).catch(() => {}); }, []);

  const load = async () => {
    const fd = new FormData();
    if (file) fd.append("file", file); else if (pick) fd.append("sample", pick); else return onErr("Choose a sample video or upload one.");
    setBusy(true);
    try { const i = await (await call("/api/sessions", { method: "POST", body: fd })).json();
      setSession({ id: i.session_id, name: i.name, dur: i.duration_s }); setZones([]); }
    catch (e) { onErr(e.message); }
    setBusy(false);
  };
  const addZone = pts => {
    if (pts.length < 3) return onErr("A zone needs at least 3 corners.");
    const name = draft.name.trim() || `Zone ${zones.length + 1}`;
    setZones(z => [...z, { name, capacity: Math.max(1, +draft.cap || 1), points: pts }]);
    setDraft({ name: `Zone ${String.fromCharCode(66 + zones.length)}`, cap: draft.cap });
  };
  const rule = (k, v) => setRules(r => ({ ...r, [k]: +v }));

  return (
    <div className="setup">
      <section className="card stage-card">
        {session ? <Draw src={`/api/sessions/${session.id}/preview?t=${session.id}`} zones={zones} onClose={addZone} />
          : <div className="empty"><svg width="54" height="54" viewBox="0 0 24 24" fill="none" stroke="#7c8cff" strokeWidth="1.5"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 9.5v5l4.5-2.5z"/></svg>
              <h3>Start with a video</h3><p>Pick a sample or upload a CCTV recording on the right. You will then outline the areas to monitor on its first frame.</p></div>}
      </section>

      <aside className="side">
        <section className="card">
          <h2><span className="n">1</span>Video source</h2>
          <label className="f">Sample video
            <select value={pick} onChange={e => { setPick(e.target.value); setFile(null); }}>
              {samples.length ? samples.map(s => <option key={s}>{s}</option>) : <option value="">No files in samples/</option>}
            </select></label>
          <label className="f">Or upload a file
            <input type="file" accept="video/*" onChange={e => setFile(e.target.files[0] || null)} /></label>
          <button className="btn" onClick={load} disabled={busy}>{busy ? "Loading..." : session ? "Reload video" : "Load video"}</button>
        </section>

        <section className={"card " + (session ? "" : "off")}>
          <h2><span className="n">2</span>Zones</h2>
          <div className="two">
            <label className="f">Name<input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
            <label className="f">Capacity<input type="number" min="1" value={draft.cap} onChange={e => setDraft({ ...draft, cap: e.target.value })} /></label>
          </div>
          <p className="hint">Click the video to place corners. Click the first point, or press Close zone, to finish.</p>
          <ul className="zl">
            {zones.map((z, i) => <li key={i}><i style={{ background: ZC[i % 6] }} /><b>{z.name}</b><span>capacity {z.capacity}</span>
              <button aria-label={`Remove ${z.name}`} onClick={() => setZones(zones.filter((_, j) => j !== i))}>×</button></li>)}
            {!zones.length && <li className="none">No zones yet</li>}
          </ul>
        </section>

        <section className={"card " + (session ? "" : "off")}>
          <h2><span className="n">3</span>Alert rules</h2>
          <Slider label="Long-dwell limit" v={rules.dwell_limit_s} min={3} max={60} step={1} unit="s" on={v => rule("dwell_limit_s", v)} />
          <Slider label="Hold before alerting" v={rules.hold_s} min={0} max={10} step={0.5} unit="s" on={v => rule("hold_s", v)} />
          <Slider label="Detection confidence" v={rules.conf} min={0.2} max={0.7} step={0.05} unit="" on={v => rule("conf", v)} />
        </section>

        <button className="btn big" disabled={!zones.length} onClick={onStart}>Start analysis</button>
        {!zones.length && <p className="hint c">Add at least one zone to start.</p>}
      </aside>
    </div>
  );
}
const Slider = ({ label, v, min, max, step, unit, on }) => (
  <label className="sl"><span>{label}<b>{v}{unit}</b></span><input type="range" {...{ min, max, step }} value={v} onChange={e => on(e.target.value)} /></label>);

function Draw({ src, zones, onClose }) {
  const cv = useRef(), im = useRef();
  const [pts, setPts] = useState([]), [hv, setHv] = useState(null), [sz, setSz] = useState([0, 0]);
  const fit = () => im.current && setSz([im.current.clientWidth, im.current.clientHeight]);
  useEffect(() => { const ro = new ResizeObserver(fit); ro.observe(im.current); return () => ro.disconnect(); }, []);
  useEffect(() => {
    const c = cv.current, [w, h] = sz; c.width = w; c.height = h;
    const x = c.getContext("2d"); x.clearRect(0, 0, w, h);
    const path = (p, close) => { x.beginPath(); p.forEach(([a, b], i) => i ? x.lineTo(a * w, b * h) : x.moveTo(a * w, b * h)); if (close) x.closePath(); };
    zones.forEach((z, i) => { path(z.points, true); x.fillStyle = ZC[i % 6] + "30"; x.fill(); x.strokeStyle = ZC[i % 6]; x.lineWidth = 2.5; x.stroke();
      x.fillStyle = "#fff"; x.font = "700 13px Manrope,sans-serif"; x.fillText(`${z.name} (${z.capacity})`, z.points[0][0] * w + 8, z.points[0][1] * h + 20); });
    const all = hv && pts.length ? [...pts, hv] : pts;
    if (all.length) { path(all, false); x.strokeStyle = "#fff"; x.setLineDash([7, 5]); x.lineWidth = 2; x.stroke(); x.setLineDash([]); }
    pts.forEach(([a, b], i) => { x.beginPath(); x.arc(a * w, b * h, i ? 4.5 : 8, 0, 7); x.fillStyle = i ? "#fff" : "#7c8cff"; x.fill(); });
  }, [pts, hv, zones, sz]);
  const rel = e => { const r = cv.current.getBoundingClientRect(); return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]; };
  const close = () => { if (pts.length >= 3) { onClose(pts); setPts([]); } else onClose(pts); };
  const click = e => {
    const p = rel(e), r = cv.current.getBoundingClientRect();
    if (pts.length >= 3 && Math.hypot((p[0] - pts[0][0]) * r.width, (p[1] - pts[0][1]) * r.height) < 14) return close();
    setPts([...pts, p]);
  };
  return (
    <div className="stage">
      <img ref={im} src={src} onLoad={fit} alt="First video frame" />
      <canvas ref={cv} onClick={click} onMouseMove={e => setHv(rel(e))} onMouseLeave={() => setHv(null)} />
      <div className="chip hint-chip">{pts.length ? `${pts.length} corner${pts.length > 1 ? "s" : ""} placed` : "Click to place the first corner"}</div>
      <div className="tools">
        <button className="btn ghost sm" onClick={() => setPts(p => p.slice(0, -1))} disabled={!pts.length}>Undo</button>
        <button className="btn sm" onClick={close} disabled={pts.length < 3}>Close zone</button>
      </div>
    </div>);
}

/* ---------------- Live dashboard ---------------- */
function Live({ session, zones, onState }) {
  const [m, setM] = useState(null), [series, setSeries] = useState([]), [alerts, setAlerts] = useState([]);
  const [peak, setPeak] = useState(0), [opt, setOpt] = useState({ heatmap: true, boxes: true }), [done, setDone] = useState(false);
  const last = useRef(-1);

  useEffect(() => {
    const es = new EventSource(`/api/sessions/${session.id}/events`);
    onState("Analysing");
    es.onmessage = ev => {
      const s = JSON.parse(ev.data); setM(s); setPeak(p => Math.max(p, s.total));
      const sec = Math.floor(s.video_time);
      if (sec !== last.current) { last.current = sec; const row = { t: sec, total: s.total }; s.zones.forEach((z, i) => row["z" + i] = z.count); setSeries(a => [...a, row]); }
      if (s.events.length) setAlerts(a => [...s.events.map(e => ({ ...e, id: Math.random() })), ...a]);
    };
    es.addEventListener("done", () => { es.close(); setDone(true); onState("Finished"); });
    return () => es.close();
  }, [session.id]);

  const flip = k => { const o = { ...opt, [k]: !opt[k] }; setOpt(o); post(`/api/sessions/${session.id}/toggle`, o).catch(() => {}); };
  const stop = () => { call(`/api/sessions/${session.id}/stop`, { method: "POST" }).catch(() => {}); setDone(true); onState("Stopped"); };
  const Z = m?.zones || [];
  const crit = Z.filter(z => z.status === "critical"), warn = Z.filter(z => z.status === "warning");
  const busiest = Z.length ? Z.reduce((a, b) => b.occupancy_pct > a.occupancy_pct ? b : a) : null;
  const totalCap = zones.reduce((s, z) => s + z.capacity, 0);

  return (
    <div className="live">
      <div className="kpis">
        <Kpi v={m?.total ?? "0"} l="People in view" s="across the whole frame" />
        <Kpi v={peak} l="Peak so far" s="highest simultaneous count" />
        <Kpi v={busiest ? `${busiest.occupancy_pct}%` : "0%"} l="Busiest zone" s={busiest?.name || "no data yet"} tone={busiest ? busiest.status : "normal"} />
        <Kpi v={m?.proc_fps ?? "0"} l="Frames per second" s="processing speed on this PC" />
      </div>

      <section className="card video-card">
        <div className="stage">
          <img src={`/api/sessions/${session.id}/stream`} alt="Live analysed video" />
          <div className={"banner " + (crit.length ? "bad show" : warn.length ? "warn show" : "")} role="status">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/></svg>
            <div>{crit.length ? <><b>Congestion warning</b><span>{crit.map(z => `${z.name} has ${z.count} people (limit ${z.capacity})`).join(". ")}</span></>
              : <><b>Approaching capacity</b><span>{warn.map(z => z.name).join(", ")} is close to its limit or has long dwell times</span></>}</div>
          </div>
          <div className="chip clock">{done ? "Ended" : "Live"} at {m?.video_time ?? 0}s</div>
        </div>
        <div className="bar">
          <Toggle on={opt.heatmap} set={() => flip("heatmap")}>Heatmap</Toggle>
          <Toggle on={opt.boxes} set={() => flip("boxes")}>Boxes and IDs</Toggle>
          <div className="legend"><span>Less crowded</span><i /><span>More crowded</span></div>
          <button className="btn ghost sm" onClick={stop} disabled={done}>Stop</button>
        </div>
        <div className="prog"><div style={{ width: (done && m ? 100 : m?.progress || 0) + "%" }} /></div>
      </section>

      <aside className="side">
        <section className="card">
          <h2>Zone occupancy</h2>
          <div className="zgrid">
            {Z.length ? Z.map((z, i) => <ZoneCard key={i} z={z} color={ZC[i % 6]} />)
              : zones.map((z, i) => <ZoneCard key={i} z={{ name: z.name, count: 0, capacity: z.capacity, occupancy_pct: 0, status: "normal", avg_dwell_s: 0, avg_speed_px_s: 0 }} color={ZC[i % 6]} />)}
          </div>
          <p className="hint">Speed is in pixels per second because the camera is not calibrated to real distances.</p>
        </section>
        <section className="card">
          <h2>Alert log <em>{alerts.length}</em></h2>
          <ul className="alerts">
            {alerts.map(a => <li key={a.id} className={a.state === "cleared" ? "ok" : a.severity}>
              <i /><div><b>{a.type === "over_capacity" ? "Over capacity" : "Long dwell time"} {a.state === "cleared" ? "cleared" : "raised"}</b>
                <span>{a.zone}: {a.count} of {a.capacity} people</span></div><time>{a.video_time}s</time></li>)}
            {!alerts.length && <li className="none">No alerts yet. One appears when a zone passes its capacity or people stay too long.</li>}
          </ul>
        </section>
      </aside>

      <section className="card chart-card">
        <h2>Headcount over time</h2>
        <div className="chart">
          <ResponsiveContainer>
            <ComposedChart data={series} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
              <defs><linearGradient id="tg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#7c8cff" stopOpacity=".45" /><stop offset="100%" stopColor="#7c8cff" stopOpacity="0" /></linearGradient></defs>
              <CartesianGrid stroke="#222c47" vertical={false} />
              <XAxis dataKey="t" stroke="#8793b2" tickLine={false} unit="s" />
              <YAxis stroke="#8793b2" tickLine={false} allowDecimals={false} />
              <Tooltip contentStyle={{ background: "#161e33", border: "1px solid #222c47", borderRadius: 10 }} labelFormatter={t => `${t}s`} />
              <Area dataKey="total" name="Total" stroke="#7c8cff" strokeWidth={2.5} fill="url(#tg)" isAnimationActive={false} />
              {zones.map((z, i) => <Line key={i} dataKey={"z" + i} name={z.name} stroke={ZC[i % 6]} strokeWidth={2} dot={false} isAnimationActive={false} />)}
              {zones.length > 0 && <ReferenceLine y={totalCap} stroke="#fb5b5b" strokeDasharray="5 5" label={{ value: "Total capacity", fill: "#fb5b5b", fontSize: 11, position: "insideTopRight" }} />}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </section>
    </div>);
}

const Kpi = ({ v, l, s, tone = "normal" }) => (
  <div className="kpi"><span className="kl">{l}</span><b style={tone !== "normal" ? { color: ST[tone] } : null}>{v}</b><span className="ks">{s}</span></div>);
const Toggle = ({ on, set, children }) => (
  <button className={"tg " + (on ? "on" : "")} role="switch" aria-checked={on} onClick={set}><i />{children}</button>);

function ZoneCard({ z, color }) {
  const r = 44, c = 2 * Math.PI * r, p = Math.min(z.occupancy_pct, 100) / 100;
  return (
    <div className={"zone " + z.status}>
      <svg viewBox="0 0 110 110" className="gauge" role="img" aria-label={`${z.name}: ${z.count} of ${z.capacity}`}>
        <circle cx="55" cy="55" r={r} className="g-bg" />
        <circle cx="55" cy="55" r={r} className="g-fg" stroke={ST[z.status]} strokeDasharray={`${c * p} ${c}`} transform="rotate(-90 55 55)" />
        <text x="55" y="56" className="g-n">{z.count}</text><text x="55" y="73" className="g-c">of {z.capacity}</text>
      </svg>
      <div className="zn"><i style={{ background: color }} />{z.name}</div>
      <div className="zs" style={{ color: ST[z.status] }}>{LABEL[z.status]}</div>
      <div className="zm"><span>Avg wait</span><b>{z.avg_dwell_s}s</b></div>
      <div className="zm"><span>Speed</span><b>{z.avg_speed_px_s} px/s</b></div>
    </div>);
}
