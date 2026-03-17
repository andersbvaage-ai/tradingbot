"""
Nordnet nExt API v2 — Python-klient for automatisk trading.

Autentisering via Ed25519 SSH-nøkkel challenge-response.
Ingen OAuth, ingen token som utløper mellom kjøringer.

Oppsett (én gang):
  1. Generer nøkkelpar:   ssh-keygen -t ed25519 -a 150 -f nordnet_ed25519
  2. Last opp nordnet_ed25519.pub i Nordnet: Mine sider > Innstillinger > Sikkerhet > API-nøkkel
  3. Nordnet gir deg en UUID (API_KEY_UUID) — lagre denne
  4. Legg inn i GitHub Actions secrets:
       NORDNET_API_KEY   = UUID-en fra Nordnet
       NORDNET_PRIV_KEY  = innholdet i nordnet_ed25519 (hele PEM-blokken)

Bruk:
  client = NordnetClient()
  if client.logg_inn():
      kontoer   = client.hent_kontoer()
      kasse     = client.hent_kasse(kontoer[0])
      saldo     = client.hent_posisjoner(kontoer[0])
      client.kjøp(kontoer[0], instrument_id=17917, antall=10)
      client.selg(kontoer[0], instrument_id=17917, antall=10)
      client.logg_ut()
"""

import os
import base64
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key, Encoding, PrivateFormat, NoEncryption
)

BASE_URL = "https://www.nordnet.no/api/2"
HEADERS  = {
    "Accept":       "application/json",
    "Content-Type": "application/json",
}


class NordnetClient:
    """
    Enkel klient for Nordnet nExt API v2.
    Henter autentiseringsnøkler fra miljøvariabler:
        NORDNET_API_KEY  — UUID fra Nordnet
        NORDNET_PRIV_KEY — Ed25519 privat nøkkel (PEM-format)
    """

    def __init__(self):
        self.api_key   = os.environ.get("NORDNET_API_KEY", "")
        self._priv_pem = os.environ.get("NORDNET_PRIV_KEY", "")
        self.session   = requests.Session()
        self.session.headers.update(HEADERS)
        self._session_key: str | None = None

    # ── Autentisering ──────────────────────────────────────────────────────────

    def logg_inn(self) -> bool:
        """Utfør Ed25519 challenge-response og sett session key."""
        if not self.api_key or not self._priv_pem:
            print("NORDNET: Mangler NORDNET_API_KEY eller NORDNET_PRIV_KEY")
            return False

        # Steg 1: hent challenge
        resp = self.session.post(f"{BASE_URL}/login/start",
                                 json={"public_key": self.api_key})
        if resp.status_code != 200:
            print(f"NORDNET: login/start feilet: {resp.status_code} {resp.text}")
            return False
        challenge = resp.json().get("challenge")
        if not challenge:
            print("NORDNET: Ingen challenge i svar")
            return False

        # Steg 2: signer challenge med Ed25519 privat nøkkel
        try:
            privat_nøkkel = load_pem_private_key(
                self._priv_pem.encode(), password=None
            )
            signatur = privat_nøkkel.sign(base64.b64decode(challenge))
            signatur_b64 = base64.b64encode(signatur).decode()
        except Exception as e:
            print(f"NORDNET: Signering feilet: {e}")
            return False

        # Steg 3: verifiser og hent session key
        resp2 = self.session.post(f"{BASE_URL}/login/verify",
                                  json={"public_key":  self.api_key,
                                        "signed_text": signatur_b64})
        if resp2.status_code != 200:
            print(f"NORDNET: login/verify feilet: {resp2.status_code} {resp2.text}")
            return False

        self._session_key = resp2.json().get("session_key")
        if not self._session_key:
            print("NORDNET: Ingen session_key i svar")
            return False

        # Session key brukes som Basic Auth (brukernavn = passord = session_key)
        self.session.auth = (self._session_key, self._session_key)
        print("NORDNET: Innlogget")
        return True

    def logg_ut(self):
        if self._session_key:
            self.session.delete(f"{BASE_URL}/login")
            self._session_key = None

    # ── Konto og saldo ─────────────────────────────────────────────────────────

    def hent_kontoer(self) -> list[dict]:
        """Returner liste over kontoer. Bruk accno fra første konto."""
        resp = self.session.get(f"{BASE_URL}/accounts")
        resp.raise_for_status()
        return resp.json()

    def hent_kasse(self, konto: dict) -> float:
        """Hent tilgjengelig kontantbeholdning (NOK) for konto."""
        accno = konto.get("accno") or konto.get("accid")
        resp  = self.session.get(f"{BASE_URL}/accounts/{accno}/ledgers")
        resp.raise_for_status()
        ledgers = resp.json()
        for ledger in ledgers:
            if ledger.get("currency") in ("NOK", "SEK"):
                return float(ledger.get("available_amount", 0))
        return 0.0

    def hent_posisjoner(self, konto: dict) -> list[dict]:
        """Hent alle åpne posisjoner for konto."""
        accno = konto.get("accno") or konto.get("accid")
        resp  = self.session.get(f"{BASE_URL}/accounts/{accno}/positions")
        resp.raise_for_status()
        return resp.json()

    def hent_ordrer(self, konto: dict) -> list[dict]:
        """Hent åpne ordrer."""
        accno = konto.get("accno") or konto.get("accid")
        resp  = self.session.get(f"{BASE_URL}/accounts/{accno}/orders")
        resp.raise_for_status()
        return resp.json()

    # ── Instrumentoppslag ──────────────────────────────────────────────────────

    def finn_instrument_id(self, symbol: str, market_id: str = "XOSL") -> int | None:
        """
        Finn Nordnets instrument_id for et Oslo Børs-symbol.
        symbol    — e.g. "EQNR" (uten .OL-suffiks)
        market_id — "XOSL" for Oslo Børs
        """
        resp = self.session.get(
            f"{BASE_URL}/instruments/lookup/market_id_identifier/{market_id}_{symbol}"
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0].get("instrument_id")
            if isinstance(data, dict):
                return data.get("instrument_id")
        # Fallback: søk
        resp2 = self.session.get(
            f"{BASE_URL}/instruments",
            params={"query": symbol, "market_id": market_id, "limit": 5}
        )
        if resp2.status_code == 200:
            hits = resp2.json()
            if hits:
                return hits[0].get("instrument_id")
        return None

    # ── Ordrehandling ──────────────────────────────────────────────────────────

    def kjøp(self, konto: dict, instrument_id: int, antall: int,
              pris: float | None = None, valuta: str = "NOK") -> dict:
        """
        Legg inn kjøpsordre.
        pris=None → markedsordre (order_type=MARKET)
        pris=X    → limitordre (order_type=LIMIT)
        """
        accno = konto.get("accno") or konto.get("accid")
        ordre = {
            "accno":         accno,
            "instrument_id": instrument_id,
            "identifier":    str(instrument_id),
            "market_id":     "XOSL",
            "currency":      valuta,
            "side":          "BUY",
            "volume":        antall,
            "order_type":    "LIMIT" if pris else "MARKET",
        }
        if pris:
            ordre["price"] = round(pris, 2)
        resp = self.session.post(f"{BASE_URL}/accounts/{accno}/orders", json=ordre)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Kjøp feilet: {resp.status_code} {resp.text}")
        print(f"NORDNET: Kjøpsordre lagt inn — instrument {instrument_id}, antall {antall}")
        return resp.json()

    def selg(self, konto: dict, instrument_id: int, antall: int,
             pris: float | None = None, valuta: str = "NOK") -> dict:
        """
        Legg inn salgsordre.
        pris=None → markedsordre
        pris=X    → limitordre
        """
        accno = konto.get("accno") or konto.get("accid")
        ordre = {
            "accno":         accno,
            "instrument_id": instrument_id,
            "identifier":    str(instrument_id),
            "market_id":     "XOSL",
            "currency":      valuta,
            "side":          "SELL",
            "volume":        antall,
            "order_type":    "LIMIT" if pris else "MARKET",
        }
        if pris:
            ordre["price"] = round(pris, 2)
        resp = self.session.post(f"{BASE_URL}/accounts/{accno}/orders", json=ordre)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Salg feilet: {resp.status_code} {resp.text}")
        print(f"NORDNET: Salgsordre lagt inn — instrument {instrument_id}, antall {antall}")
        return resp.json()

    def kanseller_ordre(self, konto: dict, ordre_id: int) -> bool:
        accno = konto.get("accno") or konto.get("accid")
        resp  = self.session.delete(f"{BASE_URL}/accounts/{accno}/orders/{ordre_id}")
        return resp.status_code == 200

    # ── Kontekst-manager ───────────────────────────────────────────────────────

    def __enter__(self):
        self.logg_inn()
        return self

    def __exit__(self, *_):
        self.logg_ut()
