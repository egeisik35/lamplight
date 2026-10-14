#!/usr/bin/env python3
"""Lamplight settings app (GTK 4 / libadwaita).

Edits the shared io.github.egeisik35.Lamplight settings; `lamplight service`
applies them to the hardware, so changes keep working after the app closes.
"""
import math
import sys
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
import cairo  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

import lamplight  # noqa: E402

APP_ID = 'io.github.egeisik35.Lamplight'
REPO = 'https://github.com/egeisik35/lamplight'

MODES = [
    ('solid', 'Solid color'), ('accent', 'Match accent color'), ('breathe', 'Breathing'),
    ('cycle', 'Color cycle'), ('rainbow', 'Rainbow'), ('wave', 'Wave'), ('wheel', 'Wheel'),
    ('gradient', 'Gradient'), ('firmware', 'Built-in effect'),
]
USES_COLOR = {'solid', 'breathe', 'wave', 'gradient'}
USES_COLOR2 = {'breathe', 'wave', 'gradient'}
SPATIAL = [('right', 'Right'), ('left', 'Left'), ('down', 'Down'), ('up', 'Up'),
           ('out', 'Outward'), ('in', 'Inward')]
DIRECTIONS = {
    'cycle': [('right', 'Forward'), ('left', 'Reverse')],
    'rainbow': SPATIAL, 'wave': SPATIAL, 'gradient': SPATIAL,
    'wheel': [('cw', 'Clockwise'), ('ccw', 'Counterclockwise')],
}
# Speed 1-10 like Windows, mapped logarithmically onto the effect period.
MIN_PERIOD, MAX_PERIOD = 0.5, 20.0

# Key counts per row of the preview keyboard (single-zone and fallback layout).
ROWS = [14, 14, 14, 13, 12, 8]


def speed_to_period(level):
    return MAX_PERIOD * (MIN_PERIOD / MAX_PERIOD) ** ((level - 1) / 9)


def period_to_speed(period):
    return 1 + 9 * math.log(period / MAX_PERIOD) / math.log(MIN_PERIOD / MAX_PERIOD)


def to_rgba(hex_color):
    rgba = Gdk.RGBA()
    rgba.parse(f'#{hex_color}')
    return rgba


def to_hex(rgba):
    return ''.join(f'{round(c * 255):02x}' for c in (rgba.red, rgba.green, rgba.blue))


def keyboard_keys():
    """Preview key rectangles as (x, y, w, h) in 0..1 units."""
    keys = []
    for r, n in enumerate(ROWS):
        w = 1 / n
        for i in range(n):
            keys.append((i * w, r / len(ROWS), w, 1 / len(ROWS)))
    return keys


class Preview(Gtk.Widget):
    """Live preview of the current effect, as the hardware would show it.

    The keyboard is drawn once into textures; each frame only tints them on
    the GPU. Redraws run at 30 fps, only while an effect animates.
    Wall-clock time keeps the preview in step with the service.
    """

    FPS = 30
    PAD, GAP, RADIUS = 12, 4, 4

    def __init__(self, settings, interface, device):
        super().__init__(hexpand=True, height_request=170)
        self.settings, self.interface = settings, interface
        self.keys = keyboard_keys()
        # Per-key/zone device: draw real lamp spots. Single zone: one color.
        self.lamps = device.positions if device and device.count > 1 else None
        self.textures, self.texture_key, self.timer = None, None, 0
        self.update_state()
        settings.connect('changed', lambda *_: self.update_state())
        if interface:
            interface.connect('changed::accent-color', lambda *_: self.update_state())

    def update_state(self):
        accent = self.interface.get_string('accent-color') if self.interface else None
        try:
            self.state = lamplight.settings_state(self.settings, accent)
        except ValueError:
            self.state = lamplight.normalize({'mode': 'off'})
        self.queue_draw()
        animated = self.state['mode'] in lamplight.ANIMATED
        if animated and not self.timer:
            self.timer = GLib.timeout_add(1000 // self.FPS, self.on_timer)
        elif not animated and self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def on_timer(self):
        if self.get_mapped():  # skip work while the window is hidden
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def build_textures(self, width, height, scale):
        """Dark keys, white key mask and rainbow keys, at device pixel size."""
        def paint(fill):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
            cr = cairo.Context(surface)
            cr.scale(scale, scale)
            kw, kh = width - 2 * self.PAD, height - 2 * self.PAD
            r = self.RADIUS
            for x, y, w, h in self.keys:
                x, y = self.PAD + x * kw, self.PAD + y * kh
                w, h = w * kw - self.GAP, h * kh - self.GAP
                cr.new_sub_path()
                cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
                cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
                cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
                cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
                cr.close_path()
            fill(cr)
            cr.fill()
            surface.flush()
            return Gdk.MemoryTexture.new(
                width * scale, height * scale, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                GLib.Bytes.new(bytes(surface.get_data())), surface.get_stride())

        def rainbow(cr):
            grad = cairo.LinearGradient(0, 0, width, 0)
            for i in range(7):
                grad.add_color_stop_rgb(i / 6, *lamplight.hue(i / 6))
            cr.set_source(grad)

        return (paint(lambda cr: cr.set_source_rgb(0.16, 0.16, 0.18)),
                paint(lambda cr: cr.set_source_rgb(1, 1, 1)),
                paint(rainbow))

    def do_snapshot(self, snap):
        width, height, scale = self.get_width(), self.get_height(), self.get_scale_factor()
        if width <= 2 * self.PAD or height <= 2 * self.PAD:
            return
        if self.texture_key != (width, height, scale):
            self.textures, self.texture_key = self.build_textures(width, height, scale), (width, height, scale)
        dark, mask, rainbow = self.textures
        bounds = Graphene.Rect().init(0, 0, width, height)
        snap.append_texture(dark, bounds)

        state = self.state
        if state['mode'] == 'firmware':  # the device runs its own effect
            snap.append_texture(rainbow, bounds)
            return
        b = state['brightness']
        if self.lamps is None:
            r, g, bl = lamplight.render(state, time.time(), [(0.5, 0.5)])[0]
            if not (r or g or bl):
                return
            tint = Graphene.Matrix().init_from_float(
                [r, 0, 0, 0, 0, g, 0, 0, 0, 0, bl, 0, 0, 0, 0, 1])
            snap.push_opacity(0.15 + 0.85 * b)
            snap.push_color_matrix(tint, Graphene.Vec4().init(0, 0, 0, 0))
            snap.append_texture(mask, bounds)
            snap.pop()
            snap.pop()
            return
        kw, kh = width - 2 * self.PAD, height - 2 * self.PAD
        for (x, y), c in zip(self.lamps, lamplight.render(state, time.time(), self.lamps)):
            rgba = Gdk.RGBA()
            rgba.red, rgba.green, rgba.blue, rgba.alpha = *c, 0.25 + 0.75 * b
            snap.append_color(rgba, Graphene.Rect().init(
                self.PAD + x * kw - 5, self.PAD + y * kh - 5, 10, 10))


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Lamplight', default_width=520, default_height=720)
        self.settings = Gio.Settings.new(lamplight.SCHEMA)
        schema = Gio.SettingsSchemaSource.get_default().lookup('org.gnome.desktop.interface', True)
        self.interface = (Gio.Settings.new('org.gnome.desktop.interface')
                          if schema and schema.has_key('accent-color') else None)
        self.syncing = False

        devices, _seen = lamplight.find_devices()
        self.device = devices[0] if devices else None
        for d in devices:
            d.close()

        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append('Report Your Device', 'app.report')
        menu.append('About Lamplight', 'app.about')
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu,
                                       tooltip_text='Main Menu'))

        self.banner = Adw.Banner(title='No permission to access the lighting device. '
                                       'Reinstall lamplight or reload the udev rules.')
        self.stack = Gtk.Stack()
        self.stack.add_named(self.build_page(devices), 'settings')
        self.stack.add_named(Adw.StatusPage(
            icon_name='keyboard-brightness-symbolic',
            title='No Compatible Lighting Found',
            description='Lamplight controls devices with Windows Dynamic Lighting '
                        '(HID LampArray). This computer has none, or its lighting needs '
                        'a vendor driver that only exists on Windows.'), 'empty')

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.set_content(view)

        self.settings.connect('changed', lambda *_: self.sync())
        self.sync()

    def build_page(self, devices):
        page = Adw.PreferencesPage()

        preview_group = Adw.PreferencesGroup()
        frame = Gtk.Frame(child=Preview(self.settings, self.interface, self.device))
        frame.add_css_class('card')
        preview_group.add(frame)
        page.add(preview_group)

        group = Adw.PreferencesGroup(title='Lighting')
        enabled = Adw.SwitchRow(title='Lighting')
        self.settings.bind('enabled', enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        group.add(enabled)

        self.effect = Adw.ComboRow(title='Effect',
                                   model=Gtk.StringList.new([label for _, label in MODES]))
        self.effect.connect('notify::selected', self.on_effect)
        group.add(self.effect)

        self.color = self.color_row('Color', 'color')
        self.color2 = self.color_row('Second Color', 'color2')
        group.add(self.color)
        group.add(self.color2)

        self.brightness = self.scale_row(group, 'Brightness', 0, 100, 1)
        self.brightness[1].connect(
            'value-changed', lambda s: self.syncing or self.settings.set_double(
                'brightness', s.get_value() / 100))
        self.speed = self.scale_row(group, 'Speed', 1, 10, 1)
        self.speed[1].connect(
            'value-changed', lambda s: self.syncing or self.settings.set_double(
                'speed', speed_to_period(s.get_value())))

        self.direction = Adw.ComboRow(title='Direction')
        self.direction.connect('notify::selected', self.on_direction)
        self.direction_mode = None
        group.add(self.direction)
        page.add(group)

        behavior = Adw.PreferencesGroup(title='Behavior')
        screen = Adw.SwitchRow(title='Turn Off With Screen',
                               subtitle='Lights go dark while the screen is off or locked')
        self.settings.bind('off-with-screen', screen, 'active', Gio.SettingsBindFlags.DEFAULT)
        behavior.add(screen)
        page.add(behavior)

        # LEDs mix colors differently from screens (blue is often too
        # strong); per-channel gains let users match what they see.
        calibration = Adw.PreferencesGroup(title='Color Calibration')
        self.balance = Adw.ExpanderRow(
            title='White Balance',
            subtitle='Lower a channel if white looks tinted on the keyboard')
        reset = Gtk.Button(icon_name='edit-undo-symbolic', valign=Gtk.Align.CENTER,
                           tooltip_text='Reset')
        reset.add_css_class('flat')
        reset.connect('clicked', lambda _b: self.settings.reset('white-balance'))
        self.balance.add_suffix(reset)
        self.balance_scales = []
        for name in ('Red', 'Green', 'Blue'):
            row = Adw.ActionRow(title=name)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 30, 100, 1)
            scale.set_size_request(240, -1)
            scale.set_draw_value(False)
            scale.connect('value-changed', lambda _s: self.syncing or self.save_balance())
            row.add_suffix(scale)
            self.balance.add_row(row)
            self.balance_scales.append(scale)
        calibration.add(self.balance)
        page.add(calibration)

        if devices:
            info = Adw.PreferencesGroup(title='Devices')
            for d in devices:
                lamps = '1 zone' if d.count == 1 else f'{d.count} lamps'
                info.add(Adw.ActionRow(title=d.kind, subtitle=f'{lamps} · {d.devnode}'))
            page.add(info)
        return page

    def color_row(self, title, key):
        row = Adw.ActionRow(title=title)
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                       valign=Gtk.Align.CENTER)
        button.connect('notify::rgba', lambda b, _p: self.syncing or self.settings.set_string(
            key, to_hex(b.get_rgba())))
        row.add_suffix(button)
        row.button = button
        return row

    def scale_row(self, group, title, lo, hi, step):
        row = Adw.ActionRow(title=title)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_size_request(240, -1)
        scale.set_draw_value(False)
        row.add_suffix(scale)
        group.add(row)
        return row, scale

    def save_balance(self):
        gains = tuple(s.get_value() / 100 for s in self.balance_scales)
        self.settings.set_value('white-balance', GLib.Variant('(ddd)', gains))

    def on_effect(self, row, _param):
        if not self.syncing:
            self.settings.set_string('mode', MODES[row.get_selected()][0])

    def on_direction(self, row, _param):
        options = DIRECTIONS.get(self.direction_mode)
        if not self.syncing and options and row.get_selected() < len(options):
            self.settings.set_string('direction', options[row.get_selected()][0])

    def sync(self):
        s = self.settings
        status = s.get_string('status')
        self.stack.set_visible_child_name('empty' if status == 'no-device' else 'settings')
        self.banner.set_revealed(status == 'no-access')

        mode = s.get_string('mode')
        self.syncing = True
        self.effect.set_selected([m for m, _ in MODES].index(mode))
        self.color.set_visible(mode in USES_COLOR)
        self.color2.set_visible(mode in USES_COLOR2)
        self.color.button.set_rgba(to_rgba(s.get_string('color')))
        self.color2.button.set_rgba(to_rgba(s.get_string('color2')))
        self.brightness[0].set_visible(mode != 'firmware')
        self.brightness[1].set_value(s.get_double('brightness') * 100)
        self.speed[0].set_visible(mode in lamplight.ANIMATED)
        self.speed[1].set_value(round(period_to_speed(s.get_double('speed'))))
        for scale, gain in zip(self.balance_scales, s.get_value('white-balance').unpack()):
            scale.set_value(gain * 100)

        options = DIRECTIONS.get(mode)
        self.direction.set_visible(bool(options))
        if options:
            if self.direction_mode != mode:
                self.direction.set_model(Gtk.StringList.new([label for _, label in options]))
            ids = [d for d, _ in options]
            stored = s.get_string('direction')
            self.direction.set_selected(ids.index(stored) if stored in ids else 0)
        self.direction_mode = mode
        self.syncing = False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        for name, callback in (('about', self.about), ('report', self.report)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
        self.set_accels_for_action('window.close', ['<Control>w'])

    def do_activate(self):
        if Gio.SettingsSchemaSource.get_default().lookup(lamplight.SCHEMA, True) is None:
            print('Error: settings schema not installed; install lamplight first', file=sys.stderr)
            self.quit()
            return
        (self.props.active_window or Window(self)).present()

    def about(self, *_):
        Adw.AboutDialog(
            application_name='Lamplight', application_icon=APP_ID, version=lamplight.__version__,
            developer_name='egeisik35', license_type=Gtk.License.MIT_X11,
            website=REPO, issue_url=f'{REPO}/issues',
            comments='RGB lighting for keyboards and devices with Windows Dynamic Lighting '
                     '(HID LampArray).',
        ).present(self.props.active_window)

    def report(self, *_):
        Gtk.UriLauncher.new(f'{REPO}/issues/new?template=device-report.yml').launch(
            self.props.active_window, None, None)


if __name__ == '__main__':
    sys.exit(App().run(sys.argv))
