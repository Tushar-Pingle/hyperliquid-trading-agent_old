FROM python:3.12-slim

WORKDIR /app

# Install dependencies directly (no poetry needed)
# Phase 0 (0.1): the retired default model needs a current SDK; pin the floor so
# adaptive-thinking / current-model params are available. (Full lockfile pinning
# is a Phase 6 deploy-hardening item.)
RUN pip install --no-cache-dir \
    hyperliquid-python-sdk \
    "anthropic>=0.69.0" \
    python-dotenv \
    aiohttp \
    requests

# Copy source
COPY src ./src

# API defaults
ENV APP_PORT=3000
EXPOSE 3000

ENTRYPOINT ["python", "-m", "src.main"]
