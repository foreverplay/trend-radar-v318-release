"""Research Lab Configuration — Phase 1 数据质量与完整性

独立于生产配置，不修改 v3/config.py
"""
from pathlib import Path

# 研究实验室根目录
LAB_ROOT = Path(__file__).resolve().parent
DATA_DIR = LAB_ROOT / "data"
RESULTS_DIR = LAB_ROOT / "results"
EXPERIMENTS_DIR = LAB_ROOT / "experiments"

# 数据文件路径
ST_STATUS_FILE = DATA_DIR / "st_status_history.csv"
DELIST_FILE = DATA_DIR / "delist_history.csv"
SUSPEND_FILE = DATA_DIR / "suspend_calendar.csv"
FINANCIAL_CALENDAR_FILE = DATA_DIR / "financial_calendar.csv"
INDUSTRY_HISTORY_FILE = DATA_DIR / "industry_history.csv"
CORPORATE_ACTIONS_FILE = DATA_DIR / "corporate_actions.csv"

# 数据质量阈值
DATA_QUALITY_THRESHOLDS = {
    "max_missing_rate": 0.05,  # 最大缺失率 5%
    "max_price_change_pct": 30.0,  # 最大日涨跌幅 30%（非涨跌停）
    "min_volume": 0,  # 最小成交量
    "max_consecutive_limit": 10,  # 最大连续涨跌停天数
}

# Phase 1 数据补充状态
PHASE1_STATUS = {
    "st_status": False,
    "delist_history": False,
    "suspend_calendar": False,
    "financial_calendar": False,
    "industry_history": False,
    "corporate_actions": False,
    "full_market_kline": False,
}

def ensure_dirs():
    """确保必要目录存在"""
    for d in [DATA_DIR, RESULTS_DIR, EXPERIMENTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

ensure_dirs()
