"""Manual GUI smoke: isolated settings, no USB and no elevated helper."""
import ctypes,json,os,shutil,time
from pathlib import Path
from types import SimpleNamespace
import main
from PIL import ImageGrab
ctypes.windll.shcore.SetProcessDpiAwareness(1)
live=main.DATA
main.DATA=main.ROOT/'test-data'/'themes-smoke'
main.DATA.mkdir(parents=True,exist_ok=True)
config=main.load_settings(live/'settings.json');config.update(theme='night',brand_name='Мой компьютер')
main.save_json(main.DATA/'settings.json',config)
for file in ('weather.json','hardware.json'):
    if (live/file).exists():shutil.copy2(live/file,main.DATA/file)
root=main.tk.Tk();app=main.App(root,SimpleNamespace(preview=True))
def descendants(w):
    yield w
    for child in w.winfo_children():yield from descendants(child)
def button_text(widget):
    if isinstance(widget,main.PillButton):return widget.label
    if isinstance(widget,main.ttk.Button):return widget.cget("text")
    return ""
def save_appearance(theme,name):
    app.settings_window();win=app.settings_dialog
    widgets=list(descendants(win))
    combo=next(w for w in widgets if isinstance(w,main.SelectField) and tuple(w.values)==tuple(main.THEMES.values()))
    combo.set(theme)
    entry=next(w for w in widgets if isinstance(w,main.TextField) and w.variable.get()==app.settings['brand_name'])
    entry.delete(0,'end');entry.insert(0,name)
    next(w for w in widgets if button_text(w)=='Сохранить изменения').invoke()
def night():
    ImageGrab.grab(window=root.winfo_id()).save(main.ROOT/'desktop-theme-night.png')
    save_appearance('Дневная','Моя станция')
    root.after(600,day)
def day():
    assert app.settings['theme']=='day' and app.settings['brand_name']=='Моя станция'
    assert root.cget('bg')==main.UI_PALETTES['day']['bg']
    assert all(app.settings[k]==config[k] for k in ('brightness','night_brightness','night_mode','night_start','night_end'))
    ImageGrab.grab(window=root.winfo_id()).save(main.ROOT/'desktop-theme-day.png')
    app.settings_window();root.after(400,settings)
def settings():
    win=app.settings_dialog
    ImageGrab.grab(window=win.winfo_id()).save(main.ROOT/'settings-theme-day.png')
    for tab,filename in (('Автоматика','settings-auto-day.png'),
                         ('Погода','settings-weather-day.png'),
                         ('Датчики и пороги','settings-sensors-day.png')):
        next(w for w in descendants(win) if button_text(w)==tab).invoke()
        win.update()
        time.sleep(0.25)
        ImageGrab.grab(window=win.winfo_id()).save(main.ROOT/filename)
    app.settings_dialog.destroy()
    save_appearance('Ночная','Моя замечательная рабочая станция')
    root.geometry('920x620');root.after(500,done)
def done():
    assert app.settings['theme']=='night' and root.cget('bg')==main.UI_PALETTES['night']['bg']
    saved=main.load_settings(main.DATA/'settings.json')
    assert saved['brand_name']==app.settings['brand_name']
    controls=[w for w in descendants(root) if button_text(w) in ('В трей','Завершить')]
    assert len(controls)==2
    for w in controls:
        assert w.winfo_rooty()-root.winfo_rooty()+w.winfo_height()<=root.winfo_height()
    ImageGrab.grab(window=root.winfo_id()).save(main.ROOT/'desktop-theme-long-name.png')
    print('PASS: night/day/night, custom name saved, brightness preserved, minimum window controls visible',flush=True)
    app.close()
root.after(1600,night);root.after(10000,app.close);root.mainloop()
