"""Small antialiased, functional Tk controls for the desktop monitor UI."""

from __future__ import annotations

import os
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _blend(first: str, second: str, amount: float) -> str:
    a, b = _rgb(first), _rgb(second)
    return "#" + "".join(f"{round(x * (1 - amount) + y * amount):02x}"
                       for x, y in zip(a, b))


def _font(size: int, bold: bool = False):
    root = os.environ.get("WINDIR", "C:/Windows")
    name = "seguisb.ttf" if bold else "segoeui.ttf"
    try:
        return ImageFont.truetype(os.path.join(root, "Fonts", name), size * 2)
    except OSError:
        return ImageFont.load_default(size=size * 2)


class PillButton(tk.Canvas):
    """A real button with rounded antialiased paint and keyboard activation."""

    def __init__(self, master, *, text: str, command, palette: dict,
                 variant: str = "glass", width: int = 108, height: int = 36,
                 corner: str | None = None):
        self.label = text
        self.command = command
        self.palette = palette
        self.variant = variant
        self.corner = corner or palette["bg"]
        self._hover = False
        self._focused = False
        self._disabled = False
        self._photo = None
        super().__init__(master, width=width, height=height, bg=self.corner,
                         highlightthickness=0, bd=0, relief="flat",
                         cursor="hand2", takefocus=True)
        self._image = self.create_image(0, 0, anchor="nw")
        self.bind("<Configure>", lambda _: self._paint())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)
        self.bind("<Return>", self._click)
        self.bind("<space>", self._click)
        self.bind("<FocusIn>", lambda _: self._focus(True))
        self.bind("<FocusOut>", lambda _: self._focus(False))
        self._paint()

    def _focus(self, focused: bool):
        self._focused = focused
        self._paint()

    def _enter(self, _event):
        self._hover = True
        self._paint()

    def _leave(self, _event):
        self._hover = False
        self._paint()

    def _click(self, _event=None):
        if not self._disabled and callable(self.command):
            self.command()

    def invoke(self):
        self._click()

    def set_variant(self, variant: str):
        self.variant = variant
        self._paint()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "text" in kw:
            self.label = str(kw.pop("text"))
        if "state" in kw:
            self._disabled = kw.pop("state") == "disabled"
        if kw:
            super().configure(**kw)
        self._paint()

    config = configure

    def set_palette(self, palette: dict, *, corner: str | None = None):
        self.palette = palette
        self.corner = corner or palette["bg"]
        super().configure(bg=self.corner)
        self._paint()

    def _paint(self):
        if not hasattr(self, "_image"):
            return
        width = max(4, self.winfo_width())
        height = max(4, self.winfo_height())
        if width <= 4:
            width = int(self.cget("width"))
        if height <= 4:
            height = int(self.cget("height"))
        image = Image.new("RGB", (width * 2, height * 2), self.corner)
        draw = ImageDraw.Draw(image)
        palette = self.palette
        if self.variant == "accent":
            fill = palette["accent_fill"]
            ink = "#FFFFFF"
        elif self.variant == "quiet":
            fill = palette["side"]
            ink = palette["text"]
        else:
            fill = palette["panel_2"]
            ink = palette["text"]
        if self._hover and not self._disabled:
            fill = _blend(fill, palette["accent"], .15 if self.variant == "accent" else .10)
        if self._disabled:
            ink = palette["muted"]
        inset = 2
        draw.rounded_rectangle((inset, inset, width * 2 - inset - 1,
                                height * 2 - inset - 1), radius=(height - 2),
                               fill=fill)
        if self.variant == "glass":
            draw.rounded_rectangle((inset, inset, width * 2 - inset - 1,
                                    height * 2 - inset - 1), radius=(height - 2),
                                   outline=palette["border"], width=2)
        if self._focused:
            draw.rounded_rectangle((inset+2, inset+2, width*2-inset-3,
                                    height*2-inset-3), radius=height-4,
                                   outline=palette["accent"], width=3)
        font = _font(14, True)
        shown = self.label
        max_width = width * 2 - 26
        while shown and draw.textlength(shown, font=font) > max_width:
            shown = shown[:-1]
        if shown != self.label:
            shown = shown[:-1] + "…" if shown else "…"
        draw.text((width, height), shown, font=font, fill=ink, anchor="mm")
        self._photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS))
        self.itemconfigure(self._image, image=self._photo)


class SidebarButton(PillButton):
    def __init__(self, master, *, text: str, command, palette: dict,
                 icon: str, icon_color: str, width: int = 172):
        self.icon = icon
        self.icon_color = icon_color
        self.selected = False
        super().__init__(master, text=text, command=command, palette=palette,
                         variant="quiet", width=width, height=42, corner=palette["side"])

    def set_selected(self, selected: bool):
        self.selected = selected
        self._paint()

    def set_palette(self, palette: dict, *, corner: str | None = None):
        super().set_palette(palette, corner=palette["side"])

    def _paint(self):
        if not hasattr(self, "_image"):
            return
        width = max(172, self.winfo_width())
        height = max(42, self.winfo_height())
        image = Image.new("RGB", (width * 2, height * 2), self.palette["side"])
        draw = ImageDraw.Draw(image)
        palette = self.palette
        if self.selected:
            fill = palette["accent_fill"]
            text = "#FFFFFF"
        elif self._hover:
            fill = palette["selection"]
            text = palette["text"]
        else:
            fill = palette["side"]
            text = palette["text"]
        draw.rounded_rectangle((2, 2, width * 2 - 2, height * 2 - 2),
                               radius=23, fill=fill)
        if self._focused:
            draw.rounded_rectangle((3, 3, width * 2 - 3, height * 2 - 3),
                                   radius=23, outline=palette["accent"], width=3)
        tile = "#FFFFFF" if self.selected else self.icon_color
        if self.selected:
            tile = _blend(palette["accent_fill"], "#FFFFFF", .27)
        draw.rounded_rectangle((17, 14, 69, 66), radius=13, fill=tile)
        glyph = "#FFFFFF" if not self.selected else "#FFFFFF"
        self._draw_icon(draw, glyph)
        draw.text((83, height), self.label, font=_font(15, self.selected),
                  fill=text, anchor="lm")
        self._photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS))
        self.itemconfigure(self._image, image=self._photo)

    def _draw_icon(self, draw: ImageDraw.ImageDraw, color: str):
        # Coordinates are supersampled and sit inside the 26 px color tile.
        icon = self.icon
        if icon in ("overview", "graphs"):
            heights = (13, 22, 31) if icon == "graphs" else (20, 30, 24)
            for x, tall in zip((26, 39, 52), heights):
                draw.rounded_rectangle((x, 55 - tall, x + 7, 55), radius=3, fill=color)
        elif icon == "thermal":
            draw.rounded_rectangle((39, 25, 47, 52), radius=4, fill=color)
            draw.ellipse((34, 45, 52, 62), fill=color)
        elif icon == "weather":
            draw.ellipse((31, 27, 48, 44), fill=color)
            draw.ellipse((24, 37, 55, 56), fill=color)
        elif icon == "clock":
            draw.ellipse((26, 21, 59, 54), outline=color, width=4)
            draw.line((43, 37, 43, 27), fill=color, width=4)
            draw.line((43, 37, 51, 42), fill=color, width=4)
        elif icon == "media":
            draw.line((40, 27, 40, 51), fill=color, width=5)
            draw.line((40, 27, 54, 23), fill=color, width=5)
            draw.ellipse((30, 46, 43, 57), fill=color)
        elif icon == "gaming":
            draw.rounded_rectangle((26, 32, 58, 54), radius=8, outline=color, width=4)
            draw.line((31, 43, 41, 43), fill=color, width=3)
            draw.line((36, 38, 36, 48), fill=color, width=3)
            draw.ellipse((48, 39, 52, 43), fill=color)


class RoundedPanel(tk.Canvas):
    """A rounded surface containing a normal Frame for ordinary Tk layouts."""

    def __init__(self, master, *, palette: dict, corner: str | None = None,
                 width: int = 120, height: int = 100, radius: int = 20):
        self.palette = palette
        self.corner = corner or palette["bg"]
        self.radius = radius
        self._photo = None
        super().__init__(master, width=width, height=height, bg=self.corner,
                         highlightthickness=0, bd=0)
        self._background = self.create_image(0, 0, anchor="nw")
        self.inner = tk.Frame(self, bg=palette["panel"])
        self._window = self.create_window(8, 8, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._resize)
        self._resize(None)

    def set_palette(self, palette: dict, *, corner: str | None = None):
        self.palette = palette
        self.corner = corner or palette["bg"]
        super().configure(bg=self.corner)
        self.inner.configure(bg=palette["panel"])
        self._paint()

    def _resize(self, _event):
        width, height = max(4, self.winfo_width()), max(4, self.winfo_height())
        self.itemconfigure(self._window, width=max(1, width - 16), height=max(1, height - 16))
        self._paint()

    def _paint(self):
        width = max(4, self.winfo_width())
        height = max(4, self.winfo_height())
        if width <= 4:
            width = int(self.cget("width"))
        if height <= 4:
            height = int(self.cget("height"))
        image = Image.new("RGB", (width * 2, height * 2), self.corner)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width * 2 - 1, height * 2 - 1),
                               radius=self.radius * 2, fill=self.palette["panel"])
        self._photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS))
        self.itemconfigure(self._background, image=self._photo)
        self.tag_lower(self._background)


class SlimScrollbar(tk.Canvas):
    """Small rounded scrollbar with native Tk canvas scrolling semantics."""

    def __init__(self, master, *, command, palette: dict,
                 orient: str = "vertical", corner: str | None = None):
        self.command = command
        self.palette = palette
        self.orient = orient
        self.corner = corner or palette["panel"]
        self._first = 0.0
        self._last = 1.0
        self._grab_offset = 0.0
        super().__init__(master, width=12 if orient == "vertical" else 14,
                         height=14 if orient == "vertical" else 12,
                         bg=self.corner, bd=0, highlightthickness=0,
                         relief="flat", cursor="hand2")
        self.bind("<Configure>", lambda _: self._paint())
        self.bind("<Button-1>", self._click)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<MouseWheel>", self._wheel)

    def set_palette(self, palette: dict, *, corner: str | None = None):
        self.palette = palette
        self.corner = corner or palette["panel"]
        self.configure(bg=self.corner)
        self._paint()

    def set(self, first: str | float, last: str | float):
        self._first = max(0.0, min(1.0, float(first)))
        self._last = max(self._first, min(1.0, float(last)))
        self._paint()

    def _geometry(self):
        length = self.winfo_height() if self.orient == "vertical" else self.winfo_width()
        length = max(length, 16)
        track_start, track_end = 4, length - 4
        track_length = max(1, track_end - track_start)
        thumb_length = min(track_length, max(28, track_length * (self._last - self._first)))
        travel = max(0, track_length - thumb_length)
        first = self._first / max(.001, 1 - (self._last - self._first))
        start = track_start + travel * first
        return track_start, track_end, start, start + thumb_length, travel

    def _paint(self):
        self.delete("all")
        if self._last - self._first >= .999:
            return
        track_start, track_end, start, end, _ = self._geometry()
        track = _blend(self.palette["border"], self.corner, .42)
        thumb = _blend(self.palette["muted"], self.palette["accent"], .24)
        if self.orient == "vertical":
            middle = self.winfo_width() / 2
            self.create_line(middle, track_start, middle, track_end,
                             fill=track, width=3, capstyle="round")
            self.create_line(middle, start, middle, end,
                             fill=thumb, width=7, capstyle="round")
        else:
            middle = self.winfo_height() / 2
            self.create_line(track_start, middle, track_end, middle,
                             fill=track, width=3, capstyle="round")
            self.create_line(start, middle, end, middle,
                             fill=thumb, width=7, capstyle="round")

    def _position(self, event):
        return event.y if self.orient == "vertical" else event.x

    def _click(self, event):
        _, _, start, end, _ = self._geometry()
        position = self._position(event)
        self._grab_offset = position - start if start <= position <= end else (end - start) / 2
        self._drag(event)

    def _drag(self, event):
        track_start, _, _, _, travel = self._geometry()
        if travel <= 0:
            return
        position = self._position(event) - self._grab_offset
        fraction = max(0.0, min(1.0, (position - track_start) / travel))
        self.command("moveto", fraction * max(0.0, 1 - (self._last - self._first)))

    def _wheel(self, event):
        self.command("scroll", -int(event.delta / 120) or -1, "units")
        return "break"


class SmoothScale(tk.Canvas):
    """Compact percentage slider with a filled track and readable value."""

    def __init__(self, master, *, variable: tk.IntVar, palette: dict,
                 minimum: int = 1, maximum: int = 100):
        self.variable = variable
        self.palette = palette
        self.minimum = minimum
        self.maximum = maximum
        super().__init__(master, width=220, height=40, bg=palette["panel"], bd=0,
                         highlightthickness=0, relief="flat", takefocus=True,
                         cursor="hand2")
        self.bind("<Configure>", lambda _: self._paint())
        self.bind("<Button-1>", self._set_from_pointer)
        self.bind("<B1-Motion>", self._set_from_pointer)
        self.bind("<Left>", lambda _: self.variable.set(max(self.minimum, self.variable.get() - 1)))
        self.bind("<Right>", lambda _: self.variable.set(min(self.maximum, self.variable.get() + 1)))
        self.bind("<FocusIn>", lambda _: self._paint())
        self.bind("<FocusOut>", lambda _: self._paint())
        self.variable.trace_add("write", lambda *_: self._paint() if self.winfo_exists() else None)
        self._paint()

    def set_palette(self, palette: dict, *, corner: str | None = None):
        self.palette = palette
        self.configure(bg=palette["panel"])
        self._paint()

    def _set_from_pointer(self, event):
        width = max(120, self.winfo_width())
        ratio = max(0.0, min(1.0, (event.x - 16) / max(1, width - 107)))
        self.variable.set(round(self.minimum + ratio * (self.maximum - self.minimum)))

    def _paint(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        width = max(120, self.winfo_width())
        start, end, middle = 16, width - 91, 20
        ratio = max(0.0, min(1.0, (self.variable.get() - self.minimum) /
                             max(1, self.maximum - self.minimum)))
        point = start + ratio * (end - start)
        self.create_line(start, middle, end, middle, fill=self.palette["border"],
                         width=8, capstyle="round")
        self.create_line(start, middle, point, middle, fill=self.palette["accent_fill"],
                         width=8, capstyle="round")
        self.create_oval(point-9, middle-9, point+9, middle+9,
                         fill=self.palette["panel"], outline=self.palette["accent_fill"], width=3)
        self.create_text(width-13, middle, text=f"{self.variable.get()}%",
                         fill=self.palette["text"], anchor="e",
                         font=("Segoe UI", 11, "bold"))
        if self.focus_get() is self:
            self.create_rectangle(2, 3, width-2, 36,
                                  outline=self.palette["accent"], width=2)


class ToggleSwitch(tk.Canvas):
    """Accessible compact on/off control for settings rows."""

    def __init__(self, master, *, variable: tk.BooleanVar, palette: dict):
        self.variable = variable
        self.palette = palette
        self._photo = None
        super().__init__(master, width=46, height=28, bg=palette["panel"],
                         bd=0, highlightthickness=0, takefocus=True,
                         cursor="hand2")
        self._image = self.create_image(0, 0, anchor="nw")
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", lambda _: self._paint())
        self.bind("<FocusOut>", lambda _: self._paint())
        self.variable.trace_add("write", lambda *_: self._paint() if self.winfo_exists() else None)
        self._paint()

    def _toggle(self, _event=None):
        self.variable.set(not self.variable.get())

    def set_palette(self, palette: dict, *, corner: str | None = None):
        self.palette = palette
        self.configure(bg=palette["panel"])
        self._paint()

    def _paint(self):
        if not hasattr(self, "_image"):
            return
        image = Image.new("RGB", (92, 56), self.palette["panel"])
        draw = ImageDraw.Draw(image)
        active = self.variable.get()
        draw.rounded_rectangle((2, 3, 90, 53), radius=26,
                               fill=self.palette["accent_fill"] if active else self.palette["border"])
        center = 65 if active else 27
        draw.ellipse((center-20, 7, center+20, 47), fill="#FFFFFF")
        if self.focus_get() is self:
            draw.rounded_rectangle((1, 2, 91, 54), radius=27,
                                   outline=self.palette["accent"], width=3)
        self._photo = ImageTk.PhotoImage(image.resize((46, 28), Image.Resampling.LANCZOS))
        self.itemconfigure(self._image, image=self._photo)


class SelectField(tk.Canvas):
    """Rounded single-choice field with a native keyboard-accessible popup."""

    def __init__(self, master, *, variable: tk.StringVar, values: list[str],
                 palette: dict, width: int = 180):
        self.variable = variable
        self.values = tuple(values)
        self.palette = palette
        self._photo = None
        self._menu = None
        super().__init__(master, width=width, height=41, bg=palette["panel"],
                         bd=0, highlightthickness=0, takefocus=True,
                         cursor="hand2")
        self._image = self.create_image(0, 0, anchor="nw")
        self.bind("<Configure>", lambda _: self._paint())
        self.bind("<Button-1>", self._open)
        self.bind("<space>", self._open)
        self.bind("<Return>", self._open)
        self.bind("<Down>", self._advance)
        self.bind("<Up>", lambda _: self._advance(None, -1))
        self.bind("<FocusIn>", lambda _: self._paint())
        self.bind("<FocusOut>", lambda _: self._paint())
        self.variable.trace_add("write", lambda *_: self._paint() if self.winfo_exists() else None)
        self._paint()

    def set(self, value: str):
        if value not in self.values:
            raise ValueError("Неизвестный вариант")
        self.variable.set(value)

    def get(self):
        return self.variable.get()

    def _advance(self, _event=None, direction: int = 1):
        if self.values:
            try:
                index = self.values.index(self.variable.get())
            except ValueError:
                index = -1
            self.variable.set(self.values[(index + direction) % len(self.values)])

    def _open(self, _event=None):
        if self._menu is not None:
            self._menu.destroy()
        menu = tk.Menu(self, tearoff=False, bg=self.palette["panel_2"],
                       fg=self.palette["text"], activebackground=self.palette["selection"],
                       activeforeground=self.palette["text"], bd=1,
                       relief="flat", font=("Segoe UI", 11))
        for item in self.values:
            menu.add_command(label=item, command=lambda chosen=item: self.variable.set(chosen))
        self._menu = menu
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty()+self.winfo_height())
        finally:
            menu.grab_release()

    def set_palette(self, palette: dict, *, corner: str | None = None):
        self.palette = palette
        self.configure(bg=palette["panel"])
        self._paint()

    def _paint(self):
        if not hasattr(self, "_image"):
            return
        width = max(68, self.winfo_width())
        if width <= 68:
            width = int(self.cget("width"))
        image = Image.new("RGB", (width*2, 82), self.palette["panel"])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, width*2-3, 79), radius=19,
                               fill=self.palette["panel_2"],
                               outline=self.palette["accent"] if self.focus_get() is self
                               else self.palette["border"], width=2)
        shown = self.variable.get()
        face = _font(14)
        while shown and draw.textlength(shown, font=face) > width*2-68:
            shown = shown[:-1]
        if shown != self.variable.get():
            shown = shown[:-1] + "…" if shown else "…"
        draw.text((25, 41), shown, font=face, fill=self.palette["text"], anchor="lm")
        arrow_x = width*2-29
        draw.line((arrow_x-8, 36, arrow_x, 44, arrow_x+8, 36),
                  fill=self.palette["muted"], width=3, joint="curve")
        self._photo = ImageTk.PhotoImage(image.resize((width, 41), Image.Resampling.LANCZOS))
        self.itemconfigure(self._image, image=self._photo)


class StepperField(tk.Canvas):
    """Rounded number field with large minus/plus hit targets."""

    def __init__(self, master, *, variable: tk.StringVar, minimum: int,
                 maximum: int, palette: dict, width: int = 118):
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.palette = palette
        self._photo = None
        super().__init__(master, width=width, height=40, bg=palette["panel"],
                         bd=0, highlightthickness=0, takefocus=True,
                         cursor="hand2")
        self._image = self.create_image(0, 0, anchor="nw")
        self.bind("<Button-1>", self._click)
        self.bind("<Left>", lambda _: self._change(-1))
        self.bind("<Right>", lambda _: self._change(1))
        self.bind("<FocusIn>", lambda _: self._paint())
        self.bind("<FocusOut>", lambda _: self._paint())
        self.variable.trace_add("write", lambda *_: self._paint() if self.winfo_exists() else None)
        self._paint()

    def _change(self, amount):
        try:
            current = int(self.variable.get())
        except ValueError:
            current = self.minimum
        self.variable.set(str(max(self.minimum, min(self.maximum, current + amount))))

    def _click(self, event):
        self._change(-1 if event.x < self.winfo_width()/2 else 1)

    def set_palette(self, palette: dict, *, corner: str | None = None):
        self.palette = palette
        self.configure(bg=palette["panel"])
        self._paint()

    def _paint(self):
        if not hasattr(self, "_image"):
            return
        width = max(80, self.winfo_width())
        if width <= 80:
            width = int(self.cget("width"))
        image = Image.new("RGB", (width*2, 80), self.palette["panel"])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, width*2-3, 77), radius=19,
                               fill=self.palette["panel_2"],
                               outline=self.palette["accent"] if self.focus_get() is self
                               else self.palette["border"], width=2)
        draw.text((27, 40), "−", font=_font(20), fill=self.palette["muted"], anchor="mm")
        draw.text((width, 40), self.variable.get(), font=_font(14, True),
                  fill=self.palette["text"], anchor="mm")
        draw.text((width*2-27, 40), "+", font=_font(20),
                  fill=self.palette["muted"], anchor="mm")
        self._photo = ImageTk.PhotoImage(image.resize((width, 40), Image.Resampling.LANCZOS))
        self.itemconfigure(self._image, image=self._photo)


class TextField(tk.Canvas):
    """Rounded entry surface with a native text caret and selection."""

    def __init__(self, master, *, variable: tk.StringVar, palette: dict,
                 font_size: int = 12):
        self.variable = variable
        self.palette = palette
        self._photo = None
        super().__init__(master, height=43, bg=palette["panel"], bd=0,
                         highlightthickness=0)
        self._image = self.create_image(0, 0, anchor="nw")
        self.entry = tk.Entry(self, textvariable=variable,
                              font=("Segoe UI", font_size), bd=0,
                              highlightthickness=0, relief="flat",
                              bg=palette["panel_2"], fg=palette["text"],
                              insertbackground=palette["accent"])
        self._window = self.create_window(13, 8, window=self.entry, anchor="nw")
        self.bind("<Configure>", self._resize)
        self.entry.bind("<FocusIn>", lambda _: self._paint())
        self.entry.bind("<FocusOut>", lambda _: self._paint())
        self._paint()

    def _resize(self, _event):
        self.itemconfigure(self._window, width=max(20, self.winfo_width()-26), height=27)
        self._paint()

    def get(self):
        return self.entry.get()

    def delete(self, *args):
        return self.entry.delete(*args)

    def insert(self, *args):
        return self.entry.insert(*args)

    def set_palette(self, palette: dict, *, corner: str | None = None):
        self.palette = palette
        self.configure(bg=palette["panel"])
        self.entry.configure(bg=palette["panel_2"], fg=palette["text"],
                             insertbackground=palette["accent"])
        self._paint()

    def _paint(self):
        if not hasattr(self, "_image"):
            return
        width = max(40, self.winfo_width())
        if width <= 40:
            width = max(80, int(self.cget("width")))
        image = Image.new("RGB", (width*2, 86), self.palette["panel"])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, width*2-3, 83), radius=19,
                               fill=self.palette["panel_2"],
                               outline=self.palette["accent"] if self.entry.focus_get() is self.entry
                               else self.palette["border"], width=2)
        self._photo = ImageTk.PhotoImage(image.resize((width, 43), Image.Resampling.LANCZOS))
        self.itemconfigure(self._image, image=self._photo)
