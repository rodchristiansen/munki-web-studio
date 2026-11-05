# Munki Web Studio: the web app, API and MCP server in one image.
# Build:  docker build -t munki-web-studio .
# Every base image is pinned by digest so a rebuild gets the same layers.

FROM node@sha256:0820eaa85e07d0490446dd01d14707e17e6108a36988cf374899cd057eb3812d AS web
WORKDIR /build/web
RUN npm install --global pnpm@10.34.7
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN mkdir -p ../src/munki_web_studio && pnpm build

FROM ghcr.io/astral-sh/uv@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/studio
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
COPY --from=web /build/src/munki_web_studio/static/ src/munki_web_studio/static/
RUN uv sync --frozen --no-dev --no-editable && rm /usr/local/bin/uv

# A fixed, unprivileged user. The repository is mounted read-only unless the
# config marks it writable; app state lives on its own volume.
RUN groupadd --system --gid 10001 studio \
 && useradd --system --uid 10001 --gid studio --home-dir /var/lib/studio --shell /usr/sbin/nologin studio \
 && mkdir -p /var/lib/studio /etc/studio \
 && chown studio:studio /var/lib/studio
USER 10001:10001
ENV PATH=/opt/studio/bin:$PATH \
    MUNKI_WEB_STUDIO_CONFIG=/etc/studio/studio.toml
WORKDIR /var/lib/studio
EXPOSE 3186
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3186/api/health', timeout=4).status == 200 else 1)"]
CMD ["mwsctl", "serve", "--host", "0.0.0.0", "--port", "3186"]
