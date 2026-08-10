import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * WCAG 2.2 contrast checks for every text/background token pair actually used
 * in `globals.css`/`tokens.css`. This is the automated half of the Phase 7
 * "contrast check for all token pairs" gate; it complements, not replaces,
 * manual visual review at real zoom/viewport combinations.
 */

const tokenCss = readFileSync(resolve(process.cwd(), "src/styles/tokens.css"), "utf8");
function token(name: string): string {
  const value = tokenCss.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, "i"))?.[1];
  if (!value) throw new Error(`Missing color token --${name}`);
  return value;
}

const tokens = {
  app: token("app-background"),
  surface: token("surface-primary"),
  surfaceMuted: token("surface-secondary"),
  navigation: token("navigation"),
  primary: token("primary"),
  primaryHover: token("primary-hover"),
  ink: token("text"),
  slate: token("text-subtle"),
  line: token("border"),
  lineStrong: token("border-strong"),
  forest: token("success"),
  forestText: token("success-text"),
  amber: token("warning"),
  amberText: token("warning-text"),
  danger: token("error"),
  white: "#ffffff",
};

function srgbToLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function relativeLuminance(hex: string): number {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  const [rl, gl, bl] = [r, g, b].map(srgbToLinear);
  return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl;
}

function contrastRatio(foreground: string, background: string): number {
  const l1 = relativeLuminance(foreground);
  const l2 = relativeLuminance(background);
  const [lighter, darker] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (lighter + 0.05) / (darker + 0.05);
}

// [foreground, background, minimum ratio, usage]
const textPairs: [string, string, number, string][] = [
  [tokens.ink, tokens.app, 4.5, "body text on page background"],
  [tokens.ink, tokens.surface, 4.5, "body text on panel surface"],
  [tokens.ink, tokens.surfaceMuted, 4.5, "body text on muted surface (code, inactive rows)"],
  [tokens.slate, tokens.app, 4.5, "secondary text on page background"],
  [tokens.slate, tokens.surface, 4.5, "secondary text on panel surface"],
  [tokens.primaryHover, tokens.app, 4.5, "links and compact blue text on page background"],
  [tokens.primary, tokens.surface, 4.5, "active nav / links on panel surface"],
  [tokens.white, tokens.primary, 4.5, "primary button text on blue fill"],
  [tokens.white, tokens.primaryHover, 4.5, "primary button text on blue hover fill"],
  [tokens.white, tokens.navigation, 4.5, "rail text and icons on navy"],
  [tokens.forestText, tokens.surface, 4.5, "approved option text on primary surface"],
  [tokens.amberText, tokens.surface, 4.5, "waiting status text on primary surface"],
  [tokens.amberText, tokens.surfaceMuted, 4.5, "waiting status text on secondary surface"],
  [tokens.danger, tokens.app, 4.5, "denied/error status text on page background"],
  [tokens.danger, tokens.surfaceMuted, 4.5, "denied/error status text on muted surface"],
];

// WCAG 1.4.11 (non-text contrast) requires 3:1 only for visual information that is
// *the* way a UI component or its state is identified. `--line` and `--line-strong`
// are used here purely as dividers/borders alongside text that already carries the
// signal (button labels, `.selected-row`'s background change, section headings) —
// so 1.4.11 does not impose a 3:1 floor on them. They still get a floor above "not
// perceivable at all" so a future regression toward near-invisible dividers is caught.
const nonTextPairs: [string, string, number, string][] = [
  [tokens.line, tokens.app, 1.2, "hairline rule against page background (decorative, not informational)"],
  [tokens.lineStrong, tokens.app, 1.8, "emphasized divider against page background (paired with text, not sole indicator)"],
];

describe("design token contrast (WCAG 2.2 AA)", () => {
  it.each(textPairs)(
    "%s on %s reaches at least %s:1 (%s)",
    (foreground, background, minimum) => {
      expect(contrastRatio(foreground, background)).toBeGreaterThanOrEqual(minimum);
    },
  );

  it.each(nonTextPairs)(
    "%s on %s reaches at least %s:1 (%s)",
    (foreground, background, minimum) => {
      expect(contrastRatio(foreground, background)).toBeGreaterThanOrEqual(minimum);
    },
  );
});
