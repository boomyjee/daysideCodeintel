import os,sys

_lib = os.path.join(os.path.dirname(os.path.realpath(__file__)),"lib")
sys.path.insert(0, _lib)


_codeintel = os.path.join(os.path.dirname(os.path.realpath(__file__)),"SublimeCodeIntel")
sys.path.insert(0, os.path.join(_codeintel, 'arch'))
sys.path.insert(0, os.path.join(_codeintel, 'libs'))
from codeintel2.util import unmark_text

import codeintel
import traceback
import time
import logging

from codeintel import DaysideCodeIntel

class View(codeintel.View):
    def on_complete(self,cplns,calltips,original_pos):
        print cplns
        print calltips
        os._exit(1);
        
    def set_status(self,lid,msg,timeout):
        print msg,timeout
        pass
        
    def erase_status(self,lid):
        print 'status erase'
        

# logging.basicConfig(level = logging.DEBUG)
root = os.path.realpath(os.path.join(os.getcwd(),"..",".."))
path = os.path.join(root,"test.php")

with open ("../test_console.php", "r") as myfile: 
    content = myfile.read()

content,data = unmark_text(content)
pos = data["pos"]
    
view = View(path,"PHP",pos,content,root)
ci = DaysideCodeIntel()

try:
    ci.start()
    ci.complete(view)
    while True:
        time.sleep(0.1)    
except:
    print traceback.format_exc()
    sys.exit()
    


    
    
    
    
    
    
    
    