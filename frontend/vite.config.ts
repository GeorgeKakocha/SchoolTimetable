/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Phase 3B.2 (docs/DECISIONS.md #32, Owner Decision 9): local development
// proxies the backend's existing route prefix through Vite -- never
// FastAPI CORS middleware. The frontend API client therefore only ever
// issues relative requests (e.g. "/schools/..."); this proxy target is
// the one and only place the backend's local address is written down.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/schools": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
