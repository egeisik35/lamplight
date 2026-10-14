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
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

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


class Preview(Gtk.DrawingArea):
    """Live preview of the current effect, as the hardware would show it."""

    def __init__(self, settings, interface, device):
        super().__init__(content_height=170, hexpand=True)
        self.settings, self.interface = settings, interface
        self.keys = keyboard_keys()
        if device and device.count > 1:
            self.lamps = device.positions  # per-key/zone device: draw real lamp spots
        else:
            self.lamps = None  # single zone: whole keyboard shows one color
        self.start = time.monotonic()
        self.set_draw_func(self.draw)
        self.add_tick_callback(lambda *_: self.queue_draw() or GLib.SOURCE_CONTINUE)

    def draw(self, _area, cr, width, height):
        accent = self.interface.get_string('accent-color') if self.interface else None
        try:
            state = lamplight.settings_state(self.settings, accent)
        except ValueError:
            return
        t = time.monotonic() - self.start
        pad, gap = 12, 4
        kw, kh = width - 2 * pad, height - 2 * pad
        if state['mode'] == 'firmware':
            colors = [lamplight.hue(t / 6 + i / 40) for i in range(len(self.keys))]
        elif self.lamps is None:
            colors = lamplight.render(state, t, [(0.5, 0.5)]) * len(self.keys)
        else:
            colors = None
        b = state['brightness'] if state['mode'] != 'firmware' else 1
        if colors is not None:
            for (x, y, w, h), c in zip(self.keys, colors):
                self.key(cr, pad + x * kw, pad + y * kh, w * kw - gap, h * kh - gap, c, b)
        else:
            for (x, y, w, h) in self.keys:
                self.key(cr, pad + x * kw, pad + y * kh, w * kw - gap, h * kh - gap, (0, 0, 0), 0)
            for (x, y), c in zip(self.lamps, lamplight.render(state, t, self.lamps)):
                cr.set_source_rgba(*c, 0.25 + 0.75 * b)
                cr.arc(pad + x * kw, pad + y * kh, 6, 0, 2 * math.pi)
                cr.fill()

    @staticmethod
    def key(cr, x, y, w, h, color, brightness):
        r = 4
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(0.16, 0.16, 0.18)
        cr.fill_preserve()
        cr.set_source_rgba(*color, 0.15 + 0.85 * brightness if any(color) else 0)
        cr.fill()


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
