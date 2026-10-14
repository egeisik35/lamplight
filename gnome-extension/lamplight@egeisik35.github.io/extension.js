// Lamplight: Quick Settings tile for HID LampArray (Dynamic Lighting) devices.
// The lamplight service drives the hardware from the shared
// io.github.egeisik35.Lamplight settings; this tile only edits them.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

const SCHEMA = 'io.github.egeisik35.Lamplight';
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
    'no-access': 'No permission (see README)',
};

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
    constructor(settings) {
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

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addSettingsAction('Lighting Settings', APP_DESKTOP);

        settings.connectObject('changed', () => this.sync(), this);
        this.sync();
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
    constructor(settings) {
        super();
        this.quickSettingsItems.push(new LightingToggle(settings));
    }

    destroy() {
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});

export default class LamplightExtension extends Extension {
    enable() {
        // The schema ships with the lamplight package, not with the extension.
        if (!Gio.SettingsSchemaSource.get_default().lookup(SCHEMA, true)) {
            console.warn('lamplight: package not installed; see https://github.com/egeisik35/lamplight');
            return;
        }
        this._settings = new Gio.Settings({schema_id: SCHEMA});
        this._indicator = new Indicator(this._settings);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
        this._settings = null;
    }
}
