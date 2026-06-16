import os


def pytest_runtest_setup(item):
    os.environ.setdefault("RAG_ENABLE_REMOTE_CALLS", "")
