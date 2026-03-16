import streamlit as st
import yfinance as yf
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
from datetime import datetime

PORTFOLIO_FIL = os.path.join(os.path.dirname(__file__), "portfolio.json")

def les_portefolje():
    with open(PORTFOLIO_FIL, "r") as f:
        return json.load(f)

def lagre_portefolje(p):
    with open(PORTFOLIO_FIL, "w") as f:
        json.dump(p, f, indent=2, default=str)

def hent_siste_kurs(ticker):
    try:
        raw = yf.download(ticker, period="2d", progress=False)
        if raw.empty:
            return None
        raw.columns = raw.columns.get_level_values(0)
        return float(raw["Close"].iloc[-1])
    except Exception:
        return None

st.set_page_config(page_title="Nordic Trading Bot", layout="wide")

# ── Indikatorer ────────────────────────────────────────────────────────────────
def SMA(values, n):
    return pd.Series(values).rolling(n).mean()

def EMA(values, n):
    return pd.Series(values).ewm(span=n, adjust=False).mean()

def RSI(values, n=14):
    delta = pd.Series(values).diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def MACD_line(values, fast=12, slow=26):
    return EMA(values, fast) - EMA(values, slow)

def MACD_signal(values, fast=12, slow=26, signal=9):
    macd = MACD_line(values, fast, slow)
    return macd.ewm(span=signal, adjust=False).mean()

def BB_upper(values, n=20, k=2):
    s = pd.Series(values)
    return s.rolling(n).mean() + k * s.rolling(n).std()

def BB_lower(values, n=20, k=2):
    s = pd.Series(values)
    return s.rolling(n).mean() - k * s.rolling(n).std()

def Momentum(values, n):
    return pd.Series(values).pct_change(n) * 100

# ── Strategier ─────────────────────────────────────────────────────────────────
class SmaRsiStrategy(Strategy):
    sma_fast   = 10
    sma_slow   = 50
    rsi_period = 14
    stop_loss  = 5

    def init(self):
        self.sma1 = self.I(SMA, self.data.Close, self.sma_fast)
        self.sma2 = self.I(SMA, self.data.Close, self.sma_slow)
        self.rsi  = self.I(RSI, self.data.Close, self.rsi_period)

    def next(self):
        if crossover(self.sma1, self.sma2) and self.rsi[-1] < 60:
            price = self.data.Close[-1]
            self.buy(sl=price * (1 - self.stop_loss / 100))
        elif crossover(self.sma2, self.sma1) or self.rsi[-1] > 70:
            if self.position:
                self.position.close()

class MacdStrategy(Strategy):
    fast      = 12
    slow      = 26
    signal    = 9
    stop_loss = 5

    def init(self):
        self.macd = self.I(MACD_line,   self.data.Close, self.fast, self.slow)
        self.sig  = self.I(MACD_signal, self.data.Close, self.fast, self.slow, self.signal)

    def next(self):
        if crossover(self.macd, self.sig):
            price = self.data.Close[-1]
            self.buy(sl=price * (1 - self.stop_loss / 100))
        elif crossover(self.sig, self.macd):
            if self.position:
                self.position.close()

class BollingerStrategy(Strategy):
    bb_period = 20
    bb_std    = 2
    stop_loss = 5

    def init(self):
        self.upper = self.I(BB_upper, self.data.Close, self.bb_period, self.bb_std)
        self.lower = self.I(BB_lower, self.data.Close, self.bb_period, self.bb_std)

    def next(self):
        price = self.data.Close[-1]
        if price <= self.lower[-1] and not self.position:
            self.buy(sl=price * (1 - self.stop_loss / 100))
        elif price >= self.upper[-1] and self.position:
            self.position.close()

class MomentumStrategy(Strategy):
    lookback  = 126   # ~6 måneder
    threshold = 0     # minimum momentum % for kjøp
    stop_loss = 8

    def init(self):
        self.mom = self.I(Momentum, self.data.Close, self.lookback)

    def next(self):
        price = self.data.Close[-1]
        if self.mom[-1] > self.threshold and not self.position:
            self.buy(sl=price * (1 - self.stop_loss / 100))
        elif self.mom[-1] <= 0 and self.position:
            self.position.close()

# ── Strategi-builder ───────────────────────────────────────────────────────────
def hent_strategi_cls(strat_navn, stop_loss_pct, params=None):
    p = params or {}
    if strat_navn == "SMA + RSI":
        SmaRsiStrategy.sma_fast   = p.get("sma_fast",   10)
        SmaRsiStrategy.sma_slow   = p.get("sma_slow",   50)
        SmaRsiStrategy.rsi_period = p.get("rsi_period", 14)
        SmaRsiStrategy.stop_loss  = stop_loss_pct
        return SmaRsiStrategy
    elif strat_navn == "MACD":
        MacdStrategy.fast      = p.get("macd_fast",   12)
        MacdStrategy.slow      = p.get("macd_slow",   26)
        MacdStrategy.signal    = p.get("macd_signal",  9)
        MacdStrategy.stop_loss = stop_loss_pct
        return MacdStrategy
    elif strat_navn == "Bollinger Bands":
        BollingerStrategy.bb_period = p.get("bb_period", 20)
        BollingerStrategy.bb_std    = p.get("bb_std",     2)
        BollingerStrategy.stop_loss = stop_loss_pct
        return BollingerStrategy
    elif strat_navn == "Momentum":
        MomentumStrategy.lookback  = p.get("mom_lookback",   126)
        MomentumStrategy.threshold = p.get("mom_threshold",    0)
        MomentumStrategy.stop_loss = stop_loss_pct
        return MomentumStrategy

# ── Aksjer ─────────────────────────────────────────────────────────────────────
TICKERS = {
    "Equinor (NO)":        "EQNR.OL",
    "Telenor (NO)":        "TEL.OL",
    "Hydro (NO)":          "NHY.OL",
    "Orkla (NO)":          "ORK.OL",
    "DNB Bank (NO)":       "DNB.OL",
    "Mowi (NO)":           "MOWI.OL",
    "Yara (NO)":           "YAR.OL",
    "Volvo (SE)":          "VOLV-B.ST",
    "Ericsson (SE)":       "ERIC-B.ST",
    "H&M (SE)":            "HM-B.ST",
    "Novo Nordisk (DK)":   "NOVO-B.CO",
    "Vestas Wind (DK)":    "VWS.CO",
    "Nokia (FI)":          "NOKIA.HE",
    "Kone (FI)":           "KNEBV.HE",
    "Skriv inn selv":      "CUSTOM",
}
ALLE_TICKERS = {k: v for k, v in TICKERS.items() if v != "CUSTOM"}
MIN_RADER = 200

# ── Oslo Børs – komplett liste ─────────────────────────────────────────────────
OSLO_BORS = {
    "Equinor":              "EQNR.OL",
    "DNB Bank":             "DNB.OL",
    "Mowi":                 "MOWI.OL",
    "Telenor":              "TEL.OL",
    "Norsk Hydro":          "NHY.OL",
    "Orkla":                "ORK.OL",
    "Yara International":   "YAR.OL",
    "Aker BP":              "AKERBP.OL",
    "SalMar":               "SALM.OL",
    "Subsea 7":             "SUBC.OL",
    "Storebrand":           "STB.OL",
    "Gjensidige":           "GJF.OL",
    "SpareBank 1 SR-Bank":  "SRBANK.OL",
    "Kongsberg Gruppen":    "KOG.OL",
    "Aker Solutions":       "AKSO.OL",
    "Scatec":               "SCATC.OL",
    "Nel Hydrogen":         "NEL.OL",
    "Nordic Semiconductor": "NOD.OL",
    "Kahoot":               "KAHOT.OL",
    "AutoStore":            "AUTO.OL",
    "REC Silicon":          "RECSI.OL",
    "TGS":                  "TGS.OL",
    "PGS":                  "PGS.OL",
    "BW Offshore":          "BWO.OL",
    "Golden Ocean":         "GOGL.OL",
    "Flex LNG":             "FLNG.OL",
    "MPC Container Ships":  "MPCC.OL",
    "Borr Drilling":        "BORR.OL",
    "AF Gruppen":           "AFG.OL",
    "Bouvet":               "BOUVET.OL",
    "Odfjell":              "ODF.OL",
    "Aker":                 "AKER.OL",
    "Wallenius Wilhelmsen": "WAWI.OL",
    "Kitron":               "KIT.OL",
    "Tomra Systems":        "TOM.OL",
    "Elkem":                "ELK.OL",
    "Var Energi":           "VAR.OL",
    "Veidekke":             "VEI.OL",
    "Lerøy Seafood":        "LSG.OL",
    "Grieg Seafood":        "GSF.OL",
}

# ── Hjelpefunksjoner ───────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def hent_data(ticker, start, slutt):
    data = yf.download(ticker, start=str(start), end=str(slutt), progress=False)
    if data.empty:
        return None
    data.columns = data.columns.get_level_values(0)
    return data

def vis_metrikker(stats):
    ret = stats["Return [%]"]
    bh  = stats["Buy & Hold Return [%]"]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Avkastning",     f"{ret:.1f}%", f"{ret - bh:.1f}% vs B&H")
    col2.metric("Buy & Hold",     f"{bh:.1f}%")
    col3.metric("Antall handler", int(stats["# Trades"]))
    win = stats["Win Rate [%]"]
    col4.metric("Vinnprosent",    f"{win:.1f}%" if not pd.isna(win) else "N/A")
    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Sharpe Ratio",   f"{stats['Sharpe Ratio']:.2f}")
    col6.metric("Max Drawdown",   f"{stats['Max. Drawdown [%]']:.1f}%")
    col7.metric("CAGR",           f"{stats['CAGR [%]']:.1f}%")
    pf = stats["Profit Factor"]
    col8.metric("Profit Factor",  f"{pf:.2f}" if not pd.isna(pf) else "N/A")

def vis_charts(stats, data):
    equity   = stats["_equity_curve"]["Equity"]
    drawdown = stats["_equity_curve"]["DrawdownPct"] * 100
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=("Kurs + handler", "Equity curve vs Buy & Hold", "Drawdown %"),
        vertical_spacing=0.06)

    fig.add_trace(go.Candlestick(
        x=data.index, open=data["Open"], high=data["High"],
        low=data["Low"], close=data["Close"], name="Kurs",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        showlegend=False), row=1, col=1)

    trades = stats["_trades"]
    if not trades.empty:
        fig.add_trace(go.Scatter(x=trades["EntryTime"], y=trades["EntryPrice"],
            mode="markers", name="Kjøp",
            marker=dict(symbol="triangle-up", color="#26a69a", size=10)), row=1, col=1)
        closed = trades.dropna(subset=["ExitTime"])
        if not closed.empty:
            fig.add_trace(go.Scatter(x=closed["ExitTime"], y=closed["ExitPrice"],
                mode="markers", name="Selg",
                marker=dict(symbol="triangle-down", color="#ef5350", size=10)), row=1, col=1)

    bh_start = equity.iloc[0]
    bh_end   = bh_start * (1 + stats["Buy & Hold Return [%]"] / 100)
    fig.add_trace(go.Scatter(x=equity.index, y=equity.values,
        name="Strategi", line=dict(color="#00b4d8")), row=2, col=1)
    fig.add_trace(go.Scatter(x=[equity.index[0], equity.index[-1]], y=[bh_start, bh_end],
        name="Buy & Hold", line=dict(color="#f77f00", dash="dash")), row=2, col=1)
    fig.add_trace(go.Scatter(x=drawdown.index, y=drawdown.values,
        name="Drawdown", fill="tozeroy",
        line=dict(color="#ef5350"), fillcolor="rgba(239,83,80,0.2)"), row=3, col=1)

    fig.update_layout(height=750, margin=dict(l=0, r=0, t=40, b=0),
                      xaxis_rangeslider_visible=False, template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
st.sidebar.title("Nordic Trading Bot")

PERIODER = {
    "Siste 1 år   (2024–nå)":          ("2024-01-01", "2025-03-01"),
    "Siste 2 år   (2023–nå)":          ("2023-01-01", "2025-03-01"),
    "Siste 3 år   (2022–nå)":          ("2022-01-01", "2025-03-01"),
    "Siste 5 år   (2020–nå)":          ("2020-01-01", "2025-03-01"),
    "Post-covid   (2022–2024)":        ("2022-01-01", "2024-01-01"),
    "Covid-krasj  (2020–2022)":        ("2020-01-01", "2022-01-01"),
    "Bull market  (2019–2021)":        ("2019-01-01", "2021-01-01"),
    "Finanskrise  (2007–2010)":        ("2007-01-01", "2010-01-01"),
    "Lang periode (2015–nå)":          ("2015-01-01", "2025-03-01"),
}

valgt_periode = st.sidebar.selectbox("Tidsperiode", list(PERIODER.keys()), index=2)
start_dato, slutt_dato = PERIODER[valgt_periode]
kapital = st.sidebar.number_input("Startkapital (kr)", value=100000, step=10000)

st.sidebar.subheader("Strategi")
strategi_valg = st.sidebar.radio("Velg strategi",
    ["SMA + RSI", "MACD", "Bollinger Bands", "Momentum"])
stop_loss_pct = st.sidebar.slider("Stop-loss %", 1, 20, 5)

sidebar_params = {}
if strategi_valg == "SMA + RSI":
    sidebar_params["sma_fast"]   = st.sidebar.slider("SMA rask",      5,  50,  10)
    sidebar_params["sma_slow"]   = st.sidebar.slider("SMA treg",     20, 200,  50)
    sidebar_params["rsi_period"] = st.sidebar.slider("RSI periode",   7,  21,  14)
elif strategi_valg == "MACD":
    sidebar_params["macd_fast"]   = st.sidebar.slider("MACD rask",   5,  20, 12)
    sidebar_params["macd_slow"]   = st.sidebar.slider("MACD treg",  15,  50, 26)
    sidebar_params["macd_signal"] = st.sidebar.slider("Signal",       3,  15,  9)
elif strategi_valg == "Bollinger Bands":
    sidebar_params["bb_period"] = st.sidebar.slider("BB periode", 10, 50, 20)
    sidebar_params["bb_std"]    = st.sidebar.slider("BB std",      1,  4,  2)
elif strategi_valg == "Momentum":
    sidebar_params["mom_lookback"]  = st.sidebar.slider("Lookback (dager)", 60, 252, 126)
    sidebar_params["mom_threshold"] = st.sidebar.slider("Min momentum %",  -10,  20,   0)

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(["Backtest", "Sammenlign aksjer", "Optimalisering", "Portefølje", "Walk-Forward", "Oslo Børs Screener", "Porteføljestyrer"])

# ─── TAB 1: BACKTEST ──────────────────────────────────────────────────────────
with tab1:
    valgt_navn = st.selectbox("Velg aksje", list(TICKERS.keys()))
    ticker = st.text_input("Ticker", value="EQNR.OL") if TICKERS[valgt_navn] == "CUSTOM" else TICKERS[valgt_navn]

    if st.button("Kjør backtest", type="primary"):
        with st.spinner("Henter data..."):
            data = hent_data(ticker, start_dato, slutt_dato)
        if data is None:
            st.error(f"Fant ingen data for {ticker}.")
        else:
            strategi_cls = hent_strategi_cls(strategi_valg, stop_loss_pct, sidebar_params)
            with st.spinner("Kjører backtest..."):
                bt    = Backtest(data, strategi_cls, cash=kapital, commission=0.002)
                stats = bt.run()
            vis_metrikker(stats)
            vis_charts(stats, data)
            trades = stats["_trades"]
            if not trades.empty:
                st.subheader("Handler")
                vis_cols = [c for c in ["EntryTime","ExitTime","EntryPrice","ExitPrice","ReturnPct","PnL"] if c in trades.columns]
                st.dataframe(trades[vis_cols].round(2), use_container_width=True)
            else:
                st.info("Ingen avsluttede handler i denne perioden.")

# ─── TAB 2: SAMMENLIGN ────────────────────────────────────────────────────────
with tab2:
    valgte = st.multiselect("Velg aksjer å sammenligne", list(ALLE_TICKERS.keys()),
        default=["Equinor (NO)", "Telenor (NO)", "Hydro (NO)", "Volvo (SE)"])

    if st.button("Sammenlign", type="primary"):
        strategi_cls = hent_strategi_cls(strategi_valg, stop_loss_pct, sidebar_params)
        rader = []
        prog  = st.progress(0)
        for i, navn in enumerate(valgte):
            data = hent_data(ALLE_TICKERS[navn], start_dato, slutt_dato)
            if data is None or len(data) < MIN_RADER:
                continue
            res = Backtest(data, strategi_cls, cash=kapital, commission=0.002).run()
            win = res["Win Rate [%]"]
            rader.append({"Aksje": navn,
                "Avkastning %": round(res["Return [%]"], 1),
                "Buy & Hold %": round(res["Buy & Hold Return [%]"], 1),
                "CAGR %":       round(res["CAGR [%]"], 1),
                "Sharpe":       round(res["Sharpe Ratio"], 2),
                "Drawdown %":   round(res["Max. Drawdown [%]"], 1),
                "Handler":      int(res["# Trades"]),
                "Win %":        round(win, 1) if not pd.isna(win) else None})
            prog.progress((i + 1) / len(valgte))

        if rader:
            df = pd.DataFrame(rader).sort_values("Avkastning %", ascending=False)
            st.dataframe(df, use_container_width=True)
            fig = go.Figure()
            for r in rader:
                fig.add_trace(go.Bar(name=r["Aksje"],
                    x=["Strategi", "Buy & Hold"], y=[r["Avkastning %"], r["Buy & Hold %"]]))
            fig.update_layout(barmode="group", title="Avkastning sammenligning",
                              template="plotly_dark", height=400)
            st.plotly_chart(fig, use_container_width=True)

# ─── TAB 3: OPTIMALISERING ────────────────────────────────────────────────────
with tab3:
    valgt_opt  = st.selectbox("Velg aksje for optimalisering", list(ALLE_TICKERS.keys()))
    maks_param = st.selectbox("Optimaliser for", ["Sharpe Ratio", "Return [%]", "CAGR [%]"])

    if st.button("Kjør optimalisering", type="primary"):
        data = hent_data(ALLE_TICKERS[valgt_opt], start_dato, slutt_dato)
        if data is None:
            st.error("Fant ingen data.")
        else:
            bt = Backtest(data, SmaRsiStrategy, cash=kapital, commission=0.002)
            with st.spinner("Optimaliserer... (30-60 sekunder)"):
                stats, heatmap = bt.optimize(
                    sma_fast=range(5, 35, 5), sma_slow=range(20, 110, 10),
                    rsi_period=range(10, 22, 2), stop_loss=range(3, 12, 2),
                    constraint=lambda p: p.sma_fast < p.sma_slow,
                    maximize=maks_param, return_heatmap=True)
            best = stats._strategy
            st.success(f"Beste parametere: SMA {best.sma_fast}/{best.sma_slow} | RSI {best.rsi_period} | Stop-loss {best.stop_loss}%")
            vis_metrikker(stats)
            st.subheader("Heatmap: SMA rask vs treg")
            try:
                hm = heatmap.groupby(["sma_fast", "sma_slow"]).mean().unstack()
                fig_hm = go.Figure(go.Heatmap(
                    z=hm.values, x=hm.columns.get_level_values(1).tolist(),
                    y=hm.index.tolist(), colorscale="RdYlGn", zmid=0,
                    text=hm.values.round(2), texttemplate="%{text}",
                    colorbar=dict(title=maks_param)))
                fig_hm.update_layout(xaxis_title="SMA treg", yaxis_title="SMA rask",
                                     template="plotly_dark", height=400)
                st.plotly_chart(fig_hm, use_container_width=True)
            except Exception:
                st.info("Heatmap ikke tilgjengelig.")

# ─── TAB 4: PORTEFØLJE ────────────────────────────────────────────────────────
with tab4:
    st.subheader("Porteføljeanalyse")
    st.caption("Tester alle strategier mot alle aksjer og bygger en optimal portefølje.")

    col_l, col_r = st.columns([1, 3])
    with col_l:
        top_n = st.slider("Topp N kombinasjoner", 3, 14, 5)
        sort_by = st.selectbox("Ranger etter", ["Sharpe", "Avkastning %", "CAGR %"])
        inkluder = st.multiselect("Strategier å teste",
            ["SMA + RSI", "MACD", "Bollinger Bands", "Momentum"],
            default=["SMA + RSI", "MACD", "Bollinger Bands", "Momentum"])
        kjor_btn = st.button("Kjør porteføljeanalyse", type="primary")

    if kjor_btn:
        alle_res    = []
        alle_equity = {}
        total_jobs  = len(ALLE_TICKERS) * len(inkluder)
        job_n       = 0
        prog        = col_r.progress(0)

        for aksje_navn, ticker in ALLE_TICKERS.items():
            data = hent_data(ticker, start_dato, slutt_dato)
            if data is None or len(data) < MIN_RADER:
                job_n += len(inkluder)
                prog.progress(job_n / total_jobs)
                continue
            for strat_navn in inkluder:
                try:
                    cls = hent_strategi_cls(strat_navn, stop_loss_pct)
                    res = Backtest(data, cls, cash=kapital, commission=0.002).run()
                    sharpe = res["Sharpe Ratio"]
                    alle_res.append({
                        "Aksje":        aksje_navn,
                        "Strategi":     strat_navn,
                        "Avkastning %": round(res["Return [%]"], 1),
                        "B&H %":        round(res["Buy & Hold Return [%]"], 1),
                        "CAGR %":       round(res["CAGR [%]"], 1),
                        "Sharpe":       round(sharpe, 2) if not pd.isna(sharpe) else -99,
                        "Drawdown %":   round(res["Max. Drawdown [%]"], 1),
                        "Handler":      int(res["# Trades"]),
                    })
                    equity = res["_equity_curve"]["Equity"]
                    alle_equity[f"{aksje_navn}|{strat_navn}"] = equity
                except Exception:
                    pass
                job_n += 1
                prog.progress(job_n / total_jobs)

        st.session_state["portfolio_results"] = (alle_res, alle_equity)

    if "portfolio_results" in st.session_state:
        alle_res, alle_equity = st.session_state["portfolio_results"]
        df_res = pd.DataFrame(alle_res)
        df_res["Sharpe"] = df_res["Sharpe"].fillna(-99)

        # Heatmap
        st.subheader("Sharpe Ratio: alle aksjer × alle strategier")
        try:
            hm = df_res.pivot(index="Aksje", columns="Strategi", values="Sharpe")
            fig_hm = go.Figure(go.Heatmap(
                z=hm.values, x=hm.columns.tolist(), y=hm.index.tolist(),
                colorscale="RdYlGn", zmid=0,
                text=hm.values.round(2), texttemplate="%{text}",
                colorbar=dict(title="Sharpe")))
            fig_hm.update_layout(template="plotly_dark", height=500,
                                  xaxis_title="Strategi", yaxis_title="Aksje")
            st.plotly_chart(fig_hm, use_container_width=True)
        except Exception:
            pass

        # Topp N tabell
        df_sorted = df_res.sort_values(sort_by, ascending=False)
        df_topp   = df_sorted.head(top_n).reset_index(drop=True)
        st.subheader(f"Topp {top_n} kombinasjoner")
        st.dataframe(df_topp, use_container_width=True)

        # Portefølje equity curve
        kurver = []
        for _, rad in df_topp.iterrows():
            key = f"{rad['Aksje']}|{rad['Strategi']}"
            if key in alle_equity:
                eq = alle_equity[key]
                kurver.append(eq / eq.iloc[0])

        if len(kurver) >= 2:
            felles_index = kurver[0].index
            for k in kurver[1:]:
                felles_index = felles_index.union(k.index)

            aligned      = [k.reindex(felles_index, method="ffill") for k in kurver]
            df_port      = pd.concat(aligned, axis=1).dropna(how="all")
            port_equity  = df_port.mean(axis=1) * kapital

            # Buy & Hold benchmark
            bh_kurver = []
            for _, rad in df_topp.iterrows():
                d = hent_data(ALLE_TICKERS[rad["Aksje"]], start_dato, slutt_dato)
                if d is not None:
                    bh_norm = d["Close"] / d["Close"].iloc[0]
                    bh_kurver.append(bh_norm.reindex(felles_index, method="ffill"))
            bh_equity = pd.concat(bh_kurver, axis=1).mean(axis=1) * kapital

            fig_port = go.Figure()
            fig_port.add_trace(go.Scatter(x=port_equity.index, y=port_equity.values,
                name=f"Portefølje (topp {top_n})", line=dict(color="#00b4d8", width=2)))
            fig_port.add_trace(go.Scatter(x=bh_equity.index, y=bh_equity.values,
                name="Equal-weight Buy & Hold", line=dict(color="#f77f00", dash="dash")))

            # Individuelle kurver (skjult som standard)
            labels = [f"{r['Aksje']} / {r['Strategi']}" for _, r in df_topp.iterrows()]
            for i, col in enumerate(df_port.columns):
                fig_port.add_trace(go.Scatter(
                    x=df_port.index, y=df_port.iloc[:, i] * kapital,
                    name=labels[i] if i < len(labels) else col,
                    line=dict(width=1), opacity=0.4, visible="legendonly"))

            fig_port.update_layout(title="Portefølje equity curve",
                                   template="plotly_dark", height=500,
                                   yaxis_title="Kapital (kr)")
            st.plotly_chart(fig_port, use_container_width=True)

            total_ret = (port_equity.iloc[-1] / port_equity.iloc[0] - 1) * 100
            bh_ret    = (bh_equity.dropna().iloc[-1] / bh_equity.dropna().iloc[0] - 1) * 100
            c1, c2, c3 = st.columns(3)
            c1.metric("Portefølje avkastning", f"{total_ret:.1f}%", f"{total_ret - bh_ret:.1f}% vs B&H")
            c2.metric("Buy & Hold avkastning", f"{bh_ret:.1f}%")
            c3.metric("Antall kombinasjoner",  top_n)
        else:
            st.warning("Ikke nok gyldige kombinasjoner til å bygge portefølje.")

# ─── TAB 5: WALK-FORWARD ──────────────────────────────────────────────────────
with tab5:
    st.subheader("Walk-Forward analyse")
    st.caption(
        "Optimaliserer parametere på treningsdata, tester på ukjent data. "
        "Gir et realistisk bilde av hvordan strategien vil prestere fremover."
    )

    wf_aksje   = st.selectbox("Aksje", list(ALLE_TICKERS.keys()), key="wf_aksje")
    col_a, col_b, col_c = st.columns(3)
    train_mnd  = col_a.slider("Treningsperiode (måneder)", 6, 24, 12)
    test_mnd   = col_b.slider("Testperiode (måneder)",     3, 12,  6)
    maks_wf    = col_c.selectbox("Optimaliser for", ["Sharpe Ratio", "Return [%]", "CAGR [%]"], key="wf_maks")

    st.info(
        f"Med perioden **{valgt_periode}** og {train_mnd} mnd trening / {test_mnd} mnd test "
        f"får du ca. **{max(1, (pd.Timestamp(slutt_dato) - pd.Timestamp(start_dato)).days // (test_mnd * 30))} vinduer**."
    )

    if st.button("Kjør walk-forward", type="primary"):
        data_full = hent_data(ALLE_TICKERS[wf_aksje], start_dato, slutt_dato)
        if data_full is None or len(data_full) < MIN_RADER:
            st.error("Ikke nok data for denne perioden.")
        else:
            from dateutil.relativedelta import relativedelta

            start_dt = pd.Timestamp(start_dato)
            slutt_dt = pd.Timestamp(slutt_dato)

            # Generer vinduer
            vinduer = []
            current = start_dt
            while True:
                train_end = current + relativedelta(months=train_mnd)
                test_end  = train_end + relativedelta(months=test_mnd)
                if test_end > slutt_dt:
                    break
                vinduer.append((current, train_end, train_end, test_end))
                current = current + relativedelta(months=test_mnd)

            if not vinduer:
                st.error("Perioden er for kort for disse innstillingene. Prøv kortere trening/test eller lengre periode.")
            else:
                oos_kurver   = []   # out-of-sample equity stykker
                is_kurver    = []   # in-sample equity stykker
                param_rader  = []
                prog = st.progress(0)

                for i, (tr_start, tr_end, te_start, te_end) in enumerate(vinduer):
                    train_data = data_full[(data_full.index >= tr_start) & (data_full.index < tr_end)]
                    test_data  = data_full[(data_full.index >= te_start) & (data_full.index < te_end)]

                    if len(train_data) < MIN_RADER or len(test_data) < 20:
                        prog.progress((i + 1) / len(vinduer))
                        continue

                    try:
                        # Optimaliser på treningsdata
                        bt_train = Backtest(train_data, SmaRsiStrategy, cash=kapital, commission=0.002)
                        best_stats = bt_train.optimize(
                            sma_fast=range(5, 30, 5),
                            sma_slow=range(20, 80, 10),
                            rsi_period=range(10, 22, 4),
                            stop_loss=range(3, 12, 3),
                            constraint=lambda p: p.sma_fast < p.sma_slow,
                            maximize=maks_wf,
                        )
                        best = best_stats._strategy
                        is_ret = best_stats["Return [%]"]

                        # Test på ukjente data med beste parametere
                        SmaRsiStrategy.sma_fast   = best.sma_fast
                        SmaRsiStrategy.sma_slow   = best.sma_slow
                        SmaRsiStrategy.rsi_period = best.rsi_period
                        SmaRsiStrategy.stop_loss  = best.stop_loss

                        bt_test  = Backtest(test_data, SmaRsiStrategy, cash=kapital, commission=0.002)
                        oos_stats = bt_test.run()
                        oos_ret  = oos_stats["Return [%]"]

                        # Normaliser equity-kurver
                        oos_eq = oos_stats["_equity_curve"]["Equity"]
                        is_eq  = best_stats["_equity_curve"]["Equity"]
                        oos_kurver.append(oos_eq / oos_eq.iloc[0])
                        is_kurver.append(is_eq  / is_eq.iloc[0])

                        param_rader.append({
                            "Vindu":       f"{te_start.strftime('%Y-%m')} → {te_end.strftime('%Y-%m')}",
                            "SMA":         f"{best.sma_fast}/{best.sma_slow}",
                            "RSI":         best.rsi_period,
                            "Stop-loss %": best.stop_loss,
                            "IS avk. %":   round(is_ret, 1),
                            "OOS avk. %":  round(oos_ret, 1),
                            "OOS Sharpe":  round(oos_stats["Sharpe Ratio"], 2) if not pd.isna(oos_stats["Sharpe Ratio"]) else None,
                        })
                    except Exception:
                        pass

                    prog.progress((i + 1) / len(vinduer))

                if not param_rader:
                    st.error("Ingen vinduer ga gyldige resultater.")
                else:
                    # Tabell med parametere og resultater per vindu
                    st.subheader("Resultater per vindu")
                    df_wf = pd.DataFrame(param_rader)
                    st.dataframe(df_wf, use_container_width=True)

                    # Sammenstilt OOS equity curve
                    st.subheader("Sammenstilt out-of-sample equity curve")

                    # Bygg sammenhengende kurve ved å kjede segmentene
                    alle_x, alle_y = [], []
                    verdi = float(kapital)
                    for seg in oos_kurver:
                        y_vals = seg.values * verdi
                        alle_x.extend(seg.index.tolist())
                        alle_y.extend(y_vals.tolist())
                        verdi = y_vals[-1]

                    # Buy & Hold for samme periode
                    bh_start_idx = data_full.index[data_full.index >= pd.Timestamp(vinduer[0][2])][0]
                    bh_end_idx   = data_full.index[data_full.index <= pd.Timestamp(vinduer[-1][3])][-1]
                    bh_data      = data_full.loc[bh_start_idx:bh_end_idx, "Close"]
                    bh_y         = (bh_data / bh_data.iloc[0] * kapital).values

                    fig_wf = go.Figure()
                    fig_wf.add_trace(go.Scatter(x=alle_x, y=alle_y,
                        name="Walk-Forward OOS", line=dict(color="#00b4d8", width=2)))
                    fig_wf.add_trace(go.Scatter(x=bh_data.index, y=bh_y,
                        name="Buy & Hold", line=dict(color="#f77f00", dash="dash")))

                    # Marker skiller mellom vinduer
                    for _, _, te_start, _ in vinduer:
                        fig_wf.add_vline(x=te_start, line_width=1,
                            line_dash="dot", line_color="rgba(255,255,255,0.2)")

                    fig_wf.update_layout(template="plotly_dark", height=450,
                        yaxis_title="Kapital (kr)",
                        title="OOS = hva strategien faktisk ville gjort på ukjent data")
                    st.plotly_chart(fig_wf, use_container_width=True)

                    # Oppsummering
                    oos_total = (alle_y[-1] / kapital - 1) * 100
                    bh_total  = (bh_y[-1]  / kapital - 1) * 100
                    is_snitt  = df_wf["IS avk. %"].mean()
                    oos_snitt = df_wf["OOS avk. %"].mean()

                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("OOS total avkastning",    f"{oos_total:.1f}%", f"{oos_total - bh_total:.1f}% vs B&H")
                    c2.metric("Buy & Hold avkastning",   f"{bh_total:.1f}%")
                    c3.metric("Snitt IS avkastning",     f"{is_snitt:.1f}%")
                    c4.metric("Snitt OOS avkastning",    f"{oos_snitt:.1f}%")

                    if oos_snitt > 0:
                        st.success("Strategien er robust — den holder seg på ukjent data.")
                    elif oos_snitt > -5:
                        st.warning("Strategien er moderat robust — noe overfit til treningsdata.")
                    else:
                        st.error("Strategien er overfittet — den fungerer på treningsdata men ikke i virkeligheten.")

# ─── TAB 6: OSLO BØRS SCREENER ────────────────────────────────────────────────
with tab6:
    st.subheader("Oslo Børs Screener")
    st.caption("Scanner alle aksjer og viser hvem som har kjøpssignal akkurat nå.")

    col_s1, col_s2 = st.columns([2, 1])
    with col_s2:
        min_score = st.slider(
            "Minimum signalstyrke",
            min_value=0, max_value=4, value=2,
            help=(
                "Antall indikatorer (av 4) som må være positive for at aksjen vises:\n\n"
                "**0** — vis alle aksjer\n\n"
                "**1** — minst én indikator positiv\n\n"
                "**2** — minst to indikatorer positive (anbefalt)\n\n"
                "**3** — tre av fire indikatorer positive (sterk kandidat)\n\n"
                "**4** — alle fire indikatorer enige om kjøp (sjelden, men sterkest signal)"
            )
        )
        vis_alle  = st.checkbox("Vis alle (inkl. uten signal)", value=False)

    if st.button("Scan Oslo Børs", type="primary"):
        rader = []
        prog  = st.progress(0)

        for i, (navn, ticker) in enumerate(OSLO_BORS.items()):
            try:
                raw = yf.download(ticker, period="1y", progress=False)
                if raw.empty or len(raw) < 60:
                    prog.progress((i + 1) / len(OSLO_BORS))
                    continue
                raw.columns = raw.columns.get_level_values(0)
                close = raw["Close"]

                # Beregn indikatorer på nåværende data
                sma10  = float(close.rolling(10).mean().iloc[-1])
                sma50  = float(close.rolling(50).mean().iloc[-1])
                pris   = float(close.iloc[-1])

                delta  = close.diff()
                gain   = delta.clip(lower=0).rolling(14).mean()
                loss   = (-delta.clip(upper=0)).rolling(14).mean()
                rs     = gain / loss
                rsi    = float((100 - 100 / (1 + rs)).iloc[-1])

                ema12  = close.ewm(span=12).mean()
                ema26  = close.ewm(span=26).mean()
                macd   = ema12 - ema26
                signal = macd.ewm(span=9).mean()
                macd_v = float(macd.iloc[-1])
                sig_v  = float(signal.iloc[-1])

                mom    = float(close.pct_change(126).iloc[-1] * 100) if len(close) >= 126 else None

                # Signaler (hver gir 1 poeng)
                sma_signal  = sma10 > sma50
                rsi_signal  = 40 < rsi < 65
                macd_signal = macd_v > sig_v
                mom_signal  = mom is not None and mom > 0

                score = sum([sma_signal, rsi_signal, macd_signal, mom_signal])

                # Anbefaling
                if score >= 4:
                    anbefaling = "Sterkt kjøp"
                elif score == 3:
                    anbefaling = "Kjøp"
                elif score == 2:
                    anbefaling = "Nøytral"
                elif score == 1:
                    anbefaling = "Svak"
                else:
                    anbefaling = "Selg / unngå"

                rader.append({
                    "Aksje":       navn,
                    "Kurs":        round(pris, 2),
                    "SMA10>50":    "✅" if sma_signal  else "❌",
                    "RSI (40-65)": "✅" if rsi_signal  else "❌",
                    "MACD":        "✅" if macd_signal else "❌",
                    "Momentum":    "✅" if mom_signal  else "❌",
                    "Score":       score,
                    "RSI verdi":   round(rsi, 1),
                    "Mom 6mnd %":  round(mom, 1) if mom else None,
                    "Signal":      anbefaling,
                    "_score":      score,
                })
            except Exception:
                pass
            prog.progress((i + 1) / len(OSLO_BORS))

        if rader:
            df_screen = pd.DataFrame(rader)
            df_screen = df_screen.sort_values("Score", ascending=False)

            if not vis_alle:
                df_screen = df_screen[df_screen["Score"] >= min_score]

            vis = df_screen.drop(columns=["_score"])
            st.dataframe(vis, use_container_width=True, hide_index=True)

            # Oppsummering
            sterkt = len(df_screen[df_screen["Score"] == 4])
            kjop   = len(df_screen[df_screen["Score"] == 3])
            noyt   = len(df_screen[df_screen["Score"] == 2])
            selg   = len(df_screen[df_screen["Score"] <= 1])

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Sterkt kjøp (4/4)", sterkt)
            c2.metric("Kjøp (3/4)",        kjop)
            c3.metric("Nøytral (2/4)",     noyt)
            c4.metric("Svak/Selg (0-1/4)", selg)

            # Bar chart
            topp = df_screen[df_screen["Score"] >= 3].head(15)
            if not topp.empty:
                st.subheader("Sterkeste kjøpssignaler")
                fig_sc = go.Figure(go.Bar(
                    x=topp["Aksje"], y=topp["Score"],
                    marker_color=["#26a69a" if s == 4 else "#66bb6a" for s in topp["Score"]],
                    text=topp["Signal"], textposition="outside",
                ))
                fig_sc.update_layout(
                    template="plotly_dark", height=350,
                    yaxis=dict(range=[0, 4.5], title="Signalstyrke"),
                    margin=dict(t=20, b=0),
                )
                st.plotly_chart(fig_sc, use_container_width=True)

# ─── TAB 7: PORTEFØLJESTYRER ──────────────────────────────────────────────────
with tab7:
    st.subheader("Porteføljestyrer (Paper Trading)")
    st.info(
        "**Hvordan det fungerer:**\n\n"
        "1. **Analyse** — Boten scanner alle aksjer på Oslo Børs og beregner fire indikatorer: "
        "SMA-trend, RSI, MACD og 6-måneders momentum.\n\n"
        "2. **Rangering** — Aksjene rangeres etter signalstyrke (0–4 poeng). "
        "De med sterkest signal plukkes ut som kandidater.\n\n"
        "3. **Allokering** — Boten fordeler kapitalen enten likt eller vektet etter signalstyrke, "
        "momentum og lav volatilitet.\n\n"
        "4. **Forslag** — Du ser konkrete kjøps- og salgsforslag med begrunnelse. "
        "Du godkjenner eller avviser hvert forslag med ett klikk.\n\n"
        "5. **Portefølje** — Godkjente handler lagres og porteføljen oppdateres med nåværende kurs og gevinst/tap."
    )

    pf = les_portefolje()

    # ── Kasse-editor ──────────────────────────────────────────────────────────
    with st.container():
        col_k1, col_k2, col_k3 = st.columns([2, 1, 1])
        ny_kasse_verdi = col_k1.number_input(
            "Kasse (kr)", value=int(pf["kasse"]), step=1000, label_visibility="collapsed"
        )
        if col_k2.button("Oppdater kasse"):
            pf["kasse"] = ny_kasse_verdi
            if "start_kapital" not in pf:
                pf["start_kapital"] = ny_kasse_verdi
            lagre_portefolje(pf)
            st.success(f"Kasse oppdatert til {ny_kasse_verdi:,.0f} kr")
            st.rerun()
        col_k3.caption(f"Nåværende: **{pf['kasse']:,.0f} kr**")

    # ── Oversikt ──────────────────────────────────────────────────────────────
    st.markdown("### Nåværende portefølje")
    kasse = pf["kasse"]

    if pf["posisjoner"]:
        pos_rader = []
        total_verdi = kasse
        for ticker, pos in pf["posisjoner"].items():
            kurs = hent_siste_kurs(ticker)
            if kurs:
                verdi    = kurs * pos["antall"]
                gevinst  = (kurs - pos["snittpris"]) * pos["antall"]
                gevinst_pct = (kurs / pos["snittpris"] - 1) * 100
                total_verdi += verdi
                pos_rader.append({
                    "Aksje":      pos["navn"],
                    "Ticker":     ticker,
                    "Antall":     pos["antall"],
                    "Snittpris":  round(pos["snittpris"], 2),
                    "Kurs nå":    round(kurs, 2),
                    "Verdi (kr)": round(verdi, 0),
                    "Gevinst (kr)": round(gevinst, 0),
                    "Gevinst %":  round(gevinst_pct, 1),
                })

        df_pos = pd.DataFrame(pos_rader)
        st.dataframe(df_pos, use_container_width=True, hide_index=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Kasse",         f"{kasse:,.0f} kr")
        c2.metric("Total verdi",   f"{total_verdi:,.0f} kr")
        c3.metric("Avkastning",    f"{(total_verdi / pf.get('start_kapital', total_verdi) - 1) * 100:.1f}%"
                  if "start_kapital" in pf else "—")
    else:
        st.info("Ingen posisjoner ennå. Kjør analyse for å få forslag.")
        c1, c2 = st.columns(2)
        c1.metric("Kasse", f"{kasse:,.0f} kr")

    st.divider()

    # ── Kjør analyse og generer forslag ──────────────────────────────────────
    st.markdown("### Botens handelsforslag")

    maks_posisjoner = st.slider("Maks antall posisjoner", 3, 15, 6)

    allokering_metode = st.radio(
        "Allokeringsmetode",
        ["Fast % per posisjon", "Bot-styrt vekting"],
        horizontal=True,
        help=(
            "**Fast %** — du bestemmer hvor mye av kassen som går til hver aksje.\n\n"
            "**Bot-styrt** — boten vekter basert på signalstyrke, momentum og lav volatilitet. "
            "Sterkere signal = større posisjon."
        )
    )

    if allokering_metode == "Fast % per posisjon":
        allokering_pct = st.slider("Allokering per posisjon (%)", 5, 20, 15,
            help="Maks 20% per posisjon for å unngå for stor konsentrasjon i én aksje")
    else:
        st.caption("Boten fordeler kassen basert på: signalstyrke (40%) + momentum (30%) + relativ styrke (20%) + lav volatilitet (10%)")

    st.divider()
    st.markdown("**Oppsidefokus**")
    col_f1, col_f2 = st.columns(2)
    maks_cap = col_f1.selectbox(
        "Maks markedsverdi",
        ["Alle størrelser", "Maks 50 mrd kr (ekskl. giganter)", "Maks 10 mrd kr (mid/small cap)"],
        help="Filtrer vekk store selskaper med lav vekstpotensial"
    )
    min_rel_styrke = col_f2.slider(
        "Min relativ styrke vs Oslo Børs (%)",
        min_value=-20, max_value=20, value=0,
        help="Vis kun aksjer som har gjort det bedre enn OSEBX siste 3 mnd"
    )

    if st.button("Kjør analyse og generer forslag", type="primary"):
        with st.spinner("Scanner Oslo Børs..."):
            kandidater = []
            for navn, ticker in OSLO_BORS.items():
                try:
                    # Markedsverdi-filter
                    if maks_cap != "Alle størrelser":
                        info = yf.Ticker(ticker).info
                        cap  = info.get("marketCap", 0)
                        grense = 50e9 if "50" in maks_cap else 10e9
                        if cap > grense:
                            continue

                    raw = yf.download(ticker, period="1y", progress=False)
                    if raw.empty or len(raw) < 60:
                        continue
                    raw.columns = raw.columns.get_level_values(0)
                    close  = raw["Close"]
                    volume = raw["Volume"]
                    pris   = float(close.iloc[-1])

                    # Klassiske signaler
                    sma10  = float(close.rolling(10).mean().iloc[-1])
                    sma50  = float(close.rolling(50).mean().iloc[-1])
                    delta  = close.diff()
                    gain   = delta.clip(lower=0).rolling(14).mean()
                    loss   = (-delta.clip(upper=0)).rolling(14).mean()
                    rsi    = float((100 - 100 / (1 + gain / loss)).iloc[-1])
                    ema12  = close.ewm(span=12).mean()
                    ema26  = close.ewm(span=26).mean()
                    macd_v = float((ema12 - ema26).iloc[-1])
                    sig_v  = float((ema12 - ema26).ewm(span=9).mean().iloc[-1])
                    mom    = float(close.pct_change(126).iloc[-1] * 100) if len(close) >= 126 else 0

                    # Relativ styrke vs OSEBX (siste 63 dager ≈ 3 mnd)
                    osebx_ret = 0.0
                    try:
                        osebx = yf.download("^OSEBX", period="6mo", progress=False)
                        osebx.columns = osebx.columns.get_level_values(0)
                        if len(osebx) >= 63:
                            osebx_ret = float(osebx["Close"].pct_change(63).iloc[-1] * 100)
                    except Exception:
                        pass
                    aksje_ret3m = float(close.pct_change(63).iloc[-1] * 100) if len(close) >= 63 else 0
                    rel_styrke  = aksje_ret3m - osebx_ret

                    # Volumøkning (siste 10 dager vs siste 50 dager)
                    vol10 = float(volume.rolling(10).mean().iloc[-1])
                    vol50 = float(volume.rolling(50).mean().iloc[-1])
                    vol_økning = (vol10 / vol50 - 1) * 100 if vol50 > 0 else 0

                    # Nærhet til 52-ukers høy (høyere = sterkere momentum)
                    høy52 = float(close.rolling(252).max().iloc[-1])
                    nærhet_topp = (pris / høy52) * 100  # 100 = på topp

                    # Relativ styrke-filter
                    if rel_styrke < min_rel_styrke:
                        continue

                    # Score (0-4 klassiske + oppsidebonus)
                    score = sum([
                        sma10 > sma50,
                        40 < rsi < 65,
                        macd_v > sig_v,
                        mom > 0,
                    ])

                    # Oppsidebonus: belønner vekstegenskaper
                    oppside_score = (
                        (rel_styrke / 10)        +   # relativ styrke
                        (vol_økning / 50)        +   # volumøkning
                        (nærhet_topp / 100)          # nærhet til 52-ukers høy
                    )

                    kandidater.append({
                        "navn": navn, "ticker": ticker, "kurs": pris,
                        "score": score, "rsi": rsi, "mom": mom,
                        "rel_styrke": rel_styrke, "vol_økning": vol_økning,
                        "nærhet_topp": nærhet_topp, "oppside_score": oppside_score,
                    })
                except Exception:
                    pass

        # Sorter på kombinert score: klassisk + oppside
        kandidater.sort(
            key=lambda x: (x["score"] + x["oppside_score"]),
            reverse=True
        )
        topp = kandidater[:maks_posisjoner]

        # Beregn vekter
        if allokering_metode == "Bot-styrt vekting" and topp:
            # Normaliser hver komponent til 0-1
            scores  = [k["score"] / 4 for k in topp]
            moms    = [max(0, k["mom"]) for k in topp]
            max_mom = max(moms) or 1
            moms    = [m / max_mom for m in moms]
            vols    = []
            for k in topp:
                try:
                    raw = yf.download(k["ticker"], period="3mo", progress=False)
                    raw.columns = raw.columns.get_level_values(0)
                    vol = float(raw["Close"].pct_change().std())
                    vols.append(vol)
                except Exception:
                    vols.append(0.02)
            max_vol = max(vols) or 0.02
            inv_vols = [(max_vol - v) / max_vol for v in vols]  # lav vol = høy vekt

            rel_styrker = [max(0, k["rel_styrke"]) for k in topp]
            max_rs = max(rel_styrker) or 1
            rel_styrker = [r / max_rs for r in rel_styrker]
            raw_vekter = [0.4*s + 0.3*m + 0.2*r + 0.1*v
                          for s, m, r, v in zip(scores, moms, rel_styrker, inv_vols)]
            sum_vekter = sum(raw_vekter) or 1
            vekter     = [w / sum_vekter for w in raw_vekter]
        else:
            vekter = [allokering_pct / 100] * len(topp)

        # Generer forslag
        forslag = []
        nåværende = set(pf["posisjoner"].keys())

        for k, vekt in zip(topp, vekter):
            ticker = k["ticker"]
            beløp  = kasse * vekt
            antall = int(beløp / k["kurs"])
            if antall < 1:
                continue
            vekt_tekst = f"{vekt*100:.1f}% av kasse"
            if ticker in nåværende:
                forslag.append({
                    "handling": "HOLD",
                    "navn": k["navn"], "ticker": ticker,
                    "kurs": k["kurs"], "antall": antall,
                    "beløp": round(antall * k["kurs"], 0),
                    "score": k["score"],
                    "begrunnelse": f"Score {k['score']}/4 · {vekt_tekst} · allerede i portefølje"
                })
            else:
                forslag.append({
                    "handling": "KJØP",
                    "navn": k["navn"], "ticker": ticker,
                    "kurs": k["kurs"], "antall": antall,
                    "beløp": round(antall * k["kurs"], 0),
                    "score": k["score"],
                    "begrunnelse": f"Score {k['score']}/4 · mom {k['mom']:.1f}% · rel.styrke {k['rel_styrke']:.1f}% · vol↑{k['vol_økning']:.0f}% · {vekt_tekst}"
                })

        # Selg det som ikke lenger er blant topp-kandidatene
        topp_tickers = {k["ticker"] for k in topp}
        for ticker, pos in pf["posisjoner"].items():
            if ticker not in topp_tickers:
                kurs = hent_siste_kurs(ticker)
                if kurs:
                    forslag.append({
                        "handling": "SELG",
                        "navn": pos["navn"], "ticker": ticker,
                        "kurs": kurs, "antall": pos["antall"],
                        "beløp": round(pos["antall"] * kurs, 0),
                        "score": 0, "begrunnelse": "Ikke lenger blant topp-kandidater"
                    })

        st.session_state["forslag"] = forslag

    # ── Vis og godkjenn forslag ───────────────────────────────────────────────
    # Last forslag fra portfolio.json hvis session ikke har dem
    if "forslag" not in st.session_state or not st.session_state["forslag"]:
        pf_aktuell = les_portefolje()
        if pf_aktuell.get("ventende_handler"):
            st.session_state["forslag"] = pf_aktuell["ventende_handler"]
            if pf_aktuell.get("sist_analysert"):
                st.caption(f"Sist analysert: {pf_aktuell['sist_analysert'][:16]}")

    if "forslag" in st.session_state and st.session_state["forslag"]:
        forslag = st.session_state["forslag"]

        kjop  = [f for f in forslag if f["handling"] == "KJØP"]
        selg  = [f for f in forslag if f["handling"] == "SELG"]
        hold  = [f for f in forslag if f["handling"] == "HOLD"]

        if kjop:
            st.markdown("#### Kjøpsforslag")
            for f in kjop:
                col1, col2, col3 = st.columns([3, 1, 1])
                col1.markdown(f"**{f['navn']}** ({f['ticker']})  \n"
                              f"Kurs: {f['kurs']:.2f} kr · {f['antall']} aksjer · "
                              f"**{f['beløp']:,.0f} kr** · {f['begrunnelse']}")
                if col2.button("✅ Godkjenn", key=f"kjop_{f['ticker']}"):
                    pf = les_portefolje()
                    pris = hent_siste_kurs(f["ticker"]) or f["kurs"]
                    kostnad = pris * f["antall"]
                    if kostnad <= pf["kasse"]:
                        if f["ticker"] in pf["posisjoner"]:
                            pos = pf["posisjoner"][f["ticker"]]
                            total = pos["antall"] * pos["snittpris"] + kostnad
                            pos["antall"]   += f["antall"]
                            pos["snittpris"] = total / pos["antall"]
                        else:
                            pf["posisjoner"][f["ticker"]] = {
                                "navn": f["navn"], "antall": f["antall"],
                                "snittpris": pris, "kjøpsdato": str(datetime.now().date())
                            }
                        pf["kasse"] -= kostnad
                        pf["historikk"].append({
                            "dato": str(datetime.now()), "handling": "KJØP",
                            "ticker": f["ticker"], "navn": f["navn"],
                            "antall": f["antall"], "kurs": pris, "beløp": kostnad
                        })
                        if "start_kapital" not in pf:
                            pf["start_kapital"] = pf["kasse"] + kostnad
                        lagre_portefolje(pf)
                        st.success(f"Kjøpt {f['antall']} × {f['navn']} for {kostnad:,.0f} kr")
                        st.rerun()
                    else:
                        st.error(f"Ikke nok kasse ({pf['kasse']:,.0f} kr)")
                col3.button("❌ Avvis", key=f"avvis_kjop_{f['ticker']}")

        if selg:
            st.markdown("#### Salgsforslag")
            for f in selg:
                col1, col2, col3 = st.columns([3, 1, 1])
                col1.markdown(f"**{f['navn']}** ({f['ticker']})  \n"
                              f"Kurs: {f['kurs']:.2f} kr · {f['antall']} aksjer · "
                              f"**{f['beløp']:,.0f} kr** · {f['begrunnelse']}")
                if col2.button("✅ Godkjenn", key=f"selg_{f['ticker']}"):
                    pf = les_portefolje()
                    pris    = hent_siste_kurs(f["ticker"]) or f["kurs"]
                    inntekt = pris * f["antall"]
                    if f["ticker"] in pf["posisjoner"]:
                        del pf["posisjoner"][f["ticker"]]
                    pf["kasse"] += inntekt
                    pf["historikk"].append({
                        "dato": str(datetime.now()), "handling": "SELG",
                        "ticker": f["ticker"], "navn": f["navn"],
                        "antall": f["antall"], "kurs": pris, "beløp": inntekt
                    })
                    lagre_portefolje(pf)
                    st.success(f"Solgt {f['antall']} × {f['navn']} for {inntekt:,.0f} kr")
                    st.rerun()
                col3.button("❌ Avvis", key=f"avvis_selg_{f['ticker']}")

        if hold:
            with st.expander(f"HOLD ({len(hold)} posisjoner beholder vi)"):
                for f in hold:
                    st.markdown(f"**{f['navn']}** — {f['begrunnelse']}")

    # ── Handelshistorikk ──────────────────────────────────────────────────────
    pf = les_portefolje()
    if pf["historikk"]:
        with st.expander("Handelshistorikk"):
            df_hist = pd.DataFrame(pf["historikk"])
            st.dataframe(df_hist, use_container_width=True, hide_index=True)

    # ── Nullstill portefølje ──────────────────────────────────────────────────
    with st.expander("Innstillinger"):
        ny_kasse = st.number_input("Start kapital (kr)", value=int(pf["kasse"]), step=10000)
        if st.button("Nullstill portefølje"):
            lagre_portefolje({
                "kasse": ny_kasse, "start_kapital": ny_kasse,
                "posisjoner": {}, "ventende_handler": [], "historikk": []
            })
            st.session_state.pop("forslag", None)
            st.success("Portefølje nullstilt!")
            st.rerun()
