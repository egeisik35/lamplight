# Lamplight

RGB lighting control on Linux for **HID LampArray** devices: the open HID
standard behind Windows 11 "Dynamic Lighting". It includes a GNOME Quick
Settings tile.

Many recent laptops expose their keyboard backlight as a LampArray, for
example ASUS Vivobook S 14/16 (ITE5570 controller). Some mice and keyboards
do too. Tools like asusctl and OpenRGB do not handle these devices, so on
Linux the color stays stuck at whatever the firmware picked.

Lamplight is vendor-neutral. It finds devices by parsing each hidraw report
descriptor for a LampArray collection (usage page `0x59`). Report IDs, report
sizes, lamp count and lamp positions all come from the device itself.

## Effects

| Mode | Notes |
|------|-------|
| solid | one color |
| accent (GNOME tile) | follows the GNOME accent color, boosted for LEDs |
| breathe | fades between color 1 and color 2 (default black) |
| cycle | all lamps step through the color wheel |
| rainbow | hue spread across the keyboard, moving |
| wave | band of color 1 sweeping over color 2 |
| wheel | hues rotating around the keyboard center |
| gradient | static color 1 to color 2, left to right |
| firmware | hand control back to the device's built-in effect |
| off | all lamps off |

Brightness, speed and direction apply to every mode where they make sense.
Position-based effects (rainbow, wave, wheel, gradient) need a per-key
device. On a single-zone keyboard, they animate the whole keyboard as one
color.

## Install

Arch: run `makepkg -si` in this directory. This installs:

- `/usr/bin/lamplight` (Python 3, stdlib only)
- `/usr/lib/udev/rules.d/70-lamplight.rules`
- the GNOME extension and its settings schema

Then:

```sh
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw
```

Log out and back in once, so GNOME Shell discovers the new extension. Then
enable it in the Extensions app.

### Permissions

The udev rule runs `lamplight probe` on each hidraw device. It tags only real
LampArray devices with `uaccess`, so only the user at the seat gets access,
without root. The rule must sort before `73-seat-late.rules`, so a
`99-*.rules` name does not work for `uaccess`.

Note: hidraw access also lets the user read that device's input reports. For
keyboard backlight controllers this is lighting state, not keystrokes. A
composite device (for example a mouse with buttons and LEDs on one
interface) would expose its button reports too.

## CLI

```sh
lamplight info                          # list LampArray devices
lamplight solid -c ff5500 -b 60         # orange, 60 % brightness
lamplight breathe -c 00ffff -c 000000 -s 5
lamplight wave -c ff0000 -c 0000ff -r
lamplight firmware                      # back to built-in effect
lamplight off
```

Animated modes run in the foreground until Ctrl+C.

`lamplight daemon` reads one JSON state per line on stdin and animates until
stdin closes. The GNOME extension uses this protocol:

```json
{"mode": "wave", "colors": ["ff0000", "0000ff"], "brightness": 0.8, "speed": 4, "reverse": false}
```

## How it works

LampArray reports used (HID Usage Tables, Lighting and Illumination page):

- `LampArrayAttributesReport`: lamp count, bounding box, kind, minimum update interval
- `LampAttributesRequest` / `Response`: position of each lamp
- `LampMultiUpdateReport`: per-lamp colors, chunked; the last chunk sets `LampUpdateComplete`
- `LampRangeUpdateReport`: one color for a range of lamps, used when all lamps share a color
- `LampArrayControlReport`: autonomous (firmware) mode on/off

Every new state re-sends autonomous mode off, because some controllers return
to their own effect after suspend. The GNOME extension also re-sends the
state on resume.

## Tests

```sh
python -I tests/test_lamplight.py
```

`tests/ite5570.desc` is the real report descriptor of an ASUS Vivobook S 14
(M5406KA). It contains a standard LampArray plus a vendor mirror on page
`0xFF59`, which lamplight ignores.

## Tested hardware

| Device | Controller | Lamps |
|--------|-----------|-------|
| ASUS Vivobook S 14 M5406KA | ITE5570 (I2C) | 1 (single zone) |

Status: early (0.1.0). Descriptor parsing is verified against this device; on-device effect testing is in progress.

Please open an issue or PR with `lamplight info` output for your device.

## Prior art

- [vivobook-rgb](https://github.com/matcraft94/vivobook-rgb) (MIT): first
  Linux tool for the ITE5570; single-zone, ASUS-only.
- Kernel work: a `hid-lamparray` helper for hid-generic (single-zone LED class
  device) and TUXEDO's virtual LampArray driver. Complex control is expected to
  stay in userspace via hidraw, which is what lamplight does.
