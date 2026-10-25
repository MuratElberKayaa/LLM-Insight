import { connection } from "next/server";
import { loadHealth } from "../lib/server/overview";
import { StatusIndicator, type StatusTone } from "./status-indicator";

const COPY: Record<Awaited<ReturnType<typeof loadHealth>>, { tone: StatusTone; label: string; detail: string }> = {
  ready: { tone: "good", label: "Backend ready", detail: "GET /health/ready returned 200 (database reachable)" },
  not_ready: { tone: "warning", label: "Backend up, database unavailable", detail: "GET /health/ready returned 503" },
  unreachable: { tone: "critical", label: "Backend unreachable", detail: "No response from GET /health/ready" },
  unconfigured: { tone: "neutral", label: "Backend not configured", detail: "Dashboard server configuration is incomplete" },
};

/** Header status: the backend readiness probe at render time (not a live connection). */
export async function BackendStatus() {
  await connection();
  const state = await loadHealth();
  const c = COPY[state];
  return (
    <div data-testid="backend-status" data-health={state}>
      <StatusIndicator tone={c.tone} label={c.label} detail={c.detail} />
    </div>
  );
}
