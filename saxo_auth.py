"""
Saxo Bank OAuth 2.0 — engangs-autentisering for aa hente refresh token.

Kjor dette lokalt EN gang:
    py saxo_auth.py

Scriptet:
  1. Aapner nettleser -> logger inn med Saxo-konto
  2. Fanger opp authorization code via lokal HTTP-server
  3. Bytter code mot access_token + refresh_token
  4. Lagrer tokens til saxo_tokens.json

Deretter legger du innholdet i saxo_tokens.json som GitHub Secrets.
"""

import http.server
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser

import requests

CLIENT_ID     = "59c803cd418b4d04a1b7ebb69c4ee619"
CLIENT_SECRET = "3ad08c5bcacf401eb253868067cd51ce"
REDIRECT_URI  = "http://localhost:8080"
AUTH_URL      = "https://sim.logonvalidation.net/authorize"
TOKEN_URL     = "https://sim.logonvalidation.net/token"
BASE_URL      = "https://gateway.saxobank.com/sim/openapi"
TOKENS_FILE   = "saxo_tokens.json"

auth_code_received = threading.Event()
auth_code = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif;padding:40px">
                <h2>Autentisering vellykket!</h2>
                <p>Du kan lukke denne fanen og gaa tilbake til terminalen.</p>
                </body></html>
            """)
            auth_code_received.set()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Ingen code i callback")

    def log_message(self, format, *args):
        pass  # Skru av request-logging


def main():
    state = secrets.token_urlsafe(16)

    auth_params = {
        "response_type": "code",
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "state":         state,
    }
    auth_link = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    print("Aapner nettleser for innlogging...", flush=True)
    print(f"URL: {auth_link}\n", flush=True)
    webbrowser.open(auth_link)
    print("Venter paa callback fra Saxo... (du har 10 minutter)", flush=True)

    # Start lokal HTTP-server for aa fange callback
    server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
    server.timeout = 1  # poll hvert sekund

    deadline = 600  # 10 minutter
    elapsed = 0
    while not auth_code_received.is_set() and elapsed < deadline:
        server.handle_request()
        elapsed += 1

    server.server_close()

    if not auth_code:
        print("Feil: Ingen authorization code mottatt innen 10 minutter.", flush=True)
        return

    print(f"Authorization code mottatt: {auth_code[:20]}...", flush=True)

    # Bytt code mot tokens
    resp = requests.post(TOKEN_URL, data={
        "grant_type":   "authorization_code",
        "code":         auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id":    CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })

    if resp.status_code not in (200, 201):
        print(f"Token-bytte feilet: {resp.status_code} {resp.text}", flush=True)
        return

    tokens = resp.json()

    # Hent AccountKey
    account_key = ""
    r = requests.get(f"{BASE_URL}/port/v1/accounts/me",
                     headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if r.ok:
        accounts = r.json().get("Data", [])
        if accounts:
            account_key = accounts[0]["AccountKey"]

    output = {
        "access_token":  tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "account_key":   account_key,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }

    with open(TOKENS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nTokens lagret til {TOKENS_FILE}")
    print("\n--- GitHub Secrets ---")
    print(f"SAXO_CLIENT_ID      = {CLIENT_ID}")
    print(f"SAXO_CLIENT_SECRET  = {CLIENT_SECRET}")
    print(f"SAXO_REFRESH_TOKEN  = {tokens.get('refresh_token')}")
    print(f"SAXO_ACCOUNT_KEY    = {account_key}")
    print("\nLegg disse inn under: github.com/andersbvaage-ai/tradingbot -> Settings -> Secrets -> Actions")


if __name__ == "__main__":
    main()
