import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import complaints, departments, pages
from app.config import Settings, get_settings
from app.database import Base, make_engine, make_session_factory
from app.seed import seed_departments
from app.services.classifier import Classifier, DepartmentCatalog, RuleBasedClassifier
from app.services.knowledge import KnowledgeRetriever
from app.services.openai_classifier import OpenAIClassifier
from app.services.pipeline import ComplaintPipeline


def build_pipeline(settings: Settings) -> ComplaintPipeline:
    catalog = DepartmentCatalog.from_json(settings.departments_path)
    retriever = KnowledgeRetriever(settings.knowledge_dir)
    classifier: Classifier

    if settings.ai_provider == "openai" and settings.openai_api_key is not None:
        classifier = OpenAIClassifier(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            catalog=catalog,
        )
    else:
        classifier = RuleBasedClassifier(catalog)

    return ComplaintPipeline(
        settings=settings,
        classifier=classifier,
        catalog=catalog,
        retriever=retriever,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    effective_settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, effective_settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    engine = make_engine(effective_settings.database_url)
    session_factory = make_session_factory(engine)
    pipeline = build_pipeline(effective_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        with session_factory() as db:
            seed_departments(db, effective_settings.departments_path)
        yield
        engine.dispose()

    app = FastAPI(
        title=effective_settings.app_name,
        version="0.1.0",
        description=(
            "Human-in-the-loop Korean civic complaint triage prototype. "
            "Demo data only; no live government integration."
        ),
        lifespan=lifespan,
    )
    app.state.settings = effective_settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.pipeline = pipeline
    templates_dir = effective_settings.package_dir / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    app.mount(
        "/static",
        StaticFiles(directory=str(effective_settings.package_dir / "static")),
        name="static",
    )
    app.include_router(pages.router)
    app.include_router(complaints.router)
    app.include_router(departments.router)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "environment": effective_settings.app_env,
            "classifier": getattr(pipeline.classifier, "provider_name", "unknown"),
        }

    return app


app = create_app()
