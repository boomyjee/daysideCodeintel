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

libs_path = os.path.join(__path__, 'SublimeCodeIntel','libs')
if libs_path not in sys.path:
    sys.path.insert(0, libs_path)

arch_path = os.path.join(__path__, 'SublimeCodeIntel','arch')
if arch_path not in sys.path:
    sys.path.insert(0, arch_path)
    
from codeintel2.common import CodeIntelError, EvalTimeout, LogEvalController, TRG_FORM_CPLN, TRG_FORM_CALLTIP, TRG_FORM_DEFN
from codeintel2.manager import Manager
from codeintel2.environment import SimplePrefsEnvironment
from codeintel2.util import guess_lang_from_path

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

def set_status(view, ltype, msg=None, timeout=None, delay=0, lid='CodeIntel', logger=None):
    
    if timeout is None:
        timeout = {'error': 3000, 'warning': 5000, 'info': 10000,
                    'event': 10000}.get(ltype, 3000)

    if msg is None:
        msg, ltype = ltype, 'debug'
    msg = msg.strip()
    
    status_msg.setdefault(lid, [None, None, 0])
    
    current_type, current_msg, current_order = status_msg.get(lid, [None, None, 0])
    
    msg = msg or ''
    current_msg = current_msg or ''
    
    if msg != current_msg:
        if msg:
            print("+", "%s: %s" % (ltype.capitalize(), msg), file=condeintel_log_file)
            (logger or log.info)(msg)
            if ltype != 'debug':
                view.set_status(lid, "%s: %s" % (ltype.capitalize(), msg),timeout)
                status_msg[lid] = [ltype, msg, 0]
        else:
            view.erase_status(lid)
            status_msg[lid][1] = None


def logger(view, ltype, msg=None, timeout=None, delay=0, lid='CodeIntel'):
    if msg is None:
        msg, ltype = ltype, 'info'
    set_status(view, ltype, msg, timeout=timeout, delay=delay, lid=lid + '-' + ltype, logger=getattr(log, ltype, None))


_ci_envs_ = {}
_ci_next_scan_ = {}
_ci_mgr_ = {}

_ci_next_savedb_ = 0
_ci_next_cullmem_ = 0

def codeintel_cleanup(id):
    if id in _ci_envs_:
        del _ci_envs_[id]
    if id in _ci_next_scan_:
        del _ci_next_scan_[id]


def codeintel_manager(folders_id,root):
    # folders_id = None
    global _ci_mgr_, condeintel_log_filename, condeintel_log_file
    mgr = _ci_mgr_.get(folders_id)
    if mgr is None:
        for thread in threading.enumerate():
            if thread.name == "CodeIntel Manager":
                thread.finalize()  # this finalizes the index, citadel and the manager and waits them to end (join)
                
        mgr = Manager(
            extra_module_dirs=None,
            db_base_dir=os.path.join(root,".codeintel"),
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

        mgr = codeintel_manager(folders_id,view.root())
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
    def __init__(self,path,lang,pos,content,root,status_callback):
        self._settings = Settings()
        self._path = path
        self._lang = lang
        self._sel = Sel(pos)
        self._content = content
        self._window = Window(root)
        self._completions = []
        self._root = root
        self._status_callback = status_callback
        
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
    
    def set_status(self,lid,msg,timeout):
        cb = self._status_callback
        cb(lid,msg,timeout)
        
    def erase_status(self,lid):
        self.set_status(lid,"",0)

class DaysideCodeIntel:
    def request(self,view,forms,callback):
        path = view.file_name()
        lang = view.lang()
        
        view_sel = view.sel()
        if not view_sel: return
        sel = view_sel[0]
        pos = sel.end()
        content = view.content()
        
        global _ci_next_savedb_, _ci_next_cullmem_
        # saving and culling cached parts of the database:
        for folders_id in _ci_mgr_.keys():
            mgr = codeintel_manager(folders_id,None)
            now = time.time()
            if now >= _ci_next_savedb_:
                if _ci_next_savedb_:
                    log.debug('Saving database')
                    mgr.db.save()  # Save every 6 seconds
                _ci_next_savedb_ = now + 6
            if now >= _ci_next_cullmem_:
                if _ci_next_cullmem_:
                    log.debug('Culling memory')
                    mgr.db.cull_mem()  # Every 30 seconds
                _ci_next_cullmem_ = now + 30
                
        codeintel(view, path, content, lang, pos, forms, callback)
    
    def goto_definition(self,view,callback):
        def _trigger(trg_pos,defns):
            if defns is not None:
                defn = defns[0]
                if defn.name and defn.doc:
                    msg = "%s: %s" % (defn.name, defn.doc)
                    logger(view, 'info', msg, timeout=3000)

                if defn.path and defn.line:
                    path = defn.path + ':' + str(defn.line)
                    msg = 'Jumping to: %s' % path
                    log.debug(msg)
                    codeintel_log.debug(msg)
                    callback(defn);
                        
                elif defn.name:
                    msg = 'Cannot find jumping point to: %s' % defn.name
                    log.debug(msg)
                    codeintel_log.debug(msg)

        self.request(view, ('defns',), _trigger)        
        
    def complete(self, view, callback):
        lang = view.lang()
        def _trigger(trg_pos,calltips, cplns=None):
            if cplns is not None or calltips is not None:
                codeintel_log.info("Autocomplete called (%s) [%s]", lang, ','.join(c for c in ['cplns' if cplns else None, 'calltips' if calltips else None] if c))
            callback(cplns,calltips,trg_pos)
        self.request(view,('calltips', 'cplns'), _trigger)
        
        