PREFIX ?= /usr
UDEVDIR ?= /usr/lib/udev/rules.d
UUID = lamplight@egeisik35.github.io
EXT = gnome-extension/$(UUID)

install:
	install -Dm755 lamplight.py $(DESTDIR)$(PREFIX)/bin/lamplight
	sed 's|/usr/bin/lamplight|$(PREFIX)/bin/lamplight|' 70-lamplight.rules > 70-lamplight.rules.out
	install -Dm644 70-lamplight.rules.out $(DESTDIR)$(UDEVDIR)/70-lamplight.rules
	rm 70-lamplight.rules.out
	install -Dm644 -t $(DESTDIR)$(PREFIX)/share/gnome-shell/extensions/$(UUID) \
		$(EXT)/extension.js $(EXT)/metadata.json $(EXT)/stylesheet.css
	install -Dm644 -t $(DESTDIR)$(PREFIX)/share/glib-2.0/schemas \
		$(EXT)/schemas/org.gnome.shell.extensions.lamplight.gschema.xml
	if [ -z "$(DESTDIR)" ]; then \
		glib-compile-schemas $(PREFIX)/share/glib-2.0/schemas; \
		udevadm control --reload-rules; \
		udevadm trigger --subsystem-match=hidraw; \
	fi

uninstall:
	rm -f $(DESTDIR)$(PREFIX)/bin/lamplight $(DESTDIR)$(UDEVDIR)/70-lamplight.rules \
		$(DESTDIR)$(PREFIX)/share/glib-2.0/schemas/org.gnome.shell.extensions.lamplight.gschema.xml
	rm -rf $(DESTDIR)$(PREFIX)/share/gnome-shell/extensions/$(UUID)

test:
	python3 -I tests/test_lamplight.py

zip:
	gnome-extensions pack --force $(EXT)

.PHONY: install uninstall test zip
