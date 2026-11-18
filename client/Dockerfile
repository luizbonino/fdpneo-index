# syntax=docker/dockerfile:1
# FDPneo Index client: the static app behind nginx, which also proxies the API
# (including FDP pings on POST /) to the index server.

FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.30-alpine AS runtime
RUN apk add --no-cache jq
ENV FDPINDEX_API_UPSTREAM=http://server:8000
COPY deploy/templates/default.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/docker-entrypoint.d/15-csp.envsh /docker-entrypoint.d/15-csp.envsh
COPY deploy/docker-entrypoint.d/40-config.sh /docker-entrypoint.d/40-config.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=6 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
