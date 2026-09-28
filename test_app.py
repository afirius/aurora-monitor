import unittest
import tempfile
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path
from datetime import datetime
from PIL import Image
from main import (App, load_settings, effective_brightness, effective_theme,
                  desktop_gb, normalize_brand_name, select_overview_content,
                  DEFAULTS, THEMES, UI_PALETTES)
from renderer import render

class AppTests(unittest.TestCase):
    def test_overview_live_content_prioritizes_confirmed_game_then_playing_track(self):
        media = {"title": "Night Drive", "artist": "Aurora", "is_playing": True,
                 "thumbnail_bytes": b"album art"}
        game = {"fps": 60.0, "frametime_ms": 16.67, "cpu_temp": 55,
                "gpu_temp": 62, "cpu_percent": 34, "gpu_percent": 71,
                "process_name": "game.exe", "game_detected": True}
        game_content = select_overview_content(game, media)
        self.assertEqual(game_content["kind"], "gaming")
        self.assertEqual(game_content["frametime_ms"], 16.67)
        self.assertEqual(game_content["cpu_temp"], 55)
        self.assertEqual(game_content["gpu_percent"], 71)
        self.assertEqual(select_overview_content({}, media), {
            "kind": "media", "title": "Night Drive", "artist": "Aurora",
            "album": None, "thumbnail_bytes": b"album art"})
        self.assertIsNone(select_overview_content({}, {**media, "is_playing": False}))
        self.assertIsNone(select_overview_content({"fps": 60.0}, {}))

    def test_auto_pages_skip_paused_or_stale_media_and_unconfirmed_fps(self):
        app = App.__new__(App)
        app.page = "clock"
        app.media = {"title": "Night Drive", "is_playing": False}
        app.last_media_sample = time.monotonic()
        app.metrics = {"fps": 60.0}
        app.last_sample = time.monotonic()
        app.started_at = app.last_sample
        app.weather = Mock()
        app.weather.snapshot.return_value={}
        self.assertEqual(app._next_auto_page(), "overview")
        app.media["is_playing"] = True
        self.assertEqual(app._next_auto_page(), "media")
        app.last_media_sample = time.monotonic() - 16
        self.assertEqual(app._next_auto_page(), "overview")

    def test_overview_render_receives_live_content_without_changing_full_page(self):
        app = App.__new__(App)
        app.closed = False
        app.root = Mock()
        app.root.state.return_value = "withdrawn"
        app.args = SimpleNamespace(preview=True)
        app.settings = DEFAULTS.copy()
        app.status = Mock()
        app.display = Mock()
        app.display.status = {}
        app.weather = Mock()
        app.weather.snapshot.return_value = {}
        app.page = "overview"
        app.history = {}
        app.alert = ""
        app.sensor_error = None
        app._ui_theme = "night"
        app._last_display_message = None
        app.media = {"title": "Night Drive", "is_playing": True}
        app.last_media_sample = time.monotonic()
        app._current_metrics = Mock(return_value=({"game_detected": True,
                                                    "fps": 61.0}, False))
        with patch("main.render", return_value=Image.new("RGB", (480, 320))) as draw:
            app._render_once()
        self.assertEqual(draw.call_args.kwargs["overview_content"]["kind"], "gaming")
        app.page = "media"
        with patch("main.render", return_value=Image.new("RGB", (480, 320))) as draw:
            app._render_once()
        self.assertEqual(draw.call_args.args[3], "media")
        self.assertEqual(draw.call_args.kwargs["media"]["title"], "Night Drive")
        app.last_media_sample = time.monotonic() - 16
        app._current_metrics.return_value = ({}, False)
        with patch("main.render", return_value=Image.new("RGB", (480, 320))) as draw:
            app._render_once()
        self.assertEqual(draw.call_args.kwargs["media"], {})
        self.assertIsNone(draw.call_args.kwargs["overview_content"])
        app.page = "overview"
        app.settings["context_auto"] = False
        app.last_media_sample = time.monotonic()
        with patch("main.render", return_value=Image.new("RGB", (480, 320))) as draw:
            app._render_once()
        self.assertIsNone(draw.call_args.kwargs["overview_content"])

    def test_callback_exception_does_not_end_update_loop(self):
        app=App.__new__(App)
        app.closed=False;app.root=Mock();app._sample_lock=threading.Lock();app.args=SimpleNamespace(preview=True)
        app._pending_sample=None;app._pending_media=None
        app.events=queue.Queue(maxsize=128)
        def broken(_):raise ValueError('callback failed')
        app.events.put(('cities',(broken,[])))
        with self.assertLogs(level='ERROR'):
            app.tick()
        app.root.after.assert_called_once_with(1000,app.tick)

    def test_missing_first_sensor_sample_becomes_explicitly_stale(self):
        app=App.__new__(App)
        app.metrics={};app.last_sample=0;app.started_at=time.monotonic()-10
        metrics,stale=app._current_metrics()
        self.assertTrue(stale)
        self.assertIsNone(metrics['ram_percent'])
        self.assertIsNone(metrics['cpu_percent'])

    def test_renderer_failure_replaces_usb_readings_with_error_frame(self):
        app=App.__new__(App)
        app.closed=False;app.root=Mock();app.root.state.return_value='withdrawn'
        app.settings=DEFAULTS.copy();app.args=SimpleNamespace(preview=False)
        app._current_metrics=Mock(return_value=({'cpu_percent':34},False))
        app.weather=Mock();app.weather.snapshot.return_value={}
        app.page='overview';app.history={};app.alert='';app.media={}
        app.display=Mock();app.status=Mock()
        with patch('main.render',side_effect=ValueError('render failed')), self.assertLogs(level='ERROR'):
            app._render_once()
        app.display.submit.assert_called_once()
        frame=app.display.submit.call_args.args[0]
        self.assertEqual(frame.size,(480,320))
        self.assertEqual(frame.getpixel((0,0)),(40,21,31))

    def test_night_schedule_crosses_midnight(self):
        cfg=DEFAULTS.copy()
        for hour in (23,0,7):self.assertEqual(effective_brightness(cfg,hour),18)
        for hour in (8,12,22):self.assertEqual(effective_brightness(cfg,hour),100)
        cfg['night_mode']=False
        self.assertEqual(effective_brightness(cfg,1),100)

    def test_automatic_theme_tracks_night_schedule_but_manual_modes_do_not(self):
        cfg=DEFAULTS.copy()
        self.assertEqual(cfg['theme'],'auto')
        self.assertEqual(cfg['brightness'],100)
        for hour in (23,0,7):
            self.assertEqual(effective_theme(cfg,hour),'night')
        for hour in (8,12,22):
            self.assertEqual(effective_theme(cfg,hour),'day')
        cfg['theme']='day'
        self.assertEqual(effective_theme(cfg,1),'day')
        cfg['theme']='night'
        self.assertEqual(effective_theme(cfg,12),'night')
        cfg['theme']='auto';cfg['night_mode']=False
        self.assertEqual(effective_theme(cfg,1),'day')

    def test_tick_applies_scheduled_theme_transition(self):
        app=App.__new__(App)
        app.closed=False;app.root=Mock();app._sample_lock=threading.Lock();app.args=SimpleNamespace(preview=True)
        app._pending_sample=None;app._pending_media=None
        app.events=queue.Queue(maxsize=128)
        app.settings=DEFAULTS.copy();app.last_brightness=100
        app._ui_theme='day';app.display=Mock()
        app.apply_theme=Mock();app._request_render=Mock()
        with patch('main.effective_theme',return_value='night'), patch('main.effective_brightness',return_value=100):
            app.tick()
        app.apply_theme.assert_called_once_with('night')
        app.display.configure.assert_not_called()

    def test_desktop_gigabytes_use_comma_decimal_separator(self):
        self.assertEqual(desktop_gb(12.34),'12,3')
        self.assertEqual(desktop_gb(32),'32,0')
        self.assertEqual(desktop_gb(None),'—')

    def test_invalid_settings_recover_without_invalid_brightness(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'settings.json'
            path.write_text('{"brightness":999,"rotation":13,"page_seconds":"bad","context_auto":"yes"}')
            cfg=load_settings(path)
            self.assertEqual(cfg['brightness'],100)
            self.assertEqual(cfg['rotation'],90)
            self.assertEqual(cfg['page_seconds'],20)
            self.assertIs(cfg['context_auto'], True)

    def test_legacy_theme_migrates_without_changing_brightness(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'settings.json'
            path.write_text('{"theme":"ember","brightness":77,"night_brightness":13,"night_mode":true,"brand_name":"  Мой   мощный\\n ПК  "}',encoding='utf-8')
            cfg=load_settings(path)
        self.assertEqual(THEMES,{'auto':'Автоматически','day':'Дневная','night':'Ночная'})
        self.assertEqual(cfg['theme'],'night')
        self.assertEqual(cfg['brand_name'],'Мой мощный ПК')
        self.assertEqual(cfg['brightness'],77)
        self.assertEqual(cfg['night_brightness'],13)
        self.assertEqual(effective_brightness(cfg,1),13)
        self.assertEqual(effective_brightness(cfg,12),77)

    def test_brand_name_normalization_limits_printable_text(self):
        self.assertEqual(normalize_brand_name('  АВТОР   ПК\t\n'), 'АВТОР ПК')
        self.assertEqual(normalize_brand_name('\x00\n\t'), 'Мой компьютер')
        self.assertEqual(normalize_brand_name('Ж'*60), 'Ж'*32)
        self.assertEqual(normalize_brand_name(None), 'Мой компьютер')

    def test_manual_theme_switch_preserves_brightness(self):
        app=App.__new__(App)
        app._ui_theme='night'
        app.settings=DEFAULTS.copy()
        app.settings['brightness']=77
        app.settings['night_brightness']=13
        app.root=Mock()
        app.root.cget.return_value=UI_PALETTES['night']['bg']
        app.root.winfo_children.return_value=[]
        app._configure_styles=Mock();app._paint_navigation=Mock()
        app._request_render=Mock();app.tray=None;app._detail_icon_labels={}
        try:
            app.apply_theme('day')
            self.assertEqual(app._ui_theme,'day')
            self.assertEqual(app.settings['brightness'],77)
            self.assertEqual(app.settings['night_brightness'],13)
            app.root.configure.assert_any_call(background=UI_PALETTES['day']['bg'])
        finally:
            from main import _set_ui_palette
            _set_ui_palette('night')

    def test_every_page_handles_unavailable_sensors_in_both_orientations(self):
        for rotation,size in [(0,(320,480)),(90,(480,320)),(180,(320,480)),(270,(480,320))]:
            for page in ('overview','graphs','thermal','weather','clock','media','gaming'):
                with self.subTest(rotation=rotation,page=page):
                    image=render({}, {}, {}, page,rotation,datetime(2026,9,28,12,0))
                    self.assertEqual(image.size,size)
                    self.assertEqual(image.mode,'RGB')

if __name__=='__main__':unittest.main()
