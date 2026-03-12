# Live Trading Tool — Session Handoff
> Last updated: 2026-03-12

---

## WHERE WE LEFT OFF (start here next session)

**2026-03-12 — Streamlit Cloud deployment + Supabase:**
- Both apps moved to `12_Trading_Journaling_Software_Primeval/` as canonical location
- **Both apps deployed to Streamlit Cloud — public repos, password protected**
- GitHub repos made public (password gate protects content — nobody gets in without password)
- Supabase replaces local JSON files for prefs/session/history on cloud
- Password gate added to both apps — password: `PrimevalTradingApps-01` (in Streamlit Secrets)
- OKX credentials load from Streamlit Secrets first, fall back to local file
- New GitHub repos: `TruePrimeval/live-trading-tool-private` + `TruePrimeval/trading-analyser-private`
- `supabase >= 2.0` added to LTT requirements.txt
- URLs: add here when confirmed

**2026-03-12 — LaunchAgents removed (VPN fix):**
- Removed all auto-start LaunchAgents — caused ProtonVPN kill switch to trigger on login
- **Both apps must be started manually** (after VPN is connected)
- **Do NOT recreate auto-start LaunchAgents**

**2026-03-09 — Morning Report Card + Safe Mode checklist built**

---

## Canonical Locations

| What | Path |
|------|------|
| **Live Trading Tool** (edit here) | `12_Trading_Journaling_Software_Primeval/2_Live Trading Tool/` |
| **Trading Analyser** (edit here) | `12_Trading_Journaling_Software_Primeval/1_Trading_Analyser/` |
| Old LTT copy (stale — do not edit) | `~/live-trading-tool/` |
| Old TA copy (stale — do not edit) | `12_Trading_Analyser/` |

---

## How to Run

**Locally:**
```bash
cd "12_Trading_Journaling_Software_Primeval/2_Live Trading Tool"
python3 -m streamlit run app.py --server.port 8502
# Opens at http://localhost:8502
# Password: PrimevalTradingApps-01
```

**Cloud:** Streamlit Cloud (URL in browser bookmarks)
- Password: `PrimevalTradingApps-01`

---

## File Structure
```
2_Live Trading Tool/
├── app.py                       # Main app — ~2460 lines
├── .streamlit/secrets.toml      # Local secrets — gitignored, never commit
├── requirements.txt             # streamlit, pandas, websocket-client, autorefresh, plotly, supabase
├── .gitignore                   # Excludes secrets, JSON data files
├── HANDOFF.md                   # This file
├── prefs.json                   # Local fallback only (cloud uses Supabase)
├── session.json                 # Local fallback only (cloud uses Supabase)
├── history.json                 # Local fallback only (cloud uses Supabase)
└── exchange_config.json         # Local OKX creds fallback — never commit
```

---

## Secrets Structure (.streamlit/secrets.toml)
```toml
[okx]
api_key    = "..."
secret_key = "..."
passphrase = "..."

[supabase]
url = "https://yawlvxyvuqpwthqxbrwp.supabase.co"
key = "sb_publishable_..."

[app]
password = "PrimevalTradingApps-01"
```

---

## Supabase Tables (ltt_prefs / ltt_session / ltt_history)
- Each table has a single row (`id = 1`) storing the full JSON blob
- Load: `SELECT data WHERE id = 1`
- Save: `UPSERT {id: 1, data: ...}`
- Falls back to local JSON files if Supabase unavailable
- Project: `yawlvxyvuqpwthqxbrwp.supabase.co`

---

## GitHub
- Repo: `TruePrimeval/live-trading-tool-private` (private)
- Push: `cd "2_Live Trading Tool" && git add app.py && git commit -m "msg" && git push`

---

## Architecture — Key Sections in app.py (~2460 lines)

| Section | Lines |
|---------|-------|
| Imports (threading, websocket, plotly, supabase) | 1–45 |
| Supabase client `_get_sb()` | ~46–58 |
| Prefs load/save (Supabase → local fallback) | ~60–105 |
| Session load/save + history engine | ~107–215 |
| Exchange config + CSV parsing (7 exchanges) + OKX REST | ~217–420 |
| **WebSocket engine** — `_WS_STATE`, `_handle_fill`, `_ws_run`, `_start_ws` | ~420–515 |
| Helpers: `_risk_score`, `_win_prob`, `_fmt` | ~517–545 |
| Morning Report Card data/logic | ~547–585 |
| Page config | ~587–595 |
| **Password gate** | ~597–606 |
| CSS | ~608–800 |
| Load state + WS start + auto-refresh | ~802–850 |
| **Sidebar** (kill switch, mode, safe mode, MRC, account, grades, API) | ~852–1020 |
| Computed values + checklist state | ~1022–1080 |
| Banners + page title | ~1082–1105 |
| **Safe Mode phase cards + forms (4 phases)** | ~1107–1315 |
| Standalone Morning Report Card | ~1317–1395 |
| Top bar (mode badge + WS status) | ~1397–1430 |
| Hero cards (Remaining R, Today's R, Wins, Losses) + mini stats | ~1432–1505 |
| **Secret Sauce panel** (Conservative + Competition) | ~1507–1695 |
| **Active Trade** (quick-launch, new trade form, EV bell curve, actions) | ~1695–2265 |
| Session Log table + delete | ~2267–2345 |
| Trade History (per-day expanders + delete + CSV export) | ~2347–2460 |

---

## WebSocket Flow (OKX)
```
App starts → _start_ws() → daemon thread → _ws_run(cfg)
  └─ on_open: HMAC-SHA256 login
  └─ login OK: subscribe orders SWAP channel
  └─ on_message:
       ├─ pnl == 0 → opening fill → query TP/SL → _WS_STATE["pending_open"]
       └─ pnl != 0 → closing fill → _WS_STATE["pending_close"]

Every 2.5s (autorefresh):
  └─ pending_open  → inject _ws_prefill → pre-populate new trade form
  └─ pending_close → auto-resolve active trade, archive, rerun
```
- `● LIVE` (green) | `◌ CONNECTING` (yellow) | `○ OFFLINE` (red) | `✕ AUTH FAILED` (red)
- Kill switch in sidebar: ENABLE SYNC / KILL ALL CONNECTIONS
- `connection_enabled` in prefs — OFF by default

---

## Safe Mode — 4-Phase Pre-Trade Checklist

| Phase | What | Gate |
|-------|------|------|
| 1 | Morning Report Card | Always required in safe mode |
| 2 | Market Context (state, D profile, price location, IB size) → auto-derives strategy | Unlocks after phase 1 |
| 3 | Key Levels (S/R marked, trend lines, alerts set) | Unlocks after phase 2 |
| 4 | Pre-Trade Gate (S/R level + footprint confirm) | Per-trade, resets each trade |

- Phase 2 auto-derives: price in D → Mean Reversion, outside D → Breakout
- `cl_gate` var (1–5) controls display. Gate 5 = trading unlocked.
- Redo buttons for each phase shown when complete

---

## EV Bell Curve (active trade)
- Plotly normal distribution, σ-band coloured (dark blue ±1σ, brownish-red ±2σ, green tails)
- White vertical line = this trade's EV position
- `EV = win_p × RR − (1 − win_p)` | `z = EV / 1.5`
- Requires `plotly` (`_HAS_PLOTLY` guard)

---

## Modes

### Standard
Plain session tracker.

### Secret Sauce (Fabio Risk Management)
**Conservative Mode:** AAA=0.35R | AA=0.25R | B+=0.15R | Max 5 trades | 1.25% daily cap
Auto-switches to Competition after 2 × ≥3R wins

**Competition Mode:** Reserve profit/3 | 2×0.5% units | Unit 2 locked until Unit 1 TP/BE
Phase tracker: 0 → 1 → 2 → 3

---

## Grade System

| Grade | Strategy | Implied R | Win Rate |
|-------|----------|-----------|----------|
| AAA | Mean Reversion | 0.35R | ~68% |
| AA | Breakout | 0.25R | ~38% |
| B+ | Lower Conviction | 0.15R | ~28% |

Quick-launch buttons: MEAN REVERSION (AAA) / BREAKOUT (AA) — pre-select grade above form.

---

## Risk Indicator
```python
exec_risk  = min(80, (rr_target / 6) * 80)   # RR=3→40, RR=6→80
grade_bias = {"AAA": -10, "AA": +5, "B+": +15}[grade]
addon_risk = add_on_count * 12
score      = min(100, exec_risk + grade_bias + addon_risk)
# LOW < 40 | MODERATE 40–69 | HIGH ≥ 70
```

---

## Data Persistence

**Cloud (Supabase):**
| Table | Written when |
|-------|-------------|
| `ltt_prefs` | Save Settings clicked |
| `ltt_session` | Every Win/Loss/BE/Add-on/Enter/Cancel |
| `ltt_history` | Immediately on every Win/Loss/BE |

**Local fallback:** same data written to `prefs.json`, `session.json`, `history.json`

---

## Morning Report Card

6 questions, score 0–18 → Grade A/B+/B/C/D/F → multiplier on daily loss limit.

| Score | Grade | Multiplier |
|-------|-------|-----------|
| 16–18 | A | 100% |
| 13–15 | B+ | 80% |
| 10–12 | B | 60% |
| 7–9 | C | 30% |
| 4–6 | D | 10% |
| 0–3 | F | 0% (do not trade) |

`effective_daily_limit_r = daily_limit_r × mr_multiplier`

---

## Position Sizer
```
Crypto (SOL/BTC/ETH/SUI):  Qty = Risk$ / |entry - stop|
MNQ futures:               Qty = Risk$ / (|entry - stop| × 2.0)
MES futures:               Qty = Risk$ / (|entry - stop| × 5.0)
Risk$ = balance × r_pct% × grade_implied_r
```

---

## Known Issues / Pending Items
- **WebSocket not tested live** — needs a real OKX trade to verify TP/SL detection end-to-end
- **Bybit WebSocket** — not implemented, CSV import only
- **`use_container_width` deprecation** — Streamlit cosmetic warning, harmless
- **Supabase `sb_publishable_` key format** — newer Supabase format, works with `supabase>=2.0`

---

## Run Commands
```bash
# Start locally
cd "12_Trading_Journaling_Software_Primeval/2_Live Trading Tool"
python3 -m streamlit run app.py --server.port 8502

# Syntax check
python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"

# Kill running instance
lsof -ti :8502 | xargs kill -9

# Install dependencies
pip3 install streamlit pandas websocket-client streamlit-autorefresh plotly supabase

# Push to cloud
git add app.py && git commit -m "msg" && git push
```

---

## Related Tools
- `1_Trading_Analyser/` — post-session FIFO PnL engine. GitHub: `TruePrimeval/trading-analyser-private`
- `11_Investing_Portfolio_Architect/` — Vite + React inverse risk weighting. Port 5173.
- Old shared Trading Analyser: `TruePrimeval/trading-analyser` (keep — others using it)
