"""
Kjøres automatisk av GitHub Actions hver børsdag kl 09:15.
Analyserer Oslo Børs, oppdaterer portfolio.json med nye forslag,
og sender e-post hvis det er nye kjøps- eller salgsforslag.
"""

import yfinance as yf
import pandas as pd
import json
import os
import smtplib
from email.mime.text import MIMEText
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

MAKS_POSISJONER  = 6
ALLOKERING_PCT   = 0.15   # 15% av kasse per posisjon
MAKS_ALLOKERING  = 0.20   # aldri mer enn 20% i én aksje
MIN_REL_STYRKE   = 0      # må slå OSEBX siste 3 mnd


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

    score = sum([sma10 > sma50, 40 < rsi < 65, macd_v > sig_v, mom > 0])
    oppside_score = (rel_styrke / 10) + (vol_økning / 50) + (nærhet_topp / 100)

    return {
        "navn": navn, "ticker": ticker, "kurs": pris,
        "score": score, "rsi": rsi, "mom": mom,
        "rel_styrke": rel_styrke, "vol_økning": vol_økning,
        "nærhet_topp": nærhet_topp, "oppside_score": oppside_score,
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

    # Hent OSEBX referanseavkastning
    osebx_ret3m = 0.0
    try:
        osebx = yf.download("^OSEBX", period="6mo", progress=False, timeout=15)
        osebx.columns = osebx.columns.get_level_values(0)
        if len(osebx) >= 63:
            osebx_ret3m = float(osebx["Close"].pct_change(63).iloc[-1] * 100)
    except Exception:
        pass

    # Analyser alle aksjer
    kandidater = []
    for navn, ticker in OSLO_BORS.items():
        print(f"  Analyserer {navn}...")
        try:
            k = analyser_aksje(navn, ticker, osebx_ret3m)
            if k:
                kandidater.append(k)
        except Exception as e:
            print(f"  Feil for {navn}: {e}")

    kandidater.sort(key=lambda x: x["score"] + x["oppside_score"], reverse=True)
    topp = kandidater[:MAKS_POSISJONER]

    # Generer forslag
    forslag   = []
    nåværende = set(pf["posisjoner"].keys())
    topp_tickers = {k["ticker"] for k in topp}

    for k in topp:
        beløp  = min(kasse * ALLOKERING_PCT, kasse * MAKS_ALLOKERING)
        antall = int(beløp / k["kurs"])
        if antall < 1:
            continue
        begrunnelse = (f"Score {k['score']}/4 · mom {k['mom']:.1f}% · "
                       f"rel.styrke {k['rel_styrke']:.1f}% · vol↑{k['vol_økning']:.0f}%")
        handling = "HOLD" if k["ticker"] in nåværende else "KJØP"
        forslag.append({
            "handling": handling, "navn": k["navn"], "ticker": k["ticker"],
            "kurs": k["kurs"], "antall": antall,
            "beløp": round(antall * k["kurs"], 0),
            "score": k["score"], "begrunnelse": begrunnelse,
        })

    # Salgsforslag for posisjoner som ikke lenger er topp-kandidater
    for ticker, pos in pf["posisjoner"].items():
        if ticker not in topp_tickers:
            kurs = hent_siste_kurs(ticker)
            if kurs:
                forslag.append({
                    "handling": "SELG", "navn": pos["navn"], "ticker": ticker,
                    "kurs": kurs, "antall": pos["antall"],
                    "beløp": round(pos["antall"] * kurs, 0),
                    "score": 0, "begrunnelse": "Ikke lenger blant topp-kandidater",
                })

    # Lagre forslag i portfolio.json
    pf["ventende_handler"] = forslag
    pf["sist_analysert"]   = str(datetime.now())
    lagre_portefolje(pf)

    nye = [f for f in forslag if f["handling"] in ("KJØP", "SELG")]
    print(f"Analyse ferdig: {len([f for f in nye if f['handling']=='KJØP'])} kjøp, "
          f"{len([f for f in nye if f['handling']=='SELG'])} salg foreslått")

    # Send e-post hvis konfigurert
    epost_til      = os.environ.get("EPOST_TIL")
    epost_fra      = os.environ.get("EPOST_FRA")
    epost_passord  = os.environ.get("EPOST_PASSORD")
    if epost_til and epost_fra and epost_passord and nye:
        try:
            send_epost(nye, epost_til, epost_fra, epost_passord)
        except Exception as e:
            print(f"E-post feilet: {e}")

    return forslag

if __name__ == "__main__":
    kjor_analyse()
