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
        self.children = []

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
        self.sync = MenuSync(FakeGtk)
        self.menu = FakeMenu()

    def test_a_changed_label_updates_the_same_item(self):
        self.sync.sync(self.menu, [disabled("dev 1/2"), SEPARATOR, Entry("Quit")])
        before = list(self.menu.children)
        made = FakeItem.created
        self.sync.sync(self.menu, [disabled("dev 2/2"), SEPARATOR, Entry("Quit")])
        self.assertEqual(self.menu.children, before)
        self.assertEqual(FakeItem.created, made)
        self.assertEqual(labels(self.menu)[0], "dev 2/2")

    def test_rows_added_and_removed_at_the_end_touch_nothing_else(self):
        self.sync.sync(self.menu, [disabled("a"), disabled("b")])
        first = self.menu.children[0]
        self.sync.sync(self.menu, [disabled("a"), disabled("b"), disabled("c")])
        self.assertIs(self.menu.children[0], first)
        removed = self.menu.children[2]
        self.sync.sync(self.menu, [disabled("a")])
        self.assertEqual(labels(self.menu), ["a"])
        self.assertTrue(removed.destroyed)
        self.assertIs(self.menu.children[0], first)

    def test_a_row_of_another_kind_is_replaced(self):
        self.sync.sync(self.menu, [disabled("a"), Entry("b")])
        self.sync.sync(self.menu, [disabled("a"), SEPARATOR])
        self.assertEqual(len(self.menu.children), 2)
        self.assertEqual(self.sync._kind[self.menu.children[1]], "sep")

    def test_a_submenu_is_kept_and_updated_in_place(self):
        self.sync.sync(self.menu, [Entry("MAG-1", children=[disabled("x"), disabled("y")])])
        sub = self.menu.children[0].submenu
        self.sync.sync(self.menu, [Entry("MAG-1", children=[disabled("x"), disabled("z")])])
        self.assertIs(self.menu.children[0].submenu, sub)
        self.assertEqual(labels(sub), ["x", "z"])

    def test_the_click_runs_the_latest_action(self):
        seen = []
        self.sync.sync(self.menu, [Entry("open", action=lambda _i: seen.append("old"))])
        self.sync.sync(self.menu, [Entry("open", action=lambda _i: seen.append("new"))])
        self.menu.children[0].click()
        self.assertEqual(seen, ["new"])

    def test_syncing_a_check_item_does_not_fire_it(self):
        seen = []
        self.sync.sync(self.menu, [Entry("auto", check=True, active=False, action=lambda i: seen.append(i.active))])
        self.sync.sync(self.menu, [Entry("auto", check=True, active=True, action=lambda i: seen.append(i.active))])
        self.assertEqual(seen, [])
        self.menu.children[0].set_active(False)  # the owner unticks it
        self.assertEqual(seen, [False])

    def test_dropped_items_are_forgotten(self):
        self.sync.sync(self.menu, [Entry("t", children=[Entry("open", action=print)])])
        self.sync.sync(self.menu, [])
        self.assertEqual(self.sync._kind, {})
        self.assertEqual(self.sync._action, {})


if __name__ == "__main__":
    unittest.main()
