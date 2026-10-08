"""Cross-platform task runner (Windows has no make). The Makefile just forwards here.

    python dev.py up         start everything and wait for healthchecks
    python dev.py down       stop containers, keep data
    python dev.py reset      stop and wipe all data volumes, then start again
    python dev.py seed       regenerate the plan; DB seeding lands in stage 1
    python dev.py contracts  regenerate contracts/*.json from the code
    python dev.py test       run all tests
    python dev.py api        run the API locally with reload (port 8000)
    python dev.py ps|logs    docker compose ps / logs -f
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
COMPOSE = ["docker", "compose", "--env-file", str(ROOT / ".env"), "-f", str(ROOT / "deploy" / "docker-compose.yml")]


def run(*cmd: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(cmd), flush=True)
    if subprocess.call(cmd, cwd=cwd) != 0:
        sys.exit(1)


def ensure_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        print("created .env from .env.example")


def up() -> None:
    ensure_env()
    run(*COMPOSE, "up", "-d", "--build", "--wait")


def down() -> None:
    ensure_env()
    run(*COMPOSE, "down")


def reset() -> None:
    ensure_env()
    run(*COMPOSE, "down", "-v")
    up()


def seed() -> None:
    run(PY, "deploy/seed/build_layout.py")
    print("DB seeding is not implemented yet (stage 1)")


def contracts() -> None:
    run(PY, "api/scripts/export_contracts.py")


def test() -> None:
    run(PY, "-m", "pytest", "-q", "common/tests", "api/tests")


def api() -> None:
    run(PY, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000", cwd=ROOT / "api")


def ps() -> None:
    run(*COMPOSE, "ps")


def logs() -> None:
    run(*COMPOSE, "logs", "-f", "--tail", "100")


COMMANDS = {f.__name__: f for f in (up, down, reset, seed, contracts, test, api, ps, logs)}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    COMMANDS[sys.argv[1]]()
