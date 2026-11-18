import { describe, expect, it } from "vitest";
import { clearHistory, decodeQuery, encodeQuery, history, remember, togglePin } from "./history";

describe("history and share links", () => {
  it("round-trips a query through a share link", async () => {
    const query = 'SELECT * WHERE { ?s ?p "ünïcode" } # 🙂';
    const encoded = await encodeQuery(query);
    expect(encoded).toMatch(/^[zb]\./);
    expect(encoded).not.toMatch(/[+/=]/);
    expect(await decodeQuery(encoded)).toBe(query);
    expect(await decodeQuery("z.broken")).toBeNull();
  });

  it("keeps recent queries first, deduplicated, and keeps pins when clearing", () => {
    clearHistory();
    remember("ASK { ?s ?p ?o }", 1);
    remember("SELECT * { ?s ?p ?o }", 5);
    remember("ASK { ?s ?p ?o }", 1);
    expect(history.value.map((h) => h.query)).toEqual([
      "ASK { ?s ?p ?o }",
      "SELECT * { ?s ?p ?o }",
    ]);
    togglePin("SELECT * { ?s ?p ?o }");
    clearHistory();
    expect(history.value.map((h) => h.query)).toEqual(["SELECT * { ?s ?p ?o }"]);
  });
});
