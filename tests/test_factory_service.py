"""Validate systemd path rendering rules without installing or restarting units."""
from pathlib import Path

import pytest

from scripts.factory_service import validate_install_path


@pytest.mark.parametrize("path", ["/srv/ai-studio", "/opt/ai-studio", "/srv/studios/project_001"])
def test_normal_checkout_paths_are_accepted(path):
    validate_install_path(Path(path))


@pytest.mark.parametrize("path", ["/srv/ai studio", "/srv/tab\tpath", "/srv/line\npath", "/srv/percent%path", '/srv/quote"path', "/srv/back\\path"])
def test_systemd_ambiguous_paths_are_rejected(path):
    with pytest.raises(ValueError, match="systemd"):
        validate_install_path(Path(path))
