# Maintainer: egeisik35 <egeisik3534@gmail.com>
pkgname=lamplight
pkgver=0.2.1
pkgrel=1
pkgdesc="Keyboard RGB lighting for Windows Dynamic Lighting (HID LampArray) devices: service, GTK app and GNOME tile"
arch=('any')
url="https://github.com/egeisik35/lamplight"
license=('MIT')
depends=('python' 'python-gobject' 'python-cairo' 'gtk4' 'libadwaita')
makedepends=('make')
optdepends=('gnome-shell: Quick Settings tile')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('5984d62159f7caea3c2b186e9b23dfe145278c9ab23d7eab1dd5c558478a7884')

check() {
    cd "$pkgname-$pkgver"
    make test
}

package() {
    cd "$pkgname-$pkgver"
    make DESTDIR="$pkgdir" install
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
