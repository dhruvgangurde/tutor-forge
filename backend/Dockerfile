# TutorForge AI — backend production image (F1).
#
# The backend is a set of flat top-level modules (main.py, core/, auth/, ...)
# imported as `main:app`, `core.config`, etc. They run from the image workdir,
# so the image installs the third-party dependencies only (not a local package)
# and launches uvicorn from /app.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# build-essential covers any dependency that ships an sdist rather than a wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first for layer caching. requirements.lock.txt is the exact,
# developer-verified pin (F14). NOTE: it is generated on the dev machine; a
# Linux-native lock regenerated in CI is tracked as a Phase-3 item (F37).
COPY requirements.lock.txt ./
RUN pip install -r requirements.lock.txt

# Application source.
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Render (and most PaaS) inject $PORT; default to 8000 for local `docker run`.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
