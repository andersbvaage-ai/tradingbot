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
import base64
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

PORTFOLIO_FIL = os.path.join(os.path.dirname(__file__), "portfolio.json")
GITHUB_REPO   = "andersbvaage-ai/tradingbot"
GITHUB_PATH   = "portfolio.json"

def _push_portefolje_til_github(innhold: str) -> bool:
    """Pusher portfolio.json til GitHub via API. Returnerer True ved suksess."""
    try:
        token = st.secrets.get("GITHUB_TOKEN")
        if not token:
            return False
        url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_PATH}"
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        get_resp = requests.get(url, headers=headers, timeout=10)
        get_resp.raise_for_status()
        sha = get_resp.json().get("sha")
        put_resp = requests.put(url, headers=headers, timeout=10, json={
            "message": f"Porteføljeoppdatering {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "content": base64.b64encode(innhold.encode()).decode(),
            "sha":     sha,
        })
        put_resp.raise_for_status()
        return True
    except Exception:
        return False

def les_portefolje():
    if not os.path.exists(PORTFOLIO_FIL):
        default = {"kasse": 0, "start_kapital": 0, "posisjoner": {},
                   "ventende_handler": [], "historikk": []}
        with open(PORTFOLIO_FIL, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(PORTFOLIO_FIL, "r") as f:
        return json.load(f)

def lagre_portefolje(p):
    innhold = json.dumps(p, indent=2, default=str)
    with open(PORTFOLIO_FIL, "w") as f:
        f.write(innhold)
    ok = _push_portefolje_til_github(innhold)
    if not ok:
        try:
            st.toast("⚠️ Kunne ikke synkronisere med GitHub — sjekk GITHUB_TOKEN", icon="⚠️")
        except Exception:
            pass  # Kalles fra scheduler (ikke Streamlit-kontekst)

_OSLO_TZ = ZoneInfo("Europe/Oslo")

def _er_markedstid() -> bool:
    """Returnerer True hvis Oslo Børs er åpen akkurat nå (man–fre 09:00–17:30)."""
    nå = datetime.now(_OSLO_TZ)
    if nå.weekday() >= 5:          # lørdag/søndag
        return False
    from datetime import time as _time
    return _time(9, 0) <= nå.time() <= _time(17, 30)

def hent_siste_kurs(ticker):
    """Henter siste kurs. I åpningstiden: ~15 min forsinket live-kurs (1m intraday).
    Utenfor åpningstiden: siste sluttkurs."""
    try:
        if _er_markedstid():
            raw = yf.download(ticker, period="1d", interval="1m", progress=False)
            if raw.empty:
                raise ValueError("tom")
            raw.columns = raw.columns.get_level_values(0)
            kurs = float(raw["Close"].dropna().iloc[-1])
        else:
            raw = yf.download(ticker, period="2d", progress=False)
            if raw.empty:
                raise ValueError("tom")
            raw.columns = raw.columns.get_level_values(0)
            kurs = float(raw["Close"].iloc[-1])
        return kurs
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

def beregn_indikatorer(close, volume=None, osebx_ret3m=0.0):
    """Beregner alle tekniske indikatorer for én aksje. Returnerer dict eller None."""
    if len(close) < 60:
        return None
    pris   = float(close.iloc[-1])
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

    # ── Ensemble: 3 uavhengige strategistemmer ────────────────────────────────
    # Kjøpssignal krever minimum 2/3 stemmer + RSI-filter
    sma_vote  = sma10 > sma50       # Strategi 1: Trend (SMA-crossover)
    macd_vote = macd_v > sig_v      # Strategi 2: MACD-konfirmasjon
    mom_vote  = mom > 0             # Strategi 3: Positiv 6-mnd momentum
    rsi_ok    = 30 < rsi < 72       # Kvalitetsfilter: ikke ekstremt overkjøpt/oversolgt

    ensemble  = sum([sma_vote, macd_vote, mom_vote])
    score     = sum([sma10 > sma50, 40 < rsi < 65, macd_v > sig_v, mom > 0])  # for screener

    stemmer = (
        ("Trend" if sma_vote else None),
        ("MACD"  if macd_vote else None),
        ("Mom"   if mom_vote else None),
    )
    ensemble_tekst = " · ".join(s for s in stemmer if s) or "Ingen"

    rel_styrke = vol_økning = nærhet_topp = oppside_score = 0.0
    if volume is not None:
        aksje_ret3m  = float(close.pct_change(63).iloc[-1] * 100) if len(close) >= 63 else 0
        rel_styrke   = aksje_ret3m - osebx_ret3m
        vol10        = float(volume.rolling(10).mean().iloc[-1])
        vol50        = float(volume.rolling(50).mean().iloc[-1])
        vol_økning   = (vol10 / vol50 - 1) * 100 if vol50 > 0 else 0
        høy52        = float(close.rolling(min(252, len(close))).max().iloc[-1])
        nærhet_topp  = (pris / høy52) * 100 if høy52 > 0 else 100
        oppside_score = (rel_styrke / 10) + (vol_økning / 50) + (nærhet_topp / 100)

    return {
        "pris": pris, "sma10": sma10, "sma50": sma50,
        "rsi": rsi, "macd_v": macd_v, "sig_v": sig_v, "mom": mom,
        "score": score, "ensemble": ensemble, "ensemble_tekst": ensemble_tekst,
        "rsi_ok": rsi_ok, "rel_styrke": rel_styrke, "vol_økning": vol_økning,
        "nærhet_topp": nærhet_topp, "oppside_score": oppside_score,
    }

def detect_regime(osebx_close):
    """Bestem markedsregime basert på OSEBX vs SMA200 og 3-måneders trend.
    Bull   → OSEBX > SMA200 og 3mnd > +3%   (aggressiv: maks 6 pos, 15% allok)
    Sideways → mellomting                    (moderat: maks 4 pos, 12% allok)
    Bear   → OSEBX < SMA200 og 3mnd < −5%   (defensiv: maks 2 pos, 10% allok)
    """
    if len(osebx_close) < 200:
        return "Sideways"
    sma200 = float(osebx_close.rolling(200).mean().iloc[-1])
    pris   = float(osebx_close.iloc[-1])
    ret3m  = float(osebx_close.pct_change(63).iloc[-1] * 100) if len(osebx_close) >= 63 else 0
    if pris > sma200 and ret3m > 3:
        return "Bull"
    elif pris < sma200 and ret3m < -5:
        return "Bear"
    else:
        return "Sideways"

REGIME_CONFIG = {
    "Bull":     {"min_ensemble": 2, "maks_pos": 6, "allok": 0.15, "ikon": "🟢", "farge": "green"},
    "Sideways": {"min_ensemble": 2, "maks_pos": 4, "allok": 0.12, "ikon": "🟡", "farge": "orange"},
    "Bear":     {"min_ensemble": 3, "maks_pos": 2, "allok": 0.10, "ikon": "🔴", "farge": "red"},
}

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
    # ── Store selskaper ──────────────────────────────────────────────────────
    "Equinor":                  "EQNR.OL",
    "DNB Bank":                 "DNB.OL",
    "Mowi":                     "MOWI.OL",
    "Telenor":                  "TEL.OL",
    "Norsk Hydro":              "NHY.OL",
    "Orkla":                    "ORK.OL",
    "Yara International":       "YAR.OL",
    "Aker BP":                  "AKERBP.OL",
    "SalMar":                   "SALM.OL",
    "Subsea 7":                 "SUBC.OL",
    "Storebrand":               "STB.OL",
    "Gjensidige":               "GJF.OL",
    "SpareBank 1 SR-Bank":      "SRBANK.OL",
    "Kongsberg Gruppen":        "KOG.OL",
    "Aker Solutions":           "AKSO.OL",
    "Scatec":                   "SCATC.OL",
    "Nel Hydrogen":             "NEL.OL",
    "Nordic Semiconductor":     "NOD.OL",
    "Kahoot":                   "KAHOT.OL",
    "AutoStore":                "AUTO.OL",
    "REC Silicon":              "RECSI.OL",
    "TGS":                      "TGS.OL",
    "PGS":                      "PGS.OL",
    "BW Offshore":              "BWO.OL",
    "Golden Ocean":             "GOGL.OL",
    "Flex LNG":                 "FLNG.OL",
    "MPC Container Ships":      "MPCC.OL",
    "Borr Drilling":            "BORR.OL",
    "AF Gruppen":               "AFG.OL",
    "Bouvet":                   "BOUVET.OL",
    "Odfjell":                  "ODF.OL",
    "Aker":                     "AKER.OL",
    "Wallenius Wilhelmsen":     "WAWI.OL",
    "Kitron":                   "KIT.OL",
    "Tomra Systems":            "TOM.OL",
    "Elkem":                    "ELK.OL",
    "Var Energi":               "VAR.OL",
    "Veidekke":                 "VEI.OL",
    "Lerøy Seafood":            "LSG.OL",
    "Grieg Seafood":            "GSF.OL",
    # ── Olje, gass og energitjenester ────────────────────────────────────────
    "DNO":                      "DNO.OL",
    "Okea":                     "OKEA.OL",
    "Archer":                   "ARCHER.OL",
    "BW Energy":                "BWE.OL",
    "Seadrill":                 "SDRL.OL",
    "Odfjell Drilling":         "ODL.OL",
    "Noreco":                   "NORECO.OL",
    "Avance Gas":               "AGAS.OL",
    "Electromagnetic GS":       "EMGS.OL",
    "Reach Subsea":             "REACH.OL",
    "American Shipping":        "AMSC.OL",
    "Aker Carbon Capture":      "ACC.OL",
    "Aker Horizons":            "AKH.OL",
    "Aker BioMarine":           "AKBM.OL",
    "Interoil Exploration":     "IOX.OL",
    "Prosafe":                  "PRS.OL",
    "Hexagon Composites":       "HEX.OL",
    "DOF Group":                "DOF.OL",
    "Eidesvik Offshore":        "EIOF.OL",
    "TECO 2030":                "TECO2.OL",
    # ── Shipping og transport ─────────────────────────────────────────────────
    "Frontline":                "FRO.OL",
    "BW LPG":                   "BWLPG.OL",
    "Höegh Autoliners":         "HAUTO.OL",
    "Havila Shipping":          "HAVI.OL",
    "Hunter Group":             "HUNT.OL",
    "Stolt-Nielsen":            "SNI.OL",
    "Solstad Offshore":         "SOFF.OL",
    "Siem Offshore":            "SIOFF.OL",
    "2020 Bulkers":             "2020.OL",
    "Sølvtrans":                "SOLT.OL",
    "Wilh. Wilhelmsen A":       "WWI.OL",
    "Wilh. Wilhelmsen B":       "WWIB.OL",
    "Offshore Heavy Transport": "OHT.OL",
    "Norwegian Air Shuttle":    "NAS.OL",
    "Fjord1":                   "FJORD.OL",
    "Bonheur":                  "BON.OL",
    # ── Bank, finans og forsikring ────────────────────────────────────────────
    "SpareBank 1 SMN":          "MING.OL",
    "SpareBank 1 Nord-Norge":   "NONG.OL",
    "SpareBank 1 BV":           "SBVG.OL",
    "SpareBank 1 Østlandet":    "SPOL.OL",
    "Sparebanken Møre":         "MORG.OL",
    "Sparebanken Vest":         "SVEG.OL",
    "Sparebanken Sør":          "SOR.OL",
    "Pareto Bank":              "PARB.OL",
    "Sandnes Sparebank":        "SADG.OL",
    "Protector Forsikring":     "PROT.OL",
    "Komplett Bank":            "KOMP.OL",
    "B2Holding":                "B2H.OL",
    "Axactor":                  "ACR.OL",
    "Helgeland Sparebank":      "HELG.OL",
    "Totens Sparebank":         "TOTG.OL",
    "Aurskog Sparebank":        "AURG.OL",
    "Jæren Sparebank":          "JAREN.OL",
    # ── Sjømat ───────────────────────────────────────────────────────────────
    "Austevoll Seafood":        "AUSS.OL",
    "Norway Royal Salmon":      "NRS.OL",
    "Bakkafrost":               "BAKKA.OL",
    "Nordic Halibut":           "NORDH.OL",
    # ── Teknologi og software ─────────────────────────────────────────────────
    "Pexip":                    "PEXIP.OL",
    "Link Mobility":            "LINK.OL",
    "IDEX Biometrics":          "IDEX.OL",
    "NEXT Biometrics":          "NEXT.OL",
    "Infront":                  "INF.OL",
    "SmartCraft":               "SMCRT.OL",
    "StrongPoint":              "STRONG.OL",
    "Q-Free":                   "QFR.OL",
    "Tekna Holding":            "TEKNA.OL",
    "Gaming Innovation Group":  "GIG.OL",
    "Webstep":                  "WSTEP.OL",
    "Zaptec":                   "ZAP.OL",
    "Thin Film Electronics":    "THIN.OL",
    "Kongsberg Automotive":     "KA.OL",
    "Carasent":                 "CARA.OL",
    "Itera":                    "ITERA.OL",
    "Norbit":                   "NORBIT.OL",
    # ── Eiendom ───────────────────────────────────────────────────────────────
    "Entra":                    "ENTRA.OL",
    "Olav Thon Eiendom":        "OLT.OL",
    "Solon Eiendom":            "SOLON.OL",
    "Norwegian Property":       "NPRO.OL",
    "Selvaag Bolig":            "SBO.OL",
    # ── Forbruker, media og handel ────────────────────────────────────────────
    "XXL":                      "XXL.OL",
    "Kid":                      "KID.OL",
    "Europris":                 "EPR.OL",
    "SATS":                     "SATS.OL",
    "Schibsted A":              "SCHA.OL",
    "Schibsted B":              "SCHB.OL",
    "Fjordkraft Holding":       "FKRAFT.OL",
    # ── Industri, helse og annet ──────────────────────────────────────────────
    "Arendals Fossekompani":    "AFK.OL",
    "Multiconsult":             "MULTI.OL",
    "Nordic Mining":            "NOM.OL",
    "Circa Group":              "CIRCA.OL",
    "Asker Healthcare":         "AHG.OL",
    "AKVA Group":               "AKVA.OL",
    "MPC Energy Solutions":     "MPCES.OL",
    "Cloudberry Clean Energy":  "CLOUD.OL",
    "PhotoCure":                "PHO.OL",
    "Vistin Pharma":            "VISTN.OL",
    "Medistim":                 "MEDI.OL",
    "NRC Group":                "NRC.OL",
    "Ultimovacs":               "ULTI.OL",
    "Nordic Nanovector":        "NNV.OL",
    "Hofseth BioCare":          "HBC.OL",
    "Targovax":                 "TRVX.OL",
    "Nordic Unmanned":          "NUM.OL",
    "Agilyx":                   "AGLX.OL",
    "Saga Pure":                "SAGA.OL",
}

# De ~15 største selskapene på Oslo Børs (ekskluderes fra bot-handel)
STORE_CAP_TICKERS = {
    "EQNR.OL",   # Equinor       ~400 mrd
    "DNB.OL",    # DNB Bank      ~250 mrd
    "NHY.OL",    # Norsk Hydro   ~100 mrd
    "MOWI.OL",   # Mowi          ~100 mrd
    "TEL.OL",    # Telenor       ~100 mrd
    "YAR.OL",    # Yara          ~80 mrd
    "KOG.OL",    # Kongsberg     ~80 mrd
    "GJF.OL",    # Gjensidige    ~70 mrd
    "AKERBP.OL", # Aker BP       ~65 mrd
    "ORK.OL",    # Orkla         ~60 mrd
    "STB.OL",    # Storebrand    ~55 mrd
    "VAR.OL",    # Var Energi    ~50 mrd
    "SALM.OL",   # SalMar        ~50 mrd
    "TOM.OL",    # Tomra         ~30 mrd
    "LSG.OL",    # Lerøy Seafood ~30 mrd
}

MID_SMALL_CAP = {k: v for k, v in OSLO_BORS.items() if v not in STORE_CAP_TICKERS}

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

_idag = datetime.now().strftime("%Y-%m-%d")
_iår  = datetime.now().year
PERIODER = {
    f"Siste 1 år   ({_iår-1}–nå)":    (f"{_iår-1}-01-01", _idag),
    f"Siste 2 år   ({_iår-2}–nå)":    (f"{_iår-2}-01-01", _idag),
    f"Siste 3 år   ({_iår-3}–nå)":    (f"{_iår-3}-01-01", _idag),
    f"Siste 5 år   ({_iår-5}–nå)":    (f"{_iår-5}-01-01", _idag),
    "Post-covid   (2022–2024)":        ("2022-01-01", "2024-01-01"),
    "Covid-krasj  (2020–2022)":        ("2020-01-01", "2022-01-01"),
    "Bull market  (2019–2021)":        ("2019-01-01", "2021-01-01"),
    "Finanskrise  (2007–2010)":        ("2007-01-01", "2010-01-01"),
    f"Lang periode (2015–nå)":         ("2015-01-01", _idag),
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

tab_dash, tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs(["Dashboard", "Backtest", "Sammenlign aksjer", "Optimalisering", "Portefølje", "Walk-Forward", "Oslo Børs Screener", "Porteføljestyrer", "Screener-backtest", "ℹ️ Info"])

# ─── TAB DASHBOARD ────────────────────────────────────────────────────────────
with tab_dash:
    _pf  = les_portefolje()
    _idag = str(datetime.now().date())

    # ── Kurs-modus-indikator ─────────────────────────────────────────────────
    if _er_markedstid():
        st.caption("🟢 Markedet er åpent — kurser er ~15 min forsinket live-data (intraday)")
    else:
        st.caption("⚫ Markedet er stengt — kurser viser siste sluttkurs")

    # ── Regime + sist analysert ───────────────────────────────────────────────
    _regime = _pf.get("regime", "Sideways")
    _rcfg   = REGIME_CONFIG.get(_regime, REGIME_CONFIG["Sideways"])
    _sist   = _pf.get("sist_analysert", "")[:16].replace("T", " ")
    _regime_info = {
        "Bull":     "OSEBX over SMA200, positiv 3-mnd trend — boten handler aggressivt.",
        "Sideways": "Blandede signaler — boten er moderat.",
        "Bear":     "OSEBX under SMA200, negativ trend — boten er defensiv.",
    }
    st.markdown(
        f"{_rcfg['ikon']} **Markedsregime: {_regime}** — {_regime_info[_regime]}"
        + (f"  ·  Sist analysert: {_sist}" if _sist else ""),
        help="Oppdateres daglig av scheduleren basert på OSEBX vs SMA200."
    )

    # ── Nøkkeltall ────────────────────────────────────────────────────────────
    _posisjoner  = _pf.get("posisjoner", {})
    _kasse       = _pf.get("kasse", 0)
    _start       = _pf.get("start_kapital", _kasse)
    _historikk   = _pf.get("historikk", [])

    _stop_loss_pct = _pf.get("stop_loss_pct", 0.15)
    _total_verdi   = _kasse
    _pos_rader     = []
    for _ticker, _pos in _posisjoner.items():
        _kurs = hent_siste_kurs(_ticker)
        if _kurs:
            _verdi       = _kurs * _pos["antall"]
            _gevinst     = _verdi - _pos["antall"] * _pos["snittpris"]
            _gevinst_pct = (_kurs / _pos["snittpris"] - 1) * 100
            _sl_kurs     = round(_pos["snittpris"] * (1 - _stop_loss_pct), 2)
            _sl_avstand  = round((_kurs / _sl_kurs - 1) * 100, 1)
            _total_verdi += _verdi
            _pos_rader.append({
                "Aksje":          _pos["navn"],
                "Kjøpsdato":      _pos.get("kjøpsdato", "–"),
                "Antall":         _pos["antall"],
                "Snittpris":      round(_pos["snittpris"], 2),
                "Nåkurs":         round(_kurs, 2),
                "Avkastning %":   round(_gevinst_pct, 1),
                "Verdi (kr)":     round(_verdi, 0),
                "SL-kurs":        _sl_kurs,
                "Avstand til SL": _sl_avstand,
            })

    _avk_pct = (_total_verdi / _start - 1) * 100 if _start > 0 else 0
    _avk_kr  = _total_verdi - _start

    _lukkede = [h for h in _historikk if h.get("handling") == "SELG"]
    _kjøp    = [h for h in _historikk if h.get("handling") == "KJØP"]

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Porteføljeverdi",  f"{_total_verdi:,.0f} kr",
                  delta=f"{_avk_kr:+,.0f} kr" if _start > 0 else None)
    col_m2.metric("Kasse",            f"{_kasse:,.0f} kr")
    col_m3.metric("Total avkastning", f"{_avk_pct:+.1f}%" if _start > 0 else "–")
    col_m4.metric("Antall posisjoner", len(_posisjoner))

    st.divider()

    # ── Åpne posisjoner ───────────────────────────────────────────────────────
    st.markdown("### Åpne posisjoner")
    if _pos_rader:
        st.dataframe(
            pd.DataFrame(_pos_rader),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Snittpris":      st.column_config.NumberColumn("Snittpris",      format="%.2f kr"),
                "Nåkurs":         st.column_config.NumberColumn("Nåkurs",         format="%.2f kr"),
                "Avkastning %":   st.column_config.NumberColumn("Avkastning %",   format="%+.1f%%"),
                "Verdi (kr)":     st.column_config.NumberColumn("Verdi",          format="%,.0f kr"),
                "SL-kurs":        st.column_config.NumberColumn(f"SL ({_stop_loss_pct*100:.0f}%)", format="%.2f kr",
                                    help=f"Selges automatisk hvis kursen faller under dette nivået"),
                "Avstand til SL": st.column_config.NumberColumn("Avstand til SL", format="%+.1f%%",
                                    help="Hvor mye kursen kan falle før stop-loss utløses"),
            },
        )
    else:
        st.info("Ingen åpne posisjoner for øyeblikket.")

    st.divider()

    # ── Dagens handler ────────────────────────────────────────────────────────
    _dagens = [h for h in _historikk if str(h.get("dato", ""))[:10] == _idag]
    st.markdown("### Dagens handler")
    if _dagens:
        for _h in _dagens:
            _ikon   = "✅" if _h["handling"] == "KJØP" else "🔴"
            _farge  = "green" if _h["handling"] == "KJØP" else "red"
            _begrunnelse = _h.get("begrunnelse", "–")
            with st.container(border=True):
                _c1, _c2 = st.columns([1, 3])
                _c1.markdown(
                    f"**{_ikon} {_h['handling']}**\n\n"
                    f"**{_h['navn']}**\n\n"
                    f"{_h['antall']} aksjer\n\n"
                    f"{_h.get('kurs', 0):,.2f} kr/aksje\n\n"
                    f"**{_h.get('beløp', 0):,.0f} kr totalt**"
                )
                _c2.markdown(f"**Begrunnelse:**\n\n{_begrunnelse}")
    else:
        st.info(f"Ingen handler utført i dag ({_idag}).")

    st.divider()

    # ── Handelslogg ───────────────────────────────────────────────────────────
    st.markdown("### Handelslogg")
    if _historikk:
        _col_f1, _col_f2, _col_f3 = st.columns([1, 1, 2])
        _filter_type = _col_f1.selectbox(
            "Type", ["Alle", "Kun kjøp", "Kun salg"],
            key="dash_hist_filter", label_visibility="visible"
        )
        _vis_antall = _col_f2.selectbox(
            "Vis siste", ["30 handler", "100 handler", "Alle"],
            key="dash_hist_antall", label_visibility="visible"
        )
        _filter_text = _col_f3.text_input(
            "Søk aksjenavn", placeholder="Filtrer på aksjenavn...",
            key="dash_hist_search", label_visibility="visible"
        )

        _filtrert = list(reversed(_historikk))
        if _filter_type == "Kun kjøp":
            _filtrert = [h for h in _filtrert if h.get("handling") == "KJØP"]
        elif _filter_type == "Kun salg":
            _filtrert = [h for h in _filtrert if h.get("handling") == "SELG"]
        if _filter_text:
            _filtrert = [h for h in _filtrert if _filter_text.lower() in h.get("navn", "").lower()]
        if _vis_antall == "30 handler":
            _filtrert = _filtrert[:30]
        elif _vis_antall == "100 handler":
            _filtrert = _filtrert[:100]

        _logg_rader = []
        for _h in _filtrert:
            _dato_str = str(_h.get("dato", ""))[:16].replace("T", " ")
            _logg_rader.append({
                "Dato":        _dato_str,
                "Handling":    _h.get("handling", ""),
                "Aksje":       _h.get("navn", ""),
                "Antall":      _h.get("antall", ""),
                "Kurs (kr)":   round(_h["kurs"], 2)  if "kurs"  in _h else None,
                "Beløp (kr)":  round(_h["beløp"], 0) if "beløp" in _h else None,
                "Begrunnelse": _h.get("begrunnelse", "–"),
            })

        st.dataframe(
            pd.DataFrame(_logg_rader),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Dato":        st.column_config.TextColumn("Dato",       width="medium"),
                "Handling":    st.column_config.TextColumn("Handling",   width="small"),
                "Aksje":       st.column_config.TextColumn("Aksje",      width="medium"),
                "Antall":      st.column_config.NumberColumn("Antall",   width="small"),
                "Kurs (kr)":   st.column_config.NumberColumn("Kurs",     format="%.2f kr", width="medium"),
                "Beløp (kr)":  st.column_config.NumberColumn("Beløp",    format="%,.0f kr", width="medium"),
                "Begrunnelse": st.column_config.TextColumn("Begrunnelse", width="large"),
            },
        )
        st.caption(f"{len(_filtrert)} handler vises (totalt {len(_historikk)} i loggen).")

        # ── Statistikk over lukkede handler ───────────────────────────────────
        if _lukkede:
            st.divider()
            st.markdown("### Statistikk")

            # Prøv å matche kjøp og salg per ticker for å beregne realisert avkastning
            _kjøp_map = {}
            for _h in _historikk:
                if _h.get("handling") == "KJØP":
                    _kjøp_map[_h["ticker"]] = _h
            _realisert = []
            for _h in _historikk:
                if _h.get("handling") == "SELG" and _h["ticker"] in _kjøp_map:
                    _innkjøp = _kjøp_map[_h["ticker"]]
                    _avk_r   = (_h["kurs"] / _innkjøp["kurs"] - 1) * 100
                    _realisert.append(_avk_r)

            _ant_kjøp  = len(_kjøp)
            _ant_salg  = len(_lukkede)
            _positive  = len([r for r in _realisert if r > 0])
            _hit_rate  = (_positive / len(_realisert) * 100) if _realisert else None
            _snitt_avk = sum(_realisert) / len(_realisert) if _realisert else None

            _sc1, _sc2, _sc3, _sc4 = st.columns(4)
            _sc1.metric("Totalt kjøp",    _ant_kjøp)
            _sc2.metric("Totalt salg",    _ant_salg)
            _sc3.metric("Hit rate",       f"{_hit_rate:.0f}%" if _hit_rate is not None else "–",
                        help="Andel lukkede handler med positiv avkastning")
            _sc4.metric("Snitt avkastning (lukket)", f"{_snitt_avk:+.1f}%" if _snitt_avk is not None else "–")
    else:
        st.info("Ingen handelshistorikk ennå. Boten kjører første gang neste hverdag kl 09:15.")

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
                time.sleep(0.05)
                if raw.empty or len(raw) < 60:
                    prog.progress((i + 1) / len(OSLO_BORS))
                    continue
                raw.columns = raw.columns.get_level_values(0)
                ind = beregn_indikatorer(raw["Close"])
                if ind is None:
                    prog.progress((i + 1) / len(OSLO_BORS))
                    continue

                sma_signal  = ind["sma10"] > ind["sma50"]
                rsi_signal  = 40 < ind["rsi"] < 65
                macd_signal = ind["macd_v"] > ind["sig_v"]
                mom_signal  = ind["mom"] > 0

                if ind["score"] >= 4:   anbefaling = "Sterkt kjøp"
                elif ind["score"] == 3: anbefaling = "Kjøp"
                elif ind["score"] == 2: anbefaling = "Nøytral"
                elif ind["score"] == 1: anbefaling = "Svak"
                else:                   anbefaling = "Selg / unngå"

                rader.append({
                    "Aksje":       navn,
                    "Kurs":        round(ind["pris"], 2),
                    "SMA10>50":    "✅" if sma_signal  else "❌",
                    "RSI (40-65)": "✅" if rsi_signal  else "❌",
                    "MACD":        "✅" if macd_signal else "❌",
                    "Momentum":    "✅" if mom_signal  else "❌",
                    "Score":       ind["score"],
                    "RSI verdi":   round(ind["rsi"], 1),
                    "Mom 6mnd %":  round(ind["mom"], 1),
                    "Signal":      anbefaling,
                    "_score":      ind["score"],
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
    kun_mid_small = st.checkbox(
        "Ekskluder de 15 største selskapene (mid/small cap fokus)",
        value=True,
        help="Fjerner Equinor, DNB, Hydro, Telenor m.fl. — samme innstilling som den daglige boten"
    )

    if st.button("Kjør analyse og generer forslag", type="primary"):
        with st.spinner("Scanner Oslo Børs..."):
            # Hent OSEBX én gang før loopen
            osebx_ret3m = 0.0
            try:
                osebx_raw = yf.download("^OSEBX", period="6mo", progress=False)
                osebx_raw.columns = osebx_raw.columns.get_level_values(0)
                if len(osebx_raw) >= 63:
                    osebx_ret3m = float(osebx_raw["Close"].pct_change(63).iloc[-1] * 100)
            except Exception:
                pass

            univers = MID_SMALL_CAP if kun_mid_small else OSLO_BORS
            kandidater = []
            for navn, ticker in univers.items():
                try:
                    raw = yf.download(ticker, period="1y", progress=False)
                    time.sleep(0.05)
                    if raw.empty or len(raw) < 60:
                        continue
                    raw.columns = raw.columns.get_level_values(0)
                    ind = beregn_indikatorer(raw["Close"], raw["Volume"], osebx_ret3m)
                    if ind is None:
                        continue
                    if ind["rel_styrke"] < min_rel_styrke:
                        continue
                    if ind["ensemble"] < 2 or not ind["rsi_ok"]:
                        continue  # Ensemble-krav: minst 2/3 strategier enige + RSI-filter

                    # Markedsverdi-filter (kun hvis valgt — unngår treg HTTP-request som standard)
                    if maks_cap != "Alle størrelser":
                        try:
                            cap    = yf.Ticker(ticker).info.get("marketCap", 0)
                            grense = 50e9 if "50" in maks_cap else 10e9
                            if cap and cap > grense:
                                continue
                        except Exception:
                            pass

                    kandidater.append({
                        "navn": navn, "ticker": ticker, "kurs": ind["pris"],
                        "score": ind["score"], "ensemble": ind["ensemble"],
                        "ensemble_tekst": ind["ensemble_tekst"],
                        "rsi": ind["rsi"], "mom": ind["mom"],
                        "rel_styrke": ind["rel_styrke"], "vol_økning": ind["vol_økning"],
                        "nærhet_topp": ind["nærhet_topp"], "oppside_score": ind["oppside_score"],
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

        # Utfør handler autonomt
        utforte      = []
        topp_tickers = {k["ticker"] for k in topp}

        # Selg posisjoner som ikke lenger er blant topp-kandidatene
        for ticker, pos in list(pf["posisjoner"].items()):
            if ticker not in topp_tickers:
                kurs = hent_siste_kurs(ticker)
                if kurs:
                    inntekt = round(pos["antall"] * kurs, 0)
                    del pf["posisjoner"][ticker]
                    pf["kasse"] += inntekt
                    pf["historikk"].append({
                        "dato": str(datetime.now()), "handling": "SELG",
                        "ticker": ticker, "navn": pos["navn"],
                        "antall": pos["antall"], "kurs": kurs, "beløp": inntekt,
                        "begrunnelse": "Ikke lenger blant topp-kandidater",
                    })
                    utforte.append({"handling": "SELG", "navn": pos["navn"], "beløp": inntekt})

        # Kjøp topp-kandidater vi ikke allerede eier
        for k, vekt in zip(topp, vekter):
            if k["ticker"] in pf["posisjoner"]:
                continue
            beløp  = pf["kasse"] * vekt
            antall = int(beløp / k["kurs"])
            if antall < 1 or beløp > pf["kasse"]:
                continue
            kostnad     = round(antall * k["kurs"], 0)
            begrunnelse = (f"Ensemble {k['ensemble']}/3 ({k['ensemble_tekst']}) · "
                           f"mom {k['mom']:.1f}% · rel.styrke {k['rel_styrke']:.1f}% · "
                           f"RSI {k['rsi']:.0f}")
            pf["posisjoner"][k["ticker"]] = {
                "navn": k["navn"], "antall": antall,
                "snittpris": k["kurs"], "kjøpsdato": str(datetime.now().date()),
            }
            pf["kasse"] -= kostnad
            pf["historikk"].append({
                "dato": str(datetime.now()), "handling": "KJØP",
                "ticker": k["ticker"], "navn": k["navn"],
                "antall": antall, "kurs": k["kurs"], "beløp": kostnad,
                "begrunnelse": begrunnelse,
            })
            utforte.append({"handling": "KJØP", "navn": k["navn"], "beløp": kostnad})

        pf["ventende_handler"] = []
        pf["sist_analysert"]   = str(datetime.now())
        lagre_portefolje(pf)
        st.session_state.pop("forslag", None)

        kjop_ant = len([u for u in utforte if u["handling"] == "KJØP"])
        selg_ant = len([u for u in utforte if u["handling"] == "SELG"])
        st.success(f"Ferdig! {kjop_ant} kjøp og {selg_ant} salg utført automatisk.")

    # ── Dagens utførte handler ────────────────────────────────────────────────
    pf_ny   = les_portefolje()
    idag    = str(datetime.now().date())
    dagens  = [h for h in pf_ny.get("historikk", []) if str(h.get("dato", ""))[:10] == idag]
    if dagens:
        st.markdown("#### Handler utført i dag")
        for h in dagens:
            ikon = "✅" if h["handling"] == "KJØP" else "🔴"
            st.markdown(f"{ikon} **{h['navn']}** ({h['ticker']}) — "
                        f"{h['handling']} {h['antall']} aksjer à {h['kurs']:.2f} kr "
                        f"= **{h['beløp']:,.0f} kr**  \n"
                        f"_{h.get('begrunnelse', '')}_")
    st.caption("Full handelslogg med begrunnelse finner du i **Dashboard**-fanen.")

    # ── Nullstill portefølje ──────────────────────────────────────────────────
    with st.expander("Innstillinger"):
        pf_inn = les_portefolje()

        st.markdown("**Stop-loss**")
        ny_sl = st.slider(
            "Stop-loss %", min_value=5, max_value=30,
            value=int(pf_inn.get("stop_loss_pct", 0.15) * 100),
            help="Posisjoner selges automatisk hvis de faller mer enn dette fra kjøpspris"
        )
        if st.button("Lagre stop-loss"):
            pf_inn["stop_loss_pct"] = ny_sl / 100
            lagre_portefolje(pf_inn)
            st.success(f"Stop-loss satt til {ny_sl}%")
            st.rerun()

        st.divider()
        st.markdown("**Nullstill portefølje**")
        ny_kasse = st.number_input("Start kapital (kr)", value=int(pf_inn["kasse"]), step=10000)
        if st.button("Nullstill portefølje"):
            lagre_portefolje({
                "kasse": ny_kasse, "start_kapital": ny_kasse,
                "posisjoner": {}, "ventende_handler": [], "historikk": [],
                "stop_loss_pct": ny_sl / 100,
            })
            st.session_state.pop("forslag", None)
            st.success("Portefølje nullstilt!")
            st.rerun()

# ─── TAB 8: SCREENER-BACKTEST ─────────────────────────────────────────────────
with tab8:
    st.subheader("Screener-backtest")
    st.caption(
        "Simulerer hva som ville skjedd om boten kjøpte topp-aksjene hver måned og rebalanserte. "
        "Tester om screener-logikken faktisk gir meravkastning over tid."
    )

    col_sb1, col_sb2, col_sb3 = st.columns(3)
    sb_topp_n    = col_sb1.slider("Antall aksjer i portefølje", 3, 10, 5, key="sb_n")
    sb_min_score = col_sb2.slider("Min score for å inkluderes", 1, 4, 2, key="sb_score")
    sb_kommisjon = col_sb3.slider("Kurtasje per handel (%)", 0.0, 1.0, 0.2, key="sb_kom") / 100

    if st.button("Kjør screener-backtest", type="primary"):
        from dateutil.relativedelta import relativedelta

        sb_start = pd.Timestamp(start_dato)
        sb_slutt = pd.Timestamp(slutt_dato)
        sb_data_start = (sb_start - relativedelta(months=3)).strftime("%Y-%m-%d")
        sb_data_slutt = sb_slutt.strftime("%Y-%m-%d")

        # Last all historisk data på forhånd
        with st.spinner("Laster historisk data for alle aksjer..."):
            all_data = {}
            for navn, ticker in OSLO_BORS.items():
                d = hent_data(ticker, sb_data_start, sb_data_slutt)
                if d is not None and len(d) > 60:
                    all_data[navn] = (ticker, d)

        if not all_data:
            st.error("Ingen data tilgjengelig.")
        else:
            # Generer månedlige rebalanseringsdatoer
            datoer = []
            dato   = sb_start
            while dato <= sb_slutt:
                datoer.append(dato)
                dato = dato + relativedelta(months=1)

            portefolje_verdi  = [float(kapital)]
            osebx_verdi       = [float(kapital)]
            rebalanse_log     = []
            nåværende_aksjer  = {}
            kasse_sb          = float(kapital)

            prog = st.progress(0)

            # Hent OSEBX for benchmark
            # Prøv flere ticker-alternativer for Oslo Børs benchmark
            osebx_data = None
            for bm_ticker in ["^OSEBX", "OSEBX.OL", "^OSEAX"]:
                osebx_data = hent_data(bm_ticker, sb_start.strftime("%Y-%m-%d"), sb_data_slutt)
                if osebx_data is not None and len(osebx_data) > 10:
                    break

            # Fallback: bruk lik-vektet snitt av alle aksjer i universet
            if osebx_data is None or len(osebx_data) < 10:
                bm_kurver = [
                    (df["Close"] / df["Close"].iloc[0])
                    for _, df in all_data.values()
                    if len(df) > 60
                ]
                if bm_kurver:
                    felles = bm_kurver[0].index
                    aligned = [k.reindex(felles, method="ffill") for k in bm_kurver]
                    bm_snitt = pd.concat(aligned, axis=1).mean(axis=1)
                    osebx_data = pd.DataFrame({"Close": bm_snitt * float(kapital)})
                    st.caption("Benchmark: lik-vektet snitt av alle Oslo Børs-aksjer i universet")

            for i, dato in enumerate(datoer[:-1]):
                neste = datoer[i + 1]

                # Score alle aksjer basert på data frem til denne datoen
                kandidater = []
                for navn, (ticker, df) in all_data.items():
                    historisk = df[df.index <= dato]
                    if len(historisk) < 60:
                        continue
                    try:
                        ind = beregn_indikatorer(historisk["Close"])
                        if ind and ind["score"] >= sb_min_score:
                            kandidater.append({
                                "navn": navn, "ticker": ticker,
                                "score": ind["score"], "mom": ind["mom"],
                            })
                    except Exception:
                        continue

                kandidater.sort(key=lambda x: (x["score"], x["mom"]), reverse=True)
                topp = {k["navn"]: k for k in kandidater[:sb_topp_n]}

                # Beregn avkastning for inneværende måned
                total_verdi = kasse_sb
                for navn, pos in nåværende_aksjer.items():
                    df = all_data[navn][1]
                    fremtid = df[(df.index > dato) & (df.index <= neste)]
                    if not fremtid.empty:
                        sluttkurs = float(fremtid["Close"].iloc[-1])
                        total_verdi += pos["antall"] * sluttkurs

                # Rebalanser: selg det som ikke er i topp
                ny_kasse = kasse_sb
                for navn in list(nåværende_aksjer.keys()):
                    if navn not in topp:
                        df      = all_data[navn][1]
                        fremtid = df[(df.index > dato) & (df.index <= neste)]
                        if not fremtid.empty:
                            kurs    = float(fremtid["Close"].iloc[-1])
                            inntekt = kurs * nåværende_aksjer[navn]["antall"]
                            ny_kasse += inntekt * (1 - sb_kommisjon)
                        del nåværende_aksjer[navn]

                # Kjøp nye
                allok = ny_kasse / max(len(topp), 1)
                for navn, k in topp.items():
                    if navn not in nåværende_aksjer:
                        df   = all_data[navn][1]
                        hist = df[df.index <= dato]
                        if hist.empty:
                            continue
                        kurs   = float(hist["Close"].iloc[-1])
                        antall = int((allok * (1 - sb_kommisjon)) / kurs)
                        if antall > 0:
                            kostnad = antall * kurs * (1 + sb_kommisjon)
                            if kostnad <= ny_kasse:
                                nåværende_aksjer[navn] = {"antall": antall, "kurs": kurs}
                                ny_kasse -= kostnad

                kasse_sb = ny_kasse

                # Beregn porteføljeverdi ved neste dato
                total_neste = kasse_sb
                for navn, pos in nåværende_aksjer.items():
                    df      = all_data[navn][1]
                    fremtid = df[(df.index > dato) & (df.index <= neste)]
                    if not fremtid.empty:
                        total_neste += pos["antall"] * float(fremtid["Close"].iloc[-1])

                portefolje_verdi.append(total_neste)

                # OSEBX benchmark
                if osebx_data is not None:
                    osebx_slice = osebx_data[(osebx_data.index > dato) & (osebx_data.index <= neste)]
                    osebx_prev  = osebx_data[osebx_data.index <= dato]
                    if not osebx_slice.empty and not osebx_prev.empty and len(osebx_verdi) > 0:
                        osebx_ret = float(osebx_slice["Close"].iloc[-1]) / float(osebx_prev["Close"].iloc[-1]) - 1
                        osebx_verdi.append(osebx_verdi[-1] * (1 + osebx_ret))
                    elif len(osebx_verdi) > 0:
                        osebx_verdi.append(osebx_verdi[-1])

                rebalanse_log.append({
                    "Dato":       dato.strftime("%Y-%m"),
                    "Portefølje": [n for n in nåværende_aksjer.keys()],
                    "Verdi (kr)": round(total_neste, 0),
                })

                prog.progress((i + 1) / (len(datoer) - 1))

            # ── Resultater ────────────────────────────────────────────────────
            port_ret  = (portefolje_verdi[-1] / portefolje_verdi[0] - 1) * 100
            osebx_ret = (osebx_verdi[-1] / osebx_verdi[0] - 1) * 100 if osebx_verdi else 0

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Screener avkastning",  f"{port_ret:.1f}%",  f"{port_ret - osebx_ret:.1f}% vs OSEBX")
            c2.metric("OSEBX avkastning",     f"{osebx_ret:.1f}%")
            c3.metric("Rebalanseringer",       len(datoer) - 1)
            c4.metric("Slutt verdi",          f"{portefolje_verdi[-1]:,.0f} kr")

            # Equity curve
            fig_sb = go.Figure()
            fig_sb.add_trace(go.Scatter(
                x=datoer[:len(portefolje_verdi)], y=portefolje_verdi,
                name="Screener-portefølje", line=dict(color="#00b4d8", width=2)))
            if osebx_verdi:
                fig_sb.add_trace(go.Scatter(
                    x=datoer[:len(osebx_verdi)], y=osebx_verdi,
                    name="OSEBX (benchmark)", line=dict(color="#f77f00", dash="dash")))
            fig_sb.update_layout(
                template="plotly_dark", height=450,
                yaxis_title="Kapital (kr)",
                title="Screener-portefølje vs Oslo Børs (månedlig rebalansering)"
            )
            st.plotly_chart(fig_sb, use_container_width=True)

            # Faktor-bidrag
            st.subheader("Hvilke faktorer ga best treff?")
            faktor_score = {"SMA (trend)": 0, "RSI (40-65)": 0, "MACD": 0, "Momentum": 0}
            faktor_count = {"SMA (trend)": 0, "RSI (40-65)": 0, "MACD": 0, "Momentum": 0}

            for navn, (ticker, df) in all_data.items():
                for dato in datoer[:-1]:
                    hist = df[df.index <= dato]
                    if len(hist) < 60:
                        continue
                    try:
                        close  = hist["Close"]
                        fremtid = df[(df.index > dato) & (df.index <= dato + relativedelta(months=1))]
                        if fremtid.empty:
                            continue
                        ret = float(fremtid["Close"].iloc[-1]) / float(close.iloc[-1]) - 1

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
                        mom    = float(close.pct_change(63).iloc[-1] * 100) if len(close) >= 63 else 0

                        if sma10 > sma50:
                            faktor_score["SMA (trend)"] += ret
                            faktor_count["SMA (trend)"] += 1
                        if 40 < rsi < 65:
                            faktor_score["RSI (40-65)"] += ret
                            faktor_count["RSI (40-65)"] += 1
                        if macd_v > sig_v:
                            faktor_score["MACD"] += ret
                            faktor_count["MACD"] += 1
                        if mom > 0:
                            faktor_score["Momentum"] += ret
                            faktor_count["Momentum"] += 1
                    except Exception:
                        continue

            snitt_ret = {
                k: (faktor_score[k] / faktor_count[k] * 100) if faktor_count[k] > 0 else 0
                for k in faktor_score
            }
            fig_fak = go.Figure(go.Bar(
                x=list(snitt_ret.keys()),
                y=list(snitt_ret.values()),
                marker_color=["#26a69a" if v > 0 else "#ef5350" for v in snitt_ret.values()],
                text=[f"{v:.2f}%" for v in snitt_ret.values()],
                textposition="outside",
            ))
            fig_fak.update_layout(
                template="plotly_dark", height=350,
                yaxis_title="Gjennomsnittlig månedlig avkastning (%)",
                title="Snittavkastning per faktor (når faktoren er positiv)"
            )
            st.plotly_chart(fig_fak, use_container_width=True)

            # Rebalanseringslogg
            with st.expander("Månedlig rebalanseringslogg"):
                for r in rebalanse_log:
                    st.markdown(f"**{r['Dato']}** — {', '.join(r['Portefølje'])} — {r['Verdi (kr)']:,.0f} kr")

# ─── TAB 9: INFO ──────────────────────────────────────────────────────────────
with tab9:
    st.markdown("# Nordic Trading Bot — Strategiguide")
    st.caption("En oversikt over alle strategier, signaler og beslutningslogikk i boten.")

    # ── Ensemble-systemet ────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 🗳️ Ensemble-systemet")
    st.markdown(
        "Boten bruker et **ensemble-system** der tre uavhengige strategier stemmer over "
        "kjøp. Aksjen kjøpes kun dersom **minst 2 av 3 stemmer** er positive — "
        "i tillegg til at RSI er innenfor et normalt område (30–72).\n\n"
        "Dette reduserer falske signaler betraktelig sammenlignet med å bruke én indikator alene."
    )
    c1, c2, c3 = st.columns(3)
    c1.info("**Stemme 1 — Trend**\n\nSMA10 > SMA50\n\nKort glidende snitt over langt = aksjen er i opptrendmodus.")
    c2.info("**Stemme 2 — MACD**\n\nMACD-linje > Signal-linje\n\nMomentum skifter positivt, kjøpstrykk tiltar.")
    c3.info("**Stemme 3 — Momentum**\n\n6-mnd avkastning > 0 %\n\nAksjen har faktisk steget siste halvår.")
    st.markdown(
        "**RSI-filter (ikke en stemme):** RSI må være mellom 30 og 72. "
        "Dette forhindrer kjøp i ekstremt overkjøpte situasjoner (RSI > 72) "
        "og i kraftige nedtrender (RSI < 30)."
    )

    # ── Regime-deteksjon ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 🌡️ Regime-deteksjon")
    st.markdown(
        "Boten tilpasser seg markedsklima automatisk. Hvert morgen analyseres OSEBX "
        "mot sin **200-dagers glidende snitt (SMA200)** og 3-måneders trend. "
        "Dette bestemmer hvor aggressivt boten handler."
    )
    regime_tabell = {
        "Regime":        ["🟢 Bull", "🟡 Sideways", "🔴 Bear"],
        "Vilkår":        [
            "OSEBX > SMA200 og 3mnd > +3%",
            "Mellomting — ikke klart bull eller bear",
            "OSEBX < SMA200 og 3mnd < −5%",
        ],
        "Ensemble-krav": ["2 av 3", "2 av 3", "3 av 3"],
        "Maks posisjoner": ["6", "4", "2"],
        "Allokering/pos":  ["15%", "12%", "10%"],
    }
    st.dataframe(pd.DataFrame(regime_tabell), use_container_width=True, hide_index=True)
    st.markdown(
        "**Hvorfor SMA200?** Det er det mest brukte skilleskillet mellom bull- og bear-markeder "
        "blant institusjonelle investorer. Enkelt, objektivt og etterprøvbart.\n\n"
        "**Hvorfor 3 av 3 i Bear?** I bjørnemarked er risikoen for falske signaler høy. "
        "Kreve full enighet reduserer antall handler og beskytter kapitalen."
    )

    # ── Handelsstrategier (backtesting) ──────────────────────────────────────
    st.markdown("---")
    st.markdown("## 📈 Handelsstrategier (Backtest-fanen)")
    st.markdown(
        "Disse strategiene brukes i **Backtest**, **Sammenlign**, **Optimalisering** og "
        "**Walk-Forward**. De kjøper og selger basert på tekniske regler, med stop-loss."
    )

    with st.expander("**SMA + RSI** — Trend med RSI-filter", expanded=True):
        col1, col2 = st.columns([2, 1])
        col1.markdown(
            "**Idé:** Kjøp når den korte glidende gjennomsnittet (SMA10) krysser over det lange (SMA50) "
            "og RSI er under 60 — altså ikke allerede overkjøpt. Selg når SMA10 krysser under SMA50 "
            "eller RSI stiger over 70.\n\n"
            "**Passer for:** Trending markeder med tydelige oppgangs- og nedgangsfaser.\n\n"
            "**Svakhet:** Gir mange falske signaler i sidelengs markeder."
        )
        col2.metric("Parametere", "SMA rask/treg, RSI-periode, Stop-loss")

    with st.expander("**MACD** — Momentumskifte"):
        col1, col2 = st.columns([2, 1])
        col1.markdown(
            "**Idé:** MACD-linjen (EMA12 − EMA26) krysser over signal-linjen (EMA9 av MACD) = kjøp. "
            "Krysser under = selg. Fanger skifte i momentum tidlig.\n\n"
            "**Passer for:** Markeder med tydelige trendskifter.\n\n"
            "**Svakhet:** Etterslep — signalet kommer litt etter toppene og bunnene."
        )
        col2.metric("Parametere", "MACD rask/treg, Signal-periode, Stop-loss")

    with st.expander("**Bollinger Bands** — Mean reversion"):
        col1, col2 = st.columns([2, 1])
        col1.markdown(
            "**Idé:** Kjøp når kursen berører det nedre Bollinger-båndet (overskjøtt ned). "
            "Selg når kursen når det øvre båndet. Basert på at kurs trekkes tilbake mot gjennomsnittet.\n\n"
            "**Passer for:** Sidelengs markeder og aksjer med stabil handelsrange.\n\n"
            "**Svakhet:** Dårlig i sterke trending markeder — kan kjøpe midt i en nedtrend."
        )
        col2.metric("Parametere", "BB-periode, Standardavvik, Stop-loss")

    with st.expander("**Momentum** — Fortsatt oppgang"):
        col1, col2 = st.columns([2, 1])
        col1.markdown(
            "**Idé:** Kjøp aksjer som allerede har steget mye siste 6 måneder — "
            "vinnere fortsetter å vinne (momentum-effekten). Hold til momentum snur negativt.\n\n"
            "**Passer for:** Bull-markeder og aksjer med sterk strukturell vekst.\n\n"
            "**Svakhet:** Kjøper høyt og kan bli truffet hardt ved brå snuoperasjoner."
        )
        col2.metric("Parametere", "Lookback-periode, Min momentum %, Stop-loss")

    # ── Screener-faktorer ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 🔍 Oslo Børs Screener — Faktorer")
    st.markdown(
        "Screener-fanen scanner alle aksjer i universet og gir en score fra 0–4 "
        "basert på fire klassiske kjøpssignaler."
    )
    faktor_data = {
        "Faktor":       ["SMA10 > SMA50", "RSI 40–65", "MACD > Signal", "Momentum > 0"],
        "Hva det måler":["Aksjen er i kortsiktig opptrendmodus",
                         "Ikke overkjøpt eller oversolgt — sunn zone",
                         "Kjøpstrykk tiltar (MACD krysser signal)",
                         "Positiv 6-måneders avkastning"],
        "Signal ved":   ["SMA10 bryter over SMA50",
                         "RSI mellom 40 og 65",
                         "MACD-linje over signal-linje",
                         "6-mnd avkastning > 0 %"],
    }
    st.dataframe(pd.DataFrame(faktor_data), use_container_width=True, hide_index=True)

    # ── Oppside-score ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 🚀 Oppside-score (Porteføljestyrer)")
    st.markdown(
        "I tillegg til den klassiske scoren (0–4) beregnes en **oppside-score** "
        "som belønner vekstegenskaper. Brukes til å rangere og velge mellom kandidater."
    )
    oppside_data = {
        "Komponent":        ["Relativ styrke vs OSEBX", "Volumøkning", "Nærhet til 52-ukers høy"],
        "Formel":           ["(aksje 3mnd % − OSEBX 3mnd %) / 10",
                             "(vol10d / vol50d − 1) × 100 / 50",
                             "(pris / høy52) / 100"],
        "Hva det belønner": ["Aksjer som slår indeksen",
                             "Tiltagende handelsaktivitet",
                             "Aksjer nær historisk toppnivå — styrke"],
    }
    st.dataframe(pd.DataFrame(oppside_data), use_container_width=True, hide_index=True)

    # ── Univers og filtre ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 🌐 Univers og filtre")
    col1, col2 = st.columns(2)
    col1.markdown(
        "**Handelsunivers — Mid/Small Cap**\n\n"
        f"Boten handler i **{len(MID_SMALL_CAP)} aksjer** fra Oslo Børs, ekskludert de 15 største "
        "selskapene (Equinor, DNB, Hydro, Telenor m.fl.). Begrunnelse: store selskaper har lavere "
        "vekstpotensial og dominerer indeksen — de vil naturlig vinne screener-rangeringen."
    )
    col2.markdown(
        "**Automatisk rebalansering**\n\n"
        "Boten kjører daglig kl 09:15 (mandag–fredag). Den selger posisjoner som har falt ut av "
        "topp-listen, og kjøper nye kandidater som oppfyller ensemble-kravet. "
        "Maks 6 posisjoner, maks 15–20 % av kassen per posisjon."
    )

    # ── Walk-Forward ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 🔄 Walk-Forward Testing")
    st.markdown(
        "Walk-Forward er en metode for å teste om en strategi faktisk er robust — "
        "ikke bare tilpasset historiske data (*overfitting*).\n\n"
        "**Slik fungerer det:**\n"
        "1. Del opp historien i overlappende vinduer\n"
        "2. **Tren** strategien på de første X månedene (in-sample)\n"
        "3. **Test** på de neste Y månedene du aldri har sett (out-of-sample)\n"
        "4. Gjenta for hvert vindu fremover i tid\n\n"
        "Hvis strategien gjør det bra *out-of-sample* over mange vinduer, "
        "er det et tegn på ekte robusthet. Hvis ikke, er den overfittet."
    )

    # ── Screener-backtest ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 📊 Screener-backtest")
    st.markdown(
        "Tester screener-strategien historisk: hver måned velges topp N aksjer basert på "
        "score, porteføljen rebalanseres, og avkastningen sammenlignes mot OSEBX.\n\n"
        "**Bruksområde:** Finn ut hvilke faktorer (SMA, RSI, MACD, Momentum) som faktisk "
        "har bidratt til meravkastning historisk, og justér vektingen deretter."
    )
