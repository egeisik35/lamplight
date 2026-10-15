# Maintainer: egeisik35 <egeisik3534@gmail.com>
pkgname=lamplight
pkgver=0.3.0
pkgrel=1
pkgdesc="Keyboard RGB lighting for Windows Dynamic Lighting (HID LampArray) devices: service, CLI and GNOME extension"
arch=('any')
url="https://github.com/egeisik35/lamplight"
license=('MIT')
depends=('python' 'python-gobject')
makedepends=('make')
optdepends=('gnome-shell: Quick Settings tile')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('d30c3b34b3a489bd795b0246e2c12fa741ffb625f6f506a682256ee712ce807b')

check() {
    cd "$pkgname-$pkgver"
    make test
}

package() {
    cd "$pkgname-$pkgver"
    make DESTDIR="$pkgdir" install
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
