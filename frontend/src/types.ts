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
};
