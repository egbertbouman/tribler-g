import logging
from logging.handlers import SocketHandler, DEFAULT_TCP_LOGGING_PORT
import time
import random


rootlogger = logging.getLogger('')
rootlogger.setLevel(logging.DEBUG)
logger1 = logging.getLogger('myapp.area1')
#logger2 = logging.getLogger('myapp.area2')
socketh = SocketHandler('gamecast.no-ip.org', DEFAULT_TCP_LOGGING_PORT)
rootlogger.addHandler(socketh)

def foo():
    print 'foo()'
    x = 0
    try:
        y = 10 / x
    except Exception, e:
        pass
        #logging.getLogger('foo').exception("Please don't try to divide by zero")

while True:
    foo()
    #logger1.debug('Quick zephyrs blow, vexing daft Jim.')
    #logger1.info('How quickly daft jumping zebras vex.')
    #logger2.warning('Jail zesty vixen who grabbed pay from quack.')
    #logger2.error('The five boxing wizards jump quickly.')
    d = {'event_type' : 'MSG_SEND',
         'ip'         : '32.123.54.32',
         'port'       : 123,
         'permid'     : 'WFDd45fDDW'}
    msg = '.. to .. with payload ..'
    logger1.info(msg, extra=d)
    time.sleep(random.random()*10)

