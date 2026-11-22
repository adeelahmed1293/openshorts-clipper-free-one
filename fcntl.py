"""Windows-compatible fcntl compatibility shim."""
import sys

LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8

if sys.platform == "win32":
    import msvcrt

    def flock(fd, operation):
        """Emulate or safely handle flock on Windows."""
        try:
            fileno = fd.fileno() if hasattr(fd, "fileno") else fd
            if operation & LOCK_UN:
                msvcrt.locking(fileno, msvcrt.LK_UNLCK, 1)
            elif operation & LOCK_NB:
                msvcrt.locking(fileno, msvcrt.LK_NBLCK, 1)
            else:
                msvcrt.locking(fileno, msvcrt.LK_LOCK, 1)
        except (OSError, IOError):
            # Ignore lock collisions / un-lockable files on local single-node dev
            pass

    def ioctl(*args, **kwargs):
        return 0

    def fcntl(*args, **kwargs):
        return 0
else:
    import fcntl as _orig_fcntl
    from fcntl import *

