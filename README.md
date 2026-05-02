# Spotify Playlist Creator From Song List

Create a Spotify playlist from a plain text song list in the format:

Song Name | Artist Name

This tool reads your list, searches each song on Spotify, creates a playlist on your account, and adds all matched tracks.

## Files

- `create_spotify_playlist.py`: the playlist creation script
- `spotify_playlist_creator_gui.py`: desktop GUI version with file upload + credential fields
- `run_gui_windows.bat`: double-click launcher for GUI on Windows
- `run_cli_windows.bat`: command-line launcher for Windows
- `example_songs.txt`: a ready-to-use sample input file in the correct format

## Requirements

- Python 3.9+
- `requests` Python package
- `tkinter` (required for the GUI)
- A Spotify Developer app (free)

## Windows Native Setup

1. Install Python 3 from https://www.python.org/downloads/windows/
2. During setup, enable `Add python.exe to PATH`
3. Open Command Prompt in this project folder and install dependencies:

```bat
py -3 -m pip install requests
```

4. Launch the GUI natively on Windows:

```bat
run_gui_windows.bat
```

You can also double-click `run_gui_windows.bat` in File Explorer.

If needed:

```bash
pip install requests
```

On Linux, `tkinter` is not bundled with Python and must be installed separately:

```bash
sudo apt-get install python3-tk
```

## Spotify App Setup (Required)

1. Go to https://developer.spotify.com/dashboard
2. Create a new app
3. In app Settings, add this Redirect URI exactly:

```text
http://127.0.0.1:8888/callback
```

Important: Spotify requires an exact match, including protocol, host, port, path, and trailing slash behavior.
Spotify no longer accepts `localhost` as a redirect host. Use loopback IP literals like `127.0.0.1` (or `::1`).
If your app uses a different redirect URI, use that exact same value in this tool.

4. Copy your Client ID and Client Secret

## Expected Input Format

Your input file must be a UTF-8 text file with one song per line.

Each song line must contain exactly one `|` separator:

```text
Song Name | Artist Name
```

Multiple artists are allowed on the right side:

```text
Song Name | Artist 1, Artist 2
```

### Valid examples

```text
Fantasy (ft. COBRAH) | Demi Lovato, COBRAH
Window | Foo Fighters
Shoulda Never (feat. USHER) | USHER, Kehlani
```

You can also use the included sample file directly:

```bash
python create_spotify_playlist.py --input example_songs.txt
```

### Invalid examples

```text
Fantasy (ft. COBRAH) - Demi Lovato, COBRAH   # wrong separator
Fantasy (ft. COBRAH) |                       # missing artist
| Demi Lovato                                # missing song
Fantasy (ft. COBRAH) | Demi | Lovato         # more than one separator
```

## Run

GUI version (recommended for easiest use):

```bash
python spotify_playlist_creator_gui.py
```

Windows launcher:

```bat
run_gui_windows.bat
```

GUI features:

- Browse and upload a `.txt` song list file
- Input fields for Client ID and Client Secret
- Input field for Redirect URI (must exactly match Spotify app settings)
- `Show Secret` toggle for easier credential entry
- `Save Client ID` checkbox to remember Client ID locally for next launch

CLI version:

```bash
python create_spotify_playlist.py \
  --input songs.txt \
  --playlist-name "My Imported Playlist" \
  --client-id YOUR_CLIENT_ID \
  --client-secret YOUR_CLIENT_SECRET \
  --redirect-uri http://127.0.0.1:8888/callback
```

Windows CLI launcher:

```bat
run_cli_windows.bat --input songs.txt --playlist-name "My Imported Playlist" --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET --redirect-uri http://127.0.0.1:8888/callback
```

Or use environment variables:

```bash
export SPOTIFY_CLIENT_ID=your_client_id
export SPOTIFY_CLIENT_SECRET=your_client_secret
export SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
python create_spotify_playlist.py --input songs.txt
```

Windows Command Prompt environment variables:

```bat
set SPOTIFY_CLIENT_ID=your_client_id
set SPOTIFY_CLIENT_SECRET=your_client_secret
set SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
py -3 create_spotify_playlist.py --input songs.txt
```

## CLI Options

- `--input`: Path to song list file
- `--playlist-name`: Name of playlist to create
- `--client-id`: Spotify Client ID (or `SPOTIFY_CLIENT_ID` env var)
- `--client-secret`: Spotify Client Secret (or `SPOTIFY_CLIENT_SECRET` env var)
- `--redirect-uri`: Redirect URI matching Spotify app settings exactly (or `SPOTIFY_REDIRECT_URI` env var)
- `--delay`: Delay between Spotify search requests (default: `0.1`)
- `--cache-dir`: Directory to store the local search/user cache and refresh token. Defaults to a platform-specific user cache folder.
- `--no-cache`: Disable reading and writing the local cache (always hit the Spotify API).
- `--clear-cache`: Clear the local cache and refresh token, then exit.

## Caching & Refresh Tokens

To reduce API calls and avoid re-authenticating every run:

- **Search cache**: successful (and failed) track searches are saved locally. Re-running the same list skips redundant API calls.
- **User ID cache**: your Spotify user ID is cached so it doesn't need to be fetched every time.
- **Refresh token**: after the first browser authorization, a refresh token is stored securely. On subsequent runs the app automatically requests a new access token without opening a browser.

Cache location by platform:

- Linux: `~/.cache/spotify_playlist_creator/` (or `$XDG_CACHE_HOME/spotify_playlist_creator/`)
- macOS: `~/Library/Caches/spotify_playlist_creator/`
- Windows: `%LOCALAPPDATA%/spotify_playlist_creator/`

To wipe everything and start fresh:

```bash
python create_spotify_playlist.py --clear-cache
```

## Rate-Limit Handling

The app automatically handles Spotify rate limits and transient server errors:

- **429 Too Many Requests**: respects the `Retry-After` header, waits, and retries (up to 5 times).
- **5xx Server Errors**: uses exponential backoff with jitter and retries.
- **Proactive spacing**: the default `--delay` of `0.1` seconds between searches helps stay under Spotify's limits. For very large lists you can increase it (e.g. `0.2` or `0.5`).

## Output

- Prints the created playlist URL
- Writes `<input_stem>_not_found_on_spotify.txt` with unmatched tracks (if any)

## Tests

Unit tests cover file parsing, caching, retry logic, and platform-specific cache paths.

Install pytest and run:

```bash
python -m pytest tests/ -v
```

## Notes

- The script opens a browser for Spotify authorization **only the first time** (or if the refresh token is revoked).
- If your list is very large, creation may take a few minutes.
- Respect Spotify rate limits and terms of service.
