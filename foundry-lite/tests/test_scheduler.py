from foundry.scheduler import _build_env_xml


def test_launchd_environment_never_persists_api_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-anthropic-value")
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-google-value")

    env_xml = _build_env_xml()

    assert "PATH" in env_xml
    assert "ANTHROPIC_API_KEY" not in env_xml
    assert "GOOGLE_API_KEY" not in env_xml
    assert "secret-" not in env_xml