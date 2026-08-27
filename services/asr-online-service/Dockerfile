FROM tinkoffcreditsystems/t-one:0.1.0

USER root

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    else \
      sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' /etc/apt/sources.list; \
    fi; \
    apt-get update -o Acquire::Retries=5 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30; \
    apt-get install -y --no-install-recommends ca-certificates ffmpeg; \
    rm -rf /var/lib/apt/lists/*

RUN /usr/local/bin/python -m pip install --no-cache-dir \
    --target /venv/lib/python3.10/site-packages \
    "pydantic-settings>=2.4" \
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
