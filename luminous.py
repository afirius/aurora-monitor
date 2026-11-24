"""Luminous: native-pixel layouts for the seven USB display pages."""

from datetime import datetime
from functools import lru_cache

from PIL import Image, ImageDraw, ImageOps

from glass_material import LUMINOUS_BOTTOM_THEMES, LUMINOUS_DAY_THEMES, bezel_fade, overview_strip
from renderer import (
    _DAY_NAMES_RU, _draw_month,
    _uptime_label, _weather_reading, compact_weather_description, hardware_icon,
    header, load_color, number, overview, overview_content_card, short_model, temperature_color, value, video_memory,
)
from weather_scene import draw_scene, sky_kind, sky_panorama
from luminous_music import draw_music, draw_music_peek


_DATE_MONTHS = (
    'ЯНВАРЯ', 'ФЕВРАЛЯ', 'МАРТА', 'АПРЕЛЯ', 'МАЯ', 'ИЮНЯ',
    'ИЮЛЯ', 'АВГУСТА', 'СЕНТЯБРЯ', 'ОКТЯБРЯ', 'НОЯБРЯ', 'ДЕКАБРЯ',
)
_TITLE = {
    'graphs': ('Нагрузка', 'Последняя минута'),
    'thermal': ('Температуры', 'Последняя минута'),
    'weather': ('Погода', 'Сейчас и на три дня'),
    'clock': ('Часы', ''),
    'media': ('Музыка', ''),
    'gaming': ('Игра', 'Производительность'),
}
_HEADER = 67
_OVERVIEW_HEADER = 53


def _header(p, page, now, brand, alert):
    title, subtitle = _TITLE.get(page, (brand, ''))
    date = f'{_DAY_NAMES_RU[now.weekday()].upper()[:2]}, {now.day} {_DATE_MONTHS[now.month-1]}'
    x, dy = (10, -11) if page == 'overview' else (15, 0)
    if page == 'media':
        x, dy = 16, -3
    p.fit(x, 16+dy, title, p.w-(2*x+(89 if page != 'clock' else 0)), 29, bold=True)
    if page != 'clock':
        time = now.strftime('%H:%M')
        p.text(p.w-x, 17+dy, time, 28, p.c['text'], False, 'rt')
    if alert:
        short = str(alert).replace(' — высокая температура', '').replace('Процессор:', 'CPU:').replace('Видеокарта:', 'GPU:')
        warning = '#A02535' if p.theme in LUMINOUS_DAY_THEMES else '#FFADA9'
        p.ellipse((x+1, 55+dy, x+7, 61+dy), warning)
        p.fit(x+13, 51+dy, short, p.w-2*x-13, 13, warning, True)
    else:
        p.fit(x+1, 46+dy, subtitle or date, p.w-2*x-2, 14, p.c['muted'], True)


def _panel(p, box):
    p.rect(box, radius=21)


def _section_label(p, x, y, title, color):
    p.text(x, y, title, 15, color, True)


def _metric(p, x, y, temp, size=16):
    n = number(temp)
    shade = temperature_color(p, n)
    hardware_icon(p, x - 4, y, 'temp', shade, size)
    p.text(x + size - 5, y + 1, value(n, '°'), size, shade, True)


def _frequency(m, key):
    mhz = number(m.get('cpu_mhz' if key == 'cpu' else 'gpu_clock_mhz'))
    if mhz is None:
        return '—'
    if 0 < mhz < 100:
        return value(mhz, ' МГц')
    return value(mhz / 1000, ' ГГц', 1).replace('.', ',')


def _load_ink(p, percent, accent):
    reading = number(percent)
    return accent if reading is not None and round(reading) < 80 else load_color(p, reading)


@lru_cache(maxsize=32)
def _polished_track(width, color):
    rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
    stops = [(round(c*.63), c, round(c+(255-c)*.24)) for c in rgb]
    tile = Image.new('RGB', (width, 6))
    draw = ImageDraw.Draw(tile)
    for y in range(6):
        highlight = 1 - abs(y-1)/4
        for x in range(width):
            t = x / max(1, width-1)
            draw.point((x, y), fill=tuple(round(a+(b-a)*t+(c-b)*highlight)
                                         for a, b, c in stops))
    return tile


def _meter(p, x, y, width, amount, color):
    p.rect((x, y, x+width, y+3), '#BCC7D9' if p.theme in LUMINOUS_DAY_THEMES else '#38383F', 1.5)
    n = number(amount)
    if n is None or n <= 0:
        return
    filled = min(width*2, max(6, round(width*2*(min(n, 100)/100))))
    # Crop a stable material, so updating a reading does not shift its highlight.
    tile = _polished_track(width*2, color)
    mask = Image.new('L', (filled, 6))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, filled-1, 5), 3, fill=255)
    p.im.paste(tile.crop((0, 0, filled, 6)), (2*x, 2*y), mask)


def _compute_card(p, box, key, m):
    x, y, x2, y2 = box
    color = p.c['primary' if key == 'cpu' else 'secondary']
    kind = 'CPU' if key == 'cpu' else 'GPU'
    load = m.get(key+'_percent')
    ink = _load_ink(p, load, color)
    model = short_model(m.get(key+'_name'), key)
    vram = video_memory(m) if key == 'gpu' else None
    freq = f'{vram[0]} / {vram[1]} ГБ' if vram else ('—' if key == 'gpu' else _frequency(m,key))
    fan = value(m.get(key+'_fan_rpm'), ' rpm')
    left, right = x+(20 if x == 0 else 13), x2-19
    span = right-left
    _section_label(p, left, y+12, kind, color)
    p.fit(left+41, y+9, model, span-41, 20, p.c['text'], True)
    shown = value(load, '%')
    temp = m.get(key+'_temp')
    temp_size = 24 if span >= 245 else 22
    # Reserve the actual right-hand string widths, not space for a fixed example.
    right_metrics = max(min(92, p.measure(freq, 18)), min(97, p.measure(fan, 18)))
    temp_width = temp_size-5 + p.measure(value(temp, '°'), temp_size, True)
    temp_x = min(left + round(span*.405), right - right_metrics - temp_width - 5)
    size = 43
    while size > 33 and p.measure(shown, size) > temp_x-left-6:
        size -= 1
    p.fit(left, y+33, shown, temp_x-left-6, size, ink)
    _metric(p, temp_x, y+42, temp, temp_size)
    p.fit(right, y+31, freq, 92, 16 if key == 'gpu' else 18, p.c['muted'], anchor='rt')
    p.fit(right, y+53, fan, 97, 18, p.c['muted'], anchor='rt')
    _meter(p, left, y2-8, span, load, ink)


def _overview_hardware(p, box, kind, m):
    x, y, x2, y2 = box
    left, right = x+12, x2-12
    width = right-left
    mem = kind == 'ram'
    gpu = kind == 'gpu'
    color = p.c[{'cpu': 'primary', 'gpu': 'secondary', 'ram': 'memory'}[kind]]
    load = m.get(kind+'_percent')
    if gpu:
        used = number(m.get('gpu_memory_used_mb'))
        capacity = number(m.get('gpu_memory_total_mb'))
        load = (min(used, capacity)/capacity*100
                if used is not None and used >= 0 and capacity is not None and capacity > 0 else None)
    ink = _load_ink(p, load, color)
    if mem:
        installed = number(m.get('ram_installed_gb'))
        capacity = value(installed if installed is not None else m.get('ram_total_gb'), ' ГБ')
        rate = value(m.get('ram_speed_mt'), ' МГц')
        title = ' '.join(part for part in (capacity, m.get('ram_type') or 'ОЗУ', rate) if part != '—')
        reading, unit = value(m.get('ram_used_gb'), digits=1).replace('.', ','), 'ГБ'
    else:
        title = short_model(m.get(kind+'_name'), kind)
        if gpu:
            reading, total = video_memory(m) or ('—', '—')
            unit = 'ГБ'
        else:
            reading, _, unit = _frequency(m, kind).partition(' ')
    reading_size = 31 if mem or gpu else 33
    unit_size = 14
    baseline = y2-21
    amount = number(load)
    pct_size = 16
    pct_width = p.measure('%', pct_size, True)+3
    load_text = value(load)
    load_size = 50 if mem or gpu else 52
    if amount is not None and round(amount) >= 100:
        load_size = 43 if mem or gpu else 45
    temp = number(m.get(kind+'_temp'))
    temp_text = value(temp, '°') if not mem or (temp is not None and 0 < temp <= 110) else None
    fan = value(m.get(kind+'_fan_rpm')) if not mem else None
    # The three slots depend only on display geometry, never on the percent.
    left_limit = round(width*(.35 if mem else .263))
    load_left = right-round(width*.275)
    load_width = right-pct_width-load_left
    sensor_left = left+left_limit+4
    fan_left = sensor_left+round((load_left-sensor_left)*.49)
    icon_size = 13
    sensor_size = 22
    temp_x, fan_x = sensor_left+icon_size+1, fan_left+icon_size+1
    temp_limit = (fan_left if fan is not None else load_left)-temp_x-3
    fan_limit = load_left-fan_x-3
    while sensor_size > 15 and ((temp_text and p.measure(temp_text,sensor_size,True)>temp_limit)
                                or (fan is not None and p.measure(fan,sensor_size,True)>fan_limit)):
        sensor_size -= 1
    while load_size > 33 and p.measure(load_text,load_size,True)>load_width:
        load_size -= 1
    unit_width = p.measure(unit, unit_size)+4 if unit else 0
    while reading_size > 21 and p.measure(reading,reading_size,True)+unit_width>left_limit:
        reading_size -= 1
    title_width = load_left-left-10
    title_size = 19
    while title_size > 16 and p.measure(title, title_size, True) > title_width:
        title_size -= 1
    p.fit(left, y+10, title, title_width, title_size, bold=True)
    if gpu:
        p.fit(left, y+31, f'из {total} ГБ' if total != '—' else 'Видеопамять',
              title_width, 14, p.c['muted'])
    p.fit(left, baseline, reading, left_limit-unit_width, reading_size, p.c['text'], True, 'ls')
    if unit:
        unit_x = left+min(left_limit-unit_width,p.measure(reading,reading_size,True))+4
        p.text(unit_x, baseline, unit, unit_size, p.c['muted'], anchor='ls')
    p.fit(right-pct_width, baseline+2, load_text, load_width, load_size, ink, True, 'rs')
    if amount is not None:
        p.text(right, baseline, '%', pct_size, ink, True, 'rs')
    if temp_text:
        heat = temperature_color(p, temp)
        hardware_icon(p, sensor_left, baseline-icon_size, 'temp', heat, icon_size)
        p.fit(temp_x, baseline, temp_text, temp_limit, sensor_size, heat, True, 'ls')
    if fan is not None:
        hardware_icon(p, fan_left, baseline-icon_size, 'fan', p.c['muted'], icon_size)
        p.fit(fan_x, baseline, fan, fan_limit, sensor_size, p.c['muted'], True, 'ls')
    _meter(p, left, y2-9, width, load, ink)


def _overview_top(p, m, weather, landscape, content, phase):
    if landscape:
        hero = (0, 0, 178, p.h)
        cpu, gpu, ram = (189, _OVERVIEW_HEADER, p.w, 144), (189, 144, p.w, 235), (189, 235, p.w, p.h)
    else:
        hero = (0, 0, p.w, 205)
        cpu, gpu, ram = (0, 205, p.w, 297), (0, 297, p.w, 389), (0, 389, p.w, p.h)
    if isinstance(content, dict) and content.get('kind') in ('gaming', 'media'):
        x, _, x2, y2 = hero
        if content['kind'] == 'media':
            draw_music_peek(p,(x,_OVERVIEW_HEADER,x2,y2),content,landscape)
        elif landscape:
            name = content.get('process_name') if content['kind'] == 'gaming' else content.get('title')
            p.wrap(x+16, _OVERVIEW_HEADER+51, name or ('Игра' if content['kind'] == 'gaming' else 'Музыка'),
                   x2-x-32, 20, 3, bold=True)
            detail = value(content.get('fps'), ' FPS') if content['kind'] == 'gaming' else content.get('artist')
            p.fit(x+16, y2-40, detail or '', x2-x-32, 17, p.c['muted'])
        else:
            overview_content_card(p, content, (x+6, _OVERVIEW_HEADER+20, x2-6, y2-10), False,
                                  surface_box=(x, _OVERVIEW_HEADER, x2, y2))
    else:
        _sky_tile(p, hero, weather, phase, landscape, flush=True)
    for box, kind in ((cpu, 'cpu'), (gpu, 'gpu'), (ram, 'ram')):
        x, y, x2, y2 = box
        p.im.paste(overview_strip(x2-x, y2-y, 'hardware', p.theme in LUMINOUS_DAY_THEMES), (2*x, 2*y))
        _overview_hardware(p, box, kind, m)


def _sky_tile(p, box, weather, phase=None, tall=False, *, flush=False):
    x, y, x2, y2 = box
    width, height = x2-x, y2-y
    code = number(weather.get('weather_code'))
    kind = sky_kind(int(code) if code is not None else None)
    day = weather.get('is_day') is not False
    top = max(0, (_OVERVIEW_HEADER if flush else _HEADER)-y)
    light = p.theme in LUMINOUS_DAY_THEMES
    full_sky = flush
    # On a wide panel the blue continues under the clock, not only over the
    # left weather column. The gray hardware strips are painted on top below.
    sky_width = p.w if full_sky else width
    tile = sky_panorama(2*sky_width, 2*height, kind, day, 2*top, tall,
                        light=light, full_sky=full_sky).copy()
    # Keep the moving scene separate from the text and from the desktop canvas.
    sw = min(width-8, 154 if tall else 162)
    sh = round(sw * 152 / 240)
    art = draw_scene(kind, day, number(phase), sw*2, sh*2)
    if art is not None:
        sx = (width-sw-4)*2
        sy = (height-49-sh if tall else top + round((height-top-sh)*.40))*2
        tile.paste(art, (sx, sy), art)
    mask = None if flush else bezel_fade(p.w, p.h, box)
    p.im.paste(tile, (2*x, 2*y), mask)
    y += top
    fg, soft = (p.c['text'], p.c['muted']) if light else ('#FCFAF7', '#E0DEE4')
    p.fit(x+17, y+10, weather.get('city') or 'Погода', width-34, 22, fg, True)
    temp = value(weather.get('temperature'))
    description = compact_weather_description(weather.get('description'))
    caption_w = width-32 if tall else width-sw+14
    wrap = not tall and p.measure(description, 18, True) > caption_w
    _weather_reading(p, x+16, y+(32 if wrap else 35), temp,
                     width-32 if tall else width-sw-4, 71 if tall else (63 if wrap else 67), fg)
    if tall:
        p.wrap(x+17, y+96, description, caption_w, 18, 2, fg, True)
    elif wrap:
        p.wrap(x+17, y+81, description, caption_w, 17, 2, fg, True)
    else:
        p.fit(x+17, y+93, description, caption_w, 18, fg, True)
    feels = value(weather.get('feels_like'), '°')
    wind = value(weather.get('wind_kmh'), ' км/ч')
    if tall:
        p.line([(x+16, y2-53), (x2-16, y2-53)], '#4B5366', .5)
    stale = weather.get('stale') and temp != '—'
    if feels != '—':
        p.fit(x+16, y2-(45 if tall else 24), f'Ощущается {feels}',
              width-32 if tall else width*.53, 16, soft, bold=True)
    if stale or wind != '—':
        p.fit(x+16 if tall else x2-16, y2-(25 if tall else 24),
              'Из кэша' if stale else f'Ветер {wind}',
              width-32 if tall else width*.45-15, 16, soft,
              anchor='lt' if tall else 'rt')


def _graphs(p, m, history, landscape, thermal):
    unit, peak, suffix = ('°', 120, '_temp') if thermal else ('%', 100, '_percent')
    for i, (key, label, color) in enumerate((
        ('cpu', 'Процессор', p.c['primary']), ('gpu', 'Видеокарта', p.c['secondary']),
    )):
        if landscape:
            half = p.w//2
            x, y, x2, y2 = (0 if not i else half+5), _HEADER, (half-5 if not i else p.w), p.h
        else:
            half = (_HEADER+p.h)//2
            x, y, x2, y2 = 0, (_HEADER if not i else half+5), p.w, (half-5 if not i else p.h)
        _panel(p, (x, y, x2, y2))
        _section_label(p, x+14, y+15, label, color)
        metric = key+suffix
        reading = number(m.get(metric))
        p.fit(x2-17, y+9, value(reading, unit), x2-x-117, 43, color, True, 'rt')
        p.fit(x+14, y+38, 'Температура' if thermal else 'Использование',
              x2-x-115, 13, p.c['muted'])
        try:
            series = list(history.get(metric, ()))[-60:]
        except TypeError:
            series = []
        if reading is not None:
            series.append(reading)
        plot = (x+44, y+67, x2-15, y2-36)
        for portion, label in ((0, f'{peak}'), (.5, f'{peak//2}'), (1, '0')):
            yy = plot[1] + (plot[3]-plot[1])*portion
            p.text(x+34, yy, label, 15, p.c['muted'], anchor='rm')
        _graph_area(p, plot, series, color, peak)
        for i in (1, 2, 3):
            gx = plot[0]+(plot[2]-plot[0])*i/4
            for yy in range(plot[1], plot[3], 7):
                p.line([(gx,yy),(gx,min(yy+2,plot[3]))], p.c['line'], .5)
        p.graph(plot, series, color, peak)
        if reading is None and not any(number(v) is not None for v in series):
            p.text((x+x2)/2+12, (plot[1]+plot[3])/2, 'Нет данных', 15, p.c['muted'], anchor='mm')
        p.text(x+44, y2-23, '−60 с', 15, p.c['muted'])
        p.text(x2-14, y2-23, 'Сейчас', 15, p.c['muted'], anchor='rt')


def _graph_area(p, box, series, color, high):
    x, y, x2, y2 = box
    w, h = 2*(x2-x)+1, 2*(y2-y)+1
    mask = Image.new('L', (w, h))
    d = ImageDraw.Draw(mask)
    series = series[-60:]
    points = []
    for i, sample in enumerate([*series, None]):
        n = number(sample)
        if n is not None:
            points.append(((60-len(series)+i)*(w-1)/59, (h-1)*(1-min(high, max(0, n))/high)))
        else:
            if len(points) > 1:
                d.polygon([*points, (points[-1][0], h-1), (points[0][0], h-1)], fill=255)
            points = []
    fade = ImageOps.invert(Image.linear_gradient('L')).resize((w, h))
    p.im.paste(color, (2*x, 2*y, 2*x+w, 2*y+h),
               Image.composite(fade.point(lambda v: round(v*.34)), Image.new('L', (w, h)), mask))


def _forecast(p, weather, landscape):
    hero = (0, 0, 215, p.h) if landscape else (0, 0, p.w, 246)
    _sky_tile(p, hero, weather, tall=landscape, flush=True)
    x, y, x2, y2 = (225, _HEADER, p.w, p.h) if landscape else (0, 253, p.w, p.h)
    _panel(p, (x, y, x2, y2))
    p.text(x+14, y+13, 'Ближайшие дни', 21, bold=True)
    humidity = value(weather.get('humidity'), '%')
    p.text(x+14, y+38, f'Влажность {humidity}', 15, p.c['muted'])
    updated = number(weather.get('updated_at'))
    if updated is not None and updated > 0 and number(weather.get('temperature')) is not None:
        try:
            stamp = datetime.fromtimestamp(updated).strftime('%H:%M')
        except (ValueError, OSError, OverflowError):
            stamp = None
        if stamp:
            p.text(x2-14, y+39, f"Кэш {stamp}" if weather.get('stale') else stamp,
                   14, p.c['muted'], anchor='rt')
    daily = weather.get('daily')
    days = [d for d in daily[:3] if isinstance(d, dict)] if isinstance(daily, list) else []
    extents = [v for day in days for v in (number(day.get('min')), number(day.get('max'))) if v is not None]
    lo, hi = (min(extents), max(extents)) if extents else (0, 0)
    if not days:
        p.text(x+14, y+100, 'Прогноз недоступен', 17, p.c['muted'])
    for i, day in enumerate(days):
        top = y+60+i*(53 if landscape else 47)
        p.line([(x+14, top), (x2-14, top)], p.c['line'])
        try:
            date = datetime.strptime(str(day.get('date', '')), '%Y-%m-%d')
            label = _DAY_NAMES_RU[date.weekday()].title()[:2] + f' · {date.day}'
        except ValueError:
            label = '—'
        p.text(x+15, top+9, label, 16, bold=True)
        probability = value(day.get('precipitation_probability'), '%')
        if probability != '—':
            p.text(x+15, top+28, probability, 14, p.c['primary'])
        p.weather_icon(x+68, top+8, day.get('weather_code'), 30)
        low, high = number(day.get('min')), number(day.get('max'))
        p.fit(x+141, top+17, value(low, '°'), 43, 17, p.c['muted'], anchor='rt')
        p.fit(x2-14, top+16, value(high, '°'), 44, 19, p.c['text'], True, 'rt')
        start, end = x+150, x2-66
        yy = top+24
        p.rect((start, yy-1.5, end, yy+1.5), p.c['line'], 1.5)
        if low is not None and high is not None:
            a, b = ((low-lo)/(hi-lo), (high-lo)/(hi-lo)) if hi != lo else (.5, .5)
            xx, xx2 = sorted((start+(end-start)*a, start+(end-start)*b))
            p.rect((xx-1.5, yy-2, xx2+1.5, yy+2), p.c['primary'], 2)


def _clock(p, m, now, landscape):
    x2, y2 = (207, p.h) if landscape else (p.w, 211)
    _panel(p, (0, _HEADER, x2, y2))
    center = x2/2
    stamp = now.strftime('%H:%M')
    size = 74 if landscape else 92
    while p.measure(stamp,size,True)>x2-24 and size>38:
        size -= 1
    p.text(center, 99 if landscape else 79, stamp, size, p.c['text'], True, 'mt')
    date = f'{_DAY_NAMES_RU[now.weekday()]} · {now.day} {_DATE_MONTHS[now.month-1].lower()}'
    p.fit(center, 181 if landscape else 163, date, x2-28, 17, p.c['primary'], True, 'mt')
    p.fit(center, y2-27, _uptime_label(m), x2-40, 15, p.c['muted'], anchor='mt')
    _draw_month(p, (217, _HEADER, p.w, p.h) if landscape else (0, 220, p.w, p.h), now)


def _game(p, m, landscape):
    x2, y2 = (214, p.h) if landscape else (p.w, 273)
    _panel(p, (0, _HEADER, x2, y2))
    fps = number(m.get('fps'))
    _section_label(p, 23, 84, 'Частота кадров', p.c['primary'])
    p.fit(21, 108, value(fps), x2-75, 72, p.c['primary'], True)
    if fps is not None:
        p.text(x2-21, 150, 'FPS', 16, p.c['muted'], True, 'rt')
    if fps is None:
        rtss = 'RTSS не запущен' in str(m.get('gaming_status') or '')
        p.wrap(23, 186, 'Запустите RTSS и игру' if rtss else 'Откройте игру — FPS появится здесь',
               x2-44, 16, 3, p.c['muted'])
    else:
        p.fit(23, 185, m.get('process_name') or 'Активная игра', x2-44, 17, bold=True)
        p.line([(23, y2-61), (x2-15, y2-61)], p.c['line'], .5)
        p.text(23, y2-43, 'Время кадра', 15, p.c['muted'])
        p.fit(x2-15, y2-24 if landscape else y2-44,
              value(m.get('frametime_ms'), ' мс', 1).replace('.', ','),
              x2-42, 19, p.c['text'], anchor='rt')
    for i, key in enumerate(('cpu', 'gpu')):
        box = ((224, _HEADER, p.w, 187) if not i else (224, 197, p.w, p.h)) if landscape else (
            (0, 282, p.w, 376) if not i else (0, 386, p.w, p.h))
        _panel(p, box)
        _compute_card(p, box, key, m)


def draw(p, page, metrics, weather, history, now, media, brand, landscape, content, animation, alert):
    if page in ('graphs', 'thermal'):
        _graphs(p, metrics, history, landscape, page == 'thermal')
    elif page == 'weather':
        _forecast(p, weather, landscape)
    elif page == 'clock':
        _clock(p, metrics, now, landscape)
    elif page == 'media':
        draw_music(p, media, landscape)
    elif page == 'gaming':
        _game(p, metrics, landscape)
    elif p.theme in LUMINOUS_BOTTOM_THEMES:
        overview(p, metrics, weather, landscape, overview_content=content, animation=animation)
        header(p, 'overview', now, brand, alert)
        return
    else:
        _overview_top(p, metrics, weather, landscape, content, animation)
    _header(p, page, now, brand, alert)
