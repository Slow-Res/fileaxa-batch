import sys

# Absolute import so this file works both as a package entry
# (python -m fileaxa_batch) AND as a PyInstaller frozen script,
# where __package__ is unset and relative imports raise ImportError.
from fileaxa_batch.app import main

sys.exit(main())
