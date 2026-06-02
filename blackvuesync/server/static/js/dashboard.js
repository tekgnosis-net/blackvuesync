// dashboard.js: Alpine.js component for phase 2c active mode. owns the SSE
// connection, the sidebar controls, and the body[data-state] machine. all
// visibility is CSS-driven off data-state; this only mutates the attribute and
// feeds the hero its reactive progress snapshot.

const SSE_BACKOFF_START_MS = 2000;
const SSE_BACKOFF_MAX_MS = 30000;
const COMPLETE_LINGER_MS = 10000; // matches publisher POST_COMPLETE_RETENTION

function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return el ? el.content : "";
}

document.addEventListener("alpine:init", () => {
  Alpine.data("dashboardSync", () => ({
    progress: {
      state: "idle",
      percent: 0,
      files_completed: 0,
      files_total: 0,
      files_failed: 0,
      current_file: null,
    },
    stopModalOpen: false,
    _source: null,
    _backoffMs: SSE_BACKOFF_START_MS,
    _reconnectTimer: null,
    _lastMonotonic: -1,

    init() {
      this.progress.state = document.body.dataset.state || "idle";
      // always open the stream so externally-started syncs are also detected.
      this.openStream();
    },

    // single writer of body[data-state]; css does the rest
    setState(state) {
      document.body.dataset.state =
        state === "running"
          ? "running"
          : state === "complete" || state === "failed"
            ? "complete"
            : "idle";
    },

    async syncNow() {
      const resp = await this.post("/api/sync/now");
      if (resp && (resp.status === 202 || resp.status === 409)) {
        this.setState("running");
        this.openStream();
      }
    },

    confirmStop() {
      this.stopModalOpen = true;
    },
    cancelStop() {
      this.stopModalOpen = false;
    },
    async doStop() {
      this.stopModalOpen = false;
      await this.post("/api/sync/stop"); // SSE will report the terminal state
    },

    async togglePause(currentlyPaused) {
      const path = currentlyPaused
        ? "/api/schedule/resume"
        : "/api/schedule/pause";
      const resp = await this.post(path);
      if (resp && resp.ok) {
        window.location.reload(); // reflect the new Pause/Resume label
      }
    },

    openStream() {
      if (this._source) return;
      const es = new EventSource("/api/sync/progress/stream");
      this._source = es;
      es.addEventListener("progress", (ev) => {
        this._backoffMs = SSE_BACKOFF_START_MS; // healthy frame resets backoff
        let snap;
        try {
          snap = JSON.parse(ev.data);
        } catch (err) {
          return;
        }
        if (snap.last_event_monotonic <= this._lastMonotonic) return; // stale
        this._lastMonotonic = snap.last_event_monotonic;
        this.progress = snap;
        this.setState(snap.state);
        if (snap.state === "complete" || snap.state === "failed") {
          this.closeStream();
          window.setTimeout(() => {
            if (!this._source) {
              this.setState("idle");
              this.openStream(); // reopen to detect future syncs
            }
          }, COMPLETE_LINGER_MS);
        }
      });
      es.onerror = () => {
        this.closeStream();
        this._reconnectTimer = window.setTimeout(() => {
          this.openStream(); // reconnect regardless of state to detect any sync
        }, this._backoffMs);
        this._backoffMs = Math.min(this._backoffMs * 2, SSE_BACKOFF_MAX_MS);
      };
    },

    closeStream() {
      if (this._source) {
        this._source.close();
        this._source = null;
      }
      if (this._reconnectTimer) {
        window.clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
      }
    },

    async post(path) {
      try {
        return await fetch(path, {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken() },
        });
      } catch (err) {
        return null;
      }
    },
  }));
});

// unified 302 -> /login for htmx-driven idle polls: flask login_required
// redirects to /login; htmx would otherwise swap the login page into a card.
document.body.addEventListener("htmx:beforeSwap", (event) => {
  const xhr = event.detail.xhr;
  if (xhr && xhr.responseURL && xhr.responseURL.indexOf("/login") !== -1) {
    event.detail.shouldSwap = false;
    window.location =
      "/login?next=" + encodeURIComponent(window.location.pathname);
  }
});
