import os,sys

_lib = os.path.join(os.path.dirname(os.path.realpath(__file__)),"lib")
sys.path.insert(0, _lib)

from SimpleWebSocketServer import WebSocket, SimpleWebSocketServer
from codeintel import DaysideCodeIntel 

from daemon import Daemon
import json, os, sys, subprocess, time, codeintel, traceback

from SimpleWebSocketServer import WebSocket, SimpleWebSocketServer
from codeintel import DaysideCodeIntel

class Autocomplete(WebSocket):
    
    params = ""
    
    def handleMessage(self):
        if self.data is None:
            return
        data = json.loads(str(self.data))
        
        lang = data['lang']
        root = os.path.realpath(data['root'])
        path = os.path.join(root,data['url'][len(data['root_url']):])
        content = data['content']
        pos = data['pos']
        
        def _status_callback(lid,msg,timeout):
            msg = {
                "type":"status",
                "id":data["id"],
                "message":msg,
                "timeout":timeout
            }
            self.sendMessage(json.dumps(msg))
        view = codeintel.View(path,lang,pos,content,root,_status_callback)
        
        global dayside_codeintel
        if 'goto' in data:
            def _definition_callback(defn):
                path = defn.path
                line = defn.line
                url = data['root_url'] + path[len(data['root']):]
                msg = {
                    "type":"goto",
                    "id":data["id"],
                    "url":url,
                    "line":line
                }
                self.sendMessage(json.dumps(msg))        
            dayside_codeintel.goto_definition(view,_definition_callback)
        else:
            def _complete_callback(cplns,calltips,trg_pos):
                msg = {
                    "type":"complete",
                    "id":data["id"],
                    "completions":cplns,
                    "calltips":calltips,
                    "pos":trg_pos
                }
                self.sendMessage(json.dumps(msg))                
            dayside_codeintel.complete(view,_complete_callback)

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
        except:
            print traceback.format_exc()
            
        if ret=='ok':
            return True
        else:
            return False


params = sys.argv[2] if len(sys.argv) >= 3 else ""
params = json.loads(params) if params else {}
port = params['port'] if 'port' in params else 8000

dayside_codeintel = DaysideCodeIntel();
class MyDaemon(Daemon):
        def run(self):
            global dayside_codeintel
            if len(sys.argv) >= 3:
                Autocomplete.params = sys.argv[2]
            try:
                server = SimpleWebSocketServer('', port, Autocomplete)
                server.serveforever()
            except:
                print traceback.format_exc()
                os._exit(1);
            
if __name__ == "__main__":
        daemon = MyDaemon('/tmp/codeintel-dayside-'+`port`+'.pid')
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