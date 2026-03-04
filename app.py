# app.py
import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback

st.set_page_config(page_title="MEXC Penny Stock Breakout Scanner", layout="wide")

# -------------------- Session State --------------------
if 'all_pairs' not in st.session_state:
    st.session_state.all_pairs = []
if 'scanned_results' not in st.session_state:
    st.session_state.scanned_results = []
if 'batch_index' not in st.session_state:
    st.session_state.batch_index = 0
if 'batch_size' not in st.session_state:
    st.session_state.batch_size = 50
if 'filtered_watchlist' not in st.session_state:
    st.session_state.filtered_watchlist = pd.DataFrame()

# -------------------- Sidebar Settings --------------------
st.sidebar.header("🔧 Scanner Settings")
timeframe = st.sidebar.selectbox("Timeframe", ["1d", "4h", "1h"], index=0)
min_volume = st.sidebar.number_input("Min 24h Volume (USDT)", min_value=0, value=100_000, step=10_000)
max_volume = st.sidebar.number_input("Max 24h Volume (USDT)", min_value=0, value=5_000_000, step=50_000)
use_volume_filter = st.sidebar.checkbox("Apply Volume Filter", value=True)
batch_size = st.sidebar.slider("Batch Size", 20, 200, 50, 10)
concurrency = st.sidebar.slider("Threads", 1, 10, 5)
st.session_state.batch_size = batch_size

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Reset All Data"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

# -------------------- Load Pairs Automatically --------------------
@st.cache_data(ttl=1800)  # 30 minutes
def load_mexc_pairs(use_volume_filter, min_vol, max_vol):
    """Load all USDT perpetual swaps from MEXC, optionally filter by volume."""
    exchange = ccxt.mexc({'enableRateLimit': True, 'timeout': 30000})
    try:
        # 1. Load markets to get all perpetual swaps
        markets = exchange.load_markets()
        all_perps = []
        for symbol, market in markets.items():
            if (symbol.endswith('/USDT') 
                and market.get('swap', False)      # perpetual swap
                and market.get('linear', False)    # USDT-margined
                and market.get('active', False)):
                all_perps.append(symbol)
        
        if not all_perps:
            st.error("No perpetual swaps found from MEXC. Check API or use manual input.")
            return []
        
        st.info(f"✅ Found {len(all_perps)} USDT perpetual swaps on MEXC")
        
        # 2. If volume filter is off, return all
        if not use_volume_filter:
            return all_perps[:500]  # limit for performance
        
        # 3. Fetch tickers for volume data
        tickers = exchange.fetch_tickers()
        filtered = []
        for sym in all_perps:
            ticker = tickers.get(sym)
            if ticker and 'quoteVolume' in ticker:
                qv = ticker['quoteVolume']
                if min_vol <= qv <= max_vol:
                    filtered.append(sym)
        
        if filtered:
            st.success(f"✅ After volume filter: {len(filtered)} low‑cap pairs")
            return filtered[:500]
        else:
            st.warning("No pairs match your volume range. Returning all perpetuals.")
            return all_perps[:500]
            
    except Exception as e:
        st.error(f"❌ Failed to load pairs: {e}")
        # Fallback: try fetch_tickers only
        try:
            tickers = exchange.fetch_tickers()
            fallback = [s for s in tickers if s.endswith('/USDT')]
            st.warning(f"Falling back to {len(fallback)} USDT pairs from tickers.")
            return fallback[:500]
        except:
            return []

# -------------------- Load pairs if not already --------------------
if not st.session_state.all_pairs:
    with st.spinner("Loading MEXC pairs..."):
        st.session_state.all_pairs = load_mexc_pairs(use_volume_filter, min_volume, max_volume)
    if not st.session_state.all_pairs:
        st.error("Could not load any pairs. Please check your internet or try again later.")
        st.stop()
    else:
        st.success(f"Ready to scan {len(st.session_state.all_pairs)} pairs")

# -------------------- Data Fetching & Analysis --------------------
@st.cache_data(ttl=600)  # 10 minutes
def fetch_ohlcv(symbol, timeframe='1d', limit=300):
    try:
        exchange = ccxt.mexc({'enableRateLimit': True, 'timeout': 30000})
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except:
        return None

def detect_breakout(df):
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']
    
    # 20-day high breakout
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
    
    # Consolidation (tight range)
    range_10 = (high.rolling(10).max() - low.rolling(10).min()) / close.rolling(10).mean()
    range_50 = (high.rolling(50).max() - low.rolling(50).min()) / close.rolling(50).mean()
    consolidation = (range_10.iloc[-1] / range_50.iloc[-1]) < 0.5 if not pd.isna(range_50.iloc[-1]) else False
    
    # ADX (trend strength)
    adx = df.ta.adx(length=14)['ADX_14'].iloc[-1] if hasattr(df, 'ta') else 25  # placeholder
    
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
    if adx > 25:
        score += 10
    
    # Grade
    if score >= 80:
        grade = 'A+'
    elif score >= 70:
        grade = 'A'
    elif score >= 60:
        grade = 'B+'
    elif score >= 50:
        grade = 'B'
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
        'Entry': entry,
        'Stop Loss': sl,
        'Take Profit': tp
    }

def analyze_pair(pair):
    df = fetch_ohlcv(pair, timeframe, 200)
    if df is None or len(df) < 100:
        return None
    try:
        breakout = detect_breakout(df)
        if breakout['Breakout Score'] < 40:  # filter weak signals
            return None
        return {'Pair': pair, 'Current Price': df['close'].iloc[-1], **breakout}
    except:
        return None

def scan_batch(pairs, max_workers):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(analyze_pair, pair): pair for pair in pairs}
        for future in as_completed(future_map):
            try:
                res = future.result(timeout=30)
                if res:
                    results.append(res)
            except:
                pass
    return results

# -------------------- UI: Scan Progress --------------------
total_pairs = len(st.session_state.all_pairs)
scanned_so_far = len(st.session_state.scanned_results)
progress = scanned_so_far / total_pairs if total_pairs else 0
st.progress(progress)
st.caption(f"Scanned: {scanned_so_far} / {total_pairs} pairs | Batch {st.session_state.batch_index+1}")

if st.button("▶️ Scan Next Batch", disabled=(scanned_so_far >= total_pairs)):
    start = scanned_so_far
    end = min(start + st.session_state.batch_size, total_pairs)
    batch = st.session_state.all_pairs[start:end]
    with st.status(f"Scanning batch {st.session_state.batch_index+1}...", expanded=True) as status:
        new_results = scan_batch(batch, concurrency)
        st.session_state.scanned_results.extend(new_results)
        st.session_state.batch_index += 1
        status.update(label=f"Batch complete! Found {len(new_results)} signals", state="complete")
    st.rerun()

# -------------------- Display Results --------------------
if st.session_state.scanned_results:
    df_all = pd.DataFrame(st.session_state.scanned_results)
    
    # Filter for strong signals
    strong = df_all[
        (df_all['Above 20d High'] == True) &
        (df_all['Volume Surge'] > 1.5) &
        (df_all['Grade'].isin(['A+', 'A', 'B+']))
    ].sort_values('Breakout Score', ascending=False)
    
    st.subheader("📊 Top Breakout Signals")
    if not strong.empty:
        cols = ['Pair', 'Grade', 'Breakout Score', 'Current Price', 'Entry', 'Stop Loss', 'Take Profit',
                'Volume Surge', 'RSI', 'Above 20d High', 'Consolidation']
        st.dataframe(strong[cols].style.format({
            'Current Price': '{:.8f}',
            'Entry': '{:.8f}',
            'Stop Loss': '{:.8f}',
            'Take Profit': '{:.8f}',
            'Breakout Score': '{:.0f}',
            'Volume Surge': '{:.2f}',
            'RSI': '{:.1f}'
        }), use_container_width=True)
    else:
        st.info("No strong breakout signals yet. Try scanning more pairs.")
    
    with st.expander("Show all scanned results"):
        st.dataframe(df_all)

# -------------------- Manual Override (in case all fails) --------------------
with st.sidebar:
    st.markdown("---")
    st.header("Manual Override")
    manual_input = st.text_area("Paste pairs (one per line, e.g. BTC/USDT)")
    if st.button("Load Manual Pairs"):
        if manual_input.strip():
            pairs = [p.strip() for p in manual_input.split('\n') if p.strip()]
            st.session_state.all_pairs = pairs
            st.session_state.batch_index = 0
            st.session_state.scanned_results = []
            st.rerun()

st.sidebar.caption("If auto‑load fails, use manual input.")
