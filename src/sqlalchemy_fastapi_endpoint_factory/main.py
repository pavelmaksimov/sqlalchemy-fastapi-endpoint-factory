import enum
from typing import Any, List, Optional, Type, Callable, Coroutine

from pydantic import BaseModel, Field, create_model
from sqlalchemy import select


def create_enum_by_names(field_names: List[str], model) -> enum.Enum:
    return enum.Enum(
        f"{model.__name__}FieldsEnum", {name: name for name in field_names}
    )


class SqlAlchemyEndpointBuilder:
    class Operator(enum.Enum):
        equal = "="
        not_equal = "!="
        less = "<"
        more = ">"
        more_or_equal = ">="
        less_or_equal = "<="
        like = "like"
        not_like = "not_like"
        ilike = "ilike"
        not_ilike = "not_ilike"
        in_ = "in"
        not_in = "not_in"
        contains = "contains"
        not_contains = "not_contains"

    class FilterModel(BaseModel):
        field: enum.Enum = Field(description="Имя поля модели")
        operator: "SqlAlchemyEndpointBuilder.Operator"
        value: str | int | float | list | bool

    class BodyModel(BaseModel):
        filters: Optional[List["SqlAlchemyEndpointBuilder.FilterModel"]] = None
        fields: Optional[List[enum.Enum]] = None
        order_by: Optional[List[enum.Enum]] = None

    @classmethod
    def build_filter(cls, column, operator: Operator, value: Any):
        match operator:
            case cls.Operator.equal:
                return column == value
            case cls.Operator.not_equal:
                return column != value
            case cls.Operator.less:
                return column < value
            case cls.Operator.more:
                return column > value
            case cls.Operator.less_or_equal:
                return column <= value
            case cls.Operator.more_or_equal:
                return column >= value
            case cls.Operator.like:
                return column.like(value)
            case cls.Operator.not_like:
                return ~column.like(value)
            case cls.Operator.ilike:
                return column.ilike(value)
            case cls.Operator.not_ilike:
                return ~column.ilike(value)
            case cls.Operator.in_:
                if not isinstance(value, list):
                    value = [value]
                return column.in_(value)
            case cls.Operator.not_in:
                if not isinstance(value, list):
                    value = [value]
                return ~column.in_(value)
            case cls.Operator.contains:
                return column.contains(value)
            case cls.Operator.not_contains:
                return ~column.contains(value)
            case _:
                raise ValueError(f"Оператор '{operator}' не поддерживается")

    @classmethod
    def make_body_schema(
        cls, sqlalchemy_model: Type, base_model: Type[BodyModel] = BodyModel
    ) -> Type[BodyModel]:
        field_names = [col.name for col in sqlalchemy_model.__table__.columns]
        FieldEnum = create_enum_by_names(field_names, sqlalchemy_model)
        OrderByFieldEnum = create_enum_by_names(
            field_names + ["-" + name for name in field_names], sqlalchemy_model
        )

        FilterSchema = create_model(
            f"{sqlalchemy_model.__name__}FilterSchema",
            __base__=cls.FilterModel,
            field=(FieldEnum, ...),
        )

        return create_model(
            f"{sqlalchemy_model.__name__}Schema",
            __base__=base_model,
            filters=(Optional[List[FilterSchema]], None),
            fields=(Optional[List[FieldEnum]], None),
            order_by=(Optional[List[OrderByFieldEnum]], None),
        )

    @classmethod
    def build_query(cls, model: Type, body: BodyModel):
        if body.fields:
            query = (
                select()
                .select_from(model)
                .add_columns(*(getattr(model, field.value) for field in body.fields))
            )
        else:
            query = select(
                *(getattr(model, field.name) for field in model.__table__.columns)
            )
        if body.filters:
            for item in body.filters:
                column = getattr(model, item.field.value)
                query = query.where(cls.build_filter(column, item.operator, item.value))

        if body.order_by:
            query = query.order_by(
                *(
                    getattr(model, field.value[1:]).desc()
                    if field.value.startswith("-")
                    else getattr(model, field.value)
                    for field in body.order_by
                )
            )

        return query

    @classmethod
    def endpoint_factory(
        cls,
        model: Type,
        query_executor: Callable,
    ) -> Callable[[BodyModel], List[dict]]:
        BodySchema = cls.make_body_schema(model, base_model=cls.BodyModel)

        def endpoint(data: BodySchema):
            """Auto generate endpoint by sqlalchemy model"""
            query = cls.build_query(model, data)
            return query_executor(query)

        return endpoint

    @classmethod
    def async_endpoint_factory(
        cls,
        model: Type,
        query_executor: Callable,
    ) -> Callable[[BodyModel], Coroutine[Any, Any, List[dict]]]:
        BodySchema = cls.make_body_schema(model, base_model=cls.BodyModel)

        async def endpoint(data: BodySchema) -> List[dict]:
            """Auto generate endpoint by sqlalchemy model"""
            query = cls.build_query(model, data)
            return await query_executor(query)

        return endpoint


class SqlAlchemyEndpointWithPaginationBuilder(SqlAlchemyEndpointBuilder):
    class BodyModel(SqlAlchemyEndpointBuilder.BodyModel):
        limit: Optional[int] = 100
        offset: Optional[int] = None

    @classmethod
    def build_query(cls, model: Type, body: BodyModel):
        query = super().build_query(model, body)

        if body.limit is not None:
            query = query.limit(body.limit)
        if body.offset:
            query = query.offset(body.offset)

        return query


class SqlAlchemyEndpointWithScrollBuilder(SqlAlchemyEndpointBuilder):
    class BodyModel(SqlAlchemyEndpointBuilder.BodyModel):
        scroll_after_id: Optional[int] = None

    @classmethod
    def build_query(cls, model: Type, body: BodyModel):
        query = super().build_query(model, body)

        if body.scroll_after_id:
            query = query.where(model.id > body.scroll_after_id)

        return query


def make_endpoint(
    sqlalchemy_model: Type,
    sqlalchemy_query_executor: Callable,
    builder_class: type[SqlAlchemyEndpointBuilder] = SqlAlchemyEndpointBuilder,
) -> Callable:
    """
    :param sqlalchemy_model:
        class SimpleModel(Base):
            __tablename__ = "simple_model"

            id = Column(Integer, primary_key=True)
            username = Column(String)
            email = Column(String)

    :param sqlalchemy_query_executor:
        def execute_query(query):
            with Session() as session:
                result = session.execute(query).mappings()

            return [dict(row) for row in result]

    """
    return builder_class.endpoint_factory(sqlalchemy_model, sqlalchemy_query_executor)


def make_async_endpoint(
    sqlalchemy_model: Type,
    sqlalchemy_query_executor: Callable,
    builder_class: Type[SqlAlchemyEndpointBuilder] = SqlAlchemyEndpointBuilder,
) -> Callable:
    """
    :param sqlalchemy_model:
        class SimpleModel(Base):
            __tablename__ = "simple_model"

            id = Column(Integer, primary_key=True)
            username = Column(String)
            email = Column(String)

    :param sqlalchemy_query_executor:
        def execute_query(query):
            with Session() as session:
                result = session.execute(query).mappings()

            return [dict(row) for row in result]

    """
    return builder_class.async_endpoint_factory(
        sqlalchemy_model, sqlalchemy_query_executor
    )
