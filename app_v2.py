"""
Primeval Trading Companion v2.0
Session R:R logger · Active trade management · Pyramiding · Risk indicator
The Primeval Session Loop: TUNE → MAP → HUNT → CLEAR
State System: PRIME · GRIND · STATIC
Modes: Standard | Secret Sauce Conservative | Secret Sauce Competition
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
        "completed":      False,
        "grade":          None,
        "multiplier":     1.0,
        "score":          None,
        "description":    "",
        "color":          "white",
        # v2 — Primeval State System
        "session_state":  None,   # PRIME / GRIND / STATIC
        "mental_state":   None,   # PRIME / GRIND / STATIC (self-assessed)
        "tactical_state": None,   # PRIME / GRIND / STATIC (self-assessed)
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
    "Normal Day Variation",
    "Inside Day",
    "Trend Day (p profile)",
    "Trend Day (b profile)",
    "Double Distribution",
    "Balanced (Merged)",
]

_PRICE_LOCATIONS = [
    "Within VA",
    "Between VA absolute and VAH/VAL",
    "Outside VA",
]

def _derive_scenario(price_location, outside_va_return):
    """Returns (scenario_num, strategy) from price location + outside VA return question."""
    if price_location == "Within VA":
        return 1, "Mean Reversion"
    elif price_location == "Between VA absolute and VAH/VAL":
        return 2, "Mean Reversion"
    elif price_location == "Outside VA":
        if outside_va_return:
            return 3, "Mean Reversion"
        else:
            return 4, "Breakout"
    return 1, "Mean Reversion"

def _schematic_for_market_state(market_state):
    """Return path to schematic PNG for a given market state."""
    _assets_dir = os.path.join(_APP_DIR, "assets")
    p1 = os.path.join(_assets_dir, "pbd_schematic_p1.png")
    p2 = os.path.join(_assets_dir, "pbd_schematic_p2.png")
    p3 = os.path.join(_assets_dir, "pbd_schematic_p3.png")
    if market_state in ("Normal Day", "Normal Day Variation", "Inside Day"):
        return p1
    elif market_state in ("Trend Day (p profile)", "Trend Day (b profile)", "Double Distribution", "Balanced (Merged)"):
        return p2
    return None

_SCENARIO_SCHEMATIC = os.path.join(_APP_DIR, "assets", "pbd_schematic_p3.png")

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

# ─── TUNE-IN DATA (v2 — Primeval Session Loop: TUNE phase) ───────────────────
_MR_QUESTIONS = [
    ("sleep",     "Sleep quality last night",         ["Excellent", "Good", "Poor", "Terrible"],       [3, 2, 1, 0]),
    ("energy",    "Energy & focus level",              ["High", "Normal", "Low", "Depleted"],           [3, 2, 1, 0]),
    ("sick",      "Feeling sick or hungover?",         ["No", "Slightly", "Yes"],                       [3, 1, 0]),
    ("emotional", "Emotional state",                   ["Centred", "Slightly off", "Off", "Triggered"], [3, 2, 1, 0]),
    ("distract",  "Outside distractions today",        ["None", "Minor", "Significant", "Major"],       [3, 2, 1, 0]),
    ("clarity",   "Mental clarity & decision-making",  ["Sharp", "Normal", "Foggy", "Scattered"],       [3, 2, 1, 0]),
]

# v2 — Self-assessed state inputs (Mental Game + Tactical Readiness)
# Options map to: PRIME / GRIND / STATIC
_STATE_OPTIONS = {
    "mental": {
        "label": "Mental game right now",
        "choices": [
            ("PRIME",  "PRIME — Calm, focused, trusting the plan"),
            ("GRIND",  "GRIND — Functional but not fully locked in"),
            ("STATIC", "STATIC — Off, scattered, emotionally reactive"),
        ],
    },
    "tactical": {
        "label": "Tactical readiness",
        "choices": [
            ("PRIME",  "PRIME — Clear scenario, levels marked, plan set"),
            ("GRIND",  "GRIND — Plan exists but gaps or uncertainty"),
            ("STATIC", "STATIC — Unprepared, no clear read on market"),
        ],
    },
}

# (min_score, grade, multiplier, css_class, description)
_MR_GRADES = [
    (16, "A",  1.00, "mr-grade-A",  "Press hard — ideal conditions"),
    (13, "B+", 0.80, "mr-grade-Bp", "Normal — slight caution"),
    (10, "B",  0.60, "mr-grade-B",  "Reduced sizing — stay selective"),
    ( 7, "C",  0.30, "mr-grade-C",  "Size way down — consider sitting out"),
    ( 4, "D",  0.10, "mr-grade-D",  "Defensive only — protect capital"),
    ( 0, "F",  0.00, "mr-grade-F",  "Do not trade today"),
]

# v2 — Map MRC grade to Primeval Session State
def _grade_to_state(grade):
    return {"A": "PRIME", "B+": "GRIND", "B": "GRIND"}.get(grade, "STATIC")

# v2 — Derive final session state from grade + mental + tactical self-assessment
def _derive_session_state(grade, mental, tactical):
    """STATIC wins any tie. PRIME requires grade A + both self-ratings PRIME."""
    grade_state = _grade_to_state(grade)
    if grade_state == "STATIC" or mental == "STATIC" or tactical == "STATIC":
        return "STATIC"
    if grade_state == "PRIME" and mental == "PRIME" and tactical == "PRIME":
        return "PRIME"
    return "GRIND"

_SESSION_STATE_META = {
    "PRIME":  {"css": "state-prime",  "color": "#22c55e", "desc": "Peak state. Sharp, calm, trusting the plan. Full throttle."},
    "GRIND":  {"css": "state-grind",  "color": "#f59e0b", "desc": "Functional. Proceed with caution and reduced size."},
    "STATIC": {"css": "state-static", "color": "#ef4444", "desc": "Signal blocked. Protect capital. Consider sitting out."},
}

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
    page_title="Primeval Trading Companion v2",
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

/* ── Primeval State System (v2) ────────────────────────────────────────────── */
.state-prime  { color: #22c55e; }
.state-grind  { color: #f59e0b; }
.state-static { color: #ef4444; }

.state-badge {
    display: flex; align-items: center; justify-content: center; gap: 14px;
    border-radius: 12px; padding: 16px 24px; margin: 12px 0;
}
.state-badge-prime  { background: #051a0a; border: 2px solid #22c55e; }
.state-badge-grind  { background: #1a1200; border: 2px solid #f59e0b; }
.state-badge-static { background: #1a0505; border: 2px solid #ef4444; }

.state-label {
    font-size: 2.6rem; font-weight: 900; letter-spacing: 0.08em;
    line-height: 1; text-transform: uppercase;
}
.state-meta { text-align: left; }
.state-meta-name {
    font-size: 0.6rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .14em; color: #4b5a7a; margin-bottom: 3px;
}
.state-meta-desc { font-size: 0.8rem; color: #94a3b8; line-height: 1.4; }

/* Session loop phase labels */
.loop-phase-row {
    display: flex; gap: 6px; margin: 10px 0 18px; justify-content: center;
}
.loop-phase {
    flex: 1; max-width: 120px; padding: 8px 10px; text-align: center;
    background: #0a0e18; border: 1px solid #1a2238; border-radius: 8px;
    font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .1em; color: #2a3a5a;
}
.loop-phase.lp-active {
    border-color: #3b82f6; color: #93c5fd;
    background: #05101a;
}
.loop-phase.lp-done {
    border-color: #16a34a; color: #4ade80;
    background: #051a0a;
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
    st.markdown("### TUNE — Pre-Session")
    if not new_safe_mode:
        _mr_enabled_current = prefs.get("morning_report_enabled", False)
        new_mr_enabled = st.checkbox(
            "Enable pre-session calibration",
            value=_mr_enabled_current,
            help="Set your PRIME / GRIND / STATIC state — adjusts today's effective loss limit",
        )
        if new_mr_enabled != _mr_enabled_current:
            prefs["morning_report_enabled"] = new_mr_enabled
            _save_prefs(prefs)
    else:
        new_mr_enabled = prefs.get("morning_report_enabled", False)
        st.caption("TUNE phase — required before MAP and HUNT")
    # Show compact result in sidebar when completed
    _sb_mr = session.get("morning_report", {})
    if (new_mr_enabled or new_safe_mode) and _sb_mr.get("completed"):
        _sb_grade = _sb_mr.get("grade", "?")
        _sb_mult  = _sb_mr.get("multiplier", 1.0)
        _sb_state = _sb_mr.get("session_state", "")
        _sb_desc  = _sb_mr.get("description", "")
        if _sb_state:
            st.caption(f"State: **{_sb_state}** · Grade {_sb_grade} · {_sb_mult*100:.0f}% limit · {_sb_desc}")
        else:
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
    'color:#e2e8f0;margin:0 0 6px 0;line-height:1;text-align:center">Primeval Trading</h1>'
    '<div style="text-align:center;font-size:0.62rem;color:#2a3a5a;letter-spacing:.18em;'
    'font-weight:700;text-transform:uppercase;margin-bottom:18px">v2.0 · The Session Loop</div>',
    unsafe_allow_html=True,
)

# ─── SESSION LOOP PHASE BAR ───────────────────────────────────────────────────
# Determine current loop phase for display
_loop_tune     = cl_phase1 if safe_mode else True           # TUNE complete once MRC done
_loop_map      = cl_any_ready if safe_mode else True        # MAP complete once assets ready
_loop_hunt     = bool(active_trades) or bool(completed)     # HUNT = any trade activity
_loop_clear    = not active_trades and bool(completed)      # CLEAR = all trades closed

def _lp_cls(done, active):
    if done:    return "lp-done"
    if active:  return "lp-active"
    return ""

_lp_tune_cls  = _lp_cls(_loop_tune,  not _loop_tune)
_lp_map_cls   = _lp_cls(_loop_map,   _loop_tune and not _loop_map)
_lp_hunt_cls  = _lp_cls(_loop_clear, _loop_map  and not _loop_hunt)
_lp_clear_cls = _lp_cls(_loop_clear, _loop_hunt and not active_trades and bool(completed))

st.markdown(
    f'<div class="loop-phase-row">'
    f'<div class="loop-phase {_lp_tune_cls}">TUNE</div>'
    f'<div class="loop-phase {_lp_map_cls}">MAP</div>'
    f'<div class="loop-phase {_lp_hunt_cls}">HUNT</div>'
    f'<div class="loop-phase {_lp_clear_cls}">CLEAR</div>'
    f'</div>',
    unsafe_allow_html=True,
)

# ─── SESSION STATE BADGE (v2) — shown once Tune-In is complete ───────────────
_ss_val = morning_report.get("session_state")
if _ss_val and _ss_val in _SESSION_STATE_META:
    _ss_meta  = _SESSION_STATE_META[_ss_val]
    _ss_cls   = _ss_meta["css"]
    _ss_color = _ss_meta["color"]
    _ss_desc  = _ss_meta["desc"]
    _mental   = morning_report.get("mental_state", "—")
    _tactical = morning_report.get("tactical_state", "—")
    st.markdown(
        f'<div class="state-badge state-badge-{_ss_val.lower()}">'
        f'<div class="state-label {_ss_cls}">{_ss_val}</div>'
        f'<div class="state-meta">'
        f'<div class="state-meta-name">Today\'s State</div>'
        f'<div class="state-meta-desc">{_ss_desc}</div>'
        f'<div style="margin-top:6px;font-size:0.62rem;color:#4b5a7a;">'
        f'Mental: <span style="color:{_ss_color};font-weight:700">{_mental}</span>'
        f' &nbsp;·&nbsp; Tactical: <span style="color:{_ss_color};font-weight:700">{_tactical}</span>'
        f'</div></div></div>',
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

    # ── TUNE phase decision box ──
    st.markdown('<div class="decision-box">', unsafe_allow_html=True)
    st.markdown('<div class="decision-hdg">TUNE · Pre-Session Calibration</div>', unsafe_allow_html=True)
    if cl_phase1:
        _ss_done    = morning_report.get("session_state")
        _ss_meta_d  = _SESSION_STATE_META.get(_ss_done, {})
        _ss_css_d   = _ss_meta_d.get("css", "white")
        _ss_desc_d  = _ss_meta_d.get("desc", "")
        _p1_html = (
            f'<div style="{_CARD_STYLE.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge-done" style="margin:0 auto">✓ Tune-In Complete</div>'
            f'<div style="{_LBL}">Calibration Grade</div>'
            f'<div style="font-size:2.2rem;font-weight:800;line-height:1" class="{_mr_css}">{_mr_g}</div>'
            f'<div style="font-size:0.75rem;color:#94a3b8">{_mr_desc}</div>'
            + (
                f'<div style="font-size:1.5rem;font-weight:900;letter-spacing:0.08em;margin-top:8px" class="{_ss_css_d}">{_ss_done}</div>'
                f'<div style="font-size:0.72rem;color:#94a3b8">{_ss_desc_d}</div>'
                if _ss_done else ""
            ) +
            f'<div style="{_LBL};margin-top:4px">Score <span style="color:#e2e8f0;font-weight:700">{_mr_sc}/18</span>'
            f' · {_limit_line}</div>'
            f'</div>'
        )
        st.markdown(_p1_html, unsafe_allow_html=True)
        _p1_rc, _ = st.columns([1, 5])
        with _p1_rc:
            if st.button("↩ Re-Tune", key="redo_p1_sm", use_container_width=True):
                session["morning_report"] = {**_SESSION_DEFAULTS["morning_report"]}
                checklist["phase1_complete"] = False
                checklist["assets"] = []
                session["checklist"] = checklist
                _save_session(session)
                st.rerun()
        if _mr_g == "F":
            st.markdown(
                '<div class="mr-no-trade">STATIC — DO NOT TRADE TODAY · Protect capital.</div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            f'<div style="{_CARD_STYLE.format(bc="#3b82f6")}">'
            f'<div class="checklist-badge" style="margin:0 auto">TUNE — Pre-Session</div>'
            f'<div style="font-size:0.88rem;color:#4b5a7a;margin-top:8px">○ Calibrate your state before the session opens</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        with st.form("morning_report_form"):
            st.markdown(
                '<div style="font-size:0.65rem;color:#4b5a7a;text-transform:uppercase;'
                'letter-spacing:.1em;font-weight:700;margin-bottom:10px">Physical & Mental Calibration</div>',
                unsafe_allow_html=True,
            )
            qc1, qc2 = st.columns(2)
            for i, (qkey, qlabel, qopts, _) in enumerate(_MR_QUESTIONS):
                with (qc1 if i < 3 else qc2):
                    st.selectbox(qlabel, qopts, key=f"mr_q_{qkey}")
            st.divider()
            st.markdown(
                '<div style="font-size:0.65rem;color:#4b5a7a;text-transform:uppercase;'
                'letter-spacing:.1em;font-weight:700;margin-bottom:10px">State Self-Assessment</div>',
                unsafe_allow_html=True,
            )
            sc1, sc2 = st.columns(2)
            with sc1:
                _mental_opts  = [c[1] for c in _STATE_OPTIONS["mental"]["choices"]]
                _mental_vals  = [c[0] for c in _STATE_OPTIONS["mental"]["choices"]]
                _mental_sel   = st.selectbox(
                    _STATE_OPTIONS["mental"]["label"],
                    _mental_opts, key="mr_mental",
                )
            with sc2:
                _tact_opts = [c[1] for c in _STATE_OPTIONS["tactical"]["choices"]]
                _tact_vals = [c[0] for c in _STATE_OPTIONS["tactical"]["choices"]]
                _tact_sel  = st.selectbox(
                    _STATE_OPTIONS["tactical"]["label"],
                    _tact_opts, key="mr_tactical",
                )
            if st.form_submit_button("Set My State →", use_container_width=True):
                _total = 0
                for qkey, _, qopts, qscores in _MR_QUESTIONS:
                    sel = st.session_state.get(f"mr_q_{qkey}", qopts[0])
                    idx = qopts.index(sel) if sel in qopts else 0
                    _total += qscores[idx]
                _grade, _mult, _css, _desc = _mr_grade(_total)
                # Map dropdown display text back to state keys
                _mental_raw   = st.session_state.get("mr_mental", _mental_opts[0])
                _tactical_raw = st.session_state.get("mr_tactical", _tact_opts[0])
                _mental_key   = _mental_vals[_mental_opts.index(_mental_raw)] if _mental_raw in _mental_opts else "GRIND"
                _tactical_key = _tact_vals[_tact_opts.index(_tactical_raw)] if _tactical_raw in _tact_opts else "GRIND"
                _sess_state   = _derive_session_state(_grade, _mental_key, _tactical_key)
                session["morning_report"] = {
                    "completed": True, "grade": _grade, "multiplier": _mult,
                    "score": _total, "description": _desc, "color": _css,
                    "session_state":  _sess_state,
                    "mental_state":   _mental_key,
                    "tactical_state": _tactical_key,
                }
                _save_session(session)
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)  # /decision-box TUNE

    if cl_phase1:
        # ── Asset management box ──
        st.markdown('<div class="decision-box">', unsafe_allow_html=True)
        st.markdown('<div class="decision-hdg">MAP — Assets in Focus Today</div>', unsafe_allow_html=True)
        _all_instruments = ["SOL", "BTC", "ETH", "SUI", "MNQ", "MES"]
        _used_names = [a["name"] for a in cl_assets]
        _avail = [x for x in _all_instruments if x not in _used_names]
        if _avail:
            _ac1, _ac2, _ = st.columns([1.5, 1, 3])
            with _ac1:
                _new_asset_name = st.selectbox("Asset", _avail, key="new_asset_sel", label_visibility="collapsed")
            with _ac2:
                if st.button("+ Add Asset", key="add_asset_btn", use_container_width=True):
                    cl_assets.append({**_ASSET_DEFAULTS, "name": _new_asset_name})
                    checklist["assets"] = cl_assets
                    session["checklist"] = checklist
                    _save_session(session)
                    st.rerun()

        if not cl_assets:
            st.markdown(
                '<div class="checklist-lock" style="margin-top:8px">Add at least one asset to begin market analysis.</div>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)  # /decision-box Assets

        # ── Per-asset boxes ──
        for _ai, _asset in enumerate(cl_assets):
            _aname  = _asset["name"]
            _a_p2c  = _asset.get("phase2_complete", False)
            _a_p3c  = _asset.get("phase3_complete", False)
            _a_ready = _a_p2c and _a_p3c
            _status = "✓ Ready to Hunt" if _a_ready else ("MAP 3 pending" if _a_p2c else "MAP pending")
            _border_col = "#22c55e" if _a_ready else ("#3b82f6" if _a_p2c else "#1e2a45")

            st.markdown(
                f'<div class="decision-box" style="border-color:{_border_col}">',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="decision-hdg">{_aname} — {_status}'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Remove asset button
            _rmcol, _ = st.columns([1, 5])
            with _rmcol:
                if st.button(f"✕ Remove {_aname}", key=f"rm_{_ai}", type="secondary"):
                    cl_assets.pop(_ai)
                    checklist["assets"] = cl_assets
                    session["checklist"] = checklist
                    _save_session(session)
                    st.rerun()

            # ── Phase 2 ──
            st.markdown('<div class="decision-box" style="margin:8px 0">', unsafe_allow_html=True)
            st.markdown(
                f'<div class="decision-hdg">MAP · Market Context · {_aname}'
                f'<br><span style="font-size:0.62rem;font-weight:500;letter-spacing:0;'
                f'text-transform:none;color:#4b5a7a">Based on PREVIOUS day\'s profile</span></div>',
                unsafe_allow_html=True,
            )
            if not _a_p2c:
                with st.form(f"p2_{_ai}"):
                    _fc1, _fc2 = st.columns(2)
                    with _fc1:
                        _p2_state = st.selectbox("Previous day type", _MARKET_STATES, key=f"p2s_{_ai}")
                        _p2_d = st.radio("Volume 'D' profile on 30M?", ["Yes", "No"], key=f"p2d_{_ai}", horizontal=True)
                        _p2_ib = st.radio("Initial Balance size", ["Large", "Small"], key=f"p2ib_{_ai}", horizontal=True)
                    with _fc2:
                        _p2_loc = st.radio("Price location vs prev day VA", _PRICE_LOCATIONS, key=f"p2l_{_ai}")
                        if st.session_state.get(f"p2l_{_ai}", "") == "Outside VA":
                            _p2_ret_raw = st.radio(
                                "Price returned to VAH/VAL and resided 15–30 min?",
                                ["Yes", "No"], key=f"p2r_{_ai}", horizontal=True,
                            )
                            _p2_ret = _p2_ret_raw == "Yes"
                        else:
                            _p2_ret = False

                    # Live scenario preview
                    _loc_now = st.session_state.get(f"p2l_{_ai}", _PRICE_LOCATIONS[0])
                    _ret_now = st.session_state.get(f"p2r_{_ai}", "No") == "Yes" if _loc_now == "Outside VA" else False
                    _sc_now, _st_now = _derive_scenario(_loc_now, _ret_now)
                    _sc_col = "#22c55e" if _st_now == "Mean Reversion" else "#f97316"
                    st.markdown(
                        f'<div style="padding:8px 0 4px">'
                        f'<span style="font-size:0.6rem;color:#4b5a7a;text-transform:uppercase;'
                        f'letter-spacing:.08em;font-weight:600">→ Scenario {_sc_now} · Strategy: </span>'
                        f'<span style="font-size:1.1rem;font-weight:900;color:{_sc_col}">{_st_now.upper()}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    # Schematics
                    _ms_now = st.session_state.get(f"p2s_{_ai}", _MARKET_STATES[0])
                    _sch1 = _schematic_for_market_state(_ms_now)
                    _im_c1, _im_c2 = st.columns(2)
                    if _sch1 and os.path.exists(_sch1):
                        with _im_c1:
                            st.image(_sch1, caption="Day type reference", use_container_width=True)
                    if os.path.exists(_SCENARIO_SCHEMATIC):
                        with _im_c2:
                            st.image(_SCENARIO_SCHEMATIC, caption="Trade scenarios", use_container_width=True)

                    if st.form_submit_button(f"Confirm Phase 2 for {_aname} →", use_container_width=True):
                        _loc_s = st.session_state.get(f"p2l_{_ai}", _PRICE_LOCATIONS[0])
                        _ret_s = st.session_state.get(f"p2r_{_ai}", "No") == "Yes" if _loc_s == "Outside VA" else False
                        _sc_f, _st_f = _derive_scenario(_loc_s, _ret_s)
                        cl_assets[_ai]["phase2"] = {
                            "market_state":      st.session_state.get(f"p2s_{_ai}", _MARKET_STATES[0]),
                            "d_profile":         st.session_state.get(f"p2d_{_ai}", "Yes"),
                            "ib_size":           st.session_state.get(f"p2ib_{_ai}", "Large"),
                            "price_location":    _loc_s,
                            "outside_va_return": _ret_s,
                            "scenario":          _sc_f,
                            "strategy":          _st_f,
                        }
                        cl_assets[_ai]["phase2_complete"] = True
                        checklist["assets"] = cl_assets
                        session["checklist"] = checklist
                        _save_session(session)
                        st.rerun()
            else:
                _p2d = _asset.get("phase2", {})
                _s_c = "#22c55e" if _p2d.get("strategy") == "Mean Reversion" else "#f97316"
                st.markdown(
                    f'<div class="checklist-badge-done" style="display:inline-block;margin:4px 0 6px">'
                    f'✓ Phase 2 — {_aname}</div><br>'
                    f'<span style="font-size:0.75rem;color:#94a3b8">'
                    f'Day: <strong>{_p2d.get("market_state","?")}</strong> · '
                    f'Loc: <strong>{_p2d.get("price_location","?")}</strong> · '
                    f'Scenario <strong>{_p2d.get("scenario","?")}</strong> · '
                    f'Strategy: <strong style="color:{_s_c}">{_p2d.get("strategy","?")}</strong>'
                    f'</span>',
                    unsafe_allow_html=True,
                )
                if st.button(f"↩ Redo Phase 2 — {_aname}", key=f"rdop2_{_ai}", type="secondary"):
                    cl_assets[_ai]["phase2"] = {}
                    cl_assets[_ai]["phase2_complete"] = False
                    cl_assets[_ai]["phase3_complete"] = False
                    checklist["assets"] = cl_assets
                    session["checklist"] = checklist
                    _save_session(session)
                    st.rerun()

            st.markdown('</div>', unsafe_allow_html=True)  # /decision-box MAP Context

            # ── MAP · Key Levels ──
            if _a_p2c:
                st.markdown('<div class="decision-box" style="margin:8px 0">', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="decision-hdg">MAP · Key Levels · {_aname}</div>',
                    unsafe_allow_html=True,
                )
                if not _a_p3c:
                    with st.form(f"p3_{_ai}"):
                        st.checkbox(f"Key S/R levels marked for {_aname}", key=f"p3sr_{_ai}")
                        st.checkbox(f"Trend lines drawn for {_aname}", key=f"p3tl_{_ai}")
                        st.checkbox(f"Price alerts set for {_aname}", key=f"p3al_{_ai}")

                        # News events — shared, shown only until checked
                        _news = checklist.get("news", {})
                        if not _news.get("checked"):
                            st.markdown('<hr style="border-color:#1a2238;margin:10px 0">', unsafe_allow_html=True)
                            st.markdown(
                                '<div style="font-size:0.62rem;color:#4b5a7a;text-transform:uppercase;'
                                'letter-spacing:.1em;font-weight:600;margin-bottom:6px">'
                                'Catalyst Check (shared — all assets)</div>',
                                unsafe_allow_html=True,
                            )
                            st.radio("Major scheduled event today?", ["No", "Yes"], key=f"news_yn_{_ai}", horizontal=True)
                            if st.session_state.get(f"news_yn_{_ai}", "No") == "Yes":
                                _nc1, _nc2 = st.columns(2)
                                with _nc1:
                                    st.text_input("Event name", placeholder="e.g. CPI, FOMC", key=f"news_nm_{_ai}")
                                with _nc2:
                                    st.text_input("Time (EST)", placeholder="e.g. 08:30", key=f"news_tm_{_ai}")

                        if st.form_submit_button(f"Lock MAP for {_aname} →", use_container_width=True):
                            if not (st.session_state.get(f"p3sr_{_ai}") and
                                    st.session_state.get(f"p3tl_{_ai}") and
                                    st.session_state.get(f"p3al_{_ai}")):
                                st.error("Check all three level items to proceed.")
                            else:
                                cl_assets[_ai]["phase3"] = {"sr_levels": True, "trend_lines": True, "price_alerts": True}
                                cl_assets[_ai]["phase3_complete"] = True
                                checklist["assets"] = cl_assets
                                _news_now = checklist.get("news", {})
                                if not _news_now.get("checked"):
                                    _hn = st.session_state.get(f"news_yn_{_ai}", "No") == "Yes"
                                    checklist["news"] = {
                                        "checked":    True,
                                        "has_news":   _hn,
                                        "event_name": st.session_state.get(f"news_nm_{_ai}", "") if _hn else "",
                                        "event_time": st.session_state.get(f"news_tm_{_ai}", "") if _hn else "",
                                    }
                                    cl_news_done = True
                                session["checklist"] = checklist
                                _save_session(session)
                                st.rerun()
                else:
                    st.markdown(
                        f'<div class="checklist-badge-done" style="display:inline-block;margin-bottom:6px">'
                        f'✓ MAP Locked — {_aname}</div><br>'
                        f'<span style="font-size:0.75rem;color:#94a3b8">✓ S/R levels · ✓ Trend lines · ✓ Price alerts</span>',
                        unsafe_allow_html=True,
                    )
                    if st.button(f"↩ Re-Map {_aname}", key=f"rdop3_{_ai}", type="secondary"):
                        cl_assets[_ai]["phase3_complete"] = False
                        checklist["assets"] = cl_assets
                        session["checklist"] = checklist
                        _save_session(session)
                        st.rerun()

                st.markdown('</div>', unsafe_allow_html=True)  # /decision-box MAP Levels

            st.markdown('</div>', unsafe_allow_html=True)  # /decision-box per-asset

        # ── News event warning banner ──
        _news_data = checklist.get("news", {})
        if _news_data.get("has_news"):
            st.markdown(
                f'<div class="warn-banner">⚠️ News event today: <strong>{_news_data.get("event_name","?")}</strong>'
                f' at <strong>{_news_data.get("event_time","?")}</strong> — trade cautiously around this window.</div>',
                unsafe_allow_html=True,
            )

elif mr_enabled:
    # ── Standalone Tune-In (no safe mode) ──
    st.markdown("""
    <div class="sec-hdr">
      <div class="sec-line"></div>
      <div class="sec-title">TUNE — Pre-Session Calibration</div>
      <div class="sec-line"></div>
    </div>""", unsafe_allow_html=True)

    if not morning_report.get("completed"):
        st.markdown(
            '<div class="info-banner" style="margin-bottom:12px">'
            'Calibrate your physical, mental, and tactical state before the session opens. '
            'Your state determines your daily risk allowance.</div>',
            unsafe_allow_html=True,
        )
        with st.form("morning_report_form"):
            st.markdown(
                '<div style="font-size:0.65rem;color:#4b5a7a;text-transform:uppercase;'
                'letter-spacing:.1em;font-weight:700;margin-bottom:10px">Physical & Mental Calibration</div>',
                unsafe_allow_html=True,
            )
            qc1, qc2 = st.columns(2)
            for i, (qkey, qlabel, qopts, _) in enumerate(_MR_QUESTIONS):
                with (qc1 if i < 3 else qc2):
                    st.selectbox(qlabel, qopts, key=f"mr_q_{qkey}")
            st.divider()
            st.markdown(
                '<div style="font-size:0.65rem;color:#4b5a7a;text-transform:uppercase;'
                'letter-spacing:.1em;font-weight:700;margin-bottom:10px">State Self-Assessment</div>',
                unsafe_allow_html=True,
            )
            sc1, sc2 = st.columns(2)
            with sc1:
                _sa_mental_opts = [c[1] for c in _STATE_OPTIONS["mental"]["choices"]]
                _sa_mental_vals = [c[0] for c in _STATE_OPTIONS["mental"]["choices"]]
                _sa_mental_sel  = st.selectbox(_STATE_OPTIONS["mental"]["label"], _sa_mental_opts, key="mr_mental_sa")
            with sc2:
                _sa_tact_opts = [c[1] for c in _STATE_OPTIONS["tactical"]["choices"]]
                _sa_tact_vals = [c[0] for c in _STATE_OPTIONS["tactical"]["choices"]]
                _sa_tact_sel  = st.selectbox(_STATE_OPTIONS["tactical"]["label"], _sa_tact_opts, key="mr_tactical_sa")
            _mr_submitted = st.form_submit_button("Set My State →", use_container_width=True)
            if _mr_submitted:
                _total = 0
                for qkey, _, qopts, qscores in _MR_QUESTIONS:
                    sel = st.session_state.get(f"mr_q_{qkey}", qopts[0])
                    idx = qopts.index(sel) if sel in qopts else 0
                    _total += qscores[idx]
                _grade, _mult, _css, _desc = _mr_grade(_total)
                _sa_m_raw = st.session_state.get("mr_mental_sa", _sa_mental_opts[0])
                _sa_t_raw = st.session_state.get("mr_tactical_sa", _sa_tact_opts[0])
                _sa_m_key = _sa_mental_vals[_sa_mental_opts.index(_sa_m_raw)] if _sa_m_raw in _sa_mental_opts else "GRIND"
                _sa_t_key = _sa_tact_vals[_sa_tact_opts.index(_sa_t_raw)] if _sa_t_raw in _sa_tact_opts else "GRIND"
                _sess_state = _derive_session_state(_grade, _sa_m_key, _sa_t_key)
                session["morning_report"] = {
                    "completed": True, "grade": _grade, "multiplier": _mult,
                    "score": _total, "description": _desc, "color": _css,
                    "session_state":  _sess_state,
                    "mental_state":   _sa_m_key,
                    "tactical_state": _sa_t_key,
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
        _sa_ss   = morning_report.get("session_state", "")
        _sa_sm   = _SESSION_STATE_META.get(_sa_ss, {})
        _sa_scl  = _sa_sm.get("css", "white")
        if _sa_ss == "STATIC":
            st.markdown(
                f'<div class="mr-no-trade">STATE: STATIC — DO NOT TRADE TODAY · '
                f'Protect capital. Come back tomorrow.</div>', unsafe_allow_html=True)
        elif _sa_ss == "GRIND":
            st.markdown(
                f'<div class="warn-banner">State: <strong>GRIND</strong> — '
                f'{_mr_desc}. Daily limit adjusted to {_mr_eff:.2f}R ({_mr_mult*100:.0f}% of {_mr_orig}R).</div>',
                unsafe_allow_html=True)
        _limit_line = (
            f"{_mr_orig}R × {_mr_mult*100:.0f}% = <strong>{_mr_eff:.2f}R effective limit</strong>"
            if _mr_mult < 1.0 else f"<strong>{_mr_orig}R</strong> — full limit (no reduction)"
        )
        st.markdown(f"""
        <div class="mr-card">
          <div class="mr-badge">TUNE — Pre-Session Calibration</div>
          <div style="display:flex;gap:32px;align-items:center;flex-wrap:wrap">
            <div>
              <div style="font-size:0.6rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600">Calibration Grade</div>
              <div style="font-size:3rem;font-weight:800;line-height:1;letter-spacing:-0.03em" class="{_mr_css}">{_mr_g}</div>
              <div style="font-size:0.78rem;color:#94a3b8;margin-top:4px">{_mr_desc}</div>
            </div>
            <div>
              <div style="font-size:0.6rem;color:#4b5a7a;text-transform:uppercase;letter-spacing:.1em;font-weight:600">Session State</div>
              <div style="font-size:2.2rem;font-weight:900;letter-spacing:0.08em;line-height:1;margin-top:4px" class="{_sa_scl}">{_sa_ss}</div>
              <div style="font-size:0.72rem;color:#94a3b8;margin-top:2px">
                Mental: <strong>{morning_report.get("mental_state","—")}</strong> ·
                Tactical: <strong>{morning_report.get("tactical_state","—")}</strong>
              </div>
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
            if st.button("↩ Re-Tune", type="secondary", use_container_width=True):
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
                if st.button("✕ Cancel", key=f"cancel_pend_{_ti}", type="secondary", use_container_width=True):
                    del st.session_state["_outcome_pending"]
                    st.rerun()
            else:
                # Normal action buttons
                _btn_cols = st.columns([2,2,2,2,2,1])
                with _btn_cols[0]:
                    if st.button("WIN",  key=f"win_{_ti}",  use_container_width=True, type="primary"):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "win"}
                        st.rerun()
                with _btn_cols[1]:
                    if st.button("LOSS", key=f"loss_{_ti}", use_container_width=True):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "loss"}
                        st.rerun()
                with _btn_cols[2]:
                    if st.button("BE",   key=f"be_{_ti}",   use_container_width=True):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "be"}
                        st.rerun()
                with _btn_cols[3]:
                    if st.button(f"+{prefs['addon_r']}R", key=f"add_{_ti}", use_container_width=True):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "addon"}
                        st.rerun()
                with _btn_cols[4]:
                    if st.button("Edit", key=f"edit_{_ti}", use_container_width=True):
                        st.session_state["_outcome_pending"] = {"idx": _ti, "type": "edit"}
                        st.rerun()
                with _btn_cols[5]:
                    if st.button("✕", key=f"cancel_{_ti}", use_container_width=True, help="Cancel trade"):
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
                if st.form_submit_button("Save", use_container_width=True):
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
                if st.form_submit_button("Pull R from CSV", use_container_width=True) and _win_csv:
                    _wp, _wf, _ws, _we = _parse_csv_last_close(_win_csv.read(), _win_csv_ex)
                    st.error(_we) if _we else st.success(f"CSV ({_ws}): {round(_wp/one_r,4):+.4f}R") if _wp else None
                if st.form_submit_button("Confirm Win", use_container_width=True):
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
                    pull_okx = st.form_submit_button(_pull_lbl, use_container_width=True)
                with lc3:
                    confirm  = st.form_submit_button("Confirm Loss", use_container_width=True)
                st.markdown("---")
                _csv_ex_h = st.selectbox("CSV exchange format", ["Auto-detect"] + _EXCHANGES, key=f"lcx_{_op_idx}")
                _csv_file = st.file_uploader("Or upload exchange CSV", type=["csv"], key=f"lcsv_{_op_idx}")
                _csv_pull = st.form_submit_button("Parse CSV", use_container_width=True)

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
                if st.form_submit_button("Confirm Break Even", use_container_width=True):
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
                if st.form_submit_button("Confirm Add-on", use_container_width=True):
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
        # Quick-launch buttons — must select before form unlocks
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
                            _p4_level = st.radio("At a valid S/R level?", ["Yes", "No — not yet"], key="p4_lvl")
                        with _p4c2:
                            _p4_fp    = st.radio("Footprint confirms?", ["Yes", "No — wait"], key="p4_fp")
                        if _p4_strat == "Breakout":
                            _p4_bo = st.radio("Breakout confirmed with order activity?",
                                              ["Yes", "No — false breakout", "N/A"], key="p4_bo")
                        else:
                            _p4_bo = "N/A"

                if st.form_submit_button("Enter Trade", use_container_width=True):
                    # Validate Phase 4 if safe mode
                    _block = None
                    if safe_mode:
                        _inst_sub = st.session_state.get("p4_lvl", "Yes")
                        _fp_sub   = st.session_state.get("p4_fp", "Yes")
                        _bo_sub   = st.session_state.get("p4_bo", "N/A")
                        if _inst_sub.startswith("No"):
                            _block = "Not at a valid S/R level — wait for better location."
                        elif _fp_sub.startswith("No"):
                            _block = "Footprint doesn't confirm — wait."
                        elif _bo_sub == "No — false breakout":
                            _block = "False breakout — do not trade."
                    if _block:
                        st.error(_block)
                    else:
                        _implied_r   = prefs["grades"][chosen_grade]["implied_r"]
                        _entry_risk  = _risk_score(rr_target, 0, chosen_grade)
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
                        }
                        st.session_state.pop("_quick_grade", None)
                        st.session_state.pop("_ws_prefill", None)
                        active_trades.append(_new_trade)
                        session["active_trades"] = active_trades
                        _save_session(session)
                        st.rerun()

# ─── CLEAR PHASE — shown when session has trades and no active positions ──────
if _loop_clear:
    _clear_total_r = session_pnl_r
    _clear_wins    = sum(1 for t in completed if t.get("actual_r", 0) > 0)
    _clear_losses  = sum(1 for t in completed if t.get("actual_r", 0) < 0)
    _clear_color   = "#22c55e" if _clear_total_r > 0 else ("#ef4444" if _clear_total_r < 0 else "#94a3b8")
    _clear_sign    = "+" if _clear_total_r > 0 else ""
    st.markdown(f"""
    <div class="sec-hdr">
      <div class="sec-line"></div><div class="sec-title">CLEAR — Session Complete</div><div class="sec-line"></div>
    </div>
    <div class="state-badge state-badge-{'prime' if _clear_total_r > 0 else ('static' if _clear_total_r < 0 else 'grind')}"
         style="margin-bottom:18px">
      <div class="state-label" style="color:{_clear_color}">{_clear_sign}{_clear_total_r:.2f}R</div>
      <div class="state-meta">
        <div class="state-meta-name">Session Result</div>
        <div class="state-meta-desc">{_clear_wins}W · {_clear_losses}L · {len(completed)} trade{'s' if len(completed)!=1 else ''}</div>
        <div style="margin-top:6px;font-size:0.72rem;color:#4b5a7a">Let go of the outcome. Reset for tomorrow.</div>
      </div>
    </div>""", unsafe_allow_html=True)

# ─── SESSION LOG ──────────────────────────────────────────────────────────────
st.markdown("""
<div class="sec-hdr">
  <div class="sec-line"></div><div class="sec-title">HUNT Log — Today's Trades</div><div class="sec-line"></div>
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
