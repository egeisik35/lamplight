# Contributing

Thanks for helping. Lamplight is small on purpose: one Python file for the
hardware, one for the app, and one JavaScript file for the GNOME tile.

## Device reports

The most useful contribution is telling us whether lamplight works on your
hardware. Open a
[device report](https://github.com/egeisik35/lamplight/issues/new?template=device-report.yml)
with the output of `lamplight info`, even if nothing works.

If your device is not detected, attach its report descriptor:

```sh
for h in /sys/class/hidraw/*; do echo "$h: $(grep HID_NAME $h/device/uevent)"; done
xxd -p /sys/class/hidraw/hidrawN/device/report_descriptor
```

## Code

- Run `make test` before sending a pull request. Tests need only Python.
- The backend (`lamplight.py`) uses only the standard library, except for the
  `service` command, which uses PyGObject.
- Keep new effects as pure functions in `render()`, and add a test.
- To try the GNOME tile, run `make install-extension`, then log out and back in.
- Only add test fixtures you are allowed to share, and note their source and
  license in `tests/THIRD_PARTY.md`.

## Releases

1. Bump `__version__` in `lamplight.py`, `pkgver` in `PKGBUILD`, and add a
   `<release>` to `data/io.github.egeisik35.Lamplight.metainfo.xml`.
2. Tag `vX.Y.Z`, then update `sha256sums` in `PKGBUILD` and regenerate
   `.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO`.
