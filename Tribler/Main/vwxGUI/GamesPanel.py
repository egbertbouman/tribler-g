# Written by Egbert Bouman
# see LICENSE for license information

import wx
import os
import sys

from Tribler.Main.vwxGUI.GamesOverviewPanel import GamesOverviewPanel
from Tribler.Main.vwxGUI.ChessMainPanel import ChessMainPanel
from Tribler.Main.vwxGUI.ChessWidgets import ChessGradientButton
from Tribler.Main.vwxGUI.GuiUtility import GUIUtility

class GamesPanel(wx.Panel):
    def __init__(self, *args):
        wx.Panel.__init__(self, *args)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.Hide()
        self.SetBackgroundColour(wx.WHITE)
        self.addComponents()
        self.SwitchPanel("Chess")
        self.Show()

    def addComponents(self):
        self.hSizer = wx.BoxSizer(wx.HORIZONTAL)

        self.chess = ChessMainPanel(self)
        self.chess.Hide()

        self.checkers = wx.Panel(self, -1)
        self.checkers.Hide()

        self.overview = GamesOverviewPanel(self)

        self.hSizer.Add(self.overview, 1, wx.EXPAND)
        self.hSizer.Add((5,0), 0, 0, 0)
        self.hSizer.Add(self.chess, 3, wx.EXPAND)
        self.hSizer.Add(self.checkers, 3, wx.EXPAND)
        self.SetSizer(self.hSizer)

    def SwitchPanel(self, panelname):
        if panelname == "Chess":
            self.chess.Show()
            self.checkers.Hide()
        elif panelname == "Checkers":
            self.chess.Hide()
            self.checkers.Show()
        self.Layout()
        self.Refresh()
