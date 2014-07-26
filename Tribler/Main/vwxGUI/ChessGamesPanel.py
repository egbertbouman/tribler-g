# Written by Egbert Bouman
# see LICENSE for license information

import wx
import time
import wx.lib.agw.toasterbox as TB
import Tribler.Main.vwxGUI.ChessImages as ChessImages

from time import time
from copy import deepcopy
from datetime import timedelta
from Tribler.Core.simpledefs import *
from Tribler.Core.GameCast.GameCast import *
from Tribler.Core.Utilities.utilities import show_permid_short
from Tribler.Main.vwxGUI.ChessInterface import FICSInterface
from Tribler.Main.vwxGUI.ChessWidgets import *
from Tribler.Main.vwxGUI.ChessBoardPanel import ChessBoardPanel
from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.Main.Utility import *

ID_TIMER = wx.NewId()

class ChessGamesPanel(wx.Panel):
    """Panel subclass for listing current games"""

    def __init__(self, parent, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.session = self.utility.session
        self.session.add_observer(self.DatabaseCallback, NTFY_GAMECAST, [NTFY_INSERT, NTFY_UPDATE, NTFY_DELETE], 'Game')
        self.peer_db = self.guiUtility.utility.session.open_dbhandler(NTFY_PEERS)
        self.gamecast_db = self.guiUtility.utility.session.open_dbhandler(NTFY_GAMECAST)
        self.gamecast = GameCast.getInstance()
        self.guiserver = ChessTaskQueue.getInstance()
        self.fics = FICSInterface.getInstance()
        self.fics.registerGameCallback(self.LoadFICSGame)
        self.AddComponents()
        self.currentGames_list = {}
        self.currentFICSGame = {}
        self.currentFICSGame_list = {}
        self.recentlyFinished = {}
        self.LoadP2PGames()
        self.timer = wx.Timer(self, ID_TIMER)
        wx.EVT_TIMER(self, ID_TIMER, self.UpdateTimeValues)
        self.timer.Start(1000)

    def DatabaseCallback(self, subject, changeType, objectID, *args):
        self.guiserver.add_task(self.LoadP2PGames, id=3)

    def LoadP2PGames(self):
        mypermid = self.session.get_permid()
        if hasattr(self, 'currentGames'):
            currentGames_old = self.currentGames
            self.currentGames = self.gamecast_db.getGamesSince(mypermid, time()-2*60)
        else:
            self.currentGames = self.gamecast_db.getGamesSince(mypermid, time()-2*60)
            currentGames_old = self.currentGames
        self.currentGames_list = {}
        index = 0
        while index < len(self.currentGames):
            game = self.currentGames[index]
            row = [None]*5
            players = game.get('players', [])
            # Chess games should have 2 players and only the games with a creation_time have actually started,
            # so we should only list those games
            if len(players) == 2 and game['creation_time'] != 0:
                for player,colour in players.iteritems():
                    if player != mypermid:
                        row[0] = show_permid_short(player)
                        peer = self.peer_db.getPeer(player)
                        if peer and peer['name']:
                            row[0] = peer['name']
                        row[1] = str(int(self.gamecast.getRating(player, 'chess')[0]))
                lastturn = game['moves'][-1][0] if game['moves'] and game['moves'][-1][0] else ''
                turn = 'black' if lastturn == 'white' else 'white'
                row[2] = 'you' if turn == game['players'][mypermid] else 'opponent'
                row[3] = '%3d / %3d' % (game['time'], game['inc'])
                if game['is_finished'] == AGREE_ABORT:
                    row[4] = 'game aborted'
                elif game['is_finished']  in [CLOSE_MOVE, RESIGN, AGREE_DRAW]:
                    if game['winner_permid'] == '':
                        row[4] = 'game drawn'
                    elif game['winner_permid'] == mypermid:
                        row[4] = 'game won'
                    else:
                        row[4] = 'game lost'
                else:
                    clock = self.gamecast.getGameExpireClock(game)
                    if clock > 0:
                        row[4] = 'expires in '+str(timedelta(seconds = clock)).split('.')[0]
                    elif clock > -2*60:
                        row[4] = 'game expired'
                    else:
                        self.currentGames.pop(index)
                        continue
                key = (game['owner_id'], game['game_id'])
                self.currentGames_list[key] = row
                old_game = None
                for g in currentGames_old:
                    if key == (g['owner_id'], g['game_id']):
                        old_game = g
                if not old_game:
                    wx.CallAfter(self.Notify, 'A new game has just started.')
                elif game['moves'] != old_game['moves'] and game['players'][mypermid] != game['moves'][-1][0]:
                    gc_board = self.GetParent().GCBoard
                    mustHaveFocus = (key == (gc_board.game.get('owner_id', -1), gc_board.game.get('game_id', -1)) and \
                                     gc_board.IsShownOnScreen())
                    wx.CallAfter(self.Notify, 'An opponent has just made a move.', mustHaveFocus)
            index += 1
        new_data = dict(self.currentGames_list.items())
        if self.currentFICSGame_list:
            new_data['FICS'] = self.currentFICSGame_list
        self.continueList.SetData(new_data)

    def LoadFICSGame(self):
        if not self.fics or self.fics.done or not self.fics.sentStyle:
            return
        if self.fics.game:
            oldFICSGame = self.currentFICSGame
            self.currentFICSGame = deepcopy(self.fics.game)
            self.currentFICSGame_list = {}
            row = [None]*5
            row[0] = self.currentFICSGame['owner']
            row[1] = '-'#self.currentFICSGame['rating']
            lastturn = self.currentFICSGame['moves'][-1][0] if self.currentFICSGame['moves'] else ''
            turn = 'black' if lastturn == 'white' else 'white'
            row[2] = 'you' if turn == self.currentFICSGame['players'][self.fics.name] else 'opponent'
            row[3] = '%3s / %3s' % (self.currentFICSGame['time'], self.currentFICSGame['inc'])
            if self.currentFICSGame['is_finished'] == AGREE_ABORT:
                row[4] = 'game aborted'
            elif self.currentFICSGame['is_finished']  in [CLOSE_MOVE, RESIGN, AGREE_DRAW]:
                if self.currentFICSGame['winner_colour'] == '':
                    row[4] = 'game drawn'
                elif self.currentFICSGame['winner_colour'] == self.currentFICSGame['players'][self.fics.name]:
                    row[4] = 'game won'
                else:
                    row[4] = 'game lost'
            else:
                clock = self.gamecast.getGameExpireClock(self.currentFICSGame)
                if clock > 0:
                    row[4] = 'expires in '+str(timedelta(seconds = clock)).split('.')[0]
                else:
                    row[4] = 'game expired'
            self.currentFICSGame_list = row
            if not oldFICSGame or oldFICSGame['game_id'] != self.currentFICSGame['game_id']:
                wx.CallAfter(self.Notify, 'A new game has just started.')
                if self.GetParent().FICSBoard.IsShown():
                    self.GetParent().SwitchPanel("games")
            elif oldFICSGame['moves'] != self.currentFICSGame['moves'] and \
                 self.currentFICSGame['players'][self.fics.name] != self.currentFICSGame['moves'][-1][0]:
                fics_board = self.GetParent().FICSBoard
                mustHaveFocus = fics_board.IsShownOnScreen()
                wx.CallAfter(self.Notify, 'An opponent has just made a move.', mustHaveFocus)
        else:
            self.currentFICSGame = {}
            self.currentFICSGame_list = {}
        new_data = dict(self.currentGames_list.items())
        if self.currentFICSGame_list:
            new_data['FICS'] = self.currentFICSGame_list
        self.continueList.SetData(new_data)

    def Notify(self, msg, mustHaveFocus = False):
        if mustHaveFocus and wx.Window.FindFocus():
            return
        tb = TB.ToasterBox(self, TB.TB_COMPLEX, TB.TB_DEFAULT_STYLE, TB.TB_ONTIME)
        tb.SetPopupSize((200, 50))

        disp = wx.Display(0)
        clientrect = disp.GetClientArea()
        x = clientrect.width+clientrect.x-200
        y = clientrect.height+clientrect.y-50
        tb.SetPopupPosition((x, y))
        tb.SetPopupPauseTime(6000)
        tb.SetPopupScrollSpeed(8)

        tbpanel = tb.GetToasterBoxWindow()
        panel = wx.Panel(tbpanel)
        panel.SetBackgroundColour(wx.Colour(192,208,214))

        myimage = ChessImages.getInfoBitmap()
        tb_bmp = wx.StaticBitmap(panel, -1, myimage)
        tb_lbl = wx.StaticText(panel, -1, msg)
        font = tb_lbl.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        tb_lbl.SetFont(font)

        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(tb_bmp, 0)
        hSizer.Add((5,0), 0, 0, 0)
        hSizer.Add(tb_lbl, 1, wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add(hSizer, 0, wx.EXPAND | wx.ALL, 10)
        vSizer.Layout()
        panel.SetSizer(vSizer)

        tb.AddPanel(panel)

        tb.SetPopupBackgroundColour(wx.WHITE)
        tb.SetPopupTextColour(wx.BLACK)
        tb.SetPopupText(msg)
        tb.Play()

    def UpdateTimeValues(self, event):
        if not self.IsShownOnScreen():
            return

        # Only update the clock for the FICS game
        if self.currentFICSGame:
            if self.currentFICSGame['is_finished']:
                if self.currentFICSGame['is_finished'] == AGREE_ABORT:
                    new_data = 'game aborted'
                elif self.currentFICSGame['is_finished'] in [CLOSE_MOVE, RESIGN, AGREE_DRAW]:
                    if self.currentFICSGame['winner_colour'] == '':
                        new_data = 'game drawn'
                    elif self.currentFICSGame['winner_colour'] == self.currentFICSGame['players'][self.fics.name]:
                        new_data = 'game won'
                    else:
                        new_data = 'game lost'
            else:
                if len(self.currentFICSGame['moves']) < 2:
                    new_data = '-'
                else:
                    clock = self.gamecast.getGameExpireClock(self.currentFICSGame)
                    if clock > 0:
                        new_data = 'expires in '+str(timedelta(seconds = clock)).split('.')[0]
                    else:
                        new_data = 'game expired'
            self.currentFICSGame_list[4] = new_data
            self.continueList.SetCell('FICS', 4, new_data)

        # Update the clocks for GameCast games
        num_cols = self.continueList.GetColumnCount()
        for game in self.currentGames:
            if game['is_finished']:
                continue
            if len(game.get('players', [])) == 2 and game['creation_time'] != 0:
                clock = self.gamecast.getGameExpireClock(game)
                if clock > 0:
                    new_data = 'expires in '+str(timedelta(seconds = clock)).split('.')[0]
                else:
                    new_data = 'game expired'
                key = (game['owner_id'], game['game_id'])
                self.continueList.SetCell(key, num_cols-1, new_data)

    def AddComponents(self):
        subPanel = ChessSubPanel(self, title = 'Continue a game')

        self.continueList = ContinueList(subPanel, data = {})
        continueLabel = wx.StaticText(subPanel, -1, "Currently, you are taking part in the following games (double-click to resume playing):")
        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,25), 0, 0, 0)
        vSizer.Add(continueLabel, 0, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(self.continueList, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        subPanel.SetSizer(vSizer)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add(subPanel, 1, wx.EXPAND)
        self.SetSizer(vSizer)

    def Show(self):
        wx.Panel.Show(self)
        # Update immediately when the panel is shown
        self.UpdateTimeValues(None)

    def OnGame(self, index):
        if index == 'FICS':
            self.GetParent().FICSBoard.SetGame()
            self.GetParent().SwitchPanel("fics")
        else:
            owner_id, game_id = index
            game = self.gamecast.getGame(owner_id, game_id)
            self.GetParent().GCBoard.SetGame(game)
            self.GetParent().SwitchPanel("gamecast")


class ContinueList(ChessList):

    def __init__(self, parent, data = {}, *args, **kwargs):
        columns = { 0 : ("Opponent", wx.LIST_FORMAT_LEFT, 220),
                    1 : ("Rating", wx.LIST_FORMAT_LEFT, 100),
                    2 : ("Next to move", wx.LIST_FORMAT_LEFT, 105),
                    3 : ("Start / inc time", wx.LIST_FORMAT_LEFT, 130),
                    4 : ("Status", wx.LIST_FORMAT_LEFT, 100) }
        ChessList.__init__(self, parent, columns = columns, data = data, filtered = False, *args, **kwargs)
        self.idx_human = self.il.Add(ChessImages.getHumanBitmap())
        self.idx_computer = self.il.Add(ChessImages.getComputerBitmap())
        self.SortListItems(4, 1)

    def OnItemActivated(self, event):
        item = event.m_itemIndex
        index = self.itemIndexMap[item]
        self.GetParent().GetParent().OnGame(index)

    def OnGetItemColumnImage(self, row, col):
        if col == 0:
            item = self.itemIndexMap[row]
            if isinstance(item, str) and item.startswith('FICS') and self.itemDataMap[item][col].endswith('(C)'):
                return self.idx_computer
            elif self.itemDataMap[item][col].startswith('ChessBot'):
                return self.idx_computer
            else:
                return self.idx_human
        return -1

    def OnGetItemText(self, row, col):
        item = self.itemIndexMap[row]
        text = self.itemDataMap[item][col]
        if isinstance(item, str) and item.startswith('FICS') and text.endswith('(C)'):
            text = text[:-3]
        return text
