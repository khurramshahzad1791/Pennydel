# app.py
import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(
    page_title="MEXC Breakout Scanner",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -------------------- CUSTOM CSS --------------------
st.markdown("""
<style>
    .main-header { font-size: 2.5rem; font-weight: 700; color: #FF4B4B; margin-bottom: 0; }
    .sub-header { font-size: 1rem; color: #888; margin-top: 0; }
    .signal-card { background: #1E1E1E; border-radius: 10px; padding: 1rem; margin: 0.5rem 0; }
    .long-text { color: #00FF88; font-weight: bold; }
    .short-text { color: #FF4B4B; font-weight: bold; }
    .wait-text { color: #AAAAAA; }
    .metric-box { background: #262730; border-radius: 8px; padding: 0.8rem; text-align: center; }
</style>
""", unsafe_allow_html=True)

st.markdown('<h1 class="main-header">📊 MEXC Pro Breakout Scanner</h1>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Real‑time scans for breakouts, volume surges & momentum setups on USDT perpetuals</p>', unsafe_allow_html=True)

# -------------------- SIDEBAR SETTINGS --------------------
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/stock-exchange.png", width=80)
    st.markdown("## ⚙️ Settings")
    
    # Timeframe selection
    timeframe_map = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "4h": "4h", "1d": "1d"
    }
    tf_display = st.selectbox("⏱️ Timeframe", list(timeframe_map.keys()), index=6)
    timeframe = timeframe_map[tf_display]
    
    # Volume filter
    st.markdown("### 📊 Volume Filter (24h)")
    use_vol_filter = st.checkbox("Enable volume filter", value=True)
    col1, col2 = st.columns(2)
    with col1:
        min_vol = st.number_input("Min (USDT)", min_value=0, value=100_000, step=10_000)
    with col2:
        max_vol = st.number_input("Max (USDT)", min_value=0, value=5_000_000, step=50_000)
    
    # Scan settings
    st.markdown("### 🚀 Scan Settings")
    batch_size = st.slider("Batch size", 20, 200, 50, 10)
    concurrency = st.slider("Threads", 1, 10, 5)
    
    # Pre-built fallback list (100+ popular MEXC perpetuals)
    DEFAULT_PAIRS = [
        "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
        "ADA/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT", "MATIC/USDT", "SHIB/USDT",
        "TRX/USDT", "ATOM/USDT", "LTC/USDT", "BCH/USDT", "NEAR/USDT", "UNI/USDT",
        "APT/USDT", "ICP/USDT", "FIL/USDT", "ETC/USDT", "XLM/USDT", "VET/USDT",
        "QNT/USDT", "ALGO/USDT", "MANA/USDT", "SAND/USDT", "AXS/USDT", "AAVE/USDT",
        "EGLD/USDT", "FLOW/USDT", "THETA/USDT", "FTM/USDT", "GALA/USDT", "GRT/USDT",
        "RUNE/USDT", "KAVA/USDT", "CHZ/USDT", "ZIL/USDT", "ENJ/USDT", "BAT/USDT",
        "CRO/USDT", "DYDX/USDT", "IMX/USDT", "RNDR/USDT", "STX/USDT", "CRV/USDT",
        "SNX/USDT", "COMP/USDT", "YFI/USDT", "SUSHI/USDT", "1INCH/USDT", "OMG/USDT",
        "KSM/USDT", "WAVES/USDT", "ZEC/USDT", "DASH/USDT", "XMR/USDT", "IOST/USDT",
        "IOTA/USDT", "ONT/USDT", "QTUM/USDT", "ZRX/USDT", "SC/USDT", "LSK/USDT",
        "DGB/USDT", "RVN/USDT", "STORJ/USDT", "CVC/USDT", "MTL/USDT", "SKL/USDT",
        "CELR/USDT", "ANKR/USDT", "TOMO/USDT", "FET/USDT", "OCEAN/USDT", "BAND/USDT",
        "NKN/USDT", "STRAX/USDT", "ARPA/USDT", "CTXC/USDT", "DATA/USDT", "DOCK/USDT",
        "FUN/USDT", "HOT/USDT", "LOOM/USDT", "MAN/USDT", "MITH/USDT",
        "NCASH/USDT", "NPXS/USDT", "OST/USDT", "POLY/USDT", "POWR/USDT", "QKC/USDT",
        "QLC/USDT", "RCN/USDT", "RDN/USDT", "REQ/USDT", "RLC/USDT", "SNT/USDT",
        "SYS/USDT", "WAN/USDT", "WPR/USDT", "XZC/USDT", "YOYO/USDT"
    ]
    
    st.markdown("### 📋 Pair Source")
    source = st.radio("Load pairs from", ["Auto (MEXC + volume)", "Use default list", "Manual input"])
    
    if source == "Manual input":
        manual_pairs = st.text_area("Enter pairs (one per line, e.g. BTC/USDT)")
        if st.button("Load manual pairs"):
            if manual_pairs.strip():
                pairs = [p.strip() for p in manual_pairs.split('\n') if p.strip()]
                st.session_state.all_pairs = pairs
                st.session_state.scanned_results = []
                st.session_state.batch_index = 0
                st.rerun()
    
    st.markdown("---")
    if st.button("🔄 Reset session"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# -------------------- SESSION STATE INIT --------------------
if 'all_pairs' not in st.session_state:
    st.session_state.all_pairs = []
if 'scanned_results' not in st.session_state:
    st.session_state.scanned_results = []
if 'batch_index' not in st.session_state:
    st.session_state.batch_index = 0

# -------------------- AUTO LOAD PAIRS --------------------
def load_pairs_from_mexc(use_vol_filter, min_vol, max_vol):
    """Load USDT perpetuals from MEXC with optional volume filter."""
    try:
        exchange = ccxt.mexc({'enableRateLimit': True, 'timeout': 30000})
        markets = exchange.load_markets()
        perps = []
        for sym, m in markets.items():
            if (sym.endswith('/USDT') 
                and m.get('swap', False) 
                and m.get('linear', False) 
                and m.get('active', False)):
                perps.append(sym)
        if not use_vol_filter:
            return perps
        # Get tickers for volume
        tickers = exchange.fetch_tickers()
        filtered = []
        for sym in perps:
            t = tickers.get(sym)
            if t and 'quoteVolume' in t and min_vol <= t['quoteVolume'] <= max_vol:
                filtered.append(sym)
        return filtered if filtered else perps
    except Exception as e:
        st.warning(f"Auto load failed: {e}")
        return []

# Determine pairs based on source
if source == "Auto (MEXC + volume)" and not st.session_state.all_pairs:
    with st.spinner("📡 Connecting to MEXC..."):
        loaded = load_pairs_from_mexc(use_vol_filter, min_vol, max_vol)
        if loaded:
            st.session_state.all_pairs = loaded[:500]
            st.success(f"✅ Loaded {len(loaded)} pairs from MEXC")
        else:
            st.warning("⚠️ Auto load failed, falling back to default list.")
            st.session_state.all_pairs = DEFAULT_PAIRS

elif source == "Use default list" and not st.session_state.all_pairs:
    st.session_state.all_pairs = DEFAULT_PAIRS
    st.info(f"📋 Using default list of {len(DEFAULT_PAIRS)} pairs")

if not st.session_state.all_pairs:
    st.warning("No pairs loaded. Please select a source and load.")
    st.stop()

# -------------------- SCANNING FUNCTIONS --------------------
@st.cache_data(ttl=600)
def fetch_ohlcv(symbol, tf, limit=200):
    try:
        ex = ccxt.mexc({'enableRateLimit': True, 'timeout': 30000})
        ohlcv = ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except:
        return None

def analyze_pair(pair):
    df = fetch_ohlcv(pair, timeframe, 200)
    if df is None or len(df) < 100:
        return None
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']
    
    # Indicators
    ema9 = close.ewm(span=9).mean()
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()
    
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
    
    # 20-period high breakout
    recent_high = high.rolling(20).max()
    above_high = close.iloc[-1] > recent_high.iloc[-2]
    
    # Trend
    uptrend = close.iloc[-1] > ema50.iloc[-1]
    downtrend = close.iloc[-1] < ema50.iloc[-1]
    
    # Consolidation (Bollinger squeeze)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_width = (bb_mid + 2*bb_std - (bb_mid - 2*bb_std)) / bb_mid
    bb_squeeze = bb_width.iloc[-1] < bb_width.rolling(20).mean().iloc[-1]
    
    # Score and direction
    long_score = 0
    short_score = 0
    
    if above_high and uptrend:
        long_score += 30
    if vol_surge > 2:
        long_score += 30
        short_score += 20
    elif vol_surge > 1.5:
        long_score += 15
        short_score += 10
    
    if rsi_val > 55 and uptrend:
        long_score += 20
    elif rsi_val < 45 and downtrend:
        short_score += 20
    
    if bb_squeeze:
        long_score += 15
        short_score += 15
    
    if uptrend:
        long_score += 10
    if downtrend:
        short_score += 10
    
    # Determine signal
    if long_score >= 70:
        signal = "LONG"
        color = "#00FF88"
        entry = close.iloc[-1]
        sl = entry * 0.96  # 4% stop
        tp1 = entry * 1.05  # 5% target
        tp2 = entry * 1.10  # 10% target
    elif short_score >= 70:
        signal = "SHORT"
        color = "#FF4B4B"
        entry = close.iloc[-1]
        sl = entry * 1.04
        tp1 = entry * 0.95
        tp2 = entry * 0.90
    else:
        return None  # Skip weak signals
    
    return {
        'Pair': pair,
        'Signal': signal,
        'Color': color,
        'Price': round(entry, 8),
        'Score': max(long_score, short_score),
        'Volume Surge': round(vol_surge, 2),
        'RSI': round(rsi_val, 2),
        'Breakout': above_high,
        'SL': round(sl, 8),
        'TP1': round(tp1, 8),
        'TP2': round(tp2, 8),
        'RR': round(abs(tp1 - entry) / abs(entry - sl), 2)
    }

def scan_batch(pairs, workers):
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(analyze_pair, p): p for p in pairs}
        for f in as_completed(futures):
            try:
                res = f.result(timeout=20)
                if res:
                    results.append(res)
            except:
                pass
    return results

# -------------------- MAIN INTERFACE --------------------
total = len(st.session_state.all_pairs)
scanned = len(st.session_state.scanned_results)
progress = scanned / total if total else 0

# Progress bar and stats
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Pairs", total)
with col2:
    st.metric("Scanned", scanned)
with col3:
    st.metric("Signals Found", len(st.session_state.scanned_results))
with col4:
    st.metric("Batch", st.session_state.batch_index + 1)

st.progress(progress)

# Scan button
col_btn, _ = st.columns([1, 3])
with col_btn:
    if st.button("▶️ Scan Next Batch", use_container_width=True, disabled=(scanned >= total)):
        start = scanned
        end = min(start + batch_size, total)
        batch = st.session_state.all_pairs[start:end]
        with st.status(f"🔍 Scanning batch {st.session_state.batch_index+1}..."):
            new = scan_batch(batch, concurrency)
            st.session_state.scanned_results.extend(new)
            st.session_state.batch_index += 1
        st.rerun()

# Display results
if st.session_state.scanned_results:
    df = pd.DataFrame(st.session_state.scanned_results)
    
    # Separate longs and shorts
    longs = df[df['Signal'] == 'LONG'].sort_values('Score', ascending=False)
    shorts = df[df['Signal'] == 'SHORT'].sort_values('Score', ascending=False)
    
    st.markdown("---")
    st.subheader("🔥 Active Signals")
    
    if not longs.empty:
        with st.expander(f"🟢 LONG Setups ({len(longs)})", expanded=True):
            st.dataframe(
                longs[['Pair', 'Score', 'Price', 'Volume Surge', 'RSI', 'Breakout', 'SL', 'TP1', 'TP2', 'RR']],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Score": st.column_config.ProgressColumn("Score", format="%d", min_value=0, max_value=100),
                    "Price": st.column_config.NumberColumn("Price", format="$%.8f"),
                    "SL": st.column_config.NumberColumn("Stop Loss", format="$%.8f"),
                    "TP1": st.column_config.NumberColumn("TP1", format="$%.8f"),
                    "TP2": st.column_config.NumberColumn("TP2", format="$%.8f"),
                    "RR": st.column_config.NumberColumn("R:R", format="%.2f"),
                }
            )
    
    if not shorts.empty:
        with st.expander(f"🔴 SHORT Setups ({len(shorts)})", expanded=True):
            st.dataframe(
                shorts[['Pair', 'Score', 'Price', 'Volume Surge', 'RSI', 'Breakout', 'SL', 'TP1', 'TP2', 'RR']],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Score": st.column_config.ProgressColumn("Score", format="%d", min_value=0, max_value=100),
                    "Price": st.column_config.NumberColumn("Price", format="$%.8f"),
                    "SL": st.column_config.NumberColumn("Stop Loss", format="$%.8f"),
                    "TP1": st.column_config.NumberColumn("TP1", format="$%.8f"),
                    "TP2": st.column_config.NumberColumn("TP2", format="$%.8f"),
                }
            )
    
    # Download CSV
    csv = df.to_csv(index=False)
    st.download_button("📥 Download All Signals", csv, "mexc_signals.csv", mime="text/csv")
    
    st.markdown("---")
    st.subheader("📈 Detailed Chart")
    selected_pair = st.selectbox("Choose a pair for chart", df['Pair'].unique())
    
    # Show chart for selected pair
    if selected_pair:
        df_detail = fetch_ohlcv(selected_pair, timeframe, 200)
        if df_detail is not None:
            fig = go.Figure(data=[
                go.Candlestick(
                    x=df_detail['timestamp'][-100:],
                    open=df_detail['open'][-100:],
                    high=df_detail['high'][-100:],
                    low=df_detail['low'][-100:],
                    close=df_detail['close'][-100:],
                    name="Price"
                )
            ])
            # Add EMAs
            ema9 = df_detail['close'].ewm(span=9).mean()[-100:]
            ema20 = df_detail['close'].ewm(span=20).mean()[-100:]
            fig.add_trace(go.Scatter(x=df_detail['timestamp'][-100:], y=ema9, name="EMA9", line=dict(color="cyan")))
            fig.add_trace(go.Scatter(x=df_detail['timestamp'][-100:], y=ema20, name="EMA20", line=dict(color="orange")))
            
            fig.update_layout(
                title=f"{selected_pair} – {tf_display}",
                xaxis_rangeslider_visible=False,
                height=500,
                template="plotly_dark"
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Show current signal details
            signal_row = df[df['Pair'] == selected_pair].iloc[0]
            sig_color = "#00FF88" if signal_row['Signal'] == 'LONG' else "#FF4B4B"
            st.markdown(f"**Current Signal:** <span style='color:{sig_color}; font-size:1.2rem;'>{signal_row['Signal']}</span>", unsafe_allow_html=True)
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Score", signal_row['Score'])
            with col2:
                st.metric("Volume Surge", f"{signal_row['Volume Surge']}x")
            with col3:
                st.metric("RSI", signal_row['RSI'])
            with col4:
                st.metric("Breakout", "✅" if signal_row['Breakout'] else "❌")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Entry", f"${signal_row['Price']:.8f}")
            with col2:
                st.metric("Stop Loss", f"${signal_row['SL']:.8f}")
            with col3:
                st.metric("TP1", f"${signal_row['TP1']:.8f}")
            with col4:
                st.metric("TP2", f"${signal_row['TP2']:.8f}")

else:
    st.info("👆 Click 'Scan Next Batch' to start scanning")

# Footer
st.markdown("---")
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | ⚠️ High risk – use stop losses")
