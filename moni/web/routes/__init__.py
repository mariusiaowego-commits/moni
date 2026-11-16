"""moni.web.routes — API router aggregation.

Register all route modules here.  ``create_app()`` imports this package
lazily and collects every router attached to ``_routers``.
"""

from __future__ import annotations

from fastapi import APIRouter

from moni.web.routes.articles import router as articles_router
from moni.web.routes.basket import router as basket_router
from moni.web.routes.config_filters import router as config_filters_router
from moni.web.routes.config_global_filters import router as config_global_filters_router
from moni.web.routes.config_sources import router as config_sources_router
from moni.web.routes.config_tags import router as config_tags_router
from moni.web.routes.cases import router as cases_router
from moni.web.routes.cron import router as cron_router
from moni.web.routes.crud import router as crud_router
from moni.web.routes.events import router as events_router
from moni.web.routes.fact_check_plus import router as fact_check_plus_router  # v1.0.22
from moni.web.routes.fetch import router as fetch_router
from moni.web.routes.llm import router as llm_router
from moni.web.routes.llm_jobs import router as llm_jobs_router
# v1.0.8: pin router — must be registered BEFORE articles_router so
# the literal path "/pinned" wins over the catch-all "/{article_id}".
# FastAPI matches in registration order for ambiguous routes.
from moni.web.routes.pins import router as pins_router
from moni.web.routes.reader import router as reader_router
from moni.web.routes.service import router as service_router
from moni.web.routes.share import router as share_router
from moni.web.routes.stats import router as stats_router  # v1.0.19: W1 量化看板

_routers: list[APIRouter] = [
    pins_router,  # v1.0.8: BEFORE articles_router (see above)
    articles_router,
    basket_router,
    cases_router,  # v1.0.17: F6 同业案例库
    config_filters_router,
    config_global_filters_router,
    config_sources_router,
    config_tags_router,
    cron_router,
    crud_router,
    events_router,
    fact_check_plus_router,  # v1.0.22
    fetch_router,
    llm_jobs_router,
    llm_router,
    reader_router,
    service_router,
    share_router,
    stats_router,  # v1.0.19: W1 量化看板 (MOA 4 视角拍板 A+A+A+A+A+(A+补丁))
]
