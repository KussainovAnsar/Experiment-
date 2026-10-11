import { describe, expect, it } from "vitest";
import { formatDuration, formatPercent, formatTimecode, pluralize } from "./format";

describe("formatTimecode", () => {
  it("formats seconds as mm:ss", () => {
    expect(formatTimecode(0)).toBe("00:00");
    expect(formatTimecode(5)).toBe("00:05");
    expect(formatTimecode(65)).toBe("01:05");
    expect(formatTimecode(599.9)).toBe("09:59");
  });
  it("switches to h:mm:ss after an hour", () => {
    expect(formatTimecode(3600)).toBe("1:00:00");
    expect(formatTimecode(3725)).toBe("1:02:05");
  });
  it("handles invalid input", () => {
    expect(formatTimecode(null)).toBe("00:00");
    expect(formatTimecode(undefined)).toBe("00:00");
    expect(formatTimecode(-3)).toBe("00:00");
    expect(formatTimecode(Number.NaN)).toBe("00:00");
  });
});

describe("formatDuration / formatPercent / pluralize", () => {
  it("formats durations in Russian", () => {
    expect(formatDuration(45)).toBe("45 с");
    expect(formatDuration(12 * 60)).toBe("12 мин");
    expect(formatDuration(3900)).toBe("1 ч 05 мин");
    expect(formatDuration(null)).toBe("—");
  });
  it("formats shares as percent", () => {
    expect(formatPercent(0.42)).toBe("42 %");
    expect(formatPercent(null)).toBe("—");
  });
  it("pluralizes Russian nouns", () => {
    expect(pluralize(1, ["урок", "урока", "уроков"])).toBe("1 урок");
    expect(pluralize(3, ["урок", "урока", "уроков"])).toBe("3 урока");
    expect(pluralize(11, ["урок", "урока", "уроков"])).toBe("11 уроков");
    expect(pluralize(22, ["урок", "урока", "уроков"])).toBe("22 урока");
  });
});
