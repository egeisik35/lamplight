# Maintainer: egeisik35 <egeisik3534@gmail.com>
pkgname=lamplight
pkgver=0.1.1
pkgrel=1
pkgdesc="Keyboard RGB lighting for Windows Dynamic Lighting (HID LampArray) devices: service, GTK app and GNOME tile"
arch=('any')
url="https://github.com/egeisik35/lamplight"
license=('MIT')
depends=('python' 'python-gobject' 'python-cairo' 'gtk4' 'libadwaita')
makedepends=('make')
optdepends=('gnome-shell: Quick Settings tile')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('1b0d502b542988b992b54e84d6edbb172d446af108cadaad7cc831c123862c89')

check() {
    cd "$pkgname-$pkgver"
    make test
}

package() {
    cd "$pkgname-$pkgver"
    make DESTDIR="$pkgdir" install
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
