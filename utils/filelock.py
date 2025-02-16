import os
import time
import errno
from contextlib import contextmanager

class FileLockException(Exception):
    pass

@contextmanager
def file_lock(filepath):
    """
    Thread/process-safe file locking context manager.
    
    Args:
        filepath: Path to the file being locked
        
    Raises:
        FileLockException if lock cannot be acquired
    """
    lock_path = f"{filepath}.lock"
    timeout = 60  # Maximum wait time in seconds
    start_time = time.time()
    
    while True:
        try:
            # Try to create the lock file
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            with os.fdopen(fd, 'w') as f:
                # Write PID into lock file
                f.write(f"{os.getpid()}")
            break
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
            # Check if we've waited too long
            if time.time() - start_time >= timeout:
                raise FileLockException(
                    f"Timeout waiting for lock on {filepath}"
                )
            # Check if the lock file is stale
            try:
                lock_time = os.path.getmtime(lock_path)
                if time.time() - lock_time > timeout:
                    os.remove(lock_path)
                    continue
            except OSError:
                # Lock file was removed before we could check it
                continue
            # Wait before trying again
            time.sleep(0.1)
    
    try:
        yield
    finally:
        try:
            os.unlink(lock_path)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
