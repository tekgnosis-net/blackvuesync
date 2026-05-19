FROM alpine:3.23.4

LABEL org.opencontainers.image.title="BlackVue Sync"
LABEL org.opencontainers.image.description="Hands-off synchronization of recordings from a BlackVue dashcam with a local directory over a LAN"
LABEL org.opencontainers.image.url="https://github.com/tekgnosis-net/blackvuesync"
LABEL org.opencontainers.image.source="https://github.com/tekgnosis-net/blackvuesync"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.authors="Alessandro Colomba"

VOLUME ["/recordings"]

RUN apk add --update bash python3 shadow su-exec tzdata \
    && rm -rf /var/cache/apk/* \
    && useradd -UMr dashcam

# Pulls the uv binary from Astral's pinned image. uv installs runtime deps faster
# than pip and handles Alpine's PEP 668 enforcement cleanly via --system.
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /usr/local/bin/uv

# Installs Python runtime deps into the system interpreter. Alpine's Python is
# marked PEP 668 externally-managed, so --break-system-packages is required;
# this is safe inside a container we own. The uv binary is removed in the same
# layer so it does not bloat the runtime image.
RUN uv pip install --system --break-system-packages --no-cache \
        "Flask~=3.1" "Flask-WTF~=1.2" "waitress~=3.0" \
        "argon2-cffi~=23.1" "APScheduler~=3.10" \
    && rm /usr/local/bin/uv

COPY COPYING /
COPY setuid.sh /setuid.sh
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV ADDRESS="" \
    PUID="" \
    PGID="" \
    KEEP="" \
    GROUPING="" \
    PRIORITY="" \
    MAX_USED_DISK="" \
    TIMEOUT="" \
    VERBOSE=0 \
    QUIET="" \
    LOG_FORMAT="" \
    METRICS_FILE="" \
    METRICS_PUSHGATEWAY_URL="" \
    METRICS_JOB="" \
    METRICS_INSTANCE="" \
    METRICS_STATE_FILE="" \
    DRY_RUN="" \
    AFFINITY_KEY=""

COPY --chown=dashcam blackvuesync /app/blackvuesync
ENV PYTHONPATH=/app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz').read()" || exit 1

ENTRYPOINT [ "/entrypoint.sh"]
