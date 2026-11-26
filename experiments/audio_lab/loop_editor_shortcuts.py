"""Consume transport keys before Tk widget/class bindings can activate controls."""
import sys
import tkinter as tk


class TransportSpaceShortcut:
    TEXT_CLASSES={'Entry','TEntry','Text','Spinbox','TSpinbox','TCombobox'}

    def __init__(self,root,toggle):
        self.root=root;self.toggle=toggle;self.held=False
        self.tag=f'ZatoTransportSpace_{id(self)}'
        root.bind_class(self.tag,'<KeyPress-space>',self.press)
        root.bind_class(self.tag,'<KeyRelease-space>',self.release)
        self.attach(root)
        root.bind('<Map>',self.mapped,add='+')
        root.bind('<FocusOut>',self.focus_out,add='+')

    def attach(self,widget):
        tags=widget.bindtags()
        if self.tag not in tags:widget.bindtags((self.tag,)+tags)
        for child in widget.winfo_children():self.attach(child)

    def mapped(self,event):
        if event.widget.winfo_toplevel()==self.root:self.attach(event.widget)

    def text_input(self,event):
        return event.widget.winfo_class() in self.TEXT_CLASSES

    def press(self,event):
        if self.text_input(event):self.held=False;return
        if not self.held:
            self.held=True
            try:self.toggle()
            except Exception:self.root.report_callback_exception(*sys.exc_info())
        return 'break'

    def release(self,event):
        self.held=False
        if not self.text_input(event):return 'break'

    def focus_out(self,event):
        try:
            focused=self.root.focus_get()
            if focused is None or focused.winfo_toplevel()!=self.root:self.held=False
        except (tk.TclError,KeyError):
            # ttk's native combobox popdown has no Python widget wrapper.
            self.held=False
