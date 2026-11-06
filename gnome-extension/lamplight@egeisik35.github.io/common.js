// Shared by extension.js (GNOME Shell) and prefs.js (settings window).
// Only Gio here: no Shell or Gtk imports, so both processes can load it.

import Gio from 'gi://Gio';

export const RULES_FILE = '/etc/udev/rules.d/70-lamplight-extension.rules';

// Runs as root through pkexec. Takes only HID IDs as arguments, rejects
// anything that is not BUS:VENDOR:PRODUCT hex, and writes a fixed rule
// shape, so no user-controlled file or text ends up in the rule.
const INSTALL_RULES = `
set -e
f=${RULES_FILE}
echo '# Lamplight: let the logged-in user control keyboard lighting' > "$f.tmp"
for id in "$@"; do
    case "$id" in
    [0-9A-F][0-9A-F][0-9A-F][0-9A-F]:[0-9A-F][0-9A-F][0-9A-F][0-9A-F]:[0-9A-F][0-9A-F][0-9A-F][0-9A-F]) ;;
    *) rm -f "$f.tmp"; exit 2 ;;
    esac
    echo "SUBSYSTEM==\\"hidraw\\", KERNELS==\\"$id.*\\", TAG+=\\"uaccess\\"" >> "$f.tmp"
done
chmod 644 "$f.tmp"
mv "$f.tmp" "$f"
udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw
udevadm settle`;

// The backend is the lamplight service (pip package lamplight-hid, or the
// distro package) on the session bus. It is Python because it needs ioctl()
// on /dev/hidraw (HID feature reports), which GJS cannot call. Any call
// starts it through D-Bus activation.
const BUS_NAME = 'io.github.egeisik35.Lamplight';
const BUS_PATH = '/io/github/egeisik35/Lamplight';

export function callBackend(method, onDone = () => {}) {
    Gio.DBus.session.call(BUS_NAME, BUS_PATH, BUS_NAME, method, null, null,
        Gio.DBusCallFlags.NONE, -1, null, (conn, res) => {
            let reply = null;
            try {
                reply = conn.call_finish(res);
            } catch (e) {
                console.warn(`lamplight: ${method}: ${e.message}`);
            }
            onDone(reply);
        });
}

// Stop the service if it runs; do not start it just to stop it.
export function stopBackend() {
    Gio.DBus.session.call(BUS_NAME, BUS_PATH, BUS_NAME, 'Quit', null, null,
        Gio.DBusCallFlags.NO_AUTO_START, -1, null, (conn, res) => {
            try {
                conn.call_finish(res);
            } catch {
                // not running: nothing to stop
            }
        });
}

// One-time root step, only on explicit user action: allow the logged-in
// user to use the LampArray devices present now. Static rules only; nothing
// runs as root afterwards. onDone runs after a successful setup.
export function setupAccess(onDone) {
    callBackend('AccessIds', reply => {
        const [ids] = reply?.deepUnpack() ?? [[]];
        if (!ids.length)
            return;
        const install = Gio.Subprocess.new(
            ['pkexec', '/bin/sh', '-c', INSTALL_RULES, 'sh', ...ids], Gio.SubprocessFlags.NONE);
        install.wait_check_async(null, (q, r) => {
            try {
                q.wait_check_finish(r);
            } catch {
                return;  // cancelled password prompt: nothing to do
            }
            callBackend('Rescan');
            onDone();
        });
    });
}
