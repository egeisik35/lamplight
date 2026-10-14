# Maintainer: egeisik35 <egeisik3534@gmail.com>
pkgname=lamplight
pkgver=0.1.3
pkgrel=1
pkgdesc="Keyboard RGB lighting for Windows Dynamic Lighting (HID LampArray) devices: service, GTK app and GNOME tile"
arch=('any')
url="https://github.com/egeisik35/lamplight"
license=('MIT')
depends=('python' 'python-gobject' 'python-cairo' 'gtk4' 'libadwaita')
makedepends=('make')
optdepends=('gnome-shell: Quick Settings tile')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('e024fa1d0724439c0df60ef840e7e8e759c64b290d4fad7636ae67635c5512fa')

check() {
    cd "$pkgname-$pkgver"
    make test
}

package() {
    cd "$pkgname-$pkgver"
    make DESTDIR="$pkgdir" install
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
