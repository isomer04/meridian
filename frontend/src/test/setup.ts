import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest globals are not enabled, so Testing Library's automatic DOM cleanup
// between tests never registers. Without this, every render() in a multi-`it`
// file stacks onto the previous test's DOM instead of replacing it.
afterEach(() => cleanup());
