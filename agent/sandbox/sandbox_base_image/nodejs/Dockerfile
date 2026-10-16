FROM node:24.22.0-bookworm-slim

ARG NEED_MIRROR=1

RUN if [ "$NEED_MIRROR" = 1 ]; then \
        npm config set registry https://registry.npmmirror.com; \
    else \
        npm config set registry https://registry.npmjs.org; \
    fi

WORKDIR /app

COPY package.json package-lock.json .

RUN apt-get update && apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/* && npm install --global npm@12.2.0 && \
    npm ci --omit=dev && npm cache clean --force

# npm 12.2.0 still bundles affected releases of these CLI dependencies.
RUN npm install --prefix /tmp/npm-security --omit=dev --ignore-scripts \
    --package-lock=false --save-exact undici@6.28.1 brace-expansion@5.0.12 \
    postcss-selector-parser@7.1.6 ip-address@10.7.1 && \
    npm_modules="$(npm root -g)/npm/node_modules" && \
    for dependency in /tmp/npm-security/node_modules/*; do \
        name="$(basename "$dependency")"; \
        rm -rf "${npm_modules:?}/${name}"; cp -a "$dependency" "$npm_modules/"; \
    done && rm -rf /tmp/npm-security && npm cache clean --force

CMD ["sleep", "infinity"]

