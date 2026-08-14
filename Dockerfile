FROM python:3.12 AS builder

RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app/
COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src

RUN uv venv --python 3.12 && \
    uv sync --no-dev --frozen

FROM python:3.12-slim

WORKDIR /app/
COPY --from=builder /app /app
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY config/logging /app/config/logging

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080
CMD ["uvicorn", "v4vapp_dash.main:app", "--host", "0.0.0.0", "--port", "8080"]
