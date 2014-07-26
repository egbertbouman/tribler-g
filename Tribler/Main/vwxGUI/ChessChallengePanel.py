# Written by Egbert Bouman
# see LICENSE for license information

import wx
import Tribler.Main.vwxGUI.ChessImages as ChessImages

from time import time
from datetime import datetime, timedelta
from Tribler.Core.simpledefs import *
from Tribler.Core.Utilities.utilities import show_permid_short
from Tribler.Core.GameCast.GameCast import *
from Tribler.Main.vwxGUI.ChessInterface import FICSInterface
from Tribler.Main.vwxGUI.ChessWidgets import *
from Tribler.Main.vwxGUI.ChessBoardPanel import ChessBoardPanel
from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.Main.Utility import *

ID_TIMER = wx.NewId()

class ChessChallengePanel(wx.Panel):
    """Panel subclass for listing open chess challenges within the Tribler network"""

    def __init__(self, parent, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.session = self.utility.session
        self.session.add_observer(self.DatabaseCallback, NTFY_GAMECAST, [NTFY_INSERT, NTFY_UPDATE, NTFY_DELETE], 'GameInvite')
        self.peer_db = self.guiUtility.utility.session.open_dbhandler(NTFY_PEERS)
        self.gamecast_db = self.guiUtility.utility.session.open_dbhandler(NTFY_GAMECAST)
        self.gamecast = GameCast.getInstance()
        self.guiserver = ChessTaskQueue.getInstance()
        self.fics = None
        self.AddComponents()
        self.currentInvites_list = {}
        self.currentFICSInvites_list = {}
        self.LoadP2PInvites()
        self.timer = wx.Timer(self, ID_TIMER)
        wx.EVT_TIMER(self, ID_TIMER, self.UpdateTimeValues)
        self.timer.Start(1000)

    def DatabaseCallback(self, subject, changeType, objectID, *args):
        self.guiserver.add_task(self.LoadP2PInvites, id=7)

    def LoadP2PInvites(self):
        mypermid = self.session.get_permid()
        myrating = self.gamecast.getRating(mypermid, 'chess')[0]
        reply_history = [k for k, v in self.currentInvites_list.iteritems() if v[5]]
        self.currentInvites = self.gamecast_db.getCurrentInvites(myrating, 'chess')
        self.currentInvites_list = {}
        index = 0
        while index < len(self.currentInvites):
            invite = self.currentInvites[index]
            key = (invite['owner_id'], invite['invite_id'])
            if invite['target_id'] < 0:
                isHandled = (self.gamecast.getInviteState(invite, 'recv', CMD_UNSEEK) or \
                             self.gamecast.getInviteState(invite, 'recv', CMD_ACCEPT) or \
                             self.gamecast.getInviteState(invite, 'recv', CMD_DECLINE))
            else:
                isHandled = (self.gamecast.getInviteState(invite, 'send', CMD_ACCEPT) or \
                             self.gamecast.getInviteState(invite, 'send', CMD_DECLINE))
            if not isHandled:
                row = [None]*6
                peer = self.gamecast.getPeer(invite['owner_id'])
                if not peer: continue
                owner_permid = peer['permid']
                row[0] = show_permid_short(owner_permid)
                if peer and peer.has_key('name'):
                    row[0] = peer['name']
                row[1] = str(int(self.gamecast.getRating(owner_permid, 'chess')[0]))
                if invite['target_id'] >= 0:
                    row[0] += " (personal invite)"
                row[2] = invite['colour']
                row[3] = '%3d / %3d' % (invite['time'], invite['inc'])
                invite['timeout_time'] = invite['creation_time']+INV_EXPIRE_TIME
                now_datetime = datetime.today()
                exp_datetime = datetime.fromtimestamp(invite['timeout_time'])
                rmv_datetime = datetime.fromtimestamp(invite['timeout_time']+2*60)
                if now_datetime < exp_datetime:
                    delta = exp_datetime - now_datetime
                    row[4] = 'expires in '+str(delta).split('.')[0]
                elif now_datetime < rmv_datetime:
                    row[4] = 'challenge expired'
                else:
                    self.currentInvites.pop(index)
                    continue
                if invite['target_id'] < 0 and self.gamecast.getInviteState(invite, 'send', CMD_PLAY):
                    row[5] = 1
                elif key in reply_history:
                    row[5] = 1
                self.currentInvites_list[key] = row
            index += 1
        new_data = dict(self.currentInvites_list.items() + self.currentFICSInvites_list.items())
        self.joinList.SetData(new_data)

    def LoadFICSInvites(self):
        if not (self.ficsCheckBox.GetValue() and self.fics and not self.fics.done and self.fics.sentStyle):
            self.currentFICSInvites = []
            self.currentFICSInvites_list = {}
            new_data = dict(self.currentInvites_list.items())
            self.joinList.SetData(new_data)
            return
        self.currentFICSInvites = self.fics.invites
        self.currentFICSInvites_list = {}
        for invite in self.currentFICSInvites:
            row = [None]*6
            row[0] = invite['owner']
            row[1] = '-'#invite['rating']
            row[2] = invite['colour']
            row[3] = '%3s / %3s' % (invite['time'], invite['inc'])
            row[4] = '-'
            key = 'FICS%s' % invite['invite_id']
            self.currentFICSInvites_list[key] = row
        new_data = dict(self.currentInvites_list.items() + self.currentFICSInvites_list.items())
        self.joinList.SetData(new_data)

    def UpdateTimeValues(self, event):
        # Every 2000s send a command to FICS (keeps the connection from timing out).
        now = int(time())
        if (now % 2000) == 0:
            if self.fics: self.fics.executeSought()

        # Don't update GUI elements if the panel is not being displayed.
        if not self.IsShownOnScreen():
            return

        # Update the clocks for GameCast invites
        num_cols = self.joinList.GetColumnCount()
        now_datetime = datetime.today()
        for invite in self.currentInvites:
            if invite['status'] <= 2**4:
                if not invite.has_key('timeout_time'):
                    continue
                timeout_time = invite['timeout_time']
                exp_datetime = datetime.fromtimestamp(timeout_time)
                if now_datetime < exp_datetime:
                    delta = exp_datetime - now_datetime
                    new_data = 'expires in '+str(delta).split('.')[0]
                else:
                    new_data = 'challenge expired'
                key = (invite['owner_id'], invite['invite_id'])
                self.joinList.SetCell(key, num_cols-1, new_data)

        # Every 5s request FICS for a list of current invites
        if (now % 5) == 0:
            if self.ficsCheckBox.GetValue() and self.fics and not self.fics.done and self.fics.sentStyle:
                self.fics.executeSought()

        # Every 60s, call LoadP2PInvites
        if (now % 60) == 0:
            self.guiserver.add_task(self.LoadP2PInvites, id=7)

    def AddComponents(self):
        subPanel = ChessSubPanel(self, title = 'Join a game')

        self.joinList = JoinList(subPanel, data = {})
        joinLabel = wx.StaticText(subPanel, -1, "To join a game, please accept one of the following challanges (by double clicking):")
        self.ficsCheckBox = wx.CheckBox(subPanel, -1, 'Import unrated challanges from FICS', (10, 10))
        self.ficsCheckBox.SetValue(False)
        wx.EVT_CHECKBOX(self, self.ficsCheckBox.GetId(), self.OnFICS)

        self.createLabel = wx.StaticText(subPanel, -1, "Or, you can ")
        self.createBtn = ChessGradientButton(subPanel, 4, -1, None, "Create a new game", size=(-1,25))
        font = self.createBtn.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.createBtn.SetFont(font)
        self.createBtn.Bind(wx.EVT_BUTTON, self.OnCreate)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(self.createLabel, 0, wx.ALIGN_CENTER_VERTICAL)
        hSizer.Add((5,0), 0, 0, 0)
        hSizer.Add(self.createBtn, 0, 0)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,25), 0, 0, 0)
        vSizer.Add(joinLabel, 0, wx.ALL | wx.EXPAND, 10)
        vSizer.Add(self.ficsCheckBox, 0, wx.LEFT | wx.RIGHT, 10)
        vSizer.Add(self.joinList, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        vSizer.Add(hSizer, 0, wx.ALL, 10)
        subPanel.SetSizer(vSizer)

        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add(subPanel, 1, wx.EXPAND)
        self.SetSizer(vSizer)

    def Show(self):
        wx.Panel.Show(self)
        # Update immediately when the panel is shown
        self.UpdateTimeValues(None)

    def OnCreate(self, event):
        self.GetParent().SwitchPanel("create")

    def OnFICS(self, event):
        if self.ficsCheckBox.GetValue():
            self.fics = FICSInterface.getInstance()
            self.fics.done = False
            if not self.fics.isAlive():
                try:
                    self.fics.start()
                except:
                    pass
            self.fics.registerInviteCallback(self.LoadFICSInvites)
        else:
            self.LoadFICSInvites()

    def OnInvite(self, index):
        if isinstance(index, str) and index.startswith('FICS'):
            if self.fics.game and not self.fics.game['is_finished']:
                dialog = wx.MessageDialog(None, 'Only allowed to play one FICS game at a time.', 'Unable to create game', wx.OK | wx.ICON_EXCLAMATION)
                dialog.ShowModal()
                return
            if self.currentFICSInvites_list[index][-1]:
                return
            self.currentFICSInvites_list[index][-1] = 1
            self.joinList.itemDataMap[index][5] = 1
            self.joinList.Refresh()
            if not self.fics.executePlay(index[4:]):
                dialog = wx.MessageDialog(None, 'Selected challenge is already taken by another gamer.', 'Unable to create game', wx.OK | wx.ICON_EXCLAMATION)
                dialog.ShowModal()
                return
            wx.CallLater(100, self.fics.executeSought)
        else:
            if self.currentInvites_list[index][4] == 'challenge expired':
                return
            if self.currentInvites_list[index][-1]:
                return
            self.currentInvites_list[index][-1] = 1
            self.joinList.itemDataMap[index][5] = 1
            self.joinList.Refresh()
            owner_id, invite_id = index
            invite = self.gamecast.getInvite(owner_id, invite_id)
            if invite['target_id'] >= 0:
                self.gamecast.executeAccept(owner_id, invite_id)
            else:
                self.gamecast.executePlay(owner_id, invite_id)
            wx.CallLater(100, self.LoadP2PInvites)


class JoinList(ChessList):

    def __init__(self, parent, data = {}, *args, **kwargs):
        columns = { 0 : ("Opponent", wx.LIST_FORMAT_LEFT, 220),
                    1 : ("Rating", wx.LIST_FORMAT_LEFT, 100),
                    2 : ("I play as", wx.LIST_FORMAT_LEFT, 90),
                    3 : ("Start / inc time", wx.LIST_FORMAT_LEFT, 130),
                    4 : ("Status", wx.LIST_FORMAT_LEFT, 100) }
        ChessList.__init__(self, parent, columns = columns, data = data, filtered = False, *args, **kwargs)
        self.idx_human = self.il.Add(ChessImages.getHumanBitmap())
        self.idx_removed = self.il.Add(ChessImages.getRemovedBitmap())
        self.idx_responded = self.il.Add(ChessImages.getBusyBitmap())
        self.idx_computer = self.il.Add(ChessImages.getComputerBitmap())
        self.SortListItems(4, 1)

    def OnItemActivated(self, event):
        item = event.m_itemIndex
        index = self.itemIndexMap[item]
        self.GetParent().GetParent().OnInvite(index)

    def OnGetItemColumnImage(self, row, col):
        if col == 0:
            item = self.itemIndexMap[row]
            if self.itemDataMap[item][-1] == 1:
                return self.idx_responded
            elif self.itemDataMap[item][-1] == 2:
                return self.idx_removed
            elif isinstance(item, str) and item.startswith('FICS') and self.itemDataMap[item][col].endswith('(C)'):
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