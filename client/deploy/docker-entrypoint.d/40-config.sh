#!/bin/sh
# Write the runtime configuration the app reads at start-up.
set -eu
target=/usr/share/nginx/html/config.js
api_url=${FDPINDEX_PUBLIC_API_URL:-}
origin=${FDPINDEX_PUBLIC_ORIGIN:-}
printf 'window.__FDPINDEX_CONFIG__ = %s;\n' \
  "$(jq -cn --arg apiUrl "$api_url" --arg publicOrigin "$origin" '{apiUrl: $apiUrl, publicOrigin: $publicOrigin}')" \
  > "$target"
