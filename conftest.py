"""Pytest configuration: put the project root on sys.path so tests can
import the `app` and `db` packages regardless of the invocation directory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
