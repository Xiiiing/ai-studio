"""Model credentials are referenced on Wkp, never exported into a deployment."""
import pytest
from scripts.wkp_inference_service import credential_directives


def test_credential_mounts_ignore_environment_values_and_unused_bindings():
    text = "Environment=PRIVATE=not-for-export\nLoadCredential=writer:/private/model-key\nLoadCredential=unused:/other\n"
    value = credential_directives(text, {"writer"})
    assert value == "LoadCredential=writer:/private/model-key\n"
    assert "not-for-export" not in value and "unused" not in value


def test_missing_or_reset_credential_mount_refuses_installation():
    with pytest.raises(RuntimeError, match="lacks required"):
        credential_directives("LoadCredential=writer:/old\nLoadCredential=\n", {"writer"})
