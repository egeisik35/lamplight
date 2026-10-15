// Lamplight settings window (Extension Manager → gear icon, or the
// "Lighting Settings" item in the tile). Edits the same settings as the tile;
// the lamplight service applies them to the hardware.

import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {setupAccess} from './common.js';

const MODES = [
    ['solid', 'Solid color'],
    ['accent', 'Match accent color'],
    ['breathe', 'Breathing'],
    ['cycle', 'Color cycle'],
    ['rainbow', 'Rainbow'],
    ['wave', 'Wave'],
    ['wheel', 'Wheel'],
    ['gradient', 'Gradient'],
    ['firmware', 'Built-in effect'],
];
const USES_COLOR = new Set(['solid', 'breathe', 'wave', 'gradient']);
const USES_COLOR2 = new Set(['breathe', 'wave', 'gradient']);
const ANIMATED = new Set(['breathe', 'cycle', 'rainbow', 'wave', 'wheel']);
const SPATIAL = [['right', 'Right'], ['left', 'Left'], ['down', 'Down'], ['up', 'Up'],
    ['out', 'Outward'], ['in', 'Inward']];
const DIRECTIONS = {
    cycle: [['right', 'Forward'], ['left', 'Reverse']],
    rainbow: SPATIAL,
    wave: SPATIAL,
    gradient: SPATIAL,
    wheel: [['cw', 'Clockwise'], ['ccw', 'Counterclockwise']],
};
const ACCENTS = {
    blue: '3584e4', teal: '2190a4', green: '3a944a', yellow: 'c88800', orange: 'ed5b00',
    red: 'e62d42', pink: 'd56199', purple: '9141ac', slate: '6f8396',
};

// Speed 1-10 like Windows, mapped logarithmically onto the effect period.
const MIN_PERIOD = 0.5, MAX_PERIOD = 20;
const speedToPeriod = level => MAX_PERIOD * (MIN_PERIOD / MAX_PERIOD) ** ((level - 1) / 9);
const periodToSpeed = p => 1 + 9 * Math.log(p / MAX_PERIOD) / Math.log(MIN_PERIOD / MAX_PERIOD);

const hexToRgb = hex => [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
const rgbToHex = rgb => rgb.map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('');

// --- Effects: same math as render() in lamplight.py, for the preview ---

function hue(h) {
    h = ((h % 1) + 1) % 1;
    const i = Math.floor(h * 6), f = h * 6 - i, q = 1 - f;
    return [[1, f, 0], [q, 1, 0], [0, 1, f], [0, q, 1], [f, 0, 1], [1, 0, q]][i];
}

const mix = (a, b, k) => a.map((x, i) => x + (b[i] - x) * k);

function axis(direction, x, y) {
    if (direction === 'out' || direction === 'in') {
        const d = Math.min(1, Math.hypot(x - 0.5, y - 0.5) / Math.hypot(0.5, 0.5));
        return direction === 'out' ? d : 1 - d;
    }
    return {right: x, left: 1 - x, down: y, up: 1 - y}[direction] ?? x;
}

function render(state, t, positions) {
    const {mode, direction, colors: [c1, c2]} = state;
    const phase = t / state.speed;
    const backwards = ['left', 'up', 'in', 'ccw'].includes(direction);
    return positions.map(([x, y]) => {
        switch (mode) {
        case 'solid': return c1;
        case 'gradient': return mix(c1, c2, axis(direction, x, y));
        case 'breathe': return mix(c2, c1, (1 - Math.cos(2 * Math.PI * phase)) / 2);
        case 'cycle': return hue(backwards ? -phase : phase);
        case 'rainbow': return hue(phase - axis(direction, x, y));
        case 'wave': {
            const s = axis(direction, x, y);
            const d = Math.abs((((s - phase) % 1) + 1) % 1 - 0.5) * 2;
            return mix(c1, c2, Math.min(1, d * 2.5));
        }
        case 'wheel':
            return hue((backwards ? -phase : phase) + Math.atan2(y - 0.5, x - 0.5) / (2 * Math.PI));
        default: return [0, 0, 0];
        }
    });
}

function settingsState(settings, accent) {
    if (!settings.get_boolean('enabled'))
        return {mode: 'off', colors: [[0, 0, 0], [0, 0, 0]], brightness: 0, speed: 4, direction: 'right'};
    let mode = settings.get_string('mode');
    let colors = [settings.get_string('color'), settings.get_string('color2')].map(hexToRgb);
    if (mode === 'accent') {
        const rgb = hexToRgb(ACCENTS[accent] ?? ACCENTS.blue);
        const top = Math.max(...rgb) || 1;
        mode = 'solid';
        colors = [rgb.map(v => v / top), [0, 0, 0]];
    }
    return {
        mode, colors,
        brightness: settings.get_double('brightness'),
        speed: settings.get_double('speed'),
        direction: settings.get_string('direction'),
    };
}

// --- Preview ---

const ROWS = [14, 14, 14, 13, 12, 8];
const KEYS = ROWS.flatMap((n, r) => Array.from({length: n}, (_, i) =>
    [i / n, r / ROWS.length, 1 / n, 1 / ROWS.length]));

function buildPreview(settings, interface_) {
    const area = new Gtk.DrawingArea({content_height: 170, hexpand: true});
    let state = null;
    const update = () => {
        state = settingsState(settings, interface_?.get_string('accent-color'));
        area.queue_draw();
    };
    area.set_draw_func((_a, cr, width, height) => {
        const pad = 12, gap = 4, r = 4;
        const kw = width - 2 * pad, kh = height - 2 * pad;
        const t = GLib.get_real_time() / 1e6;  // wall clock, like the service
        // A single-zone keyboard shows one color; per-key ones show the pattern.
        const perKey = settings.get_int('lamp-count') > 1;
        const centers = KEYS.map(([x, y, w, h]) => [x + w / 2, y + h / 2]);
        const colors = state.mode === 'firmware'
            ? centers.map(([x]) => hue(x))
            : render(state, t, perKey ? centers : [[0.5, 0.5]]);
        const alpha = state.mode === 'firmware' ? 1 : 0.15 + 0.85 * state.brightness;
        KEYS.forEach(([x, y, w, h], i) => {
            const [kx, ky, kwid, khei] = [pad + x * kw, pad + y * kh, w * kw - gap, h * kh - gap];
            cr.newSubPath();
            cr.arc(kx + kwid - r, ky + r, r, -Math.PI / 2, 0);
            cr.arc(kx + kwid - r, ky + khei - r, r, 0, Math.PI / 2);
            cr.arc(kx + r, ky + khei - r, r, Math.PI / 2, Math.PI);
            cr.arc(kx + r, ky + r, r, Math.PI, 3 * Math.PI / 2);
            cr.closePath();
            cr.setSourceRGB(0.16, 0.16, 0.18);
            cr.fillPreserve();
            const c = colors[perKey || state.mode === 'firmware' ? i : 0];
            cr.setSourceRGBA(c[0], c[1], c[2], c.some(v => v) ? alpha : 0);
            cr.fill();
        });
        cr.$dispose();
    });

    // 30 fps only while an effect animates and the window is visible.
    let timer = 0;
    const animate = () => {
        const want = state && ANIMATED.has(state.mode);
        if (want && !timer) {
            timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 33, () => {
                if (area.get_mapped())
                    area.queue_draw();
                return GLib.SOURCE_CONTINUE;
            });
        } else if (!want && timer) {
            GLib.source_remove(timer);
            timer = 0;
        }
    };
    const settingsId = settings.connect('changed', () => {
        update();
        animate();
    });
    const accentId = interface_?.connect('changed::accent-color', update);
    area.connect('destroy', () => {
        if (timer)
            GLib.source_remove(timer);
        settings.disconnect(settingsId);
        if (accentId)
            interface_.disconnect(accentId);
    });
    update();
    animate();

    const frame = new Gtk.Frame({child: area});
    frame.add_css_class('card');
    return frame;
}

// --- Widgets ---

function colorRow(settings, title, key) {
    const button = new Gtk.ColorDialogButton({
        dialog: new Gtk.ColorDialog({with_alpha: false}),
        valign: Gtk.Align.CENTER,
    });
    const row = new Adw.ActionRow({title});
    row.add_suffix(button);
    let syncing = false;
    const sync = () => {
        syncing = true;
        const rgba = new Gdk.RGBA();
        rgba.parse(`#${settings.get_string(key)}`);
        button.rgba = rgba;
        syncing = false;
    };
    button.connect('notify::rgba', () => {
        if (!syncing)
            settings.set_string(key, rgbToHex([button.rgba.red, button.rgba.green, button.rgba.blue]));
    });
    const id = settings.connect(`changed::${key}`, sync);
    row.connect('destroy', () => settings.disconnect(id));
    sync();
    return row;
}

function scaleRow(title, lower, upper, value, onChange, subtitle = '') {
    const scale = new Gtk.Scale({
        adjustment: new Gtk.Adjustment({lower, upper, step_increment: 1}),
        draw_value: false,
        width_request: 240,
        valign: Gtk.Align.CENTER,
    });
    scale.set_value(value);
    scale.connect('value-changed', () => onChange(scale.get_value()));
    const row = new Adw.ActionRow({title, subtitle});
    row.add_suffix(scale);
    row.scale = scale;
    return row;
}

export default class LamplightPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const schema = Gio.SettingsSchemaSource.get_default().lookup('org.gnome.desktop.interface', true);
        const interface_ = schema?.has_key('accent-color')
            ? new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}) : null;
        window.set_default_size(560, 820);
        window._settings = settings;  // keep alive with the window

        const page = new Adw.PreferencesPage({title: 'Lighting', icon_name: 'keyboard-brightness-symbolic'});
        window.add(page);

        // Status: shown only when something needs attention.
        const statusGroup = new Adw.PreferencesGroup();
        const statusRow = new Adw.ActionRow({title: 'Keyboard access needed'});
        const setupButton = new Gtk.Button({label: 'Set Up…', valign: Gtk.Align.CENTER});
        setupButton.add_css_class('suggested-action');
        setupButton.connect('clicked', () => setupAccess(this.path, () => {
            statusRow.subtitle = 'Done. The lights respond within a few seconds.';
            setupButton.visible = false;
        }));
        statusRow.add_suffix(setupButton);
        statusGroup.add(statusRow);
        page.add(statusGroup);

        const previewGroup = new Adw.PreferencesGroup();
        previewGroup.add(buildPreview(settings, interface_));
        page.add(previewGroup);

        // Lighting
        const lighting = new Adw.PreferencesGroup({title: 'Lighting'});
        const enabled = new Adw.SwitchRow({title: 'Lighting'});
        settings.bind('enabled', enabled, 'active', Gio.SettingsBindFlags.DEFAULT);
        lighting.add(enabled);

        const effect = new Adw.ComboRow({
            title: 'Effect',
            model: Gtk.StringList.new(MODES.map(([, label]) => label)),
        });
        lighting.add(effect);
        const color = colorRow(settings, 'Color', 'color');
        const color2 = colorRow(settings, 'Second Color', 'color2');
        lighting.add(color);
        lighting.add(color2);

        let syncing = false;
        const brightness = scaleRow('Brightness', 0, 100, settings.get_double('brightness') * 100,
            v => syncing || settings.set_double('brightness', v / 100));
        const speed = scaleRow('Speed', 1, 10, Math.round(periodToSpeed(settings.get_double('speed'))),
            v => syncing || settings.set_double('speed', speedToPeriod(v)));
        lighting.add(brightness);
        lighting.add(speed);

        const direction = new Adw.ComboRow({title: 'Direction'});
        let directionMode = null;
        lighting.add(direction);
        page.add(lighting);

        effect.connect('notify::selected', () => {
            if (!syncing)
                settings.set_string('mode', MODES[effect.selected][0]);
        });
        direction.connect('notify::selected', () => {
            const options = DIRECTIONS[directionMode];
            if (!syncing && options && effect.selected >= 0 && direction.selected < options.length)
                settings.set_string('direction', options[direction.selected][0]);
        });

        // Behavior
        const behavior = new Adw.PreferencesGroup({title: 'Behavior'});
        const screen = new Adw.SwitchRow({
            title: 'Turn Off With Screen',
            subtitle: 'Lights go dark while the screen is off or locked',
        });
        settings.bind('off-with-screen', screen, 'active', Gio.SettingsBindFlags.DEFAULT);
        behavior.add(screen);
        page.add(behavior);

        // Calibration: LEDs mix colors differently from screens (blue is often
        // too strong); per-channel gains and gamma let users match what they see.
        const calibration = new Adw.PreferencesGroup({title: 'Color Calibration'});
        const balance = new Adw.ExpanderRow({
            title: 'White Balance',
            subtitle: 'Lower a channel if white looks tinted on the keyboard',
        });
        const reset = new Gtk.Button({icon_name: 'edit-undo-symbolic', valign: Gtk.Align.CENTER,
            tooltip_text: 'Reset'});
        reset.add_css_class('flat');
        reset.connect('clicked', () => settings.reset('white-balance'));
        balance.add_suffix(reset);
        const channels = ['Red', 'Green', 'Blue'].map((name, i) => {
            const row = scaleRow(name, 30, 100, settings.get_value('white-balance').deepUnpack()[i] * 100, () => {
                if (syncing)
                    return;
                const gains = channels.map(r => r.scale.get_value() / 100);
                settings.set_value('white-balance', new GLib.Variant('(ddd)', gains));
            });
            balance.add_row(row);
            return row;
        });
        calibration.add(balance);
        const gamma = new Adw.SpinRow({
            title: 'Gamma',
            subtitle: '2.2 matches screens; 1.0 sends raw values',
            digits: 1,
            adjustment: new Gtk.Adjustment({lower: 1, upper: 3, step_increment: 0.1}),
        });
        settings.bind('gamma', gamma, 'value', Gio.SettingsBindFlags.DEFAULT);
        calibration.add(gamma);
        page.add(calibration);

        // About
        const about = new Adw.PreferencesGroup({title: 'About'});
        const version = new Adw.ActionRow({
            title: 'Lamplight',
            subtitle: `Version ${this.metadata['version-name'] ?? this.metadata.version ?? ''}`,
        });
        const link = new Gtk.LinkButton({label: 'Website & device reports',
            uri: this.metadata.url, valign: Gtk.Align.CENTER});
        version.add_suffix(link);
        about.add(version);
        page.add(about);

        const sync = () => {
            const mode = settings.get_string('mode');
            const status = settings.get_string('status');
            syncing = true;
            statusGroup.visible = status !== '';
            setupButton.visible = status === 'no-access';
            statusRow.title = {
                'no-access': 'Keyboard access needed',
                'no-device': 'No compatible lighting found',
                'no-python': 'Python 3 with PyGObject is needed',
            }[status] ?? '';
            statusRow.subtitle = {
                'no-access': 'One-time setup, asks for your password',
                'no-device': 'Lamplight controls devices with Windows Dynamic Lighting (HID LampArray)',
                'no-python': 'Install python3-gi (Debian/Ubuntu) or python3-gobject (Fedora)',
            }[status] ?? '';
            effect.selected = MODES.findIndex(([m]) => m === mode);
            color.visible = USES_COLOR.has(mode);
            color2.visible = USES_COLOR2.has(mode);
            brightness.visible = mode !== 'firmware';
            brightness.scale.set_value(settings.get_double('brightness') * 100);
            speed.visible = ANIMATED.has(mode);
            speed.scale.set_value(Math.round(periodToSpeed(settings.get_double('speed'))));
            const options = DIRECTIONS[mode];
            direction.visible = !!options;
            if (options) {
                if (directionMode !== mode)
                    direction.model = Gtk.StringList.new(options.map(([, label]) => label));
                const ids = options.map(([d]) => d);
                direction.selected = Math.max(0, ids.indexOf(settings.get_string('direction')));
            }
            directionMode = mode;
            settings.get_value('white-balance').deepUnpack()
                .forEach((g, i) => channels[i].scale.set_value(g * 100));
            syncing = false;
        };
        const id = settings.connect('changed', sync);
        window.connect('close-request', () => {
            settings.disconnect(id);
            return false;
        });
        sync();
    }
}
