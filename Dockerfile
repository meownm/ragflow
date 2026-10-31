ARG RAGFLOW_DEPS_IMAGE=infiniflow/ragflow_deps@sha256:e69762c256ea2338a786a9b2f003da8e91e5e69b26bb6797c2206276ce32e5ff

# Pin browser/parser resources independently of the model/tokenizer image.
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS resource_updates
ARG NEED_MIRROR=0
RUN apk add --no-cache ca-certificates curl
WORKDIR /resources
RUN set -eux; \
    if [ "$NEED_MIRROR" = "1" ]; then \
        tika_origin=https://repo.huaweicloud.com/repository/maven; \
        chrome_origin=https://registry.npmmirror.com/-/binary/chrome-for-testing; \
    else \
        tika_origin=https://repo1.maven.org/maven2; \
        chrome_origin=https://storage.googleapis.com/chrome-for-testing-public; \
    fi; \
    curl --fail --location --retry 5 --output tika-server-standard-3.3.3.jar \
        "$tika_origin/org/apache/tika/tika-server-standard/3.3.3/tika-server-standard-3.3.3.jar"; \
    curl --fail --location --retry 5 --output tika-server-standard-3.3.3.jar.md5 \
        "$tika_origin/org/apache/tika/tika-server-standard/3.3.3/tika-server-standard-3.3.3.jar.md5"; \
    curl --fail --location --retry 5 --output chrome-linux64-155-0-8059-84 \
        "$chrome_origin/155.0.8059.84/linux64/chrome-linux64.zip"; \
    curl --fail --location --retry 5 --output chromedriver-linux64-155-0-8059-84 \
        "$chrome_origin/155.0.8059.84/linux64/chromedriver-linux64.zip"; \
    printf '%s\n' \
        '4559c1fa6ca08390a800036438607e31a6763aba0ea11b928c3fce72315ecf0f  tika-server-standard-3.3.3.jar' \
        '24a0ab5c842bcf296bf9b8a5c5c3d76a1f1149c3fa4aedf0727929aeb32343a4  tika-server-standard-3.3.3.jar.md5' \
        'cf0f049892dea22e6a6db26d47ed0d7776b7158edcbec4634086633df8bafcbc  chrome-linux64-155-0-8059-84' \
        '4fe2433044363e5233d642e70fa50278b471bda08032d922402c8bd7c6d743fd  chromedriver-linux64-155-0-8059-84' \
        | sha256sum -c -

FROM ${RAGFLOW_DEPS_IMAGE} AS ragflow_deps
COPY --from=resource_updates /resources/ /

# base stage
FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517 AS base
USER root
SHELL ["/bin/bash", "-c"]

ARG NEED_MIRROR=0

WORKDIR /ragflow

# copy models downloaded via download_deps.py
RUN mkdir -p /ragflow/rag/res/deepdoc /root/.ragflow
RUN --mount=type=bind,from=ragflow_deps,source=/huggingface.co,target=/huggingface.co \
    tar --exclude='.*' -cf - \
        /huggingface.co/InfiniFlow/text_concat_xgb_v1.0 \
        /huggingface.co/InfiniFlow/deepdoc \
        | tar -xf - --strip-components=3 -C /ragflow/rag/res/deepdoc

# https://github.com/chrismattmann/tika-python
# This is the only way to run python-tika without internet access. Without this set, the default is to check the tika version and pull latest every time from Apache.
RUN --mount=type=bind,from=ragflow_deps,source=/,target=/deps \
    cp -r /deps/nltk_data /root/ && \
    cp /deps/tika-server-standard-3.3.3.jar /deps/tika-server-standard-3.3.3.jar.md5 /ragflow/ && \
    cp /deps/cl100k_base.tiktoken /ragflow/9b5ad71b2ce5302211f9c61530b329a4922fc6a4

ENV TIKA_SERVER_JAR="file:///ragflow/tika-server-standard-3.3.3.jar"
ENV DEBIAN_FRONTEND=noninteractive

# Setup apt
# Python package and implicit dependencies:
# opencv-python: libglib2.0-0 libglx-mesa0 libgl1
# python-pptx:   default-jdk                              tika-server-standard-3.3.3.jar
# selenium:      libatk-bridge2.0-0                       chrome-linux64-155-0-8059-84
# Building C extensions: libpython3-dev libgtk-4-1 libnss3 xdg-utils libgbm-dev
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    apt update && \
    apt --no-install-recommends install -y ca-certificates; \
    if [ "$NEED_MIRROR" == "1" ]; then \
        # CI runners may inject a proxy whose TLS certificate is not trusted inside
        # the fresh Ubuntu base image yet. Keep the Ubuntu mirror on HTTP here so
        # the mirror switch remains usable before the full CA store is available.
        sed -i 's|http://archive.ubuntu.com/ubuntu|http://mirrors.aliyun.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources; \
        sed -i 's|http://security.ubuntu.com/ubuntu|http://mirrors.aliyun.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources; \
    fi; \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    chmod 1777 /tmp && \
    apt update && \
    apt upgrade -y && \
    apt install -y \
    libglib2.0-0 libglx-mesa0 libgl1 pkg-config libgdiplus default-jdk libatk-bridge2.0-0 libgtk-4-1 libnss3 xdg-utils libjemalloc-dev gnupg unzip curl wget git vim less ghostscript pandoc texlive texlive-latex-extra texlive-xetex texlive-lang-chinese fonts-freefont-ttf fonts-noto-cjk postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Download resource from GitHub to /usr/share/infinity
RUN mkdir -p /usr/share/infinity/resource && \
    if [ "$NEED_MIRROR" == "1" ]; then \
        git clone --depth 1 --single-branch https://gitee.com/infiniflow/resource /tmp/resource; \
    else \
        git clone --depth 1 --single-branch https://github.com/infiniflow/resource.git /tmp/resource; \
    fi && \
    cp -r /tmp/resource/* /usr/share/infinity/resource && \
    rm -rf /tmp/resource

ARG NGINX_VERSION=1.31.0-1~noble
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    mkdir -p /etc/apt/keyrings && \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSL https://nginx.org/keys/nginx_signing.key | gpg --dearmor -o /etc/apt/keyrings/nginx-archive-keyring.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nginx-archive-keyring.gpg] https://nginx.org/packages/mainline/ubuntu/ noble nginx" > /etc/apt/sources.list.d/nginx.list && \
    apt -o Acquire::Retries=5 update && \
    apt -o Acquire::Retries=5 install -y nginx=${NGINX_VERSION} && \
    apt-mark hold nginx && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN --mount=type=bind,from=ragflow_deps,source=/,target=/deps \
    if [ "$NEED_MIRROR" == "1" ]; then \
        mkdir -p /etc/uv && \
        echo 'python-install-mirror = "https://registry.npmmirror.com/-/binary/python-build-standalone/"' > /etc/uv/uv.toml && \
        echo '[[index]]' >> /etc/uv/uv.toml && \
        echo 'url = "https://mirrors.aliyun.com/pypi/simple"' >> /etc/uv/uv.toml && \
        echo 'default = true' >> /etc/uv/uv.toml; \
    fi; \
    arch="$(uname -m)"; \
    if [ "$arch" = "x86_64" ]; then uv_arch="x86_64"; else uv_arch="aarch64"; fi; \
    tar xzf "/deps/uv-${uv_arch}-unknown-linux-gnu.tar.gz" \
    && cp "uv-${uv_arch}-unknown-linux-gnu/"* /usr/local/bin/ \
    && rm -rf "uv-${uv_arch}-unknown-linux-gnu" \
    && uv python install 3.13 \
    && uv pip install --break-system-packages --python "$(uv python find 3.13)" 'pip>=26.2.0,<27'

ENV PYTHONDONTWRITEBYTECODE=1 DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    UV_HTTP_TIMEOUT=200 \
    UV_HTTP_RETRIES=3
ENV PATH=/root/.local/bin:$PATH

# Use the supported Node.js 24 LTS line for browser tooling.
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt purge -y nodejs npm && \
    apt autoremove -y && \
    apt update && \
    apt install -y nodejs && \
    npm install --global npm@12.2.0 && \
    npm install --prefix /tmp/npm-security --omit=dev --ignore-scripts \
        --package-lock=false --save-exact undici@6.28.1 brace-expansion@5.0.12 \
        postcss-selector-parser@7.1.6 ip-address@10.7.1 && \
    npm_modules="$(npm root -g)/npm/node_modules" && \
    for dependency in /tmp/npm-security/node_modules/*; do \
        name="$(basename "$dependency")"; \
        rm -rf "${npm_modules:?}/${name}"; cp -a "$dependency" "$npm_modules/"; \
    done && rm -rf /tmp/npm-security && npm cache clean --force && \
    rm -rf /var/lib/apt/lists/*

# stagehand-server-v3 (Node.js SEA binary used by Browser component
# in local mode).
#
# The `v3.21.0` value below is the `stagehand-go/v3` Go module
# version pinned in `go.mod`. It is used here only to compute the
# `go_<ver>/` subdirectory that `local.go:cacheDir()` will look in
# for the binary at runtime — that subdirectory name is keyed by
# the Go module's own `internal.PackageVersion`, NOT by the server
# binary's release tag.
#
# The server binary itself is fetched separately by `download_deps.py`
# from the browserbase/stagehand GitHub releases. The two are
# LOOSELY MATCHED — both stay on the v3.x line and remain protocol-
# compatible, but the version numbers do NOT track each other (Go
# SDK is at v3.21.0, server binary is at v3.7.2 today). On every
# go.mod bump, refresh the server binary pin in `download_deps.py`
# to the current latest server release; no version correspondence
# is required to maintain.
#
# Drift on the Go SDK pin (this ARG vs go.mod) forces a fresh
# GitHub download at process boot — a hard failure in air-gapped
# deployments. CI cross-checks the two values.
#
# The binary is pre-fetched by `download_deps.py` and shipped via
# the ragflow_deps image, then written directly to the stagehand-go
# cache path that `local.go:cacheDir()` constructs at runtime —
# `/root/.cache/stagehand/lib/go_<ver>/stagehand-server-v3-<arch>`.
ARG STAGEHAND_GO_VERSION=v3.21.0
RUN --mount=type=bind,from=ragflow_deps,source=/,target=/deps \
    set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
        x86_64) stagehand_arch=x64 ;; \
        aarch64|arm64) stagehand_arch=arm64 ;; \
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    stagehand_version="${STAGEHAND_GO_VERSION#v}"; \
    stagehand_cache_dir="/root/.cache/stagehand/lib/go_${stagehand_version}"; \
    mkdir -p "${stagehand_cache_dir}"; \
    cp "/deps/stagehand-server-v3-linux-${stagehand_arch}" \
       "${stagehand_cache_dir}/stagehand-server-v3-linux-${stagehand_arch}"; \
    chmod +x "${stagehand_cache_dir}/stagehand-server-v3-linux-${stagehand_arch}"

# Add msssql ODBC driver
# macOS ARM64 environment, install msodbcsql18.
# general x86_64 environment, install msodbcsql17.
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt update && \
    arch="$(uname -m)"; \
    if [ "$arch" = "arm64" ] || [ "$arch" = "aarch64" ]; then \
        # ARM64 (macOS/Apple Silicon or Linux aarch64) \
        ACCEPT_EULA=Y apt install -y unixodbc-dev msodbcsql18; \
    else \
        # x86_64 or others \
        ACCEPT_EULA=Y apt install -y unixodbc-dev msodbcsql17; \
    fi && \
    rm -rf /var/lib/apt/lists/* || \
    { echo "Failed to install ODBC driver"; exit 1; }



# Add dependencies of selenium
RUN --mount=type=bind,from=ragflow_deps,source=/chrome-linux64-155-0-8059-84,target=/chrome-linux64.zip \
    unzip /chrome-linux64.zip && \
    mv chrome-linux64 /opt/chrome && \
    ln -s /opt/chrome/chrome /usr/local/bin/
RUN --mount=type=bind,from=ragflow_deps,source=/chromedriver-linux64-155-0-8059-84,target=/chromedriver-linux64.zip \
    unzip -j /chromedriver-linux64.zip chromedriver-linux64/chromedriver && \
    mv chromedriver /usr/local/bin/ && \
    rm -f /usr/bin/google-chrome

# Release metadata must not invalidate the Python dependency graph. These copies
# are build inputs only; production receives the original manifests below.
FROM base AS python_inputs
COPY pyproject.toml uv.lock ./
COPY deployment/runner/python_dependency_inputs.py /tmp/python_dependency_inputs.py
RUN python3 /tmp/python_dependency_inputs.py --output /python-inputs

FROM base AS python_dependencies
USER root

WORKDIR /ragflow
ARG RAGFLOW_BUILD_JOBS=2

# Install build-only dependencies for compiling Python C extensions.
# These are not inherited from base to keep the production image smaller.
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    apt update && \
    apt install -y build-essential libpython3-dev libicu-dev libgbm-dev && \
    rm -rf /var/lib/apt/lists/*

# install dependencies from uv.lock file
COPY --from=python_inputs /python-inputs/ ./
COPY vendor/python vendor/python

# https://github.com/astral-sh/uv/issues/10462
# uv records index url into uv.lock but doesn't failover among multiple indexes
# Also rewrite pypi.tuna.tsinghua.edu.cn to mirrors.aliyun.com/pypi so locks
# that were resolved against the Tsinghua mirror (e.g. when UV_INDEX pointed
# there) get normalized to the Aliyun mirror in NEED_MIRROR=1 builds. Without
# this, stale Tsinghua URLs slip through and `uv sync --frozen` 404s on
# packages that the Tsinghua mirror no longer carries.
RUN --mount=type=cache,id=ragflow_uv,target=/root/.cache/uv,sharing=locked \
    if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|pypi.org|mirrors.aliyun.com/pypi|g' uv.lock; \
        sed -i 's|pypi.tuna.tsinghua.edu.cn|mirrors.aliyun.com/pypi|g' uv.lock; \
    else \
        sed -i 's|mirrors.aliyun.com/pypi|pypi.org|g' uv.lock; \
        sed -i 's|pypi.tuna.tsinghua.edu.cn|pypi.org|g' uv.lock; \
        sed -i 's|gitee.com|github.com|g' uv.lock; \
    fi; \
    # --refresh-package litellm forces a re-download of litellm from the
    # (post-sed) URLs in uv.lock even if BuildKit's persistent uv cache mount
    # holds a stale wheel from a previous build. litellm 1.88.x has had
    # multiple internal ImportError issues (1.88.1 missing
    # DEFAULT_HEALTH_CHECK_STALENESS_MULTIPLIER, 1.88.0 wheel pulled via
    # some proxies missing RedisPipelineLpopOperation) — always re-fetching
    # the locked version avoids serving a half-broken cached copy.
    UV_CONCURRENT_BUILDS=${RAGFLOW_BUILD_JOBS} uv sync --python 3.13 --frozen --no-install-project \
        --group test --refresh-package litellm --config-settings-package "numpy:compile-args=-j${RAGFLOW_BUILD_JOBS}" && \
    # Ensure pip is available in the venv for runtime package installation (fixes #12651)
    .venv/bin/python3 -m ensurepip --upgrade && \
    uv pip install --python .venv/bin/python3 'pip>=26.2.0,<27'

# The browser download depends on the locked Playwright package bytes, rather
# than every package in the Python environment. The installer uses only stdlib
# and its bundled Node driver. Both Chromium variants remain available offline.
FROM base AS browsers
COPY --from=python_dependencies /ragflow/.venv/lib/python3.13/site-packages/playwright /tmp/playwright
RUN PYTHONPATH=/tmp PLAYWRIGHT_BROWSERS_PATH=/opt/playwright "$(uv python find 3.13)" -m playwright install chromium

FROM base AS runtime-system-files
RUN --mount=type=bind,source=deployment/runner/deduplicate_runtime.py,target=/tmp/deduplicate_runtime.py \
    python3 /tmp/deduplicate_runtime.py \
        /ragflow/rag/res/deepdoc /usr/share/infinity/resource /usr/include/node

FROM python_dependencies AS runtime-python
# The dependency builder owns binutils; no stripping tool enters the runtime.
# Keep loaded segments/dynamic symbols byte-identical, then share equal assets.
RUN --mount=type=bind,source=deployment/runner/strip_runtime_debug.py,target=/tmp/strip_runtime_debug.py \
    python3 /tmp/strip_runtime_debug.py /ragflow/.venv
RUN --mount=type=bind,source=deployment/runner/deduplicate_runtime.py,target=/tmp/deduplicate_runtime.py \
    python3 /tmp/deduplicate_runtime.py /ragflow/.venv

FROM browsers AS runtime-browsers
RUN --mount=type=bind,source=deployment/runner/deduplicate_runtime.py,target=/tmp/deduplicate_runtime.py \
    python3 /tmp/deduplicate_runtime.py /opt/playwright

# Keep all installed files, but omit superseded bytes from the system's build
# history. Only this stable system layer is compacted; application releases still
# share the same system, Python and browser layers. Build stages retain base so
# their existing dependency caches do not change.
FROM scratch AS runtime-system
COPY --link --from=runtime-system-files / /
SHELL ["/bin/bash", "-c"]
USER root
WORKDIR /ragflow
ENV PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    TIKA_SERVER_JAR=file:///ragflow/tika-server-standard-3.3.3.jar \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    UV_HTTP_TIMEOUT=200 UV_HTTP_RETRIES=3
LABEL org.opencontainers.image.version="24.04"
CMD ["/bin/bash"]

# This target contains no application, frontend or release identity. Every
# production image starts here, never from a previous application image.
FROM runtime-system AS runtime-base
ENV VIRTUAL_ENV=/ragflow/.venv
COPY --link --from=runtime-python ${VIRTUAL_ENV} ${VIRTUAL_ENV}
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
COPY --link --from=runtime-browsers /opt/playwright /opt/playwright

# Install frontend dependencies — depends only on package manifests so
# Python, browser and release changes cannot invalidate this stage.
FROM base AS frontend
COPY web/package.json web/package-lock.json web/.npmrc ./web/
RUN --mount=type=cache,id=ragflow_npm,target=/root/.npm,sharing=locked \
    cd web && NODE_OPTIONS="--max-old-space-size=8192" npm ci

# Copy full web source and docs for the frontend build.
COPY web web
COPY docs docs
COPY api/apps/business_documents/html_policy.json api/apps/business_documents/html_policy.json
RUN --mount=type=cache,id=ragflow_npm,target=/root/.npm,sharing=locked \
    cd web && NODE_OPTIONS="--max-old-space-size=4096" VITE_CSS_PREPROCESSOR_MAX_WORKERS=0 VITE_BUILD_SOURCEMAP=false VITE_MINIFY=esbuild npm run build

# Build identity is supplied by the caller, not by copying Git history.
# Standalone builds without args remain usable but explicitly unverified.
FROM base AS builder
ARG RAGFLOW_BUILD_VERSION=""
ARG RAGFLOW_SOURCE_REVISION=""
COPY pyproject.toml ./
COPY tools/quality/write_build_identity.py /ragflow/tools/quality/write_build_identity.py
RUN python3 tools/quality/write_build_identity.py \
    --version "$RAGFLOW_BUILD_VERSION" --revision "$RAGFLOW_SOURCE_REVISION"

# production stage
FROM runtime-base AS production
USER root

WORKDIR /ragflow

ENV PYTHONPATH=/ragflow/

COPY admin admin
COPY api api
COPY conf conf
COPY deepdoc deepdoc
COPY rag rag
COPY agent agent
COPY business_documents business_documents
COPY document_exchange document_exchange
COPY pyproject.toml uv.lock ./
COPY mcp mcp
COPY vendor/python vendor/python
COPY common common
COPY memory memory
COPY bin bin
COPY tools/scripts tools/scripts
COPY tools/quality tools/quality
COPY test/__init__.py test/__init__.py
COPY test/evals/business_documents test/evals/business_documents
COPY test/evals/technical_specifications test/evals/technical_specifications
COPY test/unit_test/technical_specifications test/unit_test/technical_specifications
COPY test/fixtures/technical_specifications test/fixtures/technical_specifications
COPY test/unit_test/api/apps/business_documents/helpers.py test/unit_test/api/apps/business_documents/helpers.py

COPY docker/service_conf.yaml.template ./conf/service_conf.yaml.template
COPY docker/entrypoint.sh ./
RUN chmod +x ./entrypoint*.sh

# Copy nginx configuration for frontend serving
COPY docker/nginx/ragflow.conf.golang docker/nginx/ragflow.conf.python docker/nginx/ragflow.conf.hybrid docker/nginx/ragflow.https.conf docker/nginx/nginx.conf docker/nginx/proxy.conf /etc/nginx/
RUN mv /etc/nginx/ragflow.conf.golang /etc/nginx/conf.d/ragflow.conf.golang && \
    mv /etc/nginx/ragflow.conf.python /etc/nginx/conf.d/ragflow.conf.python && \
    mv /etc/nginx/ragflow.conf.hybrid /etc/nginx/conf.d/ragflow.conf.hybrid && \
    rm -f /etc/nginx/sites-enabled/default

# Copy compiled web pages
COPY --from=frontend /ragflow/web/dist /ragflow/web/dist

COPY --from=builder /ragflow/VERSION /ragflow/VERSION
COPY --from=builder /ragflow/SOURCE_REVISION /ragflow/SOURCE_REVISION
ENTRYPOINT ["./entrypoint.sh"]
