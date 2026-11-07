"""Phase 1: 数据质量与完整性 — 主执行脚本

执行优先级：
P0: ST历史, 退市历史, 停牌日历（防止幸存者偏差和成交模拟错误）
P1: 财报披露时点, 行业分类历史（防止时间穿越）
P2: 全市场K线扩展（当前仅12只样例）
"""
import sys
from pathlib import Path

# 添加路径
LAB_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_ROOT))
sys.path.insert(0, str(LAB_ROOT / "utils"))

from config import (
    ST_STATUS_FILE, 
    DELIST_FILE, 
    SUSPEND_FILE,
    PHASE1_STATUS,
    DATA_DIR
)
import json
from datetime import datetime


def run_phase1():
    """执行 Phase 1 数据准备"""
    print("=" * 70)
    print("Phase 1: 数据质量与完整性")
    print("=" * 70)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    status = PHASE1_STATUS.copy()
    
    # ==================== P0 任务 ====================
    print("\n" + "=" * 70)
    print("P0 任务: 核心数据补充")
    print("=" * 70)
    
    # 1. ST 状态历史
    print("\n[1/3] ST 状态历史")
    print("-" * 70)
    try:
        from fetch_st_history import fetch_st_history
        st_df = fetch_st_history(output_path=ST_STATUS_FILE, save=True)
        status['st_status'] = True
        print(f"✅ ST 状态历史: {len(st_df)} 条记录")
    except Exception as e:
        print(f"❌ ST 状态历史失败: {e}")
        status['st_status'] = False
    
    # 2. 退市历史
    print("\n[2/3] 退市历史")
    print("-" * 70)
    try:
        from fetch_delist_history import fetch_delist_history
        delist_df = fetch_delist_history(output_path=DELIST_FILE, save=True)
        status['delist_history'] = True
        print(f"✅ 退市历史: {len(delist_df)} 只股票")
    except Exception as e:
        print(f"❌ 退市历史失败: {e}")
        status['delist_history'] = False
    
    # 3. 停牌日历
    print("\n[3/3] 停牌日历")
    print("-" * 70)
    try:
        from fetch_suspend_calendar import fetch_suspend_calendar
        suspend_df = fetch_suspend_calendar(output_path=SUSPEND_FILE, save=True)
        status['suspend_calendar'] = True
        print(f"✅ 停牌日历: {len(suspend_df)} 条记录")
    except Exception as e:
        print(f"❌ 停牌日历失败: {e}")
        status['suspend_calendar'] = False
    
    # ==================== 状态汇总 ====================
    print("\n" + "=" * 70)
    print("Phase 1 执行状态")
    print("=" * 70)
    
    status_lines = []
    for task, completed in status.items():
        symbol = "✅" if completed else "❌"
        status_lines.append(f"{symbol} {task}: {'完成' if completed else '未完成'}")
    
    print("\n".join(status_lines))
    
    # 保存状态
    status_file = DATA_DIR / "phase1_status.json"
    status_data = {
        "completion_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "status": status,
        "completed_count": sum(status.values()),
        "total_count": len(status)
    }
    status_file.write_text(json.dumps(status_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n状态保存: {status_file}")
    
    # ==================== 数据文件清单 ====================
    print("\n" + "=" * 70)
    print("Phase 1 数据文件")
    print("=" * 70)
    
    data_files = list(DATA_DIR.glob("*.csv"))
    for f in data_files:
        size_kb = f.stat().st_size / 1024
        print(f"  {f.name}: {size_kb:.1f} KB")
    
    # ==================== 限制与建议 ====================
    print("\n" + "=" * 70)
    print("当前限制与建议")
    print("=" * 70)
    print("""
⚠️ 当前限制:
1. ST历史: 仅当前状态，无完整时间线
2. 退市历史: 缺少精确退市日期
3. 停牌日历: akshare接口不稳定，建议从成交量推断
4. 全市场K线: 仍为12只样例，未扩展
5. 财报披露时点: 未实现（P1）
6. 行业分类历史: 未实现（P1）

✅ 已完成框架:
- 数据质量检查工具 (data_validator.py)
- 研究实验室目录结构 (research_lab/)
- ST/退市/停牌基础数据（有限）

🎯 Phase 2 前提条件:
- 至少需要全市场K线（当前P2阻塞）
- 建议补充 tushare pro 数据源（需token）
- 或使用Wind数据（需商业授权）

📋 下一步:
- Phase 2: 统一回测引擎 + RuleProvider
- Phase 3: E01 趋势基准策略定义
""")
    
    print("\n" + "=" * 70)
    print(f"Phase 1 完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    return status


if __name__ == "__main__":
    status = run_phase1()
    
    # 返回状态码
    completed = sum(status.values())
    total = len(status)
    
    if completed == total:
        print(f"\n✅ Phase 1 全部完成 ({completed}/{total})")
        sys.exit(0)
    else:
        print(f"\n⚠️ Phase 1 部分完成 ({completed}/{total})")
        sys.exit(1)
