FROM node:22-bookworm-slim AS web-builder

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY advisor/ ./advisor/
COPY config/ ./config/
COPY data/raw/ ./data/raw/
COPY --from=web-builder /build/web/dist ./web/dist/

RUN mkdir -p /var/data/profiles /var/data/processed /var/data/uploads /var/data/updates

EXPOSE 10000

CMD ["sh", "-c", "python -m advisor.server --host 0.0.0.0 --port ${PORT:-10000} --static-dir web/dist --profiles-dir /var/data/profiles --datasets-dir /var/data/processed --uploads-dir /var/data/uploads --updates-dir /var/data/updates"]
