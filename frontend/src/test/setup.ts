import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// This project never enables Vitest's `globals` option (explicit imports
// only, matching the codebase's existing no-implicit-magic style), so
// React Testing Library's own auto-cleanup detection (which looks for a
// global `afterEach`) never fires on its own -- register it explicitly
// here instead, once, for every test file.
afterEach(() => {
  cleanup();
});
