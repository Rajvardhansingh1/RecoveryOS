import { createRealtime, rawSql } from "@neon/realtime/server";

export type LiveRun = { id: string; status: string; repo: string; updated_at: string };

export const realtime = createRealtime({
  secret: process.env.NEON_REALTIME_SECRET!,
  db: "neondb",
});

export const recentRunsQuery = rawSql<LiveRun>(
  "SELECT id, status, repo, updated_at FROM public.runs ORDER BY updated_at DESC LIMIT 20",
);
