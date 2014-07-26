# Written by Egbert Bouman
# see LICENSE for license information

import os
import sys
import time
import threading
import subprocess

from Tribler.Core.GameCast.fics.client import *
from Tribler.Core.GameCast.GameCast import AGREE_ABORT, AGREE_DRAW, CLOSE_MOVE, RESIGN
from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.__init__ import LIBRARYNAME

DEBUG = True

class FICSInterface(threading.Thread, Decoder):
    __single = None
    sentStyle = False
    game_callback = None
    invite_callback = None
    update = False
    done = False

    name = ''
    game = {}
    invite = {}
    invites = []
    request_record = []

    def __init__(self):
        if FICSInterface.__single:
            raise RuntimeError, "FICSInterface is singleton"
        FICSInterface.__single = self
        Decoder.__init__(self)
        threading.Thread.__init__(self)
        self.setDaemon(True)

    def getInstance(*args, **kw):
        if FICSInterface.__single is None:
            FICSInterface(*args, **kw)
        return FICSInterface.__single
    getInstance = staticmethod(getInstance)

    def registerGameCallback(self, cb):
        self.game_callback = cb

    def registerInviteCallback(self, cb):
        self.invite_callback = cb

    def connect(self) :
    	if hasattr(self, 's') and self.s:
            self.s.close()
            self.s = 0
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect(('freechess.org', 23))

    def send(self, data):
        if DEBUG:
            print >> sys.stderr, 'FICS: sending', data
        self.s.send(data)

    def executePlay(self, number):
        if self.game and not self.game['is_finished']:
            if DEBUG:
                print >> sys.stderr, 'FICS: error while trying to send play (already playing a game)'
            return False
        invite = None
        for i in self.invites:
            if i['invite_id'] == number:
                invite = i
        if invite:
            self.invite = invite
            self.send('play %s\n' % number)
            return True
        else:
            if DEBUG:
                print >> sys.stderr, 'FICS: error while trying to send play (unknown invite)'
            return False

    def executeSought(self):
        self.invites = []
        if DEBUG:
            print >> sys.stderr, 'FICS: invites queue reset'
        self.send('sought\n')

    def executeMove(self, move):
        self.send('%s\n' % move)

    def executeAbort(self):
        self.request_record.append(('abort', self.name, len(self.game['moves'])))
        self.send('abort\n')

    def executeDraw(self):
        self.request_record.append(('draw', self.name, len(self.game['moves'])))
        self.send('draw\n')

    def executeResign(self):
        self.send('resign\n')

    def onLogin(self):
        self.send('guest\n')

    def onPrompt(self):
        if not self.sentStyle:
            self.send('style 12\n')
            self.send('set open 0\n') # Don't support being challenged yet
            self.send('set seek 0\n')
            self.send('set autoflag 1\n')
            self.send('sought\n')
            self.sentStyle = True

    def onNameAssign(self, name):
        self.name = name[0]
        self.send('\n')
        if DEBUG:
            print >> sys.stderr, 'FICS: connected to server as user', self.name

    def onAnnounce(self, number, game, player):
        if game.type not in ['blitz', 'standard', 'lightning'] or game.options != '':
            return
        invite = {}
        invite['invite_id'] = number
        invite['time'] = int(game.a)
        invite['inc'] = int(game.b)
        invite['owner'] = player.name
        invite['rating'] = player.rating
        invite['colour'] = game.colour.strip('[]') if game.colour else '-'
        self.invites.append(invite)
        if DEBUG:
            print >> sys.stderr, 'FICS: adding invite #%s (%s)' % (number, player.name)
        if self.invite_callback: self.invite_callback()

    def onGame(self, game, white, black):
        if DEBUG:
            print >> sys.stderr, 'FICS: game #%s, %s vs %s' % (game.number, white.name, black.name)

    def onGameResult(self, game, white, black, text):
        # When the game starts..
        if text.startswith('Creating'):
            self.request_record = []
            self.game = {}
            self.game['creation_time'] = time.time()
            self.game['lastmove_time'] = 0
            self.game['is_finished'] = 0
            self.game['players'] = {white.name:'white', black.name:'black'}
            self.game['game_id'] = game.number
            self.game['moves'] = []
            self.game['gamename'] = 'chess'
            self.game['owner'] = self.invite['owner']
            self.game['rating'] = self.invite['rating']
            self.game['time'] = self.invite['time']
            self.game['inc'] = self.invite['inc']
            if DEBUG:
                print >> sys.stderr, 'FICS: adding game #%s (%s vs %s)' % (game.number, white.name, black.name)
        # When the game ends..
        elif game.result != '':
            if text.startswith('Game aborted'):
                self.game['is_finished'] = AGREE_ABORT
                self.game['winner_colour'] = ''
            elif text.startswith('Game drawn by mutual agreement'):
                self.game['is_finished'] = AGREE_DRAW
                self.game['winner_colour'] = ''
            elif 'resigns' in text or 'forfeits' in text:
                self.game['is_finished'] = RESIGN
                self.game['winner_colour'] = 'black' if game.result[0] == '0' else 'white'
            else:
                self.game['is_finished'] = CLOSE_MOVE
                self.game['winner_colour'] = 'black' if game.result[0] == '0' else 'white'
            self.update = True
            if DEBUG:
                print >> sys.stderr, 'FICS: finished game #%s (%s vs %s)' % (game.number, white.name, black.name)
        if self.game_callback: self.game_callback()

    def onMove(self, move):
        if move.move == 'none':
            self.game['creation_time'] = time.time()
            if DEBUG:
                print >> sys.stderr, 'FICS: empty move received, creation_time set'
            return
        if move.colourToMove == 'W':
            colour = 'black'
        else:
            colour = 'white'
        counter = self.game['moves'][-2][2] if len(self.game['moves']) > 1 else 0
        counter += int(move.moveTime*1000)
        self.game['lastmove_time'] = time.time()
        self.game['moves'].append((colour, move.move, counter))
        if DEBUG:
            print >> sys.stderr, 'FICS: move %s made by %s' % (move.move, colour)
        if self.game_callback: self.game_callback()

    def onDrawRequest(self, name):
        self.request_record.append(('draw', name, len(self.game['moves'])))
        if DEBUG:
            print >> sys.stderr, 'FICS: request for draw made by %s' % name

    def onAbortRequest(self, name):
        self.request_record.append(('abort', name, len(self.game['moves'])))
        if DEBUG:
            print >> sys.stderr, 'FICS: request for abort made by %s' % name

    def run(self):
        self.connect()
        while not self.done:
            (data, address) = self.s.recvfrom(65535)
            self.registerIncomingData(data)


class CraftyInterface(threading.Thread):

    def __init__(self, parent):
        threading.Thread.__init__(self)
        self.parent = parent
        self.done = False

    def run(self):
        def send(cmd):
            cout.write(cmd+'\n')
            cout.flush()
            if DEBUG:
                print >> sys.stderr, 'Send command to Crafty:', cmd

        def recv():
            resp = cin.readline()
            if DEBUG:
                print >> sys.stderr, 'Recieved response from Crafty:', resp
            if "Illegal" in resp:
                print >> sys.stderr, 'CraftyInterface: Unexpected response from Crafty'
            return resp

        # Start Crafty as a subprocess
        guiUtility = GUIUtility.getInstance()
        if sys.platform == 'linux2':
            engine = os.path.join(guiUtility.utility.getPath(), LIBRARYNAME, "GameEngines", "crafty-23.2-ubuntu")
            p = subprocess.Popen([engine, 'xboard'], shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        else:
            engine = os.path.join(guiUtility.utility.getPath(), LIBRARYNAME, "GameEngines", "crafty-23.2-win32.exe")
            p = subprocess.Popen([engine, 'xboard'], shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        cin, cout = (p.stdout, p.stdin)
        # Ignore the first few lines send by Crafty
        while 1:
            s = cin.readline()
            if 'Hello' in s:
                break
        if DEBUG:
            print >> sys.stderr, 'CraftyInterface: Crafty started'
        # Set the time each chess move should take (60 moves in 1 minute)
        send('st 0.02')
        # Disable thinking on the opponent's time
        send('ponder off')
        # Disable logging
        send('log off')
        # Output notation in long format
        send('output long')
        # Process move commands until a quit command is recieved
        while 1:
            if not self.parent:
                break
            if len(self.parent.game['moves']) % 2:
                time.sleep(0.5)
                send(self.parent.game['moves'][-1][1])
                resp = recv()
                # If we don't have the answer we are looking for, try again
                if not "move" in resp:
                    resp = recv()
                if not "move" in resp:
                    print >> sys.stderr, 'CraftyInterface: Failed to recieve next chess move from Crafty'
                else:
                    move = resp.split(' ')[1]
                    move = move.strip(' +\t\n\r')
                    self.parent.game['moves'].append(('black', move, '0'))
            if self.done:
                send('quit')
                break
            # Sleep for one second to prevent high CPU usage
            time.sleep(1.0)