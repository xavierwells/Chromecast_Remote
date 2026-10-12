"""Simple Windows tray launcher and per-PC configuration for CastDeck."""

from __future__ import annotations

import json
import os
import queue
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


import pystray
import qrcode
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk
from tkinter import filedialog, messagebox, ttk


APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CastDeck"
CONFIG_PATH = APP_DATA / "config.json"
LOG_PATH = APP_DATA / "server.log"
DEFAULTS = {
    "media_dirs": [str(application_dir() / "media")],
    "port": 4173,
    "lan_access": True,
    "auto_start": False,
}


def load_config() -> dict:
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        saved = {}
    if "media_dirs" not in saved and saved.get("media_dir"):
        saved["media_dirs"] = [saved["media_dir"]]
    config = {**DEFAULTS, **{key: value for key, value in saved.items() if key in DEFAULTS}}
    directories = config.get("media_dirs")
    if not isinstance(directories, list) or not directories:
        config["media_dirs"] = list(DEFAULTS["media_dirs"])
    else:
        config["media_dirs"] = [str(item) for item in directories if str(item).strip()]
    return config


def save_config(config: dict) -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_PATH)


def tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), "#101318")
    draw = ImageDraw.Draw(image)
    lime = "#d5ff52"
    draw.rounded_rectangle((9, 8, 55, 54), radius=8, outline=lime, width=5)
    draw.arc((-12, 34, 30, 76), 270, 360, fill=lime, width=5)
    draw.arc((-20, 24, 40, 84), 270, 360, fill=lime, width=5)
    draw.ellipse((8, 47, 15, 54), fill=lime)
    return image


class SettingsDialog:
    def __init__(self, owner: "CastDeckLauncher"):
        self.owner = owner
        self.window = tk.Toplevel(owner.root)
        self.window.title("CastDeck Settings")
        self.window.geometry("610x455")
        self.window.minsize(570, 420)
        self.window.transient(owner.root)
        self.window.grab_set()

        self.port = tk.StringVar(value=str(owner.config["port"]))
        self.lan_access = tk.BooleanVar(value=bool(owner.config["lan_access"]))
        self.auto_start = tk.BooleanVar(value=bool(owner.config["auto_start"]))

        shell = ttk.Frame(self.window, padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Media folders", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(shell, text="CastDeck combines compatible files from every folder below.", foreground="#555555").pack(anchor="w", pady=(2, 8))

        list_frame = ttk.Frame(shell)
        list_frame.pack(fill="both", expand=True)
        self.folder_list = tk.Listbox(list_frame, height=8, selectmode="extended")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.folder_list.yview)
        self.folder_list.configure(yscrollcommand=scrollbar.set)
        self.folder_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for directory in owner.config["media_dirs"]:
            self.folder_list.insert("end", directory)

        folder_actions = ttk.Frame(shell)
        folder_actions.pack(fill="x", pady=(8, 15))
        ttk.Button(folder_actions, text="Add folder…", command=self.add_folder).pack(side="left")
        ttk.Button(folder_actions, text="Remove selected", command=self.remove_selected).pack(side="left", padx=7)

        network = ttk.LabelFrame(shell, text="Server", padding=10)
        network.pack(fill="x")
        ttk.Label(network, text="Port:").grid(row=0, column=0, sticky="w")
        ttk.Entry(network, textvariable=self.port, width=9).grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Checkbutton(network, text="Allow phones and tablets on this LAN", variable=self.lan_access).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(network, text="Start server when CastDeck opens", variable=self.auto_start).grid(row=1, column=0, columnspan=3, sticky="w", pady=(9, 0))

        actions = ttk.Frame(shell)
        actions.pack(fill="x", pady=(16, 0))
        ttk.Button(actions, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(actions, text="Save", command=self.save).pack(side="right", padx=7)

    def add_folder(self) -> None:
        initial = self.folder_list.get(0) if self.folder_list.size() else str(application_dir())
        selected = filedialog.askdirectory(title="Add a CastDeck media folder", initialdir=initial, parent=self.window)
        if selected and selected not in self.folder_list.get(0, "end"):
            self.folder_list.insert("end", selected)

    def remove_selected(self) -> None:
        for index in reversed(self.folder_list.curselection()):
            self.folder_list.delete(index)

    def save(self) -> None:
        directories = list(self.folder_list.get(0, "end"))
        if not directories:
            messagebox.showerror("Media folder required", "Add at least one media folder.", parent=self.window)
            return
        try:
            port = int(self.port.get())
        except ValueError:
            port = 0
        if not 1024 <= port <= 65535:
            messagebox.showerror("Invalid port", "Use a port from 1024 to 65535.", parent=self.window)
            return
        config = {
            "media_dirs": [str(Path(item).expanduser().resolve()) for item in directories],
            "port": port,
            "lan_access": self.lan_access.get(),
            "auto_start": self.auto_start.get(),
        }
        try:
            save_config(config)
        except OSError as error:
            messagebox.showerror("Settings were not saved", str(error), parent=self.window)
            return
        self.owner.config = config
        self.owner.refresh_addresses()
        if self.owner.is_running():
            self.owner.status.set("Running — restart to apply settings")
        self.window.destroy()


class CastDeckLauncher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CastDeck")
        self.root.geometry("480x315")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.config = load_config()
        self.process: subprocess.Popen | None = None
        self.log_file = None
        self.ui_actions: queue.Queue = queue.Queue()
        self.tray_icon: pystray.Icon | None = None
        self.tray_notice_shown = False

        self.status = tk.StringVar(value="Stopped")
        self.phone_address = tk.StringVar(value="")
        self.fallback_address = tk.StringVar(value="")
        self.build_ui()
        self.create_tray_icon()
        self.refresh_addresses()
        self.root.after(100, self.process_ui_actions)
        if self.config["auto_start"]:
            self.root.after(350, self.start)

    def build_ui(self) -> None:
        try:
            ttk.Style(self.root).theme_use("vista")
        except tk.TclError:
            pass
        shell = ttk.Frame(self.root, padding=20)
        shell.pack(fill="both", expand=True)

        ttk.Label(shell, text="CastDeck Server", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(shell, text="Local Chromecast control", foreground="#555555").pack(anchor="w", pady=(0, 14))

        status_box = ttk.LabelFrame(shell, text="Status", padding=12)
        status_box.pack(fill="x")
        ttk.Label(status_box, textvariable=self.status, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(status_box, textvariable=self.phone_address).pack(anchor="w", pady=(7, 0))
        ttk.Label(status_box, textvariable=self.fallback_address, foreground="#666666").pack(anchor="w")

        primary = ttk.Frame(shell)
        primary.pack(fill="x", pady=(15, 8))
        self.start_button = ttk.Button(primary, text="Start", command=self.start, width=16)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(primary, text="Stop", command=self.stop, width=16, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(primary, text="Settings…", command=self.open_settings).pack(side="right")

        secondary = ttk.Frame(shell)
        secondary.pack(fill="x")
        self.open_button = ttk.Button(secondary, text="Open Controller", command=self.open_controller, state="disabled")
        self.open_button.pack(side="left")
        self.qr_button = ttk.Button(secondary, text="Phone QR…", command=self.show_qr, state="disabled")
        self.qr_button.pack(side="left", padx=7)
        ttk.Button(secondary, text="Open Log", command=self.open_log).pack(side="right")

        ttk.Separator(shell).pack(fill="x", pady=(15, 9))
        ttk.Label(shell, text="Closing this window keeps CastDeck running in the notification area.", foreground="#666666").pack(anchor="w")

    def create_tray_icon(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Open CastDeck", lambda _icon, _item: self.enqueue(self.show_window), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start", lambda _icon, _item: self.enqueue(self.start), enabled=lambda _item: not self.is_running()),
            pystray.MenuItem("Stop", lambda _icon, _item: self.enqueue(self.stop), enabled=lambda _item: self.is_running()),
            pystray.MenuItem("Settings…", lambda _icon, _item: self.enqueue(self.open_settings)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda _icon, _item: self.enqueue(self.exit_application)),
        )
        try:
            self.tray_icon = pystray.Icon("CastDeck", tray_image(), "CastDeck", menu)
            self.tray_icon.run_detached()
        except Exception:
            self.tray_icon = None

    def enqueue(self, callback, *arguments) -> None:
        self.ui_actions.put((callback, arguments))

    def process_ui_actions(self) -> None:
        try:
            while True:
                callback, arguments = self.ui_actions.get_nowait()
                callback(*arguments)
        except queue.Empty:
            pass
        self.root.after(100, self.process_ui_actions)

    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def start(self) -> None:
        if self.is_running():
            return
        try:
            config = load_config()
            directories = [Path(item).expanduser().resolve() for item in config["media_dirs"]]
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
            port = int(config["port"])
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", port))
        except (OSError, ValueError) as error:
            messagebox.showerror("CastDeck could not start", str(error), parent=self.root)
            return

        self.config = config
        host = "0.0.0.0" if config["lan_access"] else "127.0.0.1"
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--server", "--host", host, "--port", str(port)]
        else:
            command = [sys.executable, str(Path(__file__).resolve()), "--server", "--host", host, "--port", str(port)]
        environment = os.environ.copy()
        environment["CASTDECK_MEDIA_DIRS"] = json.dumps([str(item) for item in directories])
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        APP_DATA.mkdir(parents=True, exist_ok=True)
        self.log_file = LOG_PATH.open("a", encoding="utf-8")
        self.log_file.write(f"\n--- Starting CastDeck {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        self.log_file.flush()
        self.process = subprocess.Popen(command, env=environment, cwd=application_dir(), stdin=subprocess.DEVNULL,
                                        stdout=self.log_file, stderr=subprocess.STDOUT, creationflags=flags)
        self.status.set("Starting…")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.qr_button.configure(state="disabled")
        self.update_tray_menu()
        threading.Thread(target=self.wait_until_ready, args=(self.process,), daemon=True).start()

    def wait_until_ready(self, process: subprocess.Popen) -> None:
        port = int(self.config["port"])
        for _ in range(150):
            if process.poll() is not None:
                self.enqueue(self.server_failed_if_current, process, "The server exited before it was ready. Check the log for details.")
                return
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    self.enqueue(self.server_ready, process)
                    process.wait()
                    self.enqueue(self.process_ended, process)
                    return
            except OSError:
                time.sleep(0.1)
        self.enqueue(self.server_failed_if_current, process, "The server did not start in time. Check the log for details.")

    def server_ready(self, process: subprocess.Popen) -> None:
        if process is not self.process:
            return
        self.status.set("Running")
        self.stop_button.configure(state="normal")
        self.open_button.configure(state="normal")
        self.qr_button.configure(state="normal")
        self.refresh_addresses()
        self.update_tray_menu()

    def server_failed(self, message: str) -> None:
        self.stop(show_status=False)
        self.status.set("Failed to start")
        messagebox.showerror("CastDeck could not start", message, parent=self.root)

    def server_failed_if_current(self, process: subprocess.Popen, message: str) -> None:
        if process is self.process:
            self.server_failed(message)

    def process_ended(self, process: subprocess.Popen) -> None:
        if process is self.process:
            self.process = None
            self.close_log()
            self.status.set("Stopped")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.open_button.configure(state="disabled")
            self.qr_button.configure(state="disabled")
            self.update_tray_menu()

    def stop(self, show_status: bool = True) -> None:
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self.close_log()
        if show_status:
            self.status.set("Stopped")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.qr_button.configure(state="disabled")
        self.update_tray_menu()

    def close_log(self) -> None:
        if self.log_file:
            self.log_file.close()
            self.log_file = None

    def refresh_addresses(self) -> None:
        port = int(self.config["port"])
        if self.config["lan_access"]:
            self.phone_address.set(f"Phone: http://castdeck.local:{port}")
            self.fallback_address.set(f"Fallback: http://{self.lan_address()}:{port}")
        else:
            self.phone_address.set(f"This PC only: http://127.0.0.1:{port}")
            self.fallback_address.set("")

    def update_tray_menu(self) -> None:
        if self.tray_icon:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass

    def hide_to_tray(self) -> None:
        if not self.tray_icon:
            self.root.iconify()
            return
        self.root.withdraw()
        if not self.tray_notice_shown:
            self.tray_notice_shown = True
            try:
                self.tray_icon.notify("CastDeck is still available here. Right-click to stop or exit.", "CastDeck")
            except Exception:
                pass

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def open_settings(self) -> None:
        self.show_window()
        SettingsDialog(self)

    def open_controller(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{int(self.config['port'])}")

    def show_qr(self) -> None:
        if not self.is_running():
            return
        url = self.reliable_phone_url()
        image = qrcode.make(url).convert("RGB").resize((250, 250))
        window = tk.Toplevel(self.root)
        window.title("CastDeck Phone Link")
        window.resizable(False, False)
        photo = ImageTk.PhotoImage(image)
        label = ttk.Label(window, image=photo)
        label.image = photo
        label.pack(padx=24, pady=(22, 10))
        ttk.Label(window, text="Scan once, then add CastDeck to your home screen.").pack(padx=20)
        ttk.Label(window, text=url, foreground="#555555").pack(padx=20, pady=(5, 20))

    def open_log(self) -> None:
        APP_DATA.mkdir(parents=True, exist_ok=True)
        LOG_PATH.touch(exist_ok=True)
        os.startfile(LOG_PATH)

    def reliable_phone_url(self) -> str:
        if not self.config["lan_access"]:
            return f"http://127.0.0.1:{int(self.config['port'])}"
        return f"http://{self.lan_address()}:{int(self.config['port'])}"

    @staticmethod
    def lan_address() -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 80))
            return str(probe.getsockname()[0])
        except OSError:
            try:
                return socket.gethostbyname(socket.gethostname())
            except OSError:
                return "127.0.0.1"
        finally:
            probe.close()

    def exit_application(self) -> None:
        self.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    CastDeckLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
