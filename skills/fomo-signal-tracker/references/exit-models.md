# Fee-aware exit models

These are parallel hypothetical ledgers for comparing exit rules. They are not trading instructions and must never submit a transaction.

## Shared calculation

Let `P` be starting cash, `m` the price multiple from entry, `fb` the buy-side all-in cost rate, `fs` the sell-side all-in cost rate, and `k` the desired net cash as a multiple of `P`.

- Gross marked position: `P × (1 - fb) × m`
- Token fraction to sell: `q = k / ((1 - fb) × m × (1 - fs))`
- If `q > 1`, the target cannot be recovered at that price.

Prefer observed execution quotes. Otherwise label fee, gas, tax, and slippage assumptions explicitly. Do not silently combine fee-free and fee-aware results.

## Model 1 — Fomo-retention hold

Hold the full position until either condition persists for two consecutive samples:

- current Fomo holder count is below 20% of its sampled peak; or
- Fomo holding ratio is below 15%.

Use holder-count retention as the primary interpretation of “buyers below 20%.” A holding ratio below 20% is not a valid default exit because an entry signal may begin between 15% and 20%. Neither metric proves that everyone sold; it is only a crowd-participation proxy.

## Model 2 — Recover principal, then keep halving

- At the first observed crossing of 2×, sell the fee-aware fraction required to recover `1.0P` net.
- At the next observed 4×, sell half of the remaining tokens.
- At each later observed doubling level (8×, 16×, 32×, and onward), sell half of the then-remaining tokens again.
- Continue marking the remainder without inventing fills between samples.

With `fb = fs = 12%`, selling half at 2× nets only `0.7744P`; recovering `1.0P` requires selling 64.57% of tokens.

## Model 3 — Small-bankroll runner

At the first observed crossing of 2.5×, calculate both variants:

- Recover `1.0P` net and leave the remainder running.
- Recover `1.1P` net and leave the remainder running.

For `P = $50` and `fb = fs = 12%`, the gross position at 2.5× is `$110`. Recovering `$50` requires selling 51.65%; recovering `$55` requires selling 56.82%, leaving 43.18% of the tokens.

## Required comparison output

For every model report trigger status, realized net cash, remaining token percentage, remaining gross marked value, total net value, drawdown from the model's peak, and whether the price was directly observed or estimated. Note inadequate liquidity or unusually high slippage separately.
