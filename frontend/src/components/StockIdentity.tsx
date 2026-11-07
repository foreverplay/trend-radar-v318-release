import type { ButtonHTMLAttributes, ReactNode } from "react";
import CopyCodeButton, { normalizeStockCode } from "./CopyCodeButton";

type TipProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "className" | "children">;

/** Name/code tip trigger + copy control, shared by Growth and Dividend boards. */
export default function StockIdentity({
  label,
  code,
  tipProps,
  tipChildren,
}: {
  label: string;
  code?: string | null;
  tipProps?: TipProps;
  tipChildren?: ReactNode;
}) {
  const pure = normalizeStockCode(code);
  if (!pure) return <>{label}</>;
  return (
    <span className="stock-cell">
      <button type="button" className="code-tip" {...tipProps}>
        {tipChildren ?? label}
      </button>
      <CopyCodeButton code={pure} />
    </span>
  );
}
