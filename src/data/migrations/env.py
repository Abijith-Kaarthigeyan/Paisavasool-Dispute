from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from src.core.config.settings import settings
from src.data.clients.postgres_client import DISPUTE_SCHEMA, Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.DB_SYNC_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in [DISPUTE_SCHEMA]
    if type_ == "table":
        schema_name = parent_names.get("schema_name")
        return schema_name in [None, DISPUTE_SCHEMA]
    return True


def include_object(object, name, type_, reflected, compare_to):
    # Do not include anything outside of the 'dispute' schema
    return getattr(object, "schema", None) == DISPUTE_SCHEMA


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DB_SYNC_URL,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        version_table_schema=DISPUTE_SCHEMA,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.execute(sa_text(f"CREATE SCHEMA IF NOT EXISTS {DISPUTE_SCHEMA}"))
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        connection.execute(sa_text(f"CREATE SCHEMA IF NOT EXISTS {DISPUTE_SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
            include_object=include_object,
            version_table_schema=DISPUTE_SCHEMA,
        )

        with context.begin_transaction():
            context.run_migrations()


def sa_text(statement: str):
    return text(statement)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
