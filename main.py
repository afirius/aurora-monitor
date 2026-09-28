"""АВРОРА — управление локальным монитором компьютера и USB-дисплеем."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFont, ImageTk
import psutil

from display import DisplayWorker
from renderer import render, speed, value, Painter, hardware_badge, short_model, hardware_vendor
from mac_widgets import (PillButton, SidebarButton, RoundedPanel, SlimScrollbar,
                         SmoothScale, ToggleSwitch, SelectField, StepperField, TextField)
from app_icon import ICON_ICO, icon_image
from telemetry import Telemetry
from weather import WeatherService


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "AuroraMonitor"
PAGES = {
    "overview": "Обзор",
    "graphs": "Нагрузка",
    "thermal": "Температуры",
    "weather": "Погода",
    "clock": "Часы",
    "media": "Музыка",
    "gaming": "Игра",
}
THEMES = {"auto": "Автоматически", "day": "Дневная", "night": "Ночная"}
UI_PALETTES = {
    "day": dict(bg="#F1F1F4", side="#E6E7EC", panel="#FFFFFF", panel_2="#F2F3F6",
                border="#D4D5DD", text="#202125", muted="#63646D",
                accent="#1672D8", accent_fill="#1672D8",
                selection="#DCEBFF", gpu="#7656B9",
                ram="#25A474"),
    "night": dict(bg="#18191E", side="#222329", panel="#2B2C33", panel_2="#36373F",
                  border="#454650", text="#F5F5F7", muted="#B5B6C0",
                  accent="#70BAFF", accent_fill="#176FCB",
                  selection="#244A77", gpu="#B5A6FF",
                  ram="#69D9A6"),
}
DEFAULTS = dict(
    city="Москва", latitude=55.7558, longitude=37.6173,
    rotation=90, brightness=100, night_brightness=18, night_mode=True,
    night_start=23, night_end=8, auto_pages=False, context_auto=True, page_seconds=20,
    port="AUTO", usb_enabled=True, cpu_alert=90, gpu_alert=85,
    page="overview", theme="auto", brand_name="Мой компьютер",
)
BG = UI_PALETTES["night"]["bg"]
PANEL = UI_PALETTES["night"]["panel"]
PANEL_2 = UI_PALETTES["night"]["panel_2"]
BORDER = UI_PALETTES["night"]["border"]
TEXT = UI_PALETTES["night"]["text"]
MUTED = UI_PALETTES["night"]["muted"]
ACCENT = UI_PALETTES["night"]["accent"]


def select_overview_content(metrics: dict, media: dict) -> dict | None:
    """Choose live overview content: foreground game, playing track, weather."""
    fps = metrics.get("fps")
    if (metrics.get("game_detected") is True and
            isinstance(fps, (int, float)) and not isinstance(fps, bool) and
            math.isfinite(fps) and fps > 0):
        return {
            "kind": "gaming",
            "fps": fps,
            "frametime_ms": metrics.get("frametime_ms"),
            "process_name": metrics.get("process_name"),
            "cpu_temp": metrics.get("cpu_temp"),
            "gpu_temp": metrics.get("gpu_temp"),
            "cpu_percent": metrics.get("cpu_percent"),
            "gpu_percent": metrics.get("gpu_percent"),
        }
    if media.get("is_playing") is True and media.get("title"):
        return {
            "kind": "media",
            "title": media["title"],
            "artist": media.get("artist"),
            "album": media.get("album"),
            "thumbnail_bytes": media.get("thumbnail_bytes"),
        }
    return None


def normalize_brand_name(raw: object) -> str:
    if not isinstance(raw, str):
        return DEFAULTS["brand_name"]
    clean = " ".join("".join(ch if ch.isprintable() else " " for ch in raw).split())[:32].strip()
    return clean or DEFAULTS["brand_name"]


def _set_ui_palette(theme: str) -> None:
    global BG, PANEL, PANEL_2, BORDER, TEXT, MUTED, ACCENT
    palette = UI_PALETTES[theme]
    BG, PANEL, PANEL_2 = palette["bg"], palette["panel"], palette["panel_2"]
    BORDER, TEXT, MUTED, ACCENT = (palette["border"], palette["text"],
                                  palette["muted"], palette["accent"])


def load_settings(path: Path) -> dict:
    settings = DEFAULTS.copy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            settings.update({key: item for key, item in raw.items() if key in DEFAULTS})
    except (OSError, ValueError):
        pass
    bounds = (
        ("brightness", 1, 100), ("night_brightness", 1, 100),
        ("page_seconds", 5, 300), ("night_start", 0, 23),
        ("night_end", 0, 23), ("cpu_alert", 50, 110), ("gpu_alert", 50, 110),
    )
    for key, low, high in bounds:
        try:
            settings[key] = max(low, min(high, int(settings[key])))
        except (ValueError, TypeError, OverflowError):
            settings[key] = DEFAULTS[key]
    if settings["rotation"] not in (0, 90, 180, 270):
        settings["rotation"] = 90
    for key in ("night_mode", "auto_pages", "context_auto", "usb_enabled"):
        if not isinstance(settings[key], bool):
            settings[key] = DEFAULTS[key]
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
        try:
            settings[key] = float(settings[key])
            if not low <= settings[key] <= high:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            settings[key] = DEFAULTS[key]
    for key in ("city", "port"):
        if not isinstance(settings[key], str) or not settings[key].strip():
            settings[key] = DEFAULTS[key]
    if not isinstance(settings["page"], str) or settings["page"] not in PAGES:
        settings["page"] = DEFAULTS["page"]
    if settings["theme"] in ("aurora", "ember", "ice"):
        settings["theme"] = "night"
    if not isinstance(settings["theme"], str) or settings["theme"] not in THEMES:
        settings["theme"] = DEFAULTS["theme"]
    settings["brand_name"] = normalize_brand_name(settings["brand_name"])
    return settings


def night_active(settings: dict, hour: int | None = None) -> bool:
    hour = datetime.now().hour if hour is None else hour
    start, end = settings["night_start"], settings["night_end"]
    night = (hour >= start or hour < end) if start > end else start <= hour < end
    return settings["night_mode"] and night


def effective_brightness(settings: dict, hour: int | None = None) -> int:
    return settings["night_brightness"] if night_active(settings, hour) else settings["brightness"]


def effective_theme(settings: dict, hour: int | None = None) -> str:
    """Resolve the optional automatic theme using the brightness night schedule."""
    chosen = settings.get("theme", "auto")
    if chosen in ("day", "night"):
        return chosen
    return "night" if night_active(settings, hour) else "day"


def desktop_gb(amount: object) -> str:
    """Format binary gigabytes with the Russian decimal separator."""
    return value(amount, "", 1).replace(".", ",")


def save_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def start_sensor_host(output: Path) -> bool:
    """Elevate only the read-only LHM helper; keep the desktop process normal."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        if not pythonw.is_file():
            pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            raise FileNotFoundError("pythonw.exe не найден")
        created = psutil.Process(os.getpid()).create_time()
        parameters = subprocess.list2cmdline([
            str(ROOT / "sensor_host.py"), "--output", str(output),
            "--parent-pid", str(os.getpid()), "--parent-created", repr(created),
        ])
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                       ctypes.c_wchar_p, ctypes.c_wchar_p,
                                       ctypes.c_wchar_p, ctypes.c_int]
        shell.ShellExecuteW.restype = ctypes.c_void_p
        result = shell.ShellExecuteW(None, "runas", str(pythonw), parameters,
                                     str(ROOT), 0)
        if not result or result <= 32:
            logging.warning("Помощник датчиков не запущен (код ShellExecute %s)", result)
            return False
        logging.info("Запрошен запуск помощника датчиков с правами администратора")
        return True
    except (OSError, psutil.Error):
        logging.exception("Не удалось запустить помощник датчиков")
        return False


class App:
    def __init__(self, root: tk.Tk, args: argparse.Namespace, show_event: int | None = None):
        self.root = root
        self.args = args
        self.closed = False
        self.events: queue.Queue = queue.Queue(maxsize=128)
        self._sample_lock = threading.Lock()
        self._pending_sample = None
        self._pending_media = None
        self.stop_event = threading.Event()
        self.settings = load_settings(DATA / "settings.json")
        self._ui_theme = effective_theme(self.settings)
        _set_ui_palette(self._ui_theme)
        self.metrics: dict = {}
        self.media: dict = {}
        self.last_media_sample = 0.0
        self.history = {key: deque(maxlen=60) for key in
                        ("cpu_percent", "gpu_percent", "cpu_temp", "gpu_temp")}
        self.page = self.settings["page"]
        self.page_changed = time.monotonic()
        self.started_at = time.monotonic()
        self.last_sample = 0.0
        self.sensor_error: str | None = None
        self.last_alert = 0.0
        self._last_display_message = None
        self.alert = ""
        self.tray = None
        self._render_pending = False
        self._photo = None
        self._preview_mode = "fit"
        self._show_event = show_event
        self.weather = WeatherService(DATA / "weather.json")
        self.weather.set_location(self.settings["city"], self.settings["latitude"],
                                  self.settings["longitude"])
        brightness = effective_brightness(self.settings)
        self.display = DisplayWorker(port=self.settings["port"],
                                     rotation=self.settings["rotation"],
                                     brightness=brightness)
        self.last_brightness = brightness
        self.build_ui()
        self._sensor_host_started = False
        if not args.preview:
            self._sensor_host_started = start_sensor_host(DATA / "hardware.json")
        if self.settings["usb_enabled"] and not args.preview:
            self.display.start()
        threading.Thread(target=self.sample_loop, daemon=True, name="Sensors").start()
        threading.Thread(target=self.weather_loop, daemon=True, name="Weather").start()
        threading.Thread(target=self.media_loop, daemon=True, name="Media").start()
        if show_event is not None:
            threading.Thread(target=self._watch_show_event, daemon=True,
                             name="Show existing Aurora").start()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<Left>", lambda event: self.change_page(-1))
        self.root.bind("<Right>", lambda event: self.change_page(1))
        self.root.bind("<Escape>", lambda event: self.hide())
        self.start_tray()
        self.root.after(100, self.tick)

    def _watch_show_event(self) -> None:
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        while not self.stop_event.is_set():
            result = kernel.WaitForSingleObject(self._show_event, 500)
            if result == 0 and not self.stop_event.is_set():
                self.events.put(("show", None))
            elif result == 0xFFFFFFFF:
                break

    def build_ui(self) -> None:
        root = self.root
        root.title(f"{self.settings['brand_name']} · Монитор компьютера")
        if ICON_ICO.is_file():
            root.iconbitmap(default=str(ICON_ICO))
        root.configure(bg=BG)
        root.geometry("1260x800")
        root.minsize(920, 620)

        self.ui_style = ttk.Style(root)
        self.ui_style.theme_use("clam")
        self._configure_styles()

        shell = tk.Frame(root, bg=BG)
        shell.pack(fill="both", expand=True)
        side = UI_PALETTES[self._ui_theme]["side"]
        sidebar = tk.Frame(shell, bg=side, width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.brand_label = tk.Label(sidebar, text="", bg=side, fg=TEXT,
                                    justify="left", anchor="w", wraplength=152)
        self.brand_label.pack(fill="x", padx=22, pady=(25, 3))
        self._apply_brand()
        tk.Label(sidebar, text="Монитор ПК", bg=side, fg=MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=23, pady=(0, 28))
        self.tabs: dict[str, SidebarButton] = {}
        icon_colors = dict(overview="#2F80ED", graphs="#7B6EF6", thermal="#ED6B75",
                           weather="#48A9DA", clock="#F1A84C", media="#B875D9",
                           gaming="#53A985")
        for key, title in PAGES.items():
            button = SidebarButton(sidebar, text=title,
                                   command=lambda selected=key: self.set_page(selected),
                                   palette=UI_PALETTES[self._ui_theme], icon=key,
                                   icon_color=icon_colors[key])
            button.pack(fill="x", padx=9, pady=2)
            self.tabs[key] = button
        tk.Frame(sidebar, bg=side).pack(fill="both", expand=True)
        PillButton(sidebar, text="В трей", command=self.hide,
                   palette=UI_PALETTES[self._ui_theme], variant="glass",
                   width=158, corner=side).pack(padx=16, pady=(0, 8))
        PillButton(sidebar, text="Завершить", command=self.close,
                   palette=UI_PALETTES[self._ui_theme], variant="quiet",
                   width=158, corner=side).pack(padx=16, pady=(0, 18))

        content = tk.Frame(shell, bg=BG)
        content.pack(side="left", fill="both", expand=True)
        toolbar_panel = RoundedPanel(content, palette=UI_PALETTES[self._ui_theme],
                                     width=700, height=72, radius=19)
        toolbar_panel.pack(fill="x", padx=(14, 16), pady=(14, 10))
        toolbar = toolbar_panel.inner
        title_box = tk.Frame(toolbar, bg=PANEL)
        title_box.pack(side="left", fill="x", expand=True, padx=(16, 0))
        self.page_title = tk.Label(title_box, bg=PANEL, fg=TEXT, anchor="w",
                                   font=("Segoe UI", 17, "bold"))
        self.page_title.pack(fill="x")
        PillButton(toolbar, text="Настройки", command=self.settings_window,
                   palette=UI_PALETTES[self._ui_theme], variant="glass",
                   width=106, corner=PANEL).pack(side="right", padx=(4, 12))
        self.scale_button = PillButton(toolbar, text="1:1 пиксели",
                                       command=self.toggle_preview_scale,
                                       palette=UI_PALETTES[self._ui_theme],
                                       variant="glass", width=109, corner=PANEL)
        self.scale_button.pack(side="right", padx=4)

        body = tk.Frame(content, bg=BG)
        body.pack(fill="both", expand=True, padx=(14, 16), pady=(0, 8))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        preview_surface = RoundedPanel(body, palette=UI_PALETTES[self._ui_theme],
                                       width=400, radius=22)
        preview_surface.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        preview_panel = preview_surface.inner
        preview_panel.grid_rowconfigure(1, weight=1)
        preview_panel.grid_columnconfigure(0, weight=1)
        tk.Label(preview_panel, text="Экран", bg=PANEL, fg=TEXT,
                 anchor="w", font=("Segoe UI", 12, "bold")).grid(
                     row=0, column=0, sticky="ew", padx=14, pady=(9, 6))
        self.preview_canvas = tk.Canvas(preview_panel, bg=PANEL, bd=0,
                                        highlightthickness=0, relief="flat")
        self.preview_canvas.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 7))
        self.preview_canvas.bind("<Configure>", lambda event: self._request_render())
        self.preview_hbar = SlimScrollbar(preview_panel, orient="horizontal",
                                           command=self.preview_canvas.xview,
                                           palette=UI_PALETTES[self._ui_theme])
        self.preview_vbar = SlimScrollbar(preview_panel, orient="vertical",
                                           command=self.preview_canvas.yview,
                                           palette=UI_PALETTES[self._ui_theme])
        self.preview_canvas.configure(xscrollcommand=self.preview_hbar.set,
                                      yscrollcommand=self.preview_vbar.set)
        self.preview_image_id = self.preview_canvas.create_image(0, 0, anchor="nw")

        details_surface = RoundedPanel(body, palette=UI_PALETTES[self._ui_theme],
                                       width=292, radius=22)
        details_surface.grid(row=0, column=1, sticky="ns")
        details_panel = details_surface.inner
        details_panel.grid_rowconfigure(1, weight=1)
        details_panel.grid_columnconfigure(0, weight=1)
        tk.Label(details_panel, text="Показатели", bg=PANEL, fg=TEXT,
                 anchor="w", font=("Segoe UI", 12, "bold")).grid(
                     row=0, column=0, sticky="ew", padx=10, pady=(9, 8))
        self.details_canvas = tk.Canvas(details_panel, width=265, bg=PANEL,
                                        bd=0, highlightthickness=0)
        self.details_canvas.grid(row=1, column=0, sticky="nsew")
        scroll = SlimScrollbar(details_panel, orient="vertical",
                                command=self.details_canvas.yview,
                                palette=UI_PALETTES[self._ui_theme])
        scroll.grid(row=1, column=1, sticky="ns")
        self.details_canvas.configure(yscrollcommand=scroll.set)
        self.details_inner = tk.Frame(self.details_canvas, bg=PANEL)
        self.details_window = self.details_canvas.create_window(
            (0, 0), window=self.details_inner, anchor="nw")
        self.details_inner.bind("<Configure>", lambda event: self.details_canvas.configure(
            scrollregion=self.details_canvas.bbox("all")))
        self.details_canvas.bind("<Configure>", lambda event: self.details_canvas.itemconfigure(
            self.details_window, width=event.width))
        self._build_details()

        footer = tk.Frame(content, bg=BG, height=38)
        footer.pack(fill="x", side="bottom")
        self.status = tk.Label(footer, text="Запуск…", bg=BG, fg=MUTED,
                               anchor="w", font=("Segoe UI", 10))
        self.status.pack(side="left", fill="x", expand=True, padx=24, pady=7)
        self._paint_navigation()

    def _configure_styles(self) -> None:
        style = self.ui_style
        style.configure("Aurora.TButton", background=PANEL_2, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=PANEL_2, darkcolor=PANEL_2,
                        padding=(14, 9), font=("Segoe UI", 11))
        style.map("Aurora.TButton", background=[("active", UI_PALETTES[self._ui_theme]["selection"])],
                  foreground=[("active", TEXT)])
        style.configure("Aurora.TCheckbutton", background=PANEL, foreground=TEXT,
                        font=("Segoe UI", 11))
        style.map("Aurora.TCheckbutton", background=[("active", PANEL)])
        style.configure("Aurora.TNotebook", background=BG, borderwidth=0,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("Aurora.TNotebook.Tab", background=PANEL_2, foreground=MUTED,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        padding=(17, 10), font=("Segoe UI", 11))
        style.map("Aurora.TNotebook.Tab", background=[("selected", UI_PALETTES[self._ui_theme]["selection"])],
                  foreground=[("selected", TEXT)])
        for name in ("TCombobox", "TSpinbox", "TEntry"):
            style.configure(name, fieldbackground=PANEL_2, background=PANEL_2,
                            foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                            lightcolor=PANEL_2, darkcolor=PANEL_2,
                            relief="flat", padding=(11, 8))
            style.map(name, fieldbackground=[("readonly", PANEL_2)],
                      background=[("readonly", PANEL_2), ("active", PANEL_2)],
                      arrowcolor=[("active", ACCENT)],
                      foreground=[("readonly", TEXT)],
                      selectbackground=[("!disabled", UI_PALETTES[self._ui_theme]["selection"])],
                      selectforeground=[("!disabled", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL_2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)

    def _apply_brand(self) -> None:
        name = self.settings["brand_name"]
        self.root.title(f"{name} · Монитор компьютера")
        if not hasattr(self, "brand_label"):
            return
        available = 140
        size = 21
        while size > 11 and tkfont.Font(root=self.root, family="Segoe UI",
                                       size=size, weight="bold").measure(name) > available:
            size -= 1
        face = tkfont.Font(root=self.root, family="Segoe UI", size=size,
                           weight="bold")
        displayed = name
        if face.measure(displayed) > available:
            while displayed and face.measure(displayed + "…") > available:
                displayed = displayed[:-1]
            displayed += "…"
        self.brand_label.configure(text=displayed, font=("Segoe UI", size, "bold"),
                                   wraplength=0, height=1)
        if self.tray:
            try:
                self.tray.title = f"{name} · Монитор"
            except Exception:
                logging.debug("Не удалось обновить название в трее", exc_info=True)

    def _make_tray_image(self) -> Image.Image:
        return icon_image(64)

    def apply_theme(self, theme: str) -> None:
        """Recolor the live Tk tree for a resolved manual or scheduled theme."""
        if theme not in UI_PALETTES:
            raise ValueError("Неизвестная тема")
        old_palette = UI_PALETTES[self._ui_theme]
        new_palette = UI_PALETTES[theme]
        old_to_new = {old_palette[key].lower(): new_palette[key]
                      for key in old_palette}
        self._ui_theme = theme
        _set_ui_palette(theme)
        self._configure_styles()

        options = ("background", "foreground", "activebackground",
                   "activeforeground", "highlightbackground", "highlightcolor",
                   "troughcolor", "selectbackground", "selectforeground",
                   "insertbackground")

        def recolor(widget: tk.Misc) -> None:
            if isinstance(widget, (PillButton, SidebarButton, RoundedPanel, SlimScrollbar,
                                   SmoothScale, ToggleSwitch, SelectField, StepperField, TextField)):
                previous_corner = getattr(widget, "corner", old_palette["bg"])
                widget.set_palette(new_palette,
                                   corner=old_to_new.get(previous_corner.lower(),
                                                         new_palette["bg"]))
            elif not isinstance(widget, ttk.Widget):
                for option in options:
                    try:
                        current = widget.cget(option)
                        replacement = old_to_new.get(str(current).lower())
                        if replacement:
                            widget.configure(**{option: replacement})
                    except tk.TclError:
                        pass
            for child in widget.winfo_children():
                recolor(child)

        recolor(self.root)
        self._paint_navigation()
        new_icons = []
        for key, label in getattr(self, "_detail_icon_labels", {}).items():
            photo = self._make_detail_icon(key)
            label.configure(image=photo)
            new_icons.append(photo)
        self._detail_icons = new_icons
        if self.tray:
            try:
                self.tray.icon = self._make_tray_image()
            except Exception:
                logging.debug("Не удалось обновить значок трея", exc_info=True)
        self._request_render()

    def _make_detail_icon(self, key: str) -> ImageTk.PhotoImage:
        painter = Painter(40, 40, self._ui_theme)
        painter.im.paste(PANEL, (0, 0, 80, 80))
        hardware_badge(painter, 0, 0, "ram" if key == "memory" else key,
                       UI_PALETTES[self._ui_theme]["gpu"] if key == "gpu" else
                       UI_PALETTES[self._ui_theme]["ram"] if key == "memory" else ACCENT,
                       40)
        return ImageTk.PhotoImage(painter.finish())

    def _build_details(self) -> None:
        self.detail_labels: dict[str, tk.Label] = {}
        self._detail_icons = []
        self._detail_icon_labels = {}

        def card(key: str, heading: str, *, prominent: bool = False) -> tk.Frame:
            box = tk.Frame(self.details_inner, bg=PANEL,
                           highlightthickness=0)
            box.pack(fill="x", padx=6, pady=(0, 10))
            tk.Frame(box, bg=BORDER, height=1).pack(side="bottom", fill="x", padx=14)
            heading_row = tk.Frame(box, bg=PANEL)
            heading_row.pack(fill="x", padx=14, pady=(10, 2))
            if key in ("memory", "cpu", "gpu"):
                photo = self._make_detail_icon(key)
                self._detail_icons.append(photo)
                icon_label = tk.Label(heading_row, image=photo, bg=PANEL)
                icon_label.pack(side="left", padx=(0, 9))
                self._detail_icon_labels[key] = icon_label
            tk.Label(heading_row, text=heading.capitalize(), bg=PANEL, fg=MUTED, anchor="w",
                     font=("Segoe UI", 10)).pack(side="left", fill="x")
            label = tk.Label(box, text="—", bg=PANEL,
                             fg=ACCENT if prominent else TEXT, anchor="w",
                             justify="left", wraplength=240,
                             font=("Segoe UI", 24 if prominent else 11,
                                   "bold" if prominent else "normal"))
            label.pack(fill="x", padx=14, pady=(0, 13))
            label.bind("<Configure>", lambda event, target=label: target.configure(
                wraplength=max(100, event.width - 4)))
            self.detail_labels[key] = label
            return box

        card("cpu", "ПРОЦЕССОР")
        card("gpu", "ВИДЕОКАРТА")
        memory_box = card("memory", "ПАМЯТЬ", prominent=True)
        self.memory_amount_label = tk.Label(memory_box, text="—", bg=PANEL,
                                            fg=MUTED, anchor="w", justify="left",
                                            wraplength=240,
                                            font=("Segoe UI", 10))
        self.memory_amount_label.pack(fill="x", padx=14, pady=(0, 13))
        self.memory_amount_label.bind("<Configure>", lambda event: self.memory_amount_label.configure(
            wraplength=max(100, event.width - 4)))
        card("disk", "СИСТЕМНЫЙ ДИСК")
        card("network", "СЕТЬ")
        card("sensors", "ДАТЧИКИ")

        def wheel(event: tk.Event) -> None:
            self.details_canvas.yview_scroll(-int(event.delta / 120), "units")

        self.details_canvas.bind("<MouseWheel>", wheel)
        self.details_inner.bind("<MouseWheel>", wheel)
        for box in self.details_inner.winfo_children():
            box.bind("<MouseWheel>", wheel)
            for child in box.winfo_children():
                child.bind("<MouseWheel>", wheel)

    def sample_loop(self) -> None:
        try:
            telemetry = Telemetry(hardware_path=DATA / "hardware.json")
            try:
                from gaming import read_gaming
            except ImportError:
                read_gaming = lambda: {}
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    sample = telemetry.sample()
                    gaming = read_gaming()
                    sample["gaming_status"] = gaming.pop("status", None)
                    sample.update(gaming)
                    # Keep acquisition time with the event: queued data must not
                    # become artificially fresh when the UI eventually reads it.
                    with self._sample_lock:
                        self._pending_sample = (time.monotonic(), sample)
                except Exception:
                    logging.exception("Ошибка датчиков")
                    self.events.put(("sensor_error", "Не удалось обновить датчики"))
                self.stop_event.wait(max(.1, 1 - (time.monotonic() - started)))
        except Exception:
            logging.exception("Не удалось запустить датчики")
            self.events.put(("sensor_error", "Датчики не запущены"))

    def weather_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.weather.refresh()
            except Exception:
                logging.exception("Ошибка погоды")
            self.stop_event.wait(30)

    def media_loop(self) -> None:
        try:
            from media import read_media
        except ImportError:
            return
        while not self.stop_event.is_set():
            try:
                media = read_media()
                with self._sample_lock:
                    self._pending_media = (time.monotonic(), media)
            except Exception:
                logging.debug("Медиасессия недоступна", exc_info=True)
            self.stop_event.wait(5)

    def _paint_navigation(self) -> None:
        for key, button in self.tabs.items():
            button.set_selected(key == self.page)
        self.page_title.configure(text=PAGES[self.page])

    def set_page(self, page: str) -> None:
        if page not in PAGES or page == self.page:
            return
        self.page = page
        self.page_changed = time.monotonic()
        self.settings["page"] = page
        try:
            save_json(DATA / "settings.json", self.settings)
        except OSError:
            logging.exception("Не удалось сохранить выбранную страницу")
        self._paint_navigation()
        self._request_render()

    def change_page(self, direction: int) -> None:
        pages = list(PAGES)
        self.set_page(pages[(pages.index(self.page) + direction) % len(pages)])

    def _next_auto_page(self) -> str:
        pages = list(PAGES)
        index = pages.index(self.page)
        for offset in range(1, len(pages) + 1):
            candidate = pages[(index + offset) % len(pages)]
            if candidate == "media" and not (
                    self.media.get("title") and self.media.get("is_playing") is True and
                    time.monotonic() - getattr(self, "last_media_sample", 0.0) <= 15):
                continue
            if candidate == "gaming":
                content = select_overview_content(self._current_metrics()[0], {})
                if content is None or content["kind"] != "gaming":
                    continue
            if candidate == "thermal" and all(self.metrics.get(k) is None
                                                for k in ("cpu_temp", "gpu_temp")):
                continue
            if candidate == "weather" and self.weather.snapshot().get("temperature") is None:
                continue
            return candidate
        return "overview"

    def toggle_preview_scale(self) -> None:
        self._preview_mode = "pixels" if self._preview_mode == "fit" else "fit"
        self.scale_button.configure(text="Вписать" if self._preview_mode == "pixels"
                                    else "1:1 пиксели")
        self._request_render()

    def _request_render(self) -> None:
        if self.closed or self._render_pending:
            return
        self._render_pending = True
        self.root.after_idle(self._render_once)

    def tick(self) -> None:
        if self.closed:
            return
        if not self.args.preview and (DATA / 'shutdown.request').exists():
            (DATA / 'shutdown.request').unlink(missing_ok=True)
            self.close()
            return
        more_events = False
        try:
            with self._sample_lock:
                latest_sample = self._pending_sample
                self._pending_sample = None
                if self._pending_media is not None:
                    self.last_media_sample, self.media = self._pending_media
                    self._pending_media = None
            for _ in range(256):
                try:
                    kind, data = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "sample":
                    latest_sample = data
                elif kind == "media":
                    self.media = data or {}
                    self.last_media_sample = time.monotonic()
                elif kind == "show":
                    self.show()
                elif kind == "quit":
                    self.close()
                    return
                elif kind == "cities":
                    callback, results = data
                    callback(results)
                elif kind == "sensor_error":
                    self.sensor_error = data
                    logging.warning(data)
            else:
                more_events = True
            if latest_sample is not None:
                acquired_at, sample = latest_sample
                if acquired_at >= self.last_sample:
                    gap = min(60, max(0, int(acquired_at - self.last_sample) - 1)) if self.last_sample else 0
                    for key in self.history:
                        self.history[key].extend([None] * gap)
                    self.metrics = sample
                    self.last_sample = acquired_at
                    self.sensor_error = None
                    for key in self.history:
                        self.history[key].append(sample.get(key))
                    if time.monotonic() - acquired_at <= 5:
                        self.check_alerts()
                    else:
                        self.alert = ""
            if (self.settings["auto_pages"] and
                    time.monotonic() - self.page_changed >= self.settings["page_seconds"]):
                self.set_page(self._next_auto_page())
            brightness = effective_brightness(self.settings)
            if brightness != self.last_brightness:
                self.display.configure(self.settings["port"],
                                       self.settings["rotation"], brightness)
                self.last_brightness = brightness
            wanted_theme = effective_theme(self.settings)
            if wanted_theme != self._ui_theme:
                self.apply_theme(wanted_theme)
            self._request_render()
        except Exception:
            logging.exception("Ошибка интерфейса")
        finally:
            if not self.closed:
                self.root.after(0 if more_events else 1000, self.tick)

    def _current_metrics(self) -> tuple[dict, bool]:
        metrics = self.metrics.copy()
        now = time.monotonic()
        stale = bool((self.last_sample and now - self.last_sample > 5) or
                     (not self.last_sample and now - self.started_at > 5))
        if stale:
            for key in ("cpu_percent", "cpu_temp", "cpu_mhz", "gpu_percent",
                        "gpu_temp", "gpu_memory_used_mb", "gpu_power_w",
                        "gpu_clock_mhz", "ram_used_gb", "ram_percent",
                        "disk_used_gb", "disk_percent", "net_down_bps",
                        "net_up_bps", "fan_rpm", "cpu_fan_rpm", "gpu_fan_rpm",
                        "pump_fan_rpm", "case_fan_rpm", "cpu_effective_mhz",
                        "helper_age_seconds", "uptime_seconds", "fps",
                        "frametime_ms"):
                metrics[key] = None
            metrics["fan_candidates"] = []
            metrics["game_detected"] = False
        return metrics, stale

    def _render_once(self) -> None:
        self._render_pending = False
        if self.closed:
            return
        try:
            metrics, stale = self._current_metrics()
            media = (self.media if time.monotonic() - getattr(self, "last_media_sample", 0.0) <= 15
                     else {})
            frame = render(metrics, self.weather.snapshot(), self.history,
                           self.page, self.settings["rotation"],
                           alert=(self.sensor_error or "Нет свежих данных датчиков")
                           if stale else self.alert,
                           media=media, theme=self._ui_theme,
                           brand_name=self.settings["brand_name"],
                           overview_content=(select_overview_content(metrics, media)
                                             if self.settings["context_auto"] else None))
            if self.settings["usb_enabled"] and not self.args.preview:
                self.display.submit(frame)
            if self.root.state() != "withdrawn":
                self._draw_preview(frame)
                self._update_details(metrics)
            brightness = effective_brightness(self.settings)
            state = self.display.status
            message = ("Предпросмотр · USB выключен" if self.args.preview or
                       not self.settings["usb_enabled"] else
                       state.get("message", "Подключение USB…"))
            if message != self._last_display_message:
                logging.info("Экран: %s; порт: %s", message, state.get("port"))
                self._last_display_message = message
            if stale:
                message += "   ·   " + (self.sensor_error or "Датчики не обновляются")
            mode = "Ночная яркость" if night_active(self.settings) else "Яркость"
            self.status.configure(text=f"{message}   ·   {mode} {brightness}%")
        except Exception:
            logging.exception("Не удалось обновить экран")
            self.status.configure(text="Ошибка обновления · подробности в журнале")
            # Never leave old temperatures looking live when the main renderer
            # fails. This independent Pillow path has no provider dependencies.
            try:
                size = (480, 320) if self.settings["rotation"] in (90, 270) else (320, 480)
                error_frame = Image.new("RGB", size, "#28151F")
                draw = ImageDraw.Draw(error_frame)
                font_path = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf")
                error_font = ImageFont.truetype(font_path, 23)
                draw.text((18, 80), "Нет свежего изображения", font=error_font, fill="#FFF0F3")
                draw.text((18, 125), "Ошибка обновления.", font=error_font, fill="#FF9BAA")
                draw.text((18, 170), "Проверьте журнал монитора.", font=error_font, fill="#FFF0F3")
                if self.settings["usb_enabled"] and not self.args.preview:
                    self.display.submit(error_frame)
                if self.root.state() != "withdrawn":
                    self._draw_preview(error_frame)
            except Exception:
                logging.exception("Не удалось показать состояние ошибки")

    def _draw_preview(self, frame: Image.Image) -> None:
        canvas = self.preview_canvas
        canvas_width = max(1, canvas.winfo_width())
        canvas_height = max(1, canvas.winfo_height())
        if self._preview_mode == "fit":
            factor = min(canvas_width / frame.width, canvas_height / frame.height, 2.0)
            image_size = (max(1, int(frame.width * factor)),
                          max(1, int(frame.height * factor)))
            shown = frame.resize(image_size, Image.Resampling.LANCZOS)
        else:
            shown = frame
        self._photo = ImageTk.PhotoImage(shown)
        canvas.itemconfigure(self.preview_image_id, image=self._photo)
        x = max(0, (canvas_width - shown.width) // 2)
        y = max(0, (canvas_height - shown.height) // 2)
        canvas.coords(self.preview_image_id, x, y)
        scroll_width = max(canvas_width, shown.width)
        scroll_height = max(canvas_height, shown.height)
        canvas.configure(scrollregion=(0, 0, scroll_width, scroll_height))
        if self._preview_mode == "pixels" and shown.width > canvas_width:
            self.preview_hbar.grid(row=2, column=0, sticky="ew", padx=(12, 0))
        else:
            self.preview_hbar.grid_remove()
        if self._preview_mode == "pixels" and shown.height > canvas_height:
            self.preview_vbar.grid(row=1, column=1, sticky="ns", pady=(0, 12))
        else:
            self.preview_vbar.grid_remove()

    def _update_details(self, metrics: dict) -> None:
        labels = self.detail_labels
        memory = metrics.get("ram_percent")
        labels["memory"].configure(text=(value(memory, "%") if memory is not None else "—"))
        memory_lines = [
            f"{desktop_gb(metrics.get('ram_used_gb'))} / "
            f"{desktop_gb(metrics.get('ram_total_gb'))} ГБ"]
        if metrics.get("ram_speed_mt") is not None:
            memory_lines.append(
                f"{metrics.get('ram_type') or 'ОЗУ'}  ·  "
                f"{value(metrics.get('ram_speed_mt'), ' МГц')}")
        if metrics.get("ram_installed_gb") is not None:
            memory_lines.append(f"Установлено: {desktop_gb(metrics.get('ram_installed_gb'))} ГБ")
        if metrics.get("ram_module_count") is not None:
            memory_lines.append(
                f"Модулей: {value(metrics.get('ram_module_count'))}"
                + (f"  ·  {metrics['ram_model']}" if metrics.get("ram_model") else ""))
        self.memory_amount_label.configure(text="\n".join(memory_lines))
        cpu_name=metrics.get('cpu_name')
        gpu_name=metrics.get('gpu_name')
        cpu_title=(f"{hardware_vendor(cpu_name)} {short_model(cpu_name,'cpu')}".strip()
                   if cpu_name else 'Определяется…')
        gpu_title=(f"{hardware_vendor(gpu_name)} {short_model(gpu_name,'gpu')}".strip()
                   if gpu_name else 'Определяется…')
        cpu_source = metrics.get("cpu_source")
        cpu_source_label = {
            "Windows PDH Processor Utility": "счётчик Windows",
            "psutil busy time": "системное время CPU",
        }.get(cpu_source, "—")
        labels["cpu"].configure(text=(f"{cpu_title}\n"
                                      f"Нагрузка {value(metrics.get('cpu_percent'), '%')}  ·  "
                                      f"{value(metrics.get('cpu_mhz'), ' МГц')}\n"
                                      f"Эфф. частота: "
                                      f"{value(metrics.get('cpu_effective_mhz'), ' МГц')}\n"
                                      f"Источник: {cpu_source_label}"))
        gpu_used = metrics.get("gpu_memory_used_mb")
        gpu_total = metrics.get("gpu_memory_total_mb")
        gpu_used_gb = gpu_used / 1024 if isinstance(gpu_used, (int, float)) else None
        gpu_total_gb = gpu_total / 1024 if isinstance(gpu_total, (int, float)) else None
        labels["gpu"].configure(text=(f"{gpu_title}\n"
                                      f"Нагрузка {value(metrics.get('gpu_percent'), '%')}\n"
                                      f"Память {desktop_gb(gpu_used_gb)} / "
                                      f"{desktop_gb(gpu_total_gb)} ГБ\n"
                                      f"Частота {value(metrics.get('gpu_clock_mhz'), ' МГц')}"))
        labels["disk"].configure(text=(f"{desktop_gb(metrics.get('disk_used_gb'))} / "
                                       f"{desktop_gb(metrics.get('disk_total_gb'))} ГБ  ·  "
                                       f"{value(metrics.get('disk_percent'), '%')}"))
        labels["network"].configure(text=(f"↓ {speed(metrics.get('net_down_bps'))}\n"
                                          f"↑ {speed(metrics.get('net_up_bps'))}"))
        labels["sensors"].configure(text=(
            f"CPU {value(metrics.get('cpu_temp'), '°C')}  ·  "
            f"GPU {value(metrics.get('gpu_temp'), '°C')}\n"
            f"Вентилятор CPU {value(metrics.get('cpu_fan_rpm'), ' rpm')}\n"
            f"Вентилятор GPU {value(metrics.get('gpu_fan_rpm'), ' rpm')}\n"
            f"Помпа {value(metrics.get('pump_fan_rpm'), ' rpm')}  ·  "
            f"Корпус {value(metrics.get('case_fan_rpm'), ' rpm')}\n"
            f"Мощность GPU {value(metrics.get('gpu_power_w'), ' Вт', 1)}"
            + ("\n\n" + str(metrics["sensor_hint"])
               if metrics.get("sensor_hint") else "")
            + ("\n\nИсточник GPU: " + str(metrics["gpu_source"])
               if metrics.get("gpu_source") else "")))

    def check_alerts(self) -> None:
        warnings = []
        for key, label, threshold in (
            ("cpu_temp", "Процессор", self.settings["cpu_alert"]),
            ("gpu_temp", "Видеокарта", self.settings["gpu_alert"]),
        ):
            measurement = self.metrics.get(key)
            if isinstance(measurement, (int, float)) and measurement >= threshold:
                warnings.append(f"{label}: {measurement:.0f}°C — высокая температура")
        self.alert = " / ".join(warnings)
        if warnings and time.monotonic() - self.last_alert > 300:
            self.last_alert = time.monotonic()
            if self.tray:
                try:
                    self.tray.notify(self.alert, f"{self.settings['brand_name']} · Температура")
                except Exception:
                    pass

    def retry_sensor_host(self) -> None:
        """User-triggered retry; never start an elevation prompt in a loop."""
        if self.args.preview:
            return
        if (self.metrics.get("helper_age_seconds") is not None and
                self.metrics.get("sensor_status") == "ok"):
            return
        self._sensor_host_started = start_sensor_host(DATA / "hardware.json")
        self.status.configure(text=("Запуск помощника датчиков…" if self._sensor_host_started
                                    else "Помощник датчиков не запущен"))

    def settings_window(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(650, max(520, round(screen_width * .43)))
        height = min(670, max(540, round(screen_height * .72)))
        x = max(10, min(screen_width - width - 10,
                        self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2))
        y = max(10, min(screen_height - height - 10,
                        self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2))
        tab_heights = {"Экран": height, "Автоматика": min(height, 550),
                       "Погода": min(height, 510),
                       "Датчики и пороги": min(height, 620)}
        geometry = f"{width}x{height}+{x}+{y}"
        current = getattr(self, "settings_dialog", None)
        if current is not None and current.winfo_exists():
            current.geometry(geometry)
            current.lift()
            return
        win = tk.Toplevel(self.root)
        self.settings_dialog = win
        win.title(f"{self.settings['brand_name']} · Настройки")
        if ICON_ICO.is_file():
            win.iconbitmap(default=str(ICON_ICO))
        win.configure(bg=BG)
        win.geometry(geometry)
        win.resizable(False, False)
        win.transient(self.root)
        outer = tk.Frame(win, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(outer, text="Настройки", bg=BG, fg=TEXT,
                 font=("Segoe UI", 20, "bold"), anchor="w").pack(fill="x", pady=(0, 13))
        tab_strip = tk.Frame(outer, bg=BG)
        tab_strip.pack(fill="x", pady=(0, 12))
        book = tk.Frame(outer, bg=BG)
        book.pack(fill="both", expand=True)
        scroll_targets = {}
        tab_pages: dict[str, tk.Frame] = {}
        tab_buttons: dict[str, PillButton] = {}

        def activate_tab(name: str) -> None:
            for title, panel in tab_pages.items():
                if title == name:
                    panel.pack(fill="both", expand=True)
                else:
                    panel.pack_forget()
                tab_buttons[title].set_variant("accent" if title == name else "glass")
            win.geometry(f"{width}x{tab_heights[name]}+{x}+{y}")

        def page(name: str) -> tk.Frame:
            panel = tk.Frame(book, bg=BG)
            tab_pages[name] = panel
            button = PillButton(tab_strip, text=name,
                                command=lambda selected=name: activate_tab(selected),
                                palette=UI_PALETTES[self._ui_theme],
                                variant="accent" if len(tab_pages) == 1 else "glass",
                                width={"Экран": 96, "Автоматика": 130,
                                       "Погода": 96, "Датчики и пороги": 172}[name],
                                corner=BG)
            button.pack(side="left", padx=(0, 8))
            tab_buttons[name] = button
            if len(tab_pages) == 1:
                panel.pack(fill="both", expand=True)
            return panel

        def scroll_page(name: str) -> tk.Frame:
            panel = page(name)
            surface = RoundedPanel(panel, palette=UI_PALETTES[self._ui_theme],
                                   radius=20)
            surface.pack(fill="both", expand=True)
            canvas = tk.Canvas(surface.inner, bg=PANEL, bd=0, highlightthickness=0)
            scrollbar = SlimScrollbar(surface.inner, orient="vertical",
                                      command=canvas.yview,
                                      palette=UI_PALETTES[self._ui_theme])
            canvas.pack(side="left", fill="both", expand=True)
            canvas.configure(yscrollcommand=scrollbar.set)
            inner = tk.Frame(canvas, bg=PANEL)
            window = canvas.create_window((0, 0), window=inner, anchor="nw")
            def sync_scroll() -> None:
                bbox = canvas.bbox("all")
                canvas.configure(scrollregion=bbox)
                needs_scroll = bool(bbox and bbox[3] > canvas.winfo_height() + 2)
                if needs_scroll and not scrollbar.winfo_manager():
                    scrollbar.pack(side="right", fill="y")
                elif not needs_scroll and scrollbar.winfo_manager():
                    scrollbar.pack_forget()
                    canvas.yview_moveto(0)
            inner.bind("<Configure>", lambda event: canvas.after_idle(sync_scroll))
            def resize_canvas(event) -> None:
                canvas.itemconfigure(window, width=event.width)
                canvas.after_idle(sync_scroll)
            canvas.bind("<Configure>", resize_canvas)
            scroll_targets[inner] = canvas
            return inner

        screen = scroll_page("Экран")
        automation_page = scroll_page("Автоматика")
        weather_page = scroll_page("Погода")
        alerts_page = scroll_page("Датчики и пороги")

        def scroll_settings(event):
            widget = event.widget
            while widget is not None:
                if widget in scroll_targets:
                    scroll_targets[widget].yview_scroll(-int(event.delta / 120), "units")
                    return "break"
                widget = getattr(widget, "master", None)
        win.bind("<MouseWheel>", scroll_settings, add="+")

        def heading(parent: tk.Widget, caption: str) -> None:
            tk.Label(parent, text=caption, bg=PANEL, fg=TEXT,
                     anchor="w", font=("Segoe UI", 13, "bold")).pack(
                         fill="x", padx=20, pady=(17, 7))

        def small_label(parent: tk.Widget, caption: str) -> None:
            tk.Label(parent, text=caption, bg=PANEL, fg=MUTED,
                     anchor="w", font=("Segoe UI", 10)).pack(
                         fill="x", padx=20, pady=(10, 2))

        rotation = tk.StringVar(value=str(self.settings["rotation"]))
        theme = tk.StringVar(value=THEMES[self.settings["theme"]])
        brand_name = tk.StringVar(value=self.settings["brand_name"])
        brightness = tk.IntVar(value=self.settings["brightness"])
        night_brightness = tk.IntVar(value=self.settings["night_brightness"])
        night_mode = tk.BooleanVar(value=self.settings["night_mode"])
        context_auto = tk.BooleanVar(value=self.settings["context_auto"])
        auto_pages = tk.BooleanVar(value=self.settings["auto_pages"])
        usb_enabled = tk.BooleanVar(value=self.settings["usb_enabled"])
        night_start = tk.StringVar(value=str(self.settings["night_start"]))
        night_end = tk.StringVar(value=str(self.settings["night_end"]))
        page_seconds = tk.StringVar(value=str(self.settings["page_seconds"]))
        port = tk.StringVar(value=self.settings["port"])

        palette = UI_PALETTES[self._ui_theme]

        def row_label(parent: tk.Widget, caption: str) -> None:
            tk.Label(parent, text=caption, bg=PANEL, fg=MUTED,
                     anchor="w", font=("Segoe UI", 10)).pack(fill="x", pady=(0, 4))

        def switch_row(parent: tk.Widget, caption: str,
                       variable: tk.BooleanVar, top: int = 9) -> None:
            control_row = tk.Frame(parent, bg=PANEL)
            control_row.pack(fill="x", padx=18, pady=(top, 0))
            tk.Label(control_row, text=caption, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11), anchor="w").pack(side="left", fill="x", expand=True)
            ToggleSwitch(control_row, variable=variable, palette=palette).pack(side="right")

        heading(screen, "Экран и оформление")
        appearance = tk.Frame(screen, bg=PANEL)
        appearance.pack(fill="x", padx=18, pady=(5, 0))
        theme_column = tk.Frame(appearance, bg=PANEL)
        theme_column.pack(side="left", fill="x", expand=True, padx=(0, 12))
        row_label(theme_column, "Тема")
        SelectField(theme_column, variable=theme, values=list(THEMES.values()),
                    palette=palette).pack(fill="x")
        rotation_column = tk.Frame(appearance, bg=PANEL)
        rotation_column.pack(side="right")
        row_label(rotation_column, "Поворот")
        SelectField(rotation_column, variable=rotation,
                    values=["0", "90", "180", "270"], palette=palette,
                    width=110).pack()
        tk.Label(screen, text="Авто — день и ночь по заданным часам",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(
                     fill="x", padx=18, pady=(3, 0))
        small_label(screen, "Название на экране")
        TextField(screen, variable=brand_name, palette=palette).pack(fill="x", padx=18)

        brightness_row = tk.Frame(screen, bg=PANEL)
        brightness_row.pack(fill="x", padx=18, pady=(11, 0))
        brightness_row.grid_columnconfigure(0, weight=1, uniform="brightness")
        brightness_row.grid_columnconfigure(1, weight=1, uniform="brightness")
        for index, title, variable in (
            (0, "Днём", brightness),
            (1, "Ночью", night_brightness),
        ):
            column = tk.Frame(brightness_row, bg=PANEL)
            column.grid(row=0, column=index, sticky="ew",
                        padx=(0, 7) if index == 0 else (7, 0))
            row_label(column, title)
            SmoothScale(column, variable=variable, palette=palette).pack(fill="x")

        switch_row(screen, "Ночная яркость по расписанию", night_mode, 8)
        hours = tk.Frame(screen, bg=PANEL)
        hours.pack(fill="x", padx=18, pady=(4, 0))
        tk.Label(hours, text="С", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left")
        SelectField(hours, variable=night_start,
                    values=[str(i) for i in range(24)], palette=palette,
                    width=78).pack(side="left", padx=(8, 18))
        tk.Label(hours, text="До", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left")
        SelectField(hours, variable=night_end,
                    values=[str(i) for i in range(24)], palette=palette,
                    width=78).pack(side="left", padx=(8, 0))

        heading(automation_page, "Автоматический режим")
        switch_row(automation_page, "Игра или музыка вместо погоды", context_auto, 5)
        tk.Label(automation_page,
                 text="В обзоре: игра с FPS → играющий трек → прогноз погоды.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 10), anchor="w").pack(
                     fill="x", padx=18, pady=(4, 13))

        pages_row = tk.Frame(automation_page, bg=PANEL)
        pages_row.pack(fill="x", padx=18, pady=(13, 0))
        tk.Label(pages_row, text="Автосмена страниц", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11), anchor="w").pack(side="left", fill="x", expand=True)
        ToggleSwitch(pages_row, variable=auto_pages, palette=palette).pack(side="right")
        StepperField(pages_row, variable=page_seconds, minimum=5, maximum=300,
                     palette=palette).pack(side="right", padx=(0, 12))
        tk.Label(automation_page, text="Интервал в секундах", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9), anchor="e").pack(fill="x", padx=78)

        heading(automation_page, "Подключение")
        switch_row(automation_page, "Передавать кадр на USB-экран", usb_enabled, 5)
        port_row = tk.Frame(automation_page, bg=PANEL)
        port_row.pack(fill="x", padx=18, pady=(7, 15))
        tk.Label(port_row, text="Порт USB (AUTO — найти экран)", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 10), anchor="w").pack(side="left", fill="x", expand=True)
        TextField(port_row, variable=port, palette=palette).pack(side="right", fill="x", expand=True)

        heading(weather_page, "Местоположение")
        tk.Label(weather_page, text="Поиск города нужен только при его смене. Уже выбранный прогноз хранится локально.",
                 bg=PANEL, fg=MUTED, anchor="w", wraplength=440, justify="left",
                 font=("Segoe UI", 10)).pack(fill="x", padx=18)
        search = tk.StringVar(value=self.settings["city"])
        TextField(weather_page, variable=search, palette=palette).pack(
            fill="x", padx=18, pady=(14, 9))
        city_results: list[dict] = []
        resultbox = tk.Listbox(weather_page, height=8, bg=PANEL, fg=TEXT,
                               selectbackground=UI_PALETTES[self._ui_theme]["selection"],
                               selectforeground=TEXT,
                               relief="flat", highlightthickness=0,
                               font=("Segoe UI", 11))
        note = tk.StringVar(value="Прогноз обновляется автоматически")
        note_label = tk.Label(weather_page, textvariable=note, bg=PANEL, fg=MUTED,
                              anchor="w", wraplength=440, font=("Segoe UI", 10))
        note_label.pack(fill="x", padx=18, pady=(2, 12))

        def apply_results(results: list[dict]) -> None:
            if not win.winfo_exists():
                return
            city_results.clear()
            city_results.extend(results)
            resultbox.delete(0, "end")
            for city in results:
                resultbox.insert("end", city.get("label", city["name"]))
            if results:
                resultbox.pack(fill="both", expand=True, padx=18,
                               pady=(10, 6), before=note_label)
            else:
                resultbox.pack_forget()
            note.set("Выберите город в списке" if results else
                     "Город не найден или сеть недоступна")

        def find_city() -> None:
            query = search.get().strip()
            if len(query) < 2:
                note.set("Введите не менее двух букв")
                return
            note.set("Ищем город…")

            def worker() -> None:
                try:
                    results = self.weather.search_city(query)
                except Exception:
                    results = []
                self.events.put(("cities", (apply_results, results)))

            threading.Thread(target=worker, daemon=True).start()

        PillButton(weather_page, text="Найти город", command=find_city,
                   palette=UI_PALETTES[self._ui_theme], variant="glass",
                   width=146, corner=PANEL).pack(anchor="w", padx=18, pady=(0, 8), before=note_label)

        heading(alerts_page, "Температурные предупреждения")
        tk.Label(alerts_page, text="Оповещения работают только при наличии реального датчика.",
                 bg=PANEL, fg=MUTED, anchor="w", font=("Segoe UI", 10)).pack(
                     fill="x", padx=18)
        cpu_alert = tk.StringVar(value=str(self.settings["cpu_alert"]))
        gpu_alert = tk.StringVar(value=str(self.settings["gpu_alert"]))
        for caption, variable in (("Процессор, °C", cpu_alert),
                                  ("Видеокарта, °C", gpu_alert)):
            row = tk.Frame(alerts_page, bg=PANEL)
            row.pack(fill="x", padx=18, pady=(17, 0))
            tk.Label(row, text=caption, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11)).pack(side="left")
            StepperField(row, variable=variable, minimum=50, maximum=110,
                         palette=palette).pack(side="right")
        sensor_hint = self.metrics.get("sensor_hint") or (
            "Помощник датчиков читает температуру и обороты вентиляторов. "
            "Недоступные значения отмечены прочерком.")
        tk.Label(alerts_page, text=sensor_hint, bg=PANEL, fg=MUTED,
                 anchor="w", justify="left", wraplength=440, font=("Segoe UI", 10)).pack(
                     fill="x", padx=18, pady=(27, 0))
        retry = PillButton(alerts_page, text="Повторить запуск датчиков",
                           command=self.retry_sensor_host,
                           palette=UI_PALETTES[self._ui_theme], variant="glass",
                           width=228, corner=PANEL)
        retry.pack(anchor="w", padx=18, pady=(14, 10))
        if self.args.preview:
            retry.configure(state="disabled")

        def save() -> None:
            try:
                next_settings = self.settings.copy()
                chosen_theme = next((key for key, label in THEMES.items()
                                     if label == theme.get()), None)
                next_settings.update(
                    rotation=int(rotation.get()), theme=chosen_theme,
                    brand_name=normalize_brand_name(brand_name.get()),
                    brightness=int(brightness.get()),
                    night_brightness=int(night_brightness.get()),
                    night_mode=night_mode.get(), night_start=int(night_start.get()),
                    night_end=int(night_end.get()), auto_pages=auto_pages.get(),
                    context_auto=context_auto.get(),
                    page_seconds=max(5, min(300, int(page_seconds.get()))),
                    port=port.get().strip(), usb_enabled=usb_enabled.get(),
                    cpu_alert=max(50, min(110, int(cpu_alert.get()))),
                    gpu_alert=max(50, min(110, int(gpu_alert.get()))),
                )
                if not next_settings["port"] or next_settings["theme"] not in THEMES:
                    raise ValueError("Проверьте порт и тему оформления")
                selected = resultbox.curselection()
                if selected:
                    city = city_results[selected[0]]
                    next_settings.update(city=city["name"],
                                         latitude=city["latitude"],
                                         longitude=city["longitude"])
                save_json(DATA / "settings.json", next_settings)
            except (OSError, ValueError, tk.TclError) as exc:
                messagebox.showerror(self.settings["brand_name"],
                                     f"Не удалось сохранить настройки: {exc}", parent=win)
                return
            was_enabled = self.settings["usb_enabled"]
            self.settings = next_settings
            win.destroy()
            self.apply_theme(effective_theme(self.settings))
            self._apply_brand()
            if selected:
                self.weather.set_location(city["name"], city["latitude"], city["longitude"])
                threading.Thread(target=self.weather.refresh, daemon=True).start()
            new_brightness = effective_brightness(self.settings)
            self.display.configure(self.settings["port"], self.settings["rotation"],
                                   new_brightness)
            self.last_brightness = new_brightness
            if self.settings["usb_enabled"] and not was_enabled and not self.args.preview:
                self.display.start()
            elif not self.settings["usb_enabled"] and was_enabled:
                self.display.stop()
            self._request_render()

        actions = tk.Frame(outer, bg=BG)
        actions.pack(side="bottom", fill="x", pady=(15, 0), before=book)
        PillButton(actions, text="Сохранить изменения", command=save,
                   palette=UI_PALETTES[self._ui_theme], variant="accent",
                   width=190, corner=BG).pack(side="right")

    def start_tray(self) -> None:
        try:
            import pystray
            self.tray = pystray.Icon(
                "AuroraMonitor", self._make_tray_image(),
                f"{self.settings['brand_name']} · Монитор",
                pystray.Menu(
                    pystray.MenuItem("Открыть", lambda: self.events.put(("show", None)),
                                     default=True),
                    pystray.MenuItem("Завершить", lambda: self.events.put(("quit", None))),
                ),
            )
            self.tray.run_detached()
        except Exception:
            logging.exception("Трей недоступен")
            self.tray = None

    def hide(self) -> None:
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._request_render()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.stop_event.set()
        self.display.stop()
        if self.tray:
            self.tray.stop()
        self.root.destroy()


def _windows_instance_handles() -> tuple[object | None, int | None, int | None, bool]:
    """Create the singleton mutex and a show-window event shared by launches."""
    if os.name != "nt":
        return None, None, None, False
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                    ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateEventW.restype = ctypes.c_void_p
    kernel.SetEvent.argtypes = [ctypes.c_void_p]
    kernel.SetEvent.restype = ctypes.c_bool
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_bool
    event = kernel.CreateEventW(None, False, False, "Local\\AuroraMonitor_Show")
    event_existed = ctypes.get_last_error() == 183
    # Create the event first, so a near-simultaneous second launch can never
    # signal an event that disappears before the first launch opens it.
    mutex = kernel.CreateMutexW(None, False, "Local\\AuroraMonitor")
    already_running = ctypes.get_last_error() == 183
    if already_running:
        if event:
            kernel.SetEvent(event)
        if not event_existed:
            # A previous Aurora build may hold the mutex without watching the
            # show event. Explain how to expose that still-running instance.
            brand = load_settings(DATA / "settings.json")["brand_name"]
            ctypes.windll.user32.MessageBoxW(
                0, "Монитор уже запущен. Откройте его значком в трее.", brand, 0)
        if mutex:
            kernel.CloseHandle(mutex)
        if event:
            kernel.CloseHandle(event)
        return None, None, None, True
    return kernel, mutex, event, False


def main() -> None:
    global DATA
    parser = argparse.ArgumentParser(description="Монитор компьютера и USB-экрана")
    parser.add_argument("--preview", action="store_true", help="Предпросмотр без USB")
    parser.add_argument("--data-dir", type=Path, help="Каталог настроек и журналов")
    parser.add_argument("--snapshot", type=Path, help="Сохранить кадр и выйти без USB")
    parser.add_argument("--shutdown", action="store_true", help="Завершить работающий монитор после передачи кадра")
    args = parser.parse_args()
    if args.data_dir:
        DATA = args.data_dir
    DATA.mkdir(parents=True, exist_ok=True)
    if args.shutdown:
        (DATA / 'shutdown.request').touch()
        return
    logging.basicConfig(
        level=logging.INFO,
        handlers=[RotatingFileHandler(DATA / "aurora.log", maxBytes=1_000_000,
                                      backupCount=2, encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.snapshot:
        telemetry = Telemetry(hardware_path=DATA / "hardware.json")
        telemetry.sample()
        time.sleep(1)
        deadline = time.monotonic() + 3
        metrics = telemetry.sample()
        while metrics.get("gpu_status") == "pending" and time.monotonic() < deadline:
            time.sleep(.1)
            metrics = telemetry.sample()
        weather = WeatherService(DATA / "weather.json")
        settings = load_settings(DATA / "settings.json")
        weather.set_location(settings["city"], settings["latitude"], settings["longitude"])
        weather.refresh()
        render(metrics, weather.snapshot(), {}, rotation=settings["rotation"],
               theme=effective_theme(settings), brand_name=settings["brand_name"]).save(args.snapshot)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        return
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    kernel, mutex, event, already_running = _windows_instance_handles()
    if already_running:
        return
    try:
        logging.info("Запуск АВРОРЫ 0.3")
        root = tk.Tk()
        (DATA / 'shutdown.request').unlink(missing_ok=True)
        app = App(root, args, event)
        try:
            root.mainloop()
        finally:
            app.display.stop()
            app.display.wait_closed()
            logging.info("USB: передача завершена, порт закрыт")
    finally:
        if kernel and event:
            kernel.CloseHandle(event)
        if kernel and mutex:
            kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
