import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from menu_sync import SEPARATOR, Entry, MenuSync, disabled  # noqa: E402


class FakeItem:
    created = 0

    def __init__(self, label="", check=False):
        FakeItem.created += 1
        self.label, self.check, self.sensitive, self.active = label, check, True, False
        self.submenu, self.handlers, self.destroyed = None, {}, False

    def connect(self, signal, fn):
        self.handlers[signal] = fn

    def show(self):
        pass

    def get_label(self):
        return self.label

    def set_label(self, label):
        self.label = label

    def get_sensitive(self):
        return self.sensitive

    def set_sensitive(self, value):
        self.sensitive = value

    def get_active(self):
        return self.active

    def set_active(self, value):
        self.active = value
        if "toggled" in self.handlers:
            self.handlers["toggled"](self)

    def get_submenu(self):
        return self.submenu

    def set_submenu(self, menu):
        self.submenu = menu

    def destroy(self):
        self.destroyed = True

    def click(self):
        self.handlers["toggled" if self.check else "activate"](self)


class FakeMenu:
    def __init__(self):
        self.children, self.destroyed = [], False

    def append(self, item):
        self.children.append(item)

    def show_all(self):
        pass

    def destroy(self):
        self.destroyed = True

    def get_children(self):
        return list(self.children)

    def insert(self, item, pos):
        self.children.insert(pos, item)

    def remove(self, item):
        self.children.remove(item)


class FakeGtk:
    Menu = FakeMenu

    @staticmethod
    def MenuItem(label=""):
        return FakeItem(label)

    @staticmethod
    def CheckMenuItem(label=""):
        return FakeItem(label, check=True)

    @staticmethod
    def SeparatorMenuItem():
        return FakeItem()


def labels(menu):
    return [c.label for c in menu.children]


class MenuSyncTest(unittest.TestCase):
    def setUp(self):
        self.shown = []
        self.sync = MenuSync(FakeGtk, self.shown.append)

    @property
    def menu(self):
        return self.shown[-1]

    def test_a_changed_label_updates_the_same_item(self):
        self.sync.show([disabled("dev 1/2"), SEPARATOR, Entry("Quit")])
        before = list(self.menu.children)
        made = FakeItem.created
        self.sync.show([disabled("dev 2/2"), SEPARATOR, Entry("Quit")])
        self.assertEqual(len(self.shown), 1)
        self.assertEqual(self.menu.children, before)
        self.assertEqual(FakeItem.created, made)
        self.assertEqual(labels(self.menu)[0], "dev 2/2")

    def test_a_row_added_or_gone_builds_a_new_menu_and_destroys_the_old(self):
        self.sync.show([disabled("a")])
        first = self.menu
        self.sync.show([disabled("a"), disabled("b")])
        self.assertEqual(len(self.shown), 2)
        self.assertIsNot(self.menu, first)
        self.assertTrue(first.destroyed)
        self.assertEqual(labels(self.menu), ["a", "b"])

    def test_a_submenu_change_of_shape_rebuilds_but_a_label_does_not(self):
        self.sync.show([Entry("MAG-1", children=[disabled("x"), disabled("y")])])
        self.sync.show([Entry("MAG-1", children=[disabled("x"), disabled("z")])])
        self.assertEqual(len(self.shown), 1)
        self.assertEqual(labels(self.menu.children[0].submenu), ["x", "z"])
        self.sync.show([Entry("MAG-1", children=[disabled("x")])])
        self.assertEqual(len(self.shown), 2)

    def test_the_click_runs_the_latest_action(self):
        seen = []
        self.sync.show([Entry("open", action=lambda _i: seen.append("old"))])
        self.sync.show([Entry("open", action=lambda _i: seen.append("new"))])
        self.menu.children[0].click()
        self.assertEqual(seen, ["new"])

    def test_syncing_a_check_item_does_not_fire_it(self):
        seen = []
        self.sync.show([Entry("auto", check=True, active=False, action=lambda i: seen.append(i.active))])
        self.sync.show([Entry("auto", check=True, active=True, action=lambda i: seen.append(i.active))])
        self.assertEqual(seen, [])
        self.assertTrue(self.menu.children[0].active)
        self.menu.children[0].set_active(False)  # the owner unticks it
        self.assertEqual(seen, [False])

    def test_a_rebuild_forgets_the_old_items(self):
        self.sync.show([Entry("t", children=[Entry("open", action=print)])])
        self.sync.show([])
        self.assertEqual(self.sync._action, {})


if __name__ == "__main__":
    unittest.main()
