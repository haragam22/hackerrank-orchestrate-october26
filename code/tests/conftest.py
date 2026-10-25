import sys
from pathlib import Path

# Solution modules live directly under code/ (the folder name "code" shadows a stdlib module,
# so it is put on sys.path instead of being imported as a package).
CODE_DIR = Path(__file__).resolve().parent.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))
