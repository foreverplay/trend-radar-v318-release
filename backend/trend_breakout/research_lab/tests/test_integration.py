"""旧的 mock 评分集成测试已删除，避免把简化分数当成闭环。"""
import scoring_engine


def test_simplified_fallback_is_gone():
    assert not hasattr(scoring_engine, "score_stock_asof_fallback")
    assert not hasattr(scoring_engine.V318ScoringEngine, "score_stock_asof_fallback")
