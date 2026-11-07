import { useRef, useState } from "react";

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to execCommand */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** Prefer pure 6-digit A-share code when present. */
export function normalizeStockCode(raw?: string | null): string {
  if (raw == null || raw === "") return "";
  const text = String(raw).trim();
  const m = text.match(/(\d{6})/);
  return m ? m[1] : text;
}

export default function CopyCodeButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>();
  const value = normalizeStockCode(code);
  if (!value) return null;

  return (
    <button
      type="button"
      className={copied ? "code-copy on" : "code-copy"}
      title={copied ? "已复制" : "复制股票编码"}
      aria-label={`复制股票编码 ${value}`}
      onClick={async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const ok = await copyText(value);
        if (!ok) return;
        setCopied(true);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setCopied(false), 1200);
      }}
    >
      {copied ? "已复制" : "复制"}
    </button>
  );
}
