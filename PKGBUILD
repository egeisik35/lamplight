# Maintainer: egeisik35 <egeisik3534@gmail.com>
# Local build: `makepkg -si` in this directory. For AUR, replace the local
# files below with a tagged source tarball.
pkgname=lamplight
pkgver=0.1.0
pkgrel=1
pkgdesc="RGB lighting for HID LampArray (Windows Dynamic Lighting) keyboards and devices, with a GNOME Quick Settings tile"
arch=('any')
url="https://github.com/egeisik35/lamplight"
license=('MIT')
depends=('python')
optdepends=('gnome-shell: Quick Settings tile'
            'zenity: custom color picker in the GNOME tile')

package() {
    cd "$startdir"
    install -Dm755 lamplight.py "$pkgdir/usr/bin/lamplight"
    install -Dm644 70-lamplight.rules "$pkgdir/usr/lib/udev/rules.d/70-lamplight.rules"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"

    local ext="$pkgdir/usr/share/gnome-shell/extensions/lamplight@egeisik35.github.io"
    install -Dm644 -t "$ext" gnome-extension/lamplight@egeisik35.github.io/{extension.js,metadata.json,stylesheet.css}
    install -Dm644 gnome-extension/lamplight@egeisik35.github.io/schemas/org.gnome.shell.extensions.lamplight.gschema.xml \
        "$pkgdir/usr/share/glib-2.0/schemas/org.gnome.shell.extensions.lamplight.gschema.xml"
}
