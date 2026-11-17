import { afterEach, describe, expect, it, vi } from "vitest";
import type { RouteLocationNormalized } from "vue-router";
import { adminGuard } from "./index";
import { __resetSession, signInWithToken } from "@/auth/session";

function route(admin: boolean): RouteLocationNormalized {
  return { meta: { admin }, fullPath: "/admin/settings" } as unknown as RouteLocationNormalized;
}

function respond(body: string, status: number) {
  const headers = { "content-type": "application/json" };
  return vi.fn(() => Promise.resolve(new Response(body, { status, headers })));
}

afterEach(() => {
  __resetSession();
  vi.unstubAllGlobals();
});

describe("adminGuard", () => {
  it("lets anyone through to public pages", () => {
    expect(adminGuard(route(false))).toBe(true);
  });

  it("sends visitors to sign in, remembering where they were going", () => {
    expect(adminGuard(route(true))).toEqual({
      name: "sign-in",
      query: { returnTo: "/admin/settings" },
    });
  });

  it("lets administrators in", async () => {
    vi.stubGlobal("fetch", respond("{}", 200));
    expect(await signInWithToken("fdpi_good")).toBe(true);
    expect(adminGuard(route(true))).toBe(true);
  });

  it("refuses a token the server rejects", async () => {
    vi.stubGlobal("fetch", respond('{"message":"no"}', 401));
    expect(await signInWithToken("fdpi_bad")).toBe(false);
    expect(adminGuard(route(true))).not.toBe(true);
  });
});
