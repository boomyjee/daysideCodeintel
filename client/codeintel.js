(function($,ui){
    
dayside.codeintel = dayside.plugins.codeintel = $.Class.extend({
    init: function (o) {
        this.options = $.extend({},o);
        this.Class.instance = this;
        
        var me = this;
        me.tabHash = {};
        me.connected = false;
        me.root = false;
        me.id_counter = (new Date()).getTime()*1000 + Math.floor(Math.random()*1000);
        
        var cpln_fillup_chars = {
            'Ruby': "~`@#$%^&*(+}[]|\\;:,<>/ ",
            'Python': "~`!@#$%^&()-=+{}[]|\\;:'\",.<>?/ ",
            'PHP': "~`%^&*()-+{}[]|;'\",.<> ",
            'Perl': "~`!@#$%^&*(=+}[]|\\;'\",.<>?/ ",
            'CSS': " '\";},/",
            'JavaScript': "~`!#%^&*()-=+{}[]|\\;:'\",.<>?/",
        }
        
        dayside.ready(function(){
            FileApi.request('real_path',{path:FileApi.root},true,function(res){
                me.root = res.data.path;
                me.connect();
            });
            
            dayside.editor.bind("editorOptions",function(b,e){
                e.options.extraKeys["Ctrl-Space"] = function (cm) {
                    if (!me.connected) return;
                    clearTimeout(me.changeTimeout);
                    me.request_complete(cm,e.tab,me.getLang(e.tab));
                }
            });
            
            dayside.editor.bind("editorCreated",function(b,e){
                
                e.tab.editor.addKeyMap({
                    Esc: function (cm) {
                        if (cm.hideCalltip) cm.hideCalltip();
                    }
                });
                
                e.tab.editor.on("endCompletion",function(cm){
                    cm.completionBusy = false;
                });
                
                e.tab.editor.on("change", function(cm,co) {
                    if (!me.connected) return;
                    
                    var lang = me.getLang(e.tab);
                    var timeout = 600;
                    
                    me.id_counter++;
                        
                    if (cpln_fillup_chars[lang]) {
                        var cursor = cm.getCursor();
                        var last = cm.getRange({line:cursor.line,ch:cursor.ch-1},cursor);
                        if (cpln_fillup_chars[lang].indexOf(last)!=-1) {
                            timeout = 0;
                        }
                    }
                    clearTimeout(me.changeTimeout);
                    if (timeout) {
                        me.changeTimeout = setTimeout(function(){
                            me.request_complete(cm,e.tab,lang);
                        },timeout);
                    } else {
                        me.request_complete(cm,e.tab,lang);
                    }
                });
            });
        });        
    },
    
    getLang: function (tab) {
        var ext = tab.options.file.split(".").pop();
        var lang = false;
        if (ext=='py') lang = 'Python';
        if (ext=='php') lang = 'PHP';
        if (ext=='js') lang = 'Javascript';
        return lang;
    },
    
    request_complete: function (cm,tab,lang) {
        if (!lang) return;
        if (cm.completionBusy) return;
        
        this.last_cm = tab.editor;
        this.last_request = {
            lang: lang,
            id: this.id_counter++,
            root: this.root,
            root_url: FileApi.root,
            url: tab.options.file,
            content: cm.getValue(),
            pos: cm.indexFromPos(cm.getCursor())
        }
        console.debug('send',this.last_request);
        this.client.send(this.last_request);
    },
    
    connect: function() {
        var me = this;
        this.client = dayside.codeintel.client({
            onopen: function (data) {
                me.connected = true;
                console.debug("codeintel connected");
            },
            onmessage: function (data) {
                console.debug('recv',data);
                if (data.type=="complete") {
                    if (me.last_request && me.last_request.id == data.id) {
                        var cm = me.last_cm;
                        me.showCalltip(cm,false,false);
                        var pos = cm.posFromIndex(data.pos);
                        if (data.completions && data.completions.length) {
                            cm.completionBusy = true;
                            CodeMirror.showHint(cm,function(){
                                var curr = cm.getCursor();
                                var pre = cm.getRange(pos,curr);

                                var list = [];
                                $.each(data.completions,function(i,cmp){
                                    var type = cmp[0];
                                    var word = cmp[1];
                                    if (word.slice(0, pre.length) == pre && pre.length!=word.length) 
                                    {
                                        list.push({
                                            className: "codeintel-hint codeintel-hint-"+type,
                                            text: word
                                        });
                                    }
                                });
                                return {list:list,from:pos,to:curr}
                            },{completeSingle:false});
                        } else if (data.calltips && data.calltips.length) {
                            me.showCalltip(cm,pos,data.calltips[0]);
                        }
                    }
                }
            }
        });
    },
    
    showCalltip: function (cm,pos,text) {
        if (!pos && cm.calltipPos) pos = cm.calltipPos;
        if (!cm.calltip) {
            cm.calltip = $("<div>").addClass("codeintel-calltip");        
            cm.hideCalltip = function () {
                cm.calltip.hide();
                cm.off("cursorActivity",cm.hideCalltip);
            }
        }
        cm.calltip.text(text);
        
        if (text) {
            cm.addWidget(pos,cm.calltip.show()[0]);
            cm.on("cursorActivity",cm.hideCalltip);
        } else {
            cm.hideCalltip();
        }
        cm.calltipPos = pos;
    },
    
    
    startServer: function (cb) {
        var me = this;
        if (me.serverStarted) {
            console.debug('First start server was not successful');
            return;
        }
        
        me.startCallbacks = me.startCallbacks || [];
        me.startCallbacks.push(cb);
        
        clearTimeout(me.startTimeout);
        me.startTimeout = setTimeout(function(){
            me.serverStarted = true;
            $.ajax({
                url: FileApi.ajax_url,
                data: {_type:"codeintel_start"},
                async: false,
                type: "POST",
                success: function (answer) {
                    console.debug(answer);
                    $.each(me.startCallbacks,function(i,cb){
                        cb();
                    });
                }
            });      
        },1);        
    }
});
    
dayside.codeintel.client = $.Class.extend({
    init: function (o) {
        this.options = o;
        var url = teacss.path.absolute(FileApi.ajax_url);
        url = url.split("/");
        this.socket_url = "ws://"+url[2]+":8000";
        this.createSocket();
    },
    
    createSocket: function () {
        var me = this;
        var o = this.options;
        
        me.ws = new WebSocket(this.socket_url);
        me.ws.onopen = function () {
            if (o.onopen) o.onopen.apply(me,arguments);
        }
        me.ws.onmessage = function (e) {
            if (o.onmessage) {
                var data = $.parseJSON(e.data);
                o.onmessage.call(me,data);
            }
        }
        me.ws.onclose = function () {
            if (o.onclose) o.onclose.apply(me,arguments);
        }
        me.ws.onerror = function (e) {
            e.preventDefault();
            dayside.codeintel.instance.startServer(function(){
                me.createSocket()
            });
        }
    },
    
    send: function (data) {
        this.ws.send(JSON.stringify(data));
    }
})
    
})(teacss.jQuery,teacss.ui);

