"""Cross-platform task runner (Windows has no make). The Makefile just forwards here.

    python dev.py up         start everything and wait for healthchecks (db-init migrates and seeds an empty DB)
    python dev.py down       stop containers, keep data
    python dev.py reset      stop and wipe all data volumes, then start again
    python dev.py migrate    apply PG/CH migrations and Redis groups from the host
    python dev.py seed       regenerate the plan and reset all stores to the demo state
    python dev.py revision "msg"   autogenerate an Alembic migration from db/scada_db/models.py
    python dev.py contracts  regenerate contracts/*.json from the code
    python dev.py test       run all tests (db tests need the stack running)
    python dev.py api        run the API locally with reload (port 8000)
    python dev.py ps|logs    docker compose ps / logs -f
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
COMPOSE = ["docker", "compose", "--env-file", str(ROOT / ".env"), "-f", str(ROOT / "deploy" / "docker-compose.yml")]
ARGS = sys.argv[2:]


def ensure_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        print("created .env from .env.example")


def host_env() -> dict[str, str]:
    """.env values for processes run on the host, so ports changed in .env apply everywhere."""
    ensure_env()
    env = dict(os.environ)
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def run(*cmd: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(cmd), flush=True)
    if subprocess.call(cmd, cwd=cwd, env=host_env()) != 0:
        sys.exit(1)


def up() -> None:
    run(*COMPOSE, "up", "-d", "--build", "--wait")


def down() -> None:
    run(*COMPOSE, "down")


def reset() -> None:
    run(*COMPOSE, "down", "-v")
    up()


def migrate() -> None:
    run(PY, "-m", "scada_db", "migrate")


def seed() -> None:
    run(PY, "deploy/seed/build_layout.py")
    run(PY, "-m", "scada_db", "seed", "--force")


def revision() -> None:
    run(PY, "-m", "scada_db", "revision", ARGS[0])


def contracts() -> None:
    run(PY, "api/scripts/export_contracts.py")


def test() -> None:
    run(PY, "-m", "pytest", "-q", "common/tests", "api/tests", "db/tests", "connectors/tests", "simulator/tests", "worker/tests")


def api() -> None:
    run(PY, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000", cwd=ROOT / "api")


def ps() -> None:
    run(*COMPOSE, "ps")


def logs() -> None:
    run(*COMPOSE, "logs", "-f", "--tail", "100")


COMMANDS = {f.__name__: f for f in (up, down, reset, migrate, seed, revision, contracts, test, api, ps, logs)}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else None
    if name not in COMMANDS or (name == "revision") != (len(ARGS) == 1) or (name != "revision" and ARGS):
        print(__doc__)
        sys.exit(2)
    COMMANDS[name]()
