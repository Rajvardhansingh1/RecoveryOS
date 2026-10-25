import { realtime, recentRunsQuery } from "@/lib/neon/realtime";

export const dynamic = "force-dynamic";

// Returns a sealed live query for the latest runs. Clients subscribe with
// NEON_REALTIME_URL; the SQL stays opaque to them.
export async function POST() {
  return Response.json(await realtime.seal({ query: recentRunsQuery }));
}
