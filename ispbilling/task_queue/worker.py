"""
Phase 1 worker entrypoint.

Usage (manual):
    /opt/ispbilling/venv/bin/python -m dramatiq queue.tasks --processes 2

systemd unit: isp-queue-worker.service runs this.
"""
import os
import sys
sys.path.insert(0, "/opt/ispbilling")
# Importing tasks side-effect-registers all @dramatiq.actor functions.
import task_queue.tasks  # noqa: F401
