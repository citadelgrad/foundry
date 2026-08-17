from pathlib import Path


DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"
MAKEFILE = Path(__file__).parents[1] / "Makefile"


def test_runner_image_uses_pinned_verified_security_tools():
    dockerfile = DOCKERFILE.read_text()

    assert "ARG UBS_VERSION=5.3.8" in dockerfile
    assert "ARG UBS_SHA256=" in dockerfile
    assert 'npm install -g "@ast-grep/cli@${AST_GREP_VERSION}"' in dockerfile
    assert 'ast-grep --version | grep -F "ast-grep ${AST_GREP_VERSION}"' in dockerfile
    assert "ubs doctor --fix" in dockerfile


def test_runner_image_does_not_pipe_remote_installers_to_shell():
    dockerfile = DOCKERFILE.read_text()

    assert "| bash" not in dockerfile
    assert "ultimate_bug_scanner/main/install.sh" not in dockerfile


def test_runner_image_is_local_only():
    makefile = MAKEFILE.read_text()

    assert "IMAGE := foundry-runner:local" in makefile
    assert "docker-push" not in makefile
    assert "ghcr.io" not in makefile
