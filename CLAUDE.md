# Nordic Trading Bot — CLAUDE.md

Prosjekt-instruksjoner for Claude Code. Les dette før du gjør noe.

## Hva er dette

En automatisk trading-bot for Oslo Børs, bygget i Python/Streamlit.
- **UI:** Streamlit Cloud (auto-deploy ved push til main)
- **Automatisering:** GitHub Actions kjører scheduler.py to ganger per børsdag
- **Data:** yfinance for kurser og historikk
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
| `nordnet_client.py` | Nordnet nExt API v2-klient. Klar, men ikke aktivert ennå. |
| `portfolio.json` | Porteføljestatus. Eneste persistent lagring. |
| `requirements.txt` | streamlit, yfinance, backtesting, pandas, plotly, requests, cryptography |
| `.github/workflows/daglig_analyse.yml` | To cron-jobs: 09:15 (full) og 13:30 (stop-loss) |

---

## Kjøringsplan (GitHub Actions)

| Tid (norsk) | Kommando | Hva |
|---|---|---|
| 09:15 man-fre | `python scheduler.py` | Full analyse: scan, kjøp, selg, trailing SL, snapshot |
| 13:30 man-fre | `python scheduler.py --only-stop-loss` | Kun trailing stop-loss-sjekk |

Workflow støtter også manuell kjøring med valg: `full`, `only-stop-loss`, `test-varsel`.

---

## Signallogikk (scheduler.py)

### Ensemble-signaler (3 uavhengige stemmer)
Kjøp krever minimum 2/3 (eller 3/3 i Bear-regime):
1. **Trend** — SMA10 > SMA50
2. **MACD** — MACD-linje > Signal-linje
3. **Momentum** — 6-månedersmom > 0%

### Regime-deteksjon (OSEBX vs SMA200)
| Regime | Kriterier | Maks pos | Allokering | Min ensemble |
|---|---|---|---|---|
| Bull | OSEBX > SMA200 og 3mnd > +3% | 6 | 15% | 2/3 |
| Sideways | Verken Bull eller Bear | 4 | 12% | 2/3 |
| Bear | OSEBX < SMA200 og 3mnd < -5% | 2 | 10% | 3/3 |

### Salglogikk (prioritert rekkefølge)
1. **Trailing stop-loss** — selg hvis kurs faller `stop_loss_pct`% fra `høyeste_kurs`
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
  "kurtasje_ratio_maks": 0.02
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
| `NORDNET_API_KEY` | Nordnet nExt API UUID | Ikke satt ennå |
| `NORDNET_PRIV_KEY` | Ed25519 privat nøkkel (PEM) | Ikke satt ennå |

---

## Nordnet-integrasjon (ikke aktivert)

Koden i `nordnet_client.py` er ferdig. Venter på Nordnet Trading Support-godkjenning.

**3 steg for å aktivere:**
1. E-post til `tradingsupport@nordnet.se` — be om nExt API v2-tilgang som norsk privatkunde
2. Generer nøkkelpar: `ssh-keygen -t ed25519 -a 150 -f nordnet_ed25519`, last opp `.pub` i Nordnet
3. Legg `NORDNET_API_KEY` (UUID) og `NORDNET_PRIV_KEY` (PEM-innhold) i GitHub Secrets

Auth: Ed25519 challenge-response, session-levetid 30 min — re-autentiserer hver kjøring (ingen token-problemer).

---

## Hva som IKKE skal gjøres

- Ikke endre cron-tidspunktene uten å huske UTC vs norsk tid (UTC+1 vinter, UTC+2 sommer)
- Ikke slett `høyeste_kurs` fra posisjoner — trailing stop-loss fungerer ikke uten
- Ikke fjern `git pull --rebase` fra workflow — vil skape push-konflikter
- Ikke committe `.env`-filer eller private nøkler til repoet
- Ikke endre `KURTASJE_MODELLER`-dict uten å oppdatere begge filer (scheduler + app)
- Ikke bruk `python`-kommando på Windows — bruk `py` lokalt (GitHub Actions bruker `python`)

---

## Neste mulige steg

- **Nordnet live-trading** — aktiveres med 3 steg over
- ~~**Fundamentale filtre**~~ — implementert i scheduler.py (P/E, P/B, yield-filter)
- **Posisjonsstørrelse basert på volatilitet** — Kelly-kriteriet eller vol-skalering
- ~~**Ukentlig rapport**~~ — implementert, kjører fredag 16:00 via ntfy (NTFY_TOPIC secret)
