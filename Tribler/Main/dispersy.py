#!/usr/bin/python

"""
Run Dispersy in standalone mode.  Tribler will not be started.
"""

import errno
import socket
import sys
import time
import traceback
import threading
import optparse

# from Tribler.Community.Discovery.Community import DiscoveryCommunity
from Tribler.Core.BitTornado.RawServer import RawServer
from Tribler.Core.dispersy.callback import Callback
from Tribler.Core.dispersy.dispersy import Dispersy

if __debug__:
    from Tribler.Core.dispersy.dprint import dprint

if sys.platform == 'win32':
    SOCKET_BLOCK_ERRORCODE = 10035    # WSAEWOULDBLOCK
else:
    SOCKET_BLOCK_ERRORCODE = errno.EWOULDBLOCK

def main():
    class DispersySocket(object):
        def __init__(self, rawserver, dispersy, port, ip="0.0.0.0"):
            while True:
                if __debug__: dprint("Dispersy listening at ", port)
                try:
                    self.socket = rawserver.create_udpsocket(port, ip)
                except socket.error, error:
                    port += 1
                    continue
                break
            self.rawserver = rawserver
            self.rawserver.start_listening_udp(self.socket, self)
            self.dispersy = dispersy

        def get_address(self):
            return self.socket.getsockname()

        def data_came_in(self, packets):
            # called on the Tribler rawserver

            # the rawserver SUCKS.  every now and then exceptions are not shown and apparently we are
            # sometimes called without any packets...
            if packets:
                try:
                    self.dispersy.data_came_in(packets)
                except:
                    traceback.print_exc()
                    raise

        def send(self, address, data):
            try:
                self.socket.sendto(data, address)
            except socket.error, error:
                if error[0] == SOCKET_BLOCK_ERRORCODE:
                    self.sendqueue.append((data, address))
                    self.rawserver.add_task(self.process_sendqueue, 0.1)

    def on_fatal_error(error):
        print >> sys.stderr, "Rawserver fatal error:", error
        global exit_exception
        exit_exception = error
        session_done_flag.set()

    def on_non_fatal_error(error):
        print >> sys.stderr, "Rawserver non fatal error:", error

    def start():
        # start Dispersy
        dispersy = Dispersy.get_instance(callback, opt.statedir)
        dispersy.socket = DispersySocket(rawserver, dispersy, opt.port, opt.ip)

        # load the script parser
        if opt.script:
            from Tribler.Core.dispersy.script import Script
            script = Script.get_instance(callback)

            if not opt.disable_dispersy_script:
                from Tribler.Core.dispersy.script import DispersyClassificationScript, DispersyTimelineScript, DispersyCandidateScript, DispersyDestroyCommunityScript, DispersyBatchScript, DispersySyncScript, DispersySubjectiveSetScript, DispersySignatureScript, DispersyMemberTagScript
                script.add("dispersy-classification", DispersyClassificationScript)
                script.add("dispersy-timeline", DispersyTimelineScript)
                script.add("dispersy-candidate", DispersyCandidateScript)
                script.add("dispersy-destroy-community", DispersyDestroyCommunityScript)
                script.add("dispersy-batch", DispersyBatchScript)
                script.add("dispersy-sync", DispersySyncScript)
                # script.add("dispersy-similarity", DispersySimilarityScript)
                script.add("dispersy-signature", DispersySignatureScript)
                script.add("dispersy-member-tag", DispersyMemberTagScript)
                script.add("dispersy-subjective-set", DispersySubjectiveSetScript)

            if not opt.disable_simple_dispersy_test_script:
                from Tribler.community.simpledispersytest.script import GenerateMessageBatchScript, GenerateMessagesScript, KillCommunityScript
                script.add("simpledispersytest-generate-batch", GenerateMessageBatchScript, include_with_all=False)
                script.add("simpledispersytest-generate-messages", GenerateMessagesScript, include_with_all=False)
                script.add("simpledispersytest-destroy-community", KillCommunityScript, include_with_all=False)

            # if not opt.disable_allchannel_script:
            #     from Tribler.Community.allchannel.script import AllChannelScript
            #     script = Script.get_instance(dispersy_rawserver)
            #     script.add("allchannel", AllChannelScript, include_with_all=False)

            #     from Tribler.Community.allchannel.script import AllChannelScenarioScript
            #     args = {}
            #     if opt.script_args:
            #         for arg in opt.script_args.split(','):
            #             key, value = arg.split('=')
            #             args[key] = value

            #     script.add("allchannel-scenario", AllChannelScenarioScript, args, include_with_all=False)

            # if not opt.disable_barter_script:
            #     from Tribler.Community.barter.script import BarterScript, BarterScenarioScript

            #     args = {}
            #     if opt.script_args:
            #         for arg in opt.script_args.split(','):
            #             key, value = arg.split('=')
            #             args[key] = value

            #     script.add("barter", BarterScript)
            #     script.add("barter-scenario", BarterScenarioScript, args, include_with_all=False)

            # # bump the rawserver, or it will delay everything... since it sucks.
            # def bump():
            #     pass
            # rawserver.add_task(bump)

            script.load(opt.script)

    command_line_parser = optparse.OptionParser()
    command_line_parser.add_option("--statedir", action="store", type="string", help="Use an alternate statedir", default=u".")
    command_line_parser.add_option("--ip", action="store", type="string", default="0.0.0.0", help="Dispersy uses this ip")
    command_line_parser.add_option("--port", action="store", type="int", help="Dispersy uses this UDL port", default=12345)
    command_line_parser.add_option("--timeout-check-interval", action="store", type="float", default=1.0)
    command_line_parser.add_option("--timeout", action="store", type="float", default=300.0)
    # command_line_parser.add_option("--disable-allchannel-script", action="store_true", help="Include allchannel scripts", default=False)
    # command_line_parser.add_option("--disable-barter-script", action="store_true", help="Include barter scripts", default=False)
    command_line_parser.add_option("--disable-simple-dispersy-test-script", action="store_true", help="Include simple-dispersy-test scripts", default=False)
    command_line_parser.add_option("--disable-dispersy-script", action="store_true", help="Include dispersy scripts", default=False)
    command_line_parser.add_option("--script", action="store", type="string", help="Runs the Script python file with <SCRIPT> as an argument")
    command_line_parser.add_option("--script-args", action="store", type="string", help="Executes --script with these arguments.  Example 'startingtimestamp=1292333014,endingtimestamp=12923340000'")

    # parse command-line arguments
    opt, args = command_line_parser.parse_args()
    print "Press Ctrl-C to stop Dispersy"

    # start threads
    session_done_flag = threading.Event()
    rawserver = RawServer(session_done_flag, opt.timeout_check_interval, opt.timeout, False, failfunc=on_fatal_error, errorfunc=on_non_fatal_error)
    callback = Callback()
    callback.start(name="Dispersy")
    callback.register(start)

    def watchdog():
        while True:
            try:
                yield 333.3
            except GeneratorExit:
                rawserver.shutdown()
                session_done_flag.set()
                break
    callback.register(watchdog)
    rawserver.listen_forever(None)
    callback.stop()

if __name__ == "__main__":
    exit_exception = None
    main()
    if exit_exception:
        raise exit_exception
