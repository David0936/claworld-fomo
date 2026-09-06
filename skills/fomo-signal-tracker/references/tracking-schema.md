# Tracking schema

Use local time with timezone in every timestamp. Keep numeric source values as displayed and add normalized calculations separately.

## Watchlist row

| Field | Meaning |
|---|---|
| Token / chain / CA | Stable identity; CA must be complete |
| Candidate / washout / rebound time | The three independent entry-confirmation observations |
| Confirmed entry price / market cap | Rebound baseline for return calculations |
| Fomo ratio | Windvane value at first alert |
| Trend state | Confirmed lower-close streak, reset by a confirmed recovery |
| Sampled high | Highest verified price recorded after alert |
| Status | `watch`, `strong`, or `stopped` |
| Log path | Token-specific Markdown record |

## Sample row

| Time | Price | MC | Liquidity | 24h volume | 24h change | Holders | Fomo holders | Fomo ratio | Top 10 | Baseline multiple | High drawdown | Source status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|

Use `source status` to distinguish verified readings, delayed values, estimates, and unavailable fields. Do not silently carry forward a stale value.

## Event ledger

Record threshold events separately so alerts are not repeated:

- first hit;
- 2x, 3x, 5x, and 10x crossings;
- 25% drawdown from sampled high;
- Fomo ratio crossing 20% upward or 15% downward;
- second consecutive confirmed lower close, which marks an unrecovered trend break.

## Strategy ledger

For every active strategy record: starting capital, entry price, buy/sell fee assumptions, realized cash, remaining token fraction, remaining mark-to-market value, total net value, trigger reason, and whether the calculation used an observed or estimated price. Keep fee-free and fee-aware results distinct.

## Daily review

State the observation window and missing samples. Report first/last sample, high, low, maximum baseline multiple, maximum drawdown, Fomo-ratio change, and whether the first-alert point was favorable based only on subsequently observed samples. Do not describe an unobserved interval as the true intraday high or low.
