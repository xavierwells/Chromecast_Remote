"""Native CastDeck server launcher and per-PC configurator."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def application_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def run_server_mode() -> None:
    sys.argv = [sys.argv[0], *[argument for argument in sys.argv[1:] if argument != "--server"]]
    from server import main

    main()


if "--server" in sys.argv:
    run_server_mode()
    raise SystemExit


import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CastDeck"
CONFIG_PATH = APP_DATA / "config.json"
DEFAULTS = {
    "media_dir": str(application_dir() / "media"),
    "port": 4173,
    "lan_access": True,
    "open_browser": True,
    "auto_start": False,
}


def load_config() -> dict:
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        saved = {}
    return {**DEFAULTS, **{key: value for key, value in saved.items() if key in DEFAULTS}}


def save_config(config: dict) -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_PATH)


class CastDeckLauncher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CastDeck Server")
        self.root.geometry("680x570")
        self.root.minsize(620, 550)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.process: subprocess.Popen | None = None
        self.config = load_config()

        self.media_dir = tk.StringVar(value=str(self.config["media_dir"]))
        self.port = tk.StringVar(value=str(self.config["port"]))
        self.lan_access = tk.BooleanVar(value=bool(self.config["lan_access"]))
        self.open_browser_on_start = tk.BooleanVar(value=bool(self.config["open_browser"]))
        self.auto_start = tk.BooleanVar(value=bool(self.config["auto_start"]))
        self.status = tk.StringVar(value="Ready to start")
        self.local_url = tk.StringVar(value="")
        self.phone_url = tk.StringVar(value="")

        self.build_ui()
        if self.auto_start.get():
            self.root.after(300, self.start)

    def build_ui(self) -> None:
        self.root.configure(background="#090b0f")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#090b0f")
        style.configure("Panel.TFrame", background="#11151b")
        style.configure("TLabel", background="#090b0f", foreground="#f4f1eb", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground="#89919e")
        style.configure("Title.TLabel", foreground="#f4f1eb", font=("Segoe UI", 24, "bold"))
        style.configure("Accent.TLabel", foreground="#d5ff52", font=("Consolas", 9, "bold"))
        style.configure("TCheckbutton", background="#11151b", foreground="#d2d5da")
        style.map("TCheckbutton", background=[("active", "#11151b")])
        style.configure("TButton", padding=(12, 8))
        style.configure("Accent.TButton", background="#d5ff52", foreground="#090b0f", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#e3ff82"), ("disabled", "#596638")])

        shell = ttk.Frame(self.root, padding=28)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="CASTDECK", style="Accent.TLabel").pack(anchor="w")
        ttk.Label(shell, text="Local server", style="Title.TLabel").pack(anchor="w", pady=(4, 4))
        ttk.Label(shell, text="Configure this PC, then start the controller for your home network.", style="Muted.TLabel").pack(anchor="w", pady=(0, 20))

        panel = ttk.Frame(shell, style="Panel.TFrame", padding=20)
        panel.pack(fill="x")
        panel.columnconfigure(0, weight=1)

        ttk.Label(panel, text="LOCAL MEDIA FOLDER", style="Accent.TLabel", background="#11151b").grid(row=0, column=0, columnspan=2, sticky="w")
        self.media_entry = ttk.Entry(panel, textvariable=self.media_dir)
        self.media_entry.grid(row=1, column=0, sticky="ew", pady=(7, 16), padx=(0, 8))
        self.browse_button = ttk.Button(panel, text="Browse…", command=self.browse)
        self.browse_button.grid(row=1, column=1, sticky="ew", pady=(7, 16))

        ttk.Label(panel, text="SERVER PORT", style="Accent.TLabel", background="#11151b").grid(row=2, column=0, sticky="w")
        self.port_entry = ttk.Entry(panel, textvariable=self.port, width=10)
        self.port_entry.grid(row=3, column=0, sticky="w", pady=(7, 15))

        self.lan_check = ttk.Checkbutton(panel, text="Allow phones and tablets on this LAN", variable=self.lan_access)
        self.lan_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=3)
        self.browser_check = ttk.Checkbutton(panel, text="Open the controller after starting", variable=self.open_browser_on_start)
        self.browser_check.grid(row=5, column=0, columnspan=2, sticky="w", pady=3)
        self.auto_check = ttk.Checkbutton(panel, text="Start the server automatically when CastDeck opens", variable=self.auto_start)
        self.auto_check.grid(row=6, column=0, columnspan=2, sticky="w", pady=3)

        status_row = ttk.Frame(shell, padding=(0, 18, 0, 10))
        status_row.pack(fill="x")
        ttk.Label(status_row, textvariable=self.status, style="Accent.TLabel").pack(side="left")
        ttk.Label(status_row, textvariable=self.phone_url, style="Muted.TLabel").pack(side="right")

        actions = ttk.Frame(shell)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Start CastDeck", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(actions, text="Open Controller", command=self.open_controller, state="disabled")
        self.open_button.pack(side="left", padx=(0, 8))
        self.folder_button = ttk.Button(actions, text="Open Media Folder", command=self.open_media_folder)
        self.folder_button.pack(side="right")

        ttk.Label(shell, text="Tip: Windows may ask to allow network access the first time. Choose Private networks only.", style="Muted.TLabel").pack(anchor="w", pady=(18, 0))

    def current_config(self) -> dict:
        media_dir = Path(self.media_dir.get().strip()).expanduser().resolve()
        try:
            port = int(self.port.get())
        except ValueError as error:
            raise ValueError("The port must be a number from 1024 to 65535.") from error
        if not 1024 <= port <= 65535:
            raise ValueError("The port must be between 1024 and 65535.")
        return {
            "media_dir": str(media_dir),
            "port": port,
            "lan_access": self.lan_access.get(),
            "open_browser": self.open_browser_on_start.get(),
            "auto_start": self.auto_start.get(),
        }

    def browse(self) -> None:
        selected = filedialog.askdirectory(title="Choose CastDeck media folder", initialdir=self.media_dir.get() or str(application_dir()))
        if selected:
            self.media_dir.set(selected)

    def set_editing(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (self.media_entry, self.port_entry, self.browse_button, self.lan_check, self.browser_check, self.auto_check):
            widget.configure(state=state)

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        try:
            config = self.current_config()
            media_dir = Path(config["media_dir"])
            media_dir.mkdir(parents=True, exist_ok=True)
            save_config(config)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", config["port"]))
        except (OSError, ValueError) as error:
            messagebox.showerror("CastDeck could not start", str(error), parent=self.root)
            return

        host = "0.0.0.0" if config["lan_access"] else "127.0.0.1"
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--server", "--host", host, "--port", str(config["port"])]
        else:
            command = [sys.executable, str(Path(__file__).resolve()), "--server", "--host", host, "--port", str(config["port"])]
        environment = os.environ.copy()
        environment["CASTDECK_MEDIA_DIR"] = config["media_dir"]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(command, env=environment, cwd=application_dir(), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        self.config = config
        self.status.set("Starting…")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.set_editing(False)
        threading.Thread(target=self.wait_until_ready, daemon=True).start()

    def wait_until_ready(self) -> None:
        port = int(self.config["port"])
        for _ in range(120):
            if not self.process or self.process.poll() is not None:
                self.root.after(0, lambda: self.server_failed("The server exited before it was ready."))
                return
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    self.root.after(0, self.server_ready)
                    return
            except OSError:
                time.sleep(0.1)
        self.root.after(0, lambda: self.server_failed("The server did not start in time."))

    def server_ready(self) -> None:
        port = int(self.config["port"])
        self.local_url.set(f"http://127.0.0.1:{port}")
        self.phone_url.set(f"Phone: http://{self.lan_address()}:{port}" if self.config["lan_access"] else "Local access only")
        self.status.set("Running")
        self.open_button.configure(state="normal")
        if self.config["open_browser"]:
            self.open_controller()

    def server_failed(self, message: str) -> None:
        self.process = None
        self.status.set("Stopped")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.set_editing(True)
        messagebox.showerror("CastDeck could not start", message, parent=self.root)

    def stop(self) -> None:
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self.status.set("Stopped")
        self.phone_url.set("")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.set_editing(True)

    def open_controller(self) -> None:
        port = int(self.config.get("port", self.port.get()))
        webbrowser.open(f"http://127.0.0.1:{port}")

    def open_media_folder(self) -> None:
        try:
            folder = Path(self.media_dir.get().strip()).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except OSError as error:
            messagebox.showerror("Unable to open the media folder", str(error), parent=self.root)

    @staticmethod
    def lan_address() -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 80))
            return str(probe.getsockname()[0])
        except OSError:
            return "this-pc"
        finally:
            probe.close()

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            warning = "Closing CastDeck stops the server. Local media currently playing may stop loading. Close anyway?"
            if not messagebox.askyesno("Close CastDeck", warning, parent=self.root):
                return
            self.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    CastDeckLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
