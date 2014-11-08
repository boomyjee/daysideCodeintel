"""
CodeIntel is a plugin intended to display "code intelligence" information.
The plugin is based in code from the Open Komodo Editor and has a MPL license.
Port by German M. Bravo (Kronuz). May 30, 2011

For Manual autocompletion:
    User Key Bindings are setup like this:
        { "keys": ["super+j"], "command": "code_intel_auto_complete" }

For "Jump to symbol declaration":
    User Key Bindings are set up like this
        { "keys": ["super+f3"], "command": "goto_python_definition" }
    ...and User Mouse Bindings as:
        { "button": "button1", "modifiers": ["alt"], "command": "goto_python_definition", "press_command": "drag_select" }

Configuration files (`~/.codeintel/config' or `project_root/.codeintel/config'). All configurations are optional. Example:
    {
        "PHP": {
            "php": "/usr/bin/php",
            "phpConfigFile": "php.ini",
            "phpExtraPaths": []
        },
        "JavaScript": {
            "javascriptExtraPaths": []
        },
        "Perl": {
            "perl": "/usr/bin/perl",
            "perlExtraPaths": []
        },
        "Ruby": {
            "ruby": "/usr/bin/ruby",
            "rubyExtraPaths": []
        },
        "Python": {
            "python": "/usr/bin/python",
            "pythonExtraPaths": []
        },
        "Python3": {
            "python": "/usr/bin/python3",
            "pythonExtraPaths": []
        }
    }
"""
from __future__ import print_function

VERSION = "2.0.6"

import os
import re
import sys
import stat
import time
import datetime
import collections
import threading
import logging
from cStringIO import StringIO

CODEINTEL_HOME_DIR = os.path.expanduser(os.path.join('~', '.codeintel'))
__file__ = os.path.normpath(os.path.abspath(__file__))
__path__ = os.path.dirname(__file__)

libs_path = os.path.join(__path__, 'SublimeCodeintel','libs')
if libs_path not in sys.path:
    sys.path.insert(0, libs_path)

arch_path = os.path.join(__path__, 'SublimeCodeintel','arch')
if arch_path not in sys.path:
    sys.path.insert(0, arch_path)
    
from codeintel2.common import CodeIntelError, EvalTimeout, LogEvalController, TRG_FORM_CPLN, TRG_FORM_CALLTIP, TRG_FORM_DEFN
from codeintel2.manager import Manager
from codeintel2.environment import SimplePrefsEnvironment
from codeintel2.util import guess_lang_from_path


QUEUE = {}  # views waiting to be processed by codeintel

# Setup the complex logging (status bar gets stuff from there):
class NullHandler(logging.Handler):
    def emit(self, record):
        pass

codeintel_hdlr = NullHandler()
codeintel_hdlr.setFormatter(logging.Formatter("%(name)s: %(levelname)s: %(message)s"))
stderr_hdlr = logging.StreamHandler(sys.stderr)
stderr_hdlr.setFormatter(logging.Formatter("%(name)s: %(levelname)s: %(message)s"))
codeintel_log = logging.getLogger("codeintel")
condeintel_log_filename = ''
condeintel_log_file = None
log = logging.getLogger("SublimeCodeIntel")
codeintel_log.handlers = [codeintel_hdlr]
log.handlers = [stderr_hdlr]
codeintel_log.setLevel(logging.INFO)  # INFO
for logger in ('codeintel.db', 'codeintel.pythoncile'):
    logging.getLogger(logger).setLevel(logging.WARNING)  # WARNING
for logger in ('css', 'django', 'html', 'html5', 'javascript', 'mason', 'nodejs',
             'perl', 'php', 'python', 'python3', 'rhtml', 'ruby', 'smarty',
             'tcl', 'templatetoolkit', 'xbl', 'xml', 'xslt', 'xul'):
    logging.getLogger("codeintel." + logger).setLevel(logging.INFO)  # WARNING
log.setLevel(logging.ERROR)  # ERROR

cpln_fillup_chars = {
    'Ruby': "~`@#$%^&*(+}[]|\\;:,<>/ ",
    'Python': "~`!@#$%^&()-=+{}[]|\\;:'\",.<>?/ ",
    'PHP': "~`%^&*()-+{}[]|;'\",.< ",
    'Perl': "~`!@#$%^&*(=+}[]|\\;'\",.<>?/ ",
    'CSS': " '\";},/",
    'JavaScript': "~`!#%^&*()-=+{}[]|\\;:'\",.<>?/",
}

cpln_stop_chars = {
    'Ruby': "~`@#$%^&*(+}[]|\\;:,<>/ '\".",
    'Python': "~`!@#$%^&*()-=+{}[]|\\;:'\",.<>?/ ",
    'PHP': "~`@%^&*()=+{}]|\\;:'\",.<>?/ ",
    'Perl': "-~`!@#$%^&*()=+{}[]|\\;:'\",.<>?/ ",
    'CSS': " ('\";{},.>/",
    'JavaScript': "~`!@#%^&*()-=+{}[]|\\;:'\",.<>?/ ",
}

old_pos = None
despair = 0
despaired = False

completions = {}
languages = {}

status_msg = {}
status_lineno = {}
status_lock = threading.Lock()

HISTORY_SIZE = 64
jump_history_by_window = {}  # map of window id -> collections.deque([], HISTORY_SIZE)


def pos2bytes(content, pos):
    return len(content[:pos].encode('utf-8'))


def tooltip_popup(view, snippets):
    vid = view.id()
    completions[vid] = snippets
    view.run_command('auto_complete', {
        'disable_auto_insert': True,
        'api_completions_only': True,
        'next_completion_if_showing': False,
        'auto_complete_commit_on_tab': True,
    })


def tooltip(view, calltips, original_pos):
    view_settings = view.settings()
    codeintel_snippets = view_settings.get('codeintel_snippets', True)
    codeintel_tooltips = view_settings.get('codeintel_tooltips', 'popup')

    snippets = []
    for calltip in calltips:
        tip_info = calltip.split('\n')
        text = ' '.join(tip_info[1:])
        snippet = None
        # Insert parameters as snippet:
        m = re.search(r'([^\s]+)\(([^\[\(\)]*)', tip_info[0])
        if m:
            params = [p.strip() for p in m.group(2).split(',')]
            if params:
                snippet = []
                for i, p in enumerate(params):
                    if p:
                        var, _, _ = p.partition('=')
                        var = var.strip()
                        if ' ' in var:
                            var = var.split(' ')[1]
                        if var[0] == '$':
                            var = var[1:]
                        snippet.append('${%s:%s}' % (i + 1, var))
                snippet = ', '.join(snippet)
            text += ' - ' + tip_info[0]  # Add function to the end
        else:
            text = tip_info[0] + ' ' + text  # No function match, just add the first line
        if not codeintel_snippets:
            snippet = None
        snippets.extend((('  ' if i > 0 else '') + l, snippet or '${0}') for i, l in enumerate(tip_info))

    if codeintel_tooltips == 'popup':
        tooltip_popup(view, snippets)
    elif codeintel_tooltips in ('status', 'panel'):
        if codeintel_tooltips == 'status':
            set_status(view, 'tip', text, timeout=15000)
        else:
            window = view.window()
            output_panel = window.get_output_panel('tooltips')
            output_panel.set_read_only(False)
            text = '\n'.join(list(zip(*snippets))[0])
            output_panel.run_command('tooltip_output', {'output': text})
            output_panel.set_read_only(True)
            window.run_command('show_panel', {'panel': 'output.tooltips'})
            sublime.set_timeout(lambda: window.run_command('hide_panel', {'panel': 'output.tooltips'}), 15000)

        if snippets and codeintel_snippets:
            # Insert function call snippets:
            # func = m.group(1)
            # scope = view.scope_name(pos)
            # view.run_command('new_snippet', {'contents': snippets[0][0], 'tab_trigger': func, 'scope': scope})  # FIXME: Doesn't add the new snippet... is it possible to do so?
            def _insert_snippet():
                # Check to see we are still at a position where the snippet is wanted:
                view_sel = view.sel()
                if not view_sel:
                    return
                sel = view_sel[0]
                pos = sel.end()
                if not pos or pos != original_pos:
                    return
                view.run_command('insert_snippet', {'contents': snippets[0][0]})
            sublime.set_timeout(_insert_snippet, 500)  # Delay snippet insertion a bit... it's annoying some times


def set_status(view, ltype, msg=None, timeout=None, delay=0, lid='CodeIntel', logger=None):
    
    if timeout is None:
        timeout = {'error': 3000, 'warning': 5000, 'info': 10000,
                    'event': 10000}.get(ltype, 3000)

    if msg is None:
        msg, ltype = ltype, 'debug'
    msg = msg.strip()

    status_msg.setdefault(lid, [None, None, 0])
    if msg == status_msg[lid][1]:
        return
    status_msg[lid][2] += 1
    order = status_msg[lid][2]
    
    def _set_status():
        current_type, current_msg, current_order = status_msg.get(lid, [None, None, 0])
        if msg != current_msg and order == current_order:
            print("+", "%s: %s" % (ltype.capitalize(), msg), file=condeintel_log_file)
            (logger or log.info)(msg)
            if ltype != 'debug':
                view.set_status(lid, "%s: %s" % (ltype.capitalize(), msg),timeout)
                status_msg[lid] = [ltype, msg, order]

    def _erase_status():
        if msg == status_msg.get(lid, [None, None, 0])[1]:
            view.erase_status(lid)
            status_msg[lid][1] = None

    if msg:
        _set_status()
    else:
        _erase_status()


def logger(view, ltype, msg=None, timeout=None, delay=0, lid='CodeIntel'):
    if msg is None:
        msg, ltype = ltype, 'info'
    set_status(view, ltype, msg, timeout=timeout, delay=delay, lid=lid + '-' + ltype, logger=getattr(log, ltype, None))


def autocomplete(view, timeout, busy_timeout, forms, preemptive=False, args=[], kwargs={}):
    def _autocomplete_callback(view, path, original_pos, lang):
        view_sel = view.sel()
        if not view_sel:
            return

        sel = view_sel[0]
        pos = sel.end()
        
        if not pos or pos != original_pos:
            return

        # TODO: wtf?
        #lpos = view.line(sel).begin()
        #text = view.substr(sublime.Region(lpos, pos + 1))
        #next = text[-1] if len(text) == pos + 1 - lpos else None

        #if not next or next != '_' and not next.isalnum():
        if True:
            vid = view.id()

            def _trigger(trg_pos,calltips, cplns=None):
                if cplns is not None or calltips is not None:
                    codeintel_log.info("Autocomplete called (%s) [%s]", lang, ','.join(c for c in ['cplns' if cplns else None, 'calltips' if calltips else None] if c))

                #if cplns is not None:
                #    # function = None if 'import ' in text else 'function'
                #    _completions = cplns
                #    if _completions:
                #        # Show autocompletions:
                #        completions[vid] = _completions
                #        view.run_command('auto_complete', {
                #            'disable_auto_insert': True,
                #            'api_completions_only': True,
                #            'next_completion_if_showing': False,
                #            'auto_complete_commit_on_tab': True,
                #        })
                #if calltips:
                #    tooltip(view, calltips, original_pos)
                
                view.on_complete(cplns,calltips,trg_pos)

            content = view.content()
            codeintel(view, path, content, lang, pos, forms, _trigger)
    # If it's a fill char, queue using lower values and preemptive behavior
    queue(view, _autocomplete_callback, timeout, busy_timeout, preemptive, args=args, kwargs=kwargs)


_ci_envs_ = {}
_ci_next_scan_ = {}
_ci_mgr_ = {}

_ci_next_savedb_ = 0
_ci_next_cullmem_ = 0

################################################################################
# Queue dispatcher system:

MAX_DELAY = -1  # Does not apply
queue_thread_name = "codeintel callbacks"


def queue_dispatcher(force=False):
    pass

def queue_loop():
    while __loop_:
        queue_dispatcher()
        time.sleep(0.01)

def queue(view, callback, timeout, busy_timeout=None, preemptive=False, args=[], kwargs={}):
    QUEUE[view.id()] = (view, callback, args, kwargs)

def delay_queue(timeout):
    pass

__loop_ = True
__active_codeintel_thread = threading.Thread(target=queue_loop, name=queue_thread_name)

################################################################################


def codeintel_callbacks(force=False):
    global _ci_next_savedb_, _ci_next_cullmem_
    try:
        views = QUEUE.values()
        QUEUE.clear()
    finally:
        pass
    
    for view, callback, args, kwargs in views:
        callback(view, *args, **kwargs)
        
    # saving and culling cached parts of the database:
    for folders_id in _ci_mgr_.keys():
        mgr = codeintel_manager(folders_id)
        now = time.time()
        if now >= _ci_next_savedb_ or force:
            if _ci_next_savedb_:
                log.debug('Saving database')
                mgr.db.save()  # Save every 6 seconds
            _ci_next_savedb_ = now + 6
        if now >= _ci_next_cullmem_ or force:
            if _ci_next_cullmem_:
                log.debug('Culling memory')
                mgr.db.cull_mem()  # Every 30 seconds
            _ci_next_cullmem_ = now + 30
queue_dispatcher = codeintel_callbacks


def codeintel_cleanup(id):
    if id in _ci_envs_:
        del _ci_envs_[id]
    if id in _ci_next_scan_:
        del _ci_next_scan_[id]


def codeintel_manager(folders_id):
    folders_id = None
    global _ci_mgr_, condeintel_log_filename, condeintel_log_file
    mgr = _ci_mgr_.get(folders_id)
    if mgr is None:
        for thread in threading.enumerate():
            if thread.name == "CodeIntel Manager":
                thread.finalize()  # this finalizes the index, citadel and the manager and waits them to end (join)
                
        mgr = Manager(
            extra_module_dirs=None,
            db_base_dir=os.path.join(os.path.realpath(os.path.join(os.path.dirname(__file__),"..")),".codeintel"),
            db_catalog_dirs=[],
            db_import_everything_langs=None,
        )
        mgr.upgrade()
        mgr.initialize()

        # Connect the logging file to the handler
        condeintel_log_filename = os.path.join(mgr.db.base_dir, 'codeintel.log')
        
        condeintel_log_file = open(condeintel_log_filename, 'w', 1)
        codeintel_log.handlers = [logging.StreamHandler(condeintel_log_file)]
        msg = "Starting logging CodeIntel v%s rev %s (%s) on %s" % (VERSION, get_revision()[:12], os.stat(__file__)[stat.ST_MTIME], datetime.datetime.now().ctime())
        print("%s\n%s" % (msg, "=" * len(msg)), file=condeintel_log_file)

        _ci_mgr_[folders_id] = mgr
    return mgr


def codeintel_scan(view, path, content, lang, callback=None, pos=None, forms=None):
    global despair
    for thread in threading.enumerate():
        if thread.isAlive() and thread.name == "scanning thread":
            logger(view, 'info', "Updating indexes... The first time this can take a while. Do not despair!", timeout=20000, delay=despair)
            despair = 0
            return
    logger(view, 'info', "processing `%s': please wait..." % lang)
    is_scratch = view.is_scratch()
    is_dirty = view.is_dirty()
    vid = view.id()
    folders = getattr(view.window(), 'folders', lambda: [])()  # FIXME: it's like this for backward compatibility (<= 2060)
    folders_id = str(hash(frozenset(folders)))
    view_settings = view.settings()
    codeintel_config = view_settings.get('codeintel_config', {})
    _codeintel_max_recursive_dir_depth = view_settings.get('codeintel_max_recursive_dir_depth', 10)
    _codeintel_scan_files_in_project = view_settings.get('codeintel_scan_files_in_project', True)
    _codeintel_selected_catalogs = view_settings.get('codeintel_selected_catalogs', [])

    def _codeintel_scan():
        global despair, despaired
        env = None
        mtime = None
        catalogs = []
        now = time.time()

        mgr = codeintel_manager(folders_id)
        mgr.db.event_reporter = lambda m: logger(view, 'event', m)
        
        try:
            env = _ci_envs_[vid]
            if env._folders != folders:
                raise KeyError
            if now > env._time:
                mtime = max(tryGetMTime(env._config_file), tryGetMTime(env._config_default_file))
                if env._mtime < mtime:
                    raise KeyError
        except KeyError:
            if env is not None:
                config_default_file = env._config_default_file
                project_dir = env._project_dir
                project_base_dir = env._project_base_dir
                config_file = env._config_file
            else:
                config_file = None
                project_dir = view.root()
                project_base_dir = project_dir
                
                config_default_file = os.path.join(CODEINTEL_HOME_DIR, 'config')
                if not (config_default_file and os.path.exists(config_default_file)):
                    config_default_file = None
                config_file = None
                    
            valid = True
            if not mgr.is_citadel_lang(lang) and not mgr.is_cpln_lang(lang):
                if lang in ('Console', 'Plain text'):
                    msg = "Invalid language: %s. Available: %s" % (lang, ', '.join(set(mgr.get_citadel_langs() + mgr.get_cpln_langs())))
                    log.debug(msg)
                    codeintel_log.warning(msg)
                valid = False

            codeintel_config_lang = codeintel_config.get(lang, {})
            codeintel_max_recursive_dir_depth = codeintel_config_lang.get('codeintel_max_recursive_dir_depth', _codeintel_max_recursive_dir_depth)
            codeintel_scan_files_in_project = codeintel_config_lang.get('codeintel_scan_files_in_project', _codeintel_scan_files_in_project)
            codeintel_selected_catalogs = codeintel_config_lang.get('codeintel_selected_catalogs', _codeintel_selected_catalogs)

            avail_catalogs = mgr.db.get_catalogs_zone().avail_catalogs()

            # Load configuration files:
            all_catalogs = []
            for catalog in avail_catalogs:
                all_catalogs.append("%s (for %s: %s)" % (catalog['name'], catalog['lang'], catalog['description']))
                if catalog['lang'] == lang:
                    if catalog['name'] in codeintel_selected_catalogs:
                        catalogs.append(catalog['name'])
            msg = "Avaliable catalogs: %s" % ', '.join(all_catalogs) or None
            log.debug(msg)
            codeintel_log.debug(msg)

            config = {
                'codeintel_max_recursive_dir_depth': codeintel_max_recursive_dir_depth,
                'codeintel_scan_files_in_project': codeintel_scan_files_in_project,
                'codeintel_selected_catalogs': catalogs,
            }
            config.update(codeintel_config_lang)

            _config = {}
            try:
                tryReadDict(config_default_file, _config)
            except Exception as e:
                msg = "Malformed configuration file '%s': %s" % (config_default_file, e)
                log.error(msg)
                codeintel_log.error(msg)
            try:
                tryReadDict(config_file, _config)
            except Exception as e:
                msg = "Malformed configuration file '%s': %s" % (config_default_file, e)
                log.error(msg)
                codeintel_log.error(msg)
            config.update(_config.get(lang, {}))

            for conf in ['pythonExtraPaths', 'rubyExtraPaths', 'perlExtraPaths', 'javascriptExtraPaths', 'phpExtraPaths']:
                v = [p.strip() for p in config.get(conf, []) + folders if p.strip()]
                config[conf] = os.pathsep.join(set(p if p.startswith('/') else os.path.expanduser(p) if p.startswith('~') else os.path.abspath(os.path.join(project_base_dir, p)) if project_base_dir else p for p in v if p.strip()))
            for conf, p in config.items():
                if isinstance(p, basestring) and p.startswith('~'):
                    config[conf] = os.path.expanduser(p)

            # Setup environment variables
            env = config.get('env', {})
            _environ = dict(os.environ)
            for k, v in env.items():
                _old = None
                while '$' in v and v != _old:
                    _old = v
                    v = os.path.expandvars(v)
                _environ[k] = v
            config['env'] = _environ

            env = SimplePrefsEnvironment(**config)
            env._valid = valid
            env._mtime = mtime or max(tryGetMTime(config_file), tryGetMTime(config_default_file))
            env._folders = folders
            env._config_default_file = config_default_file
            env._project_dir = project_dir
            env._project_base_dir = project_base_dir
            env._config_file = config_file
            env.__class__.get_proj_base_dir = lambda self: project_base_dir
            _ci_envs_[vid] = env
        env._time = now + 5  # don't check again in less than five seconds

        msgs = []
        if env._valid:
            if forms:
                set_status(view, 'tip', "")
                set_status(view, 'event', "")
                
                msg = "CodeIntel(%s) for %s@%s [%s]" % (', '.join(forms), path, pos, lang)
                msgs.append(('info', "\n%s\n%s" % (msg, "-" * len(msg))))

            if catalogs:
                msg = "New env with catalogs for '%s': %s" % (lang, ', '.join(catalogs) or None)
                log.debug(msg)
                codeintel_log.warning(msg)
                msgs.append(('info', msg))

            buf = mgr.buf_from_content(content, lang, env, path or "<Unsaved>", 'utf-8')
            
            if mgr.is_citadel_lang(lang):
                now = datetime.datetime.now()
                if not _ci_next_scan_.get(vid) or now > _ci_next_scan_[vid]:
                    _ci_next_scan_[vid] = now + datetime.timedelta(seconds=10)
                    despair = 0
                    despaired = False
                    msg = "Updating indexes for '%s'... The first time this can take a while." % lang
                    print(msg, file=condeintel_log_file)
                    logger(view, 'info', msg, timeout=20000, delay=1000)
                    if not path or is_scratch:
                        buf.scan()  # FIXME: Always scanning unsaved files (since many tabs can have unsaved files, or find other path as ID)
                    else:
                        if is_dirty:
                            mtime = 1
                        else:
                            mtime = os.stat(path)[stat.ST_MTIME]
                        buf.scan(mtime=mtime, skip_scan_time_check=is_dirty)
        else:
            buf = None
        if callback:
            msg = "Doing CodeIntel for '%s' (hold on)..." % lang
            print(msg, file=condeintel_log_file)
            logger(view, 'info', msg, timeout=20000, delay=1000)
            callback(buf, msgs)
        else:
            logger(view, 'info', "")
    threading.Thread(target=_codeintel_scan, name="scanning thread").start()


def codeintel(view, path, content, lang, pos, forms, callback=None, timeout=7000):
    start = time.time()

    def _codeintel(buf, msgs):
        cplns = None
        calltips = None
        defns = None

        if not buf:
            logger(view, 'warning', "`%s' (%s) is not a language that uses CIX" % (path, lang))
            return [None] * len(forms)
        
        try:
            trg = getattr(buf, 'preceding_trg_from_pos', lambda p: None)(pos2bytes(content, pos), pos2bytes(content, pos))
            defn_trg = getattr(buf, 'defn_trg_from_pos', lambda p: None)(pos2bytes(content, pos))
        except (CodeIntelError):
            codeintel_log.exception("Exception! %s:%s (%s)" % (path or '<Unsaved>', pos, lang))
            logger(view, 'info', "Error indexing! Please send the log file: '%s" % condeintel_log_filename)
            trg = None
            defn_trg = None
        except:
            codeintel_log.exception("Exception! %s:%s (%s)" % (path or '<Unsaved>', pos, lang))
            logger(view, 'info', "Error indexing! Please send the log file: '%s" % condeintel_log_filename)
            raise
        else:
            eval_log_stream = StringIO()
            _hdlrs = codeintel_log.handlers
            hdlr = logging.StreamHandler(eval_log_stream)
            hdlr.setFormatter(logging.Formatter("%(name)s: %(levelname)s: %(message)s"))
            codeintel_log.handlers = list(_hdlrs) + [hdlr]
            ctlr = LogEvalController(codeintel_log)
            try:
                if 'cplns' in forms and trg and trg.form == TRG_FORM_CPLN:
                    cplns = buf.cplns_from_trg(trg, ctlr=ctlr, timeout=20)
                if 'calltips' in forms and trg and trg.form == TRG_FORM_CALLTIP:
                    calltips = buf.calltips_from_trg(trg, ctlr=ctlr, timeout=20)
                if 'defns' in forms and defn_trg and defn_trg.form == TRG_FORM_DEFN:
                    defns = buf.defns_from_trg(defn_trg, ctlr=ctlr, timeout=20)
            except EvalTimeout:
                logger(view, 'info', "Timeout while resolving completions!")
            finally:
                codeintel_log.handlers = _hdlrs
            logger(view, 'warning', "")
            logger(view, 'event', "")
            result = False
            merge = ''
            for msg in reversed(eval_log_stream.getvalue().strip().split('\n')):
                msg = msg.strip()
                if msg:
                    try:
                        name, levelname, msg = msg.split(':', 2)
                        name = name.strip()
                        levelname = levelname.strip().lower()
                        msg = msg.strip()
                    except:
                        merge = (msg + ' ' + merge) if merge else msg
                        continue
                    merge = ''
                    if not result and msg.startswith('evaluating '):
                        set_status(view, 'warning', msg)
                        result = True
                        
        ret = []
        for f in forms:
            if f == 'cplns':
                ret.append(cplns)
            elif f == 'calltips':
                ret.append(calltips)
            elif f == 'defns':
                ret.append(defns)

        total = (time.time() - start) * 1000
        if total > 1000:
            timestr = "~%ss" % int(round(total / 1000))
        else:
            timestr = "%sms" % int(round(total))
        if not despaired or total < timeout:
            msg = "Done '%s' CodeIntel! Full CodeIntel took %s" % (lang, timestr)
            print(msg, file=condeintel_log_file)
            
            logger(view, 'info', "")

            view_sel = view.sel()
            trg_pos = trg.pos if trg!=None else None
            
            # TODO: wtf?
            #if view_sel and view.line(view_sel[0]) == view.line(pos):
            if True:
                callback(trg_pos,*ret)
        else:
            msg = "Just finished indexing '%s'! Please try again. Full CodeIntel took %s" % (lang, timestr)
            print(msg, file=condeintel_log_file)
            logger(view, 'info', msg, timeout=3000)
    codeintel_scan(view, path, content, lang, _codeintel, pos, forms)


def find_back(start_at, look_for):
    root = os.path.realpath('/')
    start_at = os.path.abspath(start_at)
    if not os.path.isdir(start_at):
        start_at = os.path.dirname(start_at)
    if start_at == root:
        return None
    while True:
        if look_for in os.listdir(start_at):
            return os.path.join(start_at, look_for)
        continue_at = os.path.abspath(os.path.join(start_at, '..'))
        if continue_at == start_at or continue_at == root:
            return None
        start_at = continue_at


def updateCodeIntelDict(master, partial):
    for key, value in partial.items():
        if isinstance(value, dict):
            master.setdefault(key, {}).update(value)
        elif isinstance(value, (list, tuple)):
            master.setdefault(key, []).extend(value)


def tryReadDict(filename, dictToUpdate):
    if filename:
        file = open(filename, 'r')
        try:
            updateCodeIntelDict(dictToUpdate, eval(file.read()))
        finally:
            file.close()


def tryGetMTime(filename):
    if filename:
        return os.stat(filename)[stat.ST_MTIME]
    return 0


def _get_git_revision(path):
    path = os.path.join(path, '.git')
    if os.path.exists(path):
        revision_file = os.path.join(path, 'refs', 'heads', 'master')
        if os.path.isfile(revision_file):
            fh = open(revision_file, 'r')
            try:
                return fh.read().strip()
            finally:
                fh.close()


def get_revision(path=None):
    """
    :returns: Revision number of this branch/checkout, if available. None if
        no revision number can be determined.
    """
    path = os.path.abspath(os.path.normpath(__path__ if path is None else path))
    while path and path != '/' and path != '\\':
        rev = _get_git_revision(path)
        if rev:
            return u'GIT-%s' % rev
        uppath = os.path.abspath(os.path.join(path, '..'))
        if uppath != path:
            path = uppath
        else:
            break
    return u'GIT-unknown'


ALL_SETTINGS = [
    'codeintel',
    'codeintel_snippets',
    'codeintel_tooltips',
    'codeintel_enabled_languages',
    'codeintel_live',
    'codeintel_live_enabled_languages',
    'codeintel_max_recursive_dir_depth',
    'codeintel_scan_files_in_project',
    'codeintel_selected_catalogs',
    'codeintel_syntax_map',
    'codeintel_scan_exclude_dir',
    'codeintel_config',
    'sublime_auto_complete',
]


def settings_changed():
    for window in sublime.windows():
        for view in window.views():
            reload_settings(view)


def codeintel_enabled(view, default=None):
    if view.settings().get('codeintel') is None:
        reload_settings(view)
    return view.settings().get('codeintel', default)

class Sel:
    def __init__(self,pos):
        self._pos = pos
        
    def end(self):
        return self._pos

class Settings:
    def get(self,key,def_val=False):
        if key=='codeintel_live':
            return True
        return def_val
    
class Window:
    def __init__(self,root):
        self._root = root
        
    def folders(self):
        return [self._root]

class View:
    def __init__(self,path,lang,pos,content,root):
        self._settings = Settings()
        self._path = path
        self._lang = lang
        self._sel = Sel(pos)
        self._content = content
        self._window = Window(root)
        self._completions = []
        self._root = root
        
    def root(self):
        return self._root
        
    def id(self):
        return self._path
        
    def lang(self):
        return self._lang
        
    def settings(self):
        return self._settings
    
    def file_name(self):
        return self._path
    
    def sel(self):
        return [self._sel]
    
    def window(self):
        return self._window
    
    def substr(self,p1,p2):
        return self._content[p1:p2]
    
    def content(self):
        return self._content
    
    def is_scratch(self):
        return False
    
    def is_dirty(self):
        return True
    
    def on_complete(self,cplns,calltips,original_pos):
        pass
        
    def set_status(self,lid,msg,timeout):
        pass
        
    def erase_status(self,lid):
        self.set_status(lid,"",0)

active_codeintel_thread = __active_codeintel_thread
class DaysideCodeIntel:
    def start(self):
        active_codeintel_thread.start()
        
    def complete(self, view):
        if not view.settings().get('codeintel_live', True):
            return

        path = view.file_name()
        lang = view.lang()

        view_sel = view.sel()
        if not view_sel:
            return

        sel = view_sel[0]
        pos = sel.end()
        text = view.substr(pos - 1, pos)
        is_fill_char = (text and text[-1] in cpln_fillup_chars.get(lang, ''))

        forms = ('calltips', 'cplns')
        autocomplete(view, 0, 50, forms, is_fill_char, args=[path, pos, lang])
