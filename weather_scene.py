"""Original sky artwork for the overview card, rendered at panel resolution."""

from functools import lru_cache
import math
from random import Random

from PIL import Image, ImageDraw, ImageFilter

from glass_material import PORCELAIN, _rgb
from weather_design import FOG, RAIN, SNOW, STORM


# Deep ink under the type; different horizon light and cloud colors for each sky.
_SKIES = {
    'clear': ((12, 37, 71), (21, 65, 105), (82, 157, 216)),
    'partly': ((16, 38, 68), (25, 57, 93), (102, 155, 210)),
    'cloud': ((23, 36, 61), (37, 57, 83), (121, 151, 183)),
    'rain': ((16, 34, 53), (26, 56, 76), (79, 134, 163)),
    'snow': ((18, 41, 63), (32, 67, 91), (108, 176, 202)),
    'fog': ((25, 41, 58), (42, 65, 79), (132, 166, 181)),
    'storm': ((23, 25, 55), (35, 45, 78), (116, 112, 179)),
    'unknown': ((22, 34, 50), (32, 49, 67), (62, 92, 116)),
}
_W, _H = 240, 152
_CLOUD = {
    'back': ((.16, .61, .15, .28), (.29, .48, .18, .35),
             (.46, .39, .20, .37), (.65, .51, .22, .32), (.81, .65, .16, .24)),
    'front': ((.16, .68, .15, .23), (.28, .51, .17, .30),
              (.45, .42, .19, .39), (.60, .54, .17, .29), (.74, .51, .18, .28),
              (.85, .65, .12, .20)),
}


def sky_kind(code):
    if code == 0:
        return 'clear'
    if code in (1, 2):
        return 'partly'
    if code == 3:
        return 'cloud'
    for codes, kind in ((FOG, 'fog'), (RAIN, 'rain'), (SNOW, 'snow'), (STORM, 'storm')):
        if code in codes:
            return kind
    return 'unknown'


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _clamp(t):
    return min(1., max(0., t))


def _smooth(t):
    t = _clamp(t)
    return t * t * (3 - 2 * t)


@lru_cache(maxsize=24)
def card_surface(w, h, kind, day, compact):
    """Cache the immobile sky, glass edge and rounded clipping mask."""
    top, bottom, horizon = _SKIES[kind]
    if not day:
        top = _mix(top, (9, 14, 36), .66)
        bottom = _mix(bottom, (17, 28, 61), .61)
        horizon = _mix(horizon, (110, 119, 196), .45)
    cx, cy = (w - (73 if compact else 122)), h * .44
    sx, sy = (98 if compact else 132), h * .75
    # The broad, smooth light only needs a low-resolution color ramp.
    ramp_w, ramp_h = max(2, w // 4), max(2, h // 4)
    horizontal = [((x * w / ramp_w - cx) / sx) ** 2 for x in range(ramp_w)]
    colors = []
    for y in range(ramp_h):
        base = _mix(top, bottom, y / (ramp_h - 1))
        dy = ((y * h / ramp_h - cy) / sy) ** 2
        for dx in horizontal:
            glow = math.exp(-1.8 * (dx + dy)) * .55
            colors.append(_mix(base, horizon, glow))
    image = Image.new('RGB', (ramp_w, ramp_h))
    image.putdata(colors)
    image = image.resize((w, h), Image.Resampling.BICUBIC)
    mask = Image.new('L', image.size)
    radius = 24 if compact else 32
    bounds = (0, 0, w - 1, h - 1)
    ImageDraw.Draw(mask).rounded_rectangle(bounds, radius, fill=255)
    # The edge catches a little sky light without flattening into a white outline.
    edge = Image.new('L', image.size)
    ImageDraw.Draw(edge).rounded_rectangle(bounds, radius, outline=44, width=2)
    image.paste((190, 216, 243), (0, 0, w, h), edge)
    return image, mask


@lru_cache(maxsize=24)
def sky_panorama(w, h, kind, day, header, tall, light=False, full_sky=False):
    """A blue daytime sky or a spotlight in black glass behind the illustration."""
    width, height = max(2, w//4), max(2, h//4)
    reserved = header / h
    horizon = _SKIES[kind][2]
    if not day:
        horizon = _mix(horizon, (88, 103, 169), .48)
    if kind == 'clear' and day:
        horizon = (170, 135, 104)
    base = _rgb(PORCELAIN) if light else (0, 0, 0)
    if light:
        # The theme sets the sky palette; the live observation still controls
        # the sun or moon, clouds and precipitation drawn over it.
        horizon = (83, 168, 233)
    cx, cy = (.57 if tall else .76), reserved + (1-reserved)*(.56 if tall else .48)
    horizontal = [((x/width-cx)/(.69 if tall else .42))**2 for x in range(width)]
    colors = []
    for y in range(height):
        v = y/(height-1)
        reveal = _smooth((v-reserved*.56)*7) * _smooth((1-v)*6)
        if light and full_sky:
            # Keep the top blue all the way to the first row of pixels.
            backdrop = _mix((132, 195, 246), (202, 231, 253), v)
        elif light:
            # Keep the clock area pale, then let the blue fill the weather card.
            fade = _smooth((v-reserved*.55)/.27)
            sky = _mix((167, 214, 248), (221, 237, 253), v)
            backdrop = _mix(base, sky, fade)
        elif full_sky:
            backdrop = _mix((8, 16, 42), (17, 30, 64), v)
        else:
            backdrop = base
        dy = ((v-cy)/(.41 if tall else .39))**2
        for dx in horizontal:
            glow = math.exp(-2.3*(dx+dy))*(.66 if light else .74)
            colors.append(_mix(backdrop, horizon, glow*reveal))
    im = Image.new('RGB', (width, height))
    im.putdata(colors)
    im = im.resize((w, h), Image.Resampling.BICUBIC)
    if full_sky and not light:
        # Bake pinned stars into the cached sky. They add no USB traffic
        # when only the separate weather illustration animates.
        stars = ImageDraw.Draw(im)
        rng = Random(1987)
        for _ in range(58 if tall else 42):
            x, y = rng.randrange(7, w-7), rng.randrange(7, h-7)
            if x < w*.30 and y < h*.69:
                continue  # leave the title, city and temperature clear
            strength = rng.randrange(55, 164)
            color = (strength, min(255, strength+13), min(255, strength+43))
            radius = rng.choice((1, 1, 1.4, 1.9, 2.5))
            stars.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
    return im


def _noise(w, h, seed):
    rng = Random(seed)
    layers = []
    for gw, gh in ((8, 5), (21, 11), (42, 25)):
        grid = Image.new('L', (gw, gh))
        grid.putdata([rng.randrange(256) for _ in range(gw * gh)])
        layers.append(grid.resize((w, h), Image.Resampling.BICUBIC).get_flattened_data())
    return [(.58 * a + .28 * b + .14 * c) / 255 - .5 for a, b, c in zip(*layers)]


@lru_cache(maxsize=20)
def _cloud(form, tone):
    """Bake lit, soft-edged cloud billows from a textured density field."""
    w, h = 204, 104
    palettes = {
        'day': ((251, 252, 255), (113, 141, 173)),
        'night': ((194, 214, 244), (55, 77, 122)),
        'winter': ((242, 251, 255), (117, 163, 190)),
        'storm': ((177, 186, 217), (54, 60, 97)),
    }
    light, shadow = palettes[tone]
    noise = _noise(w, h, 7 if form == 'front' else 19)
    lobes = _CLOUD[form]
    xs = [[(x / w - cx) / rx for x in range(w)] for cx, cy, rx, ry in lobes]
    ys = [[(y / h - cy) / ry for y in range(h)] for cx, cy, rx, ry in lobes]
    density = []
    for y in range(h):
        for x in range(w):
            # Keep the surface continuous where lobes meet, while retaining an
            # irregular silhouette and avoiding stamped, perfectly round circles.
            signal = sum(math.exp(-2.0 * (xx[x] ** 2 + yy[y] ** 2))
                         for xx, yy in zip(xs, ys))
            density.append(signal + noise[y*w + x] * .48)
    pixels = []
    for y in range(h):
        for x in range(w):
            i = y*w + x
            field = density[i]
            opacity = _smooth((field - .23) / .48)
            opacity *= _smooth(min(x, y, w - 1 - x, h - 1 - y) / 13)
            if not opacity:
                pixels.append((0, 0, 0, 0))
                continue
            dx = density[i+2] - density[i-2] if 2 <= x < w-2 else 0
            dy = density[i+2*w] - density[i-2*w] if 2 <= y < h-2 else 0
            shade = _clamp(.94 - .67 * y/h + .90 * dy + .51 * dx + noise[i] * .64)
            pixels.append((*_mix(shadow, light, shade), round(opacity * 255)))
    result = Image.new('RGBA', (w, h))
    result.putdata(pixels)
    return result


@lru_cache(maxsize=4)
def _luminary(day, small):
    side = 134
    center = side / 2
    radius = (24 if small else 34) if day else (24 if small else 32)
    core = Image.new('RGBA', (side, side))
    bloom = Image.new('RGBA', core.size)
    draw = ImageDraw.Draw(core)
    for y in range(side):
        f = _clamp((y - center + radius) / (2 * radius))
        rgb = _mix((255, 253, 233), (255, 178, 97) if day else (184, 211, 255), f)
        draw.line((0, y, side - 1, y), fill=(*rgb, 255))
    mask = Image.new('L', core.size)
    m = ImageDraw.Draw(mask)
    m.ellipse((center-radius, center-radius, center+radius, center+radius), fill=255)
    if not day:
        m.ellipse((center-radius*.64, center-radius*1.24,
                   center+radius*1.19, center+radius*.65), fill=0)
    core.putalpha(mask)
    glow_color = (255, 182, 78) if day else (139, 180, 255)
    mask = mask.filter(ImageFilter.GaussianBlur(15 if day else 9))
    bloom.paste((*glow_color, 255), (0, 0, side, side), mask)
    bloom.alpha_composite(core)
    return bloom


def _particles(scene, kind, t):
    layer = Image.new('RGBA', scene.size)
    d = ImageDraw.Draw(layer)
    snow = kind == 'snow'
    for i in range(17 if snow else 15):
        f = (i * .371 + .12 + t * ((.35 if snow else 1.1) + (i % 4) * .041)) % 1
        fade = _smooth(f * 6) * _smooth((1 - f) * 5)
        x = 62 + (i * 47) % 133 - f * (6 if snow else 20)
        y = 79 + f * 68
        if snow:
            x += 5 * math.sin(i * 3.4 + t * 1.7)
            r = 1.1 + (i % 5) * .44
            d.ellipse((x-r, y-r, x+r, y+r), fill=(224, 244, 255, round(234 * fade)))
        else:
            length = 8 + i % 7
            d.line(((x, y), (x-4, y+length)), fill=(155, 208, 245, round(180 * fade)), width=2)
            d.point((round(x-4), round(y+length)), fill=(225, 244, 255, round(200 * fade)))
    scene.alpha_composite(layer)


@lru_cache(maxsize=8)
def _lightning(pulse):
    layer = Image.new('RGBA', (_W, _H))
    d = ImageDraw.Draw(layer)
    main = [(156, 69), (145, 93), (155, 93), (131, 122), (140, 119), (130, 139)]
    branch = [(144, 105), (162, 112), (167, 128)]
    d.line(main, fill=(157, 143, 255, pulse), width=10, joint='curve')
    glow = layer.filter(ImageFilter.GaussianBlur(9))
    d = ImageDraw.Draw(glow)
    d.line(main, fill=(238, 228, 255, pulse), width=3, joint='curve')
    d.line(branch, fill=(202, 188, 255, pulse // 2), width=2)
    return glow


@lru_cache(maxsize=8)
def _mist(day, index):
    w, h = 202, 48
    noise = _noise(w, h, 40 + index)
    color = (200, 229, 242) if day else (162, 181, 221)
    alphas = []
    for y in range(h):
        for x in range(w):
            u = x / (w - 1)
            center = h * .50 + 4 * math.sin(u * 7 + index*1.9) + 2 * math.sin(u * 17)
            thickness = 4 + 4 * math.sin(math.pi * u)
            fade = math.sin(math.pi * u) ** 1.5
            fade *= _smooth(min(y, h - 1 - y) / 7)
            streak = math.exp(-.5 * ((y - center) / thickness) ** 2)
            haze = math.exp(-.5 * ((y - center - 3) / (thickness * 1.9)) ** 2)
            texture = .80 + 1.05 * noise[y*w + x]
            alphas.append(round(182 * fade * (.58 * streak + .42 * haze) * texture))
    mask = Image.new('L', (w, h))
    mask.putdata(alphas)
    fog = Image.new('RGBA', (w, h), (*color, 0))
    fog.putalpha(mask)
    return fog


def draw_scene(kind, day, phase, width, height):
    """Only this bounded layer moves. No wall clock or random state is sampled."""
    if kind == 'unknown':
        return None
    t = phase if phase is not None else 0.
    scene = Image.new('RGBA', (_W, _H))
    if kind in ('clear', 'partly'):
        stars = Image.new('RGBA', scene.size)
        d = ImageDraw.Draw(stars)
        for i, (x, y, r) in enumerate(((72, 28, 1.7), (104, 48, 1.2), (118, 16, 1.3),
                                       (203, 26, 2), (222, 52, 1.2), (207, 76, 1.3),
                                       (74, 76, 1.2), (91, 94, 1.6), (194, 100, 1.5))):
            twinkle = .70 + .24 * math.sin(t * (1.2 + i*.17) + i*2)
            if day:
                if i in (1, 3, 8) and kind == 'clear':
                    x += 1.8 * math.sin(t * .90 + i)
                    y += 2.2 * math.cos(t * .77 + i)
                    d.ellipse((x-r, y-r, x+r, y+r),
                              fill=(255, 218, 155, round(130 * twinkle)))
            else:
                d.ellipse((x-r, y-r, x+r, y+r),
                          fill=(224, 236, 255, round(240 * twinkle)))
                if i in (0, 3):
                    reach = 3 + r * twinkle
                    d.line(((x, y-reach), (x, y+reach)), fill=(213, 233, 255, round(206 * twinkle)))
                    d.line(((x-reach, y), (x+reach, y)), fill=(213, 233, 255, round(206 * twinkle)))
        scene.alpha_composite(stars)
        scene.alpha_composite(_luminary(day, kind == 'partly'),
                              (84 if kind == 'clear' else 106, 1))

    # Precipitation provides the movement in wet scenes. Holding the cloud
    # layers still lets the USB worker send only the thin curtain beneath them.
    drift = round(4 * math.sin(t * .62)) if kind in ('partly', 'cloud') else 0
    cross = round(5 * math.sin(t * .47 + 1)) if kind in ('partly', 'cloud') else 0
    tone = 'day' if day else 'night'
    if kind == 'snow':
        tone = 'winter' if day else 'night'
    if kind in ('rain', 'storm'):
        tone = 'storm'

    if kind == 'fog':
        for i, (x, y, extent) in enumerate(((36, 20, 0), (23, 47, 8), (32, 70, 0), (35, 92, 6))):
            shift = round(extent * math.sin(t * (.4 + i*.09) + i*1.7))
            scene.alpha_composite(_mist(day, i), (x + shift, y))
    elif kind != 'clear':
        if kind in ('rain', 'storm'):
            _particles(scene, kind, t)
        if kind == 'storm':
            moment = (t + .8) % 6.5
            flash = max(0., 1 - abs(moment - .20) / .16,
                        .55 * (1 - abs(moment - .50) / .11))
            scene.alpha_composite(_lightning(round(4 + 3 * flash) * 36))
        scene.alpha_composite(_cloud('back', 'night' if tone == 'day' else tone),
                              (26 - cross, 8 if kind != 'partly' else 32))
        scene.alpha_composite(_cloud('front', tone), (11 + drift, 29 if kind != 'partly' else 44))
        if kind == 'snow':
            _particles(scene, kind, t)
    return scene.resize((width, height), Image.Resampling.LANCZOS)
