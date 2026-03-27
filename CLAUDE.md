# Nordic Trading Bot — CLAUDE.md

Prosjekt-instruksjoner for Claude Code. Les dette før du gjør noe.

## Session-oppsett

Ved oppstart av hver session: opprett automatisk følgende cron:
- **Portfolio-check 16:47 hverdager** — `CronCreate` med cron `47 16 * * 1-5`, prompt: "Kjør /portfolio-check — evaluer nåværende portefølje mot signallogikken og gi en kort statusoppdatering.", recurring: true

---

## Hva er dette

En automatisk trading-bot for Oslo Børs, bygget i Python/Streamlit.
- **UI:** Streamlit Cloud (auto-deploy ved push til main)
- **Automatisering:** GitHub Actions kjører scheduler.py to ganger per børsdag
- **Data:** yfinance for kurser og historikk
- **Indikatorer:** `ta`-biblioteket (SMA, RSI, MACD, Bollinger Bands) — ikke manuell beregning
- **Persistens:** portfolio.json committes til GitHub av Actions etter hver kjøring
- **Status:** Paper money — 10 000 kr, startet 2026-03-18

**GitHub repo:** `andersbvaage-ai/tradingbot`
**Lokal mappe:** `C:\Users\ander\Dropbox\Business\Claude\Tradingbot\`

---

## Filstruktur

| Fil | Rolle |
|---|---|
| `app.py` | Streamlit-app, ~2000 linjer. All UI-logikk. |
| `scheduler.py` | Kjøres av GitHub Actions. Analyse, kjøp/salg, stop-loss. |
| `portfolio.json` | Porteføljestatus. Eneste persistent lagring. |
| `requirements.txt` | streamlit, yfinance, backtesting, pandas, plotly, requests, cryptography |
| `.github/workflows/daglig_analyse.yml` | Loop-basert: full analyse + stop-loss hvert 30. min |

---

## Kjøringsplan (GitHub Actions)

Workflowen bruker en **loop-modell** — én lang jobb per dag som itererer gjennom børsdagen:

| Cron (UTC) | Hva |
|---|---|
| `0 5 * * 1-5` | Morgen-loop: venter til 09:00 Oslo, kjører full analyse, deretter stop-loss hvert 30 min til 16:30 |
| `0 11 * * 1-5` | Ettermiddag-loop: samme logikk, fanger opp hvis morgen-cron ikke trigget |
| `0 14/15 * * 5` | Ukentlig rapport fredag 16:00 (vinter/sommertid) |

Maks kjøretid per loop: 5t 20min (under GitHub Actions' 6t-grense).

Workflow støtter også manuell kjøring med valg: `full`, `only-stop-loss`, `ukentlig-rapport`, `test-varsel`.

### Observerbarhet
- `scheduler.py` bruker Python `logging` (ikke print) — timestamps + nivå (INFO/WARNING/ERROR)
- Startup-validering sjekker yfinance og portfolio.json før analyse
- 10 min total timeout, 30s per ticker — forhindrer hengende kjøringer
- ntfy.sh-varsling har 3x retry med backoff

---

## Signallogikk (scheduler.py)

### Ensemble-signaler (3 uavhengige stemmer)
Kjøp krever minimum 2/3 (eller 3/3 i Bear-regime):
1. **Trend** — SMA10 > SMA50
2. **MACD** — MACD-linje > Signal-linje
3. **Momentum** — 6-månedersmom > 0%

**RSI-filter:** 30 < RSI < 70 — overbought/oversold ekskluderes alltid.
**Momentum-cap:** >200% 6mnd-momentum filtreres bort (parabolske aksjer).
**Momentum-reduksjon:** >100% momentum → halv posisjonsstørrelse.

### Unike signaler (score-boost i rangering)
Rangering: `(ensemble, score + oppside_score + råvare_score + insider_score + short_score)`

| Signal | Kilde | Boost |
|---|---|---|
| Råvare-overlay | Brent `BZ=F` (Energi), `BDRY` (Shipping) | ±0.5 |
| Innsiderkjøp | Oslo Børs OAM API, siste 14 hverdager | +0.75 |
| Short interest | Finanstilsynet SSR, short≥2% / ≥5% | -0.4 / -0.75 |

### Regime-deteksjon (OSEBX vs SMA200)
| Regime | Kriterier | Maks pos | Allokering | Min ensemble |
|---|---|---|---|---|
| Bull | OSEBX > SMA200 og 3mnd > +3% | 6 | 15% | 2/3 |
| Sideways | Verken Bull eller Bear | 4 | 12% | 2/3 |
| Bear | OSEBX < SMA200 og 3mnd < -5% | 2 | 10% | 3/3 |

### Salglogikk (prioritert rekkefølge)
1. **Trailing stop-loss** — per-posisjon volatilitetsjustert: `max(5%, min(10%, vol_60d × 0.5))`. Fallback til portefølje-nivå `stop_loss_pct` (7%) for eldre posisjoner.
2. **Ensemble=0** — selg hvis alle 3 signaler snur negative
3. **Ikke i topp** — selg hvis aksjen ikke lenger er blant topp-kandidatene

### Sektorspredning
Maks 2 posisjoner per sektor (SEKTORER-dict i begge filer).

### Kurtasje
Nordnet-modeller lagret i `KURTASJE_MODELLER`:
- **Mini:** 0,15% · min 29 kr (standard, best for handler < 52 667 kr)
- **Normal:** 0,049% · min 79 kr (best for handler > 52 667 kr)

Kurtasje-ratio-sjekk: hopper over kjøp der kurtasje > `kurtasje_ratio_maks` (standard 2%) av posisjonen. Skalerer opp til minimumsposisjon automatisk.

---

## portfolio.json — viktige felter

```json
{
  "kasse": 6054.0,
  "start_kapital": 10000,
  "posisjoner": {
    "TICKER.OL": {
      "navn": "...", "antall": 15, "snittpris": 257.8,
      "kjøpsdato": "2026-03-18", "høyeste_kurs": 257.8
    }
  },
  "historikk": [...],
  "verdi_historikk": [{"dato": "2026-03-18", "total_verdi": 9921.0}],
  "topp_kandidater": [...],
  "regime": "Sideways",
  "sist_analysert": "2026-03-18 09:32:51",
  "stop_loss_pct": 0.07,
  "kurtasje_modell": "Mini",
  "kurtasje_ratio_maks": 0.02,
  "råvare_trender": {"Energi": 1, "Shipping": -1},
  "insider_kjøp": ["BWLPG.OL"],
  "short_interest": {"LINK.OL": 11.4, "HEX.OL": 7.3}
}
```

`høyeste_kurs` oppdateres automatisk ved hver kjøring — aldri sett denne lavere enn den er.

---

## app.py — tab-struktur

| Tab | Innhold |
|---|---|
| Dashboard | Regime, kandidater, metrics, graf vs OSEBX, risiko, sektorer, handelslogg |
| Backtest | Enkeltaksje, velg strategi og periode |
| Sammenlign aksjer | Flere aksjer side om side |
| Optimalisering | Parameter-optimalisering med heatmap |
| Portefølje | Screener over alle aksjer × alle strategier |
| Walk-Forward | Robusthetstesting |
| Oslo Børs Screener | Live-scanner med kjøpssignaler |
| Porteføljestyrer | Manuell kontroll, innstillinger, nullstill |
| Screener-backtest | Månedlig rebalansering vs OSEBX |
| Info | Dokumentasjon |

### Cache-strategi
- `hent_siste_kurs()` — TTL 15 min (intradag under markedstid, sluttkurs ellers)
- `hent_aksje_historikk()` — TTL 1 time
- `hent_data()` — TTL 1 time

---

## Git-workflow

**Alltid** `git pull --rebase origin main` før push — GitHub Actions committer portfolio.json
og kan skape konflikter. Standard sekvens:

```bash
git add <filer>
git commit -m "beskrivelse"
git pull --rebase origin main
git push origin main
```

Push aldri direkte uten rebase-sjekk. Actions-konflikter løses alltid med rebase, ikke merge.

---

## GitHub Secrets (Actions)

| Secret | Formål | Status |
|---|---|---|
| `NTFY_TOPIC` | Push-varsler via ntfy.sh | Aktivert |
| `SAXO_CLIENT_ID` | Saxo Bank API klient-ID | Satt |
| `SAXO_CLIENT_SECRET` | Saxo Bank API klient-secret | Satt |
| `SAXO_REFRESH_TOKEN` | Saxo OAuth refresh token | Satt |
| `SAXO_ACCOUNT_KEY` | Saxo kontonøkkel | Satt |

---

## Hva som IKKE skal gjøres

- Ikke endre cron-tidspunktene uten å huske UTC vs norsk tid (UTC+1 vinter, UTC+2 sommer)
- Ikke slett `høyeste_kurs` fra posisjoner — trailing stop-loss fungerer ikke uten
- Ikke fjern `git pull --rebase` fra workflow — vil skape push-konflikter
- Ikke committe `.env`-filer eller private nøkler til repoet
- Ikke endre `KURTASJE_MODELLER`-dict uten å oppdatere begge filer (scheduler + app)
- Ikke bruk `python`-kommando på Windows — bruk `py` lokalt (GitHub Actions bruker `python`)

---

## Klar for produksjon

Før push til main — gå gjennom denne listen:

- [ ] Tester passerer (`py -m pytest`)
- [ ] `scheduler.py` kjørt manuelt med `--dry-run` hvis signallogikk er endret
- [ ] Ingen endringer i `portfolio.json`-struktur uten migrering
- [ ] `git pull --rebase origin main` kjørt før push

---

## Neste mulige steg

- **Saxo live-trading** — Saxo secrets er satt, gjenstår å bytte til live-endepunkter og sette SAXO_LIVE=1
- ~~**Fundamentale filtre**~~ — implementert (P/E, P/B, yield-filter)
- ~~**Posisjonsstørrelse basert på volatilitet**~~ — implementert (vol-basert + ensemble-boost)
- ~~**Ukentlig rapport**~~ — implementert, kjører fredag 16:00 via ntfy
- ~~**Råvare-overlay**~~ — implementert (Brent + Dry Bulk BDRY)
- ~~**Innsidekjøp-signal**~~ — implementert (Oslo Børs OAM API)
- ~~**Short interest-signal**~~ — implementert (Finanstilsynet SSR)
- **Laksepris-signal** — Fish Pool-data for Sjømat-sektoren (AKBM, AUSS, NRS)
- **Kelly-posisjonsstørrelse** — når ~20 round-trips i historikk
- **Evaluere signalkvalitet** — etter noen ukers live-kjøring
