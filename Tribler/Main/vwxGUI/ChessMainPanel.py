# Written by Egbert Bouman
# see LICENSE for license information

import wx
import os
import sys
import Tribler.Main.vwxGUI.flatnotebook as fnb
import Tribler.Main.vwxGUI.ChessImages as ChessImages

from Tribler.Main.vwxGUI.ChessBoardPanel import GCBoard, FICSBoard, CraftyBoard, ReviewBoard
from Tribler.Main.vwxGUI.ChessChallengePanel import ChessChallengePanel
from Tribler.Main.vwxGUI.ChessGamesPanel import ChessGamesPanel
from Tribler.Main.vwxGUI.ChessCreatePanel import ChessCreatePanel
from Tribler.Main.vwxGUI.ChessStatsPanel import ChessStatsPanel
from Tribler.Main.vwxGUI.ChessDiscussionPanel import ChessDiscussionPanel

class ChessMainPanel(wx.Panel):
    """Panel subclass for displaying tabs"""

    def __init__(self, parent, *args):
        wx.Panel.__init__(self, parent, *args)
        self.backgroundColour = wx.Colour(216,233,240)
        self.SetBackgroundColour(self.backgroundColour)
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.addComponents()

    def OnSize(self, event):
        self.Refresh()
        event.Skip()

    def addComponents(self):
        self.hSizer = wx.BoxSizer(wx.HORIZONTAL)

        tabTitles = ["Player Statistics", "Online Chess", "Find Opponents", \
                     "vs Computer", "Discuss Games"]

        fnbStyle = fnb.FNB_DEFAULT_STYLE|fnb.FNB_NODRAG|fnb.FNB_NO_X_BUTTON| \
                   fnb.FNB_BACKGROUND_GRADIENT|fnb.FNB_NO_NAV_BUTTONS
        self.tabs = fnb.FlatNotebook(self, wx.ID_ANY, agwStyle=fnbStyle)
        self.tabs.SetTabAreaColour(self.backgroundColour)

        imgList = wx.ImageList(16, 16)
        imgList.Add(ChessImages.getStatisticsBitmap())
        imgList.Add(ChessImages.getChessBitmap())
        imgList.Add(ChessImages.getPersonsBitmap())
        imgList.Add(ChessImages.getComputerBitmap())
        imgList.Add(ChessImages.getChatBitmap())
        self.tabs.SetImageList(imgList)
        self.tabs.Bind(fnb.EVT_FLATNOTEBOOK_PAGE_CHANGED, self.ResetPage)

        self.tab1 = TabOne(self.tabs)
        self.tab2 = TabTwo(self.tabs)
        self.tab3 = TabThree(self.tabs)
        self.tab4 = TabFour(self.tabs)
        self.tab5 = TabFive(self.tabs)

        self.tab1.SetBackgroundColour(self.backgroundColour)
        self.tab2.SetBackgroundColour(self.backgroundColour)
        self.tab3.SetBackgroundColour(self.backgroundColour)
        self.tab4.SetBackgroundColour(self.backgroundColour)
        self.tab5.SetBackgroundColour(self.backgroundColour)

        self.tabs.AddPage(self.tab1, tabTitles[0], imageId=0)
        self.tabs.AddPage(self.tab2, tabTitles[1], imageId=1)
        self.tabs.AddPage(self.tab3, tabTitles[2], imageId=2)
        self.tabs.AddPage(self.tab4, tabTitles[3], imageId=3)
        self.tabs.AddPage(self.tab5, tabTitles[4], imageId=4)

        self.hSizer.Add((10,0), 0, 0, 0)
        self.hSizer.Add(self.tabs, 1, wx.EXPAND)
        self.hSizer.Add((10,0), 0, 0, 0)
        self.SetSizer(self.hSizer)

    def ResetPage(self, event):
        currentTab = self.tabs.GetCurrentPage()
        if currentTab == self.tab2:
            currentTab.SwitchPanel('games')
        elif currentTab == self.tab3:
            currentTab.SwitchPanel('challenge')
        elif currentTab == self.tab5:
            currentTab.SwitchPanel('list')

    def OnPaint(self, event):
        dc = wx.BufferedPaintDC(self)
        gc = wx.GraphicsContext.Create(dc)
        dc.SetBackground(wx.Brush(wx.WHITE))
        dc.Clear()
        x, y, width, height = self.GetClientRect()
        path = gc.CreatePath()
        path.AddRoundedRectangle(x, y, width, height, 6)
        path.CloseSubpath()
        gc.SetBrush(wx.Brush(self.backgroundColour, wx.SOLID))
        gc.FillPath(path)

class TabOne(wx.Panel):

    def __init__(self, parent, *args):
        wx.Panel.__init__(self, parent, *args)
        statsPanel = ChessStatsPanel(self)
        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,10), 0, 0, 0)
        vSizer.Add(statsPanel, 1, wx.RIGHT | wx.LEFT | wx.EXPAND, 75)
        vSizer.Add((0,10), 0, 0, 0)
        self.SetSizer(vSizer)

class TabTwo(wx.Panel):

    def __init__(self, parent, *args):
        wx.Panel.__init__(self, parent, *args)
        self.GCBoard = GCBoard(self)
        self.FICSBoard = FICSBoard(self)
        self.gamesPanel = ChessGamesPanel(self)
        self.GCBoard.Hide()
        self.FICSBoard.Hide()
        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add(self.GCBoard, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        vSizer.Add(self.FICSBoard, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        vSizer.Add(self.gamesPanel, 1, wx.ALL | wx.EXPAND, 10)
        self.SetSizer(vSizer)

    def SwitchPanel(self, panel):
        if panel == "gamecast":
            self.GCBoard.Show()
        else:
            self.GCBoard.Hide()
        if panel == "fics":
            self.FICSBoard.Show()
        else:
            self.FICSBoard.Hide()
        if panel == "games":
            self.gamesPanel.Show()
        else:
            self.gamesPanel.Hide()
        self.Layout()
        self.Refresh()

class TabThree(wx.Panel):

    def __init__(self, parent, *args):
        wx.Panel.__init__(self, parent, *args)
        self.challengePanel = ChessChallengePanel(self)
        self.createPanel = ChessCreatePanel(self)
        self.createPanel.Hide()
        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add(self.challengePanel, 1, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(self.createPanel, 1, wx.ALL | wx.EXPAND, 10)
        self.SetSizer(vSizer)

    def SwitchPanel(self, panel):
        if panel == "challenge":
            self.challengePanel.Show()
        else:
            self.challengePanel.Hide()
        if panel == "create":
            self.createPanel.Show()
        else:
            self.createPanel.Hide()
        self.Layout()
        self.Refresh()

class TabFour(wx.Panel):

    def __init__(self, parent, *args):
        wx.Panel.__init__(self, parent=parent, *args)
        self.craftyBoard = CraftyBoard(self)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add((10,0), 0, 0, 0)
        hSizer.Add(self.craftyBoard, 1, wx.EXPAND)
        hSizer.Add((10,0), 0, 0, 0)
        self.SetSizer(hSizer)

class TabFive(wx.Panel):

    def __init__(self, parent, *args):
        wx.Panel.__init__(self, parent=parent, *args)
        self.discussionPanel = ChessDiscussionPanel(self)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add((10,0), 0, 0, 0)
        hSizer.Add(self.discussionPanel, 1, wx.EXPAND)
        hSizer.Add((10,0), 0, 0, 0)
        self.SetSizer(hSizer)

    def SwitchPanel(self, panel):
        self.discussionPanel.SwitchPanel(panel)
