export type BoardRow = {
  stock?: string;
  name?: string;
  code?: string;
  stage?: string;
  trend_grade?: string;
  maturity?: string;
  entry_status?: string;
  path?: string;
  trend_gain_pct?: string;
  tech?: string;
  cont?: string;
  sector?: string;
  protect?: string;
  break_price?: string;
  exit_signal?: string;
};

export type BoardsPayload = {
  version?: string;
  t_date?: string;
  title?: string;
  regime?: string;
  legacy_regime?: string;
  risk_off?: boolean;
  market_score?: number;
  disclaimer?: string;
  counts?: Record<string, unknown>;
  boards: {
    mature_entry: BoardRow[];
    trend_all: BoardRow[];
    watch_pool: BoardRow[];
    extended: BoardRow[];
  };
  source?: string;
  asof_mode?: string;
  asof_note?: string;
  refresh?: {
    state?: string;
    day?: string;
    message?: string;
    at?: string;
  };
};

export type IntradayPoint = {
  time: string;
  price: number;
  avg: number;
  volume: number;
  change_pct?: number;
};

export type IntradayPayload = {
  symbol: string;
  preClose: number;
  points: IntradayPoint[];
  tradeDate?: string;
  available: boolean;
  source?: string;
};

export type KlineBar = {
  time: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount?: number;
  change_pct?: number;
};

export type KlinePayload = {
  symbol: string;
  period: "day" | "month";
  asOf?: string;
  bars: KlineBar[];
  available: boolean;
  source?: string;
};

export type FlowPayload = {
  available: boolean;
  date?: string;
  largeSharePct?: number;
  mainNetPct?: number;
  note?: string;
};

export type ReasonPayload = {
  available: boolean;
  reason?: string;
  source?: string;
  note?: string;
};

export type MetaPayload = {
  dates: string[];
  dividend_dates?: string[];
  modules?: { growth?: string; dividend?: string };
  dividend_boards_rules?: Record<string, string>;
  disclaimer?: string;
};

export type DividendBoardRow = {
  symbol?: string;
  name?: string;
  code?: string;
  dividend_grade?: string;
  dividend_opportunity_score?: number | string;
  dividend_quality_score?: number | string;
  dividend_sustainability_score?: number | string;
  dividend_trend_score?: number | string;
  historical_total_return_score?: number | string;
  valuation_score?: number | string;
  dividend_risk_score?: number | string;
  dividend_risk?: string;
  dividend_entry_status?: string;
  dividend_yield_ttm?: number | string;
  dividend_yield_trap?: boolean | string;
  industry_group?: string;
  quality_rank?: number | string;
  entry_opportunity_rank?: number | string;
  action_hint?: string;
  reasons?: string[] | string;
  why_watch?: string[] | string;
  why_not_now?: string[] | string;
  wait_for?: string[] | string;
  board_tags?: string[] | string;
  protection_price?: number | string;
  trend_break_price?: number | string;
  close?: number | string;
  protect?: number | string;
  break_price?: number | string;
};

export type DividendBoardSection = {
  id: "quality" | "opportunity" | "risk_watch" | string;
  title: string;
  rows: DividendBoardRow[];
};

export type DividendBoardsPayload = {
  strategy?: string;
  version?: string;
  phase?: string;
  t_date?: string;
  as_of?: string;
  title?: string;
  disclaimer?: string;
  counts?: Record<string, number>;
  boards: {
    quality: DividendBoardSection;
    opportunity: DividendBoardSection;
    risk_watch: DividendBoardSection;
  };
  grade_counts?: Record<string, number>;
  risk_counts?: Record<string, number>;
  entry_counts?: Record<string, number>;
  n_scored?: number;
  n_trap?: number;
  known_gaps?: unknown[];
  source?: string;
};
