import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 45000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.LAB_E2E_URL || "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(process.env.LAB_BROWSER_CHANNEL
          ? { channel: process.env.LAB_BROWSER_CHANNEL }
          : {}),
        viewport: { width: 1600, height: 1050 },
      },
    },
  ],
});
