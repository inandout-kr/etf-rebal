import copy
import json
import unittest

from pretrade_estimator import composition_transition, estimate_pretrade


AS_OF = "2026-09-10T14:40:00+09:00"


def plan():
    return {"event_id": "event-1", "event_kind": "regular",
            "window_start": "2026-09-10T09:00:00+09:00",
            "available_at": "2026-09-09T18:00:00+09:00",
            "trade_date": "2026-09-10", "trade_end": "2026-09-10T15:30:00+09:00",
            "scope": "remaining_at_window_start", "assumption_no_reversal": True,
            "assumption_no_overtrade": True, "assumption_cash_completion_at_close": False,
            "rows": [{"code": "A", "required_shares": 1000, "reference_price": 10},
                     {"code": "B", "required_shares": -500, "reference_price": 20},
                     {"code": "C", "required_shares": 250, "reference_price": 40}]}


def observation(alpha=0.4):
    p = plan()
    return {"event_id": p["event_id"], "window_start": p["window_start"],
            "as_of": "2026-09-10T14:30:00+09:00", "available_at": AS_OF,
            "kind": "cumulative",
            "rows": [{"code": r["code"], "net_buy_shares": alpha * r["required_shares"] + 10,
                      "background_shares": 10, "error_bound_shares": abs(r["required_shares"]) * 0.05}
                     for r in p["rows"]]}


def snapshot():
    return {"etf_code": "ETF", "as_of": "2026-09-09T08:00:00+09:00",
            "available_at": "2026-09-09T08:10:00+09:00", "kind": "pdf",
            "cu_units": 100, "etf_units": 10000, "complete": True,
            "corporate_actions_verified": True,
            "rows": [{"code": "A", "count": 100, "weight": 0.5},
                     {"code": "B", "count": 50, "weight": 0.5}]}


class BasketAlignmentTests(unittest.TestCase):
    def test_signed_fraction_and_staleness(self):
        result = estimate_pretrade(plan(), observation(), as_of=AS_OF)
        self.assertEqual(result["status"], "basket_alignment_only")
        self.assertAlmostEqual(result["projection_fraction_raw"], 0.4)
        self.assertAlmostEqual(result["buy_side_fraction_raw"], 0.4)
        self.assertAlmostEqual(result["sell_side_fraction_raw"], 0.4)
        self.assertAlmostEqual(result["rows"][1]["basket_aligned_shares"], -200)
        self.assertEqual(result["stale_seconds"], 600)
        self.assertEqual(result["observed_notional_coverage"], 1)
        json.dumps(result, allow_nan=False)

    def test_missing_is_not_zero_but_observed_zero_is_valid(self):
        unknown = estimate_pretrade(plan(), as_of=AS_OF)
        self.assertIsNone(unknown["basket_aligned_fraction"])
        self.assertIsNone(unknown["rows"][0]["basket_aligned_shares"])
        self.assertEqual(unknown["status"], "no_observation")
        zero = estimate_pretrade(plan(), observation(0), as_of=AS_OF)
        self.assertEqual(zero["basket_aligned_fraction"], 0)
        self.assertIsNone(zero["actual_etf_completion_fraction"])

    def test_cumulative_snapshots_replace_not_sum(self):
        earlier, latest = observation(0.2), observation(0.4)
        earlier["as_of"] = earlier["available_at"] = "2026-09-10T14:00:00+09:00"
        result = estimate_pretrade(plan(), [latest, earlier], as_of=AS_OF)
        self.assertAlmostEqual(result["basket_aligned_fraction"], 0.4)
        latest["rows"].pop()
        result = estimate_pretrade(plan(), [earlier, latest], as_of=AS_OF)
        self.assertEqual(result["supported_stock_count"], 2)
        self.assertIsNone(result["basket_aligned_fraction"])

    def test_one_sided_support_insufficient(self):
        p, obs = plan(), observation()
        p["rows"][1]["required_shares"] = 500
        obs["rows"][1]["net_buy_shares"] = 210
        result = estimate_pretrade(p, obs, as_of=AS_OF)
        self.assertEqual(result["status"], "insufficient_support")
        self.assertIsNone(result["basket_aligned_fraction"])

    def test_incoherent_flows_are_conflicts_but_excess_signal_is_valid(self):
        opposing = observation()
        opposing["rows"][1]["net_buy_shares"] = 210
        for obs in (opposing,):
            result = estimate_pretrade(plan(), obs, as_of=AS_OF)
            self.assertEqual(result["status"], "model_conflict")
            self.assertIsNone(result["basket_aligned_fraction"])
        for fraction in (-0.4, 1.2):
            result = estimate_pretrade(plan(), observation(fraction), as_of=AS_OF)
            self.assertEqual(result["status"], "basket_alignment_only")
            self.assertAlmostEqual(result["projection_fraction_raw"], fraction)
            self.assertAlmostEqual(result["basket_aligned_fraction"], fraction)
            self.assertIsNone(result["projection_fraction_clipped"])

    def test_missing_background_or_error_is_excluded(self):
        for field in ("background_shares", "error_bound_shares"):
            obs = observation()
            del obs["rows"][0][field]
            result = estimate_pretrade(plan(), obs, as_of=AS_OF)
            self.assertEqual(result["status"], "insufficient_support")
            self.assertEqual(result["supported_stock_count"], 2)
            self.assertAlmostEqual(result["observed_notional_coverage"], 2 / 3)

    def test_imitation_cannot_identify_etf_executions(self):
        # The same public observations can be generated entirely by a front-runner.
        result = estimate_pretrade(plan(), observation(1), as_of=AS_OF)
        self.assertEqual(result["basket_aligned_fraction"], 1)
        self.assertIsNone(result["actual_etf_pretrade_shares"])
        for row in result["rows"]:
            self.assertIsNone(row["actual_etf_pretrade_shares"])
        self.assertEqual(result["rows"][1]["remaining_shares_unidentified_range"], [-500, 0])

    def test_attribution_is_conditional_and_not_default(self):
        obs = observation()
        self.assertEqual(estimate_pretrade(plan(), obs, as_of=AS_OF)["attribution_scenarios"], [])
        obs["attribution_scenarios"] = [{"label": "half", "etf_share": 0.5}]
        result = estimate_pretrade(plan(), obs, as_of=AS_OF)
        row = result["attribution_scenarios"][0]["rows"][1]
        self.assertAlmostEqual(row["conditional_pretrade_shares"], -100)
        self.assertAlmostEqual(row["conditional_remaining_shares"], -400)
        self.assertEqual(row["conditional_pretrade_range"], [-112.5, -87.5])
        self.assertEqual(row["conditional_remaining_range"], [-412.5, -387.5])
        self.assertIsNone(result["actual_etf_pretrade_shares"])

    def test_bounding_assumptions_do_not_bound_public_signal(self):
        for field in ("assumption_no_reversal", "assumption_no_overtrade"):
            p = plan()
            del p[field]
            result = estimate_pretrade(p, observation(), as_of=AS_OF)
            self.assertEqual(result["status"], "basket_alignment_only")
            self.assertIsNone(result["projection_fraction_clipped"])
            self.assertIsNone(result["rows"][1]["remaining_shares_unidentified_range"])
            self.assertAlmostEqual(result["projection_fraction_raw"], 0.4)

    def test_multi_day_trade_window(self):
        p = plan()
        p["available_at"] = "2026-09-08T18:00:00+09:00"
        p["trade_date"] = "2026-09-09"
        p["window_start"] = "2026-09-09T09:00:00+09:00"
        obs = observation()
        obs["window_start"] = p["window_start"]
        self.assertEqual(estimate_pretrade(p, obs, as_of=AS_OF)["status"], "basket_alignment_only")
        p["trade_date"] = "2026-09-11"
        with self.assertRaises(ValueError):
            estimate_pretrade(p, obs, as_of=AS_OF)

    def test_excess_public_signal_is_bounded_only_after_etf_attribution(self):
        obs = observation(1.2)
        obs["attribution_scenarios"] = [{"label": "half", "etf_share": 0.5},
                                         {"label": "all", "etf_share": 1}]
        result = estimate_pretrade(plan(), obs, as_of=AS_OF)
        half, all_flows = result["attribution_scenarios"]
        self.assertAlmostEqual(result["basket_aligned_fraction"], 1.2)
        self.assertAlmostEqual(half["conditional_completion_fraction"], 0.6)
        self.assertAlmostEqual(half["rows"][0]["conditional_remaining_shares"], 400)
        self.assertEqual(all_flows["status"], "model_conflict")
        self.assertIsNone(all_flows["rows"][0]["conditional_remaining_shares"])
        p = plan()
        p["assumption_no_overtrade"] = False
        unbounded = estimate_pretrade(p, obs, as_of=AS_OF)["attribution_scenarios"][1]
        self.assertAlmostEqual(unbounded["conditional_completion_fraction"], 1.2)
        self.assertAlmostEqual(unbounded["rows"][0]["conditional_remaining_shares"], -200)

    def test_target_cannot_be_known_after_measurement_window_begins(self):
        p = plan()
        p["available_at"] = "2026-09-10T10:00:00+09:00"
        with self.assertRaises(ValueError):
            estimate_pretrade(p, observation(), as_of=AS_OF)
        p["target_known_at"] = "2026-09-10T08:00:00+09:00"
        self.assertEqual(estimate_pretrade(p, observation(), as_of=AS_OF)["status"], "basket_alignment_only")

    def test_feasible_representative_does_not_hide_raw_projection(self):
        obs = observation()
        for row, target, center, radius in zip(obs["rows"], plan()["rows"], (0.05, 0.54, 0.54), (0.05, 0.46, 0.46)):
            row["net_buy_shares"] = 10 + center * target["required_shares"]
            row["error_bound_shares"] = radius * abs(target["required_shares"])
        result = estimate_pretrade(plan(), obs, as_of=AS_OF)
        self.assertLess(result["projection_fraction_raw"], result["common_fraction_feasible_interval"][0])
        self.assertAlmostEqual(result["basket_aligned_fraction"], 0.08)

    def test_future_publications_skip_without_lookahead(self):
        early, future, delayed = observation(0.4), observation(0.8), observation(0.9)
        p, final_auction = plan(), observation(1)
        p["trade_end"] = "2026-09-10T15:20:00+09:00"
        final_auction["as_of"] = "2026-09-10T15:30:00+09:00"
        final_auction["available_at"] = "2026-09-10T15:31:00+09:00"
        early["as_of"] = "2026-09-10T11:00:00+09:00"
        early["available_at"] = "2026-09-10T11:05:00+09:00"
        future["as_of"] = future["available_at"] = "2026-09-10T13:00:00+09:00"
        delayed["as_of"] = early["as_of"]
        delayed["available_at"] = "2026-09-10T12:00:00+09:00"
        result = estimate_pretrade(p, [future, delayed, final_auction, early], as_of=early["available_at"])
        self.assertAlmostEqual(result["basket_aligned_fraction"], 0.4)
        self.assertEqual(result["unavailable_snapshot_count"], 3)
        self.assertEqual(result["stale_seconds"], 300)
        unknown = estimate_pretrade(plan(), future, as_of=early["available_at"])
        self.assertEqual(unknown["status"], "no_observation")
        self.assertIsNone(unknown["basket_aligned_fraction"])

    def test_wrong_window_naive_and_impossible_publication_rejected(self):
        for field, value in (("available_at", "2026-09-10T14:00:00+09:00"),
                             ("as_of", "2026-09-10T14:30:00"),
                             ("window_start", "2026-09-10T10:00:00+09:00"),
                             ("event_id", "other"), ("kind", "incremental")):
            obs = observation()
            obs[field] = value
            with self.assertRaises(ValueError):
                estimate_pretrade(plan(), obs, as_of=AS_OF)

    def test_plan_scope_and_warnings_are_preserved(self):
        p = plan()
        p["warnings"] = ["pretrade_before_window_start_unknown"]
        result = estimate_pretrade(p, as_of=AS_OF)
        self.assertEqual(result["plan_scope"], "remaining_at_window_start")
        self.assertEqual(result["plan_warnings"], p["warnings"])

    def test_duplicate_and_nonfinite_values_rejected(self):
        p = plan()
        p["rows"].append(p["rows"][0])
        with self.assertRaises(ValueError):
            estimate_pretrade(p, as_of=AS_OF)
        obs = observation()
        obs["rows"][0]["net_buy_shares"] = float("nan")
        with self.assertRaises(ValueError):
            estimate_pretrade(plan(), obs, as_of=AS_OF)
        obs = observation()
        obs["rows"][0]["error_bound_shares"] = 0
        with self.assertRaises(ValueError):
            estimate_pretrade(plan(), obs, as_of=AS_OF)


class CompositionTests(unittest.TestCase):
    def transition(self, current, baseline=None):
        current = copy.deepcopy(current)
        current["as_of"] = "2026-09-10T08:00:00+09:00"
        if current["available_at"] == snapshot()["available_at"]:
            current["available_at"] = "2026-09-10T08:10:00+09:00"
        return composition_transition(baseline or snapshot(), current, as_of=AS_OF)

    def test_same_asof_revision_is_not_a_transition(self):
        current = snapshot()
        current["rows"][0]["count"] += 10
        current["available_at"] = "2026-09-09T09:00:00+09:00"
        with self.assertRaises(ValueError):
            composition_transition(snapshot(), current, as_of=AS_OF)

    def test_price_only_drift_does_not_change_exposure(self):
        current = snapshot()
        current["rows"][0]["weight"] = 0.7
        current["rows"][1]["weight"] = 0.3
        self.assertTrue(all(r["exposure_transition_shares"] == 0 for r in self.transition(current)["rows"]))

    def test_creations_and_proportional_cu_changes_do_not_show_pretrade(self):
        current = snapshot()
        current["etf_units"] *= 2
        result = self.transition(current)
        self.assertTrue(all(r["exposure_transition_shares"] == 0 for r in result["rows"]))
        current["cu_units"] *= 3
        for row in current["rows"]:
            row["count"] *= 3
        self.assertTrue(all(r["exposure_transition_shares"] == 0 for r in self.transition(current)["rows"]))

    def test_stock_and_etf_split_corrections(self):
        current = snapshot()
        current["rows"][0]["count"] *= 2
        current["stock_split_factors"] = {"A": 2}
        current["etf_split_factor"] = 5
        current["etf_units"] *= 5
        current["cu_units"] *= 5
        self.assertTrue(all(r["exposure_transition_shares"] == 0 for r in self.transition(current)["rows"]))

    def test_complete_missing_row_is_exit_not_unknown(self):
        current = snapshot()
        current["rows"].pop()
        result = self.transition(current)
        self.assertEqual(result["rows"][1]["exposure_transition_shares"], -5000)
        self.assertIsNone(result["rows"][1]["actual_market_execution_shares"])
        self.assertEqual(result["classification"], "composition_proxy")

    def test_transition_sized_using_current_units(self):
        current = snapshot()
        current["etf_units"] = 20000
        current["rows"][0]["count"] += 10
        self.assertAlmostEqual(self.transition(current)["rows"][0]["exposure_transition_shares"], 2000)

    def test_partial_future_unknown_actions_or_different_etf_rejected(self):
        for field, value in (("complete", False), ("corporate_actions_verified", False),
                             ("available_at", "2026-09-11T08:00:00+09:00"),
                             ("etf_code", "other")):
            current = snapshot()
            current[field] = value
            with self.assertRaises(ValueError):
                self.transition(current)
        baseline = snapshot()
        del baseline["corporate_actions_verified"]
        with self.assertRaises(ValueError):
            self.transition(snapshot(), baseline)

    def test_holdings_evidence_is_not_market_execution(self):
        baseline, current = snapshot(), snapshot()
        baseline["kind"] = current["kind"] = "holdings"
        current["rows"][0]["count"] += 10
        result = self.transition(current, baseline)
        self.assertEqual(result["classification"], "holdings_exposure")
        self.assertIsNone(result["actual_market_execution_shares"])


if __name__ == "__main__":
    unittest.main()
