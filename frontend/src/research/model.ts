export type ResearchRun = {
  run_id: string;
  status?: string;
  mode?: string | null;
  result_class?: string | null;
  evidence_class?: string | null;
  reason?: string | null;
  not_strategy_evidence?: boolean;
  config_hash?: string | null;
  code_hash?: string | null;
};

export function evidenceText(run: ResearchRun): string {
  if (run.status === "blocked") return run.reason || "这次运行被阻断，没有可展示的收益。";
  if (run.status === "unreadable") return run.reason || "这个目录读不了。";
  if (run.not_strategy_evidence || run.evidence_class === "not_strategy_evidence" || run.result_class === "diagnostic_not_official_fills") {
    return "诊断或合成产物，不是真实策略验证。";
  }
  if (run.mode === "formal" && run.status === "completed") return "正式运行完成。数字只来自该 run 的文件。";
  return "先看状态和缺项，缺文件不显示成 0。";
}

export function showReturn(run: ResearchRun, metricsPresent: boolean): boolean {
  return run.status === "completed" && metricsPresent;
}

export function cellText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}
