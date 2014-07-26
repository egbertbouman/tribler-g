# Written by Egbert Bouman
# see LICENSE for license information

import wx
import sys
import copy
import Tribler.Main.vwxGUI.ChessImages as ChessImages

from datetime import datetime, timedelta
from Tribler.Core.simpledefs import *
from Tribler.Core.GameCast.ChessBoard import ChessBoard
from Tribler.Core.GameCast.GameCast import GameCast, AGREE_ABORT, AGREE_DRAW, CLOSE_MOVE, RESIGN
from Tribler.Main.vwxGUI.ChessInterface import *
from Tribler.Main.vwxGUI.ChessWidgets import *
from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.Core.Utilities.utilities import show_permid_short
from Tribler.Main.Utility import *

ID_TIMER = wx.NewId()

class ChessBoardPanel(wx.Panel):

    # Different types of players
    HUMAN    = 0
    COMPUTER = 1

    def __init__(self, parent, *args):
        wx.Panel.__init__(self, parent, *args)
        self.foregroundColour = wx.Colour(56,122,174)
        self.backgroundColour = wx.Colour(216,233,240)
        self.SetBackgroundColour(self.backgroundColour)
        self.xmargin = 10
        self.ymargin = 10
        self.pieceSize = 55
        self.pieces = [{},{}]
        self.markPos = [-1,-1]
        self.mousePos = [-1,-1]
        self.validMoves = []
        self.chess = ChessBoard()
        self.my_colour = ChessBoard.WHITE
        self.opponent_colour = ChessBoard.BLACK
        self.game = {'moves':[], 'is_finished': 0}
        self.AddComponents()
        self.LoadPieces()
        self.Bind(wx.EVT_MOUSE_EVENTS, self.OnMouseAction)
        self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.timer = wx.Timer(self, ID_TIMER)
        wx.EVT_TIMER(self, ID_TIMER, self.Update)
        self.timer.Start(1000)

    def AddComponents(self):
        self.gameInfo = ChessInfoPanel(self)
        self.gameRecordPanel = ChessSubPanel(self, title = "Game Record")
        self.gameRecordPanel.SetMinSize((-1,270))
        self.gameRecordPanel.SetMaxSize((-1,270))
        self.gameRecord = wx.TextCtrl(self.gameRecordPanel, size=(-1,-1), style=wx.TE_MULTILINE | wx.NO_BORDER | wx.HSCROLL & wx.VSCROLL)
        self.gameRecord.SetEditable(False)
        font = wx.Font(10, wx.TELETYPE, wx.NORMAL, wx.NORMAL, False)
        if sys.platform == 'linux2':
            font.SetFaceName('Nimbus Mono L')
        else:
            font.SetFaceName('Courier New')
        self.gameRecord.SetFont(font)
        vSizer = wx.BoxSizer(wx.VERTICAL)
        vSizer.Add((0,35), 0, 0, 0)
        vSizer.Add(self.gameRecord, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        vSizer.Add((0,10), 0, 0, 0)
        self.gameRecordPanel.SetSizer(vSizer)

        self.vSizer = wx.BoxSizer(wx.VERTICAL)
        self.vSizer.Add((0,self.ymargin), 0, 0, 0)
        self.vSizer.Add(self.gameInfo, 0, wx.EXPAND)
        self.vSizer.Add((0,10), 0, 0, 0)
        self.vSizer.Add(self.gameRecordPanel, 0, wx.EXPAND)
        self.vSizer.Add((0,10), 0, 0, 0)

        self.hSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.hSizer.Add((self.pieceSize*8+self.xmargin,0), 0, 0, 0)
        self.hSizer.Add((15,0), 0, 0, 0)
        self.hSizer.Add(self.vSizer, 1, wx.EXPAND)
        self.hSizer.Add((self.xmargin,0), 0, 0, 0)
        self.SetSizer(self.hSizer)

    def LoadPieces(self):
        # The first index of self.pieces represents the kind of background: 0 - for a light
        # backgroundcolour, 1 - for a dark background. The second index represents the type of
        # piece: k (king), q (queen), b (bishop), n (night), r (rook), p (pawn). Uppercase letters
        # are use for white pieces, lowercase for black pieces. Empty squares are indexed by a period.
        self.pieces[0]["r"] = ChessImages.getBrwBitmap()
        self.pieces[0]["n"] = ChessImages.getBnwBitmap()
        self.pieces[0]["b"] = ChessImages.getBbwBitmap()
        self.pieces[0]["k"] = ChessImages.getBkwBitmap()
        self.pieces[0]["q"] = ChessImages.getBqwBitmap()
        self.pieces[0]["p"] = ChessImages.getBpwBitmap()
        self.pieces[0]["R"] = ChessImages.getWrwBitmap()
        self.pieces[0]["N"] = ChessImages.getWnwBitmap()
        self.pieces[0]["B"] = ChessImages.getWbwBitmap()
        self.pieces[0]["K"] = ChessImages.getWkwBitmap()
        self.pieces[0]["Q"] = ChessImages.getWqwBitmap()
        self.pieces[0]["P"] = ChessImages.getWpwBitmap()
        self.pieces[0]["."] = ChessImages.getWBitmap()
        self.pieces[1]["r"] = ChessImages.getBrbBitmap()
        self.pieces[1]["n"] = ChessImages.getBnbBitmap()
        self.pieces[1]["b"] = ChessImages.getBbbBitmap()
        self.pieces[1]["k"] = ChessImages.getBkbBitmap()
        self.pieces[1]["q"] = ChessImages.getBqbBitmap()
        self.pieces[1]["p"] = ChessImages.getBpbBitmap()
        self.pieces[1]["R"] = ChessImages.getWrbBitmap()
        self.pieces[1]["N"] = ChessImages.getWnbBitmap()
        self.pieces[1]["B"] = ChessImages.getWbbBitmap()
        self.pieces[1]["K"] = ChessImages.getWkbBitmap()
        self.pieces[1]["Q"] = ChessImages.getWqbBitmap()
        self.pieces[1]["P"] = ChessImages.getWpbBitmap()
        self.pieces[1]["."] = ChessImages.getBBitmap()

    def Update(self, event):
        if not self.IsShownOnScreen():
            return
        self.UpdateClock()
        self.UpdateRequests()
        self.UpdateOpponentMove()

    def UpdateClock(self):
        pass

    def UpdateRequests(self):
        pass

    def UpdateOpponentMove(self):
        # Before repainting check whether the opponent has made a move yet
        if self.IsOpponentToMove():
            opponent = 'white' if self.opponent_colour == ChessBoard.WHITE else 'black'
            if self.game['moves'][-1][0] != opponent:
                return
            move = self.game['moves'][-1][1]
            res = self.chess.addTextMove(move)
            if not res and self.chess.getReason() == self.chess.MUST_SET_PROMOTION:
                self.chess.setPromotion(self.chess.QUEEN)
                res = self.chess.addTextMove(move)
            if res:
                self.UpdateRecord()
                self.UpdateStatus()
                self.Refresh()

    def UpdateRecord(self):
        # Output the last move to the game record (if any)
        is_finished = self.game.get('is_finished', 0)
        if self.chess.isGameOver() or not is_finished:
            move = self.chess.getLastTextMove(self.chess.LAN)
            if move:
                if move[0] in ['K', 'Q', 'R', 'B', 'N']:
                    move = move[1:]
                colour = 'black' if self.chess.getTurn() == ChessBoard.WHITE else 'white'
                move = '%s: %3d.%s\n' % (colour, self.chess.getCurrentMove(), move)
                lines = self.gameRecord.GetValue().splitlines()
                if not lines or (move not in lines):
                    self.gameRecord.AppendText(move)
            if self.chess.isGameOver():
                result = self.chess.getGameResult()
                gameResults = ["","White wins!\n","Black wins!\n","Draw by stalemate\n", \
                               "Draw by the fifty move rule\n","Draw by the three repetitions rule\n"]
                self.gameRecord.AppendText(gameResults[result])
        # Output the last move to the game record (if any)
        else:
            if is_finished == AGREE_ABORT: self.gameRecord.AppendText("Game aborted\n")
            if is_finished == AGREE_DRAW:  self.gameRecord.AppendText("Draw by agreement\n")
            if is_finished == RESIGN:      self.gameRecord.AppendText("%s resigns\n" % \
                                                                     ('White' if self.GetWinnerString() == 'black' else 'Black'))

    def UpdateStatus(self):
        status = ''
        # In case the game ended in a normal way
        if self.chess.isGameOver():
            self.validMoves = []
            self.markPos[0] = -1
            self.markPos[1] = -1
            result = self.chess.getGameResult()
            if result == 1: status = 'white wins!'
            if result == 2: status = 'black wins!'
            if result >  2: status = 'draw!'
        # In case the game ended due to an abort/draw by agreement or one of the players resigning
        elif self.game.get('is_finished', 0):
            if self.game['is_finished'] == AGREE_ABORT: status = 'aborted'
            if self.game['is_finished'] == AGREE_DRAW:  status = 'draw!'
            if self.game['is_finished'] == RESIGN:      status = '%s wins!' % self.GetWinnerString()
        # In case the game is ongoing
        else:
            status = 'white to move' if self.chess.getTurn() == ChessBoard.WHITE else 'black to move'
        self.gameInfo.UpdateInfo(3, 1, status)

    def Reset(self):
        self.gameRecord.Clear()
        self.chess.resetBoard()
        self.markPos = [-1,-1]
        self.mousePos = [-1,-1]
        self.validMoves = []

    def IsOpponentToMove(self):
        return (self.game and not self.chess.isGameOver() and \
                self.game['moves'] and self.chess.getTurn() != self.my_colour)

    def IsGameExpired(self):
        return False

    def AddMove(self, move):
        self.game['moves'].append((self.my_colour, move, '0'))

    def GetWinnerString(self, UC = False):
        result = self.chess.getGameResult()
        winner_colour = ''
        if result == 1: winner_colour = 'white'
        if result == 2: winner_colour = 'black'
        if UC and winner_colour:
            winner_colour = winner_colour.title()
        return winner_colour

    def OnEraseBackground(self, event):
        pass

    def OnPaint(self, event):
        board = self.chess.getBoard()
        letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
        numbers = ['8', '7', '6', '5', '4', '3', '2', '1']
        square = copy.copy(self.markPos)
        # Reverse the board
        if self.my_colour == ChessBoard.BLACK:
            for row in board:
                row.reverse()
            board.reverse()
            letters.reverse()
            numbers.reverse()
            square[0] = 7 - square[0] if square[0] >= 0 else -1
            square[1] = 7 - square[1] if square[0] >= 0 else -1
        width, height = self.GetClientSizeTuple()
        buffer = wx.EmptyBitmap(width, height)
        # Use double duffered drawing to prevent flickering
        dc = wx.BufferedPaintDC(self, buffer)
        dc.SetBackground(wx.Brush(self.backgroundColour))
        dc.Clear()
        # Draw the individual bitmaps to the buffer
        for y, row in enumerate(board):
            for x, piece in enumerate(row):
                dc.DrawBitmap(self.pieces[(x+y)%2][piece], self.pieceSize*x+self.xmargin, \
                              self.pieceSize*y+self.ymargin)
        # Draw letters and numbers along the sides of the chess board
        font =  self.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        dc.SetFont(font)
        dc.SetTextForeground(wx.Colour(56,122,174))
        for x, letter in enumerate(letters):
            dc.DrawText(letter, self.pieceSize*(x+0.45)+self.xmargin, self.pieceSize*8+self.ymargin)
        for x, number in enumerate(numbers):
            dc.DrawText(number, self.xmargin-10, self.pieceSize*(x+0.45)+self.ymargin)

        # Draw last move to the board
        move = self.chess.getLastMove()
        if move:
            sq1 = move[0]
            sq2 = move[1]
            # Is the board reversed?
            if self.my_colour == ChessBoard.BLACK:
                sq1 = (7 - sq1[0], 7 - sq1[1])
                sq2 = (7 - sq2[0], 7 - sq2[1])
            gdc = wx.GCDC(dc)
            gdc.SetPen(wx.Pen('#ffffff', 2, wx.SOLID))
            gdc.SetBrush(wx.Brush(wx.Color(255, 255, 255, 128), style=wx.TRANSPARENT))
            gdc.DrawRectangle(sq1[0]*self.pieceSize+self.xmargin, \
                             sq1[1]*self.pieceSize+self.ymargin, \
                             self.pieceSize, self.pieceSize)
            gdc.DrawRectangle(sq2[0]*self.pieceSize+self.xmargin, \
                             sq2[1]*self.pieceSize+self.ymargin, \
                             self.pieceSize, self.pieceSize)

        # Draw a rectangle around the square that is currently selected
        if square[0] != -1:
            dc.SetPen(wx.Pen('#ff3300', 4, wx.SOLID))
            dc.SetBrush(wx.Brush('#ff3300', style=wx.TRANSPARENT))
            dc.DrawRectangle(square[0]*self.pieceSize+self.xmargin, \
                             square[1]*self.pieceSize+self.ymargin, \
                             self.pieceSize, self.pieceSize)
        # Draw rectangles around squares that a player can move to
        for move in self.validMoves:
            dc.SetPen(wx.Pen('#ff3300', 4, wx.SOLID))
            dc.SetBrush(wx.Brush('#ff3300', style=wx.TRANSPARENT))
            dc.DrawRectangle(move[0]*self.pieceSize+self.xmargin, \
                             move[1]*self.pieceSize+self.ymargin, \
                             self.pieceSize, self.pieceSize)

    def OnMouseAction(self, event):
        board = self.chess.getBoard()
        turn = self.chess.getTurn()
        if not self.chess.isGameOver() and not self.game.get('is_finished', 0):
            # If the mouse is moving, save the position
            if event.Moving():
                mx, my = event.GetPositionTuple()
                self.mousePos[0] = (mx-self.xmargin)/self.pieceSize
                self.mousePos[1] = (my-self.ymargin)/self.pieceSize
                # Check whether the mouse is on the chessboard or not.
                if self.mousePos[0] < 0 or self.mousePos[0] >= 8 or \
                   self.mousePos[1] < 0 or self.mousePos[1] >= 8:
                    self.mousePos = [-1,-1]
                # Invert coordinates in case that board is displayed upside-down
                if self.my_colour == ChessBoard.BLACK:
                    self.mousePos[0] = 7 - self.mousePos[0]
                    self.mousePos[1] = 7 - self.mousePos[1]
            elif event.ButtonDown():
                if self.mousePos[0] != -1:
                    # If the mouse is double-clicked, deselect the square
                    if self.markPos[0] == self.mousePos[0] and self.markPos[1] == self.mousePos[1]:
                        self.markPos[0] = -1
                        self.validMoves = []
                    else:
                        if self.IsGameExpired():
                            dialog = wx.MessageDialog(None, 'This game has ended because one of the players failed to move in time.', 'Game timed out', wx.OK | wx.ICON_EXCLAMATION)
                            dialog.ShowModal()
                            return
                        # If one of the chess pieces is clicked, and the piece is of the correct colour,
                        # select the square and calculate what moves can be made.
                        if turn == self.my_colour and \
                           ((turn == ChessBoard.WHITE and board[self.mousePos[1]][self.mousePos[0]].isupper()) or \
                            (turn == ChessBoard.BLACK and board[self.mousePos[1]][self.mousePos[0]].islower())):
                            self.markPos[0] = self.mousePos[0]
                            self.markPos[1] = self.mousePos[1]
                            self.validMoves = self.chess.getValidMoves(tuple(self.markPos))
                            if self.my_colour == ChessBoard.BLACK:
                                self.validMoves = [(7-x, 7-y) for (x, y) in self.validMoves]
                        else:
                            # If a square on the board is already selected, try to make the next move.
                            if self.markPos[0] != -1:
                                res = self.chess.addMove(self.markPos, self.mousePos)
                                if not res and self.chess.getReason() == self.chess.MUST_SET_PROMOTION:
                                    self.chess.setPromotion(self.chess.QUEEN)
                                    res = self.chess.addMove(self.markPos, self.mousePos)
                                if res:
                                    move = self.chess.getLastTextMove(self.chess.AN)
                                    self.AddMove(move)
                                    self.UpdateRecord()
                                    self.UpdateStatus()
                                    self.markPos[0] = -1
                                    self.validMoves = []
                # Make sure the panel is repainted, in order to reflect the changes.
                self.Refresh()


class GCBoard(ChessBoardPanel):

    def __init__(self, parent, *args):
        ChessBoardPanel.__init__(self, parent, *args)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.session = self.utility.session
        self.session.add_observer(self.DatabaseCallback, NTFY_GAMECAST, [NTFY_UPDATE], 'Game')
        self.gamecast = GameCast.getInstance()
        self.gamecast_db = self.guiUtility.utility.session.open_dbhandler(NTFY_GAMECAST)
        self.guiserver = ChessTaskQueue.getInstance()
        self.peer_db = self.guiUtility.utility.session.open_dbhandler(NTFY_PEERS)
        self.my_clock = None
        self.opponent_clock = None
        self.rr_index = 0
        self.finished = 0

    def DatabaseCallback(self, subject, changeType, objectID, *args):
        self.guiserver.add_task(self.LoadGame, id=8)

    def LoadGame(self):
        if self.game.has_key('owner_id') and self.game.has_key('game_id'):
            self.game = self.gamecast.getGame(self.game['owner_id'], self.game['game_id'])

    def AddComponents(self):
        ChessBoardPanel.AddComponents(self)
        self.backButton = ChessGradientButton(self, 4, -1, None, "Back to overview", size=(100,25))
        font = self.backButton.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.backButton.SetFont(font)
        self.backButton.Bind(wx.EVT_BUTTON, self.OnBack)
        self.popupButton = ChessGradientButton(self, 4, -1, ChessImages.getGoBitmap(), "", size=(25,25))
        self.popupButton.Bind(wx.EVT_BUTTON, self.OnPopup)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(self.backButton, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 1)
        hSizer.Add(self.popupButton, 0, wx.ALIGN_RIGHT)
        self.vSizer.Add(hSizer, 0, wx.EXPAND)
        self.vSizer.Add((0,24), 0, 0, 0)
        self.hSizer.Layout()

    def SetGame(self, game):
        self.timer.Stop()
        self.rr_index = 0
        self.Reset()
        self.game = game
        player_permids = self.game['players'].keys()
        try:
            player_permids.remove(self.session.get_permid())
        except:
            pass
        self.opponent_permid = player_permids[0]
        self.opponent_colour = ChessBoard.WHITE if self.game['players'][self.opponent_permid] == 'white' else ChessBoard.BLACK
        self.my_permid = self.session.get_permid()
        self.my_colour = ChessBoard.WHITE if self.game['players'][self.my_permid] == 'white' else ChessBoard.BLACK
        opponent = show_permid_short(self.opponent_permid)
        peer = self.peer_db.getPeer(self.opponent_permid)
        if peer and peer['name']:
            opponent = peer['name']
        self.player = self.COMPUTER if 'ChessBot'in opponent else self.HUMAN
        self.gameInfo.UpdateInfo(0, 1, opponent)
        self.gameInfo.UpdateInfo(2, 1, ('white' if self.my_colour == ChessBoard.WHITE else 'black'))
        self.finished = self.game['is_finished']
        self.game['is_finished'] = 0
        for index, item in enumerate(self.game['moves']):
            colour, move, counter = item
            if not self.chess.addTextMove(move) and self.chess.getReason() == self.chess.MUST_SET_PROMOTION:
                self.chess.setPromotion(self.chess.QUEEN)
                self.chess.addTextMove(move)
            self.UpdateRecord()
            self.UpdateStatus()
            self.UpdateRequests()
        if not self.game['moves']:
            self.UpdateRequests()
            self.UpdateRecord()
            self.UpdateStatus()
        if self.finished:
            self.game['is_finished'] = self.finished
            if self.finished != CLOSE_MOVE:
                self.UpdateRequests()
                self.UpdateRecord()
                self.UpdateStatus()
        self.UpdateClock(onSetGame = True)
        self.timer.Start(1000)
        self.gameInfo.UpdateInfo(0, -1, self.player)
        self.Refresh()

    def UpdateClock(self, onSetGame = False):
        if not self.game:
            return
        if onSetGame or (not self.chess.isGameOver() and not self.game['is_finished']):
            self.my_clock = self.gamecast.getGameClock(self.game['players'][self.my_permid], self.game)
            self.opponent_clock = self.gamecast.getGameClock(self.game['players'][self.opponent_permid], self.game)
            clock_str = ''
            if self.my_clock > 0:
                if self.opponent_clock <= 0:
                    clock_str = str(timedelta(seconds = self.my_clock)).split('.')[0]
                    clock_str += ' / ' + str(timedelta(seconds = 0)).split('.')[0]
                else:
                    clock_str = str(timedelta(seconds = self.my_clock)).split('.')[0]
                    clock_str += ' / ' + str(timedelta(seconds = self.opponent_clock)).split('.')[0]
            else:
                clock_str = str(timedelta(seconds = 0)).split('.')[0]
                clock_str += ' / ' + str(timedelta(seconds = self.opponent_clock)).split('.')[0]
            if clock_str:
                self.gameInfo.UpdateInfo(1, 1, clock_str)
            if self.gamecast.getGameExpireClock(self.game) < 0:
                self.gameInfo.UpdateInfo(3, 1, 'timed out')

    def UpdateRequests(self):
        request_record = self.gamecast.request_record.get((self.game['owner_id'], self.game['game_id']), [])
        while len(request_record) > self.rr_index:
            type, id, moveno = request_record[self.rr_index]
            if moveno == self.chess.getCurrentMove():
                c = self.my_colour if self.my_permid == id else self.opponent_colour
                colour = 'White' if c == ChessBoard.WHITE else 'Black'
                self.gameRecord.AppendText('%s seeks %s..\n' % (colour, type))
                self.rr_index += 1
            else:
                break

    def UpdateOpponentMove(self):
        ChessBoardPanel.UpdateOpponentMove(self)
        if not self.IsOpponentToMove() and self.game.get('is_finished', 0) not in [0,1] and self.finished != self.game['is_finished']:
            self.finished = self.game['is_finished']
            self.UpdateRecord()
            self.UpdateStatus()
            self.Refresh()

    def IsOpponentToMove(self):
        return (self.game and not self.chess.isGameOver() and self.game.get('is_finished', 0) in [0,1] and \
                self.game['moves'] and self.chess.getTurn() != self.my_colour)

    def IsGameExpired(self):
        return (self.gamecast.getGameExpireClock(self.game) < 0)

    def AddMove(self, move):
        self.gamecast.executeMove(self.game['owner_id'], self.game['game_id'], move)

    def GetWinnerString(self, UC = False):
        winner_permid = self.game.get('winner_permid', '')
        if not winner_permid:
            return ''
        elif UC:
            return self.game['players'][winner_permid].title()
        else:
            return self.game['players'][winner_permid]

    def OnBack(self, event):
        self.GetParent().SwitchPanel("games")

    def OnPopup(self, event):
        if not hasattr(self, "popupID1"):
            self.popupID1 = wx.NewId()
            self.popupID2 = wx.NewId()
            self.popupID3 = wx.NewId()
            self.Bind(wx.EVT_MENU, self.OnPopupAbort, id=self.popupID1)
            self.Bind(wx.EVT_MENU, self.OnPopupDraw, id=self.popupID2)
            self.Bind(wx.EVT_MENU, self.OnPopupResign, id=self.popupID3)
        menu = wx.Menu()
        menu.Append(self.popupID1, "Abort")
        menu.Append(self.popupID2, "Draw")
        menu.Append(self.popupID3, "Resign")
        btn = event.GetEventObject()
        btnSize = btn.GetSize()
        btnPt = btn.GetPosition()
        pos = wx.Point(btnPt.x+btnSize.x, btnPt.y)
        self.PopupMenu(menu, pos)
        menu.Destroy()

    def OnPopupAbort(self, event):
        if self.game['is_finished'] or self.IsGameExpired():
            return
        self.gamecast.executeAbort(self.game['owner_id'], self.game['game_id'])

    def OnPopupDraw(self, event):
        if self.game['is_finished'] or self.IsGameExpired():
            return
        self.gamecast.executeDraw(self.game['owner_id'], self.game['game_id'])

    def OnPopupResign(self, event):
        if self.game['is_finished'] or self.IsGameExpired():
            return
        self.gamecast.executeResign(self.game['owner_id'], self.game['game_id'])

class FICSBoard(ChessBoardPanel):

    def __init__(self, parent, *args):
        ChessBoardPanel.__init__(self, parent, *args)
        self.gamecast = GameCast.getInstance()
        self.my_clock = None
        self.opponent_clock = None
        self.rr_index = 0
        self.fics = FICSInterface.getInstance()
        self.fics.registerGameCallback(self.FICSCallback)

    def FICSCallback(self):
        if self.fics.game and self.game and self.game['game_id'] == self.fics.game['game_id']:
            self.game = copy.deepcopy(self.fics.game)

    def AddComponents(self):
        ChessBoardPanel.AddComponents(self)
        self.backButton = ChessGradientButton(self, 4, -1, None, "Back to overview", size=(100,25))
        font = self.backButton.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.backButton.SetFont(font)
        self.backButton.Bind(wx.EVT_BUTTON, self.OnBack)
        self.popupButton = ChessGradientButton(self, 4, -1, ChessImages.getGoBitmap(), "", size=(25,25))
        self.popupButton.Bind(wx.EVT_BUTTON, self.OnPopup)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(self.backButton, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 1)
        hSizer.Add(self.popupButton, 0, wx.ALIGN_RIGHT)
        self.vSizer.Add(hSizer, 0, wx.EXPAND)
        self.vSizer.Add((0,24), 0, 0, 0)
        self.hSizer.Layout()

    def SetGame(self):
        self.timer.Stop()
        self.rr_index = 0
        self.Reset()
        self.game = self.fics.game
        self.player = self.COMPUTER if self.game['owner'].endswith('(C)') else self.HUMAN
        self.opponent_name = self.game['owner'][0:-3] if self.game['owner'].endswith('(C)') else self.game['owner']
        self.opponent_colour = ChessBoard.WHITE if self.game['players'][self.opponent_name] == 'white' else ChessBoard.BLACK
        self.my_name = self.fics.name
        self.my_colour = ChessBoard.WHITE if self.game['players'][self.my_name] == 'white' else ChessBoard.BLACK
        self.gameInfo.UpdateInfo(0, 1, self.opponent_name)
        self.gameInfo.UpdateInfo(2, 1, ('white' if self.my_colour == ChessBoard.WHITE else 'black'))
        finished_temp = self.game['is_finished']
        self.game['is_finished'] = 0
        for index, item in enumerate(self.game['moves']):
            colour, move, counter = item
            res = self.chess.addTextMove(move)
            if not res and self.chess.getReason() == self.chess.MUST_SET_PROMOTION:
                self.chess.setPromotion(self.chess.QUEEN)
                res = self.chess.addTextMove(move)
            if res:
                self.UpdateRecord()
                self.UpdateStatus()
                self.UpdateRequests()
        if not self.game['moves']:
            self.UpdateRequests()
            self.UpdateRecord()
            self.UpdateStatus()
        self.game['is_finished'] = finished_temp
        if self.game['is_finished'] and self.game['is_finished'] != CLOSE_MOVE:
            self.UpdateRecord()
            self.UpdateStatus()
        self.UpdateClock()
        self.timer.Start(1000)
        self.gameInfo.UpdateInfo(0, -1, self.player)
        self.Refresh()

    def UpdateClock(self):
        if not self.game:
            return
        if not self.game['is_finished']:
            # We can abuse the getGameClock method from GameCast to do the work
            if not self.game:
                self.gameInfo.UpdateInfo(3, 1, 'game over')
                return
            self.my_clock = self.gamecast.getGameClock(self.game['players'][self.my_name], self.game)
            self.opponent_clock = self.gamecast.getGameClock(self.game['players'][self.opponent_name], self.game)
            clock_str = ''
            if self.my_clock > 0:
                if self.opponent_clock <= 0:
                    clock_str = str(timedelta(seconds = self.my_clock)).split('.')[0]
                    clock_str += ' / ' + str(timedelta(seconds = 0)).split('.')[0]
                else:
                    clock_str = str(timedelta(seconds = self.my_clock)).split('.')[0]
                    clock_str += ' / ' + str(timedelta(seconds = self.opponent_clock)).split('.')[0]
            else:
                clock_str = str(timedelta(seconds = 0)).split('.')[0]
                clock_str += ' / ' + str(timedelta(seconds = self.opponent_clock)).split('.')[0]
            if clock_str:
                self.gameInfo.UpdateInfo(1, 1, clock_str)

    def UpdateRequests(self):
        request_record = self.fics.request_record
        while len(request_record) > self.rr_index:
            type, id, moveno = request_record[self.rr_index]
            if moveno == self.chess.getCurrentMove():
                c = self.my_colour if self.my_name == id else self.opponent_colour
                colour = 'White' if c == ChessBoard.WHITE else 'Black'
                self.gameRecord.AppendText('%s seeks %s..\n' % (colour, type))
                self.rr_index += 1
            else:
                break

    def UpdateOpponentMove(self):
        ChessBoardPanel.UpdateOpponentMove(self)
        if self.game.get('is_finished', 0) not in [0,1] and self.fics.update:
            self.fics.update = False
            self.UpdateRecord()
            self.UpdateStatus()
            self.Refresh()

    def IsOpponentToMove(self):
        return (self.game and not self.chess.isGameOver() and self.game.get('is_finished', 0) in [0,1] and \
                self.game['moves'] and self.chess.getTurn() != self.my_colour)

    def IsGameExpired(self):
        if len(self.game['moves']) < 2:
            return False
        return (self.gamecast.getGameExpireClock(self.game) < 0)

    def AddMove(self, move):
        self.fics.executeMove(move)

    def GetWinnerString(self, UC = False):
        winner_colour = self.game.get('winner_colour', '')
        if not winner_colour:
            return ''
        elif UC:
            return winner_colour.title()
        else:
            return winner_colour

    def OnBack(self, event):
        self.GetParent().SwitchPanel("games")

    def OnPopup(self, event):
        if not hasattr(self, "popupID1"):
            self.popupID1 = wx.NewId()
            self.popupID2 = wx.NewId()
            self.popupID3 = wx.NewId()
            self.Bind(wx.EVT_MENU, self.OnPopupAbort, id=self.popupID1)
            self.Bind(wx.EVT_MENU, self.OnPopupDraw, id=self.popupID2)
            self.Bind(wx.EVT_MENU, self.OnPopupResign, id=self.popupID3)
        menu = wx.Menu()
        menu.Append(self.popupID1, "Abort")
        menu.Append(self.popupID2, "Draw")
        menu.Append(self.popupID3, "Resign")
        btn = event.GetEventObject()
        btnSize = btn.GetSize()
        btnPt = btn.GetPosition()
        pos = wx.Point(btnPt.x+btnSize.x, btnPt.y)
        self.PopupMenu(menu, pos)
        menu.Destroy()

    def OnPopupAbort(self, event):
        if self.game['is_finished'] or self.IsGameExpired():
            return
        self.fics.executeAbort()

    def OnPopupDraw(self, event):
        if self.game['is_finished'] or self.IsGameExpired():
            return
        self.fics.executeDraw()

    def OnPopupResign(self, event):
        if self.game['is_finished'] or self.IsGameExpired():
            return
        self.fics.executeResign()


class CraftyBoard(ChessBoardPanel):

    def __init__(self, parent, *args):
        ChessBoardPanel.__init__(self, parent, *args)
        self.crafty = CraftyInterface(self)
        self.crafty.start()

    def AddComponents(self):
        ChessBoardPanel.AddComponents(self)
        self.resetButton = ChessGradientButton(self, 4, -1, None, "Reset Game", size=(100,25))
        font = self.resetButton.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.resetButton.SetFont(font)
        self.resetButton.Bind(wx.EVT_BUTTON, self.OnResetGame)
        self.vSizer.Add(self.resetButton, 0, wx.EXPAND)
        self.vSizer.Add((0,24), 0, 0, 0)
        self.hSizer.Layout()

    def OnResetGame(self, event):
        self.Reset()
        self.UpdateRecord()
        self.UpdateStatus()
        self.Refresh()
        self.crafty.done = True
        self.crafty = CraftyInterface(self)
        self.crafty.start()


class ReviewBoard(ChessBoardPanel):

    def __init__(self, parent, *args):
        ChessBoardPanel.__init__(self, parent, *args)
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.session = self.utility.session
        self.peer_db = self.guiUtility.utility.session.open_dbhandler(NTFY_PEERS)
        self.timer.Stop()

    def SetGame(self, game):
        self.Reset()
        self.game = copy.deepcopy(game)
        self.gameInfo.UpdateInfo(0, 0, 'White')
        self.gameInfo.UpdateInfo(1, 0, 'Black')
        self.gameInfo.UpdateInfo(2, 0, 'Time left')
        colours = dict((value, key) for (key, value) in game['players'].items())
        p1 = show_permid_short(colours['white'])
        if colours['white'] == self.session.get_permid():
            name = self.session.sessconfig.get('nickname', '')
            if name:
                p1 = name
        else:
            peer = self.peer_db.getPeer(colours['white'])
            if peer and peer['name']:
                p1 = peer['name']
        p2 = show_permid_short(colours['black'])
        if colours['black'] == self.session.get_permid():
            name = self.session.sessconfig.get('nickname', '')
            if name:
                p2 = name
        else:
            peer = self.peer_db.getPeer(colours['black'])
            if peer and peer['name']:
                p2 = peer['name']
        self.player1 = self.COMPUTER if 'ChessBot'in p1 else self.HUMAN
        self.player2 = self.COMPUTER if 'ChessBot'in p2 else self.HUMAN
        self.gameInfo.UpdateInfo(0, 1, p1)
        self.gameInfo.UpdateInfo(1, 1, p2)
        for colour, move, counter in self.game['moves']:
            if not self.chess.addTextMove(move) and self.chess.getReason() == self.chess.MUST_SET_PROMOTION:
                self.chess.setPromotion(self.chess.QUEEN)
                self.chess.addTextMove(move)
            self.UpdateRecord()
        if not self.game['moves']:
            self.UpdateRecord()
        self.game['real_moves'] = copy.copy(self.game['moves'])
        self.game['real_is_finished'] = self.game['is_finished']
        self.game['real_lastmove_time'] = self.game['lastmove_time']
        self.game['real_finished_time'] = self.game['finished_time']
        self.movesCounter.SetLabel("move  %d/%d" % (self.chess.getCurrentMove(), self.chess.getMoveCount()))
        self.gameInfo.UpdateInfo(0, -1, self.player1)
        self.gameInfo.UpdateInfo(1, -1, self.player2)
        self.UpdateClock()
        self.Refresh()

    def AddComponents(self):
        ChessBoardPanel.AddComponents(self)
        self.gameInfo.UpdateInfo(3, 1, "game review")
        self.previousButton = ChessGradientButton(self.gameRecordPanel, 4, -1, ChessImages.getArrow_leftBitmap(), "", size=(25,25))
        self.previousButton.Bind(wx.EVT_BUTTON, self.OnPrevious)
        self.nextButton = ChessGradientButton(self.gameRecordPanel, 4, -1, ChessImages.getArrow_rightBitmap(), "", size=(25,25))
        self.nextButton.Bind(wx.EVT_BUTTON, self.OnNext)
        # Construct the counter from a GradientButton, but without any mouse events attached to it
        self.movesCounter = ChessGradientButton(self.gameRecordPanel, 0, -1, None, "move  %d/%d" % (0,0), size=(-1,25))
        font = self.movesCounter.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.movesCounter.SetFont(font)
        self.movesCounter.Unbind(wx.EVT_MOUSE_EVENTS)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(self.previousButton, 0, wx.ALIGN_LEFT)
        hSizer.Add(self.movesCounter, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 1)
        hSizer.Add(self.nextButton, 0, wx.ALIGN_RIGHT)
        vSizer = self.gameRecordPanel.GetSizer()
        vSizer.Insert(2, hSizer, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 10)
        # Add the links to the games list and message list
        listBtn = ChessGradientButton(self, 4, -1, None, "Games List", size=(-1,25))
        font = listBtn.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        listBtn.SetFont(font)
        listBtn.Bind(wx.EVT_BUTTON, self.OnList)
        messagesBtn = ChessGradientButton(self, 4, -1, None, "View Messages", size=(-1,25))
        font = messagesBtn.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        messagesBtn.SetFont(font)
        messagesBtn.Bind(wx.EVT_BUTTON, self.OnMessages)
        hSizer = wx.BoxSizer(wx.HORIZONTAL)
        hSizer.Add(listBtn, 1, 0)
        hSizer.Add((5,0), 0, 0, 0)
        hSizer.Add(messagesBtn, 1, 0)
        self.vSizer.Add(hSizer, 0, wx.EXPAND)
        self.vSizer.Add((0,24), 0, 0, 0)
        self.hSizer.Layout()

    def UpdateClock(self):
        if not self.game:
            return
        isfin = self.game['is_finished']
        self.game['is_finished'] = 1
        clock_white = GameCast.getInstance().getGameClock('white', self.game)
        clock_black = GameCast.getInstance().getGameClock('black', self.game)
        self.game['is_finished'] = isfin
        clock_str = str(timedelta(seconds = clock_white)).split('.')[0]
        clock_str += ' / ' + str(timedelta(seconds = clock_black)).split('.')[0]
        self.gameInfo.UpdateInfo(2, 1, clock_str)

    def LoadPieces(self):
        ChessBoardPanel.LoadPieces(self)
        for i in [0,1]:
            for key,piece in self.pieces[i].iteritems():
                image = piece.ConvertToImage()
                image = image.ConvertToGreyscale()
                self.pieces[i][key] = image.ConvertToBitmap()

    def UpdateRecord(self):
        move = self.chess.getLastTextMove(self.chess.LAN)
        if move:
            if move[0] in ['K', 'Q', 'R', 'B', 'N']:
                move = move[1:]
            colour = 'black' if self.chess.getTurn() == ChessBoard.WHITE else 'white'
            move = '%s: %3d.%s\n' % (colour, self.chess.getCurrentMove(), move)
            lines = self.gameRecord.GetValue().splitlines()
            if not lines or lines[-1] != move:
                self.gameRecord.AppendText(move)
        if self.chess.isGameOver():
            result = self.chess.getGameResult()
            gameResults = ["","White wins!\n","Black wins!\n","Draw by stalemate\n", \
                           "Draw by the fifty move rule\n","Draw by the three repetitions rule\n"]
            self.gameRecord.AppendText(gameResults[result])
        if (self.chess.getCurrentMove() == len(self.game['moves'])):
            is_finished = self.game.get('is_finished', 0)
            if is_finished == AGREE_ABORT: self.gameRecord.AppendText("Game aborted\n")
            if is_finished == AGREE_DRAW:  self.gameRecord.AppendText("Draw by agreement\n")
            if is_finished == RESIGN:      self.gameRecord.AppendText("%s resigns\n" % \
                                                                     ('White' if self.GetWinnerString() == 'black' else 'Black'))


    def GetWinnerString(self, UC = False):
        winner_permid = self.game.get('winner_permid', '')
        if not winner_permid:
            return ''
        elif UC:
            return self.game['players'][winner_permid].title()
        else:
            return self.game['players'][winner_permid]

    def OnMouseAction(self, event):
        event.Skip()

    def OnNext(self, event):
        if self.chess.redo():
            self.movesCounter.SetLabel("move  %d/%d" % (self.chess.getCurrentMove(), self.chess.getMoveCount()))
            self.game['moves'] = self.game['real_moves'][0:self.chess.getCurrentMove()]
            self.UpdateRecord()
            self.UpdateClock()
            self.Refresh()
            if self.chess.isGameOver():
                self.game['finished_time'] = self.game['real_finished_time']
                self.game['is_finished'] = self.game['real_is_finished']
        elif not self.game['is_finished']:
            rec = self.gameRecord.GetValue()
            rec = '\n'.join(rec.splitlines()[:-1])+'\n'
            self.gameRecord.SetValue(rec.lstrip('\n'))
            self.game['finished_time'] = self.game['real_finished_time']
            self.game['is_finished'] = self.game['real_is_finished']
            self.UpdateRecord()
            self.UpdateClock()
            self.Refresh()

    def OnPrevious(self, event):
        if self.game['is_finished']:
            rec = self.gameRecord.GetValue()
            if self.game['is_finished'] != CLOSE_MOVE:
                rec = '\n'.join(rec.splitlines()[:-1])+'\n'
            else:
                rec = '\n'.join(rec.splitlines()[:-2])+'\n'
                self.chess.undo()
                self.movesCounter.SetLabel("move  %d/%d" % (self.chess.getCurrentMove(), self.chess.getMoveCount()))
                self.game['moves'] = self.game['real_moves'][0:self.chess.getCurrentMove()]
            self.gameRecord.SetValue(rec.lstrip('\n'))
            self.game['finished_time'] = self.game['lastmove_time']
            self.game['is_finished'] = 0
            self.UpdateClock()
            self.Refresh()
        elif self.chess.undo():
            rec = self.gameRecord.GetValue()
            rec = '\n'.join(rec.splitlines()[:-1])+'\n'
            self.gameRecord.SetValue(rec.lstrip('\n'))
            self.movesCounter.SetLabel("move  %d/%d" % (self.chess.getCurrentMove(), self.chess.getMoveCount()))
            self.game['moves'] = self.game['real_moves'][0:self.chess.getCurrentMove()]
            self.UpdateClock()
            self.Refresh()

    def OnList(self, event):
        self.GetParent().SwitchPanel("list")

    def OnMessages(self, event):
        self.GetParent().SwitchPanel("messages")


class ChessInfoPanel(ChessSubPanel):

    def __init__(self, parent, *args, **kwargs):
        ChessSubPanel.__init__(self, parent, title = "Game Information", size = (-1,125), *args, **kwargs)
        # Set attributes to default settings
        self.contents = [['Opponent'  , 'Crafty v23.2' ],
                         ['Time left' , 'unlimited'    ],
                         ['I play as' , 'white'        ],
                         ['Status'    , 'white to move'] ]
        self.player1 = ChessBoardPanel.COMPUTER
        self.player2 = ChessBoardPanel.COMPUTER

    def UpdateInfo(self, row, col, value):
        if row == 0 and col == -1:
            self.player1 = value
        elif row == 1 and col == -1:
            self.player2 = value
        else:
            self.contents[row][col] = value
        self.Refresh()

    def Draw(self, dc):
        width = self.GetClientRect()[2]
        dc.SetPen(wx.Pen(wx.WHITE, 1, wx.TRANSPARENT))
        dc.SetBrush(wx.Brush((240,248,255), style=wx.SOLID))
        dc.DrawRectangle(10, 35, width-20, 20)
        dc.DrawRectangle(10, 75, width-20, 20)
        dc.SetBrush(wx.Brush((240,255,204), style=wx.SOLID))
        dc.DrawRectangle(10, 55, width-20, 20)
        dc.DrawRectangle(10, 95, width-20, 20)
        dc.SetFont(self.GetFont())
        dc.SetTextForeground(wx.BLACK)
        dc.DrawText(self.contents[0][0], 12, 38)
        dc.DrawText(self.contents[1][0], 12, 58)
        dc.DrawText(self.contents[2][0], 12, 78)
        dc.DrawText(self.contents[3][0], 12, 98)
        if self.player1 == ChessBoardPanel.COMPUTER:
            dc.DrawBitmap(ChessImages.getComputerBitmap(), 92, 38)
        else:
            dc.DrawBitmap(ChessImages.getHumanBitmap(), 92, 38)
        self.DrawText(dc, self.contents[0][1], 110, 38, width-123)
        if 'Time' in self.contents[1][0]:
            self.DrawText(dc, self.contents[1][1], 92, 58, width-105)
        else:
            if self.player2 == ChessBoardPanel.COMPUTER:
                dc.DrawBitmap(ChessImages.getComputerBitmap(), 92, 58)
            else:
                dc.DrawBitmap(ChessImages.getHumanBitmap(), 92, 58)
            self.DrawText(dc, self.contents[1][1], 110, 58, width-123)
        self.DrawText(dc, self.contents[2][1], 92, 78, width-105)
        if self.contents[3][1] != "white to move" and self.contents[3][1] != "black to move":
            font = self.GetFont()
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            dc.SetFont(font)
	    dc.SetTextForeground(wx.Colour(255,51,0))
        self.DrawText(dc, self.contents[3][1], 92, 98, width-105)
        dc.SetPen(wx.Pen(self.edgeColour, 1, wx.SOLID))
        dc.SetBrush(wx.Brush(self.edgeColour, style=wx.TRANSPARENT))
        dc.DrawRectangle(10, 35, width-20, 80)

    def DrawText(self, dc, text, x, y, maxWidth):
        for i in xrange(len(text), 0, -1):
            toDraw = text[0:i]
            if i != len(text):
                toDraw += ".."
            (width, height) = dc.GetTextExtent(toDraw)
            if width <= maxWidth:
                dc.DrawText(toDraw, x, y)
                return
