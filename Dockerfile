# Floden på server (S1, docs/DRIFT.md): webbklienten, API:t och kontosystemet i en avbildning.
# Byggs av GitHub Actions (.github/workflows/avbildning.yml) och hämtas från ghcr.io; servern bygger inget själv.
# Mönstret är lyft från O-Tid (github.com/joelberring/otid, MIT): Dockerfile, ADR-0195.

# 1. Webbklienten (TypeScript, Vite) -> webb/dist
FROM node:24-bookworm-slim AS webb
WORKDIR /src/webb
COPY webb/package.json webb/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY webb/ ./
RUN npm run build

# 2. Rust-verktygen: lasclip (LAZ) och tvad_karna (2D-lösaren)
FROM rust:1-bookworm AS rust
WORKDIR /src
COPY tools/lasclip tools/lasclip
COPY rust/tvad_karna rust/tvad_karna
RUN cargo build --release --locked --manifest-path tools/lasclip/Cargo.toml \
 && cargo build --release --locked --manifest-path rust/tvad_karna/Cargo.toml

# 3. Körning: Python 3.12 med Floden installerat från repot (redigerbart, eftersom koden läser repots mappar)
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLODEN_LASCLIP=/usr/local/bin/lasclip \
    FLODEN_TVAD_KARNA=/usr/local/bin/tvad_karna
# rasterio:s hjul har GDAL med sig men behöver libexpat från systemet.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin floden
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install -e ".[konto,mcp]"
COPY . .
COPY --from=webb /src/webb/dist ./webb/dist
COPY --from=rust /src/tools/lasclip/target/release/lasclip /usr/local/bin/lasclip
COPY --from=rust /src/rust/tvad_karna/target/release/tvad_karna /usr/local/bin/tvad_karna
RUN mkdir -p data/cache data/konto data/demo data/lokal \
 && chown -R floden:floden data \
 && chmod 755 deploy/start.sh
USER floden
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/halsa', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/app/deploy/start.sh"]
