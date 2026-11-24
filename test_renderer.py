"""Semantic and edge-case checks of the actual small-display layouts."""
from datetime import datetime
from collections import deque
import unittest
from unittest.mock import patch
from PIL import ImageChops
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

    def test_memory_temperature_matches_the_cpu_and_gpu_group(self):
        for rotation in (0, 90):
            with self.subTest(rotation=rotation):
                calls = []
                icons = []
                original = Painter.text
                def record(painter, x, y, text, size=18, *args, **kwargs):
                    calls.append((str(text), size, x, y, args[0] if args else None))
                    return original(painter, x, y, text, size, *args, **kwargs)
                def record_icon(painter, x, y, kind, color, size=40):
                    icons.append((x, y, kind, color, size))
                with patch.object(Painter, 'text', record), \
                     patch('renderer.hardware_icon', record_icon):
                    render(dict(cpu_temp=65, gpu_temp=41, ram_used_gb=16,
                                ram_total_gb=32, ram_percent=50, ram_temp=42),
                           {}, {}, rotation=rotation)
                ram_temp = next(call for call in calls if call[0] == '42°')
                gpu_temp = next(call for call in calls if call[0] == '41°')
                self.assertEqual(ram_temp[1], gpu_temp[1])
                self.assertEqual(ram_temp[4], gpu_temp[4])
                self.assertFalse(any(text.endswith('°C') for text, *_ in calls))
                icon = next(icon for icon in icons
                            if icon[2] == 'temp' and icon[1] > 220
                            and icon[0] + 18 == ram_temp[2])
                self.assertEqual(icon[1], 327 if rotation == 0 else 242)
                self.assertLessEqual(abs(ram_temp[2] - gpu_temp[2]), 2)
                self.assertGreater(ram_temp[3], 300 if rotation == 0 else 220)
        calls = []
        original = Painter.text
        def record_absent(painter, x, y, text, *args, **kwargs):
            calls.append(str(text))
            return original(painter, x, y, text, *args, **kwargs)
        with patch.object(Painter, 'text', record_absent):
            render({'ram_used_gb': 16, 'ram_total_gb': 32}, {}, {})
        self.assertFalse(any('°C' in text for text in calls))

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
                       ram_total_gb=256, ram_temp=109,
                       fps=999, frametime_ms=100.5,
                       process_name='Очень длинное название процесса игры.exe')
        weather = dict(city='Очень длинное название населённого пункта',
                       temperature=-40, feels_like=-48, wind_kmh=120,
                       description='Сильный ледяной дождь', weather_code=67,
                       daily=[dict(date='2026-12-31', min=-40, max=-30)]*3)
        with patch.object(Painter, 'text', record):
            for theme in ('night', 'luminous', 'luminous_day', 'luminous_classic_day'):
                for rotation in (0, 90):
                    for page in ('overview', 'weather', 'clock', 'graphs', 'thermal', 'gaming', 'media'):
                        render(metrics, weather, {}, page, rotation, theme=theme,
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
                   dict(description='Переменная облачность', weather_code=2),{},rotation=0)
        self.assertTrue(any(text=='4,9' and size>=30 for text,size,_ in calls))
        self.assertTrue(any(text=='29,2' and size==32 for text,size,_ in calls))
        self.assertTrue(any(text=='42' and size>=42 for text,size,_ in calls))
        lines=[y for text,_,y in calls if text in ('Переменная','облачность')]
        self.assertEqual(len(lines), 2)
        self.assertGreaterEqual(lines[1] - lines[0], 17)
        self.assertLess(lines[1], 451)

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

    def test_weather_icon_animation_is_opt_in_and_moves_every_family(self):
        def icon(code, phase, day=True):
            p = Painter(80, 80)
            p.weather_icon(8, 8, code, 64, day, phase)
            return p.finish().tobytes()
        for code, day in ((0, True), (0, False), (1, True), (1, False),
                          (2, True), (3, True), (45, True), (61, True),
                          (71, True), (95, True)):
            with self.subTest(code=code, day=day):
                self.assertNotEqual(icon(code, 0.0, day), icon(code, 1.7, day))
        for code in (0, 3, 45, 61, 71, 95):
            self.assertEqual(icon(code, None), icon(code, None))

    def test_weather_widget_animation_stays_inside_the_sky(self):
        metrics = dict(cpu_temp=65, gpu_temp=41, ram_used_gb=16,
                       ram_total_gb=32, ram_percent=50, ram_temp=42)
        weather = dict(city='Москва', temperature=-5, feels_like=-8, wind_kmh=11)
        expected = {
            # Include the 2x-to-native Lanczos filter's one-pixel fringe.
            0: (174, 374, 313, 473),
            90: (410, 273, 473, 313),
        }
        for rotation, box in expected.items():
            for code, day in ((0, True), (0, False), (2, True), (3, False), (45, True),
                              (61, True), (71, True), (95, False), (None, True)):
                with self.subTest(rotation=rotation, code=code, day=day):
                    def frame(phase):
                        return render(metrics, dict(weather, weather_code=code, is_day=day),
                                      {}, rotation=rotation,
                                      now=datetime(2026, 11, 19, 8, 0), animation=phase)
                    static = frame(None)
                    self.assertEqual(static.tobytes(), frame(None).tobytes())
                    diff = ImageChops.difference(static, frame(1.7)).getbbox()
                    if code is None:
                        self.assertIsNone(diff)
                        continue
                    self.assertIsNotNone(diff)
                    self.assertTrue(diff[0] >= box[0] and diff[1] >= box[1], (diff, box))
                    self.assertTrue(diff[2] <= box[2] and diff[3] <= box[3], (diff, box))

    def test_precipitation_fits_the_usb_budget(self):
        from display import _send_bitmap

        class Wire:
            def write(self, data):
                return len(data)

        for code in (61, 71, 95):
            previous = None
            with self.subTest(code=code):
                for step in range(12):
                    frame = render({}, dict(weather_code=code), {}, rotation=0,
                                   now=datetime(2026, 11, 19, 8, 0), animation=step * .125)
                    if previous is not None:
                        sent, _ = _send_bitmap(Wire(), frame, previous)
                        self.assertGreater(sent, 0)
                        self.assertLess(sent, 10_000)
                    previous = frame

    def test_each_palette_renders_both_panel_orientations(self):
        for theme in THEMES:
            for rotation, size in ((90, (480, 320)), (0, (320, 480))):
                self.assertEqual(render({}, {}, {}, rotation=rotation, theme=theme).size, size)

    def test_luminous_overview_preserves_hardware_readings(self):
        original = Painter.text
        metrics = dict(cpu_name='AMD Ryzen 7 7700', cpu_temp=65, cpu_mhz=4500,
                       cpu_percent=24, cpu_fan_rpm=1230, gpu_name='NVIDIA RTX 5060 Ti',
                       gpu_clock_mhz=1800, gpu_temp=42, gpu_percent=11, gpu_fan_rpm=0,
                       ram_temp=40, ram_percent=50, ram_installed_gb=32, ram_type='DDR5',
                       ram_speed_mt=6000, ram_used_gb=16)
        for rotation in (0, 90):
            with self.subTest(rotation=rotation):
                calls = []
                def record(p, x, y, text, size=18, *args, **kwargs):
                    calls.append((str(text), size))
                    return original(p, x, y, text, size, *args, **kwargs)
                with patch.object(Painter, 'text', record):
                    render(metrics, {}, {}, theme='luminous_classic', rotation=rotation)
                by_text = dict(calls)
                for reading in ('AMD Ryzen 7 7700', 'NVIDIA RTX 5060 Ti', '65°', '42°',
                                '40°', 'ГГц', '1230', '0'):
                    self.assertGreaterEqual(by_text[reading], 16)
                self.assertGreaterEqual(by_text['32 ГБ DDR5 6000 МГц'], 17)
                for label in ('CPU', 'GPU', 'ОЗУ'):
                    self.assertNotIn(label, by_text)
                for reading, minimum in (('24', 50), ('11', 50), ('4,5', 30),
                                         ('16,0', 24), ('50', 32)):
                    self.assertGreaterEqual(by_text[reading], minimum)

    def test_luminous_hardware_text_does_not_collide_at_high_readings(self):
        from renderer import font
        original = Painter.text
        metrics = dict(cpu_name='AMD Ryzen 9 9950X3D', cpu_temp=109, cpu_mhz=5590,
                       cpu_percent=100, cpu_fan_rpm=30000, gpu_name='NVIDIA RTX 5090 Ti SUPER',
                       gpu_clock_mhz=1800, gpu_temp=108, gpu_percent=100, gpu_fan_rpm=25000,
                       ram_temp=107, ram_percent=100, ram_installed_gb=256,
                       ram_type='DDR5', ram_speed_mt=9600, ram_used_gb=256)
        for rotation, bottom in ((0, 368), (90, 271)):
            boxes = []
            def record(p, x, y, text, size=18, color=None, bold=False, anchor='lt'):
                if 45 <= y <= bottom and text:
                    box = p.d.textbbox((round(x*2), round(y*2)), str(text),
                                       font=font(size, bold), anchor=anchor)
                    boxes.append((text, box))
                return original(p, x, y, text, size, color, bold, anchor)
            with patch.object(Painter, 'text', record):
                render(metrics, {}, {}, theme='luminous_classic', rotation=rotation)
            for i, (text, a) in enumerate(boxes):
                for other, b in boxes[i+1:]:
                    self.assertFalse(min(a[2], b[2]) > max(a[0], b[0])
                                     and min(a[3], b[3]) > max(a[1], b[1]),
                                     (rotation, text, other, a, b))

    def test_luminous_motion_only_affects_the_weather_panel(self):
        weather = dict(weather_code=71, temperature=-5)
        now = datetime(2026, 11, 24, 19, 0)
        for rotation, box in ((0, (169, 371, 320, 480)), (90, (405, 269, 480, 320))):
            def frame(phase, page='overview', content=None):
                return render({}, weather, {}, rotation=rotation, now=now, theme='luminous_classic',
                              page=page, overview_content=content, animation=phase)
            static = frame(None)
            self.assertEqual(static.tobytes(), frame(None).tobytes())
            x, y, x2, y2 = ImageChops.difference(static, frame(.75)).getbbox()
            self.assertTrue(box[0] <= x < x2 <= box[2] and box[1] <= y < y2 <= box[3])
            self.assertEqual(frame(None, 'weather').tobytes(), frame(.75, 'weather').tobytes())
            content = {'kind': 'gaming', 'fps': 120}
            self.assertEqual(frame(None, content=content).tobytes(), frame(.75, content=content).tobytes())

    def test_luminous_long_weather_labels_clear_the_temperature_and_footer(self):
        from renderer import font
        original = Painter.text
        now = datetime(2026, 11, 24, 20, 30)
        for rotation in (0, 90):
            boxes = []
            def record(p, x, y, text, size=18, color=None, bold=False, anchor='lt'):
                if y >= (375 if rotation == 0 else 272):
                    box = p.d.textbbox((round(x*2), round(y*2)), str(text),
                                       font=font(size, bold), anchor=anchor)
                    boxes.append((text, box))
                return original(p, x, y, text, size, color, bold, anchor)
            weather = dict(city='Очень длинное название города', temperature=-48, feels_like=-56,
                           wind_kmh=120, description='Переменная облачность', weather_code=2)
            with patch.object(Painter, 'text', record):
                render({}, weather, {}, rotation=rotation, now=now, theme='luminous_classic')
            for i, (text, a) in enumerate(boxes):
                for other, b in boxes[i+1:]:
                    self.assertFalse(min(a[2], b[2]) > max(a[0], b[0])
                                     and min(a[3], b[3]) > max(a[1], b[1]),
                                     (rotation, text, other, a, b))
            expected = ('-48', 'Переменная', 'облачность', 'Ощущается -56°', '120 км/ч') if rotation == 0 else (
                '-48', 'Переменная облачность', 'Ощущ. -56°', 'Ветер 120 км/ч')
            for text in expected:
                self.assertIn(text, dict(boxes))

    def test_luminous_uses_classic_positions_without_hardware_badges(self):
        original = Painter.text
        m = dict(cpu_name='Intel Core Ultra 9', gpu_name='NVIDIA RTX 5080',
                 cpu_percent=23, gpu_percent=15, ram_percent=43, cpu_mhz=4900,
                 gpu_clock_mhz=1600, cpu_temp=65, gpu_temp=44, ram_temp=42,
                 ram_used_gb=16, cpu_fan_rpm=1230, gpu_fan_rpm=850)
        readings = ('23', '15', '43', '4,9', '1,6', '65°', '44°', '42°', '16,0', '1230', '850')
        for rotation in (0, 90):
            positions = {}
            for theme in ('night', 'luminous_classic'):
                def record(p, x, y, text, size=18, *args, **kwargs):
                    if text in readings:
                        positions[theme, text] = (x, y, size)
                    return original(p, x, y, text, size, *args, **kwargs)
                with patch.object(Painter, 'text', record), patch('renderer.hardware_badge') as badge:
                    render(m, {}, {}, rotation=rotation, theme=theme)
                self.assertEqual(badge.call_count, 3 if theme == 'night' else 0)
            for text in readings:
                self.assertEqual(positions['night', text], positions['luminous_classic', text])

    def test_luminous_overview_has_no_gutters_or_unused_edge_rows(self):
        weather = dict(temperature=10, weather_code=2, is_day=True)
        for rotation, samples in ((0, (17, 39, 147, 152, 260, 265, 369, 375, 457)),
                                  (90, (17, 39, 119, 124, 203, 209, 270, 272, 306))):
            frame = render({}, weather, {}, rotation=rotation, theme='luminous_classic')
            for y in samples:
                self.assertNotEqual(frame.getpixel((0, y)), (0, 0, 0), (rotation, y))
                self.assertNotEqual(frame.getpixel((frame.width-1, y)), (0, 0, 0), (rotation, y))

    def test_luminous_top_overview_places_readings_left_and_loads_right(self):
        from renderer import font
        original = Painter.text
        weather = dict(city='Москва', temperature=-48, weather_code=71,
                       description='Снег', feels_like=-55, wind_kmh=120)
        metrics = dict(cpu_name='Ryzen 9 9950X3D', cpu_temp=109, cpu_percent=100,
                       cpu_mhz=5590, cpu_fan_rpm=30000, gpu_percent=87,
                       gpu_temp=108, gpu_clock_mhz=1800, gpu_fan_rpm=25000,
                       gpu_memory_used_mb=2458, gpu_memory_total_mb=8192,
                       ram_percent=99, ram_temp=107, ram_used_gb=256, ram_installed_gb=256)
        for rotation in (0, 90):
            calls, hardware = {}, []
            def record(p, x, y, text, size=18, color=None, bold=False, anchor='lt'):
                calls[str(text)] = x, y
                if (rotation == 0 and y >= 205) or (rotation == 90 and x >= 189 and y >= 53):
                    box = p.d.textbbox((round(x*2), round(y*2)), str(text),
                                       font=font(size, bold), anchor=anchor)
                    hardware.append((text, box))
                return original(p, x, y, text, size, color, bold, anchor)
            with patch.object(Painter, 'text', record):
                render(metrics, weather, {}, rotation=rotation, theme='luminous')
            for reading, temp, load in (('5,6', '109°', '100'), ('2,4', '108°', '30'),
                                        ('256,0', '107°', '99')):
                self.assertLess(calls[reading][0], calls[temp][0])
                self.assertLess(calls[temp][0], calls[load][0])
            if rotation == 0:
                self.assertLess(calls['Москва'][1], calls['5,6'][1])
            else:
                self.assertLess(calls['Москва'][0], calls['5,6'][0])
            for i, (text, a) in enumerate(hardware):
                for other, b in hardware[i+1:]:
                    self.assertFalse(min(a[2], b[2]) > max(a[0], b[0])
                                     and min(a[3], b[3]) > max(a[1], b[1]),
                                     (rotation, text, other, a, b))

    def test_luminous_fans_match_temperature_font_in_center_sensor_row(self):
        original = Painter.text
        metrics = dict(cpu_percent=42, cpu_fan_rpm=1230, cpu_temp=65,
                       gpu_percent=17, gpu_fan_rpm=850, gpu_temp=41,
                       gpu_memory_used_mb=2458, gpu_memory_total_mb=8192)
        for rotation in (0, 90):
            labels, icons = {}, []
            def record(p, x, y, text, size=18, *args, **kwargs):
                labels[str(text)] = (x, y, size)
                return original(p, x, y, text, size, *args, **kwargs)
            def icon(p, x, y, kind, color, size):
                if kind == 'fan':
                    icons.append((x, y, size))
            with patch.object(Painter, 'text', record), patch('luminous.hardware_icon', icon):
                render(metrics, {}, {}, rotation=rotation, theme='luminous')
            self.assertEqual(len(icons), 2)
            for (load, temp, fan), (x, y, size) in zip(
                (('42', '65°', '1230'), ('30', '41°', '850')), icons
            ):
                self.assertGreaterEqual(labels[load][2], 30)
                self.assertLessEqual(labels[load][2], 52)
                self.assertEqual(labels[temp][2], labels[fan][2])
                self.assertGreaterEqual(labels[temp][2], 20)
                self.assertLess(labels[fan][0], labels[load][0])
                self.assertLess(x+size, labels[fan][0])
                self.assertEqual(y+size, labels[fan][1])
                self.assertEqual(labels[temp][1], labels[fan][1])
                self.assertLess(labels[temp][0], x)
            self.assertFalse(any('rpm' in label.lower() for label in labels))

    def test_luminous_sensors_do_not_move_when_load_changes(self):
        original = Painter.text
        for rotation in (0, 90):
            positions = []
            for amount in (None, 9, 45, 100):
                readings = {}
                def record(p, x, y, text, size=18, *args, **kwargs):
                    if text in ('65°', '44°', '1230', '850'):
                        readings[text] = (x, y, size)
                    return original(p, x, y, text, size, *args, **kwargs)
                with patch.object(Painter, 'text', record):
                    render(dict(cpu_temp=65, gpu_temp=44, cpu_fan_rpm=1230, gpu_fan_rpm=850,
                                cpu_percent=amount, gpu_percent=amount, cpu_mhz=4900,
                                gpu_clock_mhz=1600), {}, {}, rotation=rotation, theme='luminous')
                self.assertEqual(len(readings), 4)
                positions.append(readings)
            self.assertEqual(positions, [positions[0]]*len(positions))

    def test_gpu_memory_takes_priority_over_frequency_in_overview(self):
        from renderer import video_memory
        self.assertEqual(video_memory(dict(gpu_memory_used_mb=0, gpu_memory_total_mb=8192)), ('0,0','8'))
        self.assertEqual(video_memory(dict(gpu_memory_used_mb=2131, gpu_memory_total_mb=8151)), ('2,1','8'))
        self.assertEqual(video_memory(dict(gpu_memory_total_mb=16384)), ('—','16'))
        self.assertIsNone(video_memory(dict(gpu_memory_used_mb=-1,gpu_memory_total_mb=0)))
        original = Painter.text
        for theme in ('luminous_classic', 'night', 'day'):
            for rotation in (0, 90):
                labels = []
                def record(p, x, y, text, *args, **kwargs):
                    labels.append(str(text))
                    return original(p, x, y, text, *args, **kwargs)
                with patch.object(Painter, 'text', record):
                    render(dict(cpu_mhz=4900, gpu_clock_mhz=1800, gpu_percent=45,
                                gpu_memory_used_mb=2458, gpu_memory_total_mb=8192),
                           {}, {}, rotation=rotation, theme=theme)
                self.assertIn('2,4', labels)
                self.assertTrue(any(s in labels for s in ('из 8 ГБ', '/ 8 ГБ')))
                self.assertNotIn('1,8', labels)
                self.assertIn('4,9', labels)
                self.assertIn('45', labels)

    def test_luminous_gpu_shows_only_memory_as_primary_reading_and_scale(self):
        from luminous import _meter
        m = dict(gpu_clock_mhz=1500,
                 gpu_memory_used_mb=2048, gpu_memory_total_mb=8192, gpu_percent=92)
        original = Painter.text
        for rotation in (0, 90):
            calls, bars = {}, []
            def record(p, x, y, text, *args, **kwargs):
                calls[str(text)] = (x, y)
                return original(p, x, y, text, *args, **kwargs)
            def meter(p, x, y, width, amount, color):
                bars.append((x, y, amount))
                return _meter(p, x, y, width, amount, color)
            with patch.object(Painter, 'text', record), patch('luminous._meter', meter):
                render(m, {}, {}, rotation=rotation, theme='luminous')
            self.assertLess(calls['2,0'][0], calls['25'][0])
            self.assertIn('из 8 ГБ', calls)
            self.assertNotIn('1,5', calls)
            self.assertNotIn('92', calls)
            self.assertEqual([item[2] for item in bars], [None, 25, None])
            self.assertEqual(bars[1][0], bars[0][0])
            self.assertLess(bars[0][1], bars[1][1])
            self.assertLess(bars[1][1], bars[2][1])

    def test_luminous_day_changes_material_not_layout(self):
        original = Painter.text
        for theme in ('luminous', 'luminous_classic'):
            timings = []
            for variant in (theme, theme+'_day'):
                calls = []
                def record(p, x, y, text, size=18, *args, **kwargs):
                    calls.append((x, y, text, size))
                    return original(p, x, y, text, size, *args, **kwargs)
                with patch.object(Painter, 'text', record):
                    frame = render(dict(cpu_percent=32, cpu_temp=59),
                                   dict(city='Москва', weather_code=71, is_day=False),
                                   {}, theme=variant, rotation=0, now=datetime(2026,11,24,12,0))
                timings.append(calls)
                pixel = frame.getpixel((0, 0))
                self.assertEqual(max(pixel) > 220, variant.endswith('_day'))
            self.assertEqual(*timings)

    def test_luminous_light_weather_has_blue_sky_in_both_layouts(self):
        weather = dict(temperature=18, weather_code=0, is_day=True)
        for rotation, x, y, min_ys in ((0, 180, 145, 420), (90, 83, 192, 296)):
            for theme, point in (('luminous_day', (x, y)),
                                 ('luminous_classic_day', (350 if rotation == 90 else 180, min_ys))):
                frame = render({}, weather, {}, theme=theme, rotation=rotation)
                r, g, b = frame.getpixel(point)
                self.assertGreater(b-r, 28, (rotation, theme, point, (r, g, b)))
                self.assertGreater(g-r, 14)

    def test_light_luminous_sky_reaches_the_top_behind_clock_and_title(self):
        for rotation in (0, 90):
            weather = dict(temperature=18, weather_code=2, is_day=True)
            frame = render({}, weather, {}, theme='luminous_day', rotation=rotation)
            for x in (0, frame.width//2, frame.width-1):
                r, g, b = frame.getpixel((x, 0))
                self.assertGreater(b-r, 75, (rotation, x, (r, g, b)))
                self.assertGreater(g-r, 45)

    def test_dark_luminous_sky_has_stars_even_when_cloudy(self):
        from weather_scene import sky_panorama
        night = sky_panorama(640, 410, 'cloud', False, 106, False, full_sky=True)
        no_stars = sky_panorama(640, 410, 'cloud', False, 106, False)
        self.assertGreater(night.getpixel((639, 0))[2], 30)
        self.assertEqual(no_stars.getpixel((639, 0)), (0, 0, 0))
        self.assertTrue(any(r > 115 and g > 125 and b > 160 for r, g, b in night.get_flattened_data()))
        self.assertIs(night, sky_panorama(640, 410, 'cloud', False, 106, False, full_sky=True))

    def test_luminous_top_overview_restores_gray_strips_flush_to_screen_edges(self):
        from datetime import datetime
        original = Painter.text
        for rotation, left, right, rows in ((0, 0, 319, (230, 322, 414)),
                                            (90, 189, 479, (78, 169, 260))):
            labels = {}
            def record(p, x, y, text, *args, **kwargs):
                labels[str(text)] = (x, y)
                return original(p, x, y, text, *args, **kwargs)
            with patch('luminous._panel') as panel:
                with patch.object(Painter, 'text', record):
                    frame = render({}, dict(weather_code=71, temperature=0), {}, rotation=rotation,
                                   theme='luminous', animation=.4, brand_name='Тест',
                                   now=datetime(2026, 11, 24, 20, 30))
            panel.assert_not_called()
            for y in rows:
                edge = frame.getpixel((right, y))
                self.assertGreater(edge[0], 20)
                self.assertLess(max(edge)-min(edge), 12)
                self.assertEqual(frame.getpixel((left+3, y)), edge)
            self.assertEqual(labels['Тест'], (10, 5))
            self.assertEqual(labels['20:30'], (frame.width-10, 6))

    def test_luminous_screen_edges_merge_into_true_black_on_all_pages(self):
        weather = dict(city='Москва', temperature=24, weather_code=0, is_day=True,
                       description='Ясно')
        metrics = dict(cpu_percent=100, gpu_percent=50, ram_percent=100, fps=999)
        for rotation in (0, 90, 180, 270):
            for page in ('graphs', 'thermal', 'clock', 'gaming'):
                with self.subTest(rotation=rotation, page=page):
                    frame = render(metrics, weather, {}, page=page, rotation=rotation,
                                   theme='luminous', now=datetime(2026, 11, 24, 20, 30),
                                   animation=.55)
                    w, h = frame.size
                    for box in ((0, 0, w, 2), (0, h-2, w, h), (0, 0, 2, h), (w-2, 0, w, h)):
                        self.assertIsNone(frame.crop(box).getbbox(), box)

    def test_luminous_music_shows_elapsed_and_remaining_on_page_and_overview(self):
        from io import BytesIO
        from PIL import Image
        data = BytesIO()
        Image.new('RGB',(64,64),'#C23850').save(data, format='PNG')
        media = dict(title='Track title', artist='Artist', album='Album', thumbnail_bytes=data.getvalue(),
                     is_playing=True, position_seconds=83, duration_seconds=180)
        original = Painter.text
        for rotation in (0,90):
            for page in ('media','overview'):
                labels = []
                def record(p,x,y,text,*args,**kwargs):
                    labels.append(str(text))
                    return original(p,x,y,text,*args,**kwargs)
                with patch.object(Painter,'text',record):
                    frame = render({}, {}, {}, page=page, rotation=rotation, theme='luminous',
                                   media=media, overview_content=dict(media, kind='media'))
                self.assertIn('Track title', labels)
                self.assertIn('1:23', labels)
                self.assertIn('−1:37', labels)
                r, g, b = frame.getpixel((95,110))
                self.assertGreater(r, g+80)
                self.assertGreater(r, b+65)

    def test_luminous_music_large_art_leaves_room_for_long_track_titles_and_time(self):
        from renderer import font
        original = Painter.text
        for rotation in (0, 90):
            boxes = {}
            def record(p,x,y,text,size=18,color=None,bold=False,anchor='lt'):
                if text:
                    boxes[str(text)] = p.d.textbbox((round(x*2),round(y*2)),str(text),
                                                    font=font(size,bold),anchor=anchor)
                return original(p,x,y,text,size,color,bold,anchor)
            track = dict(title='Очень длинное название трека из нескольких слов',
                         artist='Имя исполнителя', duration_seconds=180, position_seconds=29)
            with patch.object(Painter,'text',record), patch('luminous_music._art') as art:
                frame = render({}, {}, {}, page='media', rotation=rotation, theme='luminous', media=track)
            size = art.call_args.args[3]
            self.assertGreaterEqual(size, 288 if rotation == 0 else 244)
            self.assertLess(boxes['Имя исполнителя'][3], boxes['0:29'][1])
            self.assertLessEqual(boxes['−2:31'][3], frame.height*2)

    def test_luminous_music_aligns_art_title_and_transport_to_shared_insets(self):
        for rotation in (0, 90):
            landscape = rotation == 90
            w, h = (480,320) if landscape else (320,480)
            media = dict(title='Track', position_seconds=30, duration_seconds=180)
            for page in ('overview','media'):
                with patch('luminous_music._art') as art, patch('luminous_music._type') as text, \
                     patch('luminous_music._timeline') as timeline:
                    text.return_value = 400
                    render({}, {}, {}, rotation=rotation, page=page, theme='luminous',
                           media=media, overview_content=dict(media,kind='media'))
                _, ax, ay, size, _ = art.call_args.args
                _, tx, _, tw, *_ = text.call_args.args
                _, bx, _, bw, *_ = timeline.call_args.args
                self.assertEqual((tx,tw), (bx,bw))
                if page == 'media':
                    self.assertEqual(ax, 16)
                    self.assertEqual(tx+tw, w-16)
                    self.assertEqual(timeline.call_args.kwargs['bottom'], h-16)
                    if landscape:
                        self.assertEqual(tx-ax-size, 16)
                        self.assertEqual(ay+size, h-16)
                    else:
                        self.assertEqual((tx,tw), (ax,size))
                elif landscape:
                    self.assertEqual(tx, ax)
                    self.assertEqual(tx+tw, 178-12)
                else:
                    self.assertEqual(ax, 12)
                    self.assertEqual(tx-ax-size, 12)
                    self.assertEqual(ay+size, 205-12)
                    self.assertEqual(tx+tw, w-12)

    def test_luminous_music_omits_fake_progress_and_handles_unreadable_art(self):
        from renderer import _media_progress
        self.assertEqual(_media_progress(dict(position_seconds=999,duration_seconds=180), remaining=True),
                         (100, '3:00', '0:00'))
        original = Painter.text
        for rotation in (0, 90):
            for theme in ('luminous','luminous_day'):
                for media in ({}, dict(title='Трек', artist='Артист', thumbnail_bytes=b'broken')):
                    labels = []
                    def record(p,x,y,text,*args,**kwargs):
                        labels.append(str(text))
                        return original(p,x,y,text,*args,**kwargs)
                    with patch.object(Painter,'text',record):
                        frame = render({}, {}, {}, page='media', rotation=rotation, theme=theme, media=media)
                    self.assertEqual(frame.size, (320,480) if rotation == 0 else (480,320))
                    self.assertNotIn('0:00',labels)
                    self.assertEqual('Время трека недоступно' in labels, bool(media))

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
