# Written by Egbert Bouman
# see LICENSE for license information

import sys
import wx
import wx.lib.mixins.listctrl as listmix
import Tribler.Main.vwxGUI.ChessImages as ChessImages

from Tribler.Core.simpledefs import *
from Tribler.Core.Utilities.utilities import show_permid_short, str2bin
from Tribler.Core.GameCast.GameCast import GameCast
from Tribler.Main.vwxGUI.ChessWidgets import *
from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.Main.Utility import *

class ChessStatsPanel(wx.Panel):
    """Panel subclass for giving statistics about known games within the Tribler network"""

    def __init__(self, parent, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.backgroundColour = wx.Colour(216,233,240)
        self.SetBackgroundColour(self.backgroundColour)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.session = self.utility.session
        self.session.add_observer(self.DatabaseCallback, NTFY_GAMECAST, [NTFY_INSERT, NTFY_UPDATE, NTFY_DELETE], 'GameRatings')
        self.peer_db = self.guiUtility.utility.session.open_dbhandler(NTFY_PEERS)
        self.gamecast_db = self.guiUtility.utility.session.open_dbhandler(NTFY_GAMECAST)
        self.gamecast = GameCast.getInstance()
        self.guiserver = ChessTaskQueue.getInstance()
        self.AddComponents()
        self.guiserver.add_task(self.LoadMyStats, id=1)
        self.guiserver.add_task(self.LoadHighScores, id=2)

    def DatabaseCallback(self, subject, changeType, objectID, *args):
        self.guiserver.add_task(self.LoadMyStats, id=1)
        self.guiserver.add_task(self.LoadHighScores, id=2)

    def AddComponents(self):
        self.subPanel1 = ChessMyStatsPanel(self)
        self.subPanel2 = ChessSubPanel(self, title = 'Statistics of other players')
        self.subPanel3 = ChessSubPanel(self, title = 'Statistics of other players')
        self.subPanel2.Hide()

        # Populate subPanel2
        chartLabel = wx.StaticText(self.subPanel2, -1, "The graph below displays the rating distribution among known players in the network:")
        self.chart = ChessChart(self.subPanel2, data = [0]*20, size = (310,210))
        topLabel = wx.StaticText(self.subPanel2, -1, "To view the players with the highest ratings, please visit the")
        topButton = ChessGradientButton(self.subPanel2, 4, -1, None, "Highscores", size=(-1,25))
        topButton.Bind(wx.EVT_BUTTON, self.OnTopButton)
        font = topButton.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        topButton.SetFont(font)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,25), 0, 0, 0)
        vSizer.Add(chartLabel, 0, wx.ALL | wx.EXPAND, 10)
        vSizer.Add((0,1), 1, 0, 0)
        vSizer.Add(self.chart, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.CENTER, 10)
        vSizer.Add((0,1), 1, 0, 0)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(topLabel, 0, wx.ALIGN_CENTER_VERTICAL)
        hSizer.Add((5,0), 0, 0, 0)
        hSizer.Add(topButton, 0, 0)
        vSizer.Add(hSizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        self.subPanel2.SetSizer(vSizer)

        # Populate subPanel3
        playersLabel = wx.StaticText(self.subPanel3, -1, "The highest ranking players within your part of the network are (double-click to invite):")
        self.players = ChessPlayersList(self.subPanel3, data = {})
        distLabel = wx.StaticText(self.subPanel3, -1, "To learn what ratings are common, please visit the")
        distButton = ChessGradientButton(self.subPanel3, 4, -1, None, "Rating distribution", size=(-1,25))
        distButton.Bind(wx.EVT_BUTTON, self.OnDistButton)
        distButton.SetFont(font)
        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,25), 0, 0, 0)
        vSizer.Add(playersLabel, 0, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(self.players, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(distLabel, 0, wx.ALIGN_CENTER_VERTICAL)
        hSizer.Add((5,0), 0, 0, 0)
        hSizer.Add(distButton, 0, 0)
        vSizer.Add(hSizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        self.subPanel3.SetSizer(vSizer)

        # Add the panel to the main-sizer
        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add(self.subPanel1, 0, wx.EXPAND)
        vSizer.Add((0,20), 0, 0, 0)
        vSizer.Add(self.subPanel2, 1, wx.EXPAND)
        vSizer.Add(self.subPanel3, 1, wx.EXPAND)
        self.SetSizer(vSizer)

    def LoadMyStats(self):
        mypermid = self.session.get_permid()
        myrating = self.gamecast.getRating(mypermid, 'chess')[0]
        games = self.gamecast_db.getCurrentGames(mypermid, finished = True)
        bwins   = 0
        blosses = 0
        bdraws  = 0
        wwins   = 0
        wlosses = 0
        wdraws  = 0
        for game in games:
            mycolour = game['players'][mypermid]
            if game['winner_permid']:
                if game['winner_permid'] == mypermid:
                    if mycolour == 'white':
                        wwins += 1
                    else:
                        bwins += 1
                else:
                    if mycolour == 'white':
                        wlosses += 1
                    else:
                        blosses += 1
            else:
                if mycolour == 'white':
                    wdraws += 1
                else:
                    bdraws += 1
        self.subPanel1.UpdateInfo(rating = str(int(myrating)))
        self.subPanel1.UpdateInfo(bwins = bwins, blosses = blosses, bdraws = bdraws)
        self.subPanel1.UpdateInfo(wwins = wwins, wlosses = wlosses, wdraws = wdraws)

    def LoadHighScores(self):
        self.highScores = self.gamecast_db.getHighScores(25, 'chess')
        old_data = self.players.itemDataMap
        new_data = {}
        for i, hs in enumerate(self.highScores[:25]):
            row = [None]*5
            permid = str2bin(hs[0])
            games = self.gamecast_db.getCurrentGames(permid, finished = True)
            row[0] = show_permid_short(permid)
            if permid == self.session.get_permid():
                name = self.session.sessconfig.get('nickname', '')
                if name:
                    row[0] = name
            else:
                peer = self.peer_db.getPeer(permid)
                if peer and peer['name']:
                    row[0] = peer['name']
            row[1] = int(hs[1])
            row[2] = 0
            row[3] = 0
            row[4] = 0
            for game in games:
                if game['winner_permid']:
                    if game['winner_permid'] == permid:
                        row[2] += 1
                    else:
                        row[4] += 1
                else:
                    row[3] += 1
            new_data[i] = row
        if old_data != new_data:
            self.players.SetData(new_data)

        chartdata = [0]*20
        for hs in self.highScores:
            rating = int(hs[1])
            chartdata[rating/150] += 1
        self.chart.SetData(chartdata)

    def OnTopButton(self, event):
        self.SwitchPanel("highscores")

    def OnDistButton(self, event):
        self.SwitchPanel("distribution")

    def OnGame(self, index):
        score = self.highScores[index]
        permid = score[0]
        if str2bin(permid) == self.session.get_permid():
            return
        tabs = self.GetParent().GetParent()
        tabs.SetSelection(2)
        main = tabs.GetParent()
        main.tab3.SwitchPanel("create")
        create = main.tab3.createPanel
        create.opponentType.SetSelection(1)
        # SetSelection does not fire an event, so just call the event handler manually
        create.OnOpponentType(None)
        create.permid.SetValue(permid)

    def SwitchPanel(self, panel):
        if panel == "highscores":
            self.subPanel2.Hide()
            self.subPanel3.Show()
        elif panel == "distribution":
            self.subPanel2.Show()
            self.subPanel3.Hide()
        self.Layout()
        self.Refresh()

class ChessMyStatsPanel(ChessSubPanel):
    """ChessSubPanel subclass for displaying statics of the current player"""

    def __init__(self, parent, *args, **kwargs):
        ChessSubPanel.__init__(self, parent, title = "My statistics", *args, **kwargs)
        self.SetMinSize((-1,125))
        self.SetMaxSize((-1,125))
        self.fontitalic = self.GetFont()
        self.fontitalic.SetStyle(wx.ITALIC)
        # Set attributes to default settings
        self.rating = 50
        self.bwins = 0
        self.blosses = 0
        self.bdraws = 0
        self.wwins = 0
        self.wlosses = 0
        self.wdraws = 0
        self.wtotal = self.wwins + self.wlosses + self.wdraws
        self.btotal = self.bwins + self.blosses + self.bdraws

    def UpdateInfo(self, **kwargs):
        # Update the attributes. A keyword must be given for each of them
        for key,value in kwargs.items():
            if key in ["rating", "bwins", "blosses", "bdraws", "wwins", "wlosses", "wdraws"]:
                s = "self.%s = %s" % (key,value)
                exec(s)
        self.Refresh()
        self.wtotal = self.wwins + self.wlosses + self.wdraws
        self.btotal = self.bwins + self.blosses + self.bdraws

    def Draw(self, dc):
        width = self.GetClientRect()[2]
        dc.SetPen(wx.Pen(wx.WHITE, 1, wx.TRANSPARENT))
        dc.SetBrush(wx.Brush((240,248,255), style=wx.SOLID))
        dc.DrawRectangle(10, 35, width-20, 20)
        dc.DrawRectangle(10, 75, width-20, 20)
        dc.SetBrush(wx.Brush((240,255,204), style=wx.SOLID))
        dc.DrawRectangle(10, 55, width-20, 20)
        dc.DrawRectangle(10, 95, width-20, 20)
        dc.SetTextForeground((255,51,0))
        dc.SetFont(self.fontbold)
        dc.DrawText("Rating %s" % str(self.rating), 12, 38)
        dc.SetFont(self.fontitalic)
        dc.SetTextForeground(wx.BLACK)
        dc.DrawText("Black", 12, 58)
        dc.DrawText("White", 12, 78)
        dc.DrawText("Total", 12, 98)
        dc.DrawText("Wins", 150, 38)
        dc.SetFont(self.font)
        dc.DrawText(str(self.bwins), 150, 58)
        dc.DrawText(str(self.wwins), 150, 78)
        dc.DrawText(str(self.bwins+self.wwins), 150, 98)
        dc.SetFont(self.fontitalic)
        dc.DrawText("Losses", 250, 38)
        dc.SetFont(self.font)
        dc.DrawText(str(self.blosses), 250, 58)
        dc.DrawText(str(self.wlosses), 250, 78)
        dc.DrawText(str(self.blosses+self.wlosses), 250, 98)
        dc.SetFont(self.fontitalic)
        dc.DrawText("Draws", 350, 38)
        dc.SetFont(self.font)
        dc.DrawText(str(self.bdraws), 350, 58)
        dc.DrawText(str(self.wdraws), 350, 78)
        dc.DrawText(str(self.bdraws+self.wdraws), 350, 98)
        dc.SetFont(self.fontitalic)
        dc.DrawText("Games", 450, 38)
        dc.SetFont(self.font)
        dc.DrawText(str(self.btotal), 450, 58)
        dc.DrawText(str(self.wtotal), 450, 78)
        dc.DrawText(str(self.btotal+self.wtotal), 450, 98)
        dc.SetPen(wx.Pen(self.edgeColour, 1, wx.SOLID))
        dc.SetBrush(wx.Brush(self.edgeColour, style=wx.TRANSPARENT))
        dc.DrawRectangle(10, 35, width-20, 80)

class ChessChart(wx.Panel):
    """Panel wich displays the user-rating distribution as a simple histogram """

    def __init__(self, parent, data = [], *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.data = data
        self.font = self.GetFont()
        self.SetBackgroundColour((192,208,214))
        self.Bind(wx.EVT_PAINT, self.OnPaint)

    def SetData(self, data):
        self.data = data
        self.Refresh()

    def OnPaint(self, event):
        dc = wx.PaintDC(self)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()
        dc.SetDeviceOrigin(40, 165)
        dc.SetAxisOrientation(True, True)
        self.DrawGrid(dc)
        self.DrawData(dc)
        self.DrawAxis(dc)
        self.DrawText(dc)

    def DrawGrid(self, dc):
        dc.SetPen(wx.Pen((169,183,188)))
        # Draw a single horizontal dotted line at the top
        x, y = (2, 160)
        for i in xrange(0, 240, 2):
            dc.DrawPoint(x, y)
            x += 2
        # Draw a number of vertical dotted lines
        for i in range(24, 252, 24):
            x, y = (i, 2)
            for i in xrange(0, 160, 2):
                dc.DrawPoint(x, y)
                y += 2

    def DrawData(self, dc):
        dc.SetPen(wx.Pen(wx.BLACK, 1, wx.SOLID))
        dc.SetBrush(wx.Brush('#E1FF96', style=wx.SOLID))
        # Draw the data using rectangles
        maximum = max(self.data)
        for i in range(12, 252, 12):
            data = self.data[(i-12)/12]
            if maximum != 0:
                factor = 1.0*data/maximum
            else:
                factor = 0
            dc.DrawRectangle(i-11, 0, 12, factor*160)

    def DrawAxis(self, dc):
        dc.SetPen(wx.Pen(wx.BLACK))
        dc.SetFont(self.font)
        dc.DrawLine(1, 1, 240, 1)
        dc.DrawLine(1, 1, 1, 161)
        # Draw the maximum value on the y axis
        maximum = str(max(self.data))
        w = dc.GetFullTextExtent(maximum)[0]
        dc.DrawText(maximum, -23-w/2, 160)
        dc.DrawLine(5, 160, 0, 160)
        # Draw small lines on the x axis, which are used to denote where the values are
        for i in range(12, 252, 12):
            dc.DrawLine(i, 5, i, 0)
        # Draw values along the x axis
        for i in range(0, 11, 2):
            s = str(i*300)
            w = dc.GetFullTextExtent(s)[0]
            dc.DrawText(s, i*24-w/2, -5)

    def DrawText(self, dc):
        # Draw the description along the x&y axis
        dc.DrawText('Rating', 100, -25)
        if sys.platform == 'linux2': 
            dc.DrawRotatedText('Number of users', -30, 220, 90)
        else:
            dc.DrawRotatedText('Number of users', -30, 30, 270)

class ChessPlayersList(ChessList):

    def __init__(self, parent, data = {}, *args, **kwargs):
	columns = { 0 : ("Player", wx.LIST_FORMAT_LEFT, 200),
                1 : ("Rating", wx.LIST_FORMAT_LEFT, 80 ),
                2 : ("Wins",   wx.LIST_FORMAT_LEFT, 80 ),
                3 : ("Draws",  wx.LIST_FORMAT_LEFT, 80 ),
                4 : ("Losses", wx.LIST_FORMAT_LEFT, 80 ) }
        ChessList.__init__(self, parent, columns = columns, data = data, filtered = False, *args, **kwargs)
        self.idx_human = self.il.Add(ChessImages.getHumanBitmap())
        self.idx_computer = self.il.Add(ChessImages.getComputerBitmap())
        self.SortListItems(1, 0)

    def OnItemActivated(self, event):
        item = event.m_itemIndex
        index = self.itemIndexMap[item]
        self.GetParent().GetParent().OnGame(index)

    def OnGetItemColumnImage(self, row, col):
        if col == 0:
            item = self.itemIndexMap[row]
            if self.itemDataMap[item][col].startswith('ChessBot'):
                return self.idx_computer
            else:
                return self.idx_human
        return -1