"""评级信号到每日净值的成交循环。

T 日收盘产生信号，下一交易日开盘成交。买入当日的持仓不能在同一交易日卖出。
每日净值用收盘价计算未平仓市值。断点必须带现金、持仓和待执行订单。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from price_basis import inspect_connection
from scoring_engine import V318ScoringEngine, score_feature_row
from trading_rules_provider import (
    RuleError,
    commission,
    execution_assumptions_ok,
    limit_pct,
    min_buy_lot,
    stamp_tax,
)


class DataError(RuntimeError):
    pass


@dataclass
class Position:
    code: str
    quantity: int
    avg_cost: float
    open_date: str
    available_date: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Position":
        return cls(**raw)


@dataclass
class Pending:
    signal_date: str
    exec_date: str
    code: str
    side: str
    reason: str
    score: float
    grade: str
    entry_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Pending":
        return cls(**raw)


@dataclass
class LoopResult:
    status: str
    run_id: str
    output_dir: Path
    orders: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)
    equity: List[Dict[str, Any]] = field(default_factory=list)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    data_quality: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    manifest: Dict[str, Any] = field(default_factory=dict)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in rows}


def _next_date(dates: List[str], current: str) -> Optional[str]:
    try:
        idx = dates.index(current)
    except ValueError:
        return None
    if idx + 1 >= len(dates):
        return None
    return dates[idx + 1]


def _held_sessions(dates: List[str], open_date: str, current: str) -> int:
    return sum(1 for item in dates if open_date <= item <= current)


def inspect_database(path: Path) -> Dict[str, Any]:
    conn = _connect(path)
    try:
        report = inspect_connection(conn)
        report["tables_present"] = sorted(_tables(conn))
        return report
    finally:
        conn.close()


def _load_frame(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def _prepare(conn: sqlite3.Connection, config: Dict[str, Any]) -> Dict[str, Any]:
    tables = _tables(conn)
    quality: List[Dict[str, Any]] = []
    basis = inspect_connection(conn)
    if not basis["execution_table"]:
        raise DataError(basis["execution_block"] or "没有证明为原始名义价的行情表")
    bar_table = basis["execution_table"]
    for required in ("index_bars", "security_status_asof"):
        if required not in tables:
            raise DataError(f"缺少 {required}")
    if config.get("volume_unit") != "share":
        raise DataError("volume_unit 必须显式为 share；不把手数静默乘成股数")

    dates = [row[0] for row in conn.execute(
        "SELECT trade_date FROM index_bars WHERE symbol IN ('000001', 'sh000001') ORDER BY trade_date"
    )]
    start, end = config["start_date"], config["end_date"]
    dates = [item for item in dates if start <= item <= end]
    if len(dates) < 2:
        raise DataError("交易日历不足两个交易日，无法做 T+1")
    if len(dates) != len(set(dates)):
        raise DataError("交易日历有重复日期")

    codes = list(config.get("codes") or [])
    if not codes:
        codes = [row[0] for row in conn.execute("SELECT DISTINCT symbol FROM security_status_asof ORDER BY symbol")]
    bars = _load_frame(
        conn,
        f'SELECT symbol, trade_date, open, high, low, close, volume, amount FROM "{bar_table}" ORDER BY symbol, trade_date',
    )
    if bars.duplicated(["symbol", "trade_date"]).any():
        raise DataError("原始行情存在重复日期，拒绝静默去重")
    if "adj_factor" in [row[1] for row in conn.execute(f'PRAGMA table_info("{bar_table}")')]:
        factors = _load_frame(conn, f'SELECT symbol, trade_date, adj_factor FROM "{bar_table}"')
        for symbol, part in factors.groupby("symbol"):
            if part["adj_factor"].nunique(dropna=False) > 1:
                raise DataError(f"{symbol} 复权因子在窗口内变化，公司行为口径未核验，拒绝成交")
    else:
        quality.append({
            "code": "no_corporate_action_table",
            "detail": "原始行情没有公司行为或复权因子，不能证明价格没有跨除权缺口",
        })
        if not (config.get("assumptions") or {}).get("accept_no_corporate_action_table"):
            raise DataError("没有公司行为表。诊断模式必须显式接受 accept_no_corporate_action_table，正式模式拒绝")

    status = _load_frame(conn, "SELECT * FROM security_status_asof")
    if status.duplicated(["symbol", "trade_date"]).any():
        raise DataError("证券状态表有重叠记录")

    by_code: Dict[str, pd.DataFrame] = {}
    for symbol in codes:
        part = bars[bars["symbol"] == symbol].copy()
        window = [item for item in dates]
        have = set(part["trade_date"])
        missing = [item for item in window if item not in have]
        if missing:
            raise DataError(f"{symbol} 在交易日 {missing[:3]} 缺少原始行情，不补齐")
        by_code[symbol] = part.set_index("trade_date")

    features: Dict[tuple, Dict[str, Any]] = {}
    if config.get("score_mode") == "v318_feature_row":
        if "v318_feature_inputs" not in tables:
            raise DataError("score_mode=v318_feature_row 但没有 v318_feature_inputs")
        frame = _load_frame(conn, "SELECT * FROM v318_feature_inputs")
        for raw in frame.to_dict("records"):
            features[(raw["symbol"], raw["trade_date"])] = raw
    return {
        "dates": dates,
        "codes": codes,
        "bars": by_code,
        "status": status,
        "features": features,
        "quality": quality,
        "basis": basis,
    }


def _status_on(frame: pd.DataFrame, code: str, trade_date: str) -> Dict[str, Any]:
    part = frame[(frame["symbol"] == code) & (frame["trade_date"] == trade_date)]
    if part.empty:
        raise DataError(f"{code} 在 {trade_date} 没有历史证券状态，不用当前代码前缀或当前 ST 名单补齐")
    row = part.iloc[0].to_dict()
    if row.get("board") in (None, "", "UNKNOWN"):
        raise RuleError(f"{code} 板块未知，拒绝")
    if row.get("st_status") in (None, "", "UNKNOWN"):
        raise RuleError(f"{code} 历史 ST/退市状态未知，拒绝")
    if row.get("days_listed") in (None, ""):
        raise RuleError(f"{code} 缺少上市天数，拒绝")
    return row


def _bar(bundle: Dict[str, Any], code: str, trade_date: str) -> Optional[pd.Series]:
    frame = bundle["bars"].get(code)
    if frame is None or trade_date not in frame.index:
        return None
    return frame.loc[trade_date]


def _score_one(config: Dict[str, Any], bundle: Dict[str, Any], provider: Optional[V318ScoringEngine], code: str, trade_date: str) -> Dict[str, Any]:
    mode = config.get("score_mode", "v318_asof")
    if mode == "v318_feature_row":
        row = bundle["features"].get((code, trade_date))
        if row is None:
            return {"ok": False, "grade_v318": None, "error": "feature_row_missing", "scoring_mode": mode}
        return score_feature_row(row)
    if provider is None:
        return {"ok": False, "grade_v318": None, "error": "no_scoring_engine", "scoring_mode": mode}
    return provider.score_stock_asof(code, trade_date)


def _limit_price(prev_close: float, pct: float, side: str) -> float:
    sign = 1 if side == "BUY" else -1
    return round(prev_close * (1 + sign * pct / 100.0), 2)


def run_execution(
    config: Dict[str, Any],
    db_path: Path,
    output_dir: Path,
    *,
    run_id: str,
    provider: Optional[V318ScoringEngine] = None,
    resume_state: Optional[Dict[str, Any]] = None,
    stop_after: Optional[str] = None,
) -> LoopResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    mode = config.get("mode")
    assumptions = config.get("assumptions") or {}
    try:
        execution_assumptions_ok(mode, assumptions)
    except RuleError as exc:
        return _blocked(config, db_path, output_dir, run_id, str(exc))

    conn = _connect(db_path)
    try:
        try:
            bundle = _prepare(conn, config)
        except (DataError, RuleError) as exc:
            return _blocked(config, db_path, output_dir, run_id, str(exc))
    finally:
        conn.close()

    dates: List[str] = bundle["dates"]
    cash = float(config["portfolio"]["initial_capital"])
    positions: Dict[str, Position] = {}
    pending: List[Pending] = []
    start_idx = 0
    if resume_state is not None:
        _require_checkpoint(resume_state, config)
        cash = float(resume_state["cash"])
        positions = {code: Position.from_dict(item) for code, item in resume_state["positions"].items()}
        pending = [Pending.from_dict(item) for item in resume_state["pending_orders"]]
        start_idx = dates.index(resume_state["last_completed_date"]) + 1

    orders: List[Dict[str, Any]] = list(resume_state.get("orders", [])) if resume_state else []
    trades: List[Dict[str, Any]] = list(resume_state.get("trades", [])) if resume_state else []
    equity_rows: List[Dict[str, Any]] = list(resume_state.get("equity", [])) if resume_state else []
    position_rows: List[Dict[str, Any]] = list(resume_state.get("daily_positions", [])) if resume_state else []
    signals: List[Dict[str, Any]] = list(resume_state.get("signals", [])) if resume_state else []
    quality = list(bundle["quality"])

    entry_cfg = config["entry"]
    exit_cfg = config["exit"]
    portfolio = config["portfolio"]
    slippage = float(assumptions["slippage_bps"]) / 10000.0

    for offset, trade_date in enumerate(dates[start_idx:], start=start_idx):
        due = [item for item in pending if item.exec_date == trade_date]
        pending = [item for item in pending if item.exec_date != trade_date]
        sells = sorted([item for item in due if item.side == "SELL"], key=lambda item: item.code)
        buys = sorted(
            [item for item in due if item.side == "BUY"],
            key=lambda item: (-item.score, item.code),
        )
        cash = _execute_list(
            sells, "SELL", trade_date, bundle, positions, orders, trades, quality,
            cash, mode, assumptions, dates, portfolio, slippage,
        )
        cash = _execute_list(
            buys, "BUY", trade_date, bundle, positions, orders, trades, quality,
            cash, mode, assumptions, dates, portfolio, slippage,
        )

        market_value = 0.0
        for code, pos in list(positions.items()):
            bar = _bar(bundle, code, trade_date)
            close_px = float(bar["close"]) if bar is not None and float(bar["volume"]) > 0 else None
            if close_px is None:
                quality.append({"date": trade_date, "code": code, "detail": "no_mark_price"})
                close_px = pos.avg_cost
            market_value += pos.quantity * close_px
            position_rows.append({
                "date": trade_date,
                "code": code,
                "quantity": pos.quantity,
                "avg_cost": pos.avg_cost,
                "close": close_px,
                "market_value": pos.quantity * close_px,
                "available_date": pos.available_date,
                "open_date": pos.open_date,
            })
        total = cash + market_value
        prev = equity_rows[-1]["total_equity"] if equity_rows else float(portfolio["initial_capital"])
        equity_rows.append({
            "date": trade_date,
            "cash": cash,
            "market_value": market_value,
            "total_equity": total,
            "positions_count": len(positions),
            "daily_return": total / prev - 1 if prev else 0.0,
        })

        nxt = _next_date(dates, trade_date)
        scores = {}
        for code in bundle["codes"]:
            scored = _score_one(config, bundle, provider, code, trade_date)
            scores[code] = scored
            if not scored.get("ok"):
                quality.append({
                    "date": trade_date,
                    "code": code,
                    "detail": "uncomputable",
                    "error": scored.get("error"),
                })
        new_pending = _signals_for_next(
            trade_date, nxt, dates, scores, positions, entry_cfg, exit_cfg, signals, orders,
        )
        pending.extend(new_pending)

        if stop_after == trade_date:
            state = _checkpoint(config, trade_date, cash, positions, pending, orders, trades, equity_rows, position_rows, signals)
            _write_outputs(output_dir, run_id, orders, trades, equity_rows, position_rows, signals, quality, {}, state, None)
            result = LoopResult(status="checkpoint", run_id=run_id, output_dir=output_dir)
            result.manifest = {"status": "checkpoint", "last_completed_date": trade_date}
            return result

    metrics = _metrics(equity_rows, trades, orders, config)
    manifest = _manifest(config, db_path, run_id, bundle, metrics)
    _write_outputs(output_dir, run_id, orders, trades, equity_rows, position_rows, signals, quality, metrics, None, manifest)
    result = LoopResult(
        status="completed",
        run_id=run_id,
        output_dir=output_dir,
        orders=orders,
        trades=trades,
        equity=equity_rows,
        positions=position_rows,
        signals=signals,
        data_quality=quality,
        metrics=metrics,
        manifest=manifest,
    )
    return result


def _execute_list(items, side, trade_date, bundle, positions, orders, trades, quality, cash, mode, assumptions, dates, portfolio, slippage):
    if side == "SELL":
        for item in items:
            cash = _sell_one(item, trade_date, bundle, positions, orders, trades, cash, mode, assumptions, dates, slippage)
        return cash
    ranked = items
    for item in ranked:
        cash = _buy_one(item, trade_date, bundle, positions, orders, trades, quality, cash, mode, assumptions, dates, portfolio, slippage)
    return cash


def _buy_one(item, trade_date, bundle, positions, orders, trades, quality, cash, mode, assumptions, dates, portfolio, slippage) -> float:
    if item.code in positions:
        _order(orders, item, trade_date, "REJECTED", "already_in_position", 0, None)
        return cash
    if len(positions) >= int(portfolio["max_positions"]):
        _order(orders, item, trade_date, "REJECTED", "max_positions", 0, None)
        return cash
    bar = _bar(bundle, item.code, trade_date)
    if bar is None:
        _order(orders, item, trade_date, "REJECTED", "no_bar", 0, None)
        return cash
    if float(bar["volume"]) <= 0:
        _order(orders, item, trade_date, "REJECTED", "no_liquidity_volume_zero", 0, None)
        quality.append({"date": trade_date, "code": item.code, "detail": "volume_zero_not_confirmed_halt"})
        return cash
    prev_date = dates[dates.index(trade_date) - 1]
    prev = _bar(bundle, item.code, prev_date)
    if prev is None:
        _order(orders, item, trade_date, "REJECTED", "no_prev_close", 0, None)
        return cash
    try:
        state = _status_on(bundle["status"], item.code, trade_date)
        pct = limit_pct(state["board"], state["st_status"], int(state["days_listed"]), mode, assumptions)
        lot = min_buy_lot(state["board"], mode, assumptions)
    except (RuleError, DataError) as exc:
        _order(orders, item, trade_date, "REJECTED", f"rule:{exc}", 0, None)
        return cash
    raw_open = float(bar["open"])
    limit_px = _limit_price(float(prev["close"]), pct, "BUY")
    if raw_open >= limit_px:
        _order(orders, item, trade_date, "REJECTED", "limit_up", 0, raw_open)
        return cash
    fill = raw_open * (1 + slippage)
    budget = min(cash, float(portfolio["initial_capital"]) * float(portfolio["single_position_limit"]))
    quantity = int(budget / fill / lot) * lot
    while quantity >= lot:
        amount = quantity * fill
        fee_try = commission(amount, mode, assumptions)
        if amount + fee_try["commission"] <= cash + 1e-8:
            break
        quantity -= lot
    if quantity < lot:
        _order(orders, item, trade_date, "REJECTED", "below_min_lot_or_cash", 0, fill)
        return cash
    amount = quantity * fill
    fee = commission(amount, mode, assumptions)
    debit = amount + fee["commission"]
    cash -= debit
    positions[item.code] = Position(
        code=item.code,
        quantity=quantity,
        avg_cost=debit / quantity,
        open_date=trade_date,
        available_date=_next_date(dates, trade_date) or "9999-12-31",
    )
    _order(orders, item, trade_date, "FILLED", item.reason, quantity, fill, fee["commission"], 0.0)
    trades.append({
        "date": trade_date,
        "signal_date": item.signal_date,
        "code": item.code,
        "side": "BUY",
        "quantity": quantity,
        "price": fill,
        "amount": amount,
        "commission": fee["commission"],
        "stamp_tax": 0.0,
        "cash_after": cash,
    })
    return cash


def _sell_one(item, trade_date, bundle, positions, orders, trades, cash, mode, assumptions, dates, slippage) -> float:
    pos = positions.get(item.code)
    if pos is None:
        _order(orders, item, trade_date, "REJECTED", "no_position", 0, None)
        return cash
    if trade_date < pos.available_date:
        _order(orders, item, trade_date, "REJECTED", "t_plus_1_locked", pos.quantity, None)
        return cash
    bar = _bar(bundle, item.code, trade_date)
    if bar is None:
        _order(orders, item, trade_date, "REJECTED", "no_bar_cannot_exit", pos.quantity, None)
        return cash
    if float(bar["volume"]) <= 0:
        _order(orders, item, trade_date, "REJECTED", "no_liquidity_cannot_exit", pos.quantity, None)
        return cash
    prev_date = dates[dates.index(trade_date) - 1]
    prev = _bar(bundle, item.code, prev_date)
    if prev is None:
        _order(orders, item, trade_date, "REJECTED", "no_prev_close", pos.quantity, None)
        return cash
    try:
        state = _status_on(bundle["status"], item.code, trade_date)
        pct = limit_pct(state["board"], state["st_status"], int(state["days_listed"]), mode, assumptions)
    except (RuleError, DataError) as exc:
        _order(orders, item, trade_date, "REJECTED", f"rule:{exc}", pos.quantity, None)
        return cash
    raw_open = float(bar["open"])
    limit_px = _limit_price(float(prev["close"]), pct, "SELL")
    if raw_open <= limit_px:
        _order(orders, item, trade_date, "REJECTED", "limit_down", pos.quantity, raw_open)
        return cash
    fill = raw_open * (1 - slippage)
    quantity = pos.quantity
    amount = quantity * fill
    fee = commission(amount, mode, assumptions)
    tax = stamp_tax(trade_date, amount, True, mode, assumptions)
    cash += amount - fee["commission"] - tax["stamp_tax"]
    del positions[item.code]
    _order(orders, item, trade_date, "FILLED", item.reason, quantity, fill, fee["commission"], tax["stamp_tax"])
    trades.append({
        "date": trade_date,
        "signal_date": item.signal_date,
        "code": item.code,
        "side": "SELL",
        "quantity": quantity,
        "price": fill,
        "amount": amount,
        "commission": fee["commission"],
        "stamp_tax": tax["stamp_tax"],
        "cash_after": cash,
    })
    return cash


def _signals_for_next(trade_date, nxt, dates, scores, positions, entry_cfg, exit_cfg, signals, orders) -> List[Pending]:
    created: List[Pending] = []
    for code, pos in positions.items():
        scored = scores.get(code) or {}
        reasons = []
        exit_signals = set(exit_cfg.get("exit_signals") or ["EXIT"])
        if scored.get("ok") and str(scored.get("exit_signal") or "") in exit_signals:
            reasons.append(f"trend_exit:{scored.get('exit_signal')}")
        if exit_cfg.get("exit_on_filter") and scored.get("ok") and scored.get("grade_v318") == "FILTER":
            reasons.append("grade_filter")
        held = _held_sessions(dates, pos.open_date, trade_date)
        max_hold = exit_cfg.get("max_holding_trading_days")
        if max_hold is not None and held >= int(max_hold):
            reasons.append(f"max_holding_trading_days:{held}")
        if not reasons:
            continue
        signal = {
            "date": trade_date,
            "code": code,
            "action": "SELL",
            "reason": "+".join(reasons),
            "grade": scored.get("grade_v318"),
            "entry_status": scored.get("entry_window_v318"),
        }
        signals.append(signal)
        if nxt is None:
            orders.append({
                "signal_date": trade_date, "date": None, "code": code, "side": "SELL",
                "status": "CANCELLED", "reason": "no_next_session", "quantity": pos.quantity, "price": None,
                "commission": 0.0, "stamp_tax": 0.0,
            })
            continue
        created.append(Pending(trade_date, nxt, code, "SELL", signal["reason"], 0.0, signal["grade"] or "", signal["entry_status"] or ""))

    ranked = []
    for code, scored in scores.items():
        if code in positions or not scored.get("ok"):
            continue
        grade = scored.get("grade_v318")
        entry = scored.get("entry_window_v318")
        if grade not in entry_cfg["grades"] or entry not in entry_cfg["entry_statuses"]:
            continue
        ranked.append((float(scored.get("trend_structure_score") or 0.0), code, scored))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    for score, code, scored in ranked:
        signal = {
            "date": trade_date,
            "code": code,
            "action": "BUY",
            "reason": f"entry_{scored.get('grade_v318')}_{scored.get('entry_window_v318')}",
            "grade": scored.get("grade_v318"),
            "entry_status": scored.get("entry_window_v318"),
            "score": score,
        }
        signals.append(signal)
        if nxt is None:
            orders.append({
                "signal_date": trade_date, "date": None, "code": code, "side": "BUY",
                "status": "CANCELLED", "reason": "no_next_session", "quantity": 0, "price": None,
                "commission": 0.0, "stamp_tax": 0.0,
            })
            continue
        created.append(Pending(
            trade_date, nxt, code, "BUY", signal["reason"], score, scored.get("grade_v318") or "", scored.get("entry_window_v318") or "",
        ))
    return created


def _order(orders, item, trade_date, status, reason, quantity, price, commission_fee=0.0, stamp=0.0):
    orders.append({
        "signal_date": item.signal_date,
        "date": trade_date,
        "code": item.code,
        "side": item.side,
        "status": status,
        "reason": reason,
        "quantity": quantity,
        "price": price,
        "commission": commission_fee,
        "stamp_tax": stamp,
    })


def _metrics(equity_rows, trades, orders, config) -> Dict[str, Any]:
    final = equity_rows[-1]["total_equity"] if equity_rows else config["portfolio"]["initial_capital"]
    initial = float(config["portfolio"]["initial_capital"])
    peak = initial
    max_dd = 0.0
    for row in equity_rows:
        peak = max(peak, row["total_equity"])
        if peak:
            max_dd = min(max_dd, row["total_equity"] / peak - 1)
    return {
        "evidence_class": "not_strategy_evidence",
        "mode": config.get("mode"),
        "initial_capital": initial,
        "final_equity": final,
        "total_return": final / initial - 1 if initial else None,
        "max_drawdown": max_dd,
        "n_fills": len(trades),
        "n_orders": len(orders),
        "n_rejected": sum(1 for row in orders if row["status"] == "REJECTED"),
        "open_position_value_included": True,
    }


def _config_hash(config: Dict[str, Any]) -> str:
    body = {key: value for key, value in config.items() if key not in {"output_dir", "run_id"}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _checkpoint(config, trade_date, cash, positions, pending, orders, trades, equity, daily_positions, signals) -> Dict[str, Any]:
    return {
        "config_hash": _config_hash(config),
        "last_completed_date": trade_date,
        "cash": cash,
        "positions": {code: pos.to_dict() for code, pos in positions.items()},
        "pending_orders": [item.to_dict() for item in pending],
        "orders": orders,
        "trades": trades,
        "equity": equity,
        "daily_positions": daily_positions,
        "signals": signals,
    }


def _require_checkpoint(state: Dict[str, Any], config: Dict[str, Any]) -> None:
    required = ("config_hash", "last_completed_date", "cash", "positions", "pending_orders")
    missing = [key for key in required if key not in state]
    if missing:
        raise DataError("断点缺少账户或待执行订单，拒绝恢复: " + ",".join(missing))
    if state["config_hash"] != _config_hash(config):
        raise DataError("断点配置哈希与当前配置不一致，拒绝恢复")


def _basis_label(basis: Dict[str, Any]) -> str:
    chosen = basis.get("execution_table")
    for item in basis.get("tables") or []:
        if item.get("table") == chosen:
            return str(item.get("price_basis") or "unproven")
    return "unproven"


def _manifest(config, db_path, run_id, bundle, metrics) -> Dict[str, Any]:
    files = [
        Path(__file__).resolve(),
        Path(__file__).resolve().parent / "trading_rules_provider.py",
        Path(__file__).resolve().parent / "scoring_engine.py",
    ]
    code_hash = hashlib.sha256()
    for file in files:
        code_hash.update(file.read_bytes())
    return {
        "run_id": run_id,
        "status": "completed",
        "mode": config.get("mode"),
        "result_class": "diagnostic_not_official_fills" if config.get("mode") == "diagnostic" else "formal",
        "evidence_class": "not_strategy_evidence",
        "assumptions": config.get("assumptions"),
        "config_hash": _config_hash(config),
        "code_hash": code_hash.hexdigest(),
        "sqlite_path": str(db_path),
        "price_basis": _basis_label(bundle["basis"]),
        "start_date": config["start_date"],
        "end_date": config["end_date"],
        "score_mode": config.get("score_mode"),
        "n_dates": len(bundle["dates"]),
        "codes": bundle["codes"],
        "metrics": metrics,
        "network": "disabled",
        "resume": "requires_cash_positions_pending_orders",
    }


def _write_outputs(output_dir, run_id, orders, trades, equity, positions, signals, quality, metrics, checkpoint, manifest):
    def dump(name, rows):
        frame = pd.DataFrame(rows)
        frame.to_parquet(output_dir / f"{name}_{run_id}.parquet", index=False)
        return str(output_dir / f"{name}_{run_id}.parquet")

    paths = {
        "signals": dump("signals", signals),
        "orders": dump("orders", orders),
        "trades": dump("trades", trades),
        "daily_positions": dump("daily_positions", positions),
        "equity": dump("equity", equity),
    }
    (output_dir / f"data_quality_{run_id}.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / f"metrics_{run_id}.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    if checkpoint is not None:
        (output_dir / "checkpoint.json").write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    if manifest is not None:
        manifest["artifacts"] = paths
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _blocked(config, db_path, output_dir, run_id, reason: str) -> LoopResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "status": "blocked",
        "reason": reason,
        "mode": config.get("mode"),
        "result_class": "blocked_not_a_return",
        "evidence_class": "not_strategy_evidence",
        "assumptions": config.get("assumptions"),
        "config_hash": _config_hash(config),
        "sqlite_path": str(db_path),
        "network": "disabled",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / f"data_quality_{run_id}.json").write_text(
        json.dumps([{"detail": "blocked", "error": reason}], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    empty = pd.DataFrame({"status": pd.Series(dtype="object")})
    empty.to_parquet(output_dir / f"orders_{run_id}.parquet", index=False)
    empty.to_parquet(output_dir / f"trades_{run_id}.parquet", index=False)
    empty.to_parquet(output_dir / f"equity_{run_id}.parquet", index=False)
    result = LoopResult(status="blocked", run_id=run_id, output_dir=output_dir, manifest=manifest)
    result.data_quality = [{"error": reason}]
    return result
