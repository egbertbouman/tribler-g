# Written by Egbert Bouman
# see LICENSE for license information

import sys
import wx
import wx.lib.agw.customtreectrl as CT
import wx.lib.mixins.listctrl as listmix
import Tribler.Main.vwxGUI.ChessImages as ChessImages

from time import time
from datetime import date
from Tribler.Core.simpledefs import *
from Tribler.Core.Utilities.utilities import show_permid_short
from Tribler.Main.vwxGUI.ChessWidgets import *
from Tribler.Main.vwxGUI.ChessBoardPanel import ReviewBoard
from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.Core.GameCast.GameCast import GameCast
from Tribler.Main.Utility import *

class ChessDiscussionPanel(wx.Panel):
    """Panel subclass which enables users to discuss finshed games through message exchange"""

    def __init__(self, parent, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.backgroundColour = wx.Colour(216,233,240)
        self.SetBackgroundColour(self.backgroundColour)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.session = self.utility.session
        self.session.add_observer(self.DatabaseCallbackGames, NTFY_GAMECAST, [NTFY_INSERT, NTFY_UPDATE], 'Game')
        self.session.add_observer(self.DatabaseCallbackMessages, NTFY_GAMECAST, [NTFY_INSERT], 'GameMessage')
        self.peer_db = self.guiUtility.utility.session.open_dbhandler(NTFY_PEERS)
        self.gamecast_db = self.guiUtility.utility.session.open_dbhandler(NTFY_GAMECAST)
        self.gamecast = GameCast.getInstance()
        self.guiserver = ChessTaskQueue.getInstance()
        self.AddComponents()
        self.guiserver.add_task(self.LoadGames, id=4)

    def DatabaseCallbackGames(self, subject, changeType, objectID, *args):
        self.guiserver.add_task(self.LoadGames, id=4)

    def DatabaseCallbackMessages(self, subject, changeType, objectID, *args):
        self.guiserver.add_task(self.LoadMessages, id=5)

    def LoadGames(self):
        self.gamesdata = self.gamecast_db.getFinishedGames()
        old_data = self.gameslist.itemDataMap
        new_data = {}
        for i, game in enumerate(self.gamesdata):
            row = [None]*5
            players = game.get('players', {})
            if len(players) != 2:
                continue
            save = True
            for player, colour in players.items():
                name = show_permid_short(player)
                if player == self.session.get_permid():
                    nick = self.session.sessconfig.get('nickname', '')
                    if nick:
                        name = nick
                else:
                    peer = self.peer_db.getPeer(player)
                    if peer and peer['name']:
                        name = peer['name']
                if colour == 'white':
                    row[0] = name
                elif colour == 'black':
                    row[1] = name
                else:
                    save = False
            winner = game['winner_permid']
            if winner:
                row[2] = players[winner]
            else:
                row[2] = '-'
            row[3] = len(game['moves'])
            row[4] = date.fromtimestamp(game['lastmove_time']).isoformat() if game['lastmove_time'] else date.fromtimestamp(game['creation_time']).isoformat()
            if save:
                new_data[i] = row
        if old_data != new_data:
            self.gameslist.SetData(new_data)

    def LoadMessages(self):
        if not self.currentGame:
            return
        game_owner_id = self.currentGame['owner_id']
        game_id = self.currentGame['game_id']
        self.messagedata = self.gamecast_db.getMessages(game_owner_id = game_owner_id, game_id = game_id)
        self.messageTree.DeleteAllItems()
        if not self.messageTree.GetRootItem():
            self.root = self.messageTree.AddRoot('root', image = 0)
        self.messageTree.SelectItem(self.root)
        for i, message in enumerate(self.messagedata):
            content = message['content']
            itemname = content.replace('\n','')
            if len(itemname) > 25:
                itemname = itemname[:25]+'..'
            item = self.messageTree.AppendItem(self.root, itemname, image = 0)
            self.messageTree.SetPyData(item, i)

    def AddComponents(self):
        self.listPanel = ChessSubPanel(self, title = 'Known games')
        self.reviewBoard = ReviewBoard(self)
        self.messagesPanel = wx.Panel(self, -1)
        self.messagesPanel.SetBackgroundColour(wx.Colour(216,233,240))
        self.reviewBoard.Hide()
        self.messagesPanel.Hide()

        #Populate listPanel
        self.gameslistLabel = wx.StaticText(self.listPanel, -1, "The table below displays a list of currently known games (double-click to review):")
        self.gameslist = ChessGamesList(self.listPanel, data = {})
        self.search = wx.SearchCtrl(self.listPanel)
        self.search.SetDescriptiveText("Search for player")
        self.search.SetMinSize((150, -1))
        self.search.Bind(wx.EVT_TEXT, self.OnTextSearch)
        self.search.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self.OnCancelSearch)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,25), 0, 0, 0)
        vSizer.Add(self.gameslistLabel, 0, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(self.gameslist, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        vSizer.Add(self.search, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 10)
        self.listPanel.SetSizer(vSizer)

        #Populate messagesPanel
        self.leftPanel = ChessSubPanel(self.messagesPanel, title = 'Game messages')
        self.rightPanel = ChessSubPanel(self.messagesPanel, title = 'Message contents')
        self.messageTree = CT.CustomTreeCtrl(self.leftPanel, -1, agwStyle = CT.TR_HAS_BUTTONS | CT.TR_HAS_VARIABLE_ROW_HEIGHT |
                                                                       CT.TR_FULL_ROW_HIGHLIGHT | CT.TR_HIDE_ROOT)

        il = wx.ImageList(16, 16)
        il.Add(ChessImages.getMessageBitmap())
        self.messageTree.AssignImageList(il)
        self.messageTree.Bind(CT.EVT_TREE_SEL_CHANGED, self.OnSelChanged)
        self.root = self.messageTree.AddRoot('root', image = 0)
        self.messageTree.SelectItem(self.root)
        self.messageOwner = wx.StaticText(self.rightPanel, -1, "")
        self.message = wx.TextCtrl(self.rightPanel, -1, '', style = wx.TE_MULTILINE| wx.TE_RICH  | wx.NO_BORDER)
        self.message.SetBackgroundColour(self.rightPanel.GetBackgroundColour())
        self.message.SetEditable(False)
        #replyBtn = ChessGradientButton(self.rightPanel, 4, -1, ChessImages.getReplyBitmap(), "Reply", size=(-1,25))
        #replyBtn.SetFont(font)
        #replyBtn.Bind(wx.EVT_BUTTON, self.OnReply)
        self.newBtn = ChessGradientButton(self.rightPanel, 4, -1, ChessImages.getNewBitmap(), "New", size=(-1,25))
        font = self.newBtn.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.newBtn.SetFont(font)
        self.newBtn.Bind(wx.EVT_BUTTON, self.OnNew)
        listBtn = ChessGradientButton(self.leftPanel, 4, -1, None, "Games list", size=(-1,25))
        listBtn.SetFont(font)
        listBtn.Bind(wx.EVT_BUTTON, self.OnList)
        reviewBtn = ChessGradientButton(self.leftPanel, 4, -1, None, "Review game", size=(-1,25))
        reviewBtn.SetFont(font)
        reviewBtn.Bind(wx.EVT_BUTTON, self.OnReview)

        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        btnSizer.Add(listBtn, 1, 0)
        btnSizer.Add((5,0), 0, 0, 0)
        btnSizer.Add(reviewBtn, 1, 0)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,25), 0, 0, 0)
        vSizer.Add(self.messageTree, 1, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(btnSizer, 0, wx.RIGHT | wx.LEFT | wx.BOTTOM | wx.EXPAND, 10)
        self.leftPanel.SetSizer(vSizer)

        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        #btnSizer.Add(replyBtn, 0, 0)
        btnSizer.Add((5,0), 0, 0, 0)
        btnSizer.Add(self.newBtn, 0, 0)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,20), 0, 0, 0)
        vSizer.Add(self.messageOwner, 0, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(self.message, 1, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(btnSizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.rightPanel.SetSizer(vSizer)

        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(self.leftPanel, 1, wx.EXPAND)
        hSizer.Add((15,0), 0, 0, 0)
        hSizer.Add(self.rightPanel, 2, wx.EXPAND)
        self.messagesPanel.SetSizer(hSizer)

        #Add the panels to the main-sizer
        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add(self.listPanel, 1, wx.TOP | wx.BOTTOM | wx.EXPAND, 10)
        vSizer.Add(self.reviewBoard, 1, wx.EXPAND)
        vSizer.Add(self.messagesPanel, 1, wx.TOP | wx.BOTTOM | wx.EXPAND, 10)
        self.SetSizer(vSizer)

        self.currentGame = None
        self.currentMessage = None

    def OnTextSearch(self, event):
        self.search.ShowCancelButton(len(self.search.GetValue()))
        self.gameslist.Filter(self.search.GetValue(), [0,1])

    def OnCancelSearch(self, event):
        self.search.SetValue('')
        self.OnTextSearch(event)

    def OnSelChanged(self, event):
        index = self.messageTree.GetPyData(event.GetItem())
        if index == None:
            return
        self.currentMessage = self.messagedata[index]
        owner_id = self.currentMessage['owner_id']
        if owner_id == 0:
            owner_permid = self.session.get_permid()
        else:
            owner_permid = self.peer_db.getPermid(self.currentMessage['owner_id'])
        author = show_permid_short(owner_permid)
        if owner_permid == self.session.get_permid():
            name = self.session.sessconfig.get('nickname', '')
            if name:
                author = name
        else:
            peer = self.peer_db.getPeer(owner_permid)
            if peer and peer['name']:
                author = peer['name']
        self.messageOwner.SetLabel("author: "+author)
        self.message.SetValue(self.currentMessage['content'])
        event.Skip()
        if self.newBtn.GetLabel() == 'Post':
            self.message.SetBackgroundColour(self.rightPanel.GetBackgroundColour())
            self.message.SetEditable(False)
            self.newBtn.SetLabel('New')
            self.newBtn._bitmap = ChessImages.getNewBitmap()
            self.newBtn.Refresh()

    def OnReply(self, event):
        pass

    def OnNew(self, event):
        if self.newBtn.GetLabel() == 'New':
            self.messageOwner.SetLabel('')
            self.message.SetBackgroundColour(wx.WHITE)
            self.message.SetEditable(True)
            self.message.SetValue('')
            self.newBtn.SetLabel('Post')
            self.newBtn._bitmap = ChessImages.getSendBitmap()
            self.newBtn.Refresh()
            self.messageTree.SelectItem(self.root)
        else:
            content = self.message.GetValue()
            if len(content) > 500:
                dialog = wx.MessageDialog(None, 'There are only 500 characters allowed in a single message.', 'Message too long', wx.OK | wx.ICON_EXCLAMATION)
                dialog.ShowModal()
                return
            self.currentMessage = {}
            self.currentMessage['game_id'] = self.currentGame['game_id']
            self.currentMessage['game_owner_id'] = self.currentGame['owner_id']
            self.currentMessage['content'] = content
            self.gamecast.executeDiscuss(self.currentMessage)
            name = self.session.sessconfig.get('nickname', '')
            author = name if name else show_permid_short(self.session.get_permid())
            self.messageOwner.SetLabel("author: "+author)
            self.message.SetBackgroundColour(self.rightPanel.GetBackgroundColour())
            self.message.SetEditable(False)
            self.newBtn.SetLabel('New')
            self.newBtn._bitmap = ChessImages.getNewBitmap()
            self.newBtn.Refresh()

    def OnList(self, event):
        self.SwitchPanel("list")

    def OnReview(self, event):
        self.SwitchPanel("review")

    def OnMessages(self, event):
        self.SwitchPanel("messages")

    def OnGame(self, index):
        self.currentGame = self.gamesdata[index]
        self.reviewBoard.SetGame(self.currentGame)
        self.SwitchPanel("review")

    def SwitchPanel(self, panel):
        if panel == "list":
            self.reviewBoard.Hide()
            self.messagesPanel.Hide()
            self.listPanel.Show()
        elif panel == "review":
            self.messagesPanel.Hide()
            self.listPanel.Hide()
            self.reviewBoard.Show()
        elif panel == "messages":
            self.currentMessage = None
            self.messageOwner.SetLabel('')
            self.message.SetValue('')
            self.message.SetBackgroundColour(self.rightPanel.GetBackgroundColour())
            self.message.SetEditable(False)

            self.LoadMessages()
            self.listPanel.Hide()
            self.reviewBoard.Hide()
            self.messagesPanel.Show()
        self.Layout()
        self.Refresh()


class ChessGamesList(ChessList):

    def __init__(self, parent, data = {}, *args, **kwargs):
        columns = { 0 : ("White",  wx.LIST_FORMAT_LEFT, 200),
                    1 : ("Black",  wx.LIST_FORMAT_LEFT, 200),
                    2 : ("Winner", wx.LIST_FORMAT_LEFT,  80),
                    3 : ("Moves",  wx.LIST_FORMAT_LEFT,  80),
                    4 : ("Date",   wx.LIST_FORMAT_LEFT,  80) }
        ChessList.__init__(self, parent, columns = columns, data = data, filtered = True, *args, **kwargs)
        self.idx_human = self.il.Add(ChessImages.getHumanBitmap())
        self.idx_computer = self.il.Add(ChessImages.getComputerBitmap())
        self.SortListItems(4, 0)

    def OnItemActivated(self, event):
        item = event.m_itemIndex
        index = self.filteredItemIndexMap[item]
        self.GetParent().GetParent().OnGame(index)

    def OnGetItemColumnImage(self, row, col):
        if col == 0 or col == 1:
            item = self.filteredItemIndexMap[row]
            if self.itemDataMap[item][col].startswith('ChessBot'):
                return self.idx_computer
            else:
                return self.idx_human
        return -1