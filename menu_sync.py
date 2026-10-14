"""Keep a tray menu up to date in place, never rebuilt.

Replacing an indicator's whole menu (`set_menu(Gtk.Menu())`) makes the panel menu blink while open, and GNOME's
AppIndicator extension rebuilds its own copy each time without freeing all of the old one: gnome-shell grows by
the hour. Here the menu is described as a list of `Entry`, and `sync` edits the existing items to match it: a
changed label or state is a property update, a row added or gone is one insert or removal. The Gtk menu given to
the indicator is created once and kept for the life of the process.

The same file lives in cyrus-pilot and docker-pilot; keep the two copies identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Entry:
    label: str = ""
    sensitive: bool = True
    action: Callable | None = None  # called with the Gtk item: activate, or toggled for a check item
    check: bool = False
    active: bool = False
    children: list["Entry"] | None = None
    separator: bool = False

    @property
    def kind(self) -> str:
        if self.separator:
            return "sep"
        return ("check" if self.check else "item") + ("+sub" if self.children is not None else "")


SEPARATOR = Entry(separator=True)


def disabled(label: str) -> Entry:
    return Entry(label=label, sensitive=False)


class MenuSync:
    """Owns the Gtk items it creates: their kind and their current action live here, keyed by item."""

    def __init__(self, gtk):
        self.gtk = gtk
        self._kind: dict = {}
        self._action: dict = {}
        self._syncing = False

    def sync(self, menu, entries: list[Entry]) -> None:
        self._syncing = True
        try:
            self._sync(menu, entries)
        finally:
            self._syncing = False

    def _sync(self, menu, entries):
        items = list(menu.get_children())
        for pos, entry in enumerate(entries):
            item = items[pos] if pos < len(items) else None
            if item is not None and self._kind.get(item) != entry.kind:
                self._drop(menu, item)
                item = None
            if item is None:
                item = self._create(entry)
                menu.insert(item, pos)
                items[pos:pos + 1] = [item]
            self._update(item, entry)
        for item in items[len(entries):]:
            self._drop(menu, item)

    def _create(self, entry):
        gtk = self.gtk
        if entry.separator:
            item = gtk.SeparatorMenuItem()
        elif entry.check:
            item = gtk.CheckMenuItem(label=entry.label)
            item.connect("toggled", self._fire)
        else:
            item = gtk.MenuItem(label=entry.label)
            item.connect("activate", self._fire)
        if entry.children is not None:
            item.set_submenu(gtk.Menu())
        self._kind[item] = entry.kind
        item.show()
        return item

    def _update(self, item, entry):
        if entry.separator:
            return
        if item.get_label() != entry.label:
            item.set_label(entry.label)
        if item.get_sensitive() != entry.sensitive:
            item.set_sensitive(entry.sensitive)
        if entry.check and item.get_active() != entry.active:
            item.set_active(entry.active)  # `_syncing` keeps this from firing the action
        self._action[item] = entry.action
        if entry.children is not None:
            sub = item.get_submenu()
            self._sync(sub, entry.children)

    def _drop(self, menu, item):
        sub = item.get_submenu() if self._kind.get(item, "").endswith("+sub") else None
        if sub is not None:
            for child in list(sub.get_children()):
                self._drop(sub, child)
        self._kind.pop(item, None)
        self._action.pop(item, None)
        menu.remove(item)
        item.destroy()

    def _fire(self, item):
        if self._syncing:
            return
        action = self._action.get(item)
        if action is not None:
            action(item)
