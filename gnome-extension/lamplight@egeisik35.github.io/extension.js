// Lamplight: Quick Settings tile for HID LampArray (Dynamic Lighting) devices.
// Hardware access lives in the `lamplight daemon` backend; this extension
// spawns it and sends one JSON state per line on its stdin.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

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

// The stored direction is shared by all effects; fall back per effect.
const effectiveDirection = (mode, dir) => {
    const options = DIRECTIONS[mode];
    return !options || options.some(([d]) => d === dir) ? dir : options[0][0];
};

const SWATCHES = ['ff0000', 'ff5500', 'ffcc00', '00ff00', '00ffff', '0000ff', '8000ff', 'ff2090', 'ffffff'];

// libadwaita accent colors (GNOME 47+)
const ACCENTS = {
    blue: '3584e4', teal: '2190a4', green: '3a944a', yellow: 'c88800', orange: 'ed5b00',
    red: 'e62d42', pink: 'd56199', purple: '9141ac', slate: '6f8396',
};

const MIN_PERIOD = 0.5, MAX_PERIOD = 20;

const hexToRgb = hex => [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16));
const rgbToHex = rgb => rgb.map(v => Math.round(v).toString(16).padStart(2, '0')).join('');

// LEDs wash out darker UI colors: scale so the brightest channel is 255.
const vivid = hex => {
    const rgb = hexToRgb(hex), max = Math.max(...rgb) || 1;
    return rgbToHex(rgb.map(v => v * 255 / max));
};

const SliderRow = GObject.registerClass(
class SliderRow extends PopupMenu.PopupBaseMenuItem {
    constructor(iconName, accessibleName) {
        super({activate: false});
        this.add_child(new St.Icon({icon_name: iconName, style_class: 'popup-menu-icon'}));
        this.slider = new Slider(0);
        this.slider.accessible_name = accessibleName;
        this.add_child(this.slider);
    }
});

const ColorRow = GObject.registerClass(
class ColorRow extends PopupMenu.PopupBaseMenuItem {
    constructor(label, swatches, onPick, onCustom) {
        super({activate: false, can_focus: false});
        const box = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        box.add_child(new St.Label({text: label, style_class: 'lamplight-label'}));
        const row = new St.BoxLayout({style_class: 'lamplight-swatches'});
        this._buttons = swatches.map(hex => {
            const b = new St.Button({
                style_class: 'lamplight-swatch',
                style: `background-color: #${hex};`,
                can_focus: true,
                accessible_name: `#${hex}`,
            });
            b.connect('clicked', () => onPick(hex));
            row.add_child(b);
            return [hex, b];
        });
        const custom = new St.Button({
            style_class: 'icon-button',
            child: new St.Icon({icon_name: 'color-select-symbolic', icon_size: 16}),
            can_focus: true,
            accessible_name: `${label}: custom`,
        });
        custom.connect('clicked', onCustom);
        row.add_child(custom);
        box.add_child(row);
        this.add_child(box);
    }

    setSelected(hex) {
        for (const [h, b] of this._buttons)
            b.checked = h === hex;
    }
});

const LightingToggle = GObject.registerClass(
class LightingToggle extends QuickMenuToggle {
    constructor(ext) {
        super({title: 'Lighting', iconName: 'keyboard-brightness-symbolic', toggleMode: true});
        this._ext = ext;
        const s = ext.settings;
        s.bind('enabled', this, 'checked', Gio.SettingsBindFlags.DEFAULT);

        this.menu.setHeader('keyboard-brightness-symbolic', 'Lighting');

        // Effects and directions sit in collapsed submenus so the menu fits
        // on small screens.
        this._modeMenu = new PopupMenu.PopupSubMenuMenuItem('', false);
        this._modeItems = new Map();
        for (const [mode, label] of MODES)
            this._modeItems.set(mode, this._modeMenu.menu.addAction(label, () => this._set('mode', mode)));
        this.menu.addMenuItem(this._modeMenu);

        this._color = new ColorRow('Color', SWATCHES,
            hex => this._pickColor('color', hex), () => this._customColor('color'));
        this.menu.addMenuItem(this._color);
        this._color2 = new ColorRow('Second color', ['000000', ...SWATCHES.slice(0, 8)],
            hex => this._pickColor('color2', hex), () => this._customColor('color2'));
        this.menu.addMenuItem(this._color2);

        this._brightness = new SliderRow('display-brightness-symbolic', 'Brightness');
        this._brightness.slider.connect('notify::value', () => {
            if (!this._syncing)
                s.set_double('brightness', this._brightness.slider.value);
        });
        this.menu.addMenuItem(this._brightness);

        this._speed = new SliderRow('media-seek-forward-symbolic', 'Speed');
        this._speed.slider.connect('notify::value', () => {
            if (!this._syncing)
                s.set_double('speed', MAX_PERIOD - this._speed.slider.value * (MAX_PERIOD - MIN_PERIOD));
        });
        this.menu.addMenuItem(this._speed);

        this._direction = new PopupMenu.PopupSubMenuMenuItem('', false);
        this.menu.addMenuItem(this._direction);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._screenOff = new PopupMenu.PopupSwitchMenuItem('Turn off with screen', false);
        this._screenOff.connect('toggled', (_i, state) => s.set_boolean('off-with-screen', state));
        this.menu.addMenuItem(this._screenOff);

        s.connectObject('changed', () => this.sync(), this);
        this.sync();
    }

    _set(key, mode) {
        this._ext.settings.set_string(key, mode);
        this._ext.settings.set_boolean('enabled', true);
    }

    _pickColor(key, hex) {
        const s = this._ext.settings;
        if (key === 'color' && !USES_COLOR.has(s.get_string('mode')))
            s.set_string('mode', 'solid');
        this._set(key, hex);
    }

    _customColor(key) {
        this.menu.close();
        const [r, g, b] = hexToRgb(this._ext.settings.get_string(key));
        try {
            const proc = Gio.Subprocess.new(
                ['zenity', '--color-selection', '--title=Lighting color', `--color=rgb(${r},${g},${b})`],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
            proc.communicate_utf8_async(null, null, (p, res) => {
                const [, out] = p.communicate_utf8_finish(res);
                const m = out?.match(/(\d+)\D+(\d+)\D+(\d+)/);
                if (p.get_successful() && m)
                    this._pickColor(key, rgbToHex(m.slice(1, 4).map(Number)));
            });
        } catch (e) {
            console.warn(`lamplight: color picker (zenity) unavailable: ${e.message}`);
        }
    }

    sync() {
        const s = this._ext.settings;
        const mode = s.get_string('mode');
        this._syncing = true;
        for (const [m, item] of this._modeItems)
            item.setOrnament(m === mode ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NO_DOT);
        const modeLabel = MODES.find(([m]) => m === mode)[1];
        this._modeMenu.label.text = `Effect: ${modeLabel}`;
        this._syncDirection(mode, s.get_string('direction'));
        this._color.visible = USES_COLOR.has(mode);
        this._color2.visible = USES_COLOR2.has(mode);
        this._color.setSelected(s.get_string('color'));
        this._color2.setSelected(s.get_string('color2'));
        this._speed.visible = ANIMATED.has(mode);
        this._brightness.visible = mode !== 'firmware';
        this._brightness.slider.value = s.get_double('brightness');
        this._speed.slider.value = (MAX_PERIOD - s.get_double('speed')) / (MAX_PERIOD - MIN_PERIOD);
        this._screenOff.setToggleState(s.get_boolean('off-with-screen'));
        this._syncing = false;
        this.subtitle = this._ext.error ?? modeLabel;
    }

    _syncDirection(mode, stored) {
        const options = DIRECTIONS[mode];
        this._direction.visible = !!options;
        if (!options)
            return;
        const dir = effectiveDirection(mode, stored);
        this._direction.label.text = `Direction: ${options.find(([d]) => d === dir)[1]}`;
        if (this._directionMode !== mode) {
            this._directionMode = mode;
            this._direction.menu.removeAll();
            this._directionItems = options.map(([d, label]) =>
                [d, this._direction.menu.addAction(label, () => this._set('direction', d))]);
        }
        for (const [d, item] of this._directionItems)
            item.setOrnament(d === dir ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NO_DOT);
    }
});

const Indicator = GObject.registerClass(
class Indicator extends SystemIndicator {
    constructor(ext) {
        super();
        this.toggle = new LightingToggle(ext);
        this.quickSettingsItems.push(this.toggle);
    }

    destroy() {
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});

export default class LamplightExtension extends Extension {
    enable() {
        this.settings = this.getSettings();
        this.error = null;
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._restarts = 0;

        this._indicator = new Indicator(this);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);

        this.settings.connectObject('changed', () => {
            this._restarts = 0;
            this._queueSend();
        }, this);
        // Lights follow the screen: off while blanked or locked, if enabled.
        Main.screenShield?.connectObject('active-changed', () => this._queueSend(), this);
        this._interface.connectObject('changed::accent-color', () => {
            if (this.settings.get_string('mode') === 'accent')
                this._queueSend();
        }, this);

        // Many controllers forget their state across suspend: re-send on resume.
        this._sleepId = Gio.DBus.system.signal_subscribe('org.freedesktop.login1',
            'org.freedesktop.login1.Manager', 'PrepareForSleep', '/org/freedesktop/login1',
            null, Gio.DBusSignalFlags.NONE, (_c, _s, _p, _i, _n, params) => {
                const [suspending] = params.deep_unpack();
                if (!suspending) {
                    this._restarts = 0;
                    this._queueSend(1500);
                }
            });

        this._send();
    }

    disable() {
        Gio.DBus.system.signal_unsubscribe(this._sleepId);
        this.settings.disconnectObject(this);
        this._interface.disconnectObject(this);
        Main.screenShield?.disconnectObject(this);
        for (const id of [this._sendId, this._restartId])
            if (id)
                GLib.source_remove(id);
        this._sendId = this._restartId = null;
        // Closing stdin makes the daemon exit, keeping the last colors shown.
        const daemon = this._daemon;
        this._daemon = null;
        daemon?.get_stdin_pipe().close(null);
        this._indicator.destroy();
        this._indicator = this._interface = this.settings = null;
    }

    _setError(msg) {
        this.error = msg;
        this._indicator?.toggle.sync();
    }

    _queueSend(delay = 30) {
        if (this._sendId)
            GLib.source_remove(this._sendId);
        this._sendId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
            this._sendId = null;
            this._send();
            return GLib.SOURCE_REMOVE;
        });
    }

    _state() {
        const s = this.settings;
        const screenOff = s.get_boolean('off-with-screen') && Main.screenShield?.active;
        if (!s.get_boolean('enabled') || screenOff)
            return {mode: 'off'};
        let mode = s.get_string('mode');
        const direction = effectiveDirection(mode, s.get_string('direction'));
        let colors = [s.get_string('color'), s.get_string('color2')];
        if (mode === 'accent') {
            mode = 'solid';
            colors = [vivid(ACCENTS[this._interface.get_string('accent-color')] ?? ACCENTS.blue)];
        }
        return {
            mode, colors,
            brightness: s.get_double('brightness'),
            speed: s.get_double('speed'),
            direction,
        };
    }

    _send() {
        if (!this._ensureDaemon())
            return;
        try {
            const line = new TextEncoder().encode(`${JSON.stringify(this._state())}\n`);
            this._daemon.get_stdin_pipe().write_all(line, null);
        } catch (e) {
            console.warn(`lamplight: cannot reach daemon: ${e.message}`);
        }
    }

    _ensureDaemon() {
        if (this._daemon)
            return true;
        if (this._restartId)
            return false;
        const bin = GLib.find_program_in_path('lamplight');
        if (!bin) {
            this._setError('lamplight backend not installed');
            return false;
        }
        let proc;
        try {
            proc = Gio.Subprocess.new([bin, 'daemon'],
                Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_SILENCE |
                Gio.SubprocessFlags.STDERR_PIPE);
        } catch (e) {
            this._setError(e.message);
            return false;
        }
        this._daemon = proc;
        this._setError(null);
        proc.wait_async(null, () => this._daemonExited(proc));
        return true;
    }

    _daemonExited(proc) {
        if (this._daemon !== proc)
            return; // stopped by disable()
        this._daemon = null;
        let stderr = '';
        try {
            const bytes = proc.get_stderr_pipe().read_bytes(65536, null);
            stderr = new TextDecoder().decode(bytes.toArray());
        } catch {}
        // Exit code 3: no LampArray hardware here; hide instead of nagging.
        if (proc.get_exit_status() === 3) {
            this._indicator.toggle.visible = false;
            return;
        }
        console.warn(`lamplight: daemon exited (${proc.get_exit_status()}): ${stderr}`);
        const error = stderr.split('\n').find(l => l.startsWith('Error: '));
        this._setError(error?.slice(7) ?? 'Lighting backend stopped');
        // ponytail: 3 quick retries cover resume/re-enumeration; a real fault
        // (no device, no permission) then waits for the next settings change.
        if (this._restarts++ < 3) {
            this._restartId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
                this._restartId = null;
                this._send();
                return GLib.SOURCE_REMOVE;
            });
        }
    }
}
