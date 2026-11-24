import type { Metadata } from "next";
import { connection } from "next/server";
import { ContextBar } from "../../components/context-bar";
import { RangeLinks } from "../../components/filter-bar";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { ProjectSelector } from "../../components/project-selector";
import { SessionTable } from "../../components/sessions/session-table";
import { SessionTimeline } from "../../components/sessions/session-timeline";
import { EmptyState, ErrorState, Notice } from "../../components/states";
import { renderOutcome } from "../../lib/auto-refresh";
import { projectContext, windowContext } from "../../lib/context-links";
import { formatUtcMinute, formatUtcTime } from "../../lib/format";
import { modelStateOf, parseModelQuery, withModelFilter } from "../../lib/model-query";
import { hiddenFields, projectLinkValue } from "../../lib/project";
import { loadSessions } from "../../lib/server/sessions";
import { writeWindow } from "../../lib/time-range";
import { DEFAULT_PAGE_SIZE, SESSION_RE, type ListState } from "../../lib/trace-query";

export const metadata: Metadata = { title: "Sessions" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

function sessionsHref(project: string | null, window: Parameters<typeof writeWindow>[0], session: string | null = null): string {
  const sp = new URLSearchParams();
  if (project !== null) sp.set("project", project);
  writeWindow(window, sp);
  if (session !== null) sp.set("session", session);
  const qs = sp.toString();
  return qs ? `/sessions?${qs}` : "/sessions";
}

export default async function SessionsPage({ searchParams }: PageProps) {
  await connection();
  const sp = await searchParams;
  const query = parseModelQuery(sp);
  const state = modelStateOf(query);
  // UI-R3: the inspected session (`?session=`, the backend's hash format only; anything else is ignored).
  const session = typeof sp.session === "string" && SESSION_RE.test(sp.session) ? sp.session : null;
  const ctx = await loadSessions(query, new Date(), {}, session);
  const { sessions, fetchedAt } = ctx;
  const data = sessions.ok ? sessions.data : null;
  const project = projectLinkValue(query.project);
  const outcome = renderOutcome(fetchedAt, ctx.settledAt, [sessions, ctx.projects, ...(ctx.traces ? [ctx.traces] : [])]);
  const search = sessionsHref(project, state.window).replace(/^\/sessions\??/, "");
  const row = session !== null && data !== null ? (data.items.find((s) => s.session === session) ?? null) : null;
  // The explorer listing for the selected session: the page's window, frozen to the backend echo.
  const list: ListState | null =
    session === null
      ? null
      : {
          project,
          window: data ? { kind: "custom", from: data.window.from, to: data.window.to } : state.window,
          status: "all",
          provenance: "all",
          model: null,
          provider: null,
          session,
          limit: DEFAULT_PAGE_SIZE,
          cursor: null,
          page: 1,
        };
  const context = [
    { key: "project", label: "Project", value: projectContext(ctx.projectId, ctx.projects) },
    { key: "window", label: "Window", value: windowContext(state.window, data?.window ?? null) },
    ...(session !== null ? [{ key: "session", label: "Session", value: `${session.slice(0, 11)}…` }] : []),
  ].map((e) => ({ ...e, title: e.key === "session" ? (session ?? "") : e.value }));

  return (
    <NavigationTransition outcome={outcome}>
      <ContextBar items={context} />
      <PageHeader
        eyebrow="Observe"
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
      <section aria-label="Scope and filters" className="mb-6 flex flex-col gap-4 rounded-xl border border-line bg-surface p-4 *:mb-0 sm:p-5" data-testid="scope-panel">
        <ProjectSelector context={ctx} action="/sessions" hidden={hiddenFields(search, ["project"])} />
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <RangeLinks id="s-range-label" window={state.window} hrefFor={(window) => sessionsHref(project, withModelFilter(state, { window }).window)} />
        </div>
      </section>
      <PendingRegion>
        {!sessions.ok ? (
          <ErrorState error={sessions.error} resource="the session list" />
        ) : data !== null && data.items.length === 0 && session === null ? (
          <EmptyState title="No sessions in this window">
            <p>
              No trace of project {ctx.projectId} between {formatUtcMinute(data.window.from)} and {formatUtcMinute(data.window.to)} carries a session. Sessions come from the
              OpenTelemetry attribute <code className="font-mono text-xs">gen_ai.conversation.id</code>, which the backend replaces by a keyed pseudonym at ingest.
            </p>
            <p className="mt-2 text-ink-3">No session is shown or invented without that attribute; prompts and replies are never stored.</p>
          </EmptyState>
        ) : data !== null ? (
          <>
            <div className="mb-4">
              <Notice tone="info" title="Pseudonymous, not anonymous" testId="sessions-notice">
                A session value is a keyed hash, scoped to this project. It cannot be reversed without the server key, but anyone holding that key can confirm a guessed id,
                so treat it as personal data where the id identifies a person. Unknown cost is never counted as $0.
              </Notice>
            </div>
            {session !== null && list !== null && ctx.traces !== null && (
              <div className="mb-6">
                <SessionTimeline session={session} row={row} traces={ctx.traces} list={list} />
                <p className="mt-2 text-sm">
                  <a href={sessionsHref(project, state.window)} className="text-ink-2 underline underline-offset-2 hover:text-ink" data-testid="session-close">
                    Close the session
                  </a>
                </p>
              </div>
            )}
            <section aria-labelledby="sessions-heading" className="min-w-0 rounded-xl border border-line bg-surface p-5">
              <h2 id="sessions-heading" className="mb-3 text-[15px] font-semibold tracking-tight text-ink">
                Sessions in this window
              </h2>
              <SessionTable data={data} project={project} selected={session} inspectHref={(s) => sessionsHref(project, state.window, s.session)} />
            </section>
          </>
        ) : null}
      </PendingRegion>
    </NavigationTransition>
  );
}
