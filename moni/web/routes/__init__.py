"""moni.web.routes — API router aggregation.

Register all route modules here.  ``create_app()`` imports this package
lazily and collects every router attached to ``_routers``.
"""

from __future__ import annotations

from fastapi import APIRouter

from moni.web.routes.articles import router as articles_router
from moni.web.routes.crud import router as crud_router
from moni.web.routes.cron import router as cron_router
from moni.web.routes.fetch import router as fetch_router
from moni.web.routes.llm import router as llm_router
from moni.web.routes.llm_jobs import router as llm_jobs_router
from moni.web.routes.share import router as share_router
from moni.web.routes.config_tags import router as config_tags_router
from moni.web.routes.config_sources import router as config_sources_router
from moni.web.routes.config_filters import router as config_filters_router
from moni.web.routes.service import router as service_router
from moni.web.routes.basket import router as basket_router

_routers: list[APIRouter] = [
    articles_router,
    crud_router,
    cron_router,
    fetch_router,
    llm_router,
    llm_jobs_router,  # v1.0.5.1: async job dashboard
    share_router,
    # v1.0.2 config (sources / tags / keyword filters)
    config_tags_router,
    config_sources_router,
    config_filters_router,
    service_router,  # service control (status/restart/stop)
    basket_router,  # v1.0.6.9: 菜篮子
]
