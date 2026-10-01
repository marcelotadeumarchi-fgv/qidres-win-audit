# Complete Session Record — Undercutting Validation on B3 MBO Data

**Instrument:** WIN (mini Bovespa index future), `security_id = 200001274203`
**Data:** B3 UMDF / FIX 5.0, Market-by-Order, Parquet, `/share/fgv-quant/market-data/parquet/2025/09/`
**Period:** 2025-09-15 → 2025-09-30, 12 trading days (09-20 is a Saturday, no events)
**Scale processed:** 355,666,087 MBO events
**Session date:** 2026-09-30
**Environment:** Ubuntu 24.04, Python venv `/home/marchi/financial_ai_project/aifi/`, Polars, 32 cores, 186 GB RAM

---

## Table of contents

1. [Starting point and the question](#1)
2. [Discovery: what the data actually is](#2)
3. [The validated replay — `undercutting_valida.py`](#3)
4. [Headline measurement results](#4)
5. [Visual analysis built along the way](#5)
6. [Methodological questions answered](#6)
7. [Replicating the thesis — and what it revealed](#7)
8. [Evaluation of the proposed adaptations](#8)
9. [Complete artifact inventory](#9)
10. [Open items and known limits](#10)
11. [Applying the protocol — the pre-registered null](#11)
12. [The third flaw — contemporaneous overlap](#12)
13. [Event-level study — 1.47M events](#13)
14. [Pipeline validation against a known result](#14)
15. [Substitution test — quantity and timing](#15)
16. [The cancellation channel — found, then withdrawn](#16)
17. [The queue-based specification](#17)
18. [Engineering incidents worth recording](#18)
19. [Final state — every channel tested](#19)

> **Sections 1–10** cover the measurement work (what the data is, the validated
> replay, the headline counts, and the reproduction of the thesis). **Sections
> 11–19** cover the econometric work that followed: a pre-registered null, three
> further channels tested and rejected, one finding withdrawn under its own
> falsification protocol, and a formal queue-based specification audited and
> executed. Section 19 is the summary of record.

---

<a name="1"></a>
## 1. Starting point and the question

The session began with a request to explain `undercutting_analise.py`, an existing script
that measured "undercutting" — limit orders placed inside the bid-ask spread to take queue
priority — on B3 mini-index futures.

That script:
- flattened `md_entries`, filtered to the target instrument, rebuilt price from
  mantissa × 10^exponent;
- reconstructed "top of book" by **forward-filling** every bid and ask price seen, then
  shifting one row;
- flagged an undercut when a new order priced strictly between the lagged best bid and
  lagged best ask;
- aggregated to 1-minute windows and plotted a three-panel dashboard.

The follow-up request was to build a process that counts **how many undercutting events
really happened**, and **how many orders do not change the spread**.

---

<a name="2"></a>
## 2. Discovery: what the data actually is

Before writing anything, the feed was inspected. Four findings determined the entire
design.

### 2.1 The data is MBO, not aggregated book

Every individual order appears as its own entry, carrying `order_id`. The schema has 70
fields in `md_entries`, including `order_id`, `trade_id`, `md_entry_size`,
`md_update_action`, `md_entry_buyer`, `md_entry_seller`.

### 2.2 `md_price_level` is always NULL

```
lvl distribution: shape (1, 2)
 lvl   | count
 null  | 1,793,396
```

**Consequence:** there is no level information in the feed. The original script's
forward-fill therefore treated orders at *any* book depth as if they were the best quote.
Its "L1" series was not L1.

### 2.3 Order identity is complete; trade identity is disjoint

Full-day census, 2025-09-30:

| entry type | count | `order_id` present | `trade_id` present |
|---|---:|---:|---:|
| `1` ask orders | 10,753,935 | **10,753,934** | 0 |
| `0` bid orders | 10,570,467 | **10,570,466** | 0 |
| `2` trades | 3,915,507 | **0** | 3,915,507 |

`order_id` (FIX tag 37) is present on 21,324,400 of 21,324,402 order entries — two nulls
in a whole day. It is **absent from 100% of trade prints**. There is no
`secondary_order_id` and no aggressor-side field. `trade_condition` holds only
`L` / `L RL` / `RL` / `X` / `R` — trade-type qualifiers, not an aggressor flag.

**Consequence:** the order book can be reconstructed exactly, but a trade print cannot be
linked back to the order that caused it.

### 2.4 Structural properties

- Prices are always multiples of **5 points** (verified: 0 non-multiples in 1,793,396 samples)
- `rpt_seq` is strictly monotone and gap-free per instrument → authoritative replay order
- `transact_time` is always NULL for MBO entries; `sending_time` (YYYYMMDDHHMMSSmmm) is the usable clock
- Directory layout is `<day>/10/incremental/MBO` only — no snapshot channel

---

<a name="3"></a>
## 3. The validated replay — `undercutting_valida.py`

**2,003 lines.** Replaces inference with a true order-book replay.

### 3.1 Architecture

**Stage 1 — extraction.** Flattens `md_entries` into a minimal event stream
(`rpt_seq`, `sending_time`, side, action, `order_id`, price, size), filtered to entry
types 0/1/2 and actions 0/1/2, sorted by `rpt_seq`. Cached per day (~181 MB/day, 2.1 GB
for the month) so every later analysis is a replay-only rerun.

**Stage 2 — book replay.** Prices map to a tick grid. Two count-per-tick arrays hold the
book, two size-per-tick arrays hold resting volume, and an `order_id → (tick, size)` map
lets Change and Delete events vacate the correct price. Best bid/ask are exact pointers,
rescanned only when the best level empties.

**Stage 3 — classification.** For every New order, evaluated *before* the book mutates:

| class | condition (bid side) | spread effect |
|---|---|---|
| `aggressive` | `price ≥ best_ask` | locks/crosses |
| `undercut` | `best_bid < price < best_ask` | **narrows** |
| `join_best` | `price == best_bid` | unchanged |
| `behind_best` | `price < best_bid` | unchanged |
| `no_reference` | one side empty | undefined |

Order matters: the marketable test runs first, so an undercut is strictly non-marketable
at arrival by construction.

### 3.2 Validation invariants

Every one holds across all 355,666,087 events:

| invariant | result |
|---|---|
| New orders that widened the spread | **0** (structurally impossible) |
| `measured_narrowed − undercut_label` | **0** (label matches independent measurement) |
| Orphan deletes (delete of unknown order) | **0** |
| Orphan changes | **0** |
| Ticks ending with negative resting size | **0** (size exactly conserved) |
| Depth/landing buckets summing to all new orders | **100.0000%** |

### 3.3 Bugs found and fixed during development

1. **Report printer** read a per-minute column absent from the per-day dict → added `undercut_ticks_sum` to the report.
2. **Single-day mode clobbered the month panel** → day-suffixed output paths.
3. **Locked-book edge case** (1 event in 145.6M): an order locking the book at exactly the
   opposite best drove measured spread to 0, tripping the "narrowed" test while correctly
   labelled `aggressive`. Fixed by requiring `spread > 0` on both sides of the event and
   counting lock events in their own bucket (`new_orders_in_locked_book` = 59 month-wide).
4. **Double-counted depth buckets** (100.06%): aggressive orders arriving into an already
   locked book satisfied both the "crossed" and the depth test. Fixed by gating the depth
   histogram on the classification label.
5. **Chart: month panel identical to day panel** — `gerar_dashboard` used `.iloc[0]`,
   silently discarding 11 of 12 days. Fixed to default to the most recent day and print
   which day it is plotting.
6. **Path slicing bug** in the multi-chart writer (`Path[:-3]`).
7. **Directional hit rate of 100%** — an artifact of computing sign agreement on an `|R|`
   target where both prediction and truth are non-negative. Fixed to use a signed-return
   model (true value: 63.35% / 61.25%).

---

<a name="4"></a>
## 4. Headline measurement results

### 4.1 September 2025, 12 trading days

| | count | % of new orders |
|---|---:|---:|
| MBO events replayed | 355,666,087 | |
| New limit orders | 145,595,511 | 100% |
| **Undercut (verified spread-narrowing)** | **1,930,208** | **1.33%** |
| — of which trade-adjacent, same-side (ambiguous) | 424,881 | 0.29% |
| **Strict undercut** | **1,505,327** | **1.03%** |
| No spread change | 143,540,326 | 98.59% |
| — join at best (queue) | 54,911,013 | 37.71% |
| — behind best (depth) | 88,629,313 | 60.87% |
| Aggressive (lock/cross) | 124,944 | 0.09% |
| No reference (side empty) | 33 | 0.00% |
| Trades | 55,584,686 prints | |
| Traded volume | 183,359,503 contracts | |

Buy/sell undercutting is near-perfectly balanced: 966,599 vs 963,609.
Average improvement: 1.023 ticks.

### 4.2 The old heuristic over-counts by 21.9×

| | count |
|---|---:|
| Naive heuristic (replayed on the identical event stream) | 42,190,798 |
| Verified undercut | 1,930,208 |
| **Ratio** | **21.86×** |

Cause: forward-filling treats every order at any depth as the best quote, and deletes are
never applied, so cancelled quotes linger in the reconstructed state.

### 4.3 Why 1.33% and not more — the geometric constraint

Spread-length distribution, measured two ways:

| spread | % of **clock time** | % of **arriving orders** |
|---|---:|---:|
| 1 tick | **98.953** | 96.710 |
| 2 ticks | 1.039 | 3.148 |
| 3 ticks | 0.005 | 0.096 |
| 4+ ticks | 0.002 | 0.047 |

The book is one tick wide **99% of the time**, so undercutting is geometrically impossible
in almost all of the session. Undercutting at 1.33% of new orders fits inside the ~3.5%
opportunity window with room to spare.

**The two weightings diverge** — the book is 1 tick wide 99% of the *time* but only 96.7%
of *order arrivals* see it that way. Wide-spread moments attract ~3× the order traffic per
second. This is why the event-weighted mean spread (1.035 ticks) exceeds the time-weighted
mean (1.007).

### 4.4 Where new orders land

| bucket | count | % of new orders | % of all arrivals |
|---|---:|---:|---:|
| Crossed/locked the book | 124,944 | 0.09% | 0.08% |
| Inside the spread (undercut) | 1,930,208 | 1.33% | 1.18% |
| At the touch (join queue) | 54,911,013 | 37.71% | 33.62% |
| 1 tick behind | 14,593,059 | 10.02% | 8.94% |
| 2–5 ticks behind | 13,496,368 | 9.27% | 8.27% |
| 6–10 ticks | 8,698,892 | 5.97% | 5.33% |
| 11–20 ticks | 10,853,369 | 7.45% | 6.65% |
| 21–50 ticks | 16,782,153 | 11.53% | 10.28% |
| 51–100 ticks | 6,472,214 | 4.45% | 3.96% |
| **>100 ticks** | 17,733,258 | **12.18%** | 10.86% |

Maximum depth observed: **5,873 ticks = 29,365 points**, ~20% away from a ~147,000 price.

The distribution is **bimodal, not decaying**: a dense cluster at the touch (49.15% within
1 tick), a thin middle (2–10 ticks = 15.2%), and a large far population. Mean depth
(~302 ticks) is therefore a meaningless summary and should not be quoted.

### 4.5 Counting liquidity takers

A marketable order never appears as a New order — it arrives as trade prints plus deletes.
`trade_id` is unique per print (381,465 distinct of 381,465), so it cannot group an
execution. Aggressive orders were counted by **sweep grouping**: consecutive prints
sharing a timestamp *and* an inferred aggressor side are one incoming order.

| | September 2025 |
|---|---:|
| Trade prints | 55,584,686 |
| **Aggressive orders** | **17,728,376** |
| — buy / sell | 8,862,862 / 8,865,514 |
| Prints per aggressive order | 3.14 |
| Deepest single order | 200 prints |
| Avg size per aggressive order | 10.34 contracts |
| **Passive : aggressive** | **8.2 : 1** |
| Aggressive share of all arrivals | **10.85%** |

The buy/sell split is **49.99%** — a gap of 2,652 orders in 17.7 million, with the
aggressor side inferred independently per print. A biased inference rule would drift; this
is a strong check that it does not.

### 4.6 Change events are partial fills, not reprices

| day | Changes | price changed | size **decreased** | unchanged | increased |
|---|---:|---:|---:|---:|---:|
| 2025-09-15 | 798,571 | **0** | **798,571** | 0 | 0 |
| 2025-09-17 | 1,009,832 | **0** | **1,009,832** | 0 | 0 |
| 2025-09-30 | 681,563 | **0** | **681,563** | 0 | 0 |

**2.49 million events, zero exceptions.** Every Change decreases quantity; none changes
price; none increases size. That is the signature of partial execution, not amendment.

**Consequence:** algos *do* reprice constantly, but B3 publishes a reprice as Delete + New
(moving price forfeits queue priority anyway), so those repricings are already counted via
the New half. `reprice_narrow = 0` across the board — the reprice channel is provably
empty, so classifying New orders alone misses nothing.

---

<a name="5"></a>
## 5. Visual analysis built along the way

### 5.1 Static charts

| file | content |
|---|---|
| `painel_undercutting_validado_<date>.png` | 4-panel intraday: verified undercuts vs old heuristic (secondary axis), spread-neutral flow, net imbalance + avg spread, mid price |
| `painel_undercutting_validado_overview.png` | 12-day overview: undercuts/day, % of new orders, log-scale naive-vs-verified, intraday profile |
| `painel_undercutting_validado_continuo_1h.png` | Continuous hourly series across all days, overnight gaps collapsed, with book pressure and spread panels |
| `painel_undercutting_validado_spread_freq.png` | Spread-length frequency by clock time, time- vs event-weighted |
| `painel_undercutting_validado_1s_*.png` | 1-second resolution, full session and windowed |
| `painel_undercutting_validado_sequencia_*.png` | Event-level spread corridor with individual orders marked |

### 5.2 Key visual findings

- **Intraday decay is the dominant structure**: ~1,750 undercuts/min at the 09:00 open
  decaying to ~150/min by 17:00, with a visible 15:00 spike (US session open).
- **Undercutting is stable at 1.1–1.7% of new orders every single day** — the 1.33%
  monthly figure is structural, not an average over variable days.
- **Book pressure is near zero most hours** with sharp one-sided spikes; at 1-second
  resolution it flips violently between ±0.75 with no persistent side, which the hourly
  chart averages away.

### 5.3 The event-sequence chart

Draws the book as a **corridor** (best bid/ask step lines, spread shaded) with every new
order plotted where it landed. Required adding an optional recording window to
`replay_dia` that retains per-event detail it normally aggregates away.

Concrete example extracted from 09:00:37.500 BRT on 2025-09-30:

```
t=+ 0ms  BUY  at 147395   book 147395/147400   spread 2 → 1   qty 10
t=+ 1ms  BUY  at 147395   book 147395/147405   spread 3 → 2   qty 10
t=+ 2ms  BUY  at 147400   book 147400/147410   spread 3 → 2   qty 10
t=+ 2ms  SELL at 147405   book 147400/147405   spread 2 → 1   qty 10
t=+ 4ms  BUY  at 147405   book 147405/147410   spread 2 → 1   qty 10
```

Five spread cuts in four milliseconds, all 10 lots, alternating sides.

Aggression was initially invisible on this chart because trades were skipped before the
recorder saw them. Fixed: trades now record with the book state, and the aggressor side is
read off the print (at the ask = buyer lifted, at the bid = seller hit).

### 5.4 Movies

A chart series does not read as film — each frame is an independent panel with its own
axes. Building a real movie required a **sliding window** (overlapping frames), a **damped
camera** (rolling-mean y-limits instead of per-frame autoscale), and ≥20 fps.

| file | content |
|---|---|
| `filme_undercutting_20250930_0900.mp4` | 21 s, full flow with trades and volume, 7.8 MB |
| `filme_undercutting_limpo_20250930.mp4` | 21 s, undercuts only, 4.2 MB |
| `filme_undercutting_estrito_20250930.mp4` | 21 s, strict (filled) vs ambiguous (hollow), 4.2 MB |
| `filme_slowmo_20250930.mp4` | 8 s at quarter speed, 2.4 MB |
| `sequencia_20250930_43237000_1s.gif` | 60-frame chapter series, 5.8 MB |

`player_sequencia.py` (209 lines) plays any folder of numbered PNGs as GIF, interactive
Tkinter slideshow, or mp4. ffmpeg was installed mid-session; mp4 output is ~3.7× smaller
than GIF at the same quality, with `-pix_fmt yuv420p`, even-dimension padding and
`+faststart` for browser playback.

---

<a name="6"></a>
## 6. Methodological questions answered

### 6.1 How is the mid price calculated?

`mid = (best_bid + best_ask) / 2` from the replayed book — a simple arithmetic midpoint,
**not** size-weighted. It is the **last** mid of each window (overwritten per event), and
sampled **only on New-order events** inside the `spread > 0` guard.

Staleness of that sampling, measured: median **0 ms**, mean 5.7 ms, p95 29 ms, max 461 ms.
At ~2,000 book events per minute it is the end-of-minute mid in the median case.

**Inconsistency found and fixed:** the intraday panel used last-trade-first while the
continuous chart used mid-first. Standardised on the mid via a single `_serie_preco()`
helper. Measured impact: mean absolute difference **2.509 points**, max 7.5 — almost
exactly the half-spread, as expected.

### 6.2 How is the mean spread calculated, and why is it never 1 tick?

`avg_spread_ticks = spread_ticks_sum / spread_obs` — **event-weighted**, sampled on New
orders, post-insert, excluding locked books.

It is never exactly 1.0 because it is a **mean over a mixture of integers**. A bin averages
to 1.000 only if every observation in it is 1 tick; at ~22,000 observations per minute that
never happens — **0 of 6,485 minutes**.

| | |
|---|---:|
| Event-weighted mean, month | 1.03533 ticks (5.177 points) |
| Excess over 1 tick | 0.03533 → ≤3.53% of observations wider |
| Quietest minute | 1.00060 |
| Widest minute | 2.318 |

**Caveat surfaced:** the recorded value is `spread_after`, so an undercut contributes the
narrowed spread it just created. Pre-event mean is **1.04863** vs post-event **1.03533** —
only 1.3% of the value, but **38% of the excess over 1 tick**.

### 6.3 Are undercuts only orders that do not trade?

Yes, by construction — the marketable test runs first, so an undercut bid satisfies
`best_bid < price < best_ask` strictly. Verified: **0 marketable-at-arrival violations**
in 44,093 events.

**But one ambiguity cannot be resolved.** An order that arrives marketable, partly fills,
and rests its residual inside the spread appears as a New order inside the spread. Measured
signature: 29.7% of undercuts are preceded by a trade within 3 events (vs 14–18% for other
order types), of which 60.8% same-side and 58.2% same-price.

That is **equally** the signature of a different trader chasing momentum after a sweep.
With no `order_id` on trades, the two are observationally identical.

Upper bound across the session: **14–24%**, roughly flat from open (18.0%) to midday
(17.2%) — which argues against pure residual contamination, since residuals should scale
with aggressive flow.

**Resolution adopted:** report both bounds. `undercut_real` = 1.33%; `undercut_estrito`
= 1.03%. The column is named `undercut_maybe_residual` because it counts *ambiguity*, not
confirmed contamination.

---

<a name="7"></a>
## 7. Replicating the thesis — and what it revealed

### 7.1 Exact reproduction

`tese_apendice_a.py` (142 lines) transcribes thesis Appendix A verbatim, changing only the
output directory and `pl.count()` → `pl.len()`.

| | thesis | reproduction |
|---|---:|---:|
| β at t+1 | 134.2251 | **134.2251** |
| R² at t+1 | 10.93% | **10.93%** |
| β at t+3 | 112.2665 | **112.2665** |
| R² at t+3 | 3.78% | **3.78%** |
| windows | ~1,400 | 1,392 |
| R²_OOS | 6.68% | 7.09% |

**The code is reproducible.** The problem is what it measures.

### 7.2 Cause 1 — `avg_spread_pts` is not the spread

| percentile | thesis `avg_spread_pts` |
|---|---:|
| p0 | 50.3 points |
| p50 | **3,367.2 points** |
| p100 | 14,180.5 points |

True bid-ask spread: **5.29 points**. The thesis quantity is ~640× larger because
`bid_raw`/`ask_raw` forward-fill *any* bid or ask entry at any depth — it measures a
book-range statistic.

Two consequences follow mechanically:
- the large-tick filter `avg_spread_pts > 5.0` **can never bind** (p0 = 50.3); it drops
  **0 of 1,308** windows, so §2.2, §5 and §6.3's discussion of selection bias from this
  filter describes something that does not happen;
- `spread_relativo` in eq. (2) orthogonalises against the wrong variable, so §2.3's
  orthogonality claim does not hold.

### 7.3 Cause 2 — 6% of windows produce all of the result

| sample | β | z | p | R² | n |
|---|---:|---:|---:|---:|---:|
| **full (as the thesis runs it)** | 132.36 | 3.690 | 0.0002 | **10.72%** | 1,391 |
| **regular session 09:00–18:00** | 33.84 | 0.720 | **0.4716** | **0.06%** | 1,308 |
| outside the session only | 146.10 | 3.747 | 0.0002 | **26.37%** | 83 |

The 83 windows are **12 pre-open (08:xx)** and **71 after 18:00** — auction and
after-hours, where there is no continuous trading.

Their leverage:
- **6.0% of observations carry 94.7% of the sum of squares in `qid_res`**
- and 42.9% of the variance in the return
- mean |return| 17.59 bp outside vs 6.00 bp inside
- mean |qid_res| 0.064 vs 0.0045 — **14× larger**

Out-of-sample: 6.69% full (reproducing 6.68%) vs **4.57%, p = 0.0924** session-only.

### 7.4 Three-arm decomposition — what did *not* cause it

Identical windows, identical method, only the numerator differs:

| arm | month total | β at t+1 | z | p | R²_is | R²_oos | CW p |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive (thesis heuristic) | 41,662,330 | −4.45 | −0.227 | 0.821 | **0.00%** | −0.16% | 0.094 |
| all undercuts (verified) | 1,886,300 | −313.13 | −1.133 | 0.257 | 0.24% | 0.09% | 0.209 |
| strict undercuts | 1,468,824 | −423.67 | −1.186 | 0.236 | 0.26% | 0.17% | 0.101 |

| hypothesis | verdict |
|---|---|
| strictness killed it | **No** — 0.26% vs 0.24%, essentially identical |
| correct book reconstruction killed it | **No** — the naive arm is the *weakest* |
| **auction windows + mis-measured spread produced it** | **Yes** |

### 7.5 The §7 result is intraday seasonality

Final specification (`|R|` target, hour dummies), signal's own contribution:

| arm | R² vs mean benchmark | R² vs **dummies** benchmark | CW p |
|---|---:|---:|---:|
| naive (thesis) | 22.91% | **0.09%** | 0.259 |
| all undercuts | 22.72% | **−0.14%** | 0.459 |
| strict | 22.82% | **−0.02%** | 0.443 |

Against a mean-only benchmark the model looks strong; with the hour dummies moved into the
*restricted* model, the signal contributes nothing — for **every** measure. Table 4's 9.03%
is likely the same artifact.

### 7.6 The one result that survived

Signed measure on signed returns, session only:

| measure | β | z | p | R² |
|---|---:|---:|---:|---:|
| unsigned | −423.67 | −1.186 | 0.236 | 0.26% |
| **signed (bid − ask)** | **864.39** | **2.644** | **0.0082** | **0.61%** |
| signed + hour dummies | 906.71 | 2.694 | — | 1.51% |

Directional hit rate (from a signed-return model): **63.35%** top 20%, **61.25%** top 10%.

§7 moved to the `|R|` target *because* Table 4 showed directional accuracy **below** chance
(46.58% / 40.54%). On the validated measure it is **above** chance — so that move may have
been a response to an artifact of the old measurement.

---

<a name="8"></a>
## 8. Evaluation of the proposed adaptations

Three ideas were proposed and evaluated **before** implementation.

### 8.1 Shorter windows — recommended, highest value per effort

| window | n | min detectable R² at 5% |
|---|---:|---:|
| 5 min | 1,308 | 0.29% |
| **1 min** | 6,540 | **0.06%** |
| 30 s | 13,080 | 0.03% |

The current signed result (0.61%) sits only ~2× above the 5-minute detection floor. It is
also the direct test of §4.1's decay theory: if the mechanism is real, **1 minute should be
stronger than 5**. Cost ≈ 0 (the 1-minute panel exists).

### 8.2 Side + volume weighting — recommended, strongly motivated

**Side:** already the best-performing variant (§7.6). Not a new idea to test; the one that
survived, and it should be the centre of the design.

**Volume:** the size distribution makes the case — median new order **1 lot**, mean 6.65,
sd 18.43, max 3,999; the largest **1% of orders carry 20.4% of all contracts**. A
count-based QID is dominated by 1-lot orders.

It also resolves an internal discrepancy: **equation (1) specifies volume**
("Volume de melhorias de cotação") while the Appendix A code uses `pl.count()`.

### 8.3 Depth-weighted midprice — worth doing, but not first, and not for the stated reason

Tick discretisation was expected to be crippling. **It is not, at 5 minutes:**

- 5-min midpoint change: sd **89.15 points = 17.8 ticks**
- only **6.5%** of windows move less than one tick

So the L1 midpoint is adequate *now*. But scaling by √t, a 15-second move is ~2.7 ticks and
a 1-second move ~1.2 ticks — at which point the 2.5-point grid *is* the signal. **This is
the enabler for idea 8.1 below ~30 seconds**, not a standalone improvement.

The right construction is the **microprice**
`(bid_size·ask + ask_size·bid)/(bid_size+ask_size)` — continuous, weighted by the imbalance
that predicts the next move, standard for large-tick markets. Best-level sizes are already
tracked. A caution: a depth-weighted target responds to the same book pressure the signal
measures, which raises R² partly mechanically — close to §6.2's contemporaneity problem.

### 8.4 The discipline problem

The specification search occurred **after** observing a null. The signed result at
p = 0.0082 came from an ablation that also tried unsigned, two denominators and two
targets. It is **not** safe from multiplicity.

Resolution: `PROTOCOLO_PRE_ESPECIFICACAO.md` fixes the revised design before estimation —
one primary test at p < 0.01, held-out days (09-29, 09-30), every specification reported,
and a null reportable as a null.

---

<a name="9"></a>
## 9. Complete artifact inventory

### 9.1 Code

| file | purpose |
|---|---|
| `undercutting_valida.py` | Order-book replay, classification, validation, queue reconstruction, charts and movies |
| `qid_strict.py` | Thesis QID pipeline on the validated strict subset |
| `tese_apendice_a.py` | Verbatim reproduction of thesis Appendix A |
| `player_sequencia.py` | Chart-series player: GIF / Tkinter / mp4 |
| `protocolo_estimar.py` | Protocol I estimation (§11), `train` / `holdout` modes |
| `diagnostico_overlap.py` | Isolates the window-mean contemporaneity leak (§12) |
| `evento_microprice.py` | Event study on the 100 ms grid (§13) |
| `evento_robustez.py` | Block bootstrap + conditional splits (§13.3) |
| `substituicao.py` | Three-channel substitution test (§15) |
| `painel_1s.py` | 1-second panel builder |
| `cancelamento.py` | Cancellation reaction channel, two-stage (§16.1) |
| `falsificacao.py` | F1–F6 falsification suite (§16.2) |
| `fila_pipeline.py` | Two-instrument panel builder, spawn-safe (§17.2) |
| `spec_hipoteses.py` | Spec H1/H2/H3 as written (§17.3) |
| `spec_patches.py` | Patches P2a / P2b / P3b, kept separate (§17.4) |
| `PROTOCOLO_PRE_ESPECIFICACAO.md` | Protocol I — pre-registration, SHA256 frozen |
| `PROTOCOLO_CANCELAMENTO.md` | Protocol II — falsification design, SHA256 frozen |
| `PROTOCOLO.sha256` | Freeze record for both protocols |
| `SESSAO_COMPLETA.md` | This document |

### 9.2 Data outputs

| path | size | content |
|---|---:|---|
| `output_data/microstructure/undercut_valid_events_*_<date>.parquet` | 2.1 GB | Per-day event cache (12 days) |
| `output_data/microstructure/undercut_valid_1m_<sid>.parquet` | 44 KB | Per-minute validated panel |
| `output_data/microstructure/undercut_valid_report_<sid>.csv` | — | Per-day validation report |
| `output_data/qid_strict/qid_res_5m_strict_<sid>.parquet` | 176 KB | 5-min QID panel, strict subset |
| `output_data/tese_apendice_a/qid_res_5m_FULL_MONTH_<sid>.parquet` | 224 KB | Verbatim thesis reproduction |
| `sequencia_20250930_43237000_1s/` | 16 MB | 60 event-sequence charts + `resumo.csv` |
| `output_data/protocolo/resultado_train.txt`, `resultado_holdout.txt` | — | Protocol I results (§11) |
| `output_data/protocolo/diagnostico_overlap.txt` | — | Overlap diagnostic (§12) |
| `output_data/evento/evento_<date>.npz` | — | 100 ms price grids + event arrays (§13) |
| `output_data/evento/robustez.txt` | — | Bootstrap and conditional splits |
| `output_data/painel1s/painel_1s_FULL.parquet` | — | 1-second panel, cancellation channels |
| `output_data/spec_fila/painel_200001274203.parquet` | 27 MB | WIN, all SPEC §2 features |
| `output_data/spec_fila/painel_200001287487.parquet` | 12 MB | Control instrument |
| `output_data/spec_fila/resultado_spec.txt`, `resultado_patches.txt` | — | §17 results |

### 9.3 Figures and movies

Charts: `painel_undercutting_validado_*.png` (intraday, overview, continuous, spread
frequency, 1-second, event sequence).
Movies: `filme_undercutting_*.mp4`, `filme_slowmo_20250930.mp4`,
`sequencia_20250930_43237000_1s.gif`.

### 9.4 Environment changes made

- `pip install statsmodels` into `/home/marchi/financial_ai_project/aifi/` (0.15.0) — required by the thesis method
- `ffmpeg` 6.1.1 installed system-wide (by the user) — enables mp4 output

---

<a name="10"></a>
## 10. Open items and known limits

### 10.1 Unresolvable with this data

| limit | why |
|---|---|
| Residual-vs-momentum ambiguity (14–24% of undercuts) | No `order_id` on trade prints |
| Aggressor side | Inferred from print price vs book; no aggressor flag in the feed |
| Level-rank depth | Would need a different data structure; tick distance used instead |

### 10.2 Not yet done

- Primary estimation under `PROTOCOLO_PRE_ESPECIFICACAO.md` (checklist in §9 of that document)
- Extension beyond 12 days — the honest ceiling on any current claim
- Heckman-style selection correction (§6.3 of the thesis)
- Event-based validation against scheduled macro announcements (§6.1)
- VPIN comparison on the same data (§6.4)
- Panel extension to liquid equities (§5)

> **Sections 10.1–10.3 below were written after §1–9 and reflect the state at
> that point.** They remain accurate, but §19 is the summary of record for the
> project as a whole.

### 10.3 Recommended rewrite scope for the thesis

§4 (preliminary results), §7 (final results), §8 (conclusions) and both abstracts assert
the opposite of what the corrected data shows. The claim that survives — *a metric designed
for small-tick equities does not survive a correct large-tick implementation* — is exactly
the validity-conditions contribution §1 promises, and is now supported by an exact
reproduction plus a clean decomposition of the failure.

§2.2, §2.3, §5 and §6.3 additionally need correcting on specific factual points: the
large-tick filter is non-binding, `avg_spread_pts` is not the spread, and the
orthogonalisation target is mis-measured.

---

*All figures in this document were produced in-session and are reproducible from the
cached event parquets. Every number stated has a corresponding command in the session
transcript.*

---

<a name="11"></a>
## 11. Applying the protocol — the pre-registered null

`PROTOCOLO_PRE_ESPECIFICACAO.md` was frozen (SHA256 `404c60e9…ca411ab3`,
`2026-10-01T02:03:24Z`) **before any estimate was produced**, with the checksum
recorded in `PROTOCOLO.sha256` since this tree is not a git repository.

### 11.1 Replay additions required by the protocol

| protocol §    | column added |
|---|---|
| 3.1 | `uc_strict_vol_bid/ask`, `uc_amb_vol_bid/ask` — undercut volume in **contracts** |
| 3.2 | `new_vol_bid/ask` — passive order volume (the eq. (1) denominator) |
| 3.5 | `close_microprice`, `close_midprice` — refreshed **post-event** so a window's last value is the state it actually closed at |

**Microprice validation**, 2025-09-30 session, 541 minutes:

| | midpoint | microprice |
|---|---:|---:|
| distinct values | 194 | **541** |
| on the 2.5-pt grid | 100% | — |
| **1-min windows with zero change** | **6.5%** | **0.0%** |

Correlation 0.999995, mean \|difference\| 1.114 points, max 2.49 — bounded by the
half-tick by construction. The microprice recovers the 6.5% of windows that
quantisation was discarding.

### 11.2 Primary result — H1 NOT SUPPORTED

```
R(t -> t+1min) = a + b*QID_signed(t) + g*S(t) + hour dummies     HAC(2)
```

| | training (09-15…09-26) | full 12 days |
|---|---:|---:|
| β | 4.212 | −0.205 |
| z | 0.499 | −0.023 |
| **p** | **0.618** | **0.982** |
| R² | 0.314% | 0.135% |
| n | 5,400 | 6,473 |
| 1-sd effect | 0.234 index points | −0.011 |

Against a 5.0-point crossing cost the one-standard-deviation effect is **0.23
points** — economically irrelevant before significance is even considered.

**Every §5.2 variant is also null:** all-verified p=0.875, count-weighted
p=0.488, all-message denominator p=0.378, L1-midpoint target p=0.615. None of
the three adaptations evaluated in §8 rescues it.

**Out-of-sample**, benchmark of record (mean + controls): **R²_OOS = −0.049%,
CW = −1.601** — worse than the benchmark. Directional hit rate **50.31% / 49.39%**,
exactly chance.

**§5.4 conditional variance** reproduces the pattern a third time: `|R|` gives
13.48% against a mean-only benchmark but **−0.137% (p=0.309)** once the controls
are in.

18 specifications, all disclosed by the script itself.

---

<a name="12"></a>
## 12. The third flaw — contemporaneous overlap

The earlier exploratory signed result (z=2.644) vanished under the protocol. The
cause was isolated by changing **only** the price basis, 5-min windows, training
days:

| return basis | β | z | p | R² |
|---|---:|---:|---:|---:|
| close-to-close (protocol §3.5) | −52.20 | −0.626 | 0.532 | 0.639% |
| close-to-close, L1 midpoint | −50.30 | −0.606 | 0.545 | 0.636% |
| **window MEAN midpoint** (old) | **+417.72** | **4.773** | **<0.00001** | 2.844% |

A window-**mean** price overlaps the window in which the signal is measured; a
close-to-close return does not. One change flips z from −0.63 to +4.77 **and
flips the sign** — the signature of a mechanical contemporaneous relationship.

This is **exactly the leak thesis §6.2 anticipates**, and it is a *third*
independent problem on top of the auction windows (§7.3) and the mis-measured
spread (§7.2). All three preliminary results now have identified causes.

---

<a name="13"></a>
## 13. Event-level study — 1.47M events instead of 6,485 windows

Rather than aggregate undercutting into windows, the price response was measured
around **every strict undercut** on a continuous 100 ms grid, using two
depth-weighted prices built from the verified book:

```
microprice  = (Qb*Pa + Qa*Pb) / (Qb+Qa)                       [L1]
L3 midprice = same form, Q and P size-weighted over <=3 levels per side
```

**Decomposition per event** — the methodological core:

```
mechanical(e)    = P_post - P_pre      the order's OWN effect on the book
predictive(e,D)  = P(t+D) - P_post     what happens AFTER it
```

Measuring from `P_post` excludes the mechanical component **by construction** —
the leak that produced z=4.773 in §12.

### 13.1 First pass looked like a strong finding

1,467,632 strict undercuts (734,970 buy / 732,662 sell), response signed by side:

| horizon | microprice | L3 midprice |
|---|---:|---:|
| 0.1 s | +0.45 | +1.55 |
| 10 s (peak) | **+0.85** | **+2.21** |
| 60 s | +0.59 | +1.96 |

Significant at 1% on the across-day test at nearly every horizon, with a
rise-then-decay shape.

### 13.2 Two controls overturned it

**Control 1 — plain L1 midpoint**, which moves only when a *quote* changes and is
therefore immune to size-weighting drift.
**Control 2 — join-at-best placebo**: orders that add size at the touch but never
change the spread.

Excess over placebo, moving-block bootstrap (10,000 replicates, 10-min blocks
drawn within days):

| price | 0.1 s | 10 s | 60 s |
|---|---:|---:|---:|
| **plain midpoint** | **−1.664** ** | **−1.773** ** | **−1.732** ** |
| microprice | 0.117 ** | 0.033 (p=0.18) | 0.063 (p=0.30) |
| L3 midprice | 1.053 ** | 0.941 ** | 0.953 ** |

**On the uncontaminated price the sign is NEGATIVE** — after a buy undercut the
midpoint moves *against* the undercutting side by ~1.7 points and stays there.
That is mean reversion of the undercut's own mechanical impact (~68% of it
returned), not information.

**The microprice "effect" was queue mechanics**: join-at-best orders reproduce
**91% of it at 60 s**, and the excess is insignificant from 0.5 s onward. Without
the placebo this would have read as a clean result at t=24.25.

**L3 is the same artifact amplified.** Its mechanical effect is **−0.80** while
the microprice's is **+0.50** — opposite signs for the identical event. A price
measure that moves in opposite directions for the same order is reporting its own
weighting scheme.

### 13.3 Conditional splits

**Order size** (p80=13 lots, p95=28, max 2,940):

| microprice excess | small (≤13) | large (>28) |
|---|---:|---:|
| 10 s | −0.009 (p=0.44) | **+0.534** ** |
| 60 s | 0.045 (p=0.56) | **+0.610** ** |

But large undercuts still show a **negative midpoint** excess (−0.42 to −0.71):
they attract queue depth without moving price. Liquidity attraction, not price
discovery.

**Spread regime** — and this is the damaging one:

| spread before | share | midpoint excess at 10 s | at 60 s |
|---|---:|---:|---:|
| exactly 2 ticks | **97.0%** | −1.758 | −1.710 |
| 3+ ticks | 3.0% | **−2.565** | **−2.656** |

The reversal is *stronger* where the trader actually chooses how far to improve.
(Scaled against the spread the ratio is ~1.46 vs ~1.65, so this is largely
proportional scaling rather than a separate puzzle — an earlier overstatement
corrected.)

**97.0% of undercuts face a 2-tick spread**, so the improvement is mechanically
forced to exactly one tick. The large-tick constraint, measured at event level.

---

<a name="14"></a>
## 14. Pipeline validation — the data reproduces a known result

Before trusting any null, the apparatus was checked against order-book imbalance,
whose short-horizon predictability is long established.

`OBI = microprice − midpoint`, identically `(spread/2)·(Qb−Qa)/(Qb+Qa)`, against
the forward **midpoint** on the 100 ms grid:

| horizon | corr | β | t_day |
|---|---:|---:|---:|
| 0.1 s | **0.1604** | 0.266 | 34.0 ** |
| 1.0 s | 0.1258 | 0.621 | 24.6 ** |
| 10 s | 0.0453 | 0.708 | 18.8 ** |
| 60 s | 0.0188 | 0.668 | 6.3 ** |

Textbook: correlation peaks sub-second and decays, β rises toward full price
impact (~0.70). **The book reconstruction, prices and inference all work — so the
nulls are real, not measurement failures.**

---

<a name="15"></a>
## 15. Substitution test — quantity and timing channels

Hypothesis: when the tick forecloses the price channel, information migrates to
the dimensions that remain open. New replay machinery tracked order lifetimes via
`order_id` (touch cancellations only) and depth at the touch.

Descriptives (session): depth at touch bid 185.5 / ask 186.3 contracts;
touch-order lifetime bid 40.4 s / ask 41.4 s.

| channel | signal | 1 min | 5 min | 15 min |
|---|---|---:|---:|---:|
| PRICE | `qid_signed` | p=0.982 | p=0.994 | p=0.271 |
| QUANTITY | `depth_imb` | p=0.466 | p=0.277 | p=0.123 |
| QUANTITY | `net_flow` | p=0.749 | p=0.092 | p=0.083 |
| TIMING | `life_imb` | p=0.153 | p=0.190 | p=0.119 |

**All null.** Best is `net_flow` at p=0.083 with the **wrong sign**.

A measurement caveat was then found and fixed: window-average depth imbalance is
not the standard predictor; the point-in-time close value is. Using the identity
`OBI_close = microprice − midpoint`, re-tested against the midpoint — still null
at 1 minute (p=0.293). §14 explains why: OBI's predictability lives sub-second,
and 1-minute windows are outside that band entirely.

---

<a name="16"></a>
## 16. The cancellation channel — found, then withdrawn

Reframed from *prediction* to *reaction*: Barardehi's mechanism is that informed
risk causes liquidity providers to **withdraw**, which is a response. Three
measurement choices were changed: **rate** not level, **gross** not net, and
**seconds** not minutes.

### 16.1 Two-stage result

**Stage A — reaction confirmed.** Aggressive buy pressure at *t* drives ask-side
withdrawal at *t+k*: β = −0.0093 (1 s) to −0.0038 (10 s), significant 1–5 s.
Not news on its own.

**Stage B — information, apparently.** Cancellation imbalance predicting the
forward midpoint, nested controls:

| horizon | alone | +flow | +OBI | **+additions** |
|---|---:|---:|---:|---:|
| 1 s | −0.0350 ** | −0.0351 ** | −0.0032 * | **0.0007 (p=0.64)** |
| **5 s** | −0.0536 ** | −0.0527 ** | −0.0210 ** | **−0.0173 (p=1.6e−6)** ** |
| **10 s** | −0.0567 ** | −0.0558 ** | −0.0223 ** | **−0.0186 (p=8.1e−5)** ** |
| 30 s | −0.0469 ** | −0.0477 ** | −0.0142 | **−0.0077 (p=0.35)** |

Robustness: survived depth and spread controls (p=1.4e−6), survived a lagged
return control (p=0.008), negative on **9 of 12 days** (t=−3.79), and survived
Bonferroni over ~80 project specifications. 1-sd effect **0.11–0.12 index points**.

### 16.2 Protocol II and the falsification suite

`PROTOCOLO_CANCELAMENTO.md` was frozen before any falsification test ran, and
states in §1 that the finding is **exploratory** and that **the hold-out days are
burned** — they were used in the exploratory run.

| test | result |
|---|---|
| F1 deep-book placebo | **PASS** — β=−0.0047, p=0.35, 27% of the touch effect |
| F2 time-shuffle (500 within-day permutations) | **PASS** — \|β\|=0.0173 vs p99=0.0096 |
| F3 side symmetry | **PASS** — bid −0.0253, ask −0.0281 |
| **F4 reverse causality** | **FAIL** |

| horizon | forward β | backward β |
|---|---:|---:|
| 5 s | −0.0173 | **−0.2756** |
| 10 s | −0.0186 | **−0.2196** |

**The backward coefficient is 12–16× the forward one.** Per protocol §6 the
finding is **WITHDRAWN**. F5 showed the effect concentrates in wide-spread states
(β=−0.0448, p=3.6e−6) versus one-tick states (p=0.22), which reinforces rather
than rescues the F4 reading.

**Why the protocol was worth writing:** three placebos passed, including the one
called decisive. Without F4 this would have been reported as a finding.

---

<a name="17"></a>
## 17. The queue-based specification

A formal research specification was proposed (front-of-queue cancellations as the
large-tick analog of price undercutting) and audited before implementation.

### 17.1 Audit against the spec as written

| spec item | verdict |
|---|---|
| §1 MBO Level 3 | OK |
| §1 µs/ns timestamps | **FAILS** — millisecond only (`md_entry_time` 9 digits, `sending_time` 17) |
| §1 large-tick bucket | OK — WIN, 99.03% at one tick |
| §1 small-tick bucket | OK by criterion, **but it is a liquidity contrast** |
| §2.1–2.5 features | OK |
| §3 H1 | Executable; **causal claim not identified** |
| §3 H2, H3 | OK |
| §4 DM across tick deciles | **NOT EXECUTABLE** |
| §5 β vs tick-to-price plot | **NOT EXECUTABLE** |

**Regime classification, full day, measured by book replay:**

| security_id | events | tick | % time 1 tick | mean spread | spec regime |
|---|---:|---:|---:|---:|---|
| 200001274203 (WIN) | 21,324,400 | 5.0 | **99.03%** | 1.010 ticks | LARGE-TICK |
| 200001287487 | 1,898,805 | 5.0 | 2.83% | **2.669 ticks** | small-tick bucket |
| 100000211520 | 164,528 | 5.0 | 0.00% | 29.131 ticks | small-tick bucket |

**All three have tick = 5.0 and trade near 147,000**, so the tick-to-price ratio
is identical (~3.4e−5). The spec's criterion selects on *spread in ticks*, not on
tick size — the contrast is **liquidity**, not tick. H1's causal attribution is
therefore not identified, and the deciles/plot have no axis to vary along.

### 17.2 Queue reconstruction

`QP_i` required a FIFO queue per price level: `tick → {oid: remaining_size}`.
Python dicts preserve insertion order, which **is** exchange time priority.
Partial fills (all `Change` events) update the value in place, correctly
**keeping** priority.

Verified, 2025-09-30: 5,358,363 level-1 cancellations with QP computed, mean QP
**0.4063** bid / **0.4011** ask; front-of-queue (QP≤0.20) **34.4%** of cancelled
volume, back (QP>0.80) 22.5%.

**Run lengths — the constraint made visible:**

| measure | WIN (narrow) | control (wide) |
|---|---:|---:|
| `UR_price` mean run length | **1.64** | **2.14** |
| `QCR` mean run length | 4.79 / 4.88 | — |

Price undercutting runs are capped near 1.6 because after one undercut the spread
is already at the floor; queue cancellation runs average ~4.8.

### 17.3 Hypothesis results (48 specifications)

**H1 — not supported in either regime.** One marginal hit per bucket out of four
horizons (WIN p=0.044 at 1 s; control p=0.019 at 5 s), consistent with chance
over 8 tests.

**H2 gate — FAILS in both regimes.** In WIN the backward coefficient is **235×**
the forward one (fwd 0.0065, bwd −1.5300 at 5 s).

**H2 — the spec's own criterion is not met in WIN.** `FQCR` R²=0.136% vs
`AggCancel` R²=0.129%, with z of **6.09 vs −27.43** — the aggregate measure
dominates. In the control `FQCR` does beat `AggCancel` (R² 0.093% vs 0.051%), but
the gate failed there too and R² is negligible.

**H3 — null everywhere.** Logit on `QCR` insignificant at every horizon (p=0.07 to
0.65), and signed drift after high-`QCR` events (−0.011 to −0.027 in WIN) is
*smaller* than the baseline absolute drift (0.11 to 1.00).

### 17.4 Patches (32 further specifications)

Three accepted modifications, implemented in `spec_patches.py` kept separate from
the spec tests.

**P2b — placebo by partial R², the decisive result.** The patch proposed
comparing raw coefficients; those come from regressions with different dependent
variables and are not commensurable. Partial R² is scale-free:

| regime | k | forward | backward | ratio | verdict |
|---|---|---:|---:|---:|---|
| **WIN** | 5 s | 0.0001% | **8.1469%** | **58,916×** | REACTIVE |
| WIN | 10 s | 0.0005% | 6.9006% | 13,287× | REACTIVE |
| control | 5 s | 0.0181% | 0.0017% | 0.09 | ok |
| control | 10 s | 0.0097% | 0.0287% | 2.96 | REACTIVE |

For `QCR`, REACTIVE in **all four** cells.

**P2a — lead-lag with lagged return and OFI controls.** The joint F-test on the
`FQCR` lag block is significant in WIN (p<0.00001) but `β₀ = −0.0592` is
**negative** — pulling bids "predicts" price rising, against the mechanism. The
block is loaded with reaction, not anticipation.

**P3b — intra-asset regime test, the cleanest identification available.** Within
WIN, holding instrument, participants and tick fixed:

| regime | signal | p | partial R² |
|---|---|---:|---:|
| **constrained** (improvement illegal) | `fqcr` | 0.787 | **0.0000%** |
| constrained | `ur_price` | 0.767 | 0.0000% |
| unconstrained | `ur_price` | 0.0007 | 0.0056% |
| unconstrained | `fqcr` | 0.269 | 0.0006% |

**In the constrained regime — exactly where the substitution hypothesis predicts
queue signals should carry the load — `FQCR` has partial R² of 0.0000%.**

> **Note on window-level vs observation-level regimes.** WIN windows split
> 36.1% constrained / 63.9% unconstrained, which is *not* in conflict with
> "spread is 1 tick 99% of the time". A 1-second window is classed constrained
> only if **every** observation in it was 1 tick; at ~65 events/second with ~1%
> wider, that is ~0.99^65 ≈ 52% of seconds. The two figures measure different
> things.

**P1a (event-driven windows) was not run.** It would sharpen measurement but
cannot reverse a 59,000× reactive ratio.

---

<a name="18"></a>
## 18. Engineering incidents worth recording

**Process-pool deadlock.** The two-instrument pipeline hung with six workers in
`futex_do_wait` at 0.0% CPU for 23 minutes. Cause: the parent used polars between
the two pools (concatenating and writing WIN's panel), starting polars' rayon
thread pool; `ProcessPoolExecutor` then **forked**, and a forked child inherits
the pool's locks without the threads holding them. The first pool worked only
because the parent had not yet touched polars. Fixed with
`mp.set_start_method("spawn")` plus one-instrument-per-invocation.

**Self-killing `pkill`.** `pkill -f fila_pipeline.py` matched its **own shell**,
whose command line contained the pattern, killing the command issuing it — twice,
before the fix could be written. Resolved by patching with the editor rather than
a shell heredoc, and killing by PID with a self-exclusion.

**Analysis bugs found and corrected in my own work:** a 100% directional hit rate
that was an artifact of computing sign agreement on an `|R|` target; double-counted
depth buckets (100.06%) from aggressive orders in a locked book; a month panel
identical to a single-day panel because `gerar_dashboard` used `.iloc[0]`; and an
overstated claim that the wide-spread reversal contradicted theory when it is
largely proportional scaling.

---

<a name="19"></a>
## 19. Final state — every channel tested

| channel | measure | verdict |
|---|---|---|
| **PRICE** | undercutting (window) | null; preliminary results were auction windows + mis-measured spread + window-mean overlap |
| **PRICE** | undercutting (event study, 1.47M events) | **reversal**, −1.7 pts on the uncontaminated midpoint |
| **PRICE** | `UR_price` runs, two regimes | null; run length mechanically capped at 1.64 |
| **QUANTITY** | depth / net flow | null beyond OBI, which is reproduced only as a positive control |
| **TIMING** | lifetime imbalance | null |
| **TIMING** | cancellation rate | significant, then **withdrawn** on reverse causality (F4) |
| **QUEUE** | `FQCR`, `QCR` | null or **REACTIVE** (up to 58,916×) |

**The defensible claim:**

> On WIN over September 2025, passive liquidity provision carries no detectable
> anticipatory information in any of its dimensions — price, quantity, timing or
> queue position. The tick forecloses the price channel by construction (97% of
> improvements are mechanically pinned to one tick, run length capped at 1.64),
> and no other channel substitutes for it. Apparent effects in every case proved
> to be either queue mechanics or reaction to price moves that had already
> happened.

Specification count across the project: **~160**, all disclosed by the scripts
that produced them.

