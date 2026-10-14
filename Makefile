PREFIX ?= /usr
BINDIR = $(PREFIX)/bin
DATADIR = $(PREFIX)/share
UDEVDIR ?= /usr/lib/udev/rules.d
USERUNITDIR ?= /usr/lib/systemd/user
APP = io.github.egeisik35.Lamplight
UUID = lamplight@egeisik35.github.io
EXT = gnome-extension/$(UUID)

install:
	install -Dm644 -t $(DESTDIR)$(DATADIR)/lamplight lamplight.py lamplight_app.py
	install -d $(DESTDIR)$(BINDIR)
	ln -sf $(DATADIR)/lamplight/lamplight.py $(DESTDIR)$(BINDIR)/lamplight
	ln -sf $(DATADIR)/lamplight/lamplight_app.py $(DESTDIR)$(BINDIR)/lamplight-app
	chmod 755 $(DESTDIR)$(DATADIR)/lamplight/lamplight.py $(DESTDIR)$(DATADIR)/lamplight/lamplight_app.py
	sed 's|/usr/bin/lamplight|$(BINDIR)/lamplight|' 70-lamplight.rules > 70-lamplight.rules.out
	install -Dm644 70-lamplight.rules.out $(DESTDIR)$(UDEVDIR)/70-lamplight.rules
	rm 70-lamplight.rules.out
	sed 's|@BINDIR@|$(BINDIR)|' data/lamplight.service > lamplight.service.out
	install -Dm644 lamplight.service.out $(DESTDIR)$(USERUNITDIR)/lamplight.service
	rm lamplight.service.out
	install -d $(DESTDIR)$(USERUNITDIR)/graphical-session.target.wants
	ln -sf ../lamplight.service $(DESTDIR)$(USERUNITDIR)/graphical-session.target.wants/lamplight.service
	install -Dm644 data/$(APP).gschema.xml $(DESTDIR)$(DATADIR)/glib-2.0/schemas/$(APP).gschema.xml
	install -Dm644 data/$(APP).desktop $(DESTDIR)$(DATADIR)/applications/$(APP).desktop
	install -Dm644 data/$(APP).svg $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP).svg
	install -Dm644 -t $(DESTDIR)$(DATADIR)/gnome-shell/extensions/$(UUID) \
		$(EXT)/extension.js $(EXT)/metadata.json $(EXT)/stylesheet.css
	if [ -z "$(DESTDIR)" ]; then \
		glib-compile-schemas $(DATADIR)/glib-2.0/schemas; \
		udevadm control --reload-rules; \
		udevadm trigger --subsystem-match=hidraw; \
	fi

uninstall:
	rm -rf $(DESTDIR)$(DATADIR)/lamplight $(DESTDIR)$(DATADIR)/gnome-shell/extensions/$(UUID)
	rm -f $(DESTDIR)$(BINDIR)/lamplight $(DESTDIR)$(BINDIR)/lamplight-app \
		$(DESTDIR)$(UDEVDIR)/70-lamplight.rules \
		$(DESTDIR)$(USERUNITDIR)/lamplight.service \
		$(DESTDIR)$(USERUNITDIR)/graphical-session.target.wants/lamplight.service \
		$(DESTDIR)$(DATADIR)/glib-2.0/schemas/$(APP).gschema.xml \
		$(DESTDIR)$(DATADIR)/applications/$(APP).desktop \
		$(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP).svg

test:
	python3 -I tests/test_lamplight.py

zip:
	gnome-extensions pack --force $(EXT)

.PHONY: install uninstall test zip
