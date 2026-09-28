"""Cached original wallpaper and frosted materials for the monitor UI."""
from functools import lru_cache
import math
from PIL import Image, ImageDraw, ImageFilter


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


@lru_cache(maxsize=64)
def panel_tile(width,height,theme,box,radius=20,scale=2):
    """Blur actual wallpaper under a panel, tint it, then add edge light/shadow."""
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
    tile=Image.alpha_composite(tile,base);d=ImageDraw.Draw(tile)
    # Borderless material: no bright perimeter around content.

    return tile,(x-pad,y-pad)
