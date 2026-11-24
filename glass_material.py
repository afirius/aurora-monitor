"""Cached original wallpaper and frosted materials for the monitor UI."""
from functools import lru_cache
import math
from PIL import Image, ImageChops, ImageDraw, ImageFilter


LUMINOUS_DAY_THEMES = ('luminous_day', 'luminous_classic_day')
LUMINOUS_BOTTOM_THEMES = ('luminous_classic', 'luminous_classic_day')
LUMINOUS_THEMES = ('luminous', 'luminous_classic', *LUMINOUS_DAY_THEMES)
DAY_THEMES = ('day', *LUMINOUS_DAY_THEMES)
PORCELAIN = '#F2F4F8'


def _rgb(text):
    return tuple(int(text[i:i+2],16) for i in (1,3,5))


def _gradient(size,top,bottom):
    im=Image.new('RGB',size);d=ImageDraw.Draw(im);a=_rgb(top);b=_rgb(bottom)
    for y in range(size[1]):
        t=y/max(1,size[1]-1)
        d.line((0,y,size[0],y),fill=tuple(round(v*(1-t)+z*t) for v,z in zip(a,b)))
    return im


@lru_cache(maxsize=8)
def wallpaper(width,height,theme='night',scale=2):
    w,h=round(width*scale),round(height*scale)
    if theme in LUMINOUS_THEMES:
        return Image.new('RGB', (w, h), PORCELAIN if theme in LUMINOUS_DAY_THEMES else '#000000')
    day=theme=='day'
    im=_gradient((w,h),'#ABCFE8' if day else '#142A48','#C4AEDE' if day else '#312344')
    bands=[(-.12,.32,'#49BFD4','#4572C9'),(.20,.35,'#85CDE5','#7473D2'),(.48,.28,'#84A4EC','#D688C8'),(.84,.18,'#E9B1C7','#A078D4')]
    if not day:bands=[(-.12,.32,'#0D5A72','#174E77'),(.20,.35,'#266F91','#3F4385'),(.48,.28,'#536CAB','#723F81'),(.84,.18,'#8A577B','#392D60')]
    for offset,thickness,top,bottom in bands:
        left=[];right=[]
        for i in range(121):
            t=-.1+i/100
            center=w*(offset+.32*math.sin(t*3.9-.9)+.29*t)
            breadth=w*thickness*(.82+.23*math.cos(t*3))
            left.append((center-breadth/2,t*h));right.append((center+breadth/2,t*h))
        mask=Image.new('L',(w,h));ImageDraw.Draw(mask).polygon(left+right[::-1],fill=240)
        mask=mask.filter(ImageFilter.GaussianBlur(max(1,w*.012)))
        im.paste(_gradient((w,h),top,bottom),(0,0),mask)
        sheen=Image.new('RGBA',(w,h));draw=ImageDraw.Draw(sheen)
        draw.line(right,fill=(230,244,255,40 if day else 24),width=max(1,round(scale*2)))
        im=Image.alpha_composite(im.convert('RGBA'),sheen.filter(ImageFilter.GaussianBlur(scale*1.4))).convert('RGB')
    # A continuous porcelain / midnight surface keeps text legible without boxes.
    veil=Image.new('RGB',(w,h),'#FFFFFF' if day else '#10151F')
    im=Image.blend(im,veil,.91 if day else .83)
    return im


@lru_cache(maxsize=48)
def bezel_fade(width, height, box, scale=2):
    """Blend a full-bleed surface into black only where it meets the panel edge."""
    x, y, x2, y2 = box
    w, h = round((x2-x)*scale), round((y2-y)*scale)

    def ramp(length, first, last, spread):
        for i in range(length):
            distance = min(i if first else length, length-1-i if last else length)
            t = min(1., max(0., (distance/scale-4)/spread))
            yield round(255*t*t*(3-2*t))

    horizontal = Image.new('L', (w, 1))
    horizontal.putdata(list(ramp(w, x == 0, x2 == width, 16)))
    vertical = Image.new('L', (1, h))
    vertical.putdata(list(ramp(h, y == 0, y2 == height, 18)))
    return ImageChops.multiply(horizontal.resize((w, h)), vertical.resize((w, h)))


def _obsidian_panel(width, height, box, radius, scale, light):
    """Subtle smoke-glass relief, with no stroke along the outside edges."""
    x, y, x2, y2 = box
    w, h = round((x2-x)*scale), round((y2-y)*scale)
    im = _gradient((w, h), '#FFFFFF' if light else '#1B1D22', '#E2E7F0' if light else '#0D0E11')
    # A narrow warm reflection at the seam, not a frame around the screen.
    d = ImageDraw.Draw(im)
    for row, color in enumerate(('#C5CCDA', '#EEF1F7', '#FFFFFF') if light else ('#535052', '#332F31', '#242329')):
        d.line((0, row, w-1, row), fill=color)
    mask = bezel_fade(width, height, box, scale)
    # Artwork and other inset objects keep their own shape. Only sections
    # which meet the screen edge lose the frame.
    if x > 0 and y > 0 and x2 < width and y2 < height:
        mask = Image.new('L', (w, h))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w-1, h-1), round(radius*scale), fill=255)
    im.putalpha(mask)
    return im, (round(x*scale), round(y*scale))


@lru_cache(maxsize=12)
def overview_strip(width, height, kind, light=False):
    """Continuous edge-to-edge material under the classic overview numbers."""
    top, bottom = {
        'header': ('#000000', '#0A0A0C'),
        'cpu': ('#1B1D22', '#101116'),
        'gpu': ('#191921', '#101016'),
        'ram': ('#171D1D', '#0C1011'),
        'weather': ('#0D1015', '#090A0D'),
        'hardware': ('#24252A', '#16171B'),
    }[kind]
    if light:
        top, bottom = {
            'header': (PORCELAIN, '#EAEDF5'),
            'cpu': ('#FDFEFF', '#E3E8F3'),
            'gpu': ('#FCFAFF', '#E8E3F4'),
            'ram': ('#F9FDFC', '#DFECE8'),
            'weather': (PORCELAIN, '#E2E9F4'),
            'hardware': ('#FDFEFF', '#E4E9F3'),
        }[kind]
    return _gradient((width*2, height*2), top, bottom)


@lru_cache(maxsize=64)
def panel_tile(width,height,theme,box,radius=20,scale=2):
    """Blur actual wallpaper under a panel, tint it, then add edge light/shadow."""
    if theme in LUMINOUS_THEMES:
        return _obsidian_panel(width, height, box, radius, scale, theme in LUMINOUS_DAY_THEMES)
    x,y,x2,y2=(round(v*scale) for v in box);cw,ch=x2-x,y2-y
    pad=round(8*scale);size=(cw+pad*2,ch+pad*2);r=round(radius*scale)
    mask=Image.new('L',size);md=ImageDraw.Draw(mask)
    md.rounded_rectangle((pad,pad,pad+cw-1,pad+ch-1),radius=r,fill=255)
    shadow=Image.new('L',size);ImageDraw.Draw(shadow).rounded_rectangle((pad,pad+3*scale,pad+cw-1,pad+ch-1+3*scale),radius=r,fill=44 if theme=='day' else 75)
    tile=Image.new('RGBA',size,(0,0,0,0));dark=Image.new('RGBA',size,(5,14,35,0));dark.putalpha(shadow.filter(ImageFilter.GaussianBlur(4*scale)))
    tile=Image.alpha_composite(tile,dark)
    base=wallpaper(width,height,theme,scale).filter(ImageFilter.GaussianBlur(14*scale)).crop((x-pad,y-pad,x2+pad,y2+pad)).convert('RGBA')
    tint=Image.new('RGBA',size,(255,255,255,202) if theme=='day' else (27,30,39,195))
    base=Image.alpha_composite(base,tint)
    highlight=Image.new('RGBA',size);hd=ImageDraw.Draw(highlight)
    for row in range(ch):
        strength=round((1-row/max(1,ch-1))*(28 if theme=='day' else 14))
        hd.line((pad,pad+row,pad+cw,pad+row),fill=(255,255,255,strength))
    base=Image.alpha_composite(base,highlight);base.putalpha(mask)
    tile=Image.alpha_composite(tile,base)

    return tile,(x-pad,y-pad)
