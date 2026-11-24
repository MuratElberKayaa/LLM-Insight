"use client";

import { useEffect } from "react";

const CASE_HASH = /^#case-[1-9][0-9]{0,18}$/;

/** Moves keyboard focus to the case card a `#case-{id}` link points to (the card has tabIndex -1). */
function focusCase(hash: string): void {
  if (!CASE_HASH.test(hash)) return;
  const el = document.getElementById(hash.slice(1));
  if (el instanceof HTMLElement && el.dataset.testid === "test-case") el.focus({ preventScroll: false });
}

/**
 * Final stabilization: after a jump from the case index or a previous / next link, focus follows
 * the scroll, so keyboard and screen-reader users continue from the case they chose (a plain
 * fragment link only scrolls). Also on load with a `#case-…` hash and on Back/Forward. Renders
 * nothing and stores nothing.
 */
export function CaseFocus() {
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const a = (e.target as Element | null)?.closest?.("a[href^='#case-']");
      if (a instanceof HTMLAnchorElement) {
        // Same-hash clicks fire no hashchange; focus after the browser has scrolled.
        const hash = a.getAttribute("href") ?? "";
        requestAnimationFrame(() => focusCase(hash));
      }
    };
    const onHash = () => focusCase(window.location.hash);
    document.addEventListener("click", onClick);
    window.addEventListener("hashchange", onHash);
    onHash();
    return () => {
      document.removeEventListener("click", onClick);
      window.removeEventListener("hashchange", onHash);
    };
  }, []);
  return null;
}
