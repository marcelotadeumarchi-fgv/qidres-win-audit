# Pre-Specification Protocol II — The Cancellation Channel

**Instrument:** WIN (mini Bovespa index future), `security_id = 200001274203`
**Author:** Marcelo Tadeu Marchi
**Status:** FIXED BEFORE THE FALSIFICATION TESTS ARE RUN. The finding itself is
EXPLORATORY and is disclosed as such in §1.
**Date fixed:** 2026-10-01

---

## 1. Honest status of the finding — read this first

The result below was found by **exploratory search**, not by a pre-registered
test. Specifically:

- It emerged after roughly 80 specifications had been estimated in this project,
  across the window-level protocol, the event study, the substitution test and
  the cancellation test.
- The direction of the question (reaction rather than prediction) was proposed
  by the author only after the prediction tests had returned nulls.
- **The hold-out days (2025-09-29, 2025-09-30) are BURNED for this finding.**
  They were included in the exploratory run. No clean hold-out remains in this
  sample.

**Consequence:** nothing in this protocol can make the finding confirmatory on
this data. What this protocol can do is (a) fix the specification so it cannot
drift, (b) pre-specify FALSIFICATION tests that have not yet been run and that
could kill the result, and (c) define a genuinely confirmatory design for data
not yet in hand.

A finding that survives §4 is *robust exploratory evidence*. It is not
confirmed, and must not be described as confirmed in the thesis.

---

## 2. The finding, stated exactly

**Mechanism.** Liquidity providers withdraw quotes from the side exposed to
informed flow, and that withdrawal carries information beyond the flow itself.

**Signal (frozen).** On 1-second windows, restricted to 09:00–18:00 BRT:

```
cancel_imb(t) = ( wd_vol_bid(t) − wd_vol_ask(t) ) / ( wd_vol_bid(t) + wd_vol_ask(t) )
```

where `wd_vol_side` is the volume of orders cancelled **while resting at the
touch** on that side — gross, not netted against additions.

**Specification (frozen).**

```
R(t → t+h) = α + β·cancel_imb(t) + γ₁·agr_imb(t) + γ₂·OBI(t) + γ₃·add_imb(t)
             + Σ_j δ_j·Hour_j + ε
```

- `R` = forward **midpoint** change in basis points (NOT microprice — the
  midpoint is not mechanically linked to the book-size variables)
- `OBI(t)` = `close_microprice(t) − close_midprice(t)`, identically
  `(spread/2)·(Q_b−Q_a)/(Q_b+Q_a)`
- `agr_imb` = sweep-count imbalance (aggressive-order direction)
- `add_imb` = touch-addition volume imbalance
- OLS, Newey-West HAC, `maxlags = 5`
- **Primary horizons: h = 5 s and h = 10 s.** No other horizon is primary.

**Observed values (exploratory, for the record):**

| h | β | z | p | per-day | 1-sd effect |
|---|---:|---:|---:|---|---:|
| 5 s | −0.0173 | −4.79 | 1.6e−6 | negative 9/12 days, t=−3.79 | −0.113 pt |
| 10 s | −0.0186 | −3.94 | 8.1e−5 | negative 9/12 days, t=−2.67 | −0.121 pt |

Already checked: survives depth-level and spread controls, survives a lagged
return control, and survives Bonferroni over all ~80 specifications estimated
in this project (threshold 6e−4).

**Economic magnitude is small and is not a trading claim.** A one-standard-
deviation cancellation imbalance moves the midpoint 0.11–0.12 index points
against a 5.0 point crossing cost. The claim is informational, not economic.

---

## 3. Declared interpretation and the sign that would refute it

`cancel_imb > 0` means **bid-side** withdrawal dominates.
Declared prediction: **β < 0** — pulling bids precedes a price fall.

A significant **β > 0** refutes the mechanism and must be reported as refuting
it, not reinterpreted as an alternative story.

---

## 4. FALSIFICATION TESTS — fixed before running

Each test below can kill the finding. All are specified here before execution.
The result of every one is reported, including those that pass.

### F1 — Deep-book placebo (the decisive one)

Cancellations **away from the touch** (not at the best price) cannot be a
defensive response to being picked off, because those orders are not exposed.
Run the identical specification with `cancel_imb_deep` built from non-touch
cancellation volume.

> **Kill condition:** if the deep-book placebo shows an effect of comparable
> magnitude and significance, the result is not about informed liquidity
> provision and the finding is withdrawn.

This is the analogue of the join-at-best placebo that correctly killed the
microprice event-study result.

### F2 — Time-shuffle placebo

Randomly permute `cancel_imb` **within each trading day**, preserving its
marginal distribution and the day's other series. Repeat 500 times.

> **Kill condition:** if |β| from the real series does not exceed the 99th
> percentile of the shuffled distribution, the effect is an artifact of the
> series' marginal properties.

### F3 — Side symmetry

Estimate separately for bid-side withdrawal (`cancel_imb > 0`) and ask-side
withdrawal (`cancel_imb < 0`). The mechanism is symmetric, so both halves must
carry the same sign of effect.

> **Kill condition:** if the effect loads on only one side, it is more likely a
> directional artifact of the sample's price path than a liquidity mechanism.

### F4 — Reverse causality

Regress the **past** return `R(t−h → t)` on `cancel_imb(t)` with the same
controls. Cancellation responding to realised moves is expected and benign; the
forward effect must be the stronger of the two.

> **Kill condition:** if the backward coefficient is as large as or larger than
> the forward one, the finding is response, not anticipation.

### F5 — Adverse-selection conditioning

Split by the prevailing spread (exactly 1 tick vs wider) and by realised
volatility (above/below daily median). Adverse selection is higher in wide-spread
and high-volatility states, so the effect should be **stronger** there.

> This test cannot kill the finding on its own; a flat profile weakens the
> economic interpretation without refuting the statistical result, and is
> reported that way.

### F6 — Horizon profile

Already observed: dies at 1 s (mechanical), peaks 5–10 s, gone by 30 s. Reported
as a shape, not as six independent tests.

---

## 5. Confirmatory design for data not yet in hand

This sample cannot confirm the finding. When further data becomes available,
the confirmatory test is:

- **Specification:** §2, unchanged, with no re-tuning of any kind.
- **Primary horizon:** h = 5 s only. One test.
- **Threshold:** p < 0.01, with β < 0.
- **Sample:** months not used in this study, or a different large-tick B3
  contract (WDO is the natural second instrument).
- **Pre-registration:** this file's SHA256, recorded before the new data is
  touched.

A cross-instrument design would be stronger than more months of WIN, because it
permits the test the single-instrument design cannot make: **the effect should be
larger where the tick binds harder.** That is the claim that would turn a case
study into a general result.

---

## 6. What is reported regardless of outcome

1. All six falsification tests, including passes.
2. The total specification count for the project (~80 at the time of writing).
3. That the hold-out days are burned for this finding.
4. The economic magnitude alongside the statistical significance, so the reader
   cannot mistake one for the other.
5. A withdrawal of the finding if F1, F2, F3 or F4 triggers its kill condition.

---

## 7. Relation to the rest of the thesis

This does not rescue the undercutting metric. The price-channel results stand:
undercutting does not predict returns, and the preliminary §4/§7 findings are
artifacts (auction windows, mis-measured spread, window-mean overlap).

The claim this protocol governs is different and complementary:

> The tick size does not eliminate informed liquidity provision — it relocates
> it from the price dimension to the cancellation-timing dimension.

If it survives §4, it is the positive finding of the thesis, and the undercutting
null becomes its necessary setup rather than its conclusion.

---

*Amendments only by dated append, before the affected test is run.*
