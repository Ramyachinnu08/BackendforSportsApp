FROM python:3.12-slim

WORKDIR /srv/sportyqo
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # fail the BUILD loudly if deps didn't install (guards against a
    # poisoned cache layer shipping an image without alembic/uvicorn)
    && alembic --version && uvicorn --version

COPY . .

EXPOSE 8000
# Migrations run first, then the API. Scale-out note: move migrations to a
# one-shot job when running multiple replicas.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
