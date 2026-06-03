"""Fake program that errors and exits immediately (simulates not-logged-in)."""
import sys

sys.stdout.write("Invalid API key\n")
sys.stdout.flush()
sys.exit(1)
