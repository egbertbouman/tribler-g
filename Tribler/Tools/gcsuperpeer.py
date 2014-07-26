import sys
import time
import logging

from Tribler.Core.Overlay.OverlayThreadingBridge import OverlayThreadingBridge
from Tribler.Core.BitTornado.parseargs import parseargs
from Tribler.Core.API import *

import Tribler.Core.GameCast.GameCast as GameCastMod
import Tribler.Core.GameCast.GameCastGossip as GameCastGossipMod
GameCastMod.GameCast.DEBUG   = True
GameCastGossipMod.DEBUG      = True
GameCastGossipMod.SHOW_ERROR = True

argsdef = [('nickname', 'Superpeer1', 'name of the superpeer'),
           ('port', 7001, 'TCP+UDP listen port'),
           ('permid', '', 'filename containing EC keypair'),
           ('statedir', '.Tribler','dir to save session state'),
           ('installdir', '', 'source code install dir')]

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

if __name__ == "__main__":
    config, fileargs = parseargs(sys.argv, argsdef, presets = {})
    print >> sys.stderr, "Config is", config

    overlay_bridge = OverlayThreadingBridge.getInstance()
    overlay_bridge.gcqueue = overlay_bridge.tqueue
    overlay_bridge.add_task(startsession, 0)

    gclogger = logging.getLogger('gamecast')
    gclogger.disabled = True
    gcglogger = logging.getLogger('gamecastgossip')
    gcglogger.disabled = True

    while True:
        time.sleep(sys.maxint/2048)

    global session
    session.shutdown()
    sleep(3)