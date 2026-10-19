// Copies the newest saved benchmark results from the backend into data/benchmark.json so the
// page shows real measured output: the newest run that used a live LLM and the newest run that
// used the deterministic fallback only. If none exist the page shows an honest empty state.
import { readdirSync, readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dir = join(here, "..", "..", "backend", "benchmark", "v1", "results");
const out = join(here, "..", "data", "benchmark.json");
mkdirSync(dirname(out), { recursive: true });

function newest(files, suffix) {
  return files.filter((f) => f.endsWith(suffix)).sort().at(-1);
}

if (!existsSync(dir)) {
  // A deploy that builds only this folder has no backend next to it: keep the committed copy.
  if (existsSync(out)) {
    console.log("sync-benchmark: no backend results here, keeping the committed data/benchmark.json");
  } else {
    writeFileSync(out, "null\n");
    console.log("sync-benchmark: no results directory, wrote null");
  }
} else {
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
  const picked = [newest(files, "-llm.json"), newest(files, "-nollm.json")].filter(Boolean);
  if (picked.length === 0) {
    writeFileSync(out, "null\n");
    console.log("sync-benchmark: no result files, wrote null");
  } else {
    const runs = picked.map((file) => {
      const full = JSON.parse(readFileSync(join(dir, file), "utf8"));
      // Keep the config and the summary; drop the per-run rows to keep the bundle small.
      return { file, config: full.config, summary: full.summary };
    });
    writeFileSync(out, JSON.stringify({ runs }, null, 2) + "\n");
    console.log(`sync-benchmark: copied ${picked.join(", ")}`);
  }
}
