import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ResearchPanel from "./ResearchPanel";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ResearchPanel", () => {
  it("shows a blocked run and does not invent a return", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      const payload = url.endsWith("/status")
        ? { precheck_state: "missing", precheck_message: "还没有预检文件", precheck: null, run_count: 1, commands: [".venv/bin/python precheck_local.py"] }
        : url.endsWith("/runs")
          ? { runs: [{ run_id: "block01", status: "blocked", reason: "口径无法证明", not_strategy_evidence: true }], message: null }
          : url.endsWith("/metrics")
            ? { present: false, metrics: null, message: "这个 run 没有完成，不显示收益数字。" }
            : url.endsWith("/orders")
              ? { present: false, rows: [], total: null, message: "没有 orders 文件。" }
              : { run_id: "block01", status: "blocked", reason: "口径无法证明", not_strategy_evidence: true };
      return { ok: true, json: async () => payload };
    }));
    render(<ResearchPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "block01" }));
    expect(await screen.findByText(/不显示收益/)).toBeTruthy();
    expect(screen.queryByText("0.00%")).toBeNull();
    await waitFor(() => expect(screen.getByText(/没有 orders 文件/)).toBeTruthy());
  });
});
