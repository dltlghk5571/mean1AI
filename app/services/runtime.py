from app.config import Settings
from app.services.ai_queue import validate_local_queue
from app.services.classifier import Classifier, DepartmentCatalog, RuleBasedClassifier
from app.services.knowledge import KnowledgeRetriever
from app.services.openai_classifier import OpenAIClassifier
from app.services.pipeline import ComplaintPipeline


def build_pipeline(settings: Settings) -> ComplaintPipeline:
    deferred = settings.ai_deferred_enabled and settings.ai_provider != "rules"
    if deferred:
        validate_local_queue(settings)
        if (
            settings.openai_api_key is None
            or not settings.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError(
                "OPENAI_API_KEY is required when deferred OpenAI processing is enabled"
            )
    catalog = DepartmentCatalog.from_json(settings.departments_path)
    retriever = KnowledgeRetriever(settings.knowledge_dir)
    classifier: Classifier
    if settings.ai_provider == "openai" and settings.openai_api_key is not None:
        classifier = OpenAIClassifier(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            catalog=catalog,
            max_retries=0 if deferred else 1,
        )
    else:
        classifier = RuleBasedClassifier(catalog)
    return ComplaintPipeline(
        settings=settings, classifier=classifier, catalog=catalog, retriever=retriever
    )
