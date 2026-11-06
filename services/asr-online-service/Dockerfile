FROM tinkoffcreditsystems/t-one:0.1.0@sha256:7d4622d278ea9465c43ff0abfc70c6682aef14a3d6ecc7a76b71d118167e2334 AS tone_assets
FROM python:3.10-slim-trixie@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d

COPY --from=tone_assets /venv /venv
COPY --from=tone_assets /models /models
ENV PATH=/venv/bin:$PATH LOAD_FROM_FOLDER=/models
RUN useradd -s /bin/bash python
STOPSIGNAL SIGINT

USER root

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    else \
      sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' /etc/apt/sources.list; \
    fi; \
    apt-get update -o Acquire::Retries=5 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30; \
    apt-get upgrade -y --no-install-recommends; \
    apt-get install -y --no-install-recommends ca-certificates ffmpeg libgomp1; \
    rm -rf /var/lib/apt/lists/*

RUN /usr/local/bin/python -m pip install --no-cache-dir --upgrade \
    "pip>=26.2.0,<27" "setuptools>=83.0.0,<85" "wheel>=0.46.2,<1"

RUN /usr/local/bin/python -m pip --python /venv/bin/python install --no-cache-dir \
    "certifi>=2026.10.19" \
    "fastapi==0.135.0" \
    "starlette==1.3.1" \
    "pydantic-settings==2.14.2" \
    "anyio>=4.14.2,<5" \
    "filelock>=3.20.3,<4" \
    "fsspec>=2026.6.0" \
    "idna>=3.15,<4" \
    "protobuf>=6.33.5,<7" \
    "python-dotenv>=1.2.2,<2" \
    "requests>=2.33.0,<3" \
    "urllib3>=2.8.0,<3" \
    "pyyaml>=6.0" \
    "httpx>=0.27,<0.28" \
    "python-multipart>=0.0.9" \
    "python-docx>=1.1"

WORKDIR /app
COPY src ./src
COPY webapp ./webapp
RUN mkdir -p /app/artifacts /app/uploads && chown -R python:python /app

ENV PYTHONPATH=/app/src

USER python

EXPOSE 9011
ENTRYPOINT ["uvicorn", "asr_service.main:app", "--host", "0.0.0.0", "--port", "9011"]
