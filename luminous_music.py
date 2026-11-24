"""Album-first now-playing layouts for the USB panel."""
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from glass_material import LUMINOUS_DAY_THEMES
from renderer import _media_progress, album_image


_PAGE_INSET = 16
_PEEK_INSET = 12


@lru_cache(maxsize=8)
def _album_palette(data, day):
    art = album_image(data, 32) if isinstance(data, bytes) and len(data) <= 2*1024*1024 else None
    color = art.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)) if art else (108, 104, 192)
    factor = .38 if day else .59
    accent = tuple(round(c*factor + (35 if day else 132)) for c in color)
    return art, color, accent


@lru_cache(maxsize=12)
def _backdrop(w, h, data, day):
    art, color, _ = _album_palette(data, day)
    size = (80, max(1, round(80*h/w)))
    image = ImageOps.fit(art, size).filter(ImageFilter.GaussianBlur(8)) if art else Image.new('RGB', size, color)
    paper = (241, 244, 249) if day else (9, 12, 21)
    base = Image.new('RGB', size, paper)
    mask = Image.new('L', size)
    d = ImageDraw.Draw(mask)
    for y in range(size[1]):
        f = y/max(1, size[1]-1)
        d.line((0,y,size[0],y), fill=round((.36 if day else .34)*(1-.76*f)*255))
    return Image.composite(image, base, mask).resize((w, h), Image.Resampling.BICUBIC)


@lru_cache(maxsize=8)
def _art_tile(data, size):
    art = album_image(data, size) if isinstance(data, bytes) and len(data) <= 2*1024*1024 else None
    if art is None:
        art = Image.new('RGB', (size, size), '#353263')
        d = ImageDraw.Draw(art)
        for y in range(size):
            t = y/max(1, size-1)
            d.line((0,y,size,y), fill=(round(102-65*t),round(92-54*t),round(171-90*t)))
        for i, bulge in enumerate((.16, .30, .47, .34, .20)):
            x = size*(.32+i*.09)
            d.rounded_rectangle((x-size*.021,size*(.5-bulge/2),x+size*.021,size*(.5+bulge/2)),
                                radius=size*.02, fill='#E0DAFF')
    mask = Image.new('L', art.size)
    corner = round(size*.068)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,size-1,size-1), radius=corner, fill=255)
    edge = Image.new('L', art.size)
    ImageDraw.Draw(edge).rounded_rectangle((0,0,size-1,size-1), radius=corner, outline=51, width=2)
    tile = art.copy()
    tile.paste('#FFFFFF',(0,0,size,size),edge)
    return tile, mask


@lru_cache(maxsize=8)
def _art_shadow(size):
    pad = 20
    mask = Image.new('L', (size+pad*2, size+pad*2))
    ImageDraw.Draw(mask).rounded_rectangle((pad,pad+6,pad+size-1,pad+size+5),
                                          radius=round(size*.068),fill=110)
    return mask.filter(ImageFilter.GaussianBlur(9))


def _art(p, x, y, size, data):
    tile, mask = _art_tile(data, size*2)
    shadow = _art_shadow(size*2)
    sx, sy = 2*x-20, 2*y-20
    p.im.paste('#070810',(sx,sy,sx+shadow.width,sy+shadow.height),shadow)
    p.im.paste(tile, (2*x,2*y), mask)


def _type(p, x, y, w, media, size, *, wrap_size=None):
    title = str(media.get('title') or 'Музыка не играет')
    if wrap_size is not None and p.measure(title, size, True) > w:
        size = wrap_size
    wraps = ' ' in title and p.measure(title, size, True) > w
    p.wrap(x, y, title, w, size, 2 if wraps else 1, bold=True)
    after = y+(2*size+9 if wraps else size+8)
    p.fit(x, after, media.get('artist') or ('Исполнитель не указан' if media.get('title') else 'Запусти трек в плеере'),
          w, 18 if size >= 22 else 15, p.c['muted'])
    return after


def _timeline(p, x, y, w, media, accent, *, bottom, full=False):
    times = _media_progress(media, remaining=True)
    if times is None:
        if media.get('title'):
            p.fit(x, y+3, 'Время трека недоступно', w, 13, p.c['muted'])
        return
    percent, elapsed, remaining = times
    track = '#A8B2C6' if p.theme in LUMINOUS_DAY_THEMES else '#505469'
    height, size = (5, 16) if full else (4, 14)
    p.rect((x,y,x+w,y+height), track, height/2)
    radius = 4 if full else 3
    end = x+radius+(w-2*radius)*percent/100
    if percent > 0:
        p.rect((x,y,x+w if percent >= 100 else max(x+height,end),y+height), accent, height/2)
    mid = y+height/2
    p.ellipse((end-radius,mid-radius,end+radius,mid+radius), accent)
    p.text(x,bottom,elapsed,size,p.c['text'] if full else p.c['muted'],True,'ls')
    p.text(x+w,bottom,remaining,size,p.c['muted'],True,'rs')


def draw_music(p, media, landscape):
    data = media.get('thumbnail_bytes')
    data = data if isinstance(data,bytes) and len(data)<=2*1024*1024 else None
    day = p.theme in LUMINOUS_DAY_THEMES
    _, _, accent = _album_palette(data,day)
    p.im.paste(_backdrop(p.w*2,p.h*2,data,day))
    inset = _PAGE_INSET
    bottom = p.h-inset
    track_y = bottom-26
    if landscape:
        top = 60
        size = bottom-top
        _art(p, inset, top, size, data)
        x = inset+size+inset
        w = p.w-inset-x
        p.text(x, top+8, 'На паузе' if media.get('is_playing') is False else 'Сейчас играет' if media.get('title') else 'МУЗЫКА',
               13, p.c['muted'], True)
        after = _type(p, x, top+33, w, media, 29, wrap_size=27)
        if media.get('album'):
            p.fit(x, after+24, media['album'], w, 14, p.c['muted'])
        _timeline(p, x, track_y, w, media, accent, bottom=bottom, full=True)
    else:
        top = 60
        w = p.w-2*inset
        _art(p, inset, top, w, data)
        after = _type(p, inset, top+w+inset, w, media, 29, wrap_size=22)
        meta = 'На паузе' if media.get('is_playing') is False and media.get('title') else media.get('album')
        if meta and after+20+14 <= track_y-3:
            p.fit(inset, after+20, meta, w, 14, p.c['muted'])
        _timeline(p, inset, track_y, w, media, accent, bottom=bottom, full=True)


def draw_music_peek(p, box, media, tall):
    x,y,x2,y2 = box
    data = media.get('thumbnail_bytes')
    data = data if isinstance(data,bytes) and len(data)<=2*1024*1024 else None
    day = p.theme in LUMINOUS_DAY_THEMES
    _,_,accent = _album_palette(data,day)
    p.im.paste(_backdrop(2*(x2-x),2*(y2-y),data,day),(2*x,2*y))
    inset = _PEEK_INSET
    bottom = y2-inset
    if tall:
        size = min(x2-x-2*inset, y2-y-108)
        _art(p, x+inset, y+inset, size, data)
        tx, tw = x+inset, x2-x-2*inset
        _type(p,tx,y+size+2*inset,tw,media,19)
    else:
        size = y2-y-2*inset
        _art(p,x+inset,y+inset,size,data)
        tx = x+size+2*inset
        tw = x2-inset-tx
        _type(p,tx,y+inset,tw,media,20)
    _timeline(p,tx,bottom-20,tw,media,accent,bottom=bottom)
