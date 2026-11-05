// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { api } from "./client";

describe("API client", () => {
  it("builds typed requests from the schema", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({ items: [], total: 0, limit: 10, offset: 0 }),
    );
    const { data } = await api.GET("/api/repositories/{repo_id}/packages", {
      baseUrl: "http://studio.test",
      fetch: fetchMock,
      params: { path: { repo_id: "main" }, query: { q: "editor", limit: 10 } },
    });
    expect(data?.total).toBe(0);
    const request = (fetchMock.mock.calls[0] as unknown as [Request])[0];
    expect(request.url).toBe("http://studio.test/api/repositories/main/packages?q=editor&limit=10");
  });
});
