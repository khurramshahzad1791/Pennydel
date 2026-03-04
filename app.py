# app.py
import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback

# -------------------- Page Config --------------------
st.set_page_config(page_title="MEXC Breakout Scanner", layout="wide")

# -------------------- Session State Init --------------------
if 'all_pairs' not in st.session_state:
    st.session_state.all_pairs = []
if 'scanned_results' not in st.session_state:
    st.session_state.scanned_results = []
if 'batch_index' not in st.session_state:
    st.session_state.batch_index = 0
if 'batch_size' not in st.session_state:
    st.session_state.batch_size = 50
if 'scan_complete' not in st.session_state:
    st.session_state.scan_complete = False
if 'filtered_watchlist' not in st.session_state:
    st.session_state.filtered_watchlist = pd.DataFrame()

# -------------------- Simplified Pair Loading --------------------
@st.cache_data(ttl=3600)
def get_mexc_futures_pairs():
    """Get all USDT perpetual futures from MEXC."""
    try:
        exchange = ccxt.mexc({'enableRateLimit': True, 'timeout': 30000})
        markets = exchange.load_markets()
        pairs = [
            symbol for symbol in markets
            if symbol.endswith('/USDT')
            and markets[symbol].get('future', False)
            and markets[symbol]['active']
        ]
        return pairs[:500]  # limit for performance
    except Exception as e:
        st.error(f"❌ MEXC API error: {e}")
        return []

# -------------------- Data Fetching --------------------
@st.cache_data(ttl=1800)
def fetch_ohlcv(symbol, timeframe='1d', limit=500):
    try:
        exchange = ccxt.mexc({'enableRateLimit': True, 'timeout': 30000})
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        return None

# -------------------- Breakout Detection --------------------
def detect_breakout(df_daily, df_hourly):
    close = df_daily['close']
    high = df_daily['high']
    low = df_daily['low']
    volume = df_daily['volume']

    # 20-day high
    recent_high = high.rolling(20).max()
    above_high = close.iloc[-1] > recent_high.iloc[-2]

    # Volume surge
    vol_ma10 = volume.rolling(10).mean()
    vol_surge = volume.iloc[-1] / vol_ma10.iloc[-1] if vol_ma10.iloc[-1] > 0 else 1

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    rsi_val = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

    # Consolidation
    range_10 = (high.rolling(10).max() - low.rolling(10).min()) / close.rolling(10).mean()
    range_50 = (high.rolling(50).max() - low.rolling(50).min()) / close.rolling(50).mean()
    consolidation = (range_10.iloc[-1] / range_50.iloc[-1]) < 0.5 if not pd.isna(range_50.iloc[-1]) else False

    # Score
    score = 0
    if above_high:
        score += 30
    if vol_surge > 2:
        score += 30
    elif vol_surge > 1.5:
        score += 15
    if rsi_val > 60:
        score += 20
    elif rsi_val > 50:
        score += 10
    if consolidation:
        score += 20

    # Hourly early pump
    hourly_close = df_hourly['close']
    hourly_vol = df_hourly['volume']
    vol_ma4 = hourly_vol.rolling(4).mean()
    hourly_vol_surge = hourly_vol.iloc[-1] / vol_ma4.iloc[-1] if vol_ma4.iloc[-1] > 0 else 1
    price_change_24h = (hourly_close.iloc[-1] - hourly_close.iloc[-24]) / hourly_close.iloc[-24] if len(hourly_close) >= 24 else 0
    early_pump = (hourly_vol_surge > 2) and (abs(price_change_24h) < 0.05)

    if early_pump:
        score += 25

    # Grade
    if score >= 80:
        grade = 'A+'
    elif score >= 70:
        grade = 'A'
    elif score >= 60:
        grade = 'B+'
    elif score >= 50:
        grade = 'B'
    elif score >= 40:
        grade = 'C+'
    else:
        grade = 'C'

    entry = close.iloc[-1]
    sl = entry * 0.92
    tp = entry * 2.0

    return {
        'Breakout Score': score,
        'Grade': grade,
        'Above 20d High': above_high,
        'Volume Surge': round(vol_surge, 2),
        'RSI': round(rsi_val, 2),
        'Consolidation': consolidation,
        'Early Pump': early_pump,
        'Entry': entry,
        'Stop Loss': sl,
        'Take Profit': tp,
        'Exit Condition': 'Sell on 100% gain or RSI > 75'
    }

def analyze_pair(pair):
    df_daily = fetch_ohlcv(pair, '1d', 200)
    df_hourly = fetch_ohlcv(pair, '1h', 168)
    if df_daily is None or df_hourly is None or len(df_daily) < 50:
        return None
    try:
        breakout = detect_breakout(df_daily, df_hourly)
        if breakout['Breakout Score'] < 30:
            return None
        return {'Pair': pair, 'Current Price': df_daily['close'].iloc[-1], **breakout}
    except:
        return None

def scan_batch(pairs, max_workers=5):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_pair = {executor.submit(analyze_pair, pair): pair for pair in pairs}
        for future in as_completed(future_to_pair):
            try:
                res = future.result(timeout=30)
                if res:
                    results.append(res)
            except:
                pass
    return results

# -------------------- UI --------------------
st.title("🚀 MEXC Breakout Scanner (All Futures)")
st.markdown("Scans all MEXC USDT perpetuals for breakout signals.")

# Sidebar manual override
with st.sidebar:
    st.header("Manual Input")
    manual_pairs = st.text_area("Enter pairs (one per line)")
    if st.button("Use Manual Pairs"):
        if manual_pairs.strip():
            pairs = [p.strip() for p in manual_pairs.split('\n') if p.strip()]
            st.session_state.all_pairs = pairs
            st.session_state.batch_index = 0
            st.session_state.scanned_results = []
            st.rerun()

scan_mode = st.radio("Mode", ["Breakouts", "Early Pumps"], horizontal=True)
col1, col2, col3 = st.columns(3)
with col1:
    batch_size = st.slider("Batch size", 20, 200, st.session_state.batch_size, 10)
    st.session_state.batch_size = batch_size
with col2:
    concurrency = st.slider("Threads", 1, 10, 5)
with col3:
    if st.button("🔄 Reset"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

if not st.session_state.all_pairs:
    with st.spinner("Loading MEXC futures..."):
        st.session_state.all_pairs = get_mexc_futures_pairs()
    if st.session_state.all_pairs:
        st.success(f"Loaded {len(st.session_state.all_pairs)} pairs")
    else:
        st.error("Failed to load pairs. Use manual input.")
        st.stop()

total = len(st.session_state.all_pairs)
scanned = len(st.session_state.scanned_results)
st.progress(scanned / total if total else 0)
st.caption(f"Scanned: {scanned} / {total}")

if st.button("▶️ Scan Next Batch", disabled=(scanned >= total)):
    start = scanned
    end = min(start + st.session_state.batch_size, total)
    batch = st.session_state.all_pairs[start:end]
    with st.status(f"Batch {st.session_state.batch_index+1}..."):
        new = scan_batch(batch, max_workers=concurrency)
        st.session_state.scanned_results.extend(new)
        st.session_state.batch_index += 1
    st.rerun()

if st.session_state.scanned_results:
    df = pd.DataFrame(st.session_state.scanned_results)
    if scan_mode == "Breakouts":
        filtered = df[(df['Above 20d High'] == True) & (df['Volume Surge'] > 1.5) & (df['RSI'] > 50) & (df['Grade'].isin(['A+','A','B+']))]
    else:
        filtered = df[(df['Early Pump'] == True) & (df['Volume Surge'] > 2) & (df['Grade'].isin(['A+','A']))]
    st.subheader("📊 Signals")
    if not filtered.empty:
        cols = ['Pair','Grade','Breakout Score','Current Price','Entry','Stop Loss','Take Profit','Volume Surge','RSI','Early Pump']
        st.dataframe(filtered[cols].style.format({'Current Price':'{:.8f}','Entry':'{:.8f}','Stop Loss':'{:.8f}','Take Profit':'{:.8f}','Volume Surge':'{:.2f}','RSI':'{:.1f}'}))
    else:
        st.info("No signals match filters.")
    with st.expander("All results"):
        st.dataframe(df)
