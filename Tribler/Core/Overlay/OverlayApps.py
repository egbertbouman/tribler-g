# Written by Arno Bakker
# Updated by George Milescu
# see LICENSE.txt for license information
#
# All applications on top of the SecureOverlay should be started here.
#
from MetadataHandler import MetadataHandler
from threading import Lock
from threading import currentThread
from time import time
from traceback import print_exc
import sys

from Tribler.Core.BitTornado.BT1.MessageID import *
from Tribler.Core.BuddyCast.buddycast import BuddyCastFactory
from Tribler.Core.GameCast.GameCastGossip import GameCastGossip
from Tribler.Core.GameCast.GameCast import GameCast
from Tribler.Core.ProxyService.DoeMessageHandler import DoeMessageHandler
from Tribler.Core.ProxyService.ProxyMessageHandler import ProxyMessageHandler
from Tribler.Core.ProxyService.ProxyPeerManager import ProxyPeerManager
from Tribler.Core.NATFirewall.DialbackMsgHandler import DialbackMsgHandler
from Tribler.Core.NATFirewall.NatCheckMsgHandler import NatCheckMsgHandler
from Tribler.Core.SocialNetwork.FriendshipMsgHandler import FriendshipMsgHandler
from Tribler.Core.SocialNetwork.RemoteQueryMsgHandler import RemoteQueryMsgHandler
from Tribler.Core.SocialNetwork.RemoteTorrentHandler import RemoteTorrentHandler
from Tribler.Core.SocialNetwork.SocialNetworkMsgHandler import SocialNetworkMsgHandler
from Tribler.Core.Statistics.Crawler import Crawler
from Tribler.Core.Statistics.DatabaseCrawler import DatabaseCrawler
from Tribler.Core.Statistics.FriendshipCrawler import FriendshipCrawler
# from Tribler.Core.Statistics.SeedingStatsCrawler import SeedingStatsCrawler
# from Tribler.Core.Statistics.VideoPlaybackCrawler import VideoPlaybackCrawler
from Tribler.Core.Statistics.RepexCrawler import RepexCrawler
from Tribler.Core.Statistics.PunctureCrawler import PunctureCrawler
from Tribler.Core.Statistics.ChannelCrawler import ChannelCrawler
from Tribler.Core.Statistics.UserEventLogCrawler import UserEventLogCrawler
from Tribler.Core.Utilities.utilities import show_permid_short
from Tribler.Core.simpledefs import *
from Tribler.Core.Subtitles.SubtitlesHandler import SubtitlesHandler
from Tribler.Core.Subtitles.SubtitlesSupport import SubtitlesSupport
from Tribler.Core.Subtitles.PeerHaveManager import PeersHaveManager

DEBUG = False

class OverlayApps:
    # Code to make this a singleton
    __single = None

    def __init__(self):
        if OverlayApps.__single:
            raise RuntimeError, "OverlayApps is Singleton"
        OverlayApps.__single = self
        self.doe_handler = None
        self.proxy_handler = None
        self.proxy_peer_manager = None
        self.metadata_handler = None
        self.buddycast = None
        self.gamecast_gossip = None
        self.gamecast = None
        self.collect = None
        self.dialback_handler = None
        self.socnet_handler = None
        self.rquery_handler = None
        self.chquery_handler = None
        self.friendship_handler = None
        self.msg_handlers = {}
        self.connection_handlers = []
        self.text_mode = None
        self.requestPolicyLock = Lock()

    def getInstance(*args, **kw):
        if OverlayApps.__single is None:
            OverlayApps(*args, **kw)
        return OverlayApps.__single
    getInstance = staticmethod(getInstance)

    def register(self, overlay_bridge, session, launchmany, config, requestPolicy):
        self.overlay_bridge = overlay_bridge
        self.launchmany = launchmany
        self.requestPolicy = requestPolicy
        self.text_mode = config.has_key('text_mode')

        # OverlayApps gets all messages, and demultiplexes
        overlay_bridge.register_recv_callback(self.handleMessage)
        overlay_bridge.register_conns_callback(self.handleConnection)

        # Arno, 2010-01-28: Start with crawler support, other mods depend on
        # that, e.g. BuddyCast
        i_am_crawler = False
        if config['crawler']:
            crawler = Crawler.get_instance(session)
            self.register_msg_handler([CRAWLER_REQUEST], crawler.handle_request)

            database_crawler = DatabaseCrawler.get_instance()
            crawler.register_message_handler(CRAWLER_DATABASE_QUERY, database_crawler.handle_crawler_request, database_crawler.handle_crawler_reply)
            # seeding_stats_crawler = SeedingStatsCrawler.get_instance()
            # crawler.register_message_handler(CRAWLER_SEEDINGSTATS_QUERY, seeding_stats_crawler.handle_crawler_request, seeding_stats_crawler.handle_crawler_reply)
            friendship_crawler = FriendshipCrawler.get_instance(session)
            crawler.register_message_handler(CRAWLER_FRIENDSHIP_STATS, friendship_crawler.handle_crawler_request, friendship_crawler.handle_crawler_reply)
            natcheck_handler = NatCheckMsgHandler.getInstance()
            natcheck_handler.register(launchmany)
            crawler.register_message_handler(CRAWLER_NATCHECK, natcheck_handler.gotDoNatCheckMessage, natcheck_handler.gotNatCheckReplyMessage)
            crawler.register_message_handler(CRAWLER_NATTRAVERSAL, natcheck_handler.gotUdpConnectRequest, natcheck_handler.gotUdpConnectReply)
            # videoplayback_crawler = VideoPlaybackCrawler.get_instance()
            # crawler.register_message_handler(CRAWLER_VIDEOPLAYBACK_EVENT_QUERY, videoplayback_crawler.handle_event_crawler_request, videoplayback_crawler.handle_event_crawler_reply)
            # crawler.register_message_handler(CRAWLER_VIDEOPLAYBACK_INFO_QUERY, videoplayback_crawler.handle_info_crawler_request, videoplayback_crawler.handle_info_crawler_reply)
            repex_crawler = RepexCrawler.get_instance(session)
            crawler.register_message_handler(CRAWLER_REPEX_QUERY, repex_crawler.handle_crawler_request, repex_crawler.handle_crawler_reply)
            puncture_crawler = PunctureCrawler.get_instance()
            crawler.register_message_handler(CRAWLER_PUNCTURE_QUERY, puncture_crawler.handle_crawler_request, puncture_crawler.handle_crawler_reply)
            channel_crawler = ChannelCrawler.get_instance()
            crawler.register_message_handler(CRAWLER_CHANNEL_QUERY, channel_crawler.handle_crawler_request, channel_crawler.handle_crawler_reply)
            usereventlog_crawler = UserEventLogCrawler.get_instance()
            crawler.register_message_handler(CRAWLER_USEREVENTLOG_QUERY, usereventlog_crawler.handle_crawler_request, usereventlog_crawler.handle_crawler_reply)

            if crawler.am_crawler():
                i_am_crawler = True
                # we will only accept CRAWLER_REPLY messages when we are actully a crawler
                self.register_msg_handler([CRAWLER_REPLY], crawler.handle_reply)
                self.register_connection_handler(crawler.handle_connection)

                if "database" in sys.argv:
                    # allows access to tribler database (boudewijn)
                    crawler.register_crawl_initiator(database_crawler.query_initiator)

                # if "videoplayback" in sys.argv:
                #     # allows access to video-playback statistics (boudewijn)
                #     crawler.register_crawl_initiator(videoplayback_crawler.query_initiator)

                # if "seedingstats" in sys.argv:
                #     # allows access to seeding statistics (Boxun)
                #     crawler.register_crawl_initiator(seeding_stats_crawler.query_initiator, frequency=60*30)

                if "friendship" in sys.argv:
                    # allows access to friendship statistics (Ali)
                    crawler.register_crawl_initiator(friendship_crawler.query_initiator)

                if "natcheck" in sys.argv:
                    # allows access to nat-check statistics (Lucia)
                    crawler.register_crawl_initiator(natcheck_handler.doNatCheck, 3600)

                if "repex" in sys.argv:
                    # allows access to RePEX log statistics (Raynor Vliegendhart)
                    crawler.register_crawl_initiator(repex_crawler.query_initiator)

                if "puncture" in sys.argv:
                    # allows access to UDPPuncture log statistics (Gertjan)
                    crawler.register_crawl_initiator(puncture_crawler.query_initiator)

                if "channel" in sys.argv:
                    # allows access to tribler channels' database (nitin)
                    crawler.register_crawl_initiator(channel_crawler.query_initiator)

                if "usereventlog" in sys.argv:
                    # allows access to usereventlog database (Niels)
                    crawler.register_crawl_initiator(usereventlog_crawler.query_initiator)
        else:
            self.register_msg_handler([CRAWLER_REQUEST, CRAWLER_REPLY], self.handleDisabledMessage)

        # ProxyService_
        #
        # Create handler for metadata messages in two parts, as
        # ProxyService needs to know the metadata_handler and we need
        # to know the proxy message handler.
        # Part 1:
        self.metadata_handler = MetadataHandler.getInstance()

        # Create handler for messages to doe
        self.doe_handler = DoeMessageHandler(launchmany)
        self.register_msg_handler(ProxyMessages, self.doe_handler.handleMessage)

        # Create handler for messages to proxy
        self.proxy_handler = ProxyMessageHandler()
        self.proxy_handler.register(session, self.metadata_handler, config['proxyservice_dir'], config.get('proxy_default_dlcfg', False))
        self.register_msg_handler(DoeMessages, self.proxy_handler.handleMessage)

        # Part 2:
        # Register the ProxyHandler to be called when a .torrent is in
        self.metadata_handler.register(overlay_bridge, self.proxy_handler, launchmany, config)
        self.register_msg_handler(MetadataMessages, self.metadata_handler.handleMessage)

        # Create the ProxyPeerManager
        self.proxy_peer_manager = ProxyPeerManager(launchmany)
        #
        # _ProxyService

        # 13-04-2010 Andrea: subtitles collecting
        if not config['subtitles_collecting'] :
            self.subtitles_handler = None
        else:
            self.subtitles_handler = SubtitlesHandler.getInstance()
            self.subtitles_handler.register(self.overlay_bridge, self.launchmany.richmetadataDbHandler, self.launchmany.session)

            self.peersHaveManger = PeersHaveManager.getInstance()
            if not self.peersHaveManger.isRegistered():
                self.peersHaveManger.register(self.launchmany.richmetadataDbHandler, self.overlay_bridge)
            # I'm not sure if this is the best place to init this
            self.subtitle_support = SubtitlesSupport.getInstance()

            keypair = self.launchmany.session.keypair
            permid = self.launchmany.session.get_permid()
            self.subtitle_support._register(self.launchmany.richmetadataDbHandler,
                                           self.subtitles_handler,
                                           self.launchmany.channelcast_db, permid,
                                           keypair, self.peersHaveManger,
                                           self.overlay_bridge)

            # cleanup the subtitles database at the first launch
            self.subtitle_support.runDBConsinstencyRoutine()



        if not config['torrent_collecting']:
            self.torrent_collecting_solution = 0
        else:
            self.torrent_collecting_solution = config['buddycast_collecting_solution']

        if config['buddycast']:
            # Create handler for Buddycast messages

            self.buddycast = BuddyCastFactory.getInstance(superpeer=config['superpeer'], log=config['overlay_log'])
            # Using buddycast to handle torrent collecting since they are dependent
            self.buddycast.register(overlay_bridge, launchmany,
                                    launchmany.rawserver_fatalerrorfunc,
                                    self.metadata_handler,
                                    self.torrent_collecting_solution,
                                    config['start_recommender'],config['buddycast_max_peers'],i_am_crawler)

            self.register_msg_handler(BuddyCastMessages, self.buddycast.handleMessage)
            self.register_connection_handler(self.buddycast.handleConnection)

        if config['gamecast']:
            self.gamecast_gossip = GameCastGossip.getInstance()
            self.gamecast_gossip.register(overlay_bridge, launchmany, config)
            self.register_msg_handler(GameCastGossipMessages, self.gamecast_gossip.handleMessage)
            self.register_connection_handler(self.gamecast_gossip.handleConnection)

            self.gamecast = GameCast.getInstance()
            self.gamecast.register(overlay_bridge, launchmany, config)
            self.register_msg_handler(GameCastMessages,self.gamecast.handleMessage)
            self.register_connection_handler(self.gamecast.handleConnection)

        if config['dialback']:
            self.dialback_handler = DialbackMsgHandler.getInstance()
            # The Dialback mechanism needs the real rawserver, not the overlay_bridge
            self.dialback_handler.register(overlay_bridge, launchmany, launchmany.rawserver, config)
            self.register_msg_handler([DIALBACK_REQUEST],
                                      self.dialback_handler.olthread_handleSecOverlayMessage)
            self.register_connection_handler(self.dialback_handler.olthread_handleSecOverlayConnection)
        else:
            self.register_msg_handler([DIALBACK_REQUEST], self.handleDisabledMessage)

        if config['socnet']:
            self.socnet_handler = SocialNetworkMsgHandler.getInstance()
            self.socnet_handler.register(overlay_bridge, launchmany, config)
            self.register_msg_handler(SocialNetworkMessages,self.socnet_handler.handleMessage)
            self.register_connection_handler(self.socnet_handler.handleConnection)

            self.friendship_handler = FriendshipMsgHandler.getInstance()
            self.friendship_handler.register(overlay_bridge, launchmany.session)
            self.register_msg_handler(FriendshipMessages,self.friendship_handler.handleMessage)
            self.register_connection_handler(self.friendship_handler.handleConnection)

        if config['rquery']:
            self.rquery_handler = RemoteQueryMsgHandler.getInstance()
            self.rquery_handler.register(overlay_bridge,launchmany,config,self.buddycast,log=config['overlay_log'])
            self.register_msg_handler(RemoteQueryMessages,self.rquery_handler.handleMessage)
            self.register_connection_handler(self.rquery_handler.handleConnection)

        if config['subtitles_collecting']:
            hndl = self.subtitles_handler.getMessageHandler()
            self.register_msg_handler(SubtitleMessages, hndl)

        if config['torrent_collecting']:
            self.rtorrent_handler = RemoteTorrentHandler.getInstance()
            self.rtorrent_handler.register(overlay_bridge,self.metadata_handler,session)
            self.metadata_handler.register2(self.rtorrent_handler)

        # Add notifier as connection handler
        self.register_connection_handler(self.notifier_handles_connection)

        if config['buddycast']:
            # Arno: to prevent concurrency between mainthread and overlay
            # thread where BuddyCast schedules tasks
            self.buddycast.register2()

            # ProxyService_
            #
            # Register the ProxyPeerManager with BuddyCast
            # The ProxyPeerManager has to register after the BuddyCastCore object is created by the BuddyCastFactory.
            # The BuddyCastCore object is created in a method scheduled by the overlay_bridge.
            # If the ProxyPeerManager.register is also scheduled by the overlay_bridge, it will be executed after the
            # BuddyCastCore is created (overlay_bridge executes the scheduled tasks in the order of their execution)
            if self.proxy_peer_manager:
                self.overlay_bridge.add_task(self.proxy_peer_manager.register, 0)
            #
            # _ProxyService


    def early_shutdown(self):
        """ Called as soon as Session shutdown is initiated. Used to start
        shutdown tasks that takes some time and that can run in parallel
        to checkpointing, etc.
        """
        # Called by OverlayThread
        if self.friendship_handler is not None:
            self.friendship_handler.shutdown()
        if self.gamecast_gossip is not None:
            self.gamecast_gossip.shutdown()
        if self.gamecast is not None:
            self.gamecast.shutdown()

    def register_msg_handler(self, ids, handler):
        """
        ids is the [ID1, ID2, ..] where IDn is a sort of message ID in overlay
        swarm. Each ID can only be handled by one handler, but a handler can
        handle multiple IDs
        """
        for id in ids:
            if DEBUG:
                print >> sys.stderr,"olapps: Message handler registered for",getMessageName(id)
            self.msg_handlers[id] = handler

    def register_connection_handler(self, handler):
        """
            Register a handler for if a connection is established
            handler-function is called like:
            handler(exc,permid,selversion,locally_initiated)
        """
        assert handler not in self.connection_handlers, 'This connection_handler is already registered'
        if DEBUG:
            print >> sys.stderr, "olapps: Connection handler registered for", handler
        self.connection_handlers.append(handler)

    def handleMessage(self,permid,selversion,message):
        """ demultiplex message stream to handlers """

        # Check auth
        if not self.requestAllowed(permid, message[0]):
            if DEBUG:
                print >> sys.stderr, "olapps: Message not allowed", getMessageName(message[0])
            return False

        if message[0] in self.msg_handlers:
            # This is a one byte id. (For instance a regular
            # BitTorrent message)
            id_ = message[0]
        else:
            if DEBUG:
                print >> sys.stderr, "olapps: No handler found for", getMessageName(message[0:2])
            return True#False

        if DEBUG:
            print >> sys.stderr, "olapps: handleMessage", getMessageName(id_), "v" + str(selversion)

        try:
            if DEBUG:
                st = time()
                ret = self.msg_handlers[id_](permid, selversion, message)
                et = time()
                diff = et - st
                if diff > 0:
                    print >> sys.stderr,"olapps: ",getMessageName(id_),"returned",ret,"TOOK %.5f" % diff
                return ret
            else:
                return self.msg_handlers[id_](permid, selversion, message)
        except:
            # Catch all
            print_exc()
            return False

    def handleDisabledMessage(self, *args):
        return True

    def handleConnection(self,exc,permid,selversion,locally_initiated):
        """ An overlay-connection was established. Notify interested parties. """

        if DEBUG:
            print >> sys.stderr,"olapps: handleConnection",exc,selversion,locally_initiated,currentThread().getName()

        for handler in self.connection_handlers:
            try:
                #if DEBUG:
                #    print >> sys.stderr,"olapps: calling connection handler:",'%s.%s' % (handler.__module__, handler.__name__)
                handler(exc,permid,selversion,locally_initiated)
            except:
                print >> sys.stderr, 'olapps: Exception during connection handler calling'
                print_exc()

    def requestAllowed(self, permid, messageType):
        self.requestPolicyLock.acquire()
        try:
            rp = self.requestPolicy
        finally:
            self.requestPolicyLock.release()
        allowed = rp.allowed(permid, messageType)
        if DEBUG:
            if allowed:
                word = 'allowed'
            else:
                word = 'denied'
            print >> sys.stderr, 'olapps: Request type %s from %s was %s' % (getMessageName(messageType), show_permid_short(permid), word)
        return allowed

    def setRequestPolicy(self, requestPolicy):
        self.requestPolicyLock.acquire()
        try:
            self.requestPolicy = requestPolicy
        finally:
            self.requestPolicyLock.release()


    def notifier_handles_connection(self, exc,permid,selversion,locally_initiated):
        # Notify interested parties (that use the notifier/observer structure) about a connection
        self.launchmany.session.uch.notify(NTFY_PEERS, NTFY_CONNECTION, permid, True)
