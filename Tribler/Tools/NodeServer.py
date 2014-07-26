#!/usr/bin/python

import os
import sys
import time
import shlex
import pickle
import socket
import logging
import datetime
import threading
import xmlrpclib
import subprocess
import SimpleXMLRPCServer

from Tribler.Core.CacheDB.sqlitecachedb import bin2str, str2bin
from Tribler.Utilities.TimedTaskQueue import TimedTaskQueue

MAX_PEERS     = 150
STARTPORT     = 6000
TIME_INTERVAL = 30

class NodeServer:

    def __init__(self, host):
        self.host = host
        self.peers = {}
        self.superpeer_file = os.path.join(os.path.sep, 'home', 'ebouman', 'd11-07-08-socgames-from-release-5.3.x-21594', 'Tribler', 'Core', 'gc_superpeer.txt')
        self.acc_peer_uptime = 0
        self.nullfile = open(os.devnull, 'w')
        spfile = open(self.superpeer_file, 'w')
        spfile.write('')
        spfile.close()

    def start(self, name, superpeer, params):
        # Determine the port on which the peer should run.
        if name in self.peers.keys():
            port = self.peers[name]['port']
        elif len(self.peers) < MAX_PEERS:
            busy_ports = []
            for peer, info in self.peers.items():
                busy_ports.append(info['port'])
            free_ports = set(range(STARTPORT, STARTPORT+MAX_PEERS)) - set(busy_ports)
            try:
                port = free_ports.pop()
            except:
                pass
        else:
            return False
        file = open(os.path.join(os.getcwd(), os.path.pardir, name+'.stderr'), 'w')
        # Setting superpeer to True will cause issues with the :memory: database that cannot be shared across multiple threads.
        # Therefore, while testing, we will set superpeer to False (even if the peer is mentioned in gc_superpeer.txt).
        p = subprocess.Popen(['python', os.path.join(os.getcwd(), 'Tribler', 'Tools', 'NodeApp.py'), '--name', name, '--port', str(port), '--superpeer', str(True), '--superpeer_file', self.superpeer_file], \
                              shell = False, close_fds = True, stdin = subprocess.PIPE, stdout = subprocess.PIPE, stderr = file)#self.nullfile)#file)
        # Get the permid of the peer we just started.
        permid = pickle.load(p.stdout)
        self.peers[name] = {'superpeer':superpeer, 'port':port, 'process':p, 'permid':permid, 'start_time':time.time()}
        # Send the params to the peer.
        pickle.dump(params, p.stdin)
        p.stdin.flush()
        # Make sure that the gamecast superpeer file is updated.
        if superpeer:
            spfile = open(self.superpeer_file, "r")
            splines = spfile.readlines()
            spfile.close()
            exists = False
            index = 0
            while index < len(splines):
                line = splines[index]
                if line.strip().startswith("#"):
                    index += 1
                    continue
                items = [item.strip() for item in line.split(',')]
                if len(items) > 3 and items[3] == name:
                    exists = True
                    splines[index] = '%s, %d, %s, %s\n' % (self.host, port, bin2str(permid), name)
                index += 1
            spfile = open(self.superpeer_file, "w")
            if splines:
                spfile.writelines(splines)
            if not exists:
                spfile.write('%s, %d, %s, %s\n' % (self.host, port, bin2str(permid), name))
            spfile.close()
            print "Started superpeer %s on %s. Redirecting its output to %s." % (name, self.host, file.name)
        else:
            print "Started peer %s on %s. Redirecting its output to %s." % (name, self.host, file.name)
        return True

    def stop(self, name):
        if not self.peers.has_key(name):
            print "Unable to stop unknown peer (%s)" % name
            return False
        elif not self.peers[name]['process']:
            print "Unable to stop stopped peer (%s)" % name
            return False
        process = self.peers[name]['process']
        process.stdin.write('stop\n')
        process.stdin.flush()
        process.wait()
        uptime = time.time() - self.peers[name]['start_time']
        self.acc_peer_uptime += uptime
        self.peers[name]['process'] = None
        print "Stopped peer %s." % name
        return True

    def shell(self, cmd):
        try:
            retcode = subprocess.call(cmd, shell=True)
            if retcode == 0:
                print "Shell command '%s' executed successfully" % cmd
            else:
                print "Shell command '%s' returned: %d" % (cmd, retcode)
        except OSError, e:
            print "Shell command '%s' failed to execute" % cmd


class Unbuffered:

    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        self.stream.write(data)
        self.stream.flush()

    def __getattr__(self, attr):
        return getattr(self.stream, attr)



def traffic(interfaces):
    # Return the number of bytes that passed through the network interface(s) according to /proc/net/dev.
    if not isinstance(interfaces, list):
        interfaces = [interfaces]
    rx, tx = (0,0)
    for interface in interfaces:
        for line in open('/proc/net/dev', 'r'):
            data = line.replace(':', ' ').split()
            if data[0] == interface:
                rx += int(data[1])
                tx += int(data[9])
    return (rx, tx)

def main():
    sys.stdout      = Unbuffered(sys.stdout)

    num_rank        = int(sys.argv[1])
    num_proc        = int(sys.argv[2])

    all_hosts       = os.environ.get('PRUN_PE_HOSTS')
    all_hosts       = all_hosts.split()

    this_host       = socket.gethostname()
    this_ip         = socket.gethostbyname(this_host)

    start_time      = time.time()

    # Setup logging
    logger = logging.getLogger('nodeserver')
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(created).3f   %(event_type)-45s   %(message)s')
    fileHandler = logging.FileHandler(os.path.join(os.getcwd(), os.path.pardir, 'nodeserver%d.log' % num_rank))
    fileHandler.setFormatter(formatter)

    logger.addHandler(fileHandler)
    def log(*args, **kwargs):
        d = {'event_type'    : args[0]}
        msg = ''
        iterator = iter(sorted(kwargs.iteritems()))
        for key, value in iterator:
            msg += "%s = %s ; " % (key, value)
        if msg:
            msg = msg[:-3]
        logger.info(msg, extra=d)

    # Create a thread that periodically logs the load and the number of peers that are in existence.
    def periodicLogging():
        while True:
            # Wait
            sleeptime = TIME_INTERVAL - (time.time() % TIME_INTERVAL)
            time.sleep(sleeptime)
            # Log
            if not num_rank:
                unique_gc_peers = sum([len(value.get('peernames', []))+len(value.get('peernames_stopped', [])) for key, value in host_dict.iteritems()])
                online_gc_peers = sum([len(value.get('peernames', [])) for key, value in host_dict.iteritems()])
                log('GP_STATS', this_host=this_host, unique_gc_peers=unique_gc_peers, online_gc_peers=online_gc_peers)

            p = subprocess.Popen('LC_TIME="POSIX" sar -q 1 1 | grep -v Average | grep -v Linux | grep -v runq-sz | grep -v ^$', shell=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = p.communicate()
            runqsz, plistsz, ldavg1, ldavg5, ldavg15 = stdout.split()[-5:]
            numpeers = len([k for k, v in nodeserver.peers.iteritems() if v['process']])
            log('LD_STATS', this_host=this_host, runqsz=runqsz, plistsz=plistsz, ldavg1=ldavg1, ldavg5=ldavg5, ldavg15=ldavg15, numpeers=numpeers)
    logging_thread = threading.Thread(target=periodicLogging)
    logging_thread.setDaemon(True)

    # Startup the XMLRPC server
    print "Server is starting up on host %s (%s/%s).." % (this_host, num_rank, num_proc)
    nodeserver = NodeServer(this_host)
    server = SimpleXMLRPCServer.SimpleXMLRPCServer(('', 7000), allow_none=True, logRequests=0)
    server.register_instance(nodeserver)
    if num_rank:
        logging_thread.start()
        try:
            server.serve_forever()
        finally:
            server.server_close()
    else:
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.setDaemon(True)
        server_thread.start()

    # Startup the manager on the node with rank 0.
    if not num_rank:
        time.sleep(10)
        print "Manager is starting up on host %s.." % this_host
        locations = [{'location':'http://%s:7000' % host} for host in all_hosts]
        host_dict = dict(zip(all_hosts, locations))
        for host, info in host_dict.items():
            info['peernames'] = []
            info['peernames_stopped'] = []
        logging_thread.start()

        def chooseHost(peername):
            orig_node = None
            for host, info in host_dict.items():
                if peername in info['peernames_stopped']:
                    orig_node = host
                    if len(info['peernames']) < MAX_PEERS:
                        return orig_node
            winner = (MAX_PEERS, None)
            for host, info in host_dict.items():
                if len(info['peernames']) < winner[0]:
                    winner = (len(info['peernames']), host)
            if winner[1] and orig_node:
                # Move the state-dir
                p = subprocess.Popen('ssh %s "scp -Cr /local/ebouman/.%s %s:/local/ebouman/.%s; rm /local/ebouman/.%s"' % (orig_node, peername, winner[1], peername, peername),
                                     shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = p.communicate()
                host_dict[orig_node]['peernames_stopped'].remove(peername)
                host_dict[winner[1]]['peernames_stopped'].append(peername)
                print "Transferred state-dir of peer %s from node %s to node %s" % (peername, orig_node, winner[1])
            return winner[1]

        def peerToHost(peername):
            for host, info in host_dict.items():
                if peername in info['peernames']:
                    return host
            return None

        def hostToProxy(hostname):
            if host_dict.has_key(hostname):
                if not host_dict[hostname].has_key('proxy'):
                    host_dict[hostname]['proxy'] = xmlrpclib.ServerProxy(host_dict[hostname]['location'])
                return host_dict[hostname]['proxy']
            else:
                print "No proxy could be found, defaulting to proxy on master node."
                return xmlrpclib.ServerProxy('http://%s:7000' % this_host)
                #return None

        texec = 0
        cmdfile = open(os.path.join(os.getcwd(), 'scenario.test'), "r")
        for cmd in cmdfile:
            if cmd.strip().startswith('#'):
                continue
            text = shlex.split(cmd)
            if not text:
                continue

            # ----- Start/stop commands -----
            if text[0] == "exec":
                if text[2] in ["start", "starts"]:
                    if (text[2] == "starts" and len(text) != 3) or (text[2] == "start" and len(text) != 4):
                        print "Invalid args", text
                        continue
                    host = peerToHost(text[1])
                    if not host:
                        host = chooseHost(text[1])
                        if not host:
                            print 'Error executing start for', text[1], 'because all nodes are busy.'
                            continue
                    proxy = hostToProxy(host)
                    isSuperpeer = (text[2] == 'starts')
                    params = [int(text[3])] if len(text) == 4 else []
                    tstart = time.time()
                    try:
                        if proxy.start(text[1], isSuperpeer, params):
                            host_dict[host]['peernames'].append(text[1])
                            if text[1] in host_dict[host]['peernames_stopped']:
                                host_dict[host]['peernames_stopped'].remove(text[1])
                    except:
                        print 'Error executing start for', text[1], 'on', host
                    texec += time.time() - tstart
                elif text[2] == "stop":
                    if len(text) != 3:
                        print "Invalid args"
                        continue
                    host = peerToHost(text[1])
                    if not host:
                        print 'Error executing stop for', text[1], 'because it does not appear to be running.'
                        continue
                    proxy = hostToProxy(host)
                    tstart = time.time()
                    try:
                        if proxy.stop(text[1]):
                            host_dict[host]['peernames'].remove(text[1])
                            host_dict[host]['peernames_stopped'].append(text[1])
                    except:
                        print 'Error executing stop for', text[1], 'on', host
                    texec += time.time() - tstart

            # ----- Special commands -----
            elif text[0] == "wait":
                if len(text) != 2:
                    print "Invalid args"
                    continue
                tosleep = float(text[1])
                correct = texec if texec < tosleep else tosleep
                tosleep -= correct
                texec -= correct
                time.sleep(tosleep)
            elif text[0] == "shell":
                if len(text) != 3:
                    print "Invalid args"
                    continue
                host = peerToHost(text[1])
                proxy = hostToProxy(host)
                proxy.shell(text[2])
            else:
                print "Invalid command"

if __name__ == "__main__":
    main()
