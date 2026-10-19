import { defineConfig } from "@playwright/test";

const API = "http://localhost:8765";
const WEB = "http://localhost:3100";

// Runs the real backend (in-memory SQLite, test-only scripted LLM, see backend/tests/e2e_server.py)
// and a production build of the site that points at it. Uses the installed Google Chrome.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: WEB,
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        "D:\\Anaconda3\\envs\\RecoveryOS\\python.exe -m uvicorn tests.e2e_server:app --port 8765 --log-level warning",
      cwd: "../backend",
      url: `${API}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { PYTHONPATH: ".", E2E_LLM: "scripted" },
    },
    {
      command: "npm run build && npm run start -- -p 3100",
      url: WEB,
      reuseExistingServer: false,
      timeout: 240_000,
      env: { NEXT_PUBLIC_API_URL: API },
    },
  ],
});
