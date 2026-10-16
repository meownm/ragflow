FROM golang:1.26.9-alpine AS cli
ADD --checksum=sha256:853af65a119a538d7402515ad8d8f907effc5abe805b0dd6631ad4b2ecf26d5b \
    https://codeload.github.com/docker/cli/tar.gz/refs/tags/v29.9.0 /tmp/cli.tar.gz
WORKDIR /src
ENV GOFLAGS=-mod=mod
RUN tar xzf /tmp/cli.tar.gz --strip-components=1 && \
    cp vendor.mod go.mod && cp vendor.sum go.sum && \
    go get github.com/moby/go-archive@v0.3.0 golang.org/x/net@v0.60.0 \
        golang.org/x/crypto@v0.57.0 golang.org/x/sys@v0.48.0 && \
    go test ./cli/flags ./cli/version ./opts && \
    CGO_ENABLED=0 go build -trimpath \
        -ldflags='-s -w -X github.com/docker/cli/cli/version.Version=29.9.0' \
        -o /out/docker ./cmd/docker

FROM alpine:3.24 AS docker-cli
RUN apk upgrade --no-cache && apk add --no-cache ca-certificates
COPY --from=cli /out/docker /usr/local/bin/docker
COPY --from=cli /src/LICENSE /usr/share/licenses/docker-cli/LICENSE
CMD ["docker"]

FROM python:3.11-slim-bookworm AS manager

ARG NEED_MIRROR=1

RUN if [ "$NEED_MIRROR" = 1 ]; then \
        grep -rl 'deb.debian.org' /etc/apt/ | xargs sed -i 's|http[s]*://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g'; \
    fi; \
    apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

COPY --from=cli /out/docker /usr/local/bin/docker
COPY --from=cli /src/LICENSE /usr/share/licenses/docker-cli/LICENSE
RUN ln -s /usr/local/bin/docker /usr/bin/docker

COPY --from=ghcr.io/astral-sh/uv:0.7.5 /uv /uvx /bin/

WORKDIR /app
COPY . .

RUN if [ "$NEED_MIRROR" = 1 ]; then export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"; else export UV_INDEX_URL="https://pypi.org/simple"; fi && \
    uv pip install --system -r requirements.txt "pip>=26.2.0,<27" "setuptools>=83,<85" "wheel>=0.46.2,<1" && \
    uv cache clean

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9385"]
