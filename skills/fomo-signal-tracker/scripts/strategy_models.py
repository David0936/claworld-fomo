#!/usr/bin/env python3
"""Calculate fee-aware principal-recovery fractions for Fomo strategy tracking."""

import argparse
import json


def required_fraction(multiple: float, buy_fee: float, sell_fee: float, target: float) -> float:
    denominator = (1 - buy_fee) * multiple * (1 - sell_fee)
    if denominator <= 0:
        raise ValueError("Fees and multiple must leave a positive recoverable value")
    return target / denominator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--principal", type=float, default=100.0)
    parser.add_argument("--multiple", type=float, required=True)
    parser.add_argument("--buy-fee", type=float, default=0.005)
    parser.add_argument("--sell-fee", type=float, default=0.005)
    parser.add_argument("--target", type=float, default=1.0, help="Net cash target as a principal multiple")
    args = parser.parse_args()

    if args.principal <= 0 or args.multiple <= 0:
        parser.error("principal and multiple must be positive")
    if not 0 <= args.buy_fee < 1 or not 0 <= args.sell_fee < 1:
        parser.error("fees must be decimal rates in [0, 1)")
    if args.target < 0:
        parser.error("target must be non-negative")

    fraction = required_fraction(args.multiple, args.buy_fee, args.sell_fee, args.target)
    sold_fraction = min(fraction, 1.0)
    gross_value = args.principal * (1 - args.buy_fee) * args.multiple
    net_cash = sold_fraction * gross_value * (1 - args.sell_fee)
    output = {
        "principal": args.principal,
        "price_multiple": args.multiple,
        "buy_fee": args.buy_fee,
        "sell_fee": args.sell_fee,
        "target_net_cash": args.principal * args.target,
        "gross_position_value": gross_value,
        "required_token_fraction": fraction,
        "required_token_percent": fraction * 100,
        "target_recoverable": fraction <= 1,
        "modeled_net_cash": net_cash,
        "remaining_token_percent": (1 - sold_fraction) * 100,
        "remaining_gross_value": (1 - sold_fraction) * gross_value,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
