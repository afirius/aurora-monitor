"""Semantic and edge-case checks of the actual small-display layouts."""
from datetime import datetime
from collections import deque
import unittest
from unittest.mock import patch
from renderer import Painter, render, value, THEMES, short_model


class RendererTests(unittest.TestCase):
    def test_untrusted_values_cannot_freeze_the_renderer(self):
        for item in (None, 'broken', float('nan'), float('inf'), {}, True):
            self.assertEqual(value(item, '%'), '—')
            for page in ('overview', 'weather', 'gaming'):
                image = render({'cpu_percent': item, 'ram_percent': item},
                               {'temperature': item, 'updated_at': item}, {}, page)
                self.assertEqual(image.size, (480, 320))

    def test_memory_has_an_explicit_large_numeric_percentage(self):
        calls = []
        original = Painter.text
        def record(painter, x, y, text, size=18, *args, **kwargs):
            calls.append((str(text), size, x, y))
            return original(painter, x, y, text, size, *args, **kwargs)
        with patch.object(Painter, 'text', record):
            render({'cpu_percent': 12, 'gpu_percent': 21, 'ram_percent': 87.3,
                    'ram_used_gb': 27.1, 'ram_total_gb': 31.1}, {}, {})
        self.assertTrue(any(text == '87' and size >= 32 for text, size, _, _ in calls))
        self.assertTrue(any(text == '%' and y > 200 for text, _, _, y in calls))

    def test_essential_readings_stay_in_bounds_at_extremes(self):
        original = Painter.text
        bounds = []
        def record(painter, x, y, text, size=18, color=None, bold=False, anchor='lt'):
            from renderer import font
            box = painter.d.textbbox((round(x*2), round(y*2)), str(text),
                                     font=font(size, bold), anchor=anchor)
            bounds.append((str(text), box, painter.im.size))
            return original(painter, x, y, text, size, color, bold, anchor)
        metrics = dict(cpu_percent=100, gpu_percent=100, ram_percent=100,
                       cpu_temp=110, gpu_temp=110, ram_used_gb=256,
                       ram_total_gb=256, fps=999, frametime_ms=100.5,
                       process_name='Очень длинное название процесса игры.exe')
        weather = dict(city='Очень длинное название населённого пункта',
                       temperature=-40, feels_like=-48, wind_kmh=120,
                       description='Сильный ледяной дождь', weather_code=67,
                       daily=[dict(date='2026-12-31', min=-40, max=-30)]*3)
        with patch.object(Painter, 'text', record):
            for rotation in (0, 90):
                for page in ('overview', 'weather', 'clock', 'graphs', 'thermal', 'gaming', 'media'):
                    render(metrics, weather, {}, page, rotation,
                           now=datetime(2026, 12, 31, 23, 59),
                           brand_name="Мой любимый рабочий компьютер",
                           media={'title': 'Очень длинный трек с длинным названием',
                                  'artist': 'Очень длинное имя исполнителя'})
        for text, (x0, y0, x1, y1), (width, height) in bounds:
            with self.subTest(text=text):
                self.assertGreaterEqual(x0, 0)
                self.assertGreaterEqual(y0, 0)
                self.assertLessEqual(x1, width)
                self.assertLessEqual(y1, height)

    def test_overview_preserves_models_frequencies_and_zero_fan(self):
        calls=[]
        original=Painter.text
        def record(painter,x,y,text,*args,**kwargs):
            calls.append(str(text))
            return original(painter,x,y,text,*args,**kwargs)
        with patch.object(Painter,'text',record):
            render(dict(cpu_name='AMD Ryzen 7 7700X 8-Core Processor',
                        gpu_name='NVIDIA GeForce RTX 5060 Ti',cpu_mhz=4940,
                        gpu_clock_mhz=1567,ram_type='DDR5',ram_speed_mt=6000,ram_clock_mhz=3000,
                        ram_installed_gb=32,cpu_fan_rpm=2625,gpu_fan_rpm=0),{},{})
        for expected in ('AMD Ryzen 7 7700X','NVIDIA RTX 5060 Ti','4,9','1,6','ГГц','32 ГБ DDR5 6000 МГц','2625','0'):
            self.assertIn(expected,calls)
        self.assertFalse(any('Open-Meteo' in text or 'ГиБ' in text for text in calls))

    def test_overview_aligns_readings_and_localizes_memory(self):
        original=Painter.text
        for rotation in (0,90):
            calls=[]
            def record(p,x,y,text,*args,**kwargs):
                calls.append((str(text),x,y))
                return original(p,x,y,text,*args,**kwargs)
            with patch.object(Painter,'text',record):
                render(dict(cpu_percent=42,cpu_temp=65,cpu_fan_rpm=2580,
                            ram_used_gb=12.3,ram_total_gb=31.1,ram_installed_gb=32,
                            ram_type='DDR5',ram_speed_mt=6000),{},{},rotation=rotation)
            temp=next(c for c in calls if c[0]=='65°')
            load=next(c for c in calls if c[0]=='42')
            self.assertLess(temp[1],load[1])
            self.assertGreater(temp[2],load[2])
            fan=next(c for c in calls if c[0]=='2580')
            unit=next(c for c in calls if c[0]=='rpm')
            self.assertLessEqual(abs(fan[2]-unit[2]),1)
            self.assertGreater(fan[2],load[2])
            self.assertIn('32 ГБ DDR5 6000 МГц',[c[0] for c in calls])
            self.assertIn('12,3',[c[0] for c in calls])
            self.assertNotIn('12,3 / 31,1 ГБ',[c[0] for c in calls])

    def test_gpu_model_keeps_identifier_instead_of_marketing_suffix(self):
        self.assertEqual(short_model('NVIDIA GeForce RTX 5090 Ti SUPER OC Limited Edition','gpu'),'RTX 5090 Ti SUPER')
        self.assertEqual(short_model('AMD Radeon RX 7900 XTX Limited Edition','gpu'),'RX 7900 XTX')

    def test_idle_gpu_clock_does_not_round_to_zero(self):
        calls=[];original=Painter.text
        def record(p,x,y,text,*args,**kwargs):
            calls.append(str(text))
            return original(p,x,y,text,*args,**kwargs)
        with patch.object(Painter,'text',record):
            render({'gpu_clock_mhz':22},{},{},rotation=0)
        self.assertIn('22',calls)
        self.assertIn('МГц',calls)
        self.assertNotIn('0,0',calls)

    def test_memory_specs_and_vendors_follow_detected_hardware(self):
        original=Painter.text
        for capacity,ram_type,rate in ((32,'DDR5',6000),(64,'DDR4',3200),(96,'DDR5',5600)):
            calls=[]
            def record(p,x,y,text,*args,**kwargs):
                calls.append(str(text))
                return original(p,x,y,text,*args,**kwargs)
            with patch.object(Painter,'text',record):
                render(dict(cpu_name='AMD Ryzen 7 7700',gpu_name='NVIDIA RTX 5060',
                            ram_installed_gb=capacity,ram_type=ram_type,
                            ram_speed_mt=rate,ram_used_gb=12.3),{},{},rotation=0)
            self.assertIn(f'{capacity} ГБ {ram_type} {rate} МГц',calls)
            self.assertIn('12,3',calls)
            self.assertIn('AMD Ryzen 7 7700',calls)
            self.assertIn('NVIDIA RTX 5060',calls)

    def test_weather_without_an_observation_does_not_claim_updated(self):
        calls=[]
        original=Painter.text
        def record(painter,x,y,text,*args,**kwargs):
            calls.append(str(text))
            return original(painter,x,y,text,*args,**kwargs)
        with patch.object(Painter,'text',record):
            render({},dict(stale=True),{},page='weather',rotation=0)
        self.assertFalse(any('ОБНОВЛЕНО' in text or 'КЭШ' in text for text in calls))
        self.assertTrue(any('НЕТ ДАННЫХ' in text or 'ОЖИДАНИЕ' in text for text in calls))

    def test_portrait_frequencies_and_capacity_get_readable_type(self):
        calls=[];original=Painter.text
        def record(p,x,y,text,size=18,*args,**kwargs):
            calls.append((str(text),size,y))
            return original(p,x,y,text,size,*args,**kwargs)
        with patch.object(Painter,'text',record):
            render(dict(cpu_mhz=4920,cpu_percent=42,ram_percent=94,
                        ram_used_gb=29.2,ram_total_gb=31.1,ram_installed_gb=32,
                        ram_type='DDR5',ram_speed_mt=6000),
                   dict(description='Переменная облачность'),{},rotation=0)
        self.assertTrue(any(text=='4,9' and size>=30 for text,size,_ in calls))
        self.assertTrue(any(text=='29,2' and size==32 for text,size,_ in calls))
        self.assertTrue(any(text=='42' and size>=42 for text,size,_ in calls))
        lines=[y for text,_,y in calls if text in ('Переменная','облачность')]
        self.assertEqual(lines,[411,429])

    def test_hardware_text_groups_do_not_overlap_at_extremes(self):
        from renderer import font
        original=Painter.text
        for rotation in (0,90):
            boxes=[]
            def record(p,x,y,text,size=18,color=None,bold=False,anchor='lt'):
                box=p.d.textbbox((round(x*2),round(y*2)),str(text),font=font(size,bold),anchor=anchor)
                if 39*2<=box[1] and box[3]<=((201 if rotation==90 else 281)*2):
                    boxes.append((str(text),box))
                return original(p,x,y,text,size,color,bold,anchor)
            with patch.object(Painter,'text',record):
                render(dict(cpu_name='AMD Ryzen 7 7700',gpu_name='NVIDIA RTX 5060',
                            cpu_percent=100,gpu_percent=100,cpu_temp=110,gpu_temp=110,
                            cpu_fan_rpm=30000,gpu_fan_rpm=30000,cpu_mhz=10000,
                            gpu_clock_mhz=10000),{},{},rotation=rotation)
            for i,(left,a) in enumerate(boxes):
                for right,b in boxes[i+1:]:
                    overlap=min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])
                    self.assertFalse(overlap,(rotation,left,right,a,b))

    def test_temperature_bands_and_unknown_sensor(self):
        from renderer import temperature_color
        for theme in ('day','night'):
            p=Painter(320,480,theme)
            self.assertEqual(temperature_color(p,None),p.c['muted'])
            self.assertEqual(temperature_color(p,float('nan')),p.c['muted'])
            self.assertEqual(temperature_color(p,59),temperature_color(p,20))
            self.assertEqual(temperature_color(p,60),temperature_color(p,84))
            self.assertEqual(len({temperature_color(p,t) for t in (59,60,85)}),3)

    def test_percentage_uses_two_load_colors(self):
        from renderer import load_color
        for theme in ('day','night'):
            p=Painter(320,480,theme)
            self.assertEqual(load_color(p,0),load_color(p,79))
            self.assertEqual(load_color(p,80),load_color(p,100))
            self.assertNotEqual(load_color(p,79),load_color(p,80))
            self.assertEqual(load_color(p,None),p.c['muted'])
            self.assertEqual(load_color(p,79.6),load_color(p,80))
            self.assertEqual(load_color(p,79.4),load_color(p,79))

    def test_temperature_color_tracks_the_rounded_visible_reading(self):
        from renderer import temperature_color
        for theme in ('day','night'):
            p=Painter(320,480,theme)
            self.assertEqual(temperature_color(p,59.6),temperature_color(p,60))
            self.assertEqual(temperature_color(p,84.6),temperature_color(p,85))

    def test_landscape_memory_summary_clears_badge_and_label(self):
        from renderer import font
        original=Painter.text
        boxes={}
        def record(p,x,y,text,size=18,color=None,bold=False,anchor='lt'):
            box=p.d.textbbox((round(x*2),round(y*2)),str(text),font=font(size,bold),anchor=anchor)
            if str(text) in ('12,3','32 ГБ DDR5 6000 МГц'):
                boxes[str(text)]=box
            return original(p,x,y,text,size,color,bold,anchor)
        with patch.object(Painter,'text',record):
            render({'ram_used_gb':12.3,'ram_installed_gb':32,'ram_type':'DDR5',
                    'ram_speed_mt':6000,'ram_percent':38},{},{},rotation=90)
        used=boxes['12,3'];label=boxes['32 ГБ DDR5 6000 МГц']
        self.assertGreaterEqual(used[0],58*2)
        self.assertFalse(min(used[2],label[2])>max(used[0],label[0]) and
                         min(used[3],label[3])>max(used[1],label[1]))

    def test_standard_weather_descriptions_fit_without_ellipsis(self):
        from weather import _WEATHER_DESCRIPTIONS
        from renderer import weather_card
        original=Painter.text
        for code,description in _WEATHER_DESCRIPTIONS.items():
            calls=[]
            def record(p,x,y,text,*args,**kwargs):
                calls.append(str(text))
                return original(p,x,y,text,*args,**kwargs)
            with patch.object(Painter,'text',record):
                weather_card(Painter(320,480),dict(city='Москва',temperature=11,
                    weather_code=code,description=description),(8,357,312,472),False)
            self.assertFalse(any('…' in text for text in calls),(code,calls))

    def test_weather_silhouettes_distinguish_night_snow_storm_and_unknown(self):
        def icon(code, day=True):
            p = Painter(80, 80)
            p.weather_icon(8, 8, code, 64, day)
            return p.finish().tobytes()
        self.assertNotEqual(icon(0), icon(0, False))
        self.assertEqual(len({icon(code) for code in (None, 0, 3, 45, 61, 71, 95)}), 7)

    def test_each_palette_renders_both_panel_orientations(self):
        for theme in THEMES:
            for rotation, size in ((90, (480, 320)), (0, (320, 480))):
                self.assertEqual(render({}, {}, {}, rotation=rotation, theme=theme).size, size)

    def test_live_graphs_accept_deque_history(self):
        from renderer import Painter
        original = Painter.graph
        plotted = []
        def record(painter, box, values, color, maximum=100):
            plotted.append((list(values), maximum))
            return original(painter, box, values, color, maximum)
        with patch.object(Painter, 'graph', record):
            render({}, {}, {
                'cpu_percent': deque(range(60), maxlen=60),
                'gpu_percent': deque(range(60), maxlen=60),
            }, page='graphs', rotation=0)
        self.assertEqual(len(plotted), 2)
        self.assertEqual(plotted[0], (list(range(60)), 100))
        self.assertEqual(plotted[1], (list(range(60)), 100))

    def test_month_grid_fills_the_tail_in_sequence(self):
        from renderer import Painter, _draw_month
        original = Painter.text
        dates = []
        def record(painter, x, y, text, size=18, *args, **kwargs):
            if isinstance(text, int):
                dates.append(text)
            return original(painter, x, y, text, size, *args, **kwargs)
        with patch.object(Painter, 'text', record):
            _draw_month(Painter(320, 480), (12, 12, 308, 260), datetime(2026, 2, 28))
        self.assertEqual(dates[-7:], [2, 3, 4, 5, 6, 7, 8])

    def test_game_page_shows_fps_frametime_and_both_live_hardware_metrics(self):
        calls = []
        original = Painter.text
        def record(painter, x, y, text, *args, **kwargs):
            calls.append(str(text))
            return original(painter, x, y, text, *args, **kwargs)
        with patch.object(Painter, 'text', record):
            render({
                'fps': 62.4, 'frametime_ms': 16.8,
                'cpu_percent': 31, 'cpu_temp': 64,
                'gpu_percent': 72, 'gpu_temp': 53,
            }, {}, {}, page='gaming', rotation=0)
        self.assertIn('62', calls)
        self.assertIn('16,8 мс', calls)
        self.assertIn('CPU', calls)
        self.assertIn('GPU', calls)
        self.assertIn('64°C', calls)
        self.assertIn('53°C', calls)

    def test_game_page_omits_unavailable_hardware_and_frametime(self):
        calls=[];original=Painter.text
        def record(painter,x,y,text,*args,**kwargs):
            calls.append(str(text))
            return original(painter,x,y,text,*args,**kwargs)
        with patch.object(Painter,'text',record):
            render({'fps':60}, {}, {}, page='gaming', rotation=0)
        self.assertFalse(any('—' in text for text in calls))
        self.assertFalse(any(text in ('CPU','GPU','ВРЕМЯ КАДРА') for text in calls))

    def test_media_progress_is_rendered_only_when_both_times_exist(self):
        calls = []
        original = Painter.text
        def record(painter, x, y, text, *args, **kwargs):
            calls.append(str(text))
            return original(painter, x, y, text, *args, **kwargs)
        with patch.object(Painter, 'text', record):
            render({}, {}, {}, page='media', rotation=90,
                   media={'title': 'Трек', 'artist': 'Исполнитель',
                          'position_seconds': 83, 'duration_seconds': 180})
        self.assertIn('1:23', calls)
        self.assertIn('3:00', calls)


if __name__ == '__main__':
    unittest.main()
