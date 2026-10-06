"""Spotify Authorization Code with PKCE helpers and local callback flow."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

API_DIR = Path(__file__).resolve().parents[1]
TOKEN_PATH = API_DIR / ".spotify_tokens.json"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "user-top-read user-read-recently-played"


def pkce_pair(verifier: str | None = None) -> tuple[str, str]:
    """Return a PKCE verifier and its unpadded S256 challenge."""
    code_verifier = verifier or secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, challenge


def validate_state(expected: str, received: str | None) -> None:
    """Reject a callback whose OAuth state does not match."""
    if received is None or not secrets.compare_digest(expected, received):
        raise ValueError("OAuth state mismatch")


def _credentials() -> tuple[str, str]:
    load_dotenv(API_DIR / ".env")
    import os

    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback").strip()
    if not client_id:
        raise RuntimeError("Set SPOTIFY_CLIENT_ID in services/api/.env")
    return client_id, redirect_uri


def _save_tokens(tokens: dict[str, Any]) -> None:
    TOKEN_PATH.write_text(json.dumps(tokens), encoding="utf-8")


def _read_tokens() -> dict[str, Any] | None:
    if not TOKEN_PATH.exists():
        return None
    return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))


def _exchange(code: str, verifier: str, client_id: str, redirect_uri: str) -> dict[str, Any]:
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    payload["expires_at"] = time.time() + payload.get("expires_in", 3600)
    _save_tokens(payload)
    return payload


def _refresh(tokens: dict[str, Any], client_id: str) -> dict[str, Any]:
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Saved Spotify authorization has no refresh token")
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    response.raise_for_status()
    refreshed = response.json()
    refreshed["refresh_token"] = refreshed.get("refresh_token", refresh_token)
    refreshed["expires_at"] = time.time() + refreshed.get("expires_in", 3600)
    _save_tokens(refreshed)
    return refreshed


def _authorize(client_id: str, redirect_uri: str) -> dict[str, Any]:
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.hostname != "127.0.0.1" or parsed.port != 8888:
        raise ValueError("Callback must use 127.0.0.1:8888")
    callback_path = parsed.path or "/callback"
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            """Capture the OAuth callback without writing credentials to output."""
            request = urllib.parse.urlparse(self.path)
            if request.path != callback_path:
                self.send_error(404)
                return
            values = urllib.parse.parse_qs(request.query)
            try:
                validate_state(state, values.get("state", [None])[0])
                code = values.get("code", [None])[0]
                if not code:
                    raise ValueError("Authorization code missing")
                result["code"] = code
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Spotify authorization received. You can close this tab.")
            except ValueError:
                result["error"] = "OAuth callback rejected"
                self.send_error(400, "Authorization callback rejected")

        def log_message(self, format: str, *args: object) -> None:
            """Suppress callback request logging."""

    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    server = HTTPServer(("127.0.0.1", 8888), CallbackHandler)
    try:
        webbrowser.open(f"{AUTH_URL}?{query}")
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.timeout = max(0.1, deadline - time.monotonic())
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise ValueError(result["error"])
    if "code" not in result:
        raise TimeoutError("Spotify authorization timed out")
    return _exchange(result["code"], verifier, client_id, redirect_uri)


def get_access_token() -> str:
    """Return a current access token, refreshing or authorizing as needed."""
    client_id, redirect_uri = _credentials()
    tokens = _read_tokens()
    if tokens is None:
        tokens = _authorize(client_id, redirect_uri)
    elif float(tokens.get("expires_at", 0)) <= time.time() + 30:
        tokens = _refresh(tokens, client_id)
    access_token = tokens.get("access_token")
    if not access_token:
        raise RuntimeError("Spotify authorization returned no access token")
    return str(access_token)
