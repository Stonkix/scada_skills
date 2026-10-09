"""smoke sensor type: register it in sensor_types (data only)

The seed fills sensor_types from scada_common.catalog; a running installation gets the new
type here, so smoke detectors can be added from the API or the plan editor without a re-seed.

Revision ID: 5a1c7e2f9b04
Revises: 0ede8225617d
Create Date: 2026-10-09 14:20:00
"""

import json

import sqlalchemy as sa
from alembic import op

revision = '5a1c7e2f9b04'
down_revision = '0ede8225617d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from scada_common import SensorType, catalog

    t = next(x for x in catalog.SENSOR_TYPES if x["id"] == SensorType.SMOKE)
    op.get_bind().execute(
        sa.text("INSERT INTO sensor_types (id, name, is_mobile, metrics, payload_schema) "
                "VALUES (:id, :name, :mobile, CAST(:metrics AS jsonb), CAST(:schema AS jsonb)) ON CONFLICT (id) DO NOTHING"),
        {"id": t["id"].value, "name": t["name"], "mobile": t["is_mobile"], "metrics": json.dumps(t["metrics"], ensure_ascii=False),
         "schema": json.dumps(catalog.PAYLOAD_MODELS[SensorType.SMOKE].model_json_schema(), ensure_ascii=False)},
    )


def downgrade() -> None:
    op.execute("DELETE FROM sensor_types WHERE id = 'smoke' AND NOT EXISTS (SELECT 1 FROM sensors WHERE type = 'smoke')")
