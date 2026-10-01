# Pre-Specification Protocol — Undercutting Signal, Revised Design

**Instrument:** WIN (mini Bovespa index future), `security_id = 200001274203`
**Author:** Marcelo Tadeu Marchi
**Status:** FIXED BEFORE ESTIMATION. No result from the revised design has been observed at the time of writing.
**Date fixed:** 2026-09-30

> Written in English as a working document. It maps section-by-section onto the thesis
> and is ready to translate for §3 (Metodologia) and §6 (Riscos e plano de mitigação).

---

## 0. Why this document exists

The preliminary results (thesis §4, §7) have been shown to be artifacts of two
implementation choices, not findings. See `SESSAO_COMPLETA.md` §7 for the full
decomposition. Having observed a null on the corrected measure, any further search
over specifications is **post-hoc** and inflates Type I error.

This protocol fixes the revised design *before* estimation so that the next result is
confirmatory rather than exploratory. Every choice below is justified by theory or by a
measurement already made on the data — **none is justified by its effect on the outcome,
because no outcome has been seen.**

---

## 1. What is already established (not under test)

These are settled by the validation in `undercutting_valida.py` and are **inputs**, not
hypotheses:

| Fact | Evidence |
|---|---|
| The feed is MBO; `md_price_level` is always NULL | 100% null, full day |
| `order_id` present on 21,324,400 of 21,324,402 order entries | full day, 2 nulls |
| `order_id` absent from 100% of 3,915,507 trade entries | full day |
| Change events are **all** size-only decreases (partial fills), never reprices | 2.49M events, 3 days, zero exceptions |
| Verified undercutting = 1,930,208 (1.33% of new orders); strict = 1,505,327 (1.03%) | 355,666,087 events, 12 days |
| The spread is 1 tick 98.95% of clock time | time-weighted, 12 days |
| The thesis Appendix A `avg_spread_pts` is **not** the spread (median 3,367 pts vs true 5.29) | verbatim reproduction |
| The thesis large-tick filter is non-binding (drops 0 of 1,308 windows) | verbatim reproduction |

---

## 2. Sample definition (fixed)

- **Period:** 2025-09-15 to 2025-09-30, 12 trading days. 2025-09-20 is a Saturday with no
  trading events and is excluded automatically.
- **Session:** 09:00:00 to 18:00:00 BRT inclusive. **Auction and after-hours windows are
  excluded.** This is not a tuning choice: those 83 windows are 6.0% of observations but
  carry 94.7% of the sum of squares in the preliminary signal, and there is no continuous
  trading in them, so the midpoint and spread are not defined in the usual sense.
- **No large-tick filter on the window mean.** The thesis filter is non-binding and its
  input is mis-measured. The regime restriction, where used, is specified in §5.3.

---

## 3. Measure construction (fixed)

All quantities come from the full order-book replay in `undercutting_valida.py`
(persistent state across the session, deletes and modifications applied, verified by
`narrowed_vs_undercut_delta = 0` and `neg_size_ticks = 0`).

### 3.1 Numerator — strict undercutting, volume-weighted

An order qualifies as a **strict undercut** when all of the following hold at arrival,
evaluated against the book *before* the order is inserted:

1. it is a New order (`md_update_action = 0`);
2. both sides of the book are non-empty;
3. for a bid: `best_bid < price < best_ask` (strictly inside the spread, therefore
   non-marketable); mirror for an ask;
4. it did **not** arrive within `JANELA_RESIDUO = 3` events of a trade whose inferred
   aggressor was on the **same side** — i.e. it is not possibly the resting residual of a
   partly-filled aggressive order.

Condition 4 is the conservative choice adopted in this design. Its cost is known:
22.0% of verified undercuts are excluded. It cannot be refined further because the feed
carries no `order_id` on trades.

**Volume weighting.** The measure is in **contracts**, not order counts:

```
U_bid(t) = Σ size of strict undercutting bids in window t
U_ask(t) = Σ size of strict undercutting asks in window t
```

Justification, fixed in advance: equation (1) of the thesis defines QID over *volume*
("Volume de melhorias de cotação"), while the Appendix A code uses `pl.count()`. The
median new order is **1 lot** and the largest 1% of orders carry **20.4%** of all
contracts, so the two are materially different measures. This change makes the
implementation match the stated definition.

### 3.2 Denominator — passive order volume

```
P(t) = Σ size of all New limit orders in window t   (both sides)
```

Justification: equation (1) says "Volume de ordens passivas". The Appendix A denominator
(`pl.count()` over *all* messages, including trades and deletes) does not match the
stated definition and is ~8× larger.

### 3.3 Signal — signed, as in equation (3)

```
QID_signed(t) = ( U_bid(t) − U_ask(t) ) / P(t)
```

The **unsigned** variant `(U_bid + U_ask)/P` is retained only as a reported secondary
(§5.2). The primary measure is signed because the hypothesis is directional and because
an intensity measure is theoretically ill-posed as a predictor of a signed return.

### 3.4 Orthogonalisation

**Not applied as a separate step.** Per §6.4 of the thesis, the two-stage residualisation
is replaced by direct control of the relative spread inside the predictive regression.
This is more transparent under recursive estimation and avoids residualising against a
mis-measured quantity. The control is the **true** relative spread:

```
S(t) = avg_spread_pts(t) / avg_midprice(t)
```

where `avg_spread_pts` is the event-weighted mean of `best_ask − best_bid` in points.

### 3.5 Target — microprice return

```
MP(t) = ( bid_size·best_ask + ask_size·best_bid ) / ( bid_size + ask_size )
R(t→t+h) = 10000 · ( MP(t+h) − MP(t) ) / MP(t)      [basis points]
```

Justification, fixed in advance: the L1 midpoint is adequate at 5 minutes (typical move
17.8 ticks; only 6.5% of windows move less than one tick) but becomes dominated by the
2.5-point quantisation grid below ~30 seconds. The microprice is continuous and is the
standard target in large-tick markets.

**Contemporaneity guard.** The signal is measured over window `t`; the return is measured
from the **close of window `t`** to the close of window `t+h`. The windows do not overlap.
This addresses §6.2.

---

## 4. Primary hypothesis and specification

### H1 (primary, directional)

> Signed strict undercutting volume imbalance predicts the sign and magnitude of the
> subsequent microprice return at the 1-minute horizon.

**Primary specification — ONE test, fixed:**

```
R(t → t+1min) = α + β·QID_signed(t) + γ·S(t) + Σ_j δ_j·Hour_j + ε
```

- Window length: **1 minute**
- Horizon: **h = 1 window (1 minute)**
- Estimation: OLS, Newey-West HAC, `maxlags = 2`
- `Hour_j`: hour-of-day dummies (intraday seasonality, §6.4)
- Signal winsorised at 1%/99% **using training-window percentiles only**

**Primary statistic:** the HAC t-statistic on β.
**Primary decision rule:** H1 is supported only if **p < 0.01** two-sided on β *and* the
sign of β is stable across all three horizons in §5.1.

The 1% threshold, rather than 5%, is fixed in advance to absorb the specification search
already conducted. It is not negotiable after the fact.

### Expected sign — declared in advance

Two mechanisms are defensible and the thesis has, at different points, assumed each:

- **Barardehi–Dixon–Liu mechanism:** informed urgency causes liquidity providers to
  *withdraw* from price competition, so undercutting on the side facing informed flow
  *falls* before an adverse move → **β < 0**.
- **Queue-urgency mechanism (thesis §6.1):** informed participants themselves compete for
  priority, so undercutting on the informed side *rises* before the move → **β > 0**.

**We declare β < 0 (the reference-paper mechanism) as the directional prior.** A
significant result with β > 0 will be reported as *contradicting* the reference
mechanism, not as confirming an alternative, since the alternative was adopted
post-hoc in the preliminary stage.

---

## 5. Secondary and exploratory analyses (labelled as such, never promoted)

### 5.1 Horizon decay (confirmatory of §4.1's theory)

Same specification at **h = 1, 5, 15 minutes**. The theory predicts monotone decay in
|β| and R². Reported as a set; no individual p-value is interpreted in isolation.

### 5.2 Measure variants (robustness, not additional tests)

Reported side by side, all with the primary specification:

| variant | purpose |
|---|---|
| strict vs all verified undercuts | isolates the residual exclusion |
| volume-weighted vs count-weighted | isolates §3.1's weighting change |
| passive-volume vs all-message denominator | isolates §3.2's denominator change |
| microprice vs L1 midpoint target | isolates §3.5's target change |

### 5.3 Regime conditioning (exploratory)

Restricting to windows with `frac_wide > 2%` (share of clock time with spread > 1 tick)
keeps 166 of 1,308 windows (drops 87.3%). This is the *real* version of §2.2's large-tick
conditioning. It is **exploratory** because the threshold is arbitrary and the subsample
is small; it will be reported with its Heckman-style selection caveat (§6.3) and will not
be used to support H1.

### 5.4 Conditional-variance specification (§7's |R| target)

```
|R(t → t+h)| = α + β·QID_signed(t) + γ·S(t) + Σ_j δ_j·Hour_j + ε
```

**Required reporting rule:** R² and Clark-West for this specification **must** be computed
against a restricted model that already contains the hour dummies. Against a mean-only
benchmark the preliminary design reports 22.82%; against the dummies benchmark it reports
−0.02%. Only the second is attributable to the signal. The mean-only figure must not be
quoted.

---

## 6. Out-of-sample protocol

- **Scheme:** expanding window, chronological, as in Appendix C.
- **Warm-up:** 500 observations, fixed ex ante, with sensitivity reported over
  {300, 500, 700, 900} per §6.2. The warm-up will **not** be chosen by inspecting the
  learning curve.
- **Benchmarks:** two, always both reported — (a) historical mean, (b) mean + hour
  dummies + relative spread. **(b) is the benchmark of record**; (a) is reported only for
  comparability with the preliminary tables.
- **Test:** Clark-West, HAC `maxlags = 3`, one-sided.
- **Also reported:** rolling fixed-size windows (§6.2), and directional hit rate computed
  from a **signed-return** model (never from an |R| model, where it is 100% by
  construction).

### Held-out days

Days **2025-09-29 and 2025-09-30 are held out** and will not be examined until the
primary specification is estimated and locked on 2025-09-15 → 2025-09-26. A result that
does not survive the held-out days is reported as not surviving.

---

## 7. What will be reported regardless of outcome

1. Every specification in §4 and §5, including those that fail.
2. The exact count of specifications estimated, so the reader can judge multiplicity.
3. The three-arm decomposition (naive / all verified / strict) at the primary horizon.
4. The fraction of the session excluded by any filter, with the distribution of excluded
   windows (§6.3).
5. β standardised: the effect of a one-standard-deviation signal move in index points,
   against the one-tick crossing cost of 5 points (§6.4), so economic relevance is
   separable from statistical significance.
6. A null result, stated as a null. **"The signal has no detectable predictive power on
   WIN over this sample" is a publishable finding** given that the reference metric was
   designed for small-tick equities, and is the outcome this protocol is designed to be
   able to report honestly.

---

## 8. Known limitations, fixed in advance

| Limitation | Status |
|---|---|
| 12 trading days, one contract | Acknowledged. Any positive result is **suggestive, not confirmatory**, pending more months. |
| Aggressor side is inferred from print price vs book, not given by the feed | No `order_id` on trades. Affects the strict exclusion and the sweep count only. |
| Strict/ambiguous boundary (`JANELA_RESIDUO = 3`) is a judgement call | Both bounds (1.03% / 1.33%) reported throughout. |
| Sweep grouping estimates aggressive-order counts | Passive counts are exact; aggressive counts are not. Never quoted to the same precision. |
| Specification search already occurred before this protocol | Absorbed by the p < 0.01 threshold and the held-out days. Disclosed, not hidden. |

---

## 9. Implementation checklist (nothing estimated until all are green)

- [ ] Accumulate strict undercut **volume** by side per window (replay change)
- [ ] Accumulate passive new-order **volume** per window (replay change)
- [ ] Accumulate microprice numerator/denominator sums per window (replay change)
- [ ] Rebuild 1-minute panel for all 12 days
- [ ] Build 1/5/15-minute aggregations with the non-overlap guard
- [ ] Implement the primary specification exactly as §4
- [ ] Hold out 2025-09-29 and 2025-09-30 at the data-loading layer, not by filtering later
- [ ] Freeze this document (commit hash recorded) before the first estimate is printed

---

*This protocol may be amended only by appending a dated amendment that states what
changed and why, before the affected estimate is run. Silent revision defeats its purpose.*
