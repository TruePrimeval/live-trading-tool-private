"""
Live Trading Tool — Day Trading Companion
Session R:R logger · Active trade management · Pyramiding · Risk indicator
Modes: Standard | Fabio Conservative Long Term | Fabio Competition
"""

import streamlit as st
import json, os, hmac, hashlib, base64, requests
import pandas as pd
import threading
import time as _time
from datetime import datetime, date

try:
    from streamlit_autorefresh import st_autorefresh as _autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False

try:
    import websocket as _websocket
    _HAS_WEBSOCKET = True
except ImportError:
    _HAS_WEBSOCKET = False

try:
    import plotly.graph_objects as _go
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False

try:
    from supabase import create_client as _sb_create
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False

# ─── PATHS ───────────────────────────────────────────────────────────────────
_APP_DIR      = os.path.dirname(os.path.abspath(__file__))
_SESSION_PATH = os.path.join(_APP_DIR, "session.json")
_PREFS_PATH   = os.path.join(_APP_DIR, "prefs.json")
_HISTORY_PATH = os.path.join(_APP_DIR, "history.json")

_OKX_CONFIG_CANDIDATES = [
    os.path.normpath(os.path.join(_APP_DIR, "..", "12_Trading_Analyser", "okx_config.json")),
    os.path.expanduser("~/trading-analyser/okx_config.json"),
    os.path.join(_APP_DIR, "okx_config.json"),
]

# ─── SUPABASE CLIENT ─────────────────────────────────────────────────────────
_LOCAL_DEV = os.environ.get("LTT_LOCAL", "0") == "1"

@st.cache_resource
def _get_sb():
    if _LOCAL_DEV or not _HAS_SUPABASE:
        return None
    try:
        return _sb_create(
            st.secrets["supabase"]["url"],
            st.secrets["supabase"]["key"],
        )
    except Exception:
        return None

# ─── PREFS ───────────────────────────────────────────────────────────────────
_PREFS_DEFAULTS = {
    "balance":        3800.0,
    "r_pct":          1.0,
    "daily_limit_r":  1.0,
    "addon_r":        0.1,
    "mode":                   "Standard",
    "fabio_submode":          "Conservative Mode",
    "morning_report_enabled": False,
    "safe_mode":              False,
    "connection_enabled":     False,  # kill switch — OFF by default
    "user_tz_offset":         1,     # UTC offset in hours (default: UTC+1 / CET)
    "grades": {
        "AAA": {"label": "AAA — Mean Reversion",   "implied_r": 0.35},
        "AA":  {"label": "AA  — Breakout",         "implied_r": 0.25},
        "B+":  {"label": "B+  — Lower Conviction", "implied_r": 0.15},
    },
}

def _load_prefs():
    sb = _get_sb()
    if sb:
        try:
            r = sb.table("ltt_prefs").select("data").eq("id", 1).execute()
            if r.data:
                saved = r.data[0]["data"]
                p = {**_PREFS_DEFAULTS, **{k: v for k, v in saved.items() if k != "grades"}}
                if "grades" in saved:
                    for g, gd in saved["grades"].items():
                        if g in p["grades"]:
                            p["grades"][g] = {**p["grades"][g], **gd}
                return p
        except Exception:
            pass
    if os.path.exists(_PREFS_PATH):
        try:
            with open(_PREFS_PATH) as f:
                saved = json.load(f)
            p = {**_PREFS_DEFAULTS, **{k: v for k, v in saved.items() if k != "grades"}}
            if "grades" in saved:
                for g, gd in saved["grades"].items():
                    if g in p["grades"]:
                        p["grades"][g] = {**p["grades"][g], **gd}
            return p
        except Exception:
            pass
    return {**_PREFS_DEFAULTS}

def _save_prefs(p):
    sb = _get_sb()
    if sb:
        try:
            sb.table("ltt_prefs").upsert({"id": 1, "data": p}).execute()
            return
        except Exception:
            pass
    with open(_PREFS_PATH, "w") as f:
        json.dump(p, f, indent=2)

# ─── SESSION ─────────────────────────────────────────────────────────────────
_SESSION_DEFAULTS = {
    "session_date":     "",
    "active_trades":    [],          # multi-trade: list of active trade dicts
    "completed_trades": [],
    "balance_override": None,
    "fabio_state": {
        "phase":            0,
        "consecutive_wins": 0,
        "reserve_r":        0.0,
        "unit1_status":     None,
        "unit2_status":     None,
    },
    "morning_report": {
        "completed":   False,
        "grade":       None,
        "multiplier":  1.0,
        "score":       None,
        "description": "",
        "color":       "white",
    },
    "checklist": {
        "phase1_complete": False,
        "assets": [],              # per-asset: [{name, phase2, phase2_complete, phase3, phase3_complete}]
        "news": {
            "checked":    False,
            "has_news":   False,
            "event_name": "",
            "event_time": "",
        },
    },
}

_ASSET_DEFAULTS = {
    "name":            "",
    "phase2":          {},
    "phase2_complete": False,
    "phase3":          {"sr_levels": False, "trend_lines": False, "price_alerts": False},
    "phase3_complete": False,
}

_MARKET_STATES = [
    "Normal Day",
    "Normal Day Variation (Long)",
    "Normal Day Variation (Short)",
    "Inside Day",
    "Trend Day (P-profile)",
    "Trend Day (b-profile)",
    "Double Distribution",
]

_VA_LOCATIONS = ["Above VAH", "Within VA", "Below VAL"]

_TAIL_OPTIONS = [
    "None",
    "Strong Buying Tail (3+ TPOs)",
    "Strong Selling Tail (3+ TPOs)",
    "Weak/Poor Tail (1-2 TPOs)",
]

_IB_OPTIONS = ["Normal IB", "Large IB", "Small IB"]

# ── pbD Context Derivation ──────────────────────────────────────────────────

# Direction scores: -2 strong short … 0 neutral … +2 strong long
_DAY_TYPE_SCORE = {
    "Normal Day": 0, "Normal Day Variation (Long)": 1, "Normal Day Variation (Short)": -1,
    "Inside Day": 0, "Trend Day (P-profile)": 2, "Trend Day (b-profile)": -2,
    "Double Distribution": 0,
}
_DAY_TYPE_STRUCTURE = {
    "Normal Day": "MR", "Normal Day Variation (Long)": "MR", "Normal Day Variation (Short)": "MR",
    "Inside Day": "MR", "Trend Day (P-profile)": "TR", "Trend Day (b-profile)": "TR",
    "Double Distribution": "MR",
}
_TAIL_SCORE = {
    "None": 0, "Strong Buying Tail (3+ TPOs)": 1,
    "Strong Selling Tail (3+ TPOs)": -1, "Weak/Poor Tail (1-2 TPOs)": 0,
}

# Open/Close RELATIONSHIP matrix — close is dominant signal per pbD/Dalton
# (open_loc, close_loc) → direction score
_OPEN_CLOSE_SCORE = {
    ("Above VAH", "Above VAH"):  2,   # Strong bullish — buyers maintained
    ("Above VAH", "Within VA"): -1,   # Bearish rejection — buyers failed, returned to balance
    ("Above VAH", "Below VAL"):  -2,  # Very bearish — complete reversal
    ("Within VA",  "Above VAH"):  1,   # Bullish breakout during session
    ("Within VA",  "Within VA"):   0,  # Neutral — balanced day
    ("Within VA",  "Below VAL"):  -1,  # Bearish breakdown during session
    ("Below VAL",  "Above VAH"):  2,   # Very bullish — complete reversal
    ("Below VAL",  "Within VA"):   1,  # Bullish rejection — sellers failed, returned to balance
    ("Below VAL",  "Below VAL"):  -2,  # Strong bearish — sellers maintained
}

def _derive_context(day_type, open_loc, close_loc, tail, ib_size):
    """Derive daily context label + confidence % from 5 inputs.
    Returns (label, confidence_pct, direction_score, structure).
    Close location is the dominant signal (per pbD: close determines who controls next day).
    Open/close relationship scored as combined signal.
    """
    _oc_score = _OPEN_CLOSE_SCORE.get((open_loc, close_loc), 0)
    dir_score = (
        _DAY_TYPE_SCORE.get(day_type, 0)
        + _oc_score
        + _TAIL_SCORE.get(tail, 0)
    )
    # Structure: Mean Reverting vs Trending
    # IB is a strong confirming signal per pbD: small IB = "high probability trend day"
    struct_day = _DAY_TYPE_STRUCTURE.get(day_type, "MR")
    if ib_size == "Large IB":
        struct_ib = "MR"
    elif ib_size == "Small IB":
        struct_ib = "TR"
    else:
        struct_ib = None  # Normal IB = neutral, no opinion

    # Resolve structure: IB can override day type when it's a strong signal
    _ib_conflict = False
    if struct_ib is None:
        structure = struct_day  # Normal IB → defer to day type
    elif struct_day == struct_ib:
        structure = struct_day  # Agreement → high confidence
    else:
        # Conflict: day type says one thing, IB says another
        # Small IB overrides MR day types (course: "high probability of trend day")
        # Large IB does NOT override trend day types (trend profiles are strongest signal)
        if ib_size == "Small IB" and struct_day == "MR":
            structure = "TR"  # Small IB upgrades to trend
        else:
            structure = struct_day  # Trend day type wins over large IB
        _ib_conflict = True

    # Small IB amplifies direction if trending
    if ib_size == "Small IB" and structure == "TR":
        dir_score += (1 if dir_score > 0 else -1 if dir_score < 0 else 0)

    # Double Distribution: if open/close traversed both VAs (score ±2), upgrade to trending
    if day_type == "Double Distribution" and abs(_oc_score) >= 2:
        structure = "TR"

    # Inside Day special case — don't trade aggressively, expect breakout
    if day_type == "Inside Day":
        # Inside Day + Small IB = very high probability of explosive breakout
        if ib_size == "Small IB":
            if dir_score > 0:
                label = "BREAKOUT EXPECTED (LONG BIAS)"
            elif dir_score < 0:
                label = "BREAKOUT EXPECTED (SHORT BIAS)"
            else:
                label = "BREAKOUT EXPECTED"
            return label, 40, dir_score, "ID"
        else:
            if dir_score > 0:
                label = "CAUTION — INSIDE DAY (LONG BIAS)"
            elif dir_score < 0:
                label = "CAUTION — INSIDE DAY (SHORT BIAS)"
            else:
                label = "CAUTION — INSIDE DAY"
            return label, 20, dir_score, "ID"

    # Determine label
    if structure == "MR":
        if dir_score > 0:
            label = "MEAN REVERTING LONG"
        elif dir_score < 0:
            label = "MEAN REVERTING SHORT"
        else:
            label = "MEAN REVERTING"
    else:
        if dir_score > 0:
            label = "TREND LONG"
        elif dir_score < 0:
            label = "TREND SHORT"
        else:
            label = "TREND"

    # Confidence scoring — 4 directional signals + IB structure agreement
    _signals = [
        _DAY_TYPE_SCORE.get(day_type, 0),    # day type direction
        _oc_score,                             # open/close relationship (combined, dominant)
        _TAIL_SCORE.get(tail, 0),             # tail direction
        # IB direction signal
        0 if ib_size == "Normal IB" else (1 if ib_size == "Small IB" and dir_score > 0 else (-1 if ib_size == "Small IB" and dir_score < 0 else 0)),
    ]
    # IB-day type agreement bonus/penalty
    _ib_agreement = 0
    if struct_ib is not None:
        if struct_day == struct_ib:
            _ib_agreement = 1  # Agreement → bonus confidence signal
        else:
            _ib_agreement = -1  # Conflict → penalty

    if dir_score == 0:
        agreeing = sum(1 for s in _signals if s == 0)
    elif dir_score > 0:
        agreeing = sum(1 for s in _signals if s > 0)
    else:
        agreeing = sum(1 for s in _signals if s < 0)
    # Base confidence from 4 directional signals
    confidence = int(round(agreeing / 4 * 100))
    # IB structure agreement adjusts confidence ±15%
    confidence = max(0, min(100, confidence + _ib_agreement * 15))

    return label, confidence, dir_score, structure

def _context_color(label):
    """Return CSS color for a context label."""
    if "BREAKOUT" in label:
        return "#a855f7"  # purple — explosive move expected
    elif "CAUTION" in label:
        return "#f59e0b"  # amber warning
    elif "TREND LONG" in label:
        return "#22c55e"
    elif "TREND SHORT" in label:
        return "#ef4444"
    elif "LONG" in label:
        return "#3b82f6"
    elif "SHORT" in label:
        return "#f97316"
    elif label == "TREND":
        return "#a855f7"
    return "#94a3b8"

def _schematic_for_market_state(market_state):
    """Return path to schematic PNG for a given market state."""
    _assets_dir = os.path.join(_APP_DIR, "assets")
    p1 = os.path.join(_assets_dir, "pbd_schematic_p1.png")
    p2 = os.path.join(_assets_dir, "pbd_schematic_p2.png")
    p3 = os.path.join(_assets_dir, "pbd_schematic_p3.png")
    if market_state in ("Normal Day", "Normal Day Variation", "Inside Day"):
        return p1
    elif market_state in ("Trend Day (P-profile)", "Trend Day (b-profile)", "Double Distribution"):
        return p2
    return None

# ─── TIMEZONE HELPER ─────────────────────────────────────────────────────────

def _current_et_offset():
    """Return current ET UTC offset: -4 (EDT, Mar-Nov) or -5 (EST, Nov-Mar)."""
    now = datetime.now()
    y = now.year
    # DST: second Sunday of March 2:00 AM → first Sunday of November 2:00 AM
    mar1_wd = datetime(y, 3, 1).weekday()  # 0=Mon
    mar_sun2 = 8 + (6 - mar1_wd) % 7 if mar1_wd != 6 else 8
    nov1_wd = datetime(y, 11, 1).weekday()
    nov_sun1 = 1 + (6 - nov1_wd) % 7 if nov1_wd != 6 else 1
    dst_start = datetime(y, 3, mar_sun2, 2)
    dst_end = datetime(y, 11, nov_sun1, 2)
    return -4 if dst_start <= now < dst_end else -5

def _et_to_local(et_time_str, user_tz_offset):
    """Convert a time string like '08:30' from ET to user's local timezone. Returns 'HH:MM'."""
    try:
        parts = et_time_str.strip().replace(".", ":").split(":")
        h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        et_off = _current_et_offset()
        utc_h = h - et_off
        local_h = (utc_h + user_tz_offset) % 24
        return f"{local_h:02d}:{m:02d}"
    except Exception:
        return et_time_str

def _now_et_minutes():
    """Return current time as minutes-since-midnight in ET."""
    from datetime import timezone, timedelta
    et_off = _current_et_offset()
    now_utc = datetime.now(timezone.utc)
    et_now = now_utc + timedelta(hours=et_off)
    return et_now.hour * 60 + et_now.minute

def _ib_timer_info():
    """Return IB timer state for futures.
    Returns dict: {status, label, minutes_left, pct, color}
    status: 'pre_market' | 'ib_active' | 'ib_complete' | 'market_closed'
    """
    et_mins = _now_et_minutes()
    mkt_open = 9 * 60 + 30   # 9:30 ET
    ib_end = 10 * 60          # 10:00 ET
    mkt_close = 16 * 60       # 4:00 PM ET

    if et_mins < mkt_open:
        mins_to_open = mkt_open - et_mins
        h, m = divmod(mins_to_open, 60)
        return {
            "status": "pre_market",
            "label": f"Market opens in {h}h {m:02d}m",
            "minutes_left": mins_to_open,
            "pct": 0,
            "color": "#4b5a7a",
        }
    elif et_mins < ib_end:
        mins_left = ib_end - et_mins
        pct = (30 - mins_left) / 30
        return {
            "status": "ib_active",
            "label": f"IB Active — {mins_left} min remaining",
            "minutes_left": mins_left,
            "pct": pct,
            "color": "#f97316",
        }
    elif et_mins < mkt_close:
        return {
            "status": "ib_complete",
            "label": "IB Complete",
            "minutes_left": 0,
            "pct": 1.0,
            "color": "#22c55e",
        }
    else:
        return {
            "status": "market_closed",
            "label": "Market Closed",
            "minutes_left": 0,
            "pct": 0,
            "color": "#4b5a7a",
        }

def _news_window_status(local_time_str):
    """Check if a news event window is active, expired, or upcoming.
    Returns 'active' (within ±30min), 'expired' (>30min after), or 'upcoming' (>30min before).
    """
    try:
        parts = local_time_str.split(":")
        ev_h, ev_m = int(parts[0]), int(parts[1])
        now_mins = datetime.now().hour * 60 + datetime.now().minute
        ev_mins = ev_h * 60 + ev_m
        diff = now_mins - ev_mins
        if diff > 30:
            return "expired"
        elif diff < -30:
            return "upcoming"
        else:
            return "active"
    except Exception:
        return "active"  # fail safe — assume active

# ─── CONTEXT HISTORY CSV ─────────────────────────────────────────────────────
_CONTEXT_CSV = os.path.join(_APP_DIR, "context_history.csv")
_CONTEXT_CSV_HEADER = "date,asset,day_type,open_location,close_location,tail,ib_size,context_label,confidence,direction_score,structure"

def _log_context(asset_name, phase2_data):
    """Append a row to context_history.csv when Phase 2 is confirmed."""
    import csv
    _exists = os.path.exists(_CONTEXT_CSV)
    with open(_CONTEXT_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if not _exists:
            w.writerow(_CONTEXT_CSV_HEADER.split(","))
        w.writerow([
            str(date.today()),
            asset_name,
            phase2_data.get("day_type", ""),
            phase2_data.get("open_location", ""),
            phase2_data.get("close_location", ""),
            phase2_data.get("tail", ""),
            phase2_data.get("ib_size", ""),
            phase2_data.get("context_label", ""),
            phase2_data.get("confidence", ""),
            phase2_data.get("direction_score", ""),
            phase2_data.get("structure", ""),
        ])

def _get_asset_context(instrument, checklist_assets):
    """Return the Phase 2 context dict for an instrument, or None if not found."""
    for a in checklist_assets:
        if a.get("name") == instrument and a.get("phase2_complete"):
            return a.get("phase2", {})
    return None

def _load_history():
    sb = _get_sb()
    if sb:
        try:
            r = sb.table("ltt_history").select("data").eq("id", 1).execute()
            if r.data:
                return r.data[0]["data"]
        except Exception:
            pass
    if os.path.exists(_HISTORY_PATH):
        try:
            with open(_HISTORY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_history(h):
    sb = _get_sb()
    if sb:
        try:
            sb.table("ltt_history").upsert({"id": 1, "data": h}).execute()
            return
        except Exception:
            pass
    with open(_HISTORY_PATH, "w") as f:
        json.dump(h, f, indent=2)

def _archive_trade(trade, session_date):
    """Append a single trade to history immediately — called on every Win/Loss/BE.
    Archives to trade's open_date so overnight trades stay with the correct day."""
    h = _load_history()
    day = str(trade.get("open_date", session_date))
    if day not in h:
        h[day] = []
    # Avoid duplicates by order id + close_time
    existing_keys = {(t.get("id"), t.get("close_time")) for t in h[day]}
    if (trade.get("id"), trade.get("close_time")) not in existing_keys:
        h[day].append(trade)
    _save_history(h)

def _recover_stale_session(data):
    """If session.json has trades from a previous date, archive them before resetting."""
    stale_date = data.get("session_date", "")
    stale_trades = data.get("completed_trades", [])
    if stale_trades and stale_date and stale_date != str(date.today()):
        h = _load_history()
        if stale_date not in h:
            h[stale_date] = []
        existing_keys = {(t.get("id"), t.get("close_time")) for t in h[stale_date]}
        for t in stale_trades:
            if (t.get("id"), t.get("close_time")) not in existing_keys:
                h[stale_date].append(t)
        _save_history(h)

def _load_session():
    data = None
    sb = _get_sb()
    if sb:
        try:
            r = sb.table("ltt_session").select("data").eq("id", 1).execute()
            if r.data:
                data = r.data[0]["data"]
        except Exception:
            pass
    if data is None and os.path.exists(_SESSION_PATH):
        try:
            with open(_SESSION_PATH) as f:
                data = json.load(f)
        except Exception:
            pass
    if data:
        # ── Migrate legacy active_trade → active_trades ──
        if "active_trade" in data and "active_trades" not in data:
            old = data.pop("active_trade")
            data["active_trades"] = [old] if old else []
        # ── Migrate legacy checklist structure ──
        if "checklist" in data:
            cl = data["checklist"]
            if "phase2" in cl and "assets" not in cl:
                # old flat structure → wrap into an asset if we have data
                old_p2 = cl.pop("phase2", {})
                old_p2c = cl.pop("phase2_complete", False)
                old_p3c = cl.pop("phase3_complete", False)
                cl.pop("phase4_complete", None)
                cl.pop("phase4_trade_num", None)
                if old_p2 and old_p2.get("strategy"):
                    cl["assets"] = [{
                        **_ASSET_DEFAULTS,
                        "name": old_p2.get("instrument", "SOL"),
                        "phase2": old_p2,
                        "phase2_complete": old_p2c,
                        "phase3_complete": old_p3c,
                    }]
                else:
                    cl["assets"] = []
                if "news" not in cl:
                    cl["news"] = dict(_SESSION_DEFAULTS["checklist"]["news"])
        if data.get("session_date") != str(date.today()):
            _recover_stale_session(data)
            s = {**_SESSION_DEFAULTS}
            s["session_date"] = str(date.today())
            s["balance_override"] = data.get("balance_override")
            # carry over active trades from previous day (overnight)
            carried = [t for t in data.get("active_trades", []) if t]
            if carried:
                s["active_trades"] = carried
            return s
        return {**_SESSION_DEFAULTS, **data}
    s = {**_SESSION_DEFAULTS}
    s["session_date"] = str(date.today())
    return s

def _save_session(s):
    sb = _get_sb()
    if sb:
        try:
            sb.table("ltt_session").upsert({"id": 1, "data": s}).execute()
            return
        except Exception:
            pass
    with open(_SESSION_PATH, "w") as f:
        json.dump(s, f, indent=2)

# ─── EXCHANGE CONFIG ─────────────────────────────────────────────────────────
_EXCHANGE_CFG_PATH = os.path.join(_APP_DIR, "exchange_config.json")
_EXCHANGES = ["OKX", "Bybit", "Binance", "KuCoin", "Kraken", "Interactive Brokers", "Hyperliquid"]

_EX_CFG_DEFAULTS = {
    "exchange":   "OKX",
    "api_key":    "",
    "secret_key": "",
    "passphrase": "",   # OKX only
}

def _load_ex_cfg():
    # Try Streamlit secrets first (cloud deployment)
    try:
        return {
            **_EX_CFG_DEFAULTS,
            "exchange":   "OKX",
            "api_key":    st.secrets["okx"]["api_key"],
            "secret_key": st.secrets["okx"]["secret_key"],
            "passphrase": st.secrets["okx"]["passphrase"],
        }
    except Exception:
        pass
    # Prefer local exchange_config.json; fall back to old okx_config.json candidates
    if os.path.exists(_EXCHANGE_CFG_PATH):
        try:
            with open(_EXCHANGE_CFG_PATH) as f:
                return {**_EX_CFG_DEFAULTS, **json.load(f)}
        except Exception:
            pass
    # Legacy fallback — try Trading Analyser's okx_config.json
    for p in _OKX_CONFIG_CANDIDATES:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    d = json.load(f)
                return {**_EX_CFG_DEFAULTS, "exchange": "OKX", **d}
            except Exception:
                pass
    return dict(_EX_CFG_DEFAULTS)

def _save_ex_cfg(cfg):
    with open(_EXCHANGE_CFG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── EXCHANGE CSV PARSING ─────────────────────────────────────────────────────
def _detect_exchange_from_headers(headers):
    h = {c.strip() for c in headers}
    if "Order Time" in h and "Avg. Filled Price" in h:      return "OKX"
    if "fillType" in h or ("orderID" in h and "pnl" in h):  return "Kraken"
    if "RealizedProfit" in h or ("OrderId" in h and "Commission" in h): return "Binance"
    if any(x in h for x in ("filledSize", "dealValue")) and \
       any(x in h for x in ("Realized PNL", "Realized PnL", "realizedPnl")): return "KuCoin"
    if "T. Price" in h and any(x in h for x in ("Realized P/L", "Realized P&L")): return "Interactive Brokers"
    if any(x in h for x in ("Realized PnL", "Closed PnL", "Reduce-only", "Reduce Only", "reduceOnly")): return "Bybit"
    if "closedPnl" in h and any(x in h for x in ("coin", "px", "sz", "dir")): return "Hyperliquid"
    return "OKX"

def _parse_csv_last_close(content_bytes, exchange_hint="Auto-detect"):
    """Parse uploaded CSV, return (net_pnl, fee, symbol, error_str) of most recent closing trade."""
    import io, csv as _csv
    try:
        text = content_bytes.decode("utf-8-sig", errors="replace")
        reader = _csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return None, None, None, "CSV is empty."
        headers = set(rows[0].keys())
        exchange = exchange_hint if exchange_hint != "Auto-detect" else _detect_exchange_from_headers(headers)

        def _f(v):
            if v is None: return 0.0
            s = str(v).strip().replace(",", "")
            return float(s) if s and s not in ("--", "N/A", "-", "") else 0.0

        def _is_close(row, exchange):
            """Return True if this row represents a closing trade."""
            if exchange == "Bybit":
                reduce = str(row.get("Reduce-only") or row.get("Reduce Only") or row.get("reduceOnly") or "").lower()
                return reduce in ("true", "1", "yes") if reduce else (_f(row.get("Realized PnL") or row.get("Closed PnL") or "") != 0)
            if exchange == "Interactive Brokers":
                code = str(row.get("Code") or "")
                codes = [c.strip() for c in code.split(";")]
                return "C" in codes if code else (_f(row.get("Realized P/L") or "") != 0)
            # OKX, Binance, KuCoin, Kraken, Hyperliquid: pnl != 0 means closing
            pnl_cols = ["PNL", "RealizedProfit", "Realized PNL", "Realized PnL",
                        "realizedPnl", "pnl", "closedPnl", "ClosedPnl", "Closed PnL",
                        "Realized P/L"]
            for col in pnl_cols:
                if col in row:
                    return _f(row[col]) != 0.0
            return False

        # Walk rows newest-first (CSV usually newest last for OKX, so reverse)
        for row in reversed(rows):
            if not _is_close(row, exchange):
                continue
            # Extract pnl + fee
            pnl_col_map = {
                "OKX":                  ("PNL",           "Fee"),
                "Bybit":                ("Realized PnL",  "Fee"),
                "Binance":              ("RealizedProfit", "Commission"),
                "KuCoin":               ("Realized PNL",  "Fee"),
                "Kraken":               ("pnl",           "fee"),
                "Interactive Brokers":  ("Realized P/L",  "Comm/Fee"),
                "Hyperliquid":          ("closedPnl",     "fee"),
            }
            pc, fc = pnl_col_map.get(exchange, ("PNL", "Fee"))
            pnl = _f(row.get(pc) or row.get("Realized PnL") or row.get("Closed PnL") or "")
            fee = abs(_f(row.get(fc) or row.get("Fee") or ""))
            sym = str(row.get("Symbol") or row.get("symbol") or row.get("coin") or row.get("Pair") or "")
            net = round(pnl + fee, 4) if exchange not in ("OKX",) else round(pnl, 4)
            return net, round(fee, 4), sym, None

        return None, None, None, f"No closing trade found in CSV ({exchange} format detected)."
    except Exception as e:
        return None, None, None, str(e)

# ─── OKX API FETCH ────────────────────────────────────────────────────────────
def _fetch_last_close():
    """Fetch most recent closed SWAP order from OKX API. Returns (net_pnl, fee, error_str)."""
    cfg = _load_ex_cfg()
    if not cfg.get("api_key"):
        return None, None, "No API credentials set. Configure them in the Exchange & API sidebar section."
    if cfg.get("exchange", "OKX") != "OKX":
        return None, None, f"API sync only supported for OKX. Use CSV import for {cfg['exchange']}."
    try:
        path = "/api/v5/trade/orders-history?instType=SWAP&state=filled&limit=10"
        ts   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        msg  = f"{ts}GET{path}"
        sig  = base64.b64encode(
            hmac.new(cfg["secret_key"].encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()
        hdrs = {
            "OK-ACCESS-KEY":        cfg["api_key"],
            "OK-ACCESS-SIGN":       sig,
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": cfg.get("passphrase", ""),
            "Content-Type":         "application/json",
        }
        resp = requests.get(f"https://www.okx.com{path}", headers=hdrs, timeout=8).json()
        if resp.get("code") != "0":
            return None, None, resp.get("msg", "OKX API error")
        for order in resp.get("data", []):
            pnl = float(order.get("pnl") or 0)
            fee = float(order.get("fee") or 0)
            if pnl != 0:
                return round(pnl + fee, 4), round(fee, 4), None
        return None, None, "No recent closing order found in last 10 fills."
    except Exception as e:
        return None, None, str(e)

def _fetch_todays_orders(cfg):
    """Fetch today's filled SWAP orders from OKX REST API.
    Returns (list_of_orders, open_positions, error_str).
    Each order: {instId, side, sz, avgPx, pnl, fee, fillTime, state}
    """
    if not cfg.get("api_key"):
        return [], [], "No API credentials"
    if cfg.get("exchange", "OKX") != "OKX":
        return [], [], f"REST sync only for OKX. Use CSV for {cfg['exchange']}."
    errors = []
    orders_out = []
    positions_out = []

    # 1. Fetch filled orders (today)
    try:
        path = "/api/v5/trade/orders-history?instType=SWAP&state=filled&limit=100"
        ts   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        sig  = base64.b64encode(
            hmac.new(cfg["secret_key"].encode(), f"{ts}GET{path}".encode(), hashlib.sha256).digest()
        ).decode()
        hdrs = {
            "OK-ACCESS-KEY":        cfg["api_key"],
            "OK-ACCESS-SIGN":       sig,
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": cfg.get("passphrase", ""),
            "Content-Type":         "application/json",
        }
        resp = requests.get(f"https://www.okx.com{path}", headers=hdrs, timeout=10).json()
        if resp.get("code") != "0":
            errors.append(resp.get("msg", "OKX API error"))
        else:
            today_str = str(date.today())
            for o in resp.get("data", []):
                fill_ts = int(o.get("fillTime") or o.get("uTime") or 0)
                if fill_ts > 0:
                    fill_date = datetime.utcfromtimestamp(fill_ts / 1000).strftime("%Y-%m-%d")
                else:
                    fill_date = ""
                if fill_date == today_str:
                    orders_out.append({
                        "instId":   o.get("instId", ""),
                        "side":     o.get("side", ""),
                        "sz":       o.get("sz", ""),
                        "avgPx":    o.get("avgPx", ""),
                        "pnl":      float(o.get("pnl") or 0),
                        "fee":      float(o.get("fee") or 0),
                        "fillTime": fill_date,
                        "fillTs":   fill_ts,
                        "ordType":  o.get("ordType", ""),
                    })
    except Exception as e:
        errors.append(f"Orders: {e}")

    # 2. Fetch open positions
    try:
        path2 = "/api/v5/account/positions?instType=SWAP"
        ts2   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        sig2  = base64.b64encode(
            hmac.new(cfg["secret_key"].encode(), f"{ts2}GET{path2}".encode(), hashlib.sha256).digest()
        ).decode()
        hdrs2 = {
            "OK-ACCESS-KEY":        cfg["api_key"],
            "OK-ACCESS-SIGN":       sig2,
            "OK-ACCESS-TIMESTAMP":  ts2,
            "OK-ACCESS-PASSPHRASE": cfg.get("passphrase", ""),
            "Content-Type":         "application/json",
        }
        resp2 = requests.get(f"https://www.okx.com{path2}", headers=hdrs2, timeout=10).json()
        if resp2.get("code") == "0":
            for p in resp2.get("data", []):
                pos_sz = float(p.get("pos") or 0)
                if pos_sz != 0:
                    positions_out.append({
                        "instId":  p.get("instId", ""),
                        "side":    "long" if pos_sz > 0 else "short",
                        "sz":      abs(pos_sz),
                        "avgPx":   p.get("avgPx", ""),
                        "upl":     float(p.get("upl") or 0),
                        "lever":   p.get("lever", ""),
                    })
    except Exception as e:
        errors.append(f"Positions: {e}")

    return orders_out, positions_out, "; ".join(errors) if errors else None

# ─── WEBSOCKET ENGINE ────────────────────────────────────────────────────────
# Module-level shared state — survives Streamlit reruns, shared across tabs
_WS_LOCK  = threading.Lock()
_WS_STATE = {
    "connected":     False,
    "connecting":    False,
    "auth_failed":   False,  # True = bad credentials, stop retrying
    "thread":        None,
    "pending_open":  None,   # set when OKX fill detected (opening trade)
    "pending_close": None,   # set when OKX fill detected (closing trade)
    "last_error":    "",
    "last_event":    "",
}

def _ws_sign(ts, secret):
    msg = f"{ts}GET/users/self/verify"
    return base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()

def _query_algo_orders(inst_id, cfg):
    """Return live TP/SL algo orders attached to a position."""
    path = f"/api/v5/trade/orders-algo?instId={inst_id}&ordType=conditional&state=live"
    ts  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    sig = base64.b64encode(
        hmac.new(cfg["secret_key"].encode(), f"{ts}GET{path}".encode(), hashlib.sha256).digest()
    ).decode()
    hdrs = {
        "OK-ACCESS-KEY":        cfg["api_key"],
        "OK-ACCESS-SIGN":       sig,
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": cfg.get("passphrase", ""),
        "Content-Type":         "application/json",
    }
    try:
        resp = requests.get(f"https://www.okx.com{path}", headers=hdrs, timeout=5).json()
        if resp.get("code") == "0":
            return resp.get("data", [])
    except Exception:
        pass
    return []

def _handle_fill(order, cfg):
    """Process a filled order from the WebSocket stream."""
    inst_id = order.get("instId", "")
    avg_px  = float(order.get("avgPx") or order.get("fillPx") or 0)
    pnl     = float(order.get("pnl") or 0)
    fee     = float(order.get("fee") or 0)
    side    = order.get("side", "buy")
    sz      = float(order.get("sz") or 0)
    ts      = datetime.now().strftime("%H:%M:%S")
    # Derive instrument name from instId: "SOL-USDT-SWAP" → "SOL"
    instrument = inst_id.split("-")[0] if inst_id else "?"

    if pnl == 0 and avg_px > 0:
        # ── Opening fill — query TP/SL algo orders ──
        _time.sleep(0.8)   # let OKX register attached orders
        algo  = _query_algo_orders(inst_id, cfg)
        tp_px = None
        sl_px = None
        for ao in algo:
            tp = float(ao.get("tpTriggerPx") or 0)
            sl = float(ao.get("slTriggerPx") or 0)
            if tp > 0: tp_px = tp
            if sl > 0: sl_px = sl
        rr = None
        if tp_px and sl_px and avg_px and abs(avg_px - sl_px) > 0:
            rr = round(abs(tp_px - avg_px) / abs(avg_px - sl_px), 2)
        with _WS_LOCK:
            _WS_STATE["pending_open"] = {
                "instrument": instrument,
                "inst_id":    inst_id,
                "entry_px":   avg_px,
                "sl_px":      sl_px,
                "tp_px":      tp_px,
                "rr":         rr,
                "side":       side,
                "sz":         sz,
                "time":       ts,
            }
            _WS_STATE["last_event"] = f"{ts} — {instrument} entry @ {avg_px}"

    elif pnl != 0:
        # ── Closing fill ──
        net_pnl = round(pnl + fee, 4)
        with _WS_LOCK:
            _WS_STATE["pending_close"] = {
                "net_pnl":    net_pnl,
                "fee":        round(fee, 4),
                "instrument": instrument,
                "close_px":   avg_px,
                "time":       ts,
            }
            _WS_STATE["last_event"] = f"{ts} — {instrument} close @ {avg_px} PnL ${net_pnl:+,.4f}"

def _ws_run(cfg):
    """WebSocket run loop — reconnects automatically on drop."""
    _retry_delay = 30   # seconds between reconnect attempts
    while True:
        with _WS_LOCK:
            if _WS_STATE.get("auth_failed"):
                break       # bad credentials — stop hammering the server
            _WS_STATE["connecting"] = True
            _WS_STATE["connected"]  = False
        try:
            _ws_connect(cfg)
        except Exception as e:
            with _WS_LOCK:
                _WS_STATE["connected"]  = False
                _WS_STATE["connecting"] = False
                _WS_STATE["last_error"] = str(e)
        with _WS_LOCK:
            if _WS_STATE.get("auth_failed"):
                break       # login was rejected — no point retrying
        _time.sleep(_retry_delay)

def _ws_connect(cfg):
    import json as _j
    def on_open(ws):
        ts   = str(int(_time.time()))
        sign = _ws_sign(ts, cfg["secret_key"])
        ws.send(_j.dumps({"op": "login", "args": [{
            "apiKey": cfg["api_key"], "passphrase": cfg.get("passphrase", ""),
            "timestamp": ts, "sign": sign,
        }]}))
    def on_message(ws, message):
        try:
            data = _j.loads(message)
        except Exception:
            return
        if data.get("event") == "login":
            if data.get("code") == "0":
                ws.send(_j.dumps({"op": "subscribe",
                                  "args": [{"channel": "orders", "instType": "SWAP"}]}))
                with _WS_LOCK:
                    _WS_STATE["connected"]  = True
                    _WS_STATE["connecting"] = False
                    _WS_STATE["last_error"] = ""
            else:
                with _WS_LOCK:
                    _WS_STATE["last_error"] = f"Auth failed: {data.get('msg','check API key/passphrase')}"
                    _WS_STATE["connecting"] = False
                    _WS_STATE["auth_failed"] = True   # stop retry loop
                ws.close()
            return
        if data.get("arg", {}).get("channel") == "orders":
            for order in data.get("data", []):
                if order.get("state") == "filled":
                    _handle_fill(order, cfg)
    def on_error(ws, error):
        with _WS_LOCK:
            _WS_STATE["connected"]  = False
            _WS_STATE["connecting"] = False
            _WS_STATE["last_error"] = str(error)
    def on_close(ws, *_):
        with _WS_LOCK:
            _WS_STATE["connected"]  = False
            _WS_STATE["connecting"] = False
    app = _websocket.WebSocketApp(
        "wss://ws.okx.com:8443/ws/v5/private",
        on_open=on_open, on_message=on_message,
        on_error=on_error, on_close=on_close,
    )
    app.run_forever(ping_interval=25, ping_timeout=10)

def _start_ws():
    """Start WS thread once — idempotent."""
    if not _HAS_WEBSOCKET:
        return
    cfg = _load_ex_cfg()
    if not cfg.get("api_key") or cfg.get("exchange", "OKX") != "OKX":
        return
    with _WS_LOCK:
        t = _WS_STATE.get("thread")
        if t and t.is_alive():
            return
        _WS_STATE["connecting"] = True
    thread = threading.Thread(target=_ws_run, args=(cfg,), daemon=True, name="okx-ws")
    thread.start()
    with _WS_LOCK:
        _WS_STATE["thread"] = thread

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def _fmt(n):
    sign = "+" if n > 0 else ""
    return f"{sign}{n:,.2f}R"

# Empirical win rate ranges per grade type (from research):
# Mean Reversion (AAA): 65–87% — high hit rate, price bounces back
# Breakout (AA):        20–55% — low hit rate, needs momentum continuation
# B+ (low conviction):  25–35% — weakest setup, smallest expected edge
_GRADE_WIN_RATES = {"AAA": 0.68, "AA": 0.38, "B+": 0.28}
# Grade execution bias (how hard it is for price to reach target):
# AAA: price tends to snap back quickly → easier to hit → -10
# AA:  needs trend continuation → harder → +5
# B+:  lower conviction → hardest → +15
_GRADE_EXEC_BIAS = {"AAA": -10, "AA": +5, "B+": +15}

def _risk_score(rr_target, add_on_count, grade="AA"):
    """
    0-100 risk score combining:
      - Execution difficulty: how hard it is for price to reach R:R target
        (RR=3 AA → ~45pts, scales with RR)
      - Grade bias: breakout harder to hit than mean reversion
        (AAA=-10, AA=+5, B+=+15)
      - Add-on penalty: each add-on = +12pts (more capital exposed)
    """
    if rr_target <= 0:
        return 0
    exec_risk  = min(80, (rr_target / 6) * 80)      # RR=3→40, RR=5→67, RR=6→80 max
    grade_bias = _GRADE_EXEC_BIAS.get(grade, 5)
    addon_risk = add_on_count * 12
    return min(100, max(0, exec_risk + grade_bias + addon_risk))

def _win_prob(grade="AA"):
    """Expected win probability based on grade type (from research)."""
    return _GRADE_WIN_RATES.get(grade, 0.38)

# ─── MORNING REPORT CARD DATA ────────────────────────────────────────────────
_MR_QUESTIONS = [
    ("sleep",     "Sleep quality last night",         ["Excellent", "Good", "Poor", "Terrible"],       [3, 2, 1, 0]),
    ("energy",    "Energy & focus level",              ["High", "Normal", "Low", "Depleted"],           [3, 2, 1, 0]),
    ("sick",      "Feeling sick or hungover?",         ["No", "Slightly", "Yes"],                       [3, 1, 0]),
    ("emotional", "Emotional state",                   ["Centred", "Slightly off", "Off", "Triggered"], [3, 2, 1, 0]),
    ("distract",  "Outside distractions today",        ["None", "Minor", "Significant", "Major"],       [3, 2, 1, 0]),
    ("clarity",   "Mental clarity & decision-making",  ["Sharp", "Normal", "Foggy", "Scattered"],       [3, 2, 1, 0]),
]
# (min_score, grade, multiplier, css_class, description)
_MR_GRADES = [
    (16, "A",  1.00, "mr-grade-A",  "Press hard — ideal conditions"),
    (13, "B+", 0.80, "mr-grade-Bp", "Normal — slight caution"),
    (10, "B",  0.60, "mr-grade-B",  "Reduced sizing — stay selective"),
    ( 7, "C",  0.30, "mr-grade-C",  "Size way down — consider sitting out"),
    ( 4, "D",  0.10, "mr-grade-D",  "Defensive only — protect capital"),
    ( 0, "F",  0.00, "mr-grade-F",  "Do not trade today"),
]

def _mr_grade(score):
    for threshold, grade, mult, css, desc in _MR_GRADES:
        if score >= threshold:
            return grade, mult, css, desc
    return "F", 0.00, "mr-grade-F", "Do not trade today"

def _session_pnl_r(trades):
    today = str(date.today())
    return sum(t.get("actual_r", 0) for t in trades
               if t.get("open_date", today) == today)

def _session_losses_r(trades):
    today = str(date.today())
    return sum(abs(t.get("actual_r", 0)) for t in trades
               if t.get("actual_r", 0) < 0 and t.get("open_date", today) == today)

# ─── PAGE CONFIG ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Live Trading Tool",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── PASSWORD GATE ────────────────────────────────────────────────────────────
if not _LOCAL_DEV and not st.session_state.get("_auth"):
    _pwd = st.text_input("Password", type="password", key="_pwd")
    if _pwd:
        if _pwd == st.secrets.get("app", {}).get("password", "Prime_LTT_01"):
            st.session_state["_auth"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

# ─── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #080c14; }
[data-testid="stSidebar"]          { background: #0c1018; border-right: 1px solid #1a2035; }
[data-testid="stHeader"]           { background: transparent; }
[data-testid="stSidebar"] .stMarkdown h3 {
    color: #f97316; font-size: 0.8rem; text-transform: uppercase;
    letter-spacing: .1em; font-weight: 700; margin-bottom: 4px;
}

.kpi-grid { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
.kpi-card {
    flex: 1; min-width: 130px; background: #0e1220;
    border: 1px solid #1a2238; border-top: 2px solid #1e2a45;
    border-radius: 8px; padding: 16px 14px 14px; text-align: center;
}
.kpi-val { font-size: 1.5rem; font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; text-align: center; }
.kpi-lbl { font-size: 0.63rem; color: #4b5a7a; text-transform: uppercase;
           letter-spacing: .1em; margin-top: 7px; font-weight: 600; text-align: center; }

.green  { color: #22c55e; }
.red    { color: #ef4444; }
.yellow { color: #f59e0b; }
.orange { color: #f97316; }
.white  { color: #e2e8f0; }
.grey   { color: #94a3b8; }
.purple { color: #a855f7; }

.sec-hdr { display: flex; align-items: center; gap: 20px; margin: 36px 0 18px; }
.sec-line { flex: 1; height: 1px; background: #1a2238; }
.sec-title { color: #e2e8f0; font-size: 1.25rem; font-weight: 700;
             letter-spacing: -0.01em; white-space: nowrap; }

/* Hero cards */
.hero-grid { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.hero-card {
    flex: 1; min-width: 200px; background: #0e1220;
    border: 1px solid #1a2238; border-radius: 10px; padding: 20px 24px 16px;
    text-align: center;
}
.hero-lbl { font-size: 0.65rem; color: #4b5a7a; text-transform: uppercase;
            letter-spacing: .12em; font-weight: 600; margin-bottom: 8px; }
.hero-val { font-size: 2.4rem; font-weight: 800; letter-spacing: -0.03em; line-height: 1; }
.hero-sub { font-size: 0.78rem; color: #4b5a7a; margin-top: 8px; }
.limit-bar-bg   { background: #1a2238; border-radius: 6px; height: 8px;
                  margin-top: 12px; overflow: hidden; }
.limit-bar-fill { height: 8px; border-radius: 6px; }

/* Mini stats strip */
.mini-grid { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.mini-card {
    flex: 1; min-width: 100px; background: #0a0e18;
    border: 1px solid #1a2238; border-radius: 8px; padding: 10px 12px; text-align: center;
}
.mini-val { font-size: 1.05rem; font-weight: 700; line-height: 1.1; }
.mini-lbl { font-size: 0.58rem; color: #4b5a7a; text-transform: uppercase;
            letter-spacing: .1em; margin-top: 5px; font-weight: 600; }

/* Quick-launch trade buttons */
.stButton > button {
    min-height: 52px !important;
    font-size: 1.05rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.05em !important;
}
/* Selected quick-launch (primary) → green */
.stButton > button[data-testid="baseButton-primary"] {
    background-color: #16a34a !important;
    border-color: #16a34a !important;
    color: #fff !important;
}
/* Enter Trade form submit */
[data-testid="stFormSubmitButton"] > button {
    min-height: 64px !important;
    font-size: 1.2rem !important;
    font-weight: 800 !important;
    letter-spacing: 0.08em !important;
}

/* Active trade card */
.active-card {
    background: #0e1220; border: 1px solid #f97316;
    border-radius: 10px; padding: 20px 24px;
    margin-bottom: 12px; position: relative;
}
.active-badge {
    display: inline-block; background: #f97316; color: #000;
    font-size: 0.6rem; font-weight: 800; letter-spacing: .12em;
    text-transform: uppercase; padding: 3px 8px; border-radius: 4px;
    margin-bottom: 12px;
}
.trade-detail-row { display: flex; gap: 36px; flex-wrap: wrap; align-items: baseline; }
.td-lbl { font-size: 0.68rem; color: #4b5a7a; text-transform: uppercase;
          letter-spacing: .1em; font-weight: 600; }
.td-val { font-size: 1.55rem; font-weight: 700; color: #e2e8f0; margin-top: 4px; }
.addon-badge {
    display: inline-block; background: #1a2238; border: 1px solid #2a3a5a;
    border-radius: 4px; padding: 2px 8px; font-size: 0.75rem;
    color: #94a3b8; margin-right: 4px; margin-top: 4px;
}

/* Risk gauge */
.risk-gauge-wrap { background: #0e1220; border: 1px solid #1a2238;
                   border-radius: 8px; padding: 16px 20px; }
.risk-bar-bg   { background: #1a2238; border-radius: 6px; height: 14px;
                 margin: 8px 0; overflow: hidden; }
.risk-bar-fill { height: 14px; border-radius: 6px; transition: width 0.3s; }
.risk-lbl-row  { display: flex; justify-content: space-between; font-size: 0.65rem;
                 color: #4b5a7a; text-transform: uppercase; letter-spacing: .08em; margin-top: 4px; }

/* Outcome buttons */
.outcome-row { display: flex; gap: 8px; margin-top: 8px; }

/* Fabio panel */
.fabio-card {
    background: #0e1220; border: 1px solid #a855f7;
    border-radius: 10px; padding: 18px 22px; margin-bottom: 12px;
}
.fabio-badge {
    display: inline-block; background: #a855f7; color: #fff;
    font-size: 0.6rem; font-weight: 800; letter-spacing: .12em;
    text-transform: uppercase; padding: 3px 8px; border-radius: 4px;
    margin-bottom: 10px;
}
.phase-row { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.phase-step {
    flex: 1; min-width: 80px; background: #0a0e18; border: 1px solid #1a2238;
    border-radius: 6px; padding: 10px 12px; text-align: center;
    font-size: 0.7rem; color: #4b5a7a; text-transform: uppercase; letter-spacing: .08em;
}
.phase-step.active { border-color: #a855f7; color: #a855f7; font-weight: 700; }
.phase-step.done   { border-color: #22c55e; color: #22c55e; }

/* Banners */
.stop-banner {
    background: #2a0a0a; border: 1px solid #ef4444; border-radius: 8px;
    padding: 14px 20px; color: #ef4444; font-weight: 800; font-size: 1rem;
    text-align: center; letter-spacing: 0.04em; margin-bottom: 12px;
}
.warn-banner {
    background: #2a1a0a; border: 1px solid #f97316; border-radius: 8px;
    padding: 12px 18px; color: #f97316; font-weight: 600; font-size: 0.88rem;
    margin-bottom: 12px;
}
.info-banner {
    background: #0a1a2a; border: 1px solid #3b82f6; border-radius: 8px;
    padding: 12px 18px; color: #3b82f6; font-size: 0.85rem; margin-bottom: 12px;
}

section[data-testid="stHorizontalBlock"] > div { gap: 8px; }
[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
h3 { color: #e2e8f0 !important; }

/* Safe Mode Checklist — matches .mr-card / .mr-badge exactly */
.checklist-card {
    background: #0e1220; border: 1px solid #3b82f6;
    border-radius: 10px; padding: 18px 22px; margin-bottom: 16px;
}
.checklist-card-done {
    background: #0e1220; border: 1px solid #3b82f6;
    border-radius: 10px; padding: 18px 22px; margin-bottom: 16px;
}
.checklist-badge {
    display: inline-block; background: #3b82f6; color: #fff;
    font-size: 0.6rem; font-weight: 800; letter-spacing: .12em;
    text-transform: uppercase; padding: 3px 8px; border-radius: 4px;
    margin-bottom: 10px;
}
.checklist-badge-done {
    display: inline-block; background: #3b82f6; color: #fff;
    font-size: 0.6rem; font-weight: 800; letter-spacing: .12em;
    text-transform: uppercase; padding: 3px 8px; border-radius: 4px;
    margin-bottom: 10px;
}
.checklist-lock {
    background: #0a0e18; border: 1px solid #1a2238; border-radius: 10px;
    padding: 24px; text-align: center; color: #4b5a7a; font-size: 0.88rem;
    margin-bottom: 16px; letter-spacing: 0.02em;
}

/* Morning Report Card */
.mr-card {
    background: #0e1220; border: 1px solid #3b82f6;
    border-radius: 10px; padding: 18px 22px; margin-bottom: 16px;
}
.mr-badge {
    display: inline-block; background: #3b82f6; color: #fff;
    font-size: 0.6rem; font-weight: 800; letter-spacing: .12em;
    text-transform: uppercase; padding: 3px 8px; border-radius: 4px;
    margin-bottom: 10px;
}
.mr-grade-A  { color: #22c55e; }
.mr-grade-Bp { color: #4ade80; }
.mr-grade-B  { color: #f59e0b; }
.mr-grade-C  { color: #f97316; }
.mr-grade-D  { color: #ef4444; }
.mr-grade-F  { color: #ef4444; }
.mr-no-trade {
    background: #1a0a00; border: 1px solid #f97316; border-radius: 8px;
    padding: 14px 20px; color: #f97316; font-weight: 800; font-size: 0.95rem;
    text-align: center; letter-spacing: 0.04em; margin-bottom: 12px;
}

/* Asset tabs — big, clean, active border */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap: 0; background: transparent; border-bottom: 1px solid #1e2a45;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    background: #0a0e18; color: #4b5a7a; font-size: 0.85rem; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase;
    padding: 14px 28px; border: 1px solid #1e2a45; border-bottom: none;
    border-radius: 10px 10px 0 0; margin-right: 4px;
    transition: all 0.15s ease;
}
[data-testid="stTabs"] [data-baseweb="tab"]:hover {
    background: #0e1220; color: #94a3b8;
}
[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] {
    background: #0e1220; color: #e2e8f0;
    border-color: #3b82f6; border-top: 2px solid #3b82f6;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    display: none;
}
[data-testid="stTabs"] [data-baseweb="tab-border"] {
    display: none;
}
[data-testid="stTabs"] [data-testid="stTabContent"] {
    background: #0e1220; border: 1px solid #1e2a45; border-top: none;
    border-radius: 0 0 10px 10px; padding: 16px 20px;
}

/* Decision section boxes */
.decision-box {
    background: #0a0e18;
    border: 1px solid #1e2a45;
    border-radius: 12px;
    padding: 20px 24px;
    margin: 14px 0;
}
.decision-hdg {
    text-align: center;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .14em;
    color: #94a3b8;
    margin: 0 0 16px 0;
    padding-bottom: 12px;
    border-bottom: 1px solid #1a2238;
}

/* Confirm outcome banners */
.confirm-banner {
    border-radius: 10px; padding: 14px 20px; text-align: center;
    font-size: 1rem; font-weight: 800; letter-spacing: 0.05em; margin: 16px 0 12px;
}
.confirm-win  { background: #0a1a0a; border: 2px solid #22c55e; color: #22c55e; }
.confirm-loss { background: #1a0a0a; border: 2px solid #ef4444; color: #ef4444; }
.confirm-be   { background: #0e0e1a; border: 2px solid #94a3b8; color: #94a3b8; }
.confirm-edit { background: #0e1220; border: 2px solid #f97316; color: #f97316; }
.confirm-addon{ background: #0e0e0a; border: 2px solid #f59e0b; color: #f59e0b; }

/* Pending state on trade card */
.pending-card-notice {
    text-align: center; padding: 10px 0 6px;
    font-size: 0.8rem; font-weight: 700; color: #f97316;
    letter-spacing: .06em;
}

/* Grade gate message */
.grade-gate-msg {
    text-align: center; padding: 22px 20px;
    background: #0e1220; border: 1px dashed #1e2a45;
    border-radius: 10px; color: #4b5a7a;
    font-size: 0.88rem; font-weight: 600; margin: 12px 0;
}

/* IB Timer */
.ib-timer {
    display: flex; align-items: center; gap: 12px;
    background: #0a0e18; border: 1px solid #1e2a45;
    border-radius: 10px; padding: 12px 20px;
    margin: 0 0 16px; justify-content: center;
}
.ib-timer-dot {
    width: 10px; height: 10px; border-radius: 50%;
}
.ib-timer-label {
    font-size: 0.82rem; font-weight: 700;
    letter-spacing: 0.04em; text-transform: uppercase;
}
.ib-bar-bg { background: #1a2238; border-radius: 4px; height: 6px; width: 120px; overflow: hidden; }
.ib-bar-fill { height: 6px; border-radius: 4px; transition: width 0.3s; }
@keyframes ib-pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
.ib-pulse { animation: ib-pulse 1.5s ease-in-out infinite; }

/* P/b Setup stages */
.setup-stage {
    flex: 1; min-width: 100px; background: #0a0e18;
    border: 1px solid #1a2238; border-radius: 8px;
    padding: 12px 14px; text-align: center;
}
.setup-stage.active { border-color: #f97316; }
.setup-stage.done { border-color: #22c55e; }
.setup-stage-lbl {
    font-size: 0.6rem; color: #4b5a7a; text-transform: uppercase;
    letter-spacing: .08em; font-weight: 600;
}
.setup-stage-val {
    font-size: 0.9rem; font-weight: 700; margin-top: 4px;
}
</style>
""", unsafe_allow_html=True)

# ─── LOAD STATE ──────────────────────────────────────────────────────────────
prefs   = _load_prefs()
session = _load_session()

# ─── START WEBSOCKET + AUTO-REFRESH ──────────────────────────────────────────
_conn_enabled = prefs.get("connection_enabled", False)
if _conn_enabled:
    _WS_STATE["auth_failed"] = False   # re-arm retry loop when connection re-enabled
    _start_ws()
    if _HAS_AUTOREFRESH:
        _autorefresh(interval=2500, key="ws_poll")  # poll every 2.5s
else:
    # Ensure WS thread stays dead when kill switch is off
    with _WS_LOCK:
        _WS_STATE["auth_failed"] = True
        _WS_STATE["connected"]   = False
        _WS_STATE["connecting"]  = False

# IB timer autorefresh — only during active IB period (every 30s)
if _HAS_AUTOREFRESH and not _conn_enabled:
    _ib_info = _ib_timer_info()
    if _ib_info["status"] == "ib_active":
        _autorefresh(interval=30000, key="ib_timer_poll")

# ─── PROCESS PENDING WS EVENTS ───────────────────────────────────────────────
with _WS_LOCK:
    _ws_pending_open  = _WS_STATE.get("pending_open")
    _ws_pending_close = _WS_STATE.get("pending_close")
    _WS_STATE["pending_open"]  = None
    _WS_STATE["pending_close"] = None

# Auto-pre-fill new trade form from WS open event (only if no trade open for that instrument)
if _ws_pending_open:
    _pf_inst = _ws_pending_open.get("instrument", "")
    _already_open = any(t.get("instrument") == _pf_inst for t in session.get("active_trades", []))
    if not _already_open:
        st.session_state["_ws_prefill"] = _ws_pending_open

# Auto-resolve active trade from WS close event (match by instrument)
if _ws_pending_close and session.get("active_trades"):
    _close_inst = _ws_pending_close.get("instrument", "")
    _match_idx  = next(
        (i for i, t in enumerate(session["active_trades"]) if t.get("instrument") == _close_inst),
        0 if session["active_trades"] else None,
    )
    if _match_idx is not None:
        _at   = session["active_trades"][_match_idx]
        _cp   = _ws_pending_close["net_pnl"]
        _ar   = round(_cp / (prefs["balance"] * prefs["r_pct"] / 100), 4)
        _outcome = "Win" if _cp > 0 else ("Loss" if _cp < 0 else "BE")
        _cr   = _risk_score(_at["rr_target"], len(_at.get("add_ons", [])), _at.get("grade", "AA"))
        _trade = {**_at, "outcome": _outcome,
                  "close_time": _ws_pending_close["time"],
                  "actual_r": _ar, "risk_tool": _ar,
                  "okx_pnl": _cp, "okx_fee": _ws_pending_close["fee"],
                  "risk_score_close": _cr}
        session["completed_trades"].append(_trade)
        _archive_trade(_trade, session["session_date"])
        session["active_trades"].pop(_match_idx)
        _save_session(session)
        st.session_state["_ws_auto_closed"] = _outcome
        st.rerun()

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── SYNC BUTTON (large, top of sidebar) ──
    if st.button("SYNC EXCHANGE DATA", width="stretch", type="primary", key="sync_btn"):
        _sync_cfg = _load_ex_cfg()
        if _LOCAL_DEV:
            st.error("Sync disabled in local dev mode (VPN safe). Deploy to cloud to sync.")
        elif not _sync_cfg.get("api_key"):
            st.error("No API credentials — configure in Exchange & API below.")
        elif not prefs.get("connection_enabled", False):
            st.error("Enable connections first (toggle below).")
        else:
            with st.spinner("Syncing..."):
                _s_orders, _s_positions, _s_err = _fetch_todays_orders(_sync_cfg)
            if _s_err:
                st.warning(f"Sync warning: {_s_err}")
            st.session_state["_sync_orders"] = _s_orders
            st.session_state["_sync_positions"] = _s_positions
            st.session_state["_sync_ts"] = datetime.now().strftime("%H:%M:%S")
            if _s_orders or _s_positions:
                st.success(f"Synced: {len(_s_orders)} fills today, {len(_s_positions)} open positions")
            else:
                st.info("No fills today, no open positions.")

    # ── CONNECTION KILL SWITCH ──
    _conn_current = prefs.get("connection_enabled", False)
    _conn_label   = "CONNECTED" if _conn_current else "OFFLINE"
    _conn_style   = "background:#14532d;border:2px solid #22c55e;" if _conn_current else "background:#1c0a0a;border:2px solid #ef4444;"
    st.markdown(
        f'<div style="{_conn_style}border-radius:8px;padding:10px 16px;margin:8px 0;'
        f'text-align:center;font-size:0.7rem;font-weight:800;letter-spacing:.06em;color:#e2e8f0">'
        f'{_conn_label}</div>',
        unsafe_allow_html=True,
    )
    if st.button(
        "ENABLE SYNC" if not _conn_current else "KILL ALL CONNECTIONS",
        width="stretch",
        type="primary" if not _conn_current else "secondary",
        key="kill_switch_btn",
    ):
        prefs["connection_enabled"] = not _conn_current
        _save_prefs(prefs)
        if _conn_current:
            with _WS_LOCK:
                _WS_STATE["auth_failed"]  = True
                _WS_STATE["connected"]    = False
                _WS_STATE["connecting"]   = False
                _WS_STATE["last_error"]   = "Disconnected by kill switch"
        st.rerun()
    st.markdown("---")

    st.markdown("### Mode")
    mode = st.selectbox(
        "Trading mode", ["Standard", "Secret Sauce"],
        index=["Standard", "Secret Sauce"].index(prefs.get("mode", "Standard")),
        label_visibility="collapsed",
    )
    fabio_submode = None
    if mode == "Secret Sauce":
        _ss_opts = ["Conservative Mode", "Competition Mode"]
        _ss_saved = prefs.get("fabio_submode", "Conservative Mode")
        _ss_idx = _ss_opts.index(_ss_saved) if _ss_saved in _ss_opts else 0
        fabio_submode = st.selectbox("Secret Sauce model", _ss_opts, index=_ss_idx)

    st.divider()
    st.markdown("### Safe Mode")
    _sm_current = prefs.get("safe_mode", False)
    new_safe_mode = st.checkbox(
        "Sequential pre-trade checklist",
        value=_sm_current,
        help="4 phases before trading unlocks: self-check → market context → key levels → pre-trade gate",
    )
    if new_safe_mode != _sm_current:
        prefs["safe_mode"] = new_safe_mode
        _save_prefs(prefs)
        st.rerun()

    st.divider()
    st.markdown("### Morning Report Card")
    if not new_safe_mode:
        _mr_enabled_current = prefs.get("morning_report_enabled", False)
        new_mr_enabled = st.checkbox(
            "Enable daily performance check-in",
            value=_mr_enabled_current,
            help="Grade yourself each morning — your grade adjusts today's effective loss limit",
        )
        if new_mr_enabled != _mr_enabled_current:
            prefs["morning_report_enabled"] = new_mr_enabled
            _save_prefs(prefs)
    else:
        new_mr_enabled = prefs.get("morning_report_enabled", False)
        st.caption("Phase 1 of Safe Mode — always required")
    # Show compact result in sidebar when completed
    _sb_mr = session.get("morning_report", {})
    if (new_mr_enabled or new_safe_mode) and _sb_mr.get("completed"):
        _sb_grade = _sb_mr.get("grade", "?")
        _sb_mult  = _sb_mr.get("multiplier", 1.0)
        _sb_desc  = _sb_mr.get("description", "")
        st.caption(f"Today: **{_sb_grade}** → {_sb_mult*100:.0f}% of daily limit · {_sb_desc}")

    st.divider()
    st.markdown("### Timezone & Region")
    _tz_options = list(range(-12, 15))
    _tz_labels = [f"UTC{'+' if o >= 0 else ''}{o}" for o in _tz_options]
    _tz_current = prefs.get("user_tz_offset", 1)
    _tz_idx = _tz_options.index(_tz_current) if _tz_current in _tz_options else _tz_options.index(1)
    new_tz_offset = st.selectbox(
        "Your timezone (UTC offset)",
        _tz_options,
        index=_tz_idx,
        format_func=lambda x: f"UTC{'+' if x >= 0 else ''}{x}",
    )
    _et_now = _current_et_offset()
    _et_lbl = "EDT (UTC-4)" if _et_now == -4 else "EST (UTC-5)"
    st.caption(f"US markets: {_et_lbl} · NY 9:30 = your {_et_to_local('09:30', new_tz_offset)}")

    st.divider()
    st.markdown("### Account")
    new_balance = st.number_input(
        "Starting Balance ($)", min_value=100.0, max_value=1_000_000.0,
        value=float(prefs["balance"]), step=100.0, format="%.0f",
    )
    new_r_pct = st.number_input(
        "R Size (% of balance)", min_value=0.1, max_value=10.0,
        value=float(prefs["r_pct"]), step=0.1, format="%.1f",
    )
    new_daily_limit = st.number_input(
        "Daily Loss Limit (R)", min_value=0.1, max_value=10.0,
        value=float(prefs["daily_limit_r"]), step=0.25, format="%.2f",
    )

    st.divider()
    st.markdown("### Grade Implied Risk (R)")
    grade_updates = {}
    _grade_order = ["AAA", "AA", "B+"]  # always render in this order
    for gkey in _grade_order:
        if gkey not in prefs["grades"]:
            continue
        gdata = prefs["grades"][gkey]
        val = st.number_input(
            f"{gkey} ({gdata['label'].split('—')[1].strip()})",
            min_value=0.01, max_value=5.0,
            value=float(gdata["implied_r"]), step=0.05, format="%.2f",
            key=f"grade_{gkey}",
        )
        grade_updates[gkey] = val
    new_addon_r = st.number_input(
        "Add-on R per unit", min_value=0.01, max_value=2.0,
        value=float(prefs["addon_r"]), step=0.05, format="%.2f",
    )

    if st.button("Save Settings", width="stretch"):
        prefs["balance"]                = new_balance
        prefs["r_pct"]                  = new_r_pct
        prefs["daily_limit_r"]          = new_daily_limit
        prefs["addon_r"]                = new_addon_r
        prefs["mode"]                   = mode
        prefs["fabio_submode"]          = fabio_submode or prefs.get("fabio_submode", "Conservative Mode")
        prefs["morning_report_enabled"] = new_mr_enabled
        prefs["user_tz_offset"]         = new_tz_offset
        for gk, gv in grade_updates.items():
            prefs["grades"][gk]["implied_r"] = gv
        session["balance_override"] = new_balance
        _save_prefs(prefs)
        _save_session(session)
        st.success("Saved")
        st.rerun()

    st.divider()
    with st.expander("Exchange & API", expanded=False):
        _ex_cfg = _load_ex_cfg()
        _ex_sel = st.selectbox(
            "Exchange", _EXCHANGES,
            index=_EXCHANGES.index(_ex_cfg.get("exchange", "OKX")) if _ex_cfg.get("exchange", "OKX") in _EXCHANGES else 0,
            key="sb_exchange",
        )
        _api_key  = st.text_input("API Key",    value=_ex_cfg.get("api_key", ""),    key="sb_api_key")
        _sec_key  = st.text_input("Secret Key", value=_ex_cfg.get("secret_key", ""), key="sb_sec_key",  type="password")
        _pass     = st.text_input("Passphrase", value=_ex_cfg.get("passphrase", ""), key="sb_pass",     type="password",
                                   help="OKX only — leave blank for other exchanges")
        _ex_c1, _ex_c2 = st.columns(2)
        with _ex_c1:
            if st.button("Save Credentials", width="stretch"):
                _save_ex_cfg({"exchange": _ex_sel, "api_key": _api_key,
                               "secret_key": _sec_key, "passphrase": _pass})
                st.success("Saved")
        with _ex_c2:
            if st.button("Test Connection", width="stretch", type="secondary"):
                if not prefs.get("connection_enabled", False):
                    st.error("Enable connections first (kill switch is OFF)")
                else:
                    _tp, _tf, _te = _fetch_last_close()
                    if _te:
                        st.error(_te)
                    else:
                        st.success(f"OK — last close: ${_tp:+,.4f}")

    st.divider()
    st.markdown("### Session")
    st.caption(f"Date: {session['session_date']}")
    if st.button("Reset Session", width="stretch", type="secondary"):
        ns = {**_SESSION_DEFAULTS}
        ns["session_date"]    = str(date.today())
        ns["balance_override"] = new_balance
        _save_session(ns)
        st.rerun()

# ─── COMPUTED VALUES ──────────────────────────────────────────────────────────
balance        = session.get("balance_override") or prefs["balance"]
one_r          = balance * (prefs["r_pct"] / 100)
daily_limit_r  = prefs["daily_limit_r"]
daily_limit_usd = daily_limit_r * one_r
completed      = session["completed_trades"]
active_trades  = session.get("active_trades", [])   # list of open trade dicts
# Backward-compat alias: active = first open trade (or None). Used in single-trade sections.
active         = active_trades[0] if active_trades else None
session_pnl_r  = _session_pnl_r(completed)
losses_r       = _session_losses_r(completed)
safe_mode       = prefs.get("safe_mode", False)
# Morning Report Card — apply multiplier to daily limit if enabled + completed
mr_enabled      = prefs.get("morning_report_enabled", False)
morning_report  = session.get("morning_report", {**_SESSION_DEFAULTS["morning_report"]})
mr_completed    = (mr_enabled or safe_mode) and morning_report.get("completed", False)
mr_grade_val    = morning_report.get("grade") if mr_completed else None
mr_multiplier   = morning_report.get("multiplier", 1.0) if mr_completed else 1.0
effective_daily_limit_r = round(daily_limit_r * mr_multiplier, 4)
# Pool exposure from ALL active trades
_active_exposure = sum(
    t.get("implied_r", 0) + sum(a["r"] for a in t.get("add_ons", []))
    for t in active_trades
)
_used_r        = losses_r + _active_exposure
remaining_r    = max(0.0, effective_daily_limit_r - _used_r)
limit_hit      = _used_r >= effective_daily_limit_r
pnl_pct        = min(1.0, _used_r / effective_daily_limit_r) if effective_daily_limit_r > 0 else 1.0
fabio_state    = session.get("fabio_state", _SESSION_DEFAULTS["fabio_state"])

# ─── CHECKLIST STATE ──────────────────────────────────────────────────────────
_cl_def   = _SESSION_DEFAULTS["checklist"]
_cl_raw   = session.get("checklist", {})
checklist = {
    "phase1_complete": _cl_raw.get("phase1_complete", False),
    "assets":          _cl_raw.get("assets", []),
    "news":            {**_cl_def["news"], **_cl_raw.get("news", {})},
}
cl_phase1  = checklist.get("phase1_complete", False)
cl_assets  = checklist.get("assets", [])  # list of per-asset dicts
# An asset is "ready" when both phase2 + phase3 are complete
cl_any_ready = any(a.get("phase2_complete") and a.get("phase3_complete") for a in cl_assets)
cl_news_done = checklist["news"].get("checked", False)
# Sync phase1 with MRC completion
if safe_mode and mr_completed and not cl_phase1:
    checklist["phase1_complete"] = True
    cl_phase1 = True
    session["checklist"] = checklist
    _save_session(session)
# Gate level: 1=need phase1, 2=need to add/setup assets, 5=at least one asset ready
if not safe_mode or active_trades:
    cl_gate = 5
elif not cl_phase1:
    cl_gate = 1
elif not cl_assets:
    cl_gate = 2       # no assets added yet
elif not cl_any_ready:
    cl_gate = 3       # assets added but phase2/phase3 not done
else:
    cl_gate = 5

# ─── BANNERS ─────────────────────────────────────────────────────────────────
if limit_hit:
    st.markdown('<div class="stop-banner">DAILY LOSS LIMIT REACHED — STOP TRADING</div>',
                unsafe_allow_html=True)
elif pnl_pct >= 0.75:
    st.markdown(f'<div class="warn-banner">Warning — {pnl_pct*100:.0f}% of daily loss limit used. Be selective.</div>',
                unsafe_allow_html=True)

# Overnight trade banners — for any active trade opened before today
for _ot in active_trades:
    if _ot.get("open_date") and _ot["open_date"] != str(date.today()):
        st.markdown(
            f'<div class="info-banner">⚠️ {_ot.get("instrument","Trade")} carried over from {_ot["open_date"]} — '
            f'{_ot.get("grade","?")} @ R:R {_ot.get("rr_target","?")} still open. '
            f'Close it when ready.</div>',
            unsafe_allow_html=True,
        )

# ─── PAGE TITLE ──────────────────────────────────────────────────────────────
st.markdown(
    '<h1 style="font-size:2.4rem;font-weight:900;letter-spacing:0.04em;'
    'color:#e2e8f0;margin:0 0 18px 0;line-height:1;text-align:center">Trading Tool</h1>',
    unsafe_allow_html=True,
)

# ─── PHASES (safe mode = Phase 1 card + per-asset Phase 2/3 + news events) ──────
if safe_mode:
    _mr_g    = morning_report.get("grade", "?")
    _mr_mult = morning_report.get("multiplier", 1.0)
    _mr_css  = morning_report.get("color", "white")
    _mr_desc = morning_report.get("description", "")
    _mr_sc   = morning_report.get("score", "?")
    _mr_orig = daily_limit_r
    _mr_eff  = effective_daily_limit_r
    _limit_line = (
        f"{_mr_orig}R × {_mr_mult*100:.0f}% = {_mr_eff:.2f}R"
        if _mr_mult < 1.0 else f"{_mr_orig}R — full limit"
    )
    _LBL = "font-size:0.58rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;text-align:center;width:100%"
    _CARD_STYLE = ("background:#0e1220;border:1px solid #1a2238;border-top:3px solid {bc};"
                   "border-radius:10px;padding:16px 18px;"
                   "display:flex;flex-direction:column;align-items:center;text-align:center;gap:5px")

    # ── Phase 1 ──
    # If complete, collapse into expander; if not, show full
    if cl_phase1:
        with st.expander(f"Phase 1 — ✓ Grade {_mr_g} · {_mr_desc} · {_limit_line}", expanded=False):
            _p1_html = (
                f'<div style="{_CARD_STYLE.format(bc="#3b82f6")}">'
                f'<div class="checklist-badge-done" style="margin:0 auto">✓ Morning Report Card Complete</div>'
                f'<div style="{_LBL}">Grade</div>'
                f'<div style="font-size:2.2rem;font-weight:800;line-height:1" class="{_mr_css}">{_mr_g}</div>'
                f'<div style="font-size:0.75rem;color:#94a3b8">{_mr_desc}</div>'
                f'<div style="{_LBL};margin-top:4px">Score <span style="color:#e2e8f0;font-weight:700">{_mr_sc}/18</span>'
                f' · {_limit_line}</div>'
                f'</div>'
            )
            st.markdown(_p1_html, unsafe_allow_html=True)
            if _mr_g == "F":
                st.markdown(
                    '<div class="mr-no-trade">MORNING GRADE: F — DO NOT TRADE TODAY · Protect capital.</div>',
                    unsafe_allow_html=True,
                )
            st.markdown('<div style="margin-top:12px"></div>', unsafe_allow_html=True)
            _p1_lpad, _p1_rc, _p1_rpad = st.columns([2, 1, 2])
            with _p1_rc:
                if st.button("↩ Redo Check-In", key="redo_p1_sm", width="stretch"):
                    session["morning_report"] = {**_SESSION_DEFAULTS["morning_report"]}
                    checklist["phase1_complete"] = False
                    checklist["assets"] = []
                    session["checklist"] = checklist
                    _save_session(session)
                    st.rerun()
    else:
        st.markdown("""
        <div class="sec-hdr">
          <div class="sec-line"></div>
          <div class="sec-title">Phase 1</div>
          <div class="sec-line"></div>
        </div>
        <div style="text-align:center;font-size:0.7rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin:-12px 0 16px">Human Performance Check-In</div>""", unsafe_allow_html=True)
        st.markdown(
            f'<div style="{_CARD_STYLE.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge" style="margin:0 auto">Phase 1 — Human Performance</div>'
            f'<div style="font-size:0.88rem;color:#4b5a7a;margin-top:8px">○ Answer 6 questions to set your daily grade</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        with st.form("morning_report_form"):
            qc1, qc2 = st.columns(2)
            for i, (qkey, qlabel, qopts, _) in enumerate(_MR_QUESTIONS):
                with (qc1 if i < 3 else qc2):
                    st.selectbox(qlabel, qopts, key=f"mr_q_{qkey}")
            if st.form_submit_button("Submit Phase 1 →", width="stretch"):
                _total = 0
                for qkey, _, qopts, qscores in _MR_QUESTIONS:
                    sel = st.session_state.get(f"mr_q_{qkey}", qopts[0])
                    idx = qopts.index(sel) if sel in qopts else 0
                    _total += qscores[idx]
                _grade, _mult, _css, _desc = _mr_grade(_total)
                session["morning_report"] = {
                    "completed": True, "grade": _grade, "multiplier": _mult,
                    "score": _total, "description": _desc, "color": _css,
                }
                _save_session(session)
                st.rerun()
    if cl_phase1:
        # Check if all pre-trade phases are complete
        _all_p2_done = cl_assets and all(a.get("phase2_complete") for a in cl_assets)
        _all_p3_done = cl_assets and all(a.get("phase3_complete") for a in cl_assets)
        _news_done = checklist.get("news", {}).get("checked", False)
        _all_phases_done = _all_p2_done and _all_p3_done and _news_done

        # Check if all pre-trade phases are complete
        _all_p2_done = cl_assets and all(a.get("phase2_complete") for a in cl_assets)
        _all_p3_done = cl_assets and all(a.get("phase3_complete") for a in cl_assets)
        _news_done = checklist.get("news", {}).get("checked", False)
        _all_phases_done = _all_p2_done and _all_p3_done and _news_done

        # If all done → collapsed summary with toggle to expand
        if _all_phases_done:
            _p2_summary_parts = []
            for _sa in cl_assets:
                _sa_lbl = _sa.get("phase2", {}).get("context_label", "?")
                _p2_summary_parts.append(f"{_sa['name']}: {_sa_lbl}")
            _p2_summary = " · ".join(_p2_summary_parts)
            st.caption(f"Daily analysis complete: {_p2_summary}")
            _render_phases = st.checkbox("Show / edit daily analysis", value=False, key="show_phases_toggle")
        else:
            _render_phases = True

        if not _render_phases:
            # Still show news banner even when phases collapsed (but greyed out if expired)
            _news_data = checklist.get("news", {})
            if _news_data.get("has_news"):
                _nev_impact = _news_data.get("event_impact", "HIGH")
                _nev_et = _news_data.get("event_time", "?")
                _nev_local = _et_to_local(_nev_et, prefs.get("user_tz_offset", 1))
                _nev_status = _news_window_status(_nev_local)
                if _nev_status == "expired":
                    st.markdown(
                        f'<div style="background:#0e1220;border:1px solid #1e2a45;border-radius:12px;'
                        f'padding:12px;text-align:center;margin:8px 0;opacity:0.5">'
                        f'<span style="font-size:0.8rem;color:#4b5a7a;text-decoration:line-through">'
                        f'{_news_data.get("event_name","?")} at {_nev_local} local — event passed</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    try:
                        _lp = _nev_local.split(":")
                        _lh, _lm = int(_lp[0]), int(_lp[1])
                        _from_m = _lh * 60 + _lm - 30
                        _to_m = _lh * 60 + _lm + 30
                        _from_str = f"{(_from_m // 60) % 24:02d}:{_from_m % 60:02d}"
                        _to_str = f"{(_to_m // 60) % 24:02d}:{_to_m % 60:02d}"
                        _window = f"{_from_str} — {_to_str} local"
                    except Exception:
                        _window = f"30 min around {_nev_local}"
                    if _nev_impact in ("EXTREME", "HIGH"):
                        st.markdown(
                            f'<div style="background:#1a0000;border:2px solid #ef4444;border-radius:12px;'
                            f'padding:28px;text-align:center;margin:16px 0">'
                            f'<div style="font-size:2rem;font-weight:900;color:#ef4444;letter-spacing:0.06em;line-height:1">'
                            f'DO NOT TRADE</div>'
                            f'<div style="font-size:1.1rem;color:#e2e8f0;margin-top:12px;font-weight:600">'
                            f'{_window}</div>'
                            f'<div style="font-size:0.85rem;color:#94a3b8;margin-top:8px">'
                            f'{_news_data.get("event_name","?")} ({_nev_impact} impact) · {_nev_local} local / {_nev_et} ET</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

        if _render_phases:
         # ── Phase 2 — Market Context ──
         st.markdown("""
         <div class="sec-hdr">
           <div class="sec-line"></div>
           <div class="sec-title">Phase 2</div>
           <div class="sec-line"></div>
         </div>
         <div style="text-align:center;font-size:0.7rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin:-12px 0 16px">Market Context</div>""", unsafe_allow_html=True)
         _all_instruments = ["SOL", "BTC", "MNQ", "MES", "ETH", "SUI"]
         _DEFAULT_ASSETS = ["SOL", "BTC", "MNQ", "MES"]
         _used_names = [a["name"] for a in cl_assets]

         # Auto-populate defaults on first use
         if not cl_assets:
             for _da in _DEFAULT_ASSETS:
                 cl_assets.append({**_ASSET_DEFAULTS, "name": _da})
             checklist["assets"] = cl_assets
             session["checklist"] = checklist
             _save_session(session)
             st.rerun()

         # ── Per-asset TABS with "+" tab ──
         def _tab_label(a):
             _done = a.get("phase2_complete") and a.get("phase3_complete")
             _ctx = a.get("phase2", {}).get("context_label", "") if a.get("phase2_complete") else ""
             # Shorten context for tab: "MEAN REVERTING LONG" → "MR LONG"
             _short = _ctx.replace("MEAN REVERTING", "MR").replace("BREAKOUT EXPECTED", "BO").replace("CAUTION — INSIDE DAY", "ID")
             if _done and _short:
                 return f"✓ {a['name']} · {_short}"
             elif _short:
                 return f"{a['name']} · {_short}"
             return a["name"]
         _tab_labels = [_tab_label(a) for a in cl_assets] + ["+"]
         _asset_tabs = st.tabs(_tab_labels)

         # ── "+" tab — add new asset ──
         with _asset_tabs[-1]:
             _avail = [x for x in _all_instruments if x not in _used_names]
             _options = _avail + (["Custom..."] if True else [])
             if _options:
                 _sel = st.selectbox("Select asset to add", _options, key="new_asset_sel", label_visibility="collapsed")
                 if _sel == "Custom...":
                     _custom_name = st.text_input("Enter asset name", key="custom_asset_name", placeholder="e.g. DOGE, XRP, ES")
                     _final_name = _custom_name.strip().upper() if _custom_name else ""
                     if _final_name and _final_name in _used_names:
                         st.warning(f"{_final_name} already added.")
                         _final_name = ""
                 else:
                     _final_name = _sel
                 if _final_name and st.button(f"Add {_final_name}", key="add_asset_btn", width="stretch"):
                     cl_assets.append({**_ASSET_DEFAULTS, "name": _final_name})
                     checklist["assets"] = cl_assets
                     session["checklist"] = checklist
                     _save_session(session)
                     st.rerun()
                 if not _avail:
                     st.caption("All preset assets added. Use Custom to add more.")
             else:
                 st.caption("All preset assets added. Use Custom to add more.")

         # ── Asset content tabs ──
         for _ai, _asset in enumerate(cl_assets):
           with _asset_tabs[_ai]:
             _aname  = _asset["name"]
             _a_p2c  = _asset.get("phase2_complete", False)
             _a_p3c  = _asset.get("phase3_complete", False)
             _a_ready = _a_p2c and _a_p3c

             # Centered: ◀ Remove ▶ with confirm
             _rm_key = f"rm_pending_{_ai}"
             if st.session_state.get(_rm_key):
                 _cpad_l, _cf_y, _cf_n, _cpad_r = st.columns([2, 0.8, 0.8, 2])
                 with _cf_y:
                     if st.button("Confirm", key=f"rm_yes_{_ai}", type="primary", width="stretch"):
                         st.session_state[_rm_key] = False
                         cl_assets.pop(_ai)
                         checklist["assets"] = cl_assets
                         session["checklist"] = checklist
                         _save_session(session)
                         st.rerun()
                 with _cf_n:
                     if st.button("Cancel", key=f"rm_no_{_ai}", type="secondary", width="stretch"):
                         st.session_state[_rm_key] = False
                         st.rerun()
             else:
                 _pad_l, _ml, _rm, _mr, _pad_r = st.columns([2, 0.4, 0.8, 0.4, 2])
                 with _ml:
                     if _ai > 0 and st.button("◀", key=f"mvl_{_ai}", width="stretch"):
                         cl_assets[_ai], cl_assets[_ai - 1] = cl_assets[_ai - 1], cl_assets[_ai]
                         checklist["assets"] = cl_assets
                         session["checklist"] = checklist
                         _save_session(session)
                         st.rerun()
                 with _rm:
                     if st.button("✕ Remove", key=f"rm_{_ai}", type="secondary", width="stretch"):
                         st.session_state[_rm_key] = True
                         st.rerun()
                 with _mr:
                     if _ai < len(cl_assets) - 1 and st.button("▶", key=f"mvr_{_ai}", width="stretch"):
                         cl_assets[_ai], cl_assets[_ai + 1] = cl_assets[_ai + 1], cl_assets[_ai]
                         checklist["assets"] = cl_assets
                         session["checklist"] = checklist
                         _save_session(session)
                         st.rerun()

             # ── Phase 2 — Market Context (pbD 5-step derivation) ──
             if not _a_p2c:
                 # Step 1 — Day Type (clickable image cards OUTSIDE form)
                 st.markdown(
                     '<div class="decision-hdg">Step 1 — Previous Day\'s TPO Profile Type</div>',
                     unsafe_allow_html=True,
                 )
                 _DAY_TYPE_IMAGES = {
                     "Normal Day": os.path.join(_APP_DIR, "assets", "day_normal.png"),
                     "Normal Day Variation (Long)": os.path.join(_APP_DIR, "assets", "day_ndv long.png"),
                     "Normal Day Variation (Short)": os.path.join(_APP_DIR, "assets", "day_ndv short.png"),
                     "Inside Day": os.path.join(_APP_DIR, "assets", "day_inside.png"),
                     "Trend Day (P-profile)": os.path.join(_APP_DIR, "assets", "day_trend_p.png"),
                     "Trend Day (b-profile)": os.path.join(_APP_DIR, "assets", "day_trend_b.png"),
                     "Double Distribution": os.path.join(_APP_DIR, "assets", "day_double_dist.png"),
                 }
                 _DAY_TYPE_SHORT_LABELS = {
                     "Normal Day": "Normal Day",
                     "Normal Day Variation (Long)": "Normal Variation Long",
                     "Normal Day Variation (Short)": "Normal Variation Short",
                     "Inside Day": "Inside Day",
                     "Trend Day (P-profile)": "Trend P",
                     "Trend Day (b-profile)": "Trend b",
                     "Double Distribution": "Double Dist.",
                 }
                 _dt_key = f"p2s_{_ai}"
                 _current_dt = st.session_state.get(_dt_key, _MARKET_STATES[0])

                 # Row 1: Normal Day, NDV Long, NDV Short, Inside Day (4 cols)
                 _dt_r1 = st.columns(4)
                 _r1_types = ["Normal Day", "Normal Day Variation (Long)", "Normal Day Variation (Short)", "Inside Day"]
                 for _dti, _dt_name in enumerate(_r1_types):
                     with _dt_r1[_dti]:
                         _dt_img = _DAY_TYPE_IMAGES.get(_dt_name)
                         if _dt_img and os.path.exists(_dt_img):
                             st.image(_dt_img, width="stretch")
                         _is_sel = _current_dt == _dt_name
                         _btn_type = "primary" if _is_sel else "secondary"
                         if st.button(
                             _DAY_TYPE_SHORT_LABELS.get(_dt_name, _dt_name),
                             key=f"dt_{_ai}_{_dti}", type=_btn_type, width="stretch",
                         ):
                             st.session_state[_dt_key] = _dt_name
                             st.rerun()

                 # Row 2: Trend P, Trend b, Double Distribution, empty (4 cols to match row 1)
                 _dt_r2 = st.columns(4)
                 _r2_types = ["Trend Day (P-profile)", "Trend Day (b-profile)", "Double Distribution"]
                 for _dti2, _dt_name2 in enumerate(_r2_types):
                     with _dt_r2[_dti2]:
                         _dt_img2 = _DAY_TYPE_IMAGES.get(_dt_name2)
                         if _dt_img2 and os.path.exists(_dt_img2):
                             st.image(_dt_img2, width="stretch")
                         _is_sel2 = _current_dt == _dt_name2
                         _btn_type2 = "primary" if _is_sel2 else "secondary"
                         if st.button(
                             _DAY_TYPE_SHORT_LABELS.get(_dt_name2, _dt_name2),
                             key=f"dt_{_ai}_{_dti2 + 4}", type=_btn_type2, width="stretch",
                         ):
                             st.session_state[_dt_key] = _dt_name2
                             st.rerun()

                 st.markdown('<hr style="border-color:#1a2238;margin:16px 0">', unsafe_allow_html=True)

                 # Steps 2-5 (outside form so session state updates live)

                 # Step 2 & 3 — Open & Close Location
                 st.markdown(
                     '<div class="decision-hdg">Step 2 & 3 — Previous Day Open & Close</div>',
                     unsafe_allow_html=True,
                 )
                 _oc1, _oc2 = st.columns(2)
                 with _oc1:
                     _p2_open = st.radio("Yesterday price OPEN", _VA_LOCATIONS, key=f"p2o_{_ai}", index=1)
                 with _oc2:
                     _p2_close = st.radio("Yesterday price CLOSE", _VA_LOCATIONS, key=f"p2c_{_ai}", index=1)

                 st.markdown('<hr style="border-color:#1a2238;margin:16px 0">', unsafe_allow_html=True)

                 # Step 4 — Tails
                 st.markdown(
                     '<div class="decision-hdg">Step 4 — Tail Quality</div>',
                     unsafe_allow_html=True,
                 )
                 _p2_tail = st.radio(
                     "Previous day buying/selling tails?", _TAIL_OPTIONS,
                     key=f"p2t_{_ai}", horizontal=True,
                 )

                 st.markdown('<hr style="border-color:#1a2238;margin:16px 0">', unsafe_allow_html=True)

                 # Step 5 — Initial Balance
                 st.markdown(
                     '<div class="decision-hdg">Step 5 — Initial Balance (first 30 min)</div>',
                     unsafe_allow_html=True,
                 )
                 _p2_ib = st.radio(
                     "Today's IB size", _IB_OPTIONS, key=f"p2ib_{_ai}", horizontal=True,
                 )

                 st.markdown('<hr style="border-color:#1a2238;margin:16px 0">', unsafe_allow_html=True)

                 # ── Live derived context preview ──
                 _ctx_label, _ctx_conf, _ctx_dir, _ctx_struct = _derive_context(
                     st.session_state.get(f"p2s_{_ai}", _MARKET_STATES[0]),
                     _p2_open,
                     _p2_close,
                     _p2_tail,
                     _p2_ib,
                 )
                 _ctx_col = _context_color(_ctx_label)
                 st.markdown(
                     f'<div style="background:#0a0e18;border:2px solid {_ctx_col};border-radius:12px;'
                     f'padding:24px;text-align:center;margin:8px 0">'
                     f'<div style="font-size:0.6rem;color:#4b5a7a;text-transform:uppercase;'
                     f'letter-spacing:.12em;font-weight:600;margin-bottom:8px">Derived Context</div>'
                     f'<div style="font-size:2rem;font-weight:900;color:{_ctx_col};'
                     f'letter-spacing:0.04em;line-height:1.1">{_ctx_label}</div>'
                     f'<div style="font-size:0.85rem;color:#94a3b8;margin-top:8px">'
                     f'Confidence: <strong style="color:{_ctx_col}">{_ctx_conf}%</strong></div>'
                     f'</div>',
                     unsafe_allow_html=True,
                 )

                 # Confirm button
                 _cf_pad1, _cf_btn, _cf_pad2 = st.columns([1, 2, 1])
                 with _cf_btn:
                     if st.button(f"Confirm Market Context for {_aname} →", key=f"p2_confirm_{_ai}", width="stretch"):
                         cl_assets[_ai]["phase2"] = {
                             "day_type":       st.session_state.get(f"p2s_{_ai}", _MARKET_STATES[0]),
                             "open_location":  _p2_open,
                             "close_location": _p2_close,
                             "tail":           _p2_tail,
                             "ib_size":        _p2_ib,
                             "context_label":  _ctx_label,
                             "confidence":     _ctx_conf,
                             "direction_score": _ctx_dir,
                             "structure":      _ctx_struct,
                         }
                         cl_assets[_ai]["phase2_complete"] = True
                         checklist["assets"] = cl_assets
                         session["checklist"] = checklist
                         _save_session(session)
                         _log_context(_aname, cl_assets[_ai]["phase2"])
                         st.rerun()
             else:
                 # ── Phase 2 Complete — show summary ──
                 _p2d = _asset.get("phase2", {})
                 _ctx_lbl = _p2d.get("context_label", "?")
                 _ctx_cnf = _p2d.get("confidence", 0)
                 _ctx_c = _context_color(_ctx_lbl)
                 st.markdown(
                     f'<div style="background:#0a0e18;border:2px solid {_ctx_c};border-radius:12px;'
                     f'padding:20px;text-align:center;margin:8px 0">'
                     f'<div style="font-size:0.6rem;color:#4b5a7a;text-transform:uppercase;'
                     f'letter-spacing:.12em;font-weight:600;margin-bottom:6px">Today\'s Context · {_aname}</div>'
                     f'<div style="font-size:2rem;font-weight:900;color:{_ctx_c};'
                     f'letter-spacing:0.04em;line-height:1.1">{_ctx_lbl}</div>'
                     f'<div style="font-size:0.85rem;color:#94a3b8;margin-top:8px">'
                     f'Confidence: <strong style="color:{_ctx_c}">{_ctx_cnf}%</strong></div>'
                     f'<div style="font-size:0.7rem;color:#4b5a7a;margin-top:10px">'
                     f'{_p2d.get("day_type","?")} · Open: {_p2d.get("open_location","?")} · '
                     f'Close: {_p2d.get("close_location","?")} · {_p2d.get("tail","?")} · '
                     f'{_p2d.get("ib_size","?")}</div>'
                     f'</div>',
                     unsafe_allow_html=True,
                 )
                 _rd_pad, _rd_btn, _rd_pad2 = st.columns([2, 1, 2])
                 with _rd_btn:
                     if st.button(f"✏ Edit", key=f"rdop2_{_ai}", type="secondary", width="stretch"):
                         # Pre-populate session_state keys from saved Phase 2 data
                         _p2_saved = cl_assets[_ai].get("phase2", {})
                         if _p2_saved.get("day_type"):
                             st.session_state[f"p2s_{_ai}"] = _p2_saved["day_type"]
                         if _p2_saved.get("open_location"):
                             st.session_state[f"p2o_{_ai}"] = _p2_saved["open_location"]
                         if _p2_saved.get("close_location"):
                             st.session_state[f"p2c_{_ai}"] = _p2_saved["close_location"]
                         if _p2_saved.get("tail"):
                             st.session_state[f"p2t_{_ai}"] = _p2_saved["tail"]
                         if _p2_saved.get("ib_size"):
                             st.session_state[f"p2ib_{_ai}"] = _p2_saved["ib_size"]
                         # Keep phase2 data but mark incomplete so form re-renders
                         cl_assets[_ai]["phase2_complete"] = False
                         checklist["assets"] = cl_assets
                         session["checklist"] = checklist
                         _save_session(session)
                         st.rerun()

             # ── Trade Setup (Strategy Tabs: Swing / Day Trade / Scalp) ──
             if _a_p2c:
                 st.markdown(
                     f'<div class="sec-hdr" style="margin:20px 0 6px">'
                     f'<div class="sec-line"></div>'
                     f'<div class="sec-title">Trade Setup</div>'
                     f'<div class="sec-line"></div>'
                     f'</div>'
                     f'<div style="text-align:center;font-size:0.7rem;color:#4b5a7a;text-transform:uppercase;'
                     f'letter-spacing:.1em;font-weight:600;margin:0 0 12px">{_aname}</div>',
                     unsafe_allow_html=True,
                 )

                 _strat_tabs = st.tabs(["Swing Trade", "Day Trade", "Scalp"])

                 # ── TAB 1: Swing Trade (Opening Variants — existing rules) ──
                 with _strat_tabs[0]:
                     _VARIANT_IMAGES = {
                         "Variant 1": os.path.join(_APP_DIR, "assets", "variant_1_within_va.png"),
                         "Variant 2": os.path.join(_APP_DIR, "assets", "variant_2_va_absolute.png"),
                         "Variant 3": os.path.join(_APP_DIR, "assets", "variant_3_outside_stays.png"),
                         "Variant 4": os.path.join(_APP_DIR, "assets", "variant_4_outside_returns.png"),
                     }
                     _VARIANT_INFO = {
                         "Variant 1": {
                             "title": "Open Within VA",
                             "desc": "Price opens within yesterday's VA. Once it moves to VAH or VAL and stays 30min → full VA traverse likely.",
                             "entry": "At VAH or VAL (whichever price reaches first)",
                             "tp": "Opposite VAH/VAL",
                             "sl": "Beyond absolute high/low of the day",
                         },
                         "Variant 2": {
                             "title": "Open Between VA-abs and VAH/VAL",
                             "desc": "Price opened outside VA but within absolute range. Likely heading to vPOC. Trade the bounce back.",
                             "entry": "At vPOC after bounce confirmation",
                             "tp": "Back to today's opening price",
                             "sl": "Beyond vPOC",
                         },
                         "Variant 3": {
                             "title": "Open Outside VA-abs (Stays Out)",
                             "desc": "Strong gap/imbalance. Price stays outside — expect acceleration away from previous VA.",
                             "entry": "On retests of structure breaks",
                             "tp": "Big-picture levels, naked POCs, prior VAs",
                             "sl": "Between VA-absolute and VAH/VAL",
                         },
                         "Variant 4": {
                             "title": "Open Outside VA-abs → Returns Into VA (80% Rule)",
                             "desc": "Price opened outside but returned into VA and stayed 30min. 80% probability of full VA traverse.",
                             "entry": "At VAH or VAL (NOT at VA-absolute)",
                             "tp": "Opposite VAH/VAL (full traverse)",
                             "sl": "Between VAH/VAL and absolute high/low",
                         },
                     }
                     _vs_key = f"variant_{_ai}"
                     _current_variant = st.session_state.get(_vs_key, None)

                     st.markdown(
                         '<div style="text-align:center;font-size:0.65rem;color:#4b5a7a;text-transform:uppercase;'
                         'letter-spacing:.1em;font-weight:600;margin:0 0 10px">Opening Variant</div>',
                         unsafe_allow_html=True,
                     )

                     # 4 variant cards in a row
                     _v_cols = st.columns(4)
                     for _vi, (_vk, _vinfo) in enumerate(_VARIANT_INFO.items()):
                         with _v_cols[_vi]:
                             _v_img = _VARIANT_IMAGES.get(_vk)
                             if _v_img and os.path.exists(_v_img):
                                 st.image(_v_img, use_container_width=True)
                             _v_sel = _current_variant == _vk
                             _v_btn_type = "primary" if _v_sel else "secondary"
                             if st.button(
                                 _vinfo["title"].split("(")[0].strip(),
                                 key=f"var_{_ai}_{_vi}", type=_v_btn_type, width="stretch",
                             ):
                                 st.session_state[_vs_key] = _vk
                                 st.rerun()

                     # Show selected variant details
                     if _current_variant and _current_variant in _VARIANT_INFO:
                         _sv = _VARIANT_INFO[_current_variant]
                         st.markdown(
                             f'<div style="background:#0a0e18;border:1px solid #1e2a45;border-radius:10px;'
                             f'padding:16px 20px;margin:10px 0;text-align:center">'
                             f'<div style="font-size:0.95rem;font-weight:700;color:#e2e8f0;margin-bottom:8px">'
                             f'{_sv["title"]}</div>'
                             f'<div style="font-size:0.8rem;color:#94a3b8;margin-bottom:12px">{_sv["desc"]}</div>'
                             f'<div style="display:flex;gap:16px;justify-content:center;flex-wrap:wrap">'
                             f'<div style="text-align:center">'
                             f'<div style="font-size:0.6rem;color:#22c55e;text-transform:uppercase;letter-spacing:.08em;font-weight:600">Entry</div>'
                             f'<div style="font-size:0.75rem;color:#e2e8f0">{_sv["entry"]}</div></div>'
                             f'<div style="text-align:center">'
                             f'<div style="font-size:0.6rem;color:#3b82f6;text-transform:uppercase;letter-spacing:.08em;font-weight:600">Take Profit</div>'
                             f'<div style="font-size:0.75rem;color:#e2e8f0">{_sv["tp"]}</div></div>'
                             f'<div style="text-align:center">'
                             f'<div style="font-size:0.6rem;color:#ef4444;text-transform:uppercase;letter-spacing:.08em;font-weight:600">Stop Loss</div>'
                             f'<div style="font-size:0.75rem;color:#e2e8f0">{_sv["sl"]}</div></div>'
                             f'</div></div>',
                             unsafe_allow_html=True,
                         )

                 # ── TAB 2: Day Trade (placeholder) ──
                 with _strat_tabs[1]:
                     st.markdown(
                         '<div style="background:#0a0e18;border:1px dashed #1e2a45;border-radius:12px;'
                         'padding:40px 24px;text-align:center;margin:8px 0">'
                         '<div style="font-size:1.1rem;font-weight:700;color:#4b5a7a;margin-bottom:8px">Day Trade Rules</div>'
                         '<div style="font-size:0.82rem;color:#3a4560">Coming soon — intraday setups, IB-based entries, '
                         'micro-structure analysis, and session-specific timing rules.</div>'
                         '</div>',
                         unsafe_allow_html=True,
                     )

                 # ── TAB 3: Scalp (placeholder) ──
                 with _strat_tabs[2]:
                     st.markdown(
                         '<div style="background:#0a0e18;border:1px dashed #1e2a45;border-radius:12px;'
                         'padding:40px 24px;text-align:center;margin:8px 0">'
                         '<div style="font-size:1.1rem;font-weight:700;color:#4b5a7a;margin-bottom:8px">Scalp Rules</div>'
                         '<div style="font-size:0.82rem;color:#3a4560">Coming soon — footprint-based scalp entries, '
                         'order flow confirmation, delta divergence, and micro-pullback setups.</div>'
                         '</div>',
                         unsafe_allow_html=True,
                     )

             # ── P-Setup / b-Setup Entry Logic (when day type is trending) ──
             if _a_p2c:
                 _p2_dt = _asset.get("phase2", {}).get("day_type", "")
                 _p2_struct = _asset.get("phase2", {}).get("structure", "MR")
                 if _p2_dt in ("Trend Day (P-profile)", "Trend Day (b-profile)") or _p2_struct == "TR":
                     _is_p = _p2_dt == "Trend Day (P-profile)" or (_p2_struct == "TR" and _asset.get("phase2", {}).get("direction_score", 0) > 0)
                     _setup_name = "P-Setup (Long)" if _is_p else "b-Setup (Short)"
                     _setup_dir = "long" if _is_p else "short"
                     _sk = f"pbs_{_ai}"

                     st.markdown(
                         f'<div class="sec-hdr" style="margin:20px 0 6px">'
                         f'<div class="sec-line"></div>'
                         f'<div class="sec-title">{_setup_name}</div>'
                         f'<div class="sec-line"></div>'
                         f'</div>'
                         f'<div style="text-align:center;font-size:0.7rem;color:#4b5a7a;text-transform:uppercase;'
                         f'letter-spacing:.1em;font-weight:600;margin:0 0 12px">3-Stage Entry · {_aname}</div>',
                         unsafe_allow_html=True,
                     )

                     _stages = [
                         ("Pullback", "Price retraces to key level (vPOC, VA edge, or prior structure)"),
                         ("Break-In", "30M candle closes back into value / structure — acceptance confirmed"),
                         ("Break-Out", "Price breaks beyond structure in trend direction — enter on retest"),
                     ]
                     _stage_val = st.session_state.get(_sk, 0)
                     _stage_html = '<div style="display:flex;gap:8px;margin:8px 0 12px">'
                     for _si, (_sname, _sdesc) in enumerate(_stages):
                         _scls = "done" if _si < _stage_val else ("active" if _si == _stage_val else "")
                         _scolor = "#22c55e" if _si < _stage_val else ("#f97316" if _si == _stage_val else "#4b5a7a")
                         _sicon = "✓" if _si < _stage_val else f"{_si + 1}"
                         _stage_html += (
                             f'<div class="setup-stage {_scls}">'
                             f'<div class="setup-stage-val" style="color:{_scolor}">{_sicon} {_sname}</div>'
                             f'<div class="setup-stage-lbl">{_sdesc}</div>'
                             f'</div>'
                         )
                     _stage_html += '</div>'
                     st.markdown(_stage_html, unsafe_allow_html=True)

                     _pb_cols = st.columns(3)
                     with _pb_cols[0]:
                         if _stage_val == 0:
                             if st.button("Pullback Confirmed", key=f"pbs1_{_ai}", width="stretch", type="primary"):
                                 st.session_state[_sk] = 1
                                 st.rerun()
                         else:
                             st.button("✓ Pullback", key=f"pbs1d_{_ai}", width="stretch", disabled=True)
                     with _pb_cols[1]:
                         if _stage_val == 1:
                             if st.button("Break-In Confirmed", key=f"pbs2_{_ai}", width="stretch", type="primary"):
                                 st.session_state[_sk] = 2
                                 st.rerun()
                         elif _stage_val > 1:
                             st.button("✓ Break-In", key=f"pbs2d_{_ai}", width="stretch", disabled=True)
                         else:
                             st.button("Break-In", key=f"pbs2w_{_ai}", width="stretch", disabled=True)
                     with _pb_cols[2]:
                         if _stage_val == 2:
                             if st.button("Break-Out — ENTER", key=f"pbs3_{_ai}", width="stretch", type="primary"):
                                 st.session_state[_sk] = 3
                                 st.rerun()
                         elif _stage_val > 2:
                             st.button("✓ Entry Confirmed", key=f"pbs3d_{_ai}", width="stretch", disabled=True)
                         else:
                             st.button("Break-Out", key=f"pbs3w_{_ai}", width="stretch", disabled=True)

                     if _stage_val >= 3:
                         _entry_msg = "LONG entry on retest — trade WITH the trend" if _is_p else "SHORT entry on retest — trade WITH the trend"
                         st.markdown(
                             f'<div style="background:#0a1a0a;border:2px solid #22c55e;border-radius:10px;'
                             f'padding:14px;text-align:center;margin:8px 0">'
                             f'<div style="font-size:0.9rem;font-weight:800;color:#22c55e;letter-spacing:0.04em">'
                             f'{_entry_msg}</div></div>',
                             unsafe_allow_html=True,
                         )
                     if _stage_val > 0:
                         if st.button("Reset Stages", key=f"pbs_reset_{_ai}", type="secondary"):
                             st.session_state[_sk] = 0
                             st.rerun()

             # ── Breakout Models (when structure is trending or ID) ──
             if _a_p2c:
                 _p2_struct2 = _asset.get("phase2", {}).get("structure", "MR")
                 if _p2_struct2 in ("TR", "ID"):
                     st.markdown(
                         f'<div class="sec-hdr" style="margin:20px 0 6px">'
                         f'<div class="sec-line"></div>'
                         f'<div class="sec-title">Breakout Model</div>'
                         f'<div class="sec-line"></div>'
                         f'</div>'
                         f'<div style="text-align:center;font-size:0.7rem;color:#4b5a7a;text-transform:uppercase;'
                         f'letter-spacing:.1em;font-weight:600;margin:0 0 12px">Opening Type · {_aname}</div>',
                         unsafe_allow_html=True,
                     )
                     _BREAKOUT_MODELS = {
                         "Open Drive": {
                             "desc": "Price opens and drives immediately in one direction with conviction. No test of open price.",
                             "action": "Enter immediately on first pullback to microstructure. Aggressive — small SL.",
                             "confidence": "Highest conviction — strong institutional flow.",
                             "color": "#22c55e",
                         },
                         "Open Test Drive": {
                             "desc": "Price opens, tests a nearby level (IB edge, prior VA), then drives directionally.",
                             "action": "Wait for the test, confirm rejection, then enter in drive direction.",
                             "confidence": "High — the test confirms the level holds.",
                             "color": "#3b82f6",
                         },
                         "Open Rejection Reverse": {
                             "desc": "Price opens in one direction, gets rejected at key level, reverses with conviction.",
                             "action": "Enter the reversal after 30M close confirms new direction. Fade the initial move.",
                             "confidence": "Medium — need clear rejection signal (footprint, volume).",
                             "color": "#f59e0b",
                         },
                         "Open Auction": {
                             "desc": "Price opens and auctions in both directions. No immediate conviction. Rotational.",
                             "action": "Wait for IB to form. Trade the break of IB range. Do NOT enter early.",
                             "confidence": "Lower — wait for IB breakout confirmation.",
                             "color": "#94a3b8",
                         },
                     }
                     _bm_key = f"bm_{_ai}"
                     _current_bm = st.session_state.get(_bm_key, None)

                     _bm_cols = st.columns(4)
                     for _bmi, (_bmk, _bmv) in enumerate(_BREAKOUT_MODELS.items()):
                         with _bm_cols[_bmi]:
                             _bm_sel = _current_bm == _bmk
                             _bm_type = "primary" if _bm_sel else "secondary"
                             if st.button(_bmk, key=f"bm_{_ai}_{_bmi}", type=_bm_type, width="stretch"):
                                 st.session_state[_bm_key] = _bmk
                                 st.rerun()

                     if _current_bm and _current_bm in _BREAKOUT_MODELS:
                         _bmd = _BREAKOUT_MODELS[_current_bm]
                         st.markdown(
                             f'<div style="background:#0a0e18;border:1px solid {_bmd["color"]};border-radius:10px;'
                             f'padding:16px 20px;margin:10px 0;text-align:center">'
                             f'<div style="font-size:0.95rem;font-weight:700;color:#e2e8f0;margin-bottom:8px">{_current_bm}</div>'
                             f'<div style="font-size:0.8rem;color:#94a3b8;margin-bottom:12px">{_bmd["desc"]}</div>'
                             f'<div style="display:flex;gap:16px;justify-content:center;flex-wrap:wrap">'
                             f'<div style="text-align:center">'
                             f'<div style="font-size:0.6rem;color:{_bmd["color"]};text-transform:uppercase;letter-spacing:.08em;font-weight:600">Action</div>'
                             f'<div style="font-size:0.75rem;color:#e2e8f0">{_bmd["action"]}</div></div>'
                             f'<div style="text-align:center">'
                             f'<div style="font-size:0.6rem;color:{_bmd["color"]};text-transform:uppercase;letter-spacing:.08em;font-weight:600">Confidence</div>'
                             f'<div style="font-size:0.75rem;color:#e2e8f0">{_bmd["confidence"]}</div></div>'
                             f'</div></div>',
                             unsafe_allow_html=True,
                         )

             # ── Phase 3 ──
             if _a_p2c:
                 st.markdown(
                     f'<div class="sec-hdr" style="margin:20px 0 6px">'
                     f'<div class="sec-line"></div>'
                     f'<div class="sec-title">Phase 3</div>'
                     f'<div class="sec-line"></div>'
                     f'</div>'
                     f'<div style="text-align:center;font-size:0.7rem;color:#4b5a7a;text-transform:uppercase;'
                     f'letter-spacing:.1em;font-weight:600;margin:0 0 12px">Key Levels · {_aname}</div>',
                     unsafe_allow_html=True,
                 )
                 if not _a_p3c:
                     _p3_items = [
                         (f"p3sr_{_ai}", "Support / Resistance", "Key levels marked on chart"),
                         (f"p3tl_{_ai}", "Trend Lines", "Drawn on relevant timeframes"),
                         (f"p3al_{_ai}", "Price Alerts", "Set at key levels"),
                     ]
                     _p3_cols = st.columns(3)
                     for _p3i, (_p3k, _p3title, _p3sub) in enumerate(_p3_items):
                         _p3_on = st.session_state.get(_p3k, False)
                         with _p3_cols[_p3i]:
                             _p3_border = "#22c55e" if _p3_on else "#1e2a45"
                             _p3_bg = "#0a1a0a" if _p3_on else "#0a0e18"
                             _p3_icon = "✓" if _p3_on else "○"
                             _p3_icon_col = "#22c55e" if _p3_on else "#4b5a7a"
                             st.markdown(
                                 f'<div style="background:{_p3_bg};border:2px solid {_p3_border};border-radius:10px;'
                                 f'padding:18px 14px;text-align:center;min-height:100px;'
                                 f'display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px">'
                                 f'<div style="font-size:1.4rem;color:{_p3_icon_col};line-height:1">{_p3_icon}</div>'
                                 f'<div style="font-size:0.95rem;font-weight:700;color:#e2e8f0">{_p3title}</div>'
                                 f'<div style="font-size:0.7rem;color:#4b5a7a">{_p3sub}</div>'
                                 f'</div>',
                                 unsafe_allow_html=True,
                             )
                             if st.button(
                                 "Done ✓" if _p3_on else "Mark Done",
                                 key=f"p3btn_{_p3k}",
                                 type="primary" if _p3_on else "secondary",
                                 width="stretch",
                             ):
                                 st.session_state[_p3k] = not _p3_on
                                 st.rerun()
                     _p3_all = all(st.session_state.get(k, False) for k, _, _ in _p3_items)
                     if _p3_all:
                         _p3_pad1, _p3_btn, _p3_pad2 = st.columns([1, 2, 1])
                         with _p3_btn:
                             if st.button(f"Confirm Phase 3 for {_aname} →", key=f"p3_confirm_{_ai}", width="stretch", type="primary"):
                                 cl_assets[_ai]["phase3"] = {"sr_levels": True, "trend_lines": True, "price_alerts": True}
                                 cl_assets[_ai]["phase3_complete"] = True
                                 checklist["assets"] = cl_assets
                                 session["checklist"] = checklist
                                 _save_session(session)
                                 st.rerun()
                 else:
                     _p3_done_html = (
                         '<div style="display:flex;gap:10px;justify-content:center;margin:8px 0">'
                     )
                     for _p3_lbl in ["Support / Resistance", "Trend Lines", "Price Alerts"]:
                         _p3_done_html += (
                             f'<div style="background:#0a1a0a;border:2px solid #22c55e;border-radius:10px;'
                             f'padding:14px 18px;text-align:center;flex:1;max-width:200px">'
                             f'<div style="font-size:1.2rem;color:#22c55e;line-height:1">✓</div>'
                             f'<div style="font-size:0.85rem;font-weight:700;color:#e2e8f0;margin-top:4px">{_p3_lbl}</div>'
                             f'</div>'
                         )
                     _p3_done_html += '</div>'
                     st.markdown(_p3_done_html, unsafe_allow_html=True)
                     _p3e_pad, _p3e_btn, _p3e_pad2 = st.columns([2, 1, 2])
                     with _p3e_btn:
                         if st.button(f"✏ Edit Phase 3", key=f"rdop3_{_ai}", type="secondary", width="stretch"):
                             cl_assets[_ai]["phase3_complete"] = False
                             checklist["assets"] = cl_assets
                             session["checklist"] = checklist
                             _save_session(session)
                             st.rerun()

         # ── News Events Gate (shared — after all Phase 3s, before trading) ──
         _NEWS_EVENTS = [
             ("FOMC Rate Decision", "EXTREME"),
             ("NFP (Non-Farm Payrolls)", "EXTREME"),
             ("CPI / Core CPI", "EXTREME"),
             ("PPI", "EXTREME"),
             ("PCE (Core)", "EXTREME"),
             ("Fed Chair Speaks", "EXTREME"),
             ("FOMC Minutes", "HIGH"),
             ("ECB / BOJ Decision", "HIGH"),
             ("GDP (Advance)", "MEDIUM"),
             ("Jobless Claims", "MEDIUM"),
             ("Other", "MEDIUM"),
         ]
         _news_data = checklist.get("news", {})
         if cl_any_ready and not _news_data.get("checked"):
             st.markdown("""
             <div class="sec-hdr" style="margin:24px 0 6px">
               <div class="sec-line"></div>
               <div class="sec-title">News Check</div>
               <div class="sec-line"></div>
             </div>
             <div style="text-align:center;font-size:0.7rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin:0 0 12px">Before you trade — check for high-impact events</div>""",
                 unsafe_allow_html=True,
             )
             _nyn_pad1, _nyn_c, _nyn_pad2 = st.columns([1, 2, 1])
             with _nyn_c:
                 _news_yn = st.radio("High-impact news event today?", ["No", "Yes"], key="news_yn_gate", horizontal=True)
             if _news_yn == "Yes":
                 _nev_pad1, _nev_c, _nev_pad2 = st.columns([1, 2, 1])
                 with _nev_c:
                     _ev_names = [f"{n} ({lvl})" for n, lvl in _NEWS_EVENTS]
                     _sel_event = st.selectbox("Select event", _ev_names, key="news_event_sel")
                     _ev_time = st.text_input("Event time (ET / New York)", placeholder="e.g. 08:30, 14:00", key="news_event_time")
                     if _ev_time:
                         _local_preview = _et_to_local(_ev_time, prefs.get("user_tz_offset", 1))
                         st.caption(f"Your local time: **{_local_preview}** (UTC+{prefs.get('user_tz_offset', 1)})")
                 _ev_base_name = _sel_event.split(" (")[0] if _sel_event else ""
                 _ev_impact = next((lvl for n, lvl in _NEWS_EVENTS if n == _ev_base_name), "MEDIUM")
             _npad1, _nbtn, _npad2 = st.columns([1, 2, 1])
             with _nbtn:
                 if st.button("Confirm News Check →", key="news_confirm", width="stretch"):
                     _hn = _news_yn == "Yes"
                     checklist["news"] = {
                         "checked":    True,
                         "has_news":   _hn,
                         "event_name": _ev_base_name if _hn else "",
                         "event_time": _ev_time if _hn else "",
                         "event_impact": _ev_impact if _hn else "",
                     }
                     session["checklist"] = checklist
                     _save_session(session)
                     st.rerun()

         # ── News event warning banner (persistent, status-aware) ──
         if _news_data.get("has_news"):
             _nev_impact = _news_data.get("event_impact", "HIGH")
             _nev_et = _news_data.get("event_time", "?")
             _nev_local = _et_to_local(_nev_et, prefs.get("user_tz_offset", 1))
             _nev_status = _news_window_status(_nev_local)
             try:
                 _lp = _nev_local.split(":")
                 _lh, _lm = int(_lp[0]), int(_lp[1])
                 _from_m = _lh * 60 + _lm - 30
                 _to_m = _lh * 60 + _lm + 30
                 _from_str = f"{(_from_m // 60) % 24:02d}:{_from_m % 60:02d}"
                 _to_str = f"{(_to_m // 60) % 24:02d}:{_to_m % 60:02d}"
                 _window = f"{_from_str} — {_to_str} local"
             except Exception:
                 _window = f"30 min around {_nev_local}"
             if _nev_status == "expired":
                 # Event passed — greyed out
                 st.markdown(
                     f'<div style="background:#0e1220;border:1px solid #1e2a45;border-radius:12px;'
                     f'padding:16px;text-align:center;margin:8px 0;opacity:0.5">'
                     f'<div style="font-size:0.85rem;color:#4b5a7a;text-decoration:line-through">'
                     f'{_news_data.get("event_name","?")} at {_nev_local} local — event passed</div>'
                     f'</div>',
                     unsafe_allow_html=True,
                 )
             elif _nev_impact in ("EXTREME", "HIGH"):
                 st.markdown(
                     f'<div style="background:#1a0000;border:2px solid #ef4444;border-radius:12px;'
                     f'padding:28px;text-align:center;margin:16px 0">'
                     f'<div style="font-size:2rem;font-weight:900;color:#ef4444;letter-spacing:0.06em;line-height:1">'
                     f'DO NOT TRADE</div>'
                     f'<div style="font-size:1.1rem;color:#e2e8f0;margin-top:12px;font-weight:600">'
                     f'{_window}</div>'
                     f'<div style="font-size:0.85rem;color:#94a3b8;margin-top:8px">'
                     f'{_news_data.get("event_name","?")} ({_nev_impact} impact) · {_nev_local} local / {_nev_et} ET</div>'
                     f'</div>',
                     unsafe_allow_html=True,
                 )
             else:
                 st.markdown(
                     f'<div style="background:#1a1400;border:2px solid #f59e0b;border-radius:12px;'
                     f'padding:28px;text-align:center;margin:16px 0">'
                     f'<div style="font-size:2rem;font-weight:900;color:#f59e0b;letter-spacing:0.06em;line-height:1">'
                     f'CAUTION</div>'
                     f'<div style="font-size:1.1rem;color:#e2e8f0;margin-top:12px;font-weight:600">'
                     f'{_window}</div>'
                     f'<div style="font-size:0.85rem;color:#94a3b8;margin-top:8px">'
                     f'{_news_data.get("event_name","?")} ({_nev_impact} impact) · {_nev_local} local / {_nev_et} ET</div>'
                     f'</div>',
                     unsafe_allow_html=True,
                 )

elif mr_enabled:
    # ── Standalone Morning Report Card (no safe mode) — same style as safe mode ──
    _mr_g    = morning_report.get("grade", "?")
    _mr_mult = morning_report.get("multiplier", 1.0)
    _mr_css  = morning_report.get("color", "white")
    _mr_desc = morning_report.get("description", "")
    _mr_sc   = morning_report.get("score", "?")
    _mr_orig = daily_limit_r
    _mr_eff  = effective_daily_limit_r
    _limit_line = (
        f"{_mr_orig}R × {_mr_mult*100:.0f}% = {_mr_eff:.2f}R"
        if _mr_mult < 1.0 else f"{_mr_orig}R — full limit"
    )
    _LBL_NS = "font-size:0.58rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;text-align:center;width:100%"
    _CARD_NS = ("background:#0e1220;border:1px solid #1a2238;border-top:3px solid {bc};"
                "border-radius:10px;padding:16px 18px;"
                "display:flex;flex-direction:column;align-items:center;text-align:center;gap:5px")

    st.markdown("""
    <div class="sec-hdr">
      <div class="sec-line"></div>
      <div class="sec-title">Phase 1</div>
      <div class="sec-line"></div>
    </div>
    <div style="text-align:center;font-size:0.7rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin:-12px 0 16px">Human Performance Check-In</div>""", unsafe_allow_html=True)

    if morning_report.get("completed"):
        _p1ns_html = (
            f'<div style="{_CARD_NS.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge-done" style="margin:0 auto">✓ Morning Report Card Complete</div>'
            f'<div style="{_LBL_NS}">Grade</div>'
            f'<div style="font-size:2.2rem;font-weight:800;line-height:1" class="{_mr_css}">{_mr_g}</div>'
            f'<div style="font-size:0.75rem;color:#94a3b8">{_mr_desc}</div>'
            f'<div style="{_LBL_NS};margin-top:4px">Score <span style="color:#e2e8f0;font-weight:700">{_mr_sc}/18</span>'
            f' · {_limit_line}</div>'
            f'</div>'
        )
        st.markdown(_p1ns_html, unsafe_allow_html=True)
        if _mr_g == "F":
            st.markdown(
                '<div class="mr-no-trade">MORNING GRADE: F — DO NOT TRADE TODAY · '
                'Protect capital. Come back tomorrow.</div>', unsafe_allow_html=True)
        st.markdown('<div style="margin-top:12px"></div>', unsafe_allow_html=True)
        _ns_pad1, _ns_btn, _ns_pad2 = st.columns([2, 1, 2])
        with _ns_btn:
            if st.button("↩ Redo Check-In", key="redo_mr_ns", width="stretch"):
                session["morning_report"] = {**_SESSION_DEFAULTS["morning_report"]}
                _save_session(session)
                st.rerun()
    else:
        st.markdown(
            f'<div style="{_CARD_NS.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge" style="margin:0 auto">Phase 1 — Human Performance</div>'
            f'<div style="font-size:0.88rem;color:#4b5a7a;margin-top:8px">○ Answer 6 questions to set your daily grade</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        with st.form("morning_report_form"):
            qc1, qc2 = st.columns(2)
            for i, (qkey, qlabel, qopts, _) in enumerate(_MR_QUESTIONS):
                with (qc1 if i < 3 else qc2):
                    st.selectbox(qlabel, qopts, key=f"mr_q_{qkey}")
            if st.form_submit_button("Submit Phase 1 →", width="stretch"):
                _total = 0
                for qkey, _, qopts, qscores in _MR_QUESTIONS:
                    sel = st.session_state.get(f"mr_q_{qkey}", qopts[0])
                    idx = qopts.index(sel) if sel in qopts else 0
                    _total += qscores[idx]
                _grade, _mult, _css, _desc = _mr_grade(_total)
                session["morning_report"] = {
                    "completed": True, "grade": _grade, "multiplier": _mult,
                    "score": _total, "description": _desc, "color": _css,
                }
                _save_session(session)
                st.rerun()

# ─── TOP BAR: mode badge + WS status ────────────────────────────────────────
_top_l, _top_r = st.columns([3, 1])
with _top_l:
    if mode == "Secret Sauce":
        badge_label = f"SECRET SAUCE — {fabio_submode.upper()}"
        st.markdown(f'<div style="margin-bottom:12px"><span class="fabio-badge">{badge_label}</span></div>',
                    unsafe_allow_html=True)
with _top_r:
    if _HAS_WEBSOCKET:
        with _WS_LOCK:
            _ws_conn     = _WS_STATE["connected"]
            _ws_conn_ing = _WS_STATE["connecting"]
            _ws_err      = _WS_STATE["last_error"]
            _ws_last_ev  = _WS_STATE["last_event"]
        _ws_auth_fail = _WS_STATE.get("auth_failed", False)
        if _ws_conn:
            _ws_dot, _ws_lbl, _ws_col = "●", "LIVE", "#22c55e"
        elif _ws_auth_fail:
            _ws_dot, _ws_lbl, _ws_col = "✕", "AUTH FAILED", "#ef4444"
        elif _ws_conn_ing:
            _ws_dot, _ws_lbl, _ws_col = "◌", "CONNECTING", "#f59e0b"
        else:
            _ws_dot, _ws_lbl, _ws_col = "○", "OFFLINE", "#ef4444"
        _ev_html  = f'<br><span style="font-size:0.58rem;color:#4b5a7a">{_ws_last_ev}</span>' if _ws_last_ev else ""
        _err_html = f'<br><span style="font-size:0.58rem;color:#ef4444">{_ws_err[:60]}</span>' if _ws_err and not _ws_conn else ""
        st.markdown(
            f'<div style="text-align:right;margin-bottom:12px">'
            f'<span style="font-size:0.65rem;font-weight:700;letter-spacing:.1em;'
            f'text-transform:uppercase;color:{_ws_col}">{_ws_dot} {_ws_lbl}</span>'
            f'{_ev_html}{_err_html}</div>',
            unsafe_allow_html=True,
        )

# Auto-close toast
_auto_outcome = st.session_state.pop("_ws_auto_closed", None)
if _auto_outcome:
    _outcome_icon = {"Win": "🟢", "Loss": "🔴", "BE": "⚪"}.get(_auto_outcome, "")
    st.success(f"{_outcome_icon} Trade auto-closed by OKX: **{_auto_outcome}** — logged automatically.")

# ─── SESSION STATS (pre-computed for use in multiple places) ─────────────────
_wins    = [t for t in completed if t.get("actual_r", 0) > 0]
_losses  = [t for t in completed if t.get("actual_r", 0) < 0]
_win_rate = len(_wins) / len(completed) * 100 if completed else 0
_avg_win  = sum(t["actual_r"] for t in _wins)  / len(_wins)  if _wins  else 0
_avg_loss = sum(t["actual_r"] for t in _losses) / len(_losses) if _losses else 0
_pnl_usd  = session_pnl_r * one_r

# ─── HERO CARDS ──────────────────────────────────────────────────────────────
remaining_color = "green" if remaining_r > effective_daily_limit_r * 0.5 else \
                  ("yellow" if remaining_r > 0 else "red")
pnl_color = "green" if session_pnl_r >= 0 else "red"
pnl_sign  = "+" if session_pnl_r >= 0 else ""
bar_color = "#ef4444" if limit_hit else ("#f59e0b" if pnl_pct >= 0.75 else "#22c55e")
bar_pct   = int(pnl_pct * 100)
_rem_r_fmt = f"{remaining_r:.0f}" if remaining_r == int(remaining_r) else f"{remaining_r:.2f}"

st.markdown(f"""
<div class="hero-grid">
  <div class="hero-card">
    <div class="hero-lbl">Remaining R for the day</div>
    <div class="hero-val {remaining_color}">{_rem_r_fmt}R</div>
    <div class="hero-sub">Limit: {effective_daily_limit_r:.2f}R ({_fmt(0 if losses_r == 0 else -losses_r)} used){' · Grade ' + mr_grade_val if mr_grade_val and mr_multiplier < 1.0 else ''}</div>
    <div class="limit-bar-bg">
      <div class="limit-bar-fill" style="width:{bar_pct}%;background:{bar_color}"></div>
    </div>
  </div>
  <div class="hero-card">
    <div class="hero-lbl">Today's R</div>
    <div class="hero-val {pnl_color}">{pnl_sign}{session_pnl_r:.2f}R</div>
    <div class="hero-sub">{len(completed)} trade{'s' if len(completed)!=1 else ''} · 1R = ${one_r:,.2f}</div>
  </div>
  <div style="min-width:120px;background:#0e1220;border:1px solid #1a2238;border-top:2px solid #1e2a45;border-radius:8px;padding:16px 14px;flex:0.5;display:flex;flex-direction:column;align-items:center;justify-content:center">
    <div style="font-size:1.5rem;font-weight:700;line-height:1.1;color:#e2e8f0">{len([t for t in completed if t.get('actual_r',0)>0])}</div>
    <div style="font-size:0.63rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin-top:7px">Wins</div>
  </div>
  <div style="min-width:120px;background:#0e1220;border:1px solid #1a2238;border-top:2px solid #1e2a45;border-radius:8px;padding:16px 14px;flex:0.5;display:flex;flex-direction:column;align-items:center;justify-content:center">
    <div style="font-size:1.5rem;font-weight:700;line-height:1.1;color:#e2e8f0">{len([t for t in completed if t.get('actual_r',0)<0])}</div>
    <div style="font-size:0.63rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin-top:7px">Losses</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ─── MINI STATS STRIP ────────────────────────────────────────────────────────
if completed:
    _wr_c   = "green" if _win_rate >= 50 else "red"
    _aw_c   = "green" if _avg_win > 0 else "grey"
    _al_c   = "red"   if _avg_loss < 0 else "grey"
    _pusd_c = "green" if _pnl_usd >= 0 else "red"
    _pusd_s = "+" if _pnl_usd >= 0 else ""
    st.markdown(f"""
    <div class="mini-grid">
      <div class="mini-card">
        <div class="mini-val {_wr_c}">{_win_rate:.0f}%</div>
        <div class="mini-lbl">Win Rate ({len(_wins)}W/{len(_losses)}L)</div>
      </div>
      <div class="mini-card">
        <div class="mini-val {_aw_c}">+{_avg_win:.2f}R</div>
        <div class="mini-lbl">Avg Win</div>
      </div>
      <div class="mini-card">
        <div class="mini-val {_al_c}">{_avg_loss:.2f}R</div>
        <div class="mini-lbl">Avg Loss</div>
      </div>
      <div class="mini-card">
        <div class="mini-val {_pusd_c}">{_pusd_s}${abs(_pnl_usd):,.2f}</div>
        <div class="mini-lbl">Session P&L</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

# ─── SECRET SAUCE PANEL ───────────────────────────────────────────────────────
# Auto-detect if competition mode should be unlocked:
# 2 trades in session with actual_r >= implied_r * 3 (i.e. R:R >= 3 achieved)
if mode == "Secret Sauce":
    big_wins = [t for t in completed if t.get("actual_r", 0) >= t.get("implied_r", 0.25) * 3]
    if len(big_wins) >= 2 and fabio_submode == "Conservative Mode" \
            and fabio_state.get("phase", 0) == 0:
        # Auto-switch to competition mode
        profit_r  = session_pnl_r
        reserve   = round(profit_r / 3, 4)
        fabio_state["phase"]        = 1
        fabio_state["reserve_r"]    = reserve
        fabio_state["unit1_status"] = None
        fabio_state["unit2_status"] = None
        session["fabio_state"] = fabio_state
        prefs["fabio_submode"] = "Competition Mode"
        _save_prefs(prefs)
        _save_session(session)
        fabio_submode = "Competition Mode"
        st.rerun()

    st.markdown("""
    <div class="sec-hdr">
      <div class="sec-line"></div><div class="sec-title">Secret Sauce</div><div class="sec-line"></div>
    </div>""", unsafe_allow_html=True)

    if fabio_submode == "Conservative Mode":
        # Max 5 AA trades per day = 1.25% daily exposure
        max_trades  = 5
        done_trades = len(completed)
        daily_exp   = sum(t.get("implied_r", 0) * one_r / balance * 100 for t in completed)
        max_exp     = 1.25
        trades_left = max(0, max_trades - done_trades)
        exp_color   = "green" if daily_exp < 0.75 else ("yellow" if daily_exp < 1.0 else "red")

        big_wins_count = len(big_wins)
        bw_color = "green" if big_wins_count >= 2 else ("yellow" if big_wins_count == 1 else "white")
        st.markdown(f"""
        <div class="fabio-card">
          <div class="fabio-badge">Conservative Mode</div>
          <div style="font-size:0.82rem;color:#94a3b8;margin-bottom:12px">
            0.25% per AA · 0.35% per AAA · Max 5 trades/day · Target: 1.25% daily ·
            Auto-switches to Competition after 2 × ≥3R wins
          </div>
          <div class="kpi-grid">
            <div class="kpi-card">
              <div class="kpi-val {exp_color}">{daily_exp:.3f}%</div>
              <div class="kpi-lbl">Daily Exposure Used</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-val white">{max_exp - daily_exp:.3f}%</div>
              <div class="kpi-lbl">Exposure Remaining</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-val {'red' if trades_left==0 else 'white'}">{trades_left}</div>
              <div class="kpi-lbl">Trades Remaining</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-val {bw_color}">{big_wins_count} / 2</div>
              <div class="kpi-lbl">≥3R Wins (unlock)</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    else:  # Competition Mode
        phase = fabio_state.get("phase", 0)
        cons_wins = fabio_state.get("consecutive_wins", 0)
        reserve_r = fabio_state.get("reserve_r", 0.0)
        u1 = fabio_state.get("unit1_status")
        u2 = fabio_state.get("unit2_status")

        phase_labels = ["Normal\n(0.25%/trade)", "2 Wins!\nScale?", "Unit 1\nActive", "Unit 2\nActive"]
        phase_html = ""
        for i, lbl in enumerate(phase_labels):
            cls = "done" if i < phase else ("active" if i == phase else "")
            phase_html += f'<div class="phase-step {cls}">{lbl}</div>'

        u1_color = "green" if u1=="tp" else ("red" if u1=="sl" else ("yellow" if u1=="be" else "grey"))
        u2_color = "green" if u2=="tp" else ("red" if u2=="sl" else ("yellow" if u2=="be" else "grey"))
        u2_locked = (phase < 2 or (phase == 2 and u1 not in ("tp", "be")))

        st.markdown(f"""
        <div class="fabio-card">
          <div class="fabio-badge">Competition Mode</div>
          <div style="font-size:0.82rem;color:#94a3b8;margin-bottom:10px">
            Start 0.25%/trade · After 2 wins: reserve 1/3 · Scale 2×0.5% units ·
            Unit 2 unlocks only after Unit 1 TP or BE
          </div>
          <div class="phase-row">{phase_html}</div>
          <div class="kpi-grid" style="margin-top:12px">
            <div class="kpi-card">
              <div class="kpi-val white">{cons_wins}</div>
              <div class="kpi-lbl">Consecutive Wins</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-val green">{reserve_r:.3f}R</div>
              <div class="kpi-lbl">Reserved (safe)</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-val {u1_color}">{u1.upper() if u1 else '—'}</div>
              <div class="kpi-lbl">Unit 1 Status</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-val {'grey' if u2_locked else u2_color}">
                {'LOCKED' if u2_locked else (u2.upper() if u2 else '—')}
              </div>
              <div class="kpi-lbl">Unit 2 Status</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Scaling unlock button
        if phase == 0 and cons_wins >= 2:
            st.markdown('<div class="info-banner">2 consecutive wins detected on a trending day. Activate scaling?</div>',
                        unsafe_allow_html=True)
            col_act, col_skip = st.columns(2)
            with col_act:
                if st.button("Activate Scaling", width="stretch"):
                    profit_r  = session_pnl_r
                    reserve   = round(profit_r / 3, 4)
                    remaining_for_units = profit_r - reserve
                    fabio_state["phase"]       = 1
                    fabio_state["reserve_r"]   = reserve
                    fabio_state["unit1_status"] = None
                    fabio_state["unit2_status"] = None
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with col_skip:
                if st.button("Keep Normal", width="stretch", type="secondary"):
                    fabio_state["consecutive_wins"] = 0
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()

        # Unit outcome buttons (competition phase 1+)
        if phase >= 1 and u1 is None:
            st.markdown("**Unit 1 outcome:**")
            cu1, cu2, cu3 = st.columns(3)
            with cu1:
                if st.button("Unit 1 TP", width="stretch"):
                    fabio_state["unit1_status"] = "tp"
                    fabio_state["phase"] = 2
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with cu2:
                if st.button("Unit 1 BE", width="stretch", type="secondary"):
                    fabio_state["unit1_status"] = "be"
                    fabio_state["phase"] = 2
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with cu3:
                if st.button("Unit 1 SL", width="stretch", type="secondary"):
                    fabio_state["unit1_status"] = "sl"
                    fabio_state["phase"] = 0  # back to defensive
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()

        if phase >= 2 and not u2_locked and u2 is None:
            st.markdown("**Unit 2 outcome:**")
            cu1, cu2, cu3 = st.columns(3)
            with cu1:
                if st.button("Unit 2 TP", width="stretch"):
                    fabio_state["unit2_status"] = "tp"
                    fabio_state["phase"] = 3
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with cu2:
                if st.button("Unit 2 BE", width="stretch", type="secondary"):
                    fabio_state["unit2_status"] = "be"
                    fabio_state["phase"] = 3
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with cu3:
                if st.button("Unit 2 SL", width="stretch", type="secondary"):
                    fabio_state["unit2_status"] = "sl"
                    fabio_state["phase"] = 3
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()

# ─── TODAY'S CONTEXT SUMMARY (above active trading) ───────────────────────────
_ctx_assets = checklist.get("assets", []) if safe_mode else []
_ctx_confirmed = [a for a in _ctx_assets if a.get("phase2_complete")]
if _ctx_confirmed:
    _ctx_cards_html = '<div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin:0 0 20px">'
    for _ca in _ctx_confirmed:
        _ca_lbl = _ca.get("phase2", {}).get("context_label", "?")
        _ca_cnf = _ca.get("phase2", {}).get("confidence", 0)
        _ca_col = _context_color(_ca_lbl)
        _ctx_cards_html += (
            f'<div style="background:#0a0e18;border:2px solid {_ca_col};border-radius:10px;'
            f'padding:14px 24px;text-align:center;min-width:140px;flex:1;max-width:220px">'
            f'<div style="font-size:0.6rem;color:#4b5a7a;text-transform:uppercase;'
            f'letter-spacing:.1em;font-weight:600;margin-bottom:4px">{_ca["name"]}</div>'
            f'<div style="font-size:1.1rem;font-weight:900;color:{_ca_col};line-height:1.2">{_ca_lbl}</div>'
            f'<div style="font-size:0.7rem;color:#94a3b8;margin-top:4px">{_ca_cnf}%</div>'
            f'</div>'
        )
    _ctx_cards_html += '</div>'
    st.markdown(_ctx_cards_html, unsafe_allow_html=True)

# ─── IB TIMER ────────────────────────────────────────────────────────────────
_ib = _ib_timer_info()
if _ib["status"] != "market_closed":
    _ib_pulse = ' ib-pulse' if _ib["status"] == "ib_active" else ''
    _ib_bar_html = ""
    if _ib["status"] == "ib_active":
        _ib_pct = int(_ib["pct"] * 100)
        _ib_bar_html = (
            f'<div class="ib-bar-bg">'
            f'<div class="ib-bar-fill" style="width:{_ib_pct}%;background:{_ib["color"]}"></div>'
            f'</div>'
        )
    _ib_et_now = _now_et_minutes()
    _ib_h, _ib_m = divmod(_ib_et_now, 60)
    st.markdown(
        f'<div class="ib-timer">'
        f'<div class="ib-timer-dot{_ib_pulse}" style="background:{_ib["color"]}"></div>'
        f'<div class="ib-timer-label" style="color:{_ib["color"]}">{_ib["label"]}</div>'
        f'{_ib_bar_html}'
        f'<div style="font-size:0.65rem;color:#4b5a7a">{_ib_h:02d}:{_ib_m:02d} ET</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ─── ACTIVE TRADE ─────────────────────────────────────────────────────────────
st.markdown("""
<div class="sec-hdr">
  <div class="sec-line"></div><div class="sec-title">Active Trading</div><div class="sec-line"></div>
</div>""", unsafe_allow_html=True)

# EV bell curve helper — defined once, reused per trade
import math as _math
def _npdf(v): return _math.exp(-0.5 * v * v) / _math.sqrt(2 * _math.pi)
_peak_pdf = _npdf(0)
_bx = [i * 0.02 - 3.5 for i in range(351)]
_by = [_npdf(v) for v in _bx]
def _seg(lo, hi):
    xs = [lo] + [v for v in _bx if lo < v < hi] + [hi]
    return xs, [_npdf(v) for v in xs]

def _draw_ev_chart(win_p, rr_target, grade_lbl):
    if not _HAS_PLOTLY:
        return
    _ev  = win_p * rr_target - (1 - win_p) * 1.0
    _z   = max(-3.2, min(3.2, _ev / 1.5))
    _evc = "#22c55e" if _ev > 0.3 else ("#f59e0b" if _ev > -0.2 else "#ef4444")
    _evs = "+" if _ev > 0 else ""
    _band_cfg = [(-3.5,-3.0,'rgba(34,100,34,0.70)'),(-3.0,-2.0,'rgba(34,100,34,0.70)'),
                 (-2.0,-1.0,'rgba(140,75,75,0.70)'),(-1.0,0.0,'rgba(45,79,181,0.80)'),
                 (0.0,1.0,'rgba(45,79,181,0.80)'),(1.0,2.0,'rgba(140,75,75,0.70)'),
                 (2.0,3.0,'rgba(34,100,34,0.70)'),(3.0,3.5,'rgba(34,100,34,0.70)')]
    fig = _go.Figure()
    for _lo, _hi, _fc in _band_cfg:
        _sx, _sy = _seg(_lo, _hi)
        fig.add_trace(_go.Scatter(x=_sx+_sx[::-1], y=_sy+[0]*len(_sy),
            fill='toself', fillcolor=_fc, line=dict(width=0), showlegend=False, hoverinfo='skip'))
    fig.add_trace(_go.Scatter(x=_bx, y=_by,
        line=dict(color='rgba(226,232,240,0.6)', width=2), showlegend=False, hoverinfo='skip'))
    for _sv in [-3,-2,-1,0,1,2,3]:
        fig.add_vline(x=_sv, line_dash='solid', line_color='rgba(255,255,255,0.12)', line_width=1)
    fig.add_vline(x=_z, line_dash='solid', line_color='#ffffff', line_width=2.5)
    _annots = [dict(x=_px, y=_npdf(_px)*0.55, text=_pt, showarrow=False,
                    font=dict(color='rgba(255,255,255,0.85)', size=9), xanchor='center')
               for _px, _pt in [(-3.25,'0.1%'),(-2.5,'2.1%'),(-1.5,'13.6%'),(-0.5,'34.1%'),
                                  (0.5,'34.1%'),(1.5,'13.6%'),(2.5,'2.1%'),(3.25,'0.1%')]]
    _annots.append(dict(x=_z, y=_peak_pdf*1.30,
        text=f'<b>{grade_lbl}</b><br>Avg: {_evs}{_ev:.2f}R/trade',
        showarrow=True, arrowhead=2, arrowcolor='#ffffff', ax=0, ay=-32,
        font=dict(color=_evc, size=10), xanchor='center',
        bgcolor='rgba(14,18,32,0.90)', bordercolor=_evc, borderwidth=1, borderpad=4))
    _layout = dict(height=220, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                   margin=dict(l=4,r=4,t=10,b=38), annotations=_annots)
    _layout["xaxis"] = dict(range=[-3.6,3.6], tickvals=[-3,-2,-1,0,1,2,3],
        ticktext=['-3σ','-2σ','-1σ','μ=0','+1σ','+2σ','+3σ'],
        tickfont=dict(color='#94a3b8',size=11,family='monospace'),
        showgrid=False, zeroline=False, color='#94a3b8', tickangle=0)
    _layout["yaxis"] = dict(visible=False, range=[-0.008, _peak_pdf*1.65])
    fig.update_layout(**_layout)
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

# ── Active trade cards — side by side ──
_op = st.session_state.get("_outcome_pending")  # dict {idx, type} or None

if active_trades:
    _ncols = min(len(active_trades), 3)
    _tcols = st.columns(_ncols)
    for _ti, _trd in enumerate(active_trades):
        with _tcols[_ti % _ncols]:
            _ao       = _trd.get("add_ons", [])
            _t_risk   = _trd["implied_r"] + sum(a["r"] for a in _ao)
            _t_rr     = _trd["rr_target"]
            _t_grade  = _trd.get("grade", "AA")
            _t_winp   = _win_prob(_t_grade)
            _t_rsc    = _risk_score(_t_rr, len(_ao), _t_grade)
            _t_rc     = "#22c55e" if _t_rsc < 40 else ("#f59e0b" if _t_rsc < 70 else "#ef4444")
            _t_rl     = "LOW" if _t_rsc < 40 else ("MOD" if _t_rsc < 70 else "HIGH")
            _t_badges = "".join(f'<span class="addon-badge">+{a["r"]}R</span>' for a in _ao) or ""
            _t_inst   = _trd.get("instrument", "")
            st.markdown(f"""
            <div class="active-card" style="margin-bottom:6px">
              <div class="active-badge">IN TRADE</div>
              <div class="trade-detail-row">
                {'<div><div class="td-lbl">Instr.</div><div class="td-val orange">'+_t_inst+'</div></div>' if _t_inst else ""}
                <div><div class="td-lbl">Grade</div><div class="td-val orange">{_t_grade}</div></div>
                <div><div class="td-lbl">R:R</div><div class="td-val white">1:{_t_rr}</div></div>
                <div><div class="td-lbl">At Risk</div><div class="td-val {'red' if _t_risk > _trd['implied_r'] else 'white'}">{_t_risk:.2f}R</div></div>
                <div><div class="td-lbl">Win %</div><div class="td-val white">{_t_winp*100:.0f}%</div></div>
                <div><div class="td-lbl">Risk</div><div style="font-size:0.78rem;font-weight:700;color:{_t_rc}">{_t_rl} {_t_rsc:.0f}</div></div>
              </div>
              {('<div style="margin-top:8px">'+_t_badges+'</div>') if _t_badges else ""}
              {f'<div style="font-size:0.75rem;color:#4b5a7a;margin-top:4px">{_trd["note"]}</div>' if _trd.get("note") else ""}
            </div>
            """, unsafe_allow_html=True)

            # If this trade has a pending action, show notice instead of buttons
            _this_pending = _op and isinstance(_op, dict) and _op.get("idx") == _ti
            if _this_pending:
                _pend_labels = {"win": "✅ WIN", "loss": "❌ LOSS", "be": "⚪ BE",
                                "addon": "➕ ADD-ON", "edit": "✏️ EDIT"}
                _pl = _pend_labels.get(_op.get("type",""), "ACTION")
                st.markdown(
                    f'<div class="pending-card-notice">⚠ {_pl} — confirm in the form below ↓</div>',
                    unsafe_allow_html=True,
                )
                if st.button("✕ Cancel", key=f"cancel_pend_{_ti}", type="secondary", width="stretch"):
                    del st.session_state["_outcome_pending"]
                    st.rerun()
            else:
                # Normal action buttons
                _btn_cols = st.columns([2,2,2,2,2,1])
                with _btn_cols[0]:
                    if st.button("WIN",  key=f"win_{_ti}",  width="stretch", type="primary"):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "win"}
                        st.rerun()
                with _btn_cols[1]:
                    if st.button("LOSS", key=f"loss_{_ti}", width="stretch"):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "loss"}
                        st.rerun()
                with _btn_cols[2]:
                    if st.button("BE",   key=f"be_{_ti}",   width="stretch"):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "be"}
                        st.rerun()
                with _btn_cols[3]:
                    if st.button(f"+{prefs['addon_r']}R", key=f"add_{_ti}", width="stretch"):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "addon"}
                        st.rerun()
                with _btn_cols[4]:
                    if st.button("Edit", key=f"edit_{_ti}", width="stretch"):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "edit"}
                        st.rerun()
                with _btn_cols[5]:
                    if st.button("✕", key=f"cancel_{_ti}", width="stretch", help="Cancel trade"):
                        active_trades.pop(_ti)
                        session["active_trades"] = active_trades
                        _save_session(session)
                        st.rerun()

            # EV chart — show when no pending action, single trade only
            if not _op and len(active_trades) == 1:
                _draw_ev_chart(_t_winp, _t_rr, f"{_t_grade} — {_t_inst or 'trade'}")

# ── Outcome forms — prominent banner + form ──
if _op and isinstance(_op, dict):
    _op_idx  = _op.get("idx", 0)
    _op_type = _op.get("type", "")
    if _op_idx < len(active_trades):
        _act = active_trades[_op_idx]
        _act_inst   = _act.get("instrument", "Trade")
        _act_rr     = _act["rr_target"]
        _act_risk_r = _act["implied_r"] + sum(a["r"] for a in _act.get("add_ons", []))

        # Prominent confirm banner
        _banner_map = {
            "win":   ("confirm-banner confirm-win",  "✅  CONFIRM WIN"),
            "loss":  ("confirm-banner confirm-loss", "❌  CONFIRM LOSS"),
            "be":    ("confirm-banner confirm-be",   "⚪  CONFIRM BREAK EVEN"),
            "edit":  ("confirm-banner confirm-edit", "✏️  EDIT TRADE"),
            "addon": ("confirm-banner confirm-addon","➕  ADD ON"),
        }
        _bcls, _blbl = _banner_map.get(_op_type, ("confirm-banner confirm-edit", "ACTION REQUIRED"))
        st.markdown(
            f'<div class="{_bcls}">{_blbl} — {_act_inst}</div>',
            unsafe_allow_html=True,
        )

        if _op_type == "edit":
            with st.form(f"edit_form_{_op_idx}"):
                st.markdown(f"**Edit — {_act_inst}**")
                ec1, ec2, ec3 = st.columns([2, 2, 3])
                with ec1:
                    _go_e = list(prefs["grades"].keys())
                    _gl_e = [f"{k} ({prefs['grades'][k]['implied_r']}R)" for k in _go_e]
                    _gi_e = _go_e.index(_act["grade"]) if _act["grade"] in _go_e else 0
                    _ngl  = st.selectbox("Grade", _gl_e, index=_gi_e)
                    _ng   = _go_e[_gl_e.index(_ngl)]
                with ec2:
                    _nrr = st.number_input("R:R Target", min_value=0.1, value=float(_act_rr), step=0.1, format="%.2f")
                with ec3:
                    _nt  = st.text_input("Note", value=_act.get("note", ""))
                if st.form_submit_button("Save", width="stretch"):
                    active_trades[_op_idx]["grade"]     = _ng
                    active_trades[_op_idx]["implied_r"] = prefs["grades"][_ng]["implied_r"]
                    active_trades[_op_idx]["rr_target"] = _nrr
                    active_trades[_op_idx]["note"]      = _nt
                    session["active_trades"] = active_trades
                    del st.session_state["_outcome_pending"]
                    _save_session(session)
                    st.rerun()

        elif _op_type == "win":
            with st.form(f"win_form_{_op_idx}"):
                st.markdown(f"**Enter R:R achieved for {_act_inst}:**")
                wc1, wc2 = st.columns([3, 2])
                with wc1:
                    rr_achieved = st.number_input("R:R achieved", min_value=0.0,
                                                  value=float(_act_rr), step=0.1, format="%.2f")
                with wc2:
                    actual_r = round(rr_achieved * _act["implied_r"], 4)
                    st.markdown(f"**Actual R: `+{actual_r:.4f}R`**")
                st.markdown("---")
                _win_csv_ex = st.selectbox("CSV exchange format (optional)",
                                           ["Auto-detect"] + _EXCHANGES, key=f"wcx_{_op_idx}")
                _win_csv    = st.file_uploader("Verify via CSV (optional)", type=["csv"], key=f"wcsv_{_op_idx}")
                if st.form_submit_button("Pull R from CSV", width="stretch") and _win_csv:
                    _wp, _wf, _ws, _we = _parse_csv_last_close(_win_csv.read(), _win_csv_ex)
                    st.error(_we) if _we else st.success(f"CSV ({_ws}): {round(_wp/one_r,4):+.4f}R") if _wp else None
                if st.form_submit_button("Confirm Win", width="stretch"):
                    _cr = _risk_score(_act["rr_target"], len(_act.get("add_ons",[])), _act.get("grade","AA"))
                    _trade = {**_act, "outcome": "Win", "close_time": datetime.now().strftime("%H:%M:%S"),
                              "risk_tool": rr_achieved, "actual_r": actual_r, "risk_score_close": _cr}
                    completed.append(_trade)
                    _archive_trade(_trade, session["session_date"])
                    if mode == "Secret Sauce" and fabio_submode == "Competition Mode":
                        fabio_state["consecutive_wins"] += 1
                        session["fabio_state"] = fabio_state
                    active_trades.pop(_op_idx)
                    session["completed_trades"] = completed
                    session["active_trades"]    = active_trades
                    del st.session_state["_outcome_pending"]
                    _save_session(session)
                    st.rerun()

        elif _op_type == "loss":
            _ex_cfg_l = _load_ex_cfg()
            _is_okx_l = _ex_cfg_l.get("exchange", "OKX") == "OKX"
            _pull_lbl = "Pull from API" if _is_okx_l else f"Pull from {_ex_cfg_l.get('exchange','API')}"
            with st.form(f"loss_form_{_op_idx}"):
                st.markdown(f"**Confirm R lost on {_act_inst}:**")
                lc1, lc2, lc3 = st.columns([2, 2, 2])
                with lc1:
                    manual_r = st.number_input("Actual R (negative)", max_value=0.0,
                                               value=float(round(-_act_risk_r, 4)), step=0.01, format="%.4f")
                with lc2:
                    pull_okx = st.form_submit_button(_pull_lbl, width="stretch")
                with lc3:
                    confirm  = st.form_submit_button("Confirm Loss", width="stretch")
                st.markdown("---")
                _csv_ex_h = st.selectbox("CSV exchange format", ["Auto-detect"] + _EXCHANGES, key=f"lcx_{_op_idx}")
                _csv_file = st.file_uploader("Or upload exchange CSV", type=["csv"], key=f"lcsv_{_op_idx}")
                _csv_pull = st.form_submit_button("Parse CSV", width="stretch")

                def _close_loss(ar, extra=None):
                    _cr2 = _risk_score(_act["rr_target"], len(_act.get("add_ons",[])), _act.get("grade","AA"))
                    _t2  = {**_act, "outcome": "Loss", "close_time": datetime.now().strftime("%H:%M:%S"),
                            "risk_tool": ar, "actual_r": ar, "risk_score_close": _cr2, **(extra or {})}
                    completed.append(_t2)
                    _archive_trade(_t2, session["session_date"])
                    if mode == "Secret Sauce" and fabio_submode == "Competition Mode":
                        fabio_state["consecutive_wins"] = 0
                        session["fabio_state"] = fabio_state
                    active_trades.pop(_op_idx)
                    session["completed_trades"] = completed
                    session["active_trades"]    = active_trades
                    del st.session_state["_outcome_pending"]
                    _save_session(session)
                    st.rerun()

                if pull_okx:
                    if not prefs.get("connection_enabled"):
                        st.error("Enable connections first — kill switch is OFF")
                    else:
                        _np, _nf, _ne = _fetch_last_close()
                        if _ne:
                            st.error(f"OKX pull failed: {_ne}")
                        elif _np is not None:
                            _ar_okx = round(_np / one_r, 4)
                            st.success(f"OKX: ${_np:,.4f} → {_ar_okx:.4f}R")
                            _close_loss(_ar_okx, {"okx_pnl": _np, "okx_fee": _nf})

                if _csv_pull:
                    if not _csv_file:
                        st.error("No CSV file uploaded.")
                    else:
                        _cp, _cf, _cs, _ce = _parse_csv_last_close(_csv_file.read(), _csv_ex_h)
                        if _ce:
                            st.error(f"CSV parse failed: {_ce}")
                        elif _cp is not None:
                            _ar_c = round(_cp / one_r, 4)
                            st.success(f"CSV ({_cs}): {_ar_c:.4f}R")
                            _close_loss(_ar_c, {"csv_pnl": _cp, "csv_fee": _cf})

                if confirm:
                    _close_loss(manual_r)

        elif _op_type == "be":
            with st.form(f"be_form_{_op_idx}"):
                st.markdown(f"**Log {_act_inst} at 0R?**")
                if st.form_submit_button("Confirm Break Even", width="stretch"):
                    _cr = _risk_score(_act["rr_target"], len(_act.get("add_ons",[])), _act.get("grade","AA"))
                    _t3 = {**_act, "outcome": "BE", "close_time": datetime.now().strftime("%H:%M:%S"),
                           "risk_tool": 0.0, "actual_r": 0.0, "risk_score_close": _cr}
                    completed.append(_t3)
                    _archive_trade(_t3, session["session_date"])
                    active_trades.pop(_op_idx)
                    session["completed_trades"] = completed
                    session["active_trades"]    = active_trades
                    del st.session_state["_outcome_pending"]
                    _save_session(session)
                    st.rerun()

        elif _op_type == "addon":
            with st.form(f"addon_form_{_op_idx}"):
                st.markdown(f"**Add {prefs['addon_r']}R to {_act_inst}?**")
                new_rr = st.number_input("Update R:R target", min_value=0.1,
                                         value=float(_act_rr), step=0.1, format="%.1f")
                if st.form_submit_button("Confirm Add-on", width="stretch"):
                    active_trades[_op_idx]["add_ons"].append({
                        "r":    prefs["addon_r"],
                        "time": datetime.now().strftime("%H:%M:%S"),
                    })
                    active_trades[_op_idx]["rr_target"] = new_rr
                    session["active_trades"] = active_trades
                    del st.session_state["_outcome_pending"]
                    _save_session(session)
                    st.rerun()

# ── New trade form ──
if not limit_hit:
    if safe_mode and cl_gate < 5:
        st.markdown(
            '<div class="checklist-lock">🔒 Complete the pre-trading checklist above to unlock trading.</div>',
            unsafe_allow_html=True,
        )
    else:
        # ── News hard gate — block trade entry during high-impact events ──
        _news_blocked = False
        _ngate = checklist.get("news", {}) if safe_mode else {}
        if _ngate.get("has_news") and _ngate.get("event_impact") in ("EXTREME", "HIGH"):
            _ngate_et = _ngate.get("event_time", "")
            _ngate_local = _et_to_local(_ngate_et, prefs.get("user_tz_offset", 1))
            _news_status = _news_window_status(_ngate_local)
            if _news_status == "active":
                _news_blocked = True

        if _news_blocked:
            st.markdown(
                '<div style="background:#1a0000;border:2px solid #ef4444;border-radius:12px;'
                'padding:24px;text-align:center;margin:8px 0">'
                '<div style="font-size:1.5rem;font-weight:900;color:#ef4444">TRADE ENTRY BLOCKED</div>'
                '<div style="font-size:0.85rem;color:#94a3b8;margin-top:8px">'
                f'News event window active — {_ngate.get("event_name","?")} at {_ngate_local} local. '
                'Wait 30 min after event.</div></div>',
                unsafe_allow_html=True,
            )

        # Quick-launch buttons — must select before form unlocks
        _sel_grade = st.session_state.get("_quick_grade", "") if not _news_blocked else ""
        if not _news_blocked:
            # Grade gating: restrict grades based on derived context
            _ctx_structs = set()
            for _ga in checklist.get("assets", []):
                if _ga.get("phase2_complete"):
                    _ctx_structs.add(_ga.get("phase2", {}).get("structure", "MR"))
            _show_mr = not _ctx_structs or "MR" in _ctx_structs or "ID" in _ctx_structs
            _show_bo = not _ctx_structs or "TR" in _ctx_structs or "ID" in _ctx_structs

            qb1, qb2 = st.columns(2)
            with qb1:
                if _show_mr:
                    _mr_type = "primary" if _sel_grade == "AAA" else "secondary"
                    if st.button("MEAN REVERSION", width="stretch", type=_mr_type,
                                 help="AAA setup — " + str(prefs["grades"]["AAA"]["implied_r"]) + "R implied risk"):
                        st.session_state["_quick_grade"] = "AAA"
                        st.rerun()
                else:
                    st.markdown(
                        '<div style="padding:8px;text-align:center;color:#4b5a7a;font-size:0.75rem">'
                        'MR not available — all assets trending</div>',
                        unsafe_allow_html=True,
                    )
            with qb2:
                if _show_bo:
                    _bo_type = "primary" if _sel_grade == "AA" else "secondary"
                    if st.button("BREAKOUT", width="stretch", type=_bo_type,
                                 help="AA setup — " + str(prefs["grades"]["AA"]["implied_r"]) + "R implied risk"):
                        st.session_state["_quick_grade"] = "AA"
                        st.rerun()
                else:
                    st.markdown(
                        '<div style="padding:8px;text-align:center;color:#4b5a7a;font-size:0.75rem">'
                        'BO not available — all assets mean reverting</div>',
                        unsafe_allow_html=True,
                    )

        # Grade gate — must select MEAN REVERSION or BREAKOUT first
        _pf = st.session_state.get("_ws_prefill") or {}

        if not _sel_grade:
            st.markdown(
                '<div class="grade-gate-msg">👆 Select MEAN REVERSION or BREAKOUT above to unlock trade entry</div>',
                unsafe_allow_html=True,
            )
        else:
            if _pf:
                st.markdown(
                    f'<div class="info-banner">⚡ OKX fill detected — {_pf.get("instrument","?")} entry @ {_pf.get("entry_px","?")} · '
                    f'SL {_pf.get("sl_px","?")} · TP {_pf.get("tp_px","?")} · '
                    f'{"R:R " + str(_pf.get("rr")) if _pf.get("rr") else "No TP — enter R:R manually"}'
                    f'<br>Select grade + confirm to log.</div>',
                    unsafe_allow_html=True,
                )

            with st.form("new_trade_form", clear_on_submit=True):
                _default_rr   = float(_pf.get("rr") or 3.0)
                fr1, fr2, fr3 = st.columns([1, 2, 3])
                with fr1:
                    rr_target = st.number_input("R:R", min_value=0.1, max_value=50.0,
                                                value=_default_rr, step=0.1, format="%.1f")
                with fr2:
                    _go2     = list(prefs["grades"].keys())
                    _gl2     = [f"{k} ({prefs['grades'][k]['implied_r']}R)" for k in _go2]
                    _dg2     = st.session_state.get("_quick_grade", "AA")
                    _di2     = _go2.index(_dg2) if _dg2 in _go2 else 0
                    _gsel    = st.selectbox("Grade", _gl2, index=_di2)
                    chosen_grade = _go2[_gl2.index(_gsel)]
                with fr3:
                    note = st.text_input("Note", placeholder="Setup / reason for entry")

                _instruments = ["SOL", "BTC", "ETH", "SUI", "MNQ", "MES"]
                _futures     = {"MNQ": 2.0, "MES": 5.0}
                _pf_ii = _instruments.index(_pf.get("instrument","SOL")) if _pf.get("instrument") in _instruments else 0
                fi1, fi2, fi3, fi4 = st.columns([1.5, 2, 2, 2])
                with fi1:
                    instrument = st.selectbox("Instrument", _instruments, index=_pf_ii)
                with fi2:
                    entry_price = st.number_input("Entry Price", min_value=0.0,
                                                  value=float(_pf.get("entry_px") or 0.0),
                                                  step=0.01, format="%.4f")
                with fi3:
                    stop_price  = st.number_input("Stop Price", min_value=0.0,
                                                  value=float(_pf.get("sl_px") or 0.0),
                                                  step=0.01, format="%.4f")
                with fi4:
                    _risk_usd = one_r * prefs["grades"][chosen_grade]["implied_r"]
                    _diff     = abs(entry_price - stop_price)
                    if entry_price > 0 and stop_price > 0 and _diff > 0:
                        _qty_lbl = (f"{_risk_usd/(_diff*_futures[instrument]):.3f} lots"
                                    if instrument in _futures
                                    else f"{_risk_usd/_diff:.4f}")
                        st.markdown(
                            f"<div style='margin-top:28px'>"
                            f"<div style='font-size:0.58rem;color:#4b5a7a;text-transform:uppercase;"
                            f"letter-spacing:.1em;font-weight:600;margin-bottom:4px'>Qty</div>"
                            f"<div style='font-size:1.1rem;font-weight:700;color:#f97316'>{_qty_lbl}</div>"
                            f"<div style='font-size:0.65rem;color:#4b5a7a'>Risk ${_risk_usd:,.2f}</div></div>",
                            unsafe_allow_html=True)
                    else:
                        st.markdown("<div style='margin-top:28px;font-size:0.75rem;color:#4b5a7a'>"
                                    "Enter entry &amp; stop for qty</div>", unsafe_allow_html=True)

                # ── Inline Phase 4 gate (safe mode only) ──
                _p4_ok = True
                if safe_mode:
                    _inst_now = st.session_state.get("Instrument", instrument)
                    _asset_match = next((a for a in cl_assets if a["name"] == _inst_now and
                                         a.get("phase2_complete") and a.get("phase3_complete")), None)
                    if _asset_match:
                        _p4_strat = _asset_match["phase2"].get("strategy", "Mean Reversion")
                        _p4_sc    = "#22c55e" if _p4_strat == "Mean Reversion" else "#f97316"
                        st.markdown(
                            f'<div style="padding:10px 0 6px;font-size:0.62rem;color:#4b5a7a;text-transform:uppercase;'
                            f'letter-spacing:.1em;font-weight:600">Phase 4 — Pre-Trade Gate · '
                            f'<span style="color:{_p4_sc}">{_p4_strat.upper()}</span></div>',
                            unsafe_allow_html=True,
                        )
                        _p4c1, _p4c2 = st.columns(2)
                        with _p4c1:
                            _p4_level = st.radio("At a valid Support / Resistance level?", ["Yes", "No — not yet"], key="p4_lvl")
                        with _p4c2:
                            _p4_fp    = st.radio("Footprint confirms?", ["Yes", "No — wait"], key="p4_fp")
                        if _p4_strat == "Breakout":
                            _p4_bo = st.radio("Breakout confirmed with order activity?",
                                              ["Yes", "No — false breakout", "N/A"], key="p4_bo")
                        else:
                            _p4_bo = "N/A"

                if st.form_submit_button("Enter Trade", width="stretch"):
                    # Validate Phase 4 if safe mode
                    _block = None
                    if safe_mode:
                        _inst_sub = st.session_state.get("p4_lvl", "Yes")
                        _fp_sub   = st.session_state.get("p4_fp", "Yes")
                        _bo_sub   = st.session_state.get("p4_bo", "N/A")
                        if _inst_sub.startswith("No"):
                            _block = "Not at a valid Support / Resistance level — wait for better location."
                        elif _fp_sub.startswith("No"):
                            _block = "Footprint doesn't confirm — wait."
                        elif _bo_sub == "No — false breakout":
                            _block = "False breakout — do not trade."
                    if _block:
                        st.error(_block)
                    else:
                        _implied_r   = prefs["grades"][chosen_grade]["implied_r"]
                        _entry_risk  = _risk_score(rr_target, 0, chosen_grade)
                        _trade_ctx = _get_asset_context(instrument, checklist.get("assets", []))
                        _new_trade   = {
                            "id":               len(completed) + len(active_trades) + 1,
                            "open_date":        str(date.today()),
                            "start_time":       datetime.now().strftime("%H:%M:%S"),
                            "grade":            chosen_grade,
                            "implied_r":        _implied_r,
                            "rr_target":        rr_target,
                            "risk_score_entry": _entry_risk,
                            "instrument":       instrument,
                            "entry_price":      entry_price if entry_price > 0 else None,
                            "stop_price":       stop_price  if stop_price  > 0 else None,
                            "add_ons":          [],
                            "note":             note,
                            "context":          _trade_ctx,
                        }
                        st.session_state.pop("_quick_grade", None)
                        st.session_state.pop("_ws_prefill", None)
                        active_trades.append(_new_trade)
                        session["active_trades"] = active_trades
                        _save_session(session)
                        st.rerun()

# ─── SYNCED EXCHANGE DATA ─────────────────────────────────────────────────────
_sync_orders = st.session_state.get("_sync_orders", [])
_sync_positions = st.session_state.get("_sync_positions", [])
_sync_ts = st.session_state.get("_sync_ts", "")

if _sync_positions:
    st.markdown(f"""
    <div class="sec-hdr">
      <div class="sec-line"></div><div class="sec-title">Open Positions</div><div class="sec-line"></div>
    </div>
    <div style="text-align:center;font-size:0.65rem;color:#4b5a7a;margin:-8px 0 10px">
    Last sync: {_sync_ts}</div>""", unsafe_allow_html=True)
    _pos_rows = []
    for _sp in _sync_positions:
        _inst = _sp["instId"].replace("-SWAP", "").replace("-", "/")
        _side_col = "#22c55e" if _sp["side"] == "long" else "#ef4444"
        _upl_col = "#22c55e" if _sp["upl"] >= 0 else "#ef4444"
        _pos_rows.append({
            "Instrument": _inst,
            "Side": _sp["side"].upper(),
            "Size": _sp["sz"],
            "Entry": _sp["avgPx"],
            "Leverage": f"{_sp['lever']}x",
            "Unrealized P&L": f"${_sp['upl']:+.2f}",
        })
    st.dataframe(_pos_rows, width="stretch", hide_index=True)

if _sync_orders:
    st.markdown(f"""
    <div class="sec-hdr">
      <div class="sec-line"></div><div class="sec-title">Today's Exchange Fills</div><div class="sec-line"></div>
    </div>
    <div style="text-align:center;font-size:0.65rem;color:#4b5a7a;margin:-8px 0 10px">
    {len(_sync_orders)} fills · Last sync: {_sync_ts}</div>""", unsafe_allow_html=True)
    _fill_rows = []
    for _so in sorted(_sync_orders, key=lambda x: x.get("fillTs", 0), reverse=True):
        _inst = _so["instId"].replace("-SWAP", "").replace("-", "/")
        _net = round(_so["pnl"] + _so["fee"], 4)
        _fill_rows.append({
            "Instrument": _inst,
            "Side": _so["side"].upper(),
            "Size": _so["sz"],
            "Avg Price": _so["avgPx"],
            "P&L": f"${_so['pnl']:+.4f}",
            "Fee": f"${_so['fee']:.4f}",
            "Net": f"${_net:+.4f}",
            "Type": _so["ordType"],
        })
    st.dataframe(_fill_rows, width="stretch", hide_index=True)

# ─── SESSION LOG ──────────────────────────────────────────────────────────────
st.markdown("""
<div class="sec-hdr">
  <div class="sec-line"></div><div class="sec-title">Session Log</div><div class="sec-line"></div>
</div>""", unsafe_allow_html=True)

if not completed:
    st.caption("No completed trades this session.")
else:
    rows = []
    running_r = 0.0
    for t in completed:
        running_r += t.get("actual_r", 0)
        outcome    = t.get("outcome", "—")
        actual_r   = t.get("actual_r", 0)
        addons     = t.get("add_ons", [])
        rows.append({
            "#":            t.get("id", "—"),
            "Time":         t.get("close_time", "—"),
            "Instr.":       t.get("instrument", "—"),
            "Grade":        t.get("grade", "—"),
            "R:R":          t.get("rr_target", "—"),
            "Implied R":    float(t.get("implied_r", 0)) if t.get("implied_r") not in (None, "—") else None,
            "Risk(entry)":  int(t["risk_score_entry"]) if t.get("risk_score_entry") not in (None, "—") else "—",
            "Risk(close)":  int(t["risk_score_close"]) if t.get("risk_score_close") not in (None, "—") else "—",
            "Add-ons":      len(addons),
            "Actual R":     actual_r,
            "Outcome":      outcome,
            "Cumulative R": running_r,
            "Note":         t.get("note", ""),
        })

    df = pd.DataFrame(rows[::-1])

    def _c_r(v):
        c = "#22c55e" if v > 0 else ("#ef4444" if v < 0 else "#94a3b8")
        return f"color:{c};font-weight:600"
    def _c_outcome(v):
        c = "#22c55e" if v=="Win" else ("#ef4444" if v=="Loss" else "#94a3b8")
        return f"color:{c};font-weight:700"

    styled = (
        df.style
        .map(_c_r,       subset=["Actual R", "Cumulative R"])
        .map(_c_outcome, subset=["Outcome"])
        .format({
            "R:R":          lambda x: f"{x}" if isinstance(x, str) else f"{float(x):.2f}",
            "Implied R":    lambda x: f"{x:.2f}" if x is not None else "—",
            "Actual R":     lambda x: f"+{x:.2f}R" if x > 0 else f"{x:.2f}R",
            "Cumulative R": lambda x: f"+{x:.2f}R" if x > 0 else f"{x:.2f}R",
        }, na_rep="—")
        .set_properties(**{"background-color": "#0e1220", "color": "#e2e8f0",
                           "border-color": "#1a2238"})
    )
    st.dataframe(styled, width="stretch", hide_index=True)

    # ── Delete a session trade ──
    with st.expander("Delete a trade from this session", expanded=False):
        _del_opts = [f"#{t.get('id','?')} {t.get('close_time','?')} — {t.get('outcome','?')} {t.get('actual_r',0):+.3f}R ({t.get('grade','?')})"
                     for t in completed]
        _del_sel = st.selectbox("Select trade to delete", _del_opts, key="del_session_sel")
        if st.button("Delete Selected Trade", key="del_session_btn", type="secondary"):
            _del_idx = _del_opts.index(_del_sel)
            _del_trade = completed[_del_idx]
            completed.pop(_del_idx)
            session["completed_trades"] = completed
            _save_session(session)
            # Also remove from history
            _h = _load_history()
            _day = session["session_date"]
            if _day in _h:
                _h[_day] = [t for t in _h[_day]
                             if not (t.get("id") == _del_trade.get("id") and
                                     t.get("close_time") == _del_trade.get("close_time"))]
                _save_history(_h)
            st.rerun()

# ─── TRADE HISTORY ────────────────────────────────────────────────────────────
st.markdown("""
<div class="sec-hdr">
  <div class="sec-line"></div><div class="sec-title">Trade History</div><div class="sec-line"></div>
</div>""", unsafe_allow_html=True)

_history = _load_history()

if not _history:
    st.caption("No historical trades yet. Trades are saved automatically as you log them.")
else:
    # Build flat list of all trades across all days
    _all_rows = []
    for _day in sorted(_history.keys(), reverse=True):
        for t in _history[_day]:
            _all_rows.append({
                "Date":          _day,
                "Time":          t.get("close_time", "—"),
                "Instr.":        t.get("instrument", "—"),
                "Grade":         t.get("grade", "—"),
                "R:R":           t.get("rr_target", "—"),
                "Implied R":     t.get("implied_r", "—"),
                "Risk(entry)":   t.get("risk_score_entry", "—"),
                "Risk(close)":   t.get("risk_score_close", "—"),
                "Actual R":      t.get("actual_r", 0),
                "Outcome":       t.get("outcome", "—"),
                "Note":          t.get("note", ""),
            })

    _hist_df = pd.DataFrame(_all_rows)

    # Per-day summary in expanders
    for _day in sorted(_history.keys(), reverse=True):
        _day_trades = _history[_day]
        _day_r      = sum(t.get("actual_r", 0) for t in _day_trades)
        _day_wins   = sum(1 for t in _day_trades if t.get("actual_r", 0) > 0)
        _day_sign   = "+" if _day_r >= 0 else ""
        _day_color  = "🟢" if _day_r >= 0 else "🔴"
        _exp_label  = f"{_day_color} {_day}  —  {len(_day_trades)} trades  ·  {_day_sign}{_day_r:.3f}R  ·  {_day_wins}W/{len(_day_trades)-_day_wins}L"
        with st.expander(_exp_label, expanded=False):
            _d_rows = []
            _run = 0.0
            for t in _day_trades:
                _run += t.get("actual_r", 0)
                _d_rows.append({
                    "Time":         t.get("close_time", "—"),
                    "Instr.":       t.get("instrument", "—"),
                    "Grade":        t.get("grade", "—"),
                    "R:R":          t.get("rr_target", "—"),
                    "Implied R":    float(t.get("implied_r", 0)) if t.get("implied_r") not in (None, "—") else None,
                    "Risk(entry)":  int(t["risk_score_entry"]) if t.get("risk_score_entry") not in (None, "—") else "—",
                    "Risk(close)":  int(t["risk_score_close"]) if t.get("risk_score_close") not in (None, "—") else "—",
                    "Actual R":     t.get("actual_r", 0),
                    "Outcome":      t.get("outcome", "—"),
                    "Cumulative R": _run,
                    "Note":         t.get("note", ""),
                })
            _ddf = pd.DataFrame(_d_rows[::-1])
            def _hc_r(v):
                c = "#22c55e" if v > 0 else ("#ef4444" if v < 0 else "#94a3b8")
                return f"color:{c};font-weight:600"
            def _hc_out(v):
                c = "#22c55e" if v=="Win" else ("#ef4444" if v=="Loss" else "#94a3b8")
                return f"color:{c};font-weight:700"
            _dstyled = (
                _ddf.style
                .map(_hc_r,   subset=["Actual R", "Cumulative R"])
                .map(_hc_out, subset=["Outcome"])
                .format({
                    "R:R":          lambda x: f"{x}" if isinstance(x, str) else f"{float(x):.2f}",
                    "Implied R":    lambda x: f"{x:.2f}" if x is not None else "—",
                    "Actual R":     lambda x: f"+{x:.2f}R" if x > 0 else f"{x:.2f}R",
                    "Cumulative R": lambda x: f"+{x:.2f}R" if x > 0 else f"{x:.2f}R",
                }, na_rep="—")
                .set_properties(**{"background-color": "#0e1220", "color": "#e2e8f0",
                                   "border-color": "#1a2238"})
            )
            st.dataframe(_dstyled, width="stretch", hide_index=True)

            # Delete a trade from this day
            _hdel_opts = [
                f"#{t.get('id','?')} {t.get('close_time','?')} — {t.get('outcome','?')} {t.get('actual_r',0):+.3f}R ({t.get('grade','?')})"
                for t in _day_trades
            ]
            _hd_col1, _hd_col2 = st.columns([4, 1])
            with _hd_col1:
                _hdel_sel = st.selectbox("Delete trade", _hdel_opts,
                                         key=f"hdel_sel_{_day}", label_visibility="collapsed")
            with _hd_col2:
                if st.button("Delete", key=f"hdel_btn_{_day}", type="secondary"):
                    _hidx = _hdel_opts.index(_hdel_sel)
                    _h2 = _load_history()
                    if _day in _h2:
                        _h2[_day].pop(_hidx)
                        if not _h2[_day]:
                            del _h2[_day]   # remove empty day entry
                        _save_history(_h2)
                    st.rerun()

    # ── CSV Export ──
    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)
    _csv_bytes = _hist_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Export All History to CSV",
        data=_csv_bytes,
        file_name=f"trading_history_{date.today()}.csv",
        mime="text/csv",
        width="stretch",
    )

st.markdown("<div style='height:60px'></div>", unsafe_allow_html=True)
