"""Unit tests for V3.1.8 TrendGrade independence + RAPID overlay."""
from __future__ import annotations

from v3.engines.rapid_main_rise import detect_rapid_main_rise
from v3.grading_v318 import grade_trend_v318
from v3.types import Grade
from v3.v31.entry_window import apply_v318_entry_overlay
from v3.v31.types import EntryWindow, ExhaustionLevel


def test_a_plus_structure_no_maturity_required():
    g = grade_trend_v318(
        trend_structure_score=90,
        continuity_score=82,
        sector_score=75,
        fund_risk_level="NONE",
        trend_state="MAIN_RISE",
    )
    assert g.grade == Grade.A_PLUS
    assert g.path == "structure_classic"


def test_building_does_not_demote_a_plus():
    g = grade_trend_v318(
        trend_structure_score=90,
        continuity_score=82,
        sector_score=75,
        trend_state="MAIN_RISE",
    )
    # Maturity not even an input — BUILDING cannot demote
    assert g.grade == Grade.A_PLUS


def test_ganli_plan_a_band():
    g = grade_trend_v318(
        tech_score=96,
        continuity_score=73.2,
        sector_score=68,
        trend_state="CONFIRMED",
    )
    assert g.grade == Grade.A


def test_rapid_follow_overlay():
    rapid = detect_rapid_main_rise(
        trend_state="ACCELERATION",
        return20=0.20,
        continuity_score=76,
        ma5_gt_ma10_gt_ma20=True,
        higher_low_intact=True,
    )
    assert rapid.active
    ew, notes = apply_v318_entry_overlay(
        entry_window=EntryWindow.NO_ENTRY,
        maturity_level="BUILDING",
        rapid_main_rise=True,
        exhaustion=ExhaustionLevel.NORMAL,
    )
    assert ew == EntryWindow.FOLLOW
    assert any("RAPID" in n for n in notes)


def test_rapid_does_not_upgrade_extended():
    ew, _ = apply_v318_entry_overlay(
        entry_window=EntryWindow.EXTENDED,
        maturity_level="BUILDING",
        rapid_main_rise=True,
        exhaustion=ExhaustionLevel.NORMAL,
    )
    assert ew == EntryWindow.EXTENDED


def test_c_grade_restored():
    g = grade_trend_v318(tech_score=58, continuity_score=52, trend_state="CONFIRMED")
    assert g.grade == Grade.C


if __name__ == "__main__":
    for fn in [
        test_a_plus_structure_no_maturity_required,
        test_building_does_not_demote_a_plus,
        test_ganli_plan_a_band,
        test_rapid_follow_overlay,
        test_rapid_does_not_upgrade_extended,
        test_c_grade_restored,
    ]:
        fn()
        print("OK", fn.__name__)
    print("all passed")
