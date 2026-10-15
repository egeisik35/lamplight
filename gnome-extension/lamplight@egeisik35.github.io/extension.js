// Lamplight: Quick Settings tile for HID LampArray (Dynamic Lighting) devices.
// The lamplight service drives the hardware from the shared
// org.gnome.shell.extensions.lamplight settings; this tile only edits them.
// With the lamplight package installed, systemd runs that service. Without
// it (installed from extensions.gnome.org), the extension runs the bundled
// lamplight.py itself. That part is Python because it needs ioctl() on
// /dev/hidraw (HID feature reports), which GJS cannot call.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

const SCHEMA = 'org.gnome.shell.extensions.lamplight';
const APP_DESKTOP = 'io.github.egeisik35.Lamplight.desktop';

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

const SWATCHES = ['ff0000', 'ff5500', 'ffcc00', '00ff00', '00ffff', '0000ff', '8000ff', 'ff2090', 'ffffff'];
const STATUS = {
    'no-access': 'Needs one-time setup',
    'no-python': 'Needs Python 3 with PyGObject',
};
const RULES_FILE = '/etc/udev/rules.d/70-lamplight-extension.rules';

// Runs as root through pkexec. Takes only HID IDs as arguments, rejects
// anything that is not BUS:VENDOR:PRODUCT hex, and writes a fixed rule
// shape, so no user-controlled file or text ends up in the rule.
const INSTALL_RULES = `
set -e
f=${RULES_FILE}
echo '# Lamplight: let the logged-in user control keyboard lighting' > "$f.tmp"
for id in "$@"; do
    case "$id" in
    [0-9A-F][0-9A-F][0-9A-F][0-9A-F]:[0-9A-F][0-9A-F][0-9A-F][0-9A-F]:[0-9A-F][0-9A-F][0-9A-F][0-9A-F]) ;;
    *) rm -f "$f.tmp"; exit 2 ;;
    esac
    echo "SUBSYSTEM==\\"hidraw\\", KERNELS==\\"$id.*\\", TAG+=\\"uaccess\\"" >> "$f.tmp"
done
chmod 644 "$f.tmp"
mv "$f.tmp" "$f"
udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw
udevadm settle`;

const MIN_PERIOD = 0.5, MAX_PERIOD = 20;

// The stored direction is shared by all effects; fall back per effect.
const effectiveDirection = (mode, dir) => {
    const options = DIRECTIONS[mode];
    return !options || options.some(([d]) => d === dir) ? dir : options[0][0];
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
    constructor(label, swatches, onPick) {
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
    constructor(settings, ext) {
        super({title: 'Lighting', iconName: 'keyboard-brightness-symbolic', toggleMode: true});
        this._settings = settings;
        settings.bind('enabled', this, 'checked', Gio.SettingsBindFlags.DEFAULT);

        this.menu.setHeader('keyboard-brightness-symbolic', 'Lighting');

        // Effects and directions sit in collapsed submenus so the menu fits
        // on small screens.
        this._modeMenu = new PopupMenu.PopupSubMenuMenuItem('', false);
        this._modeItems = new Map();
        for (const [mode, label] of MODES)
            this._modeItems.set(mode, this._modeMenu.menu.addAction(label, () => this._set('mode', mode)));
        this.menu.addMenuItem(this._modeMenu);

        this._color = new ColorRow('Color', SWATCHES, hex => this._pickColor('color', hex));
        this.menu.addMenuItem(this._color);
        this._color2 = new ColorRow('Second color', ['000000', ...SWATCHES.slice(0, 8)],
            hex => this._pickColor('color2', hex));
        this.menu.addMenuItem(this._color2);

        this._brightness = new SliderRow('display-brightness-symbolic', 'Brightness');
        this._brightness.slider.connect('notify::value', () => {
            if (!this._syncing)
                settings.set_double('brightness', this._brightness.slider.value);
        });
        this.menu.addMenuItem(this._brightness);

        this._speed = new SliderRow('media-seek-forward-symbolic', 'Speed');
        this._speed.slider.connect('notify::value', () => {
            if (!this._syncing)
                settings.set_double('speed', MAX_PERIOD - this._speed.slider.value * (MAX_PERIOD - MIN_PERIOD));
        });
        this.menu.addMenuItem(this._speed);

        this._direction = new PopupMenu.PopupSubMenuMenuItem('', false);
        this.menu.addMenuItem(this._direction);

        this._setupItem = this.menu.addAction('Set up keyboard access…', () => ext.setupAccess());

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        if (Shell.AppSystem.get_default().lookup_app(APP_DESKTOP))  // app comes with the package
            this.menu.addSettingsAction('Lighting Settings', APP_DESKTOP);

        this._fitToScreen(this._modeMenu);
        this._fitToScreen(this._direction);

        settings.connectObject('changed', () => this.sync(), this);
        this.sync();
    }

    // Quick Settings menus do not scroll, so an expanded list could run off
    // the screen. While a list is open, hide the other rows (accordion), then
    // cap the list to the space left below the menu and let it scroll.
    _fitToScreen(item) {
        const sub = item.menu;
        const open = sub.open.bind(sub);
        sub._needsScrollbar = () => true;
        sub.connect('open-state-changed', (_m, isOpen) => {
            if (!isOpen && this._picking === item) {
                this._picking = null;
                this.sync();
            }
        });
        sub.open = params => {
            this._picking = item;
            this.sync();
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            const [, top] = this.menu.actor.get_transformed_position();
            const [, rest] = this.menu.box.get_preferred_height(-1);
            const monitor = Main.layoutManager.findIndexForActor(this.menu.actor);
            const work = Main.layoutManager.getWorkAreaForMonitor(monitor);
            const spare = work.y + work.height - top - rest - 24 * scale;
            sub.actor.style = `max-height: ${Math.max(96 * scale, spare) / scale}px;`;
            return open(params);
        };
    }

    _set(key, value) {
        this._settings.set_string(key, value);
        this._settings.set_boolean('enabled', true);
    }

    _pickColor(key, hex) {
        if (key === 'color' && !USES_COLOR.has(this._settings.get_string('mode')))
            this._settings.set_string('mode', 'solid');
        this._set(key, hex);
    }

    sync() {
        const s = this._settings;
        const mode = s.get_string('mode');
        const status = s.get_string('status');
        this.visible = status !== 'no-device';
        this._setupItem.visible = status === 'no-access';
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
        if (this._picking) {
            for (const row of [this._modeMenu, this._color, this._color2,
                this._brightness, this._speed, this._direction]) {
                if (row !== this._picking)
                    row.visible = false;
            }
        }
        this._syncing = false;
        this.subtitle = STATUS[status] ?? modeLabel;
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
    constructor(settings, ext) {
        super();
        this.quickSettingsItems.push(new LightingToggle(settings, ext));
    }

    destroy() {
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});

export default class LamplightExtension extends Extension {
    enable() {
        this._packaged = !!Gio.SettingsSchemaSource.get_default().lookup(SCHEMA, true);
        this._settings = this._packaged
            ? new Gio.Settings({schema_id: SCHEMA}) : this.getSettings();
        this._indicator = new Indicator(this._settings, this);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
        this._restarts = 0;
        if (!this._packaged)
            this._startService();
    }

    disable() {
        if (this._restartId)
            GLib.source_remove(this._restartId);
        this._restartId = null;
        const service = this._service;
        this._service = null;
        // SIGTERM: the service exits, keeping the colors. When this runs because
        // the screen locked, it turns the lights off first if the user chose
        // "turn off with screen".
        service?.send_signal(15);
        this._indicator?.destroy();
        this._indicator = null;
        this._settings = null;
    }

    _script() {
        const bundled = GLib.build_filenamev([this.path, 'lamplight.py']);
        return GLib.file_test(bundled, GLib.FileTest.EXISTS)
            ? [GLib.find_program_in_path('python3'), bundled]
            : [GLib.find_program_in_path('lamplight')];
    }

    _startService() {
        const argv = this._script();
        if (argv.includes(null)) {
            this._settings.set_string('status', 'no-python');
            return;
        }
        const launcher = new Gio.SubprocessLauncher({
            flags: Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE,
        });
        launcher.setenv('GSETTINGS_SCHEMA_DIR', GLib.build_filenamev([this.path, 'schemas']), true);
        const proc = launcher.spawnv([...argv, 'service']);
        this._service = proc;
        proc.communicate_utf8_async(null, null, (p, res) => {
            let stderr = '';
            try {
                [, , stderr] = p.communicate_utf8_finish(res);
            } catch {}
            if (this._service !== p)
                return;  // stopped by disable()
            this._service = null;
            console.warn(`lamplight: service exited: ${stderr}`);
            if (stderr?.includes('PyGObject')) {
                this._settings.set_string('status', 'no-python');
                return;
            }
            if (this._restarts++ < 5) {
                this._restartId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 3, () => {
                    this._restartId = null;
                    this._startService();
                    return GLib.SOURCE_REMOVE;
                });
            }
        });
    }

    // One-time root step, only on explicit user action: allow the
    // logged-in user to use the LampArray devices present now. Static rules
    // only; nothing runs as root afterwards.
    setupAccess() {
        const argv = this._script();
        if (argv.includes(null))
            return;
        const rules = Gio.Subprocess.new([...argv, 'udev-rules'],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        rules.communicate_utf8_async(null, null, (p, res) => {
            const [, out] = p.communicate_utf8_finish(res);
            const ids = [...(out ?? '').matchAll(/KERNELS=="([0-9A-F:]+)\.\*"/g)].map(m => m[1]);
            if (!p.get_successful() || !ids.length)
                return;
            const install = Gio.Subprocess.new(
                ['pkexec', '/bin/sh', '-c', INSTALL_RULES, 'sh', ...ids], Gio.SubprocessFlags.NONE);
            install.wait_check_async(null, (q, r) => {
                try {
                    q.wait_check_finish(r);
                } catch {
                    return;  // cancelled password prompt: nothing to do
                }
                Main.notify('Lighting', 'Keyboard access is set up.');
                this._restarts = 0;
                this._service?.send_signal(15);  // restart the bundled service to pick up access
            });
        });
    }
}
