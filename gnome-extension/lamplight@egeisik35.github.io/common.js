// Shared by extension.js (GNOME Shell) and prefs.js (settings window).
// Only Gio/GLib here: no Shell or Gtk imports, so both processes can load it.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

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

// The backend: the copy bundled with the extension (store install), or the
// lamplight package. It is Python because it needs ioctl() on /dev/hidraw
// (HID feature reports), which GJS cannot call.
export function backendCommand(extensionPath) {
    const bundled = GLib.build_filenamev([extensionPath, 'lamplight.py']);
    return GLib.file_test(bundled, GLib.FileTest.EXISTS)
        ? [GLib.find_program_in_path('python3'), bundled]
        : [GLib.find_program_in_path('lamplight')];
}

// One-time root step, only on explicit user action: allow the logged-in
// user to use the LampArray devices present now. Static rules only; nothing
// runs as root afterwards. onDone runs after a successful setup.
export function setupAccess(extensionPath, onDone) {
    const argv = backendCommand(extensionPath);
    if (argv.includes(null))
        return;
    const rules = Gio.Subprocess.new([...argv, 'udev-rules'],
        Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
    rules.communicate_utf8_async(null, null, (p, res) => {
        const [, out] = p.communicate_utf8_finish(res);
        const ids = [...(out ?? '').matchAll(/KERNELS=="([0-9A-F:]+)\.\*"/g)].map(m => m[1]);
        if (!p.get_successful() || !ids.length)
            return;
        const install = Gio.Subprocess.new(
            ['pkexec', '/bin/sh', '-c', INSTALL_RULES, 'sh', ...ids], Gio.SubprocessFlags.NONE);
        install.wait_check_async(null, (q, r) => {
            try {
                q.wait_check_finish(r);
            } catch {
                return;  // cancelled password prompt: nothing to do
            }
            onDone();
        });
    });
}
