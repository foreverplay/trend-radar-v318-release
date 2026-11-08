#!/usr/bin/env bash
# 本地预检脚本 - 在用户本地运行前检查
# 验证数据路径、依赖、基础评分

set -e

echo "==================================================================="
echo "研究实验室本地预检"
echo "==================================================================="

# 1. 检查Python环境
echo ""
echo "[1/5] 检查Python环境..."
if ! command -v python3 &> /dev/null; then
    echo "❌ python3 未找到"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
echo "✅ Python: $PYTHON_VERSION"

# 2. 检查依赖
echo ""
echo "[2/5] 检查Python依赖..."
python3 -c "import pandas; import numpy; import pyarrow; print('✅ 核心依赖已安装')" || {
    echo "❌ 缺少依赖，请安装: pip install pandas numpy pyarrow"
    exit 1
}

# 3. 检查数据路径
echo ""
echo "[3/5] 检查数据路径..."

SQLITE_PATH="../longtou-web/backend/data/longtou.sqlite3"
PARQUET_ROOT="./data/kline_market/raw/daily"

if [ ! -f "$SQLITE_PATH" ]; then
    echo "❌ SQLite数据库不存在: $SQLITE_PATH"
    echo "   请检查longtou-web项目路径"
    exit 1
fi

SQLITE_SIZE=$(du -h "$SQLITE_PATH" | cut -f1)
echo "✅ SQLite: $SQLITE_PATH ($SQLITE_SIZE)"

if [ ! -d "$PARQUET_ROOT" ]; then
    echo "❌ Parquet目录不存在: $PARQUET_ROOT"
    exit 1
fi

PARQUET_COUNT=$(ls -1 "$PARQUET_ROOT"/*.parquet 2>/dev/null | wc -l)
echo "✅ Parquet: $PARQUET_ROOT ($PARQUET_COUNT 文件)"

# 4. 测试SQLite只读访问
echo ""
echo "[4/5] 测试SQLite只读访问..."
python3 << 'EOF'
import sys
import sqlite3
from pathlib import Path

try:
    db_path = Path("../longtou-web/backend/data/longtou.sqlite3")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    
    # 查询daily_bars_qfq
    cursor = conn.execute("SELECT COUNT(*), COUNT(DISTINCT code) FROM daily_bars_qfq")
    total_rows, total_codes = cursor.fetchone()
    
    # 查询最大日期
    cursor = conn.execute("SELECT MAX(date) FROM daily_bars_qfq")
    max_date = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"✅ daily_bars_qfq: {total_rows:,} 条记录, {total_codes} 只股票")
    print(f"   最新日期: {max_date}")

except Exception as e:
    print(f"❌ SQLite访问失败: {e}")
    sys.exit(1)
EOF

# 5. 测试基础评分（单只股票）
echo ""
echo "[5/5] 测试基础评分（600519 @ 2024-12-31）..."
python3 << 'EOF'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from research_config import create_default_config
    from data_provider import ReadOnlyDataProvider
    from scoring_engine import V318ScoringEngine, setup_production_data_paths
    
    config = create_default_config()
    provider = ReadOnlyDataProvider(config)
    setup_production_data_paths(config)
    
    engine = V318ScoringEngine(provider)
    
    # 评分单只股票
    result = engine.score_stock_asof("600519", "2024-12-31")
    
    provider.close()
    
    if result:
        print(f"✅ 评分成功:")
        print(f"   Grade: {result['grade_v318']}")
        print(f"   Entry: {result.get('entry_window_v318', 'N/A')}")
        print(f"   Structure: {result.get('trend_structure_score', 'N/A')}")
    else:
        print("⚠️  评分返回None（可能数据不足）")
        sys.exit(1)

except Exception as e:
    print(f"❌ 评分失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
EOF

echo ""
echo "==================================================================="
echo "✅ 预检通过！可以运行完整评级和回测"
echo "==================================================================="
echo ""
echo "下一步:"
echo "  1. 运行12月诊断测试 (21天):"
echo "     python3 run_research_backtest.py configs/baseline_2024_month.json"
echo ""
echo "  2. 运行全年回测 (244天):"
echo "     python3 run_research_backtest.py configs/baseline_2024_year.json"
echo ""
