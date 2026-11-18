/**
 * Who is using the app: a visitor, or a signed-in administrator (ADR-0003).
 *
 * Two ways in, offered according to what the server reports at
 * `/index/config`: OIDC (authorization code + PKCE via oidc-client-ts), or a
 * static admin token pasted by the user. Tokens live in sessionStorage only.
 * Whether a signed-in user is an administrator is the server's call: the app
 * asks once, by reading the admin-only settings.
 */

import { reactive, readonly } from "vue";
import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { ApiError, request, setTokenSource } from "@/api/client";
import type { ServiceInfo } from "@/api";
import { runtimePublicOrigin } from "@/runtimeConfig";

const TOKEN_KEY = "fdpindex.adminToken";

interface SessionState {
  mode: "none" | "oidc" | "token";
  token: string | null;
  name: string | null;
  isAdmin: boolean;
  /** Signed in, but the server does not grant the admin role. */
  notAdmin: boolean;
  oidcAvailable: boolean;
  tokenAvailable: boolean;
  ready: boolean;
}

const state = reactive<SessionState>({
  mode: "none",
  token: null,
  name: null,
  isAdmin: false,
  notAdmin: false,
  oidcAvailable: false,
  tokenAvailable: false,
  ready: false,
});

let manager: UserManager | null = null;

setTokenSource(() => state.token);

export const session = readonly(state);

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function oidcManager(info: ServiceInfo): UserManager | null {
  const issuer = info.oidc?.issuer;
  const clientId = info.oidc?.clientId;
  if (!issuer || !clientId) return null;
  manager ??= new UserManager({
    authority: issuer,
    client_id: clientId,
    redirect_uri: `${runtimePublicOrigin()}/auth/callback`,
    post_logout_redirect_uri: runtimePublicOrigin(),
    response_type: "code",
    scope: "openid profile",
    loadUserInfo: false,
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
  return manager;
}

/** Ask the server whether the current token is an administrator's. */
async function checkAdmin(): Promise<void> {
  try {
    await request("/index/settings");
    state.isAdmin = true;
    state.notAdmin = false;
  } catch (err) {
    state.isAdmin = false;
    if (err instanceof ApiError && err.status === 403) {
      state.notAdmin = true;
    } else {
      clear();
    }
  }
}

function clear(): void {
  state.mode = "none";
  state.token = null;
  state.name = null;
  state.isAdmin = false;
  state.notAdmin = false;
  storage()?.removeItem(TOKEN_KEY);
}

/** Restore a session at start-up, from a stored token or an OIDC user. */
export async function initSession(info: ServiceInfo): Promise<void> {
  state.oidcAvailable = info.auth.includes("oidc") && !!info.oidc?.clientId;
  state.tokenAvailable = info.auth.includes("token");
  const stored = storage()?.getItem(TOKEN_KEY);
  if (stored && state.tokenAvailable) {
    state.mode = "token";
    state.token = stored;
    state.name = "Admin token";
    await checkAdmin();
  } else {
    const oidc = state.oidcAvailable ? oidcManager(info) : null;
    const user = oidc ? await oidc.getUser() : null;
    if (user && !user.expired) {
      state.mode = "oidc";
      state.token = user.access_token;
      state.name = user.profile.preferred_username ?? user.profile.sub;
      await checkAdmin();
      oidc?.events.addUserLoaded((renewed) => {
        state.token = renewed.access_token;
      });
    }
  }
  state.ready = true;
}

export async function signInWithToken(token: string): Promise<boolean> {
  state.mode = "token";
  state.token = token.trim();
  state.name = "Admin token";
  await checkAdmin();
  if (state.isAdmin) {
    storage()?.setItem(TOKEN_KEY, state.token);
    return true;
  }
  clear();
  return false;
}

export async function signInWithOidc(info: ServiceInfo, returnTo: string): Promise<void> {
  const oidc = oidcManager(info);
  if (!oidc) throw new Error("OIDC is not configured for this index.");
  await oidc.signinRedirect({ state: { returnTo } });
}

export async function completeOidc(info: ServiceInfo): Promise<string> {
  const oidc = oidcManager(info);
  if (!oidc) return "/";
  const user = await oidc.signinRedirectCallback();
  state.mode = "oidc";
  state.token = user.access_token;
  state.name = user.profile.preferred_username ?? user.profile.sub;
  await checkAdmin();
  const target = (user.state as { returnTo?: string } | undefined)?.returnTo;
  return safeReturnTo(target);
}

/**
 * A path on this site to go back to after signing in, or "/".
 *
 * "//evil.example" and "/\\evil.example" start with a slash but are other sites
 * to a browser; resolving against our origin and comparing catches every form.
 */
export function safeReturnTo(target: unknown): string {
  if (typeof target !== "string" || !target.startsWith("/")) return "/";
  try {
    const url = new URL(target, window.location.origin);
    if (url.origin !== window.location.origin) return "/";
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/";
  }
}

export async function signOut(info: ServiceInfo | undefined): Promise<void> {
  const wasOidc = state.mode === "oidc";
  clear();
  if (wasOidc && info) {
    const oidc = oidcManager(info);
    await oidc?.removeUser();
  }
}

/** Test seam: reset all session state. */
export function __resetSession(): void {
  clear();
  state.ready = false;
  state.oidcAvailable = false;
  state.tokenAvailable = false;
  manager = null;
}
