"""Tests for create_spotify_playlist.py."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from create_spotify_playlist import (
    SpotifyClient,
    _default_cache_dir,
    _parse_redirect_uri,
    parse_names_only_file,
)


# ---------------------------------------------------------------------------
# parse_names_only_file
# ---------------------------------------------------------------------------

class TestParseNamesOnlyFile:
    def test_parses_valid_lines(self, tmp_path: Path) -> None:
        file = tmp_path / "songs.txt"
        file.write_text(
            "Window | Foo Fighters\n"
            "Fantasy (ft. COBRAH) | Demi Lovato, COBRAH\n",
            encoding="utf-8",
        )
        result = parse_names_only_file(file)
        assert result == [
            ("Window", "Foo Fighters"),
            ("Fantasy (ft. COBRAH)", "Demi Lovato, COBRAH"),
        ]

    def test_skips_blank_lines_and_headers(self, tmp_path: Path) -> None:
        file = tmp_path / "songs.txt"
        file.write_text(
            "\n"
            "Super Friday Chart\n"
            "Total 100\n"
            "\n"
            "Song | Artist\n"
            "\n",
            encoding="utf-8",
        )
        result = parse_names_only_file(file)
        assert result == [("Song", "Artist")]

    def test_skips_wrong_separator_count(self, tmp_path: Path) -> None:
        file = tmp_path / "songs.txt"
        file.write_text(
            "A | B | C\n"
            "Song | Artist\n",
            encoding="utf-8",
        )
        result = parse_names_only_file(file)
        assert result == [("Song", "Artist")]

    def test_skips_missing_track_or_artist(self, tmp_path: Path) -> None:
        file = tmp_path / "songs.txt"
        file.write_text(
            " | Artist\n"
            "Song | \n"
            "Valid | Entry\n",
            encoding="utf-8",
        )
        result = parse_names_only_file(file)
        assert result == [("Valid", "Entry")]


# ---------------------------------------------------------------------------
# _parse_redirect_uri
# ---------------------------------------------------------------------------

class TestParseRedirectUri:
    def test_valid_ipv4(self) -> None:
        host, port, path = _parse_redirect_uri("http://127.0.0.1:8888/callback")
        assert host == "127.0.0.1"
        assert port == 8888
        assert path == "/callback"

    def test_valid_ipv6(self) -> None:
        host, port, path = _parse_redirect_uri("http://[::1]:9000/callback")
        assert host == "::1"
        assert port == 9000
        assert path == "/callback"

    def test_rejects_https(self) -> None:
        with pytest.raises(ValueError, match="http"):
            _parse_redirect_uri("https://127.0.0.1:8888/callback")

    def test_rejects_localhost(self) -> None:
        with pytest.raises(ValueError, match="localhost"):
            _parse_redirect_uri("http://localhost:8888/callback")


# ---------------------------------------------------------------------------
# _default_cache_dir
# ---------------------------------------------------------------------------

class TestDefaultCacheDir:
    def test_linux_xdg(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert _default_cache_dir() == tmp_path / "spotify_playlist_creator"

    def test_linux_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        expected = Path.home() / ".cache/spotify_playlist_creator"
        assert _default_cache_dir() == expected

    def test_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        expected = Path.home() / "Library/Caches/spotify_playlist_creator"
        assert _default_cache_dir() == expected

    def test_windows_localappdata(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert _default_cache_dir() == tmp_path / "spotify_playlist_creator"


# ---------------------------------------------------------------------------
# SpotifyClient caching
# ---------------------------------------------------------------------------

class TestSpotifyClientCache:
    def test_cache_roundtrip(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client._cache_set("search:hello", "spotify:track:123")
        assert client._cache_get("search:hello") == "spotify:track:123"

    def test_no_cache_returns_none(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path, no_cache=True)
        client._cache_set("search:hello", "spotify:track:123")
        assert client._cache_get("search:hello") is None

    def test_clear_cache_removes_files(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client._cache_set("k", "v")
        client._save_token({"refresh_token": "abc"})
        assert client.cache_path.exists()
        assert client.token_path.exists()
        client.clear_cache()
        assert not client.cache_path.exists()
        assert not client.token_path.exists()
        assert client._cache == {}

    def test_load_cache_on_init(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client._cache_set("user_id", "alice")
        # New instance should load existing cache
        client2 = self._make_client(tmp_path)
        assert client2._cache_get("user_id") == "alice"

    def _make_client(self, cache_dir: Path, no_cache: bool = False) -> SpotifyClient:
        return SpotifyClient(
            client_id="id",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8888/callback",
            cache_dir=cache_dir,
            no_cache=no_cache,
        )


# ---------------------------------------------------------------------------
# SpotifyClient._request retries
# ---------------------------------------------------------------------------

class TestSpotifyClientRetries:
    def _make_client(self, tmp_path: Path) -> SpotifyClient:
        return SpotifyClient(
            client_id="id",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8888/callback",
            cache_dir=tmp_path,
        )

    def test_success_no_retry(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        with patch("create_spotify_playlist.requests.request", return_value=mock_resp) as mock_req:
            resp = client._request("GET", "https://api.spotify.com/v1/me")
            assert resp is mock_resp
            assert mock_req.call_count == 1

    def test_retry_on_429_with_retry_after(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)

        def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            resp = MagicMock()
            if side_effect.call_count == 0:  # type: ignore[attr-defined]
                resp.status_code = 429
                resp.headers = {"Retry-After": "0.01"}
            else:
                resp.status_code = 200
                resp.raise_for_status.return_value = None
            side_effect.call_count += 1  # type: ignore[attr-defined]
            return resp

        side_effect.call_count = 0  # type: ignore[attr-defined]

        with patch("create_spotify_playlist.requests.request", side_effect=side_effect) as mock_req:
            with patch("create_spotify_playlist.time.sleep"):
                resp = client._request("GET", "https://api.spotify.com/v1/me")
            assert resp.status_code == 200
            assert mock_req.call_count == 2

    def test_retry_on_500(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)

        def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            resp = MagicMock()
            if side_effect.call_count < 2:  # type: ignore[attr-defined]
                resp.status_code = 503
            else:
                resp.status_code = 200
                resp.raise_for_status.return_value = None
            side_effect.call_count += 1  # type: ignore[attr-defined]
            return resp

        side_effect.call_count = 0  # type: ignore[attr-defined]

        with patch("create_spotify_playlist.requests.request", side_effect=side_effect) as mock_req:
            with patch("create_spotify_playlist.time.sleep"):
                resp = client._request("GET", "https://api.spotify.com/v1/me")
            assert resp.status_code == 200
            assert mock_req.call_count == 3

    def test_fail_after_max_retries(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}

        with patch("create_spotify_playlist.requests.request", return_value=mock_resp):
            with patch("create_spotify_playlist.time.sleep"):
                with pytest.raises(Exception, match="429"):
                    client._request("GET", "https://api.spotify.com/v1/me", max_retries=3)

    def test_no_retry_on_400(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.raise_for_status.side_effect = Exception("Bad Request")

        with patch("create_spotify_playlist.requests.request", return_value=mock_resp):
            with pytest.raises(Exception, match="Bad Request"):
                client._request("GET", "https://api.spotify.com/v1/me")


# ---------------------------------------------------------------------------
# SpotifyClient.search_track caching
# ---------------------------------------------------------------------------

class TestSpotifyClientSearchTrack:
    def _make_client(self, tmp_path: Path) -> SpotifyClient:
        return SpotifyClient(
            client_id="id",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8888/callback",
            cache_dir=tmp_path,
        )

    def test_search_caches_hit(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client._token = "fake"
        client._cache_set("search:hello", "spotify:track:abc")

        with patch("create_spotify_playlist.requests.request") as mock_req:
            uri = client.search_track("hello")
            assert uri == "spotify:track:abc"
            mock_req.assert_not_called()

    def test_search_caches_miss(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client._token = "fake"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tracks": {"items": []}}
        mock_resp.raise_for_status.return_value = None

        with patch("create_spotify_playlist.requests.request", return_value=mock_resp):
            uri = client.search_track("hello")
            assert uri is None
            # A miss should be cached so the next call is skipped
            assert client._cache_get("search:hello:miss") is True

    def test_search_caches_result(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client._token = "fake"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tracks": {"items": [{"uri": "spotify:track:xyz"}]}
        }
        mock_resp.raise_for_status.return_value = None

        with patch("create_spotify_playlist.requests.request", return_value=mock_resp):
            uri = client.search_track("hello")
            assert uri == "spotify:track:xyz"
            assert client._cache_get("search:hello") == "spotify:track:xyz"
