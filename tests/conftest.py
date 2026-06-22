import os


def pytest_runtest_setup(item):
    os.environ["RAG_ENABLE_REMOTE_CALLS"] = ""
    # Paid providers must be enabled explicitly inside an individual test.
    os.environ["IMAGE_GENERATION_PROVIDER"] = "disabled"
