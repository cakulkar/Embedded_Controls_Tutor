import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from ecsim.app.services import bootstrap  # noqa: E402

HAS_CC = shutil.which("gcc") is not None
needs_cc = pytest.mark.skipif(not HAS_CC, reason="gcc not installed")


@pytest.fixture
def ctx(tmp_path):
    c = bootstrap(tmp_path, ":memory:")
    yield c
    c.close()


@pytest.fixture
def learner(ctx):
    return ctx.learning.create_learner("Test Learner", "JR_EMB_CTL")
