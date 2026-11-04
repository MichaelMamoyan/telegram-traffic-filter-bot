FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y build-essential && apt-get autoremove -y

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

RUN adduser --disabled-password --gecos "" botuser && chown -R botuser /app
USER botuser

CMD ["sh", "-c", "alembic upgrade head && python -m app"]
