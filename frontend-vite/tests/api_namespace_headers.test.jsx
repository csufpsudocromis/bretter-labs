import { afterEach, describe, expect, it } from "vitest";

import { api } from "../src/api";

const readNamespaceHeader = (headers) => {
  if (!headers) return "";
  if (typeof headers.get === "function") {
    return String(headers.get("X-Bretter-Namespace") || headers.get("x-bretter-namespace") || "").trim();
  }
  return String(headers["X-Bretter-Namespace"] || headers["x-bretter-namespace"] || "").trim();
};

const runWithCapture = async (requestConfig) => {
  let capturedConfig = null;
  await api.request({
    method: "GET",
    url: "/health",
    ...requestConfig,
    adapter: async (config) => {
      capturedConfig = config;
      return {
        data: { ok: true },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
      };
    },
  });
  return capturedConfig;
};

describe("API namespace request interception", () => {
  afterEach(() => {
    window.history.pushState({}, "", "/");
    if (api?.defaults?.headers?.common) {
      delete api.defaults.headers.common["X-Bretter-Namespace"];
      delete api.defaults.headers.common["x-bretter-namespace"];
    }
  });

  it("preserves explicit namespace header for root/all-mode requests", async () => {
    window.history.pushState({}, "", "/");
    const captured = await runWithCapture({
      url: "/user/templates",
      headers: { "X-Bretter-Namespace": "cbe" },
    });
    expect(readNamespaceHeader(captured?.headers)).toBe("cbe");
  });

  it("keeps unscoped requests namespace-neutral when no explicit header is set", async () => {
    window.history.pushState({}, "", "/");
    const captured = await runWithCapture({ url: "/auth/me", headers: {} });
    expect(readNamespaceHeader(captured?.headers)).toBe("");
  });

  it("uses route namespace for /ns/<namespace> requests", async () => {
    window.history.pushState({}, "", "/ns/test-namespace");
    const captured = await runWithCapture({
      url: "/user/templates",
      headers: { "X-Bretter-Namespace": "cbe" },
    });
    expect(readNamespaceHeader(captured?.headers)).toBe("test-namespace");
  });
});
