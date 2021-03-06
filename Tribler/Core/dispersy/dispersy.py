# Python 2.5 features
from __future__ import with_statement

"""
The Distributed Permission System, or Dispersy, is a platform to simplify the design of distributed
communities.  At the heart of Dispersy lies a simple identity and message handling system where each
community and each user is uniquely and securely identified using elliptic curve cryptography.

Since we can not guarantee each member to be online all the time, messages that they created at one
point in time should be able to retain their meaning even when the member is off-line.  This can be
achieved by signing such messages and having them propagated though other nodes in the network.
Unfortunately, this increases the strain on these other nodes, which we try to alleviate using
specific message policies, which will be described below.

Following from this, we can easily package each message into one UDP packet to simplify
connectability problems since UDP packets are much easier to pass though NAT's and firewalls.

Earlier we hinted that messages can have different policies.  A message has the following four
different policies, and each policy defines how a specific part of the message should be handled.

 - Authentication defines if the message is signed, and if so, by how many members.

 - Resolution defines how the permission system should resolve conflicts between messages.

 - Distribution defines if the message is send once or if it should be gossipped around.  In the
   latter case, it can also define how many messages should be kept in the network.

 - Destination defines to whom the message should be send or gossipped.

To ensure that every node handles a messages in the same way, i.e. has the same policies associated
to each message, a message exists in two stages.  The meta-message and the implemented-message
stage.  Each message has one meta-message associated to it and tells us how the message is supposed
to be handled.  When a message is send or received an implementation is made from the meta-message
that contains information specifically for that message.  For example: a meta-message could have the
member-authentication-policy that tells us that the message must be signed by a member but only the
an implemented-message will have data and this signature.

A community can tweak the policies and how they behave by changing the parameters that the policies
supply.  Aside from the four policies, each meta-message also defines the community that it is part
of, the name it uses as an internal identifier, and the class that will contain the payload.
"""

from sys import maxint
from hashlib import sha1
from itertools import groupby, islice
from os.path import abspath
from threading import Lock

from authentication import NoAuthentication, MemberAuthentication, MultiMemberAuthentication
from bloomfilter import BloomFilter
from callback import Callback
from candidate import Candidate
from crypto import ec_generate_key, ec_to_public_bin, ec_to_private_bin
from destination import CommunityDestination, AddressDestination, MemberDestination, SubjectiveDestination, SimilarityDestination
from dispersydatabase import DispersyDatabase
from distribution import SyncDistribution, FullSyncDistribution, LastSyncDistribution, DirectDistribution
from member import PrivateMember, MasterMember
from message import Message
from message import DropPacket, DelayPacket
from message import DropMessage, DelayMessage, DelayMessageBySequence, DelayMessageBySubjectiveSet
from payload import AuthorizePayload, RevokePayload
from payload import MissingSequencePayload
from payload import SyncPayload
from payload import SignatureRequestPayload, SignatureResponsePayload
from payload import CandidateRequestPayload, CandidateResponsePayload
from payload import IdentityPayload, IdentityRequestPayload
from payload import SubjectiveSetPayload, SubjectiveSetRequestPayload
from payload import SimilarityRequestPayload, SimilarityPayload
from payload import DestroyCommunityPayload
from resolution import PublicResolution, LinearResolution
from singleton import Singleton
from trigger import TriggerCallback, TriggerPacket, TriggerMessage

if __debug__:
    from dprint import dprint
    from time import clock

class DummySocket(object):
    """
    A dummy socket class.

    When Dispersy starts it does not yet have a socket object, however, it may (under certain
    conditions) start sending packets anyway.

    To avoid problems we initialize the Dispersy socket to this dummy object that will do nothing
    but throw away all packets it is supposed to sent.
    """
    def send(address, data):
        if __debug__: dprint("Thrown away ", len(data), " bytes worth of outgoing data")

class Statistics(object):
    def __init__(self):
        self._drop = {}
        self._delay = {}
        self._success = {}
        self._sequence_number = 0

    def reset(self):
        """
        Returns, and subsequently removes, all statistics.
        """
        try:
            return {"drop":self._drop, "delay":self._delay, "success":self._success, "sequence_number":self._sequence_number}

        finally:
            self._drop = {}
            self._delay = {}
            self._success = {}
            self._sequence_number += 1

    def drop(self, key, bytes, count=1):
        """
        Called when an incoming packet or message failed a check and was dropped.
        """
        assert isinstance(key, (str, unicode))
        assert isinstance(bytes, (int, long))
        assert isinstance(count, (int, long))
        a, b = self._drop.get(key, (0, 0))
        self._drop[key] = (a+count, b+bytes)

    def delay(self, key, bytes, count=1):
        """
        Called when an incoming packet or message was delayed.
        """
        assert isinstance(key, (str, unicode))
        assert isinstance(bytes, (int, long))
        assert isinstance(count, (int, long))
        a, b = self._delay.get(key, (0, 0))
        self._delay[key] = (a+count, b+bytes)

    def success(self, key, bytes, count=1):
        """
        Called when an incoming message was accepted.
        """
        assert isinstance(key, (str, unicode))
        assert isinstance(bytes, (int, long))
        assert isinstance(count, (int, long))
        a, b = self._success.get(key, (0, 0))
        self._success[key] = (a+count, b+bytes)

class Dispersy(Singleton):
    """
    The Dispersy class provides the interface to all Dispersy related commands, managing the in- and
    outgoing data for, possibly, multiple communities.
    """
    def __init__(self, callback, working_directory):
        """
        Initialize the Dispersy singleton instance.

        Currently we use the rawserver to schedule events.  This may change in the future to offload
        all data processing to a different thread.  The only mechanism used from the rawserver is
        the add_task method.

        @param callback: Object for callback scheduling.
        @type rawserver: Callback

        @param working_directory: The directory where all files should be stored.
        @type working_directory: unicode
        """
        # the raw server
        self._callback = callback
        self._callback.register(self._watchdog)

        # batch caching incoming packets
        self._batch_cache = {}
        if __debug__:
            self._debug_batch_cache_performance = {}

        # where we store all data
        self._working_directory = abspath(working_directory)

        # our data storage
        self._database = DispersyDatabase.get_instance(working_directory)

        # our external address
        try:
            host, = self._database.execute(u"SELECT value FROM option WHERE key = 'my_external_ip' LIMIT 1").next()
            host = str(host)
        except StopIteration:
            host = "0.0.0.0"

        try:
            port, = self._database.execute(u"SELECT value FROM option WHERE key = 'my_external_port' LIMIT 1").next()
        except StopIteration:
            port = 0

        if __debug__: dprint("my external address is ", host, ":", port)
        self._my_external_address = (host, port)
        self._external_address_votes = {self._my_external_address:set()}
        self.external_address_vote(self._my_external_address, ("", -1))

#         try:
#             public_key, = self._database.execute(u"SELECT value FROM option WHERE key == 'my_public_key' LIMIT 1").next()
#             public_key = str(public_key)
#             private_key = None
#         except StopIteration:
#             # one of the keys was not found in the database, we need
#             # to generate a new one
#             ec = ec_generate_key(u"low")
#             public_key = ec_to_public_bin(ec)
#             private_key = ec_to_private_bin(ec)
#             self._database.execute(u"INSERT INTO option VALUES('my_public_key', ?)", (buffer(public_key),))

        # all available communities.  cid:Community pairs.
        self._communities = {}

        # outgoing communication
        self._socket = DummySocket()

        # triggers for incoming messages
        self._triggers = []

        self._check_distribution_batch_map = {DirectDistribution:self._check_direct_distribution_batch,
                                              FullSyncDistribution:self._check_full_sync_distribution_batch,
                                              LastSyncDistribution:self._check_last_sync_distribution_batch}


        # cleanup the database periodically
        self._callback.register(self._periodically_cleanup_database, delay=120.0)

        # statistics...
        self._statistics = Statistics()
        if __debug__:
            self._total_send = 0
            self._total_received = 0
            self._callback.register(self._periodically_stats, delay=1.0)

    @property
    def working_directory(self):
        """
        The full directory path where all dispersy related files are stored.
        @rtype: unicode
        """
        return self._working_directory

    # @property
    def __get_socket(self):
        """
        The socket object used to send packets.
        @rtype: Object with a send(address, data) method
        """
        return self._socket
    # @socket.setter
    def __set_socket(self, socket):
        """
        Set a socket object.
        @param socket: The socket object.
        @type socket: Object with a send(address, data) method
        """
        self._socket = socket
        self.external_address_vote(socket.get_address(), ("", -1))
    # .setter was introduced in Python 2.6
    socket = property(__get_socket, __set_socket)

    @property
    def external_address(self):
        """
        The external address where we believe that we can be found.

        Our external address is determined by majority voting.  Each time when we receive a message
        that contains anothers opinion about our external address, we take this into account.  The
        address with the most votes wins.

        Votes can be added by calling the external_address_vote(...) method.

        Usually these votes are received through dispersy-candidate-request and
        dispersy-candidate-response messages.

        @rtype: (str, int)
        """
        return self._my_external_address

    @property
    def rawserver(self):
        import sys
        print >> sys.stderr, "Depricated: Dispersy.rawserver.  Use Dispersy.callback instead"
        return self._callback

    @property
    def callback(self):
        return self._callback

    @property
    def database(self):
        """
        The Dispersy database singleton.
        @rtype: DispersyDatabase
        """
        return self._database

    def initiate_meta_messages(self, community):
        """
        Create the meta messages that Dispersy uses.

        This method is called once for each community when it is created.  The resulting meta
        messages can be obtained by either community.get_meta_message(name) or
        community.get_meta_messages().

        Since these meta messages will be used along side the meta messages that each community
        provides, all message names are prefixed with 'dispersy-' to ensure that the names are
        unique.

        @param community: The community that will get the messages.
        @type community: Community

        @return: The new meta messages.
        @rtype: [Message]
        """
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        return [Message(community, u"dispersy-candidate-request", MemberAuthentication(), PublicResolution(), DirectDistribution(), AddressDestination(), CandidateRequestPayload(), self.check_candidate_request, self.on_candidate_request, delay=0.0),
                Message(community, u"dispersy-candidate-response", MemberAuthentication(), PublicResolution(), DirectDistribution(), AddressDestination(), CandidateResponsePayload(), self.check_candidate_response, self.on_candidate_response, delay=2.5),
                Message(community, u"dispersy-identity", MemberAuthentication(encoding="bin"), PublicResolution(), LastSyncDistribution(enable_sequence_number=False, synchronization_direction=u"in-order", history_size=1), CommunityDestination(node_count=10), IdentityPayload(), self.check_identity, self.on_identity, priority=512, delay=1.0),
                Message(community, u"dispersy-identity-request", NoAuthentication(), PublicResolution(), DirectDistribution(), AddressDestination(), IdentityRequestPayload(), self.check_identity_request, self.on_identity_request, delay=0.0),
                Message(community, u"dispersy-sync", MemberAuthentication(), PublicResolution(), DirectDistribution(), CommunityDestination(node_count=community.dispersy_sync_member_count), SyncPayload(), self.check_sync, self.on_sync, delay=0.0),
                Message(community, u"dispersy-missing-sequence", NoAuthentication(), PublicResolution(), DirectDistribution(), AddressDestination(), MissingSequencePayload(), self.check_missing_sequence, self.on_missing_sequence, delay=0.0),
                Message(community, u"dispersy-signature-request", NoAuthentication(), PublicResolution(), DirectDistribution(), MemberDestination(), SignatureRequestPayload(), self.check_signature_request, self.on_signature_request, delay=0.0),
                Message(community, u"dispersy-signature-response", NoAuthentication(), PublicResolution(), DirectDistribution(), AddressDestination(), SignatureResponsePayload(), self.check_signature_response, self.on_signature_response, delay=0.0),
#                 Message(community, u"dispersy-similarity", MemberAuthentication(), PublicResolution(), LastSyncDistribution(enable_sequence_number=False, synchronization_direction=u"in-order", history_size=1), CommunityDestination(node_count=10), SimilarityPayload(), self.check_similarity, self.on_similarity, delay=0.0),
#                 Message(community, u"dispersy-similarity-request", NoAuthentication(), PublicResolution(), DirectDistribution(), AddressDestination(), SimilarityRequestPayload(), self.check_similarity_request, self.on_similarity_request, delay=0.0),
                Message(community, u"dispersy-authorize", MemberAuthentication(), PublicResolution(), FullSyncDistribution(enable_sequence_number=True, synchronization_direction=u"in-order"), CommunityDestination(node_count=10), AuthorizePayload(), self.check_authorize, self.on_authorize, priority=504, delay=1.0),
                Message(community, u"dispersy-revoke", MemberAuthentication(), PublicResolution(), FullSyncDistribution(enable_sequence_number=True, synchronization_direction=u"in-order"), CommunityDestination(node_count=10), RevokePayload(), self.check_revoke, self.on_revoke, priority=504, delay=1.0),
                Message(community, u"dispersy-destroy-community", MemberAuthentication(), LinearResolution(), FullSyncDistribution(enable_sequence_number=False, synchronization_direction=u"in-order"), CommunityDestination(node_count=50), DestroyCommunityPayload(), self.check_destroy_community, self.on_destroy_community, delay=0.0),
                Message(community, u"dispersy-subjective-set", MemberAuthentication(), PublicResolution(), LastSyncDistribution(enable_sequence_number=False, synchronization_direction=u"in-order", history_size=1), CommunityDestination(node_count=10), SubjectiveSetPayload(), self.check_subjective_set, self.on_subjective_set, delay=1.0),
                Message(community, u"dispersy-subjective-set-request", NoAuthentication(), PublicResolution(), DirectDistribution(), AddressDestination(), SubjectiveSetRequestPayload(), self.check_subjective_set_request, self.on_subjective_set_request, delay=0.0)]

    @staticmethod
    def _rawserver_task_id(community, prefix):
        """
        Periodically sending sync and candidate messages requires a rawserver tast to be identified
        per community.  To ensure that each community is uniquely idntified we use both the
        community id and the memory address id(community).

        This may still give problems when detaching and attaching (i.e. because of a reclassify).
        But it is the best we can do at this point.
        """
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert isinstance(prefix, str)
        return "-".join((prefix, str(id(community)), community.cid))

    def attach_community(self, community):
        """
        Add a community to the Dispersy instance.

        Each community must be known to Dispersy, otherwise an incoming message will not be able to
        be passed along to it's associated community.

        In general this method is called from the Community.__init__(...) method.

        @param community: The community that will be added.
        @type community: Community
        """
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert not community.cid in self._communities
        if __debug__: dprint(community.cid.encode("HEX"), " ", community.get_classification())
        self._communities[community.cid] = community

        # periodically send dispery-sync messages
        if __debug__: dprint("start in ", community.dispersy_sync_initial_delay, " every ", community.dispersy_sync_interval, " seconds call _periodically_create_sync")
        self._callback.register(self._periodically_create_sync, (community,), delay=community.dispersy_sync_initial_delay, id_=self._rawserver_task_id(community, "id:sync"))

        # periodically send dispery-candidate-request messages
        if __debug__: dprint("start in ", community.dispersy_candidate_request_initial_delay, " every ", community.dispersy_candidate_request_interval, " seconds call _periodically_create_candidate_request")
        self._callback.register(self._periodically_create_candidate_request, (community,), delay=community.dispersy_candidate_request_initial_delay, id_=self._rawserver_task_id(community, "id:candidate"))

    def detach_community(self, community):
        """
        Remove an attached community from the Dispersy instance.

        Once a community is detached it will no longer receive incoming messages.  When the
        community is marked as auto_load it will be loaded, using community.load_community(...),
        when a message for this community is received.

        @param community: The community that will be added.
        @type community: Community
        """
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert community.cid in self._communities
        if __debug__: dprint(community.cid.encode("HEX"), " ", community.get_classification())
        self._callback.unregister(self._rawserver_task_id(community, "id:sync"))
        self._callback.unregister(self._rawserver_task_id(community, "id:candidate"))
        del self._communities[community.cid]

    def reclassify_community(self, community, destination):
        """
        Change a community classification.

        Each community has a classification that dictates what source code is handling this
        community.  By default the classification of a community is the unicode name of the class in
        the source code.

        In some cases it may be usefull to change the classification, for instance: if community A
        has a subclass community B, where B has similar but reduced capabilities, we could
        reclassify B to A at some point and keep all messages collected so far while using the
        increased capabilities of community A.

        @param community: The community that will be reclassified.  This must be either a Community
         instance (when the community is loaded) or a 20 byte cid (when the community is not
         loaded).
        @type community: Community or str

        @param destination: The new community classification.  This must be a Community class.
        @type destination: Community class
        """
        if __debug__:
            from community import Community
        assert isinstance(community, (str, Community))
        assert issubclass(destination, Community)

        if isinstance(community, str):
            assert len(community) == 20
            assert not community in self._communities
            if __debug__: dprint(community.encode("HEX"), "??? -> ", destination.get_classification())
            cid = community
        else:
            if __debug__: dprint(community.cid.encode("HEX"), " ", community.get_classification(), " -> ", destination.get_classification())
            cid = community.cid
            community.unload_community()

        self._database.execute(u"UPDATE community SET classification = ? WHERE cid = ?", (destination.get_classification(), buffer(cid)))
        assert self._database.changes == 1
        return destination.load_community(cid, "")

    def has_community(self, cid):
        """
        Returns True when there is a community CID.
        """
        return cid in self._communities

    def get_community(self, cid, load=False, auto_load=True):
        """
        Returns a community by its community id.

        The community id, or cid, is the binary representation of the public key of the master
        member for the community.

        When the community is available but not currently loaded it will be automatically loaded
        when (a) the load parameter is True or (b) the auto_load parameter is True and the auto_load
        flag for this community is True (this flag is set in the database).

        @param cid: The community identifier.
        @type cid: string

        @param load: When True, will load the community when available and not yet loaded.
        @type load: bool

        @param auto_load: When True, will load the community when available, the auto_load flag is
         True, and, not yet loaded.
        @type load: bool

        @warning: It is possible, however unlikely, that multiple communities will have the same
         cid.  This is currently not handled.
        """
        assert isinstance(cid, str)
        assert len(cid) == 20
        assert isinstance(load, bool)
        assert isinstance(auto_load, bool)

        if not cid in self._communities:
            try:
                # did we load this community at one point and set it to auto-load?
                classification, public_key, auto_load_flag = self._database.execute(u"SELECT classification, public_key, auto_load FROM community WHERE cid = ?",
                                                                                    (buffer(cid),)).next()

            except StopIteration:
                pass

            else:
                if load or (auto_load and auto_load_flag):

                    def recursive_subclasses(cls):
                        l = set()
                        for subcls in cls.__subclasses__():
                            l.add(subcls)
                            l.update(recursive_subclasses(subcls))
                        return l

                    # todo: get some other mechanism to obtain the class from classification
                    from community import Community

                    public_key = str(public_key)
                    # attempt to load this community
                    for cls in recursive_subclasses(Community):
                        if classification == cls.get_classification():
                            self._communities[cid] = cls.load_community(cid, public_key)
                            break

                    else:
                        if __debug__: dprint("Failed to obtain class [", classification, "]", level="warning")

        return self._communities[cid]

    def get_communities(self):
        """
        Returns a list with all known Community instances.
        """
        return self._communities.values()

    def external_address_vote(self, address, voter_address):
        """
        Add one vote and possibly re-determine our external address.

        Our external address is determined by majority voting.  Each time when we receive a message
        that contains anothers opinion about our external address, we take this into account.  The
        address with the most votes wins.

        Usually these votes are received through dispersy-candidate-request and
        dispersy-candidate-response messages.

        @param address: The external address that the voter believes us to have.
        @type address: (str, int)

        @param voter_address: The address of the voter.
        @type voter_address: (str, int)
        """
        assert isinstance(address, tuple)
        assert len(address) == 2
        assert isinstance(address[0], str)
        assert isinstance(address[1], int)
        assert isinstance(voter_address, tuple)
        assert len(voter_address) == 2
        assert isinstance(voter_address[0], str)
        assert isinstance(voter_address[1], int)
        if self._is_valid_external_address(address):
            if not address in self._external_address_votes:
                self._external_address_votes[address] = set()
            self._external_address_votes[address].add(voter_address)

            # change when new vote count equal or higher than old address vote count
            if self._my_external_address != address and len(self._external_address_votes[address]) >= len(self._external_address_votes[self._my_external_address]):
                if __debug__: dprint("Update my external address: ", self._my_external_address, " -> ", address)
                self._my_external_address = address
                self._database.execute(u"REPLACE INTO option (key, value) VALUES ('my_external_ip', ?)", (unicode(address[0]),))
                self._database.execute(u"REPLACE INTO option (key, value) VALUES ('my_external_port', ?)", (address[1],))

                # notify all communities that our external address has changed.
                for community in self._communities.itervalues():
                    community.create_dispersy_identity()

    def _check_full_sync_distribution_batch(self, messages):
        """
        Ensure that we do not yet have the messages and that, if sequence numbers are enabled, we
        are not missing any previous messages.

        This method is called when a batch of messages with the FullSyncDistribution policy is
        received.  Duplicate messages will yield DropMessage.  And if enable_sequence_number is
        True, missing messages will yield the DelayMessageBySequence exception.

        @param messages: The messages that are to be checked.
        @type message: [Message.Implementation]

        @return: A generator with messages, DropMessage, or DelayMessageBySequence instances
        @rtype: [Message.Implementation|DropMessage|DelayMessageBySequence]
        """
        assert isinstance(messages, list)
        assert len(messages) > 0
        assert not filter(lambda x: not isinstance(x, Message.Implementation), messages)
        assert not filter(lambda x: not x.community == messages[0].community, messages), "All messages need to be from the same community"
        assert not filter(lambda x: not x.meta == messages[0].meta, messages), "All messages need to have the same meta"

        # a message is considered unique when (creator, global-time), i.r. (authentication.member,
        # distribution.global_time), is unique.
        unique = set()
        execute = self._database.execute
        enable_sequence_number = messages[0].meta.distribution.enable_sequence_number

        # sort the messages by their (1) global_time and (2) binary packet
        messages = sorted(messages, lambda a, b: a.distribution.global_time - b.distribution.global_time or cmp(a.packet, b.packet))

        if enable_sequence_number:
            # obtain the highest sequence_number from the database
            highest = {}
            for message in messages:
                if not message.authentication.member in highest:
                    try:
                        seq, = execute(u"SELECT distribution_sequence FROM sync WHERE community = ? AND user = ? AND sync.name = ? ORDER BY distribution_sequence DESC LIMIT 1",
                                       (message.community.database_id, message.authentication.member.database_id, message.database_id)).next()
                    except StopIteration:
                        seq = 0
                    highest[message.authentication.member] = seq

            # all messages must follow the sequence_number order
            for message in messages:
                key = (message.authentication.member, message.distribution.global_time)
                if key in unique:
                    yield DropMessage(message, "duplicate message by member^global_time (1)")

                else:
                    unique.add(key)
                    seq = highest[message.authentication.member]

                    if seq >= message.distribution.sequence_number:
                        # we already have this message (drop)
                        yield DropMessage(message, "duplicate message by sequence_number")

                    elif seq + 1 == message.distribution.sequence_number:
                        # we have the previous message, check for duplicates based on community, user,
                        # and global_time
                        try:
                            execute(u"SELECT 1 FROM sync WHERE community = ? AND user = ? AND global_time = ?",
                                    (message.community.database_id, message.authentication.member.database_id, message.distribution.global_time)).next()

                        except StopIteration:
                            # we accept this message
                            highest[message.authentication.member] += 1
                            yield message

                        else:
                            # we have the previous message (drop)
                            yield DropMessage(message, "duplicate message by global_time (1)")

                    else:
                        # we do not have the previous message (delay and request)
                        yield DelayMessageBySequence(message, seq+1, message.distribution.sequence_number-1)

        else:
            for message in messages:
                key = (message.authentication.member, message.distribution.global_time)
                if key in unique:
                    yield DropMessage(message, "duplicate message by member^global_time (2)")

                else:
                    unique.add(key)

                    # check for duplicates based on community, user, and global_time
                    try:
                        execute(u"SELECT 1 FROM sync WHERE community = ? AND user = ? AND global_time = ?",
                                (message.community.database_id, message.authentication.member.database_id, message.distribution.global_time)).next()

                    except StopIteration:
                        # we accept this message
                        yield message

                    else:
                        # we have the previous message (drop)
                        yield DropMessage(message, "duplicate message by global_time (2)")

    def _check_last_sync_distribution_batch(self, messages):
        """
        Check that the messages do not violate any database consistency rules.

        This method is called when a batch of messages with the LastSyncDistribution policy is
        received.  A iterator will be returned where each element is either: DropMessage (for
        duplicate and old messages), DelayMessage (for messages that requires something before they
        can be processed), or Message.Implementation when the message does not violate any rules.

        The rules:

         - The combination community, member, global_time must be unique.

         - When sequence numbers are enabled, the message with the previous sequence number must be
           present (either in MESSAGES or in the database).

         - When the MemberAuthentication policy is used: the message owner may not have more than
           history_size messages in the database at any one time.  Hence, if this limit is reached
           and the new message is older than the older message that is already available, it is
           dropped.

         - When the MultiMemberAuthentication policy is used: the members that signed the message
           may not have more than history_size messages in the database at any one time.  Hence, if
           this limit is reached and the new message is older than the older message that is already
           available, it is dropped.  Note that the signature order is not important.

        @param messages: The messages that are to be checked.
        @type message: [Message.Implementation]

        @return: A generator with messages, DropMessage, or DelayMessageBySequence instances
        @rtype: [Message.Implementation|DropMessage|DelayMessageBySequence]
        """
        assert isinstance(messages, list)
        assert len(messages) > 0
        assert not filter(lambda x: not isinstance(x, Message.Implementation), messages)
        assert not filter(lambda x: not x.community == messages[0].community, messages), "All messages need to be from the same community"
        assert not filter(lambda x: not x.meta == messages[0].meta, messages), "All messages need to have the same meta"
        assert not filter(lambda x: not isinstance(x.authentication, (MemberAuthentication.Implementation, MultiMemberAuthentication.Implementation)), messages)

        def check_member_and_global_time(unique, times, message):
            """
            The user + global_time combination must always be unique in the database
            """
            assert isinstance(unique, set)
            assert isinstance(times, dict)
            assert isinstance(message, Message.Implementation)
            assert isinstance(message.distribution, LastSyncDistribution.Implementation)

            key = (message.authentication.member, message.distribution.global_time)
            if key in unique:
                return DropMessage(message, "already processed message by member^global_time")

            else:
                unique.add(key)

                if message.authentication.member in times:
                    tim = times[message.authentication.member]
                else:
                    tim = [global_time for global_time, in self._database.execute(u"SELECT global_time FROM sync WHERE community = ? AND user = ? AND name = ?",
                                                                                  (message.community.database_id, message.authentication.member.database_id, message.database_id))]
                    assert len(tim) <= message.distribution.history_size
                    times[message.authentication.member] = tim

                if message.distribution.global_time in tim:
                    # we have the previous message (drop)
                    return DropMessage(message, "duplicate message by member^global_time (3)")

                elif len(tim) >= message.distribution.history_size and min(tim) > message.distribution.global_time:
                    # we have newer messages (drop)

                    # if the history_size is one, we can sent that on message back because
                    # apparently the sender does not have this message yet
                    if message.distribution.history_size == 1:
                        try:
                            packet, = self._database.execute(u"SELECT packet FROM sync WHERE community = ? AND user = ? ORDER BY global_time DESC LIMIT 1",
                                                             (message.community.database_id, message.authentication.member.database_id)).next()
                        except StopIteration:
                            # TODO can still fail when packet is in one of the received messages
                            # from this batch.
                            pass
                        else:
                            self._send([message.address], [str(packet)])

                    return DropMessage(message, "old message by member^global_time")


                else:
                    # we accept this message
                    tim.append(message.distribution.global_time)
                    return message

        def check_multi_member_and_global_time(unique, times, message):
            """
            No other message may exist with this message.authentication.members / global_time
            combination, regardless of the ordering of the members
            """
            assert isinstance(unique, set)
            assert isinstance(times, dict)
            assert isinstance(message, Message.Implementation)
            assert isinstance(message.authentication, MultiMemberAuthentication.Implementation)

            key = (message.authentication.member, message.distribution.global_time)
            if key in unique:
                return DropMessage(message, "already processed message by member^global_time")

            else:
                unique.add(key)

                members = tuple(sorted(member.database_id for member in message.authentication.members))
                key = members + (message.distribution.global_time,)
                if key in unique:
                    return DropMessage(message, "already processed message by members^global_time")

                else:
                    unique.add(key)

                    # ensure that the community / member / global_time is always unique
                    try:
                        self._database.execute(u"SELECT 1 FROM sync WHERE community = ? AND user = ? AND global_time = ?",
                                               (message.community.database_id, message.authentication.member.database_id, message.distribution.global_time)).next()
                    except StopIteration:
                        pass
                    else:
                        # we have the previous message (drop)
                        return DropMessage(message, "duplicate message by member^global_time (4)")

                    if members in times:
                        tim = times[members]

                    else:
                        # the next query obtains a list with all global times that we have in the
                        # database for all message.meta messages that were signed by
                        # message.authentication.members where the order of signing is not taken
                        # into account.
                        tim = [global_time
                               for count, global_time
                               in self._database.execute(u"""
                               SELECT COUNT(*), sync.global_time
                               FROM sync
                               JOIN reference_user_sync ON reference_user_sync.sync = sync.id
                               WHERE sync.community = ? AND sync.name = ? AND reference_user_sync.user IN (%s)
                               GROUP BY sync.id
                               """ % ", ".join("?" for _ in xrange(len(members))),
                                          (message.community.database_id, message.database_id) + members)
                               if count == message.authentication.count]
                        times[members] = tim

                    if message.distribution.global_time in tim:
                        # we have the previous message (drop)
                        return DropMessage(message, "duplicate message by members^global_time")

                    elif len(tim) >= message.distribution.history_size and min(tim) > message.distribution.global_time:
                        # we have newer messages (drop)

                        # if the history_size is one, we can sent that on message back because
                        # apparently the sender does not have this message yet
                        if message.distribution.history_size == 1:
                            assert len(tim) == 1
                            packets = [packet
                                       for count, packet
                                       in self._database.execute(u"""
                                       SELECT COUNT(*), sync.packet
                                       FROM sync
                                       JOIN reference_user_sync ON reference_user_sync.sync = sync.id
                                       WHERE sync.community = ? AND sync.global_time = ? AND sync.name = ? AND reference_user_sync.user IN (%s)
                                       GROUP BY sync.id
                                       """ % ", ".join("?" for _ in xrange(len(members))),
                                                                 (message.community.database_id, tim[0], message.database_id) + members)
                                       if count == message.authentication.count]

                            if packets:
                                assert len(packets) == 1
                                self._send([message.address], map(str, packets))

                            else:
                                # TODO can still fail when packet is in one of the received messages
                                # from this batch.
                                pass

                        return DropMessage(message, "old message by members^global_time")

                    else:
                        # we accept this message
                        tim.append(message.distribution.global_time)
                        return message

        def check_sequence_number(highest, message):
            """
            The message.distribution.sequence_number must be the next number in line
            """
            assert isinstance(highest, dict)
            assert isinstance(message, Message.Implementation)

            if message.authentication.member in highest:
                seq = highest[message.authentication.member]
            else:
                try:
                    seq, = self._database.execute(u"SELECT distribution_sequence FROM sync WHERE community = ? AND user = ? AND name = ? ORDER BY distribution_sequence DESC LIMIT 1",
                                                  (message.community.database_id, message.authentication.member.database_id, message.database_id)).next()
                except StopIteration:
                    seq = 0
                highest[message.authentication.member] = seq

            if seq >= message.distribution.sequence_number:
                # we already have this message (drop)
                return DropMessage(message, "duplicate message by sequence_number")

            elif seq + 1 == message.distribution.sequence_number:
                # we have the previous message
                highest[message.authentication.member] += 1
                return message

            else:
                # we do not have the previous message (delay and request)
                return DelayMessageBySequence(message, seq+1, message.distribution.sequence_number-1)

        # meta message
        meta = messages[0].meta

        # sort the messages by their (1) global_time and (2) binary packet
        messages = sorted(messages, lambda a, b: a.distribution.global_time - b.distribution.global_time or cmp(a.packet, b.packet))

        if isinstance(meta.authentication, MemberAuthentication):
            # a message is considered unique when (creator, global-time), i.r. (authentication.member,
            # distribution.global_time), is unique.  UNIQUE is used in the check_member_and_global_time
            # function
            unique = set()
            times = {}
            messages = [check_member_and_global_time(unique, times, message) for message in messages]

        # instead of storing HISTORY_SIZE messages for each authentication.member, we will store
        # HISTORY_SIZE messages for each combination of authentication.members.
        else:
            assert isinstance(meta.authentication, MultiMemberAuthentication)
            unique = set()
            times = {}
            messages = [check_multi_member_and_global_time(unique, times, message) for message in messages]

        # when sequence numbers are enabled, we need to have the previous message in the sequence
        # before we can process this message
        if meta.distribution.enable_sequence_number:
            highest = {}
            messages = [check_sequence_number(highest, message) if isinstance(message, Message.Implementation) else message for message in messages]

        return messages

    def _check_direct_distribution_batch(self, messages):
        """
        Returns the messages in the correct processing order.

        This method is called when a message with the DirectDistribution policy is received.  This
        message is not stored and hence we will not be able to see if we have already received this
        message.

        Receiving the same DirectDistribution multiple times indicates that the sending -wanted- to
        send this message multiple times.

        @param messages: Ignored.
        @type messages: [Message.Implementation]

        @return: All messages that are not dropped, i.e. all messages
        @rtype: [Message.Implementation]
        """
        # sort the messages by their (1) global_time and (2) binary packet
        return sorted(messages, lambda a, b: a.distribution.global_time - b.distribution.global_time or cmp(a.packet, b.packet))

    def data_came_in(self, packets):
        """
        UDP packets were received from the Tribler rawserver.

        This must be called on the Triber rawserver thread.  It will add the packets to the Dispersy
        Callback thread for processing.
        """
        self._callback.register(self.on_incoming_packets, (packets,))
        if __debug__:
            # update statistics
            self._total_received += sum(len(packet) for _, packet in packets)

    def on_incoming_packets(self, packets):
        """
        Process incoming UDP packets.

        This method is called to process one or more UDP packets.  This occurs when new packets are
        received, to attempt to process previously delayed packets, or when a user explicitly
        creates a packet to process.  The last option should only occur for debugging purposes.

        All the received packets are processed in batches, a batch consists of all packets for the
        same community and the same meta message.  Batches are formed with the following steps:

         1. The associated community is retrieved.  Failure results in packet drop.

         2. The associated conversion is retrieved.  Failure results in packet drop, this probably
            indicates that we are running outdated software.

         3. The associated meta message is retrieved.  Failure results in a packet drop, this
            probably indicates that we are running outdated software.

        All packets are grouped by their meta message.  All batches are scheduled based on the
        meta.delay and meta.priority.  Finally, the candidate table is updated in regards to the
        incoming source addresses.

        @param packets: The sequence of packets.
        @type packets: [(address, packet)]
        """
        assert isinstance(packets, (tuple, list))
        assert len(packets) > 0
        assert not filter(lambda x: not len(x) == 2, packets)

        addresses = set()
        key = lambda tup: tup[0] # meta, address, packet, conversion
        for meta, iterator in groupby(sorted(self._convert_packets_into_batch(packets), key=key), key=key):
            batch = [(address, packet, conversion) for _, address, packet, conversion in iterator]

            # build unique set containing source addresses
            addresses.update(address for address, _, _ in batch)

            # schedule batch processing (taking into account the message priority)
            if meta in self._batch_cache:
                self._batch_cache[meta].extend(batch)
                if __debug__:
                    self._debug_batch_cache_performance[meta.name].append(len(batch))
            else:
                self._batch_cache[meta] = batch
                self._callback.register(self._on_batch_cache, (meta,), delay=meta.delay, priority=meta.priority)
                if __debug__:
                    self._debug_batch_cache_performance[meta.name] = [len(batch)]

        # update candidate table.  We know that some peer (not necessarily
        # message.authentication.member) exists at this address.
        for host, port in addresses:
            self._database.execute(u"UPDATE candidate SET incoming_time = DATETIME('now') WHERE community = ? AND host = ? AND port = ?",
                                   (meta.community.database_id, unicode(host), port))
            if self._database.changes == 0:
                self._database.execute(u"INSERT INTO candidate(community, host, port, incoming_time, outgoing_time) VALUES(?, ?, ?, DATETIME('now'), '2010-01-01 00:00:00')",
                                       (meta.community.database_id, unicode(host), port))

    def _on_batch_cache(self, meta):
        """
        Start processing a batch of messages.

        This method is called meta.delay seconds after the first message in this batch arrived.  All
        messages in this batch have been 'cached' together in self._batch_cache[meta].  Hopefully
        the delay caused the batch to collect as many messages as possible.

        The batch is processed in the following steps:

         1. All duplicate binary packets are removed.

         2. All binary packets are converted into Message.Implementation instances.  Some packets
            are dropped or delayed at this stage.

         3. All remaining messages are passed to on_message_batch.
        """
        assert meta in self._batch_cache

        def unique(batch):
            unique = set()
            for address, packet, conversion in batch:
                assert isinstance(packet, str)
                if packet in unique:
                    if __debug__: dprint("drop a ", len(packet), " byte packet (duplicate in batch) from ", address[0], ":", address[1], level="warning")
                    self._statistics.drop("_convert_packets_into_batch:duplicate in batch", len(packet))
                else:
                    unique.add(packet)
                    yield address, packet, conversion

        if __debug__:
            if meta.delay:
                performance = "good" if len(self._debug_batch_cache_performance[meta.name]) > 1 else "bad"
                dprint("performance: ", performance, " [", ":".join(map(str, self._debug_batch_cache_performance[meta.name])), "] for ", meta.name, " after ", meta.delay, "s")
            del self._debug_batch_cache_performance[meta.name]

        # remove duplicated
        # todo: make _convert_batch_into_messages accept iterator instead of list to avoid conversion
        batch = list(unique(self._batch_cache.pop(meta)))

        # convert binary packets into Message.Implementation instances
        messages = list(self._convert_batch_into_messages(batch))
        assert not filter(lambda x: not isinstance(x, Message.Implementation), messages), "_convert_batch_into_messages must return only Message.Implementation instances"
        assert not filter(lambda x: not x.meta == meta, messages), "All Message.Implementation instances must be in the same batch"
        if __debug__: dprint(len(messages), " ", meta.name, " messages after conversion")

        # handle the incoming messages
        if messages:
            self.on_message_batch(messages)

    def on_messages(self, messages):
        batches = dict()
        for message in messages:
            if not message.meta in batches:
                batches[message.meta] = set()
            batches[message.meta].add(message)

        for messages in batches.itervalues():
            self.on_message_batch(list(messages))

    def on_message_batch(self, messages):
        """
        Process one batch of messages.

        This method is called to process one or more Message.Implementation instances that all have
        the same meta message.  This occurs when new packets are received, to attempt to process
        previously delayed messages, or when a user explicitly creates a message to process.  The
        last option should only occur for debugging purposes.

        The messages are processed with the following steps:

         1. Messages created by a member in our blacklist are droped.

         2. Messages that are old or duplicate, based on their distribution policy, are dropped.

         3. The meta.check_callback(...) is used to allow messages to be dropped or delayed.

         4. Messages are stored, based on their distribution policy.

         5. The meta.handle_callback(...) is used to process the messages.

         6. A check is performed if any of these messages triggers a delayed action.

        @param packets: The sequence of messages with the same meta message from the same community.
        @type packets: [Message.Implementation]
        """
        assert isinstance(messages, list)
        assert len(messages) > 0
        assert not filter(lambda x: not isinstance(x, Message.Implementation), messages)
        assert not filter(lambda x: not x.meta == messages[0].meta, messages), ("All messages need to have the same meta", messages[0].name, len(messages))
        assert not filter(lambda x: not x.community == messages[0].community, messages), ("All messages need to be from the same community", messages[0].name, len(messages))

        def _filter_fail(message):
            if isinstance(message, DelayMessage):
                self._statistics.delay("on_message_batch:%s" % message, len(message.delayed.packet))
                if __debug__: dprint(message.request.address[0], ":", message.request.address[1], ": delay a ", len(message.request.packet), " byte message (", message, ")")
                # try to extend an existing Trigger with the same pattern
                for trigger in self._triggers:
                    if isinstance(trigger, TriggerMessage) and trigger.extend(message.pattern, [message.delayed]):
                        if __debug__: dprint("extended an existing TriggerMessage")
                        break
                else:
                    # create a new Trigger with this pattern
                    trigger = TriggerMessage(message.pattern, self.on_messages, [message.delayed])
                    if __debug__: dprint("created a new TriggeMessage")
                    self._triggers.append(trigger)
                    self._callback.register(trigger.on_timeout, delay=10.0)
                    self._send([message.delayed.address], [message.request.packet])
                return False

            elif isinstance(message, DropMessage):
                self._statistics.drop("on_message_batch:%s" % message, len(message.dropped.packet))
                return False

            else:
                return True

        meta = messages[0].meta

        if __debug__:
            debug_begin = clock()
            dprint("[0.0 msg] ", len(messages), " ", meta.name, " messages")

        # drop if this is a blacklisted member
        messages = [message for message in messages if not (isinstance(message.authentication, (MemberAuthentication.Implementation, MultiMemberAuthentication.Implementation)) and message.authentication.member.must_drop)]
        # todo: we currently do not add this message in the bloomfilter, hence we will
        # continually receive this packet.
        if __debug__: dprint("[", clock() - debug_begin, " msg] ", len(messages), " ", meta.name, " messages after blacklisted members")
        if not messages:
            return 0

        # drop all duplicate or old messages
        assert type(meta.distribution) in self._check_distribution_batch_map
        messages = list(self._check_distribution_batch_map[type(meta.distribution)](messages))
        assert len(messages) > 0 # should return at least one item for each message
        assert not filter(lambda x: not isinstance(x, (Message.Implementation, DropMessage, DelayMessage)), messages)

        if __debug__:
            i_messages = len(list(message for message in messages if isinstance(message, Message.Implementation)))
            i_delay = len(list(message for message in messages if isinstance(message, DelayMessage)))
            i_drop = len(list(message for message in messages if isinstance(message, DropMessage)))
            for message in messages:
                if isinstance(message, DropMessage):
                    dprint("drop message because: ", message)
            dprint("[", clock() - debug_begin, " msg] ", i_messages, ":", i_delay, ":", i_drop, " ", meta.name, " messages after distribution check")

        # handle/remove DropMessage and DelayMessage instances
        messages = [message for message in messages if _filter_fail(message)]
        if __debug__: dprint("[", clock() - debug_begin, " msg] ", len(messages), " ", meta.name, " messages after delay and drop")
        if not messages:
            return 0

        # check all remaining messages on the community side.  may yield Message.Implementation,
        # DropMessage, and DelayMessage instances
        messages = list(meta.check_callback(messages))
        assert len(messages) >= 0 # may return zero messages
        assert not filter(lambda x: not isinstance(x, (Message.Implementation, DropMessage, DelayMessage)), messages)

        if __debug__:
            i_messages = len(list(message for message in messages if isinstance(message, Message.Implementation)))
            i_delay = len(list(message for message in messages if isinstance(message, DelayMessage)))
            i_drop = len(list(message for message in messages if isinstance(message, DropMessage)))
            dprint("[", clock() - debug_begin, " msg] ", i_messages, ":", i_delay, ":", i_drop, " ", meta.name, " messages after community check")

        # handle/remove DropMessage and DelayMessage instances
        messages = [message for message in messages if _filter_fail(message)]
        if __debug__: dprint("[", clock() - debug_begin, " msg] ", len(messages), " ", meta.name, " messages after delay and drop")
        if not messages:
            return 0

        # store to disk and update locally
        self._statistics.success(meta.name, sum(len(message.packet) for message in messages), len(messages))
        self.store_update_forward(messages, True, True, False)
        if __debug__: dprint("[", clock() - debug_begin, " msg] ", len(messages), " ", meta.name , " messages after store and update")

        # try to 'trigger' zero or more previously delayed 'things'
        self._triggers = [trigger for trigger in self._triggers if trigger.on_messages(messages)]
        if __debug__: dprint("[", clock() - debug_begin, " msg] ", len(messages), " ", meta.name, " messages after triggers")

        # return the number of messages that were correctly handled (non delay, duplictes, etc)
        return len(messages)

    def _convert_packets_into_batch(self, packets):
        """
        Convert a list with one or more (address, data) tuples into a list with zero or more
        (Message, (address, packet, conversion)) tuples using a generator.

        # 22/06/11 boudewijn: no longer checks for duplicates.  duplicate checking is pointless
        # because new duplicates may be introduces because of the caching mechanism.
        #
        # Duplicate packets are removed.  This will result in drops when two we receive the exact same
        # binary packet from multiple nodes.  While this is usually not a problem, packets are usually
        # signed and hence unique, in rare cases this may result in invalid drops.

        Packets from invalid sources are removed.  The _is_valid_external_address is used to
        determine valid addresses.

        Packets associated with an unknown community are removed.  Packets from a known community
        encoded in an unknown conversion, are also removed.

        The results can be used to easily create a dictionary batch using
         > batch = dict(_convert_packets_into_batch(packets))
        """
        assert isinstance(packets, (tuple, list))
        assert len(packets) > 0
        assert not filter(lambda x: not len(x) == 2, packets)

        # unique = set()
        for address, packet in packets:
            assert isinstance(address, tuple)
            assert len(address) == 2
            assert isinstance(address[0], str)
            assert isinstance(address[1], int)
            assert isinstance(packet, str)

            # # we may have receive this packet in this on_incoming_packets callback
            # if packet in unique:
            #     if __debug__: dprint("drop a ", len(packet), " byte packet (duplicate in batch) from ", address[0], ":", address[1], level="warning")
            #     self._statistics.drop("_convert_packets_into_batch:duplicate in batch", len(packet))
            #     continue

            # is it from an external source
            if not self._is_valid_external_address(address):
                if __debug__: dprint("drop a ", len(packet), " byte packet (received from an invalid source) from ", address[0], ":", address[1], level="warning")
                self._statistics.drop("_convert_packets_into_batch:invalid source", len(packet))
                continue

            # find associated community
            try:
                community = self.get_community(packet[2:22])
            except KeyError:
                if __debug__: dprint("drop a ", len(packet), " byte packet (received packet for unknown community) from ", address[0], ":", address[1], level="warning")
                self._statistics.drop("_convert_packets_into_batch:unknown community", len(packet))
                continue

            # find associated conversion
            try:
                conversion = community.get_conversion(packet[:22])
            except KeyError:
                if __debug__: dprint("drop a ", len(packet), " byte packet (received packet for unknown conversion) from ", address[0], ":", address[1], level="warning")
                self._statistics.drop("_convert_packets_into_batch:unknown conversion", len(packet))
                continue

            try:
                # convert binary data into the meta message
                yield conversion.decode_meta_message(packet), address, packet, conversion

            except DropPacket, exception:
                if __debug__: dprint(address[0], ":", address[1], ": drop a ", len(packet), " byte packet (", exception, ")", level="warning")
                self._statistics.drop("_convert_packets_into_batch:decode_meta_message:%s" % exception, len(packet))

    def _convert_batch_into_messages(self, batch):
        if __debug__:
            from conversion import Conversion
        assert isinstance(batch, (list, set))
        assert len(batch) > 0
        assert not filter(lambda x: not isinstance(x, tuple), batch)
        assert not filter(lambda x: not len(x) == 3, batch)

        if __debug__:
            begin_stats = Conversion.debug_stats.copy()

        for address, packet, conversion in batch:
            assert isinstance(address, tuple)
            assert isinstance(address[0], str)
            assert isinstance(address[1], int)
            assert isinstance(packet, str)
            assert isinstance(conversion, Conversion)

            try:
                # convert binary data to internal Message
                yield conversion.decode_message(address, packet)

            except DropPacket, exception:
                if __debug__: dprint(address[0], ":", address[1], ": drop a ", len(packet), " byte packet (", exception, ")", level="warning")
                self._statistics.drop("_convert_batch_into_messages:%s" % exception, len(packet))

            except DelayPacket, delay:
                if __debug__: dprint(address[0], ":", address[1], ": delay a ", len(packet), " byte packet (", delay, ")")
                self._statistics.delay("_convert_batch_into_messages:%s" % delay, len(packet))
                # try to extend an existing Trigger with the same pattern
                for trigger in self._triggers:
                    if isinstance(trigger, TriggerPacket) and trigger.extend(delay.pattern, [(address, packet)]):
                        if __debug__: dprint("extended an existing TriggerPacket")
                        break
                else:
                    # create a new Trigger with this pattern
                    trigger = TriggerPacket(delay.pattern, self.on_incoming_packets, [(address, packet)])
                    if __debug__: dprint("created a new TriggerPacket")
                    self._triggers.append(trigger)
                    self._callback.register(trigger.on_timeout, delay=10.0)
                    self._send([address], [delay.request_packet])

        if __debug__:
            if len(batch) > 100:
                for key, value in sorted(Conversion.debug_stats.iteritems()):
                    if value - begin_stats[key] > 0.0:
                        dprint("[", value - begin_stats[key], " cnv] ", len(batch), "x ", key)

    def _store(self, messages):
        """
        Store a message in the database.

        Messages with the Last- or Full-SyncDistribution policies need to be stored in the database
        to allow them to propagate to other members.

        Messages with the LastSyncDistribution policy may also cause an older message to be removed
        from the database.

        Messages created by a member that we have marked with must_store will also be stored in the
        database, and hence forwarded to others.

        @param message: The unstored message with the SyncDistribution policy.
        @type message: Message.Implementation
        """
        assert isinstance(messages, list)
        assert len(messages) > 0
        assert not filter(lambda x: not isinstance(x, Message.Implementation), messages)
        assert not filter(lambda x: not x.community == messages[0].community, messages), "All messages need to be from the same community"
        assert not filter(lambda x: not x.meta == messages[0].meta, messages), "All messages need to have the same meta"
        assert not filter(lambda x: not isinstance(x.distribution, SyncDistribution.Implementation), messages)
        # ensure no duplicate messages are present, this MUST HAVE been checked before calling this
        # method!
        assert len(messages) == len(set((message.authentication.member.database_id, message.distribution.global_time) for message in messages)), messages[0].name

        meta = messages[0].meta
        if __debug__: dprint("storing ", len(messages), " ", meta.name, " messages")
        is_subjective_destination = isinstance(meta.destination, SubjectiveDestination)
        is_similarity_destination = isinstance(meta.destination, SimilarityDestination)
        is_multi_member_authentication = isinstance(meta.authentication, MultiMemberAuthentication)

        update_sync_range = []
        free_sync_range = []
        for message in messages:
            # the signature must be set
            assert isinstance(message.authentication, (MemberAuthentication.Implementation, MultiMemberAuthentication.Implementation)), message.authentication
            assert message.authentication.is_signed
            assert not message.packet[-10:] == "\x00" * 10, message.packet[-10:].encode("HEX")

            # we do not store a message when it uses SubjectiveDestination and it is not in our set
            if is_subjective_destination and not message.destination.is_valid:
                # however, ignore the SimilarityDestination when we are forced so store this message
                if not message.authentication.member.must_store:
                    if __debug__: dprint("Not storing message")
                    continue

            # we do not store a message when it uses SimilarityDestination and it is not similar
            if is_similarity_destination and not message.destination.is_similar:
                # however, ignore the SimilarityDestination when we are forced so store this message
                if not message.authentication.member.must_store:
                    if __debug__: dprint("Not storing message.  bic:", message.destination.bic_occurrence, "  threshold:", message.destination.threshold)
                    continue

            # add packet to database
            self._database.execute(u"INSERT INTO sync (community, name, user, global_time, synchronization_direction, distribution_sequence, destination_cluster, packet) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (message.community.database_id,
                     message.database_id,
                     message.authentication.member.database_id,
                     message.distribution.global_time,
                     message.distribution.synchronization_direction_id,
                     message.distribution.sequence_number if isinstance(message.distribution, SyncDistribution.Implementation) else 0,
                     # isinstance(message.distribution, LastSyncDistribution.Implementation) and message.distribution.cluster or 0,
                     message.destination.cluster if isinstance(message.destination, SimilarityDestination.Implementation) else 0,
                     buffer(message.packet)))
            assert self._database.changes == 1
            update_sync_range.append(message)

            # ensure that we can reference this packet
            message.packet_id = self._database.last_insert_rowid
            if __debug__: dprint("insert_rowid: ", message.packet_id, " for ", message.name)

            # link multiple members is needed
            if is_multi_member_authentication:
                self._database.executemany(u"INSERT INTO reference_user_sync (user, sync) VALUES (?, ?)",
                                           [(member.database_id, message.packet_id) for member in message.authentication.members])
                assert self._database.changes == message.authentication.count

        if isinstance(meta.distribution, LastSyncDistribution):
            # delete packets that have become obsolete
            items = set()
            if is_multi_member_authentication:
                for member_database_ids in set(tuple(sorted(member.database_id for member in message.authentication.members)) for message in messages):
                    OR = u" OR ".join(u"reference_user_sync.user = ?" for _ in xrange(meta.authentication.count))
                    iterator = self._database.execute(u"""
                            SELECT sync.id, sync.user, sync.global_time, reference_user_sync.user
                            FROM sync
                            JOIN reference_user_sync ON reference_user_sync.sync = sync.id
                            WHERE community = ? AND name = ? AND (%s)
                            ORDER BY sync.global_time, sync.packet""" % OR,
                                       (meta.community.database_id, meta.database_id) + member_database_ids)
                    all_items = []
                    # TODO: weird.  group by using row[0], that is sync.id, and that is unique, so
                    # groupby makes no sence.  Furthermore, groupby requires row[0] to be sorted,
                    # and that is not the case either.
                    for id_, group in groupby(iterator, key=lambda row: row[0]):
                        group = list(group)
                        if len(group) == meta.authentication.count and member_database_ids == tuple(sorted(check_member_id for _, _, _, check_member_id in group)):
                            _, creator_database_id, global_time, _ = group[0]
                            all_items.append((id_, creator_database_id, global_time))

                    if len(all_items) > meta.distribution.history_size:
                        items.update(all_items[:len(all_items) - meta.distribution.history_size])

            else:
                for member_database_id in set(message.authentication.member.database_id for message in messages):
                    all_items = list(self._database.execute(u"SELECT id, user, global_time FROM sync WHERE community = ? AND name = ? AND user = ? ORDER BY global_time, packet",
                                             (meta.community.database_id, meta.database_id, member_database_id)))
                    if len(all_items) > meta.distribution.history_size:
                        items.update(all_items[:len(all_items) - meta.distribution.history_size])

            if items:
                self._database.executemany(u"DELETE FROM sync WHERE id = ?", [(id_,) for id_, _, _ in items])
                assert len(items) == self._database.changes
                if __debug__: dprint("deleted ", self._database.changes, " messages ", [id_ for id_, _, _ in items])

                if is_multi_member_authentication:
                    community_database_id = meta.community.database_id
                    self._database.executemany(u"DELETE FROM reference_user_sync WHERE sync = ?", [(id_,) for id_, _, _ in items])
                    assert len(items) * meta.authentication.count == self._database.changes

                free_sync_range.extend(global_time for _, _, global_time in items)

        if update_sync_range:
            # add items to the sync bloom filters
            meta.community.update_sync_range(update_sync_range)

        if free_sync_range:
            # update bloom filters
            meta.community.free_sync_range(free_sync_range)

    def yield_online_candidates(self, community, limit, batch=100):
        """
        Returns a generator that yields LIMIT unique Candicate objects ordered by most likely to
        least likely to be online.
        """
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert isinstance(limit, int)
        assert isinstance(batch, int)
        return islice(self._yield_online_candidates(community, min(limit, batch)), limit)

    def _yield_online_candidates(self, community, batch=100):
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert isinstance(batch, int)

        for offset in xrange(0, maxint, batch):
            # cache all items returned from the select statement, otherwise the cursur will be
            # re-used whenever another query is performed by the caller
            sql = u"""SELECT host, port, incoming_time, outgoing_time, external_time
                      FROM candidate
                      WHERE community = ?
                      ORDER BY incoming_time DESC
                      LIMIT ?
                      OFFSET ?"""
            candidates = list(self._database.execute(sql, (community.database_id, batch, offset)))
            for host, port, incoming_time, outgoing_time, external_time in candidates:
                yield Candidate(str(host), int(port), incoming_time, outgoing_time, external_time)
            else:
                if not candidates:
                    break

    def yield_mixed_candidates(self, community, limit, diff_range=(0.0, 30.0), age_range=(120.0, 300.0), batch=100):
        """
        Returns a generator that yields LIMIT unique Candidate objects where the selection is a
        mixed between peers that are most likely to be online and peers that are less likely to be
        online.

        Note that the diff_range and age_range parameters will be replaced in the future.
        """
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert isinstance(limit, int)
        assert isinstance(batch, int)
        return islice(self._yield_mixed_candidates(community, diff_range, age_range, min(limit, batch)), limit)

    def _yield_mixed_candidates(self, community, diff_range, age_range, batch):
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert isinstance(batch, int)

        unique = set()

        # the theory behind the address selection is:
        # a. we want to keep contact with those who are online, hence we send messages to those that
        #    have a small diff.
        # b. we want to get connections to those that have been away for some time, hence we send
        #    messages to those that have a high age.
        sql = u"""SELECT host, port, incoming_time, outgoing_time, external_time
                  FROM candidate
                  WHERE community = ? AND (ABS(STRFTIME('%s', outgoing_time) - STRFTIME('%s', incoming_time)) BETWEEN ? AND ?
                                           OR STRFTIME('%s', DATETIME('now')) - STRFTIME('%s', incoming_time) BETWEEN ? AND ?)
                  ORDER BY RANDOM()
                  LIMIT ?
                  OFFSET ?"""
        for offset in xrange(0, maxint, batch):
            # cache all items returned from the select statement, otherwise the cursur will be
            # re-used whenever another query is performed by the caller
            candidates = list(self._database.execute(sql, (community.database_id, diff_range[0], diff_range[1], age_range[0], age_range[1], batch, offset)))
            for host, port, incoming_time, outgoing_time, external_time in candidates:
                if not (host, port) in unique:
                    unique.add((host, port))
                    yield Candidate(str(host), int(port), incoming_time, outgoing_time, external_time)
            else:
                if not candidates:
                    break

        # we will try a few addresses from external sources (3rd party).  note that selecting these
        # will add a value to the outgoing_time column because we will sent something to this
        # address.
        sql = u"""SELECT host, port, incoming_time, outgoing_time, external_time
                  FROM candidate
                  WHERE community = ? AND STRFTIME('%s', DATETIME('now')) - STRFTIME('%s', external_time) BETWEEN ? AND ?
                  ORDER BY RANDOM()
                  LIMIT ?
                  OFFSET ?"""
        for offset in xrange(0, maxint, batch):
            # cache all items returned from the select statement, otherwise the cursur will be
            # re-used whenever another query is performed by the caller
            candidates = list(self._database.execute(sql, (community.database_id, age_range[0], age_range[1], batch, offset)))
            for host, port, incoming_time, outgoing_time, external_time in candidates:
                if not (host, port) in unique:
                    unique.add((host, port))
                    yield Candidate(str(host), int(port), incoming_time, outgoing_time, external_time)
            else:
                if not candidates:
                    break

        # at this point we do not have sufficient nodes that were online recently.  as an
        # alternative we will add the addresses of dispersy routers that should always be online
        sql = u"""SELECT host, port, incoming_time, outgoing_time, external_time
                  FROM candidate
                  WHERE community = 0
                  ORDER BY RANDOM()
                  LIMIT ?
                  OFFSET ?"""
        for offset in xrange(0, maxint, batch):
            # cache all items returned from the select statement, otherwise the cursur will be
            # re-used whenever another query is performed by the caller
            candidates = list(self._database.execute(sql, (batch, offset)))
            for host, port, incoming_time, outgoing_time, external_time in candidates:
                if not (host, port) in unique:
                    unique.add((host, port))
                    yield Candidate(str(host), int(port), incoming_time, outgoing_time, external_time)
            else:
                if not candidates:
                    break

        # fallback to just picking random addresses within this community.  unfortunately it is
        # likely that the addresses will contain nodes that are offline
        sql = u"""SELECT host, port, incoming_time, outgoing_time, external_time
                  FROM candidate
                  WHERE community = ?
                  ORDER BY RANDOM()
                  LIMIT ?
                  OFFSET ?"""
        for offset in xrange(0, maxint, batch):
            # cache all items returned from the select statement, otherwise the cursur will be
            # re-used whenever another query is performed by the caller
            candidates = list(self._database.execute(sql, (community.database_id, batch, offset)))
            for host, port, incoming_time, outgoing_time, external_time in candidates:
                if not (host, port) in unique:
                    unique.add((host, port))
                    yield Candidate(str(host), int(port), incoming_time, outgoing_time, external_time)
            else:
                if not candidates:
                    break

    def store_update_forward(self, messages, store, update, forward):
        """
        Usually we need to do three things when we have a valid messages: (1) store it in our local
        database, (2) process the message locally by calling the handle_callback method, and (3)
        forward the message to other nodes in the community.  This method is a shorthand for doing
        those three tasks.

        To reduce the disk activity, namely syncing the database to disk, we will perform the
        database commit not after the (1) store operation but after the (2) update operation.  This
        will ensure that any database changes from handling the message are also synced to disk.  It
        is important to note that the sync will occur before the (3) forward operation to ensure
        that no external nodes will obtain data that we have not safely synced ourselves.

        For performance reasons messages are processed in batches, where each batch contains only
        messages from the same community and the same meta message instance.  This method, or more
        specifically the methods that handle the actual storage, updating, and forwarding, assume
        this clustering.

        @param messages: A list with the messages that need to be stored, updated, and forwarded.
         All messages need to be from the same community and meta message instance.
        @type messages: [Message.Implementation]

        @param store: When True the messages are stored (as defined by their message distribution
         policy) in the local dispersy database.  This parameter should (almost always) be True, its
         inclusion is mostly to allow certain debugging scenarios.
        @type store: bool

        @param update: When True the messages are passed to their handle_callback methods.  This
         parameter should (almost always) be True, its inclusion is mostly to allow certain
         debugging scenarios.
        @type update: bool

        @param forward: When True the messages are forwarded (as defined by their message
         destination policy) to other nodes in the community.  This parameter should (almost always)
         be True, its inclusion is mostly to allow certain debugging scenarios.
        @type store: bool
        """
        assert isinstance(messages, list)
        assert len(messages) > 0
        assert not filter(lambda x: not isinstance(x, Message.Implementation), messages)
        assert not filter(lambda x: not x.community == messages[0].community, messages), "All messages need to be from the same community"
        assert not filter(lambda x: not x.meta == messages[0].meta, messages), "All messages need to have the same meta"
        assert isinstance(store, bool)
        assert isinstance(update, bool)
        assert isinstance(forward, bool)

        store = store and isinstance(messages[0].meta.distribution, SyncDistribution)
        if store:
            self._store(messages)

        if update:
            messages[0].handle_callback(messages)

        if store:
            self._database.commit()

        if forward:
            self._forward(messages)

    def _forward(self, messages):
        """
        Queue a sequence of messages to be sent to other members.

        First all messages that use the SyncDistribution policy are stored to the database to allow
        them to propagate when a dispersy-sync message is received.

        Second all messages are sent depending on their destination policy:

         - AddressDestination causes a message to be sent to the addresses in
           message.destination.addresses.

         - MemberDestination causes a message to be sent to the address associated to the member in
           message.destination.members.

         - CommunityDestination causes a message to be sent to one or more addresses to be picked
           from the database candidate table.

         - SubjectiveDestination is currently handled in the same way as CommunityDestination.
           Obviously this needs to be modified.

         - SimilarityDestination is currently handled in the same way as CommunityDestination.
           Obviously this needs to be modified.

        @param messages: A sequence with one or more messages.
        @type messages: [Message.Implementation]

        @todo: Ensure messages with the SimilarityDestination policy are only sent to similar
         members.
        """
        if __debug__:
            from message import Message
        assert isinstance(messages, (tuple, list))
        assert len(messages) > 0
        assert not filter(lambda x: not isinstance(x, Message.Implementation), messages)

        # todo: we can optimize below code given the following two restrictions
        assert not filter(lambda x: not x.community == messages[0].community, messages), "All messages need to be from the same community"
        assert not filter(lambda x: not x.meta == messages[0].meta, messages), "All messages need to have the same meta"

        for message in messages:
            if isinstance(message.destination, (CommunityDestination.Implementation, SubjectiveDestination.Implementation, SimilarityDestination.Implementation)):
                assert message.destination.node_count, "CommunityDestination.node_count is allowed to be zero, however, make sure that _forward isn't called when it is"
                addresses = [candidate.address
                             for candidate
                             in self.yield_online_candidates(message.community, message.destination.node_count)]
                if addresses:
                    self._send(addresses, [message.packet])

                if __debug__:
                    if addresses:
                        dprint("outgoing ", message.name, " (", len(message.packet), " bytes) to ", ", ".join("%s:%d" % address for address in addresses))
                    else:
                        dprint("failed to send ", message.name, " (", len(message.packet), " bytes) because there are no destination addresses", level="warning")

            elif isinstance(message.destination, AddressDestination.Implementation):
                if __debug__: dprint("outgoing ", message.name, " (", len(message.packet), " bytes) to ", ", ".join("%s:%d" % address for address in message.destination.addresses))
                self._send(message.destination.addresses, [message.packet])

            elif isinstance(message.destination, MemberDestination.Implementation):
                if __debug__:
                    for member in message.destination.members:
                        if not self._is_valid_external_address(member.address):
                            dprint("unable to send ", message.name, " to member (", member.address, ")", level="error")
                    dprint("outgoing ", message.name, " (", len(message.packet), " bytes) to ", ", ".join("%s:%d" % member.address for member in message.destination.members))
                self._send([member.address for member in message.destination.members], [message.packet])

            else:
                raise NotImplementedError(message.destination)

    def _send(self, addresses, packets):
        """
        Send one or more packets to one or more addresses.

        To clarify: every packet is sent to every address.

        @param addresses: A sequence with one or more addresses.
        @type addresses: [(string, int)]

        @patam packets: A sequence with one or more packets.
        @type packets: string
        """
        assert isinstance(addresses, (tuple, list, set)), type(addresses)
        assert isinstance(packets, (tuple, list, set)), type(packets)

        if __debug__:
            if not addresses:
                # this is a programming bug.
                dprint("no addresses given (wanted to send ", len(packets), " packets)", level="error", stack=True)
            if not packets:
                # this is a programming bug.
                dprint("no packets given (wanted to send to ", len(addresses), " addresses)", level="error", stack=True)

        if __debug__:
            # update statistics
            self._total_send += len(addresses) * sum([len(packet) for packet in packets])

        # update candidate table and send packets
        for address in addresses:
            assert isinstance(address, tuple)
            assert isinstance(address[0], str)
            assert isinstance(address[1], int)

            if not self._is_valid_external_address(address):
                # this is a programming bug.  apparently an invalid address is being used
                if __debug__: dprint("aborted sending a ", len(packet), " byte packet (invalid external address) to ", address[0], ":", address[1], level="error")
                continue

            for packet in packets:
                assert isinstance(packet, str)
                self._socket.send(address, packet)
            if __debug__: dprint(len(packets), " packets (", sum(len(packet) for packet in packets), " bytes) to ", address[0], ":", address[1])
            self._database.execute(u"UPDATE candidate SET outgoing_time = DATETIME('now') WHERE host = ? AND port = ?", (unicode(address[0]), address[1]))

    def await_message(self, footprint, response_func, response_args=(), timeout=10.0, max_responses=1):
        """
        Register a callback to occur when a message with a specific footprint is received, or after
        a certain timeout occurs.

        When the footprint of an incoming message matches the regular expression footprint it is
        passed to both the response_func (or several if the message matches multiple footprints) and
        its regular message handler.  First the regular message handler is called, followed by
        response_func.

        The response_func is called each time when a message is received that matches the expression
        footprint or after timeout seconds when fewer than max_responses incoming messages have
        matched footprint.  The first argument is the sender address (or ('', -1) on a timeout), the
        second argument is the incoming message, following this are any optional arguments in
        response_args.

        Response_args is a tuple that can be given optional values that are included in the call to
        response_func, following the address and message arguments.

        When the timeout expires and less than max_responses messages have matched the expression
        footprint, the response_func is called one last time.  The address and the message will be
        sent to ('', -1) None, respectively and response_args will be appended as normal.  Once a
        timeout callback is given no further callbacks will be made.

        The Trigger that is created will be removed either on timeout or when max_responses messages
        have matched the expression footprint.

        The footprint matching is done as follows: for each incoming message a message footprint is
        made.  This footprint is a string that contains a summary of all the message properties.
        Such as 'MemberAuthentication:ABCDE' and 'FullSyncDistribution:102'.

        @param footprint: The regular expression to match all incoming messages.
        @type footprint: string

        @param response_func: The method called when a message matches footprint.
        @type response_func: callable

        @param response_args: Optional arguments to added when calling response_func.
        @type response_args: tuple

        @param timeout: Number of seconds until a timeout occurs.
        @type timeout: float

        @param max_responses: Maximal number of messages to match until the Trigger is removed.
        @type max_responses: int
        """
        assert isinstance(footprint, str)
        assert hasattr(response_func, "__call__")
        assert isinstance(response_args, tuple)
        assert isinstance(timeout, float)
        assert timeout > 0.0
        assert isinstance(max_responses, (int, long))
        assert max_responses > 0

        trigger = TriggerCallback(footprint, response_func, response_args, max_responses)
        self._triggers.append(trigger)
        self._callback.register(trigger.on_timeout, delay=timeout)

    def create_candidate_request(self, community, address, routes, response_func=None, response_args=(), timeout=10.0, max_responses=1, store=True, forward=True):
        """
        Create a dispersy-candidate-request message.

        The dispersy-candidate-request and -response messages are used to keep track of the address
        where a member can be found.  It is also used to check if the member is still alive because
        it triggers a response message.  Finally, it is used to spread addresses of other members
        aswell.

        The optional response_func is used to obtain a callback for this specific request.  The
        parameters response_func, response_args, timeout, and max_responses are all related to this
        callback and are explained in the await_message method.

        @param community: The community for wich the dispersy-candidate-request message will be
         created.
        @type community: Community

        @param address: The destination address.
        @type address: (string, int)

        @param response_func: The method called when a message matches footprint.
        @type response_func: callable

        @param response_args: Optional arguments to added when calling response_func.
        @type response_args: tuple

        @param timeout: Number of seconds until a timeout occurs.
        @type timeout: float

        @param max_responses: Maximal number of messages to match until the Trigger is removed.
        @type max_responses: int

        @param store: When True the messages are stored (as defined by their message distribution
         policy) in the local dispersy database.  This parameter should (almost always) be True, its
         inclusion is mostly to allow certain debugging scenarios.
        @type store: bool

        @param forward: When True the messages are forwarded (as defined by their message
         destination policy) to other nodes in the community.  This parameter should (almost always)
         be True, its inclusion is mostly to allow certain debugging scenarios.
        @type store: bool
        """
        assert isinstance(community, Community)
        assert isinstance(address, tuple)
        assert isinstance(address[0], str)
        assert isinstance(address[1], int)
        assert isinstance(routes, (tuple, list))
        assert not filter(lambda route: not isinstance(route, tuple), routes)
        assert not filter(lambda route: not len(route) == 2, routes)
        assert not filter(lambda route: not isinstance(route[0], tuple), routes)
        assert not filter(lambda route: not len(route[0]) == 2, routes)
        assert not filter(lambda route: not isinstance(route[0][0], str), routes)
        assert not filter(lambda route: not isinstance(route[0][1], (int, long)), routes)
        assert not filter(lambda route: not isinstance(route[1], float), routes)
        assert hasattr(response_func, "__call__")
        assert isinstance(response_args, tuple)
        assert isinstance(timeout, float)
        assert timeout > 0.0
        assert isinstance(max_responses, (int, long))
        assert max_responses > 0
        assert isinstance(store, bool)
        assert isinstance(forward, bool)
        meta = community.get_meta_message(u"dispersy-candidate-request")
        request = meta.implement(meta.authentication.implement(community.my_member),
                                 meta.distribution.implement(meta.community.global_time),
                                 meta.destination.implement(address),
                                 meta.payload.implement(self._my_external_address, address, community.get_conversion(), routes))

        if response_func:
            meta = community.get_meta_message(u"dispersy-candidate-response")
            footprint = meta.generate_footprint(payload=(sha1(request.packet).digest(),))
            self.await_message(footprint, response_func, response_args, timeout, max_responses)

        self.store_update_forward([request], store, False, forward)
        return request

    def check_candidate_request(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def _is_valid_external_address(self, address):
        if address[0] == "":
            return False

        if address[1] <= 0:
            return False

        if address[0].endswith(".0"):
            return False

        if address[0].endswith(".255"):
            return False

        if address == self._my_external_address:
            return False

        if address == ("127.0.0.1", self._my_external_address[1]):
            return False

        return True

    def _update_routes_from_external_source(self, community, routes):
        assert isinstance(routes, (tuple, list))
        assert not filter(lambda x: not isinstance(x, tuple), routes)
        assert not filter(lambda x: not len(x) == 2, routes)
        assert not filter(lambda x: not isinstance(x[0], tuple), routes), "(host, ip) tuple"
        assert not filter(lambda x: not isinstance(x[1], float), routes), "age in seconds"

        for address, age in routes:
            if self._is_valid_external_address(address):
                if __debug__: dprint("update candidate table for ", address[0], ":", address[1])

                # TODO: we are overwriting our own age... first check that if we have this
                # address, that our age is higher before updating
                age = u"-%d seconds" % age
                self._database.execute(u"UPDATE candidate SET external_time = DATETIME('now', ?) WHERE community = ? AND host = ? AND port = ?",
                                       (age, community.database_id, unicode(address[0]), address[1]))
                if self._database.changes == 0:
                    self._database.execute(u"INSERT INTO candidate(community, host, port, external_time) VALUES(?, ?, ?, DATETIME('now', ?))",
                                           (community.database_id, unicode(address[0]), address[1], age))

            elif __debug__:
                level = "normal" if address == self.external_address else "warning"
                dprint("dropping invalid route ", address[0], ":", address[1], level=level)

    def on_candidate_request(self, messages):
        """
        We received a dispersy-candidate-request message.

        This message contains the external address that the sender believes it has
        (message.payload.source_address), and our external address
        (message.payload.destination_address).

        We should send a dispersy-candidate-response message back.  Allowing us to inform them of
        their external address.

        @param address: The sender address.
        @type address: (string, int)

        @param message: The dispersy-sync message.
        @type message: Message.Implementation
        """
        community = messages[0].community
        meta = community.get_meta_message(u"dispersy-candidate-response")
        minimal_age, maximal_age = community.dispersy_candidate_age_range
        sql = u"""SELECT host, port, STRFTIME('%s', DATETIME('now')) - STRFTIME('%s', incoming_time) AS age
            FROM candidate
            WHERE community = ? AND age BETWEEN ? AND ?
            ORDER BY age
            LIMIT 30"""
        routes = [((str(host), port), float(age)) for host, port, age in self._database.execute(sql, (community.database_id, minimal_age, maximal_age))]
        responses = []

        for message in messages:
            assert message.name == u"dispersy-candidate-request"
            if __debug__: dprint(message)

            if __debug__: dprint("Our external address may be: ", message.payload.destination_address)
            self.external_address_vote(message.payload.destination_address, message.address)

            # update or insert the member who sent the request
            # self._database.execute(u"UPDATE user SET user = ? WHERE community = ? AND host = ? AND port = ?",
            #                        (message.authentication.member.database_id, message.community.database_id, unicode(address[0]), address[1]))

            routes.extend(message.payload.routes)

            responses.append(meta.implement(meta.authentication.implement(community.my_member),
                                            meta.distribution.implement(community.global_time),
                                            meta.destination.implement(message.address),
                                            meta.payload.implement(sha1(message.packet).digest(), self._my_external_address, message.address, meta.community.get_conversion().version, routes)))

        # add routes in our candidate table
        self._update_routes_from_external_source(community, routes)

        # send response
        self.store_update_forward(responses, False, False, True)

    def check_candidate_response(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def on_candidate_response(self, messages):
        """
        We received dispersy-candidate-response messages.

        This message contains the external address that the sender believes it has
        (message.payload.source_address), and our external address
        (message.payload.destination_address).

        We need to be carefull with this message.  It is very much possible that the
        destination_address is invalid.  Furthermore, currently anyone is free to send this message,
        making it very easy to generate any number of members to override simple security schemes
        that use counting.

        @param messages: The dispersy-candidate-response messages.
        @type messages: [Message.Implementation]
        """
        community = messages[0].community
        routes = []
        for message in messages:
            if __debug__: dprint("Our external address may be: ", message.payload.destination_address)
            self.external_address_vote(message.payload.destination_address, message.address)

            # update or insert the member who sent the request
            # self._database.execute(u"UPDATE user SET user = ? WHERE community = ? AND host = ? AND port = ?",
            #                        (message.authentication.member.database_id, message.community.database_id, unicode(address[0]), address[1]))

            routes.extend(message.payload.routes)

        # add routes in our candidate table
        self._update_routes_from_external_source(community, routes)

    def create_identity(self, community, store=True, forward=True):
        """
        Create a dispersy-identity message.

        The dispersy-identity message contains information on community.my_member.  Such as your
        public key and the IP address and port where you are reachable.

        Typically, every member is represented my the most recent dispersy-identity message that she
        created and provided to the network.  Generally one such message is created whenever a
        member joins an existing community for the first time, or when she creates a new community.

        @param community: The community for wich the dispersy-identity message will be created.
        @type community: Community

        @param store: When True the messages are stored (as defined by their message distribution
         policy) in the local dispersy database.  This parameter should (almost always) be True, its
         inclusion is mostly to allow certain debugging scenarios.
        @type store: bool

        @param forward: When True the messages are forwarded (as defined by their message
         destination policy) to other nodes in the community.  This parameter should (almost always)
         be True, its inclusion is mostly to allow certain debugging scenarios.
        @type store: bool
        """
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert isinstance(store, bool)
        assert isinstance(forward, bool)
        meta = community.get_meta_message(u"dispersy-identity")
        message = meta.implement(meta.authentication.implement(community.my_member),
                                 meta.distribution.implement(community.claim_global_time()),
                                 meta.destination.implement(),
                                 meta.payload.implement(self._my_external_address))
        self.store_update_forward([message], store, False, forward)
        return message

    def check_identity(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def on_identity(self, messages):
        """
        We received a dispersy-identity message.

        @see: create_identity

        @param address: The sender address.
        @type address: (string, int)

        @param message: The dispersy-identity message.
        @type message: Message.Implementation
        """
        for message in messages:
            assert message.name == u"dispersy-identity"
            if __debug__: dprint(message)
            host, port = message.payload.address
            # TODO: we should drop messages that contain invalid addresses... or at the very least we
            # should ignore the address part.

            # execute(u"INSERT OR IGNORE INTO candidate(community, host, port, incoming_time, outgoing_time) VALUES(?, ?, ?, DATETIME('now'), '2010-01-01 00:00:00')", (message.community.database_id, unicode(host), port))
            self._database.execute(u"UPDATE user SET host = ?, port = ? WHERE id = ?", (unicode(host), port, message.authentication.member.database_id))
            # execute(u"UPDATE identity SET packet = ? WHERE user = ? AND community = ?", (buffer(message.packet), message.authentication.member.database_id, message.community.database_id))
            # if self._database.changes == 0:
            #     execute(u"INSERT INTO identity(user, community, packet) VALUES(?, ?, ?)", (message.authentication.member.database_id, message.community.database_id, buffer(message.packet)))

        for message in messages:
            message.authentication.member.update()

    def create_identity_request(self, community, mid, addresses, forward=True):
        """
        Create a dispersy-identity-request message.

        To verify a message signature we need the corresponding public key from the member who made
        the signature.  When we are missing a public key, we can request a dispersy-identity message
        which contains this public key.

        The missing member is identified by the sha1 digest over the member key.  This mid can
        indicate multiple members, hence the dispersy-identity-response will contain one or more
        public keys.

        Most often we will need to request a dispersy-identity when we receive a message containing
        an, to us, unknown mid.  Hence, sending the request to the address where we got that message
        from is usually most effective.

        @see: create_identity

        @param community: The community for wich the dispersy-identity message will be created.
        @type community: Community

        @param mid: The 20 byte identifier for the member.
        @type mid: string

        @param address: The address to send the request to.
        @type address: (string, int)

        @param forward: When True the messages are forwarded (as defined by their message
         destination policy) to other nodes in the community.  This parameter should (almost always)
         be True, its inclusion is mostly to allow certain debugging scenarios.
        @type forward: bool
        """
        meta = community.get_meta_message(u"dispersy-identity-request")
        message = meta.implement(meta.authentication.implement(),
                                 meta.distribution.implement(community.global_time),
                                 meta.destination.implement(*addresses),
                                 meta.payload.implement(mid))
        self.store_update_forward([message], False, False, forward)
        return message

    def check_identity_request(self, messages):
        # we can not timeline.check this message because it uses the NoAuthentication policy
        return messages

    def on_identity_request(self, messages):
        """
        We received dispersy-identity-request messages.

        The message contains the mid of a member.  The sender would like to obtain one or more
        associated dispersy-identity messages.

        @see: create_identity_request

        @param messages: The dispersy-identity message.
        @type messages: [Message.Implementation]
        """
        meta = messages[0].community.get_meta_message(u"dispersy-identity")
        for message in messages:
            # todo: we are assuming that no more than 10 members have the same sha1 digest.
            # sql = u"SELECT identity.packet FROM identity JOIN user ON user.id = identity.user WHERE identity.community = ? AND user.mid = ? LIMIT 10"
            sql = u"""SELECT packet
                FROM sync
                JOIN user ON user.id = sync.user
                WHERE sync.community = ? AND sync.name = ? AND user.mid = ?
                LIMIT 10
                """
            packets = [str(packet) for packet, in self._database.execute(sql, (message.community.database_id, meta.database_id, buffer(message.payload.mid)))]
            if packets:
                self._send([message.address], packets)

    def create_subjective_set(self, community, cluster, members, reset=True, store=True, update=True, forward=True):
        if __debug__:
            from community import Community
            from member import Member
        assert isinstance(community, Community)
        assert isinstance(cluster, int)
        assert isinstance(members, (tuple, list))
        assert not filter(lambda member: not isinstance(member, Member), members)
        assert isinstance(reset, bool)
        assert isinstance(store, bool)
        assert isinstance(update, bool)
        assert isinstance(forward, bool)

        # modify the subjective set (bloom filter)
        try:
            subjective_set = community.get_subjective_set(community.my_member, cluster)
        except KeyError:
            subjective_set = BloomFilter(len(members), 0.1)
        if reset:
            subjective_set.clear()
        map(subjective_set.add, (member.public_key for member in members))

        # implement the message
        meta = community.get_meta_message(u"dispersy-subjective-set")
        message = meta.implement(meta.authentication.implement(community.my_member),
                                 meta.distribution.implement(community.claim_global_time()),
                                 meta.destination.implement(),
                                 meta.payload.implement(cluster, subjective_set))
        self.store_update_forward([message], store, update, forward)
        return message

    def check_subjective_set(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def on_subjective_set(self, messages):
        # we do not need to do anything here for now because we retrieve all information directly
        # from the database each time we need it.  Hence no in-memory actions needs to occur.  Note
        # that this data is immediately stored in the database when this method returns.
        pass

    def check_subjective_set_request(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def on_subjective_set_request(self, address, message):
        """
        We received a dispersy-subjective-set-request message.

        The dispersy-subjective-set-request message contains one member (20 byte sha1 digest) for
        which the subjective set is requested.  We will search our database for any maching
        subjective sets (there may be more, as the 20 byte sha1 digest may match more than one
        member) and sent them back.

        @see: create_subjective_set_request

        @param address: The sender address.
        @type address: (string, int)

        @param message: The dispersy-subjective-set-request message.
        @type message: Message.Implementation
        """
        if __debug__:
            from message import Message
        assert isinstance(message, Message.Implementation), type(message)
        assert message.name == u"dispersy-subjective-set-request"

        subjective_set_message_id = message.community.get_meta_message(u"dispersy-subjective-set")
        packets = []
        for member in message.payload.members:
            # retrieve the packet from the database
            try:
                packet, = self._database.execute(u"SELECT packet FROM sync WHERE community = ? AND user = ? AND name = ? LIMIT",
                                                 (message.community.database.id, member.database_id, subjective_set_message_id)).next()
            except StopIteration:
                continue
            packet = str(packet)

            # check that this is the packet we are looking for, i.e. has the right cluster
            conversion = self.get_conversion(packet[:22])
            subjective_set_message = conversion.decode_message(packet)
            if subjective_set_message.destination.cluster == message.payload.clusters:
                packets.append(packet)

        if packets:
            self._send([address], [packet])

    # def create_subjective_set_request(community, community, cluster, members, update_locally=True, store_and_forward=True):
    #     if __debug__:
    #         from community import Community
    #         from member import Member
    #     assert isinstance(community, Community)
    #     assert isinstance(cluster, int)
    #     assert isinstance(members, (tuple, list))
    #     assert not filter(lambda member: not isinstance(member, Member), members)
    #     assert isinstance(update_locally, bool)
    #     assert isinstance(store_and_forward, bool)

    #     # implement the message
    #     meta = community.get_meta_message(u"dispersy-subjective-set-request")
    #     message = meta.implement(meta.authentication.implement(),
    #                              meta.distribution.implement(community.global_time),
    #                              meta.destination.implement(),
    #                              meta.payload.implement(cluster, members))

    #     if update_locally:
    #         assert community._timeline.check(message)
    #         message.handle_callback(("", -1), message)

    #     if store_and_forward:
    #         self.store_and_forward([message])

    #     return message

#     def create_similarity(self, community, meta_message, keywords, update_locally=True, store_and_forward=True):
#         """
#         Create a dispersy-similarity message.

#         The SimilarityDestination policy allows messages to be disseminated between members that are
#         deemed to be similar.  Calculating how similar members are is done using similarity data
#         disseminated using dispersy-similarity messages.

#         A dispersy-similarity message contains a bitstream, in the form of a one slice bloom filter,
#         which is filled with items, in the form of keywords.  Each keyword sets one bit in the bloom
#         filter to True, assuming that this bit was previously False.

#         Each message that uses the SimilarityDestination policy can have its own similarity value
#         associated to it, depending on the value of the meta_message.destination.cluster parameter.

#         For example: we have a meta_message called 'forum-post' that uses the SimilarityDestination
#         policy.  First we define that we are similar to peers with the words 'candy', 'chips', and
#         'food' by calling create_similarity(meta_message, ['candy', 'chips', 'food']).  Now we can
#         send a forum-post message using meta_message.implement(...) that will be disseminated based
#         on our and their similarity.

#         The create_similarity method can me called repeatedly.  Each time a new dispersy-similarity
#         message will be generated and disseminated across the community.  Only the most recent value
#         is propagated.

#         @param community: The community for wich the dispersy-similarity message will be created.
#         @type community: Community

#         @param message: The meta message for which we are definding the similarity.
#         @type message: Message

#         @param keywords: The keywords that are used to populate the similarity bitstring.
#         @type timeout: [string]

#         @param update_locally: When True the community.on_authorize_message is called with each
#          created message.  This parameter should (almost always) be True, its inclusion is mostly to
#          allow certain debugging scenarios.
#         @type update_locally: bool

#         @param store_and_forward: When True the created messages are stored (as defined by the
#          message distribution policy) in the local Dispersy database and the messages are forewarded
#          to other peers (as defined by the message destination policy).  This parameter should
#          (almost always) be True, its inclusion is mostly to allow certain debugging scenarios.
#         @type store_and_forward: bool

#         @note: Multiple dispersy-similarity messages are not possible yet.  Hence using multiple
#          messages with the SimilarityDestination and different cluster values will not work.
#         """
#         assert isinstance(community, Community)
#         assert isinstance(meta_message, Message)
#         assert isinstance(keywords, (tuple, list))
#         assert not filter(lambda x: not isinstance(x, str), keywords)
#         assert isinstance(update_locally, bool)
#         assert isinstance(store_and_forward, bool)

#         meta = community.get_meta_message(u"dispersy-similarity")

#         # BloomFilter created with 1 slice and defined number of bits
#         similarity = BloomFilter(1, meta_message.destination.size)
#         map(similarity.add, keywords)

#         # store into db
#         self._database.execute(u"INSERT OR REPLACE INTO my_similarity(community, user, cluster, similarity) VALUES(?, ?, ?, ?)",
#                                (community.database_id,
#                                 community.my_member.database_id,
#                                 meta_message.destination.cluster,
#                                 buffer(str(similarity))))

#         similarity = self._regulate_similarity(community, meta_message.destination)

#         # implement the message
#         message = meta.implement(meta.authentication.implement(community.my_member),
#                                  meta.distribution.implement(community.claim_global_time()),
#                                  meta.destination.implement(),
#                                  meta.payload.implement(meta_message.destination.identifier, similarity))

#         if store_and_forward:
#             self.store_and_forward([message])

#         if update_locally:
#             assert community._timeline.check(message)
#             message.handle_callback(("", -1), message)

#         return message

#     def check_similarity(self, address, message):
#         if not message.community._timeline.check(message):
#             raise DropMessage("TODO: implement delay of proof")

#     def on_similarity(self, address, message):
#         """
#         We received a dispersy-similarity message.

#         The message contains a bloom-filter with only one slice that represents the sphere of
#         influence of the creator of the message.

#         We store this bloomfilter in our database and later use it, when we receive a dispersy-sync
#         message, to check if we need to synchronize certain messages between members.

#         @see create_similarity

#         @param address: The sender address.
#         @type address: (string, int)

#         @param message: The dispersy-similarity message.
#         @type message: Message.Implementation
#         """
#         if __debug__:
#             from message import Message
#         assert isinstance(message, Message.Implementation)

#         self._database.execute(u"INSERT OR REPLACE INTO similarity(community, user, cluster, similarity, packet) VALUES(?, ?, ?, ?, ?)",
#                                (message.community.database_id,
#                                 message.authentication.member.database_id,
#                                 message.payload.cluster,
#                                 buffer(str(message.payload.similarity)),
#                                 buffer(message.packet)))

#     def _regulate_similarity(self, community, similarity_destination):
#         """
#         Regulate the BloomFilter similarity by randomly inserting extra bits until the number of
#         bits is at least the minumum amound of bits as defined in similarity_destination

#         @todo: figure out this method... is a bit messy and doesn't do anything yet.  Randomness
#          should be replaced by something usefull to promote semantic clustering.
#         """
#         # assert here
#         if __debug__:
#             from destination import SimilarityDestination
#         assert isinstance(similarity_destination, SimilarityDestination)

#         minimum_bits = similarity_destination.minimum_bits
#         maximum_bits = similarity_destination.maximum_bits

#         # fetch my_similarity from db
#         try:
#             my_similarity, = self._database.execute(u"SELECT similarity FROM my_similarity WHERE community == ? AND user == ? AND cluster == ? LIMIT 1",
#                                                     (community.database_id, community.my_member.database_id, similarity_destination.cluster)).next()
#         except StopIteration:
#             raise ValueError(u"Similarity not found in database")

#         # the database returns <buffer> types, we use the binary
#         # <str> type internally
#         similarity = BloomFilter(str(my_similarity), 0)

#         # todo: make this into a bloomfilter method
#         # count the 1's
#         set_bits = 0
#         for c in similarity._bytes.tostring():
#             s = "{0:08d}".format(int(bin(ord(c))[2:]))
#             for bit in s:
#                 if bit == '1':
#                     set_bits += 1

#         if set_bits > maximum_bits:
#             raise ValueError("To many bits set in the similarity")

#         # todo: make this into a bloomfilter method (the setting of specific bits)
#         # add new bits
#         new_bits = 0
#         check = 0b1
#         while new_bits < minimum_bits - set_bits:
#             for b in range(len(similarity._bytes)):
#                 if not similarity._bytes[b] & check:
#                     similarity._bytes[b] |= check
#                     new_bits += 1
#             check <<= 1

#         return similarity

#     # todo: implement a create_similarity_request method
#     # def create_similarity_request(self,

#     def check_similarity_request(self, address, message):
#         if not message.community._timeline.check(message):
#             raise DropMessage("TODO: implement delay of proof")

#     def on_similarity_request(self, address, message):
#         """
#         We received a dispersy-similarity-request message.

#         The dispersy-similarity-request message contains a list of members for which the similarity
#         is requested.  We will search out database for any similarity data that we can find and send
#         them back.

#         @see: create_similarity_request

#         @param address: The sender address.
#         @type address: (string, int)

#         @param message: The dispersy-signature-request message.
#         @type message: Message.Implementation
#         """
#         if __debug__:
#             from message import Message
#         assert isinstance(message, Message.Implementation), type(message)
#         assert message.name == u"dispersy-similarity-request"

#         for member in message.payload.members:
#             try:
#                 packet, = self._database.execute(u"SELECT packet FROM similarity WHERE community = ? AND user = ? AND cluster = ? LIMIT 1",
#                                                  (message.community.database.id, member.database_id, message.payload.cluster)).next()
#             except StopIteration:
#                 continue

#             self._send([address], [packet])
#             if __debug__: log("dispersy.log", "dispersy-missing-sequence - send back packet", length=len(packet), packet=packet, low=message.payload.missing_low, high=message.payload.missing_high)

    def create_signature_request(self, community, message, response_func, response_args=(), timeout=10.0, store=True, forward=True):
        """
        Create a dispersy-signature-request message.

        The dispersy-signature-request message contains a sub-message that is to be signed my
        multiple members.  The sub-message must use the MultiMemberAuthentication policy in order to
        store the multiple members and their signatures.

        Typically, each member that should add a signature will receive the
        dispersy-signature-request message.  If they choose to add their signature, a
        dispersy-signature-response message is send back.  This in turn will result in a call to
        response_func with the message that now has one additional signature.

        Each dispersy-signed-response message will result in one call to response_func.  The
        parameters for this call are the address where the response came from and the sub-message.
        When all signatures are available the property sub-message.authentication.is_signed will be
        True.

        If not all members sent a reply withing timeout seconds, one final call to response_func is
        made with parameters ('', -1) and None, for the address and message respectively.

        @param community: The community for wich the dispersy-signature-request message will be
         created.
        @type community: Community

        @param message: The message that is to receive multiple signatures.
        @type message: Message.Implementation

        @param response_func: The method that is called when a signature or a timeout is received.
        @type response_func: callable method

        @param response_args: Optional arguments added when calling response_func.
        @type response_args: tuple

        @param timeout: How long before a timeout is generated.
        @type timeout: float

        @param store: When True the messages are stored (as defined by their message distribution
         policy) in the local dispersy database.  This parameter should (almost always) be True, its
         inclusion is mostly to allow certain debugging scenarios.
        @type store: bool

        @param forward: When True the messages are forwarded (as defined by their message
         destination policy) to other nodes in the community.  This parameter should (almost always)
         be True, its inclusion is mostly to allow certain debugging scenarios.
        @type store: bool
        """
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert isinstance(message, Message.Implementation)
        assert isinstance(message.authentication, MultiMemberAuthentication.Implementation)
        assert hasattr(response_func, "__call__")
        assert isinstance(response_args, tuple)
        assert isinstance(timeout, float)
        assert isinstance(store, bool)
        assert isinstance(forward, bool)

        # the members that need to sign
        members = [member for signature, member in message.authentication.signed_members if not (signature or isinstance(member, PrivateMember))]

        # the dispersy-signature-request message that will hold the
        # message that should obtain more signatures
        meta = community.get_meta_message(u"dispersy-signature-request")
        request = meta.implement(meta.authentication.implement(),
                                 meta.distribution.implement(community.global_time),
                                 meta.destination.implement(*members),
                                 meta.payload.implement(message))

        # set callback and timeout
        identifier = sha1(request.packet).digest()
        footprint = community.get_meta_message(u"dispersy-signature-response").generate_footprint(payload=(identifier,))
        self.await_message(footprint, self._on_signature_response, (request, response_func, response_args), timeout, len(members))

        self.store_update_forward([request], store, False, forward)
        return request

    # def check_similarity_request(self, messages):
    #     for message in messages:
    #         if not message.community._timeline.check(message):
    #             yield DropMessage("TODO: implement delay of proof")
    #             continue

    #         # submsg contains the message that should receive multiple signatures
    #         submsg = message.payload.message

    #         has_private_member = False
    #         try:
    #             for is_signed, member in submsg.authentication.signed_members:
    #                 # Security: do NOT allow to accidentally sign with MasterMember.
    #                 if isinstance(member, MasterMember):
    #                     raise DropMessage("You may never ask for a MasterMember signature")

    #                 # is this signature missing, and could we provide it
    #                 if not is_signed and isinstance(member, PrivateMember):
    #                     has_private_member = True
    #                     break
    #         except DropMessage, exception:
    #             yield exception
    #             continue

    #         # we must be one of the members that needs to sign
    #         if not has_private_member:
    #             yield DropMessage("Nothing to sign")
    #             continue

    #         # the message must be valid
    #         if not submsg.community._timeline.check(submsg):
    #             yield DropMessage("Doesn't fit timeline")
    #             continue

    #         # the community must allow this signature
    #         if not submsg.authentication.allow_signature_func(submsg):
    #             yield DropMessage("We choose not to add our signature")
    #             continue

    #         yield message

    def check_signature_request(self, messages):
        for message in messages:
            # we can not timeline.check this message because it uses the NoAuthentication policy

            # submsg contains the message that should receive multiple signatures
            submsg = message.payload.message

            has_private_member = False
            try:
                for is_signed, member in submsg.authentication.signed_members:
                    # Security: do NOT allow to accidentally sign with MasterMember.
                    if isinstance(member, MasterMember):
                        raise DropMessage(message, "You may never ask for a MasterMember signature")

                    # is this signature missing, and could we provide it
                    if not is_signed and isinstance(member, PrivateMember):
                        has_private_member = True
                        break
            except DropMessage, exception:
                yield exception
                continue

            # we must be one of the members that needs to sign
            if not has_private_member:
                yield DropMessage(message, "Nothing to sign")
                continue

            # we can not timeline.check the submessage because it uses the MultiMemberAuthentication policy
            # # the message that we are signing must be valid according to our timeline
            # # if not message.community._timeline.check(submsg):
            # #     raise DropMessage("Does not fit timeline")

            # the community must allow this signature
            if not submsg.authentication.allow_signature_func(submsg):
                yield DropMessage(message, "We choose not to add our signature")
                continue

            # allow message
            yield message

    def on_signature_request(self, messages):
        """
        We received a dispersy-signature-request message.

        This message contains a sub-message (message.payload) that the message creator would like to
        have us sign.  The message may, or may not, have already been signed by some of the other
        members.  Furthermore, we can choose for ourselves if we want to add our signature to the
        sub-message or not.

        Once we have determined that we could provide a signature and that the sub-message is valid,
        from a timeline perspective, we will ask the community to say yes or no to adding our
        signature.  This question is done by calling the
        sub-message.authentication.allow_signature_func method.

        Only when the allow_signature_func method returns True will we add our signature.  In this
        case a dispersy-signature-response message is send to the creator of the message, the first
        one in the authentication list.

        Note that if for whatever reason we can add multiple signatures, i.e. we have the private
        key for more that one member signing the sub-message, we will send one
        dispersy-signature-response message for each signature that we can supply.

        @see: create_signature_request

        @param address: The sender address.
        @type address: (string, int)

        @param message: The dispersy-signature-request message.
        @type message: Message.Implementation
        """
        if __debug__:
            from message import Message
            from authentication import MultiMemberAuthentication

        responses = []
        for message in messages:
            assert isinstance(message, Message.Implementation), type(message)
            assert isinstance(message.payload.message, Message.Implementation), type(message.payload.message)
            assert isinstance(message.payload.message.authentication, MultiMemberAuthentication.Implementation), type(message.payload.message.authentication)

            # submsg contains the message that should receive multiple signatures
            submsg = message.payload.message

            # create signature(s) and reply
            identifier = sha1(message.packet).digest()
            first_signature_offset = len(submsg.packet) - sum([member.signature_length for member in submsg.authentication.members])
            for member in submsg.authentication.members:
                if isinstance(member, PrivateMember):
                    signature = member.sign(submsg.packet, 0, first_signature_offset)

                    # send response
                    meta = message.community.get_meta_message(u"dispersy-signature-response")
                    responses.append(meta.implement(meta.authentication.implement(),
                                                    meta.distribution.implement(message.community.global_time),
                                                    meta.destination.implement(message.address,),
                                                    meta.payload.implement(identifier, signature)))

        self.store_update_forward(responses, False, False, True)

    def check_signature_response(self, messages):
        # we can not timeline.check this message because it uses the NoAuthentication policy
        return messages

    def on_signature_response(self, messages):
        pass

    def _on_signature_response(self, response, request, response_func, response_args):
        """
        A Trigger matched a received dispersy-signature-response message.

        We sent out a dispersy-signature-request, though the create_signature_request method, and
        have now received a dispersy-signature-response in reply.  If the signature is valid, we
        will call response_func with address and sub-message, where sub-message is the message
        parameter given to the create_signature_request method.

        When a timeout occurs the response_func will also be called, although now the address and
        sub-message parameters will be set to ('', -1) and None, respectively.

        Note that response_func is also called when the sub-message does not yet contain all the
        signatures.  This can be checked using sub-message.authentication.is_signed.

        @see: create_signature_request

        @param address: The sender address.
        @type address: (string, int)

        @param response: The dispersy-signature-response message.
        @type response: Message.Implementation

        @param request: The dispersy-dispersy-request message.
        @type message: Message.Implementation

        @param response_func: The method that is called when a signature or a timeout is received.
        @type response_func: callable method

        @param response_args: Optional arguments added when calling response_func.
        @type response_args: tuple
        """
        assert response is None or isinstance(response, Message.Implementation)
        assert response is None or response.name == u"dispersy-signature-response"
        assert isinstance(request, Message.Implementation)
        assert request.name == u"dispersy-signature-request"
        assert hasattr(response_func, "__call__")
        assert isinstance(response_args, tuple)

        # check for timeout
        if response is None:
            response_func(response, *response_args)

        else:
            # the multi signed message
            submsg = request.payload.message

            first_signature_offset = len(submsg.packet) - sum([member.signature_length for member in submsg.authentication.members])
            body = submsg.packet[:first_signature_offset]

            for signature, member in submsg.authentication.signed_members:
                if not signature and member.verify(body, response.payload.signature):
                    submsg.authentication.set_signature(member, response.payload.signature)
                    response_func(submsg, *response_args)

                    # assuming this signature only matches one member, we can break
                    break

    def check_missing_sequence(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def on_missing_sequence(self, address, message):
        """
        We received a dispersy-missing-sequence message.

        The message contains a member and a range of sequence numbers.  We will send the messages,
        up to a certain limit, in this range back to the sender.

        To limit the amount of bandwidth used we will not sent back more data after a certain amount
        has been sent.  This magic number is subject to change.

        @param address: The sender address.
        @type address: (string, int)

        @param message: The dispersy-missing-sequence message.
        @type message: Message.Implementation

        @todo: we need to optimise this to include a bandwidth throttle.  Otherwise a node can
         easilly force us to send arbitrary large amounts of data.
        """
        if __debug__:
            from message import Message
        assert isinstance(message, Message)
        assert message.name == u"dispersy-missing-sequence"

        # we limit the response by byte_limit bytes
        byte_limit = message.community.dispersy_missing_sequence_response_limit

        payload = message.payload
        for packet, in self._database.execute(u"SELECT packet FROM sync_full WHERE community = ? and sequence >= ? AND sequence <= ? ORDER BY sequence LIMIT ?",
                                              (payload.message.community.database_id, payload.missing_low, payload.missing_high, packet_limit)):
            if __debug__:
                # update statistics
                self._total_send += len(packet)
                dprint("Syncing ", len(packet), " bytes from sync_full to " , address[0], ":", address[1])

            self._socket.send(address, packet)
            byte_limit -= len(packet)
            if byte_limit > 0:
                if __debug__: dprint("Bandwidth throttle")
                break

    def check_sync(self, messages):
        """
        We received a dispersy-sync message.

        The message contains a bloom-filter that needs to be checked.  If we find any messages that
        are not in the bloom-filter, we will sent those to the sender.

        To limit the amount of bandwidth used we will not sent back more data after a certain amount
        has been sent.  This magic number is subject to change.

        @param address: The sender address.
        @type address: (string, int)

        @param message: The dispersy-sync message.
        @type message: Message.Implementation

        @todo: we should look into optimizing this method, currently it just sends back data.
         Therefore, if multiple nodes receive this dispersy-sync message they will probably all send
         the same messages back.  So we need to make things smarter!

        @todo: we need to optimise this to include a bandwidth throttle.  Otherwise a node can
         easilly force us to send arbitrary large amounts of data.
        """
        # def get_similarity(cluster):
        #     try:
        #         similarity, = self._database.execute(u"SELECT similarity FROM similarity WHERE community = ? AND user = ? AND cluster = ?",
        #                                              (message.community.database_id, message.authentication.member.database_id, cluster)).next()
        #     except StopIteration:
        #         # this message should never have been stored in the database without a similarity.
        #         # Thus the Database is corrupted.
        #         raise DelayMessageBySimilarity(message, cluster)

        #     for msg in message.community.get_meta_messages():
        #         if isinstance(msg.destination, SimilarityDestination) and msg.destination.cluster == cluster:
        #             threshold = msg.destination.threshold
        #             break
        #     else:
        #         raise NotImplementedError("No messages are defined that use this cluster")

        #     return BloomFilter(str(similarity), 0), threshold

        def get_packets(community_id, time_low, time_high):
            # first priority is to return the 'in-order' packets
            sql = u"""SELECT sync.packet, sync.name, user.public_key
                      FROM sync
                      JOIN user ON user.id = sync.user
                      WHERE sync.community = ? AND synchronization_direction = 1 AND sync.global_time BETWEEN ? AND ?
                      ORDER BY sync.global_time ASC"""
            for tup in self._database.execute(sql, (community_id, time_low, time_high)):
                yield tup

            # second priority is to return the 'out-order' packets
            sql = u"""SELECT sync.packet, sync.name, user.public_key
                      FROM sync
                      JOIN user ON user.id = sync.user
                      WHERE sync.community = ? AND synchronization_direction = 2 AND sync.global_time BETWEEN ? AND ?
                      ORDER BY sync.global_time DESC"""
            for tup in self._database.execute(sql, (community_id, time_low, time_high)):
                yield tup

            # third priority is to return the 'random-order' packets
            sql = u"""SELECT sync.packet, sync.name, user.public_key
                      FROM sync
                      JOIN user ON user.id = sync.user
                      WHERE sync.community = ? AND synchronization_direction = 3 AND sync.global_time BETWEEN ? AND ?
                      ORDER BY RANDOM()"""
            for tup in self._database.execute(sql, (community_id, time_low, time_high)):
                yield tup

        community = messages[0].community

        # similarity_cache = {}

        # obtain all available messages for this community
        meta_messages = dict((meta_message.database_id, meta_message) for meta_message in community.get_meta_messages())

        for message in messages:
            assert message.name == u"dispersy-sync", "this method is called in batches, i.e. community and meta message grouped together"
            assert message.community == community, "this method is called in batches, i.e. community and meta message grouped together"

            if not community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue

            # obtain all subjective sets for the sender of the dispersy-sync message
            subjective_sets = community.get_subjective_sets(message.authentication.member)

            # we limit the response by byte_limit bytes
            byte_limit = community.dispersy_sync_response_limit

            bloom_filter = message.payload.bloom_filter
            time_low = message.payload.time_low
            time_high = message.payload.time_high if message.payload.has_time_high else community.global_time
            packets = []

            for packet, meta_message_id, packet_public_key in get_packets(community.database_id, time_low, time_high):
                packet = str(packet)
                packet_public_key = str(packet_public_key)

                if not packet in bloom_filter:
                    # check if the packet uses the SubjectiveDestination policy
                    packet_meta = meta_messages.get(meta_message_id, None)
                    if packet_meta and isinstance(packet_meta.destination, SubjectiveDestination):
                        packet_cluster = packet_meta.destination.cluster

                        # we need the subjective set for this particular cluster
                        if not packet_cluster in subjective_sets:
                            if __debug__: dprint("Subjective set not available (not ", packet_cluster, " in ", subjective_sets.keys(), ")")
                            yield DelayMessageBySubjectiveSet(message, packet_cluster)
                            break

                        # is packet_public_key in the subjective set
                        if not packet_public_key in subjective_sets[packet_cluster]:
                            # do not send this packet: not in the requester's subjective set
                            continue

                    # # check if the packet uses the SimilarityDestination policy
                    # if similarity_cluster:
                    #     similarity, threshold = similarity_cache.get(similarity_cluster, (None, None))
                    #     if similarity is None:
                    #         similarity, threshold = get_similarity(similarity_cluster)
                    #         similarity_cache[similarity_cluster] = (similarity, threshold)

                    #     if similarity.bic_occurrence(BloomFilter(str(packet_similarity), 0)) < threshold:
                    #         if __debug__: dprint("do not send this packet: not similar")
                    #         # do not send this packet: not similar
                    #         continue

                    packets.append(packet)
                    byte_limit -= len(packet)
                    if byte_limit <= 0:
                        if __debug__:
                            dprint("bandwidth throttle")
                        break

            # let the message be processed, although that will not actually result in any processing
            # since we choose to already do everything...
            yield message

            if packets:
                if __debug__: dprint("syncing ", len(packets), " packets (", sum(len(packet) for packet in packets), " bytes) over [", time_low, ":", time_high, "] to " , message.address[0], ":", message.address[1])
                self._send([message.address], packets)

    def on_sync(self, messages):
        # everything has already been done in check_sync.
        pass

    def create_authorize(self, community, permission_triplets, sign_with_master=False, store=True, update=True, forward=True):
        """
        Grant permissions to members in a community.

        This method will generate a message that grants the permissions in permission_triplets.
        Each item in permission_triplets contains (Member, Message, permission) where permission is
        either u'permit', u'authorize', or u'revoke'.

        By default, community.my_member is doing the authorization.  This means, that
        community.my_member must have the authorize permission for each of the permissions that she
        is authorizing.

        >>> # Authorize Bob to use Permit payload for 'some-message'
        >>> from Payload import Permit
        >>> bob = Member.get_instance(bob_public_key)
        >>> msg = self.get_meta_message(u"some-message")
        >>> self.create_authorize(community, [(bob, msg, u'permit')])

        @param community: The community where the permissions must be applied.
        @type sign_with_master: Community

        @param permission_triplets: The permissions that are granted.  Must be a list or tuple
         containing (Member, Message, permission) tuples.
        @type permissions_pairs: [(Member, Message, string)]

        @param sign_with_master: When True community.master_member is used to sign the authorize
         message.  Otherwise community.my_member is used.
        @type sign_with_master: bool

        @param store: When True the messages are stored (as defined by their message distribution
         policy) in the local dispersy database.  This parameter should (almost always) be True, its
         inclusion is mostly to allow certain debugging scenarios.
        @type store: bool

        @param update: When True the messages are passed to their handle_callback methods.  This
         parameter should (almost always) be True, its inclusion is mostly to allow certain
         debugging scenarios.
        @type update: bool

        @param forward: When True the messages are forwarded (as defined by their message
         destination policy) to other nodes in the community.  This parameter should (almost always)
         be True, its inclusion is mostly to allow certain debugging scenarios.
        @type store: bool
        """
        if __debug__:
            from community import Community
            from member import Member
            from message import Message
            assert isinstance(community, Community)
            assert isinstance(permission_triplets, (tuple, list))
            for triplet in permission_triplets:
                assert isinstance(triplet, tuple)
                assert len(triplet) == 3
                assert isinstance(triplet[0], Member)
                assert isinstance(triplet[1], Message)
                assert isinstance(triplet[2], unicode)
                assert triplet[2] in (u'permit', u'authorize', u'revoke')

        meta = community.get_meta_message(u"dispersy-authorize")
        message = meta.implement(meta.authentication.implement(community.master_member if sign_with_master else community.my_member),
                                 meta.distribution.implement(community.claim_global_time()),
                                 meta.destination.implement(),
                                 meta.payload.implement(permission_triplets))

        self.store_update_forward([message], store, update, forward)
        return message

    def check_authorize(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def on_authorize(self, messages):
        """
        Process a dispersy-authorize message.

        This method is called to process a dispersy-authorize message.  This message is either
        received from an external source or locally generated.

        When the message is locally generated the address will be set to ('', -1).

        @param address: The address from where we received this message.
        @type address: (string, int)

        @param message: The received message.
        @type message: Message.Implementation
        @raise DropMessage: When unable to verify that this message is valid.
        @todo: We should raise a DelayMessageByProof to ensure that we request the proof for this
         message immediately.
        """
        for message in messages:
            message.community._timeline.authorize(message.authentication.member, message.distribution.global_time, message.payload.permission_triplets)

    def create_revoke(self, community, permission_triplets, sign_with_master=False, store=True, update=True, forward=True):
        """
        Revoke permissions from a members in a community.

        This method will generate a message that revokes the permissions in permission_triplets.
        Each item in permission_triplets contains (Member, Message, permission) where permission is
        either u'permit', u'authorize', or u'revoke'.

        By default, community.my_member is doing the revoking.  This means, that community.my_member
        must have the revoke permission for each of the permissions that she is revoking.

        >>> # Revoke the right of Bob to use Permit payload for 'some-message'
        >>> from Payload import Permit
        >>> bob = Member.get_instance(bob_public_key)
        >>> msg = self.get_meta_message(u"some-message")
        >>> self.create_revoke(community, [(bob, msg, u'permit')])

        @param community: The community where the permissions must be applied.
        @type sign_with_master: Community

        @param permission_triplets: The permissions that are revoked.  Must be a list or tuple
         containing (Member, Message, permission) tuples.
        @type permissions_pairs: [(Member, Message, string)]

        @param sign_with_master: When True community.master_member is used to sign the revoke
         message.  Otherwise community.my_member is used.
        @type sign_with_master: bool

        @param store: When True the messages are stored (as defined by their message distribution
         policy) in the local dispersy database.  This parameter should (almost always) be True, its
         inclusion is mostly to allow certain debugging scenarios.
        @type store: bool

        @param update: When True the messages are passed to their handle_callback methods.  This
         parameter should (almost always) be True, its inclusion is mostly to allow certain
         debugging scenarios.
        @type update: bool

        @param forward: When True the messages are forwarded (as defined by their message
         destination policy) to other nodes in the community.  This parameter should (almost always)
         be True, its inclusion is mostly to allow certain debugging scenarios.
        @type store: bool
        """
        if __debug__:
            from community import Community
            from member import Member
            from message import Message
            assert isinstance(community, Community)
            assert isinstance(permission_triplets, (tuple, list))
            for triplet in permission_triplets:
                assert isinstance(triplet, tuple)
                assert len(triplet) == 3
                assert isinstance(triplet[0], Member)
                assert isinstance(triplet[1], Message)
                assert isinstance(triplet[2], unicode)
                assert triplet[2] in (u'permit', u'authorize', u'revoke')

        meta = community.get_meta_message(u"dispersy-revoke")
        message = meta.implement(meta.authentication.implement(community.master_member if sign_with_master else community.my_member),
                                 meta.distribution.implement(community.claim_global_time()),
                                 meta.destination.implement(),
                                 meta.payload.implement(permission_triplets))

        self.store_update_forward([message], store, update, forward)
        return message

    def check_revoke(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def on_revoke(self, messages):
        """
        Process a dispersy-revoke message.

        This method is called to process a dispersy-revoke message.  This message is either received
        from an external source or locally generated.

        When the message is locally generated the address will be set to ('', -1).

        @param address: The address from where we received this message.
        @type address: (string, int)

        @param message: The received message.
        @type message: Message.Implementation
        @raise DropMessage: When unable to verify that this message is valid.
        @todo: We should raise a DelayMessageByProof to ensure that we request the proof for this
         message immediately.
        """
        for message in messages:
            message.community._timeline.revoke(message.authentication.member, message.distribution.global_time, message.payload.permission_triplets)

    def create_destroy_community(self, community, degree, sign_with_master=False, store=True, update=True, forward=True):
        if __debug__:
            from community import Community
        assert isinstance(community, Community)
        assert isinstance(degree, unicode)
        assert degree in (u"soft-kill", u"hard-kill")

        meta = community.get_meta_message(u"dispersy-destroy-community")
        message = meta.implement(meta.authentication.implement(community.master_member if sign_with_master else community.my_member),
                                 meta.distribution.implement(community.claim_global_time()),
                                 meta.destination.implement(),
                                 meta.payload.implement(degree))

        # in this special case we need to forward the message before processing it locally.
        # otherwise the candidate table will have been cleaned and we won't have any destination
        # addresses.
        self.store_update_forward([message], False, False, forward)

        # now store and update without forwarding.  forwarding now will result in new entries in our
        # candidate table that we just cleane.
        self.store_update_forward([message], store, update, False)
        return message

    def check_destroy_community(self, messages):
        for message in messages:
            if not message.community._timeline.check(message):
                yield DropMessage(message, "TODO: implement delay of proof")
                continue
            yield message

    def on_destroy_community(self, messages):
        if __debug__:
            from community import Community

        for message in messages:
            assert message.name == u"dispersy-destroy-community"
            if __debug__: dprint(message)

            community = message.community

            # let the community code cleanup first.
            new_classification = community.dispersy_cleanup_community(message)
            assert issubclass(new_classification, Community)

            # community cleanup is done.  Now we will cleanup the dispersy database.

            if message.payload.is_soft_kill:
                # soft-kill: The community is frozen.  Dispersy will retain the data it has obtained.
                # However, no messages beyond the global-time of the dispersy-destroy-community message
                # will be accepted.  Responses to dispersy-sync messages will be send like normal.
                raise NotImplementedError()

            elif message.payload.is_hard_kill:
                # hard-kill: The community is destroyed.  Dispersy will throw away everything except the
                # dispersy-destroy-community message and the authorize chain that is required to verify
                # this message.  The community should also remove all its data and cleanup as much as
                # possible.

                # delete everything except (a) all dispersy-destroy-community messages (these both
                # authorize and revoke the usage of this message) and (b) the associated
                # dispersy-identity messages to verify the dispersy-destroy-community messages.

                # todo: this should be made more efficient.  not all dispersy-destroy-community messages
                # need to be kept.  Just the ones in the chain to authorize the message that has just
                # been received.

                authorize_message_id = community.get_meta_message(u"dispersy-authorize").database_id
                destroy_message_id = community.get_meta_message(u"dispersy-destroy-community").database_id
                identity_message_id = community.get_meta_message(u"dispersy-identity").database_id

                # TODO we should only remove the 'path' of authorize and identity messages
                # leading to the destroy message

                # 1. remove all except the dispersy-authorize, dispersy-destroy-community, and
                # dispersy-identity messages
                self._database.execute(u"DELETE FROM sync WHERE community = ? AND NOT (name = ? OR name = ? OR name = ?)", (community.database_id, authorize_message_id, destroy_message_id, identity_message_id))

                # 2. cleanup the reference_user_sync table.  however, we should keep the ones
                # that are still referenced
                self._database.execute(u"DELETE FROM reference_user_sync WHERE NOT EXISTS (SELECT * FROM sync WHERE community = ? AND sync.id = reference_user_sync.sync)", (community.database_id,))

                # 3. cleanup the candidate table.  we need nothing here anymore
                self._database.execute(u"DELETE FROM candidate WHERE community = ?", (community.database_id,))

            self.reclassify_community(community, new_classification)


    def _periodically_create_sync(self, community):
        """
        Periodically disperse the latest bloom filters for this community.

        Every N seconds one or more dispersy-sync message will be send for community.  This may
        result in members detecting that we are missing messages and them sending them to us.

        Note that there are several magic numbers involved:

         1. The frequency of calling _periodically_create_sync.  This is determined by
            self.dispersy_sync_interval.  This defaults to 20.0 (currently).

         2. Each interval up to L bloom filters are selected and sent in seperate dispersy-sync
            messages.  This is determined by the self.dispersy_sync_bloom_count.  This defaults to 2
            (currently).

         3. Each interval each dispersy-sync message is sent to up to C different members.  This is
            determined by the self.dispersy_sync_member_count.  This defaults to 10 (currently).

         4. How many bytes -at most- are sent back in response to a received dispersy-sync message.
            This is determined by the self.dispersy_sync_response_limit.  This defaults to 5KB.
        """
        meta = community.get_meta_message(u"dispersy-sync")
        while community.dispersy_sync_initial_delay > 0.0 and community.dispersy_sync_interval > 0.0:
            messages = [meta.implement(meta.authentication.implement(community.my_member),
                                       meta.distribution.implement(community.global_time),
                                       meta.destination.implement(),
                                       meta.payload.implement(time_low, time_high, bloom_filter))
                        for time_low, time_high, bloom_filter
                        in community.dispersy_sync_bloom_filters]
            if __debug__:
                for message in messages:
                    dprint("requesting sync in range [", message.payload.time_low, ":", message.payload.time_high if message.payload.time_high else "inf", "] (", community.get_classification(), ")")
            self.store_update_forward(messages, False, False, True)
            yield community.dispersy_sync_interval

    def _periodically_create_candidate_request(self, community):
        while community.dispersy_candidate_request_initial_delay > 0.0 and community.dispersy_candidate_request_interval > 0.0:
            minimal_age, maximal_age = community.dispersy_candidate_age_range
            limit = community.dispersy_candidate_limit
            sql = u"""SELECT host, port, STRFTIME('%s', DATETIME('now')) - STRFTIME('%s', incoming_time) AS age
                FROM candidate
                WHERE community = ? AND age BETWEEN ? AND ?
                ORDER BY age
                LIMIT ?"""
            candidates = [((str(host), port), float(age)) for host, port, age in self._database.execute(sql, (community.database_id, minimal_age, maximal_age, limit))]

            meta = community.get_meta_message(u"dispersy-candidate-request")
            authentication_impl = meta.authentication.implement(community.my_member)
            distribution_impl = meta.distribution.implement(community.global_time)
            conversion_version = community.get_conversion().version
            requests = [meta.implement(authentication_impl, distribution_impl, meta.destination.implement(candidate.address), meta.payload.implement(self._my_external_address, candidate.address, conversion_version, candidates))
                        for candidate
                        in self.yield_mixed_candidates(community, community.dispersy_candidate_request_member_count, community.dispersy_candidate_request_destination_diff_range, community.dispersy_candidate_request_destination_age_range)]
            self.store_update_forward(requests, False, False, True)
            yield community.dispersy_candidate_request_interval

    def _periodically_cleanup_database(self):
        # cleannup candidate tables
        while True:
            for community in self._communities.itervalues():
                self._database.execute(u"DELETE FROM candidate WHERE community = ? AND STRFTIME('%s', DATETIME('now')) - STRFTIME('%s', incoming_time) > ?",
                                       (community.database_id, community.dispersy_candidate_cleanup_age_threshold))
            yield 120.0

    def _periodically_stats(self):
        """
        Periodically write bandwidth statistics to a log file.
        """
        if __debug__:
            while True:
                grouped = {}
                for community in self._communities.itervalues():
                    classification = community.get_classification()
                    if not classification in grouped:
                        grouped[classification] = set()
                    grouped[classification].add(community.cid)
                communities = "; ".join("%s:%d" % (classification, len(cids)) for classification, cids in grouped.iteritems())
                dprint("in: ", self._total_received, "; out: ", self._total_send, "; ", communities)

                yield 5.0

    def _watchdog(self):
        """
        Periodically called to flush changes to disk, most importantly, it will catch the
        GeneratorExit exception when it is thrown to properly shutdown the database.
        """
        while True:
            try:
                yield 300.0
                # flush changes to disk every 5 minutes
                self._database.commit()
            except GeneratorExit:
                if __debug__: dprint("shutdown")
                self._database.commit()
                break

    def info(self, statistics=True, transfers=True, attributes=True, sync_ranges=True, database_sync=True):
        """
        Returns a dictionary with runtime statistical information.

        The dictionary should only contain simple data types such as dictionaries, lists, tuples,
        strings, integers, etc.  Just no objects, methods, and the like.

        Depending on __debug__ more or less information may be available.  Note that Release
        versions do NOT run __debug__ mode and will hence return less information.
        """
        # when something is removed or changed, the major version number is incremented.  when
        # somethind is added, the minor version number is incremented.

        # 1.1: added info["statistics"]
        # 1.2: bugfix in Community.free_sync_range, should free more ranges; changed
        #      dispersy_candidate_request_member_count to 3 down from 10

        info = {"version":1.2, "class":"Dispersy"}

        if statistics:
            info["statistics"] = self._statistics.reset()
            # if __debug__: dprint(info["statistics"], pprint=1)

        if __debug__:
            if transfers:
                info["transfers"] = {"total_received":self._total_received, "total_send":self._total_send}

        info["communities"] = []
        for community in self._communities.itervalues():
            community_info = {"classification":community.get_classification(), "hex_cid":community.cid.encode("HEX"), "global_time":community.global_time}
            info["communities"].append(community_info)

            if attributes:
                community_info["attributes"] = dict((attr, getattr(community, attr))
                                                    for attr
                                                    in ("dispersy_candidate_request_initial_delay",
                                                        "dispersy_candidate_request_interval",
                                                        "dispersy_candidate_age_range",
                                                        "dispersy_candidate_request_member_count",
                                                        "dispersy_candidate_request_destination_diff_range",
                                                        "dispersy_candidate_request_destination_age_range",
                                                        "dispersy_candidate_cleanup_age_threshold",
                                                        "dispersy_candidate_limit",
                                                        "dispersy_sync_initial_delay",
                                                        "dispersy_sync_interval",
                                                        "dispersy_sync_bloom_filter_error_rate",
                                                        "dispersy_sync_bloom_filter_bits",
                                                        "dispersy_sync_member_count",
                                                        "dispersy_sync_response_limit",
                                                        "dispersy_missing_sequence_response_limit"))

            if sync_ranges:
                community_info["sync_ranges"] = [{"time_low":range.time_low, "space_freed":range.space_freed, "space_remaining":range.space_remaining, "capacity":range.capacity}
                                                 for range
                                                 in community._sync_ranges]

            if database_sync:
                community_info["database_sync"] = dict(self._database.execute(u"SELECT name.value, COUNT(sync.id) FROM sync JOIN name ON name.id = sync.name WHERE community = ? GROUP BY sync.name", (community.database_id,)))

        if __debug__: dprint(info, pprint=True)
        return info
