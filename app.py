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
@st.cache_resource
def _get_sb():
    if not _HAS_SUPABASE:
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
    "active_trade":     None,
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
        "phase1_complete":  False,
        "phase2":           {},
        "phase2_complete":  False,
        "phase3_complete":  False,
        "phase4_complete":  False,
        "phase4_trade_num": -1,
    },
}

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
        if data.get("session_date") != str(date.today()):
            _recover_stale_session(data)
            s = {**_SESSION_DEFAULTS}
            s["session_date"] = str(date.today())
            s["balance_override"] = data.get("balance_override")
            if data.get("active_trade"):
                s["active_trade"] = data["active_trade"]
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
if not st.session_state.get("_auth"):
    _pwd = st.text_input("Password", type="password", key="_pwd")
    if _pwd:
        if _pwd == st.secrets.get("app", {}).get("password", ""):
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

# ─── PROCESS PENDING WS EVENTS ───────────────────────────────────────────────
with _WS_LOCK:
    _ws_pending_open  = _WS_STATE.get("pending_open")
    _ws_pending_close = _WS_STATE.get("pending_close")
    _WS_STATE["pending_open"]  = None
    _WS_STATE["pending_close"] = None

# Auto-pre-fill new trade form from WS open event
if _ws_pending_open and session.get("active_trade") is None:
    st.session_state["_ws_prefill"] = _ws_pending_open

# Auto-resolve active trade from WS close event
if _ws_pending_close and session.get("active_trade") is not None:
    _at   = session["active_trade"]
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
    session["active_trade"] = None
    _save_session(session)
    st.session_state["_ws_auto_closed"] = _outcome
    st.rerun()

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── CONNECTION KILL SWITCH (top of sidebar, always visible) ──
    _conn_current = prefs.get("connection_enabled", False)
    _conn_label   = "🟢 CONNECTED — click to disconnect" if _conn_current else "🔴 OFFLINE MODE — click to connect"
    _conn_style   = "background:#14532d;border:2px solid #22c55e;" if _conn_current else "background:#1c0a0a;border:2px solid #ef4444;"
    st.markdown(
        f'<div style="{_conn_style}border-radius:8px;padding:12px 16px;margin-bottom:14px;'
        f'text-align:center;font-size:0.8rem;font-weight:800;letter-spacing:.06em;color:#e2e8f0">'
        f'{_conn_label}</div>',
        unsafe_allow_html=True,
    )
    if st.button(
        "ENABLE SYNC" if not _conn_current else "KILL ALL CONNECTIONS",
        use_container_width=True,
        type="primary" if not _conn_current else "secondary",
        key="kill_switch_btn",
    ):
        prefs["connection_enabled"] = not _conn_current
        _save_prefs(prefs)
        if _conn_current:
            # Immediately kill WS thread
            with _WS_LOCK:
                _WS_STATE["auth_failed"]  = True   # stops retry loop
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

    if st.button("Save Settings", use_container_width=True):
        prefs["balance"]                = new_balance
        prefs["r_pct"]                  = new_r_pct
        prefs["daily_limit_r"]          = new_daily_limit
        prefs["addon_r"]                = new_addon_r
        prefs["mode"]                   = mode
        prefs["fabio_submode"]          = fabio_submode or prefs.get("fabio_submode", "Conservative Mode")
        prefs["morning_report_enabled"] = new_mr_enabled
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
            if st.button("Save Credentials", use_container_width=True):
                _save_ex_cfg({"exchange": _ex_sel, "api_key": _api_key,
                               "secret_key": _sec_key, "passphrase": _pass})
                st.success("Saved")
        with _ex_c2:
            if st.button("Test Connection", use_container_width=True, type="secondary"):
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
    if st.button("Reset Session", use_container_width=True, type="secondary"):
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
active         = session["active_trade"]
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
# Include active trade's total exposure (implied_r + add-ons) as already "used"
_active_exposure = (
    active.get("implied_r", 0) + sum(a["r"] for a in active.get("add_ons", []))
    if active else 0.0
)
_used_r        = losses_r + _active_exposure
remaining_r    = max(0.0, effective_daily_limit_r - _used_r)
limit_hit      = _used_r >= effective_daily_limit_r
pnl_pct        = min(1.0, _used_r / effective_daily_limit_r) if effective_daily_limit_r > 0 else 1.0
fabio_state    = session.get("fabio_state", _SESSION_DEFAULTS["fabio_state"])

# ─── CHECKLIST STATE ──────────────────────────────────────────────────────────
_cl_def   = _SESSION_DEFAULTS["checklist"]
checklist = {**_cl_def, **session.get("checklist", {})}
cl_phase1 = checklist.get("phase1_complete", False)
cl_phase2 = checklist.get("phase2_complete", False)
cl_phase3 = checklist.get("phase3_complete", False)
# Phase 4 valid only for the next trade after it was completed (trade_num tracks this)
cl_phase4 = (
    checklist.get("phase4_complete", False) and
    checklist.get("phase4_trade_num", -1) == len(completed)
) or (active is not None)
# Sync phase1 with MRC completion
if safe_mode and mr_completed and not cl_phase1:
    checklist["phase1_complete"] = True
    cl_phase1 = True
    session["checklist"] = checklist
    _save_session(session)
# Gate level: 1=need phase1, 2=need phase2, 3=need phase3, 4=need phase4, 5=open
if not safe_mode or active is not None:
    cl_gate = 5
elif not cl_phase1:
    cl_gate = 1
elif not cl_phase2:
    cl_gate = 2
elif not cl_phase3:
    cl_gate = 3
elif not cl_phase4:
    cl_gate = 4
else:
    cl_gate = 5

# ─── BANNERS ─────────────────────────────────────────────────────────────────
if limit_hit:
    st.markdown('<div class="stop-banner">DAILY LOSS LIMIT REACHED — STOP TRADING</div>',
                unsafe_allow_html=True)
elif pnl_pct >= 0.75:
    st.markdown(f'<div class="warn-banner">Warning — {pnl_pct*100:.0f}% of daily loss limit used. Be selective.</div>',
                unsafe_allow_html=True)

# Overnight trade banner — only if open_date is before today
if active and active.get("open_date") and active["open_date"] != str(date.today()):
    st.markdown(
        f'<div class="info-banner">⚠️ Trade carried over from {active["open_date"]} — '
        f'{active.get("grade","?")} @ R:R {active.get("rr_target","?")} still open. '
        f'Close it when ready.</div>',
        unsafe_allow_html=True,
    )

# ─── PAGE TITLE ──────────────────────────────────────────────────────────────
st.markdown(
    '<h1 style="font-size:2.4rem;font-weight:900;letter-spacing:0.04em;'
    'color:#e2e8f0;margin:0 0 18px 0;line-height:1;text-align:center">Trading Tool</h1>',
    unsafe_allow_html=True,
)

# ─── PHASES (safe mode = equal-size status cards + active form below) ──────────
if safe_mode:
    # Pre-compute display values
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
    _cl2    = checklist.get("phase2", {})
    _cl2_s  = _cl2.get("strategy", "?")
    _cl2_sc = "#22c55e" if _cl2_s == "Mean Reversion" else "#f97316"

    # ── Phase status cards — single HTML block so all boxes are identical size ──
    _CARD_STYLE  = (
        "background:#0e1220;border:1px solid #1a2238;border-top:3px solid {bc};"
        "border-radius:10px;padding:18px 20px;min-height:200px;"
        "display:flex;flex-direction:column;align-items:center;text-align:center;gap:6px"
    )
    _LOCK_CARD = (
        "background:#080c14;border:1px solid #1a2238;border-top:3px solid #1a2238;"
        "border-radius:10px;padding:18px 20px;min-height:200px;"
        "display:flex;flex-direction:column;align-items:center;justify-content:center;"
        "text-align:center;gap:8px"
    )
    _LBL = "font-size:0.58rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;text-align:center;width:100%"
    _VAL = "font-size:0.88rem;font-weight:700;color:#e2e8f0"

    # Phase 1 card
    if cl_phase1:
        _p1_bc  = "#3b82f6"
        _p1_top = f'<div class="checklist-badge-done" style="margin:0 auto">✓ Phase 1 — Human Performance</div>'
        _p1_body = (
            f'<div style="{_LBL}">Grade</div>'
            f'<div style="font-size:2.6rem;font-weight:800;line-height:1;letter-spacing:-0.03em" class="{_mr_css}">{_mr_g}</div>'
            f'<div style="font-size:0.78rem;color:#94a3b8">{_mr_desc}</div>'
            f'<div style="{_LBL};margin-top:6px">Score: <span style="color:#e2e8f0;font-weight:700">{_mr_sc}/18</span>'
            f' &nbsp;·&nbsp; {_limit_line}</div>'
        )
    else:
        _p1_bc  = "#3b82f6"
        _p1_top = '<div class="checklist-badge" style="margin:0 auto">Phase 1 — Human Performance</div>'
        _p1_body = '<div style="font-size:0.88rem;color:#4b5a7a;margin-top:8px">○ Awaiting check-in</div>'

    # Phase 2 card
    if not cl_phase1:
        _p2_card_html = f'<div style="{_LOCK_CARD}"><div style="font-size:1.4rem">🔒</div><div style="font-size:0.78rem;color:#4b5a7a">Complete Phase 1 first</div></div>'
    elif cl_phase2:
        _p2_card_html = (
            f'<div style="{_CARD_STYLE.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge-done" style="margin:0 auto">✓ Phase 2 — Market Context</div>'
            f'<div style="{_LBL}">Strategy</div>'
            f'<div style="font-size:2.6rem;font-weight:800;line-height:1;letter-spacing:-0.03em;color:{_cl2_sc}">{_cl2_s.upper()}</div>'
            f'<div style="{_LBL};margin-top:6px">'
            f'State: <span style="{_VAL}">{_cl2.get("market_state","?")}</span>'
            f' &nbsp;·&nbsp; D: <span style="{_VAL}">{_cl2.get("d_profile","?")}</span>'
            f' &nbsp;·&nbsp; Loc: <span style="{_VAL}">{_cl2.get("price_location","?")}</span>'
            f' &nbsp;·&nbsp; IB: <span style="{_VAL}">{_cl2.get("ib_size","?")}</span>'
            f'</div></div>'
        )
    else:
        _p2_card_html = (
            f'<div style="{_CARD_STYLE.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge" style="margin:0 auto">Phase 2 — Market Context</div>'
            f'<div style="font-size:0.88rem;color:#4b5a7a;margin-top:8px">○ Awaiting market analysis</div>'
            f'</div>'
        )

    # Phase 3 card
    if not cl_phase2:
        _p3_card_html = f'<div style="{_LOCK_CARD}"><div style="font-size:1.4rem">🔒</div><div style="font-size:0.78rem;color:#4b5a7a">Complete Phase 2 first</div></div>'
    elif cl_phase3:
        _p3_card_html = (
            f'<div style="{_CARD_STYLE.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge-done" style="margin:0 auto">✓ Phase 3 — Key Levels</div>'
            f'<div style="font-size:0.88rem;color:#94a3b8;margin-top:10px;line-height:1.9">'
            f'✓ S/R levels marked<br>✓ Trend lines drawn<br>✓ Price alerts set</div>'
            f'</div>'
        )
    else:
        _p3_card_html = (
            f'<div style="{_CARD_STYLE.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge" style="margin:0 auto">Phase 3 — Key Levels</div>'
            f'<div style="font-size:0.88rem;color:#4b5a7a;margin-top:8px">○ Awaiting confirmation</div>'
            f'</div>'
        )

    # Build Phase 1 card html
    _p1_card_html = (
        f'<div style="{_CARD_STYLE.format(bc=_p1_bc)}">'
        f'{_p1_top}{_p1_body}'
        f'</div>'
    )

    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:10px">'
        f'{_p1_card_html}{_p2_card_html}{_p3_card_html}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Redo buttons (only shown when phase is complete) ──
    _rb1, _rb2, _rb3 = st.columns(3)
    with _rb1:
        if cl_phase1 and st.button("↩ Redo Check-In", key="redo_p1_sm", use_container_width=True):
            session["morning_report"] = {**_SESSION_DEFAULTS["morning_report"]}
            checklist["phase1_complete"] = False
            checklist["phase2_complete"] = False
            checklist["phase3_complete"] = False
            checklist["phase4_complete"] = False
            session["checklist"] = checklist
            _save_session(session)
            st.rerun()
    with _rb2:
        if cl_phase2 and st.button("↩ Redo Market Analysis", key="redo_p2", use_container_width=True):
            checklist["phase2"]          = {}
            checklist["phase2_complete"] = False
            checklist["phase3_complete"] = False
            checklist["phase4_complete"] = False
            session["checklist"] = checklist
            _save_session(session)
            st.rerun()
    with _rb3:
        if cl_phase3 and st.button("↩ Redo Key Levels", key="redo_p3", use_container_width=True):
            checklist["phase3_complete"] = False
            checklist["phase4_complete"] = False
            session["checklist"] = checklist
            _save_session(session)
            st.rerun()

    # ── Active form — shown below the cards, one at a time ──
    if not cl_phase1:
        # Phase 1 form
        st.markdown('<div style="margin-top:4px"></div>', unsafe_allow_html=True)
        with st.form("morning_report_form"):
            qc1, qc2 = st.columns(2)
            for i, (qkey, qlabel, qopts, _) in enumerate(_MR_QUESTIONS):
                with (qc1 if i < 3 else qc2):
                    st.selectbox(qlabel, qopts, key=f"mr_q_{qkey}")
            if st.form_submit_button("Submit Phase 1 →", use_container_width=True):
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

    elif not cl_phase2:
        # Phase 2 form
        st.markdown('<div style="margin-top:4px"></div>', unsafe_allow_html=True)
        with st.form("checklist_phase2"):
            _p2c1, _p2c2 = st.columns(2)
            with _p2c1:
                _p2_market = st.radio("Market state", ["Balanced", "Imbalanced"])
                _p2_d      = st.radio("Volume 'D' profile on 30M?", ["Yes", "No"])
            with _p2c2:
                _p2_loc = st.radio("Price location", ["Top of D", "Middle of D", "Bottom of D", "Outside D"])
                _p2_ib  = st.radio("Initial Balance size", ["Large", "Small"])
            _price_in_d    = (_p2_d == "Yes") and (_p2_loc != "Outside D")
            _derived_strat = "Mean Reversion" if _price_in_d else "Breakout"
            _ds_col        = "#22c55e" if _derived_strat == "Mean Reversion" else "#f97316"
            st.markdown(
                f'<div style="padding:8px 0 4px;font-size:0.62rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600">Suggested Strategy</div>'
                f'<div style="font-size:1.8rem;font-weight:900;color:{_ds_col};line-height:1">{_derived_strat.upper()}</div>',
                unsafe_allow_html=True,
            )
            if st.form_submit_button("Confirm Market Context →", use_container_width=True):
                checklist["phase2"] = {
                    "market_state": _p2_market, "d_profile": _p2_d,
                    "price_location": _p2_loc, "ib_size": _p2_ib,
                    "strategy": _derived_strat,
                }
                checklist["phase2_complete"] = True
                session["checklist"] = checklist
                _save_session(session)
                st.rerun()

    elif not cl_phase3:
        # Phase 3 form
        st.markdown('<div style="margin-top:4px"></div>', unsafe_allow_html=True)
        with st.form("checklist_phase3"):
            _p3_1 = st.checkbox("Key S/R levels marked on chart")
            _p3_2 = st.checkbox("Trend lines drawn on key charts")
            _p3_3 = st.checkbox("Price alerts set on watchlist")
            if st.form_submit_button("Confirm Key Levels →", use_container_width=True):
                if not (_p3_1 and _p3_2 and _p3_3):
                    st.error("Check all three to proceed.")
                else:
                    checklist["phase3_complete"] = True
                    session["checklist"] = checklist
                    _save_session(session)
                    st.rerun()

    # F grade banner (shown below forms if applicable)
    if cl_phase1 and _mr_g == "F":
        st.markdown(
            '<div class="mr-no-trade">MORNING GRADE: F — DO NOT TRADE TODAY · Protect capital.</div>',
            unsafe_allow_html=True,
        )

elif mr_enabled:
    # ── Standalone Morning Report Card (no safe mode) ──
    st.markdown("""
    <div class="sec-hdr">
      <div class="sec-line"></div>
      <div class="sec-title">Morning Report Card</div>
      <div class="sec-line"></div>
    </div>""", unsafe_allow_html=True)

    if not morning_report.get("completed"):
        st.markdown(
            '<div class="info-banner" style="margin-bottom:12px">'
            'Answer 6 quick questions to calibrate your R allowance for today. '
            'Based on Lance Brightstein\'s daily performance check-in method.</div>',
            unsafe_allow_html=True,
        )
        with st.form("morning_report_form"):
            qc1, qc2 = st.columns(2)
            for i, (qkey, qlabel, qopts, _) in enumerate(_MR_QUESTIONS):
                with (qc1 if i < 3 else qc2):
                    st.selectbox(qlabel, qopts, key=f"mr_q_{qkey}")
            _mr_submitted = st.form_submit_button("Submit Report Card", use_container_width=True)
            if _mr_submitted:
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
    else:
        _mr_g    = morning_report.get("grade", "?")
        _mr_mult = morning_report.get("multiplier", 1.0)
        _mr_css  = morning_report.get("color", "white")
        _mr_desc = morning_report.get("description", "")
        _mr_sc   = morning_report.get("score", "?")
        _mr_eff  = effective_daily_limit_r
        _mr_orig = daily_limit_r
        if _mr_g == "F":
            st.markdown(
                '<div class="mr-no-trade">MORNING GRADE: F — DO NOT TRADE TODAY · '
                'Protect capital. Come back tomorrow.</div>', unsafe_allow_html=True)
        elif _mr_g in ("D", "C"):
            st.markdown(
                f'<div class="warn-banner">Morning Grade: <strong>{_mr_g}</strong> — '
                f'{_mr_desc}. Daily limit reduced to {_mr_eff:.2f}R ({_mr_mult*100:.0f}% of {_mr_orig}R).</div>',
                unsafe_allow_html=True)
        _limit_line = (
            f"{_mr_orig}R × {_mr_mult*100:.0f}% = <strong>{_mr_eff:.2f}R effective limit</strong>"
            if _mr_mult < 1.0 else f"<strong>{_mr_orig}R</strong> — full limit (no reduction)"
        )
        st.markdown(f"""
        <div class="mr-card">
          <div class="mr-badge">Morning Report Card</div>
          <div style="display:flex;gap:32px;align-items:center;flex-wrap:wrap">
            <div>
              <div style="font-size:0.6rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600">Grade</div>
              <div style="font-size:3rem;font-weight:800;line-height:1;letter-spacing:-0.03em" class="{_mr_css}">{_mr_g}</div>
              <div style="font-size:0.78rem;color:#94a3b8;margin-top:4px">{_mr_desc}</div>
            </div>
            <div style="flex:1;min-width:200px">
              <div style="font-size:0.6rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin-bottom:6px">Score & Limit</div>
              <div style="font-size:0.88rem;color:#e2e8f0">Score: <strong>{_mr_sc}/18</strong></div>
              <div style="font-size:0.88rem;color:#e2e8f0;margin-top:4px">{_limit_line}</div>
            </div>
          </div>
        </div>""", unsafe_allow_html=True)
        _mr_redo_col, _ = st.columns([1, 4])
        with _mr_redo_col:
            if st.button("Redo Check-In", type="secondary", use_container_width=True):
                session["morning_report"] = {**_SESSION_DEFAULTS["morning_report"]}
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

st.markdown(f"""
<div class="hero-grid">
  <div class="hero-card">
    <div class="hero-lbl">Remaining R for the day</div>
    <div class="hero-val {remaining_color}">{remaining_r:.3f}R</div>
    <div class="hero-sub">Limit: {effective_daily_limit_r:.2f}R ({_fmt(0 if losses_r == 0 else -losses_r)} used){' · Grade ' + mr_grade_val if mr_grade_val and mr_multiplier < 1.0 else ''}</div>
    <div class="limit-bar-bg">
      <div class="limit-bar-fill" style="width:{bar_pct}%;background:{bar_color}"></div>
    </div>
  </div>
  <div class="hero-card">
    <div class="hero-lbl">Today's R</div>
    <div class="hero-val {pnl_color}">{pnl_sign}{session_pnl_r:.3f}R</div>
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
        <div class="mini-val {_aw_c}">+{_avg_win:.3f}R</div>
        <div class="mini-lbl">Avg Win</div>
      </div>
      <div class="mini-card">
        <div class="mini-val {_al_c}">{_avg_loss:.3f}R</div>
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
                if st.button("Activate Scaling", use_container_width=True):
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
                if st.button("Keep Normal", use_container_width=True, type="secondary"):
                    fabio_state["consecutive_wins"] = 0
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()

        # Unit outcome buttons (competition phase 1+)
        if phase >= 1 and u1 is None:
            st.markdown("**Unit 1 outcome:**")
            cu1, cu2, cu3 = st.columns(3)
            with cu1:
                if st.button("Unit 1 TP", use_container_width=True):
                    fabio_state["unit1_status"] = "tp"
                    fabio_state["phase"] = 2
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with cu2:
                if st.button("Unit 1 BE", use_container_width=True, type="secondary"):
                    fabio_state["unit1_status"] = "be"
                    fabio_state["phase"] = 2
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with cu3:
                if st.button("Unit 1 SL", use_container_width=True, type="secondary"):
                    fabio_state["unit1_status"] = "sl"
                    fabio_state["phase"] = 0  # back to defensive
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()

        if phase >= 2 and not u2_locked and u2 is None:
            st.markdown("**Unit 2 outcome:**")
            cu1, cu2, cu3 = st.columns(3)
            with cu1:
                if st.button("Unit 2 TP", use_container_width=True):
                    fabio_state["unit2_status"] = "tp"
                    fabio_state["phase"] = 3
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with cu2:
                if st.button("Unit 2 BE", use_container_width=True, type="secondary"):
                    fabio_state["unit2_status"] = "be"
                    fabio_state["phase"] = 3
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()
            with cu3:
                if st.button("Unit 2 SL", use_container_width=True, type="secondary"):
                    fabio_state["unit2_status"] = "sl"
                    fabio_state["phase"] = 3
                    session["fabio_state"] = fabio_state
                    _save_session(session)
                    st.rerun()

# ─── ACTIVE TRADE ─────────────────────────────────────────────────────────────
st.markdown("""
<div class="sec-hdr">
  <div class="sec-line"></div><div class="sec-title">Active Trade</div><div class="sec-line"></div>
</div>""", unsafe_allow_html=True)

if active is None:
    # ── Safe Mode Phase 4 gate ──
    if safe_mode and cl_gate == 4:
        _cl2_data  = checklist.get("phase2", {})
        _cl2_strat = _cl2_data.get("strategy", "Mean Reversion")
        _cl2_strat_c = "#22c55e" if _cl2_strat == "Mean Reversion" else "#f97316"
        st.markdown(f"""
        <div class="checklist-card">
          <div class="checklist-badge">SAFE MODE — Phase 4 of 4</div>
          <div style="font-size:0.85rem;color:#94a3b8;margin:4px 0 14px">
            Pre-Trade Gate — confirm setup conditions before entering
          </div>
          <div style="font-size:0.65rem;color:#4b5a7a;text-transform:uppercase;
                      letter-spacing:.1em;font-weight:600;margin-bottom:4px">Strategy today</div>
          <div style="font-size:1rem;font-weight:700;color:{_cl2_strat_c}">{_cl2_strat.upper()}</div>
        </div>""", unsafe_allow_html=True)
        with st.form("checklist_phase4"):
            _p4c1, _p4c2 = st.columns(2)
            with _p4c1:
                _p4_level = st.radio("Are we at a valid S/R level?", ["Yes", "No — not yet"])
            with _p4c2:
                _p4_fp = st.radio("Footprint / order activity confirms?", ["Yes — confirmed", "No — wait"])
            if _cl2_strat == "Breakout":
                _p4_bo = st.radio(
                    "Did price break the 'D' with confirmed order activity?",
                    ["Yes — confirmed breakout", "No — false breakout", "N/A"],
                )
            else:
                _p4_bo = "N/A"
            if st.form_submit_button("Confirm — Unlock Trading", use_container_width=True):
                if _p4_level.startswith("No"):
                    st.warning("Not at a valid S/R level — wait for better location.")
                elif _p4_fp.startswith("No"):
                    st.warning("No order activity confirmation — wait for footprint to confirm.")
                elif _cl2_strat == "Breakout" and _p4_bo.startswith("No — false"):
                    st.error("False breakout — do not trade. Wait for confirmed order activity.")
                else:
                    checklist["phase4_complete"]  = True
                    checklist["phase4_trade_num"] = len(completed)
                    session["checklist"] = checklist
                    _save_session(session)
                    st.rerun()

    elif safe_mode and cl_gate < 4:
        st.markdown(
            '<div class="checklist-lock">🔒 Complete the pre-trading checklist above to unlock trading.</div>',
            unsafe_allow_html=True,
        )

    if not safe_mode or cl_gate == 5:
        # ── New trade form ──
        # ── Quick-launch buttons ──
        _sel_grade = st.session_state.get("_quick_grade", "")
        qb1, qb2 = st.columns(2)
        with qb1:
            _mr_type = "primary" if _sel_grade == "AAA" else "secondary"
            if st.button("MEAN REVERSION", use_container_width=True, type=_mr_type,
                         help="AAA setup — " + str(prefs["grades"]["AAA"]["implied_r"]) + "R implied risk"):
                st.session_state["_quick_grade"] = "AAA"
                st.rerun()
        with qb2:
            _bo_type = "primary" if _sel_grade == "AA" else "secondary"
            if st.button("BREAKOUT", use_container_width=True, type=_bo_type,
                         help="AA setup — " + str(prefs["grades"]["AA"]["implied_r"]) + "R implied risk"):
                st.session_state["_quick_grade"] = "AA"
                st.rerun()

        st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)

        # Pull WS prefill values (populated when OKX fill detected)
        _pf = st.session_state.get("_ws_prefill") or {}
        if _pf:
            _pf_rr   = _pf.get("rr")
            _pf_inst = _pf.get("instrument", "SOL")
            st.markdown(
                f'<div class="info-banner">⚡ OKX fill detected — {_pf_inst} entry @ {_pf.get("entry_px","?")} · '
                f'SL {_pf.get("sl_px","?")} · TP {_pf.get("tp_px","?")} · '
                f'{"R:R " + str(_pf_rr) if _pf_rr else "No TP set — enter R:R manually"}'
                f'<br>Select grade + confirm to log.</div>',
                unsafe_allow_html=True,
            )

        with st.form("new_trade_form", clear_on_submit=True):
            # Row 1: R:R + Grade + Note
            _default_rr = float(_pf.get("rr") or 3.0)
            fr1, fr2, fr3 = st.columns([1, 2, 3])
            with fr1:
                rr_target = st.number_input(
                    "R:R", min_value=0.1, max_value=50.0,
                    value=_default_rr, step=0.1, format="%.1f",
                )
            with fr2:
                grade_opts    = list(prefs["grades"].keys())
                grade_lbls    = [f"{k} ({prefs['grades'][k]['implied_r']}R)" for k in grade_opts]
                default_grade = st.session_state.get("_quick_grade", "AA")
                default_idx   = grade_opts.index(default_grade) if default_grade in grade_opts else 0
                grade_lbl     = st.selectbox("Grade", grade_lbls, index=default_idx)
                chosen_grade  = grade_opts[grade_lbls.index(grade_lbl)]
            with fr3:
                note = st.text_input("Note", placeholder="Setup / reason for entry")

            # Row 2: Instrument + Entry + Stop + Qty (position sizer)
            _instruments = ["SOL", "BTC", "ETH", "SUI", "MNQ", "MES"]
            _futures = {"MNQ": 2.0, "MES": 5.0}   # $ per point
            _pf_inst_idx = _instruments.index(_pf.get("instrument", "SOL")) \
                           if _pf.get("instrument") in _instruments else 0
            fi1, fi2, fi3, fi4 = st.columns([1.5, 2, 2, 2])
            with fi1:
                instrument = st.selectbox("Instrument", _instruments, index=_pf_inst_idx)
            with fi2:
                entry_price = st.number_input("Entry Price", min_value=0.0,
                                              value=float(_pf.get("entry_px") or 0.0),
                                              step=0.01, format="%.4f")
            with fi3:
                stop_price  = st.number_input("Stop Price",  min_value=0.0,
                                              value=float(_pf.get("sl_px") or 0.0),
                                              step=0.01, format="%.4f")
            with fi4:
                _risk_usd = one_r * prefs["grades"][chosen_grade]["implied_r"]
                _diff     = abs(entry_price - stop_price)
                if entry_price > 0 and stop_price > 0 and _diff > 0:
                    if instrument in _futures:
                        _qty = _risk_usd / (_diff * _futures[instrument])
                        _qty_lbl = f"{_qty:.3f} lots (min 1)"
                    else:
                        _qty = _risk_usd / _diff
                        _qty_lbl = f"{_qty:.4f}"
                    st.markdown(
                        f"<div style='margin-top:28px'>"
                        f"<div style='font-size:0.58rem;color:#4b5a7a;text-transform:uppercase;"
                        f"letter-spacing:.1em;font-weight:600;margin-bottom:4px'>Qty</div>"
                        f"<div style='font-size:1.1rem;font-weight:700;color:#f97316'>{_qty_lbl}</div>"
                        f"<div style='font-size:0.65rem;color:#4b5a7a'>Risk ${_risk_usd:,.2f}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<div style='margin-top:28px;font-size:0.75rem;color:#4b5a7a'>"
                        "Enter entry &amp; stop for qty</div>",
                        unsafe_allow_html=True,
                    )

            submitted = st.form_submit_button("Enter Trade", use_container_width=True)
            if submitted:
                implied_r = prefs["grades"][chosen_grade]["implied_r"]
                _entry_risk = _risk_score(rr_target, 0, chosen_grade)
                active = {
                    "id":               len(completed) + 1,
                    "open_date":        str(date.today()),
                    "start_time":       datetime.now().strftime("%H:%M:%S"),
                    "grade":            chosen_grade,
                    "implied_r":        implied_r,
                    "rr_target":        rr_target,
                    "risk_score_entry": _entry_risk,
                    "instrument":       instrument,
                    "entry_price":      entry_price if entry_price > 0 else None,
                    "stop_price":  stop_price  if stop_price  > 0 else None,
                    "add_ons":     [],
                    "note":        note,
                }
                if "_quick_grade" in st.session_state:
                    del st.session_state["_quick_grade"]
                st.session_state.pop("_ws_prefill", None)   # clear WS prefill
                session["active_trade"] = active
                _save_session(session)
                st.rerun()
else:
    # ── Active trade card ──
    add_ons    = active.get("add_ons", [])
    total_risk_r = active["implied_r"] + sum(a["r"] for a in add_ons)
    rr_target    = active["rr_target"]
    _grade       = active.get("grade", "AA")
    win_p        = _win_prob(_grade)
    risk_sc      = _risk_score(rr_target, len(add_ons), _grade)
    risk_color   = "#22c55e" if risk_sc < 40 else ("#f59e0b" if risk_sc < 70 else "#ef4444")
    risk_label   = "LOW" if risk_sc < 40 else ("MODERATE" if risk_sc < 70 else "HIGH")

    addon_badges = "".join(
        f'<span class="addon-badge">+{a["r"]}R @ {a["time"]}</span>' for a in add_ons
    ) or '<span class="grey" style="font-size:0.8rem">No add-ons</span>'

    _inst_html = (
        f'<div><div class="td-lbl">Instrument</div>'
        f'<div class="td-val orange">{active["instrument"]}</div></div>'
        if active.get("instrument") else ""
    )
    st.markdown(f"""
    <div class="active-card">
      <div class="active-badge">IN TRADE</div>
      <div class="trade-detail-row">
        {_inst_html}
        <div>
          <div class="td-lbl">Grade</div>
          <div class="td-val orange">{active['grade']}</div>
        </div>
        <div>
          <div class="td-lbl">R:R Target</div>
          <div class="td-val white">1 : {rr_target}</div>
        </div>
        <div>
          <div class="td-lbl">Implied Risk</div>
          <div class="td-val white">{active['implied_r']}R</div>
        </div>
        <div>
          <div class="td-lbl">Total at Risk</div>
          <div class="td-val {'red' if total_risk_r > active['implied_r'] else 'white'}">{total_risk_r:.2f}R</div>
        </div>
        <div>
          <div class="td-lbl">Win Probability</div>
          <div class="td-val white">{win_p*100:.1f}%</div>
        </div>
        <div>
          <div class="td-lbl">Started</div>
          <div class="td-val grey">{active['start_time']}</div>
        </div>
      </div>
      <div style="margin-top:10px">{addon_badges}</div>
      {f'<div style="margin-top:6px;font-size:0.8rem;color:#4b5a7a">{active["note"]}</div>' if active.get("note") else ""}
    </div>
    """, unsafe_allow_html=True)

    # ── Expected Value Bell Curve ──
    # Logic: EV = win_rate × R:R − (1 − win_rate) × 1
    #   μ = 0  → break-even trade
    #   right  → positive EV (good trade)
    #   left   → negative EV (bad trade)
    # The vertical line shows exactly where THIS trade's EV lands on the distribution.
    # Scale: z = EV / 1.5  (so EV=+3 → z≈+2σ, EV=−1.5 → z=−1σ)
    import math as _math
    def _npdf(v): return _math.exp(-0.5 * v * v) / _math.sqrt(2 * _math.pi)
    _peak = _npdf(0)

    _ev   = win_p * rr_target - (1 - win_p) * 1.0
    _z    = max(-3.2, min(3.2, _ev / 1.5))   # trade position on σ axis
    _ev_color = "#22c55e" if _ev > 0.3 else ("#f59e0b" if _ev > -0.2 else "#ef4444")

    # Build dense x array
    _bx = [i * 0.02 - 3.5 for i in range(351)]
    _by = [_npdf(v) for v in _bx]

    # Helper to get a segment of the curve within [lo, hi] (inclusive boundary points)
    def _seg(lo, hi):
        xs = [lo] + [v for v in _bx if lo < v < hi] + [hi]
        return xs, [_npdf(v) for v in xs]

    if _HAS_PLOTLY:
        _fig = _go.Figure()

        # Sigma band fills (colours matching standard stats diagram)
        # dark green tails: beyond ±3σ
        # brownish-red: ±1σ to ±3σ
        # dark blue centre: 0 to ±1σ
        _band_cfg = [
            (-3.5, -3.0, 'rgba(34,100,34,0.70)'),
            (-3.0, -2.0, 'rgba(34,100,34,0.70)'),
            (-2.0, -1.0, 'rgba(140,75,75,0.70)'),
            (-1.0,  0.0, 'rgba(45,79,181,0.80)'),
            ( 0.0,  1.0, 'rgba(45,79,181,0.80)'),
            ( 1.0,  2.0, 'rgba(140,75,75,0.70)'),
            ( 2.0,  3.0, 'rgba(34,100,34,0.70)'),
            ( 3.0,  3.5, 'rgba(34,100,34,0.70)'),
        ]
        for _lo, _hi, _fc in _band_cfg:
            _sx, _sy = _seg(_lo, _hi)
            _fig.add_trace(_go.Scatter(
                x=_sx + _sx[::-1], y=_sy + [0]*len(_sy),
                fill='toself', fillcolor=_fc,
                line=dict(width=0), showlegend=False, hoverinfo='skip',
            ))

        # Curve outline
        _fig.add_trace(_go.Scatter(x=_bx, y=_by,
            line=dict(color='rgba(226,232,240,0.6)', width=2),
            showlegend=False, hoverinfo='skip'))

        # σ boundary lines
        for _sv in [-3, -2, -1, 0, 1, 2, 3]:
            _fig.add_vline(x=_sv, line_dash='solid',
                line_color='rgba(255,255,255,0.12)', line_width=1)

        # THIS TRADE vertical line — bright, with arrow annotation
        _fig.add_vline(x=_z, line_dash='solid', line_color='#ffffff', line_width=2.5)

        # Percentage labels inside each band
        _pct_labels = [
            (-3.25, '0.1%'), (-2.5, '2.1%'), (-1.5, '13.6%'),
            (-0.5, '34.1%'), (0.5, '34.1%'), (1.5, '13.6%'),
            (2.5, '2.1%'),   (3.25, '0.1%'),
        ]
        _annots = []
        for _px, _pt in _pct_labels:
            _annots.append(dict(
                x=_px, y=_npdf(_px) * 0.55,
                text=_pt, showarrow=False,
                font=dict(color='rgba(255,255,255,0.85)', size=9),
                xanchor='center',
            ))

        # Trade label — above the line
        _ev_sign = "+" if _ev > 0 else ""
        _annots.append(dict(
            x=_z, y=_peak * 1.30,
            text=f'<b>This trade ({_grade})</b><br>Avg outcome: {_ev_sign}{_ev:.2f}R per trade',
            showarrow=True, arrowhead=2, arrowcolor='#ffffff',
            ax=0, ay=-32,
            font=dict(color=_ev_color, size=10),
            xanchor='center', bgcolor='rgba(14,18,32,0.90)',
            bordercolor=_ev_color, borderwidth=1, borderpad=4,
        ))

        _fig.update_layout(
            height=280, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=4, r=4, t=14, b=42),
            xaxis=dict(
                range=[-3.6, 3.6],
                tickvals=[-3, -2, -1, 0, 1, 2, 3],
                ticktext=['-3σ', '-2σ', '-1σ', 'μ=0', '+1σ', '+2σ', '+3σ'],
                tickfont=dict(color='#94a3b8', size=13, family='monospace'),
                showgrid=False, zeroline=False, color='#94a3b8',
                tickangle=0,
            ),
            yaxis=dict(visible=False, range=[-0.008, _peak * 1.65]),
            annotations=_annots,
        )
        st.plotly_chart(_fig, use_container_width=True, config={'displayModeBar': False})

    _wp_color = "#22c55e" if win_p >= 0.5 else ("#f59e0b" if win_p >= 0.35 else "#ef4444")
    st.markdown(
        f'<div style="display:flex;gap:20px;flex-wrap:wrap;font-size:0.68rem;color:#4b5a7a;'
        f'text-transform:uppercase;letter-spacing:.08em;margin:-4px 0 14px;padding:0 4px">'
        f'<span>Grade: <strong style="color:#e2e8f0">{_grade}</strong></span>'
        f'<span>Win Rate: <strong style="color:{_wp_color}">{win_p*100:.0f}%</strong></span>'
        f'<span>R:R: <strong style="color:#e2e8f0">1:{rr_target}</strong></span>'
        f'<span>Avg outcome per trade: <strong style="color:{_ev_color}">{_ev_sign}{_ev:.2f}R</strong></span>'
        f'<span>Exposure: <strong style="color:#e2e8f0">{total_risk_r:.2f}R</strong></span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Action buttons ──
    col_win, col_loss, col_be, col_add, col_edit, col_cancel = st.columns([2, 2, 2, 2, 2, 1])

    with col_win:
        if st.button("WIN", use_container_width=True, type="primary"):
            st.session_state["_outcome_pending"] = "win"
            st.rerun()
    with col_loss:
        if st.button("LOSS", use_container_width=True):
            st.session_state["_outcome_pending"] = "loss"
            st.rerun()
    with col_be:
        if st.button("BREAK EVEN", use_container_width=True):
            st.session_state["_outcome_pending"] = "be"
            st.rerun()
    with col_add:
        if st.button(f"+ Add {prefs['addon_r']}R", use_container_width=True):
            st.session_state["_outcome_pending"] = "addon"
            st.rerun()
    with col_edit:
        if st.button("Edit Trade", use_container_width=True):
            st.session_state["_outcome_pending"] = "edit"
            st.rerun()
    with col_cancel:
        if st.button("✕", use_container_width=True, help="Cancel trade (no log entry)"):
            session["active_trade"] = None
            _save_session(session)
            st.rerun()

    # ── EDIT form ──
    if st.session_state.get("_outcome_pending") == "edit":
        with st.form("edit_form"):
            st.markdown("**Edit Trade**")
            ec1, ec2, ec3 = st.columns([2, 2, 3])
            with ec1:
                grade_opts = list(prefs["grades"].keys())
                grade_lbls = [f"{k} ({prefs['grades'][k]['implied_r']}R)" for k in grade_opts]
                curr_idx   = grade_opts.index(active["grade"]) if active["grade"] in grade_opts else 0
                new_grade_lbl = st.selectbox("Grade", grade_lbls, index=curr_idx)
                new_grade = grade_opts[grade_lbls.index(new_grade_lbl)]
            with ec2:
                new_rr = st.number_input("R:R Target", min_value=0.1,
                                         value=float(active["rr_target"]),
                                         step=0.1, format="%.2f")
            with ec3:
                new_note = st.text_input("Note", value=active.get("note", ""))
            if st.form_submit_button("Save Changes", use_container_width=True):
                active["grade"]     = new_grade
                active["implied_r"] = prefs["grades"][new_grade]["implied_r"]
                active["rr_target"] = new_rr
                active["note"]      = new_note
                session["active_trade"] = active
                del st.session_state["_outcome_pending"]
                _save_session(session)
                st.rerun()

    # ── WIN form ──
    if st.session_state.get("_outcome_pending") == "win":
        with st.form("win_form"):
            st.markdown("**Win — Enter R:R achieved:**")
            wc1, wc2 = st.columns([3, 2])
            with wc1:
                rr_achieved = st.number_input("R:R achieved (from ATAS)", min_value=0.0,
                                              value=float(rr_target), step=0.1, format="%.2f")
            with wc2:
                actual_r = round(rr_achieved * active["implied_r"], 4)
                st.markdown(f"**Actual R: `+{actual_r:.4f}R`**")
            # Optional: verify via CSV
            st.markdown("---")
            _win_csv_ex = st.selectbox("CSV exchange format (optional)",
                                       ["Auto-detect"] + _EXCHANGES, key="win_csv_ex")
            _win_csv    = st.file_uploader("Verify via CSV (optional)", type=["csv"], key="win_csv")
            _win_csv_btn = st.form_submit_button("Pull R from CSV", use_container_width=True)
            if _win_csv_btn and _win_csv:
                _wp, _wf, _ws, _we = _parse_csv_last_close(_win_csv.read(), _win_csv_ex)
                if _we:
                    st.error(_we)
                elif _wp is not None:
                    _wr = round(_wp / one_r, 4)
                    st.success(f"CSV ({_ws}): Net PnL = ${_wp:+,.4f} → {_wr:+.4f}R")
            if st.form_submit_button("Confirm Win", use_container_width=True):
                _close_risk = _risk_score(active["rr_target"], len(active.get("add_ons", [])), active.get("grade", "AA"))
                trade = {**active, "outcome": "Win", "close_time": datetime.now().strftime("%H:%M:%S"),
                         "risk_tool": rr_achieved, "actual_r": actual_r,
                         "risk_score_close": _close_risk}
                completed.append(trade)
                _archive_trade(trade, session["session_date"])   # persist immediately
                # Fabio competition: track consecutive wins
                if mode == "Secret Sauce" and fabio_submode == "Competition Mode":
                    fabio_state["consecutive_wins"] += 1
                    session["fabio_state"] = fabio_state
                session["completed_trades"] = completed
                session["active_trade"]     = None
                del st.session_state["_outcome_pending"]
                _save_session(session)
                st.rerun()

    # ── LOSS form ──
    elif st.session_state.get("_outcome_pending") == "loss":
        _ex_cfg_loss = _load_ex_cfg()
        _is_okx = _ex_cfg_loss.get("exchange", "OKX") == "OKX"
        _pull_label = "Pull from API" if _is_okx else f"Pull from {_ex_cfg_loss.get('exchange','API')}"

        with st.form("loss_form"):
            st.markdown("**Loss — confirm R lost:**")
            lc1, lc2, lc3 = st.columns([2, 2, 2])
            with lc1:
                default_loss = -total_risk_r
                manual_r = st.number_input("Actual R (negative)", max_value=0.0,
                                           value=float(round(default_loss, 4)),
                                           step=0.01, format="%.4f")
            with lc2:
                pull_okx = st.form_submit_button(_pull_label, use_container_width=True)
            with lc3:
                confirm  = st.form_submit_button("Confirm Loss", use_container_width=True)

            # CSV upload (non-OKX or as fallback)
            st.markdown("---")
            _csv_ex_hint = st.selectbox("CSV exchange format",
                                        ["Auto-detect"] + _EXCHANGES,
                                        key="loss_csv_ex")
            _csv_file = st.file_uploader("Or upload exchange CSV", type=["csv"],
                                         key="loss_csv_upload")
            _csv_pull = st.form_submit_button("Parse CSV", use_container_width=True)

            if pull_okx:
                if not prefs.get("connection_enabled", False):
                    st.error("Enable connections first — kill switch is OFF")
                    net_pnl, fee, err = None, None, "offline"
                else:
                    net_pnl, fee, err = _fetch_last_close()
                if err and err != "offline":
                    st.error(f"OKX pull failed: {err}")
                elif net_pnl is not None:
                    actual_r_okx = round(net_pnl / one_r, 4)
                    st.success(f"OKX: Net PnL = ${net_pnl:,.4f} (fee ${fee:,.4f}) → {actual_r_okx:.4f}R")
                    _close_risk = _risk_score(active["rr_target"], len(active.get("add_ons", [])), active.get("grade", "AA"))
                    trade = {**active, "outcome": "Loss", "close_time": datetime.now().strftime("%H:%M:%S"),
                             "risk_tool": actual_r_okx, "actual_r": actual_r_okx,
                             "okx_pnl": net_pnl, "okx_fee": fee,
                             "risk_score_close": _close_risk}
                    completed.append(trade)
                    _archive_trade(trade, session["session_date"])   # persist immediately
                    if mode == "Secret Sauce" and fabio_submode == "Competition Mode":
                        fabio_state["consecutive_wins"] = 0
                        session["fabio_state"] = fabio_state
                    session["completed_trades"] = completed
                    session["active_trade"]     = None
                    del st.session_state["_outcome_pending"]
                    _save_session(session)
                    st.rerun()

            if _csv_pull:
                if _csv_file is None:
                    st.error("No CSV file uploaded.")
                else:
                    _cp, _cf, _cs, _ce = _parse_csv_last_close(_csv_file.read(), _csv_ex_hint)
                    if _ce:
                        st.error(f"CSV parse failed: {_ce}")
                    elif _cp is not None:
                        _ar_csv = round(_cp / one_r, 4)
                        st.success(f"CSV ({_cs}): Net PnL = ${_cp:+,.4f} (fee ${_cf:,.4f}) → {_ar_csv:.4f}R")
                        _close_risk = _risk_score(active["rr_target"], len(active.get("add_ons", [])), active.get("grade", "AA"))
                        trade = {**active, "outcome": "Loss", "close_time": datetime.now().strftime("%H:%M:%S"),
                                 "risk_tool": _ar_csv, "actual_r": _ar_csv,
                                 "csv_pnl": _cp, "csv_fee": _cf, "risk_score_close": _close_risk}
                        completed.append(trade)
                        _archive_trade(trade, session["session_date"])
                        if mode == "Secret Sauce" and fabio_submode == "Competition Mode":
                            fabio_state["consecutive_wins"] = 0
                            session["fabio_state"] = fabio_state
                        session["completed_trades"] = completed
                        session["active_trade"]     = None
                        del st.session_state["_outcome_pending"]
                        _save_session(session)
                        st.rerun()

            if confirm:
                _close_risk = _risk_score(active["rr_target"], len(active.get("add_ons", [])), active.get("grade", "AA"))
                trade = {**active, "outcome": "Loss", "close_time": datetime.now().strftime("%H:%M:%S"),
                         "risk_tool": manual_r, "actual_r": manual_r,
                         "risk_score_close": _close_risk}
                completed.append(trade)
                _archive_trade(trade, session["session_date"])   # persist immediately
                if mode == "Secret Sauce" and fabio_submode == "Competition Mode":
                    fabio_state["consecutive_wins"] = 0
                    session["fabio_state"] = fabio_state
                session["completed_trades"] = completed
                session["active_trade"]     = None
                del st.session_state["_outcome_pending"]
                _save_session(session)
                st.rerun()

    # ── BREAK EVEN ──
    elif st.session_state.get("_outcome_pending") == "be":
        with st.form("be_form"):
            st.markdown("**Break Even — log at 0R?**")
            if st.form_submit_button("Confirm Break Even", use_container_width=True):
                _close_risk = _risk_score(active["rr_target"], len(active.get("add_ons", [])), active.get("grade", "AA"))
                trade = {**active, "outcome": "BE", "close_time": datetime.now().strftime("%H:%M:%S"),
                         "risk_tool": 0.0, "actual_r": 0.0,
                         "risk_score_close": _close_risk}
                completed.append(trade)
                _archive_trade(trade, session["session_date"])   # persist immediately
                session["completed_trades"] = completed
                session["active_trade"]     = None
                del st.session_state["_outcome_pending"]
                _save_session(session)
                st.rerun()

    # ── ADD-ON ──
    elif st.session_state.get("_outcome_pending") == "addon":
        with st.form("addon_form"):
            st.markdown(f"**Add {prefs['addon_r']}R to position?**")
            new_rr = st.number_input("Update R:R target (optional)",
                                     min_value=0.1, value=float(rr_target), step=0.1, format="%.1f")
            if st.form_submit_button("Confirm Add-on", use_container_width=True):
                active["add_ons"].append({
                    "r":    prefs["addon_r"],
                    "time": datetime.now().strftime("%H:%M:%S"),
                })
                active["rr_target"] = new_rr
                session["active_trade"] = active
                del st.session_state["_outcome_pending"]
                _save_session(session)
                st.rerun()

    # Cancel pending action
    if st.session_state.get("_outcome_pending") and \
       st.button("Cancel", key="cancel_pending"):
        del st.session_state["_outcome_pending"]
        st.rerun()

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
    st.dataframe(styled, use_container_width=True, hide_index=True)

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
            st.dataframe(_dstyled, use_container_width=True, hide_index=True)

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
        use_container_width=True,
    )

st.markdown("<div style='height:60px'></div>", unsafe_allow_html=True)
