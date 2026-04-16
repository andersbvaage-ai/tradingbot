"""
Kjøres automatisk av GitHub Actions hver børsdag kl 09:15.
Analyserer Oslo Børs, oppdaterer portfolio.json med nye forslag,
og sender e-post hvis det er nye kjøps- eller salgsforslag.
"""

import re
import yfinance as yf
import pandas as pd
import json
import logging
import os
import requests
import signal
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from ta.trend import SMAIndicator, MACD as TAmacd
from ta.momentum import RSIIndicator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scheduler")

ANALYSE_TIMEOUT_SEC = 600  # 10 min maks for hele kjor_analyse()
PER_TICKER_TIMEOUT  = 30   # 30 sek maks per aksje-analyse


def _run_with_timeout(fn, timeout_sec, label="operasjon"):
    """Run fn() in a thread with hard timeout. Returns None on timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout_sec)
        except FuturesTimeout:
            log.error("TIMEOUT: %s tok mer enn %ds — hopper over", label, timeout_sec)
            return None

PORTFOLIO_FIL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.json")

DEFAULT_PORTEFOLJE = {
    "kasse": 100000,
    "start_kapital": 100000,
    "posisjoner": {},
    "ventende_handler": [],
    "historikk": [],
    "stop_loss_pct": 0.07,
    "kurtasje_modell": "Mini",
    "kurtasje_ratio_maks": 0.02,
    "regime": "Sideways",
}

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
    "Kongsberg Gruppen":        "KOG.OL",
    "Aker Solutions":           "AKSO.OL",
    "Scatec":                   "SCATC.OL",
    "Nel Hydrogen":             "NEL.OL",
    "Nordic Semiconductor":     "NOD.OL",
    "AutoStore":                "AUTO.OL",
    "REC Silicon":              "RECSI.OL",
    "TGS":                      "TGS.OL",
    "BW Offshore":              "BWO.OL",
    "CMB.TECH":                 "CMBTO.OL",
    "MPC Container Ships":      "MPCC.OL",
    "Borr Drilling":            "BORR.OL",
    "AF Gruppen":               "AFG.OL",
    "Bouvet":                   "BOUV.OL",
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
    "Archer":                   "ARCH.OL",
    "BW Energy":                "BWE.OL",
    "Odfjell Drilling":         "ODL.OL",
    "Electromagnetic GS":       "EMGS.OL",
    "Reach Subsea":             "REACH.OL",
    "Aker Horizons":            "AKH.OL",
    "Aker BioMarine":           "AKBM.OL",
    "Interoil Exploration":     "IOX.OL",
    "Prosafe":                  "PRS.OL",
    "Hexagon Composites":       "HEX.OL",
    "DOF Group":                "DOFG.OL",
    "Eidesvik Offshore":        "EIOF.OL",
    # ── Shipping og transport ─────────────────────────────────────────────────
    "Frontline":                "FRO.OL",
    "BW LPG":                   "BWLPG.OL",
    "Höegh Autoliners":         "HAUTO.OL",
    "Havila Shipping":          "HAVI.OL",
    "Hunter Group":             "HUNT.OL",
    "Stolt-Nielsen":            "SNI.OL",
    "Solstad Offshore":         "SOFF.OL",
    "2020 Bulkers":             "2020.OL",
    "Wilh. Wilhelmsen A":       "WWI.OL",
    "Wilh. Wilhelmsen B":       "WWIB.OL",
    "Norwegian Air Shuttle":    "NAS.OL",
    "Bonheur":                  "BONHR.OL",
    # ── Bank, finans og forsikring ────────────────────────────────────────────
    "SpareBank 1 SMN":          "MING.OL",
    "SpareBank 1 Nord-Norge":   "NONG.OL",
    "SpareBank 1 Østlandet":    "SPOL.OL",
    "Sparebanken Møre":         "MORG.OL",
    "Pareto Bank":              "PARB.OL",
    "Protector Forsikring":     "PROT.OL",
    "Axactor":                  "ACR.OL",
    "Helgeland Sparebank":      "HELG.OL",
    "Aurskog Sparebank":        "AURG.OL",
    "Jæren Sparebank":          "JAREN.OL",
    # ── Sjømat ───────────────────────────────────────────────────────────────
    "Austevoll Seafood":        "AUSS.OL",
    "Bakkafrost":               "BAKKA.OL",
    "Nordic Halibut":           "NORDH.OL",
    # ── Teknologi og software ─────────────────────────────────────────────────
    "Pexip":                    "PEXIP.OL",
    "Link Mobility":            "LINK.OL",
    "IDEX Biometrics":          "IDEX.OL",
    "NEXT Biometrics":          "NEXT.OL",
    "SmartCraft":               "SMCRT.OL",
    "StrongPoint":              "STRO.OL",
    "Tekna Holding":            "TEKNA.OL",
    "Webstep":                  "WSTEP.OL",
    "Zaptec":                   "ZAP.OL",
    "Kongsberg Automotive":     "KOA.OL",
    "Itera":                    "ITERA.OL",
    "Norbit":                   "NORBT.OL",
    # ── Eiendom ───────────────────────────────────────────────────────────────
    "Entra":                    "ENTRA.OL",
    "Olav Thon Eiendom":        "OLT.OL",
    "Selvaag Bolig":            "SBO.OL",
    # ── Forbruker, media og handel ────────────────────────────────────────────
    "Kid":                      "KID.OL",
    "Europris":                 "EPR.OL",
    "SATS":                     "SATS.OL",
    "Elmera Group":             "ELMRA.OL",
    # ── Industri, helse og annet ──────────────────────────────────────────────
    "Arendals Fossekompani":    "AFK.OL",
    "Multiconsult":             "MULTI.OL",
    "Nordic Mining":            "NOM.OL",
    "AKVA Group":               "AKVA.OL",
    "MPC Energy Solutions":     "MPCES.OL",
    "Cloudberry Clean Energy":  "CLOUD.OL",
    "PhotoCure":                "PHO.OL",
    "Vistin Pharma":            "VISTN.OL",
    "Medistim":                 "MEDI.OL",
    "NRC Group":                "NRC.OL",
    "Hofseth BioCare":          "HBC.OL",
    "Agilyx":                   "AGLX.OL",
    "Saga Pure":                "SAGA.OL",
}

# De ~15 største selskapene — ekskluderes fra bot-handel (for høy markedsverdi)
STORE_CAP_TICKERS = {
    "EQNR.OL", "DNB.OL", "NHY.OL", "MOWI.OL", "TEL.OL",
    "YAR.OL",  "KOG.OL", "GJF.OL", "AKERBP.OL", "ORK.OL",
    "STB.OL",  "VAR.OL", "SALM.OL", "TOM.OL", "LSG.OL",
}

# Univers boten handler i — kun mid/small cap
UNIVERS = {k: v for k, v in OSLO_BORS.items() if v not in STORE_CAP_TICKERS}

MAKS_POSISJONER   = 6
ALLOKERING_PCT    = 0.15   # 15% av kasse per posisjon
MAKS_ALLOKERING   = 0.20   # aldri mer enn 20% i én aksje
MIN_REL_STYRKE    = 0      # må slå OSEBX siste 3 mnd
MIN_ENSEMBLE      = 3      # krever 3/3 strategier enige (Trend+MACD+Momentum)
DEFAULT_STOP_LOSS  = 0.15   # selg hvis posisjon er ned >15% fra kjøpspris
MAKS_PER_SEKTOR    = 2      # maks antall posisjoner fra samme sektor

SEKTORER = {
    "EQNR.OL":"Energi",    "DNB.OL":"Finans",     "MOWI.OL":"Sjømat",    "TEL.OL":"Telekom",
    "NHY.OL":"Industri",   "ORK.OL":"Forbruker",  "YAR.OL":"Industri",   "AKERBP.OL":"Energi",
    "SALM.OL":"Sjømat",    "SUBC.OL":"Energi",    "STB.OL":"Finans",     "GJF.OL":"Finans",
    "KOG.OL":"Industri",   "AKSO.OL":"Energi",    "SCATC.OL":"Fornybar",
    "NEL.OL":"Fornybar",   "NOD.OL":"Teknologi",  "AUTO.OL":"Teknologi",
    "RECSI.OL":"Fornybar", "TGS.OL":"Energi",          "BWO.OL":"Energi",
    "CMBTO.OL":"Shipping",  "MPCC.OL":"Shipping",  "BORR.OL":"Energi",
    "AFG.OL":"Industri",   "BOUV.OL":"Teknologi","ODF.OL":"Shipping",  "AKER.OL":"Industri",
    "WAWI.OL":"Shipping",  "KIT.OL":"Teknologi",  "TOM.OL":"Industri",   "ELK.OL":"Industri",
    "VAR.OL":"Energi",     "VEI.OL":"Industri",   "LSG.OL":"Sjømat",     "GSF.OL":"Sjømat",
    "DNO.OL":"Energi",     "OKEA.OL":"Energi",    "ARCH.OL":"Energi",  "BWE.OL":"Energi",
    "ODL.OL":"Energi",
    "EMGS.OL":"Energi",    "REACH.OL":"Energi",
    "AKH.OL":"Industri",   "AKBM.OL":"Sjømat",    "IOX.OL":"Energi",     "PRS.OL":"Energi",
    "HEX.OL":"Industri",   "DOFG.OL":"Energi",    "EIOF.OL":"Energi",
    "FRO.OL":"Shipping",   "BWLPG.OL":"Shipping", "HAUTO.OL":"Shipping", "HAVI.OL":"Shipping",
    "HUNT.OL":"Shipping",  "SNI.OL":"Shipping",   "SOFF.OL":"Energi",
    "2020.OL":"Shipping",  "WWI.OL":"Shipping",   "WWIB.OL":"Shipping",
    "NAS.OL":"Transport",  "BONHR.OL":"Energi",
    "MING.OL":"Finans",    "NONG.OL":"Finans",    "SPOL.OL":"Finans",
    "MORG.OL":"Finans",    "PARB.OL":"Finans",
    "PROT.OL":"Finans",
    "ACR.OL":"Finans",     "HELG.OL":"Finans",    "AURG.OL":"Finans",
    "JAREN.OL":"Finans",   "AUSS.OL":"Sjømat",    "BAKKA.OL":"Sjømat",
    "NORDH.OL":"Sjømat",   "PEXIP.OL":"Teknologi","LINK.OL":"Teknologi", "IDEX.OL":"Teknologi",
    "NEXT.OL":"Teknologi", "SMCRT.OL":"Teknologi","STRO.OL":"Teknologi",
    "TEKNA.OL":"Teknologi","WSTEP.OL":"Teknologi",
    "ZAP.OL":"Teknologi",  "KOA.OL":"Industri",
    "ITERA.OL":"Teknologi","NORBT.OL":"Teknologi","ENTRA.OL":"Eiendom", "OLT.OL":"Eiendom",
    "SBO.OL":"Eiendom",
    "KID.OL":"Forbruker",  "EPR.OL":"Forbruker",  "SATS.OL":"Forbruker", "SCHA.OL":"Media",
    "ELMRA.OL":"Fornybar", "AFK.OL":"Industri",  "MULTI.OL":"Industri",
    "NOM.OL":"Industri",   "AKVA.OL":"Industri",
    "MPCES.OL":"Fornybar", "CLOUD.OL":"Fornybar", "PHO.OL":"Helse",      "VISTN.OL":"Helse",
    "MEDI.OL":"Helse",     "NRC.OL":"Industri",
    "HBC.OL":"Industri",   "AGLX.OL":"Industri",
    "SAGA.OL":"Industri",
}
MAKS_KURTASJE_RATIO = 0.02  # kurtasje skal ikke overstige 2% av posisjonsstørrelsen
TARGET_VOL          = 0.20  # referansevolatilitet for posisjonsstørrelse (20% annualisert)
MOM_CAP             = 200   # Maks 6mnd momentum i % — filtrerer ut parabolske aksjer
MOM_REDUKSJON_TERSKEL = 100 # Reduser posisjonsstørrelse ved momentum over dette

# Nordnet kurtasjemodeller (Norden/Oslo Børs)
KURTASJE_MODELLER = {
    "Mini":   {"pct": 0.0015, "min_kr": 29},   # Best for handler < 52 667 kr
    "Normal": {"pct": 0.00049, "min_kr": 79},  # Best for handler > 52 667 kr
}
KURTASJE_STANDARD = "Mini"  # Standard-modell

SYKLISKE_SEKTORER = {"Energi", "Shipping"}  # sektorer der utbytte er et kvalitetstegn

# Råvarer som leder aksjer i sine sektorer
RÅVARE_MAP = {
    "Energi":   "BZ=F",   # Brent crude
    "Shipping": "BDRY",   # Breakwave Dry Bulk ETF — tracker BDI-futures
}

def hent_råvare_trend(råvare_ticker):
    """Returnerer 1 (uptrend), -1 (downtrend) eller 0 (ukjent) basert på SMA10 vs SMA50."""
    try:
        df = yf.download(råvare_ticker, period="6mo", progress=False, timeout=15)
        if df.empty or len(df) < 50:
            return 0
        close = df["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        sma10 = float(close.rolling(10).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        return 1 if sma10 > sma50 else -1
    except Exception:
        log.warning("Råvaretrend feilet for %s", råvare_ticker, exc_info=True)
        return 0

def _norm_navn(s):
    """Normaliserer selskapsnavn for matching mot Finanstilsynet-data."""
    s = s.upper()
    for suffix in [" ASA", " SA", " S.A.", " AB", " PLC", " LTD", " INC",
                   " CORP", " SE", " NV", " HOLDING", " HOLDINGS", " GROUP",
                   " LIMITED", " AS", " BIOTECH"]:
        s = s.replace(suffix, "")
    return re.sub(r"[^A-Z0-9 ]", "", s).strip()

def hent_short_interest(univers):
    """
    Henter netto short-posisjoner fra Finanstilsynet SSR.
    Returnerer dict {ticker: short_pct} for tickers i universet.
    """
    short_map = {}
    try:
        resp = requests.get(
            "https://ssr.finanstilsynet.no/api/v2/instruments",
            timeout=20,
        )
        resp.raise_for_status()
        # Build lookup: normalized_name → short_pct
        ft_map = {}
        for inst in resp.json():
            if inst["events"] and inst["events"][0]["shortPercent"] > 0:
                ft_map[_norm_navn(inst["issuerName"])] = inst["events"][0]["shortPercent"]

        # Match universe names to Finanstilsynet names
        for navn, ticker in univers.items():
            n = _norm_navn(navn)
            pct = None
            if n in ft_map:
                pct = ft_map[n]
            else:
                # Substring match i begge retninger
                for ft_n, ft_pct in ft_map.items():
                    if n in ft_n or ft_n in n or (
                        len(n) >= 4 and n.split()[0] == ft_n.split()[0] and
                        len(set(n.split()) & set(ft_n.split())) >= 2
                    ):
                        pct = ft_pct
                        break
            if pct is not None:
                short_map[ticker] = pct
    except Exception as e:
        log.error("Short-interest-data feilet: %s", e, exc_info=True)
    return short_map

def hent_innsidekjøp(univers_tickers, dager=14):
    """
    Henter tickers med insiderkjøp siste N dager fra Oslo Børs OAM-system.
    Itererer over hverdager siden fromDate=YYYY-MM-DD er eksakt dato, ikke range.
    Returnerer set av tickers (format: TICKER.OL).
    """
    kjøp_tickers = set()
    univers_sign = {t.replace(".OL", "") for t in univers_tickers}
    today = datetime.utcnow().date()
    try:
        hverdager_sett = 0
        for dag in range(dager * 2):  # buffer for helgedager — sikrer N hverdager
            dato = today - timedelta(days=dag)
            if dato.weekday() >= 5:   # hopp over helg
                continue
            if hverdager_sett >= dager:
                break
            hverdager_sett += 1
            resp = requests.get(
                "https://api3.oslo.oslobors.no/v1/newsreader/list",
                params={"category": 1102, "fromDate": dato.isoformat()},
                timeout=15,
            )
            messages = resp.json()["data"]["messages"]
            for msg in messages:
                sign = msg.get("issuerSign", "")
                if sign not in univers_sign or sign + ".OL" in kjøp_tickers:
                    continue
                detail = requests.get(
                    "https://api3.oslo.oslobors.no/v1/newsreader/message",
                    params={"messageId": msg["messageId"]},
                    timeout=10,
                )
                body = detail.json().get("data", {}).get("message", {}).get("body", "").lower()
                if "has bought" in body or "has purchased" in body or "har kjøpt" in body:
                    kjøp_tickers.add(sign + ".OL")
    except Exception as e:
        log.error("Insider-data feilet: %s", e, exc_info=True)
    return kjøp_tickers

def hent_fundamentals(ticker):
    """Henter P/E, P/B og dividendYield fra yfinance. Returnerer dict — felt er None hvis ukjent."""
    try:
        info = yf.Ticker(ticker).info
        pe    = info.get("trailingPE") or info.get("forwardPE")
        pb    = info.get("priceToBook")
        raw_yield = info.get("dividendYield")  # yfinance returnerer desimal, f.eks. 0.045 = 4,5%
        yield_ = round(raw_yield * 100, 2) if raw_yield is not None else None
        return {"pe": pe, "pb": pb, "yield": yield_}
    except Exception:
        log.warning("Fundamentals feilet for %s", ticker, exc_info=True)
        return {"pe": None, "pb": None, "yield": None}

def fundamental_ok(fund, sektor=None):
    """
    Sjekk om fundamentale er akseptable. None = data ikke tilgjengelig = pass through.
    Returnerer (True/False, grunn-tekst).
    """
    pe     = fund.get("pe")
    pb     = fund.get("pb")
    yield_ = fund.get("yield")

    if pe is not None and pe < 0:
        return False, f"Negativ P/E ({pe:.1f}) — taper penger"
    if pe is not None and pe > 60:
        return False, f"P/E for høy ({pe:.1f} > 60)"
    if pb is not None and pb > 15:
        return False, f"P/B for høy ({pb:.1f} > 15)"

    # Sykliske sektorer: lønnsomme selskaper uten utbytte er et rødt flagg
    if sektor in SYKLISKE_SEKTORER:
        if yield_ is not None and yield_ == 0.0 and pe is not None and pe > 0:
            return False, f"Ingen utbytte i syklisk sektor ({sektor})"

    return True, ""

def beregn_kurtasje(beløp, pf):
    modell_navn = pf.get("kurtasje_modell", KURTASJE_STANDARD)
    modell      = KURTASJE_MODELLER.get(modell_navn, KURTASJE_MODELLER[KURTASJE_STANDARD])
    pct         = modell["pct"]
    min_kr      = modell["min_kr"]
    return round(max(beløp * pct, min_kr), 0)

REGIME_CONFIG = {
    "Bull":     {"min_ensemble": 3, "maks_pos": 6, "allok": 0.15, "maks_per_sektor": 3},
    "Sideways": {"min_ensemble": 3, "maks_pos": 4, "allok": 0.12, "maks_per_sektor": 2},
    "Bear":     {"min_ensemble": 3, "maks_pos": 2, "allok": 0.10, "maks_per_sektor": 1},
}

def detect_regime(osebx_close):
    """Bestem markedsregime basert på OSEBX vs SMA200 og 3-måneders trend."""
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


def les_portefolje():
    if not os.path.exists(PORTFOLIO_FIL):
        lagre_portefolje(DEFAULT_PORTEFOLJE)
        return DEFAULT_PORTEFOLJE.copy()
    with open(PORTFOLIO_FIL, "r", encoding="utf-8") as f:
        return json.load(f)

def lagre_portefolje(p):
    with open(PORTFOLIO_FIL, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, default=str)

def hent_siste_kurs(ticker):
    """Henter siste kurs via 1-minutts intraday (kjøres alltid i åpningstiden fra scheduler)."""
    try:
        raw = yf.download(ticker, period="1d", interval="1m", progress=False, timeout=15)
        if raw.empty:
            raise ValueError("tom intraday-respons")
        raw.columns = raw.columns.get_level_values(0)
        return float(raw["Close"].dropna().iloc[-1])
    except Exception as e:
        log.warning("Intraday-kurs feilet for %s: %s — prøver daglig", ticker, e)
        try:
            raw = yf.download(ticker, period="2d", progress=False, timeout=10)
            if raw.empty:
                log.error("Daglig kurs også tom for %s — returnerer None", ticker)
                return None
            raw.columns = raw.columns.get_level_values(0)
            return float(raw["Close"].iloc[-1])
        except Exception as e2:
            log.error("Kurs helt utilgjengelig for %s: %s", ticker, e2)
            return None

def hent_ensemble_for_posisjon(ticker: str) -> int:
    """Beregn ensemble-score uten RSI/rel_styrke-filter — brukes for eksisterende posisjoner."""
    try:
        raw = yf.download(ticker, period="1y", progress=False, timeout=15)
        if raw.empty or len(raw) < 60:
            return 1  # fail-safe: behold posisjon ved utilstrekkelig data
        raw.columns = raw.columns.get_level_values(0)
        close  = raw["Close"]
        sma10  = float(SMAIndicator(close, window=10).sma_indicator().iloc[-1])
        sma50  = float(SMAIndicator(close, window=50).sma_indicator().iloc[-1])
        macd_obj = TAmacd(close)
        macd_v = float(macd_obj.macd().iloc[-1])
        sig_v  = float(macd_obj.macd_signal().iloc[-1])
        mom    = float(close.pct_change(126).iloc[-1] * 100) if len(close) >= 126 else 0
        return sum([sma10 > sma50, macd_v > sig_v, mom > 0])
    except Exception:
        log.warning("Ensemble-beregning feilet for %s — beholder posisjon (fail-safe)", ticker, exc_info=True)
        return 1  # fail-safe: behold posisjon ved datafeil


def analyser_aksje(navn, ticker, osebx_ret3m):
    raw = yf.download(ticker, period="1y", progress=False, timeout=15)
    if raw.empty or len(raw) < 60:
        return None
    raw.columns = raw.columns.get_level_values(0)
    close  = raw["Close"]
    volume = raw["Volume"]
    pris   = float(close.iloc[-1])

    sma10  = float(SMAIndicator(close, window=10).sma_indicator().iloc[-1])
    sma50  = float(SMAIndicator(close, window=50).sma_indicator().iloc[-1])
    rsi    = float(RSIIndicator(close, window=14).rsi().iloc[-1])
    macd_obj = TAmacd(close)
    macd_v = float(macd_obj.macd().iloc[-1])
    sig_v  = float(macd_obj.macd_signal().iloc[-1])
    mom    = float(close.pct_change(126).iloc[-1] * 100) if len(close) >= 126 else 0

    aksje_ret3m = float(close.pct_change(63).iloc[-1] * 100) if len(close) >= 63 else 0
    rel_styrke  = aksje_ret3m - osebx_ret3m

    vol10     = float(volume.rolling(10).mean().iloc[-1])
    vol50     = float(volume.rolling(50).mean().iloc[-1])
    vol_økning = (vol10 / vol50 - 1) * 100 if vol50 > 0 else 0

    høy52      = float(close.rolling(252).max().iloc[-1])
    nærhet_topp = (pris / høy52) * 100

    if rel_styrke < MIN_REL_STYRKE:
        return None

    # Ensemble: 3 uavhengige strategistemmer
    sma_vote  = sma10 > sma50
    macd_vote = macd_v > sig_v
    mom_vote  = mom > 0
    rsi_ok    = 30 < rsi < 70
    ensemble  = sum([sma_vote, macd_vote, mom_vote])

    if not rsi_ok:
        return None  # RSI utenfor gyldig sone

    if not sma_vote:
        return None  # Trend (SMA10>SMA50) er obligatorisk — alle vinnere hadde Trend

    if mom > MOM_CAP:
        return None  # Parabolsk momentum — for høy risiko for korreksjon

    stemmer = " · ".join(s for s, v in [("Trend", sma_vote), ("MACD", macd_vote), ("Mom", mom_vote)] if v)
    score         = sum([sma10 > sma50, 40 < rsi < 65, macd_v > sig_v, mom > 0])
    oppside_score = (rel_styrke / 10) + (vol_økning / 50) + (nærhet_topp / 100)

    # 60-dagers realisert volatilitet (annualisert)
    vol_60d = float(close.pct_change().rolling(60).std().iloc[-1] * (252 ** 0.5)) if len(close) >= 60 else 0.20

    return {
        "navn": navn, "ticker": ticker, "kurs": pris,
        "score": score, "ensemble": ensemble, "ensemble_tekst": stemmer,
        "rsi": rsi, "mom": mom, "rel_styrke": rel_styrke,
        "vol_økning": vol_økning, "nærhet_topp": nærhet_topp, "oppside_score": oppside_score,
        "vol_60d": vol_60d,
    }


_kurs_cache: dict[str, float | None] = {}

def _hent_kurs_cached(ticker: str) -> float | None:
    """Hent siste kurs med cache — unngår gjentatte API-kall per kjøring."""
    if ticker not in _kurs_cache:
        _kurs_cache[ticker] = hent_siste_kurs(ticker)
    return _kurs_cache[ticker]


def _utfor_salg(pf: dict, ticker: str, pos: dict, kurs: float,
                begrunnelse: str, utforte: list) -> None:
    """Felles salgslogikk — oppdaterer portefølje, historikk og utforte-liste."""
    brutto   = round(pos["antall"] * kurs, 0)
    kurtasje = beregn_kurtasje(brutto, pf)
    inntekt  = brutto - kurtasje

    kjøpsdato = pos.get("kjøpsdato", "")
    try:
        holdingstid = (datetime.now().date() - datetime.fromisoformat(kjøpsdato).date()).days
    except Exception:
        log.warning("Kunne ikke beregne holdingstid for kjøpsdato=%s", kjøpsdato)
        holdingstid = None
    avkastning_pct = round((kurs / pos["snittpris"] - 1) * 100, 2)

    del pf["posisjoner"][ticker]
    pf["kasse"] += inntekt

    oppføring = {
        "dato": str(datetime.now()), "handling": "SELG",
        "ticker": ticker, "navn": pos["navn"],
        "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
        "kurtasje": kurtasje, "begrunnelse": begrunnelse,
        "snittpris": pos["snittpris"], "avkastning_pct": avkastning_pct,
        "holdingstid": holdingstid, "signaler": pos.get("signaler"),
    }
    pf["historikk"].append(oppføring)
    utforte.append({
        "handling": "SELG", "navn": pos["navn"], "ticker": ticker,
        "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
        "kurtasje": kurtasje, "begrunnelse": begrunnelse,
        "snittpris": pos["snittpris"], "avkastning_pct": avkastning_pct,
    })
    log.info("SOLGT: %d × %s à %.2f kr = %.0f kr (kurtasje %.0f kr) — %s",
             pos["antall"], pos["navn"], kurs, brutto, kurtasje, begrunnelse)


def kjor_analyse(dry_run: bool = False):
    log.info("Starter analyse%s...", " (DRY RUN)" if dry_run else "")
    pf    = les_portefolje()
    kasse = pf["kasse"]
    _kurs_cache.clear()

    # Hent OSEBX — trenger 1y for SMA200 (regime) og 3mnd-retur
    osebx_ret3m = 0.0
    regime      = "Sideways"
    try:
        osebx = yf.download("OSEBX.OL", period="1y", progress=False, timeout=15)
        osebx.columns = osebx.columns.get_level_values(0)
        osebx_close = osebx["Close"]
        if len(osebx_close) >= 63:
            osebx_ret3m = float(osebx_close.pct_change(63).iloc[-1] * 100)
        regime = detect_regime(osebx_close)
    except Exception:
        log.error("OSEBX-data feilet — bruker Sideways som fallback", exc_info=True)

    rcfg             = REGIME_CONFIG[regime]
    min_ensemble     = rcfg["min_ensemble"]
    maks_pos         = rcfg["maks_pos"]
    allok            = rcfg["allok"]
    maks_per_sektor  = rcfg["maks_per_sektor"]
    log.info("Regime: %s (ensemble≥%d, maks %d pos, %.0f%%/pos, maks %d/sektor)",
             regime, min_ensemble, maks_pos, allok*100, maks_per_sektor)

    # Hent råvaretrender — brukes som sektorboost i rangering
    log.info("Henter råvaretrender...")
    råvare_trender = {}
    for sektor, råvare_ticker in RÅVARE_MAP.items():
        trend = hent_råvare_trend(råvare_ticker)
        råvare_trender[sektor] = trend
        retning = "↑ uptrend" if trend == 1 else ("↓ downtrend" if trend == -1 else "– ukjent")
        log.info("  %s (%s): %s", sektor, råvare_ticker, retning)

    # Hent innsidekjøp siste 14 dager fra Oslo Børs OAM
    log.info("Henter innsidekjøp...")
    insider_kjøp = hent_innsidekjøp(set(UNIVERS.values()))
    if insider_kjøp:
        log.info("  Insiderkjøp siste 14 dager: %s", ", ".join(sorted(insider_kjøp)))
    else:
        log.info("  Ingen insiderkjøp funnet i universet")

    # Hent short interest fra Finanstilsynet
    log.info("Henter short interest...")
    short_interest = hent_short_interest(UNIVERS)
    høy_short = {t: p for t, p in short_interest.items() if p >= 2.0}
    if høy_short:
        log.info("  Short ≥2%%: %s", ", ".join(f"{t} {p:.1f}%" for t, p in sorted(høy_short.items(), key=lambda x: -x[1])))

    # Analyser alle aksjer — samle ensemble for alle (inkl. eksisterende posisjoner)
    kandidater    = []
    alle_ensemble = {}   # ticker → ensemble-count for posisjonssjekk
    for navn, ticker in UNIVERS.items():
        log.info("  Analyserer %s...", navn)
        try:
            k = _run_with_timeout(
                lambda n=navn, t=ticker: analyser_aksje(n, t, osebx_ret3m),
                PER_TICKER_TIMEOUT, label=navn,
            )
            if k:
                sektor = SEKTORER.get(ticker, "Annet")
                k["råvare_score"]  = råvare_trender.get(sektor, 0) * 0.5
                k["insider_score"] = 0.75 if ticker in insider_kjøp else 0.0
                short_pct = short_interest.get(ticker, 0.0)
                k["short_pct"]   = short_pct
                k["short_score"] = -0.75 if short_pct >= 5.0 else (-0.4 if short_pct >= 2.0 else 0.0)
                alle_ensemble[ticker] = k["ensemble"]
                if k["ensemble"] >= min_ensemble:
                    kandidater.append(k)
        except Exception as e:
            log.error("Feil for %s: %s", navn, e)

    # Fyll inn ensemble for posisjoner filtrert bort av RSI/rel_styrke-sjekken
    # slik at ensemble=0-salgslogikken fungerer korrekt for disse
    for ticker in pf["posisjoner"]:
        if ticker not in alle_ensemble:
            ens = hent_ensemble_for_posisjon(ticker)
            alle_ensemble[ticker] = ens
            log.info("  Ensemble (RSI-filtrert posisjon): %s = %d/3", ticker, ens)

    kandidater.sort(
        key=lambda x: (
            x["ensemble"],
            x["score"] + x["oppside_score"] + x.get("råvare_score", 0)
            + x.get("insider_score", 0) + x.get("short_score", 0)
        ),
        reverse=True,
    )
    topp = kandidater[:maks_pos]

    # Utfør handler automatisk
    utforte       = []
    stop_loss_pct = pf.get("stop_loss_pct", DEFAULT_STOP_LOSS)
    topp_tickers  = {k["ticker"] for k in topp}

    # Hold-sone: behold posisjoner i topp 3×maks_pos med ensemble≥1 (hindrer unødvendig churning)
    hold_tickers  = {k["ticker"] for k in kandidater[:maks_pos * 3] if k["ensemble"] >= 1}

    # ── Trailing stop-loss: oppdater høyeste kurs, selg ved brudd ────────────
    for ticker, pos in list(pf["posisjoner"].items()):
        kurs = _hent_kurs_cached(ticker)
        if not kurs:
            continue
        høyeste = max(pos.get("høyeste_kurs", pos["snittpris"]), kurs)
        pf["posisjoner"][ticker]["høyeste_kurs"] = høyeste

        pos_sl = pos.get("stop_loss_pct", stop_loss_pct)
        tap_fra_topp = (kurs / høyeste - 1) * 100
        if tap_fra_topp <= -(pos_sl * 100):
            begrunnelse = (f"Trailing stop-loss utløst ({tap_fra_topp:.1f}% fra topp "
                           f"{høyeste:.2f} kr · kjøpspris {pos['snittpris']:.2f} kr)")
            _utfor_salg(pf, ticker, pos, kurs, begrunnelse, utforte)
            topp_tickers.discard(ticker)

    # ── Selg posisjoner der alle 3 ensemble-signaler har snudd negativt ──────
    for ticker, pos in list(pf["posisjoner"].items()):
        if ticker in topp_tickers:
            continue
        if alle_ensemble.get(ticker, 1) == 0:
            kurs = _hent_kurs_cached(ticker)
            if not kurs:
                continue
            _utfor_salg(pf, ticker, pos, kurs,
                        "Ensemble snudd (0/3 — Trend, MACD og Momentum alle negative)", utforte)
            topp_tickers.discard(ticker)

    # ── Oppdater utenfor-topp-streak (unike datoer) og selg ved streak ≥ 3 + min 15 dager ──
    idag = str(datetime.now().date())
    for ticker, pos in list(pf["posisjoner"].items()):
        if ticker in hold_tickers:
            pos["utenfor_topp_streak"] = 0
            pos.pop("utenfor_topp_sist_dato", None)
            log.info("HOLDER: %s — fortsatt i hold-sone (topp %d)", pos["navn"], maks_pos * 3)
            continue
        if ticker not in topp_tickers:
            # Bare tell opp streak én gang per dag (unngår at 30-min loop inflater streak)
            sist_dato = pos.get("utenfor_topp_sist_dato")
            if sist_dato != idag:
                pos["utenfor_topp_streak"] = pos.get("utenfor_topp_streak", 0) + 1
                pos["utenfor_topp_sist_dato"] = idag
            streak = pos["utenfor_topp_streak"]
            kjøpsdato = datetime.strptime(pos["kjøpsdato"][:10], "%Y-%m-%d").date()
            dager_holdt = (datetime.now().date() - kjøpsdato).days
            if dager_holdt < 15:
                log.info("HOLDER: %s — minimum holdingstid ikke nådd (%d/15 dager)", pos["navn"], dager_holdt)
                continue
            if streak < 3:
                log.info("HOLDER: %s — utenfor topp-liste %d/3 dager", pos["navn"], streak)
                continue
            kurs = _hent_kurs_cached(ticker)
            if not kurs:
                continue
            _utfor_salg(pf, ticker, pos, kurs,
                        f"Utenfor topp-kandidater {streak} dager på rad", utforte)

    # ── Kjøp topp-kandidater vi ikke allerede eier ───────────────────────────
    # Hent fundamentals for topp-kandidater (kun disse — sparer tid vs alle ~150 tickers)
    log.info("Henter fundamentals for %d topp-kandidater...", len(topp))
    topp_med_fund = []
    for k in topp:
        if k["ticker"] in pf["posisjoner"]:
            topp_med_fund.append(k)   # allerede eid — ikke filtrer ut
            continue
        fund = hent_fundamentals(k["ticker"])
        sektor = SEKTORER.get(k["ticker"], "Annet")
        ok, grunn = fundamental_ok(fund, sektor=sektor)
        if not ok:
            log.info("FUNDAMENTAL-FILTER: hopper over %s — %s", k["navn"], grunn)
            continue
        k["pe"]    = fund["pe"]
        k["pb"]    = fund["pb"]
        k["yield"] = fund["yield"]
        topp_med_fund.append(k)
    topp = topp_med_fund

    # Tell opp nåværende sektoreksponering
    sektor_teller = {}
    for ticker in pf["posisjoner"]:
        s = SEKTORER.get(ticker, "Annet")
        sektor_teller[s] = sektor_teller.get(s, 0) + 1

    for k in topp:
        if k["ticker"] in pf["posisjoner"]:
            continue
        # Sektorkap — hopp over hvis sektoren allerede er fullt
        sektor = SEKTORER.get(k["ticker"], "Annet")
        if sektor_teller.get(sektor, 0) >= maks_per_sektor:
            log.info("SEKTOR-KAP: hopper over %s (%s har allerede %d pos)", k["navn"], sektor, sektor_teller[sektor])
            continue
        # Volatilitetsbasert posisjonsstørrelse — stabile aksjer får mer, volatile mindre
        vol_60d    = k.get("vol_60d", TARGET_VOL)
        vol_faktor = TARGET_VOL / vol_60d if vol_60d > 0 else 1.0
        vol_faktor = max(0.5, min(2.0, vol_faktor))   # clamp til [50%, 200%]
        # Ensemble-boost: 3/3 signaler = høyere konfidens → 15% større posisjon
        ensemble_boost = 1.15 if k["ensemble"] >= 3 else 1.0
        # Momentum-reduksjon: halver posisjon ved ekstrem momentum (>100%)
        mom_faktor = 0.5 if abs(k.get("mom", 0)) > MOM_REDUKSJON_TERSKEL else 1.0
        beløp    = min(pf["kasse"] * allok * vol_faktor * ensemble_boost * mom_faktor, pf["kasse"] * MAKS_ALLOKERING)
        kurtasje = beregn_kurtasje(beløp, pf)

        # Kurtasje-ratio-sjekk: skaler opp posisjonen hvis kurtasjen er for dyr
        kurtasje_ratio_maks = pf.get("kurtasje_ratio_maks", MAKS_KURTASJE_RATIO)
        modell_navn = pf.get("kurtasje_modell", KURTASJE_STANDARD)
        min_kr      = KURTASJE_MODELLER.get(modell_navn, KURTASJE_MODELLER[KURTASJE_STANDARD])["min_kr"]
        min_beløp   = min_kr / kurtasje_ratio_maks         # f.eks. 29/0.02 = 1 450 kr (Mini)
        if beløp < min_beløp:
            # Forsøk å skalere opp til lønnsom posisjonsstørrelse (maks 50% av kasse)
            beløp    = min(min_beløp, pf["kasse"] * 0.5)
            kurtasje = beregn_kurtasje(beløp, pf)
            if kurtasje / beløp > kurtasje_ratio_maks:
                log.info("KURTASJE-KAP: hopper over %s — kassen (%.0f kr) er for liten for lønnsom handel",
                         k["navn"], pf["kasse"])
                continue

        antall   = int((beløp - kurtasje) / k["kurs"])
        if antall < 1 or beløp > pf["kasse"]:
            continue
        kostnad     = round(antall * k["kurs"], 0)
        totalt      = kostnad + kurtasje
        if totalt > pf["kasse"]:
            continue
        pe_tekst = f"P/E {k['pe']:.0f}" if k.get("pe") else "P/E –"
        pb_tekst = f"P/B {k['pb']:.1f}" if k.get("pb") else "P/B –"
        begrunnelse = (f"[{regime}] Ensemble {k['ensemble']}/3 ({k['ensemble_tekst']}) · "
                       f"mom {k['mom']:.1f}% · rel.styrke {k['rel_styrke']:.1f}% · "
                       f"RSI {k['rsi']:.0f} · {pe_tekst} · {pb_tekst}")
        vol_stop = max(0.05, min(0.10, k.get("vol_60d", TARGET_VOL) * 0.5))
        pf["posisjoner"][k["ticker"]] = {
            "navn": k["navn"], "antall": antall,
            "snittpris": k["kurs"], "kjøpsdato": str(datetime.now().date()),
            "høyeste_kurs": k["kurs"],
            "stop_loss_pct": round(vol_stop, 4),
            "signaler": {
                "ensemble":       k["ensemble"],
                "ensemble_tekst": k["ensemble_tekst"],
                "insider":        k.get("insider_score", 0) > 0,
                "short_score":    round(k.get("short_score", 0), 2),
                "råvare_score":   round(k.get("råvare_score", 0), 2),
                "regime":         regime,
            },
        }
        pf["kasse"] -= totalt
        sektor_teller[sektor] = sektor_teller.get(sektor, 0) + 1
        pf["historikk"].append({
            "dato": str(datetime.now()), "handling": "KJØP",
            "ticker": k["ticker"], "navn": k["navn"],
            "antall": antall, "kurs": k["kurs"], "beløp": kostnad,
            "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            "signaler": pf["posisjoner"][k["ticker"]]["signaler"],
        })
        utforte.append({
            "handling": "KJØP", "navn": k["navn"], "ticker": k["ticker"],
            "antall": antall, "kurs": k["kurs"], "beløp": kostnad,
            "kurtasje": kurtasje, "begrunnelse": begrunnelse,
        })
        log.info("KJØPT: %d × %s à %.2f kr = %.0f kr (kurtasje %.0f kr)",
                 antall, k["navn"], k["kurs"], kostnad, kurtasje)

    pf["ventende_handler"]  = []
    pf["sist_analysert"]    = str(datetime.now())
    pf["regime"]            = regime
    pf["råvare_trender"]    = råvare_trender
    pf["insider_kjøp"]      = sorted(insider_kjøp)
    pf["short_interest"]    = {t: round(p, 2) for t, p in short_interest.items() if p >= 2.0}

    # ── Lagre topp-kandidater for visning i Dashboard ────────────────────────
    pf["topp_kandidater"] = [
        {
            "navn":           k["navn"],
            "ticker":         k["ticker"],
            "ensemble":       k["ensemble"],
            "ensemble_tekst": k["ensemble_tekst"],
            "score":          round(k["score"], 2),
            "mom":            round(k["mom"], 1),
            "rel_styrke":     round(k["rel_styrke"], 1),
            "rsi":            round(k["rsi"], 0),
            "kurs":           round(k["kurs"], 2),
            "pe":             round(k["pe"], 1) if k.get("pe") else None,
            "pb":             round(k["pb"], 2) if k.get("pb") else None,
            "yield":          round(k["yield"], 1) if k.get("yield") else None,
        }
        for k in topp[:8]   # topp 8, uavhengig av om de ble kjøpt
    ]

    # ── Daglig snapshot av porteføljeverdi ───────────────────────────────────
    total_pos_verdi = 0
    for ticker, pos in pf["posisjoner"].items():
        kurs = _hent_kurs_cached(ticker)
        if kurs:
            total_pos_verdi += kurs * pos["antall"]
    total_verdi = pf["kasse"] + total_pos_verdi

    snapshot = {"dato": str(datetime.now().date()), "total_verdi": round(total_verdi, 0)}
    historikk_verdi = pf.get("verdi_historikk", [])
    # Erstatt hvis det allerede finnes en entry for i dag
    historikk_verdi = [s for s in historikk_verdi if s["dato"] != snapshot["dato"]]
    historikk_verdi.append(snapshot)
    historikk_verdi = historikk_verdi[-365:]   # behold maks 1 år
    pf["verdi_historikk"] = historikk_verdi

    # ── Daglig snapshot av urealisert P&L ────────────────────────────────────
    urealisert_kr = sum(
        (_hent_kurs_cached(t) or pos["snittpris"]) * pos["antall"] - pos["snittpris"] * pos["antall"]
        for t, pos in pf["posisjoner"].items()
    )
    u_snapshot = {"dato": str(datetime.now().date()), "verdi": round(urealisert_kr, 0)}
    u_hist = pf.get("urealisert_historikk", [])
    u_hist = [s for s in u_hist if s["dato"] != u_snapshot["dato"]]
    u_hist.append(u_snapshot)
    pf["urealisert_historikk"] = u_hist[-365:]

    if not dry_run:
        lagre_portefolje(pf)

    kjop_antall = len([f for f in utforte if f["handling"] == "KJØP"])
    selg_antall = len([f for f in utforte if f["handling"] == "SELG"])
    log.info("Analyse ferdig: %d kjøp utført, %d salg utført — porteføljeverdi %.0f kr%s",
             kjop_antall, selg_antall, total_verdi, " (DRY RUN — ikke lagret)" if dry_run else "")

    return utforte

# ── Saxo live-utførelse ───────────────────────────────────────────────────────

def _oppdater_saxo_refresh_token(nytt_token: str) -> None:
    """Update SAXO_REFRESH_TOKEN in GitHub Actions secrets after rotation."""
    import base64
    repo  = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not (repo and token):
        return
    try:
        import requests as _req
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        # Fetch repo public key
        r = _req.get(
            f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
        )
        if not r.ok:
            log.error("SAXO: Kunne ikke hente GitHub public key: %s", r.status_code)
            return
        key_data = r.json()
        key_id   = key_data["key_id"]
        pub_key  = base64.b64decode(key_data["key"])

        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
        from cryptography.hazmat.primitives.asymmetric import x25519
        recipient_key = x25519.X25519PublicKey.from_public_bytes(pub_key)
        # PyNaCl sealed box
        from nacl.encoding import Base64Encoder
        from nacl.public import PublicKey, SealedBox
        box       = SealedBox(PublicKey(pub_key))
        encrypted = base64.b64encode(box.encrypt(nytt_token.encode())).decode()

        r2 = _req.put(
            f"https://api.github.com/repos/{repo}/actions/secrets/SAXO_REFRESH_TOKEN",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            json={"encrypted_value": encrypted, "key_id": key_id},
        )
        if r2.status_code in (201, 204):
            log.info("SAXO: SAXO_REFRESH_TOKEN oppdatert i GitHub Secrets")
        else:
            log.error("SAXO: Kunne ikke oppdatere GitHub Secret: %s", r2.status_code)
    except Exception as e:
        log.error("SAXO: Feil ved oppdatering av refresh token: %s", e, exc_info=True)


def utfør_saxo_handler(utforte: list) -> None:
    """
    Execute trades via Saxo Bank OpenAPI.
    Only runs if SAXO_CLIENT_ID and SAXO_REFRESH_TOKEN are set in environment.
    Rotates SAXO_REFRESH_TOKEN in GitHub Secrets after each run.

    Ticker-mapping: yfinance uses "EQNR.OL" — Saxo wants "EQNR" (no .OL suffix).
    """
    if not (os.environ.get("SAXO_CLIENT_ID") and os.environ.get("SAXO_REFRESH_TOKEN")):
        return  # Saxo not configured — skip

    try:
        from saxo_client import SaxoClient
    except ImportError:
        log.warning("SAXO: saxo_client.py ikke funnet — hopper over live-utførelse")
        return

    if not utforte:
        log.info("SAXO: Ingen handler å utføre")
        return

    log.info("── Saxo live-utførelse ──────────────────────────────")
    try:
        klient = SaxoClient()
        if not klient.logg_inn():
            log.error("SAXO: Innlogging feilet — hopper over live-utførelse")
            return

        # Rotate refresh token in GitHub Secrets
        if klient.refresh_token:
            _oppdater_saxo_refresh_token(klient.refresh_token)

        # Build set of UICs we actually hold in Saxo
        saxo_posisjoner = klient.hent_posisjoner()
        holdte_uic = {
            p.get("NetPositionBase", {}).get("Uic")
            or p.get("PositionBase", {}).get("Uic")
            for p in saxo_posisjoner
        } - {None}

        for handel in utforte:
            ticker   = handel["ticker"]          # e.g. "EQNR.OL"
            symbol   = ticker.replace(".OL", "") # e.g. "EQNR"
            antall   = handel["antall"]
            handling = handel["handling"]

            uic = klient.finn_uic(symbol)
            if not uic:
                log.warning("SAXO: Fant ikke UIC for %s — hopper over", symbol)
                continue

            if handling == "KJØP":
                klient.kjop(uic, antall)
            elif handling == "SELG":
                if uic not in holdte_uic:
                    log.warning("SAXO: %s ikke i Saxo-portefølje — hopper over salg", symbol)
                    continue
                klient.selg(uic, antall)

    except Exception as e:
        log.error("SAXO: Feil under live-utførelse: %s", e, exc_info=True)


def sjekk_stop_loss() -> list:
    """
    Lettversjon: kun trailing stop-loss + oppdater høyeste_kurs.
    Ingen univers-scan, ingen nye kjøp. Brukes av ettermiddagskjøringen.
    """
    pf            = les_portefolje()
    stop_loss_pct = pf.get("stop_loss_pct", DEFAULT_STOP_LOSS)
    utforte       = []
    _kurs_cache.clear()

    log.info("Stop-loss-sjekk — %d posisjoner", len(pf["posisjoner"]))

    for ticker, pos in list(pf["posisjoner"].items()):
        kurs = _hent_kurs_cached(ticker)
        if not kurs:
            continue
        høyeste = max(pos.get("høyeste_kurs", pos["snittpris"]), kurs)
        pf["posisjoner"][ticker]["høyeste_kurs"] = høyeste

        pos_sl = pos.get("stop_loss_pct", stop_loss_pct)
        tap_fra_topp = (kurs / høyeste - 1) * 100
        if tap_fra_topp <= -(pos_sl * 100):
            begrunnelse = (f"Trailing stop-loss utløst ({tap_fra_topp:.1f}% fra topp "
                           f"{høyeste:.2f} kr · kjøpspris {pos['snittpris']:.2f} kr)")
            _utfor_salg(pf, ticker, pos, kurs, begrunnelse, utforte)

    lagre_portefolje(pf)
    log.info("Stop-loss-sjekk ferdig: %d salg utført", len(utforte))
    return utforte


def send_varsel(utforte: list, modus: str = "full") -> None:
    """
    Send push-varsel via ntfy.sh når boten har kjøpt eller solgt.
    Krever env-variabel NTFY_TOPIC (sett i GitHub Secrets).
    Ingen konto nødvendig — installer ntfy-appen og abonner på topic-et.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic or not utforte:
        return

    kjøp  = [h for h in utforte if h["handling"] == "KJØP"]
    salg  = [h for h in utforte if h["handling"] == "SELG"]
    linjer = []

    for h in kjøp:
        linjer.append(f"KJ: {h['navn']} — {h['antall']} stk à {h['kurs']:.2f} kr "
                      f"= {h['beløp']:,.0f} kr")
    for h in salg:
        linjer.append(f"SL: {h['navn']} — {h['antall']} stk à {h['kurs']:.2f} kr "
                      f"= {h['beløp']:,.0f} kr")

    tittel = f"Trading Bot - {len(kjøp)} kjøp, {len(salg)} salg"
    if modus == "stop-loss":
        tittel = f"Trading Bot - Stop-loss: {len(salg)} salg"

    for forsøk in range(3):
        try:
            resp = requests.post(
                f"https://ntfy.sh/{topic}",
                data="\n".join(linjer).encode("utf-8"),
                headers={
                    "Title":    tittel,
                    "Priority": "high" if salg else "default",
                    "Tags":     "chart_with_upwards_trend",
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Varsel sendt: %s", tittel)
            return
        except Exception as e:
            log.warning("Varsel forsøk %d/3 feilet: %s", forsøk + 1, e)
            if forsøk < 2:
                time.sleep(2 ** forsøk)
    log.error("Varsel feilet etter 3 forsøk — tittel: %s", tittel)


def send_ukentlig_rapport() -> None:
    """
    Sender ukentlig oppsummering via ntfy.sh hver fredag kl. 16:00.
    Viser avkastning, åpne posisjoner og ukens handler.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        log.warning("Ukentlig rapport: NTFY_TOPIC ikke satt — hopper over")
        return

    pf = les_portefolje()

    # Beregn live porteføljeverdi
    total_pos_verdi = 0
    pos_linjer = []
    for ticker, pos in pf["posisjoner"].items():
        kurs = hent_siste_kurs(ticker)
        if kurs:
            verdi        = kurs * pos["antall"]
            gevinst_pct  = (kurs / pos["snittpris"] - 1) * 100
            total_pos_verdi += verdi
            pos_linjer.append(f"  {pos['navn']}: {gevinst_pct:+.1f}% ({verdi:,.0f} kr)")

    total_verdi    = pf["kasse"] + total_pos_verdi
    start_kapital  = pf.get("start_kapital", 10000)
    avkastning_pct = (total_verdi / start_kapital - 1) * 100

    # Handler siste 7 dager
    fra_dato = (datetime.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    ukens_handler = [h for h in pf.get("historikk", []) if str(h["dato"])[:10] >= fra_dato]
    kjøp_uke = [h for h in ukens_handler if h["handling"] == "KJØP"]
    salg_uke = [h for h in ukens_handler if h["handling"] == "SELG"]

    linjer = [
        f"Uke {datetime.now().strftime('%W')} — {datetime.now().strftime('%d.%m.%Y')}",
        f"Portefølje: {total_verdi:,.0f} kr ({avkastning_pct:+.1f}% siden start)",
        f"Regime: {pf.get('regime', '–')} | Kasse: {pf['kasse']:,.0f} kr",
        "",
    ]

    if pos_linjer:
        linjer.append("Åpne posisjoner:")
        linjer.extend(pos_linjer)
    else:
        linjer.append("Ingen åpne posisjoner")

    if kjøp_uke or salg_uke:
        linjer.append("")
        linjer.append(f"Denne uken: {len(kjøp_uke)} kjøp, {len(salg_uke)} salg")
        for h in kjøp_uke:
            linjer.append(f"  KJ: {h['navn']} {h['antall']} stk à {h['kurs']:.2f} kr")
        for h in salg_uke:
            linjer.append(f"  SL: {h['navn']} {h['antall']} stk à {h['kurs']:.2f} kr")
    else:
        linjer.append("")
        linjer.append("Ingen handler denne uken")

    for forsøk in range(3):
        try:
            resp = requests.post(
                f"https://ntfy.sh/{topic}",
                data="\n".join(linjer).encode("utf-8"),
                headers={
                    "Title":    f"Trading Bot - Ukesrapport {avkastning_pct:+.1f}%",
                    "Priority": "default",
                    "Tags":     "bar_chart",
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Ukentlig rapport sendt: %.0f kr (%+.1f%%)", total_verdi, avkastning_pct)
            return
        except Exception as e:
            log.warning("Ukentlig rapport forsøk %d/3 feilet: %s", forsøk + 1, e)
            if forsøk < 2:
                time.sleep(2 ** forsøk)
    log.error("Ukentlig rapport feilet etter 3 forsøk")


def validate_startup():
    """Fail fast if critical dependencies are unavailable."""
    errors = []

    if not os.environ.get("NTFY_TOPIC"):
        log.warning("NTFY_TOPIC ikke satt — varsler vil bli hoppet over")

    # Test yfinance with multiple tickers and retry — single ticker can temporarily fail
    test_tickers = ["EQNR.OL", "DNB.OL", "NHY.OL"]
    yf_ok = False
    for attempt in range(3):
        for ticker in test_tickers:
            try:
                test = yf.download(ticker, period="1d", progress=False, timeout=15)
                if not test.empty:
                    yf_ok = True
                    break
            except Exception:
                continue
        if yf_ok:
            break
        if attempt < 2:
            log.warning("yfinance-test forsøk %d/3 feilet — prøver igjen om 10s", attempt + 1)
            time.sleep(10)

    if not yf_ok:
        errors.append("yfinance utilgjengelig etter 3 forsøk med flere tickers")

    # Sjekk at portfolio.json er lesbar
    try:
        les_portefolje()
    except Exception as e:
        errors.append(f"portfolio.json kan ikke leses: {e}")

    if errors:
        for err in errors:
            log.error("STARTUP-FEIL: %s", err)
        raise SystemExit(1)

    log.info("Startup-validering OK")


if __name__ == "__main__":
    import sys
    if "--test-varsel" in sys.argv:
        send_varsel([{
            "handling": "KJØP", "navn": "Test AS", "antall": 10,
            "kurs": 100.0, "beløp": 1000.0,
        }], modus="full")
    elif "--ukentlig-rapport" in sys.argv:
        send_ukentlig_rapport()
    elif "--only-stop-loss" in sys.argv:
        validate_startup()
        resultat = sjekk_stop_loss()
        utfør_saxo_handler(resultat)
        send_varsel(resultat, modus="stop-loss")
    elif "--dry-run" in sys.argv:
        validate_startup()
        resultat = _run_with_timeout(
            lambda: kjor_analyse(dry_run=True),
            ANALYSE_TIMEOUT_SEC, label="kjor_analyse (dry-run)")
        if resultat is None:
            log.error("Full analyse timed out etter %ds", ANALYSE_TIMEOUT_SEC)
    else:
        validate_startup()
        resultat = _run_with_timeout(kjor_analyse, ANALYSE_TIMEOUT_SEC, label="kjor_analyse")
        if resultat is None:
            log.error("Full analyse timed out etter %ds — ingen handler utført", ANALYSE_TIMEOUT_SEC)
            resultat = []
        utfør_saxo_handler(resultat)
        send_varsel(resultat, modus="full")
