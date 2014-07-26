#!/usr/bin/python

import os
import sys
import bisect
import pickle
import random
import logging
import threading
from collections import deque
try:
    from resource import getrusage, RUSAGE_SELF
except ImportError:
    RUSAGE_SELF = 0
    def getrusage(who=0):
        return [0.0, 0.0] # on non-UNIX platforms cpu_time always 0.0
p_stats = None
p_start_time = None


from time import time, sleep

from Tribler.Core.API import *
from Tribler.Core.BitTornado.parseargs import parseargs
from Tribler.Core.BitTornado.bencode import bencode, bdecode
from Tribler.Core.CacheDB.sqlitecachedb import bin2str, str2bin
from Tribler.Core.GameCast.GameCast import *
from Tribler.Core.GameCast.GameCastGossip import GameCastGossip
from Tribler.Core.GameCast.ChessBoard import ChessBoard
from Tribler.Core.Utilities.utilities import show_permid_short as showPermid
from Tribler.Core.Overlay.OverlayThreadingBridge import OverlayThreadingBridge

argsdef = [('name',            None,       'name of the peer'),
           ('superpeer',       False,      'whether this peer is a superpeer'),
           ('superpeer_file',  None,       'filename of the GameCast superpeer file'),
           ('port',            0,          'listen port'),
           ('statedir',        '.Tribler', 'dir to save session state')]

GCLOG  = 0
GCGLOG = 1

class TaskDispatcher(threading.Thread):
    __single = None

    def getInstance(*args, **kw):
        if TaskDispatcher.__single is None:
            TaskDispatcher(*args, **kw)
        return TaskDispatcher.__single
    getInstance = staticmethod(getInstance)

    def __init__(self, session):
        if TaskDispatcher.__single:
            raise RuntimeError, "TaskDispatcher is singleton"
        TaskDispatcher.__single = self
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.overlay_bridge = OverlayThreadingBridge.getInstance()

        self.thegame       = ['e2e4', 'd7d5', 'b1c3', 'Ng8f6', 'g1f3', 'd5xe4', 'c3e4', 'Nf6xe4', 'd2d4', 'Nb8c6', \
                              'c1f4', 'e7e6', 'f1b5', 'Bf8b4+', 'c2c3', 'Bb4d6', 'e1g1', 'Bd6xf4', 'd1d3', 'f7f5', \
                              'b5c6', 'b7xc6', 'f3e5', 'Bf4xe5', 'd4e5', 'Qd8xd3', 'c3c4', 'Qd3d4', 'c4c5', 'Bc8a6', \
                              'g2g4', 'Ba6xf1', 'g1h1', 'Qd4xf2', 'h2h4', 'Qf2g2#']

        # Maximum number of games that can be played simultaneously.
        self.max_games     = 1

        # Keep all games that are being played and all received invites in memory.
        self.open_games    = []
        self.open_invites  = []
        # Keep a number of closed games in the cache. These games will be used when testing the discuss command.
        self.closed_games  = []

        # Register db callbacks
        self.session = session
        self.session.add_observer(self.gameCallback, NTFY_GAMECAST, [NTFY_INSERT, NTFY_UPDATE, NTFY_DELETE], 'Game')
        self.session.add_observer(self.inviteCallback, NTFY_GAMECAST, [NTFY_INSERT, NTFY_UPDATE, NTFY_DELETE], 'GameInvite')

        self.done              = False
        self.mypermid          = self.session.get_permid()
        self.myip              = self.session.get_external_ip()
        self.myport            = self.session.get_listen_port()
        self.gamecast          = GameCast.getInstance()
        self.gamecast_gossip   = GameCastGossip.getInstance()
        self.gamecast_database = self.session.open_dbhandler(NTFY_GAMECAST)
        self.peers_database    = self.session.open_dbhandler(NTFY_PEERS)

        # For stats
        self.logged_peers      = []
        self.logged_games      = []
        self.start_time        = time()
        self.gc_logger         = logging.getLogger('gamecast')
        self.gcg_logger        = logging.getLogger('gamecastgossip')
        self.logging           = threading.Thread(target=self.periodicLogging)
        self.logging.daemon    = True
        self.logging_interval  = 30
        self.logging.start()

    def periodicLogging(self):
        while True:
            # Wait
            sleeptime = self.logging_interval - (time() % self.logging_interval)
            sleep(sleeptime)
            # Log stats on OverlayThread
            def doLogging():
                if not self.logged_peers:
                    all_peers = [p[0] for p in self.peers_database.getAll(['name'], where='gc_member=1')]
                else:
                    all_peers = self.gamecast_gossip.peer_stats
                new_peers = []
                for peer in all_peers:
                    if peer not in self.logged_peers:
                        self.logged_peers.append(peer)
                        new_peers.append(peer)
                all_games = self.gamecast_database.getFinishedGames()
                new_games = []
                for game in all_games:
                    tuple = (game['owner_id'], game['game_id'])
                    if tuple not in self.logged_games:
                        self.logged_games.append(tuple)
                        if game['owner_id'] == 0:
                            permid = self.mypermid
                        else:
                            permid = self.gamecast.getPeer(game['owner_id'])['permid']
                        new_games.append((bin2str(permid), game['game_id']))

                num_gc_members = len(all_peers)
                num_games_tot = len(all_games)
                num_games_own = len([game for game in all_games if game['owner_id'] == 0])
                self.log(GCLOG, 'DB_STATS', uptime=int(time()-self.start_time), games_tot=num_games_tot, games_own=num_games_own, gc_members=num_gc_members, \
                         new_peers=bencode(new_peers), new_games=bencode(new_games))

                nCo = len(self.gamecast_gossip.connections)
                nCg = len(self.gamecast_gossip.connected_game_buddies)
                nCr = len(self.gamecast_gossip.connected_random_peers)
                nCu = len(self.gamecast_gossip.connected_unconnectable_peers)
                self.log(GCGLOG, 'NW_STATS', uptime=int(time()-self.start_time), nCo=nCo, nCg=nCg, nCr=nCr, nCu=nCu)

                gc_bytes_in = self.gamecast.message_stats['bytes_in']
                gc_messages_in = self.gamecast.message_stats['messages_in']
                gc_bytes_out = self.gamecast.message_stats['bytes_out']
                gc_messages_out = self.gamecast.message_stats['messages_out']
                gcg_bytes_in = self.gamecast_gossip.message_stats['bytes_in']
                gcg_messages_in = self.gamecast_gossip.message_stats['messages_in']
                gcg_bytes_out = self.gamecast_gossip.message_stats['bytes_out']
                gcg_messages_out = self.gamecast_gossip.message_stats['messages_out']
                self.log(GCLOG, 'BW_STATS', uptime=int(time()-self.start_time), bytes_in=gc_bytes_in, messages_in=gc_messages_in, bytes_out=gc_bytes_out, messages_out=gc_messages_out)
                self.log(GCGLOG, 'BW_STATS', uptime=int(time()-self.start_time), bytes_in=gcg_bytes_in, messages_in=gcg_messages_in, bytes_out=gcg_bytes_out, messages_out=gcg_messages_out)
            self.overlay_bridge.add_task(doLogging, 0)

    def gameCallback(self, subject, changeType, objectID, *args):
        # Keep db access on a single thread (the OverlayThread), since we are running this program with SQLite in :memory: mode.
        self.overlay_bridge.add_task(self._gameCallback, 0)

    def inviteCallback(self, subject, changeType, objectID, *args):
        # Keep db access on a single thread (the OverlayThread), since we are running this program with SQLite in :memory: mode.
        self.overlay_bridge.add_task(self._inviteCallback, 0)

    def _gameCallback(self):
        # Refresh the list of open games.
        games = self.gamecast_database.getCurrentGames(self.mypermid)
        index = 0
        while index < len(games):
            game = games[index]
            players = game.get('players', [])
            # Chess games should have 2 players and only the games with a creation_time have actually started.
            if len(players) == 2 and game['creation_time'] != 0:
                # Ignore games that have expired.
                if self.gamecast.getGameExpireClock(game) <= 0:
                    games.pop(index)
                    continue
            else:
                games.pop(index)
                continue
            index += 1
        self.open_games = games
        # Ensure that there are some games listed in closed games (if available).
        if not self.closed_games or len(self.closed_games) < 10:
            self.closed_games  = self.gamecast_database.getFinishedGames()

    def _inviteCallback(self):
        # Only perform a db query if needed.
        #for invite in self.open_invites:
        #    # We have at least one valid invite to respond to, which is enough.
        #    if time() < invite['creation_time']+INV_EXPIRE_TIME:
        #        return
        # Refresh the list of open invites.
        myrating = self.gamecast.getRating(self.mypermid, 'chess')[0]
        invites = self.gamecast_database.getCurrentInvites(myrating, 'chess')
        index = 0
        while index < len(invites):
            invite = invites[index]
            # Check whether the invite is still valid and has not already been replied to.
            if invite['status'] <= 2**4:
                # Ignore invites that have expired.
                if time() > invite['creation_time']+INV_EXPIRE_TIME:
                    invites.pop(index)
                    continue
            index += 1
        self.open_invites = invites

    def selectTaskType(self):
        # Randomly decide which type of task is to be executed next.
        if not self.open_games and random.random() < 0.20:
            ttype = None
        elif self.selectGame() and random.random() < 0.99:
            ttype = CMD_MOVE
        elif len(self.open_games) < self.max_games:
            if self.open_invites and random.random() < 0.75:
                ttype = CMD_PLAY
            else:
                if random.random() < 0.75:
                    ttype = CMD_SEEK
                else:
                    ttype = CMD_MATCH
        else:
            ttype = CMD_DISCUSS
        return ttype

    def selectTask(self, ttype):
        # Determine the actual task.
        if ttype == CMD_MOVE:
            return self.selectMove(ttype)
        elif ttype == CMD_PLAY:
            return self.selectPlay(ttype)
        elif ttype == CMD_SEEK or ttype == CMD_MATCH:
            return self.selectSeekOrMatch(ttype)
        elif ttype == CMD_DISCUSS:
            return self.selectDiscuss(ttype)
        else:
            return self.selectNop(ttype)

    def selectGame(self):
        # Remove any expired games.
        self.open_games = [game for game in self.open_games if self.gamecast.getGameExpireClock(game) > 0]
        # Randomly select a game from a list of open games in which we are expected to move next.
        games = [game for game in self.open_games if self.gamecast.getGameTurn(game) == game['players'][self.mypermid]]
        if games:
            return random.choice(games)
        else:
            return None

    def selectMove(self, ttype):
        task = None
        time = 0
        # Pick a game for which we still need to make a move.
        game = self.selectGame()
        # Now that we picked a game for which we are about to send a move, we need to decide on the move itself.
        if len(self.open_games) > self.max_games:
            games = [game for game in self.open_games if len(game['moves']) == 0 or (len(game['moves']) == 1 and self.gamecast.getGameTurn(game) == game['players'][self.mypermid])]
            if games:
                game = random.choice(games)
                task = lambda:self.gamecast.executeAbort(game['owner_id'], game['game_id'])
                time = 1
                print 'TaskDispatcher next task: ttype=%s ; sleep_before=%d ; owner_id=%d ; game_id=%d ; move=ABORT' % \
                  (ttype, time, game['owner_id'], game['game_id'])
                return (task, time)
        r = random.random()
        if r > 0.98:
            task = lambda:self.gamecast.executeResign(game['owner_id'], game['game_id'])
            time = random.randint(1,10)
            print 'TaskDispatcher next task: ttype=%s ; sleep_before=%d ; owner_id=%d ; game_id=%d ; move=RESIGN' % \
                  (ttype, time, game['owner_id'], game['game_id'])
        elif r > 0.95:
            task = lambda:self.gamecast.executeDraw(game['owner_id'], game['game_id'])
            time = random.randint(1,10)
            print 'TaskDispatcher next task: ttype=%s ; sleep_before=%d ; owner_id=%d ; game_id=%d ; move=DRAW' % \
                  (ttype, time, game['owner_id'], game['game_id'])
        else:
            move = self.thegame[len(game['moves'])]
            task = lambda:self.gamecast.executeMove(game['owner_id'], game['game_id'], move)
            time = random.randint(1, 10)
            print 'TaskDispatcher next task: ttype=%s ; sleep_before=%d ; owner_id=%d ; game_id=%d ; move=%s' % \
                  (ttype, time, game['owner_id'], game['game_id'], move)
        return (task, time)

    def selectPlay(self, ttype):
        # Pick a random open invite, and send a play command to its owner.
        invite = random.choice(self.open_invites)
        task = lambda:self.gamecast.executePlay(invite['owner_id'], invite['invite_id'])
        time = random.randint(5, 30)
        print 'TaskDispatcher next task: ttype=%s ; sleep_before=%d ; owner_id=%d ; game_id=%s' % \
              (ttype, time, invite['owner_id'], invite['game_id'])
        return (task, time)

    def selectSeekOrMatch(self, ttype):
        def doSeekOrMatch():
            # Randomly determine the timing controls (not all possible timing control are taken into consideration).
            tc_time = random.choice(range(1,10))
            tc_inc = random.choice(range(0,30,5))
            # Randomly decide on the colour of the players.
            colour = random.choice(['white', 'black'])
            # Create the game and add it to the db.
            game = {}
            game['game_id'] = self.gamecast_database.getNextGameID(0)
            game['owner_id'] = 0
            game['winner_permid'] = ''
            game['moves'] = bencode([])
            mypermid = bin2str(self.mypermid)
            players = {}
            players[mypermid] = colour
            game['players'] = bencode(players)
            game['gamename'] = 'chess'
            game['time'] = tc_time
            game['inc'] = tc_inc
            game['is_finished'] = 0
            game['lastmove_time'] = 0
            game['creation_time'] = 0
            self.gamecast_database.addGame(game)
            # Create the invite, add it to the db, and send it.
            invite = {}
            invite['game_id'] = game['game_id']
            if ttype == CMD_SEEK:
                invite['target_id'] = -1
            else:
                if not self.gamecast_gossip.data_handler.peers:
                    time = random.randint(20, 60)
                    print 'TaskDispatcher next task: ttype=%s ; sleep_before=%d' % (None, time)
                    return (None, time)
                invite['target_id'] = random.choice(self.gamecast_gossip.data_handler.peers.keys())
            invite['min_rating'] = random.randint(1000, 2000)
            invite['max_rating'] = random.randint(2000, 3000)
            invite['time'] = tc_time
            invite['inc'] = tc_inc
            invite['gamename'] = 'chess'
            invite['colour'] = 'white' if colour == 'black' else 'black'
            self.gamecast.executeSeekOrMatch(invite)
        task = lambda:self.overlay_bridge.add_task(doSeekOrMatch, 0)
        time = random.randint(20, 60)
        print 'TaskDispatcher next task: ttype=%s ; sleep_before=%d' % (ttype, time)
        return (task, time)

    def selectDiscuss(self, ttype):
        # Pick a random closed game, and send a discuss command to its owner.
        game = {}
        if self.closed_games:
            game = random.choice(self.closed_games)
            message = {}
            message['game_id'] = game['game_id']
            message['game_owner_id'] = game['owner_id']
            message['content'] = 'This is a testing message'
            task = lambda:self.gamecast.executeDiscuss(message)
        else:
            task = None
        time = random.randint(10, 60)
        print 'TaskDispatcher next task: ttype=%s ; sleep_before=%d ; owner_id=%d ; game_id=%s' % \
              (ttype, time, game.get('owner_id', -1), game.get('game_id', -1))
        return (task, time)

    def selectNop(self, ttype):
        # Selecting No Operation
        task = None
        time = random.randint(0, 600)
        print 'TaskDispatcher next task: ttype=nop ; sleep_before=%d' % time
        return (task, time)

    def run(self):
        while not self.done:
            # First sleep, then execute the next task.
            ttype = self.selectTaskType()
            task, time = self.selectTask(ttype)
            sleep(time)
            if task:
                task()
                if ttype == CMD_MOVE:
                    sleep(4)

    def showIP(self, ip):
        return "%3s.%3s.%3s.%3s" % tuple(ip.split("."))

    def showPort(self, port):
        return "%5s" % port

    def log(self, loggertype, *args, **kwargs):
        if loggertype == GCLOG:
            logger = self.gc_logger
        elif loggertype == GCGLOG:
            logger = self.gcg_logger
        else:
            return
        if logger and args:
            d = {'event_type'    : args[0],
                 'at_peer_short' : '%s (%s:%s)' % (showPermid(self.mypermid), self.showIP(self.myip), self.showPort(self.myport)),
                 'at_peer'       : '%s (%s:%s)' % (bin2str(self.mypermid), self.showIP(self.myip), self.showPort(self.myport)),
                 'tf_peer_short' : '%s (%s:%s)' % (showPermid(args[3]), self.showIP(args[1]), self.showPort(args[2])) if len(args) > 1 else '',
                 'tf_peer'       : '%s (%s:%s)' % (bin2str(args[3]), self.showIP(args[1]), self.showPort(args[2])) if len(args) > 1 else ''}
            msg = ''
            iterator = iter(sorted(kwargs.iteritems()))
            for key, value in iterator:
                msg += "%s = %s ; " % (key, value)
            if msg:
                msg = msg[:-3]
            logger.info(msg, extra=d)


class Unbuffered:
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        self.stream.write(data)
        self.stream.flush()

    def __getattr__(self, attr):
        return getattr(self.stream, attr)

def profiler(frame, event, arg):
    if event not in ('call','return'): return profiler
    #### gather stats ####
    rusage = getrusage(RUSAGE_SELF)
    t_cpu = rusage[0] + rusage[1] # user time + system time
    code = frame.f_code
    fun = (code.co_name, code.co_filename, code.co_firstlineno)
    #### get stack with functions entry stats ####
    ct = threading.currentThread()
    try:
        p_stack = ct.p_stack
    except AttributeError:
        ct.p_stack = deque()
        p_stack = ct.p_stack
    #### handle call and return ####
    if event == 'call':
        p_stack.append((time(), t_cpu, fun))
    elif event == 'return':
        try:
            t,t_cpu_prev,f = p_stack.pop()
            assert f == fun
        except IndexError: # TODO investigate
            t,t_cpu_prev,f = p_start_time, 0.0, None
        call_cnt, t_sum, t_cpu_sum = p_stats.get(fun, (0, 0.0, 0.0))
        p_stats[fun] = (call_cnt+1, t_sum+time()-t, t_cpu_sum+t_cpu-t_cpu_prev)
    return profiler

def profile_on():
    global p_stats, p_start_time
    p_stats = {}
    p_start_time = time()
    threading.setprofile(profiler)
    sys.setprofile(profiler)

def profile_off():
    threading.setprofile(None)
    sys.setprofile(None)

def get_profile_stats():
    return p_stats

def startsession():
    sscfg = SessionStartupConfig()
    sscfg.set_nickname(config['name'])
    sscfg.set_listen_port(config['port'])
    sscfg.set_state_dir(config['statedir'])
    sscfg.set_superpeer(config['superpeer'])
    sscfg.set_gc_superpeer_file(config['superpeer_file'])
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

    global session, orig_stdin, orig_stdout
    session = Session(sscfg)
    pickle.dump(session.get_permid(), orig_stdout)
    orig_stdout.flush()


if __name__ == "__main__":
#    profile_on()

    global orig_stdin, orig_stdout
    orig_stdin  = sys.stdin#Unbuffered(sys.stdin)
    orig_stdout = sys.stdout#Unbuffered(sys.stdout)
    sys.stdout  = sys.stderr
    sys.stdin   = None

    config, fileargs = parseargs(sys.argv, argsdef, presets = {})
    config['statedir'] = os.path.join(os.getcwd(), os.path.pardir, '.'+config['name'])
    print >> sys.stderr, "Config is", config

    overlay_bridge = OverlayThreadingBridge.getInstance()
    overlay_bridge.gcqueue = overlay_bridge.tqueue
    overlay_bridge.add_task(startsession, 0)

    params = pickle.load(orig_stdin)
    print 'TaskDispatcher params', params
    global session
    td = TaskDispatcher.getInstance(session)
    if params:
        td.max_games = params[0]
        td.start()
        print 'TaskDispatcher has started'

    while True:
        try:
            text = orig_stdin.readline()
            text = text.strip().split(' ')
        except:
            break
        print "Received command:", text[0]
        if text[0] == 'stop':
            # Stop the application
            if len(text) != 1:
                print 'Invalid args'
                continue
            td.done = True
            break
        else:
            print "Invalid command"
        sleep(1)

 #   profile_off()
 #   from pprint import pprint
 #   stats = get_profile_stats()
 #   stats = dict([(k,v) for k,v in stats.iteritems() if v[1] > 10])
 #   pprint(stats)

#    global session
    session.shutdown()
    sleep(3)
