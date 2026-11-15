# Test package initialization.

# The suite must not inherit a portable run from the shell, or every config test
# would suddenly write under that drive's data folder.
import os as _os

for _name in ("BABYAI_HOME", "BABYAI_PAIRING"):
    _os.environ.pop(_name, None)
