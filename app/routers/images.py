from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import images

router = APIRouter(tags=["images"])


@router.get("/img/{id_product}")
def card_image(
    id_product: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    ready = images.serve_cached(db, id_product)
    if ready is None:
        if images.try_acquire_slot():
            try:
                ready = images.get_image(db, id_product)
            finally:
                images.release_slot()
        else:
            db.rollback()
            background.add_task(images.resolve_in_background, id_product)
            ready = images.placeholder(pending=True)
    else:
        db.rollback()
    content, content_type, cache_control = ready
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": cache_control},
    )
