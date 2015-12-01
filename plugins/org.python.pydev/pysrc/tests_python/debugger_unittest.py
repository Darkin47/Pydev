from _pydev_bundle.pydev_imports import quote_plus, quote, unquote_plus
from _pydevd_bundle.pydevd_constants import IS_PY3K

import socket
import os
import threading
import time
from _pydev_bundle import pydev_localhost
import subprocess

CMD_SET_PROPERTY_TRACE, CMD_EVALUATE_CONSOLE_EXPRESSION, CMD_RUN_CUSTOM_OPERATION, CMD_ENABLE_DONT_TRACE = 133, 134, 135, 141

SHOW_WRITES_AND_READS = False
SHOW_OTHER_DEBUG_INFO = False
SHOW_STDOUT = False

import pydevd
PYDEVD_FILE = pydevd.__file__

try:
    from thread import start_new_thread
except ImportError:
    from _thread import start_new_thread  # @UnresolvedImport

try:
    xrange
except:
    xrange = range


#=======================================================================================================================
# ReaderThread
#=======================================================================================================================
class ReaderThread(threading.Thread):

    def __init__(self, sock):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.sock = sock
        self.lastReceived = ''

    def run(self):
        last_printed = None
        try:
            buf = ''
            while True:
                l = self.sock.recv(1024)
                if IS_PY3K:
                    l = l.decode('utf-8')
                buf += l

                if '\n' in buf:
                    self.lastReceived = buf
                    buf = ''

                if SHOW_WRITES_AND_READS:
                    if last_printed != self.lastReceived.strip():
                        last_printed = self.lastReceived.strip()
                        print('Test Reader Thread Received %s' % last_printed)
        except:
            pass  # ok, finished it

    def do_kill(self):
        self.sock.close()


class DebuggerRunner(object):

    def get_command_line(self):
        raise NotImplementedError

    def check_case(self, writer_thread_class):
        port = get_free_port()
        writer_thread = writer_thread_class(port)
        writer_thread.start()
        time.sleep(1)

        localhost = pydev_localhost.get_localhost()
        args = self.get_command_line()
        args += [
            PYDEVD_FILE,
            '--DEBUG_RECORD_SOCKET_READS',
            '--qt-support',
            '--client',
            localhost,
            '--port',
            str(port),
            '--file',
            writer_thread.TEST_FILE,
        ]

        if SHOW_OTHER_DEBUG_INFO:
            print('executing', ' '.join(args))

        return self.run_process(args, writer_thread)

    def run_process(self, args, writer_thread):
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=os.path.dirname(PYDEVD_FILE))

        stdout = []
        stderr = []

        def read(stream, buffer):
            for line in stream.readlines():
                if IS_PY3K:
                    line = line.decode('utf-8')

                if SHOW_STDOUT:
                    print(line)
                buffer.append(line)

        start_new_thread(read, (process.stdout, stdout))


        if SHOW_OTHER_DEBUG_INFO:
            print('Both processes started')

        # polls can fail (because the process may finish and the thread still not -- so, we give it some more chances to
        # finish successfully).
        check = 0
        while True:
            if process.poll() is not None:
                break
            else:
                if writer_thread is not None:
                    if not writer_thread.isAlive():
                        check += 1
                        if check == 20:
                            print('Warning: writer thread exited and process still did not.')
                        if check == 100:
                            self.fail_with_message(
                                "The other process should've exited but still didn't (timeout for process to exit).",
                                stdout, stderr, writer_thread
                            )
            time.sleep(.2)


        poll = process.poll()
        if poll < 0:
            self.fail_with_message(
                "The other process exited with error code: " + str(poll), stdout, stderr, writer_thread)


        if stdout is None:
            self.fail_with_message(
                "The other process may still be running -- and didn't give any output.", stdout, stderr, writer_thread)

        if 'TEST SUCEEDED' not in ''.join(stdout):
            self.fail_with_message("TEST SUCEEDED not found in stdout.", stdout, stderr, writer_thread)

        if writer_thread is not None:
            for i in xrange(100):
                if not writer_thread.finished_ok:
                    time.sleep(.1)

            if not writer_thread.finished_ok:
                self.fail_with_message(
                    "The thread that was doing the tests didn't finish successfully.", stdout, stderr, writer_thread)

        return {'stdout':stdout, 'stderr':stderr}

    def fail_with_message(self, msg, stdout, stderr, writerThread):
        raise AssertionError(msg+
            "\nStdout: \n"+'\n'.join(stdout)+
            "\nStderr:"+'\n'.join(stderr)+
            "\nLog:\n"+'\n'.join(getattr(writerThread, 'log', [])))



#=======================================================================================================================
# AbstractWriterThread
#=======================================================================================================================
class AbstractWriterThread(threading.Thread):

    def __init__(self, port):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.finished_ok = False
        self._next_breakpoint_id = 0
        self.log = []
        self.port = port


    def do_kill(self):
        if hasattr(self, 'readerThread'):
            # if it's not created, it's not there...
            self.readerThread.do_kill()
        self.sock.close()

    def write(self, s):

        last = self.readerThread.lastReceived
        if SHOW_WRITES_AND_READS:
            print('Test Writer Thread Written %s' % (s,))
        msg = s + '\n'
        if IS_PY3K:
            msg = msg.encode('utf-8')
        self.sock.send(msg)
        time.sleep(0.2)

        i = 0
        while last == self.readerThread.lastReceived and i < 10:
            i += 1
            time.sleep(0.1)


    def start_socket(self):
        if SHOW_WRITES_AND_READS:
            print('start_socket')

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('', self.port))
        s.listen(1)
        if SHOW_WRITES_AND_READS:
            print('Waiting in socket.accept()')
        newSock, addr = s.accept()
        if SHOW_WRITES_AND_READS:
            print('Test Writer Thread Socket:', newSock, addr)

        readerThread = self.readerThread = ReaderThread(newSock)
        readerThread.start()
        self.sock = newSock

        self._sequence = -1
        # initial command is always the version
        self.write_version()
        self.log.append('start_socket')

    def next_breakpoint_id(self):
        self._next_breakpoint_id += 1
        return self._next_breakpoint_id

    def next_seq(self):
        self._sequence += 2
        return self._sequence


    def wait_for_new_thread(self):
        i = 0
        # wait for hit breakpoint
        while not '<xml><thread name="' in self.readerThread.lastReceived or '<xml><thread name="pydevd.' in self.readerThread.lastReceived:
            i += 1
            time.sleep(1)
            if i >= 15:
                raise AssertionError('After %s seconds, a thread was not created.' % i)

        # we have something like <xml><thread name="MainThread" id="12103472" /></xml>
        splitted = self.readerThread.lastReceived.split('"')
        threadId = splitted[3]
        return threadId

    def wait_for_breakpoint_hit(self, reason='111', get_line=False):
        '''
            108 is over
            109 is return
            111 is breakpoint
        '''
        self.log.append('Start: wait_for_breakpoint_hit')
        i = 0
        # wait for hit breakpoint
        last = self.readerThread.lastReceived
        while not ('stop_reason="%s"' % reason) in last:
            i += 1
            time.sleep(1)
            last = self.readerThread.lastReceived
            if i >= 10:
                raise AssertionError('After %s seconds, a break with reason: %s was not hit. Found: %s' % \
                    (i, reason, last))

        # we have something like <xml><thread id="12152656" stop_reason="111"><frame id="12453120" ...
        splitted = last.split('"')
        threadId = splitted[1]
        frameId = splitted[7]
        if get_line:
            self.log.append('End(0): wait_for_breakpoint_hit')
            return threadId, frameId, int(splitted[13])

        self.log.append('End(1): wait_for_breakpoint_hit')
        return threadId, frameId

    def wait_for_custom_operation(self, expected):
        i = 0
        # wait for custom operation response, the response is double encoded
        expectedEncoded = quote(quote_plus(expected))
        while not expectedEncoded in self.readerThread.lastReceived:
            i += 1
            time.sleep(1)
            if i >= 10:
                raise AssertionError('After %s seconds, the custom operation not received. Last found:\n%s\nExpected (encoded)\n%s' %
                    (i, self.readerThread.lastReceived, expectedEncoded))

        return True

    def wait_for_evaluation(self, expected):
        return self._wait_for(expected, 'the expected evaluation was not found')


    def wait_for_vars(self, expected):
        i = 0
        # wait for hit breakpoint
        while not expected in self.readerThread.lastReceived:
            i += 1
            time.sleep(1)
            if i >= 10:
                raise AssertionError('After %s seconds, the vars were not found. Last found:\n%s' %
                    (i, self.readerThread.lastReceived))

        return True

    def wait_for_var(self, expected):
        self._wait_for(expected, 'the var was not found')

    def _wait_for(self, expected, error_msg):
        '''
        :param expected:
            If a list we'll work with any of the choices.
        '''
        if not isinstance(expected, (list, tuple)):
            expected = [expected]

        i = 0
        found = False
        while not found:
            last = self.readerThread.lastReceived
            for e in expected:
                if e in last:
                    found = True
                    break

            last = unquote_plus(last)
            for e in expected:
                if e in last:
                    found = True
                    break

            if found:
                break

            i += 1
            time.sleep(1)
            if i >= 10:
                raise AssertionError('After %s seconds, %s. Last found:\n%s' %
                    (i, error_msg, last))

        return True

    def wait_for_multiple_vars(self, expected_vars):
        i = 0
        # wait for hit breakpoint
        while True:
            for expected in expected_vars:
                if expected not in self.readerThread.lastReceived:
                    break  # Break out of loop (and don't get to else)
            else:
                return True

            i += 1
            time.sleep(1)
            if i >= 10:
                raise AssertionError('After %s seconds, the vars were not found. Last found:\n%s' %
                    (i, self.readerThread.lastReceived))

        return True

    def write_make_initial_run(self):
        self.write("101\t%s\t" % self.next_seq())
        self.log.append('write_make_initial_run')

    def write_version(self):
        self.write("501\t%s\t1.0\tWINDOWS\tID" % self.next_seq())

    def write_add_breakpoint(self, line, func):
        '''
            @param line: starts at 1
        '''
        breakpoint_id = self.next_breakpoint_id()
        self.write("111\t%s\t%s\t%s\t%s\t%s\t%s\tNone\tNone" % (self.next_seq(), breakpoint_id, 'python-line', self.TEST_FILE, line, func))
        self.log.append('write_add_breakpoint: %s line: %s func: %s' % (breakpoint_id, line, func))
        return breakpoint_id

    def write_remove_breakpoint(self, breakpoint_id):
        self.write("112\t%s\t%s\t%s\t%s" % (self.next_seq(), 'python-line', self.TEST_FILE, breakpoint_id))

    def write_change_variable(self, thread_id, frame_id, varname, value):
        self.write("117\t%s\t%s\t%s\t%s\t%s\t%s" % (self.next_seq(), thread_id, frame_id, 'FRAME', varname, value))

    def write_get_frame(self, threadId, frameId):
        self.write("114\t%s\t%s\t%s\tFRAME" % (self.next_seq(), threadId, frameId))
        self.log.append('write_get_frame')

    def write_get_variable(self, threadId, frameId, var_attrs):
        self.write("110\t%s\t%s\t%s\tFRAME\t%s" % (self.next_seq(), threadId, frameId, var_attrs))

    def write_step_over(self, threadId):
        self.write("108\t%s\t%s" % (self.next_seq(), threadId,))

    def write_step_in(self, threadId):
        self.write("107\t%s\t%s" % (self.next_seq(), threadId,))

    def write_step_return(self, threadId):
        self.write("109\t%s\t%s" % (self.next_seq(), threadId,))

    def write_suspend_thread(self, threadId):
        self.write("105\t%s\t%s" % (self.next_seq(), threadId,))

    def write_run_thread(self, threadId):
        self.log.append('write_run_thread')
        self.write("106\t%s\t%s" % (self.next_seq(), threadId,))

    def write_kill_thread(self, threadId):
        self.write("104\t%s\t%s" % (self.next_seq(), threadId,))

    def write_debug_console_expression(self, locator):
        self.write("%s\t%s\t%s" % (CMD_EVALUATE_CONSOLE_EXPRESSION, self.next_seq(), locator))

    def write_custom_operation(self, locator, style, codeOrFile, operation_fn_name):
        self.write("%s\t%s\t%s||%s\t%s\t%s" % (CMD_RUN_CUSTOM_OPERATION, self.next_seq(), locator, style, codeOrFile, operation_fn_name))

    def write_evaluate_expression(self, locator, expression):
        self.write("113\t%s\t%s\t%s\t1" % (self.next_seq(), locator, expression))

    def write_enable_dont_trace(self, enable):
        if enable:
            enable = 'true'
        else:
            enable = 'false'
        self.write("%s\t%s\t%s" % (CMD_ENABLE_DONT_TRACE, self.next_seq(), enable))

def _get_debugger_test_file(filename):
    try:
        rPath = os.path.realpath  # @UndefinedVariable
    except:
        # jython does not support os.path.realpath
        # realpath is a no-op on systems without islink support
        rPath = os.path.abspath

    return os.path.normcase(rPath(os.path.join(os.path.dirname(__file__), filename)))

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((pydev_localhost.get_localhost(), 0))
    _, port = s.getsockname()
    s.close()
    return port