# Maintainer: egeisik35 <egeisik3534@gmail.com>
# Local build: `makepkg -si` in this directory. For AUR, replace the local
# files below with a tagged source tarball.
pkgname=lamplight
pkgver=0.1.0
pkgrel=1
pkgdesc="Keyboard RGB lighting for Windows Dynamic Lighting (HID LampArray) devices: service, GTK app and GNOME tile"
arch=('any')
url="https://github.com/egeisik35/lamplight"
license=('MIT')
depends=('python' 'python-gobject' 'python-cairo' 'gtk4' 'libadwaita')
makedepends=('make')
optdepends=('gnome-shell: Quick Settings tile')

package() {
    cd "$startdir"
    make DESTDIR="$pkgdir" install
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
