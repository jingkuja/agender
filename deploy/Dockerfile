FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock /app/
RUN pip install --no-cache-dir -r requirements.lock \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config /app/config
RUN pip install --no-cache-dir --no-deps .
ENV AGENDER_CONFIG=/app/config/config.example.yaml
ENV AGENDER_BROWSER_MODE=playwright
CMD ["agender-worker", "worker"]
