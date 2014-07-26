import os
import sys
import random
import logging
import threading
import subprocess
import Tribler.Core.BitTornado.parseargs as parseargs

from time import time, sleep
from traceback import print_exc
from Tribler.Core.API import *
from Tribler.Core.BitTornado.bencode import bencode
from Tribler.Core.CacheDB.sqlitecachedb import bin2str, str2bin
from Tribler.Core.Overlay.OverlayThreadingBridge import OverlayThreadingBridge
from Tribler.Core.CacheDB.CacheDBHandler import PeerDBHandler, GameCastDBHandler
from Tribler.Core.GameCast.ChessBoard import ChessBoard
from Tribler.Core.GameCast.GameCast import *
from Tribler.Core.GameCast.GameCastGossip import GameCastGossip

import Tribler.Core.GameCast.GameCast as GameCastMod
import Tribler.Core.GameCast.GameCastGossip as GameCastGossipMod
GameCastMod.GameCast.DEBUG   = False
GameCastGossipMod.DEBUG      = False
GameCastGossipMod.SHOW_ERROR = False

DEBUG   = True
MAXBOTS = 1

argsdef = [('nickname', 'Chessbot1', 'name of the peer'),
           ('port', 7002, 'listen port'),
           ('permid', '', 'filename containing EC keypair'),
           ('statedir', '.Tribler','dir to save session state'),
           ('installdir', '', 'source code install dir')]


class chessbot(threading.Thread):

    def __init__(self, game):
        threading.Thread.__init__(self)
        self.overlay_bridge = OverlayThreadingBridge.getInstance()
        global session
        session.add_observer(self.gameCallback, NTFY_GAMECAST, [NTFY_UPDATE], 'Game')
        self.mypermid = session.get_permid()
        self.colour = game['players'][self.mypermid]
        self.chess = ChessBoard()
        self.game = game
        self.done = False
        if DEBUG:
            print >> sys.stderr, 'chessbot: chessbot started for game (%d,%d)' % (self.game['owner_id'], self.game['game_id'])

    def gameCallback(self, subject, changeType, objectID, *args):
        # Keep db access on a single thread (the OverlayThread), since we are running this program with SQLite in :memory: mode.
        self.overlay_bridge.add_task(self._gameCallback, 0)

    def _gameCallback(self):
        # Refresh the current game
        global session
        self.gamecast_db = session.open_dbhandler(NTFY_GAMECAST)
        game_id = self.game['game_id']
        owner_id = self.game['owner_id']
        games = self.gamecast_db.getGames(game_id = game_id, owner_id = owner_id)
        if games and len(games) == 1:
            self.game = games[0]
        if self.game['is_finished']:
            self.done = True
            try:
                global session
                session.remove_observer(self.gameCallback)
                global mygames
                mygames.remove((owner_id, game_id))
            except:
                if DEBUG:
                    print >> sys.stderr, 'chessbot: failed to properly close finished game (%d,%d)' % (owner_id, game_id)

    def run(self):
        def send(cmd):
            cout.write(cmd+'\n')
            cout.flush()
            if DEBUG:
                print >> sys.stderr, 'chessbot: send command to engine:', cmd

        def recv():
            resp = cin.readline()
            resp = resp.strip('\n ')
            if DEBUG:
                print >> sys.stderr, 'chessbot: recieved response from engine:', resp
            if "Illegal" in resp:
                print >> sys.stderr, 'chessbot: unexpected response from engine'
            return resp

        # Start Crafty as a subprocess
        engine = os.path.join(os.getcwd(), 'Tribler', 'GameEngines', 'crafty-23.2-ubuntu')
        p = subprocess.Popen([engine, 'xboard'], shell=False, \
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        cin, cout = (p.stdout, p.stdin)
        # Ignore the first few lines send by Crafty
        while 1:
            s = cin.readline()
            if 'Hello' in s:
                break
        if DEBUG:
            print >> sys.stderr, 'chessbot: chess engine started'
        # Set the time each chess move should take (60 moves in 1 minute)
        send('st 0.02')
        # Disable thinking on the opponent's time
        send('ponder off')
        # Disable logging
        send('log off')
        # Output notation in long format
        send('output long')
        gc = GameCast.getInstance()
        if self.colour == 'white':
            i = 1
            send('move')
        else:
            i = 0
        # Are we resuming a game?
        if len(self.game['moves']) > 1:
            if DEBUG:
                print >> sys.stderr, 'chessbot: currently not capable of resuming a game'
            send('quit')
            return
        # Process move commands until a quit command is recieved
        while not self.done:
            if (len(self.game['moves'])+i) % 2:
                sleep(0.5)
                if self.game['moves']:
                    send(self.game['moves'][-1][1])
                resp = recv()
                # If we don't have the answer we are looking for, try again
                if not "move" in resp:
                    resp = recv()
                if not "move" in resp:
                    print >> sys.stderr, 'chessbot: failed to recieve next chess move from engine'
                else:
                    move = resp.split(' ')[1]
                    move = move.strip(' +\t\n\r')
                    gc.executeMove(self.game['owner_id'], self.game['game_id'], move)
            if gc.getGameExpireClock(self.game) < 0:
                self.done = True
                try:
                    global session
                    session.remove_observer(self.gameCallback)
                    global mygames
                    mygames.remove((self.game['owner_id'], self.game['game_id']))
                except:
                    if DEBUG:
                        print >> sys.stderr, 'chessbot: failed to properly close expired game (%d,%d)' % (owner_id, game_id)
            # Sleep for one second to prevent high CPU usage
            sleep(1.0)
        send('quit')
        sleep(1.0)

def launchchessbot(game):
    cb = chessbot(game)
    cb.start()

def startsession():
    sscfg = SessionStartupConfig()
    sscfg.set_nickname(config['nickname'])
    sscfg.set_listen_port(config['port'])
    sscfg.set_state_dir("%s%d" % (config['statedir'],config['port']))
    sscfg.set_superpeer(True)
    sscfg.set_crawler(False)
    sscfg.set_social_networking(False)
    sscfg.set_remote_query(False)
    sscfg.set_subtitles_collecting(False)
    sscfg.set_torrent_collecting(False)
    sscfg.set_torrent_checking(False)
    sscfg.set_dialback(False)
    sscfg.set_nat_detect(False)
    sscfg.set_internal_tracker(False)
    sscfg.set_megacache(True)
    sscfg.set_overlay(True)
    sscfg.set_buddycast(False)
    sscfg.set_gamecast(True)
    sscfg.sessconfig['dispersy'] = False

    global session
    session = Session(sscfg)

def round():
    global session, mygames, myinvite, rounds
    gc = GameCast.getInstance()
    gc_db = session.open_dbhandler(NTFY_GAMECAST)
    if not myinvite and len(mygames) < MAXBOTS:
        # Send out a new random game/invite
        game = {}
        game['game_id'] = gc_db.getNextGameID(0)
        game['owner_id'] = 0
        game['winner_permid'] = ''
        game['moves'] = bencode([])
        mycolour = random.choice(['black','white'])
        mypermid = bin2str(session.get_permid())
        players = {mypermid:mycolour}
        game['players'] = bencode(players)
        game['gamename'] = 'chess'
        game['time'] = random.choice([10,20,30])
        game['inc'] = random.choice([60,120,180])
        game['is_finished'] = 0
        game['lastmove_time'] = 0
        game['creation_time'] = 0
        gc_db.addGame(game)
        invite = {}
        invite['target_id'] = -1
        invite['game_id'] = game['game_id']
        invite['min_rating'] = 0
        invite['max_rating'] = 9999
        invite['time'] = game['time']
        invite['inc'] = game['inc']
        invite['gamename'] = 'chess'
        if mycolour == 'black':
            invite['colour'] = 'white'
        else:
            invite['colour'] = 'black'
        if DEBUG:
            print >> sys.stderr, 'chessbot: sending out invite for game (%d,%d)' % (0, game['game_id'])
        myinvite = gc._executeSeekOrMatch(invite)
    elif myinvite:
        # Check if the game related to our invite has started yet
        owner_id = myinvite['owner_id']
        game_id = myinvite['game_id']
        games = gc_db.getGames(game_id = game_id, owner_id = owner_id)
        if games and len(games) == 1:
            game = games[0]
            if game['creation_time']:
                # Game has started
                myinvite = None
                mygames.append((owner_id, game_id))
                if DEBUG:
                    print >> sys.stderr, 'chessbot: starting chessbot for game (%d,%d)' % (owner_id, game_id)
                func = lambda:launchchessbot(game)
                overlay_bridge.add_task(func, 0)
            else:
                # Game has not yet started, resend the invite
                if int(time()) > myinvite['creation_time']+INV_EXPIRE_TIME:
                    myinvite = None
                elif (rounds % 5) == 0:
                    gc.resendSeek(myinvite)
        else:
            myinvite = None

if __name__ == "__main__":
    config, fileargs = parseargs.parseargs(sys.argv, argsdef, presets = {})
    print >> sys.stderr, "config =", config

    overlay_bridge = OverlayThreadingBridge.getInstance()
    overlay_bridge.gcqueue = overlay_bridge.tqueue
    overlay_bridge.add_task(startsession, 0)
    sleep(20)

    gclogger = logging.getLogger('gamecast')
    gclogger.disabled = True
    gcglogger = logging.getLogger('gamecastgossip')
    gcglogger.disabled = True

    global session, mygames, myinvite, rounds
    rounds = 0
    mygames = []
    myinvite = None

    try:
        while True:
            overlay_bridge.add_task(round, 0)
            sleep(5)
    except:
        print_exc()

    session.shutdown()
    sleep(3)
