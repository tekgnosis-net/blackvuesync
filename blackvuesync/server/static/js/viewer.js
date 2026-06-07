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

  init() {
    this.el = document.getElementById("viewer-app");
    if (!this.el) return;
    this.front = document.getElementById("viewer-front");
    this.rear = document.getElementById("viewer-rear");
    this.player = this.el.querySelector(".viewer-player");
    this.speedUnit = this.el.dataset.speedUnit || "kmh";
    this.journeyMode = this.el.dataset.journeyMode || "progressive";
    this.bindTransport();
    this.bindSync();
    this.loadRecordings();
    this.initTelemetry();
  },

  async loadRecordings() {
    const data = await fetchJson("/api/viewer/recordings");
    const side = document.getElementById("viewer-recordings");
    side.replaceChildren();
    if (!data) return;
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
    this.markActive(recordingKey(rec));
    const journey = await fetchJson("/api/viewer/recordings/" + recordingKey(rec) + "/journey");
    this.chain = journey?.segments ?? [rec];
    this.index = 0;
    this.resetTelemetry();
    await this.loadSegment(0, true);
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

  initTelemetry() {},
  resetTelemetry() {},
  loadSegmentTelemetry() {},
  onTick() {},
  onSegmentEnded() {},
};

document.addEventListener("DOMContentLoaded", () => viewer.init());
