import os
import tempfile

# Must be set before the app module is imported.
os.environ.setdefault("ORBIT_STORAGE_DIR", tempfile.mkdtemp(prefix="orbit-test-"))
