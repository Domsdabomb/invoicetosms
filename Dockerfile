FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# SQLite database lives in /data so it can be mounted as a volume
# and survive container rebuilds
ENV DATABASE_PATH=/data/crucible.db
RUN mkdir -p /data

EXPOSE 8000

CMD gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 60
