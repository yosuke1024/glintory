import pytest
from pydantic import ValidationError

from glintory.config import Settings


def test_settings_defaults():
    settings = Settings()
    assert settings.signal_title_max_chars == 500
    assert settings.signal_excerpt_max_chars == 5000
    assert settings.signal_url_max_chars == 4096
    assert settings.signal_metadata_max_bytes == 65536
    assert settings.signal_hash_version == "v1"
    assert settings.signal_default_source_quality_score == 0.5


def test_settings_validation():
    # Negative value for title max chars
    with pytest.raises(ValidationError):
        Settings(signal_title_max_chars=-1)

    # Score > 1.0
    with pytest.raises(ValidationError):
        Settings(signal_default_source_quality_score=1.5)

    # Score < 0.0
    with pytest.raises(ValidationError):
        Settings(signal_default_source_quality_score=-0.1)
