"""macOS current-user memory-only CAS transport; no credential files or file queue."""
import ctypes
import errno
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import threading

MAX_FRAME = 65536


class AuthSocketError(RuntimeError):
    pass


def peer_identity(connection):
    if sys.platform != 'darwin':
        raise AuthSocketError('mac_required')
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise AuthSocketError('auth_peer_unverified')
    # macOS SDK sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=0x002.
    pid = struct.unpack('i', connection.getsockopt(0, 0x002, 4))[0]
    return uid.value, pid


def _read(connection, size):
    data = bytearray()
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise AuthSocketError('auth_transport_disconnected')
        data.extend(part)
    return bytes(data)


def read_message(connection):
    length = struct.unpack('!I', _read(connection, 4))[0]
    if not 0 < length <= MAX_FRAME:
        raise AuthSocketError('invalid_auth_frame')
    try:
        return json.loads(_read(connection, length))
    except (ValueError, UnicodeError):
        raise AuthSocketError('invalid_auth_frame') from None


def write_message(connection, value):
    raw = json.dumps(value, ensure_ascii=True).encode('ascii')
    if len(raw) > MAX_FRAME:
        raise AuthSocketError('invalid_auth_frame')
    connection.sendall(struct.pack('!I', len(raw)) + raw)


def private_endpoint(name):
    path = Path(name)
    if not path.is_absolute() or path.name != 'auth.sock' or not path.parent.name.startswith('ustc-auth-'):
        return False
    try:
        parent, endpoint = path.parent.lstat(), path.lstat()
        return (stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700
                and stat.S_ISSOCK(endpoint.st_mode) and endpoint.st_uid == os.getuid() and stat.S_IMODE(endpoint.st_mode) == 0o600)
    except OSError:
        return False


def call(name, host_pid, packet):
    if type(host_pid) is not int or host_pid <= 0 or not private_endpoint(name):
        raise AuthSocketError('auth_socket_unverified')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(22)
        connection.connect(name)
        if peer_identity(connection) != (os.getuid(), host_pid):
            raise AuthSocketError('auth_peer_identity_mismatch')
        write_message(connection, packet)
        return read_message(connection)


class AuthSocketServer:
    def __init__(self, callback):
        if sys.platform != 'darwin':
            raise AuthSocketError('mac_required')
        self.directory = Path(tempfile.mkdtemp(prefix='ustc-auth-', dir='/tmp'))
        os.chmod(self.directory, 0o700)
        self.name = str(self.directory / 'auth.sock')
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(self.name)
        os.chmod(self.name, 0o600)
        self.socket.listen(1)
        self.socket.settimeout(.2)
        self.stop = threading.Event()
        self.callback = callback
        self.connection = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def acl_is_private(self):
        return private_endpoint(self.name)

    def _serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError as error:
                if not self.stop.is_set() and error.errno in {errno.ECONNABORTED, errno.EINTR, errno.EAGAIN}:
                    continue
                return
            with connection:
                self.connection = connection
                dispatched = False
                packet = None
                try:
                    connection.settimeout(22)
                    if peer_identity(connection)[0] != os.getuid():
                        raise AuthSocketError('auth_peer_identity_mismatch')
                    packet = read_message(connection)
                    dispatched = True
                    value = self.callback(packet)
                    write_message(connection, value)
                except Exception:
                    try:
                        write_message(connection, {'ok': False, 'state': 'auth_result_unknown' if dispatched else 'auth_transport_failed',
                                                   'login_submitted': None if dispatched else False, 'system_write': False})
                    except Exception:
                        pass
                finally:
                    if isinstance(packet, dict):
                        packet.clear()
                    self.connection = None

    def close(self):
        self.stop.set()
        if self.connection is not None:
            try: self.connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self.socket.close()
        self.thread.join(timeout=1)
        Path(self.name).unlink(missing_ok=True)
        self.directory.rmdir()
