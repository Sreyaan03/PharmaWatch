"""
Portable deployment contract (static checks, no Docker daemon needed):
  - the image bakes in no dataset and runs as a non-root user,
  - there is one compose file with the core/kafka/demo profiles (the two old files are gone),
  - the health check reports the versions a pilot operator needs.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCKER = os.path.join(ROOT, "docker")


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def test_dockerfile_bakes_in_no_data():
    df = _read(DOCKER, "Dockerfile")
    assert "COPY data/" not in df, "the image must not bake in any dataset"


def test_dockerfile_runs_as_non_root():
    df = _read(DOCKER, "Dockerfile")
    assert "USER pharma" in df, "the container must run as a non-root user"


def test_single_compose_file_with_profiles():
    compose = _read(DOCKER, "compose.yml")
    for profile in ("[core]", "[kafka]", "[demo]"):
        assert f"profiles: {profile}" in compose, f"missing profile {profile}"


def test_old_compose_files_are_gone():
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        assert not os.path.exists(os.path.join(DOCKER, name)), f"{name} should be replaced by compose.yml"


def test_scale_compose_is_kept_separate():
    assert os.path.exists(os.path.join(DOCKER, "docker-compose.scale.yml")), \
        "horizontal scaling stays in its own file"


def test_health_reports_pilot_versions():
    import sys
    backend = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from services.health import health
    report = health()
    for key in ("status", "database", "model", "faers_data", "ddi_model", "jobs"):
        assert key in report, f"health check missing {key!r}"
