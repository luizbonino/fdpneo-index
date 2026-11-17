import { describe, expect, it } from "vitest";
import { countryLabel, hostOf, isoToSeconds, relativeTime, toAmount, toIso } from "./format";

describe("durations", () => {
  it.each([
    ["PT168H", 604800],
    ["P7D", 604800],
    ["PT10M", 600],
    ["PT1M30S", 90],
    ["P1DT2H", 93600],
  ])("reads %s", (iso, seconds) => {
    expect(isoToSeconds(iso)).toBe(seconds);
  });

  it.each(["", "P", "PT", "7 days", "P1W"])("rejects %j", (iso) => {
    expect(isoToSeconds(iso)).toBeNull();
  });

  it("picks the largest whole unit", () => {
    expect(toAmount("PT168H")).toEqual({ amount: 7, unit: "days" });
    expect(toAmount("PT6H")).toEqual({ amount: 6, unit: "hours" });
    expect(toAmount("PT10M")).toEqual({ amount: 10, unit: "minutes" });
    expect(toAmount("PT90S")).toEqual({ amount: 90, unit: "seconds" });
  });

  it("writes durations as Java does", () => {
    expect(toIso(7, "days")).toBe("PT168H");
    expect(toIso(90, "minutes")).toBe("PT1H30M");
    expect(toIso(45, "seconds")).toBe("PT45S");
    expect(toIso(0, "hours")).toBe("PT0S");
  });
});

describe("display helpers", () => {
  it("formats relative times", () => {
    const now = Date.parse("2026-11-17T12:00:00Z");
    expect(relativeTime("2026-11-17T10:00:00Z", now)).toBe("2 hours ago");
    expect(relativeTime("2026-11-10T12:00:00Z", now)).toBe("last week");
    expect(relativeTime(null, now)).toBe("—");
  });

  it("names countries from lexvo IRIs and codes", () => {
    expect(countryLabel("http://lexvo.org/id/iso3166/NL")).toBe("Netherlands");
    expect(countryLabel("de")).toBe("Germany");
    expect(countryLabel("https://www.wikidata.org/wiki/Q55")).toBe("Q55");
  });

  it("shortens URLs to their host", () => {
    expect(hostOf("https://fdp.example.org/path")).toBe("fdp.example.org");
    expect(hostOf("not a url")).toBe("not a url");
  });
});
