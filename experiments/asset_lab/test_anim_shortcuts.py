import unittest
from anim_preview import TransportSpaceShortcut


class PopupFocusTests(unittest.TestCase):
    def test_native_combobox_popup_focus_does_not_stop_preview(self):
        class Root:
            def focus_get(self):raise KeyError('popdown')
        shortcut=object.__new__(TransportSpaceShortcut)
        shortcut.root=Root();shortcut.held=True
        shortcut.focus_out(None)
        self.assertFalse(shortcut.held)


if __name__=='__main__':unittest.main()
