FROM python:3.12-slim

LABEL maintainer="abdulwahab.yusuf"
LABEL description="Offshore separation train sensor simulator"
LABEL version="1.0.0"

WORKDIR /app

# Non-root user (IEC 62443 least privilege)
RUN groupadd -r simulator && \
    useradd -r -g simulator -d /app -s /sbin/nologin simulator

# Data directory for SQLite (volume mount point)
RUN mkdir -p /data /app/config && chown simulator:simulator /data

# Copy in order of change frequency  for layer caching
COPY --chown=simulator:simulator requirements.txt .
COPY --chown=simulator:simulator config/ /app/config/
COPY --chown=simulator:simulator src/ /app/src/

# Runtime configuration
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/src
ENV LOG_LEVEL=INFO
ENV API_HOST=0.0.0.0
ENV API_PORT=8080
ENV DB_PATH=/data/sensors.db
ENV CONFIG_PATH=/app/config/sensors.json
ENV POLL_INTERVAL_MS=1000
ENV CLEANUP_HOURS=24

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

USER simulator

CMD ["python", "-m", "simulator"]