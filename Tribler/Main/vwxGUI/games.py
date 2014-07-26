# Written by Egbert Bouman
# see LICENSE for license information

import wx
import sys

from Tribler.Main.vwxGUI.GamesPanel import GamesPanel

class Games(wx.Panel):
    def __init__(self):
        pre = wx.PrePanel()
        # the Create step is done by XRC.
        self.PostCreate(pre)
        if sys.platform == 'linux2':
            self.Bind(wx.EVT_SIZE, self.OnCreate)
        else:
            self.Bind(wx.EVT_WINDOW_CREATE, self.OnCreate)

    def OnCreate(self, event):
        if sys.platform == 'linux2':
            self.Unbind(wx.EVT_SIZE)
        else:
            self.Unbind(wx.EVT_WINDOW_CREATE)

        wx.CallAfter(self._PostInit)
        event.Skip()

    def _PostInit(self):
        self.SetBackgroundColour(wx.WHITE)
        font = self.GetFont()
        font.SetFaceName('Verdana')
        if sys.platform == 'linux2':
            font.SetPointSize(9)
        self.SetFont(font)
        vSizer = wx.BoxSizer(wx.VERTICAL)
        gmspanel = GamesPanel(self)
        vSizer.Add(gmspanel, 1, wx.EXPAND)
        self.SetSizer(vSizer)
        self.Layout()

