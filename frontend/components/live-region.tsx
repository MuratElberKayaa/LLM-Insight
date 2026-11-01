"use client";

import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import type { ApiFailure } from "../lib/api/result";
import { failureClass, type RefreshController, type RetainedView } from "../lib/auto-refresh";
import { formatUtcTime } from "../lib/format";
import { useNavigationState } from "./navigation-transition";

const noSubscribe = () => () => {};
const none = (): RetainedView | null => null;

/** Whether `failure` may be answered with the last good content of the same view. */
export function showsStale(failure: ApiFailure | null, retained: RetainedView | null, viewKey: string, fetchedAt: string): retained is RetainedView {
  return failure !== null && failureClass(failure.kind) !== "fatal" && retained !== null && retained.viewKey === viewKey && retained.fetchedAt !== fetchedAt;
}

/**
 * Phase 6C-C: the data region of a refreshable page. A successful render is shown and kept. When
 * a later render of the SAME view (same project, filters and window; `viewKey`) fails with an
 * outage, timeout, 5xx or 429, the failure is shown first and the last good content stays below
 * it, dimmed and labelled stale with its load time: a failed read is never shown as current data
 * and never as an empty result. A fatal failure (rejected token, unknown project, refused query,
 * a response for another project) and any other view never show retained content.
 */
export function LiveRegion({ viewKey, fetchedAt, failure, children }: { viewKey: string; fetchedAt: string; failure: ApiFailure | null; children: ReactNode }) {
  const controller: RefreshController | undefined = useNavigationState()?.controller;
  const retained = useSyncExternalStore(controller?.subscribe ?? noSubscribe, controller?.getRetained ?? none, controller?.getServerRetained ?? none);

  useEffect(() => {
    if (controller !== undefined && failure === null) controller.retain({ viewKey, fetchedAt, content: children });
  }, [controller, failure, viewKey, fetchedAt, children]);

  if (!showsStale(failure, retained, viewKey, fetchedAt)) {
    return (
      <div data-live="current" data-fetched-at={fetchedAt}>
        {children}
      </div>
    );
  }
  return (
    <div data-live="stale" data-fetched-at={retained.fetchedAt}>
      <div role="status" className="mb-3 rounded-lg border border-synthetic/60 bg-synthetic/10 px-4 py-3" data-testid="stale-notice">
        <p className="text-sm font-semibold text-ink">Showing earlier data: the latest refresh failed</p>
        <p className="mt-1 text-sm text-ink-2">
          The values below were loaded at {formatUtcTime(new Date(retained.fetchedAt))} and may be out of date. The refresh at {formatUtcTime(new Date(fetchedAt))} did not return
          data (reason below). They are not a new result.
        </p>
      </div>
      {children}
      <div className="mt-4 opacity-60" data-testid="stale-data" aria-label={`Stale data loaded at ${formatUtcTime(new Date(retained.fetchedAt))}`}>
        {retained.content}
      </div>
    </div>
  );
}
