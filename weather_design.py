"""Atmospheric weather page for the 480×320 / 320×480 USB panel."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import math
import re

from PIL import Image, ImageChops, ImageDraw, ImageFilter


WEEKDAYS = ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")
RAIN = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
SNOW = {71, 73, 75, 77, 85, 86}
FOG = {45, 48}
STORM = {95, 96, 99}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _temperature(value):
    numeric = _number(value)
    return "—" if numeric is None else f"{numeric:.0f}°"


def _metric(value, suffix):
    numeric = _number(value)
    return "—" if numeric is None else f"{numeric:.0f}{suffix}"


def _colors(weather):
    code = _number(weather.get("weather_code"))
    is_day = weather.get("is_day")
    if code is None or is_day is None:
        return "#172C46", "#254660", "#91BBCF", "#DCECF5"
    if not is_day:
        return "#07142B", "#1B365E", "#8199CC", "#FFF0CA"
    if int(code) in STORM:
        return "#16223D", "#475878", "#A9BDD8", "#FFE1A6"
    if int(code) in RAIN or int(code) in FOG:
        return "#173956", "#3F7290", "#9ED6E6", "#E7F5FF"
    if int(code) in SNOW:
        return "#2B5879", "#6895AC", "#D0ECF5", "#FAFCFF"
    return "#125A91", "#328EAB", "#9BE0EC", "#E7F5FF"


@lru_cache(maxsize=12)
def _sky_image(w, h, top, bottom, haze, is_day):
    image = Image.new("RGB", (w * 2, h * 2))
    draw = ImageDraw.Draw(image)
    first = tuple(int(top[i:i + 2], 16) for i in (1, 3, 5))
    last = tuple(int(bottom[i:i + 2], 16) for i in (1, 3, 5))
    for y in range(h * 2):
        t = y / max(1, h * 2 - 1)
        eased = t * t * (3 - 2 * t)
        rgb = tuple(round(a * (1 - eased) + b * eased) for a, b in zip(first, last))
        draw.line((0, y, w * 2, y), fill=rgb)

    # A diffused glow and horizon add depth without borrowing a stock image.
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    glow = tuple(int(haze[i:i + 2], 16) for i in (1, 3, 5))
    cx = w * (0.79 if w > h else 0.76) * 2
    cy = (108 if w > h else 129) * 2
    radius = (125 if w > h else 105) * 2
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
              fill=(*glow, 67))
    layer = layer.filter(ImageFilter.GaussianBlur(56))
    image = Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.ellipse((-152, (h - 90) * 2, (w + 120) * 2, (h + 170) * 2),
                 fill="#2C7894" if is_day else "#284869")
    draw.ellipse((-300, (h - 49) * 2, (w + 80) * 2, (h + 170) * 2),
                 fill="#245B7B" if is_day else "#1D3A61")
    return image


def _sky(p, weather):
    top, bottom, haze, _ = _colors(weather)
    p.im = _sky_image(p.w, p.h, top, bottom, haze,
                      weather.get("is_day") is True).copy()
    p.d = ImageDraw.Draw(p.im)


def _cloud(p, x, y, scale=1, color="#E7F5FF"):
    def box(a, b, c, d):
        return (x + a * scale, y + b * scale, x + c * scale, y + d * scale)
    p.ellipse(box(4, 28, 60, 76), color)
    p.ellipse(box(30, 5, 100, 76), color)
    p.ellipse(box(77, 31, 130, 76), color)
    p.rect(box(16, 48, 119, 78), color, 13 * scale)


def _crescent(p, cx, cy, radius):
    """Paint the lit part of a thin waxing moon without an occluding disk."""
    pixel_radius = round(radius * 2)
    side = round(pixel_radius * 3.1)
    center = side // 2
    outer = Image.new("L", (side, side))
    inner = Image.new("L", (side, side))
    ImageDraw.Draw(outer).ellipse((center - pixel_radius, center - pixel_radius,
                                  center + pixel_radius, center + pixel_radius), fill=255)
    # The overlap is removed from a mask, so the sky remains visible through
    # the hollow of the crescent rather than showing a pasted dark circle.
    shift_x = round(pixel_radius * .32)
    shift_y = round(pixel_radius * .07)
    ImageDraw.Draw(inner).ellipse((center - pixel_radius - shift_x,
                                  center - pixel_radius - shift_y,
                                  center + pixel_radius - shift_x,
                                  center + pixel_radius - shift_y), fill=255)
    moon_mask = ImageChops.subtract(outer, inner)
    left, top = round(cx * 2) - center, round(cy * 2) - center
    halo = moon_mask.filter(ImageFilter.GaussianBlur(round(radius * .46)))
    halo = halo.point(lambda alpha: round(alpha * .3))
    p.im.paste((255, 224, 163), (left, top), halo)
    moon = Image.new("RGB", (side, side))
    moon_draw = ImageDraw.Draw(moon)
    for y in range(side):
        blend = y / max(1, side - 1)
        moon_draw.line((0, y, side, y), fill=(255, round(251 - 22 * blend),
                                            round(227 - 45 * blend)))
    p.im.paste(moon, (left, top), moon_mask)
    p.d = ImageDraw.Draw(p.im)


def _night_stars(p, landscape):
    stars = ((325, 67, 3.5), (455, 76, 2.3), (339, 173, 2.0),
             (463, 176, 2.8)) if landscape else (
                 (201, 92, 3.2), (304, 82, 2.5), (193, 162, 2.0),
                 (299, 189, 2.7), (225, 183, 1.5))
    for cx, cy, radius in stars:
        if cx + radius >= p.w:
            continue
        x, y, r = cx * 2, cy * 2, radius * 2
        points = [(x, y - r), (x + r * .16, y - r * .16),
                  (x + r, y), (x + r * .16, y + r * .16),
                  (x, y + r), (x - r * .16, y + r * .16),
                  (x - r, y), (x - r * .16, y - r * .16)]
        p.d.polygon(points, fill="#FFF3CE")
    dots = ((184, 105), (215, 73), (290, 196), (311, 151)) if not landscape else (
        (306, 118), (352, 47), (438, 192), (466, 108))
    for x, y in dots:
        p.d.ellipse((x * 2 - 1, y * 2 - 1, x * 2 + 1, y * 2 + 1),
                    fill="#C4DBEE")


def _glass_panel(p, box, *, night):
    x, y, x2, y2 = (round(v * 2) for v in box)
    layer = Image.new("RGBA", (x2 - x, y2 - y))
    draw = ImageDraw.Draw(layer)
    color = (13, 33, 65, 190) if night else (13, 75, 108, 172)
    draw.rounded_rectangle((0, 0, x2 - x - 1, y2 - y - 1),
                           radius=38, fill=color,
                           outline=(211, 231, 253, 55), width=2)
    base = p.im.crop((x, y, x2, y2)).convert("RGBA")
    p.im.paste(Image.alpha_composite(base, layer).convert("RGB"), (x, y))
    p.d = ImageDraw.Draw(p.im)


def _hero(p, weather, landscape):
    code = _number(weather.get("weather_code"))
    if code is None:
        return
    code = int(code)
    is_day = weather.get("is_day") is not False
    x = 314 if landscape else 224
    y = 74 if landscape else 78
    scale = 1.1 if landscape else 0.73
    light = "#E7F5FF" if is_day else "#DDE9F7"
    orb = "#FFE3A2" if is_day else "#FFF0CC"
    center_x, center_y = x + 57 * scale, y + 38 * scale
    if code <= 2:
        if is_day:
            for angle in range(0, 360, 45):
                t = math.radians(angle)
                p.line([(center_x + 38 * scale * math.cos(t),
                         center_y + 38 * scale * math.sin(t)),
                        (center_x + 51 * scale * math.cos(t),
                         center_y + 51 * scale * math.sin(t))], orb, 3 * scale)
            p.ellipse((center_x - 30 * scale, center_y - 30 * scale,
                       center_x + 30 * scale, center_y + 30 * scale), orb)
        else:
            moon_x, moon_y, moon_radius = ((399, 119, 47) if landscape
                                          else (257, 129, 42))
            _crescent(p, moon_x, moon_y, moon_radius)
            _night_stars(p, landscape)
    if code == 2:
        _cloud(p, x - 5 * scale, y + 43 * scale, scale * .78, light)
    elif code >= 3:
        _cloud(p, x - 8 * scale, y + 12 * scale, scale, light)
        if code in RAIN:
            for dx in (26, 54, 82, 109):
                p.line([(x + dx * scale, y + 96 * scale),
                        (x + (dx - 7) * scale, y + 113 * scale)], "#9FECFA", 3 * scale)
        elif code in SNOW:
            for dx, dy in ((22, 99), (60, 110), (95, 98)):
                ox, oy = x + dx * scale, y + dy * scale
                p.line([(ox - 5 * scale, oy), (ox + 5 * scale, oy)], "#EDF9FF", 2 * scale)
                p.line([(ox, oy - 5 * scale), (ox, oy + 5 * scale)], "#EDF9FF", 2 * scale)
        elif code in STORM:
            points = [(x + 66 * scale, y + 85 * scale),
                      (x + 51 * scale, y + 113 * scale),
                      (x + 69 * scale, y + 109 * scale),
                      (x + 56 * scale, y + 132 * scale),
                      (x + 94 * scale, y + 96 * scale),
                      (x + 73 * scale, y + 97 * scale)]
            p.d.polygon([(round(px * 2), round(py * 2)) for px, py in points],
                        fill="#FFDA8E")
        elif code in FOG:
            for yy in (92, 108, 124):
                p.line([(x + 10 * scale, y + yy * scale),
                        (x + 126 * scale, y + yy * scale)], "#C3E3EB", 3 * scale)


def _forecast(p, weather, landscape):
    daily = weather.get("daily")
    daily = [item for item in daily[:3] if isinstance(item, dict)] if isinstance(daily, list) else []
    y, height = (245, 63) if landscape else (258, 214)
    if not landscape:
        night = weather.get("is_day") is False
        _glass_panel(p, (12, y, p.w - 12, y + height), night=night)
        p.text(29, y + 11, "ВЕТЕР", 13, "#C1D9E9", True)
        p.text(29, y + 30, _metric(weather.get("wind_kmh"), " км/ч"), 22,
               "#F7FBFF", True)
        p.line([(161, y + 12), (161, y + 64)], "#4D7295", 1)
        p.text(176, y + 11, "ВЛАЖНОСТЬ", 13, "#C1D9E9", True)
        p.text(176, y + 30, _metric(weather.get("humidity"), "%"), 22,
               "#F7FBFF", True)
        p.line([(29, y + 72), (p.w - 29, y + 72)], "#4D7295", 1)
        p.text(29, y + 80, "ПРОГНОЗ НА 3 ДНЯ", 14, "#D7EAF5", True)
    if not daily:
        if landscape:
            p.rect((12, y, p.w - 12, y + height), "#173B5B", 14)
        p.text(24, y + (17 if landscape else 124), "Прогноз недоступен", 18,
               "#DFECF6")
        return
    gap = 8
    width = (p.w - 24 - gap * 2) / 3
    for index, day in enumerate(daily):
        x = 12 + index * (width + gap)
        if landscape:
            p.rect((x, y, x + width, y + height), "#173B5B", 13)
        try:
            date = datetime.strptime(str(day.get("date", "")), "%Y-%m-%d")
            label = f"{WEEKDAYS[date.weekday()]} {date.day:02d}"
        except (ValueError, TypeError):
            label = "—"
        if landscape:
            p.fit(x + 10, y + 5, label, width - 20, 16, "#C8DEEC", True)
            p.weather_icon(x + 8, y + 25, day.get("weather_code"), 26, True)
            temperatures = f"{_temperature(day.get('max'))}  {_temperature(day.get('min'))}"
            size = 18 if p.measure(temperatures, 18, True) <= width - 48 else 16
            p.text(x + width - 8, y + 29, temperatures,
                   size, "#F7FBFF", True, "rt")
        else:
            if index:
                p.line([(x - gap / 2, y + 106), (x - gap / 2, y + height - 15)],
                       "#4D7295", 1)
            p.text(x + width / 2, y + 107, label, 15, "#C8DEEC", True, "mt")
            p.weather_icon(x + width / 2 - 17, y + 128,
                           day.get("weather_code"), 34, True)
            high, low = _temperature(day.get("max")), _temperature(day.get("min"))
            p.text(x + width / 2, y + 163, high, 19, "#F7FBFF", True, "mt")
            p.text(x + width / 2, y + 184, low, 15, "#B8D0E2", True, "mt")
            probability = _number(day.get("precipitation_probability"))
            if probability is not None and probability >= 5:
                p.text(x + width / 2, y + 201, f"Осадки {probability:.0f}%",
                       11, "#9DDBF4", False, "mt")


def _compact_description(description):
    text = str(description or "Погода недоступна")
    text = text.replace("Преимущественно ясно", "В основном ясно")
    text = text.replace("Гроза с небольшим градом", "Гроза с градом")
    text = text.replace("Гроза с сильным градом", "Гроза с градом")
    text = re.sub(r"^(?:Сильн(?:ый|ая)|Небольш(?:ой|ая)|Умеренн(?:ый|ая)|Слаб(?:ый|ая))\s+", "", text, flags=re.I)
    return text[:1].upper() + text[1:]


def draw_weather_page(p, weather, landscape):
    """Draw only supplied observations/forecast; missing values remain dashes."""
    weather = weather if isinstance(weather, dict) else {}
    _sky(p, weather)
    white = "#F7FBFF"
    muted = "#D5E8F2"
    city = weather.get("city") or "Выберите город"
    p.fit(18, 16 if landscape else 13, city, (260 if landscape else 164),
          25 if landscape else 23,
          white, True)
    updated = _number(weather.get("updated_at"))
    stamp = ""
    if updated is not None and updated > 0:
        try:
            stamp = datetime.fromtimestamp(updated).strftime("%H:%M")
        except (ValueError, OverflowError, OSError):
            pass
    if _number(weather.get("temperature")) is None:
        status = "НЕТ ДАННЫХ"
    elif not stamp:
        status = "ОЖИДАНИЕ"
    else:
        status = ("КЭШ" if weather.get("stale") else "ОБНОВЛЕНО") + f" {stamp}"
    if landscape:
        p.text(p.w - 16, 23, status, 16,
               "#FFE1A6" if weather.get("stale") else muted, True, "rt")
        p.line([(18, 52), (p.w - 18, 52)], "#80AEC4", 1)
    else:
        p.text(p.w - 18, 26, status, 12,
               "#FFE1A6" if weather.get("stale") else muted, True, "rt")
        p.line([(18, 54), (p.w - 18, 54)], "#80AEC4", 1)

    temperature = _temperature(weather.get("temperature"))
    max_width = 256 if landscape else 190
    size = 98 if landscape else 91
    while size > 72 and p.measure(temperature, size, True) > max_width:
        size -= 2
    p.text(17, 64 if landscape else 67, temperature, size, white, True)
    _hero(p, weather, landscape)

    description = _compact_description(weather.get("description"))
    if landscape:
        p.fit(19, 165, description, 275, 22, white, True)
        p.text(20, 198, f"Ощущается {_temperature(weather.get('feels_like'))}",
               18, muted)
        p.text(20, 223, f"Ветер {_metric(weather.get('wind_kmh'), ' км/ч')}",
               17, muted)
        p.text(268, 223, f"Влажность {_metric(weather.get('humidity'), '%')}",
               17, muted)
    else:
        p.wrap(18, 172, description, p.w - 36, 22, 2, white, True)
        p.text(19, 220, f"Ощущается {_temperature(weather.get('feels_like'))}",
               18, muted)
    _forecast(p, weather, landscape)
