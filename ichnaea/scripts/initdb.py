"""
Initialize Ichnaea database schema and users for the first time.

Script is installed as `location_initdb`.
"""

import argparse
from collections import namedtuple
import os
import sys

from alembic.config import Config
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from ichnaea.config import read_config
from ichnaea.db import configure_db
from ichnaea.log import configure_raven

# make sure all models are imported
from ichnaea.models import _Model

DBCreds = namedtuple('DBCreds', 'user pwd')


def _db_creds(connection):
    # for example 'mysql+pymysql://user:pwd@localhost/location'
    result = connection.split('@')[0].split('//')[-1].split(':')
    return DBCreds(*result)


def add_api_key(conn):  # pragma: no cover
    stmt = text('select valid_key from api_key')
    result = conn.execute(stmt).fetchall()
    if not ('test', ) in result:
        stmt = text('INSERT INTO api_key (valid_key) VALUES ("test")')
        conn.execute(stmt)


def add_export_config(conn):  # pragma: no cover
    stmt = text('select name from export_config')
    result = conn.execute(stmt).fetchall()
    if not ('internal', ) in result:
        stmt = text('''\
INSERT INTO export_config (`name`, `batch`, `schema`, `skip_keys`)
VALUES ("internal", 100, "internal", "test")
''')
        conn.execute(stmt)


def add_users(conn, location_cfg):  # pragma: no cover
    # We don't take into account hostname or database restrictions
    # the users / grants, but use global privileges.
    database_section = location_cfg.get_map('database')

    creds = {}
    creds['rw'] = _db_creds(database_section.get('rw_url'))
    creds['ro'] = _db_creds(database_section.get('ro_url'))

    stmt = text('SELECT user FROM mysql.user')
    result = conn.execute(stmt)
    userids = set([r[0] for r in result.fetchall()])

    create_stmt = text('CREATE USER :user IDENTIFIED BY :pwd')
    grant_stmt = text('GRANT delete, insert, select, update ON *.* TO :user')
    for cred in creds.values():
        if cred.user not in userids:
            conn.execute(create_stmt.bindparams(user=cred.user, pwd=cred.pwd))
            conn.execute(grant_stmt.bindparams(user=cred.user))
    # create a monitoring user without a password nor grants
    if 'lbcheck' not in userids:
        conn.execute(text('CREATE USER lbcheck'))


def create_schema(engine, alembic_cfg, location_cfg):  # pragma: no cover
    old_version = False
    with engine.connect() as conn:
        trans = conn.begin()
        stmt = text('select version_num from alembic_version')
        try:
            result = conn.execute(stmt).fetchall()
            if len(result):
                old_version = True
        except ProgrammingError:
            pass

        if not old_version:
            _Model.metadata.create_all(engine)

        add_api_key(conn)
        add_export_config(conn)
        add_users(conn, location_cfg)

        trans.commit()

    # Now stamp the latest alembic version
    if not old_version:
        command.stamp(alembic_cfg, 'head')
    command.current(alembic_cfg)


def main(argv, _db_rw=None, _raven_client=None):  # pragma: no cover
    parser = argparse.ArgumentParser(
        prog=argv[0], description='Initialize Ichnaea database.')

    parser.add_argument('--alembic_ini',
                        help='Path to the alembic migration config.')
    parser.add_argument('--location_ini',
                        help='Path to the ichnaea app config.')
    parser.add_argument('--initdb', action='store_true',
                        help='Initialize database.')

    args = parser.parse_args(argv[1:])

    if args.initdb:
        # Either use explicit config file location or fallback
        # on environment variable or finally file in current directory
        if not args.location_ini:
            location_ini = os.environ.get('ICHNAEA_CFG', 'location.ini')
        else:
            location_ini = args.location_ini
        location_ini = os.path.abspath(location_ini)
        location_cfg = read_config(filename=location_ini)

        # Either use explicit config file location or fallback
        # to a file in the same directory as the location.ini
        if not args.alembic_ini:
            alembic_ini = os.path.join(
                os.path.dirname(location_ini), 'alembic.ini')
        else:
            alembic_ini = args.alembic_ini
        alembic_ini = os.path.abspath(alembic_ini)
        alembic_cfg = Config(alembic_ini)
        alembic_section = alembic_cfg.get_section('alembic')

        db_rw = configure_db(
            alembic_section['sqlalchemy.url'], _db=_db_rw)
        configure_raven(
            location_cfg.get('sentry', 'dsn'),
            transport='sync', _client=_raven_client)

        engine = db_rw.engine
        create_schema(engine, alembic_cfg, location_cfg)
    else:
        parser.print_help()


def console_entry():  # pragma: no cover
    main(sys.argv)
