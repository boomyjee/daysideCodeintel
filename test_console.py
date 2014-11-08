from SimpleWebSocketServer import WebSocket, SimpleWebSocketServer

class Autocomplete(WebSocket):
    def handleClose(self):
        print self.address, 'closed'

server = SimpleWebSocketServer('', 8000, Autocomplete)
server.serveforever()

x = "abc";
x.center(20);

mystring = "test";

import math

math.<|>