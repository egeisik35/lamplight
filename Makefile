PREFIX ?= /usr
BINDIR = $(PREFIX)/bin
DATADIR = $(PREFIX)/share
UDEVDIR ?= /usr/lib/udev/rules.d
USERUNITDIR ?= /usr/lib/systemd/user
SCHEMA = org.gnome.shell.extensions.lamplight
BUS_NAME = io.github.egeisik35.Lamplight
UUID = lamplight@egeisik35.github.io
EXT = gnome-extension/$(UUID)

# Pieces build on each other: cli < service < extension.
install: install-extension

install-cli:
	install -Dm755 -t $(DESTDIR)$(DATADIR)/lamplight lamplight.py
	install -d $(DESTDIR)$(BINDIR)
	ln -sf $(DATADIR)/lamplight/lamplight.py $(DESTDIR)$(BINDIR)/lamplight
	sed 's|/usr/bin/lamplight|$(BINDIR)/lamplight|' 70-lamplight.rules > 70-lamplight.rules.out
	install -Dm644 70-lamplight.rules.out $(DESTDIR)$(UDEVDIR)/70-lamplight.rules
	rm 70-lamplight.rules.out
	if [ -z "$(DESTDIR)" ]; then \
		udevadm control --reload-rules; \
		udevadm trigger --subsystem-match=hidraw; \
	fi

install-service: install-cli
	sed 's|@BINDIR@|$(BINDIR)|' data/lamplight.service > lamplight.service.out
	install -Dm644 lamplight.service.out $(DESTDIR)$(USERUNITDIR)/lamplight.service
	rm lamplight.service.out
	install -d $(DESTDIR)$(USERUNITDIR)/graphical-session.target.wants
	ln -sf ../lamplight.service $(DESTDIR)$(USERUNITDIR)/graphical-session.target.wants/lamplight.service
	sed 's|@BINDIR@|$(BINDIR)|' data/$(BUS_NAME).service > $(BUS_NAME).service.out
	install -Dm644 $(BUS_NAME).service.out $(DESTDIR)$(DATADIR)/dbus-1/services/$(BUS_NAME).service
	rm $(BUS_NAME).service.out
	install -Dm644 data/$(SCHEMA).gschema.xml $(DESTDIR)$(DATADIR)/glib-2.0/schemas/$(SCHEMA).gschema.xml
	if [ -z "$(DESTDIR)" ]; then glib-compile-schemas $(DATADIR)/glib-2.0/schemas; fi

install-extension: install-service
	install -Dm644 -t $(DESTDIR)$(DATADIR)/gnome-shell/extensions/$(UUID) \
		$(EXT)/extension.js $(EXT)/prefs.js $(EXT)/common.js $(EXT)/metadata.json $(EXT)/stylesheet.css

uninstall:
	rm -rf $(DESTDIR)$(DATADIR)/lamplight $(DESTDIR)$(DATADIR)/gnome-shell/extensions/$(UUID)
	rm -f $(DESTDIR)$(BINDIR)/lamplight \
		$(DESTDIR)$(UDEVDIR)/70-lamplight.rules \
		$(DESTDIR)$(USERUNITDIR)/lamplight.service \
		$(DESTDIR)$(USERUNITDIR)/graphical-session.target.wants/lamplight.service \
		$(DESTDIR)$(DATADIR)/dbus-1/services/$(BUS_NAME).service \
		$(DESTDIR)$(DATADIR)/glib-2.0/schemas/$(SCHEMA).gschema.xml

test:
	python3 -I tests/test_lamplight.py

# Store build: the extension and its schema only. The service comes from
# pip (lamplight-hid) or a package.
zip:
	gnome-extensions pack --force --extra-source=common.js \
		--schema=../../data/$(SCHEMA).gschema.xml $(EXT)

.PHONY: install install-cli install-service install-extension uninstall test zip
