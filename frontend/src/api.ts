import type {
  BoardsPayload,
  FlowPayload,
  IntradayPayload,
  KlinePayload,
  MetaPayload,
  ReasonPayload,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8765";

const trendCache = new Map<string, IntradayPayload>();
const klineCache = new Map<string, KlinePayload>();
const flowCache = new Map<string, FlowPayload>();
const reasonCache = new Map<string, ReasonPayload>();

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { signal });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail || "数据请求失败");
  }
  return (await res.json()) as T;
}

export async function fetchMeta(): Promise<MetaPayload> {
  return getJson<MetaPayload>("/api/meta");
}

export async function fetchBoardsByDate(date: string): Promise<BoardsPayload> {
  return getJson<BoardsPayload>(`/api/boards/${encodeURIComponent(date)}`);
}

export async function fetchLatestBoards(): Promise<BoardsPayload> {
  try {
    const res = await fetch(`${API_BASE}/api/boards/latest`, {
      signal: AbortSignal.timeout(4000),
    });
    if (res.ok) return (await res.json()) as BoardsPayload;
  } catch {
    /* fall through to bundled sample */
  }
  const local = await fetch("/sample/boards_v318_20261008.json");
  if (!local.ok) throw new Error("无法加载榜单数据");
  return (await local.json()) as BoardsPayload;
}

export async function fetchTrends(code: string, date: string, signal?: AbortSignal) {
  const key = `${code}:${date}`;
  const hit = trendCache.get(key);
  if (hit) return hit;
  const data = await getJson<IntradayPayload>(
    `/api/quotes/${code}/trends?date=${encodeURIComponent(date)}`,
    signal,
  );
  trendCache.set(key, data);
  return data;
}

export async function fetchKlines(
  code: string,
  period: "day" | "month",
  asOf: string,
  signal?: AbortSignal,
) {
  const key = `${code}:${period}:${asOf}`;
  const hit = klineCache.get(key);
  if (hit) return hit;
  const data = await getJson<KlinePayload>(
    `/api/quotes/${code}/klines?period=${period}&as_of=${encodeURIComponent(asOf)}`,
    signal,
  );
  klineCache.set(key, data);
  return data;
}

export async function fetchFlow(code: string, date: string, signal?: AbortSignal) {
  const key = `${code}:${date}`;
  const hit = flowCache.get(key);
  if (hit) return hit;
  const data = await getJson<FlowPayload>(
    `/api/quotes/${code}/flow?date=${encodeURIComponent(date)}`,
    signal,
  );
  flowCache.set(key, data);
  return data;
}

export async function fetchRiseReason(
  code: string,
  name: string,
  date: string,
  signal?: AbortSignal,
) {
  const key = `${code}:${date}`;
  const hit = reasonCache.get(key);
  if (hit) return hit;
  const data = await getJson<ReasonPayload>(
    `/api/quotes/${code}/rise-reason?date=${encodeURIComponent(date)}&name=${encodeURIComponent(name)}`,
    signal,
  );
  reasonCache.set(key, data);
  return data;
}
