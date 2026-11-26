"""Real Tk event regression tests; no audio device or user project involved."""
import tkinter as tk
from tkinter import ttk
import unittest
from loop_editor_shortcuts import TransportSpaceShortcut


class SpaceShortcutTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.geometry('380x180+15+15');self.calls=[]
        self.button=ttk.Button(self.root,text='test',command=lambda:self.calls.append('button'));self.button.pack()
        self.checkvar=tk.BooleanVar(value=False)
        self.check=ttk.Checkbutton(self.root,text='check',variable=self.checkvar,command=lambda:self.calls.append('check'));self.check.pack()
        self.radio=ttk.Radiobutton(self.root,text='radio',command=lambda:self.calls.append('radio'));self.radio.pack()
        self.entry=ttk.Entry(self.root);self.entry.pack()
        self.shortcut=TransportSpaceShortcut(self.root,lambda:self.calls.append('transport'))
        self.root.update()

    def tearDown(self):self.root.destroy()

    def focus(self,widget):widget.focus_force();self.root.update()
    def press(self,widget):widget.event_generate('<KeyPress-space>');self.root.update()
    def release(self,widget):widget.event_generate('<KeyRelease-space>');self.root.update()

    def test_button_checkbox_and_radio_only_control_transport(self):
        for widget in [self.button,self.check,self.radio]:
            with self.subTest(widget=widget.winfo_class()):
                self.calls.clear();self.focus(widget);self.press(widget);self.release(widget)
                self.assertEqual(self.calls,['transport'])
                self.assertFalse(self.checkvar.get())

    def test_entry_inserts_space_without_transport(self):
        self.focus(self.entry);self.entry.insert(0,'hello');self.press(self.entry);self.release(self.entry)
        self.assertEqual(self.entry.get(),'hello ');self.assertEqual(self.calls,[])

    def test_held_space_toggles_once_and_release_does_not_invoke_widget(self):
        self.button.bind('<KeyRelease-space>',lambda e:self.calls.append('button-release'))
        self.focus(self.button)
        for _ in range(4):self.press(self.button)
        self.release(self.button);self.assertEqual(self.calls,['transport'])
        self.press(self.button);self.release(self.button)
        self.assertEqual(self.calls,['transport','transport'])

    def test_widgets_created_later_receive_shortcut_priority(self):
        late=ttk.Button(self.root,text='late',command=lambda:self.calls.append('late'));late.pack();self.root.update()
        self.focus(late);self.press(late);self.release(late)
        self.assertEqual(self.calls,['transport'])


if __name__=='__main__':unittest.main()
