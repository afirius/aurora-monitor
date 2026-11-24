"""Aurora 0.2. Native-panel layouts: large readings, essential labels >=16px."""
from datetime import datetime, timedelta
import calendar
from functools import lru_cache
from io import BytesIO
import math
import os
import time
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
from glass_material import DAY_THEMES, LUMINOUS_DAY_THEMES, LUMINOUS_THEMES, PORCELAIN, overview_strip, wallpaper, panel_tile
from weather_scene import card_surface, draw_scene, sky_kind, sky_panorama

THEMES = {
 'luminous': dict(bg='#000000',card='#1B1D22',line='#3C3D45',text='#FCFAF6',muted='#CFCCD0',primary='#DEC49F',secondary='#C5BEF4',warm='#FFD896',memory='#A8E8D3'),
 'day': dict(bg='#EAF0FB',card='#FFFFFF',line='#CFD7E7',text='#202126',muted='#515866',primary='#0068DC',secondary='#7848CF',warm='#AC5A0A',memory='#167C50'),
 'night': dict(bg='#111321',card='#25293E',line='#3D4560',text='#FAFAFC',muted='#C1C5D0',primary='#65B7FF',secondary='#C4A0FF',warm='#FFD08A',memory='#67DFAB'),
}
THEMES['luminous_classic'] = THEMES['luminous']
THEMES['luminous_day'] = dict(bg=PORCELAIN,card='#FFFFFF',line='#AEB9CD',text='#181F2B',
                             muted='#42516A',primary='#225C96',secondary='#614487',
                             warm='#875413',memory='#166C59')
THEMES['luminous_classic_day'] = THEMES['luminous_day']
RED='#FF8B98'

@lru_cache(maxsize=80)
def font(size,bold=False):
    base=os.path.join(os.environ.get('WINDIR','C:/Windows'),'Fonts')
    for name in ('seguisb.ttf' if bold else 'segoeui.ttf','arial.ttf'):
        try:return ImageFont.truetype(os.path.join(base,name),round(size*2))
        except OSError:pass
    return ImageFont.load_default(size=round(size*2))

def number(v):
    if isinstance(v,bool):return None
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError,OverflowError):return None

def value(v,suffix='',digits=0):
    n=number(v)
    return '—' if n is None else f'{n:.{digits}f}{suffix}'

def speed(v):
    n=number(v)
    if n is None:return '—'
    return f'{n/1048576:.1f} МБ/с' if n>=1048576 else f'{max(0,n)/1024:.0f} КБ/с'

@lru_cache(maxsize=4)
def album_image(data,size):
    try:
        with Image.open(BytesIO(data)) as im:
            if im.width*im.height>20_000_000:return None
            return ImageOps.fit(im.convert('RGB'),(size,size),method=Image.Resampling.LANCZOS)
    except Exception:return None

@lru_cache(maxsize=6)
def background(w,h,theme):
    return wallpaper(w,h,theme,2)

@lru_cache(maxsize=48)
def _vertical(w, h, top, bottom):
    im = Image.new('RGB', (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        f = y / max(1, h - 1)
        d.line((0, y, w, y), fill=tuple(round(a + (b - a) * f) for a, b in zip(top, bottom)))
    return im


@lru_cache(maxsize=48)
def _disc(diameter, inner, outer):
    g = Image.radial_gradient('L').resize((diameter, diameter), Image.Resampling.LANCZOS)
    return ImageOps.colorize(g, inner, outer).convert('RGBA')


@lru_cache(maxsize=48)
def _halo(diameter, color, power=1.7):
    g = Image.radial_gradient('L').resize((diameter, diameter), Image.Resampling.LANCZOS)
    alpha = ImageOps.invert(g).point(lambda v: int((v / 255) ** power * 255))
    im = Image.new('RGBA', (diameter, diameter), color + (0,))
    im.putalpha(alpha)
    return im


def _mix(a, b, f):
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b))


class Painter:
    def __init__(self,w,h,theme='night'):
        self.theme=theme if theme in THEMES else 'night'
        self.c=THEMES[self.theme];self.im=background(w,h,self.theme).copy()
        self.d=ImageDraw.Draw(self.im);self.w=w;self.h=h

    def text(self,x,y,text,size=18,color=None,bold=False,anchor='lt'):
        self.d.text((round(x*2),round(y*2)),str(text),font=font(size,bold),fill=color or self.c['text'],anchor=anchor)

    def measure(self,text,size=18,bold=False):
        return self.d.textlength(str(text),font=font(size,bold))/2

    def fit(self,x,y,text,width,size=18,color=None,bold=False,anchor='lt'):
        text=str(text or '')
        if self.measure(text,size,bold)>width:
            while text and self.measure(text+'…',size,bold)>width:text=text[:-1]
            text+='…'
        self.text(x,y,text,size,color,bold,anchor)

    def wrap(self,x,y,text,width,size=20,lines=2,color=None,bold=False):
        line='';output=[]
        for word in str(text or '').split():
            candidate=f'{line} {word}'.strip()
            if line and self.measure(candidate,size,bold)>width:output.append(line);line=word
            else:line=candidate
        if line:output.append(line)
        for i,line in enumerate(output[:lines]):
            if i==lines-1 and len(output)>lines:line+='…'
            self.fit(x,y+i*(size+6),line,width,size,color,bold)

    def rect(self,box,fill=None,radius=14,outline=None):
        coords=tuple(round(v*2) for v in box)
        if fill is None and radius>=10:
            tile,position=panel_tile(self.w,self.h,self.theme,tuple(box),max(18,radius),2)
            self.im.paste(tile,position,tile)
        else:
            self.d.rounded_rectangle(coords,radius=round(radius*2),fill=fill or self.c['card'],outline=outline,width=2)

    def line(self,points,color=None,width=1):
        self.d.line([(round(x*2),round(y*2)) for x,y in points],fill=color or self.c['line'],width=max(1,round(width*2)))

    def ellipse(self,box,fill=None,outline=None,width=1):
        self.d.ellipse(tuple(round(v*2) for v in box),fill=fill,outline=outline,width=max(1,round(width*2)))

    def bar(self,x,y,w,percent,color=None,height=6):
        self.rect((x,y,x+w,y+height),self.c['line'],height/2)
        n=number(percent)
        if n is not None and n>0:self.rect((x,y,x+max(height,w*min(100,n)/100),y+height),color or self.c['primary'],height/2)

    def percent(self,x,y,v,size=64,color=None):
        text=value(v);self.text(x,y,text,size,color,True)
        if number(v) is not None:self.text(x+self.measure(text,size,True)+3,y+size*.43,'%',round(size*.39),color,True)

    def graph(self,box,values,color,maximum=100):
        x,y,x2,y2=box
        for level in (0,.5,1):self.line([(x,y+(y2-y)*level),(x2,y+(y2-y)*level)])
        points=[];values=list(values)[-60:]
        for i,v in enumerate(values):
            v=number(v)
            if v is None:
                if len(points)>1:self.line(points,color,3)
                points=[];continue
            points.append((x+(60-len(values)+i)*(x2-x)/59,y2-min(maximum,max(0,v))*(y2-y)/maximum))
        if len(points)>1:self.line(points,color,3)
        if points:
            px,py=points[-1];self.ellipse((px-3,py-3,px+3,py+3),color)

    def weather_icon(self, x, y, code, size=64, is_day=True, phase=None):
        code = number(code)
        W = max(8, round(size * 2))
        u = W / 64.0
        if code is None:
            self.ellipse((x + 9, y + 9, x + size - 9, y + size - 9),
                         outline=self.c['muted'], width=2 * size / 64)
            self.text(x + size / 2, y + size * .22, '?', round(size * .5),
                      self.c['muted'], True, 'mt')
            return
        code = int(code)
        motion = number(phase)
        animate = motion is not None
        t = motion or 0.0
        sprite = Image.new('RGBA', (W, W), (0, 0, 0, 0))

        def add(im, dx=0, dy=0):
            nonlocal sprite
            sprite.alpha_composite(im, (round(dx), round(dy)))

        def layer():
            lay = Image.new('RGBA', (W, W), (0, 0, 0, 0))
            return lay, ImageDraw.Draw(lay)

        def box(a, b, c, d):
            return (a * u, b * u, c * u, d * u)

        def pt(a, b):
            return (a * u, b * u)

        def sway(rate, offset=0.0, amount=1.0):
            return amount * math.sin(t * rate + offset) if animate else 0.0

        def halo(cx, cy, radius, color, alpha=255, power=1.7):
            diameter = max(2, round(radius * 2 * u))
            im = _halo(diameter, color, power)
            if alpha < 255:
                im = im.copy()
                im.putalpha(im.getchannel('A').point(lambda v: int(v * alpha / 255)))
            add(im, cx * u - diameter / 2, cy * u - diameter / 2)

        def disc(cx, cy, radius, inner, outer):
            diameter = max(3, round(radius * 2 * u))
            im = _disc(diameter, inner, outer).copy()
            mask = Image.new('L', (diameter, diameter), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
            im.putalpha(mask)
            add(im, cx * u - diameter / 2, cy * u - diameter / 2)

        def star4(cx, cy, radius, alpha, angle=-math.pi / 2):
            lay, d = layer()
            points = []
            for i in range(8):
                a = angle + i * math.pi / 4
                r = radius if i % 2 == 0 else radius * .2
                points.append(pt(cx + math.cos(a) * r, cy + math.sin(a) * r))
            d.polygon(points, fill=(255, 244, 198, alpha))
            d.ellipse(box(cx - .55, cy - .55, cx + .55, cy + .55),
                      fill=(255, 255, 238, min(255, alpha + 30)))
            add(lay)

        def sun(cx=32, cy=29, radius=12.5, strong=True):
            reach = min(radius * 2.05, cx - 1, cy - 1, 63 - cx, 63 - cy)
            halo(cx, cy, reach, (255, 183, 78), 120 if strong else 90, 1.75)
            sweep = math.radians(t * 18) if animate else 0.0
            rays, rd = layer()
            for i in range(8):
                a = sweep + math.radians(i * 45)
                pulse = .5 * math.sin(t * 1.25 + i * .7) if animate else 0.0
                r0, r1 = radius + 3.6, radius + 8.8 + pulse
                w0, w1 = 1.45, .14
                ca, sa = math.cos(a), math.sin(a)
                px, py = -sa, ca
                pts = [(cx + r0 * ca + w0 * px, cy + r0 * sa + w0 * py),
                       (cx + r1 * ca + w1 * px, cy + r1 * sa + w1 * py),
                       (cx + r1 * ca - w1 * px, cy + r1 * sa - w1 * py),
                       (cx + r0 * ca - w0 * px, cy + r0 * sa - w0 * py)]
                rd.polygon([pt(a, b) for a, b in pts],
                           fill=(255, 207, 116, 205 if strong else 175))
            soft = rays.filter(ImageFilter.GaussianBlur(max(1, .85 * u)))
            soft.putalpha(soft.getchannel('A').point(lambda v: int(v * .5)))
            add(soft)
            add(rays)
            disc(cx, cy, radius, (255, 251, 228), (246, 166, 48))
            hl, hd = layer()
            hd.ellipse(box(cx - radius * .55, cy - radius * .68,
                           cx + radius * .25, cy - radius * .08),
                       fill=(255, 255, 255, 78))
            hl = hl.filter(ImageFilter.GaussianBlur(max(1, 1.15 * u)))
            add(hl)
            if strong:
                for i, (mx, my, mr, ph) in enumerate(((13, 12, .8, .2), (51, 8, .55, 1.8),
                                                       (54, 43, .7, 3.2))):
                    mx += sway(.28, ph, .65)
                    my += sway(.34, ph + 1.1, .55)
                    lay, d = layer()
                    d.ellipse(box(mx - mr, my - mr, mx + mr, my + mr),
                              fill=(255, 228, 160, 95))
                    add(lay)

        def moon(cx=32, cy=27):
            halo(cx, cy, min(25, cx - 1, cy - 1, 63 - cx, 63 - cy),
                 (164, 197, 255), 72, 1.9)
            lay, d = layer()
            d.ellipse(box(cx - 19, cy - 19, cx + 19, cy + 19), fill=(190, 214, 255, 18))
            add(lay)
            mask = Image.new('L', (W, W), 0)
            md = ImageDraw.Draw(mask)
            md.ellipse(box(cx - 19, cy - 19, cx + 19, cy + 19), fill=255)
            md.ellipse(box(cx - 6, cy - 27, cx + 30, cy + 8), fill=0)
            body = _vertical(W, W, (255, 251, 231), (245, 196, 104)).convert('RGBA')
            body.putalpha(mask)
            add(body)
            for i, (sx, sy, sr, ph) in enumerate(((53, 13, 3.1, .2), (10, 44, 2.2, 1.9),
                                                   (53, 45, 1.5, 3.4))):
                tw = .72 + .28 * math.sin(t * 1.4 + ph) if animate else 1.0
                halo(sx, sy, sr * 2.1, (255, 230, 158), int(44 * tw), 2.1)
                star4(sx, sy, sr * tw, int(198 + 45 * tw))

        def cloud(dx=0.0, dy=0.0, scale=1.0, storm=False, drift=0.0, bob=0.0):
            dx += drift
            dy += bob
            k = scale

            def cx(a):
                return 34 + (a - 34) * k + dx

            def cy(b):
                return 30 + (b - 30) * k + dy

            def px(a, b):
                return (cx(a) * u, cy(b) * u)

            # A soft shadow makes the cloud sit above the card instead of floating flat.
            sh, sd = layer()
            sd.ellipse((*px(5, 40), *px(61, 55)), fill=(3, 12, 28, 70 if not storm else 92))
            sh = sh.filter(ImageFilter.GaussianBlur(max(1, 2.9 * u * k)))
            add(sh)
            puffs = ((2, 23, 27, 46), (15, 9, 47, 44), (33, 17, 61, 46),
                     (23, 14, 41, 32))
            base = (5, 32, 62, 47)

            def silhouette(draw, ox=0.0, oy=0.0):
                for a, b, c, d in puffs:
                    draw.ellipse((*px(a + ox, b + oy), *px(c + ox, d + oy)), fill=255)
                draw.rounded_rectangle((*px(base[0] + ox, base[1] + oy),
                                        *px(base[2] + ox, base[3] + oy)),
                                       radius=7.5 * u * k, fill=255)

            mask = Image.new('L', (W, W), 0)
            silhouette(ImageDraw.Draw(mask))
            top = (214, 222, 237) if storm else (253, 254, 255)
            bottom = (138, 155, 179) if storm else (201, 220, 238)
            body = _vertical(W, W, top, bottom).convert('RGBA')
            body.putalpha(mask)
            add(body)
            # Volume comes from a soft base shadow clipped to the silhouette, not
            # from a second bright ball on top of the cloud.
            dep, dd = layer()
            dd.ellipse((*px(6, 35), *px(62, 54)),
                       fill=(38, 66, 104, 58 if not storm else 78))
            dep = dep.filter(ImageFilter.GaussianBlur(max(1, 2.7 * u * k)))
            dep.putalpha(ImageChops.multiply(dep.getchannel('A'), mask))
            add(dep)
            # A cool rim light along the upper-left edge separates the cloud from
            # the sky without drawing a hard outline.
            rim_mask = Image.new('L', (W, W), 0)
            silhouette(ImageDraw.Draw(rim_mask), -.9, -1.05)
            rim_mask = ImageChops.subtract(rim_mask, mask)
            rim = Image.new('RGBA', (W, W), (255, 255, 255, 0))
            rim.putalpha(rim_mask.point(lambda v: int(v * (.26 if storm else .38))))
            rim = rim.filter(ImageFilter.GaussianBlur(max(1, .7 * u * k)))
            add(rim)
            hl, hd = layer()
            hd.ellipse((*px(21, 13), *px(47, 30)), fill=(255, 255, 255, 32 if storm else 46))
            hl = hl.filter(ImageFilter.GaussianBlur(max(1, 2.5 * u * k)))
            hl.putalpha(ImageChops.multiply(hl.getchannel('A'), mask))
            add(hl)

        def rain(count=5, heavy=False):
            lay, d = layer()
            span = 18
            for i in range(count):
                phase = i * 3.7 + (2.1 if heavy else 0)
                x = 12 + i * (38 / max(1, count - 1))
                y = 42 + ((t * (12.0 if heavy else 9.0) + phase) % span) if animate else 45 + (i % 4) * 4
                length = 7.4 if heavy else 6.0
                x += sway(.75, i * 1.7, .55)
                d.line([pt(x, y), pt(x - 1.25, y + length)], fill=(126, 214, 255, 72),
                       width=max(2, round(3.0 * u)))
                d.line([pt(x, y), pt(x - 1.25, y + length)], fill=(228, 250, 255, 232),
                       width=max(1, round(1.35 * u)))
                d.ellipse(box(x - .95, y - .95, x + .95, y + .95),
                          fill=(242, 253, 255, 238))
            add(lay)

        def snow(count=3):
            lay, d = layer()
            span = 11
            for i in range(count):
                phase = i * 3.6
                x = (15 + i * (34 / max(1, count - 1))) if count > 1 else 32
                y = 52 + ((t * 4.2 + phase) % span) if animate else 54 + (i * 3) % 5
                r = 5.0 if count <= 3 else 3.6
                rot = t * .75 + i * 1.1 if animate else i * .8
                x += sway(1.05, i * 2.2, 1.6)
                halo(x, y, r * 2.2, (188, 227, 255), 78, 1.9)
                for arm in range(6):
                    a = rot + arm * math.pi / 3
                    ex, ey = x + math.cos(a) * r, y + math.sin(a) * r
                    d.line([pt(x, y), pt(ex, ey)], fill=(235, 249, 255, 244),
                           width=max(1, round(1.3 * u)))
                    if r >= 2.8:
                        bx, by = x + math.cos(a) * r * .62, y + math.sin(a) * r * .62
                        for side in (-1, 1):
                            ba = a + side * math.pi / 3
                            d.line([pt(bx, by), pt(bx + math.cos(ba) * r * .36,
                                                  by + math.sin(ba) * r * .36)],
                                   fill=(219, 242, 255, 215), width=max(1, round(.9 * u)))
                d.ellipse(box(x - .8, y - .8, x + .8, y + .8), fill=(255, 255, 255, 245))
            add(lay)

        def fog():
            halo(32, 35, 28, (196, 228, 253), 52, 2.0)
            lay, d = layer()
            for i, (yy, x0, x1, alpha, width) in enumerate((
                    (18, 12, 50, 182, 4.6), (30, 5, 57, 236, 5.4),
                    (42, 13, 58, 212, 5.0), (53, 8, 46, 160, 4.2))):
                shift = sway(.36 + i * .03, i * 1.6, 2.6)
                prev = None
                steps = 18
                for j in range(steps + 1):
                    f = j / steps
                    bx = x0 + (x1 - x0) * f + shift
                    by = yy + .7 * math.sin(f * 4.2 + i * 1.7 + t * .45)
                    taper = math.sin(math.pi * f) ** .55
                    shade = (226, 244, 255, max(12, int(alpha * taper)))
                    point = pt(bx, by)
                    if prev is not None:
                        d.line([prev, point], fill=shade,
                               width=max(1, round(width * (.5 + .5 * taper) * u)))
                    prev = point
            lay = lay.filter(ImageFilter.GaussianBlur(max(1, .9 * u)))
            add(lay)

        def bolt(pulse=0.0):
            pts = [(31, 41), (22, 53), (31, 52), (27, 62), (42, 46), (33, 46)]
            glow, gd = layer()
            gd.polygon([pt(a, b) for a, b in pts], fill=(255, 179, 58, 215))
            glow = glow.filter(ImageFilter.GaussianBlur(max(1, 2.1 * u)))
            add(glow)
            core = _mix((255, 208, 105), (255, 255, 244), pulse)
            lay, d = layer()
            d.polygon([pt(a, b) for a, b in pts], fill=core + (255,))
            d.line([pt(a, b) for a, b in pts + [pts[0]]],
                   fill=(255, 252, 220, 210), width=max(1, round(.8 * u)), joint='curve')
            add(lay)
            if pulse > .01:
                halo(33, 34, 23, (255, 218, 126), int(75 * pulse), 1.9)

        drift = sway(.62, 0, 2.0)
        bob = sway(1.15, .7, .5)
        day = bool(is_day)
        prelim = code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82)
        snowing = code in (71, 73, 75, 77, 85, 86)
        storm = code in (95, 96, 99)

        if code == 0:
            sun() if day else moon()
        elif code == 1:
            sun(25, 23, 12.0, False) if day else moon(27, 24)
            cloud(7, 9, .72, drift=drift * .8, bob=bob)
        elif code == 2:
            sun(18, 16, 8.8, False) if day else moon(19, 17)
            cloud(0, 2, .96, drift=drift, bob=bob)
        elif code == 3:
            cloud(0, 0, 1.0, drift=drift *.8, bob=bob)
        elif code in (45, 48):
            fog()
        elif prelim:
            rain(4 if code in (51, 53, 55) else 7 if code in (65, 67) else 6,
                 heavy=code in (65, 67))
            cloud(0, -1, 1.0, drift=drift * .6, bob=bob * .6)
        elif snowing:
            cloud(0, -1, 1.0, drift=drift * .6, bob=bob * .6)
            snow(5 if code in (75, 86) else 3)
        elif storm:
            rain(3, heavy=False)
            cloud(0, -1, 1.0, storm=True, drift=drift * .35, bob=bob * .45)
            if animate:
                uu = t % 4.3
                pulse = max(0.0, 1 - abs(uu - .14) / .23)
                pulse = max(pulse, .78 * max(0.0, 1 - abs(uu - .47) / .14))
            else:
                pulse = .0
            bolt(pulse)
        else:
            lay, d = layer()
            d.ellipse(box(9, 9, 55, 55), outline=(255, 255, 255, 55), width=max(1, round(1.2 * u)))
            add(lay)
            self.text(x + size / 2, y + size * .76, '?', round(size * .22),
                      self.c['muted'], True, 'mt')

        self.im.paste(sprite, (round(x * 2), round(y * 2)), sprite)

    def finish(self):return self.im.resize((self.w,self.h),Image.Resampling.LANCZOS)

def header(p,page,now,brand_name="Мой компьютер",alert=''):
    titles={'overview':brand_name,'graphs':'Нагрузка','thermal':'Температуры','weather':'Погода','clock':'Часы','media':'Музыка','gaming':'Игра'}
    if p.theme in LUMINOUS_THEMES:
        title = str(alert).replace(' — высокая температура', '').replace('Процессор:', 'CPU:').replace('Видеокарта:', 'GPU:') if alert else titles.get(page,brand_name)
        warning='#A02535' if p.theme in DAY_THEMES else '#FFADA9'
        p.fit(20,22,title,p.w-120,17,warning if alert else p.c['text'],True,'lm')
        p.text(p.w-20,22,now.strftime('%H:%M'),21,p.c['text'],True,'rm')
        return
    p.fit(20,22,titles.get(page,brand_name),p.w-120,17,p.c['text'],True,'lm')
    p.text(p.w-20,22,now.strftime('%H:%M'),21,p.c['text'],True,'rm')


def hardware_icon(p,x,y,kind,color,size=40):
    """Distinct hardware silhouettes remain readable at native panel size."""
    z=size/40
    def line(points,width=2):p.line([(x+a*z,y+b*z) for a,b in points],color,width*z)
    def rect(box,r=2,fill=None):p.rect(tuple((x if i%2==0 else y)+v*z for i,v in enumerate(box)),fill or p.c['card'],r*z,color)
    if kind=='cpu':
        rect((8,8,32,32),4);rect((13,13,27,27),2,color)
        for n in (12,20,28):
            line([(n,2),(n,7)]);line([(n,33),(n,38)]);line([(2,n),(7,n)]);line([(33,n),(38,n)])
    elif kind=='gpu':
        rect((4,9,37,29),3);line([(2,5),(2,34),(7,34)]);line([(10,33),(29,33)],3)
        for cx in (14,28):
            p.ellipse((x+(cx-5)*z,y+14*z,x+(cx+5)*z,y+24*z),outline=color,width=1.5*z)
            line([(cx-3,16),(cx+3,22)],1.5);line([(cx+3,16),(cx-3,22)],1.5)
    elif kind=='ram':
        rect((2,10,38,29),2)
        for cx in (6,16,26):rect((cx,14,cx+7,24),1,color)
        for cx in (5,10,15,24,29,34):line([(cx,30),(cx,34)],2)
    elif kind=='fan':
        p.ellipse((x+2*z,y+2*z,x+38*z,y+38*z),outline=color,width=1.5*z)
        for angle in (0,120,240):
            t=math.radians(angle)
            blade=[(20,20),(17,11),(20,6),(28,7),(30,12),(25,18)]
            points=[(round((x+(20+(a-20)*math.cos(t)-(b-20)*math.sin(t))*z)*2),
                     round((y+(20+(a-20)*math.sin(t)+(b-20)*math.cos(t))*z)*2)) for a,b in blade]
            p.d.polygon(points,fill=color)
        p.ellipse((x+16*z,y+16*z,x+24*z,y+24*z),color)
    elif kind=='temp':
        rect((17,3,23,28),3);p.ellipse((x+13*z,y+24*z,x+27*z,y+38*z),color)
        line([(20,12),(20,30)],3)


def hardware_badge(p,x,y,kind,color,size=36):
    # Glossy rounded app-style symbol, with the hardware silhouette kept distinct.
    color={'cpu':'#087AF0','gpu':'#8951D0','ram':'#209963'}.get(kind,color)
    edge=round(size*2);tile=Image.new('RGBA',(edge,edge));draw=ImageDraw.Draw(tile)
    rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))
    for yy in range(edge):
        t=yy/max(1,edge-1)
        shade=tuple(round(v+(255-v)*(.24*(1-t))) for v in rgb)
        draw.line((0,yy,edge,yy),fill=(*shade,255))
    mask=Image.new('L',(edge,edge));ImageDraw.Draw(mask).rounded_rectangle((0,0,edge-1,edge-1),radius=round(size*.26*2),fill=255)
    tile.putalpha(mask);p.im.paste(tile,(round(x*2),round(y*2)),tile)
    original=p.c;p.c=dict(original,card=color)
    hardware_icon(p,x+size*.15,y+size*.15,kind,'#FFFFFF',size*.7)
    p.c=original
    p.d.rounded_rectangle((round(x*2),round(y*2),round((x+size)*2-1),round((y+size)*2-1)),radius=round(size*.26*2),outline='#FFFFFF',width=1)


def short_model(raw,kind):
    text=str(raw or ('Процессор' if kind=='cpu' else 'Видеокарта'))
    for word in ('NVIDIA ', 'GeForce ', 'AMD ', 'Intel(R) ', '(R)', '(TM)', 'Intel ', ' Processor', ' CPU'):
        text=text.replace(word,'')
    import re
    if kind=='gpu':
        model=re.search(r'\b(?:RTX|GTX|RX)\s*\d{3,5}(?:\s*(?:Ti|SUPER|XTX|XT|GRE))\b|\b(?:RTX|GTX|RX)\s*\d{3,5}\b',text,re.I)
        if model:
            tail=text[model.end():]
            suffix=re.match(r'\s+(Ti|SUPER|XTX|XT|GRE)\b',tail,re.I)
            return model.group(0)+((' '+suffix.group(1)) if suffix else '')
    return re.sub(r'\s+\d+-Core.*$','',text).strip()


def hardware_vendor(raw):
    name=str(raw or '').upper()
    for vendor in ('NVIDIA','AMD','INTEL'):
        if vendor in name:return 'Intel' if vendor=='INTEL' else vendor
    return ''


def surface(p,box,kind):
    """Shared softly tinted, borderless surfaces for every overview section."""
    day=p.theme=='day'
    tones={
        'cpu':('#F5FAFF','#DFEDFC') if day else ('#253A52','#1B293D'),
        'gpu':('#FAF7FF','#EAE3F7') if day else ('#393047','#292439'),
        'ram':('#F5FCF9','#E0F0EA') if day else ('#233E3C','#1C302F'),
        'weather':('#17649F','#529BCC') if day else ('#273F69','#172A4A'),
    }
    from glass_material import _gradient
    x,y,x2,y2=box;size=(round((x2-x)*2),round((y2-y)*2))
    tile=_gradient(size,*tones[kind]);mask=Image.new('L',size)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,size[0]-1,size[1]-1),radius=30,fill=255)
    p.im.paste(tile,(round(x*2),round(y*2)),mask)


def temperature_color(p,temperature):
    """Display bands, not hardware safety limits: <60, 60–84, >=85 °C."""
    temperature=number(temperature)
    if temperature is None:return p.c['muted']
    temperature=round(temperature)
    day=p.theme in DAY_THEMES
    if temperature<60:return '#147D48' if day else '#73E3A6'
    if temperature<85:return '#9C6500' if day else '#FFD36A'
    return '#C52D40' if day else '#FF8490'


def load_color(p,percent):
    percent=number(percent)
    if percent is None:return p.c['muted']
    percent=round(percent)
    if percent>=80:return '#925100' if p.theme in LUMINOUS_DAY_THEMES else ('#B15B00' if p.theme=='day' else '#FFBD69')
    return p.c['primary']


def video_memory(metrics):
    """Localized dedicated GPU memory, or None when the source reports neither value."""
    used = number(metrics.get('gpu_memory_used_mb'))
    total = number(metrics.get('gpu_memory_total_mb'))
    used = used/1024 if used is not None and used >= 0 else None
    total = total/1024 if total is not None and total > 0 else None
    if used is None and total is None:
        return None
    total_digits = 0 if total is None or round(total,1).is_integer() else 1
    return (value(used,digits=1).replace('.',','),
            value(total,digits=total_digits).replace('.',','))


def overview(p,m,weather,landscape,overview_content=None,animation=None):
    w=p.w
    borderless=p.theme in LUMINOUS_THEMES
    cpu_y,gpu_y,ram_y,footer_y=(39,124,209,272) if landscape else (39,152,265,375)
    if borderless:
        for top,end,kind in ((0,cpu_y,'header'),(cpu_y,gpu_y,'cpu'),
                             (gpu_y,ram_y,'gpu'),(ram_y,footer_y,'ram')):
            p.im.paste(overview_strip(w,end-top,kind,p.theme in LUMINOUS_DAY_THEMES),(0,2*top))
    def percent_width(key,size):
        unit=p.measure('%',21,True)+3 if number(m.get(key)) is not None else 0
        return unit+p.measure(value(m.get(key)),size,True)
    def percent_right(y,key,size=42,baseline=False):
        unit=p.measure('%',21,True) if number(m.get(key)) is not None else 0
        color=load_color(p,m.get(key))
        p.text(w-20-unit-3,y,value(m.get(key)),size,color,True,'rs' if baseline else 'rm')
        if unit:p.text(w-20,y if baseline else y+6,'%',21,color,True,'rs' if baseline else 'rm')
    def hardware(top,key,color):
        height=77 if landscape else 105
        if not borderless:
            surface(p,(8,top,w-8,top+height),key)
        y=top+9
        if not borderless:
            hardware_badge(p,20,y,key,color,28)
        model=short_model(m.get(key+'_name'),key)
        vendor=hardware_vendor(m.get(key+'_name'))
        heat=temperature_color(p,m.get(key+'_temp'))
        temperature=value(m.get(key+'_temp'),'°')
        mhz=number(m.get('cpu_mhz' if key=='cpu' else 'gpu_clock_mhz'))
        frequency=value(mhz/1000 if mhz is not None else None,'',1).replace('.',',')
        frequency_unit='ГГц'
        if mhz is not None and 0<mhz<100:
            frequency=value(mhz);frequency_unit='МГц'
        vram=video_memory(m) if key=='gpu' else None
        if vram:
            frequency, total=vram
            frequency_unit=f'/ {total} ГБ'
        rpm=value(m.get(key+'_fan_rpm'))
        # One title row, then three fixed columns: clock, cooling, load.
        # Align complete visual groups rather than unrelated text anchors.
        header_size=21
        model_width=170 if landscape else 146
        model=f'{vendor} {model}' if vendor else model
        load=number(m.get(key+'_percent'))
        percent_size=50 if landscape else (42 if load is not None and load>=100 else 58)
        model_x=20 if borderless else 58
        if borderless:
            model_width=w-20-percent_width(key+'_percent',percent_size)-model_x-9
        while header_size>16 and p.measure(model,header_size,True)>model_width:header_size-=1
        p.fit(model_x,y+14,model,model_width,header_size,p.c['text'],True,'lm')
        if landscape:
            p.fit(20,top+64,frequency,120,32,p.c['text'],True,'ls')
            p.text(25+min(120,p.measure(frequency,32,True)),top+64,frequency_unit,18,p.c['muted'],anchor='ls')
            cooling_x=285
            rpm_size=20;rpm_unit=17
            rpm_width=20+p.measure(rpm,rpm_size,True)+4+p.measure('rpm',rpm_unit)
            rpm_x=w-20-rpm_width
            hardware_icon(p,rpm_x,top+48,'fan',p.c['muted'],18)
            p.text(rpm_x+20,top+64,rpm,rpm_size,p.c['text'],True,'ls')
            p.text(rpm_x+24+p.measure(rpm,rpm_size,True),top+64,'rpm',rpm_unit,p.c['muted'],anchor='ls')
            temp_width=18+p.measure(temperature,rpm_size,True)
            hardware_icon(p,cooling_x-temp_width/2,top+47,'temp',heat,18)
            p.text(cooling_x-temp_width/2+18,top+64,temperature,rpm_size,heat,True,'ls')
            percent_right(top+41,key+'_percent',percent_size,baseline=True)
        else:
            # Three metric groups on a common baseline, with fixed column gaps.
            freq_size=30 if vram else 32
            suffix_size=13 if vram else 17
            while freq_size>(19 if vram else 24) and p.measure(frequency,freq_size,True)+4+p.measure(frequency_unit,suffix_size)>94:
                freq_size-=1
            freq_width=94-4-p.measure(frequency_unit,suffix_size)
            p.fit(20,top+82,frequency,freq_width if vram else 100,freq_size,p.c['text'],True,'ls')
            p.text(24+min(freq_width if vram else 100,p.measure(frequency,freq_size,True)),
                   top+82,frequency_unit,suffix_size,p.c['muted'],anchor='ls')
            rpm_size=20 if len(rpm)>=5 else 22;rpm_unit=17
            unit_x=w-20-p.measure('rpm',rpm_unit)
            rpm_right=unit_x-5
            hardware_icon(p,rpm_right-p.measure('00000' if len(rpm)>=5 else '0000',rpm_size,True)-24,top+65,'fan',p.c['muted'],20)
            p.text(rpm_right,top+82,rpm,rpm_size,p.c['text'],True,'rs')
            p.text(unit_x,top+82,'rpm',rpm_unit,p.c['muted'],anchor='ls')
            temp_width=18+p.measure(temperature,rpm_size,True)
            temp_x=150-temp_width/2
            hardware_icon(p,temp_x,top+65,'temp',heat,18)
            p.text(temp_x+18,top+82,temperature,rpm_size,heat,True,'ls')
            percent_right(top+52,key+'_percent',percent_size,baseline=True)
        p.bar(20,top+height-(7 if landscape else 12),w-40,m.get(key+'_percent'),color,4)
    if landscape:
        hardware(cpu_y,'cpu',p.c['primary']);hardware(gpu_y,'gpu',p.c['secondary']);ry,rh=ram_y,57
    else:
        hardware(cpu_y,'cpu',p.c['primary']);hardware(gpu_y,'gpu',p.c['secondary']);ry,rh=ram_y,102
    if not borderless:
        surface(p,(8,ry,w-8,ry+rh),'ram')
        hardware_badge(p,20,ry+9,'ram',p.c['memory'],28)
    ram_type=m.get('ram_type') or 'ОЗУ';mt=number(m.get('ram_speed_mt'))
    installed=number(m.get('ram_installed_gb'))
    capacity=value(installed if installed is not None else m.get('ram_total_gb'))
    used=value(m.get('ram_used_gb'),digits=1).replace('.',',')
    ram_temp=number(m.get('ram_temp'))
    if ram_temp is not None and not 0 < ram_temp <= 110:
        ram_temp=None
    memory_label=f"{capacity} ГБ {ram_type}"+(f" {mt:.0f} МГц" if mt else '')
    label_size=21
    label_x=20 if borderless else 58
    label_width=w-label_x-20
    while label_size>16 and p.measure(memory_label,label_size,True)>label_width:label_size-=1
    if landscape:
        label_size=min(label_size,17 if borderless else 14)
        p.fit(label_x,ry+14,memory_label,250,label_size,p.c['text'],True,'lm')
    else:
        p.fit(label_x,ry+23,memory_label,label_width,label_size,p.c['text'],True,'lm')
    memory_baseline=ry+(50 if landscape else 79)
    used_size=24 if landscape else 32
    used_x=58 if landscape else 20
    if borderless and not landscape and ram_temp is not None:
        available = w-20-percent_width('ram_percent',32)-used_x-39-p.measure('ГБ',17)-p.measure(value(ram_temp,'°'),22,True)
        while used_size > 21 and p.measure(used,used_size,True)>available:
            used_size-=1
    p.text(used_x,memory_baseline,used,used_size,p.c['text'],True,'ls')
    used_unit_size=15 if landscape else 17
    used_unit_x=used_x+4+p.measure(used,used_size,True)
    p.text(used_unit_x,memory_baseline,'ГБ',used_unit_size,p.c['muted'],anchor='ls')
    if ram_temp is not None:
        # Same temperature group as the CPU/GPU cards: icon left, reading right, bottom row.
        heat=temperature_color(p,ram_temp)
        temperature=value(ram_temp,'°')
        temp_size=20 if landscape else 22
        temp_width=18+p.measure(temperature,temp_size,True)
        percent=value(m.get('ram_percent'))
        percent_unit=p.measure('%',21,True) if number(m.get('ram_percent')) is not None else 0
        percent_left=w-20-percent_unit-3-p.measure(percent,32,True)
        temp_x=(285-temp_width/2) if landscape else (150-temp_width/2)
        used_end=used_unit_x+p.measure('ГБ',used_unit_size,True)
        temp_x=max(used_end+8,temp_x)
        temp_x=min(temp_x,percent_left-8-temp_width)
        hardware_icon(p,temp_x,memory_baseline-17,'temp',heat,18)
        p.text(temp_x+18,memory_baseline,temperature,temp_size,heat,True,'ls')
    percent_right(memory_baseline,'ram_percent',32,baseline=True)
    p.bar(20,ry+rh-(5 if landscape else 12),w-40,m.get('ram_percent'),p.c['memory'],4)
    card_box=(8,274,w-8,312) if landscape else (8,375,w-8,472)
    full_footer=(0,footer_y,w,p.h) if borderless else None
    if not overview_content_card(p,overview_content,card_box,landscape,surface_box=full_footer):
        weather_card(p,weather,card_box,landscape,animation,surface_box=full_footer)


def compact_weather_description(description):
    import re
    text=str(description or 'Нет прогноза')
    text=text.replace('Преимущественно ясно','В основном ясно')
    text=text.replace('Гроза с небольшим градом','Гроза с градом').replace('Гроза с сильным градом','Гроза с градом')
    text=re.sub(r'^(?:Сильн(?:ый|ая)|Небольш(?:ой|ая)|Умеренн(?:ый|ая)|Слаб(?:ый|ая))\s+', '',text,flags=re.I)
    return text[:1].upper()+text[1:]


def _weather_reading(p, x, y, temp, width, size, color):
    degree_size = round(size * .59)
    degree_width = p.measure('°', degree_size) + 1 if temp != '—' else 0
    while size > 21 and p.measure(temp, size) + degree_width > width:
        size -= 1
    p.fit(x, y, temp, width - degree_width, size, color)
    if degree_width:
        p.text(x + min(width - degree_width, p.measure(temp, size)) + 1,
               y + 1, '°', degree_size, color)


def weather_card(p, weather, box, landscape, animation=None, *, surface_box=None):
    x, y, x2, y2 = box
    sx,sy,sx2,sy2=surface_box or box
    w, h = round((sx2-sx)*2), round((sy2-sy)*2)
    code = number(weather.get('weather_code'))
    kind = sky_kind(int(code) if code is not None else None)
    day = weather.get('is_day') is not False
    temp = value(weather.get('temperature'))
    city = weather.get('city') or 'Погода'
    description = compact_weather_description(weather.get('description'))
    if surface_box is not None:
        cached = overview_weather_surface(w, h, kind, day, p.theme in LUMINOUS_DAY_THEMES)
        mask = None
    else:
        cached, mask = card_surface(w, h, kind, day, landscape)
    card = cached.copy()
    # A compact panorama on the right, composed separately from the text.
    scene_w, scene_h = (112, 72) if landscape else ((234, 171) if kind == 'fog' else (270, 171))
    scene = draw_scene(kind, day, number(animation), scene_w, scene_h)
    if scene is not None:
        margin = (5 if landscape else (12 if kind == 'fog' else 1)) + round((sx2-x2)*2)
        card.paste(scene, (w - scene_w - margin, (h - scene_h) // 2), scene)
    p.im.paste(card, (round(sx*2), round(sy*2)), mask)

    white, soft = (p.c['text'], p.c['muted']) if surface_box is not None and p.theme in LUMINOUS_DAY_THEMES else ('#FAFCFF', '#CFDEEF')
    feels = value(weather.get('feels_like'), '°')
    wind = value(weather.get('wind_kmh'), ' км/ч')
    stale = weather.get('stale') and (temp != '—' or kind != 'unknown')
    if landscape:
        _weather_reading(p, x + 13, y + 7, temp, 79, 30, white)
        p.fit(x + 100, y + 3, city, 176, 16, white, True)
        p.fit(x + 100, y + 21, description, 176, 13, soft)
        detail_x = x2 - 66
        p.line([(x + 284, y + 8), (x + 284, y2 - 8)], '#54718B')
        if feels != '—':
            p.fit(detail_x, y + 5, f'Ощущ. {feels}', 108, 13, white, anchor='rt')
        if stale or wind != '—':
            p.fit(detail_x, y + 21 if feels != '—' else y + 12,
                  'Кэш прогноза' if stale else f'Ветер {wind}', 108, 13, soft, anchor='rt')
    else:
        left = x+14
        p.fit(left, y + 10, city, 164, 18, white, True)
        _weather_reading(p, left, y + 33, temp, 92, 47, white)
        p.wrap(x + 112, y + (40 if p.measure(description, 14) <= 88 else 30),
               description, 88, 14, 2, white)
        if feels != '—':
            p.fit(left, y2-19, f'Ощущается {feels}', 116, 13, soft)
        if stale or wind != '—':
            p.fit(x+138 if feels != '—' else left, y2-19,
                  'Кэш прогноза' if stale else wind, 88, 13, soft)


@lru_cache(maxsize=24)
def overview_weather_surface(w, h, kind, day, light=False):
    if light:
        return sky_panorama(w, h, kind, day, 0, False, light=True)
    return ImageChops.lighter(sky_panorama(w, h, kind, day, 0, False),
                              overview_strip(w//2, h//2, 'weather'))

def graph_page(p,m,history,thermal,landscape):
    """Show only the CPU/GPU telemetry streams that actually feed this page."""
    keys = ([('cpu_temp', 'Процессор', 'ЦП', p.c['primary']),
             ('gpu_temp', 'Видеокарта', 'GPU', p.c['secondary'])]
            if thermal else
            [('cpu_percent', 'Процессор', 'ЦП', p.c['primary']),
             ('gpu_percent', 'Видеокарта', 'GPU', p.c['secondary'])])
    unit = '°C' if thermal else '%'
    maximum = 120 if thermal else 100
    margin, gap, top = 8, 10, 42
    if landscape:
        card_w = (p.w - margin * 2 - gap) / 2
        card_h = p.h - top - 10
        boxes = [(margin, top, margin + card_w, top + card_h),
                 (margin + card_w + gap, top, p.w - margin, top + card_h)]
    else:
        card_h = (p.h - top - 12 - gap) / 2
        boxes = [(margin, top, p.w - margin, top + card_h),
                 (margin, top + card_h + gap, p.w - margin, p.h - 10)]

    for (key, label, short, color), (x, y, x2, y2) in zip(keys, boxes):
        p.rect((x, y, x2, y2), radius=18)
        p.text(x + 14, y + 10, label, 18, p.c['text'], True)
        p.text(x + 14, y + 34, 'ТЕМПЕРАТУРА' if thermal else 'ЗАГРУЗКА',
               14, p.c['muted'], True)
        current = number(m.get(key))
        shown = value(current, unit)
        current_size = 36 if landscape else 38
        p.fit(x2 - 14, y + 8, shown, min(112, (x2 - x) * .46),
              current_size, color, True, 'rt')
        p.line([(x + 14, y + 58), (x2 - 14, y + 58)], p.c['line'], 1)

        chart_top = y + 77
        chart_bottom = y2 - 35
        axis_x = x + 52
        chart_x2 = x2 - 14
        if chart_bottom - chart_top < 45:
            chart_top, chart_bottom = y + 70, y2 - 28
        series = history.get(key, []) if isinstance(history, dict) else []
        try:
            series = list(series)[-60:]
        except TypeError:
            series = []
        if current is not None:
            series.append(current)
        has_data = current is not None or any(number(item) is not None for item in series)
        if has_data:
            p.graph((axis_x, chart_top, chart_x2, chart_bottom), series, color, maximum)
            p.text(axis_x - 6, chart_top - 4, f'{maximum} {unit}', 13,
                   p.c['muted'], False, 'rt')
            p.text(x + 13, chart_bottom - 13, '0', 13, p.c['muted'])
        else:
            p.line([(axis_x, chart_top), (chart_x2, chart_top)], p.c['line'], 1)
            p.line([(axis_x, chart_bottom), (chart_x2, chart_bottom)], p.c['line'], 1)
            p.fit(axis_x + 6, (chart_top + chart_bottom) / 2,
                  'Нет датчика' if thermal else 'Ожидание данных',
                  chart_x2 - axis_x - 12, 16, p.c['muted'], False, 'lm')
        p.text(x + 14, y2 - 22, 'За минуту', 13, p.c['muted'])
        p.text(x2 - 14, y2 - 22, 'СЕЙЧАС', 13, p.c['muted'], True, 'rt')

def weather_page(p,weather,landscape):
    from weather_design import draw_weather_page
    original_colors=p.c
    p.c=dict(THEMES['night'],secondary='#DBEBFA',primary='#82DEFF')
    try:draw_weather_page(p,weather,landscape)
    finally:p.c=original_colors


_MONTHS_RU = ('ЯНВАРЬ', 'ФЕВРАЛЬ', 'МАРТ', 'АПРЕЛЬ', 'МАЙ', 'ИЮНЬ',
              'ИЮЛЬ', 'АВГУСТ', 'СЕНТЯБРЬ', 'ОКТЯБРЬ', 'НОЯБРЬ', 'ДЕКАБРЬ')
_WEEKDAYS_RU = ('ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС')
_DAY_NAMES_RU = ('Понедельник', 'Вторник', 'Среда', 'Четверг',
                 'Пятница', 'Суббота', 'Воскресенье')


def _uptime_label(metrics):
    uptime = number(metrics.get('uptime_seconds'))
    if uptime is None:
        return 'Время работы недоступно'
    hours, minutes = int(uptime // 3600), int(uptime % 3600 // 60)
    return f'Работает {hours} ч {minutes} мин'


def _draw_month(p, box, now):
    x, y, x2, y2 = box
    p.rect(box, radius=16)
    p.text((x + x2) / 2, y + 7,
           f'{_MONTHS_RU[now.month - 1]} {now.year}',
           19, p.c['text'], True, 'mt')
    grid_top = y + 37
    width = (x2 - x - 20) / 7
    left = x + 10
    row_h = (y2 - grid_top - 8) / 7
    weekday_size, day_size = (14, 17) if p.theme in LUMINOUS_THEMES else (13, 15)
    for index, weekday in enumerate(_WEEKDAYS_RU):
        p.text(left + width * (index + .5), grid_top + 2, weekday,
               weekday_size, p.c['muted'], True, 'mt')
    month_dates = calendar.Calendar(firstweekday=0).monthdatescalendar(now.year, now.month)
    # Keep a stable six-row grid so every month fits the same well-balanced card.
    dates = [day for week in month_dates for day in week]
    if len(dates) < 42:
        last_visible_day = dates[-1]
        dates.extend(last_visible_day + timedelta(days=offset)
                     for offset in range(1, 43 - len(dates)))
    dates = dates[:42]
    for index, day in enumerate(dates):
        row, column = divmod(index, 7)
        cx = left + width * (column + .5)
        cy = grid_top + row_h * (row + 1) + 1
        if day == now.date():
            radius = min(12, row_h * .43)
            p.rect((cx - radius, cy - radius, cx + radius, cy + radius),
                   p.c['primary'], radius)
            color = '#112334' if p.theme in LUMINOUS_THEMES and p.theme not in DAY_THEMES else '#FFFFFF'
        else:
            color = p.c['text'] if day.month == now.month else p.c['muted']
        p.text(cx, cy, day.day, day_size, color, day == now.date(), 'mm')


def clock_page(p,m,now,landscape):
    w,h=p.w,p.h
    if landscape:
        p.text(111, 73, now.strftime('%H:%M'), 72, p.c['text'], True, 'mt')
        p.text(111, 167, _DAY_NAMES_RU[now.weekday()], 20,
               p.c['primary'], True, 'mt')
        p.text(111, 196, now.strftime('%d.%m.%Y'), 19,
               p.c['muted'], False, 'mt')
        p.fit(25, 254, _uptime_label(m), 174, 15, p.c['muted'], False)
        _draw_month(p, (218, 49, 468, 300), now)
    else:
        p.text(w / 2, 50, now.strftime('%H:%M'), 76, p.c['text'], True, 'mt')
        p.text(w / 2, 134, _DAY_NAMES_RU[now.weekday()], 18,
               p.c['primary'], True, 'mt')
        p.text(w / 2, 157, now.strftime('%d.%m.%Y'), 19,
               p.c['muted'], False, 'mt')
        _draw_month(p, (12, 190, w - 12, 437), now)
        p.fit(w / 2, 448, _uptime_label(m), w - 32, 14, p.c['muted'], False, 'mt')

def _duration_label(seconds):
    numeric = number(seconds)
    if numeric is None or numeric < 0:
        return None
    whole = int(numeric)
    return f'{whole // 60}:{whole % 60:02d}'


def _media_progress(media, *, remaining=False):
    position = number(media.get('position_seconds'))
    duration = number(media.get('duration_seconds'))
    if position is None or duration is None or duration <= 0:
        return None
    sampled_at = number(media.get('position_sampled_at'))
    if media.get('is_playing') is True and sampled_at is not None:
        position += max(0.0, min(15.0, time.monotonic() - sampled_at))
    position = min(max(0.0, position), duration)
    end = '−'+_duration_label(duration-position) if remaining and position < duration else (
        '0:00' if remaining else _duration_label(duration))
    return position / duration * 100, _duration_label(position), end


def _media_cover(p,media,box):
    x,y,x2,y2=box
    size=round(x2-x)
    data=media.get('thumbnail_bytes')
    art=album_image(data,size*2) if isinstance(data,bytes) and len(data)<=2*1024*1024 else None
    if art:
        mask=Image.new('L',art.size,0)
        ImageDraw.Draw(mask).rounded_rectangle((0,0,art.width-1,art.height-1),
                                               radius=round(size*.1*2),fill=255)
        p.im.paste(art,(round(x*2),round(y*2)),mask)
        p.d.rounded_rectangle((round(x*2),round(y*2),round(x2*2),round(y2*2)),
                              radius=round(size*.1*2),outline='#FFFFFF',width=2)
    else:
        p.rect(box, radius=18)
        center_x, center_y = (x+x2)/2, (y+y2)/2
        scale = min(1, size/125)
        for i,hh in enumerate((28,54,82,62,38)):
            xx=center_x+(-48+i*20)*scale
            p.rect((xx,center_y-hh/2*scale,xx+9*scale,center_y+hh/2*scale),
                   p.c['primary'] if i%2 else p.c['secondary'],5*scale)


def media_page(p,media,landscape):
    w=p.w
    has_title=bool(media.get('title'))
    status=('ИГРАЕТ' if media.get('is_playing') is True else
            'ПАУЗА' if media.get('is_playing') is False else 'ТРЕК') if has_title else 'НЕТ ТРЕКА'
    status_color=p.c['memory'] if media.get('is_playing') is True else p.c['primary']
    pill_fill=('#204A45' if p.theme not in DAY_THEMES else '#E5F5EE') if media.get('is_playing') is True else (
        '#323B58' if p.theme not in DAY_THEMES else '#E8EDF8')
    progress=_media_progress(media) if has_title else None
    if landscape:
        cover=(20,60,218,258)
        tx,tw=238,w-256
        _media_cover(p,media,cover)
        p.rect((tx,59,tx+96,84),pill_fill,12)
        p.fit(tx+11,64,status,76,14,status_color,True)
        if has_title:
            p.text(tx,94,'НАЗВАНИЕ',12,p.c['muted'],True)
            p.fit(tx,109,media.get('title'),tw,25,p.c['text'],True)
            p.text(tx,152,'ИСПОЛНИТЕЛЬ',12,p.c['muted'],True)
            p.fit(tx,168,media.get('artist') or 'Не указан',tw,19,p.c['text'])
            p.text(tx,205,'АЛЬБОМ',12,p.c['muted'],True)
            p.fit(tx,220,media.get('album') or 'Не указан',tw,17,p.c['text'])
        else:
            p.wrap(tx,104,'Музыка не играет',tw,25,2,p.c['text'],True)
            p.wrap(tx,171,'Запустите трек в плеере Windows',tw,17,3,p.c['muted'])
        if progress:
            percent,position,duration=progress
            p.bar(tx,262,tw,percent,p.c['primary'],5)
            p.text(tx,273,position,15,p.c['muted'])
            p.text(tx+tw,273,duration,15,p.c['muted'],False,'rt')
        elif has_title:
            p.text(tx,264,'Время трека недоступно',14,p.c['muted'])
    else:
        cover=(64,55,256,247)
        _media_cover(p,media,cover)
        pill_width=108 if has_title else 122
        p.rect((w/2-pill_width/2,254,w/2+pill_width/2,279),pill_fill,12)
        p.text(w/2,258,status,14,status_color,True,'mt')
        if has_title:
            p.text(20,286,'НАЗВАНИЕ',12,p.c['muted'],True)
            p.fit(20,301,media.get('title'),w-40,27,p.c['text'],True)
            p.text(20,341,'ИСПОЛНИТЕЛЬ',12,p.c['muted'],True)
            p.fit(20,356,media.get('artist') or 'Не указан',w-40,19,p.c['text'])
            p.text(20,392,'АЛЬБОМ',12,p.c['muted'],True)
            p.fit(20,407,media.get('album') or 'Не указан',w-40,17,p.c['text'])
        else:
            p.text(20,304,'Музыка не играет',25,p.c['text'],True)
            p.wrap(20,354,'Запустите трек в плеере Windows',w-40,18,2,p.c['muted'])
        if progress:
            percent,position,duration=progress
            p.bar(20,439,w-40,percent,p.c['primary'],5)
            p.text(20,449,position,15,p.c['muted'])
            p.text(w-20,449,duration,15,p.c['muted'],False,'rt')
        elif has_title:
            p.text(20,440,'Время трека недоступно',14,p.c['muted'])

def gaming_page(p,m,landscape):
    w,h=p.w,p.h
    fps=number(m.get('fps'))
    if fps is None:
        p.rect((8,46,w-8,h-10),radius=20)
        rtss_off='RTSS не запущен' in str(m.get('gaming_status') or '')
        title='RTSS выключен' if rtss_off else 'Ожидаем игру'
        hint=('Для FPS запустите RTSS и игру' if rtss_off
              else 'Откройте игру — FPS появится сам')
        if landscape:
            p.text(27,57,'ЧАСТОТА КАДРОВ',14,p.c['secondary'],True)
            p.text(28,77,'FPS',72,p.c['secondary'],True)
            p.text(234,71,title,24,p.c['text'],True)
            p.wrap(234,111,hint,218,17,3,p.c['muted'])
            cards=((18,198,234,293,'CPU','cpu'),(246,198,462,293,'GPU','gpu'))
            for x,y,x2,y2,label,key in cards:
                percent=number(m.get(key+'_percent'))
                temperature=number(m.get(key+'_temp'))
                color=p.c['primary'] if key=='cpu' else p.c['secondary']
                p.rect((x,y,x2,y2),p.c['card'],radius=15,outline=p.c['line'])
                p.text(x+12,y+9,label,15,color,True)
                p.text(x+55,y+8,value(percent,'%'),24,p.c['text'],True)
                p.text(x+12,y+39,'ЗАГРУЗКА',12,p.c['muted'],True)
                p.text(x2-12,y+8,value(temperature,'°C'),19,color,True,'rt')
                p.text(x2-12,y+39,'ТЕМПЕРАТУРА',12,p.c['muted'],True,'rt')
                p.bar(x+12,y2-12,x2-x-24,percent,color,4)
        else:
            p.text(22,56,'ЧАСТОТА КАДРОВ',14,p.c['secondary'],True)
            p.text(23,76,'FPS',72,p.c['secondary'],True)
            p.text(24,146,'ПОКА НЕТ ДАННЫХ',13,p.c['muted'],True)
            p.rect((18,176,w-18,247),p.c['card'],radius=16,outline=p.c['line'])
            p.text(30,185,title,22,p.c['text'],True)
            p.fit(30,216,hint,w-60,15,p.c['muted'])
            cards=((18,260,154,457,'CPU','cpu'),(166,260,302,457,'GPU','gpu'))
            for x,y,x2,y2,label,key in cards:
                percent=number(m.get(key+'_percent'))
                temperature=number(m.get(key+'_temp'))
                color=p.c['primary'] if key=='cpu' else p.c['secondary']
                p.rect((x,y,x2,y2),p.c['card'],radius=16,outline=p.c['line'])
                p.text(x+12,y+12,label,16,color,True)
                p.text(x2-12,y+10,value(percent,'%'),30,p.c['text'],True,'rt')
                p.text(x+12,y+57,'ЗАГРУЗКА',12,p.c['muted'],True)
                p.bar(x+12,y+80,x2-x-24,percent,color,5)
                p.line([(x+12,y+105),(x2-12,y+105)],p.c['line'],1)
                p.text(x+12,y+119,'ТЕМП.',13,p.c['muted'],True)
                p.text(x2-12,y+115,value(temperature,'°C'),25,color,True,'rt')
        return

    p.rect((8,46,w-8,h-10),radius=20)
    p.fit(22,53,m.get('process_name') or 'Активная игра',w-44,18,p.c['muted'],True)
    if landscape:
        p.text(30,78,value(fps, digits=0),82,p.c['text'],True)
        p.text(34,164,'КАДРОВ / С',15,p.c['primary'],True)
        if number(m.get('frametime_ms')) is not None:
            frame_time=value(m.get('frametime_ms'),' мс',1).replace('.',',')
            p.text(268,105,frame_time,46,p.c['secondary'],True,'mt')
            p.text(268,162,'ВРЕМЯ КАДРА',14,p.c['muted'],True,'mt')
        cards=((18,198,234,293,'CPU','cpu'),(246,198,462,293,'GPU','gpu'))
        for x,y,x2,y2,label,key in cards:
            percent=number(m.get(key+'_percent'))
            temperature=number(m.get(key+'_temp'))
            if percent is None and temperature is None:
                continue
            color=p.c['primary'] if key=='cpu' else p.c['secondary']
            p.rect((x,y,x2,y2),p.c['card'],radius=15,outline=p.c['line'])
            p.text(x+12,y+9,label,15,color,True)
            if percent is not None:
                p.text(x+52,y+8,value(percent,'%'),24,p.c['text'],True)
                p.text(x+12,y+39,'НАГРУЗКА',12,p.c['muted'],True)
                p.bar(x+12,y2-12,x2-x-24,percent,color,4)
            if temperature is not None:
                p.text(x2-12,y+8,value(temperature,'°C'),19,color,True,'rt')
                p.text(x2-12,y+39,'ТЕМПЕРАТУРА',12,p.c['muted'],True,'rt')
    else:
        p.text(w/2,75,value(fps,digits=0),86,p.c['text'],True,'mt')
        p.text(w/2,160,'КАДРОВ / С',15,p.c['primary'],True,'mt')
        if number(m.get('frametime_ms')) is not None:
            p.rect((18,188,w-18,249),p.c['card'],radius=16,outline=p.c['line'])
            frame_time=value(m.get('frametime_ms'),' мс',1).replace('.',',')
            p.text(32,198,'ВРЕМЯ КАДРА',14,p.c['muted'],True)
            p.text(w-32,193,frame_time,34,p.c['secondary'],True,'rt')
        cards=((18,260,154,457,'CPU','cpu'),(166,260,302,457,'GPU','gpu'))
        for x,y,x2,y2,label,key in cards:
            percent=number(m.get(key+'_percent'))
            temperature=number(m.get(key+'_temp'))
            if percent is None and temperature is None:
                continue
            color=p.c['primary'] if key=='cpu' else p.c['secondary']
            p.rect((x,y,x2,y2),p.c['card'],radius=16,outline=p.c['line'])
            p.text(x+12,y+12,label,16,color,True)
            if percent is not None:
                p.text(x2-12,y+10,value(percent,'%'),30,p.c['text'],True,'rt')
                p.text(x+12,y+57,'ЗАГРУЗКА',12,p.c['muted'],True)
                p.bar(x+12,y+80,x2-x-24,percent,color,5)
            if temperature is not None:
                if percent is not None:
                    p.line([(x+12,y+105),(x2-12,y+105)],p.c['line'],1)
                p.text(x+12,y+119,'ТЕМП.',13,p.c['muted'],True)
                p.text(x2-12,y+115,value(temperature,'°C'),25,
                       color,True,'rt')


def overview_content_card(p,content,box,landscape,*,surface_box=None):
    """Render the optional live game/music tile without disturbing overview geometry."""
    if not isinstance(content,dict) or content.get('kind') not in ('gaming','media'):
        return False
    x,y,x2,y2=box
    if surface_box is not None:
        sx,sy,sx2,sy2=surface_box
        p.im.paste(overview_strip(sx2-sx,sy2-sy,'header',p.theme in LUMINOUS_DAY_THEMES),(2*sx,2*sy))
    else:
        p.rect(box, '#D9EAFB' if p.theme=='day' else '#203B5A', radius=16)
    kind=content.get('kind')
    if kind=='gaming':
        fps=value(content.get('fps'))
        frametime=value(content.get('frametime_ms'),' мс',1).replace('.',',')
        process=content.get('process_name') or 'Активная игра'
        if landscape:
            p.text(x+12,(y+y2)/2,'ИГРА',13,p.c['muted'],True,'lm')
            p.fit(x+55,(y+y2)/2,
                  f'{fps} FPS · {frametime}',137,16,p.c['text'],True,'lm')
            p.fit(x+202,(y+y2)/2,process,145,14,p.c['muted'],False,'lm')
            available=[]
            for label,key in (('CPU','cpu_temp'),('GPU','gpu_temp')):
                metric=value(content.get(key),'°')
                if metric!='—': available.append(f'{label} {metric}')
            if available:
                p.fit(x2-12,(y+y2)/2,' · '.join(available),105,12,
                      p.c['text'],False,'rm')
        else:
            p.text(x+13,y+11,'ИГРА',13,p.c['muted'],True)
            p.text(x+13,y+36,f'{fps} FPS',30,p.c['text'],True)
            p.fit(x+137,y+21,process,x2-x-151,17,p.c['text'],True)
            p.fit(x+139,y+56,frametime,x2-x-151,15,p.c['muted'])
            available=[]
            for label,key in (('CPU','cpu_percent'),('GPU','gpu_percent')):
                metric=value(content.get(key),'%')
                if metric!='—': available.append(f'{label} {metric}')
            if available:
                p.fit(x+139,y+75,' · '.join(available),x2-x-151,14,p.c['muted'])
    else:
        title=content.get('title') or 'Музыка'
        artist=content.get('artist') or 'Исполнитель не указан'
        if landscape:
            if isinstance(content.get('thumbnail_bytes'),bytes):
                _media_cover(p,content,(x+5,y+4,x+35,y2-4))
                text_x=x+45
            else:
                p.text(x+14,(y+y2)/2,'♫',24,p.c['primary'],True,'lm')
                text_x=x+48
            line=f'{title} · {artist}'
            p.fit(text_x,(y+y2)/2,line,x2-text_x-12,15,p.c['text'],True,'lm')
        else:
            if isinstance(content.get('thumbnail_bytes'),bytes):
                _media_cover(p,content,(x+12,y+16,x+78,y+82))
                text_x=x+91
            else:
                p.text(x+18,y+24,'♫',32,p.c['primary'],True)
                text_x=x+60
            p.fit(text_x,y+17,title,x2-text_x-14,18,p.c['text'],True)
            p.fit(text_x,y+44,artist,x2-text_x-14,16,p.c['muted'])
            p.text(text_x,y+68,'СЕЙЧАС ИГРАЕТ',13,p.c['memory'],True)
    return True

def render(metrics,weather,history,page='overview',rotation=90,now=None,alert='',media=None,theme='night',brand_name='Мой компьютер',overview_content=None,animation=None):
    now=now or datetime.now();metrics=metrics if isinstance(metrics,dict) else {};weather=weather if isinstance(weather,dict) else {}
    history=history if isinstance(history,dict) else {};media=media if isinstance(media,dict) else {};landscape=rotation in (90,270)
    p=Painter(480 if landscape else 320,320 if landscape else 480,theme)
    if p.theme in LUMINOUS_THEMES:
        from luminous import draw
        draw(p, page, metrics, weather, history, now, media, brand_name,
             landscape, overview_content, animation, alert)
    else:
        header(p,page,now,brand_name)
        if page in ('clock','media') or (page=='gaming' and number(metrics.get('fps')) is not None):
            p.rect((8,39,p.w-8,p.h-8),radius=20)
        if page=='overview':overview(p,metrics,weather,landscape,overview_content=overview_content,animation=animation)
        elif page in ('graphs','thermal'):graph_page(p,metrics,history,page=='thermal',landscape)
        elif page=='weather':weather_page(p,weather,landscape)
        elif page=='clock':clock_page(p,metrics,now,landscape)
        elif page=='media':media_page(p,media,landscape)
        elif page=='gaming':gaming_page(p,metrics,landscape)
        else:overview(p,metrics,weather,landscape,animation=animation)
    if alert and p.theme not in LUMINOUS_THEMES:
        p.rect((4,p.h-54,p.w-4,p.h-4),'#512330',10,RED)
        short=str(alert).replace(' — высокая температура','').replace('Процессор:','CPU').replace('Видеокарта:','GPU')
        p.wrap(14,p.h-47,short,p.w-28,18,2,'#FFF5F7',True)
    return p.finish()
