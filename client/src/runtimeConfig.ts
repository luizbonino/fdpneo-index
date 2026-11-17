/**
 * Runtime configuration from `public/config.js`, which the container writes
 * from its environment. Both values default to the page's own origin, which is
 * how the container serves the app and the API together.
 */

export function runtimeApiUrl(): string {
  return (window.__FDPINDEX_CONFIG__?.apiUrl ?? "").replace(/\/+$/, "");
}

export function runtimePublicOrigin(): string {
  return window.__FDPINDEX_CONFIG__?.publicOrigin || window.location.origin;
}
