(function($,ui){
    
dayside.codeintel = dayside.plugins.codeintel = $.Class.extend({
    init: function (o) {
        this.options = $.extend({port:8000},o);
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
        
        dayside.core.bind("configDefaults",function(b,e){
            e.value.codeintel_enable = false;
            e.value.codeintel_live = true;
        });

        dayside.core.bind("configUpdate",function(b,e){
            if (e.value.codeintel_enable) {
                if (!me.client) me.connect();
            } else {
                if (me.client) me.disconnect();
            }
            me.live_completion = e.value.codeintel_live;
        });

        dayside.core.bind("configTabsCreated",function(b,e){
            var configTab = teacss.ui.panel({
                label: "Autocomplete", padding: "1em"
            }).push(
                ui.label({ value: "Codeintel options:", margin: "5px 0" }),
                ui.check({ label: "Autocomplete enabled", name: "codeintel_enable", width: "100%", margin: "5px 0" }),
                ui.check({ label: "Live completion", name: "codeintel_live", width: "100%", margin: "5px 0 0" })
            );
            e.tabs.addTab(configTab);
        });        
        
        dayside.ready(function(){
            FileApi.request('real_path',{path:FileApi.root},true,function(res){
                me.root = res.data.path;
            });
            
            dayside.editor.bind("editorOptions",function(b,e){
                e.options.extraKeys["Ctrl-Space"] = function (cm) {
                    if (!me.connected) return;
                    clearTimeout(me.changeTimeout);
                    me.request(cm,e.tab,me.getLang(e.tab));
                }
            });
            
            dayside.editor.bind("editorCreated",function(b,e){
                e.tab.editor.addKeyMap({
                    Esc: function (cm) {
                        if (cm.hideCalltip) cm.hideCalltip();
                    }
                });
                
                e.tab.editor.on("mousedown",function(cm,mouse_e){
                    if (!me.connected) return;
                    if (mouse_e.altKey) {
                        setTimeout(function(){
                            var cursor = cm.getCursor();
                            clearTimeout(me.changeTimeout);
                            me.request(cm,e.tab,me.getLang(e.tab),true);
                        },1);
                    }
                });
                
                e.tab.editor.on("endCompletion",function(cm){
                    cm.completionBusy = false;
                });
                
                e.tab.editor.on("change", function(cm,co) {
                    if (!me.connected) return;
                    if (!me.live_completion) return;
                    
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
                            me.request(cm,e.tab,lang);
                        },timeout);
                    } else {
                        me.request(cm,e.tab,lang);
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
    
    request: function (cm,tab,lang,goto) {
        if (!lang) return;
        if (cm.completionBusy) return;
        if (!this.connected) return;
        
        this.last_cm = tab.editor;
        this.last_request = {
            goto: goto,
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
                else if (data.type=="goto") {
                    if (me.last_request && me.last_request.id == data.id) {
                        var tab = dayside.editor.selectFile(data.url);
                        
                        function positionCursor() {
                            tab.editor.focus();
                            tab.editor.setCursor({line:data.line-1,ch:0},{scroll:true});
                        }
                        
                        if (tab.editor) {
                            positionCursor();
                        } else {
                            tab.bind("editorCreated",function(){
                                positionCursor();
                                tab.saveState();
                            });
                        }
                    }
                }
                else if (data.type=="status") {
                    dayside.editor.setStatus(data.message,data.timeout);
                }
            }
        });
    },
    
    disconnect: function () {
        if (this.client && this.connected) {
            this.client.ws.close();
            this.connected = false;
            this.client = false;
            console.debug("codeintel disconnected");
        }
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
                data: {_type:"codeintel_start",port:me.options.port},
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
        this.socket_url = "ws://"+url[2]+":"+dayside.codeintel.instance.options.port;
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

