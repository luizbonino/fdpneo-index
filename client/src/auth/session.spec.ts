import { describe, expect, it } from "vitest";
import { safeReturnTo } from "./session";

describe("safeReturnTo", () => {
  it("keeps paths on this site and refuses everything else", () => {
    expect(safeReturnTo("/admin/settings?x=1#y")).toBe("/admin/settings?x=1#y");
    for (const evil of [
      "//evil.example",
      "/\\evil.example",
      "https://evil.example/",
      "evil",
      42,
      null,
    ]) {
      expect(safeReturnTo(evil)).toBe("/");
    }
  });
});
