"""Deterministic, fee-aware replay of the Fomo exit models.

The module deliberately has no market-data, database, or trading dependencies.  A
caller supplies the observations that were actually collected and the models are
replayed against those observations in timestamp order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from numbers import Real
from typing import Any, Dict, List, Optional, Tuple


_RATIO_EXIT = 15.0
_HOLDER_RETENTION = 0.20
_STOP_MULTIPLE = 0.60
_RECOVER_MULTIPLE = 2.0
_RUNNER_MULTIPLE = 2.5
_HALF_MULTIPLE = 4.0


@dataclass(frozen=True)
class _FeePolicy:
    """One platform-fee rule, evaluated against a trade's gross amount."""

    kind: str
    rate: Optional[float] = None

    def fee(self, gross: float) -> float:
        if gross <= 0:
            return 0.0
        if self.kind == "percentage":
            assessed = gross * self.rate
        elif self.kind == "evm":
            assessed = gross * 0.005
        elif self.kind == "solana":
            if gross < 5.0:
                assessed = 0.10
            elif gross < 47.5:
                assessed = gross * 0.02
            elif gross < 190.0:
                assessed = 0.95
            else:
                assessed = gross * 0.005
        else:  # pragma: no cover - guarded by _fee_policies
            raise RuntimeError(f"unknown fee policy: {self.kind}")
        # A flat fee can exceed a very small trade.  A fee cannot consume more
        # than the trade's gross amount, or the ledger would create negative
        # proceeds.
        return min(gross, assessed)

    def net(self, gross: float) -> float:
        return gross - self.fee(gross)

    def effective_rate(self, gross: float) -> float:
        if gross <= 0:
            return 0.0
        return self.fee(gross) / gross


@dataclass(frozen=True)
class _Sample:
    observed_at: str
    when: datetime
    price: float
    fomo_ratio_lower: Optional[float]
    fomo_ratio_upper: Optional[float]
    fomo_holders: Optional[float]
    confirmation: bool
    price_estimated: bool


def _number(value: Any, field: str, *, positive: bool = False,
            nonnegative: bool = False) -> float:
    """Return a finite numeric value, rejecting bools and numeric strings."""

    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    if positive and result <= 0:
        raise ValueError(f"{field} must be greater than zero")
    if nonnegative and result < 0:
        raise ValueError(f"{field} must be nonnegative")
    return result


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("observed_at must be a timezone-aware ISO timestamp")
    text = value
    # datetime.fromisoformat gained direct ``Z`` support after Python 3.9.
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("observed_at must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone offset")
    return parsed


def _optional_number(value: Any, field: str, *, nonnegative: bool = False) -> Optional[float]:
    if value is None:
        return None
    return _number(value, field, nonnegative=nonnegative)


def _parse_samples(samples: Iterable[Mapping[str, Any]]) -> List[_Sample]:
    if isinstance(samples, (str, bytes, bytearray)) or not isinstance(samples, Iterable):
        raise ValueError("samples must be an iterable of mappings")

    parsed: List[_Sample] = []
    seen = set()
    for index, raw in enumerate(samples):
        if not isinstance(raw, Mapping):
            raise ValueError(f"sample {index} must be a mapping")
        observed_at = raw.get("observed_at")
        when = _timestamp(observed_at)
        instant = when.astimezone(timezone.utc)
        if instant in seen:
            raise ValueError("duplicate observed_at timestamp")
        seen.add(instant)

        price = _number(raw.get("price"), "price", positive=True)
        lower = _optional_number(raw.get("fomo_ratio_lower"), "fomo_ratio_lower", nonnegative=True)
        upper = _optional_number(raw.get("fomo_ratio_upper"), "fomo_ratio_upper", nonnegative=True)
        if lower is not None and lower > 100:
            raise ValueError("fomo_ratio_lower must be at most 100")
        if upper is not None and upper > 100:
            raise ValueError("fomo_ratio_upper must be at most 100")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("fomo_ratio_lower cannot exceed fomo_ratio_upper")
        holders = _optional_number(raw.get("fomo_holders"), "fomo_holders", nonnegative=True)

        confirmation = raw.get("confirmation", True)
        if not isinstance(confirmation, bool):
            raise ValueError("confirmation must be a boolean")
        price_estimated = raw.get("price_estimated", False)
        if not isinstance(price_estimated, bool):
            raise ValueError("price_estimated must be a boolean")

        parsed.append(_Sample(
            observed_at=observed_at,
            when=when,
            price=price,
            fomo_ratio_lower=lower,
            fomo_ratio_upper=upper,
            fomo_holders=holders,
            confirmation=confirmation,
            price_estimated=price_estimated,
        ))

    if not parsed:
        raise ValueError("samples must contain at least one observation")
    parsed.sort(key=lambda sample: sample.when.astimezone(timezone.utc))
    return parsed


def _ratio_state(sample: _Sample) -> Optional[bool]:
    """Return True/False when the ratio condition is known, otherwise None.

    A range that crosses 15% is deliberately treated as unknown.  A lone upper
    bound below 15% is sufficient evidence for the low-ratio condition, while a
    lone lower bound at or above 15% is sufficient evidence that it is false.
    """

    lower = sample.fomo_ratio_lower
    upper = sample.fomo_ratio_upper
    if upper is not None and upper < _RATIO_EXIT:
        return True
    if lower is not None and lower >= _RATIO_EXIT:
        return False
    return None


class _Ledger:
    """A single model's cash, token, and mark-to-market ledger."""

    def __init__(self, model_id: str, kind: str, principal: float,
                 buy_fee: _FeePolicy, sell_fee: _FeePolicy, entry: _Sample,
                 target_net: Optional[float] = None):
        self.model_id = model_id
        self.kind = kind
        self.principal = principal
        self.buy_fee = buy_fee
        self.sell_fee = sell_fee
        self.entry = entry
        self.entry_price = entry.price
        self.target_net = target_net
        self.buy_fee_paid = self.buy_fee.fee(principal)
        invested_gross = principal - self.buy_fee_paid
        self.initial_units = invested_gross / entry.price
        self.units = self.initial_units
        self.cash = 0.0
        self.sell_fee_paid = 0.0
        self.trades: List[Dict[str, Any]] = [{
            "time": entry.observed_at,
            "observed_at": entry.observed_at,
            "type": "buy",
            "price": entry.price,
            "gross": invested_gross,
            "fee": self.buy_fee_paid,
            "net": principal,
            "cash_delta": -principal,
            "tokens": self.initial_units,
            "fraction_of_initial": 1.0,
            "fraction_of_remaining": 1.0,
            "effective_fee_rate": self.buy_fee.effective_rate(principal),
            "price_estimated": entry.price_estimated,
        }]

        self.triggered = False
        self.terminal = False
        self.stop_triggered = False
        self.terminal_reason: Optional[str] = None
        self.trigger_observed_at: Optional[str] = None
        self.trigger_price: Optional[float] = None
        self._target_unreachable = False

        # Hold-model state.
        self._peak_holders: Optional[float] = None
        self._holder_streak = 0
        self._ratio_streak = 0

        # Recover2 state.
        self._principal_recovered = False
        self._recovery_index: Optional[int] = None
        self._half_sold = False

        # Drawdown starts at the post-buy, hypothetical-liquidation value.
        initial_net = self._net_value(entry.price)
        self._peak_net_value = initial_net
        self._max_drawdown = 0.0

    def _net_value(self, price: float) -> float:
        gross = self.units * price
        return self.cash + self.sell_fee.net(gross)

    def _stop_if_needed(self, sample: _Sample) -> bool:
        if sample.price > self.entry_price * _STOP_MULTIPLE:
            return False
        if self.units > 0:
            self._sell(sample, self.units, "stop_loss")
        self.stop_triggered = True
        self.terminal = True
        self.terminal_reason = "stop_loss"
        self.trigger_observed_at = sample.observed_at
        self.trigger_price = sample.price
        return True

    def _sell(self, sample: _Sample, tokens: float, reason: str) -> float:
        if tokens <= 0 or self.units <= 0:
            return 0.0
        # Small floating-point overages can occur when a target is exactly all
        # remaining tokens.  Never create tokens by rounding beyond the ledger.
        tokens = min(tokens, self.units)
        before = self.units
        gross = tokens * sample.price
        fee = self.sell_fee.fee(gross)
        net = gross - fee
        self.units -= tokens
        if abs(self.units) <= max(1e-15, self.initial_units * 1e-15):
            self.units = 0.0
        self.cash += net
        self.sell_fee_paid += fee
        self.trades.append({
            "time": sample.observed_at,
            "observed_at": sample.observed_at,
            "type": "sell",
            "price": sample.price,
            "gross": gross,
            "fee": fee,
            "net": net,
            "cash_delta": net,
            "tokens": tokens,
            "fraction_of_initial": tokens / self.initial_units,
            "fraction_of_remaining": tokens / before,
            "effective_fee_rate": self.sell_fee.effective_rate(gross),
            "price_estimated": sample.price_estimated,
            "reason": reason,
        })
        return net

    def _sell_all(self, sample: _Sample, reason: str) -> float:
        return self._sell(sample, self.units, reason)

    def _sell_fraction_of_remaining(self, sample: _Sample, fraction: float,
                                    reason: str) -> float:
        fraction = min(max(fraction, 0.0), 1.0)
        return self._sell(sample, self.units * fraction, reason)

    def _sell_target(self, sample: _Sample, target_net: float, reason: str) -> bool:
        if self.units <= 0 or target_net <= 0:
            return False
        available_gross = self.units * sample.price
        available_net = self.sell_fee.net(available_gross)
        if available_net < target_net and not math.isclose(
                available_net, target_net, rel_tol=1e-12, abs_tol=1e-12):
            self._target_unreachable = True
            return False

        # The platform schedules include flat-fee tiers, so the inverse is
        # calculated against the monotonic net(gross) function rather than a
        # single percentage.  The upper bound is the gross value of the
        # remaining tokens at this observation.
        low = 0.0
        high = available_gross
        for _ in range(100):
            middle = (low + high) / 2.0
            if self.sell_fee.net(middle) < target_net:
                low = middle
            else:
                high = middle
        gross_required = high
        tokens = gross_required / sample.price
        fraction = tokens / self.units
        if fraction > 1.0 + 1e-12:
            self._target_unreachable = True
            return False
        self._sell(sample, min(tokens, self.units), reason)
        return True

    def _record_mark(self, price: float) -> None:
        value = self._net_value(price)
        if value > self._peak_net_value:
            self._peak_net_value = value
        drawdown = self._peak_net_value - value
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown

    def _process_hold(self, sample: _Sample) -> None:
        if self.terminal or self._stop_if_needed(sample):
            return
        # Only a confirmed sample can advance either evidence streak.  An
        # unconfirmed sample is not a contrary observation, so it leaves the
        # previous confirmed streak untouched.
        if not sample.confirmation:
            return

        if sample.fomo_holders is None:
            self._holder_streak = 0
        else:
            if self._peak_holders is None or sample.fomo_holders > self._peak_holders:
                self._peak_holders = sample.fomo_holders
            holder_low = (
                self._peak_holders > 0
                and sample.fomo_holders < self._peak_holders * _HOLDER_RETENTION
            )
            self._holder_streak = self._holder_streak + 1 if holder_low else 0

        ratio = _ratio_state(sample)
        if ratio is True:
            self._ratio_streak += 1
        else:
            # Missing and uncertain bounds both reset ratio evidence.  A known
            # ratio at or above 15% also breaks a consecutive low-ratio run.
            self._ratio_streak = 0

        if self._holder_streak >= 2 or self._ratio_streak >= 2:
            self._sell_all(sample, "fomo_retention")
            self.triggered = True
            self.terminal = True
            self.terminal_reason = "fomo_retention"
            self.trigger_observed_at = sample.observed_at
            self.trigger_price = sample.price

    def _process_recover2(self, sample: _Sample, index: int) -> None:
        if self.terminal or self._stop_if_needed(sample):
            return
        multiple = sample.price / self.entry_price
        if not self._principal_recovered:
            if multiple >= _RECOVER_MULTIPLE and self._sell_target(sample, self.principal, "recover_principal"):
                self._principal_recovered = True
                self.triggered = True
                self._recovery_index = index
                self.trigger_observed_at = sample.observed_at
                self.trigger_price = sample.price
            return

        # Even if the recovery observation leaps directly beyond 4x, the 4x
        # sale is required on a later observed sample.
        if not self._half_sold and self._recovery_index is not None:
            if index > self._recovery_index and multiple >= _HALF_MULTIPLE:
                self._sell_fraction_of_remaining(sample, 0.5, "halve_at_4x")
                self._half_sold = True

    def _process_runner(self, sample: _Sample) -> None:
        if self.terminal or self._stop_if_needed(sample):
            return
        if self.triggered:
            return
        multiple = sample.price / self.entry_price
        if multiple >= _RUNNER_MULTIPLE and self._sell_target(sample, self.target_net, "runner_recover"):
            self.triggered = True
            self.trigger_observed_at = sample.observed_at
            self.trigger_price = sample.price

    def process(self, sample: _Sample, index: int) -> None:
        if self.kind == "hold":
            self._process_hold(sample)
        elif self.kind == "recover2":
            self._process_recover2(sample, index)
        elif self.kind == "runner":
            self._process_runner(sample)
        else:  # pragma: no cover - guarded by this module's construction
            raise RuntimeError(f"unknown model kind: {self.kind}")
        self._record_mark(sample.price)

    def result(self, latest: _Sample) -> Dict[str, Any]:
        remaining_gross = self.units * latest.price
        hypothetical_fee = self.sell_fee.fee(remaining_gross)
        net_liquidation = self.cash + remaining_gross - hypothetical_fee
        gross_equity = self.cash + remaining_gross
        paid_fees = self.buy_fee_paid + self.sell_fee_paid
        remaining_fraction = (
            self.units / self.initial_units if self.initial_units > 0 else 0.0
        )
        max_drawdown_pct = (
            self._max_drawdown / self._peak_net_value * 100.0
            if self._peak_net_value > 0 else 0.0
        )
        if self.stop_triggered:
            status = "stopped"
            reason = "stop_loss"
        elif self.kind == "hold" and self.terminal_reason == "fomo_retention":
            status = "exited"
            reason = "fomo_retention"
        elif self.kind == "recover2":
            if self._half_sold:
                status = "running"
                reason = "principal_recovered_then_halved"
            elif self._principal_recovered:
                status = "running"
                reason = "principal_recovered"
            elif self._target_unreachable:
                status = "holding"
                reason = "principal_recovery_unreachable"
            else:
                status = "holding"
                reason = "no_2x_observed"
        elif self.kind == "runner":
            if self.triggered:
                status = "running"
                reason = "runner_recover_110" if self.target_net > self.principal else "runner_recover_100"
            elif self._target_unreachable:
                status = "holding"
                reason = "runner_recovery_unreachable"
            else:
                status = "holding"
                reason = "no_2_5x_observed"
        else:
            status = "holding"
            reason = "no_exit_trigger"

        result: Dict[str, Any] = {
            "id": self.model_id,
            "status": status,
            "reason": reason,
            "reason_code": reason,
            "triggered": self.triggered,
            "terminal": self.terminal,
            "stop_triggered": self.stop_triggered,
            "entry_price": self.entry_price,
            "latest_price": latest.price,
            "latest_multiple": latest.price / self.entry_price,
            "latest_observed_at": latest.observed_at,
            "latest_price_estimated": latest.price_estimated,
            "entry_price_estimated": self.entry.price_estimated,
            "trigger_observed_at": self.trigger_observed_at,
            "trigger_price": self.trigger_price,
            "realized_cash": self.cash,
            "cash": self.cash,
            "remaining_units": self.units,
            "remaining_token_fraction": remaining_fraction,
            "remaining_token_percent": remaining_fraction * 100.0,
            "remaining_percent": remaining_fraction * 100.0,
            "remaining_gross_value": remaining_gross,
            "gross_equity": gross_equity,
            "buy_fee_paid": self.buy_fee_paid,
            "sell_fee_paid": self.sell_fee_paid,
            "paid_fees": paid_fees,
            "hypothetical_remaining_liquidation_fee": hypothetical_fee,
            "liquidation_fee": hypothetical_fee,
            "net_liquidation_value": net_liquidation,
            "net_value": net_liquidation,
            "pnl": net_liquidation - self.principal,
            "return_pct": (net_liquidation - self.principal) / self.principal * 100.0,
            "net_multiple": net_liquidation / self.principal,
            "peak_net_value": self._peak_net_value,
            "max_net_value_drawdown": self._max_drawdown,
            "max_net_value_drawdown_pct": max_drawdown_pct,
            "max_drawdown": self._max_drawdown,
            "trades": self.trades,
        }
        if self.kind == "recover2":
            result.update({
                "principal_recovered": self._principal_recovered,
                "half_sold": self._half_sold,
                "target_net": self.principal,
            })
        elif self.kind == "runner":
            result.update({"target_net": self.target_net})
        elif self.kind == "hold":
            result.update({
                "holder_streak": self._holder_streak,
                "ratio_streak": self._ratio_streak,
                "sampled_peak_holders": self._peak_holders,
            })
        return result


def _assert_finite_output(value: Any, path: str = "result") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} is not finite")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _assert_finite_output(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_finite_output(child, f"{path}[{index}]")


def _fee_policies(fee_schedule: Optional[str], buy_fee: float,
                  sell_fee: float) -> Tuple[_FeePolicy, _FeePolicy]:
    if fee_schedule is not None and fee_schedule not in ("solana", "evm"):
        raise ValueError("fee_schedule must be None, 'solana', or 'evm'")

    buy_fee = _number(buy_fee, "buy_fee", nonnegative=True)
    sell_fee = _number(sell_fee, "sell_fee", nonnegative=True)
    if buy_fee >= 1.0 or sell_fee >= 1.0:
        raise ValueError("buy_fee and sell_fee must be less than one")

    if fee_schedule is not None:
        policy = _FeePolicy(fee_schedule)
        return policy, policy
    return _FeePolicy("percentage", buy_fee), _FeePolicy("percentage", sell_fee)


def simulate(samples: Iterable[Mapping[str, Any]], principal: float = 100.0,
             buy_fee: float = 0.06, sell_fee: float = 0.06,
             fee_schedule: Optional[str] = None) -> Dict[str, Any]:
    """Replay the exit models over the supplied observations.

    ``samples[0]`` after timestamp sorting is the already-qualified entry
    observation.  No price is interpolated: every buy and sell in the returned
    ledgers uses one of the supplied sample prices.
    """

    principal = _number(principal, "principal", positive=True)
    buy_policy, sell_policy = _fee_policies(fee_schedule, buy_fee, sell_fee)

    ordered = _parse_samples(samples)
    entry = ordered[0]
    latest = ordered[-1]
    ledgers = [
        _Ledger("hold", "hold", principal, buy_policy, sell_policy, entry),
        _Ledger("recover2", "recover2", principal, buy_policy, sell_policy, entry),
        _Ledger("runner25", "runner", principal, buy_policy, sell_policy, entry,
                target_net=principal),
        _Ledger("runner25_110", "runner", principal, buy_policy, sell_policy, entry,
                target_net=principal * 1.10),
    ]

    for index, sample in enumerate(ordered):
        for ledger in ledgers:
            ledger.process(sample, index)

    result: Dict[str, Any] = {
        "entry_price": entry.price,
        "latest_price": latest.price,
        "multiple": latest.price / entry.price,
        "entry_observed_at": entry.observed_at,
        "observed_at": latest.observed_at,
        "entry_price_estimated": entry.price_estimated,
        "latest_price_estimated": latest.price_estimated,
        "sample_count": len(ordered),
        "models": [ledger.result(latest) for ledger in ledgers],
    }
    _assert_finite_output(result)
    return result


__all__ = ["simulate"]
