import os,sys

_lib = os.path.join(os.path.dirname(os.path.realpath(__file__)),"lib")
sys.path.insert(0, _lib)

from SimpleWebSocketServer import WebSocket, SimpleWebSocketServer
from codeintel import DaysideCodeIntel 

from daemon import Daemon
import json, os, sys, subprocess, time, codeintel, traceback

from SimpleWebSocketServer import WebSocket, SimpleWebSocketServer
from codeintel import DaysideCodeIntel

class View(codeintel.View):
    def on_complete(self,cplns,calltips,original_pos):
        msg = {
            "type":"complete",
            "id":self.data["id"],
            "completions":cplns,
            "calltips":calltips,
            "pos":original_pos
        }
        self.socket.sendMessage(json.dumps(msg))
        
    def on_goto_definition(self,defn):
        path = defn.path
        line = defn.line
        url = self.data['root_url'] + path[len(self.data['root']):]
        msg = {
            "type":"goto",
            "id":self.data["id"],
            "url":url,
            "line":line
        }
        self.socket.sendMessage(json.dumps(msg))        
        
    def set_status(self,lid,msg,timeout):
        msg = {
            "type":"status",
            "id":self.data["id"],
            "message":msg,
            "timeout":timeout
        }
        self.socket.sendMessage(json.dumps(msg))        
        
class Autocomplete(WebSocket):
    def complete(self,data):
        lang = data['lang']
        root = os.path.realpath(data['root'])
        path = os.path.join(root,data['url'][len(data['root_url']):])
        content = data['content']
        pos = data['pos']
        
        view = View(path,lang,pos,content,root)
        view.socket = self
        view.data = data
        
        global dayside_codeintel
        
        if data['goto']:
            dayside_codeintel.goto_definition(view)
        else:
            dayside_codeintel.complete(view)

    def handleMessage(self):
        if self.data is None:
            return
        data = json.loads(str(self.data))
        self.complete(data)

    def handleConnected(self):
        if self.auth():
            print self.address, 'connected'
        else:
            print self.address, 'auth failure'

    def handleClose(self):
        print self.address, 'closed'
        
    def auth(self):
        script = os.path.join(os.path.dirname(os.path.realpath(__file__)),"auth.php")
        try:
            ret = subprocess.check_output(["php",script,Autocomplete.params,self.request.headers["Cookie"]]);
        except subprocess.CalledProcessError as e:
            ret = 'failure'
        
        if ret=='ok':
            return True
        else:
            return False
           

dayside_codeintel = DaysideCodeIntel();
class MyDaemon(Daemon):
        def run(self):
            global dayside_codeintel
            if len(sys.argv) >= 3:
                Autocomplete.params = sys.argv[2]
                
            try:
                dayside_codeintel.start()
                server = SimpleWebSocketServer('', 8000, Autocomplete)
                server.serveforever()
            except:
                print traceback.format_exc()
                os._exit(1);
            
if __name__ == "__main__":
        daemon = MyDaemon('/tmp/codeintel-dayside.pid')
        if len(sys.argv) >= 2:
                if 'start' == sys.argv[1]:
                        daemon.start()
                elif 'stop' == sys.argv[1]:
                        daemon.stop()
                elif 'restart' == sys.argv[1]:
                        daemon.restart()
                elif 'run' == sys.argv[1]:
                        daemon.run()
                else:
                        print "Unknown command"
                        sys.exit(2)
                sys.exit(0)
        else:
                print "usage: %s start|stop|restart" % sys.argv[0]
                sys.exit(2)