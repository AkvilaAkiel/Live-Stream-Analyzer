FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p /app/config /app/screenshots

COPY analyzer_service.py /app/analyzer_service.py

ENV PYTHONUNBUFFERED=1
ENV THRESHOLDS_PATH=/app/config/config.json
ENV STREAMS_PATH=/app/config/streams.json
ENV SCREENSHOT_DIR=/app/screenshots

CMD ["python", "analyzer_service.py"]
