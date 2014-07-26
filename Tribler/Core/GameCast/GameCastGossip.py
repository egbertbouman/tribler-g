# Written by Egbert Bouman. Based on buddycast.py.
# see LICENSE for license information

import gc
import os
import sys
import copy
import logging

from sets import Set
from array import array
from copy import deepcopy
from traceback import print_exc
from random import sample, randint
from threading import currentThread
from time import time, gmtime, strftime
from logging.handlers import SocketHandler, DEFAULT_TCP_LOGGING_PORT

from Tribler.Core.Utilities.unicode import dunno2unicode
from Tribler.Core.BitTornado.bencode import bencode, bdecode
from Tribler.Core.BitTornado.BT1.MessageID import GC_GOSSIP, GC_ALIVE
from Tribler.Core.simpledefs import NTFY_ACT_MEET, NTFY_ACT_RECOMMEND, NTFY_MYPREFERENCES, NTFY_INSERT, NTFY_DELETE
from Tribler.Core.Utilities.utilities import show_permid_short, show_permid, validPermid, validIP, validPort, hostname_or_ip2ip
from Tribler.Core.Overlay.SecureOverlay import OLPROTO_VER_SEVENTEETH
from Tribler.Core.NATFirewall.DialbackMsgHandler import DialbackMsgHandler
from Tribler.Core.CacheDB.sqlitecachedb import bin2str, str2bin
from Tribler.Core.Statistics.Logger import Logger

DEBUG      = True  # For status info
SHOW_ERROR = True  # For errors/warnings
INFO       = 0
WARNING    = 1
ERROR      = 2

MAX_GOSSIP_LENGTH = 100*1024

def now():
    return int(time())

def ctime(t):
    return strftime("%Y-%m-%d.%H:%M:%S", gmtime(t))

class GameCastGossip:
    __single = None

    def getInstance(*args, **kw):
        if GameCastGossip.__single is None:
            GameCastGossip(*args, **kw)
        return GameCastGossip.__single
    getInstance = staticmethod(getInstance)

    def __init__(self):
        if GameCastGossip.__single:
            raise RuntimeError, "GameCastGossip is singleton"
        GameCastGossip.__single = self

        # --- parameters ---
        self.block_interval = 4*60*60                    # block interval for a peer to gossip to
        self.short_block_interval = 4*60*60              # block interval if failed to connect the peer

        # for testing
        self.block_interval = 5*60                    # block interval for a peer to gossip to
        self.short_block_interval = 5*60              # block interval if failed to connect the peer

        self.num_gbs = 10                                # num of game buddies in gossip msg
        self.num_rps = 10                                # num of random peers in gossip msg
        self.num_gms = 50                                # num of games in gossip msg
        self.num_msg = 50                                # num if messages in gossip msg
        self.max_conn_cand = 100                         # max number of connection candidates
        self.max_conn_gb = 10                            # max number of connectable game buddies
        self.max_conn_rp = 10                            # max number of connectable random peers
        self.max_conn_up = 10                            # max number of unconnectable peers
        self.bootstrap_num = 10                          # max number of peers to fill when bootstrapping
        self.bootstrap_interval = 5*60                   # 5 min
        self.network_delay = 30                          # 30 seconds
        self.check_period = 120                          # how many seconds to send keep alive message and check updates

        # --- memory ---
        self.send_block_list = {}                        # {permid: unlock_time}
        self.recv_block_list = {}                        # {permid: unlock_time}
        self.connections = {}                            # {permid: overlay_version}
        self.connections_unverified = {}                 # {permid: (selversion, locally_initiated)}
        self.connected_game_buddies = []                 # [permid]
        self.connected_random_peers = []                 # [permid]
        self.connected_connectable_peers = {}            # {permid: {'connect_time', 'ip', 'port', 'name', 'oversion'}}
        self.connected_unconnectable_peers = {}          # {permid: connect_time}
        self.connection_candidates = {}                  # {permid: last_seen}

        # --- stats ---
        self.target_type = 0
        self.next_initiate = 0
        self.round = 0                                   # every call to work() is a round
        self.bootstrapped = False                        # bootstrap once every 1 hours
        self.bootstrap_time = 0                          # number of times to bootstrap
        self.total_bootstrapped_time = 0
        self.last_bootstrapped = now()                   # bootstrap time of the last time
        self.start_time = now()
        self.last_check_time = 0

    def register(self, overlay_bridge, launchmany, config):
        self.overlay_bridge = overlay_bridge
        self.launchmany = launchmany
        self.session = launchmany.session
        self.config = config
        self.data_handler = DataHandler(self.launchmany, self.overlay_bridge, max_num_peers=self.config['buddycast_max_peers'])
        self.dialback = DialbackMsgHandler.getInstance()

        # --- properties of this peer ---
        self.ip = self.data_handler.getMyIp()
        self.port = self.data_handler.getMyPort()
        self.permid = self.data_handler.getMyPermid()
        self.nameutf8 = self.data_handler.getMyName().encode("UTF-8")

        # -- logging related ---
        self.dnsindb = launchmany.secure_overlay.get_dns_from_peerdb
        self.logger = logging.getLogger('gamecastgossip')
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(created).3f   %(event_type)-8s   %(tf_peer_short)-34s   %(message)s')
        fileHandler = logging.FileHandler(os.path.join(self.session.get_state_dir(), 'gamecastgossip.log'))
        fileHandler.setFormatter(formatter)
        self.memoryHandler = logging.handlers.MemoryHandler(100, flushLevel=logging.DEBUG, target=fileHandler)
        self.logger.addHandler(self.memoryHandler)

        #socketHandler = SocketHandler('gamecast.no-ip.org', DEFAULT_TCP_LOGGING_PORT)
        #self.logger.addHandler(socketHandler)

        # -- startup --
        self.overlay_bridge.add_task(self.doGossip, 3)
        self.overlay_bridge.add_task(self.data_handler.postInit, 0)
        print >> sys.stderr, "gcg: starting up"

    def getCurrrentInterval(self):
        past = now() - self.start_time
        if past > 60*60:
            interval = 30
        else:
            interval = 10
        return interval

    def doGossip(self):
        self.overlay_bridge.add_task(self.doGossip, self.getCurrrentInterval())
        if DEBUG:
            print >> sys.stderr, ""
        self.debug('Active', "doGossip", currentThread().getName())
        try:
            self.round += 1
            self.print_round()
            nPeer, nCc, nBs, nBr, nCo, nCg, nCr, nCu = self.get_stats()
            self.log('GC_STATE', Round=self.round, nPeer=nPeer, nCc=nCc, nBs=nBs, nBr=nBr, nCo=nCo, nCg=nCg, nCr=nCr, nCu=nCu)
            self.debug('Active', "check blocked peers: Round", self.round)

            self.updateSendBlockList()

            _now = now()
            if _now - self.last_check_time >= self.check_period:
                self.debug('Active', "keep connections with peers: Round", self.round)
                self.keepConnections()
                gc.collect()
                self.last_check_time = _now

            if self.next_initiate > 0:
                # It replied some meesages in the last rounds, so it doesn't initiate GameCastGossip
                self.debug('Active', "idle loop:", self.next_initiate)
                self.next_initiate -= 1
            else:
                if len(self.connection_candidates) == 0:
                    self.booted = self._bootstrap(self.bootstrap_num)
                    self.print_bootstrap()
                # It didn't reply any message in the last rounds, so it can initiate GameCastGossip
                if len(self.connection_candidates) > 0:
                    r, target_permid = self.selectTarget()
                    self.print_target(target_permid, r=r)
                    self.startGossip(target_permid)
        except:
            print_exc()

    def get_peer_info(self, target_permid, include_permid=True):
        if not target_permid:
            return ' None '
        dns = self.dnsindb(target_permid)
        if not dns:
            return ' None '
        try:
            ip = dns[0]
            port = dns[1]
            ifactor = self.data_handler.getInteractionFactor(target_permid)
            if include_permid:
                s_pid = show_permid_short(target_permid)
                return ' %s %s:%s %.3f ' % (s_pid, ip, port, ifactor)
            else:
                return ' %s:%s %.3f' % (ip, port, ifactor)
        except:
            return ' ' + repr(dns) + ' '

    def _bootstrap(self, number):
        _now = now()
        # Bootstrapped recently, so wait for a while
        if self.bootstrapped and _now - self.last_bootstrapped < self.bootstrap_interval:
            self.bootstrap_time = 0      # Let it read the most recent peers next time
            return -1

        send_block_list_ids = []
        for permid in self.send_block_list:
            peer_id = self.data_handler.getPeerID(permid)
            send_block_list_ids.append(peer_id)
        target_cands_ids = Set(self.data_handler.peers) - Set(send_block_list_ids)
        recent_peers_ids = self.selectRecentPeers(target_cands_ids, number, startfrom=self.bootstrap_time*number)
        for peer_id in recent_peers_ids:
            last_seen = self.data_handler.getPeerIDLastSeen(peer_id)
            self.addConnCandidate(self.data_handler.getPeerPermid(peer_id), last_seen)
        self.limitConnCandidate()

        self.bootstrap_time += 1
        self.total_bootstrapped_time += 1
        self.last_bootstrapped = _now
        if len(self.connection_candidates) < self.bootstrap_num:
            self.bootstrapped = True     # Don't reboot until self.bootstrap_interval later
        else:
            self.bootstrapped = False    # Reset it to allow read more peers if needed
        return 1

    def selectRecentPeers(self, cand_ids, number, startfrom=0):
        if not cand_ids: return []

        peerids = []
        last_seens = []
        for peer_id in cand_ids:
            peerids.append(peer_id)
            last_seens.append(self.data_handler.getPeerIDLastSeen(peer_id))
        npeers = len(peerids)
        if npeers == 0: return []
        aux = zip(last_seens, peerids)
        aux.sort()
        aux.reverse()

        # Roll back when startfrom is bigger than npeers
        peers = []
        startfrom = startfrom % npeers
        endat = startfrom + number
        for _, peerid in aux[startfrom:endat]:
            peers.append(peerid)
        return peers

    def addConnCandidate(self, peer_permid, last_seen):
        if self.isBlocked(peer_permid, self.send_block_list) or peer_permid == self.permid:
            return
        self.connection_candidates[peer_permid] = last_seen

    def limitConnCandidate(self):
        if len(self.connection_candidates) > self.max_conn_cand:
            tmp_list = zip(self.connection_candidates.values(),self.connection_candidates.keys())
            tmp_list.sort()
            while len(self.connection_candidates) > self.max_conn_cand:
                ls,peer_permid = tmp_list.pop(0)
                self.removeConnCandidate(peer_permid)

    def removeConnCandidate(self, peer_permid):
        if peer_permid in self.connection_candidates:
            self.connection_candidates.pop(peer_permid)

    def updateSendBlockList(self):
        _now = now()
        for p in self.send_block_list.keys():
            if _now >= self.send_block_list[p] - self.network_delay:
                self.debug("", "*** unblock peer in send block list" + self.get_peer_info(p) + "expiration:", ctime(self.send_block_list[p]))
                self.send_block_list.pop(p)

    def isConnected(self, peer_permid):
        return peer_permid in self.connections

    def isConnectedUnverified(self, peer_permid):
        return peer_permid in self.connections_unverified

    def keepConnections(self):
        for peer_permid in self.connections:
            if (peer_permid in self.connected_connectable_peers or peer_permid in self.connected_unconnectable_peers):
                self.overlay_bridge.send(peer_permid, GC_ALIVE, self.keepaliveSendCallback)
                self.debug("", "*** Send keep alive to peer", self.get_peer_info(peer_permid))

    def keepaliveSendCallback(self, exc, peer_permid):
        if exc is not None:
            self.debug("", "send keep alive msg", exc, self.get_peer_info(peer_permid), "Round", self.round, kind=ERROR)
            self.closeConnection(peer_permid, 'keepalive:'+Exception.__str__(exc).strip())

    def selectTarget(self):
        def selectGBTarget():
            # Select the game buddy with the highest interaction-factor
            max_ifactor = (-1, None)
            for permid in self.connection_candidates:
                peer_id = self.data_handler.getPeerID(permid)
                if peer_id:
                    ifactor = self.data_handler.getInteractionFactor(permid)
                    max_ifactor = max(max_ifactor, (ifactor, permid))
            selected_permid = max_ifactor[1]
            if selected_permid is None:
                return None
            else:
                return selected_permid

        def selectRPTarget():
            # Randomly select a random peer
            selected_permid = None
            while len(self.connection_candidates) > 0:
                selected_permid = sample(self.connection_candidates, 1)[0]
                selected_peer_id = self.data_handler.getPeerID(selected_permid)
                if selected_peer_id is None:
                    self.removeConnCandidate(selected_permid)
                    selected_permid = None
                elif selected_peer_id:
                    break
            return selected_permid

        self.target_type = 1 - self.target_type
        if self.target_type == 0:
            target_permid = selectGBTarget()
        else:
            target_permid = selectRPTarget()
        return self.target_type, target_permid

    def startGossip(self, target_permid):
        if not target_permid or target_permid == self.permid:
            return

        if not self.isBlocked(target_permid, self.send_block_list):
            self.debug('Active', "connecting a peer to start gossip", show_permid_short(target_permid))
            self.overlay_bridge.connect(target_permid, self.gossipConnectCallback)

            dns = self.dnsindb(target_permid)
            if dns:
                ip,port = dns
                self.log('CONN_TRY', ip, port, target_permid)

            # Remove it from candidates no matter if it has been connected
            self.removeConnCandidate(target_permid)
            self.debug('Active', "remove connected peer from Cc", self.get_peer_info(target_permid))
        else:
            self.debug("", 'peer', self.get_peer_info(target_permid), 'is blocked while running startGossip.', "Round", self.round)

    def gossipConnectCallback(self, exc, dns, target_permid, selversion):
        if exc is None:
            try:
                if not (self.isConnected(target_permid) or self.isConnectedUnverified(target_permid)):
                    if DEBUG: raise RuntimeError, 'gcg: not connected while calling connect callback'
                    return
                self.debug('Active', "peer is connected", self.get_peer_info(target_permid), "overlay version", selversion, currentThread().getName())
                self.gossipSend(target_permid, selversion, active=True)
            except:
                print_exc()
                self.debug("", "in connect callback", exc, dns, show_permid_short(target_permid), selversion, "Round", self.round, kind=ERROR)
        else:
            self.debug("", "connecting to", show_permid_short(target_permid),exc,dns, ctime(now()), kind=WARNING)

    def createGossipMessage(self, target_permid, selversion, target_ip=None, target_port=None):
        try:
            target_ip,target_port = self.dnsindb(target_permid)
        except:
            raise
        if not target_ip or not target_port:
            return {}

        # Get the game buddies that should be included in the message
        game_buddies = []
        for permid in self.connected_game_buddies:
            if (permid == target_permid) or (permid not in self.connected_connectable_peers):
                continue
            peer = deepcopy(self.connected_connectable_peers[permid])
            if peer['ip'] == target_ip and peer['port'] == target_port:
                continue
            peer['permid'] = permid
            peer['ip'] = str(peer['ip'])
            game_buddies.append(peer)
        # Get the random peers that should be included in the message
        random_peers = []
        for permid in self.connected_random_peers:
            if (permid == target_permid) or (permid not in self.connected_connectable_peers):
                continue
            peer = deepcopy(self.connected_connectable_peers[permid])
            if peer['ip'] == target_ip and peer['port'] == target_port:
                continue
            peer['permid'] = permid
            peer['ip'] = str(peer['ip'])
            random_peers.append(peer)
        # Get a number (maximal self.num_gms) of the most recent games (and their players & messages) from the database
        recent_games = self.data_handler.getMyRecentGames(self.num_gms)
        player_cache = {}
        for game in recent_games:
            game['players'] = dict((str2bin(key), value) for (key, value) in bdecode(game['players']).items())
            for permid, colour in game['players'].iteritems():
                game['players'][permid] = {}
                if permid != self.permid and permid not in player_cache:
                    keys = ('ip', 'port', 'name')
                    res = self.data_handler.getPeer(permid, keys)
                    peer = dict(zip(keys,res))
                    if not peer['name']:
                        peer.pop('name')
                    player_cache[permid] = peer
                if permid in player_cache:
                    game['players'][permid].update(player_cache[permid])
                game['players'][permid]['colour'] = colour
            game['players'] = bencode(dict((bin2str(key), value) for (key, value) in game['players'].items()))
            messages = self.data_handler.getMessages(game.pop('owner_id'), game['game_id'])
            if len(messages) > self.num_msg:
                messages = sample(messages, self.num_msg)
            for message in messages:
                message.pop('creation_time')
                message.pop('game_owner_id')
                owner_id = message.pop('owner_id')
                if owner_id == 0:
                    message['owner_permid'] = self.data_handler.getMyPermid()
                else:
                    message['owner_permid'] = self.data_handler.getPeerPermid(owner_id)
            game['messages'] = messages
            lmtime = game.pop('lastmove_time')
            game['lm_age'] = int(now() - lmtime)
            fdtime = game.pop('finished_time')
            game['fd_age'] = int(now() - fdtime)

        # Finally, pack all the information into a dictionary and return it
        return {'ip'          : self.ip,
                'port'        : self.port,
                'name'        : self.nameutf8,
                'game buddies': game_buddies,
                'random peers': random_peers,
                'recent games': recent_games,
                'connectable' : True}
                #'connectable' : self.dialback.isConnectable()}

    def gossipSend(self, target_permid, selversion, active):
        gossip_data = self.createGossipMessage(target_permid, selversion)
        self.debug("", "gossipSend", len(gossip_data), currentThread().getName())
        try:
            gossip_msg = bencode(gossip_data)
        except:
            print_exc()
            self.debug("", "gossip_data:", gossip_data, kind=ERROR)
            return

        if active:
            self.debug('Active', "create GC_GOSSIP to send to", self.get_peer_info(target_permid))
        else:
            self.debug('Passive', "create GC_GOSSIP to reply to", self.get_peer_info(target_permid))

        self.overlay_bridge.send(target_permid, GC_GOSSIP+gossip_msg, self.gossipSendCallback)
        self.blockPeer(target_permid, self.send_block_list, self.short_block_interval)
        self.removeConnCandidate(target_permid)

        self.debug("", '****************--------------'*2)
        self.debug("", 'sent GC_GOSSIP to', show_permid_short(target_permid), len(gossip_msg))

        if active:
            self.debug('Active', "send GC_GOSSIP to", self.get_peer_info(target_permid))
        else:
            self.debug('Passive', "reply GC_GOSSIP to", self.get_peer_info(target_permid))

        dns = self.dnsindb(target_permid)
        if dns:
            ip,port = dns
            if active:
                MSG_ID = 'GC_GOSSIP (active)'
            else:
                MSG_ID = 'GC_GOSSIP (passive)'
            msg = repr(self.displayGossipData(gossip_data,selversion))
            self.log('SEND_MSG', ip, port, target_permid, msg_type=MSG_ID, payload=msg)

        return gossip_data

    def gossipSendCallback(self, exc, target_permid):
        if exc is None:
            self.debug("", "msg was sent successfully to peer", self.get_peer_info(target_permid))
        else:
            self.debug("", "error in sending msg to", self.get_peer_info(target_permid), exc, kind=WARNING)
            self.closeConnection(target_permid, Exception.__str__(exc).strip())

    def blockPeer(self, peer_permid, block_list, block_interval=None):
        if block_interval is None:
            block_interval = self.block_interval
        unblock_time = now() + block_interval
        block_list[peer_permid] = unblock_time

    def isBlocked(self, peer_permid, block_list):
        if peer_permid not in block_list:
            return False
        unblock_time = block_list[peer_permid]
        if now() >= unblock_time - self.network_delay:
            block_list.pop(peer_permid)
            return False
        return True

    def handleMessage(self, permid, selversion, message):
        t = message[0]
        if t == GC_ALIVE and message[1:] == '':
            return self.gotKeepAliveMessage(permid)
        elif t == GC_GOSSIP:
            return self.gotGossipMessage(message[1:], permid, selversion)
        self.debug("", "wrong message to GameCastGossip", ord(t), "Round", self.round)
        return False

    def gotKeepAliveMessage(self, peer_permid):
        if self.isConnected(peer_permid) or self.isConnectedUnverified(peer_permid):
            self.debug("", "received GC_ALIVE from", show_permid_short(peer_permid))
            return True
        else:
            self.debug("", "received GC_ALIVE from a not connected peer. Round", self.round, kind=ERROR)
            return False

    def gotGossipMessage(self, recv_msg, sender_permid, selversion):
        # If the connection is not verified yet, do so now
        if self.isConnectedUnverified(sender_permid):
            sv, li = self.connections_unverified.pop(sender_permid)
            self.addConnection(sender_permid, sv, li)

        active = self.isBlocked(sender_permid, self.send_block_list)
        thread = 'Active' if active else 'Passive'

        self.debug(thread, "received GC_GOSSIP from", show_permid_short(sender_permid))

        if not sender_permid or sender_permid == self.permid:
            self.debug(thread, "ignoring GC_GOSSIP from a None peer", show_permid_short(sender_permid), kind=ERROR)
            return False

        if self.isBlocked(sender_permid, self.recv_block_list):
            self.debug(thread, "ignoring GC_GOSSIP from a recv blocked peer", show_permid_short(sender_permid), kind=WARNING)
            # Allow the connection to be kept. That peer may have restarted in 4 hours
            return True

        if MAX_GOSSIP_LENGTH > 0 and len(recv_msg) > MAX_GOSSIP_LENGTH:
            self.debug(thread, "ignoring large GC_GOSSIP (size %d)" % len(recv_msg), kind=WARNING)
            return False

        gossip_data = {}
        try:
            try:
                gossip_data = bdecode(recv_msg)
            except ValueError, msg:
                self.debug("", "got invalid GC_GOSSIP:", msg, "Round", self.round, kind=WARNING)
                return False
            gossip_data.update({'permid':sender_permid})
            try:
                self.validGossipData(gossip_data, self.num_gbs, self.num_rps, self.num_gms, selversion)
            except RuntimeError, msg:
                self.debug("", "got invalid GC_GOSSIP:", msg, "From", self.dnsindb(sender_permid), "Round", self.round, kind=WARNING)
                return False

            # Update sender's ip and port in gossip_data
            dns = self.dnsindb(sender_permid)
            if dns != None:
                sender_ip = dns[0]
                sender_port = dns[1]
                gossip_data.update({'ip':sender_ip})
                gossip_data.update({'port':sender_port})

            MSG_ID = 'GC_GOSSIP (active)' if active else 'GC_GOSSIP (passive)'
            msg = repr(self.displayGossipData(gossip_data,selversion))
            self.log('RECV_MSG', sender_ip, sender_port, sender_permid, msg_type=MSG_ID, payload=msg)

            self.handleGossipData(sender_permid, gossip_data, selversion)
            self.debug(thread, "store peers from incoming msg to cache and db")

            # Update sender and other peers in connection list
            conn = 1 if active else gossip_data.get('connectable', 0)
            addto = self.addPeerToConnList(sender_permid, conn)
            self.debug(thread, "add connected peer %s to connection list %s" % (self.get_peer_info(sender_permid), addto))

        except Exception, msg:
            print_exc()
            raise Exception, msg
            return True

        self.blockPeer(sender_permid, self.recv_block_list)
        self.debug(thread, "block connected peer in recv block list", self.get_peer_info(sender_permid), self.recv_block_list[sender_permid])

        # If we haven't already send a GC_GOSSIP, do so now
        if not active and self.isConnected(sender_permid):
            self.gossipSend(sender_permid, selversion, active=False)
            self.debug(thread, "block connected peer in send block list", self.get_peer_info(sender_permid), self.send_block_list[sender_permid])
            self.debug(thread, "remove connected peer from Cc", self.get_peer_info(sender_permid))
            # Be idle in next round
            self.next_initiate += 1
            self.debug(thread, "add idle loops", self.next_initiate)

        return True

    def validGossipData(self, gossip_data, nbuddies=10, npeers=10, ngames=10, selversion=0):

        def validPeer(peer):
            validPermid(peer['permid'])
            validIP(peer['ip'])
            validPort(peer['port'])

        def validGame(game):
            if not isinstance(game, dict):
                raise RuntimeError, "gcg: invalid game type " + str(type(game))
            if not (game.has_key('game_id') and game.has_key('winner_permid') and
                    game.has_key('moves') and game.has_key('players') and
                    game.has_key('time') and game.has_key('inc') and
                    game.has_key('gamename') and game.has_key('messages') and
                    game.has_key('lm_age') and game.has_key('fd_age') and
                    game.has_key('is_finished') and len(game) == 11):
                raise RuntimeError, "gcg: invalid game fields " + str(game)
            messages = game.get('messages', [])
            for message in messages:
                if not (message.has_key('message_id') and message.has_key('owner_permid') and
                        message.has_key('game_id') and message.has_key('content') and len(message) == 4):
                    raise RuntimeError, "gcg: invalid message fields " + str(message)

        validIP(gossip_data['ip'])
        validPort(gossip_data['port'])
        if not (isinstance(gossip_data['name'], str)):
            raise RuntimeError, "gcg: invalid name type " + str(type(gossip_data['name']))
        if len(gossip_data['game buddies']) > nbuddies:
            raise RuntimeError, "gcg: too many game buddies %d" % len(gossip_data['game buddies'])
        if len(gossip_data['random peers']) > npeers:
            raise RuntimeError, "gcg: too many random peers %d" % len(gossip_data['random peers'])
        if len(gossip_data['recent games']) > ngames:
            raise RuntimeError, "gcg: too many recent games %d" % len(gossip_data['recent games'])

        for gb in gossip_data['game buddies']:
            validPeer(gb)
        for rp in gossip_data['random peers']:
            validPeer(rp)
        for rg in gossip_data['recent games']:
            validGame(rg)
        return True

    def handleGossipData(self, sender_permid, gossip_data, selversion):
        _now = now()

        gossip_data['oversion'] = selversion
        gbs = gossip_data.pop('game buddies')
        rps = gossip_data.pop('random peers')
        gms = gossip_data.pop('recent games')


        # Preprocess games and messages
        sender_id = self.data_handler.getPeerID(sender_permid)
        games = []
        peers = {}
        messages = []
        for game in gms:
            plrs = game.pop('players')
            plrs = dict((str2bin(key), value) for (key, value) in bdecode(plrs).items())
            for permid, info in plrs.iteritems():
                if permid != sender_permid and permid != self.permid:
                    peer = {'ip':hostname_or_ip2ip(info['ip']), 'port':info['port'], 'gc_member':1}
                    if info.has_key('name'):
                        peer['name'] = dunno2unicode(info['name'])
                    peers[permid] = peer
                plrs[permid] = info['colour']
            game['players'] = bencode(dict((bin2str(key), value) for (key, value) in plrs.items()))
            msgs = game.pop('messages')
            for message in msgs:
                message['game_owner_id'] = sender_id
                owner_permid = message.pop('owner_permid')
                if owner_permid == self.data_handler.getMyPermid():
                    # Don't update our own messages
                    continue
                message['owner_id'] = self.data_handler.getPeerID(owner_permid)
                if message['owner_id'] == None:
                    continue
                message['creation_time'] = _now
                messages.append(message)
            game['owner_id'] = sender_id
            lm_age = game.pop('lm_age')
            fd_age = game.pop('fd_age')
            game['lastmove_time'] = _now - lm_age if lm_age < _now else 0
            game['finished_time'] = _now - fd_age if fd_age < _now else 0
            game['creation_time'] = _now
            games.append(game)
        self.data_handler.importPeers(peers, sender_permid)
        self.data_handler.importGames(games)
        self.data_handler.importMessages(messages)

        # Preprocess random peers and game buddies
        peers = {}
        gc_data = [gossip_data] + gbs + rps
        for peer in gc_data:
            peer_permid = peer['permid']
            if peer_permid == self.permid:
                continue
            last_seen = _now
            if peer_permid != sender_permid:
                self.addConnCandidate(peer_permid, last_seen)
            new_peer_data = {}
            new_peer_data['ip'] = hostname_or_ip2ip(peer['ip'])
            new_peer_data['port'] = peer['port']
            new_peer_data['gc_last_seen'] = last_seen
            new_peer_data['gc_member'] = 1
            if peer.has_key('name'):
                new_peer_data['name'] = dunno2unicode(peer['name'])
            peers[peer_permid] = new_peer_data
        peers[sender_permid]['gc_last_recieved'] = _now
        self.data_handler.importPeers(peers, sender_permid)

        self.limitConnCandidate()
        if len(self.connection_candidates) > self.bootstrap_num:
            self.bootstrapped = True

    def displayGossipData(self, gossip_data, selversion):
        msg = copy.deepcopy(gossip_data)

        if msg.has_key('permid'):
            msg.pop('permid')
        if msg.has_key('ip'):
            msg.pop('ip')
        if msg.has_key('port'):
            msg.pop('port')

        # Avoid coding error
        name = repr(msg['name'])

        if msg.has_key('game buddies'):
            for buddy in msg['game buddies']:
                buddy['permid'] = show_permid(buddy['permid'])

        if msg.has_key('random peers'):
            for peer in msg['random peers']:
                peer['permid'] = show_permid(peer['permid'])

        if msg.has_key('recent games'):
            for game in msg['recent games']:
                game['winner_permid'] = show_permid(game['winner_permid'])

        return msg

    def updateGBandRPList(self):
        nconnpeers = len(self.connected_connectable_peers)
        if nconnpeers == 0:
            self.connected_game_buddies = []
            self.connected_random_peers = []
            return

        tmplist = []
        gbs = []
        rps = []
        for peer in self.connected_connectable_peers:
            ifactor = self.data_handler.getInteractionFactor(peer)
            if ifactor > 0:
                tmplist.append([ifactor, peer])
            else:
                rps.append(peer)
        tmplist.sort()
        tmplist.reverse()

        # Move the peers with the highest interaction-factor to GB
        ngb = min((nconnpeers+1)/2, self.max_conn_gb)
        if len(tmplist) > 0:
            for ifactor,peer in tmplist[:ngb]:
                gbs.append(peer)

        # The remaining peers (if any) go to RP
        if len(tmplist) > ngb:
            rps = [peer for ifactor,peer in tmplist[ngb:]] + rps

        # Remove the oldest peer from both random peer list and connected_connectable_peers
        if len(rps) > self.max_conn_rp:
            tmplist = []
            for peer in rps:
                connect_time = self.connected_connectable_peers[peer]
                tmplist.append([connect_time, peer])
            tmplist.sort()
            tmplist.reverse()
            rps = []
            i = 0
            for last_seen,peer in tmplist:
                if i < self.max_conn_rp:
                    rps.append(peer)
                else:
                    self.connected_connectable_peers.pop(peer)
                i += 1

        self.connected_game_buddies = gbs
        self.connected_random_peers = rps

    def addPeerToConnList(self, peer_permid, connectable=0):
        # Remove the existing peer from lists so that its status can be updated later
        self.removePeerFromConnList(peer_permid)

        if not self.isConnected(peer_permid):
            return

        _now = now()
        if connectable == 1:
            # Add peer to connected_connectable_peers
            keys = ('ip', 'port', 'oversion', 'name')
            res = self.data_handler.getPeer(peer_permid, keys)
            peer = dict(zip(keys,res))
            if not peer['name']:
                peer.pop('name')
            peer['connect_time'] = _now
            self.connected_connectable_peers[peer_permid] = peer
            self.updateGBandRPList()
            addto = '(reachable peer)'
        else:
            # Add peer to connected_unconnectable_peers
            ups = self.connected_unconnectable_peers
            if peer_permid not in ups:
                if self.max_conn_up <= 0 or len(ups) < self.max_conn_up:
                    ups[peer_permid] = _now
                else:
                    oldest = (None, _now+1)
                    for item in ups.items():
                        if item[1] < oldest[1]:
                            oldest = item
                    if _now >= oldest[1]:
                        ups.pop(oldest[0])
                        ups[peer_permid] = _now
            addto = '(peer deemed unreachable)'
        return addto

    def removePeerFromConnList(self, peer_permid):
        removed = 0
        if peer_permid in self.connected_connectable_peers:
            self.connected_connectable_peers.pop(peer_permid)
            if peer_permid in self.connected_game_buddies:
                self.connected_game_buddies.remove(peer_permid)
            if peer_permid in self.connected_random_peers:
                self.connected_random_peers.remove(peer_permid)
            removed = 1
        if peer_permid in self.connected_unconnectable_peers:
            self.connected_unconnectable_peers.pop(peer_permid)
            removed = 2
        return removed

    def handleConnection(self,exc,permid,selversion,locally_initiated):
        # Ignore connections with overlay versions < 17
        if selversion < OLPROTO_VER_SEVENTEETH:
            self.debug("", "ignoring connection from", show_permid_short(permid))
        elif self.isConnected(permid) or self.isConnectedUnverified(permid):
            if exc is not None: self.closeConnection(permid, 'overlayswarm:'+Exception.__str__(exc).strip())
        # Postpone fully adding the connection until the peer has send a GC_GOSSIP (to filter out non-gamecast connections)
        elif exc is None and permid != self.permid:
            self.connections_unverified[permid] = (selversion, locally_initiated)
            self.debug("", "add unverified connection", self.get_peer_info(permid))
            dns = self.dnsindb(permid)
            if dns:
                ip,port = dns
                self.log('CONN_ADD', ip, port, permid)

        if exc:
            if permid in self.send_block_list:
                self.send_block_list.pop(permid)
            if permid in self.recv_block_list:
                self.recv_block_list.pop(permid)

        self.debug("", "handle conn from overlay", exc, \
                self.get_peer_info(permid), "selversion:", selversion, \
                "local_init:", locally_initiated, ctime(now()), "; #connections:", len(self.connected_connectable_peers), \
                "; #GB:", len(self.connected_game_buddies), "; #RP:", len(self.connected_random_peers))

    def addConnection(self, peer_permid, selversion, locally_initiated):
        self.debug("", "addConnection", self.isConnected(peer_permid))

        if not self.isConnected(peer_permid):
            # SecureOverlay has already added the peer to db
            self.connections[peer_permid] = selversion
            addto = self.addPeerToConnList(peer_permid, locally_initiated)
            self.debug("", "add verified connection", self.get_peer_info(peer_permid), "to", addto)

            dns = self.dnsindb(peer_permid)
            if dns:
                ip,port = dns
                self.log('CONN_VER', ip, port, peer_permid)

    def closeConnection(self, peer_permid, reason):
        self.debug("", "closeConnection", self.get_peer_info(peer_permid))

        removed = False

        if self.isConnected(peer_permid):
            self.connections.pop(peer_permid)
            removed = True

        if self.isConnectedUnverified(peer_permid):
            self.connections_unverified.pop(peer_permid)
            removed = True

        if self.removePeerFromConnList(peer_permid) == 1:
            self.updateGBandRPList()

        if removed:
            dns = self.dnsindb(peer_permid)
            if dns:
                ip,port = dns
                self.log('CONN_DEL', ip, port, peer_permid, reason=reason)

    def shutdown(self):
        # Called by OverlayThread
        self.memoryHandler.close()

    def get_stats(self):
        nPeer = len(self.data_handler.peers)
        nCc = len(self.connection_candidates)
        nBs = len(self.send_block_list)
        nBr = len(self.recv_block_list)
        nCo = len(self.connections)
        nCg = len(self.connected_game_buddies)
        nCr = len(self.connected_random_peers)
        nCu = len(self.connected_unconnectable_peers)
        return nPeer, nCc, nBs, nBr, nCo, nCg, nCr, nCu

    def print_round(self):
        self.debug("", "Working:", now() - self.start_time, "seconds since start. Round", self.round, "Time:", ctime(now()))
        nPeer, nCc, nBs, nBr, nCo, nCg, nCr, nCu = self.get_stats()
        self.debug("", "*** Status: nPeer nCc: %d %d  nBs nBr: %d %d  nCo nCg nCr nCu: %d %d %d %d" % \
                                    (nPeer,nCc,        nBs,nBr,        nCo,nCg,nCr,nCu))
        if nCc > self.max_conn_cand or nCg > self.max_conn_gb or nCr > self.max_conn_rp or nCu > self.max_conn_up:
            self.debug("", "nCC or nCg or nCr or nCu overloads", kind=WARNING)
        _now = now()
        for i, p in enumerate(self.connected_game_buddies):
            self.debug("", "%d game buddies: "%i + self.get_peer_info(p) + str(_now-self.connected_connectable_peers[p]['connect_time']) + " version: " + str(self.connections[p]))
        for i, p in enumerate(self.connected_random_peers):
            self.debug("", "%d random peers: "%i + self.get_peer_info(p) + str(_now-self.connected_connectable_peers[p]['connect_time']) + " version: " + str(self.connections[p]))
        for i, p in enumerate(self.connected_unconnectable_peers):
            self.debug("", "%d unconnectable peers: "%i + self.get_peer_info(p) + str(_now-self.connected_unconnectable_peers[p]) + " version: " + str(self.connections[p]))

        sims = []
        for p in self.data_handler.peers:
            sim = self.data_handler.peers[p][0]
            if sim > 0:
                sims.append(sim)
        if sims:
            meansim = 0
            nsimpeers = len(sims)
            totalsim = sum(sims)
            if nsimpeers > 0:
                meansim = totalsim/nsimpeers
            self.debug("", "* sim peer: %d %.3f %.3f %.3f %.3f\n" % (nsimpeers, totalsim, meansim, min(sims), max(sims)))

    def print_bootstrap(self):
        self.debug("", "bootstrapping: select", self.bootstrap_num, "peers recently seen from Mega Cache")
        if self.booted < 0:
            self.debug("", "*** bootstrapped recently, so wait for a while")
        elif self.booted == 0:
            self.debug("", "*** no peers to bootstrap. Try next time")
        else:
            self.debug("", "*** bootstrapped, got", len(self.connection_candidates), \
                        "peers in Cc. Times of bootstrapped", self.total_bootstrapped_time)
            for p in self.connection_candidates:
                self.debug("", "* cand:" + `p`)

    def print_target(self, target_permid, r):
        if r == 0:
            self.debug("", "select a game buddy with the highest interaction-factor from Cc for GameCastGossip out")
        else:
            self.debug("", "select a most likely online random peer from Cc for GameCastGossip out")

        if target_permid:
            self.debug("", "*** got target %s sim: %s last_seen: %s" % \
                        (self.get_peer_info(target_permid),
                        self.data_handler.getInteractionFactor(target_permid),
                        ctime(self.data_handler.getPeerLastSeen(target_permid))))
        else:
            self.debug("", "*** no target to select. Skip this round")

    def showIP(self, ip):
        return "%3s.%3s.%3s.%3s" % tuple(ip.split("."))

    def showPort(self, port):
        return "%5s" % port

    def debug(self, thread, *args, **kwargs):
        kind = kwargs.get('kind', INFO)
        if kind is INFO and not DEBUG:
            return
        if (kind is WARNING or kind is ERROR) and not SHOW_ERROR:
            return
        buf = "gcg: "
        if kind is WARNING : buf += "warning - "
        if kind is ERROR   : buf += "error - "
        if thread is not "": buf += "%s thread - " % thread
        buf += " ".join(map(str, args))
        print >> sys.stderr, buf
        sys.stderr.flush()

    def log(self, *args, **kwargs):
        # Local & remote logging..
        if self.logger and args:
            d = {'event_type'    : args[0],
                 'at_peer_short' : '%s (%s:%s)' % (show_permid_short(self.permid), self.showIP(self.ip), self.showPort(self.port)),
                 'at_peer'       : '%s (%s:%s)' % (bin2str(self.permid), self.showIP(self.ip), self.showPort(self.port)),
                 'tf_peer_short' : '%s (%s:%s)' % (show_permid_short(args[3]), self.showIP(args[1]), self.showPort(args[2])) if len(args) > 1 else '',
                 'tf_peer'       : '%s (%s:%s)' % (bin2str(args[3]), self.showIP(args[1]), self.showPort(args[2])) if len(args) > 1 else ''}
            msg = ''
            iterator = iter(sorted(kwargs.iteritems()))
            for key, value in iterator:
                msg += "%s = %s ; " % (key, value)
            if msg:
                msg = msg[:-3]
            self.logger.info(msg, extra=d)


class DataHandler:
    def __init__(self, launchmany, overlay_bridge, max_num_peers=2500):
        self.launchmany = launchmany
        self.overlay_bridge = overlay_bridge
        self.config = self.launchmany.session.sessconfig
        self.peer_db = launchmany.peer_db
        self.gamecast_db = launchmany.gamecast_db
        self.peers = {}                                          # The actual cache {peer_id: [gc_int_factor, gc_last_seen]}
        self.max_num_peers = min(max(max_num_peers, 100), 2500)  # At least 100, at most 2500 peers

    def postInit(self):
        # Build up a cache layer between app and db
        peer_values = self.peer_db.getAll(['peer_id','gc_last_seen'], where='gc_member=1', order_by='last_connected desc', limit=self.max_num_peers)
        self.peers = dict(zip([p[0] for p in peer_values], [[0,p[1]] for p in peer_values]))
        self.updateInteractionFactors()

    def getMyName(self, name=''):
        return self.config.get('nickname', name)

    def getMyIp(self):
        return self.launchmany.get_ext_ip()

    def getMyPort(self):
        return self.launchmany.listen_port

    def getMyPermid(self):
        return self.launchmany.session.get_permid()

    def getPeer(self, permid, keys=None):
        return self.peer_db.getPeer(permid, keys)

    def getPeerID(self, permid):
        if isinstance(permid, int) and permid > 0:
            return permid
        else:
            return self.peer_db.getPeerID(permid)

    def getPeerPermid(self, peer_id):
        return self.peer_db.getPermid(peer_id)

    def getPeerLastSeen(self, peer_permid):
        peer_id = self.getPeerID(peer_permid)
        return self.getPeerIDLastSeen(peer_id)

    def getPeerIDLastSeen(self, peer_id):
        if not peer_id or peer_id not in self.peers:
            return 0
        return self.peers[peer_id][1]

    def getInteractionFactor(self, peer_permid):
        peer_id = self.getPeerID(peer_permid)
        if peer_id is None or peer_id not in self.peers:
            ifactor = 0
        else:
            ifactor = self.peers[peer_id][0]
        return ifactor

    def updateInteractionFactors(self):
        # Retrieve the number of interactions (= # games played) the current peer has had with other peers
        interactionCount = self.gamecast_db.getInteractionCount(self.getMyPermid())
        for permid, count in interactionCount.items():
            peer_id = self.getPeerID(permid)
            if peer_id and peer_id in self.peers:
                self.peers[peer_id][0] = min(count, 100) / 100.0

    def getMyRecentGames(self, num):
        return self.gamecast_db.getMyRecentGames(num)

    def getMessages(self, game_owner_id, game_id):
        return self.gamecast_db.getMessages(game_owner_id = game_owner_id, game_id = game_id)

    def importPeers(self, peer_data, sender_permid):
        for permid in peer_data:
            new_peer = peer_data[permid]
            old_peer = self.peer_db.getPeer(permid)
            new_peer['gc_member'] = 1
            updateDNS = (permid != sender_permid)
            # Import the peer-information to the database
            if not old_peer:
                self.peer_db.addPeer(permid, new_peer, update_dns=updateDNS, commit=False)
            elif new_peer:
                for k in new_peer.keys():
                    if old_peer[k] == new_peer[k]:
                        new_peer.pop(k)
                if not updateDNS:
                    if 'ip' in new_peer:
                        del new_peer['ip']
                    if 'port' in new_peer:
                        del new_peer['port']
                if new_peer:
                    self.peer_db.updatePeer(permid, commit=False, **new_peer)
            self.peer_db.commit()
            # Import the peer-information to the cache
            peerid = self.getPeerID(permid)
            if peerid not in self.peers:
                self.peers[peerid] = [0, new_peer.get('gc_last_seen', now())]
            else:
                self.peers[peerid][1] = new_peer.get('gc_last_seen', now())
            from Tribler.Core.GameCast.GameCast import GameCast
            gc = GameCast.getInstance()
            if peerid in gc.peers.keys():
                gc.peers.pop(peerid)

    def importGames(self, game_data):
        update = []
        for new_game in game_data:
            game_id = new_game['game_id']
            owner_id = new_game['owner_id']
            old_game = self.gamecast_db.getGames(game_id = game_id, owner_id = owner_id)
            # Import the game-information to the database
            if old_game:
                # For now, we assume that games can't be changed, once they are finished
                pass
            else:
                self.gamecast_db.addGame(new_game, commit=False)
                if new_game['gamename'] not in update:
                    update.append(new_game['gamename'])
        self.gamecast_db.commit()
        from Tribler.Core.GameCast.GameCast import GameCast
        for gamename in update:
            GameCast.getInstance().updateRating(gamename)

    def importMessages(self, message_data):
        for new_message in message_data:
            message_id = new_message['message_id']
            owner_id = new_message['owner_id']
            old_message = self.gamecast_db.getMessages(message_id = message_id, owner_id = owner_id)
            # Import the game-information to the database
            if not old_message:
                self.gamecast_db.addMessage(new_message, commit=True)
            else:
                pass#self.gamecast_db.updateMessage(new_message, commit=True)
