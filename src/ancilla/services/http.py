"""
Shared requests.Session for the whole app.

Reusing one Session gives HTTP keep-alive and connection pooling, so repeated
calls to the same host (whisper-server, Open-Meteo, Nominatim, Overpass,
Navidrome) skip the TCP + TLS handshake after the first request.
"""
import requests

USER_AGENT = "ancilla/0.1 (local hobby project)"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
