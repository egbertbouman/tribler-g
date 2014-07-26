# Written by Egbert Bouman
# see LICENSE for license information

import wx
import os
import sys

from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.__init__ import LIBRARYNAME


class GamesHeaderPanel(wx.Panel):
    
    def __init__(self, parent, title = "", *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.title = title
        self.font = self.GetFont()
        self.SetBackgroundColour(wx.Colour(230,230,230))
        self.SetForegroundColour(wx.BLACK)
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)

    def OnEraseBackground(self, event):
        pass

    def OnSize(self, event):
        self.Refresh()
        event.Skip()

    def OnPaint(self, event):
        dc = wx.BufferedPaintDC(self)
        gc = wx.GraphicsContext.Create(dc)
        dc.SetBackground(wx.Brush(wx.WHITE))
        dc.Clear()
        x, y, width, height = self.GetClientRect()
        path = gc.CreatePath()
        path.AddRoundedRectangle(x, y, width, height-1, 6)
        path.CloseSubpath()
        gc.SetBrush(wx.Brush(self.GetBackgroundColour(), wx.SOLID))
        gc.DrawPath(path)
        path = gc.CreatePath()
        path.AddRectangle(x, y+height/2, width, height/2)
        path.CloseSubpath()
        gc.DrawPath(path)
        if self.title != "":
            font = self.GetFont()
            #font.SetPointSize(font.GetPointSize()+1)
            dc.SetFont(font)
            dc.SetTextForeground(self.GetForegroundColour())
            x, y = (10, 4)
            dc.DrawText(self.title, x, y)
            dc.SetPen(wx.Pen(self.GetForegroundColour(), 2))
            dc.DrawLine(10, 25, width-10, 25)


class GamesOverviewPanel(wx.Panel):
    """This Panel shows the list of available games"""

    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.currentItem = None
        self.SetBackgroundColour(wx.WHITE)
        self.itemPanels = []
        self.addComponents()

    def addComponents(self):
        header = GamesHeaderPanel(self, 'Available games')
        header.SetSize((200, -1))
        header.SetMinSize((200, -1))

        self.vSizer = wx.BoxSizer(wx.VERTICAL)
        self.vSizer.Add(header, 0, wx.EXPAND, 0)

        self.itemPanels += [GamesItemPanel(self, 'Chess', True)]
        #self.itemPanels += [GamesItemPanel(self, 'Checkers')]
        #self.itemPanels += [GamesItemPanel(self, 'Scrabble')]
        for i in range(1,20):
            self.itemPanels += [GamesItemPanel(self)]
        for gip in self.itemPanels:
            self.vSizer.Add(gip)
        self.SetSizer(self.vSizer)

        self.currentItem = self.itemPanels[0]

    def SetSelectedItem(self, item):
        if self.currentItem != None:
            self.currentItem.selected = False
            self.currentItem.mouseAction(wx.MouseEvent(wx.wxEVT_LEAVE_WINDOW))
        self.currentItem = item
        self.GetParent().SwitchPanel(self.currentItem.content)

class GamesItemPanel(wx.Panel):
    """This Panel shows one content item inside the GamesOverviewPanel"""

    def __init__(self, parent, content = "", selected = False, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.content = content
        self.selected = selected
        self.selectedColour = self.guiUtility.selectedColour
        self.unselectedColour = self.guiUtility.unselectedColour
        self.SetMinSize((-1,22))
        self.addComponents()

    def addComponents(self):
        bitmap = wx.EmptyBitmap(949, 2)
        dc = wx.MemoryDC()
        dc.SelectObject(bitmap)
        dc.SetBackground(wx.Brush(wx.WHITE))
        dc.Clear()
        dc.SetPen(wx.Pen(wx.Colour(230,230,230), 1, wx.SOLID))
        dc.DrawLine(0, 0, 949, 0)
        self.hLine = wx.StaticBitmap(self, -1, bitmap)

        self.title = wx.StaticText(self, -1, self.content, size = (410,5))
        if self.selected:
            self.SetBackgroundColour(self.selectedColour)
            self.title.SetBackgroundColour(self.selectedColour)
        else:
            self.SetBackgroundColour(self.unselectedColour)
            self.title.SetBackgroundColour(self.unselectedColour)
        self.title.SetForegroundColour(wx.BLACK)
        self.title.SetMinSize((410,160))

        self.hSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.hSizer.Add((10,5),0,0)
        self.hSizer.Add(self.title, 0, wx.TOP | wx.BOTTOM, 3)
        self.hSizer.Add((5,0),0 ,0 ,0)

        self.vSizerOverall = wx.BoxSizer(wx.VERTICAL)
        self.vSizerOverall.Add(self.hLine, 0, 0, 0)
        self.vSizerOverall.Add(self.hSizer, 0, wx.EXPAND)

        self.Bind(wx.EVT_MOUSE_EVENTS, self.mouseAction)
        if sys.platform != 'linux2':
            self.title.Bind(wx.EVT_MOUSE_EVENTS, self.mouseAction)

        self.SetSizer(self.vSizerOverall);

    def mouseAction(self, event):
        event.Skip()
        colour = wx.Colour(216,233,240)

        if self.content == "":
            colour = self.unselectedColour
        elif event.Entering():
            colour = self.selectedColour
        elif event.Leaving() and self.selected == False:
            colour = self.unselectedColour
        elif event.LeftUp():
            self.GetParent().SetSelectedItem(self)
            self.selected = True

        self.title.SetBackgroundColour(colour)
        self.SetBackgroundColour(colour)
        self.Refresh()
