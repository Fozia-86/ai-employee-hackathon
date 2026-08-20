# AI Employee Vault -- production container image
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# System deps: build tools for cryptography, plus Playwright/Chromium runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        wget \
        gnupg \
        ca-certificates \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libasound2 \
        libpango-1.0-0 \
        libcairo2 \
        libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies (no requirements.txt in this repo -- see CLAUDE.md "Helper scripts")
RUN pip install --no-cache-dir \
        watchdog \
        rank-bm25 \
        cryptography \
        requests \
        anthropic \
        python-dotenv \
        "mcp>=1.2,<2" \
        google-auth \
        google-auth-oauthlib \
        google-api-python-client \
        playwright \
        fastapi \
        uvicorn \
        jinja2 \
        python-multipart \
        itsdangerous \
        requests-oauthlib

# Install Playwright's Chromium build + its OS-level dependencies
RUN playwright install --with-deps chromium

COPY . /app

# Dynamic path support (Phase 1 multi-tenancy refactor): VAULT_PATH/PYTHON_EXEC
# default to the container's own cwd/interpreter when unset, so this image is
# tenant-portable rather than baking in a single host path.
ENV VAULT_PATH=/app \
    PYTHON_EXEC=/usr/local/bin/python3 \
    EXECUTION_ZONE=cloud \
    CLOUD_ZONE=true

CMD ["python3", "mcp_servers/agent_runner.py"]
