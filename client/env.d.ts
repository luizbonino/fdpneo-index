/// <reference types="vite/client" />

/** Client version, inlined from package.json at build time. */
declare const __APP_VERSION__: string;

interface Window {
  /** Runtime configuration written by the container at start-up (public/config.js). */
  __FDPINDEX_CONFIG__?: {
    /** Base URL of the index API; empty when the API is served from the same origin. */
    apiUrl?: string;
    /** Public origin of this app (OIDC redirect URIs); defaults to window.location.origin. */
    publicOrigin?: string;
  };
}
