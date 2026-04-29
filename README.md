# Spotify Playlist Creator From Song List

Create a Spotify playlist from a plain text song list in the format:

Song Name | Artist Name

This tool reads your list, searches each song on Spotify, creates a playlist on your account, and adds all matched tracks.

## Files

- `create_spotify_playlist.py`: the playlist creation script
- `spotify_playlist_creator_gui.py`: desktop GUI version with file upload + credential fields
- `example_songs.txt`: a ready-to-use sample input file in the correct format

## Requirements

- Python 3.9+
- `requests` Python package
- A Spotify Developer app (free)

If needed:

```bash
pip install requests
```

## Spotify App Setup (Required)

1. Go to https://developer.spotify.com/dashboard
2. Create a new app
3. In app Settings, add this Redirect URI exactly:

```text
http://localhost:8888/callback
```

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
/usr/bin/python3 create_spotify_playlist.py --input example_songs.txt
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
/usr/bin/python3 spotify_playlist_creator_gui.py
```

GUI features:

- Browse and upload a `.txt` song list file
- Input fields for Client ID and Client Secret
- `Show Secret` toggle for easier credential entry
- `Save Client ID` checkbox to remember Client ID locally for next launch

CLI version:

```bash
/usr/bin/python3 create_spotify_playlist.py \
  --input songs.txt \
  --playlist-name "My Imported Playlist" \
  --client-id YOUR_CLIENT_ID \
  --client-secret YOUR_CLIENT_SECRET
```

Or use environment variables:

```bash
export SPOTIFY_CLIENT_ID=your_client_id
export SPOTIFY_CLIENT_SECRET=your_client_secret
/usr/bin/python3 create_spotify_playlist.py --input songs.txt
```

## CLI Options

- `--input`: Path to song list file
- `--playlist-name`: Name of playlist to create
- `--client-id`: Spotify Client ID (or `SPOTIFY_CLIENT_ID` env var)
- `--client-secret`: Spotify Client Secret (or `SPOTIFY_CLIENT_SECRET` env var)
- `--delay`: Delay between Spotify search requests (default: `0.05`)

## Output

- Prints the created playlist URL
- Writes `<input_stem>_not_found_on_spotify.txt` with unmatched tracks (if any)

## Notes

- The script opens a browser for Spotify authorization.
- If your list is very large, creation may take a few minutes.
- Respect Spotify rate limits and terms of service.
