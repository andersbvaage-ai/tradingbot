import streamlit as st
import yfinance as yf
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Nordic Trading Bot", layout="wide")
st.title("Nordic Trading Bot")

# --- Strategi ---
def SMA(values, n):
    return pd.Series(values).rolling(n).mean()

def RSI(values, n=14):
    delta = pd.Series(values).diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

class SmaRsiStrategy(Strategy):
    sma_fast = 10
    sma_slow = 50
    rsi_period = 14

    def init(self):
        self.sma1 = self.I(SMA, self.data.Close, self.sma_fast)
        self.sma2 = self.I(SMA, self.data.Close, self.sma_slow)
        self.rsi  = self.I(RSI, self.data.Close, self.rsi_period)

    def next(self):
        if crossover(self.sma1, self.sma2) and self.rsi[-1] < 60:
            self.buy()
        elif crossover(self.sma2, self.sma1) or self.rsi[-1] > 70:
            if self.position:
                self.position.close()

# --- Sidebar ---
st.sidebar.header("Innstillinger")

TICKERS = {
    "Equinor (NO)":      "EQNR.OL",
    "Telenor (NO)":      "TEL.OL",
    "Hydro (NO)":        "NHY.OL",
    "Orkla (NO)":        "ORK.OL",
    "Volvo (SE)":        "VOLV-B.ST",
    "Novo Nordisk (DK)": "NOVO-B.CO",
    "Nokia (FI)":        "NOKIA.HE",
    "Skriv inn selv":    "CUSTOM",
}

valgt_navn = st.sidebar.selectbox("Velg aksje", list(TICKERS.keys()))
if TICKERS[valgt_namn := valgt_navn] == "CUSTOM":
    ticker = st.sidebar.text_input("Ticker (f.eks. AAPL, EQNR.OL)", value="EQNR.OL")
else:
    ticker = TICKERS[valgt_navn]

start_dato = st.sidebar.date_input("Fra dato", value=pd.Timestamp("2019-01-01"))
slutt_dato = st.sidebar.date_input("Til dato", value=pd.Timestamp("2024-01-01"))
kapital    = st.sidebar.number_input("Startkapital ($)", value=10000, step=1000)

st.sidebar.subheader("Strategi-parametere")
sma_fast   = st.sidebar.slider("SMA rask", 5, 50, 10)
sma_slow   = st.sidebar.slider("SMA treg", 20, 200, 50)
rsi_period = st.sidebar.slider("RSI periode", 7, 21, 14)

st.sidebar.subheader("Optimalisering")
kjor_optimalisering = st.sidebar.checkbox("Kjør automatisk optimalisering")

# --- Hent data og kjør backtest ---
if st.button("Kjør backtest", type="primary"):
    with st.spinner("Henter data..."):
        data = yf.download(ticker, start=str(start_dato), end=str(slutt_dato), progress=False)
        if data.empty:
            st.error(f"Fant ingen data for {ticker}. Sjekk ticker-koden.")
            st.stop()
        data.columns = data.columns.get_level_values(0)

    SmaRsiStrategy.sma_fast   = sma_fast
    SmaRsiStrategy.sma_slow   = sma_slow
    SmaRsiStrategy.rsi_period = rsi_period

    bt = Backtest(data, SmaRsiStrategy, cash=kapital, commission=0.002)

    if kjor_optimalisering:
        with st.spinner("Optimaliserer parametere... (kan ta litt tid)"):
            stats, heatmap = bt.optimize(
                sma_fast=range(5, 30, 5),
                sma_slow=range(20, 100, 10),
                rsi_period=range(10, 20, 2),
                constraint=lambda p: p.sma_fast < p.sma_slow,
                maximize="Sharpe Ratio",
                return_heatmap=True,
            )
            best = stats._strategy
            st.success(f"Beste parametere: SMA {best.sma_fast}/{best.sma_slow}, RSI {best.rsi_period}")
    else:
        with st.spinner("Kjører backtest..."):
            stats = bt.run()

    # --- Resultater ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Avkastning", f"{stats['Return [%]']:.1f}%")
    col2.metric("Buy & Hold", f"{stats['Buy & Hold Return [%]']:.1f}%")
    col3.metric("Antall handler", int(stats['# Trades']))
    win = stats['Win Rate [%]']
    col4.metric("Vinnprosent", f"{win:.1f}%" if not pd.isna(win) else "N/A")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Sharpe Ratio",   f"{stats['Sharpe Ratio']:.2f}")
    col6.metric("Max Drawdown",   f"{stats['Max. Drawdown [%]']:.1f}%")
    col7.metric("CAGR",           f"{stats['CAGR [%]']:.1f}%")
    pf = stats['Profit Factor']
    col8.metric("Profit Factor",  f"{pf:.2f}" if not pd.isna(pf) else "N/A")

    # --- Equity curve ---
    st.subheader("Equity curve")
    equity = stats["_equity_curve"]["Equity"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=equity.index, y=equity.values, name="Strategi", line=dict(color="#00b4d8")))

    bh_start = equity.iloc[0]
    bh_slutt = bh_start * (1 + stats['Buy & Hold Return [%]'] / 100)
    fig.add_trace(go.Scatter(
        x=[equity.index[0], equity.index[-1]],
        y=[bh_start, bh_slutt],
        name="Buy & Hold",
        line=dict(color="#f77f00", dash="dash")
    ))
    fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig, use_container_width=True)

    # --- Handler ---
    trades = stats["_trades"]
    if not trades.empty:
        st.subheader("Handler")
        vis_cols = ["EntryTime", "ExitTime", "Size", "EntryPrice", "ExitPrice", "ReturnPct", "PnL"]
        vis_cols = [c for c in vis_cols if c in trades.columns]
        st.dataframe(trades[vis_cols].round(2), use_container_width=True)
    else:
        st.info("Ingen avsluttede handler i denne perioden.")
