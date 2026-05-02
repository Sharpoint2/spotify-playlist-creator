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
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import requests

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-modify-public playlist-modify-private"

# Number of tracks per API add-to-playlist call (Spotify maximum is 100)
BATCH_SIZE = 100

# Rate-limit / retry constants
MAX_RETRIES = 5
BASE_BACKOFF = 1.0  # seconds
MAX_BACKOFF = 60.0  # seconds


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _default_cache_dir() -> Path:
    """Return a cross-platform default cache directory."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "spotify_playlist_creator"
    elif sys.platform == "darwin":
        return Path.home() / "Library/Caches/spotify_playlist_creator"

    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "spotify_playlist_creator"
    return Path.home() / ".cache/spotify_playlist_creator"


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

    def _normalize_path(path: str) -> str:
        normalized = path.rstrip("/")
        return normalized if normalized else "/"

    expected_path = _normalize_path(callback_path)

    def handler(conn: socket.socket) -> None:
        data = conn.recv(4096).decode("utf-8", errors="replace")
        request_line = data.splitlines()[0] if data else ""
        target_match = re.match(r"^GET\s+(\S+)\s+HTTP/", request_line)

        code = ""
        if target_match:
            target = target_match.group(1)
            parsed_target = urlparse(target)
            request_path = parsed_target.path if parsed_target.scheme else target.split("?", 1)[0]
            request_query = parsed_target.query if parsed_target.scheme else (target.split("?", 1)[1] if "?" in target else "")

            if _normalize_path(request_path) == expected_path:
                code_values = parse_qs(request_query).get("code", [])
                if code_values:
                    code = code_values[0]

        if code:
            code_holder.append(code)
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
    try:
        if family == socket.AF_INET6:
            server.bind((host, port, 0, 0))
        else:
            server.bind((host, port))
    except OSError as exc:
        server.close()
        if exc.errno == 98:
            raise RuntimeError(
                f"Port {port} is already in use for redirect URI {host}:{port}. "
                "Close the app/process using this port, or change the redirect URI port "
                "in Spotify app settings and this tool."
            ) from exc
        raise
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


def _open_browser(url: str) -> str | None:
    """Attempt to open *url* in a browser; return the launcher name or None."""
    is_wsl = bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in os.uname().release.lower()

    launch_commands: list[tuple[str, list[str]]] = []
    if is_wsl:
        launch_commands.extend(
            [
                ("wslview", ["wslview", url]),
                (
                    "powershell.exe",
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-Command",
                        f'Start-Process "{url}"',
                    ],
                ),
            ]
        )

    launch_commands.extend(
        [
            ("xdg-open", ["xdg-open", url]),
            ("gio", ["gio", "open", url]),
            ("sensible-browser", ["sensible-browser", url]),
        ]
    )

    for launcher_name, command in launch_commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                return launcher_name
        except (FileNotFoundError, subprocess.SubprocessError):
            continue

    try:
        if webbrowser.open(url, new=2):
            return "webbrowser"
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# SpotifyClient
# ---------------------------------------------------------------------------

class SpotifyClient:
    """Authenticated Spotify API client with caching, refresh tokens, and rate-limit handling."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        cache_dir: Path | None = None,
        no_cache: bool = False,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.no_cache = no_cache
        self.cache_dir = cache_dir or _default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.cache_dir / "cache.json"
        self.token_path = self.cache_dir / "token.json"
        self._cache: dict[str, Any] = {}
        self._token: str | None = None
        if not no_cache:
            self._load_cache()

    # -- Cache internals ----------------------------------------------------

    def _load_cache(self) -> None:
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}
        else:
            self._cache = {}

    def _save_cache(self) -> None:
        if self.no_cache:
            return
        try:
            self.cache_path.write_text(json.dumps(self._cache, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _cache_get(self, key: str) -> Any | None:
        if self.no_cache:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        # We store entries as {"v": value, "ts": timestamp}
        return entry.get("v")

    def _cache_set(self, key: str, value: Any) -> None:
        if self.no_cache:
            return
        self._cache[key] = {"v": value, "ts": time.time()}
        self._save_cache()

    def clear_cache(self) -> None:
        """Remove all locally cached data (searches, user id, token)."""
        self._cache = {}
        try:
            self.cache_path.unlink(missing_ok=True)
            self.token_path.unlink(missing_ok=True)
        except OSError:
            pass
        print("Local cache and token cleared.")

    # -- Token management ---------------------------------------------------

    def _save_token(self, token_data: dict[str, Any]) -> None:
        try:
            self.token_path.write_text(json.dumps(token_data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _load_token(self) -> dict[str, Any] | None:
        if not self.token_path.exists():
            return None
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _token_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = self._get_access_token()
        return self._token

    def _get_access_token(self) -> str:
        """Return a valid access token, refreshing or re-authorizing as needed."""
        existing = self._load_token()
        if existing and existing.get("refresh_token"):
            try:
                return self._refresh_access_token(existing["refresh_token"])
            except requests.HTTPError as exc:
                print(f"Refresh token invalid ({exc}), falling back to browser auth...")
                self.token_path.unlink(missing_ok=True)

        return self._authorize()

    def _refresh_access_token(self, refresh_token: str) -> str:
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        response = requests.post(
            SPOTIFY_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        access_token = data["access_token"]
        # Spotify may return a new refresh token; if not, keep the old one.
        new_refresh = data.get("refresh_token", refresh_token)
        self._save_token({
            "access_token": access_token,
            "refresh_token": new_refresh,
            "timestamp": time.time(),
        })
        return access_token

    def _authorize(self) -> str:
        """Run the full Authorization Code flow and persist the refresh token."""
        host, port, callback_path = _parse_redirect_uri(self.redirect_uri)

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
        }
        auth_url = f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"

        print(f"\nSpotify authorization URL:\n{auth_url}\n")
        print("Attempting to open your browser automatically...")

        launcher = _open_browser(auth_url)
        if launcher:
            print(f"Browser opened via {launcher}. Approve access, then return here.\n")
        else:
            print(
                "Could not auto-open a browser in this environment.\n"
                "Please copy the URL above into any browser, approve access,\n"
                "then return here so the app can capture the callback.\n"
            )

        code = _wait_for_auth_code(host=host, port=port, callback_path=callback_path)

        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        response = requests.post(
            SPOTIFY_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            timeout=15,
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        if refresh_token:
            self._save_token({
                "access_token": access_token,
                "refresh_token": refresh_token,
                "timestamp": time.time(),
            })
        return access_token

    # -- Request wrapper with retries ---------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = MAX_RETRIES,
        **kwargs: Any,
    ) -> requests.Response:
        """Make an HTTP request with automatic rate-limit / transient-error retries."""
        for attempt in range(max_retries):
            try:
                response = requests.request(method, url, **kwargs)
            except requests.RequestException as exc:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Request failed after {max_retries} attempts: {exc}") from exc
                backoff = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                jitter = random.uniform(0, 1)
                time.sleep(backoff + jitter)
                continue

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after is not None:
                    wait = float(retry_after)
                else:
                    wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                jitter = random.uniform(0, 1)
                if attempt < max_retries - 1:
                    print(f"  Rate limited (429). Waiting {wait + jitter:.1f}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(wait + jitter)
                    continue
                else:
                    raise requests.HTTPError(
                        f"429 Too Many Requests after {max_retries} retries. Retry-After: {retry_after}",
                        response=response,
                    )

            if 500 <= response.status_code < 600:
                if attempt < max_retries - 1:
                    backoff = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                    jitter = random.uniform(0, 1)
                    print(f"  Server error ({response.status_code}). Waiting {backoff + jitter:.1f}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(backoff + jitter)
                    continue

            # For all other status codes, let raise_for_status() speak.
            try:
                response.raise_for_status()
            except requests.HTTPError:
                if attempt < max_retries - 1 and response.status_code in (408, 502, 503, 504):
                    backoff = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                    jitter = random.uniform(0, 1)
                    time.sleep(backoff + jitter)
                    continue
                raise
            return response

        raise RuntimeError(f"Max retries ({max_retries}) exceeded for {method} {url}")

    # -- API helpers --------------------------------------------------------

    def get_current_user_id(self) -> str:
        cached = self._cache_get("user_id")
        if cached:
            return cached

        response = self._request(
            "GET",
            f"{SPOTIFY_API_BASE}/me",
            headers=self._token_headers(),
            timeout=15,
        )
        user_id = response.json()["id"]
        self._cache_set("user_id", user_id)
        return user_id

    def search_track(self, query: str, verbose: bool = False) -> str | None:
        """Return the Spotify URI for the best match, or None if not found."""
        cache_key = f"search:{query}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        if self._cache_get(cache_key + ":miss") is not None:
            return None

        response = self._request(
            "GET",
            f"{SPOTIFY_API_BASE}/search",
            headers=self._token_headers(),
            params={"q": query, "type": "track", "limit": 1},
            timeout=15,
        )
        items = response.json().get("tracks", {}).get("items", [])
        uri = items[0]["uri"] if items else None
        if uri:
            self._cache_set(cache_key, uri)
        else:
            # Cache the miss so we don't hammer Spotify for tracks that don't exist.
            self._cache_set(cache_key + ":miss", True)
        return uri

    def create_playlist(self, user_id: str, name: str, description: str) -> str:
        response = self._request(
            "POST",
            f"{SPOTIFY_API_BASE}/users/{user_id}/playlists",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            json={"name": name, "description": description, "public": True},
            timeout=15,
        )
        return response.json()["id"]

    def add_tracks_to_playlist(self, playlist_id: str, uris: list[str]) -> None:
        """Add tracks to a playlist in batches of BATCH_SIZE."""
        for i in range(0, len(uris), BATCH_SIZE):
            batch = uris[i : i + BATCH_SIZE]
            self._request(
                "POST",
                f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={"uris": batch},
                timeout=30,
            )
            if i + BATCH_SIZE < len(uris):
                time.sleep(0.2)


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
    cache_dir: Path | None,
    no_cache: bool,
) -> int:
    entries = parse_names_only_file(input_file)
    if not entries:
        print("No entries found in input file.", file=sys.stderr)
        return 1

    print(f"Loaded {len(entries)} entries from {input_file}")

    client = SpotifyClient(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        cache_dir=cache_dir,
        no_cache=no_cache,
    )

    user_id = client.get_current_user_id()
    print(f"Authenticated as Spotify user: {user_id}")

    uris: list[str] = []
    not_found: list[str] = []
    cache_hits = 0

    for index, (track, artists) in enumerate(entries, start=1):
        first_artist = artists.split(",")[0].strip()
        query = f"track:{track} artist:{first_artist}"

        # Check cache before any output so we don't spam for cached items.
        cache_key = f"search:{query}"
        cached_uri = client._cache_get(cache_key)
        if cached_uri is not None:
            uris.append(cached_uri)
            cache_hits += 1
            if index % 50 == 0:
                print(f"  Searched {index}/{len(entries)} tracks...")
            if delay > 0:
                time.sleep(delay)
            continue

        verbose = index <= 5 or len(entries) <= 10
        uri = client.search_track(query, verbose=verbose)
        if not uri:
            # Fallback: looser search without field filters
            simple_query = f"{track} {first_artist}"
            uri = client.search_track(simple_query, verbose=verbose)
        if uri:
            uris.append(uri)
        else:
            not_found.append(f"{track} — {artists}")
            if verbose:
                print(f"  Not found: {track} — {artists}")

        if index % 50 == 0:
            print(f"  Searched {index}/{len(entries)} tracks...")

        if delay > 0:
            time.sleep(delay)

    if cache_hits:
        print(f"  {cache_hits} track(s) served from cache (no API call).")

    print(f"\nFound {len(uris)} tracks on Spotify. {len(not_found)} not found.")

    if not uris:
        print("No tracks could be matched. Exiting without creating a playlist.", file=sys.stderr)
        return 1

    description = f"Exported from superfridaychart.com — {len(uris)} tracks"
    playlist_id = client.create_playlist(user_id, playlist_name, description)
    print(f"Created playlist: {playlist_name} (ID: {playlist_id})")

    client.add_tracks_to_playlist(playlist_id, uris)
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
        description="Create a Spotify playlist from a Super Friday Chart names-only export.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        default=0.1,
        help="Delay between search requests in seconds (default: 0.1)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Directory to store cache and refresh token. Default is a platform-specific user cache folder.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable reading and writing the local search/user cache.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the local cache and refresh token, then exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.clear_cache:
        client = SpotifyClient(
            client_id="",
            client_secret="",
            redirect_uri=DEFAULT_REDIRECT_URI,
            cache_dir=args.cache_dir,
            no_cache=True,
        )
        client.clear_cache()
        return 0

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
            cache_dir=args.cache_dir,
            no_cache=args.no_cache,
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
    except RuntimeError as exc:
        print(f"Runtime error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
