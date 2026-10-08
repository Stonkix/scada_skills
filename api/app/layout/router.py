from fastapi import APIRouter, HTTPException, status

from app.layout.schemas import LayoutDocument, LayoutUpdate, ObjectsResponse
from app.mock import world

router = APIRouter(tags=["layout"])


@router.get("/objects", response_model=ObjectsResponse)
def get_objects() -> ObjectsResponse:
    return world.objects()


@router.get("/layout", response_model=LayoutDocument)
def get_layout() -> LayoutDocument:
    return LayoutDocument(version=world.layout_version, geojson=world.layout)


@router.post("/layout", response_model=LayoutDocument, responses={409: {"description": "Layout was changed by someone else"}})
def save_layout(body: LayoutUpdate) -> LayoutDocument:
    if body.base_version != world.layout_version:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Layout is at version {world.layout_version}, you edited {body.base_version}")
    if body.geojson.get("type") != "FeatureCollection":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "geojson must be a FeatureCollection")
    world.layout = body.geojson
    world.layout_version += 1
    return LayoutDocument(version=world.layout_version, geojson=world.layout)
