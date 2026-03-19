"""
Saxo Bank OpenAPI — Python-klient for automatisk trading.

Autentisering via OAuth 2.0 med refresh token.
Access token utloper etter ~20 min, refresh token byttes ut ved hver kjoring.

Oppsett (en gang):
  1. Kjor: py saxo_auth.py
     Dette apner nettleser, logger inn og lagrer tokens til saxo_tokens.json
  2. Legg inn i GitHub Actions secrets:
       SAXO_CLIENT_ID      = App Key fra developer.saxobank.com
       SAXO_CLIENT_SECRET  = App Secret
       SAXO_REFRESH_TOKEN  = refresh_token fra saxo_tokens.json
       SAXO_ACCOUNT_KEY    = AccountKey fra saxo_tokens.json
  3. GitHub Action oppdaterer SAXO_REFRESH_TOKEN automatisk etter hver kjoring

Bruk:
  client = SaxoClient()
  if client.logg_inn():
      kasse = client.hent_kasse()
      posisjoner = client.hent_posisjoner()
      uic = client.finn_uic('EQNR')
      client.kjop(uic, antall=10)
      client.selg(uic, antall=10)
"""

import os
import requests

SIM_BASE   = "https://gateway.saxobank.com/sim/openapi"
LIVE_BASE  = "https://gateway.saxobank.com/openapi"
TOKEN_URL  = "https://sim.logonvalidation.net/token"


class SaxoClient:
    """
    Klient for Saxo Bank OpenAPI.
    Henter autentiseringsnokler fra miljovariabler:
        SAXO_ACCESS_TOKEN   — direkte token (24h-token for testing)
        SAXO_CLIENT_ID      — App Key
        SAXO_CLIENT_SECRET  — App Secret
        SAXO_REFRESH_TOKEN  — OAuth refresh token
        SAXO_ACCOUNT_KEY    — AccountKey (hentes automatisk hvis ikke satt)
        SAXO_LIVE           — sett til "1" for live-modus (standard: SIM)
    """

    def __init__(self):
        self.live         = os.environ.get("SAXO_LIVE", "0") == "1"
        self.base         = LIVE_BASE if self.live else SIM_BASE
        self.client_id    = os.environ.get("SAXO_CLIENT_ID", "")
        self.client_secret= os.environ.get("SAXO_CLIENT_SECRET", "")
        self.refresh_token= os.environ.get("SAXO_REFRESH_TOKEN", "")
        self.access_token = os.environ.get("SAXO_ACCESS_TOKEN", "")
        self.account_key  = os.environ.get("SAXO_ACCOUNT_KEY", "")
        self.client_key   = ""
        self.session      = requests.Session()

    # ── Autentisering ─────────────────────────────────────────────────────────

    def logg_inn(self) -> bool:
        """Sett access token fra env eller bytt ut refresh token."""
        if self.access_token:
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"
            print("SAXO: Bruker access token fra miljovariabler")
        elif self.refresh_token and self.client_id and self.client_secret:
            if not self._bytt_refresh_token():
                return False
        else:
            print("SAXO: Mangler SAXO_ACCESS_TOKEN eller SAXO_REFRESH_TOKEN+SAXO_CLIENT_ID+SAXO_CLIENT_SECRET")
            return False

        # Hent bruker- og kontoinfo
        return self._hent_brukerinfo()

    def _bytt_refresh_token(self) -> bool:
        """Bytt refresh token mot nytt access token + refresh token."""
        resp = requests.post(TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
        })
        if resp.status_code not in (200, 201):
            print(f"SAXO: Token-bytte feilet: {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        self.access_token  = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        self.session.headers["Authorization"] = f"Bearer {self.access_token}"
        print("SAXO: Ny access token hentet via refresh token")
        print(f"SAXO: Nytt refresh token: {self.refresh_token}")
        return True

    def _hent_brukerinfo(self) -> bool:
        r = self.session.get(f"{self.base}/port/v1/users/me")
        if r.status_code != 200:
            print(f"SAXO: Innlogging feilet: {r.status_code} {r.text}")
            return False
        self.client_key = r.json().get("ClientKey", "")

        if not self.account_key:
            r2 = self.session.get(f"{self.base}/port/v1/accounts/me")
            if r2.ok:
                accounts = r2.json().get("Data", [])
                if accounts:
                    self.account_key = accounts[0]["AccountKey"]

        print(f"SAXO: Innlogget | ClientKey={self.client_key} | AccountKey={self.account_key}")
        return True

    # ── Konto og saldo ────────────────────────────────────────────────────────

    def hent_kasse(self) -> float:
        """Hent tilgjengelig kontantbeholdning."""
        r = self.session.get(f"{self.base}/port/v1/balances",
                             params={"ClientKey": self.client_key,
                                     "AccountKey": self.account_key})
        if not r.ok:
            return 0.0
        data = r.json()
        return float(data.get("CashAvailableForTrading", 0))

    def hent_posisjoner(self) -> list[dict]:
        """Hent alle apne posisjoner."""
        r = self.session.get(f"{self.base}/port/v1/positions",
                             params={"ClientKey": self.client_key,
                                     "AccountKey": self.account_key})
        if not r.ok:
            return []
        return r.json().get("Data", [])

    def hent_ordrer(self) -> list[dict]:
        """Hent apne ordrer."""
        r = self.session.get(f"{self.base}/trade/v2/orders",
                             params={"AccountKey": self.account_key})
        if not r.ok:
            return []
        return r.json().get("Data", [])

    # ── Instrumentoppslag ─────────────────────────────────────────────────────

    def finn_uic(self, symbol: str, exchange: str = "OSE") -> int | None:
        """
        Finn Saxo UIC (Unique Instrument Code) for et Oslo Bors-symbol.
        symbol   — e.g. "EQNR" (uten .OL-suffiks)
        exchange — "OSE" for Oslo Bors
        """
        r = self.session.get(f"{self.base}/ref/v1/instruments", params={
            "Keywords":   symbol,
            "ExchangeId": exchange,
            "AssetTypes": "Stock",
            "$top":       5,
        })
        if not r.ok:
            return None
        hits = r.json().get("Data", [])
        # Finn eksakt symboltreff
        for hit in hits:
            if hit.get("Symbol", "").upper() == symbol.upper():
                return hit["Identifier"]
        # Fallback: forste treff
        return hits[0]["Identifier"] if hits else None

    # ── Ordrehandling ─────────────────────────────────────────────────────────

    def kjop(self, uic: int, antall: int,
             pris: float | None = None, valuta: str = "NOK") -> dict:
        """
        Legg inn kjopsordre.
        pris=None -> markedsordre
        pris=X    -> limitordre
        """
        ordre = {
            "AccountKey":  self.account_key,
            "AssetType":   "Stock",
            "Uic":         uic,
            "BuySell":     "Buy",
            "Amount":      antall,
            "OrderType":   "Limit" if pris else "Market",
            "ManualOrder": False,
        }
        if pris:
            ordre["Price"] = round(pris, 4)
        if not pris:
            ordre["OrderDuration"] = {"DurationType": "DayOrder"}
        else:
            ordre["OrderDuration"] = {"DurationType": "GoodTillCancel"}

        r = self.session.post(f"{self.base}/trade/v2/orders", json=ordre)
        if not r.ok:
            raise RuntimeError(f"Kjop feilet: {r.status_code} {r.text}")
        result = r.json()
        print(f"SAXO: Kjopsordre lagt inn — UIC {uic}, antall {antall}, OrderId={result.get('OrderId')}")
        return result

    def selg(self, uic: int, antall: int,
             pris: float | None = None, valuta: str = "NOK") -> dict:
        """
        Legg inn salgsordre.
        pris=None -> markedsordre
        pris=X    -> limitordre
        """
        ordre = {
            "AccountKey":  self.account_key,
            "AssetType":   "Stock",
            "Uic":         uic,
            "BuySell":     "Sell",
            "Amount":      antall,
            "OrderType":   "Limit" if pris else "Market",
            "ManualOrder": False,
        }
        if pris:
            ordre["Price"] = round(pris, 4)
        ordre["OrderDuration"] = {"DurationType": "DayOrder"}

        r = self.session.post(f"{self.base}/trade/v2/orders", json=ordre)
        if not r.ok:
            raise RuntimeError(f"Salg feilet: {r.status_code} {r.text}")
        result = r.json()
        print(f"SAXO: Salgsordre lagt inn — UIC {uic}, antall {antall}, OrderId={result.get('OrderId')}")
        return result

    def kanseller_ordre(self, order_id: str) -> bool:
        r = self.session.delete(f"{self.base}/trade/v2/orders/{order_id}",
                                params={"AccountKey": self.account_key})
        return r.status_code == 200

    # ── Kontekst-manager ──────────────────────────────────────────────────────

    def __enter__(self):
        self.logg_inn()
        return self

    def __exit__(self, *_):
        pass
