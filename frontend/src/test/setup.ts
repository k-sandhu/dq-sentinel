import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount React trees and clear per-test localStorage between tests so DOM and prefs
// state never leak across them (the appearance/prefs chokepoint is localStorage-backed).
afterEach(() => {
  cleanup();
  try {
    localStorage.clear();
  } catch {
    /* jsdom provides localStorage; defensive for odd environments */
  }
});
