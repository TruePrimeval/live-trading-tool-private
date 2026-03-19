# pbD Market Context Reference

> Extracted from TTT Masterclass Course material. Technical reference for building the LTT daily planning UI.

---

## What is pbD?

**pbD** stands for **p, b, D** — the three Volume/Market Profile shapes that define market structure:

- **P-profile** = bullish imbalance (broad base at top, narrow foot at bottom). Price accepted higher prices. Longs favoured.
- **b-profile** = bearish imbalance (broad base at bottom, narrow foot at top). Price accepted lower prices. Shorts favoured.
- **D-profile** = balance/neutral (symmetrical bell curve around POC). No dominant side. Mean reversion.

These three shapes are the foundation of the entire TTT trading methodology. Everything — daily context, trade direction, entry logic, invalidation — derives from identifying which structure the market is in and how price relates to it.

---

## Core Concepts

### Value Area (VA)
- **VA** = price range where 70% of volume traded in a given period
- **VAH** = Value Area High (top of the 70% zone)
- **VAL** = Value Area Low (bottom of the 70% zone)
- **vPOC** = Volume Point of Control — single price with highest volume. Acts as magnet.
- VAH/VAL = the **"business zone"** — primary support/resistance for mean reversion trades

### Single Prints
- Single TPOs between two value areas (in Market Profile) or low volume nodes (in Volume Profile)
- Act as **support/resistance** but ONLY if price doesn't stay there >30 minutes
- If price lingers at single prints >30M, they will likely break in trend continuation

### Tails (Buying/Selling)
- At least 3 consecutive TPOs at the extreme top or bottom of a profile
- **Buying tail** (bottom) = aggressive buying rejection = bullish
- **Selling tail** (top) = aggressive selling rejection = bearish
- Especially strong if they appear on 2+ consecutive days

#### Real vs Fake Tails
| Criteria | Real (Strong) Tail | Fake / Poor Tail |
|----------|-------------------|------------------|
| TPO count | 3+ single print TPOs at extreme | 1-2 TPOs only ("poor high" / "poor low") |
| Session timing | Formed mid-session, confirmed by next time period | Formed in LAST period = "spike" — unconfirmed, unreliable |
| Volume taper | Clear volume tapering toward extreme — fewer contracts at each price as it approaches high/low | No clear taper — messy, no aggressive rejection visible |
| Implication | Strong S/R — price rejected aggressively, unlikely to return soon | Unfinished business — price WILL likely revisit and test this level |
| Directional weight | Strong — confirms bias in tail direction | Weak or opposite — poor highs/lows invite retest |

**Key rule:** Poor highs/lows are the OPPOSITE of real tails — they signal the market will come back to test that level, not that it was rejected.

### Initial Balance (IB)
- Price range within the **first 30 minutes** of the session open
- **Small IB** → high probability of a **Trend Day** (strong directional move coming)
- **Large IB** → high probability of a **Range Day** (contained within IB extremes)
- Critical for determining the type of day EARLY in the session

### 30-Minute Close Rule
- A 30-minute candle CLOSE within a zone = **acceptance** (the market considers this price fair)
- A mere wick/pierce is NOT acceptance
- This is the primary signal giver throughout the methodology
- Wait for 2nd 30M candle close to confirm (stronger confirmation)

---

## Step 1: Pre-Session — Gain Overview (Multi-Day Context)

Before looking at today, assess the bigger picture across recent days:

### 1.1 Value Area Direction
| Pattern | Meaning | Bias |
|---------|---------|------|
| Rising VAs (each day's VA higher than previous) | Buyers accepting higher prices | Bullish |
| Falling VAs (each day's VA lower than previous) | Sellers enforcing lower prices | Bearish |
| Overlapping VAs | Balance / consolidation | Neutral — breakout coming |

### 1.2 vPOC Direction
| Pattern | Meaning | Bias |
|---------|---------|------|
| Rising vPOCs | Fair value migrating up | Bullish |
| Falling vPOCs | Fair value migrating down | Bearish |
| Flat/alternating vPOCs | Consolidation | Neutral |

### 1.3 Who Drives the Market?
| Signal | Driver | Implication |
|--------|--------|-------------|
| Buying/Selling tails present | Long-term players | Strong, sustainable trend |
| Trend profiles (P/b) present | Long-term players | Strong, sustainable trend |
| Weak highs/lows, no tails | Short/mid-term players | Trend may reverse or correct |
| Several overlapping D-profiles | Neither | High probability of explosive move coming |

### 1.4 Anomalies
- **Weak highs** = high of day that has thin volume / single TPO. Will be tested/resolved.
- **Weak lows** = low of day with thin volume / single TPO. Will be tested/resolved.
- **Naked (untested) vPOCs** = targets. Price is drawn to untested POCs.
- **Naked VAH/VAL** = untested levels from prior days. Act as S/R targets.
- **Single prints between value areas** = always get resolved (filled), though not necessarily same day.

---

## Step 2: Determine Previous Day's Profile

Classify what happened yesterday. This sets today's context.

### Market Profile Day Types

| Day Type | Profile Shape | Characteristics | How to Trade |
|----------|--------------|-----------------|--------------|
| **Normal Day (D)** | Symmetrical D | Compact TPO block around vPOC. Opens near VA, stays within. Balanced. | Mean reversion VAH↔VAL. Fade extremes. |
| **Normal Day Variation** | D with extension | Like Normal Day but with impulse outside VA that returns. One side briefly wins then balance restored. | Mean reversion. Trend continuation possible only after pullback to VA. |
| **Inside Day** | Very narrow D | Extremely compressed range. Low volume. TPOs close together. Often before major news. | DO NOT trade aggressively. Prepare for explosive breakout next day. |
| **Trend Day (P-profile)** | P shape | Price moves up quickly. Clear imbalance. Staircase TPOs upward. | Trade WITH the trend only. Do NOT trade against P-profiles. Breakouts on retests. |
| **Trend Day (b-profile)** | b shape | Price moves down quickly. Clear imbalance. Staircase TPOs downward. | Trade WITH the trend only. Do NOT trade against b-profiles. Breakouts on retests. |
| **Double Distribution** | Two D zones + single prints | Started balanced, broke out, formed new balance. Two separate VA zones. | Trade within each VA. Break of VA = entry signal. Re-enter on retest of single print transition zone. |

### Key Rules from Profile Type
- **Trend profiles (P/b) are STRONG** — never trade against them
- **D-profiles** — trade VAH/VAL as mean reversion
- **Multiple consecutive D-profiles** with no trend in between → expect explosive breakout
- **Double Distribution** — if price opens in one VA, it will likely fill the single print gap to test the other VA

---

## Step 3: Determine Close Location

Where did yesterday's session CLOSE relative to its own Value Area?

| Close Location | Meaning | Next Day Bias |
|----------------|---------|---------------|
| **Within VA** | Neutral / balanced | If next day also opens within VA → likely stays in VA range |
| **Above VA (above VAH)** | Buyer dominance | Bullish bias. VAH becomes support. |
| **Below VA (below VAL)** | Seller dominance | Bearish bias. VAL becomes resistance. |

---

## Step 4: Determine Today's Open Location (The 4 Variants)

This is the critical daily context setter. Where price opens relative to yesterday's VA determines the trading variant for the day.

### Variant 1: Open Within VA (between VAH and VAL)

**Context:** Balanced open. Market considers yesterday's value fair.

**Logic:**
- Once price moves from open toward VAH or VAL and stays there ≥30 minutes → high probability it traverses the entire VA to the opposite side
- If opens near vPOC and moves to VAH → expect full traverse to VAL
- If opens near vPOC and moves to VAL → expect full traverse to VAH

**Trade:**
- Entry at VAH or VAL (whichever price reaches first)
- TP at opposite VAH/VAL
- SL immediately beyond the absolute high/low of the day

**Key rule:** Pay attention to trend direction from Step 1.

---

### Variant 2: Open Between VA-Absolute and VAH/VAL

**Context:** Price opened outside the VA but still within the previous day's absolute range (between PDH/PDL and VAH/VAL). Out of balance — market will likely test fair value.

**Logic:**
- Price is likely heading toward previous day's vPOC
- Once it hits vPOC and bounces → trade back to today's opening price

**Trade:**
- Wait for price to reach vPOC
- Look for reversal pattern at vPOC (footprint, candle confirmation)
- Entry at vPOC bounce
- TP = today's opening price
- SL immediately above/below vPOC

**Key rule:** The trade is NOT "to the vPOC" — it's "FROM the vPOC back to open."

---

### Variant 3: Open Completely Outside VA-Absolute (and stays outside)

**Context:** Strong gap/imbalance. Price opened completely outside yesterday's range and does NOT return to it.

**Logic:**
- Very strong conviction by buyers/sellers
- Expect strong acceleration AWAY from the previous VA
- Must look at other profiles (weekly, composite) to find S/R targets
- Need to determine: technical gap (fills fast) vs fundamental gap (fills slowly/never)

**Trade:**
- Trend-following structure
- Entry on retests of structure breaks
- Targets from big-picture VP levels (naked POCs, prior VAs, LVNs)
- SL between VA-Absolute and VAH/VAL

**Key rule:** This is the hardest variant. Fast-moving, difficult entries. Look at higher timeframe levels.

---

### Variant 4: Open Outside VA-Absolute, Then Returns Into VA

**Context:** Price opened outside but moved back into the previous day's VA and STAYED there ≥30 minutes.

**Logic:**
- **80% probability** that the entire previous day's VA will be fully traversed
- This is the "80% rule" — one of the highest-probability setups

**Trade:**
- Entry at VAH or VAL (do NOT enter at VA-Absolute)
- TP at opposite VAH/VAL (full VA traverse)
- SL between VAH/VAL and previous day's absolute high/low

**Key rule:** Once price has traded through entire VA and established above/below, likely continues in that direction.

---

## Step 5: Derive Scenario and Strategy

Combining the above information:

### Scenario Derivation Matrix

| Open Location | Previous Profile | IB Size | Scenario | Strategy |
|--------------|-----------------|---------|----------|----------|
| Within VA | D (balanced) | Large | Range day | Mean Reversion VAH↔VAL |
| Within VA | D (balanced) | Small | Trend day likely | Wait for direction, then trend-follow |
| Within VA | P/b (trending) | Any | Continuation | Trade with trend, not against |
| Between VA-abs & VAH/VAL | D | Any | Reversion to fair value | Trade from vPOC bounce to open |
| Between VA-abs & VAH/VAL | P/b | Any | Retest of trend structure | Cautious — POC may hold or break |
| Outside VA-abs (stays out) | Any | Small | Strong trend continuation | Trend-follow, find new S/R levels |
| Outside VA-abs → returns in | Any | Any | VA traverse (80% rule) | Full VA traverse from VAH/VAL |

### Strategy Summary

| Strategy | When | How |
|----------|------|-----|
| **Mean Reversion** | D-profiles, open within VA, large IB | Fade VAH/VAL extremes. TP at opposite side. |
| **Breakout** | P/b profiles, small IB, consecutive inside days | Trade confirmed breaks (2nd higher high / 2nd lower low). Never first break. |
| **Trend Continuation** | Open outside VA, strong trend profiles | Join direction of big money. Re-enter on small pullbacks/retests. |

---

## Step 6: Draw Zones and Set Targets

Before trading:
1. Draw previous day's **VAH, VAL, vPOC** as horizontal S/R
2. Mark **single prints** as S/R zones
3. Identify **naked (untested) vPOCs** from prior days as targets
4. Mark **naked VAH/VAL** from prior days as targets
5. Note any **LVNs** (Low Volume Nodes) — these are pass-through zones / targets
6. Transfer all zones to the 30M chart for day trading

### Target Priority
1. Nearest naked vPOC
2. Opposite VAH/VAL (for full VA traverse trades)
3. Single print fill zones
4. LVN zones (market skips through these)
5. Big-picture composite VP peaks/valleys

---

## Step 7: Wait for Confirmation

**Do NOT enter without confirmation:**

1. **30M close** within the zone = acceptance (primary signal)
2. **Footprint confirmation** — absorption, imbalance, delta exhaustion at the level
3. **CVD confirmation** — divergence or strength aligning with trade direction
4. **Candle pattern at S/R** — hammer, outside bar, inside bar breakout (ONLY at VP levels, never in isolation)
5. **R:R must be ≥ 2:1** — do not trade below this

### Candle Patterns (Only Valid at VP S/R Levels)
| Pattern | Signal | Context Required |
|---------|--------|-----------------|
| Hammer / Shooting Star | Reversal | Must be at VAH/VAL/vPOC after a trend |
| Inside Bar | Breakout setup | Enter when price breaks mother bar range |
| Outside Bar | Momentum / reversal | Bullish outside bar after downtrend = reversal |
| 3 in a Row | Strong direction | 3 consecutive same-direction candles from S/R = high probability trend |
| Doji | Indecision / reversal pending | At extremes only |

---

## pbD Setup Entry Logic (P-Setup and b-Setup)

### P-Setup (Bullish — upward acceptance)

The P-profile forms when price accepts higher prices. Entry logic in 3 stages:

1. **Pullback to Business Zone** — after acceptance, price pulls back to former VAH/single print edge. First entry.
2. **Break-In to P-Profile** — price re-enters the balance from below. Second entry.
3. **Break-Out from Balance** — price breaks above the P-profile range. Third entry.

- SL: below vPOC (or below the profile foot)
- TP: swing high of the impulse that started the move
- Invalidation: clear trend breakout downward or lower lows

### b-Setup (Bearish — downward acceptance)

Mirror of P-Setup:

1. **Pullback to Business Zone** — price pulls back up to former VAL/single print edge. First entry short.
2. **Break-In to b-Profile** — price re-enters the balance from above. Second entry.
3. **Break-Out from Balance** — price breaks below the b-profile range. Third entry.

- SL: above vPOC (or above the profile foot)
- TP: swing low of the impulse that started the move
- Invalidation: clear trend breakout upward or higher highs

### Range Definition (for P/b setups)
- **Upper boundary**: defined by 2 candles closing BELOW after an up-move (for P)
- **Lower boundary**: defined by 2 candles closing ABOVE after a down-move (for b)
- SL set at middle of range (vPOC)
- It is NOT about red/green candles — it's about where candles CLOSE

---

## Trade Setup — Opening Variants (IMPLEMENTED in LTT)

Separate section between Phase 2 (context) and Phase 3 (key levels). Per asset, user selects which variant applies based on where TODAY's price opened.

| Variant | Today's Open | Entry | TP | SL | Image |
|---------|-------------|-------|----|----|-------|
| 1 | Within VA | VAH or VAL (whichever reached first, stays 30min) | Opposite VAH/VAL | Beyond absolute H/L | `variant_1_within_va.png` |
| 2 | Between VA-abs and VAH/VAL | vPOC after bounce confirmation | Today's opening price | Beyond vPOC | `variant_2_va_absolute.png` |
| 3 | Outside VA-abs (stays out) | Retests of structure breaks | Big-picture levels, naked POCs | Between VA-abs and VAH/VAL | `variant_3_outside_stays.png` |
| 4 | Outside VA-abs → returns into VA (30min) | VAH or VAL (NOT VA-absolute) | Opposite VAH/VAL (80% traverse) | Between VAH/VAL and absolute H/L | `variant_4_outside_returns.png` |

UI: 4 clickable image cards in a row. Selected variant shows details panel with Entry/TP/SL.

---

## Breakout Models (4 Types)

### Model 1: Breakout from D-Profile (Normal Range)
- Wait for **2nd higher high** (longs) or **2nd lower low** (shorts) — never trade 1st breakout
- 1st breakout can be fake/liquidity test. 2nd confirms imbalance shift.

### Model 2: Breakout from P/b-Profile (Trend)
- Market already trending. Look for 2nd HH/LL for continuation.
- Less explosive than Model 1 but very consistent.

### Model 3: Break-In Then Breakout (Counter-Trend)
- Market opens outside old VA → breaks BACK IN → then breaks out the other side
- Requires narrow IB
- Very powerful: trapped traders create momentum

### Model 4: Trading Into a P-Profile
- Market moves back into old P-profile's VA
- If turns at vPOC → trend continuation (enter with trend)
- If goes deeper below VA → needs time to build new balance (be patient)

---

## LTT Asset Tab Spec — Daily Context Derivation

> This is the exact UI sequence each asset tab walks through. Each step must be completed before proceeding to the next. The combination of all inputs produces a **derived context** (the day's trading verdict).

### Step 1: Previous Day Profile Type (visual selection)

User selects ONE schematic. Displayed as clickable visual cards with the profile image + name.

| Option | Profile Shape | Inherent Bias Weight |
|--------|--------------|---------------------|
| Normal Day | D (symmetrical) | Neutral → Mean Reverting |
| Normal Day Variation | D with extension | Neutral → Mean Reverting (slight directional lean possible) |
| Inside Day | Narrow D (compressed) | Neutral → DO NOT TRADE (expect explosive move next day) |
| Trend Day (P-profile) | P shape (bullish) | Strong Long |
| Trend Day (b-profile) | b shape (bearish) | Strong Short |
| Double Distribution | Two D zones + single prints | Neutral → trade within each VA, breakout at transitions |

Each option shows its schematic image. Selected option is highlighted.

---

### Step 2: Previous Day Open Location

Where did price OPEN relative to previous day's VA?

| Option | Value |
|--------|-------|
| Above VAH | Bullish weight |
| Within VA | Neutral weight |
| Below VAL | Bearish weight |

---

### Step 3: Previous Day Close Location

Where did price CLOSE relative to previous day's VA?

| Option | Value |
|--------|-------|
| Above VAH | Bullish weight |
| Within VA | Neutral weight |
| Below VAL | Bearish weight |

---

### Step 4: Tail Quality

Did the previous day's profile show buying or selling tails?

| Option | Directional Weight |
|--------|-------------------|
| Strong Buying Tail (3+ TPOs, confirmed mid-session) | Bullish — aggressive rejection of lows |
| Strong Selling Tail (3+ TPOs, confirmed mid-session) | Bearish — aggressive rejection of highs |
| Weak/Poor Tail (1-2 TPOs or end-of-session spike) | Opposite — poor highs/lows invite retest |
| None | No tail weight |

---

### Step 5: Initial Balance (IB)

Size of the first 30 minutes of today's session.

| Option | Implication |
|--------|------------|
| Large IB | Confirms Mean Reverting day expectation (range contained) |
| Small IB | Confirms Trending day expectation (directional move coming) |

---

### Step 6: DERIVED CONTEXT (auto-calculated output)

The combination of Steps 1-5 produces the day's verdict. Displayed as a **large dominant box**.

#### Possible Outputs
| Context Label | Meaning |
|--------------|---------|
| **MEAN REVERTING** | Range day, trade both long and short within VA |
| **MEAN REVERTING LONG** | Range day but only take long trades (directional bias within range) |
| **MEAN REVERTING SHORT** | Range day but only take short trades (directional bias within range) |
| **TREND LONG** | Trending bullish — only take longs, trade with momentum |
| **TREND SHORT** | Trending bearish — only take shorts, trade with momentum |

#### Display
- **Large label** (e.g. "TREND SHORT") — dominant, colour-coded
- **Confidence %** below — based on signal alignment (how many of the 5 inputs agree)

#### Scoring Logic

Each input contributes a directional score from -2 (strong short) to +2 (strong long):

| Input | Strong Short (-2) | Short (-1) | Neutral (0) | Long (+1) | Strong Long (+2) |
|-------|------------------|-----------|-------------|----------|-----------------|
| Day Type | Trend Day b | — | D / Inside / DD / NDV | — | Trend Day P |
| Open Location | — | Below VAL | Within VA | Above VAH | — |
| Close Location | — | Below VAL | Within VA | Above VAH | — |
| Tails | — | Strong Selling Tail | None / Weak | Strong Buying Tail | — |
| IB Size | — | Small (trend confirm if other signals short) | — | Small (trend confirm if other signals long) | Large = Mean Reverting weight |

**Direction score** = sum of all directional weights

**Structure score** (Mean Reverting vs Trending):
- Day Type D/NDV/Inside/DD + Large IB → Mean Reverting
- Day Type P/b + Small IB → Trending
- Mixed signals → use direction score magnitude to decide

**Confidence %:**
- Count how many of the 5 inputs agree with the derived context
- 5/5 = 100%, 4/5 = 80%, 3/5 = 60%, 2/5 = 40%, 1/5 = 20%

**Mapping:**
- If structure = Mean Reverting AND direction score = 0 → **MEAN REVERTING**
- If structure = Mean Reverting AND direction score > 0 → **MEAN REVERTING LONG**
- If structure = Mean Reverting AND direction score < 0 → **MEAN REVERTING SHORT**
- If structure = Trending AND direction score > 0 → **TREND LONG**
- If structure = Trending AND direction score < 0 → **TREND SHORT**
- If structure = Trending AND direction score = 0 → **TREND** (no direction yet — wait for IB break)

**Inside Day special case:**
- If day type = Inside Day → output is **CAUTION — INSIDE DAY** (with optional long/short bias suffix)
- Confidence is reduced ×0.6 (signals unreliable on inside days)
- Course says: do NOT trade aggressively, prepare for explosive breakout next day
- Colour: amber warning

---

### After Context is Set

Full sequence per asset:
1. **Phase 2** — Market Context (5-step derivation) ✅ implemented
2. **Phase 3** — Key Levels (S/R, trend lines, price alerts) ✅ implemented
3. **News Events** — manual check with preset high-impact events. Hard gate if active. Blocks trade entry 30min before/after event.
4. **Trade entry** — pre-trade gate inline in trade form

The derived context label persists in the asset tab header and influences which trade grades are available (e.g. if MEAN REVERTING LONG → only AAA/Mean Reversion grade enabled).

### IB Timing Per Asset Class
- **Crypto** (SOL, BTC, ETH, SUI): IB = first 30min after midnight UTC (daily open)
- **Futures** (MNQ, MES): IB = first 30min after NY session open (9:30 ET). Context is PROVISIONAL until 10:00 ET.
- Default IB to "Pending" for futures until session opens. Show live clock + notification when IB window closes.
- For crypto: can set IB immediately since daily open is known.

### News Events (after Phase 3, before trade entry)
- Manual toggle: "High-impact news event today?"
- Preset list of events ranked by impact:
  - **EXTREME (DO NOT TRADE):** FOMC Rate Decision, NFP, CPI/Core CPI, PPI, PCE Core, Fed Chair speaks
  - **HIGH (DO NOT TRADE):** FOMC Minutes, ECB/BOJ decisions
  - **MEDIUM (CAUTION):** GDP advance, Jobless Claims (if unexpected)
- Enter event time → persistent red warning banner with countdown
- **Hard gate:** 30min before and after event → app blocks trade entry
- Core rule: do NOT trade during news events. No exceptions.

---

## Data Persistence — Context History & Trade Association

### Daily Context CSV Log

Every time a market context is confirmed for an asset, a row is appended to `context_history.csv`:

```
date,asset,day_type,open_location,close_location,tail,ib_size,context_label,confidence,direction_score,structure
2026-03-18,SOL,Normal Day,Within VA,Below VAL,None,Large IB,MEAN REVERTING SHORT,40,-1,MR
2026-03-18,BTC,Trend Day (P-profile),Above VAH,Above VAH,Strong Buying Tail (3+ TPOs),Small IB,TREND LONG,100,4,TR
```

This file builds up over time for post-session analysis: which contexts were traded, win rates per context type, etc.

### Trade ↔ Context Association

Every trade record (in `session.json` active trades, `history.json` completed trades) carries the daily context of the asset it was traded on:

```json
{
  "instrument": "SOL",
  "grade": "AAA",
  "rr_target": 3.0,
  "context": {
    "day_type": "Normal Day",
    "open_location": "Within VA",
    "close_location": "Below VAL",
    "tail": "None",
    "ib_size": "Large IB",
    "context_label": "MEAN REVERTING SHORT",
    "confidence": 40
  }
}
```

**How context is attached:**
1. When a **new trade is opened manually** → the instrument name is matched to an asset tab → that asset's confirmed Phase 2 context is copied into the trade record
2. When **CSV data is imported** from an exchange → the instrument/symbol is matched to an asset tab → same context attachment
3. When **WebSocket sync** detects a fill → the instrument from the fill is matched to an asset tab → same context attachment

**Matching logic:** Trade instrument name (e.g. "SOL", "BTC", "MNQ") must match an asset tab name. If no matching asset tab has a confirmed context, the trade is recorded without context metadata.

### Analysis Use Cases (future)
- Win rate by context label (e.g. "MEAN REVERTING LONG trades win 72% vs TREND SHORT at 45%")
- Confidence vs outcome (do higher confidence contexts produce better trades?)
- Day type distribution (which day types appear most often per asset?)
- Context override analysis (did you trade against the derived context? What happened?)

---

## Key Rules (Never Break These)

1. **Never trade against P/b (trend) profiles**
2. **Never enter on the 1st breakout** — wait for 2nd HH/LL
3. **30M close = acceptance** — don't act on wicks/pierces
4. **Don't diddle in the middle** — never trade at/near vPOC (unless it's your target)
5. **Never trade between two zones** — wait patiently for price to reach a zone
6. **R:R must be ≥ 2:1** — skip anything below this
7. **Candle patterns only valid at VP/MP levels** — never in isolation
8. **If price stays at single prints >30M** — they will break (trend continuation)
9. **80% rule**: open outside VA + return into VA + stay 30M = full VA traverse
10. **Anomalies always get resolved** — weak highs/lows, single prints, naked POCs

---

## Source Documents

| Document | Location | Content |
|----------|----------|---------|
| Planning and Analysis ENG | TTT Documents/01 | Daily planning template + checklist |
| P-Setup trading edge logic | TTT Documents/02 - PbD Playbook | P-profile entry logic (3 stages) |
| b-Setup trading edge logic | TTT Documents/02 - PbD Playbook | b-profile entry logic (3 stages) |
| pbD setup schematics Primeval | TTT Documents/02 - PbD Playbook | Visual schematics (current LTT images) |
| Breakout Strategy (Tom) | TTT Documents/02 - PbD Playbook | 4 breakout models |
| Volume Profile Mastery | Part 4 | VP concepts, 4 trading variants, P/b/D structures |
| Market Profile Mastery | Part 5 | MP day types, balance/imbalance, IB, tails |
| Actionable Checklists | TTT Documents/07 | Pre-trade, execution, post-trade checklists |
