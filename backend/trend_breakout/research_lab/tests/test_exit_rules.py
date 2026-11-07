"""测试退出规则 - 合成数据测试

测试:
- V3.1.8 exit_signal触发
- TrendGrade降级FILTER
- 最大持仓期
- 强制止损
- 停牌无法退出
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exit_rules import ExitRulesEngine, ExitConfig


def test_exit_signal_watch():
    """测试exit_signal=WATCH触发退出"""
    config = ExitConfig(
        use_v318_exit_signal=True,
        exit_signals=['WATCH', 'EXIT'],
    )
    engine = ExitRulesEngine(config)
    
    should_exit, reason = engine.should_exit(
        code="600519",
        current_date="2024-01-15",
        open_date="2024-01-05",
        avg_cost=1600.0,
        current_price=1650.0,
        current_score={
            "grade_v318": "A",
            "_full_result": {"exit_signal": "WATCH"}
        }
    )
    
    assert should_exit, "exit_signal=WATCH应触发退出"
    assert "v318_exit_signal_WATCH" in reason
    
    print("✅ test_exit_signal_watch 通过")


def test_grade_filter():
    """测试Grade降级到FILTER触发退出"""
    config = ExitConfig(exit_on_filter=True)
    engine = ExitRulesEngine(config)
    
    should_exit, reason = engine.should_exit(
        code="600519",
        current_date="2024-01-15",
        open_date="2024-01-05",
        avg_cost=1600.0,
        current_price=1620.0,
        current_score={
            "grade_v318": "FILTER",
            "_full_result": {"exit_signal": "HOLD"}
        }
    )
    
    assert should_exit, "Grade=FILTER应触发退出"
    assert "grade_filter" in reason
    
    print("✅ test_grade_filter 通过")


def test_max_holding_days():
    """测试最大持仓期"""
    config = ExitConfig(max_holding_days=60)
    engine = ExitRulesEngine(config)
    
    # 持有61天
    should_exit, reason = engine.should_exit(
        code="600519",
        current_date="2024-03-10",
        open_date="2024-01-05",
        avg_cost=1600.0,
        current_price=1700.0,
        current_score={
            "grade_v318": "A",
            "_full_result": {"exit_signal": "HOLD"}
        }
    )
    
    assert should_exit, "持有>60天应触发退出"
    assert "max_holding_days" in reason
    
    print("✅ test_max_holding_days 通过")


def test_stop_loss():
    """测试强制止损"""
    config = ExitConfig(stop_loss_pct=-20.0)
    engine = ExitRulesEngine(config)
    
    # 亏损21%
    should_exit, reason = engine.should_exit(
        code="600519",
        current_date="2024-01-15",
        open_date="2024-01-05",
        avg_cost=1600.0,
        current_price=1264.0,  # -21%
        current_score={
            "grade_v318": "B",
            "_full_result": {"exit_signal": "HOLD"}
        }
    )
    
    assert should_exit, "亏损>-20%应触发止损"
    assert "stop_loss" in reason
    
    print("✅ test_stop_loss 通过")


def test_suspended_cannot_exit():
    """测试停牌无法退出"""
    config = ExitConfig()
    engine = ExitRulesEngine(config)
    
    should_exit, reason = engine.should_exit(
        code="600519",
        current_date="2024-01-15",
        open_date="2024-01-05",
        avg_cost=1600.0,
        current_price=1600.0,
        current_score=None  # 停牌
    )
    
    assert not should_exit, "停牌应无法退出"
    assert "suspended_cannot_exit" in reason
    
    print("✅ test_suspended_cannot_exit 通过")


def test_hold_normal():
    """测试正常持有（不触发退出）"""
    config = ExitConfig(
        use_v318_exit_signal=True,
        exit_signals=['WATCH', 'EXIT'],
        exit_on_filter=True,
        max_holding_days=60,
        stop_loss_pct=-20.0,
    )
    engine = ExitRulesEngine(config)
    
    should_exit, reason = engine.should_exit(
        code="600519",
        current_date="2024-01-15",
        open_date="2024-01-05",  # 持有10天
        avg_cost=1600.0,
        current_price=1650.0,  # +3.1%
        current_score={
            "grade_v318": "A",
            "_full_result": {"exit_signal": "HOLD"}
        }
    )
    
    assert not should_exit, "正常持有不应退出"
    assert reason == "hold"
    
    print("✅ test_hold_normal 通过")


def run_all_tests():
    """运行所有测试"""
    print("\n退出规则测试 (合成数据)")
    print("=" * 80)
    
    tests = [
        test_exit_signal_watch,
        test_grade_filter,
        test_max_holding_days,
        test_stop_loss,
        test_suspended_cannot_exit,
        test_hold_normal,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test_func.__name__} 失败: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {test_func.__name__} 错误: {e}")
            failed += 1
    
    print("\n" + "=" * 80)
    print(f"总计: {len(tests)} 测试")
    print(f"通过: {passed}")
    print(f"失败: {failed}")
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
