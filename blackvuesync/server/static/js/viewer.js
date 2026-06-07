// viewer.js: plain-JS dashcam viewer. loads recordings, plays front+rear in
// lockstep (front master, rear slaved), and (part 2) drives a Leaflet map +
// Chart.js telemetry off video.currentTime, accumulating across an
// auto-advanced journey. csp-clean: no eval, no innerHTML for server data.

const KMH_PER_KNOT = 1.852;
const MPH_PER_KNOT = 1.15078;
const DRIFT_TOLERANCE = 0.15; // seconds before re-pinning the slave video

function fmtTime(seconds) {
  const total = Math.floor(Number(seconds) || 0);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return mins + ":" + String(secs).padStart(2, "0");
}

async function fetchJson(url) {
  try {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    return resp.ok ? await resp.json() : null;
  } catch {
    // network error: caller keeps the current state
    return null;
  }
}

function recordingKey(rec) {
  return rec.base_filename + "_" + rec.type;
}

const viewer = {
  el: null,
  front: null,
  rear: null,
  player: null,
  speedUnit: "kmh",
  journeyMode: "progressive",
  chain: [],
  index: 0,
  _selectSeq: 0,

  init() {
    this.el = document.getElementById("viewer-app");
    if (!this.el) return;
    this.front = document.getElementById("viewer-front");
    this.rear = document.getElementById("viewer-rear");
    this.player = this.el.querySelector(".viewer-player");
    this.speedUnit = this.el.dataset.speedUnit || "kmh";
    this.journeyMode = this.el.dataset.journeyMode || "progressive"; // consumed in part 2 (progressive vs full telemetry loading)
    this.bindTransport();
    this.bindSync();
    this.loadRecordings();
    this.initTelemetry();
  },

  async loadRecordings() {
    const data = await fetchJson("/api/viewer/recordings");
    const side = document.getElementById("viewer-recordings");
    side.replaceChildren();
    if (!data) {
      const note = document.createElement("p");
      note.className = "viewer-note";
      note.textContent = "Could not load recordings.";
      side.append(note);
      return;
    }
    for (const day of data.days) {
      const label = document.createElement("div");
      label.className = "viewer-day-label";
      label.textContent = day.date;
      side.append(label);
      for (const rec of day.recordings) {
        side.append(this.recRow(rec));
      }
    }
  },

  recRow(rec) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "viewer-rec";
    row.dataset.key = recordingKey(rec);
    if (rec.thumb) {
      const img = document.createElement("img");
      img.src = rec.thumb;
      img.alt = "";
      row.append(img);
    }
    const time = document.createElement("span");
    time.textContent = rec.datetime.slice(11, 16);
    const badge = document.createElement("span");
    badge.className = "viewer-badge";
    badge.textContent = rec.type;
    row.append(time, badge);
    row.addEventListener("click", () => this.selectRecording(rec));
    return row;
  },

  markActive(key) {
    this.el.querySelectorAll(".viewer-rec").forEach((row) => {
      row.classList.toggle("active", row.dataset.key === key);
    });
  },

  async selectRecording(rec) {
    const seq = (this._selectSeq = this._selectSeq + 1);
    this.markActive(recordingKey(rec));
    const journey = await fetchJson(
      "/api/viewer/recordings/" + recordingKey(rec) + "/journey"
    );
    if (seq !== this._selectSeq) return; // a newer selection superseded this one
    this.chain = journey?.segments ?? [rec];
    this.index = 0;
    this.resetTelemetry();
    await this.loadSegment(0, true);
    if (this.journeyMode === "full") {
      this.prefetchRest(0);
    }
  },

  async loadSegment(i, autoplay) {
    const seg = this.chain[i];
    if (!seg) return;
    this.index = i;
    this.front.src = seg.videos.F || seg.videos[seg.directions[0]];
    if (seg.videos.R) {
      this.rear.src = seg.videos.R;
      this.rear.style.display = "";
    } else {
      this.rear.removeAttribute("src");
      this.rear.style.display = "none";
    }
    await this.loadSegmentTelemetry(seg, i);
    if (autoplay) {
      this.front.play().catch(() => {
        // autoplay may be blocked until a user gesture; ignore
      });
    }
  },

  bindSync() {
    const sync = () => {
      if (!this.rear.src) return;
      if (Math.abs(this.rear.currentTime - this.front.currentTime) > DRIFT_TOLERANCE) {
        this.rear.currentTime = this.front.currentTime;
      }
    };
    this.front.addEventListener("play", () => {
      if (this.rear.src) this.rear.play().catch(() => { /* slave play blocked; ignore */ });
    });
    this.front.addEventListener("pause", () => this.rear.pause());
    this.front.addEventListener("seeking", sync);
    this.front.addEventListener("ratechange", () => {
      this.rear.playbackRate = this.front.playbackRate;
    });
    this.front.addEventListener("timeupdate", () => {
      this.updateTimeUi();
      sync();
      this.onTick();
    });
    this.front.addEventListener("ended", () => this.onSegmentEnded());
  },

  bindTransport() {
    document.getElementById("viewer-play").addEventListener("click", () => {
      if (this.front.paused) this.front.play().catch(() => { /* ignore */ });
      else this.front.pause();
    });
    document.getElementById("viewer-seek").addEventListener("input", (ev) => {
      const frac = Number(ev.currentTarget.value) / 1000;
      if (this.front.duration) this.front.currentTime = frac * this.front.duration;
    });
    document.getElementById("viewer-layout").addEventListener("click", () => {
      const pip = this.player.dataset.layout === "pip";
      this.player.dataset.layout = pip ? "sbs" : "pip";
    });
    document.getElementById("viewer-swap").addEventListener("click", () => {
      this.front.classList.toggle("viewer-video-primary");
      this.front.classList.toggle("viewer-video-secondary");
      this.rear.classList.toggle("viewer-video-primary");
      this.rear.classList.toggle("viewer-video-secondary");
    });
    document.getElementById("viewer-next").addEventListener("click", () => this.onSegmentEnded());
  },

  updateTimeUi() {
    const seek = document.getElementById("viewer-seek");
    if (this.front.duration) {
      seek.value = String(Math.round((this.front.currentTime / this.front.duration) * 1000));
    }
    document.getElementById("viewer-time").textContent =
      fmtTime(this.front.currentTime) + " / " + fmtTime(this.front.duration);
  },

  // --- telemetry state (part 2) ---
  map: null,
  pathLayer: null,
  marker: null,
  gsChart: null,
  track: [], // accumulated {st: session-time s, lat, lon, speed} across the journey
  gforce: [], // accumulated {st, mag} for the g-sensor chart
  offsets: [], // per-segment duration (s); offsets[i] = duration of segment i
  loaded: null, // Set of chain indices whose telemetry has been appended (idempotency)

  initTelemetry() {
    const leaflet = globalThis.L;
    this.map = leaflet.map("viewer-map");
    leaflet
      .tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "© OpenStreetMap",
      })
      .addTo(this.map);
    this.map.setView([0, 0], 2);
    this.gsChart = new globalThis.Chart(document.getElementById("viewer-gsensor"), {
      type: "line",
      data: { labels: [], datasets: [{ label: "G", data: [], pointRadius: 0, borderColor: "#ff9f0a" }] },
      options: {
        responsive: true,
        animation: false,
        plugins: { legend: { display: false } },
        scales: { x: { display: false } },
      },
    });
  },

  resetTelemetry() {
    this.track = [];
    this.gforce = [];
    this.offsets = [];
    this.loaded = new Set();
    if (this.pathLayer) {
      this.pathLayer.remove();
      this.pathLayer = null;
    }
    if (this.marker) {
      this.marker.remove();
      this.marker = null;
    }
  },

  segmentOffset(i) {
    // cumulative session time (s) at the start of segment i
    let total = 0;
    for (let k = 0; k < i; k += 1) {
      total += this.offsets[k] || 0;
    }
    return total;
  },

  async loadSegmentTelemetry(seg, i) {
    if (this.loaded.has(i)) {
      return; // this segment's telemetry is already accumulated
    }
    this.loaded.add(i);
    const key = recordingKey(seg);
    const offset = this.segmentOffset(i);
    let span = 0;
    if (seg.has_gps) {
      const gps = await fetchJson("/api/viewer/recordings/" + key + "/gps");
      for (const p of gps?.points ?? []) {
        this.track.push({ st: offset + p.t, lat: p.lat, lon: p.lon, speed: p.speed });
        span = Math.max(span, p.t);
      }
    }
    if (seg.has_3gf) {
      const gs = await fetchJson("/api/viewer/recordings/" + key + "/gsensor");
      for (const s of gs?.samples ?? []) {
        this.gforce.push({ st: offset + s.t, mag: Math.hypot(s.x, s.y, s.z) });
        span = Math.max(span, s.t);
      }
    }
    // duration estimate (telemetry span) offsets the next segment on the journey timeline
    this.offsets[i] = span || 60;
    this.redrawTrack();
  },

  async prefetchRest(fromIndex) {
    // full mode: load the remaining chain's telemetry up front (each call is
    // idempotent via the `loaded` set, so this never double-appends).
    for (let i = fromIndex + 1; i < this.chain.length; i += 1) {
      await this.loadSegmentTelemetry(this.chain[i], i);
    }
  },

  redrawTrack() {
    const leaflet = globalThis.L;
    const latlngs = this.track.filter((p) => p.lat != null).map((p) => [p.lat, p.lon]);
    if (latlngs.length) {
      if (this.pathLayer) {
        this.pathLayer.remove();
      }
      this.pathLayer = leaflet.polyline(latlngs, { color: "#0a84ff", weight: 3 }).addTo(this.map);
      this.map.fitBounds(this.pathLayer.getBounds(), { padding: [20, 20] });
      if (!this.marker) {
        this.marker = leaflet
          .circleMarker(latlngs[0], { radius: 6, color: "#fff", fillColor: "#0a84ff", fillOpacity: 1 })
          .addTo(this.map);
      }
    }
    this.gsChart.data.labels = this.gforce.map(() => "");
    this.gsChart.data.datasets[0].data = this.gforce.map((g) => g.mag);
    this.gsChart.update("none");
  },

  nearest(sessionTime) {
    // nearest accumulated track point to a session time (linear scan; tracks are small)
    let best = null;
    let bestDelta = Infinity;
    for (const p of this.track) {
      const delta = Math.abs(p.st - sessionTime);
      if (delta < bestDelta) {
        bestDelta = delta;
        best = p;
      }
    }
    return best;
  },

  onTick() {
    const sessionTime = this.segmentOffset(this.index) + this.front.currentTime;
    const point = this.nearest(sessionTime);
    if (point && this.marker) {
      this.marker.setLatLng([point.lat, point.lon]);
    }
    if (point) {
      const knots = point.speed ?? 0;
      const factor = this.speedUnit === "mph" ? MPH_PER_KNOT : KMH_PER_KNOT;
      document.getElementById("viewer-speed-value").textContent = String(Math.round(knots * factor));
    }
  },

  onSegmentEnded() {
    const next = this.index + 1;
    if (next < this.chain.length) {
      this.loadSegment(next, true);
    }
  },
};

document.addEventListener("DOMContentLoaded", () => viewer.init());
