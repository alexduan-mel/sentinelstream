import { describe, expect, it } from "vitest";

// Tailwind only styles classes its config defines; a misspelt one is silently ignored. The meaning
// colours live under `semantic` (tailwind.config.ts), so `text-positive` does nothing while
// `text-semantic-positive` colours the text. #50 shipped uncoloured returns that way.
const SOURCES = import.meta.glob("../**/*.tsx", { query: "?raw", import: "default", eager: true });
const UNPREFIXED = /\b(?:text|bg|border)-(?:positive|negative|warning|info|neutral|accent)\b/g;

describe("Tailwind class names", () => {
  it("use the semantic- prefix for meaning colours", () => {
    const offenders = Object.entries(SOURCES).flatMap(([file, source]) =>
      [...String(source).matchAll(UNPREFIXED)].map((match) => `${file}: ${match[0]}`)
    );
    expect(offenders).toEqual([]);
  });
});
