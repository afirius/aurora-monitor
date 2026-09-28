"""Aurora 0.2. Native-panel layouts: large readings, essential labels >=16px."""
from datetime import datetime, timedelta
import calendar
from functools import lru_cache
from io import BytesIO
import math
import os
import time
from PIL import Image, ImageDraw, ImageFont, ImageOps
from glass_material import wallpaper, panel_tile

THEMES = {
 'day': dict(bg='#EAF0FB',card='#FFFFFF',line='#CFD7E7',text='#202126',muted='#515866',primary='#0068DC',secondary='#7848CF',warm='#AC5A0A',memory='#167C50'),
 'night': dict(bg='#111321',card='#25293E',line='#3D4560',text='#FAFAFC',muted='#C1C5D0',primary='#65B7FF',secondary='#C4A0FF',warm='#FFD08A',memory='#67DFAB'),
}
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

    def weather_icon(self,x,y,code,size=64,is_day=True):
        s=size/64
        def box(a,b,c,d):return (x+a*s,y+b*s,x+c*s,y+d*s)
        def line(points,color,width=2):self.line([(x+a*s,y+b*s) for a,b in points],color,width*s)
        primary,blue,warm=self.c['primary'],self.c.get('cloud',('#657D97' if self.theme=='day' else '#B6CDDF')),('#E79B20' if self.theme=='day' else '#FFD08A')
        code=number(code)
        if code is None:
            self.ellipse(box(9,9,55,55),outline=self.c['muted'],width=2*s)
            self.text(x+size/2,y+size*.22,'?',round(size*.5),self.c['muted'],True,'mt');return
        code=int(code)
        def sun(cx=32,cy=29,r=13):
            self.ellipse(box(cx-r,cy-r,cx+r,cy+r),warm)
            for a in range(0,360,45):
                t=math.radians(a);line([(cx+(r+5)*math.cos(t),cy+(r+5)*math.sin(t)),(cx+(r+10)*math.cos(t),cy+(r+10)*math.sin(t))],warm,2)
        def moon():
            edge=round(size*2)
            mask=Image.new('L',(edge,edge));md=ImageDraw.Draw(mask)
            def local(a,b,c,d):return tuple(round(v*s*2) for v in (a,b,c,d))
            # A slim, clean young crescent with a soft warm fill.
            md.ellipse(local(11,7,51,47),fill=255)
            md.ellipse(local(24,-1,59,34),fill=0)
            self.im.paste(warm,(round(x*2),round(y*2),round(x*2)+edge,round(y*2)+edge),mask)
            def star(cx,cy,r):
                points=[]
                for index in range(8):
                    angle=-math.pi/2+index*math.pi/4
                    radius=r if index%2==0 else r*.28
                    points.append((round((x+(cx+radius*math.cos(angle))*s)*2),
                                   round((y+(cy+radius*math.sin(angle))*s)*2)))
                self.d.polygon(points,fill=warm)
            star(53,14,3.2)
            star(11,43,2.3)
        def cloud():
            self.ellipse(box(8,23,34,47),blue);self.ellipse(box(22,11,51,44),blue);self.ellipse(box(39,24,62,47),blue)
            self.rect(box(11,32,57,47),blue,6*s)
        if code==0:sun() if is_day else moon()
        elif code==1:
            # Mostly clear: a prominent sun/moon and one small cloud, distinct
            # from both clear sky and the larger partly-cloudy silhouette.
            sun(26,24,14) if is_day else moon()
            self.ellipse(box(33,34,49,49),blue)
            self.ellipse(box(42,28,59,49),blue)
            self.rect(box(36,41,60,51),blue,5*s)
        elif code==2:
            sun(19,19,9) if is_day else moon();cloud()
        elif code==3:cloud()
        elif code in (45,48):
            for yy,xx in ((18,8),(30,2),(42,10),(54,6)):line([(xx,yy),(60-xx/2,yy)],blue,3)
        else:
            cloud()
            if code in (71,73,75,77,85,86):
                for xx in (16,35,53):
                    for a in (0,60,120):
                        t=math.radians(a);line([(xx-4*math.cos(t),56-4*math.sin(t)),(xx+4*math.cos(t),56+4*math.sin(t))],primary,1.5)
            elif code in (95,96,99):
                pts=[(31,44),(23,57),(32,56),(29,65),(43,49),(34,49)]
                self.d.polygon([(round((x+a*s)*2),round((y+b*s)*2)) for a,b in pts],fill=warm)
            elif code in (51,53,55,56,57,61,63,65,66,67,80,81,82):
                for xx in (17,35,53):line([(xx,52),(xx-4,61)],primary,2.5)
            else:self.text(x+size/2,y+size*.76,'?',round(size*.22),self.c['muted'],True,'mt')

    def finish(self):return self.im.resize((self.w,self.h),Image.Resampling.LANCZOS)

def header(p,page,now,brand_name="Мой компьютер"):
    titles={'overview':brand_name,'graphs':'Нагрузка','thermal':'Температуры','weather':'Погода','clock':'Часы','media':'Музыка','gaming':'Игра'}
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
    day=p.theme=='day'
    if temperature<60:return '#147D48' if day else '#73E3A6'
    if temperature<85:return '#9C6500' if day else '#FFD36A'
    return '#C52D40' if day else '#FF8490'


def load_color(p,percent):
    percent=number(percent)
    if percent is None:return p.c['muted']
    percent=round(percent)
    if percent>=80:return '#B15B00' if p.theme=='day' else '#FFBD69'
    return p.c['primary']


def overview(p,m,weather,landscape,overview_content=None):
    w=p.w
    def percent_right(y,key,size=42,baseline=False):
        unit=p.measure('%',21,True) if number(m.get(key)) is not None else 0
        color=load_color(p,m.get(key))
        p.text(w-20-unit-3,y,value(m.get(key)),size,color,True,'rs' if baseline else 'rm')
        if unit:p.text(w-20,y if baseline else y+6,'%',21,color,True,'rs' if baseline else 'rm')
    def hardware(top,key,color):
        height=77 if landscape else 105
        surface(p,(8,top,w-8,top+height),key)
        y=top+9
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
        rpm=value(m.get(key+'_fan_rpm'))
        # One title row, then three fixed columns: clock, cooling, load.
        # Align complete visual groups rather than unrelated text anchors.
        header_size=21
        model_width=170 if landscape else 146
        model=f'{vendor} {model}' if vendor else model
        while header_size>16 and p.measure(model,header_size,True)>model_width:header_size-=1
        p.fit(58,y+14,model,model_width,header_size,p.c['text'],True,'lm')
        if landscape:
            p.text(20,top+64,frequency,32,p.c['text'],True,'ls')
            p.text(25+p.measure(frequency,32,True),top+64,frequency_unit,18,p.c['muted'],anchor='ls')
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
            percent_right(top+41,key+'_percent',50,baseline=True)
        else:
            # Three metric groups on a common baseline, with fixed column gaps.
            freq_size=32
            while freq_size>24 and p.measure(frequency,freq_size,True)+4+p.measure(frequency_unit,17)>94:
                freq_size-=1
            p.text(20,top+82,frequency,freq_size,p.c['text'],True,'ls')
            p.text(24+p.measure(frequency,freq_size,True),top+82,frequency_unit,17,p.c['muted'],anchor='ls')
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
            load=number(m.get(key+'_percent'))
            percent_size=42 if load is not None and load>=100 else 58
            percent_right(top+52,key+'_percent',percent_size,baseline=True)
        p.bar(20,top+height-(7 if landscape else 12),w-40,m.get(key+'_percent'),color,4)
    if landscape:
        hardware(39,'cpu',p.c['primary']);hardware(124,'gpu',p.c['secondary']);ry,rh=209,57
    else:
        hardware(39,'cpu',p.c['primary']);hardware(152,'gpu',p.c['secondary']);ry,rh=265,102
    surface(p,(8,ry,w-8,ry+rh),'ram')
    hardware_badge(p,20,ry+9,'ram',p.c['memory'],28)
    ram_type=m.get('ram_type') or 'ОЗУ';mt=number(m.get('ram_speed_mt'))
    installed=number(m.get('ram_installed_gb'))
    capacity=value(installed if installed is not None else m.get('ram_total_gb'))
    used=value(m.get('ram_used_gb'),digits=1).replace('.',',')
    memory_label=f"{capacity} ГБ {ram_type}"+(f" {mt:.0f} МГц" if mt else '')
    label_size=21
    while label_size>16 and p.measure(memory_label,label_size,True)>w-78:label_size-=1
    if landscape:
        label_size=min(label_size,14)
        p.fit(58,ry+14,memory_label,250,label_size,p.c['text'],True,'lm')
    else:
        p.fit(58,ry+23,memory_label,w-78,label_size,p.c['text'],True,'lm')
    memory_baseline=ry+(50 if landscape else 79)
    used_size=24 if landscape else 32
    used_x=58 if landscape else 20
    p.text(used_x,memory_baseline,used,used_size,p.c['text'],True,'ls')
    p.text(used_x+4+p.measure(used,used_size,True),memory_baseline,'ГБ',15 if landscape else 17,p.c['muted'],anchor='ls')
    percent_right(memory_baseline,'ram_percent',32,baseline=True)
    p.bar(20,ry+rh-(5 if landscape else 12),w-40,m.get('ram_percent'),p.c['memory'],4)
    card_box=(8,274,w-8,312) if landscape else (8,375,w-8,472)
    if not overview_content_card(p,overview_content,card_box,landscape):
        weather_card(p,weather,card_box,landscape)


def compact_weather_description(description):
    import re
    text=str(description or 'Нет прогноза')
    text=text.replace('Преимущественно ясно','В основном ясно')
    text=text.replace('Гроза с небольшим градом','Гроза с градом').replace('Гроза с сильным градом','Гроза с градом')
    text=re.sub(r'^(?:Сильн(?:ый|ая)|Небольш(?:ой|ая)|Умеренн(?:ый|ая)|Слаб(?:ый|ая))\s+', '',text,flags=re.I)
    return text[:1].upper()+text[1:]


def weather_card(p,weather,box,landscape):
    original_colors=p.c
    p.c=dict(THEMES['night'],cloud='#DBEBFA',primary='#82DEFF')
    x,y,x2,y2=box;cw,ch=x2-x,y2-y
    # A restrained sky gradient gives the live weather its own visual identity.
    card=Image.new('RGB',(round(cw*2),round(ch*2)));d=ImageDraw.Draw(card)
    day=weather.get('is_day',True);top=(29,82,117) if day else (32,48,89);bottom=(20,46,72) if day else (19,29,57)
    for row in range(card.height):
        t=row/max(1,card.height-1)
        d.line((0,row,card.width,row),fill=tuple(round(a*(1-t)+b*t) for a,b in zip(top,bottom)))
    mask=Image.new('L',card.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,card.width-1,card.height-1),radius=24,fill=255)
    p.im.paste(card,(round(x*2),round(y*2)),mask)
    temp=value(weather.get('temperature'),'°');city=weather.get('city') or 'Погода'
    if landscape:
        p.text(x+10,y+3,temp,32,p.c['text'],True)
        p.weather_icon(x+75,y+1,weather.get('weather_code'),36,day)
        p.fit(x+120,y+10,city,155,18,p.c['text'],True)
        p.text(x2-10,y+11,'Кэш погоды' if weather.get('stale') else f"Ветер {value(weather.get('wind_kmh'))} км/ч",16,p.c['text'],anchor='rt')
    else:
        p.fit(x+12,y+10,city,190,19,p.c['text'],True)
        p.text(x+12,y+35,temp,44,p.c['text'],True)
        p.weather_icon(x2-76,y+8,weather.get('weather_code'),66,day)
        description=compact_weather_description(weather.get('description'))
        lines=[];line=''
        for word in description.split():
            candidate=(line+' '+word).strip()
            if line and p.measure(candidate,16)>110:lines.append(line);line=word
            else:line=candidate
        if line:lines.append(line)
        # Original morning composition, only the leading is reduced: 18 vs 22 px.
        for i,line in enumerate(lines[:2]):
            if i==1 and len(lines)>2:line+='…'
            p.fit(x+108,y+36+i*18,line,110,16,p.c['text'])
        p.text(x+12,y+77,f"По ощущениям {value(weather.get('feels_like'),'°')}",16,p.c['muted'])
        p.text(x2-12,y+77,'Кэш' if weather.get('stale') else f"{value(weather.get('wind_kmh'))} км/ч",16,p.c['muted'],anchor='rt')

    p.c=original_colors

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
    for index, weekday in enumerate(_WEEKDAYS_RU):
        p.text(left + width * (index + .5), grid_top + 2, weekday,
               13, p.c['muted'], True, 'mt')
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
            color = '#FFFFFF'
        else:
            color = p.c['text'] if day.month == now.month else p.c['muted']
        p.text(cx, cy, day.day, 15, color, day == now.date(), 'mm')


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


def _media_progress(media):
    position = number(media.get('position_seconds'))
    duration = number(media.get('duration_seconds'))
    if position is None or duration is None or duration <= 0:
        return None
    sampled_at = number(media.get('position_sampled_at'))
    if media.get('is_playing') is True and sampled_at is not None:
        position += max(0.0, min(15.0, time.monotonic() - sampled_at))
    position = min(max(0.0, position), duration)
    return position / duration * 100, _duration_label(position), _duration_label(duration)


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
        for i,hh in enumerate((28,54,82,62,38)):
            xx=center_x-48+i*20
            p.rect((xx,center_y-hh/2,xx+9,center_y+hh/2),
                   p.c['primary'] if i%2 else p.c['secondary'],5)


def media_page(p,media,landscape):
    w=p.w
    has_title=bool(media.get('title'))
    status=('ИГРАЕТ' if media.get('is_playing') is True else
            'ПАУЗА' if media.get('is_playing') is False else 'ТРЕК') if has_title else 'НЕТ ТРЕКА'
    status_color=p.c['memory'] if media.get('is_playing') is True else p.c['primary']
    pill_fill=('#204A45' if p.theme=='night' else '#E5F5EE') if media.get('is_playing') is True else (
        '#323B58' if p.theme=='night' else '#E8EDF8')
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


def overview_content_card(p,content,box,landscape):
    """Render the optional live game/music tile without disturbing overview geometry."""
    if not isinstance(content,dict) or content.get('kind') not in ('gaming','media'):
        return False
    x,y,x2,y2=box
    p.rect(box, '#203B5A' if p.theme=='night' else '#D9EAFB', radius=16)
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

def render(metrics,weather,history,page='overview',rotation=90,now=None,alert='',media=None,theme='night',brand_name='Мой компьютер',overview_content=None):
    now=now or datetime.now();metrics=metrics if isinstance(metrics,dict) else {};weather=weather if isinstance(weather,dict) else {}
    history=history if isinstance(history,dict) else {};media=media if isinstance(media,dict) else {};landscape=rotation in (90,270)
    p=Painter(480 if landscape else 320,320 if landscape else 480,theme);header(p,page,now,brand_name)
    if page in ('clock','media') or (page=='gaming' and number(metrics.get('fps')) is not None):
        p.rect((8,39,p.w-8,p.h-8),radius=20)
    if page=='overview':overview(p,metrics,weather,landscape,overview_content)
    elif page in ('graphs','thermal'):graph_page(p,metrics,history,page=='thermal',landscape)
    elif page=='weather':weather_page(p,weather,landscape)
    elif page=='clock':clock_page(p,metrics,now,landscape)
    elif page=='media':media_page(p,media,landscape)
    elif page=='gaming':gaming_page(p,metrics,landscape)
    else:overview(p,metrics,weather,landscape)
    if alert:
        p.rect((4,p.h-54,p.w-4,p.h-4),'#512330',10,RED)
        short=str(alert).replace(' — высокая температура','').replace('Процессор:','CPU').replace('Видеокарта:','GPU')
        p.wrap(14,p.h-47,short,p.w-28,18,2,'#FFF5F7',True)
    return p.finish()
