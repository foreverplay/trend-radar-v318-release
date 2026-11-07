/** 将 API/样例中的英文枚举与指标名映射为中文展示 */

const REGIME: Record<string, string> = {
  BULL: "牛市",
  NORMAL: "常态",
  BEAR: "熊市",
  CHOP: "震荡",
  STRONG: "强势",
  WEAK: "弱势",
  RISK_OFF: "极弱避险",
};

const STAGE: Record<string, string> = {
  SECOND_BREAKOUT: "二次突破",
  MAIN_RISE: "主升",
  MAIN_RISE_ACCEL: "主升加速",
  ACCELERATION: "加速段",
  NEW_WAVE: "新波段",
  OVERHEATED: "过热",
  HEALTHY_PULLBACK: "健康回撤",
  PLATFORM_HOLD: "平台整理",
};

const MATURITY: Record<string, string> = {
  EARLY: "早期",
  BUILDING: "构建中",
  ADVANCED: "中后期",
  MATURE: "成熟",
};

const ENTRY: Record<string, string> = {
  PRIME: "主窗口",
  CONFIRMED: "已确认",
  FOLLOW: "跟随",
  EXTENDED: "延伸过热",
  NO_ENTRY: "无入场",
};

const PATH: Record<string, string> = {
  RAPID: "快速主升",
  RAPID_MAIN_RISE: "快速主升浪",
  b: "常规路径",
  B: "常规路径",
};

const EXIT: Record<string, string> = {
  HOLD: "持有观察",
  WATCH: "重点观察",
  PROTECT: "保护观察",
  SELL: "卖出",
  REDUCE: "减仓",
  REDUCE_RISK: "降风险",
};

const FIELD: Record<string, string> = {
  stage: "阶段",
  trend_grade: "趋势等级",
  maturity: "成熟度",
  entry_status: "入场状态",
  path: "路径",
  trend_gain_pct: "趋势涨幅%",
  tech: "技术分",
  cont: "延续分",
  sector: "板块分",
  protect: "保护价",
  break_price: "破坏价",
  exit_signal: "退出信号",
  stock: "股票",
};

const TRANSLATABLE_KEYS = new Set([
  "stage",
  "maturity",
  "entry_status",
  "path",
  "exit_signal",
  "regime",
  "legacy_regime",
]);

function lookup(map: Record<string, string>, raw: string): string {
  if (raw in map) return map[raw];
  const upper = raw.toUpperCase();
  if (upper in map) return map[upper];
  return raw;
}

export function zhRegime(v?: string | null): string {
  if (!v) return "—";
  return lookup(REGIME, v);
}

export function zhSource(v?: string | null): string {
  if (!v) return "—";
  const s = v.toLowerCase();
  if (s === "api") return "接口";
  if (s === "sample") return "本地样例";
  if (s.includes("sample") && s.includes("api")) return "接口（失败时回退样例）";
  return v;
}

export function zhCell(key: string, value?: string | null): string {
  if (value == null || value === "" || value === "—") return "—";
  if (!TRANSLATABLE_KEYS.has(key)) return value;
  switch (key) {
    case "stage":
      return lookup(STAGE, value);
    case "maturity":
      return lookup(MATURITY, value);
    case "entry_status":
      return lookup(ENTRY, value);
    case "path":
      return lookup(PATH, value);
    case "exit_signal":
      return lookup(EXIT, value);
    case "regime":
    case "legacy_regime":
      return lookup(REGIME, value);
    default:
      return value;
  }
}

export function zhColumnLabel(key: string): string {
  return FIELD[key] || key;
}

export const BANNER_TEXT =
  "观察池；A+ 表示图形质量高，不等于今天必买；「主窗口 / 已确认」才是成熟入场窗口。禁止荐买、预测必涨。趋势等级与入场状态为双轨独立维度。";


const DIVIDEND_FIELD: Record<string, string> = {
  stock: "股票",
  dividend_grade: "红利等级",
  dividend_quality_score: "质量分",
  dividend_sustainability_score: "可持续分",
  dividend_trend_score: "趋势分",
  dividend_opportunity_score: "机会分",
  historical_total_return_score: "总回报分",
  valuation_score: "估值分",
  dividend_risk_score: "风险分",
  dividend_risk: "风险",
  dividend_entry_status: "入场状态",
  dividend_yield_ttm: "股息率TTM",
  industry_group: "行业",
  action_hint: "观察提示",
  quality_rank: "质量排名",
  entry_opportunity_rank: "机会排名",
};

const DIVIDEND_RISK: Record<string, string> = {
  SAFE: "安全",
  WATCH: "留意",
  WARNING: "警告",
  HIGH: "高风险",
};

const DIVIDEND_ENTRY: Record<string, string> = {
  TREND_CONFIRM: "趋势确认",
  ACCUMULATE_WATCH: "逢低观察",
  PULLBACK_WATCH: "回撤观察",
  HOLD_WATCH: "持有观察",
  EXTENDED: "过热延伸",
  TREND_BROKEN: "趋势破坏",
  AVOID: "回避",
  NO_ENTRY: "无入场",
};

export function zhDividendColumnLabel(key: string): string {
  return DIVIDEND_FIELD[key] || key;
}

export function zhDividendCell(key: string, value?: string | null): string {
  if (value == null || value === "" || value === "—") return "—";
  if (key === "dividend_risk") return lookup(DIVIDEND_RISK, value);
  if (key === "dividend_entry_status") return lookup(DIVIDEND_ENTRY, value);
  return value;
}

export const DIVIDEND_BANNER_TEXT =
  "红利三区均须当日趋势观察评级为 A+ 或 A；当前机会的红利等级也须为 A 及以上。不以股息率最高为目标。禁止荐买、预测必涨。";
