# Maintainer: egeisik35 <egeisik3534@gmail.com>
pkgname=lamplight
pkgver=0.4.0
pkgrel=1
pkgdesc="Keyboard RGB lighting for Windows Dynamic Lighting (HID LampArray) devices: service, CLI and GNOME extension"
arch=('any')
url="https://github.com/egeisik35/lamplight"
license=('MIT')
depends=('python' 'python-gobject')
makedepends=('make')
optdepends=('gnome-shell: Quick Settings tile')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('4de57faa8ebebbeb69179c61bbb8c6d541b50c5ca565e56e7bf7d3a18de7298a')

check() {
    cd "$pkgname-$pkgver"
    make test
}

package() {
    cd "$pkgname-$pkgver"
    make DESTDIR="$pkgdir" install
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
