# Copyright (c) 2002-2005 Sylvain Thenault (syt@logilab.fr).
# Copyright (c) 2002-2005 LOGILAB S.A. (Paris, FRANCE).
# http://www.logilab.fr/ -- mailto:contact@logilab.fr
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
""" %prog [options] module_or_package

  Check that a module satisfy a coding standard (and more !).

    %prog --help
    
  Display this help message and exit.

    %prog --help-msg <msg-id>[,<msg-id>]

  Display help messages about given message identifiers and exit.
"""

__revision__ = "$Id: lint.py,v 1.6 2005-02-03 12:31:14 fabioz Exp $"

from __future__ import nested_scopes

#patched -- Fabio
import sys
import os
sys.path.insert(1, os.path.join(os.path.dirname(sys.argv[0]), "../../../ThirdParty"))

# import this first to avoid further builtins pollution possibilities
from logilab.pylint.checkers import utils

import sys
import os
import re
import tokenize
from os.path import dirname, basename, splitext, exists, isdir, join

from logilab.common.configuration import OptionsManagerMixIn
from logilab.common.astng import ASTNGManager
from logilab.common.modutils import modpath_from_file, get_module_files, \
     file_from_modpath
from logilab.common.interface import implements
from logilab.common.textutils import get_csv
from logilab.common.fileutils import norm_open
from logilab.common.ureports import Table, Text

from logilab.pylint.utils import UnknownMessage, MessagesHandlerMixIn, \
     ReportsHandlerMixIn, MSG_TYPES, sort_checkers
from logilab.pylint.interfaces import ILinter, IRawChecker, IASTNGChecker
from logilab.pylint.checkers import BaseRawChecker, EmptyReport, \
     table_lines_from_stats
from logilab.pylint.reporters.text import TextReporter
from logilab.pylint import config

from logilab.pylint.__pkginfo__ import version


OPTION_RGX = re.compile('#*\s*pylint:(.*)')

# Python Linter class #########################################################
    
MSGS = {
    'F0001': ('%s',
              'Used when an error occured preventing the analyzing of a \
              module (unable to find it for instance).'),
    'F0002': ('%s: %s',
              'Used when an unexpected error occured while building the ASTNG \
              representation. This is usually accomopagned by a traceback. \
              Please report such errors !'),
    
    'I0001': ('Unable to run raw checkers on built-in module %s',
              'Used to inform that a built-in module has not been checked \
              using the raw checkers.'),
    
    'I0011': ('Locally disabling %r',
              'Used when an inline option disable a message or a messages \
              category.'),
    'I0012': ('Locally enabling %r',
              'Used when an inline option enable a message or a messages \
              category.'),
    
    'E0001': ('%s',
              'Used when a syntax error is raised for a module.'),

    'E0011': ('Unrecognized file option %r',
              'Used when an unknown inline option is encountered.'),
    'E0012': ('Bad option value %r',
              'Used when an bad value for an inline option is encountered.'),
    }

class PyLinter(OptionsManagerMixIn, MessagesHandlerMixIn, ReportsHandlerMixIn,
               BaseRawChecker):
    """lint Python modules using external checkers.                            
                                                                               
    This is the main checker controling the other ones and the reports         
    generation. It is itself both a raw checker and an astng checker in order  
    to:                                                                        
    * handle message activation / deactivation at the module level             
    * handle some basic but necessary stats'data (number of classes, methods...)
    """

    __implements__ = (ILinter, IRawChecker, IASTNGChecker)
    
    name = 'master'
    priority = 0
    msgs = MSGS
    may_be_disabled = False
    
    options = (("ignore",
                {'type' : "csv", 'metavar' : "<file>",
                 'dest' : "black_list", "default" : ('CVS',),
                 'help' : "Add <file or directory> to the black list. It \
should be a base name, not a path. You may set this option multiple times."}),
               ("persistent",
                {'default': 1, 'type' : 'yn', 'metavar' : '<y_or_n>',
                 'help' : 'Pickle collected data for later comparisons.'}),
               
               ("cache-size",
                {'default': 500, 'type' : 'int', 'metavar': '<size>',
                 'help' : "Set the cache size for astng objects."}),
               
               ("reports",
                {'default': 1, 'type' : 'yn', 'metavar' : '<y_or_n>',
                 'group': 'Reports',
                 'help' : "Tells wether to display a full report or only the\
 messages"}),
               ("html",
                {'default': 0, 'type' : 'yn', 'metavar' : '<y_or_n>',
                 'group': 'Reports',
                 'help' : "Use HTML as output format instead of text"}),
               
               ("parseable",
                {'default': 0, 'type' : 'yn', 'metavar' : '<y_or_n>',
                 'group': 'Reports',
                 'help' : "Use a parseable text output format, so your favorite\
 text editor will be able to jump to the line corresponding to a message."}),
               
               ("color",
                {'default': 0, 'type' : 'yn', 'metavar' : '<y_or_n>',
                 'group': 'Reports',
                 'help' : "Colorizes text output using ansi escape codes"}),
               
               ("files-output",
                {'default': 0, 'type' : 'yn', 'metavar' : '<y_or_n>',
                 'group': 'Reports',
                 'help' : 'Put messages in a separate file for each module / \
package specified on the command line instead of printing them on stdout. \
Reports (if any) will be written in a file name "pylint_global.[txt|html]".'}),
               
               ("evaluation",
                {'type' : 'string', 'metavar' : '<python_expression>',
                 'group': 'Reports',
                 'default': '10.0 - ((float(5 * error + warning + refactor + \
convention) / statement) * 10)', 
                 'help' : 'Python expression which should return a note less \
than 10 (10 is the highest note).You have access to the variables errors \
warning, statement which respectivly contain the number of errors / warnings\
 messages and the total number of statements analyzed. This is used by the \
 global evaluation report (R0004).'}),
               
               ("comment",
                {'default': 0, 'type' : 'yn', 'metavar' : '<y_or_n>',
                 'group': 'Reports',
                 'help' : 'Add a comment according to your evaluation note. \
This is used by the global evaluation report (R0004).'}),

               ("include-ids",
                {'type' : "yn", 'metavar' : '<y_or_n>', 'default' : 0,
                 'group': 'Reports',
                 'help' : "Include message's id in output"}),
               
               ("enable-msg-cat",
                {'type' : 'csv', 'metavar': '<msg cats>',
                 'group': 'Reports',
                 'help' : 'Enable all messages in the listed categories.'}),

               ("disable-msg-cat",
                {'type' : 'csv', 'metavar': '<msg cats>',
                 'group': 'Reports',
                 'help' : 'Disable all messages in the listed categories.'}),
               
               ("enable-msg",
                {'type' : 'csv', 'metavar': '<msg ids>',
                 'group': 'Reports',
                 'help' : 'Enable the message with the given id.'}),
            
               ("disable-msg",
                {'type' : 'csv', 'metavar': '<msg ids>',
                 'group': 'Reports',
                 'help' : 'Disable the message with the given id.'}),

               ("enable-report",
                {'type' : 'csv', 'metavar': '<rpt ids>',
                 'group': 'Reports',
                 'help' : 'Enable the report with the given id.'}),
               
               ("disable-report",
                {'type' : 'csv', 'metavar': '<rpt ids>',
                 'group': 'Reports',
                 'help' : 'Disable the report with the given id.'}),
               
               )
    
    option_groups = (
        ('Reports', 'Options related to messages / statistics reporting'),
        )
    
    def __init__(self, options=(), reporter=None, option_groups=()):
        # some stuff has to be done before ancestors initialization...
        #
        # checkers / reporter / astng manager
        self.reporter = None
        self.set_reporter(reporter or TextReporter())
        self.manager = ASTNGManager()
        self._checkers = {}
        # visit variables
        self.base_name = None
        self.base_file = None
        self.current_name = None
        self.current_file = None
        self.stats = None
        # init options
        self.options = options + PyLinter.options
        self.option_groups = option_groups + PyLinter.option_groups
        self._options_methods = {
            'enable-report': self.enable_report,
            'disable-report': self.disable_report,
            'enable-msg': self.enable_message,
            'disable-msg': self.disable_message,
            'enable-msg-cat': self.enable_message_category,
            'disable-msg-cat': self.disable_message_category}
        full_version = "%%prog %s\nPython %s" % (version,
                                                 sys.version)
        OptionsManagerMixIn.__init__(self, usage=__doc__,
                                     version=full_version,
                                     config_file=config.PYLINTRC)
        MessagesHandlerMixIn.__init__(self)
        ReportsHandlerMixIn.__init__(self)
        BaseRawChecker.__init__(self)
        # provided reports
        self.reports = (('R0001', 'Total errors / warnings',
                         self.report_error_warning_stats),
                        ('R0002', '% errors / warnings by module',
                         self.report_error_warning_by_module_stats),
                        ('R0003', 'Messages',
                         self.report_messages_stats),
                        ('R0004', 'Global evaluation',
                         self.report_evaluation),
                        )
        self.register_checker(self)

    def set_reporter(self, reporter):
        """set the reporter used to display messages and reports"""
        self.reporter = reporter
        reporter.linter = self
            
    def set_option(self, opt_name, value, action=None, opt_dict=None):
        """overriden from configuration.OptionsProviderMixin to handle some
        special options
        """
        if opt_name in self._options_methods:
            if value:
                meth = self._options_methods[opt_name]
                for _id in value:
                    meth(_id)
            else:
                value = ()
        elif opt_name == 'html':
            if value:
                from logilab.pylint.reporters.html import HTMLReporter
                self.set_reporter(HTMLReporter())
        elif opt_name == 'parseable':
            if value:
                from logilab.pylint.reporters.text import TextReporter2
                self.set_reporter(TextReporter2())
        elif opt_name == 'color':
            if value:
                from logilab.pylint.reporters.text import ColorizedTextReporter
                self.set_reporter(ColorizedTextReporter())
        elif opt_name == 'cache-size':
            self.manager.set_cache_size(int(value))
        BaseRawChecker.set_option(self, opt_name, value, action, opt_dict)

    # checkers manipulation methods ###########################################
    
    def register_checker(self, checker):
        """register a new checker

        checker is an object implementing IRawChecker or / and IASTNGChecker
        """
        assert checker.priority <= 0, 'checker priority can\'t be >= 0'
        self._checkers[checker] = 1
        if hasattr(checker, 'reports'):
            for r_id, r_title, r_cb in checker.reports:
                self.register_report(r_id, r_title, r_cb, checker)
            need_space = 80 - (len(checker.__doc__.splitlines()[-1]) % 80)
            checker.__doc__ += """%s
This checker also defines the following reports:                                    
  * %s
""" % (' ' * need_space,
       '\n  * '.join(['% -76s' % ('%s: %s' % report[:2])
                      for report in checker.reports]))
        self.register_options_provider(checker)
        if hasattr(checker, 'msgs'):
            self.register_messages(checker)
            
                
    def disable_all_checkers(self):
        """disable all possible checkers """
        for checker in self._checkers.keys():
            checker.enable(False)


    def process_tokens(self, tokens):
        """process tokens from the current module to search for module level
        options
        """
        comment = tokenize.COMMENT
        newline = tokenize.NEWLINE
        #line_num = 0
        for (tok_type, _, start, _, line) in tokens:
            if tok_type not in (comment, newline):
                break
            #if start[0] == line_num:
            #    continue
            match = OPTION_RGX.match(line)
            if match is None:
                continue
            opt, value = match.group(1).split('=', 1)
            opt = opt.strip()
            #line_num = start[0]
            if opt in self._options_methods and not opt.endswith('-report'):
                meth = self._options_methods[opt]
                for msg_id in get_csv(value):
                    try:
                        meth(msg_id, 'module', start[0])
                    except UnknownMessage:
                        self.add_message('E0012', args=msg_id, line=start[0])
            else:
                self.add_message('E0011', args=opt, line=start[0])
        
    # code checking methods ###################################################

    def check(self, files_or_modules):
        """main checking entry: check a list of files or modules from their
        name.
        """
        self.reporter.include_ids = self.config.include_ids
        if type(files_or_modules) not in (type(()), type([])):
            files_or_modules = [files_or_modules]
        checkers = sort_checkers(self._checkers.keys())
        rev_checkers = checkers[:]
        rev_checkers.reverse()
        # notify global begin
        for checker in checkers:
            checker.open()
        # check modules or packages        
        for something in files_or_modules:
            self.base_name = self.base_file = something
            if exists(something):
                # this is a file or a directory
                try:
                    modname = '.'.join(modpath_from_file(something))
                except Exception:
                    modname = splitext(basename(something))[0]
                if isdir(something):
                    filepath = join(something, '__init__.py')
                else:
                    filepath = something
            else:
                # suppose it's a module or package
                modname = something
                try:
                    filepath = file_from_modpath(modname.split('.'))
                except Exception, ex:
                    #if __debug__:
                    #    import traceback
                    #    traceback.print_exc()
                    self.set_current_module(modname)
                    self.add_message('F0001', args=str(ex))
                    continue
            if self.config.files_output:
                reportfile = 'pylint_%s.%s' % (modname, self.reporter.extension)
                self.reporter.set_output(open(reportfile, 'w'))
            self.check_file(filepath, modname, checkers)
        # notify global end
        for checker in rev_checkers:
            checker.close()

    def check_file(self, filepath, modname, checkers):
        """check a module or package from its name
        if modname is a package, recurse on its subpackages / submodules
        """
        # normalize the file path to parse the python source file
        filepath = splitext(filepath)[0] + '.py'
        # get the given module representation
        self.base_name = modname
        self.base_file = filepath
        # check this module
        astng = self._check_file(filepath, modname, checkers)
        if astng is None:
            return
        # recurse in package except if __init__ was explicitly given
        if not modname.endswith('.__init__') and astng.package:
            for filepath in get_module_files(dirname(filepath),
                                             self.config.black_list):
                if filepath == self.base_file:
                    continue
                modname = '.'.join(modpath_from_file(filepath))
                self._check_file(filepath, modname, checkers)

    def _check_file(self, filepath, modname, checkers):
        """check a module by building its astng representation"""
        self.set_current_module(modname, filepath)
        # get the module representation
        astng = self.get_astng(filepath, modname)
        if astng is not None:
            # set the base file if necessary
            self.base_file = self.base_file or astng.file
            # fix the current file (if the source file was not available or
            # if its actually a c extension
            self.current_file = astng.file
            # and check it
            self.check_astng_module(astng, checkers)
        return astng
        
    def set_current_module(self, modname, filepath=None):
        """set the name of the currently analyzed module and
        init statistics for it
        """
        self.current_name = modname 
        self.current_file = filepath or modname
        self.stats['by_module'][modname] = {}
        self.stats['by_module'][modname]['statement'] = 0
        for msg_cat in MSG_TYPES.values():
            self.stats['by_module'][modname][msg_cat] = 0
        self._module_msgs_state = {}
        self._module_msg_cats_state = {}
            
    def get_astng(self, filepath, modname):
        """return a astng representation for a module"""
        try:
            return self.manager.astng_from_file(filepath, modname)
        except SyntaxError, ex:
            self.add_message('E0001', line=ex.lineno, args=ex.msg)
        except KeyboardInterrupt:
            raise
        except Exception, ex:
            #if __debug__:
            #    import traceback
            #    traceback.print_exc()
            self.add_message('F0002', args=(ex.__class__, ex))
        

    def check_astng_module(self, astng, checkers):
        """check a module from its astng representation, real work"""
        # this is required to make works relative imports in analyzed code
        sys.path.insert(0, dirname(astng.file))
        try:
            # call raw checkers if possible
            if not astng.pure_python:
                self.add_message('I0001', args=astng.name)
            else:
                assert astng.file.endswith('.py')
                stream = norm_open(astng.file)
                for checker in checkers:
                    if implements(checker, IRawChecker):
                        stream.seek(0)
                        checker.process_module(stream)
            # generate events to astng checkers
            self.astng_events(astng, [checker for checker in checkers
                                      if implements(checker, IASTNGChecker)])

        finally:
            sys.path.pop(0)
    
    def astng_events(self, astng, checkers):
        """generate event to astng checkers according to the current astng
        node and recurse on its children
        """
        if astng.is_statement():
            self.stats['statement'] += 1
        # generate events for this node on each checkers
        for checker in checkers:
            checker.visit(astng)
        # recurse on children
        for child in astng.getChildNodes():
            self.astng_events(child, checkers)
        checkers.reverse()
        for checker in checkers:
            checker.leave(astng)
        

    # IASTNGChecker interface #################################################
        
    def open(self):
        """initialize counters"""
        self.stats = { 'by_module' : {},
                       'by_msg' : {},
                       'statement' : 0
                       }
        for msg_cat in MSG_TYPES.values():
            self.stats[msg_cat] = 0
    
    def close(self):
        """close the whole package /module, it's time to make reports !
        
        if persistent run, pickle results for later comparison
        """
        # load old results if any
        old_stats = config.load_results(self.base_name)
        if self.config.reports:
            self.make_reports(self.stats, old_stats)
        # save results if persistent run
        if self.config.persistent:
            config.save_results(self.stats, self.base_name)
            
    # specific reports ########################################################
        
    def report_error_warning_stats(self, sect, stats, old_stats):
        """make total errors / warnings report"""
        lines = ['type', 'number', 'previous', 'difference']
        lines += table_lines_from_stats(stats, old_stats,
                                        ('convention', 'refactor',
                                         'warning', 'error'))
        sect.append(Table(children=lines, cols=4, rheaders=1))
    
    def report_messages_stats(self, sect, stats, _):
        """make messages type report"""
        if not stats['by_msg']:
            # don't print this report when we didn't detected any errors
            raise EmptyReport()
        in_order = [(value, msg_id)
                    for msg_id, value in stats['by_msg'].items()
                    if not msg_id.startswith('I')]
        in_order.sort()
        in_order.reverse()
        lines = ('message id', 'occurences')
        for value, msg_id in in_order:
            lines += (msg_id, str(value))
        sect.append(Table(children=lines, cols=2, rheaders=1))
        
    def report_error_warning_by_module_stats(self, sect, stats, _):
        """make errors / warnings by modules report"""
        if len(stats['by_module']) == 1:
            # don't print this report when we are analysing a single module
            raise EmptyReport()
        by_mod = {} 
        for m_type in ('fatal', 'error', 'warning', 'refactor', 'convention'):
            total = stats[m_type]
            for module in stats['by_module'].keys():
                mod_total = stats['by_module'][module][m_type]
                if total == 0:
                    percent = 0
                else:
                    percent = float((mod_total)*100) / total
                by_mod.setdefault(module, {})[m_type] = percent            
        sorted_result = []
        for module, mod_info in by_mod.items():
            sorted_result.append((mod_info['error'],
                                  mod_info['warning'],
                                  mod_info['refactor'],
                                  mod_info['convention'],
                                  module))
        sorted_result.sort()
        sorted_result.reverse()
        lines = ['module', 'error', 'warning', 'refactor', 'convention']
        for line in sorted_result:
            if line[0] == 0 and line[1] == 0:
                break
            lines.append(line[-1])
            for val in line[:-1]:
                lines.append('%.2f' % val)
        if len(lines) == 5:
            raise EmptyReport()
        sect.append(Table(children=lines, cols=5, rheaders=1))

    def report_evaluation(self, sect, stats, old_stats):
        """make the global evaluation report"""
        # check with at least check 1 statements (usually 0 when there is a
        # syntax error preventing pylint from further processing)
        if stats['statement'] == 0:
            raise EmptyReport()
        # get a global note for the code
        evaluation = self.config.evaluation
        try:
            note = eval(evaluation, {}, self.stats)
        except Exception, ex:
            msg = 'An exception occured while rating: %s' % ex
        else:
            stats['global_note'] = note
            msg = 'Your code has been rated at %.2f/10' % note
            if old_stats.has_key('global_note'):
                msg += ' (previous run: %.2f/10)' % old_stats['global_note']
            if self.config.comment:
                msg = '%s\n%s' % (msg, config.get_note_message(note))
        sect.append(Text(msg))

        

# utilities ###################################################################

# this may help to import modules using gettext

try:
    __builtins__._ = str
except AttributeError:
    __builtins__['_'] = str


class Run:
    """helper class to use as main for pylint :
    
    run(*sys.argv[1:])
    """
    
    def __init__(self, args, reporter=None, quiet=0):
        from logilab.pylint import checkers
        self.linter = linter = PyLinter((
            ("zope",
            {'action' : "callback", 'callback' : self.cb_init_zope,
             'help' : "Initialize Zope products before starting."}),
            
            ("disable-all",
             {'action' : "callback",
              'callback' : self.cb_disable_all_checkers,
              'help' : '''Disable all possible checkers. This option should 
              precede enable-* options.'''}),

            ("help-msg",
             {'action' : "callback", 'type' : 'string', 'metavar': '<msg-id>',
              'callback' : self.cb_help_message,
              'help' : '''Display a help message for the given message id and \
exit. This option may be a comma separated list.'''}),

            ("list-msgs",
             {'action' : "callback", 'metavar': '<msg-id>',
              'callback' : self.cb_list_messages,
              'help' : 'List and explain every available messages.'}),

            ("generate-rcfile",
             {'action' : "callback", 'callback' : self.cb_generate_config,
              'help' : '''Generate a sample configuration file according to \
the current configuration. You can put other options before this one to use \
them in the configuration. This option causes the program to exit'''}),
            
            ("profile",
             {'action' : "store_true",
              'help' : 'Profiled execution.'}),

            ), reporter=reporter)
        linter.quiet = quiet
        # register checkers
        checkers.initialize(linter)
        # add some help section
        linter.add_help_section('Environment variables', config.ENV_HELP)
        linter.add_help_section('Output', '''
Using the default text output, the message format is :                         
        MESSAGE_TYPE: LINE_NUM:[OBJECT:] MESSAGE                               
There are 5 kind of message types :                                            
    * (C) convention, for programming standard violation                       
    * (R) refactor, for bad code smell                                         
    * (W) warning, for python specific problems                                
    * (E) error, for much probably bugs in the code                                                
    * (F) fatal, if an error occured which prevented pylint from doing further \
processing.     
        ''')
        # load configuration
        linter.load_file_configuration()
        args = linter.load_command_line_configuration(args)
        if not args:
            linter.help()
            sys.exit(1)
        # insert current working directory to the python path to have a correct
        # behaviour
        sys.path.insert(0, os.getcwd())
        if self.linter.config.profile:
            print >> sys.stderr, '** profiled run'
            from hotshot import Profile, stats
            prof = Profile("stones.prof")
            prof.runcall(linter.check, args)
            prof.close()
            stats = stats.load("stones.prof")
            stats.strip_dirs()
            stats.sort_stats('time', 'calls')
            stats.print_stats(30)
        else:
            linter.check(args)
        sys.path.pop(0)

    def cb_init_zope(self, *args, **kwargs):
        """optik callback for Zope products initialization"""
        print >> sys.stderr, 'this option is deprecated since pylint is not \
importing analyzed code now'
        #import Zope
        #if hasattr(Zope, 'startup'):
        #    # Zope >= 2.6
        #    Zope.startup()

    def cb_disable_all_checkers(self, *args, **kwargs):
        """optik callback for disabling all checkers"""
        self.linter.disable_all_checkers()
            
    def cb_generate_config(self, *args, **kwargs):
        """optik callback for sample config file generation"""
        self.linter.generate_config()
        sys.exit(0)
         
    def cb_help_message(self, option, opt_name, value, parser):
        """optik callback for printing some help about a particular message"""
        self.linter.help_message(get_csv(value))
        sys.exit(0)
        
    def cb_list_messages(self, option, opt_name, value, parser):
        """optik callback for printing available messages"""
        self.linter.list_messages()
        sys.exit(0)


if __name__ == "__main__":
    #patched
    import sys
    sys.stderr = sys.stdout
    Run(sys.argv[1:])
