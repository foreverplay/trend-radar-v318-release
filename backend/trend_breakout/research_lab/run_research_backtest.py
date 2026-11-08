#!/usr/bin/env python3
"""研究回测命令。

从只读 SQLite 读取原始行情，生成信号、订单、成交、持仓和每日净值。
同一 run_id 目录不覆盖。断点必须包含现金、持仓和待执行订单。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_LAB = Path(__file__).resolve().parent
_BACKEND = _LAB.parent
sys.path.insert(0, str(_LAB))
sys.path.insert(0, str(_BACKEND))

from execution_loop import DataError, run_execution
from scoring_engine import V318ScoringEngine


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="研究实验室回测：评级到每日净值")
    parser.add_argument("config", type=Path)
    parser.add_argument("--resume", type=Path, help="从含账户状态的 checkpoint.json 继续")
    parser.add_argument("--stop-after", help="写断点并停在该交易日")
    args = parser.parse_args(argv)

    config = _load(args.config)
    sqlite_env = os.environ.get("RESEARCH_SQLITE_PATH")
    db_path = Path(sqlite_env).expanduser() if sqlite_env else Path(config["sqlite_path"]).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    if not db_path.is_file():
        print("数据库不存在")
        return 2

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    results_env = os.environ.get("RESEARCH_RESULTS_ROOT")
    output_root = Path(results_env).expanduser() if results_env else Path(config.get("output_dir", "results/research_backtest"))
    if not output_root.is_absolute():
        output_root = (Path.cwd() / output_root).resolve()
    resume_state = None
    if args.resume:
        checkpoint = args.resume / "checkpoint.json"
        if not checkpoint.is_file():
            print("断点文件不存在，拒绝恢复")
            return 2
        resume_state = json.loads(checkpoint.read_text(encoding="utf-8"))
        output_dir = args.resume
        run_id = config.get("run_id") or resume_state.get("run_id") or output_dir.name
    else:
        output_dir = output_root / run_id
        if output_dir.exists():
            print(f"输出目录已存在，拒绝覆盖: {output_dir}")
            return 2

    provider = None
    if config.get("score_mode", "v318_asof") == "v318_asof":
        from research_config import ResearchDataConfig
        from data_provider import ReadOnlyDataProvider

        data_config = ResearchDataConfig.from_explicit_paths(sqlite_path=str(db_path), parquet_root=config.get("parquet_root"))
        data_config.validate()
        if not data_config.validation_results.get("valid"):
            print("数据配置不能用于 as-of 评分")
            print(json.dumps(data_config.validation_results, ensure_ascii=False, indent=2))
            return 2
        provider = V318ScoringEngine(ReadOnlyDataProvider(data_config))

    try:
        result = run_execution(
            config,
            db_path,
            output_dir,
            run_id=run_id,
            provider=provider,
            resume_state=resume_state,
            stop_after=args.stop_after,
        )
    except DataError as exc:
        print(f"拒绝恢复或数据不合法: {exc}")
        return 2

    print(json.dumps({"status": result.status, "run_id": result.run_id, "output_dir": str(result.output_dir), "manifest": result.manifest}, ensure_ascii=False, indent=2))
    if result.status == "blocked":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
