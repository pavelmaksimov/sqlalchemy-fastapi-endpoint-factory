```python
from fastapi import FastAPI
from sqlalchemy import Integer, Column, String, insert

from sqlalchemy_fastapi_endpoint_factory.main import make_endpoint
from sqlalchemy.orm import declarative_base
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

Base = declarative_base()


class SimpleModel(Base):
    __tablename__ = "simple_model"

    id = Column(Integer, primary_key=True)
    username = Column(String)
    email = Column(String)


engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
connection = engine.connect()
ScopedSession = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=connection)
)
SimpleModel.__table__.create(bind=engine, checkfirst=True)

# Test data
TEST_DATA = [
    {"id": 1, "username": "user1", "email": "user1@example.com"},
    {"id": 2, "username": "user2", "email": "user2@example.com"},
    {"id": 3, "username": "user3", "email": "user3@example.com"},
    {"id": 4, "username": "admin", "email": "admin@example.com"},
    {"id": 5, "username": "test", "email": "test@example.com"},
]
with engine.begin() as conn:
    conn.execute(insert(SimpleModel), TEST_DATA)


def execute_query(query):
    with ScopedSession() as session:
        result = session.execute(query).mappings()

    return [dict(row) for row in result]


app = FastAPI()
app.add_api_route(
    "/simple/filter",
    endpoint=make_endpoint(SimpleModel, execute_query),
    methods=["POST"],
)
```