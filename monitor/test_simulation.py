import math
import unittest

from simulation import simulate as engine_simulate
from functools import partial

# Preserve the original 12% stress scenarios with explicit rates.
simulate = partial(engine_simulate, buy_fee=0.12, sell_fee=0.12)


def sample(time, price, **extra):
    value = {
        "observed_at": time,
        "price": price,
    }
    value.update(extra)
    return value


class SimulationTests(unittest.TestCase):
    def model(self, result, model_id):
        return next(model for model in result["models"] if model["id"] == model_id)

    def test_official_schedule_entry_and_initial_liquidation(self):
        observations = [sample("2026-09-06T00:00:00+00:00", 10.0)]
        expected = {
            "evm": (99.5, 0.5, 99.0025, 0.005),
            "solana": (99.05, 0.95, 98.10, 0.0095),
        }
        for schedule, (invested, fee, net_value, effective_rate) in expected.items():
            with self.subTest(schedule=schedule):
                result = engine_simulate(observations, fee_schedule=schedule)
                model = self.model(result, "hold")
                buy = model["trades"][0]
                self.assertAlmostEqual(buy["gross"], invested)
                self.assertAlmostEqual(buy["fee"], fee)
                self.assertAlmostEqual(buy["effective_fee_rate"], effective_rate)
                self.assertAlmostEqual(model["remaining_gross_value"], invested)
                self.assertAlmostEqual(model["net_liquidation_value"], net_value)

    def test_solana_schedule_boundaries_and_tiny_or_zero_balances(self):
        # A 100U entry at 10U buys 9.905 tokens.  Marking the position at
        # these prices makes the sell gross land exactly on each fee tier.
        entry_price = 10.0
        initial_units = 99.05 / entry_price
        expected_fees = {
            0.05: 0.05,
            5.0: 0.10,
            47.5: 0.95,
            190.0: 0.95,
            200.0: 1.00,
        }
        for gross, expected_fee in expected_fees.items():
            with self.subTest(gross=gross):
                result = engine_simulate([
                    sample("2026-09-06T00:00:00+00:00", entry_price),
                    sample("2026-09-06T00:01:00+00:00", gross / initial_units),
                ], fee_schedule="solana")
                model = self.model(result, "hold")
                if gross / initial_units < entry_price * 0.5:
                    trade = model["trades"][1]
                    self.assertAlmostEqual(trade["gross"], gross)
                    self.assertAlmostEqual(trade["fee"], expected_fee)
                    self.assertAlmostEqual(trade["net"], gross - expected_fee)
                    self.assertAlmostEqual(
                        trade["effective_fee_rate"], expected_fee / gross
                    )
                else:
                    self.assertAlmostEqual(model["remaining_gross_value"], gross)
                    self.assertAlmostEqual(
                        model["hypothetical_remaining_liquidation_fee"],
                        expected_fee,
                    )

        tiny = engine_simulate(
            [sample("2026-09-06T00:00:00+00:00", 10.0)],
            principal=0.05,
            fee_schedule="solana",
        )
        for model in tiny["models"]:
            self.assertAlmostEqual(model["trades"][0]["fee"], 0.05)
            self.assertAlmostEqual(model["trades"][0]["gross"], 0.0)
            self.assertAlmostEqual(model["hypothetical_remaining_liquidation_fee"], 0.0)
            self.assertAlmostEqual(model["net_liquidation_value"], 0.0)

    def test_official_target_recovery_and_staged_sell_recompute_fee_tier(self):
        observations = [
            sample("2026-09-06T00:00:00+00:00", 10.0),
            sample("2026-09-06T00:01:00+00:00", 20.0),
            sample("2026-09-06T00:02:00+00:00", 80.0),
        ]
        for schedule in ("evm", "solana"):
            with self.subTest(schedule=schedule):
                result = engine_simulate(observations, fee_schedule=schedule)
                recover = self.model(result, "recover2")
                runner100 = self.model(result, "runner25")
                runner110 = self.model(result, "runner25_110")

                self.assertAlmostEqual(recover["trades"][1]["net"], 100.0)
                staged = recover["trades"][2]
                self.assertAlmostEqual(
                    staged["fee"],
                    staged["gross"] * (0.005 if schedule == "evm" else 0.005),
                )
                self.assertAlmostEqual(
                    staged["net"], staged["gross"] - staged["fee"]
                )
                self.assertAlmostEqual(runner100["trades"][1]["net"], 100.0)
                self.assertAlmostEqual(runner110["trades"][1]["net"], 110.0)

    def test_unknown_fee_schedule_is_rejected(self):
        with self.assertRaises(ValueError):
            engine_simulate(
                [sample("2026-09-06T00:00:00+00:00", 10.0)],
                fee_schedule="unknown",
            )

    def test_initial_ledger_includes_both_fee_sides(self):
        result = simulate([sample("2026-09-06T00:00:00+00:00", 10.0)])
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["entry_price"], 10.0)
        self.assertEqual(result["latest_price"], 10.0)
        self.assertAlmostEqual(result["multiple"], 1.0)
        for model in result["models"]:
            self.assertAlmostEqual(model["remaining_token_percent"], 100.0)
            self.assertAlmostEqual(model["remaining_gross_value"], 88.0)
            self.assertAlmostEqual(model["realized_cash"], 0.0)
            self.assertAlmostEqual(model["paid_fees"], 12.0)
            self.assertAlmostEqual(model["hypothetical_remaining_liquidation_fee"], 10.56)
            self.assertAlmostEqual(model["net_liquidation_value"], 77.44)
            self.assertAlmostEqual(model["pnl"], -22.56)
            self.assertEqual(len(model["trades"]), 1)

    def test_recover2_fraction_and_staged_4x_sale(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0),
            sample("2026-09-06T00:01:00+00:00", 20.0),
            sample("2026-09-06T00:02:00+00:00", 40.0),
        ])
        model = self.model(result, "recover2")
        self.assertAlmostEqual(model["trades"][1]["fraction_of_initial"], 0.6456611570247934)
        self.assertAlmostEqual(model["trades"][1]["net"], 100.0)
        self.assertAlmostEqual(model["trades"][2]["fraction_of_initial"], 0.1771694214876033)
        self.assertAlmostEqual(model["trades"][2]["fraction_of_remaining"], 0.5)
        self.assertTrue(model["principal_recovered"])
        self.assertTrue(model["half_sold"])
        self.assertAlmostEqual(model["remaining_token_percent"], 17.71694214876033)
        self.assertAlmostEqual(model["net_liquidation_value"], 209.76)

    def test_recover2_leap_to_4x_waits_for_later_sample(self):
        leap = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0),
            sample("2026-09-06T00:01:00+00:00", 50.0),
        ])
        leap_model = self.model(leap, "recover2")
        self.assertEqual(len(leap_model["trades"]), 2)
        self.assertTrue(leap_model["principal_recovered"])
        self.assertFalse(leap_model["half_sold"])

        staged = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0),
            sample("2026-09-06T00:01:00+00:00", 50.0),
            sample("2026-09-06T00:02:00+00:00", 40.0),
        ])
        staged_model = self.model(staged, "recover2")
        self.assertEqual(len(staged_model["trades"]), 3)
        self.assertTrue(staged_model["half_sold"])

    def test_runner_100_and_110_fractions(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0),
            sample("2026-09-06T00:01:00+00:00", 25.0),
        ])
        runner100 = self.model(result, "runner25")
        runner110 = self.model(result, "runner25_110")
        self.assertAlmostEqual(runner100["trades"][1]["fraction_of_initial"], 0.5165289256198347)
        self.assertAlmostEqual(runner110["trades"][1]["fraction_of_initial"], 0.5681818181818182)
        self.assertAlmostEqual(runner100["trades"][1]["net"], 100.0)
        self.assertAlmostEqual(runner110["trades"][1]["net"], 110.0)

    def test_hold_requires_two_confirmed_low_samples(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0, fomo_holders=100,
                   fomo_ratio_lower=20, fomo_ratio_upper=22),
            sample("2026-09-06T00:01:00+00:00", 12.0, fomo_holders=19,
                   fomo_ratio_lower=20, fomo_ratio_upper=22),
            sample("2026-09-06T00:02:00+00:00", 11.0, fomo_holders=18,
                   fomo_ratio_lower=20, fomo_ratio_upper=22),
        ])
        model = self.model(result, "hold")
        self.assertEqual(model["status"], "exited")
        self.assertEqual(model["reason"], "fomo_retention")
        self.assertEqual(len(model["trades"]), 2)
        self.assertAlmostEqual(model["trades"][1]["price"], 11.0)

    def test_missing_and_uncertain_ratio_reset_streak(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0, fomo_holders=100, fomo_ratio_lower=20, fomo_ratio_upper=22),
            sample("2026-09-06T00:01:00+00:00", 12.0, fomo_ratio_lower=14, fomo_ratio_upper=16),
            sample("2026-09-06T00:02:00+00:00", 13.0, fomo_ratio_lower=14, fomo_ratio_upper=16),
            sample("2026-09-06T00:03:00+00:00", 14.0),
            sample("2026-09-06T00:04:00+00:00", 15.0, fomo_ratio_lower=15, fomo_ratio_upper=15),
        ])
        model = self.model(result, "hold")
        self.assertEqual(model["status"], "holding")
        self.assertEqual(len(model["trades"]), 1)

    def test_nonconfirmation_does_not_advance_hold_streak(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0, fomo_holders=100,
                   fomo_ratio_lower=20, fomo_ratio_upper=22),
            sample("2026-09-06T00:01:00+00:00", 12.0, fomo_holders=19, fomo_ratio_lower=20, fomo_ratio_upper=22),
            sample("2026-09-06T00:02:00+00:00", 13.0, fomo_holders=18, fomo_ratio_lower=20, fomo_ratio_upper=22,
                   confirmation=False),
            sample("2026-09-06T00:03:00+00:00", 14.0, fomo_holders=18, fomo_ratio_lower=20, fomo_ratio_upper=22),
        ])
        model = self.model(result, "hold")
        self.assertEqual(model["status"], "exited")
        self.assertAlmostEqual(model["trades"][1]["price"], 14.0)

    def test_forty_percent_stop_is_inclusive_and_terminal(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0),
            sample("2026-09-06T00:01:00+00:00", 6.1),
            sample("2026-09-06T00:02:00+00:00", 6.0),
            sample("2026-09-06T00:03:00+00:00", 40.0),
        ])
        for model in result["models"]:
            self.assertEqual(model["status"], "stopped")
            self.assertTrue(model["terminal"])
            self.assertEqual(len(model["trades"]), 2)
            self.assertAlmostEqual(model["trades"][1]["price"], 6.0)
            self.assertAlmostEqual(model["remaining_units"], 0.0)
        at_boundary = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0),
            sample("2026-09-06T00:01:00+00:00", 6.1),
        ])
        self.assertNotEqual(self.model(at_boundary, "hold")["status"], "stopped")

    def test_confirmed_fomo_below_fifteen_exits_every_model_immediately(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0, fomo_ratio_lower=17, fomo_ratio_upper=18),
            sample("2026-09-06T00:01:00+00:00", 12.0, fomo_ratio_lower=12.5, fomo_ratio_upper=13),
            sample("2026-09-06T00:02:00+00:00", 20.0, fomo_ratio_lower=20, fomo_ratio_upper=21),
        ])
        for model in result["models"]:
            self.assertEqual(model["status"], "exited")
            self.assertEqual(model["reason"], "fomo_below_15")
            self.assertEqual(model["remaining_units"], 0)
            self.assertEqual(model["trades"][-1]["price"], 12.0)
            self.assertEqual(model["trades"][-1]["reason"], "fomo_below_15")

    def test_ratio_interval_crossing_fifteen_does_not_force_exit(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0, fomo_ratio_lower=17, fomo_ratio_upper=18),
            sample("2026-09-06T00:01:00+00:00", 12.0, fomo_ratio_lower=14, fomo_ratio_upper=16),
        ])
        for model in result["models"]:
            self.assertNotEqual(model["reason"], "fomo_below_15")

    def test_time_sorting_and_duplicate_or_invalid_input(self):
        result = simulate([
            sample("2026-09-06T02:00:00+02:00", 20.0),
            sample("2026-09-05T23:00:00+00:00", 10.0),
        ])
        self.assertEqual(result["entry_price"], 10.0)
        self.assertEqual(result["latest_price"], 20.0)
        self.assertEqual(result["entry_observed_at"], "2026-09-05T23:00:00+00:00")

        invalid = [
            [sample("2026-09-06T00:00:00", 10.0)],
            [sample("2026-09-06T00:00:00+00:00", 0.0)],
            [sample("2026-09-06T00:00:00+00:00", float("nan"))],
            [sample("2026-09-06T00:00:00+00:00", 10.0),
             sample("2026-09-06T00:00:00+00:00", 11.0)],
        ]
        for samples in invalid:
            with self.assertRaises(ValueError):
                simulate(samples)

    def test_cash_conservation_and_all_outputs_finite(self):
        result = simulate([
            sample("2026-09-06T00:00:00+00:00", 10.0),
            sample("2026-09-06T00:01:00+00:00", 20.0),
            sample("2026-09-06T00:02:00+00:00", 40.0),
        ])
        for model in result["models"]:
            sell_gross = sum(t["gross"] for t in model["trades"] if t["type"] == "sell")
            sell_fees = sum(t["fee"] for t in model["trades"] if t["type"] == "sell")
            sell_net = sum(t["net"] for t in model["trades"] if t["type"] == "sell")
            self.assertAlmostEqual(sell_gross - sell_fees, sell_net)
            self.assertAlmostEqual(model["realized_cash"], sell_net)
            self.assertAlmostEqual(
                model["net_liquidation_value"],
                model["realized_cash"] + model["remaining_gross_value"]
                - model["hypothetical_remaining_liquidation_fee"],
            )

            def assert_finite(value):
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value))
                elif isinstance(value, dict):
                    for child in value.values():
                        assert_finite(child)
                elif isinstance(value, list):
                    for child in value:
                        assert_finite(child)

            assert_finite(model)


if __name__ == "__main__":
    unittest.main()
