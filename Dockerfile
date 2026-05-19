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
