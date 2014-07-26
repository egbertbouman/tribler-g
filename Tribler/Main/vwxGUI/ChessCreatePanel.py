# Written by Egbert Bouman
# see LICENSE for license information

import wx
import sys
import Tribler.Main.vwxGUI.ChessImages as ChessImages

from time import time
from datetime import datetime, timedelta
from Tribler.Core.simpledefs import *
from Tribler.Core.BitTornado.bencode import bencode
from Tribler.Core.CacheDB.sqlitecachedb import bin2str, str2bin
from Tribler.Core.Utilities.utilities import show_permid_short
from Tribler.Core.GameCast.GameCast import *
from Tribler.Core.GameCast.GameCastGossip import GameCastGossip
from Tribler.Main.vwxGUI.ChessWidgets import *
from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.Main.Utility import *

ID_TIMER = wx.NewId()

class ChessCreatePanel(wx.Panel):
    """ChessSubPanel subclass for creating new on-line chess games"""

    def __init__(self, parent, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.backgroundColour = wx.Colour(216,233,240)
        self.SetBackgroundColour(self.backgroundColour)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.session = self.utility.session
        self.session.add_observer(self.DatabaseCallback, NTFY_GAMECAST, [NTFY_INSERT, NTFY_UPDATE, NTFY_DELETE], 'GameInvite')
        self.peer_db = self.guiUtility.utility.session.open_dbhandler(NTFY_PEERS)
        self.gamecast_db = self.guiUtility.utility.session.open_dbhandler(NTFY_GAMECAST)
        self.gamecast = GameCast.getInstance()
        self.guiserver = ChessTaskQueue.getInstance()
        self.AddComponents()
        self.myInvites = []
        self.guiserver.add_task(self.LoadMyInvites, id=6)
        self.timer = wx.Timer(self, ID_TIMER)
        wx.EVT_TIMER(self, ID_TIMER, self.UpdateTimeValues)
        self.timer.Start(1000)

    def DatabaseCallback(self, subject, changeType, objectID, *args):
        self.guiserver.add_task(self.LoadMyInvites, id=6)

    def LoadMyInvites(self):
        self.myInvites = self.gamecast_db.getMyInvites()
        results = {}
        index = 0
        while index < len(self.myInvites):
            invite = self.myInvites[index]
            #Only display invites that have not been accepted yet
            if not (self.gamecast.getInviteState(invite, 'send', 'accept') and invite['target_id'] < 0) and \
               not (self.gamecast.getInviteState(invite, 'recv', 'accept') and invite['target_id'] >= 0) :
                row = [None]*5
                if invite['target_id'] < 0:
                    row[0] = 'random player'
                    row[1] = str(invite['min_rating'])+'-'+str(invite['max_rating'])
                else:
                    permid = self.peer_db.getPermid(invite['target_id'])
                    row[0] = show_permid_short(permid)
                    peer = self.peer_db.getPeer(permid)
                    if peer and peer['name']:
                        row[0] = peer['name']
                    row[1] = str(int(self.gamecast.getRating(permid, 'chess')[0]))
                if invite['colour'] == 'black':
                    row[2] = 'white'
                else:
                    row[2] = 'black'
                row[3] = '%d / %d' % (invite['time'], invite['inc'])
                now_datetime = datetime.today()
                exp_datetime = datetime.fromtimestamp(invite['creation_time']+INV_EXPIRE_TIME)
                rmv_datetime = datetime.fromtimestamp(invite['creation_time']+INV_EXPIRE_TIME+2*60)
                if now_datetime < exp_datetime:
                    delta = exp_datetime - now_datetime
                    row[4] = str(delta).split('.')[0]
                elif now_datetime < rmv_datetime:
                    row[4] = 'timed out'
                else:
                    self.myInvites.pop(index)
                    continue
                key = (invite['owner_id'], invite['invite_id'])
                results[key] = row
            index += 1
        self.myList.SetData(results)

    def UpdateTimeValues(self, event):
        num_cols = self.myList.GetColumnCount()
        num_itms = self.myList.GetItemCount()
        now_datetime = datetime.today()
        for invite in self.myInvites:
            if invite['status'] <= 2**4:
                exp_datetime = datetime.fromtimestamp(invite['creation_time']+INV_EXPIRE_TIME)
                if now_datetime < exp_datetime:
                    delta = exp_datetime - now_datetime
                    new_data = str(delta).split('.')[0]
                else:
                    new_data = 'timed out'
                key = (invite['owner_id'], invite['invite_id'])
                self.myList.SetCell(key, num_cols-1, new_data)

    def AddComponents(self):
        subPanel1 = ChessSubPanel(self, title = 'Your challenges')
        subPanel2 = ChessSubPanel(self, title = 'Create a new game', center = True)

        self.myList = ChessMyChallengeList(subPanel1, data = {})
        myLabel = wx.StaticText(subPanel1, -1, "The list below contains the challenges that you have created, and are waiting to be accepted by others:")

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,25), 0, 0, 0)
        vSizer.Add(myLabel, 0, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(self.myList, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        subPanel1.SetSizer(vSizer)

        self.opponentLbl = wx.StaticText(subPanel2, -1, 'Opponent:')
        self.opponentType = wx.Choice(subPanel2, -1, choices = ['random','friend'])
        self.opponentType.SetSelection(0)
        self.opponentType.Bind(wx.EVT_CHOICE, self.OnOpponentType)
        self.permidLbl = wx.StaticText(subPanel2, -1, ' with PermID ')
        self.permid = wx.TextCtrl(subPanel2, -1, '', size = (150,-1))
        self.permid.Bind(wx.EVT_KEY_DOWN, self.OnPermid)
        self.minRatingLbl = wx.StaticText(subPanel2, -1, ' with rating between ')
        self.minRating = wx.TextCtrl(subPanel2, -1, '', size = (40,-1))
        self.minRating.SetValue('0')
        self.minRating.Bind(wx.EVT_KEY_DOWN, self.OnMinRating)
        self.maxRatingLbl = wx.StaticText(subPanel2, -1, ' and ')
        self.maxRating = wx.TextCtrl(subPanel2, -1, '', size = (40,-1))
        self.maxRating.SetValue('9999')
        self.maxRating.Bind(wx.EVT_KEY_DOWN, self.OnMaxRating)
        self.opponentSzr = wx.BoxSizer(wx.HORIZONTAL)
        self.opponentSzr.Add(self.opponentType, 0, 0)
        self.opponentSzr.Add(self.permidLbl, 0, wx.ALIGN_CENTER_VERTICAL)
        self.opponentSzr.Add(self.permid, 0, 0)
        self.opponentSzr.Add(self.minRatingLbl, 0, wx.ALIGN_CENTER_VERTICAL)
        self.opponentSzr.Add(self.minRating, 0, 0)
        self.opponentSzr.Add(self.maxRatingLbl, 0, wx.ALIGN_CENTER_VERTICAL)
        self.opponentSzr.Add(self.maxRating, 0, 0)
        self.permidLbl.Hide()
        self.permid.Hide()
        self.minRatingLbl.Show()
        self.minRating.Show()
        self.maxRatingLbl.Show()
        self.maxRating.Show()
        self.opponentSzr.Layout()

        self.colourLbl = wx.StaticText(subPanel2, -1, 'Colour to play with:')
        self.colour = wx.Choice(subPanel2, -1, choices = ['white','black'])
        self.colour.SetSelection(0)

        self.movetimeLbl = wx.StaticText(subPanel2, -1, 'Clock time to start:')
        self.movetime = wx.TextCtrl(subPanel2, -1, '5', size = (40,-1), style = wx.TE_RIGHT)
        self.movetime.Bind(wx.EVT_KEY_DOWN, self.OnMovetime)
        self.movetimeInf = wx.StaticText(subPanel2, -1, ' minutes (enter a number between 1-999)')
        self.movetimeSzr = wx.BoxSizer(wx.HORIZONTAL)
        self.movetimeSzr.Add(self.movetime, 0, 0)
        self.movetimeSzr.Add(self.movetimeInf, 0, wx.ALIGN_CENTER_VERTICAL)

        self.inctimeLbl = wx.StaticText(subPanel2, -1, 'Incremental clock time:')
        self.inctime = wx.TextCtrl(subPanel2, -1, '0', size = (40,-1), style = wx.TE_RIGHT)
        self.inctime.Bind(wx.EVT_KEY_DOWN, self.OnMovetime)
        self.inctimeInf = wx.StaticText(subPanel2, -1, ' seconds (enter a number between 0-999)')
        self.inctimeSzr = wx.BoxSizer(wx.HORIZONTAL)
        self.inctimeSzr.Add(self.inctime, 0, 0)
        self.inctimeSzr.Add(self.inctimeInf, 0, wx.ALIGN_CENTER_VERTICAL)

        returnBtn = ChessGradientButton(subPanel2, 4, -1, ChessImages.getArrow_leftBitmap(), "", size=(25,25))
        font = returnBtn.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        returnBtn.SetFont(font)
        returnBtn.Bind(wx.EVT_BUTTON, self.OnReturn)
        self.createBtn = ChessGradientButton(subPanel2, 4, -1, None, "Create new game", size=(120,25))
        self.createBtn.SetFont(font)
        self.createBtn.Bind(wx.EVT_BUTTON, self.OnCreate)

        formSizer = wx.BoxSizer(wx.VERTICAL)
        gridSizer = wx.FlexGridSizer(rows=4, cols=2, hgap=5, vgap=5)
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)

        gridSizer.Add(self.opponentLbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALIGN_RIGHT)
        gridSizer.Add(self.opponentSzr, 0, 0)
        gridSizer.Add(self.colourLbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALIGN_RIGHT)
        gridSizer.Add(self.colour, 0, 0)
        gridSizer.Add(self.movetimeLbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALIGN_RIGHT)
        gridSizer.Add(self.movetimeSzr, 0, 0)
        gridSizer.Add(self.inctimeLbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALIGN_RIGHT)
        gridSizer.Add(self.inctimeSzr, 0, 0)

        btnSizer.Add(returnBtn, 0, wx.ALL, 5)
        btnSizer.Add(self.createBtn, 0, wx.ALL, 5)

        formSizer.Add((0,35), 0, 0)
        formSizer.Add(gridSizer, 0, wx.ALL|wx.EXPAND, 5)
        formSizer.Add(btnSizer, 0, wx.ALL|wx.CENTER, 10)
        subPanel2.SetSizer(formSizer)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add(subPanel1, 1, wx.EXPAND)
        vSizer.Add((0,20), 0, 0, 0)
        vSizer.Add(subPanel2, 0, wx.CENTER)
        self.SetSizer(vSizer)

    def OnOpponentType(self, event):
        if self.opponentType.GetStringSelection() == "friend":
            self.permidLbl.Show()
            self.permid.Show()
            self.minRatingLbl.Hide()
            self.minRating.Hide()
            self.maxRatingLbl.Hide()
            self.maxRating.Hide()
        else:
            self.permidLbl.Hide()
            self.permid.Hide()
            self.minRatingLbl.Show()
            self.minRating.Show()
            self.maxRatingLbl.Show()
            self.maxRating.Show()
        self.opponentSzr.Layout()

    def OnPermid(self, event):
        self.permid.SetForegroundColour(wx.BLACK)
        event.Skip()

    def OnMinRating(self, event):
        self.minRating.SetForegroundColour(wx.BLACK)
        event.Skip()

    def OnMaxRating(self, event):
        self.maxRating.SetForegroundColour(wx.BLACK)
        event.Skip()

    def OnMovetime(self, event):
        self.movetime.SetForegroundColour(wx.BLACK)
        event.Skip()

    def OnCreate(self, event):
        self.createBtn.Disable()
        self.createBtn.SetForegroundColour(wx.Colour(150,150,150))
        save = True

        choice = self.opponentType.GetSelection()
        min_rating = 1
        max_rating = 100
        peer_id = -1
        #Retrieve min & max ratings, if given
        if choice == 0:
            # If we are creating a random invite, first check if there are connected peers
            gcg = GameCastGossip.getInstance()
            if (len(gcg.connected_game_buddies) + len(gcg.connected_random_peers)) == 0:
                dialog = wx.MessageDialog(None, 'There are currectly no peers connected, please try again later.', 'No connected peers', wx.OK | wx.ICON_EXCLAMATION)
                dialog.ShowModal()
                self.createBtn.Enable()
                self.createBtn.SetForegroundColour(wx.WHITE)
                return
            text1 = self.minRating.GetValue().strip()
            text2 = self.maxRating.GetValue().strip()
            if text1 != '':
                if text1.isdigit() and int(text1) >= 0 and int(text1) <= 9999:
                    min_rating = int(text1)
                else:
                    save = False
                    if sys.platform != 'darwin':
                        self.minRating.SetForegroundColour(wx.RED)
                    self.minRating.SetValue('Error')
            if text2 != '':
                if text2.isdigit() and int(text2) >= 0 and int(text2) <= 9999:
                    max_rating = int(text2)
                else:
                    save = False
                    if sys.platform != 'darwin':
                        self.maxRating.SetForegroundColour(wx.RED)
                    self.maxRating.SetValue('Error')
            if min_rating > max_rating:
                save = False
                if sys.platform != 'darwin':
                    self.minRating.SetForegroundColour(wx.RED)
                    self.maxRating.SetForegroundColour(wx.RED)
                self.minRating.SetValue('Error')
                self.maxRating.SetValue('Error')
        #Retrieve permid, if given
        elif choice == 1:
            text = self.permid.GetValue().strip()
            try:
                peer_id = self.peer_db.getPeerID(str2bin(text))
            except:
                peer_id = None
            if not peer_id:
                save = False
                if sys.platform != 'darwin':
                    self.permid.SetForegroundColour(wx.RED)
                self.permid.SetValue('Error')

        #Retrieve the colour that the player will use
        colour = self.colour.GetStringSelection()

        #Retrieve the clock time that each of the players should start with
        text = self.movetime.GetValue().strip()
        if text != '':
            if text.isdigit() and int(text) >= 1 and int(text) <= 999:
                time = int(text)
            else:
                save = False
                if sys.platform != 'darwin': # on mac can't reset the font colour back to black again
                    self.movetime.SetForegroundColour(wx.RED)
                self.movetime.SetValue('Error')

        #Retrieve the time with which the player's clock is incremented per move
        text = self.inctime.GetValue().strip()
        if text != '':
            if text.isdigit() and int(text) >= 0 and int(text) <= 999:
                inc = int(text)
            else:
                save = False
                if sys.platform != 'darwin': # on mac can't reset the font colour back to black again
                    self.inctime.SetForegroundColour(wx.RED)
                self.inctime.SetValue('Error')

        if save:
            # Create a game dict with the appropriate keys, which we will add to the db

            game = {}
            game['game_id'] = self.gamecast_db.getNextGameID(0)
            game['owner_id'] = 0
            game['winner_permid'] = ''
            game['moves'] = bencode([])
            mypermid = bin2str(self.session.get_permid())
            players = {}
            players[mypermid] = colour
            game['players'] = bencode(players)
            game['gamename'] = 'chess'
            game['time'] = time
            game['inc'] = inc
            game['is_finished'] = 0
            game['lastmove_time'] = 0
            game['creation_time'] = 0

            self.gamecast_db.addGame(game)

            # For every game we can add multiple invites. Invites are either targetted at a specific peer,
            # or at any peer that has a rating within a certain range.

            invite = {}
            invite['target_id'] = peer_id
            invite['game_id'] = game['game_id']
            invite['min_rating'] = min_rating
            invite['max_rating'] = max_rating
            invite['time'] = time
            invite['inc'] = inc
            invite['gamename'] = 'chess'
            if colour == 'white':
                invite['colour'] = 'black'
            else:
                invite['colour'] = 'white'

            gc = GameCast.getInstance()
            gc._executeSeekOrMatch(invite)
            self.LoadMyInvites()
        wx.Yield()
        self.createBtn.Enable()
        self.createBtn.SetForegroundColour(wx.WHITE)

    def OnReturn(self, event):
        self.GetParent().SwitchPanel("challenge")


class ChessMyChallengeList(ChessList):

    def __init__(self, parent, data = {}, *args, **kwargs):
	columns = { 0 : ("Opponent", wx.LIST_FORMAT_LEFT, 220),
	            1 : ("Rating", wx.LIST_FORMAT_LEFT, 100),
		    2 : ("I play as", wx.LIST_FORMAT_LEFT, 90),
		    3 : ("Start / inc time", wx.LIST_FORMAT_LEFT, 130),
		    4 : ("Expires in", wx.LIST_FORMAT_LEFT, 100) }
        ChessList.__init__(self, parent, columns = columns, data = data, filtered = False, *args, **kwargs)
        self.idx_human = self.il.Add(ChessImages.getHumanBitmap())
        self.SortListItems(4, 1)

    def OnGetItemColumnImage(self, row, col):
        if col == 0:
            return self.idx_human
        return -1