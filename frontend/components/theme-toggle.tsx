"use client";

import { useState, type ReactNode } from "react";

export type ThemeMode = "system" | "light" | "dark";

/**
 * Header control for the color theme. "System" (the default) leaves the choice to the OS via
 * `prefers-color-scheme`; "Light" and "Dark" set `data-theme` on <html>, which the token
 * blocks in app/globals.css honor. Nothing is stored (the dashboard uses no browser storage
 * or cookies), so the choice lasts until the next full page load; client-side navigation
 * keeps it. Icons are decorative; each button carries its name as text.
 */

const icon = (path: ReactNode) => (
  <svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    {path}
  </svg>
);

const MODES: { mode: ThemeMode; label: string; glyph: ReactNode }[] = [
  {
    mode: "system",
    label: "System",
    glyph: icon(
      <>
        <rect x="2" y="2.75" width="12" height="8.5" rx="1.5" />
        <path d="M6 13.75h4M8 11.25v2.5" />
      </>,
    ),
  },
  {
    mode: "light",
    label: "Light",
    glyph: icon(
      <>
        <circle cx="8" cy="8" r="2.75" />
        <path d="M8 1.5v1.25M8 13.25v1.25M1.5 8h1.25M13.25 8h1.25M3.4 3.4l.9.9M11.7 11.7l.9.9M3.4 12.6l.9-.9M11.7 4.3l.9-.9" />
      </>,
    ),
  },
  {
    mode: "dark",
    label: "Dark",
    glyph: icon(<path d="M13.25 9.6A5.5 5.5 0 0 1 6.4 2.75a5.5 5.5 0 1 0 6.85 6.85Z" />),
  },
];

/** Applies a mode to the document root; "system" removes the override. */
export function applyThemeMode(root: { dataset: DOMStringMap }, mode: ThemeMode): void {
  if (mode === "system") delete root.dataset.theme;
  else root.dataset.theme = mode;
}

export function ThemeToggle({ initial = "system" }: { initial?: ThemeMode }) {
  const [mode, setMode] = useState<ThemeMode>(initial);
  const choose = (next: ThemeMode) => {
    applyThemeMode(document.documentElement, next);
    setMode(next);
  };
  return (
    <div role="group" aria-label="Color theme" className="inline-flex w-fit rounded-lg border border-line bg-canvas p-0.5" data-testid="theme-toggle">
      {MODES.map(({ mode: m, label, glyph }) => (
        <button
          key={m}
          type="button"
          aria-pressed={mode === m}
          title={`${label} theme`}
          onClick={() => choose(m)}
          data-theme-mode={m}
          className="inline-flex h-7 w-7 items-center justify-center rounded text-ink-3 hover:text-ink aria-pressed:bg-raised aria-pressed:text-ink"
        >
          {glyph}
          <span className="sr-only">{label}</span>
        </button>
      ))}
    </div>
  );
}
