from datetime import date

from unirag.synthesis.formatting import fix_inr_grouping, inr, rows_to_prompt_table
from unirag.synthesis.verifier import build_pool, extract_figures, verify_answer

ROWS = [
    {"grant_code": "GR-AI-01", "amount": 150000, "application_deadline": "2026-12-20", "status": "open"},
    {"grant_code": "GR-ML-02", "amount": 60000, "application_deadline": "2027-01-10", "status": "open"},
]
POLICY = ("[Research Grant Policy 2026-27 > §3 > GR-AI-01]\n- Minimum CGPA: 8.5 (10-point scale)\n"
          "[Fee Payment & Refund Policy 2026-27 > §4 Late payment fines]\nA late fine of ₹100 per day ... "
          "capped at ₹5,000 per component. [Scholarship Policy §2] 9.00 and above: 25% of annual tuition; "
          "8.50 – 8.99: 15% of annual tuition")


def _pool(rows=ROWS, question="I am a third-year CS student with a 3.8 GPA", extra=None):
    return build_pool(rows=rows, passages_text=POLICY, rule_text="", profile={"cgpa": 9.5, "gpa_4": 3.8},
                      question=question, as_of=date(2026, 11, 23), extra_dates=extra)


def test_inr_grouping():
    assert inr(145000) == "₹1,45,000"
    assert inr(5000) == "₹5,000"
    assert inr(21750.5) == "₹21,750.50"
    assert fix_inr_grouping("pay ₹150,000 or ₹ 60000") == "pay ₹1,50,000 or ₹60,000"


def test_prompt_table_formats_money_and_dates():
    t = rows_to_prompt_table(["grant_code", "amount", "application_deadline"], ROWS)
    assert "₹1,50,000" in t and "20 Dec 2026" in t


def test_extract_ignores_codes_sections_and_years():
    figs = extract_figures("GR-AI-01 [Doc: Grant Policy §3] for S2024CS007 in Year 3, term 2026-ODD, 2026-27")
    assert figs == []


def test_extract_dates_in_several_formats():
    vals = {f.value for f in extract_figures("by 20 Dec 2026, December 31, 2026 or 2027-01-10")}
    assert vals == {"2026-12-20", "2026-12-31", "2027-01-10"}


def test_correct_answer_fully_verified():
    ans = ("Your 3.8 GPA equals CGPA 9.5 [Doc: Student Handbook §4]. You qualify for GR-AI-01 (₹1,50,000, "
           "apply by 20 Dec 2026) [SQL] and GR-ML-02 (₹60,000, by 10 Jan 2027) [SQL]. Minimum CGPA 8.5 [Doc].")
    r = verify_answer(ans, _pool())
    assert r.ok, r.summary()


def test_hallucinated_amount_and_date_flagged():
    ans = "GR-AI-01 pays ₹1,75,000 and closes on 15 Jan 2027 [SQL]."
    r = verify_answer(ans, _pool())
    assert {f.text for f in r.unverified} == {"₹1,75,000", "15 Jan 2027"}


def test_derived_values_accepted():
    ledger = [{"component": "lab", "amount_due": 8000, "amount_paid": 0, "due_date": "2026-08-14"},
              {"component": "tuition_inst1", "amount_due": 90000, "amount_paid": 45000, "due_date": "2026-08-14"},
              {"component": "annual_tuition", "amount": 145000}]
    pool = _pool(rows=ledger, question="As of 30 November 2026 ...", extra=[date(2026, 11, 30)])
    ans = ("Balance ₹45,000 on tuition [SQL]; lab overdue 108 days, so 108 × ₹100 = ₹10,800, capped at ₹5,000 "
           "[Doc]. 15% of ₹1,45,000 is ₹21,750.")
    r = verify_answer(ans, pool)
    assert r.ok, r.summary()


def test_total_of_verified_figures_across_sources_accepted():
    rows = [{"component": "tuition_inst1", "late_fine": 5000}]
    pool = build_pool(rows=rows, passages_text="A late examination fee is charged a flat ₹500.", rule_text="",
                      profile={}, question="late fine?", as_of=date(2026, 11, 23))
    r = verify_answer("Tuition fine ₹5,000 [SQL] plus exam ₹500 [Doc] = total ₹5,500.", pool)
    assert r.ok, r.summary()
    r2 = verify_answer("Tuition fine ₹5,000 [SQL] plus exam ₹500 [Doc] = total ₹5,900.", pool)
    assert [f.text for f in r2.unverified] == ["₹5,900"]


def test_prompt_table_handles_booleans_in_money_like_columns():
    t = rows_to_prompt_table(["student_id", "has_overdue_fees", "tuition_fee"],
                             [{"student_id": "S2025CS017", "has_overdue_fees": False, "tuition_fee": 145000}])
    assert "| S2025CS017 | no | ₹1,45,000 |" in t


def test_total_of_four_items_is_not_accepted_by_coincidence():
    rows = [{"component": "tuition_inst1", "balance": 45000, "late_fine": 5000},
            {"component": "exam", "balance": 3500, "late_fine": 500},
            {"component": "tuition_inst2", "balance": 60000, "late_fine": 0}]
    pool = build_pool(rows=rows, passages_text="", rule_text="", profile={}, question="my pending fees",
                      as_of=date(2026, 11, 23))
    ans = "Balances ₹45,000, ₹3,500 and ₹60,000; fines ₹5,000 and ₹500. Total outstanding ₹1,13,500."
    r = verify_answer(ans, pool)
    assert [f.text for f in r.unverified] == ["₹1,13,500"]


def test_answer_total_must_match_database_total():
    from unirag.synthesis.verifier import database_totals
    rows = [{"component": "tuition_inst1", "late_fine": 5000, "total_late_fine": 5500},
            {"component": "exam", "late_fine": 500, "total_late_fine": 5500}]
    pool = build_pool(rows=rows, passages_text="processing fee of ₹1,000", rule_text="", profile={},
                      question="late fine?", as_of=date(2026, 11, 23))
    bad = verify_answer("Fines: ₹5,000 and ₹1,000. Total ₹6,000.", pool, db_totals=database_totals(rows))
    assert not bad.ok and any(f.kind == "consistency" for f in bad.unverified)
    good = verify_answer("Fines: ₹5,000 and ₹500. Total ₹5,500.", pool, db_totals=database_totals(rows))
    assert good.ok, good.summary()
