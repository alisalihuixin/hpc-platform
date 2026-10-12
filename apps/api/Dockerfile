FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# gcc + libpq are needed to build a couple of wheels; curl is used by healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies + package first so the layer is cached across code changes.
COPY pyproject.toml ./
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --upgrade pip --index-url "$PIP_INDEX_URL" \
    && pip install --index-url "$PIP_INDEX_URL" .

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
