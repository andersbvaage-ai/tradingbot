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
DEFAULT_STOP_LOSS = 0.15   # selg hvis posisjon er ned >15% fra kjøpspris

REGIME_CONFIG = {
    "Bull":     {"min_ensemble": 2, "maks_pos": 6, "allok": 0.15},
    "Sideways": {"min_ensemble": 2, "maks_pos": 4, "allok": 0.12},
    "Bear":     {"min_ensemble": 3, "maks_pos": 2, "allok": 0.10},
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

    return {
        "navn": navn, "ticker": ticker, "kurs": pris,
        "score": score, "ensemble": ensemble, "ensemble_tekst": stemmer,
        "rsi": rsi, "mom": mom, "rel_styrke": rel_styrke,
        "vol_økning": vol_økning, "nærhet_topp": nærhet_topp, "oppside_score": oppside_score,
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

    rcfg         = REGIME_CONFIG[regime]
    min_ensemble = rcfg["min_ensemble"]
    maks_pos     = rcfg["maks_pos"]
    allok        = rcfg["allok"]
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Regime: {regime} "
          f"(ensemble≥{min_ensemble}, maks {maks_pos} pos, {allok*100:.0f}%/pos)")

    # Analyser alle aksjer — bruk regime-basert ensemble-krav
    kandidater = []
    for navn, ticker in UNIVERS.items():
        print(f"  Analyserer {navn}...")
        try:
            k = analyser_aksje(navn, ticker, osebx_ret3m)
            if k and k["ensemble"] >= min_ensemble:
                kandidater.append(k)
        except Exception as e:
            print(f"  Feil for {navn}: {e}")

    kandidater.sort(key=lambda x: x["score"] + x["oppside_score"], reverse=True)
    topp = kandidater[:maks_pos]

    # Utfør handler automatisk
    utforte       = []
    stop_loss_pct = pf.get("stop_loss_pct", DEFAULT_STOP_LOSS)
    topp_tickers  = {k["ticker"] for k in topp}

    # ── Stop-loss: selg posisjoner som har falt for mye fra kjøpspris ────────
    for ticker, pos in list(pf["posisjoner"].items()):
        kurs = hent_siste_kurs(ticker)
        if not kurs:
            continue
        tap_pct = (kurs / pos["snittpris"] - 1) * 100
        if tap_pct <= -(stop_loss_pct * 100):
            inntekt     = round(pos["antall"] * kurs, 0)
            begrunnelse = f"Stop-loss utløst ({tap_pct:.1f}% fra kjøpspris {pos['snittpris']:.2f} kr)"
            del pf["posisjoner"][ticker]
            pf["kasse"] += inntekt
            topp_tickers.discard(ticker)   # ikke kjøp igjen samme dag
            pf["historikk"].append({
                "dato": str(datetime.now()), "handling": "SELG",
                "ticker": ticker, "navn": pos["navn"],
                "antall": pos["antall"], "kurs": kurs, "beløp": inntekt,
                "begrunnelse": begrunnelse,
            })
            utforte.append({
                "handling": "SELG", "navn": pos["navn"], "ticker": ticker,
                "antall": pos["antall"], "kurs": kurs, "beløp": inntekt,
                "begrunnelse": begrunnelse,
            })
            print(f"  STOP-LOSS: {pos['antall']} × {pos['navn']} à {kurs:.2f} kr "
                  f"({tap_pct:.1f}%) = {inntekt:,.0f} kr")

    # ── Selg posisjoner som ikke lenger er blant topp-kandidater ─────────────
    for ticker, pos in list(pf["posisjoner"].items()):
        if ticker not in topp_tickers:
            kurs = hent_siste_kurs(ticker)
            if not kurs:
                continue
            inntekt     = round(pos["antall"] * kurs, 0)
            begrunnelse = "Ikke lenger blant topp-kandidater"
            del pf["posisjoner"][ticker]
            pf["kasse"] += inntekt
            pf["historikk"].append({
                "dato": str(datetime.now()), "handling": "SELG",
                "ticker": ticker, "navn": pos["navn"],
                "antall": pos["antall"], "kurs": kurs, "beløp": inntekt,
                "begrunnelse": begrunnelse,
            })
            utforte.append({
                "handling": "SELG", "navn": pos["navn"], "ticker": ticker,
                "antall": pos["antall"], "kurs": kurs, "beløp": inntekt,
                "begrunnelse": begrunnelse,
            })
            print(f"  SOLGT: {pos['antall']} × {pos['navn']} à {kurs:.2f} kr = {inntekt:,.0f} kr")

    # Kjøp topp-kandidater vi ikke allerede eier
    for k in topp:
        if k["ticker"] in pf["posisjoner"]:
            continue  # Allerede i portefølje
        beløp  = min(pf["kasse"] * allok, pf["kasse"] * MAKS_ALLOKERING)
        antall = int(beløp / k["kurs"])
        if antall < 1 or beløp > pf["kasse"]:
            continue
        kostnad     = round(antall * k["kurs"], 0)
        begrunnelse = (f"[{regime}] Ensemble {k['ensemble']}/3 ({k['ensemble_tekst']}) · "
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
        utforte.append({
            "handling": "KJØP", "navn": k["navn"], "ticker": k["ticker"],
            "antall": antall, "kurs": k["kurs"], "beløp": kostnad,
            "begrunnelse": begrunnelse,
        })
        print(f"  KJØPT: {antall} × {k['navn']} à {k['kurs']:.2f} kr = {kostnad:,.0f} kr")

    pf["ventende_handler"] = []
    pf["sist_analysert"]   = str(datetime.now())
    pf["regime"]           = regime
    lagre_portefolje(pf)

    kjop_antall = len([f for f in utforte if f["handling"] == "KJØP"])
    selg_antall = len([f for f in utforte if f["handling"] == "SELG"])
    print(f"Analyse ferdig: {kjop_antall} kjøp utført, {selg_antall} salg utført")

    return utforte

if __name__ == "__main__":
    kjor_analyse()
