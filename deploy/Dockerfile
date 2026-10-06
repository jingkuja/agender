FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config /app/config
RUN pip install --no-cache-dir .
ENV AGENDER_CONFIG=/app/config/config.example.yaml
# Image name: agender-worker. Prefer web.* only; do not treat this as a Douyin farm.
CMD ["agender-worker", "worker"]
