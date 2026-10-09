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
};
