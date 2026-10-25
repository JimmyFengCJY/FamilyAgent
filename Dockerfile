FROM node:22-bookworm-slim AS web
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html tsconfig.json vite.config.ts ./
COPY apps/web/src ./apps/web/src
COPY public ./public
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FAMILYAGENT_PROVIDER=demo \
    FAMILYAGENT_DEMO_LOGIN=0 \
    FAMILYAGENT_EMBEDDED_WORKER=0 \
    FAMILYAGENT_DB=/data/familyagent.sqlite3
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --only-binary=cryptography,cffi,rpds-py,pydantic-core -r requirements.lock \
    && pip check \
    && groupadd --gid 10001 familyagent \
    && useradd --uid 10001 --gid 10001 --no-create-home familyagent \
    && mkdir /data && chown 10001:10001 /data
COPY services ./services
COPY --from=web /build/dist ./dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD ["python", "-m", "services.api.app.container_health"]
ENTRYPOINT ["python", "-m", "services.api.app.container_start"]
CMD ["api"]
