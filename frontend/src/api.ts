import type { BoardsPayload } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8765";

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
