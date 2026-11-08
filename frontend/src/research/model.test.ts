import { describe, expect, it } from "vitest";
import { evidenceText, showReturn } from "./model";

describe("research result labels", () => {
  it("does not turn a blocked run into a return", () => {
    const run = { run_id: "a", status: "blocked", reason: "口径无法证明" };
    expect(showReturn(run, true)).toBe(false);
    expect(evidenceText(run)).toContain("口径无法证明");
  });

  it("marks diagnostic output as not a strategy result", () => {
    const run = { run_id: "b", status: "completed", not_strategy_evidence: true, mode: "diagnostic" };
    expect(evidenceText(run)).toContain("不是真实策略验证");
    expect(showReturn(run, false)).toBe(false);
    expect(showReturn(run, true)).toBe(true);
  });
});
