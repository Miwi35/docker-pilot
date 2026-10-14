"""Keep a tray menu up to date in place, never rebuilt.

Replacing an indicator's whole menu (`set_menu(Gtk.Menu())`) makes the panel menu blink while open, and GNOME's
AppIndicator extension rebuilds its own copy each time without freeing all of the old one: gnome-shell grows by
the hour. Here the menu is described as a list of `Entry`: when only labels or states change, `show` edits the
existing items (property updates, no blink); the menu is built anew only when rows appear or go.

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
    """Shows entries in the indicator's menu. Same shape as the menu shown (same rows, same kinds, same submenus):
    only labels, states and actions change, as property updates on the existing items. Another shape: a new menu,
    built whole, then the old one destroyed. GNOME's AppIndicator extension handles rows added or removed one by
    one badly (its submenus stop opening), so the structure only ever changes all at once."""

    def __init__(self, gtk, set_menu):
        self.gtk = gtk
        self._set_menu = set_menu
        self.menu = None
        self._shape = None
        self._action: dict = {}
        self._syncing = False

    def show(self, entries: list[Entry]) -> None:
        shape = _shape(entries)
        self._syncing = True
        try:
            if self.menu is not None and shape == self._shape:
                self._update_all(self.menu, entries)
                return
            old, self._action = self.menu, {}
            self.menu = self._build(entries)
            self.menu.show_all()
            self._shape = shape
            self._set_menu(self.menu)
            if old is not None:
                old.destroy()
        finally:
            self._syncing = False

    def _build(self, entries):
        menu = self.gtk.Menu()
        for entry in entries:
            menu.append(self._create(entry))
        return menu

    def _create(self, entry):
        gtk = self.gtk
        if entry.separator:
            return gtk.SeparatorMenuItem()
        if entry.check:
            item = gtk.CheckMenuItem(label=entry.label)
            item.set_active(entry.active)
            item.connect("toggled", self._fire)
        else:
            item = gtk.MenuItem(label=entry.label)
            item.connect("activate", self._fire)
        item.set_sensitive(entry.sensitive)
        self._action[item] = entry.action
        if entry.children is not None:
            item.set_submenu(self._build(entry.children))
        return item

    def _update_all(self, menu, entries):
        for item, entry in zip(menu.get_children(), entries):
            if entry.separator:
                continue
            if item.get_label() != entry.label:
                item.set_label(entry.label)
            if item.get_sensitive() != entry.sensitive:
                item.set_sensitive(entry.sensitive)
            if entry.check and item.get_active() != entry.active:
                item.set_active(entry.active)  # `_syncing` keeps this from firing the action
            self._action[item] = entry.action
            if entry.children is not None:
                self._update_all(item.get_submenu(), entry.children)

    def _fire(self, item):
        if self._syncing:
            return
        action = self._action.get(item)
        if action is not None:
            action(item)


def _shape(entries):
    return tuple((e.kind, _shape(e.children) if e.children is not None else None) for e in entries)
