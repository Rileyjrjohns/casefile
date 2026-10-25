import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from casefile.bootstrap.schema import database_metadata

config = context.config
url = config.get_main_option("sqlalchemy.url") or os.environ.get("CASEFILE_DATABASE_URL") or ""
if not url:
    raise RuntimeError("Set CASEFILE_DATABASE_URL to the PostgreSQL database to migrate")
target_metadata = database_metadata()


def run_migrations() -> None:
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    settings = dict(config.get_section(config.config_ini_section) or {})
    settings["sqlalchemy.url"] = url
    engine = engine_from_config(settings, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
