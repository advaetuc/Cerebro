"""Offline tests for the Spotify probe spike."""

import base64
import hashlib

import pytest

from spike.probe_spotify import build_report
from spike.spotify_auth import pkce_pair, validate_state


def test_pkce_s256_challenge_matches_verifier() -> None:
    verifier, challenge = pkce_pair("a-fixed-verifier-for-testing")
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert "=" not in challenge


def test_state_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="state mismatch"):
        validate_state("expected", "received")


def test_report_builder_excludes_banned_keys() -> None:
    report = build_report(
        [{"endpoint": "/me", "status": 200, "latency_ms": 1, "item_count": 1, "name": "private", "access_token": "secret"}],
        3,
    )
    assert "name" not in str(report)
    assert "access_token" not in str(report)
    assert report["unique_track_count"] == 3
