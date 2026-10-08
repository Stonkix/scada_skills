"""Write contracts/: openapi.json (REST), event.schema.json (sensor events), ws.schema.json (WebSocket).

Run after any schema change:  python api/scripts/export_contracts.py
The frontend generates its types from these files (openapi-typescript).
"""

import json
from pathlib import Path

from pydantic import TypeAdapter
from scada_common.events import EVENT_ADAPTER

from app.live.schemas import WS_SERVER_ADAPTER, WsSubscribe
from app.main import app

OUT = Path(__file__).resolve().parents[2] / "contracts"


def write(name: str, data: dict) -> None:
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote contracts/{name}")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    write("openapi.json", app.openapi())
    write("event.schema.json", {"title": "Event", **EVENT_ADAPTER.json_schema()})
    write("ws.schema.json", {
        "title": "WebSocket /ws/live",
        "description": "client → server: WsSubscribe; server → client: WsServerMessage",
        "$defs": {
            "WsSubscribe": TypeAdapter(WsSubscribe).json_schema(ref_template="#/$defs/WsSubscribe/$defs/{model}"),
            "WsServerMessage": WS_SERVER_ADAPTER.json_schema(ref_template="#/$defs/WsServerMessage/$defs/{model}"),
        },
    })


if __name__ == "__main__":
    main()
