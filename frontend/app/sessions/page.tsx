import type { Metadata } from "next";
import { connection } from "next/server";
import { RangeLinks } from "../../components/filter-bar";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { ProjectSelector } from "../../components/project-selector";
import { SessionTable } from "../../components/sessions/session-table";
import { EmptyState, ErrorState, Notice } from "../../components/states";
import { renderOutcome } from "../../lib/auto-refresh";
import { formatUtcMinute, formatUtcTime } from "../../lib/format";
import { modelStateOf, parseModelQuery, withModelFilter } from "../../lib/model-query";
import { hiddenFields, projectLinkValue } from "../../lib/project";
import { loadSessions } from "../../lib/server/sessions";
import { writeWindow } from "../../lib/time-range";

export const metadata: Metadata = { title: "Sessions" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

function sessionsHref(project: string | null, window: Parameters<typeof writeWindow>[0]): string {
  const sp = new URLSearchParams();
  if (project !== null) sp.set("project", project);
  writeWindow(window, sp);
  const qs = sp.toString();
  return qs ? `/sessions?${qs}` : "/sessions";
}

export default async function SessionsPage({ searchParams }: PageProps) {
  await connection();
  const query = parseModelQuery(await searchParams);
  const state = modelStateOf(query);
  const ctx = await loadSessions(query);
  const { sessions, fetchedAt } = ctx;
  const data = sessions.ok ? sessions.data : null;
  const project = projectLinkValue(query.project);
  const outcome = renderOutcome(fetchedAt, ctx.settledAt, [sessions, ctx.projects]);
  const search = sessionsHref(project, state.window).replace(/^\/sessions\??/, "");

  return (
    <NavigationTransition outcome={outcome}>
      <PageHeader
        title="Sessions"
        description="Traces, inference attempts, tokens and known cost per conversation session. Sessions are keyed pseudonyms computed at ingest; the raw conversation id is never stored (ADR-56)."
        meta={
          <>
            {data ? (
              <span data-testid="window" data-from={data.window.from} data-to={data.window.to}>
                Window {formatUtcMinute(data.window.from)} → {formatUtcMinute(data.window.to)} (trace start time)
              </span>
            ) : null}
            <span data-testid="fetched-at" data-fetched-at={fetchedAt.toISOString()}>
              Fetched {formatUtcTime(fetchedAt)} · not live; refresh to update
            </span>
          </>
        }
        actions={<RefreshButton />}
      />
      <ProjectSelector context={ctx} action="/sessions" hidden={hiddenFields(search, ["project"])} />
      <div className="mb-5 flex flex-wrap items-center gap-x-6 gap-y-3">
        <RangeLinks id="s-range-label" window={state.window} hrefFor={(window) => sessionsHref(project, withModelFilter(state, { window }).window)} />
      </div>
      <PendingRegion>
        {!sessions.ok ? (
          <ErrorState error={sessions.error} resource="the session list" />
        ) : data !== null && data.items.length === 0 ? (
          <EmptyState title="No sessions in this window">
            <p>
              No trace of project {ctx.projectId} between {formatUtcMinute(data.window.from)} and {formatUtcMinute(data.window.to)} carries a session. Sessions come from the
              OpenTelemetry attribute <code className="font-mono text-xs">gen_ai.conversation.id</code>, which the backend replaces by a keyed pseudonym at ingest.
            </p>
          </EmptyState>
        ) : data !== null ? (
          <>
            <div className="mb-4">
              <Notice tone="info" title="Pseudonymous, not anonymous" testId="sessions-notice">
                A session value is a keyed hash, scoped to this project. It cannot be reversed without the server key, but anyone holding that key can confirm a guessed id,
                so treat it as personal data where the id identifies a person. Unknown cost is never counted as $0.
              </Notice>
            </div>
            <SessionTable data={data} project={project} />
          </>
        ) : null}
      </PendingRegion>
    </NavigationTransition>
  );
}
