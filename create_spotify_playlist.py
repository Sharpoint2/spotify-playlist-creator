#!/usr/bin/env python3
"""Create a Spotify playlist from a names-only chart export file.

SETUP:
1. Go to https://developer.spotify.com/dashboard and create an app.
2. In the app settings, add this Redirect URI exactly:
    http://127.0.0.1:8888/callback
3. Copy your Client ID and Client Secret.
4. Pass them via --client-id / --client-secret, or set env vars:
       SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET

USAGE:
    /usr/bin/python3 create_spotify_playlist.py \\
        --input super_friday_chart_all_entries_song_artist_only.txt \\
        --playlist-name "Super Friday Chart" \\
        --client-id YOUR_ID \\
        --client-secret YOUR_SECRET
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import socket
import sys
import time
from urllib.parse import urlparse, urlencode
import webbrowser
from pathlib import Path
from threading import Event, Thread

import requests

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-modify-public playlist-modify-private"

# Number of tracks per API add-to-playlist call (Spotify maximum is 100)
BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def _parse_redirect_uri(redirect_uri: str) -> tuple[str, int, str]:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or ""
    port = parsed.port or 80
    path = parsed.path or "/"

    if parsed.scheme != "http":
        raise ValueError("Redirect URI must use http.")
    if host not in {"127.0.0.1", "::1"}:
        raise ValueError("Redirect URI host must be 127.0.0.1 or ::1 (Spotify does not allow localhost).")

    return host, port, path


def _wait_for_auth_code(
    host: str,
    port: int,
    callback_path: str,
    timeout: int = 120,
) -> str:
    """Start a one-shot local HTTP server to capture the OAuth callback code."""
    code_holder: list[str] = []
    ready = Event()

    def handler(conn: socket.socket) -> None:
        data = conn.recv(4096).decode("utf-8", errors="replace")
        match = re.search(
            rf"GET {re.escape(callback_path)}\?.*?code=([^& ]+)",
            data,
        )
        if match:
            code_holder.append(match.group(1))
            response = (
                "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
                "<html><body><h2>Authorization complete — you can close this tab.</h2></body></html>"
            )
        else:
            response = "HTTP/1.1 400 Bad Request\r\n\r\nMissing code."
        conn.sendall(response.encode())
        conn.close()
        ready.set()

    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    server = socket.socket(family, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        server.bind((host, port, 0, 0))
    else:
        server.bind((host, port))
    server.listen(1)
    server.settimeout(timeout)

    def serve() -> None:
        try:
            conn, _ = server.accept()
            handler(conn)
        except OSError:
            pass
        finally:
            server.close()

    Thread(target=serve, daemon=True).start()
    ready.wait(timeout=timeout)

    if not code_holder:
        print("Timed out waiting for Spotify authorization.", file=sys.stderr)
        sys.exit(1)

    return code_holder[0]


def authorize(client_id: str, client_secret: str, redirect_uri: str) -> str:
    """Run the OAuth Authorization Code flow and return a valid access token."""
    host, port, callback_path = _parse_redirect_uri(redirect_uri)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
    }
    auth_url = f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"

    print(f"\nSpotify authorization URL:\n{auth_url}\n")
    print("Attempting to open your browser automatically...")

    opened = False
    try:
        opened = bool(webbrowser.open(auth_url, new=2))
    except Exception:
        opened = False

    if opened:
        print("Browser opened. Approve access, then return here.\n")
    else:
        print(
            "Could not auto-open a browser in this environment.\n"
            "Please copy the URL above into any browser, approve access,\n"
            "then return here so the app can capture the callback.\n"
        )

    code = _wait_for_auth_code(host=host, port=port, callback_path=callback_path)

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    response.raise_for_status()
    token_data = response.json()
    return token_data["access_token"]


# ---------------------------------------------------------------------------
# Spotify API helpers
# ---------------------------------------------------------------------------

def get_current_user_id(token: str) -> str:
    response = requests.get(
        f"{SPOTIFY_API_BASE}/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["id"]


def search_track(query: str, token: str) -> str | None:
    """Return the Spotify URI for the best match, or None if not found."""
    response = requests.get(
        f"{SPOTIFY_API_BASE}/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query, "type": "track", "limit": 1},
        timeout=15,
    )
    if response.status_code != 200:
        return None
    items = response.json().get("tracks", {}).get("items", [])
    return items[0]["uri"] if items else None


def create_playlist(user_id: str, name: str, description: str, token: str) -> str:
    """Create a new playlist and return its ID."""
    response = requests.post(
        f"{SPOTIFY_API_BASE}/users/{user_id}/playlists",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"name": name, "description": description, "public": True},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["id"]


def add_tracks_to_playlist(playlist_id: str, uris: list[str], token: str) -> None:
    """Add tracks to a playlist in batches of BATCH_SIZE."""
    for i in range(0, len(uris), BATCH_SIZE):
        batch = uris[i : i + BATCH_SIZE]
        response = requests.post(
            f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"uris": batch},
            timeout=30,
        )
        response.raise_for_status()
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------

def parse_names_only_file(path: Path) -> list[tuple[str, str]]:
    """Parse a names-only export file into (track, artists) pairs."""
    entries: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # Skip header / blank lines
        if not line or "|" not in line or line.startswith("Super Friday") or line.startswith("Total"):
            continue
        if line.count("|") != 1:
            continue
        parts = line.split("|", 1)
        if len(parts) != 2:
            continue
        track = parts[0].strip()
        artists = parts[1].strip()
        if track and artists:
            entries.append((track, artists))
    return entries


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def run(
    input_file: Path,
    playlist_name: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    delay: float,
) -> int:
    entries = parse_names_only_file(input_file)
    if not entries:
        print("No entries found in input file.", file=sys.stderr)
        return 1

    print(f"Loaded {len(entries)} entries from {input_file}")

    token = authorize(client_id, client_secret, redirect_uri)
    user_id = get_current_user_id(token)
    print(f"Authenticated as Spotify user: {user_id}")

    uris: list[str] = []
    not_found: list[str] = []

    for index, (track, artists) in enumerate(entries, start=1):
        query = f"track:{track} artist:{artists.split(',')[0].strip()}"
        uri = search_track(query, token)
        if uri:
            uris.append(uri)
        else:
            not_found.append(f"{track} — {artists}")

        if index % 50 == 0:
            print(f"  Searched {index}/{len(entries)} tracks...")

        if delay > 0:
            time.sleep(delay)

    print(f"\nFound {len(uris)} tracks on Spotify. {len(not_found)} not found.")

    if not uris:
        print("No tracks could be matched. Exiting without creating a playlist.", file=sys.stderr)
        return 1

    description = f"Exported from superfridaychart.com — {len(uris)} tracks"
    playlist_id = create_playlist(user_id, playlist_name, description, token)
    print(f"Created playlist: {playlist_name} (ID: {playlist_id})")

    add_tracks_to_playlist(playlist_id, uris, token)
    print(f"Added {len(uris)} tracks to the playlist.")

    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
    print(f"\nPlaylist ready: {playlist_url}")

    if not_found:
        not_found_path = input_file.with_name(f"{input_file.stem}_not_found_on_spotify.txt")
        not_found_path.write_text("\n".join(not_found) + "\n", encoding="utf-8")
        print(f"Tracks not found on Spotify written to: {not_found_path}")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Spotify playlist from a Super Friday Chart names-only export."
    )
    parser.add_argument(
        "--input",
        default="super_friday_chart_all_entries_song_artist_only.txt",
        help="Names-only export file (default: super_friday_chart_all_entries_song_artist_only.txt)",
    )
    parser.add_argument(
        "--playlist-name",
        default="Super Friday Chart",
        help="Spotify playlist name to create (default: Super Friday Chart)",
    )
    parser.add_argument(
        "--client-id",
        default=os.environ.get("SPOTIFY_CLIENT_ID", ""),
        help="Spotify app Client ID (or set SPOTIFY_CLIENT_ID env var)",
    )
    parser.add_argument(
        "--client-secret",
        default=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
        help="Spotify app Client Secret (or set SPOTIFY_CLIENT_SECRET env var)",
    )
    parser.add_argument(
        "--redirect-uri",
        default=os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        help=(
            "Spotify app Redirect URI (must exactly match your app settings). "
            f"Default: {DEFAULT_REDIRECT_URI}"
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Delay between search requests in seconds (default: 0.05)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.client_id or not args.client_secret:
        print(
            "Error: --client-id and --client-secret are required.\n"
            "Create a Spotify app at https://developer.spotify.com/dashboard\n"
            "and add your exact redirect URI in app settings.\n"
            "Use --redirect-uri to match that value.",
            file=sys.stderr,
        )
        return 1

    input_file = Path(args.input).expanduser().resolve()
    if not input_file.exists():
        print(f"Error: input file not found: {input_file}", file=sys.stderr)
        return 1

    try:
        return run(
            input_file=input_file,
            playlist_name=args.playlist_name,
            client_id=args.client_id,
            client_secret=args.client_secret,
            redirect_uri=args.redirect_uri,
            delay=args.delay,
        )
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"Redirect URI error: {exc}", file=sys.stderr)
        return 1
    except requests.HTTPError as exc:
        print(f"Spotify API error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
