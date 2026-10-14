# Lamplight

**Keyboard RGB lighting on Linux for laptops that use Windows Dynamic Lighting.**

[![test](https://github.com/egeisik35/lamplight/actions/workflows/test.yml/badge.svg)](https://github.com/egeisik35/lamplight/actions/workflows/test.yml)
![license](https://img.shields.io/badge/license-MIT-blue)

On Windows, some laptops let you set the keyboard color under
*Settings > Personalization > Dynamic Lighting* or in MyASUS. On Linux, the
keyboard stays on whatever color the firmware chose. Lamplight brings that
control to Linux. It has a command-line tool and a GNOME Quick Settings tile.

These keyboards speak **HID LampArray**, the open USB HID standard behind
Dynamic Lighting. ROG-focused tools such as asusctl do not handle it.
Lamplight works with any LampArray device: it reads the report layout, lamp
count and lamp positions from the device itself, with no per-model table.

- Effects: solid, match GNOME accent color, breathing, color cycle, rainbow,
  wave, wheel, gradient, firmware effect, off
- Brightness, speed and direction controls
- Restores your lighting after suspend
- No root needed at runtime; a udev rule grants access only to LampArray devices
- One Python file, standard library only

## Supported hardware

**Confirmed:**

| Device | Controller | Lamps |
|--------|-----------|-------|
| ASUS Vivobook S 14 M5406KA | ITE5570 (I2C) | 1 (single zone) |

**Likely:** any device whose lighting works with Windows Dynamic Lighting.
This includes other ASUS Vivobook S models with the ITE5570 controller.

To check your device:

```sh
lamplight info
```

If it lists a device, it is supported. Please
[open a device report](https://github.com/egeisik35/lamplight/issues/new?template=device-report.yml),
whatever the result, so this table can grow.

Position-based effects (rainbow, wave, wheel, gradient) need a per-key
keyboard. On a single-zone keyboard, they color the whole keyboard at once.

## Install

### Arch Linux

```sh
git clone https://github.com/egeisik35/lamplight.git
cd lamplight
makepkg -si
```

### Other distributions

You need Python 3.9 or newer.

```sh
git clone https://github.com/egeisik35/lamplight.git
cd lamplight
sudo make install
```

`make install` installs the CLI, the udev rule and the GNOME extension. It
then reloads udev. To remove everything, run `sudo make uninstall`.

### GNOME tile

Log out and back in once, so GNOME Shell finds the extension. Then turn on
**Lamplight** in the Extensions app. A **Keyboard Light** tile appears in
Quick Settings.

The custom color picker needs `zenity`.

## Command line

```sh
lamplight info                              # list devices
lamplight solid -c ff5500 -b 60             # orange at 60 % brightness
lamplight breathe -c 00ffff -s 5            # cyan, 5 s per breath
lamplight wave -c ff0000 -c 0000ff -r       # red over blue, reversed
lamplight firmware                          # hand back to the built-in effect
lamplight off
```

| Option | Meaning |
|--------|---------|
| `-c RRGGBB` | color; give it twice to set the second color |
| `-b 0-100` | brightness in percent |
| `-s SECONDS` | effect period; lower is faster |
| `-r` | reverse direction |

Animated modes run until you press Ctrl+C.

## Troubleshooting

**`Permission denied`**: the udev rule did not apply. Run:

```sh
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw
```

**`no usable HID LampArray device found`**: your device does not expose a
LampArray. Please open a device report with the output of
`grep -H HID_NAME /sys/class/hidraw/*/device/uevent`.

**Tile says "lamplight backend not installed"**: `lamplight` is not in the
`PATH` of GNOME Shell. Install with `make install` or the package.

## How it works

Lamplight talks to `/dev/hidraw*` with HID feature reports. It uses the
LampArray reports from the HID Usage Tables (Lighting and Illumination page,
`0x59`):

| Report | Use |
|--------|-----|
| LampArrayAttributes | lamp count, size, kind, minimum update interval |
| LampAttributesRequest / Response | position of each lamp |
| LampMultiUpdate | per-lamp colors, sent in chunks |
| LampRangeUpdate | one color for all lamps |
| LampArrayControl | firmware (autonomous) mode on or off |

`lamplight daemon` reads one JSON state per line on stdin and animates until
stdin closes. The GNOME extension uses it like this:

```json
{"mode": "wave", "colors": ["ff0000", "0000ff"], "brightness": 0.8, "speed": 4, "reverse": false}
```

### Permissions

The udev rule runs `lamplight probe` on each hidraw device. Only devices with
a LampArray get the `uaccess` tag, which gives the logged-in user access. The
rule is numbered 70 because `uaccess` must come before
`73-seat-late.rules`.

hidraw access also lets the user read that device's input reports. On laptop
lighting controllers, these are not keystrokes. A mouse that has its buttons
and LEDs on the same interface would expose its button reports.

## Development

```sh
make test     # unit tests, including a real ITE5570 descriptor
make zip      # GNOME extension zip for extensions.gnome.org
```

## Related projects

- [vivobook-rgb](https://github.com/matcraft94/vivobook-rgb): single-color CLI
  for the ITE5570 controller. It confirmed that this controller accepts
  LampArray range updates on Linux.
- Linux kernel: a proposed `hid-lamparray` helper exposes LampArray devices as
  a single-color LED. Per-key control is meant to stay in user space over
  hidraw, which is the approach Lamplight takes.

## License

MIT
