from app.config import Settings
from app.database import Base, make_engine, make_session_factory
from app.main import build_pipeline
from app.schemas import Channel, ComplaintCreate
from app.seed import seed_departments

EXAMPLES = (
    ComplaintCreate(
        title="가로등이 꺼져 있습니다",
        content="정자동 공원 입구 가로등 두 개가 꺼져 밤길이 어둡습니다. 010-1234-5678",
        location_text="정자동 공원 입구",
        channel=Channel.WEB,
    ),
    ComplaintCreate(
        title="포트홀 신고",
        content="야탑역 인근 도로에 큰 포트홀이 생겨 차량이 피해서 다닙니다.",
        location_text="야탑역 인근",
        channel=Channel.SMS,
    ),
    ComplaintCreate(
        title="복지 지원 대상인지 문의",
        content="기초생활 지원 대상인지 자동으로 결정해 주세요.",
        location_text=None,
        channel=Channel.CALL_CENTER,
    ),
)


def main() -> None:
    settings = Settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    Base.metadata.create_all(engine)
    pipeline = build_pipeline(settings)

    with session_factory() as db:
        seed_departments(db, settings.departments_path)
        for payload in EXAMPLES:
            complaint = pipeline.create_and_process(db, payload)
            print(complaint.id, complaint.status, complaint.category)


if __name__ == "__main__":
    main()
