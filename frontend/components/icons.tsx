import type { ReactNode } from "react";

/**
 * Inline stroke icons (16px grid, currentColor). Decorative only: every icon sits next to a
 * visible text label, so each is aria-hidden and never carries meaning on its own. Inline SVG
 * keeps them dependency-free and CSP-safe (no icon font, no external sprite).
 */
function Icon({ children, size = 16 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
    >
      {children}
    </svg>
  );
}

export const OverviewIcon = () => (
  <Icon>
    <rect x="2" y="2" width="5" height="5" rx="1.2" />
    <rect x="9" y="2" width="5" height="5" rx="1.2" />
    <rect x="2" y="9" width="5" height="5" rx="1.2" />
    <rect x="9" y="9" width="5" height="5" rx="1.2" />
  </Icon>
);

export const TracesIcon = () => (
  <Icon>
    <path d="M2 4h7M4 8h8M6 12h6" />
  </Icon>
);

/** UI-R1: two conversation bubbles (pseudonymous sessions). */
export const SessionsIcon = () => (
  <Icon>
    <path d="M2.5 3.5h7a1 1 0 011 1v3.5a1 1 0 01-1 1H6L3.5 11V9h-1a1 1 0 01-1-1V4.5a1 1 0 011-1z" />
    <path d="M12.5 6.5h1a1 1 0 011 1v3.5a1 1 0 01-1 1h-1V14L10 12H8" />
  </Icon>
);

export const ModelsIcon = () => (
  <Icon>
    <path d="M8 2l6 3-6 3-6-3 6-3z" />
    <path d="M2 8l6 3 6-3M2 11l6 3 6-3" />
  </Icon>
);

export const DatasetsIcon = () => (
  <Icon>
    <ellipse cx="8" cy="4" rx="5" ry="2" />
    <path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" />
  </Icon>
);

export const ExperimentsIcon = () => (
  <Icon>
    <path d="M6 2h4M6.5 2v4.5L3 13a1 1 0 00.9 1.5h8.2A1 1 0 0013 13L9.5 6.5V2" />
    <path d="M4.5 10.5h7" />
  </Icon>
);

export const LockIcon = () => (
  <Icon size={14}>
    <rect x="3" y="7" width="10" height="7" rx="1.5" />
    <path d="M5.5 7V5a2.5 2.5 0 015 0v2" />
  </Icon>
);

/** Brand mark: a rounded square in the accent token with a rising trace line (UI-R1). */
export function BrandMark() {
  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 26 26" width="26" height="26" className="shrink-0">
      <rect x="0.5" y="0.5" width="25" height="25" rx="7" className="fill-accent" />
      <path d="M7 16.5l3.5-4 3 2.5L19 9" fill="none" stroke="#ffffff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
