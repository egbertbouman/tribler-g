# Written by Egbert Bouman
# see LICENSE for license information

import re
import sys
import cPickle
import logging
import Tribler.Core.GameCast.glicko.glicko as glicko

from sets import Set
from time import time
from copy import deepcopy, copy
from logging.handlers import SocketHandler, DEFAULT_TCP_LOGGING_PORT

from Tribler.Core.simpledefs import *
from Tribler.Core.BitTornado.bencode import bencode, bdecode
from Tribler.Core.BitTornado.BT1.MessageID import GC_CMD
from Tribler.Core.CacheDB.CacheDBHandler import PeerDBHandler, GameCastDBHandler
from Tribler.Core.CacheDB.sqlitecachedb import bin2str, str2bin
from Tribler.Core.Overlay.permid import verify_data, sign_data
from Tribler.Core.Overlay.SecureOverlay import OLPROTO_VER_SEVENTEETH
from Tribler.Core.GameCast.GameCastGossip import GameCastGossip
from Tribler.Core.Statistics.Logger import Logger
from Tribler.Core.Utilities.utilities import validPermid, validIP, validPort
from Tribler.Core.Utilities.utilities import show_permid_short as showPermid
from Tribler.Utilities.TimedTaskQueue import TimedTaskQueue

DEBUG         = True
HOPS          = 2

# Ways of how a game can finish
AGREE_ABORT   = -1
AGREE_DRAW    = 2
CLOSE_MOVE    = 1
RESIGN        = 3

# List of available commands
CMD_SEEK      = 'seek'
CMD_MATCH     = 'match'
CMD_PLAY      = 'play'
CMD_UNSEEK    = 'unseek'
CMD_ACCEPT    = 'accept'
CMD_DECLINE   = 'decline'
CMD_START     = 'start'
CMD_MOVE      = 'move'
CMD_ABORT     = 'abort'
CMD_DRAW      = 'draw'
CMD_RESIGN    = 'resign'
CMD_DISCUSS   = 'discuss'

# Regular expressions used for parsing commands
REG_SEEK      = '^(?P<cmd>seek) (?P<time>\d+) (?P<inc>\d+) rated (?P<colour>\S*) manual (?P<min_rating>\d+)-(?P<max_rating>\d+) (?P<gamename>\S*) (?P<invite_id>\d+) (?P<game_id>\d+)[ ]?(?P<age>\d*)$'
REG_MATCH     = '^(?P<cmd>match) (?P<user>.+) rated (?P<time>\d+) (?P<inc>\d+) (?P<colour>\S*) (?P<gamename>\S*) (?P<invite_id>\d+) (?P<game_id>\d+)$'
REG_PLAY      = '^(?P<cmd>play) (?P<invite_id>\d+)$'
REG_UNSEEK    = '^(?P<cmd>unseek) (?P<invite_id>\d+)$'
REG_ACCEPT    = '^(?P<cmd>accept) (?P<invite_id>\d+)$'
REG_DECLINE   = '^(?P<cmd>decline) (?P<invite_id>\d+)$'
REG_START     = '^(?P<cmd>start) (?P<game_id>\d+) (?P<players>.*)$'
REG_MOVE      = '^(?P<move>\S*[^a-z]\S*) (?P<moveno>\d+) (?P<time_taken>\d+) (?P<game_id>\d+)$'
REG_ABORT     = '^(?P<cmd>abort) (?P<moveno>\d+) (?P<game_id>\d+)$'
REG_DRAW      = '^(?P<cmd>draw) (?P<moveno>\d+) (?P<game_id>\d+)$'
REG_RESIGN    = '^(?P<cmd>resign) (?P<moveno>\d+) (?P<game_id>\d+)$'
REG_DISCUSS   = '^(?P<cmd>discuss) (?P<game_id>\d+) (?P<message_id>\d+) (?P<content>.*)$'

MAX_CORRECTION      = 10000  # Maximum number of milliseconds that should be added to your opponents clock to account for network delay
INV_EXPIRE_TIME     = 15*60  # If an invite has not received any response within this time it is considered expired
GMS_EXPIRE_TIME     = 15*60  # If a game that has just started has not received a first move for each of the players within this time it is considered expired
MSG_EXPIRE_TIME     = 15*60  # If a message has not been send within this time, discard
MSG_POSTPONE_TIME   = 15*60  # If a message has not been delivered within this time, discard
RETRY_INCOMING_TIME = 30     # Interval in which the retryIncoming method should be executed
RETRY_OUTGOING_TIME = 30     # Interval in which the retryOutgoing method should be executed


class GameCast:
    __single = None

    def getInstance(*args, **kw):
        if GameCast.__single is None:
            GameCast(*args, **kw)
        return GameCast.__single
    getInstance = staticmethod(getInstance)

    def __init__(self):
        if GameCast.__single:
            raise RuntimeError, "GameCast is singleton"
        GameCast.__single = self

        self.invites              = []  # [{invite_id, owner_id, target_id, game_id, min_rating, max_rating,
                                        #   time, inc, gamename, colour, status, creation_time}]
        self.games                = []  # [{game_id, owner_id, winner_permid, moves, players,
                                        #   time, inc, creation_time, timeout_time, is_finished}]
        self.peers                = {}  # {peer_id: {permid, ip, port}}
        self.messages_out         = {}  # {permid: [(msg_type, msg_payload, exp_time)]}
        self.messages_out_sending = {}  # {permid: [(msg_type, msg_payload, exp_time)]}
        self.messages_in          = {}  # {permid: {game_id: [(msg_payload, exp_time)]}}
        self.request_cache        = {}  # {(game_id, CMD_..): {permid: timestamp})
        self.request_record       = {}

        self.regExpressions       = [REG_SEEK, REG_MATCH, REG_PLAY, REG_UNSEEK, REG_ACCEPT, REG_DECLINE, REG_START, REG_MOVE, REG_DISCUSS]
        self.inviteCommands       = [CMD_SEEK, CMD_MATCH, CMD_PLAY, CMD_UNSEEK, CMD_ACCEPT, CMD_DECLINE]
        self.gamingCommands       = [CMD_START, CMD_MOVE, CMD_ABORT, CMD_DRAW, CMD_RESIGN]
        self.reviewCommands       = [CMD_DISCUSS]

    def register(self, overlay_bridge, launchmany, config):
        self.overlay_bridge = overlay_bridge
        self.launchmany = launchmany
        self.session = launchmany.session
        self.config = config
        self.dnsindb = launchmany.secure_overlay.get_dns_from_peerdb

        self.mypermid = self.session.get_permid()
        self.myip = self.session.get_external_ip()
        self.myport = self.session.get_listen_port()
        self.myname = self.session.sessconfig.get('nickname', '')

        self.peer_db = launchmany.peer_db
        self.gamecast_db = launchmany.gamecast_db
        self.gamecast_gossip = GameCastGossip.getInstance()

        # Setup a local & remote logger
        self.logger = logging.getLogger('gamecast')
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(created).3f   %(event_type)-8s   %(tf_peer_short)-34s   %(message)s')
        fileHandler = logging.FileHandler(os.path.join(self.session.get_state_dir(), 'gamecast.log'))
        fileHandler.setFormatter(formatter)
        self.memoryHandler = logging.handlers.MemoryHandler(100, flushLevel=logging.DEBUG, target=fileHandler)
        self.logger.addHandler(self.memoryHandler)
        #socketHandler = SocketHandler('gamecast.no-ip.org', DEFAULT_TCP_LOGGING_PORT)
        #self.logger.addHandler(socketHandler)

        self.loadMessageQueue()

        self.overlay_bridge.add_task(self.loadPeers, 1, gamecast=True)
        self.overlay_bridge.add_task(self.loadInvites, 1, gamecast=True)
        self.overlay_bridge.add_task(self.loadGames, 2, gamecast=True)
        self.overlay_bridge.add_task(self.retryOutgoing, 4, gamecast=True)
        self.overlay_bridge.add_task(self.retryIncoming, RETRY_INCOMING_TIME, gamecast=True)

    def loadPeers(self):
        # Load all GameCast peers into the cache
        peers = self.peer_db.getAll(['peer_id', 'permid', 'ip', 'port', 'name'], where='gc_member=1', limit=2500)
        for peer in peers:
            peerdict = {'permid':str2bin(peer[1]), 'ip':peer[2], 'port':peer[3]}
            if peer[4]:
                peerdict.update({'name':peer[4]})
            self.peers[peer[0]] = peerdict

    def loadInvites(self):
        # Load all valid invites into the cache
        self.invites = self.gamecast_db.getInvites()
        self.deleteExpiredInvites()

    def loadGames(self):
        # Load all unfinished games into the cache
        self.games = self.gamecast_db.getGames(is_finished = 0)

    def deleteExpiredInvites(self):
        # Remove expired invites from the cache and the db
        index = 0
        nextid = self.gamecast_db.getNextInviteID()
        while index < len(self.invites):
            invite = self.invites[index]
            if time() > invite['creation_time']+INV_EXPIRE_TIME:
                # Delete all expired invites, except the most recent one that we own
                if not (nextid == invite['invite_id']+1 and invite['owner_id'] == 0):
                    self.gamecast_db.deleteInvite(invite['invite_id'], invite['owner_id'], commit=False)
                    self.invites.pop(index)
                    continue
            index += 1
        self.gamecast_db.commit()

    def getPeer(self, peer_permid):
        if isinstance(peer_permid, int) and peer_permid > 0:
            peer_id = peer_permid
            if self.peers.has_key(peer_id):
                return self.peers[peer_id]
            else:
                peer_permid = self.peer_db.getPermid(peer_id)
        elif peer_permid == self.mypermid or peer_permid == 0:
            if self.myname:
                return {'permid':self.mypermid, 'ip':self.myip, 'port':self.myport, 'name':self.myname}
            else:
                return {'permid':self.mypermid, 'ip':self.myip, 'port':self.myport}
        else:
           for peer_id, peer in self.peers.iteritems():
               if peer['permid'] == peer_permid:
                   return peer
           peer_id = self.peer_db.getPeerID(peer_permid)
        # The peer-info was not in the cache, so we need to retrieve it from the db.
        peer = self.peer_db.getPeer(peer_permid)
        if peer:
            self.peers[peer_id] = {'permid':peer_permid, 'ip':str(peer['ip']), 'port':peer['port']}
            if peer.get('name', ''):
                self.peers[peer_id].update({'name':peer['name']})
            return self.peers[peer_id]
        return None

    def getPeerID(self, peer_permid):
        for peer_id, peer in self.peers.iteritems():
            if peer['permid'] == peer_permid:
                return peer_id
        return self.peer_db.getPeerID(peer_permid)

    def getInvite(self, owner_permid, invite_id):
        if isinstance(owner_permid, int) and owner_permid > 0:
            owner_id = owner_permid
        elif owner_permid == self.mypermid or owner_permid == 0:
            owner_id = 0
        else:
            owner_id = self.getPeerID(owner_permid)
        for invite in self.invites:
            if invite['invite_id'] == invite_id and invite['owner_id'] == owner_id:
                return invite
        invites = self.gamecast_db.getInvites(invite_id = invite_id, owner_id = owner_id)
        if invites and len(invites) == 1:
            self.invites.append(invites[0])
            return invites[0]
        return None

    def getGame(self, owner_permid, game_id):
        if isinstance(owner_permid, int) and owner_permid > 0:
            owner_id = owner_permid
        elif owner_permid == self.mypermid or owner_permid == 0:
            owner_id = 0
        else:
            owner_id = self.getPeerID(owner_permid)
        for game in self.games:
            if game['game_id'] == game_id and game['owner_id'] == owner_id:
                return game
        games = self.gamecast_db.getGames(game_id = game_id, owner_id = owner_id)
        if games and len(games) == 1:
            self.games.append(games[0])
            return games[0]
        return None

    def setGame(self, game):
        gamerec = deepcopy(game)
        gamerec['winner_permid'] = bin2str(game['winner_permid'])
        gamerec['players'] = bencode(dict((bin2str(key), value) for (key, value) in game['players'].items()))
        gamerec['moves'] = bencode(game['moves'])
        self.gamecast_db.updateGame(gamerec)

    def getInviteState(self, invite, send_or_recv, cmd_type):
        # For each invite, the status-field keeps track of whether a certain command is send/received.
        # To retrieve a boolean variable from the status-field, a bitmask is used.
        if cmd_type not in self.inviteCommands or send_or_recv not in ['send', 'recv']:
            return False
        index = 2*self.inviteCommands.index(cmd_type)
        if send_or_recv == 'recv':
            index += 1
        if not invite.has_key('status'):
            invite['status'] = 0
        return ((invite['status'] & 2**index) > 0)

    def setInviteState(self, invite, send_or_recv, cmd_type, update=True):
        if cmd_type not in self.inviteCommands or send_or_recv not in ['send', 'recv']:
            return False
        index = 2*self.inviteCommands.index(cmd_type)
        if send_or_recv == 'recv':
            index += 1
        if not invite.has_key('status'):
            invite['status'] = 0
        invite['status'] = 2**index | invite['status']
        if update:
            self.gamecast_db.updateInvite(invite)
        return invite['status']

    def getOutstandingInvites(self, game):
        # Retrieve any outstanding invites for this game
        index = 0
        outstanding_invites = self.gamecast_db.getInvites(owner_id = game['owner_id'], game_id = game['game_id'])
        while index < len(outstanding_invites):
            invite = outstanding_invites[index]
            if invite['target_id'] < 0 and not self.getInviteState(invite, 'send', CMD_ACCEPT):
                index += 1
                continue
            elif invite['target_id'] > 0 and not self.getInviteState(invite, 'recv', CMD_ACCEPT):
                index += 1
                continue
            else:
                outstanding_invites.pop(index)
        return outstanding_invites

    def getRating(self, permid, gamename):
        rating = self.gamecast_db.getRating(permid = permid, gamename = gamename)
        if rating:
            return (rating['rating'], rating['rd'])
        return self.getDefaultRating(gamename)

    def getDefaultRating(self, gamename):
        # Default ratings depend on which game we are dealing with (1500 is the default for chess)
        if gamename == 'chess':
            return (1500, 200)
        else:
            return (0, 0)

    def updateRating(self, gamename):
        # This method for update ratings is currently not very efficient,
        # but for now it will do. Works only with Glicko (and therefore 2-player games)

        queue = []
        permids = []
        permid_to_lmtime = {}
        results = self.gamecast_db.getResults(gamename)

        for result in results:
            for player in result[1].keys():
                if player not in permids:
                    permids.append(player)

        default_rating = self.getDefaultRating(gamename)
        new_ratings = dict([(permid, default_rating) for permid in permids])

        for index in range(len(results)):
            time = results[index][0]/60
            players = results[index][1].keys()
            winner = results[index][2]

            player1 = players[0]
            player2 = players[1]
            time1 = time - permid_to_lmtime.get(player1, time-1)
            time2 = time - permid_to_lmtime.get(player2, time-1)
            permid_to_lmtime[player1] = time
            permid_to_lmtime[player2] = time
            score1 = 0.5 if not winner else (1 if winner == player1 else 0)
            score2 = 0.5 if not winner else (1 if winner == player2 else 0)
            rating1, rd1 = new_ratings[player1]
            rating2, rd2 = new_ratings[player2]
            glicko1 = glicko.Player(rating = rating1, rd = rd1)
            glicko2 = glicko.Player(rating = rating2, rd = rd2)
            glicko1.update_player(time1, [rating2], [rd2], [score1])
            glicko2.update_player(time2, [rating1], [rd1], [score2])
            new_ratings[player1] = (glicko1.rating, glicko1.rd)
            new_ratings[player2] = (glicko2.rating, glicko2.rd)

        for player, rating in new_ratings.items():
            self.gamecast_db.setRating(player, gamename, rating)

    def addPlayerToGame(self, invite, permid):
        # Update the list of players for the game
        game = self.getGame(0, invite['game_id'])
        game['players'][permid] = invite['colour']
        start_game = not bool(self.getOutstandingInvites(game))
        if start_game:
            game['creation_time'] = time()
        self.setGame(game)
        # Are there any other outstanding invites for this game?? If not, start the game
        if start_game:
            self._executeStart(invite['owner_id'], invite['game_id'])

    def addMoveToGame(self, player_permid, move, game, correction = 0):
        now = time()
        player_colour = game['players'][player_permid]
        player_counter = 0
        num_players = len(game['players'])
        if len(game['moves']) >= num_players:
            # Calculate the amount of time that it has taken to make this move
            player_counter = now - game['lastmove_time']
            player_counter = int(player_counter*1000)
            player_counter -= correction
            # Add to that the time taken by moves from previous rounds
            player_counter += game['moves'][-num_players][2]
        game['moves'].append((player_colour, move, player_counter))
        game['lastmove_time'] = now
        self.setGame(game)
        # If needed, update the ratings (works only for 2-player games)
        if game['is_finished'] > 0:
            self.updateRating(game['gamename'])

    def addRequestToGame(self, player_permid, command, moveno, game):
        if not command in [CMD_ABORT, CMD_DRAW]:
            return

        moves = game['moves']
        num_moves = len(moves)
        players = game['players']
        num_players = len(players)
        if not player_permid in players.keys():
            return

        game_id = game['game_id']
        owner_id = game['owner_id']

        # Filter out expired requests
        requests = self.request_cache.get((owner_id, game_id, command), {})
        index = 0
        while index < len(requests):
            for permid, moveno in requests.items():
                if moveno < num_moves-num_players:
                    requests.pop(permid)
                else:
                    index += 1

        # Save the new request
        requests[player_permid] = moveno
        self.request_cache[(owner_id, game_id, command)] = requests

        # For GUI, should probably be done different..
        rr = self.request_record.get((owner_id, game_id), [])
        rr.append((command, player_permid, num_moves))
        self.request_record[(owner_id, game_id)] = rr

        # Have all players sent the same command?
        if len(requests) >= num_players:
            if command == CMD_ABORT:
                game['is_finished'] = AGREE_ABORT
                game['finished_time'] = time()
                self.debug('game aborted by agreement (owner_id = %d, game_id = %d)' % (owner_id, game_id))
            else:
                game['is_finished'] = AGREE_DRAW
                game['finished_time'] = time()
                self.debug('game draw by agreement (owner_id = %d, game_id = %d)' % (owner_id, game_id))
                # Update the ratings (works only for 2-player games)
                self.updateRating(game['gamename'])
            self.setGame(game)
        elif moveno < num_players and command == CMD_ABORT:
            game['is_finished'] = AGREE_ABORT
            game['finished_time'] = time()
            self.debug('game aborted by %s (owner_id = %d, game_id = %d)' % (game['players'][player_permid], owner_id, game_id))
            self.setGame(game)
        if game['is_finished']:
            self.gamecast_gossip.data_handler.updateInteractionFactors()
            self.log('GAMEDONE', is_finished=game['is_finished'], time_left=self.getGameClocks(game), \
                     game='(%d,%s)' % (game['owner_id'], game['game_id']), owner_permid=bin2str(self.getPeer(game['owner_id'])['permid']), my_colour=game['players'][self.mypermid])

    def resendSeek(self, invite):
        self.debug('scheduling _resendSeek on gamecast thread')
        func = lambda:self._resendSeek(invite)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def executeSeekOrMatch(self, invite):
        self.debug('scheduling _executeSeekOrMatch on gamecast thread')
        func = lambda:self._executeSeekOrMatch(invite)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def executePlay(self, owner_id, invite_id):
        self.debug('scheduling _executePlay on gamecast thread')
        func = lambda:self._executePlay(owner_id, invite_id)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def executeAccept(self, owner_id, invite_id):
        self.debug('scheduling _executeAccept on gamecast thread')
        func = lambda:self._executeAccept(owner_id, invite_id)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def executeMove(self, owner_id, game_id, move):
        self.debug('scheduling _executeMove on gamecast thread')
        func = lambda:self._executeMove(owner_id, game_id, move)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def executeAbort(self, owner_id, game_id):
        self.debug('scheduling _executeAbort on gamecast thread')
        func = lambda:self._executeAbort(owner_id, game_id)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def executeDraw(self, owner_id, game_id):
        self.debug('scheduling _executeDraw on gamecast thread')
        func = lambda:self._executeDraw(owner_id, game_id)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def executeResign(self, owner_id, game_id):
        self.debug('scheduling _executeResign on gamecast thread')
        func = lambda:self._executeResign(owner_id, game_id)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def executeDiscuss(self, message):
        self.debug('scheduling _executeDiscuss on gamecast thread')
        func = lambda:self._executeDiscuss(message)
        self.overlay_bridge.add_task(func, 0, gamecast=True)

    def _resendSeek(self, invite):
        # Only used by chessbot
        targets = self.gamecast_gossip.connected_game_buddies + self.gamecast_gossip.connected_random_peers
        self.debug('trying to resend %s to %d connected peers' % (CMD_SEEK, len(targets)))
        self.messageSend(targets, CMD_SEEK, invite, HOPS)

    def _executeSeekOrMatch(self, invite):
        # Invite should be of the form
        # {'target_id', 'game_id', 'min_rating', 'max_rating', 'time', 'inc', 'gamename', 'colour'}
        required_keys = ['target_id', 'game_id', 'min_rating', 'max_rating', 'time', 'inc', 'gamename', 'colour']
        if set(invite.keys()) - set(required_keys):
            self.debug('executeSeekOrMatch received incorrect params')
            return
        target_id = invite['target_id']
        if target_id == 0:
            self.debug('executeSeekOrMatch received target_id = 0')
            return

        self.debug('sending invite', invite)

        # Save the invite to the cache
        invite['invite_id']     = self.gamecast_db.getNextInviteID()
        invite['owner_id']      = 0
        invite['status']        = 0
        invite['creation_time'] = time()
        self.invites.append(invite)
        # Import the invite to the database
        self.gamecast_db.addInvite(invite)

        if target_id > 0:
            # We are dealing with a personal invite, which is send using the match command
            target_permid = self.getPeer(target_id)['permid']
            targets = [target_permid]
            self.debug('trying to send %s to' % CMD_MATCH, showPermid(target_permid))
            self.messageSend(targets, CMD_MATCH, invite)
        else:
            # We are dealing with a random invite, which is send to all connected
            # game buddies & random peers using the seek command
            targets = self.gamecast_gossip.connected_game_buddies + self.gamecast_gossip.connected_random_peers
            self.debug('trying to send %s to %d connected peers' % (CMD_SEEK, len(targets)))
            self.messageSend(targets, CMD_SEEK, invite, HOPS)
        return invite

    def _executePlay(self, owner_id, invite_id):
        # Owner_id and invite_id are used to identify
        # the invite in question. This method is used for responding to seek requests.
        if owner_id == 0:
            self.debug('executePlay received owner_id = 0')
            return
        invite = self.getInvite(owner_id, invite_id)
        if not invite:
            self.debug('executePlay received unknown invite (owner_id = %d, invite_id = %d)' % (owner_id, invite_id))
            return
        if invite['target_id'] >= 0:
            self.debug('executePlay received personal invite (owner_id = %d, invite_id = %d)' % (owner_id, invite_id))
            return

        owner_permid = self.getPeer(owner_id)['permid']
        self.messageSend([owner_permid], CMD_PLAY, invite)

    def _executeAccept(self, owner_id, invite_id):
        # Can be called from any thread; owner_id and invite_id are used to identify
        # the invite in question. This method is used only for accepting match requests.
        if owner_id == 0:
            self.debug('executeAccept received owner_id = 0')
            return
        invite = self.getInvite(owner_id, invite_id)
        if not invite:
            self.debug('executeAccept received unknown invite (owner_id = %d, invite_id = %d)' % (owner_id, invite_id))
            return
        if invite['target_id'] < 0:
            self.debug('executeAccept received random invite (owner_id = %d, invite_id = %d)' % (owner_id, invite_id))
            return

        owner_permid = self.getPeer(owner_id)['permid']
        self.messageSend([owner_permid], CMD_ACCEPT, invite)

    def _executeStart(self, owner_id, game_id):
        # Retrieve the game from the cache or from the db
        if owner_id != 0:
            self.debug('executeStart received owner_id = %d' % owner_id)
            return
        game = self.getGame(owner_id, game_id)
        if not game:
            self.debug('executeStart received unknown game (owner_id = %d, game_id = %d)' % (owner_id, game_id))
            return

        # Send the start command to all players
        targets = game['players'].keys()
        try:
            targets.remove(self.mypermid)
        except:
            pass
        self.debug('trying to send %s to %d gamers' % (CMD_START, len(targets)))
        self.messageSend(targets, CMD_START, game)

    def _executeMove(self, owner_id, game_id, move):
        # Retrieve the game from the cache or from the db
        game = self.getGame(owner_id, game_id)
        if not game:
            self.debug('executeMove received unknown game (owner_id = %d, game_id = %d)' % (owner_id, game_id))
            return
        # Is it our turn?
        mycolour = game['players'][self.mypermid]
        if self.getGameTurn(game) != mycolour:
            self.debug('cannot send %s while it is not our turn' % CMD_MOVE)
            return
        # Is this move a valid move?
        state = self.getGameState(game, move)
        if state < 0:
            self.debug('cannot send %s with an illegal move (%s)' % (CMD_MOVE, move))
            return
        # Is this move a closing move?
        if state > 0:
            self.debug('about to send closing %s (%s)' % (CMD_MOVE, move))
            game['is_finished'] = CLOSE_MOVE
            game['finished_time'] = time()
            game['winner_permid'] = self.getGameWinner(game, move)

        # Add the move to the game
        self.addMoveToGame(self.mypermid, move, game)
        if game['is_finished']:
            self.gamecast_gossip.data_handler.updateInteractionFactors()
            self.log('GAMEDONE', is_finished=game['is_finished'], time_left=self.getGameClocks(game), \
                     game='(%d,%s)' % (game['owner_id'], game['game_id']), owner_permid=bin2str(self.getPeer(game['owner_id'])['permid']), my_colour=game['players'][self.mypermid])

        # Send the move command to all other players
        targets = game['players'].keys()
        try:
            targets.remove(self.mypermid)
        except:
            pass
        self.messageSend(targets, CMD_MOVE, game)

    def _executeAbort(self, owner_id, game_id):
        # Retrieve the game from the cache or from the db
        game = self.getGame(owner_id, game_id)
        if not game:
            self.debug('executeAbort received unknown game (owner_id = %d, game_id = %d)' % (owner_id, game_id))
            return

        self.addRequestToGame(self.mypermid, CMD_ABORT, len(game['moves']), game)

        # Send the abort command to all other players
        targets = game['players'].keys()
        try:
            targets.remove(self.mypermid)
        except:
            pass
        self.messageSend(targets, CMD_ABORT, game)

    def _executeDraw(self, owner_id, game_id):
        # Retrieve the game from the cache or from the db
        game = self.getGame(owner_id, game_id)
        if not game:
            self.debug('executeDraw received unknown game (owner_id = %d, game_id = %d)' % (owner_id, game_id))
            return

        self.addRequestToGame(self.mypermid, CMD_DRAW, len(game['moves']), game)

        # Send the draw command to all other players
        targets = game['players'].keys()
        try:
            targets.remove(self.mypermid)
        except:
            pass
        self.messageSend(targets, CMD_DRAW, game)

    def _executeResign(self, owner_id, game_id):
        # Retrieve the game from the cache or from the db
        game = self.getGame(owner_id, game_id)
        if not game:
            self.debug('executeResign received unknown game (owner_id = %d, game_id = %d)' % (owner_id, game_id))
            return

        # Save resign to db
        players = game['players'].keys()
        try:
            players.remove(self.mypermid)
        except:
            pass
        opponent_permid = players[0]
        game['winner_permid'] = opponent_permid
        game['is_finished'] = RESIGN
        game['finished_time'] = time()
        self.setGame(game)
        self.gamecast_gossip.data_handler.updateInteractionFactors()
        self.log('GAMEDONE', is_finished=game['is_finished'], time_left=self.getGameClocks(game), \
                 game='(%d,%s)' % (game['owner_id'], game['game_id']), owner_permid=bin2str(self.getPeer(game['owner_id'])['permid']), my_colour=game['players'][self.mypermid])
        # Update the ratings (works only for 2-player games)
        self.updateRating(game['gamename'])

        # Send the resign command to all other players
        targets = game['players'].keys()
        try:
            targets.remove(self.mypermid)
        except:
            pass
        self.messageSend(targets, CMD_RESIGN, game)

    def _executeDiscuss(self, message):
        # Can be called from any thread; message should be of the form
        # {'game_id', 'game_owner_id', 'content'}
        required_keys = ['game_id', 'game_owner_id', 'content']
        if set(message.keys()) - set(required_keys):
            self.debug('executeDiscuss received incorrect params')
            return

        self.debug('sending discussion message', str(message))

        # Add the discussion message to the db
        message['message_id'] = self.gamecast_db.getNextMessageID()
        message['owner_id'] = 0
        message['creation_time'] = time()
        self.gamecast_db.addMessage(message)

        # Only send the discuss command for games that we do not own
        target_id = message['game_owner_id']
        if target_id == 0:
            return

        # Send the discuss command to the owner of the game (who will then spread the message further)
        target_permid = self.getPeer(target_id)['permid']
        self.debug('trying to send %s to' % CMD_DISCUSS, showPermid(target_permid))
        self.messageSend([target_permid], CMD_DISCUSS, message)

    def createInviteCommand(self, cmd_type, invite, target = None):
        cmd = None
        if cmd_type == CMD_SEEK:
            # Construct the seek command, which is in the form:
            # seek <time> <inc> <type> <colour> <start> <min_rating>-<max_rating> <gamename> <invite_id> <game_id>
            # NOTE: the final three arguments are not available in ICS
            time_start = invite['time']
            inc        = invite['inc']
            type       = 'rated'
            colour     = invite['colour']
            start      = 'manual'
            min_rating = invite['min_rating']
            max_rating = invite['max_rating']
            gamename   = invite['gamename']
            invite_id  = invite['invite_id']
            game_id    = invite['game_id']
            age        = time()-invite['creation_time']
            cmd        = '%s %d %d %s %s %s %d-%d %s %d %d %d' % (CMD_SEEK, time_start, inc, type, colour, start, min_rating, max_rating, gamename, invite_id, game_id, age)
        elif cmd_type == CMD_MATCH and target != None:
            # Construct the match command, which is in the form:
            # match <user> <type> <time> <inc> <colour> <gamename> <invite_id> <game_id>
            # NOTE: the final three arguments are not available in ICS
            user       = bin2str(target)
            type       = 'rated'
            time_start = invite['time']
            inc        = invite['inc']
            colour     = invite['colour']
            gamename   = invite['gamename']
            invite_id  = invite['invite_id']
            game_id    = invite['game_id']
            cmd        = '%s %s %s %d %d %s %s %d %d' % (CMD_MATCH, user, type, time_start, inc, colour, gamename, invite_id, game_id)
        elif cmd_type == CMD_PLAY:
            # Construct the play command, which is in the form:
            # play <invite_id>
            invite_id  = invite['invite_id']
            cmd        = '%s %d' % (CMD_PLAY, invite_id)
        elif cmd_type == CMD_UNSEEK:
            # Construct the unseek command, which is in the form:
            # unseek <invite_id>
            invite_id  = invite['invite_id']
            cmd        = '%s %d' % (CMD_UNSEEK, invite_id)
        elif cmd_type == CMD_ACCEPT:
            # Construct the accept command, which is in the form:
            # accept <invite_id>
            invite_id  = invite['invite_id']
            cmd        = '%s %d' % (CMD_ACCEPT, invite_id)
        elif cmd_type == CMD_DECLINE:
            # Construct the decline command, which is in the form:
            # decline <invite_id>
            invite_id  = invite['invite_id']
            cmd        = '%s %d' % (CMD_DECLINE, invite_id)
        return cmd

    def createGamingCommand(self, cmd_type, game):
        cmd = None
        if cmd_type == CMD_START:
            # Construct the start command, which is in the form:
            # start <game_id> <players>
            # NOTE: this command is not available in ICS
            game_id    = game['game_id']
            players    = bencode(dict((bin2str(key), value) for (key, value) in game['players'].items()))
            cmd        = '%s %d %s' % (CMD_START, game_id, players)
        elif cmd_type == CMD_MOVE:
            # Construct the move command, which is in the form:
            # <move> <moveno> <time_taken> <game_id>
            # NOTE: the final three arguments are not available in ICS
            move          = game['moves'][-1][1]
            moveno        = len(game['moves'])
            num_players   = len(game['players'])
            cntr_latest   = game['moves'][-1][2]
            cntr_previous = game['moves'][-(num_players+1)][2] if len(game['moves']) > num_players else 0
            time_taken    = cntr_latest - cntr_previous
            game_id       = game['game_id']
            cmd           = '%s %d %d %d' % (move, moveno, time_taken, game_id)
        elif cmd_type in [CMD_ABORT, CMD_DRAW, CMD_RESIGN]:
            # Construct the abort or draw command, which is in the form:
            # abort <moveno> <game_id>
            # draw <moveno> <game_id>
            # resign <moveno> <game_id>
            # NOTE: the final two arguments are not available in ICS
            moveno     = len(game['moves'])
            game_id    = game['game_id']
            cmd        = '%s %d %d' % (cmd_type, moveno, game_id)
        return cmd

    def createReviewCommand(self, cmd_type, message):
        cmd = None
        if cmd_type == CMD_DISCUSS:
            # Construct the discuss command, which is in the form:
            # discuss <game_id> <message_id> <content>
            # NOTE: this command is not available in ICS
            game_id    = message['game_id']
            message_id = message['message_id']
            content    = message['content']
            cmd        = '%s %d %d %s' % (CMD_DISCUSS, game_id, message_id, content)
        return cmd

    def messageSend(self, target_permids, cmd_type, data, hops = HOPS, signature = None):
        # Create a message for each of the targets and append it to their outgoing queue.
        # Next, we will try to establish a connection to the target.
        for target_permid in target_permids:
            # Construct a new message
            msg = {}
            # Set the owner-field
            if cmd_type in self.reviewCommands:
                owner_id = data['game_owner_id']
            else:
                owner_id = data['owner_id']
            msg.update({'owner':self.getPeer(owner_id)})
            # Set the cmd-field
            if cmd_type in self.inviteCommands:
                cmd = self.createInviteCommand(cmd_type, data, target_permid)
            elif cmd_type in self.gamingCommands:
                cmd = self.createGamingCommand(cmd_type, data)
            elif cmd_type in self.reviewCommands:
                cmd = self.createReviewCommand(cmd_type, data)
            else:
                self.debug('cannot create message with command of unknown type')
                return
            msg.update({'cmd':cmd})
            # Set the hops and signature-fields for (un)seeks only (since only these messages can be forwarded)
            if cmd_type in [CMD_SEEK, CMD_UNSEEK]:
                msg.update({'hops':hops})
                if signature:
                    msg.update({'signature':signature})
                else:
                    part_cmd = cmd.rpartition(' ')[0]
                    msg.update({'signature':sign_data(bencode(part_cmd), self.session.keypair)})

            # Queue the message for sending and establish a connection to the target peer
            exp_time = int(time())+MSG_EXPIRE_TIME
            if not self.messages_out.has_key(target_permid):
                self.messages_out[target_permid] = []
            queue = self.messages_out[target_permid]
            send = True
            if cmd_type in [CMD_SEEK, CMD_UNSEEK]:
                for index, msg_tuple in enumerate(queue):
                    if msg_tuple[1] == msg:
                        queue[index] = (GC_CMD, msg, exp_time)
                        self.debug('not adding %s for %s to outgoing queue (duplicate exists)' % (self.showMessage(msg), showPermid(target_permid)))
                        send = False
                        break
            if send:
                queue.append((GC_CMD, msg, exp_time))
                self.debug('adding %s for %s to outgoing queue' % (self.showMessage(msg), showPermid(target_permid)))
                self.overlay_bridge.connect(target_permid, self.messageConnectCallback, gamecast=True)

    def messageConnectCallback(self, exc, dns, permid, selversion):
        if exc:
            self.debug('error connecting to', showPermid(permid))
            return

        now = int(time())
        queue = self.messages_out.get(permid, [])
        # Take the messages out of the queue one by one and send them.
        while queue:
            message = queue.pop(0)
            msg_type, msg_payload, exp_time = message
            if now < exp_time:
                # If Tribler exits and the message-queue is saved right after we remove the message from the
                # queue, but before we actually send it, the message will be lost. To prevent this the message
                # is temporarily added to a separate queue (self.messages_out_sending).
                sending_queue = self.messages_out_sending.get(permid, [])
                sending_queue.append(message)
                self.messages_out_sending[permid] = sending_queue

                # Send the message
                self.debug('sending %s for %s' % (self.showMessage(msg_payload), showPermid(permid)))
                cb = lambda exc, permid, message=message:self.messageSendCallback(exc, permid, message)
                self.overlay_bridge.send(permid, msg_type+bencode(msg_payload), cb, gamecast=True)

    def messageSendCallback(self, exc, permid, message):
        msg_type, msg_payload, exp_time = message
        cmd_dict = self.validCommand(msg_payload['cmd']) if msg_type == GC_CMD else None
        cmd_type = cmd_dict['cmd'] if cmd_dict else None

        # Take the message out of the messages_out_sending queue
        try:
            self.messages_out_sending[permid].remove(message)
        except:
            self.debug('could not find message', message, 'in queue', self.messages_out_sending[permid])

        # If the message was send sucessfully, log the event and do post-processing
        if exc is None:
            self.debug('successfully send %s to' % self.showMessage(msg_payload), showPermid(permid))
            peer = self.getPeer(permid)
            if peer:
                self.log('SEND_MSG', peer['ip'], peer['port'], permid, speer=self.myname, dpeer=peer.get('name', 'unknown'), msg_type=self.showMessage(msg_payload), payload=msg_payload)

            if cmd_type in self.inviteCommands:
                owner_permid = msg_payload['owner']['permid']
                invite_id = cmd_dict['invite_id']
                invite = self.getInvite(owner_permid, invite_id)

            # Do some command specific work
            if cmd_type == CMD_SEEK:
                # In case we are not forwarding the message
                if msg_payload.get('hops', HOPS) == HOPS:
                    self.setInviteState(invite, 'send', CMD_SEEK)
            elif cmd_type == CMD_MATCH:
                self.setInviteState(invite, 'send', CMD_MATCH)
            elif cmd_type == CMD_PLAY:
                self.setInviteState(invite, 'send', CMD_PLAY)
            elif cmd_type == CMD_UNSEEK:
                self.setInviteState(invite, 'send', CMD_UNSEEK)
            elif cmd_type == CMD_DECLINE:
                self.setInviteState(invite, 'send', CMD_DECLINE)
            elif cmd_type == CMD_ACCEPT:
                self.setInviteState(invite, 'send', CMD_ACCEPT)
                if invite['target_id'] < 0:
                    # If a random invite has just been accepted, it is time to send out a unseek command
                    targets = self.gamecast_gossip.connected_game_buddies + self.gamecast_gossip.connected_random_peers
                    self.debug('trying to send %s to %d connected peers' % (CMD_UNSEEK, len(targets)))
                    self.messageSend(targets, CMD_UNSEEK, invite)
                    # After an accept command is send, the peer needs to be added the list of players of the game
                    self.addPlayerToGame(invite, permid)
        else:
            # Put the message back into the queue for resending
            queue = self.messages_out.get(permid, [])
            queue.append(message)
            self.messages_out[permid] = queue
            self.debug('could not send %s to' % self.showMessage(msg_payload), showPermid(permid))
            self.debug(exc)

    def handleConnection(self, exc, permid, selversion, locally_initiated):
        if (exc is None) and (selversion >= OLPROTO_VER_SEVENTEETH):
            self.debug('established connection to', showPermid(permid))
            func = lambda:self.messageConnectCallback(None, None, permid, selversion)
            self.overlay_bridge.add_task(func, 4, gamecast=True)
        elif exc:
            self.debug('error connecting to %s (%s)' % (showPermid(permid), Exception.__str__(exc).strip()))
        return True

    def handleMessage(self, permid, selversion, message):
        # Only process messages with protovol version >= 17
        if selversion < OLPROTO_VER_SEVENTEETH:
            self.debug('received message with protocol version < 17 from peer', showPermid(permid))
            return False
        # Decode the message payload
        try:
            msg_type = message[0]
            payload = bdecode(message[1:])
        except:
            print_exc()
            return False
        # Check whether the payload is valid for that particular message type
        if msg_type == GC_CMD:
            if not self.validCommandMessage(payload):
                return False
            org_payload = deepcopy(payload)
            payload['cmd_dict'] = self.validCommand(payload['cmd'])
            # Make sure that the peer in the owner-field available in the db
            owner_permid = payload['owner']['permid']
            if not self.getPeer(owner_permid):
                peer = copy(payload['owner'])
                peer['last_seen'] = 0
                peer['gc_last_seen'] = 0
                peer['gc_member'] = 1
                self.peer_db.addPeer(peer['permid'], peer, update_dns=True, commit=True)
            # Check whether we should postpone gaming messages that are not yet ready to be processed
            if self.postponeDelivery(permid, payload):
                dns = self.dnsindb(permid)
                if dns:
                    ip,port = dns
                    self.log('POSTPONE', ip, port, permid, msg_type=self.showMessage(org_payload), payload=org_payload)
                self.debug('postponed %s from' % self.showMessage(org_payload), showPermid(permid))
                return True
            # Log the successful delivery of the message
            peer = self.getPeer(permid)
            if peer:
                self.log('RECV_MSG', peer['ip'], peer['port'], permid, speer=peer.get('name', 'unknown'), dpeer=self.myname, msg_type=self.showMessage(org_payload), payload=org_payload)
            self.debug('received %s from' % self.showMessage(org_payload), showPermid(permid))
        # Process the message
        return self.processMessage(msg_type, payload, permid)

    def processMessage(self, msg_type, payload, permid):
        # Process the message
        if msg_type == GC_CMD:
            cmd_dict = payload['cmd_dict']
            cmd_type = cmd_dict['cmd']
            if cmd_type == CMD_SEEK:
                return self.gotSeek(payload, permid)
            elif cmd_type in CMD_MATCH:
                return self.gotMatch(payload, permid)
            elif cmd_type == CMD_PLAY:
                return self.gotPlay(payload, permid)
            elif cmd_type == CMD_ACCEPT:
                return self.gotAccept(payload, permid)
            elif cmd_type == CMD_DECLINE:
                return self.gotDecline(payload, permid)
            elif cmd_type == CMD_UNSEEK:
                return self.gotUnseek(payload, permid)
            elif cmd_type == CMD_START:
                return self.gotStart(payload, permid)
            elif cmd_type == CMD_MOVE:
                return self.gotMove(payload, permid)
            elif cmd_type in [CMD_ABORT, CMD_DRAW]:
                return self.gotAbortOrDraw(payload, permid)
            elif cmd_type == CMD_RESIGN:
                return self.gotResign(payload, permid)
            elif cmd_type == CMD_DISCUSS:
                return self.gotDiscuss(payload, permid)
        return False

    def gotSeek(self, msg, sender_permid):
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        # If we dont already have this invite, import it to the cache & db
        invite = self.getInvite(owner_permid, cmd_dict['invite_id'])
        if not invite:
            # Import the invite
            invite = {}
            invite['time']          = cmd_dict['time']
            invite['inc']           = cmd_dict['inc']
            invite['colour']        = cmd_dict['colour']
            invite['min_rating']    = cmd_dict['min_rating']
            invite['max_rating']    = cmd_dict['max_rating']
            invite['gamename']      = cmd_dict['gamename']
            invite['invite_id']     = cmd_dict['invite_id']
            invite['game_id']       = cmd_dict['game_id']
            invite['owner_id']      = self.getPeerID(owner_permid)
            invite['target_id']     = -1
            invite['status']        = self.setInviteState(invite, 'recv', CMD_SEEK, update=False)
            invite['creation_time'] = time()-cmd_dict.get('age', 0)
            self.invites.append(invite)
            self.gamecast_db.addInvite(invite)
            self.debug('new invite found in %s from' % CMD_SEEK, showPermid(sender_permid))
        elif time() > invite['creation_time']+INV_EXPIRE_TIME:
            self.debug('expired invite found in %s from' % CMD_SEEK, showPermid(sender_permid))
            return True
        else:
            self.debug('duplicate invite found in %s from' % CMD_SEEK, showPermid(sender_permid))
            #return True
        # If necessary, forward the message
        if msg['hops'] > 1:
            targets = self.gamecast_gossip.connected_game_buddies + self.gamecast_gossip.connected_random_peers
            try:
                targets.remove(sender_permid)
            except:
                pass
            self.debug('trying to forward %s to %d connected peers' % (CMD_SEEK, len(targets)))
            self.messageSend(targets, CMD_SEEK, invite, msg['hops']-1, msg['signature'])
        return True

    def gotMatch(self, msg, sender_permid):
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        # If we dont already have this invite, import it to the cache & db
        invite = self.getInvite(owner_permid, cmd_dict['invite_id'])
        if not invite:
            # Import the invite
            invite = {}
            invite['time']          = cmd_dict['time']
            invite['inc']           = cmd_dict['inc']
            invite['colour']        = cmd_dict['colour']
            invite['gamename']      = cmd_dict['gamename']
            invite['invite_id']     = cmd_dict['invite_id']
            invite['game_id']       = cmd_dict['game_id']
            invite['owner_id']      = self.getPeerID(owner_permid)
            invite['target_id']     = 0
            invite['status']        = self.setInviteState(invite, 'recv', CMD_MATCH)
            invite['creation_time'] = time()
            self.invites.append(invite)
            self.gamecast_db.addInvite(invite)
            self.debug('new invite found in %s from' % CMD_MATCH, showPermid(sender_permid))
        elif time() > invite['creation_time']+INV_EXPIRE_TIME:
            self.debug('expired invite found in %s from' % CMD_MATCH, showPermid(sender_permid))
            return True
        else:
            self.debug('duplicate invite found in %s from' % CMD_MATCH, showPermid(sender_permid))
            return True
        return True

    def gotPlay(self, msg, sender_permid):
        # Before we process the message, we need to be sure that we are the owner of the invite.
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        if owner_permid != self.mypermid:
            self.debug('not the owner of the invite denoted in %s from %s' % (CMD_PLAY, showPermid(sender_permid)))
            return True
        # Check if the invite exists.
        invite = self.getInvite(owner_permid, cmd_dict['invite_id'])
        if not invite:
            self.debug('ignoring %s from %s due to unknown invite' % (CMD_PLAY, showPermid(sender_permid)))
            return True
        # Ensure that the related invite is a random invite.
        elif invite['target_id'] >= 0:
            self.debug('ignoring %s from %s because the invite is personal' % (CMD_PLAY, showPermid(sender_permid)))
            return True
        # Ensure that we did actually send a seek command.
        elif not self.getInviteState(invite, 'send', CMD_SEEK):
            self.debug('ignoring %s from %s because we are not expecting one' % (CMD_PLAY, showPermid(sender_permid)))
            return True
        # If the invite has expired, ignore the message.
        elif time() > invite['creation_time']+INV_EXPIRE_TIME:
            self.debug('ignoring %s from %s due to expired invite' % (CMD_PLAY, showPermid(sender_permid)))
            return True
        # If a player is responding to a seek without having the required rating, ignore the message.
        sender_rating = self.getRating(sender_permid, invite['gamename'])[0]
        if sender_rating < invite['min_rating'] or sender_rating > invite['max_rating']:
            self.debug('ignoring %s from %s (not allowed in the original %s)' % (CMD_PLAY, showPermid(sender_permid), CMD_SEEK))
            return True
        # Ensure that we did not already send an accept to another player.
        elif invite['target_id'] < 0 and self.getInviteState(invite, 'send', CMD_ACCEPT):
            self.debug('sending %s in response to %s from %s' % (CMD_DECLINE, CMD_PLAY, showPermid(sender_permid)))
            self.messageSend([sender_permid], CMD_DECLINE, invite)
            return True
        self.setInviteState(invite, 'recv', CMD_PLAY)
        # If the peer is not already a participant of the game, send an accept command
        game = self.gamecast_db.getGames(game_id = invite['game_id'], owner_id = 0)[0]
        players = game['players']
        if sender_permid in players.keys():
            self.debug('ignoring %s from %s because the player already joined the game' % (CMD_PLAY, showPermid(sender_permid)))
            return True
        self.messageSend([sender_permid], CMD_ACCEPT, invite)
        return True

    def gotAccept(self, msg, sender_permid):
        # Check to make sure that the sender peer is allowed to accept the invite.
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        # Do some checks and mark the receipt of the accept.
        invite = self.getInvite(owner_permid, cmd_dict['invite_id'])
        if not invite:
            self.debug('ignoring %s from %s due to unknown invite' % (CMD_ACCEPT, showPermid(sender_permid)))
            return True
        # If the invite has expired, ignore the message.
        elif time() > invite['creation_time']+INV_EXPIRE_TIME:
            self.debug('ignoring %s from %s due to expired invite' % (CMD_ACCEPT, showPermid(sender_permid)))
            return True
        # Mark the receipt of the message.
        self.setInviteState(invite, 'recv', CMD_ACCEPT)
        # For dealing with a personal invite.
        if invite['target_id'] > 0:
            # Ensure that we did actually send a match command.
            if not self.getInviteState(invite, 'send', CMD_MATCH):
                self.debug('ignoring %s from %s because we are not expecting one' % (CMD_ACCEPT, showPermid(sender_permid)))
                return True
            # If a player is somehow responding to a match without being the one that is invited, ignore the message.
            elif sender_permid != self.peers[invite['target_id']].get('permid', None):
                self.debug('ignoring %s from %s (not allowed in the original %s)' % (CMD_ACCEPT, showPermid(sender_permid), CMD_MATCH))
                return True
            # Add the player to the game.
            self.addPlayerToGame(invite, sender_permid)
        # For dealing with a random invite.
        else:
            # Are we the owner of the invite?
            if owner_permid != sender_permid:
                self.debug('sender is not the owner of the invite denoted in %s from %s' % (CMD_ACCEPT, showPermid(sender_permid)))
                return True
            # Ensure that we did actually send a play command.
            elif not self.getInviteState(invite, 'send', CMD_PLAY):
                self.debug('ignoring %s from %s because we are not expecting one' % (CMD_ACCEPT, showPermid(sender_permid)))
                return True
        return True

    def gotDecline(self, msg, sender_permid):
        # Check to make sure that the sender peer is allowed to decline the invite
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        if owner_permid != sender_permid:
            self.debug('sender is not the owner of the invite denoted in %s from %s' % (CMD_DECLINE, showPermid(sender_permid)))
            return True
        # Do some checks and mark the receipt of the decline
        invite = self.getInvite(owner_permid, cmd_dict['invite_id'])
        if not invite:
            self.debug('ignoring %s from %s due to unknown invite' % (CMD_DECLINE, showPermid(sender_permid)))
            return True
        elif invite['target_id'] >= 0:
            self.debug('ignoring %s from %s because the invite is personal' % (CMD_DECLINE, showPermid(sender_permid)))
            return True
        elif not self.getInviteState(invite, 'send', CMD_PLAY):
            self.debug('ignoring %s from %s because we are not expecting one' % (CMD_DECLINE, showPermid(sender_permid)))
            return True
        elif time() > invite['creation_time']+INV_EXPIRE_TIME:
            self.debug('ignoring %s from %s due to expired invite' % (CMD_DECLINE, showPermid(sender_permid)))
            return True
        self.setInviteState(invite, 'recv', CMD_DECLINE)
        return True

    def gotUnseek(self, msg, sender_permid):
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        # Do some checks and mark the receipt of the unseek
        invite = self.getInvite(owner_permid, cmd_dict['invite_id'])
        if not invite:
            self.debug('ignoring %s from %s due to unknown invite' % (CMD_UNSEEK, showPermid(sender_permid)))
            return True
        elif invite['target_id'] > 0:
            self.debug('ignoring %s from %s because the invite is personal' % (CMD_UNSEEK, showPermid(sender_permid)))
            return True
        elif self.getInviteState(invite, 'recv', CMD_ACCEPT):
            self.debug('ignoring %s from %s because we already received an accept' % (CMD_UNSEEK, showPermid(sender_permid)))
        elif not self.getInviteState(invite, 'recv', CMD_SEEK):
            self.debug('ignoring %s from %s because we are not expecting one' % (CMD_UNSEEK, showPermid(sender_permid)))
            return True
        elif time() > invite['creation_time']+INV_EXPIRE_TIME:
            self.debug('ignoring %s from %s due to expired invite' % (CMD_UNSEEK, showPermid(sender_permid)))
            return True
        elif self.getInviteState(invite, 'recv', CMD_UNSEEK):
            self.debug('ignoring %s from %s because invite is already marked invalid ' % (CMD_UNSEEK, showPermid(sender_permid)))
            #return True
        else:
            self.setInviteState(invite, 'recv', CMD_UNSEEK)
            self.debug('invite marked invalid due to %s from %s' % (CMD_UNSEEK, showPermid(sender_permid)))
        # If necessary, forward the message
        if msg['hops'] > 1:
            targets = self.gamecast_gossip.connected_game_buddies + self.gamecast_gossip.connected_random_peers
            try:
                targets.remove(sender_permid)
            except:
                pass
            self.debug('trying to forward %s to %d connected peers' % (CMD_UNSEEK, len(targets)))
            self.messageSend(targets, CMD_UNSEEK, invite, msg['hops']-1, msg['signature'])
        return True

    def gotStart(self, msg, sender_permid):
        # Check to make sure that the sender peer is allowed to execute the start
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        if owner_permid != sender_permid:
            self.debug('sender is not the owner of the invite denoted in %s from %s' % (CMD_START, showPermid(sender_permid)))
            return True
        # If we don't already have this game, import it to the cache & db
        game = self.getGame(owner_permid, cmd_dict['game_id'])
        if not game:
            # First, check if there is a corresponding invite with the correct status
            invite = None
            for i in self.invites:
                if i['game_id'] == cmd_dict['game_id'] and self.getPeer(i['owner_id'])['permid'] == owner_permid:
                    if i['target_id'] < 0 and self.getInviteState(i, 'recv', CMD_ACCEPT):
                        invite = i
                    elif i['target_id'] >= 0 and self.getInviteState(i, 'send', CMD_ACCEPT):
                        invite = i
            if not invite:
                self.debug('never received an invite related to %s from %s' % (CMD_START, showPermid(sender_permid)))
                return True
            elif invite and time() > (invite['creation_time']+INV_EXPIRE_TIME):
                self.debug('expired invite related to %s from %s' % (CMD_START, showPermid(sender_permid)))
                return True
            # Next, import the game
            game = {}
            game['game_id']       = cmd_dict['game_id']
            game['owner_id']      = self.getPeerID(owner_permid)
            game['winner_permid'] = ''
            game['moves']         = []
            game['players']       = dict((str2bin(key), value) for (key, value) in bdecode(cmd_dict['players']).items())
            game['gamename']      = invite['gamename']
            game['time']          = invite['time']
            game['inc']           = invite['inc']
            game['is_finished']   = 0
            game['lastmove_time'] = 0
            game['creation_time'] = time()
            self.games.append(game)
            gamerec = deepcopy(game)
            gamerec['winner_permid'] = bin2str(game['winner_permid'])
            gamerec['players'] = bencode(dict((bin2str(key), value) for (key, value) in game['players'].items()))
            gamerec['moves'] = bencode(game['moves'])
            self.gamecast_db.addGame(gamerec)
            self.debug('new game found in %s from %s' % (CMD_START, showPermid(sender_permid)))
        else:
            self.debug('duplicate game found in %s from %s' % (CMD_START, showPermid(sender_permid)))
        return True

    def gotMove(self, msg, sender_permid):
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        # If we dont know about this game or we are not a participant in it, ignore the message
        game = self.getGame(owner_permid, cmd_dict['game_id'])
        if not game:
            self.debug('unknown game referenced by %s from %s' % (CMD_MOVE, showPermid(sender_permid)))
            return True
        players = game['players']
        if not sender_permid in players.keys():
            self.debug('ignoring %s from %s because the player never joined the game' % (CMD_MOVE, showPermid(sender_permid)))
            return True
        # Does this message have the moveno we are expecting?
        if cmd_dict['moveno'] != len(game['moves'])+1:
            self.debug('ignoring %s from %s because of an unexpected moveno' % (CMD_MOVE, showPermid(sender_permid)))
            return True
        # If the game has finished, ignore the message
        if game['is_finished']:
            self.debug('ignoring %s from %s because the game has finished' % (CMD_MOVE, showPermid(sender_permid)))
            return True
        # If the game has expired, ignore the message
        clock = self.getGameExpireClock(game)
        if clock < 0:
            self.debug('ignoring %s from %s because the game has expired' % (CMD_MOVE, showPermid(sender_permid)))
            return True
        # Is it this peer's turn?
        if self.getGameTurn(game) != players[sender_permid]:
            self.debug('ignoring %s from %s its not this player\'s turn' % (CMD_MOVE, showPermid(sender_permid)))
            return True
        # Is this move correct?
        move = cmd_dict['move']
        state = self.getGameState(game, move)
        if state < 0:
            self.debug('ignoring %s from %s because of an illegal move %s' % (CMD_MOVE, showPermid(sender_permid), move))
            return True
        # Is this move a closing move?
        if state > 0:
            self.debug('accepted closing %s from %s (%s)' % (CMD_MOVE, showPermid(sender_permid), move))
            game['is_finished'] = CLOSE_MOVE
            game['finished_time'] = time()
            game['winner_permid'] = self.getGameWinner(game, move)
        else:
            self.debug('accepted %s from %s (%s)' % (CMD_MOVE, showPermid(sender_permid), move))
        if game['is_finished']:
            self.gamecast_gossip.data_handler.updateInteractionFactors()
            self.log('GAMEDONE', is_finished=game['is_finished'], time_left=self.getGameClocks(game), \
                     game='(%d,%s)' % (game['owner_id'], game['game_id']), owner_permid=bin2str(self.getPeer(game['owner_id'])['permid']), my_colour=game['players'][self.mypermid])
        # Add the move to the game
        correction = 0
        correction_needed = 0
        if cmd_dict['moveno'] > 2:
            time_taken1 = cmd_dict['time_taken']
            time_taken2 = time() - (game['lastmove_time'] if game['lastmove_time'] else game['creation_time'])
            time_taken2 = int(time_taken2*1000)
            correction_needed = (time_taken2 - time_taken1)
            correction = min((time_taken2 - time_taken1), MAX_CORRECTION)
        self.addMoveToGame(sender_permid, move, game, correction)
        dns = self.dnsindb(sender_permid)
        if dns:
            ip,port = dns
            self.log('CLOCKCOR', ip, port, sender_permid, moveno=cmd_dict['moveno'], correction_given=correction, correction_needed=correction_needed, \
                      game='(%d,%s)' % (game['owner_id'], game['game_id']), owner_permid=bin2str(self.getPeer(game['owner_id'])['permid']))
        return True

    def gotAbortOrDraw(self, msg, sender_permid):
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        # If we dont know about this game or we are not a participant in it, ignore the message
        game = self.getGame(owner_permid, cmd_dict['game_id'])
        if not game:
            self.debug('unknown game referenced by %s from %s' % (cmd_dict['cmd'], showPermid(sender_permid)))
            return True
        players = game['players']
        if not sender_permid in players.keys():
            self.debug('ignoring %s from %s because the player never joined the game' % (cmd_dict['cmd'], showPermid(sender_permid)))
            return True
        last_moveno_sender = 0
        colour_sender = game['players'][sender_permid]
        for i, (col, mov, ctr) in enumerate(reversed(game['moves'])):
            if col == colour_sender:
                last_moveno_sender = len(game['moves']) - i
                break
        if cmd_dict['moveno'] < last_moveno_sender or cmd_dict['moveno'] >= len(game['moves'])+len(game['players']):
            self.debug('ignoring %s from %s because due to incorrect moveno' % (cmd_dict['cmd'], showPermid(sender_permid)))
            return True
        self.addRequestToGame(sender_permid, cmd_dict['cmd'], cmd_dict['moveno'], game)
        return True

    def gotResign(self, msg, sender_permid):
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        # If we dont know about this game or we are not a participant in it, ignore the message
        game = self.getGame(owner_permid, cmd_dict['game_id'])
        if not game:
            self.debug('unknown game referenced by %s from %s' % (CMD_MOVE, showPermid(sender_permid)))
            return True
        players = game['players']
        if not sender_permid in players.keys():
            self.debug('ignoring %s from %s because the player never joined the game' % (CMD_MOVE, showPermid(sender_permid)))
            return True
        game['winner_permid'] = self.mypermid
        game['is_finished'] = RESIGN
        game['finished_time'] = time()
        self.setGame(game)
        self.gamecast_gossip.data_handler.updateInteractionFactors()
        self.log('GAMEDONE', is_finished=game['is_finished'], time_left=self.getGameClocks(game), \
                 game='(%d,%s)' % (game['owner_id'], game['game_id']), owner_permid=bin2str(self.getPeer(game['owner_id'])['permid']), my_colour=game['players'][self.mypermid])
        # Update the ratings (works only for 2-player games)
        self.updateRating(game['gamename'])
        return True

    def gotDiscuss(self, msg, sender_permid):
        owner_permid = msg['owner']['permid']
        cmd_dict = msg['cmd_dict']
        # Check to make sure that we are the owner of the game
        if owner_permid != self.mypermid:
            self.debug('we are not the owner of the invite denoted in %s from %s' % (CMD_DISCUSS, showPermid(sender_permid)))
            return True
        # Check if the game exists in our db
        game = self.getGame(owner_permid, cmd_dict['game_id'])
        if not game:
            self.debug('unknown game referenced by %s from %s' % (CMD_DISCUSS, showPermid(sender_permid)))
            return True
        # Add the message to the database
        messagerec = {}
        messagerec['message_id']    = cmd_dict['message_id']
        messagerec['owner_id']      = self.getPeerID(sender_permid)
        messagerec['game_id']       = cmd_dict['game_id']
        messagerec['game_owner_id'] = 0
        messagerec['content']       = cmd_dict['content']
        messagerec['creation_time'] = time()
        self.gamecast_db.addMessage(messagerec)
        return True

    def retryOutgoing(self):
        now = int(time())
        reconnects = []
        # If there are messages in messages_out that need to be send for a certain peer, append it to reconnects
        for permid, queue in self.messages_out.items():
            for message in queue:
                msg_type, msg_payload, exp_time = message
                # Schedule messages that are already waiting for some time, but are not yet expired, for sending.
                if (now - exp_time) > MSG_EXPIRE_TIME/2:
                    if not permid in reconnects:
                        reconnects.append(permid)

        # Do the actual connecting
        for permid in reconnects:
            self.debug('attempting to reconnect to', showPermid(permid))
            self.overlay_bridge.connect(permid, self.messageConnectCallback, gamecast=True)
        self.overlay_bridge.add_task(self.retryOutgoing, RETRY_OUTGOING_TIME, gamecast=True)

    def retryIncoming(self):
        # Check if there are buffered messages that can be delivered
        for owner_permid, queues in self.messages_in.iteritems():
            for game_id, queue in queues.iteritems():
                # Retrieve the game
                if not queue:
                    continue
                game = self.getGame(owner_permid, game_id)
                now = int(time())
                index = 0
                movenos = []
                # Create a list with moveno's and their corresponding indices within the queue
                while index < len(queue):
                    msg = queue[index]
                    sender_permid, msg_payload, exp_time = msg
                    if now < exp_time:
                        # Add the moveno to the list
                        cmd_dict = msg_payload['cmd_dict']
                        movenos.append((cmd_dict.get('moveno', 0), index))
                        index += 1
                    else:
                        # Drop expired messages
                        queue.pop(index)
                movenos.sort()
                if not game:
                    invites = self.gamecast_db.getInvites(game_id = game_id, owner_id = owner_permid)
                    invite = invites[0]
                    if invite['target_id'] < 0 and not self.getInviteState(invite, 'recv', CMD_ACCEPT):
                        next_moveno = -1
                    else:
                        next_moveno = 0
                else:
                    next_moveno = len(game['moves'])+1
                removed = []
                # Remove messages blocking the queue
                while movenos and movenos[0][0] < next_moveno:
                    movenos.pop(0)
                # Deliver messages that can be delivered
                while movenos and movenos[0][0] == next_moveno:
                    index = movenos[0][1]
                    sender_permid, msg_payload, exp_time = queue[index]
                    # Log the successful delivery of the message
                    dns = self.dnsindb(sender_permid)
                    if dns:
                        ip,port = dns
                        self.log('RECV_MSG', ip, port, sender_permid, msg_type=self.showMessage(msg_payload), payload=msg_payload)
                    self.debug('received %s from' % self.showMessage(msg_payload), showPermid(sender_permid))
                    # Process the message
                    self.processMessage(GC_CMD, msg_payload, sender_permid)
                    movenos.pop(0)
                    next_moveno += 1
                    removed.append(index)
                self.messages_in[owner_permid][game_id] = [value for index, value in enumerate(queue) if index not in removed]
        self.overlay_bridge.add_task(self.retryIncoming, RETRY_INCOMING_TIME, gamecast=True)

    def postponeDelivery(self, permid, payload):
        cmd_dict = payload.get('cmd_dict', None)

        if cmd_dict and cmd_dict['cmd'] == CMD_START:
            invites = self.gamecast_db.getInvites(game_id = cmd_dict['game_id'], owner_id = self.getPeerID(payload['owner']['permid']))
            if invites:
                invite = invites[0]
                # Buffer start commands that are not ready to be processed yet
                if invite['target_id'] < 0 and not self.getInviteState(invite, 'recv', CMD_ACCEPT):
                    queue = self.messages_in.get(owner_permid, {}).get(game_id, [])
                    # Prevent a single (malicious) peer from overloading the queue (by allowing only one start command per peer per game)
                    for msg in queue:
                        sender_permid, msg_payload, exp_time = msg
                        if sender_permid == permid and msg_payload['cmd_dict']['cmd'] == CMD_START:
                            return False
                    # Buffer the message
                    self.debug('buffering incoming %s message from %s' % (CMD_START, showPermid(permid)))
                    queue.append((permid, payload, int(time())+MSG_POSTPONE_TIME))
                    if not self.messages_in.has_key(owner_permid):
                        self.messages_in[owner_permid] = {}
                    self.messages_in[owner_permid][game_id] = queue
                    return True
        elif cmd_dict and cmd_dict['cmd'] == CMD_MOVE:
            # If we dont know about this game, do not postpone delivery
            owner_permid = payload['owner']['permid']
            game_id = cmd_dict['game_id']
            game = self.getGame(owner_permid, game_id)
            if not game:
                return False
            # If the sender is not a participant in the game, dont postpone
            if not permid in game['players'].keys():
                return False
            # Buffer messages with future moveno's
            moveno = cmd_dict['moveno']
            next_moveno = len(game['moves'])+1
            if moveno > next_moveno:
                queue = self.messages_in.get(owner_permid, {}).get(game_id, [])
                # Prevent a single (malicious) peer from overloading the queue (by allowing only one move command per peer per game)
                for msg in queue:
                    sender_permid, msg_payload, exp_time = msg
                    if sender_permid == permid and msg_payload['cmd_dict']['cmd'] == CMD_MOVE:
                        return False
                # Buffer the message
                self.debug('buffering incoming %s message from %s' % (CMD_MOVE, showPermid(permid)))
                queue.append((permid, payload, int(time())+MSG_POSTPONE_TIME))
                if not self.messages_in.has_key(owner_permid):
                    self.messages_in[owner_permid] = {}
                self.messages_in[owner_permid][game_id] = queue
                return True
        return False

    def validCommandMessage(self, message):
        # Check if the message has the correct type
        if type(message) != dict:
            self.debug('message payload does not contain dict')
            return False
        # Check if the message contains the corrent fields
        req_fields = set(('owner', 'cmd'))
        opt_fields = set(('hops', 'signature'))
        msg_fields = set(message.keys())
        if not msg_fields >= req_fields:
            self.debug('message does not contain the required fields')
            return False
        if (msg_fields - req_fields) - opt_fields:
            self.debug('message contains invalid fields')
            return False
        # Check for owner
        if not self.validPeer(message['owner']):
            self.debug('message contains invalid owner peer')
            return False
        # Check for command
        cmd_dict = self.validCommand(message['cmd'])
        if not cmd_dict:
            self.debug('message contains invalid command')
            return False
        # Messages with certain commands require a signature
        cmd_type = cmd_dict['cmd']
        if not message.has_key('signature') and cmd_type in (CMD_SEEK, CMD_UNSEEK):
            self.debug('%s does not include required signature field' % self.showMessage(message))
            return False
        # If a signature is available, check its correctness
        if message.has_key('signature'):
            blob = message['signature']
            permid = message['owner']['permid']
            cmd = message['cmd']
            part_cmd = cmd.rpartition(' ')[0]
            if not verify_data(bencode(part_cmd), permid, blob):
                self.debug('invalid signature detected for %s' % self.showMessage(message))
                return False
            else:
                pass
                #self.debug('valid signature detected for %s' % self.showMessage(message))
        return True

    def validCommand(self, cmd):
        result = None
        match  = re.search(REG_SEEK, cmd)
        if match:
            result = match.groupdict()
            for arg in ('time', 'inc', 'min_rating', 'max_rating', 'invite_id', 'game_id'):
                result[arg] = int(result[arg])
            if result['age']:
                result['age'] = int(result['age'])
            else:
                result.pop('age')
        match  = re.search(REG_MATCH, cmd)
        if match:
            result = match.groupdict()
            for arg in ('time', 'inc', 'invite_id', 'game_id'):
                result[arg] = int(result[arg])
        match  = re.search(REG_PLAY, cmd)
        if match:
            result = match.groupdict()
            for arg in ('invite_id', ):
                result[arg] = int(result[arg])
        match  = re.search(REG_UNSEEK, cmd)
        if match:
            result = match.groupdict()
            for arg in ('invite_id', ):
                result[arg] = int(result[arg])
        match  = re.search(REG_ACCEPT, cmd)
        if match:
            result = match.groupdict()
            for arg in ('invite_id', ):
                result[arg] = int(result[arg])
        match  = re.search(REG_DECLINE, cmd)
        if match:
            result = match.groupdict()
            for arg in ('invite_id', ):
                result[arg] = int(result[arg])
        match  = re.search(REG_START, cmd)
        if match:
            result = match.groupdict()
            for arg in ('game_id', ):
                result[arg] = int(result[arg])
        match  = re.search(REG_MOVE, cmd)
        if match:
            result = match.groupdict()
            result['cmd'] = 'move'
            for arg in ('moveno', 'time_taken', 'game_id'):
                result[arg] = int(result[arg])
        match  = re.search(REG_ABORT, cmd)
        if match:
            result = match.groupdict()
            for arg in ('moveno', 'game_id'):
                result[arg] = int(result[arg])
        match  = re.search(REG_DRAW, cmd)
        if match:
            result = match.groupdict()
            for arg in ('moveno', 'game_id'):
                result[arg] = int(result[arg])
        match  = re.search(REG_RESIGN, cmd)
        if match:
            result = match.groupdict()
            for arg in ('moveno', 'game_id'):
                result[arg] = int(result[arg])
        match  = re.search(REG_DISCUSS, cmd)
        if match:
            result = match.groupdict()
            for arg in ('game_id', 'message_id'):
                result[arg] = int(result[arg])
        return result

    def validPeer(self, p):
        if (p.has_key('ip') and p.has_key('port') and p.has_key('permid')
            and validPermid(p['permid']) and validIP(p['ip'])and validPort(p['port'])):
            return True
        return False

    def getGameTurn(self, game):
        if game['gamename'] == 'chess':
            if (len(game['moves']) % 2) == 0:
                return "white"
            else:
                return "black"
        else:
            return None

    def getGameState(self, game, nextmove):
        moves = [move for colour, move, clock in game['moves']] + [nextmove]

        if game['gamename'] == 'chess':
            from Tribler.Core.GameCast.ChessBoard import ChessBoard
            cb = ChessBoard()
            for move in moves:
                retval = cb.addTextMove(move)
                if not retval and cb.getReason() == cb.MUST_SET_PROMOTION:
                    cb.setPromotion(cb.QUEEN)
                    retval = cb.addTextMove(move)
                if not retval:
                    return -1
            return int(cb.isGameOver())
        return -1

    def getGameWinner(self, game, nextmove):
        moves = [move for colour, move, clock in game['moves']] + [nextmove]
        players = game['players']

        if game['gamename'] == 'chess':
            from Tribler.Core.GameCast.ChessBoard import ChessBoard
            cb = ChessBoard()
            for move in moves:
                retval = cb.addTextMove(move)
                if not retval and cb.getReason() == cb.MUST_SET_PROMOTION:
                    cb.setPromotion(cb.QUEEN)
                    retval = cb.addTextMove(move)
            gr = cb.getGameResult()
            players_rev = dict((value,key) for (key,value) in players.items())
            if gr == ChessBoard.WHITE_WIN:
                return players_rev['white']
            elif gr == ChessBoard.BLACK_WIN:
                return players_rev['black']
        return ''

    def getGameClock(self, colour, game):
        clock = 0
        counter = 0
        num_players = len(game['players'])
        if self.getGameTurn(game) == colour and len(game['moves']) >= num_players:
            # Calculate the amount of time that has pasted since the last move
            if not game['is_finished']:
                counter = time() - game['lastmove_time']
                counter = int(counter*1000)
            else:
                counter += int((game['finished_time'] - game['lastmove_time'])*1000)
        # Add the time taken by moves from previous rounds
        counter_previous = 0
        for col, mov, ctr in reversed(game['moves']):
            if col == colour:
                counter_previous = ctr
                break
        counter += counter_previous
        # Calculate the remaining time
        num_moves = len([mov for col, mov, ctr in game['moves'] if col == colour])
        num_moves = num_moves-1 if num_moves > 0 else 0
        clock = 60*game['time'] + num_moves*game['inc'] - int(counter/1000.0)
        return clock

    def getGameClocks(self, game):
        clocks = ''
        iterator = iter(sorted(game['players'].iteritems()))
        for permid, colour in iterator:
            clocks += '%s%d / ' % (colour[0], self.getGameClock(colour, game))
        clocks = clocks[:-3]
        return clocks

    def getGameExpireClock(self, game):
        if len(game['moves']) > 1:
            return self.getGameClock(self.getGameTurn(game), game)
        else:
            return int(game['creation_time'] + GMS_EXPIRE_TIME - time())

    def shutdown(self):
        # Called by OverlayThread
        self.memoryHandler.close()
        self.saveMessageQueue()

    def saveMessageQueue(self):
        statedir = self.session.get_state_dir()
        filename = os.path.join(statedir, 'gamecast-msgs.pickle')
        messages = deepcopy(self.messages_out)
        for permid, queue in self.messages_out_sending.iteritems():
            if messages.has_key(permid):
                messages[permid] = messages[permid] + queue
            else:
                messages[permid] = queue
        try:
            file = open(filename, 'wb')
            cPickle.dump(messages, file)
            cPickle.dump(self.messages_in, file)
            file.close()
        except:
            self.debug('unable to save messages')

    def loadMessageQueue(self):
        statedir = self.session.get_state_dir()
        filename = os.path.join(statedir, 'gamecast-msgs.pickle')
        try:
            file = open(filename, 'rb')
            self.messages_out = cPickle.load(file)
            self.messages_in = cPickle.load(file)
            file.close()
        except:
            self.debug('unable to load messages')

        # Remove expired messages
        now = int(time())
        for permid, queue in self.messages_out.items():
            index = 0
            while index < len(queue):
                msg_type, msg_payload, exp_time = queue[index]
                if now > exp_time:
                    queue.pop(index)
                else:
                    index += 1

    def showMessage(self, message):
        if message.has_key('cmd_dict'):
            return 'GC_CMD (%s)' % message['cmd_dict']['cmd']
        elif message.has_key('cmd'):
            return 'GC_CMD (%s)' % self.showCommand(message['cmd'])
        return ''

    def showCommand(self, cmd):
        cmd_dict = self.validCommand(cmd)
        return (cmd_dict['cmd'] if cmd_dict else None)

    def showIP(self, ip):
        return "%3s.%3s.%3s.%3s" % tuple(ip.split("."))

    def showPort(self, port):
        return "%5s" % port

    def debug(self, *args):
        if not DEBUG:
            return
        buf = "gc: "
        buf += " ".join(map(str, args))
        print >> sys.stderr, buf
        sys.stderr.flush()

    def log(self, *args, **kwargs):
        # Local & remote logging..
        if self.logger and args:
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
            self.logger.info(msg, extra=d)

