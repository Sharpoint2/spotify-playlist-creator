#!/usr/bin/env python3
"""Simple GUI wrapper for create_spotify_playlist.py.

This app lets you:
- pick a .txt song list file
- enter Spotify Client ID and Client Secret
- set playlist name and optional request delay
- run playlist creation and view live logs
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


class PlaylistCreatorGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Spotify Playlist Creator")
        self.root.geometry("860x620")
        self.root.minsize(760, 520)

        self.output_queue: queue.Queue[str] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None

        self.input_file_var = tk.StringVar(value="")
        self.playlist_name_var = tk.StringVar(value="My Imported Playlist")
        self.client_id_var = tk.StringVar(value="")
        self.client_secret_var = tk.StringVar(value="")
        self.delay_var = tk.StringVar(value="0.05")
        self.show_secret_var = tk.BooleanVar(value=False)
        self.save_client_id_var = tk.BooleanVar(value=False)

        self.client_secret_entry: ttk.Entry | None = None

        self._load_config()

        self._build_ui()
        self._start_log_poller()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(main, text="Spotify Playlist Creator", font=("TkDefaultFont", 14, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(
            main,
            text="Upload a song list file and enter your Spotify app credentials.",
        )
        subtitle.pack(anchor="w", pady=(2, 10))

        form = ttk.Frame(main)
        form.pack(fill=tk.X)

        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Song List (.txt):").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(form, textvariable=self.input_file_var).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Button(form, text="Browse...", command=self._browse_file).grid(row=0, column=2, sticky="w", padx=(8, 0), pady=5)

        ttk.Label(form, text="Playlist Name:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(form, textvariable=self.playlist_name_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(form, text="Client ID:").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(form, textvariable=self.client_id_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(form, text="Client Secret:").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)
        self.client_secret_entry = ttk.Entry(form, textvariable=self.client_secret_var, show="*")
        self.client_secret_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=5)

        options_row = ttk.Frame(form)
        options_row.grid(row=4, column=1, columnspan=2, sticky="w", pady=(0, 3))
        ttk.Checkbutton(
            options_row,
            text="Show Secret",
            variable=self.show_secret_var,
            command=self._toggle_secret_visibility,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            options_row,
            text="Save Client ID",
            variable=self.save_client_id_var,
        ).pack(side=tk.LEFT, padx=(14, 0))

        ttk.Label(form, text="Delay (seconds):").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(form, textvariable=self.delay_var).grid(row=5, column=1, columnspan=2, sticky="ew", pady=5)

        buttons = ttk.Frame(main)
        buttons.pack(fill=tk.X, pady=(10, 8))

        self.run_btn = ttk.Button(buttons, text="Create Playlist", command=self._run)
        self.run_btn.pack(side=tk.LEFT)

        self.stop_btn = ttk.Button(buttons, text="Stop", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Button(buttons, text="Clear Log", command=self._clear_log).pack(side=tk.RIGHT)

        ttk.Label(main, text="Live Output:").pack(anchor="w")

        self.log_text = tk.Text(main, wrap="word", height=22)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Song List File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.input_file_var.set(path)

    def _validate_inputs(self) -> bool:
        input_path = Path(self.input_file_var.get().strip()).expanduser()
        if not input_path.exists():
            messagebox.showerror("Missing File", "Please select a valid .txt input file.")
            return False

        if not self.client_id_var.get().strip():
            messagebox.showerror("Missing Client ID", "Please enter your Spotify Client ID.")
            return False

        if not self.client_secret_var.get().strip():
            messagebox.showerror("Missing Client Secret", "Please enter your Spotify Client Secret.")
            return False

        try:
            float(self.delay_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Delay", "Delay must be a number, e.g. 0.05")
            return False

        return True

    def _run(self) -> None:
        if self.process is not None:
            return

        if not self._validate_inputs():
            return

        self._save_config()

        script_path = Path(__file__).with_name("create_spotify_playlist.py")
        if not script_path.exists():
            messagebox.showerror("Missing Script", f"Could not find: {script_path}")
            return

        command = [
            sys.executable,
            str(script_path),
            "--input",
            self.input_file_var.get().strip(),
            "--playlist-name",
            self.playlist_name_var.get().strip() or "My Imported Playlist",
            "--client-id",
            self.client_id_var.get().strip(),
            "--client-secret",
            self.client_secret_var.get().strip(),
            "--delay",
            self.delay_var.get().strip(),
        ]

        self._append_log("Starting playlist creation...\n")
        self._append_log("Command: " + " ".join(command[:-2]) + " [hidden] [hidden]\n\n")

        self.run_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        def worker() -> None:
            try:
                self.process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                assert self.process.stdout is not None
                for line in self.process.stdout:
                    self.output_queue.put(line)

                code = self.process.wait()
                if code == 0:
                    self.output_queue.put("\nDone. Playlist creation finished successfully.\n")
                else:
                    self.output_queue.put(f"\nProcess exited with code {code}.\n")
            except Exception as exc:  # noqa: BLE001
                self.output_queue.put(f"\nUnexpected GUI runner error: {exc}\n")
            finally:
                self.process = None
                self.output_queue.put("__PROCESS_DONE__")

        threading.Thread(target=worker, daemon=True).start()

    def _stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        self._append_log("\nStop requested. Terminating process...\n")

    def _clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def _toggle_secret_visibility(self) -> None:
        if self.client_secret_entry is None:
            return
        self.client_secret_entry.config(show="" if self.show_secret_var.get() else "*")

    def _config_path(self) -> Path:
        return Path(__file__).with_name(".spotify_playlist_creator_gui.json")

    def _load_config(self) -> None:
        path = self._config_path()
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            client_id = str(data.get("client_id", "")).strip()
            if client_id:
                self.client_id_var.set(client_id)
                self.save_client_id_var.set(True)
        except (OSError, json.JSONDecodeError):
            # Ignore malformed or unreadable config and continue with defaults.
            return

    def _save_config(self) -> None:
        path = self._config_path()

        if not self.save_client_id_var.get():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return

        payload = {"client_id": self.client_id_var.get().strip()}
        try:
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError:
            self._append_log("Warning: could not save Client ID preference to config file.\n")

    def _append_log(self, text: str) -> None:
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def _start_log_poller(self) -> None:
        def poll() -> None:
            while True:
                try:
                    line = self.output_queue.get_nowait()
                except queue.Empty:
                    break

                if line == "__PROCESS_DONE__":
                    self.run_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                else:
                    self._append_log(line)

            self.root.after(100, poll)

        poll()


def main() -> int:
    root = tk.Tk()
    app = PlaylistCreatorGUI(root)
    _ = app
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
