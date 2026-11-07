from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base

def make_session_factory(url: str) -> sessionmaker:
    connect_args = {}
    if url.startswith("mysql+pymysql:"):
        connect_args = {"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30}
    engine = create_engine(
        url, future=True, pool_pre_ping=True, pool_recycle=1800,
        connect_args=connect_args,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
