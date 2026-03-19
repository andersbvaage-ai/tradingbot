"""
Kjøres automatisk av GitHub Actions hver børsdag kl 09:15.
Analyserer Oslo Børs, oppdaterer portfolio.json med nye forslag,
og sender e-post hvis det er nye kjøps- eller salgsforslag.
"""

import yfinance as yf
import pandas as pd
import json
import os
import requests
from datetime import datetime

PORTFOLIO_FIL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.json")

DEFAULT_PORTEFOLJE = {
    "kasse": 100000,
    "start_kapital": 100000,
    "posisjoner": {},
    "ventende_handler": [],
    "historikk": []
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
MIN_ENSEMBLE      = 2      # krever minst 2 av 3 strategier enige (Trend/MACD/Momentum)
DEFAULT_STOP_LOSS  = 0.15   # selg hvis posisjon er ned >15% fra kjøpspris
MAKS_PER_SEKTOR    = 2      # maks antall posisjoner fra samme sektor

SEKTORER = {
    "EQNR.OL":"Energi",    "DNB.OL":"Finans",     "MOWI.OL":"Sjømat",    "TEL.OL":"Telekom",
    "NHY.OL":"Industri",   "ORK.OL":"Forbruker",  "YAR.OL":"Industri",   "AKERBP.OL":"Energi",
    "SALM.OL":"Sjømat",    "SUBC.OL":"Energi",    "STB.OL":"Finans",     "GJF.OL":"Finans",
    "SRBANK.OL":"Finans",  "KOG.OL":"Industri",   "AKSO.OL":"Energi",    "SCATC.OL":"Fornybar",
    "NEL.OL":"Fornybar",   "NOD.OL":"Teknologi",  "KAHOT.OL":"Teknologi","AUTO.OL":"Teknologi",
    "RECSI.OL":"Fornybar", "TGS.OL":"Energi",     "PGS.OL":"Energi",     "BWO.OL":"Energi",
    "GOGL.OL":"Shipping",  "FLNG.OL":"Shipping",  "MPCC.OL":"Shipping",  "BORR.OL":"Energi",
    "AFG.OL":"Industri",   "BOUVET.OL":"Teknologi","ODF.OL":"Shipping",  "AKER.OL":"Industri",
    "WAWI.OL":"Shipping",  "KIT.OL":"Teknologi",  "TOM.OL":"Industri",   "ELK.OL":"Industri",
    "VAR.OL":"Energi",     "VEI.OL":"Industri",   "LSG.OL":"Sjømat",     "GSF.OL":"Sjømat",
    "DNO.OL":"Energi",     "OKEA.OL":"Energi",    "ARCHER.OL":"Energi",  "BWE.OL":"Energi",
    "SDRL.OL":"Energi",    "ODL.OL":"Energi",     "NORECO.OL":"Energi",  "AGAS.OL":"Energi",
    "EMGS.OL":"Energi",    "REACH.OL":"Energi",   "AMSC.OL":"Shipping",  "ACC.OL":"Fornybar",
    "AKH.OL":"Industri",   "AKBM.OL":"Sjømat",    "IOX.OL":"Energi",     "PRS.OL":"Energi",
    "HEX.OL":"Industri",   "DOF.OL":"Energi",     "EIOF.OL":"Energi",    "TECO2.OL":"Fornybar",
    "FRO.OL":"Shipping",   "BWLPG.OL":"Shipping", "HAUTO.OL":"Shipping", "HAVI.OL":"Shipping",
    "HUNT.OL":"Shipping",  "SNI.OL":"Shipping",   "SOFF.OL":"Energi",    "SIOFF.OL":"Energi",
    "2020.OL":"Shipping",  "SOLT.OL":"Shipping",  "WWI.OL":"Shipping",   "WWIB.OL":"Shipping",
    "OHT.OL":"Shipping",   "NAS.OL":"Transport",  "FJORD.OL":"Transport","BON.OL":"Energi",
    "MING.OL":"Finans",    "NONG.OL":"Finans",    "SBVG.OL":"Finans",    "SPOL.OL":"Finans",
    "MORG.OL":"Finans",    "SVEG.OL":"Finans",    "SOR.OL":"Finans",     "PARB.OL":"Finans",
    "SADG.OL":"Finans",    "PROT.OL":"Finans",    "KOMP.OL":"Finans",    "B2H.OL":"Finans",
    "ACR.OL":"Finans",     "HELG.OL":"Finans",    "TOTG.OL":"Finans",    "AURG.OL":"Finans",
    "JAREN.OL":"Finans",   "AUSS.OL":"Sjømat",    "NRS.OL":"Sjømat",     "BAKKA.OL":"Sjømat",
    "NORDH.OL":"Sjømat",   "PEXIP.OL":"Teknologi","LINK.OL":"Teknologi", "IDEX.OL":"Teknologi",
    "NEXT.OL":"Teknologi", "INF.OL":"Teknologi",  "SMCRT.OL":"Teknologi","STRONG.OL":"Teknologi",
    "QFR.OL":"Teknologi",  "TEKNA.OL":"Teknologi","GIG.OL":"Teknologi",  "WSTEP.OL":"Teknologi",
    "ZAP.OL":"Teknologi",  "THIN.OL":"Teknologi", "KA.OL":"Industri",    "CARA.OL":"Teknologi",
    "ITERA.OL":"Teknologi","NORBIT.OL":"Teknologi","ENTRA.OL":"Eiendom", "OLT.OL":"Eiendom",
    "SOLON.OL":"Eiendom",  "NPRO.OL":"Eiendom",   "SBO.OL":"Eiendom",    "XXL.OL":"Forbruker",
    "KID.OL":"Forbruker",  "EPR.OL":"Forbruker",  "SATS.OL":"Forbruker", "SCHA.OL":"Media",
    "SCHB.OL":"Media",     "FKRAFT.OL":"Fornybar", "AFK.OL":"Industri",  "MULTI.OL":"Industri",
    "NOM.OL":"Industri",   "CIRCA.OL":"Industri", "AHG.OL":"Helse",      "AKVA.OL":"Industri",
    "MPCES.OL":"Fornybar", "CLOUD.OL":"Fornybar", "PHO.OL":"Helse",      "VISTN.OL":"Helse",
    "MEDI.OL":"Helse",     "NRC.OL":"Industri",   "ULTI.OL":"Helse",     "NNV.OL":"Helse",
    "HBC.OL":"Industri",   "TRVX.OL":"Helse",     "NUM.OL":"Industri",   "AGLX.OL":"Industri",
    "SAGA.OL":"Industri",
}
MAKS_KURTASJE_RATIO = 0.02  # kurtasje skal ikke overstige 2% av posisjonsstørrelsen
TARGET_VOL          = 0.20  # referansevolatilitet for posisjonsstørrelse (20% annualisert)

# Nordnet kurtasjemodeller (Norden/Oslo Børs)
KURTASJE_MODELLER = {
    "Mini":   {"pct": 0.0015, "min_kr": 29},   # Best for handler < 52 667 kr
    "Normal": {"pct": 0.00049, "min_kr": 79},  # Best for handler > 52 667 kr
}
KURTASJE_STANDARD = "Mini"  # Standard-modell

SYKLISKE_SEKTORER = {"Energi", "Shipping"}  # sektorer der utbytte er et kvalitetstegn

def hent_fundamentals(ticker):
    """Henter P/E, P/B og dividendYield fra yfinance. Returnerer dict — felt er None hvis ukjent."""
    try:
        info = yf.Ticker(ticker).info
        pe    = info.get("trailingPE") or info.get("forwardPE")
        pb    = info.get("priceToBook")
        yield_ = info.get("dividendYield")   # f.eks. 0.045 = 4,5%
        return {"pe": pe, "pb": pb, "yield": yield_}
    except Exception:
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
    "Bull":     {"min_ensemble": 2, "maks_pos": 6, "allok": 0.15, "maks_per_sektor": 3},
    "Sideways": {"min_ensemble": 2, "maks_pos": 4, "allok": 0.12, "maks_per_sektor": 2},
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
    with open(PORTFOLIO_FIL, "r") as f:
        return json.load(f)

def lagre_portefolje(p):
    with open(PORTFOLIO_FIL, "w") as f:
        json.dump(p, f, indent=2, default=str)

def hent_siste_kurs(ticker):
    """Henter siste kurs via 1-minutts intraday (kjøres alltid i åpningstiden fra scheduler)."""
    try:
        raw = yf.download(ticker, period="1d", interval="1m", progress=False, timeout=15)
        if raw.empty:
            raise ValueError("tom")
        raw.columns = raw.columns.get_level_values(0)
        return float(raw["Close"].dropna().iloc[-1])
    except Exception:
        try:
            raw = yf.download(ticker, period="2d", progress=False, timeout=10)
            if raw.empty:
                return None
            raw.columns = raw.columns.get_level_values(0)
            return float(raw["Close"].iloc[-1])
        except Exception:
            return None

def analyser_aksje(navn, ticker, osebx_ret3m):
    raw = yf.download(ticker, period="1y", progress=False, timeout=15)
    if raw.empty or len(raw) < 60:
        return None
    raw.columns = raw.columns.get_level_values(0)
    close  = raw["Close"]
    volume = raw["Volume"]
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
    rsi_ok    = 30 < rsi < 72
    ensemble  = sum([sma_vote, macd_vote, mom_vote])

    if not rsi_ok:
        return None  # RSI utenfor gyldig sone

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

def send_epost(forslag, epost_til, epost_fra, epost_passord):
    if not forslag:
        return

    kjop = [f for f in forslag if f["handling"] == "KJØP"]
    selg = [f for f in forslag if f["handling"] == "SELG"]

    linjer = [f"Nordic Trading Bot — {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"]

    if kjop:
        linjer.append("KJØPSFORSLAG:")
        for f in kjop:
            linjer.append(f"  ✅ {f['navn']} ({f['ticker']}) — {f['antall']} aksjer à {f['kurs']:.2f} kr = {f['beløp']:,.0f} kr")
            linjer.append(f"     {f['begrunnelse']}")

    if selg:
        linjer.append("\nSALGSFORSLAG:")
        for f in selg:
            linjer.append(f"  🔴 {f['navn']} ({f['ticker']}) — {f['antall']} aksjer à {f['kurs']:.2f} kr = {f['beløp']:,.0f} kr")
            linjer.append(f"     {f['begrunnelse']}")

    linjer.append("\nÅpne appen for å godkjenne eller avvise forslagene.")

    msg = MIMEText("\n".join(linjer), "plain", "utf-8")
    msg["Subject"] = f"Trading Bot: {len(kjop)} kjøp, {len(selg)} salg foreslått"
    msg["From"]    = epost_fra
    msg["To"]      = epost_til

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(epost_fra, epost_passord)
        smtp.send_message(msg)

    print(f"E-post sendt til {epost_til}")

def kjor_analyse():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starter analyse...")
    pf    = les_portefolje()
    kasse = pf["kasse"]

    # Hent OSEBX — trenger 1y for SMA200 (regime) og 3mnd-retur
    osebx_ret3m = 0.0
    regime      = "Sideways"
    try:
        osebx = yf.download("^OSEBX", period="1y", progress=False, timeout=15)
        osebx.columns = osebx.columns.get_level_values(0)
        osebx_close = osebx["Close"]
        if len(osebx_close) >= 63:
            osebx_ret3m = float(osebx_close.pct_change(63).iloc[-1] * 100)
        regime = detect_regime(osebx_close)
    except Exception:
        pass

    rcfg             = REGIME_CONFIG[regime]
    min_ensemble     = rcfg["min_ensemble"]
    maks_pos         = rcfg["maks_pos"]
    allok            = rcfg["allok"]
    maks_per_sektor  = rcfg["maks_per_sektor"]
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Regime: {regime} "
          f"(ensemble≥{min_ensemble}, maks {maks_pos} pos, {allok*100:.0f}%/pos, "
          f"maks {maks_per_sektor}/sektor)")

    # Analyser alle aksjer — samle ensemble for alle (inkl. eksisterende posisjoner)
    kandidater    = []
    alle_ensemble = {}   # ticker → ensemble-count for posisjonssjekk
    for navn, ticker in UNIVERS.items():
        print(f"  Analyserer {navn}...")
        try:
            k = analyser_aksje(navn, ticker, osebx_ret3m)
            if k:
                alle_ensemble[ticker] = k["ensemble"]
                if k["ensemble"] >= min_ensemble:
                    kandidater.append(k)
        except Exception as e:
            print(f"  Feil for {navn}: {e}")

    kandidater.sort(key=lambda x: x["score"] + x["oppside_score"], reverse=True)
    topp = kandidater[:maks_pos]

    # Utfør handler automatisk
    utforte       = []
    stop_loss_pct = pf.get("stop_loss_pct", DEFAULT_STOP_LOSS)
    topp_tickers  = {k["ticker"] for k in topp}

    # Hold-sone: behold posisjoner i topp 2×maks_pos med ensemble≥1 (hindrer unødvendig churning)
    hold_tickers  = {k["ticker"] for k in kandidater[:maks_pos * 3] if k["ensemble"] >= 1}

    # ── Trailing stop-loss: oppdater høyeste kurs, selg ved brudd ────────────
    for ticker, pos in list(pf["posisjoner"].items()):
        kurs = hent_siste_kurs(ticker)
        if not kurs:
            continue
        # Oppdater høyeste kurs siden kjøp (trailing-referanse)
        høyeste = max(pos.get("høyeste_kurs", pos["snittpris"]), kurs)
        pf["posisjoner"][ticker]["høyeste_kurs"] = høyeste

        # Utløs salg hvis kursen faller mer enn stop_loss_pct fra toppen
        tap_fra_topp = (kurs / høyeste - 1) * 100
        if tap_fra_topp <= -(stop_loss_pct * 100):
            brutto      = round(pos["antall"] * kurs, 0)
            kurtasje    = beregn_kurtasje(brutto, pf)
            inntekt     = brutto - kurtasje
            begrunnelse = (f"Trailing stop-loss utløst ({tap_fra_topp:.1f}% fra topp "
                           f"{høyeste:.2f} kr · kjøpspris {pos['snittpris']:.2f} kr)")
            del pf["posisjoner"][ticker]
            pf["kasse"] += inntekt
            topp_tickers.discard(ticker)
            pf["historikk"].append({
                "dato": str(datetime.now()), "handling": "SELG",
                "ticker": ticker, "navn": pos["navn"],
                "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
                "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            })
            utforte.append({
                "handling": "SELG", "navn": pos["navn"], "ticker": ticker,
                "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
                "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            })
            print(f"  TRAILING SL: {pos['antall']} × {pos['navn']} à {kurs:.2f} kr "
                  f"({tap_fra_topp:.1f}% fra topp {høyeste:.2f} kr) = {brutto:,.0f} kr "
                  f"(kurtasje {kurtasje:,.0f} kr)")

    # ── Selg posisjoner der alle 3 ensemble-signaler har snudd negativt ──────
    for ticker, pos in list(pf["posisjoner"].items()):
        if ticker in topp_tickers:
            continue   # allerede planlagt å beholde
        if alle_ensemble.get(ticker, 1) == 0:
            kurs = hent_siste_kurs(ticker)
            if not kurs:
                continue
            brutto      = round(pos["antall"] * kurs, 0)
            kurtasje    = beregn_kurtasje(brutto, pf)
            inntekt     = brutto - kurtasje
            begrunnelse = "Ensemble snudd (0/3 — Trend, MACD og Momentum alle negative)"
            del pf["posisjoner"][ticker]
            pf["kasse"] += inntekt
            topp_tickers.discard(ticker)
            pf["historikk"].append({
                "dato": str(datetime.now()), "handling": "SELG",
                "ticker": ticker, "navn": pos["navn"],
                "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
                "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            })
            utforte.append({
                "handling": "SELG", "navn": pos["navn"], "ticker": ticker,
                "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
                "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            })
            print(f"  ENSEMBLE=0: solgt {pos['navn']} à {kurs:.2f} kr = {brutto:,.0f} kr")

    # ── Selg posisjoner som har falt ut av hold-sonen (topp 2×N, ensemble≥1) ──
    for ticker, pos in list(pf["posisjoner"].items()):
        if ticker in hold_tickers:
            print(f"  HOLDER: {pos['navn']} — fortsatt i hold-sone (topp {maks_pos * 2})")
            continue
        if ticker not in topp_tickers:
            kurs = hent_siste_kurs(ticker)
            if not kurs:
                continue
            brutto      = round(pos["antall"] * kurs, 0)
            kurtasje    = beregn_kurtasje(brutto, pf)
            inntekt     = brutto - kurtasje
            begrunnelse = "Ikke lenger blant topp-kandidater"
            del pf["posisjoner"][ticker]
            pf["kasse"] += inntekt
            pf["historikk"].append({
                "dato": str(datetime.now()), "handling": "SELG",
                "ticker": ticker, "navn": pos["navn"],
                "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
                "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            })
            utforte.append({
                "handling": "SELG", "navn": pos["navn"], "ticker": ticker,
                "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
                "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            })
            print(f"  SOLGT: {pos['antall']} × {pos['navn']} à {kurs:.2f} kr = {brutto:,.0f} kr "
                  f"(kurtasje {kurtasje:,.0f} kr)")

    # ── Kjøp topp-kandidater vi ikke allerede eier ───────────────────────────
    # Hent fundamentals for topp-kandidater (kun disse — sparer tid vs alle ~150 tickers)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Henter fundamentals for {len(topp)} topp-kandidater...")
    topp_med_fund = []
    for k in topp:
        if k["ticker"] in pf["posisjoner"]:
            topp_med_fund.append(k)   # allerede eid — ikke filtrer ut
            continue
        fund = hent_fundamentals(k["ticker"])
        sektor = SEKTORER.get(k["ticker"], "Annet")
        ok, grunn = fundamental_ok(fund, sektor=sektor)
        if not ok:
            print(f"  FUNDAMENTAL-FILTER: hopper over {k['navn']} — {grunn}")
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
            print(f"  SEKTOR-KAP: hopper over {k['navn']} ({sektor} har allerede {sektor_teller[sektor]} pos)")
            continue
        # Volatilitetsbasert posisjonsstørrelse — stabile aksjer får mer, volatile mindre
        vol_60d    = k.get("vol_60d", TARGET_VOL)
        vol_faktor = TARGET_VOL / vol_60d if vol_60d > 0 else 1.0
        vol_faktor = max(0.5, min(2.0, vol_faktor))   # clamp til [50%, 200%]
        # Ensemble-boost: 3/3 signaler = høyere konfidens → 15% større posisjon
        ensemble_boost = 1.15 if k["ensemble"] >= 3 else 1.0
        beløp    = min(pf["kasse"] * allok * vol_faktor * ensemble_boost, pf["kasse"] * MAKS_ALLOKERING)
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
                print(f"  KURTASJE-KAP: hopper over {k['navn']} — "
                      f"kassen ({pf['kasse']:,.0f} kr) er for liten for lønnsom handel")
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
        pf["posisjoner"][k["ticker"]] = {
            "navn": k["navn"], "antall": antall,
            "snittpris": k["kurs"], "kjøpsdato": str(datetime.now().date()),
            "høyeste_kurs": k["kurs"],
        }
        pf["kasse"] -= totalt
        sektor_teller[sektor] = sektor_teller.get(sektor, 0) + 1
        pf["historikk"].append({
            "dato": str(datetime.now()), "handling": "KJØP",
            "ticker": k["ticker"], "navn": k["navn"],
            "antall": antall, "kurs": k["kurs"], "beløp": kostnad,
            "kurtasje": kurtasje, "begrunnelse": begrunnelse,
        })
        utforte.append({
            "handling": "KJØP", "navn": k["navn"], "ticker": k["ticker"],
            "antall": antall, "kurs": k["kurs"], "beløp": kostnad,
            "kurtasje": kurtasje, "begrunnelse": begrunnelse,
        })
        print(f"  KJØPT: {antall} × {k['navn']} à {k['kurs']:.2f} kr = {kostnad:,.0f} kr "
              f"(kurtasje {kurtasje:,.0f} kr)")

    pf["ventende_handler"] = []
    pf["sist_analysert"]   = str(datetime.now())
    pf["regime"]           = regime

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
            "yield":          round(k["yield"] * 100, 1) if k.get("yield") else None,
        }
        for k in topp[:8]   # topp 8, uavhengig av om de ble kjøpt
    ]

    # ── Daglig snapshot av porteføljeverdi ───────────────────────────────────
    total_pos_verdi = 0
    for ticker, pos in pf["posisjoner"].items():
        kurs = hent_siste_kurs(ticker)
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

    lagre_portefolje(pf)

    kjop_antall = len([f for f in utforte if f["handling"] == "KJØP"])
    selg_antall = len([f for f in utforte if f["handling"] == "SELG"])
    print(f"Analyse ferdig: {kjop_antall} kjøp utført, {selg_antall} salg utført "
          f"— porteføljeverdi {total_verdi:,.0f} kr")

    return utforte

# ── Nordnet live-utførelse ────────────────────────────────────────────────────

def utfør_nordnet_handler(utforte: list) -> None:
    """
    Kjør de samme handlene som scheduler bestemte, men nå mot Nordnet API.
    Kalles kun hvis NORDNET_API_KEY og NORDNET_PRIV_KEY er satt i miljøet.

    Ticker-mapping: yfinance bruker "EQNR.OL" — Nordnet vil ha "EQNR" (uten .OL).
    """
    if not (os.environ.get("NORDNET_API_KEY") and os.environ.get("NORDNET_PRIV_KEY")):
        return  # Nordnet ikke konfigurert — hopp over

    try:
        from nordnet_client import NordnetClient
    except ImportError:
        print("NORDNET: nordnet_client.py ikke funnet — hopper over live-utførelse")
        return

    if not utforte:
        print("NORDNET: Ingen handler å utføre")
        return

    print("\n── Nordnet live-utførelse ──────────────────────────")
    try:
        with NordnetClient() as klient:
            kontoer = klient.hent_kontoer()
            if not kontoer:
                print("NORDNET: Ingen kontoer funnet")
                return
            konto = kontoer[0]
            print(f"NORDNET: Bruker konto {konto.get('accno') or konto.get('accid')}")

            for handel in utforte:
                ticker   = handel["ticker"]          # e.g. "EQNR.OL"
                symbol   = ticker.replace(".OL", "") # e.g. "EQNR"
                antall   = handel["antall"]
                handling = handel["handling"]

                instrument_id = klient.finn_instrument_id(symbol)
                if not instrument_id:
                    print(f"NORDNET: Fant ikke instrument for {symbol} — hopper over")
                    continue

                if handling == "KJØP":
                    klient.kjøp(konto, instrument_id, antall)
                elif handling == "SELG":
                    klient.selg(konto, instrument_id, antall)

    except Exception as e:
        print(f"NORDNET: Feil under live-utførelse: {e}")


def sjekk_stop_loss() -> list:
    """
    Lettversjon: kun trailing stop-loss + oppdater høyeste_kurs.
    Ingen univers-scan, ingen nye kjøp. Brukes av ettermiddagskjøringen.
    """
    pf            = les_portefolje()
    stop_loss_pct = pf.get("stop_loss_pct", DEFAULT_STOP_LOSS)
    utforte       = []

    print(f"[{datetime.now().strftime('%H:%M')}] Stop-loss-sjekk — {len(pf['posisjoner'])} posisjoner")

    for ticker, pos in list(pf["posisjoner"].items()):
        kurs = hent_siste_kurs(ticker)
        if not kurs:
            continue
        høyeste = max(pos.get("høyeste_kurs", pos["snittpris"]), kurs)
        pf["posisjoner"][ticker]["høyeste_kurs"] = høyeste

        tap_fra_topp = (kurs / høyeste - 1) * 100
        if tap_fra_topp <= -(stop_loss_pct * 100):
            brutto      = round(pos["antall"] * kurs, 0)
            kurtasje    = beregn_kurtasje(brutto, pf)
            inntekt     = brutto - kurtasje
            begrunnelse = (f"Trailing stop-loss utløst ({tap_fra_topp:.1f}% fra topp "
                           f"{høyeste:.2f} kr · kjøpspris {pos['snittpris']:.2f} kr)")
            del pf["posisjoner"][ticker]
            pf["kasse"] += inntekt
            pf["historikk"].append({
                "dato": str(datetime.now()), "handling": "SELG",
                "ticker": ticker, "navn": pos["navn"],
                "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
                "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            })
            utforte.append({
                "handling": "SELG", "navn": pos["navn"], "ticker": ticker,
                "antall": pos["antall"], "kurs": kurs, "beløp": brutto,
                "kurtasje": kurtasje, "begrunnelse": begrunnelse,
            })
            print(f"  TRAILING SL: solgt {pos['navn']} à {kurs:.2f} kr "
                  f"({tap_fra_topp:.1f}% fra topp {høyeste:.2f} kr)")

    lagre_portefolje(pf)
    print(f"Stop-loss-sjekk ferdig: {len(utforte)} salg utført")
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

    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data="\n".join(linjer).encode("utf-8"),
            headers={
                "Title":    tittel,
                "Priority": "high" if salg else "default",
                "Tags":     "chart_with_upwards_trend",
            },
            timeout=10,
        )
        print(f"Varsel sendt: {tittel}")
    except Exception as e:
        print(f"Varsel feilet: {e}")


def send_ukentlig_rapport() -> None:
    """
    Sender ukentlig oppsummering via ntfy.sh hver fredag kl. 16:00.
    Viser avkastning, åpne posisjoner og ukens handler.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("Ukentlig rapport: NTFY_TOPIC ikke satt — hopper over")
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

    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data="\n".join(linjer).encode("utf-8"),
            headers={
                "Title":    f"Trading Bot — Ukesrapport {avkastning_pct:+.1f}%",
                "Priority": "default",
                "Tags":     "bar_chart",
            },
            timeout=10,
        )
        print(f"Ukentlig rapport sendt: {total_verdi:,.0f} kr ({avkastning_pct:+.1f}%)")
    except Exception as e:
        print(f"Ukentlig rapport feilet: {e}")


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
        resultat = sjekk_stop_loss()
        send_varsel(resultat, modus="stop-loss")
    else:
        resultat = kjor_analyse()
        send_varsel(resultat, modus="full")
        utfør_nordnet_handler(resultat)
