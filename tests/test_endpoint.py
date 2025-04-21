import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from starlette.testclient import TestClient

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

from sqlalchemy import insert

from sqlalchemy_fastapi_endpoint_factory.main import (
    make_endpoint,
    SqlAlchemyEndpointWithPaginationBuilder,
    SqlAlchemyEndpointWithScrollBuilder,
)

Base = declarative_base()


class SimpleModel(Base):
    __tablename__ = "simple_model"

    id = Column(Integer, primary_key=True)
    username = Column(String)
    email = Column(String)


engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
connection = engine.connect()
TestingSessionLocal = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=connection)
)
SimpleModel.__table__.create(bind=engine, checkfirst=True)

TEST_DATA = [
    {"id": 1, "username": "user1", "email": "user1@example.com"},
    {"id": 2, "username": "user2", "email": "user2@example.com"},
    {"id": 3, "username": "user3", "email": "user3@example.com"},
    {"id": 4, "username": "admin", "email": "admin@example.com"},
    {"id": 5, "username": "test", "email": "test@example.com"},
]


@pytest.fixture(scope="module")
def db_session():
    with engine.begin() as conn:
        conn.execute(insert(SimpleModel), TEST_DATA)

    yield TestingSessionLocal()

    TestingSessionLocal.remove()
    connection.close()
    engine.dispose()


@pytest.fixture
def app(db_session):
    app = FastAPI()

    def sync_query_executor(query):
        return [dict(row) for row in db_session.execute(query).mappings()]

    app.add_api_route(
        "/sync",
        endpoint=make_endpoint(SimpleModel, sync_query_executor),
        methods=["POST"],
    )
    app.add_api_route(
        "/pagination",
        endpoint=make_endpoint(
            SimpleModel,
            sync_query_executor,
            builder_class=SqlAlchemyEndpointWithPaginationBuilder,
        ),
        methods=["POST"],
    )
    app.add_api_route(
        "/scroll",
        endpoint=make_endpoint(
            SimpleModel,
            sync_query_executor,
            builder_class=SqlAlchemyEndpointWithScrollBuilder,
        ),
        methods=["POST"],
    )

    return app


@pytest.fixture
def client(app):
    yield TestClient(app)


def test_basic_filter(client):
    response = client.post(
        "/sync",
        json={"filters": [{"field": "username", "operator": "=", "value": "user1"}]},
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert len(data) == 1
    assert data[0]["username"] == "user1"


def test_all_operators(client):
    test_cases = [
        {
            "operator": "=",
            "field": "username",
            "value": "user1",
            "expected_ids": [1],
        },
        {
            "operator": "!=",
            "field": "username",
            "value": "user1",
            "expected_ids": [2, 3, 4, 5],
        },
        {
            "operator": "<",
            "field": "id",
            "value": 3,
            "expected_ids": [1, 2],
        },
        {
            "operator": ">",
            "field": "id",
            "value": 3,
            "expected_ids": [4, 5],
        },
        {
            "operator": ">=",
            "field": "id",
            "value": 3,
            "expected_ids": [3, 4, 5],
        },
        {
            "operator": "<=",
            "field": "id",
            "value": 3,
            "expected_ids": [1, 2, 3],
        },
        {
            "operator": "like",
            "field": "username",
            "value": "user%",
            "expected_ids": [1, 2, 3],
        },
        {
            "operator": "not_like",
            "field": "username",
            "value": "user%",
            "expected_ids": [4, 5],
        },
        {
            "operator": "ilike",
            "field": "username",
            "value": "USER%",
            "expected_ids": [1, 2, 3],
        },
        {
            "operator": "not_ilike",
            "field": "username",
            "value": "USER%",
            "expected_ids": [4, 5],
        },
        {
            "operator": "in",
            "field": "id",
            "value": [2, 4],
            "expected_ids": [2, 4],
        },
        {
            "operator": "not_in",
            "field": "id",
            "value": [2, 4],
            "expected_ids": [1, 3, 5],
        },
        {
            "operator": "contains",
            "field": "email",
            "value": "example",
            "expected_ids": [1, 2, 3, 4, 5],
        },
        {
            "operator": "not_contains",
            "field": "email",
            "value": "example",
            "expected_ids": [],
        },
    ]

    for case in test_cases:
        response = client.post(
            "/sync",
            json={
                "filters": [
                    {
                        "field": case["field"],
                        "operator": case["operator"],
                        "value": case["value"],
                    }
                ]
            },
        )
        assert response.status_code == 200, response.json()

        data = response.json()
        result_ids = [item["id"] for item in data]

        assert sorted(result_ids) == sorted(case["expected_ids"]), (
            f"Failed for operator '{case['operator']}'. Expected {case['expected_ids']}, got {result_ids}"
        )


def test_multiple_filters(client):
    response = client.post(
        "/sync",
        json={
            "filters": [
                {"field": "username", "operator": "like", "value": "user%"},
                {"field": "id", "operator": "<", "value": 3},
            ]
        },
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert len(data) == 2
    assert all(item["username"].startswith("user") for item in data)
    assert all(item["id"] < 3 for item in data)


def test_fields_selection(client):
    response = client.post(
        "/sync",
        json={
            "fields": ["username"],
            "filters": [{"field": "id", "operator": "=", "value": 1}],
        },
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert len(data) == 1
    assert set(data[0].keys()) == {"username"}


def test_order_by(client):
    response = client.post(
        "/sync",
        json={
            "order_by": ["id"],
        },
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert [item["id"] for item in data] == [1, 2, 3, 4, 5]

    response = client.post(
        "/sync",
        json={
            "order_by": ["id"],
            "filters": [{"field": "id", "operator": ">", "value": 2}],
        },
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert [item["id"] for item in data] == [3, 4, 5]


def test_pagination(client):
    response = client.post(
        "/pagination",
        json={
            "limit": 2,
            "offset": 1,
            "order_by": ["id"],
        },
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert len(data) == 2
    assert [item["id"] for item in data] == [2, 3]


def test_scroll(client):
    response = client.post(
        "/scroll",
        json={
            "scroll_after_id": 2,
            "order_by": ["id"],
        },
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert len(data) == 3
    assert [item["id"] for item in data] == [3, 4, 5]


def test_in_operator(client):
    response = client.post(
        "/sync",
        json={"filters": [{"field": "id", "operator": "in", "value": [1, 3, 5]}]},
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert len(data) == 3
    assert {item["id"] for item in data} == {1, 3, 5}


def test_contains_operator(client):
    response = client.post(
        "/sync",
        json={
            "filters": [{"field": "email", "operator": "contains", "value": "example"}]
        },
    )
    assert response.status_code == 200, response.json()

    data = response.json()

    assert len(data) == len(TEST_DATA)
    assert all("example" in item["email"] for item in data)
