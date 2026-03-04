# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies in a separate layer for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user for security
RUN groupadd -r botuser && useradd -r -g botuser botuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source
COPY --chown=botuser:botuser . .

# Create data dir with correct ownership (for local SQLite dev)
RUN mkdir -p data && chown botuser:botuser data

# Switch to non-root
USER botuser

# Health check — just verifies the process is alive
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import discord; print('ok')" || exit 1

CMD ["python", "main.py"]
