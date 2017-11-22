# -*- coding: utf-8 -*-
import os
import os.path
import sys
from datetime import datetime, timedelta
import time
import json
import getpass
import logging
import pkg_resources

# pygments is optional for now
try:
    got_pygments = True
    from pygments import highlight, util
    from pygments.lexers import get_lexer_by_name
    from pygments.formatters import get_formatter_by_name, get_all_formatters
    from pygments.styles import get_style_by_name, get_all_styles
except ImportError:
    got_pygments = False

from driftconfig.relib import create_backend, get_store_from_url, diff_meta, diff_tables, CHECK_INTEGRITY
from driftconfig.config import get_drift_table_store, push_to_origin, pull_from_origin, TSTransaction, TSLocal
from driftconfig.config import update_cache
from driftconfig.backends import FileBackend
from driftconfig.util import config_dir, get_domains, get_default_drift_config, get_default_drift_config_and_source

log = logging.getLogger(__name__)


# Enable simple in-line color and styling of output
try:
    from colorama.ansi import Fore, Back, Style
    styles = {'f': Fore, 'b': Back, 's': Style}
    # Example: "{s.BRIGHT}Bold and {f.RED}red{f.RESET}{s.NORMAL}".format(**styles)
except ImportError:
    class EmptyString(object):
        def __getattr__(self, name):
            return ''

    styles = {'f': EmptyString(), 'b': EmptyString(), 's': EmptyString()}


def get_options(parser):

    subparsers = parser.add_subparsers(
        title="Config file management",
        description="These sets of commands help you with setting up configuration for Drift products.",
        dest="command",
    )

    # 'init' command
    p = subparsers.add_parser(
        'init',
        help='Initialize configuration from a given source.',
        description="Initialize configuration using a given source, like S3, and write it somewhere else, like on local disk.\n"
                    "An example of S3 source: s3://bucket-name/path-name"
    )
    p.add_argument(
        'source',
        action='store',
    )
    p.add_argument(
        '--ignore-errors', '-i',
        action='store_true',
        help='Ignore any errors.'
    )

    # 'list' command
    p = subparsers.add_parser(
        'list',
        help='List locally stored configurations.',
        description="List out all configuration that are stored locally."
    )

    # 'pull' command
    p = subparsers.add_parser(
        'pull',
        help='Pull config.',
        description="Pull latest configuration from source."
    )
    p.add_argument(
        '--loop',
        action='store_true',
        help='pull config continuously for 1 minute.'
    )
    p.add_argument(
        '--ignore-if-modified', '-i',
        action='store_true',
        help='Force a pull from origin even though local version has been modified.'
    )
    p.add_argument(
        '-f', '--force',
        action='store_true',
        help='Force a pull from origin even though local version matches.'
    )
    p.add_argument(
        'domain',
        action='store', nargs='?',
    )

    # 'cache' command
    p = subparsers.add_parser(
        'cache',
        help='Update cache for config.',
        description="Add the config to Redis cache ."
    )
    p.add_argument(
        'domain',
        action='store', nargs='?',
    )
    p.add_argument(
        '-t', '--tier',
        help="The tier on which to update cache, or all if ommitted."
    )

    # 'migrate' command
    p = subparsers.add_parser(
        'migrate',
        help='Migrate config.',
        description="Migrate config to latest definition of TableStore."
    )
    p.add_argument(
        'domain',
        action='store',
    )

    # 'push' command
    p = subparsers.add_parser(
        'push',
        help='Push config.',
        description="Push local config to source. Use with causion."
    )
    p.add_argument(
        'domain',
        action='store',
    )
    p.add_argument(
        '-f', '--force',
        action='store_true',
        help='Force a push to origin even though origin has changed.'
    )

    # 'copy' command
    p = subparsers.add_parser(
        'copy',
        help='Copy config.',
        description="Copy config from one url to another."
    )
    p.add_argument(
        'source_url',
        action='store',  help="Source url, or . for default config url."
    )
    p.add_argument(
        'dest_url',
        action='store',
    )
    p.add_argument(
        '-p', '--pickle',
        action='store_true', help="Use pickle format for destination."
    )

    # 'diff' command
    p = subparsers.add_parser(
        'diff',
        help='Diff origin.',
        description="Diff local config to origin."
    )
    p.add_argument(
        'domain',
        action='store', help="Short name to identify the domain or owner of the config.",
    )
    p.add_argument(
        '-d', '--details',
        action='store_true',
        help='Do a detailed diff on modified tables.'
    )

    # CRUD command suite -------------------------------------------
    # 'create' command
    p = subparsers.add_parser(
        'create',
        description='Create a new Drift configuration.',
    )
    p.add_argument(
        'domain',
        action='store', help="Short name to identify the domain or owner of the config.",
    )
    p.add_argument(
        'source',
        action='store', help="The source location of the config, normally an S3 location."
    )
    p.add_argument(
        '--display-name',
        action='store', help="Display name."
    )

    # 'tier' command
    p = subparsers.add_parser(
        'tier',
        description='Add a new entry to the config database.',
    )

    p.add_argument(
        'action',
        action='store', help="Action to perform.",
        default='info',
        choices=['info', 'add', 'update', 'delete'],
    )

    p.add_argument(
        'name',
        action='store',
        help="The name of the tier.",
        nargs='?'
    )

    # 'addtenant' command
    p = subparsers.add_parser(
        'addtenant',
        help='Add a new tenant',
    )
    p.add_argument(
        'domain',
    )
    p.add_argument(
        '-n', '--name',
        required=True, help="Short name to identify the tenant.",
    )
    p.add_argument(
        '-t', '--tier',
        required=True, help="The name of the tier."
    )
    p.add_argument(
        '-o', '--organization',
        required=True, help="The name of the organization."
    )
    p.add_argument(
        '-p', '--product',
        required=True, help="The name of the product."
    )
    p.add_argument(
        '--preview',
        action='store_true',
        help="Preview the action."
    )
    p.add_argument(
        '-d', '--deployables',
        nargs='*',
        help="One or more deployables to create the tenant on."
    )


def init_command(args):
    print "Initializing config from", args.source
    from config import load_from_origin
    ##ts = get_store_from_url(args.source)
    if args.ignore_errors:
        del CHECK_INTEGRITY[:]
    ts = load_from_origin(create_backend(args.source))
    domain_name = ts.get_table('domain')['domain_name']
    print "Config domain name: ", domain_name
    local_store = create_backend('file://' + config_dir(domain_name, user_dir=args.user_dir))
    local_store.save_table_store(ts)
    print "Config stored at: ", local_store


def _format_domain_info(domain_info):
    domain = domain_info['table_store'].get_table('domain')
    return "{}: \"{}\" at '{}'. Origin: '{}'".format(
        domain['domain_name'], domain['display_name'], domain_info['path'], domain['origin'])


def list_command(args):
    # Enumerate subfolders at drift/config and see what's there
    domains = get_domains(user_dir=args.user_dir)
    if not domains:
        print "No Drift configuration found at", config_dir('', user_dir=args.user_dir)
    else:
        for d in domains.values():
            print _format_domain_info(d)


def pull_command(args):
    if args.loop:
        pull_config_loop(args)
    else:
        _pull_command(args)


def pull_config_loop(args):
    print "Starting the pull config loop"
    while now() < end_time:
        st = time.time()
        _pull_command(args)
        diff = time.time() - st
        this_sleep_time = max(sleep_time - diff, 0)
        print "Waiting for %.1f sec" % this_sleep_time
        time.sleep(this_sleep_time)
    print "Completed in %.1f sec" % (now() - start_time).total_seconds()


def _pull_command(args):
    for domain_name, domain_info in get_domains(user_dir=args.user_dir).items():
        if args.domain and args.domain != domain_name:
            continue

        result = pull_from_origin(domain_info['table_store'], ignore_if_modified=args.ignore_if_modified, force=args.force)

        if not result['pulled']:
            print "Pull failed for", domain_name, ". Reason:", result['reason']
            if result['reason'] == 'local_is_modified':
                print "Use --ignore-if-modified to overwrite local changes."
            else:
                print "Use --force to force a pull."
        else:
            if result['reason'] == 'pulled_from_origin':
                local_backend = create_backend('file://' + domain_info['path'])
                local_backend.save_table_store(result['table_store'])

            print "Config for {} pulled. Reason: {}".format(domain_name, result['reason'])


def cache_command(args):
    if args.domain:
        os.environ['DRIFT_CONFIG_URL'] = args.domain
    ts = get_default_drift_config()
    print "Updating cache for '{}' - {}".format(
        ts.get_table('domain')['domain_name'], ts)

    for tier in ts.get_table('tiers').find():
        tier_name = tier['tier_name']
        if args.tier and args.tier.upper() != tier_name:
            continue
        click.secho("{}: ".format(tier_name), nl=False, bold=True)
        try:
            b = update_cache(ts, tier_name)
        except Exception as e:
            if "Timeout" not in str(e):
                raise
            click.secho("Updating failed. VPN down? {}".format(e), fg='red', bold=True)
        else:
            if b:
                click.secho("Cache updated on {}.".format(b))
            else:
                click.secho("No Redis resource defined for this tier.", fg='red', bold=True)

    '''
    # bench test:
    def test_redis_config_fetch(count=10):
        import time
        import os
        from driftconfig.util import get_default_drift_config
        os.environ['DRIFT_CONFIG_URL'] = 'redis://redis.devnorth.dg-api.com/?prefix=dgnorth'
        t = time.time()
        for i in xrange(count):
            ts = get_default_drift_config()
        t = time.time() - t
        avg = t / count
        print "Average time to fetch config from redis: %.1f ms." % (avg * 1000.0)
    '''


def migrate_command(args):
    print "Migrating '{}'".format(args.domain)
    path = config_dir(args.domain, user_dir=args.user_dir)
    if not os.path.exists(path):
        print "Path not found:", path
        sys.exit(1)

    ts = get_drift_table_store()

    class PatchBackend(FileBackend):
        def load_data(self, file_name):
            path_name = self.get_filename(file_name)
            if not os.path.exists(path_name):
                # Attempt to create it just-in-time as a table with zero rows
                head, tail = os.path.split(path_name)
                if not os.path.exists(head):
                    os.makedirs(head)
                with open(path_name, 'w') as f:
                    f.write('[]\n')
            return super(PatchBackend, self).load_data(file_name)

    local_store = PatchBackend(path)
    ts._load_from_backend(local_store, skip_definition=True)
    local_store.save_table_store(ts)


def now():
    return datetime.utcnow()


sleep_time = 10
run_time = 50

start_time = now()
end_time = start_time + timedelta(seconds=run_time)


def push_command(args):
    domain_info = get_domains(user_dir=args.user_dir).get(args.domain)
    if not domain_info:
        print "Can't push '{}'.".format(args.domain)
        sys.exit(1)

    ts = domain_info['table_store']
    origin = ts.get_table('domain')['origin']
    print "Pushing local config to source", origin
    result = push_to_origin(ts, args.force)
    if not result['pushed']:
        print "Push failed. Reason:", result['reason']
        print "Origin has changed. Use --force to force push."
        if 'time_diff' in result:
            print "Time diff", result['time_diff']
    else:
        print "Config pushed. Reason: ", result['reason']
        local_store = create_backend('file://' + domain_info['path'])
        local_store.save_table_store(ts)


def copy_command(args):
    print "Copy '%s' to '%s'" % (args.source_url, args.dest_url)
    if args.source_url == '.':
        ts = get_default_drift_config()
    else:
        ts = get_store_from_url(args.source_url)
    b = create_backend(args.dest_url)
    b.default_format = 'pickle' if args.pickle else 'json'
    b.save_table_store(ts)
    print "Done."


def create_command(args):

    domain_info = get_domains(user_dir=args.user_dir).get(args.domain)
    if domain_info:
        print "The domain name '{}' is taken:".format(args.domain)
        print _format_domain_info(domain_info)
        sys.exit(1)

    # Force s3 naming convention. The root folder name and domain name must match.
    if args.source.startswith('s3://'):
        # Strip trailing slashes
        if args.source.endswith('/'):
            args.source = args.source[:-1]

        s3_backend = create_backend(args.source)
        target_folder = s3_backend.folder_name.rsplit('/')[-1]
        if target_folder != args.domain:
            print "Error: For S3 source, the target folder name and domain name must match."
            print "Target folder is '{}' but domain name is '{}'".format(target_folder, args.domain)
            print "Suggestion: {}".format(args.source.replace(target_folder, args.domain))
            sys.exit(1)
    elif args.source.startswith('file://'):
        # Expand user vars
        args.source = args.source.replace('~', os.path.expanduser('~'))

    # Get empty table store for Drift.
    ts = get_drift_table_store()
    ts.get_table('domain').add(
        {'domain_name': args.domain, 'origin': args.source, 'display_name': args.display_name or ''})

    # Save it locally
    domain_folder = config_dir(args.domain, user_dir=args.user_dir)
    local_store = create_backend('file://' + domain_folder)
    local_store.save_table_store(ts)
    print "New config for '{}' saved to {}.".format(args.domain, domain_folder)
    print "Pushing to origin..."
    result = push_to_origin(ts, _first=True)
    if not result['pushed']:
        print "Push failed. Reason:", result['reason']
    print "Done."


_ENTRY_TO_TABLE_NAME = {
    'tier': 'tiers',
    'deployable': 'deployable-names',
    'organization': 'organizations',
    'product': 'products',
}


def tier_command(args):
    if args.name is None and args.action != 'info':
        print "Tier name is missing!"
        sys.exit(1)

    if args.action == 'info':
        conf = get_default_drift_config()
        if args.name is None:
            print "Tiers:"
            for tier in conf.get_table('tiers').find():
                print "\t{} state={}, is_live={}".format(
                    tier['tier_name'].ljust(21), tier['state'], tier['is_live'])
        else:
            tier = conf.get_table('tiers').find({'tier_name': args.name})
            if not tier:
                print "No tier named {} found.".format(args.name)
                sys.exit(1)
            tier = tier[0]
            print "Tier {}:".format(tier['tier_name'])
            print json.dumps(tier, indent=4)

    elif args.action == 'add':
        with TSTransaction() as ts:
            tiers = ts.get_table('tiers')
            if tiers.find({'tier_name': args.name}):
                print "Tier {} already exists!".format(args.name)
                sys.exit(1)
            tiers.add({'tier_name': args.name})
    elif args.action == 'update':
        pass

    print "Done!"


def diff_command(args):
    # Get local table store and its meta state
    domain_info = get_domains(user_dir=args.user_dir).get(args.domain)
    if domain_info is None:
        click.secho("Configuration not found: {}".format(args.domain), fg='red')
        sys.exit(1)
    local_ts = domain_info['table_store']
    local_m1, local_m2 = local_ts.refresh_metadata()

    # Get origin table store meta info
    origin = local_ts.get_table('domain')['origin']
    origin_backend = create_backend(origin)
    origin_ts = origin_backend.load_table_store()
    origin_meta = origin_ts.meta.get()

    local_diff = ("Local store and scratch", local_m1, local_m2, False)
    origin_diff = ("Local and origin", origin_meta, local_m2, args.details)

    for title, m1, m2, details in local_diff, origin_diff:
        diff = diff_meta(m1, m2)
        if diff['identical']:
            print title, "is clean."
        else:
            print title, "are different:"
            print "\tFirst checksum: ", diff['checksum']['first'][:7]
            print "\tSecond checksum:", diff['checksum']['second'][:7]
            if diff['modified_diff']:
                print "\tTime since pull: ", str(diff['modified_diff']).split('.')[0]

            print "\tNew tables:", diff['new_tables']
            print "\tDeleted tables:", diff['deleted_tables']
            print "\tModified tables:", diff['modified_tables']

            if details:
                # Diff origin
                origin_ts = get_store_from_url(origin)
                for table_name in diff['modified_tables']:
                    t1 = local_ts.get_table(table_name)
                    t2 = origin_ts.get_table(table_name)
                    tablediff = diff_tables(t1, t2)
                    print "\nTable diff for", table_name, "\n(first=local, second=origin):"
                    print json.dumps(tablediff, indent=4, sort_keys=True)


def addtenant_command(args):

    print "Adding a new tenant."
    print "  Domain:      ", args.domain
    print "  Tenant:      ", args.name
    print "  Tier:        ", args.tier
    print "  Organization:", args.organization
    print "  Product:     ", args.product
    print "  Deployables: ", args.deployables

    domain_info = get_domains(user_dir=args.user_dir).get(args.domain)
    if not domain_info:
        print "The domain '{}'' is not found locally. Run 'init' to fetch it.".format(args.domain)
        sys.exit(1)

    print _format_domain_info(domain_info)

    ts = domain_info['table_store']
    row = ts.get_table('tenant-names').update({
        'tenant_name': args.name,
        'organization_name': args.organization,
        'product_name': args.product,
        'reserved_by': getpass.getuser(),
        'reserved_at': datetime.utcnow().isoformat() + 'Z',
    })

    print "\nNew tenant record:\n", json.dumps(row, indent=4)

    if args.deployables:
        print "Associating with deployables:"
        tenants = ts.get_table('tenants')
        for deployable_name in args.deployables:
            row = tenants.add({
                'tier_name': args.tier,
                'tenant_name': args.name,
                'deployable_name': deployable_name
            })
            print json.dumps(row, indent=4)

    if args.preview:
        print "Previewing only. Exiting now."
        sys.exit(0)

    # Save it locally
    local_store = create_backend('file://' + config_dir(args.domain, user_dir=args.user_dir))
    local_store.save_table_store(ts)
    print "Changes to config saved at {}.".format(local_store)
    print "Remember to push changes to persist them."


def run_command(args):
    fn = globals()["{}_command".format(args.command.replace("-", "_"))]
    fn(args)


def main(as_module=False):
    import argparse
    parser = argparse.ArgumentParser(description="")
    parser.add_argument('--loglevel', default='WARNING')
    parser.add_argument('--nocheck', action='store_true', help="Skip all relational integrity and schema checks.")
    parser.add_argument('--user-dir', action='store_true', help="Choose user directory over site for locally stored configs.")
    get_options(parser)
    args = parser.parse_args()

    if args.loglevel:
        logging.basicConfig(level=args.loglevel)

    if args.nocheck:
        import driftconfig.relib
        del driftconfig.relib.CHECK_INTEGRITY[:]

    fn = globals()["{}_command".format(args.command.replace("-", "_"))]
    fn(args)


if __name__ == '__main__':
    main(as_module=True)


import click
import posixpath


def _header(ts):
    domain = ts.get_table('domain')
    click.secho("Drift config DB ", nl=False)
    click.secho(domain['domain_name'], bold=True, nl=False)
    click.secho(" at origin ", nl=False)
    click.secho(domain['origin'], bold=True)


def _epilogue(ts):
    name = ts.get_table('domain')['domain_name']
    click.secho("Run \"driftconfig diff {} -d\" to see changes. Run \"driftconfig push {}\" to commit them.".format(name, name))


class Globals(object):
    pass


pass_repo = click.make_pass_decorator(Globals)


@click.group()
@click.option('--config-url', '-u', envvar='DRIFT_CONFIG_URL', metavar='',
    help="Url to DB origin.")
@click.option('--verbose', '-v', is_flag=True,
    help='Enables verbose mode.')
@click.option('--organization', '-o', is_flag=True,
    help='Specify organization name/short name.')
@click.option('--product', '-p', is_flag=True,
    help='Specify product name.')
@click.version_option('1.0')
@click.pass_context
def cli(ctx, config_url, verbose, organization, product):
    """This command line tool helps you manage and maintain Drift
    Configuration databases.
    """
    ctx.obj = Globals()
    ctx.obj.config_url = config_url
    if config_url:
        os.environ['DRIFT_CONFIG_URL'] = config_url
    ctx.obj.verbose = verbose
    ctx.obj.organization = organization
    ctx.obj.product = product


@cli.command()
def info():
    """List out all Drift configuration DB's that are active on this machine.
    """
    domains = get_domains()
    if not domains:
        click.secho("No Drift configuration found on this machine. Run 'init' or 'create' "
            "command to remedy.")
    else:
        ts, source = get_default_drift_config_and_source()
        got_default = False

        for domain_info in domains.values():
            domain = domain_info['table_store'].get_table('domain')
            is_default = domain['domain_name'] == ts.get_table('domain')['domain_name']
            if is_default:
                click.secho(domain['domain_name'] + " [DEFAULT]:", bold=True, nl=False)
                got_default = True
            else:
                click.secho(domain['domain_name'] + ":", bold=True, nl=False)

            click.secho(" \"{}\"".format(domain['display_name']), fg='green')
            click.secho("\tOrigin: " + domain['origin'])
            click.secho("\tLocal: " + domain_info['path'])
            click.secho("")

        if got_default:
            if 'DRIFT_CONFIG_URL' in os.environ:
                click.secho("The default config is specified using the 'DRIFT_CONFIG_URL' environment variable.")
            else:
                click.secho("The config above is the default one as it's the only one cached locally in ~/.drift/config.")
        else:
            click.secho("Note: There is no default config specified!")


@cli.command()
@click.argument('table-name')
def edit(table_name):
    """Edit a config table.\n
    TABLE_NAME is one of: domain, organizations, tiers, deployable-names, deployables,
    products, tenant-names, tenants.
    """
    ts, source = get_default_drift_config_and_source()
    table = ts.get_table(table_name)
    backend = create_backend(source)
    path = backend.get_filename(table.get_filename())
    with open(path, 'r') as f:
        text = click.edit(f.read(), editor='nano')
    if text:
        click.secho("Writing changes to " + path)
        with open(path, 'w') as f:
            f.write(text)

        _epilogue(ts)


@cli.group()
@pass_repo
def tier(repo):
    """Manage tier related entries in the configuration database."""


@tier.command()
@click.option('--tier-name', '-t', type=str, default=None)
def info(tier_name):
    """Show tier info."""
    conf = get_default_drift_config()
    _header(conf)
    if tier_name is None:
        click.echo("Tiers:")
        tabulate(['tier_name', 'state', 'is_live'], conf.get_table('tiers').find(), indent='  ')
    else:
        tier = conf.get_table('tiers').find({'tier_name': tier_name})
        if not tier:
            click.secho("No tier named {} found.".format(tier_name), fg='red', bold=True)
            sys.exit(1)
        tier = tier[0]
        click.echo("Tier {}:".format(tier['tier_name']))
        click.echo(pretty(tier))


@tier.command()
@click.argument('tier-name', type=str)
@click.option('--is-live/--is-dev', help="Flag tier for 'live' or 'development' purposes. Default is 'live'.")
@click.option('--edit', '-e', help="Use editor to modify the entry.", is_flag=True)
def add(tier_name, is_live, edit):
    """Add a new tier.\n
    TIER_NAME is a 3-20 character long upper case string containing only the letters A-Z."""
    with TSLocal() as ts:
        tiers = ts.get_table('tiers')
        entry = {'tier_name': tier_name, 'is_live': is_live}
        if edit:
            edit = click.edit(json.dumps(entry, indent=4), editor='nano')
            if edit:
                entry = json.loads(edit)
        if tiers.find(entry):
            click.secho("Tier {} already exists!".format(entry['tier_name']), fg='red', bold=True)
            sys.exit(1)
        tiers.add(entry)

        _epilogue(ts)


@tier.command()
@click.argument('tier-name', type=str)
def edit(tier_name):
    """Edit a tier."""
    with TSLocal() as ts:
        tiers = ts.get_table('tiers')
        entry = tiers.get({'tier_name': tier_name})
        if not entry:
            click.secho("tier {} not found!".format(tier_name))
            sys.exit(1)

        edit = click.edit(json.dumps(entry, indent=4), editor='nano')
        if edit:
            entry = json.loads(edit)
            tiers.update(entry)


@cli.group()
@pass_repo
def deployable(repo):
    """Manage registration of deployables in the configuration database."""


@deployable.command()
def info():
    """Show deployable registration info."""
    click.secho("Registered Drift deployable plugins:")

    # setuptools distribution object:
    # http://setuptools.readthedocs.io/en/latest/pkg_resources.html#distribution-objects
    # 'activate', 'as_requirement', 'check_version_conflict', 'clone', 'egg_name', 'extras',
    # 'from_filename', 'from_location', 'get_entry_info', 'get_entry_map', 'has_version',
    # 'hashcmp', 'insert_on', 'key', 'load_entry_point', 'location', 'parsed_version',
    # 'platform', 'precedence', 'project_name', 'py_version', 'requires', 'version'

    # setuptools entry point object:
    # http://setuptools.readthedocs.io/en/latest/pkg_resources.html#entrypoint-objects
    # 'attrs', 'dist', 'extras', 'load', 'module_name', 'name', 'parse', 'parse_group',
    # 'parse_map', 'pattern', 'require', 'resolve'

    ts = get_default_drift_config()
    click.echo("List of Drift deployable plugins in ", nl=False)
    _header(ts)
    deployables = ts.get_table('deployable-names')

    click.secho("Deployables and api routes registered in config:\n", bold=True)

    def join_tables(master_table, *tables, **search_criteria):
        """
        Joins rows from 'tables' to the rows of 'master_table' and returns them
        as a single sequence.
        'search_criteria' is applied to the 'master_table'.
        """
        rows = master_table.find(search_criteria)
        for row in rows:
            row = row.copy()
            for table in tables:
                other = table.get(row)
                if other:
                    row.update(other)
            yield row


    tabulate(
        ['deployable_name', 'api', 'requires_api_key', 'display_name', 'tags'],
        list(join_tables(deployables, ts.get_table('routing'), ts.get_table('deployable-names'))),
        indent='  ',
    )
    registered = [d['deployable_name'] for d in deployables.find()]

    click.secho("\nDeployables registered as plugins on this machine:\n", bold=True)
    for d in _enumerate_plugins('drift.plugin', 'register_deployable'):
        dist, meta, classifiers, tags = d['dist'], d['meta'], d['classifiers'], d['tags']
        click.secho(dist.key, bold=True, nl=False)
        entry = deployables.get({'deployable_name': dist.key})
        if entry:
            click.secho("")
        else:
            click.secho(" (Plugin NOT registered in config DB!)", fg='red')

        if dist.key in registered:
            registered.remove(dist.key)

        assigned = ts.get_table('deployables').find({'deployable_name': dist.key})
        if assigned:
            click.secho("\tTier assignment:")
            for assignment in assigned:
                if 'version' in assignment:
                    click.secho("\t\t{tier_name} [{version}]".format(**assignment), nl=False)
                else:
                    click.secho("\t\t{tier_name}".format(**assignment), nl=False)
                if assignment['is_active']:
                    click.secho("")
                else:
                    click.secho(" [inactive]", fg='white')

        click.secho("\tTags: {}".format(', '.join(tags)))
        click.secho("\tVersion: {}".format(dist.parsed_version))

        if meta:
            for key in ['Author', 'Summary']:
                if key in meta:
                    click.secho("\t{}:{}".format(key, meta[key]))
            for classifier in classifiers:
                if 'Programming Language' in classifier and classifier.count('::') == 1:
                    click.secho("\t{}".format(classifier))
        else:
            click.secho("\t(meta info missing)")
        click.secho("")

    if registered:
        click.secho("Note! The following deployables are registered in the config, but are not "
            "registered as plugins on this machine:\n{}".format(', '.join(registered)))

    click.secho("\nDeployables assigned to tiers:\n", bold=True)
    ta = {}
    for d in ts.get_table('deployables').find():
        ta.setdefault(d['tier_name'], []).append(d)
    for tier_name, dep in ta.items():
        click.secho("{}:".format(tier_name), bold=True)
        for d in dep:
            click.secho(d['deployable_name'], fg='black' if d['is_active'] else 'red', nl=False)
            click.secho(" ", nl=False)
        click.secho("\n")


@deployable.command()
@click.argument('deployable-name', type=str)
@click.option('--tier', '-t', type=str, multiple=True,
    help="Associate deployable to a tier. You can repeat this option for multiple tiers. "
    "Specify 'all' to associate with all available tiers.")
def register(deployable_name, tier):
    """Add or update the registration of a deployable plugin.\n
    DEPLOYABLE_NAME is the name of the plugin to register. Specify 'all' to register all plugins."""
    tiers = tier  # The 'tier' option is a list, so find a better name for it.
    with TSLocal() as ts:
        deployable_names = ts.get_table('deployable-names')
        deployables = ts.get_table('deployables')

        for d in _enumerate_plugins('drift.plugin', 'provision'):
            dist = d['dist']
            if deployable_name == dist.key or deployable_name == 'all':
                summary = d['meta'].get('Summary', "(No description available)")
                row = {'deployable_name': dist.key, 'display_name': summary}
                deployable_names.update(row)
                click.secho("{} added/updated.".format(dist.key))
                for tier_entry in ts.get_table('tiers').find():
                    if tier_entry['tier_name'] in tiers or 'all' in tiers:
                        row = {
                            'tier_name': tier_entry['tier_name'],
                            'deployable_name': dist.key,
                            'is_active': True,
                            'tags': d['tags'],
                        }
                        # By default, live tiers use version affinity
                        if tier_entry['is_live']:
                            row['version'] = str(dist.parsed_version)
                        deployables.update(row)
                        click.secho("Adding registration:\n" + json.dumps(row, indent=4))

        _epilogue(ts)


@cli.group()
@pass_repo
def organization(repo):
    """Manage organizations in the configuration database."""


@organization.command()
@click.option('--name', '-n', 'organization_name', type=str,
    help="Show full info for given organization. Specify name or short name.")
def info(organization_name):
    """Show organization info."""
    conf = get_default_drift_config()
    _header(conf)

    if organization_name is None:
        tabulate(
            ['organization_name', 'short_name', 'state', 'display_name'],
            conf.get_table('organizations').find(),
            indent='  ',
        )
    else:
        org = conf.get_table('organizations').find({'organization_name': organization_name})
        if not org:
            org = conf.get_table('organizations').find({'short_name': organization_name})
        if not org:
            click.secho("No organization named {} found.".format(organization_name), fg='red', bold=True)
            sys.exit(1)
        org = org[0]
        click.echo("Organization {}:".format(org['organization_name']))
        click.echo(json.dumps(org, indent=4))


@organization.command()
@click.argument('organization-name', type=str)
@click.argument('short-name', type=str)
@click.option('--display-name', '-d', help="Display name.", type=str)
@click.option('--edit', '-e', help="Use editor to modify the entry.", is_flag=True)
def add(organization_name, short_name, display_name, edit):
    """Add a new organization.\n
    ORGANIZATION_NAME is a 2-20 character long string containing only lower case letters and digits.\n
    SHORT_NAME is a 2-20 character long string containing only lower case letters and digits."""
    with TSLocal() as ts:
        organizations = ts.get_table('organizations')
        entry = {
            'organization_name': organization_name,
            'short_name': short_name,
        }
        if display_name:
            entry['display_name'] = display_name

        if edit:
            edit = click.edit(json.dumps(entry, indent=4), editor='nano')
            if edit:
                entry = json.loads(edit)
        if organizations.find(entry):
            click.secho("Organization {} already exists!".format(entry['organization_name']), fg='red', bold=True)
            sys.exit(1)
        organizations.add(entry)

        _epilogue(ts)


@organization.command()
@click.argument('organization-name', type=str)
def edit(organization_name):
    """Edit a organization."""
    with TSLocal() as ts:
        organizations = ts.get_table('organizations')
        entry = organizations.get({'organization_name': organization_name})
        if not entry:
            click.secho("organization {} not found!".format(organization_name))
            sys.exit(1)

        edit = click.edit(json.dumps(entry, indent=4), editor='nano')
        if edit:
            entry = json.loads(edit)
            organizations.update(entry)


@cli.group()
@pass_repo
def product(repo):
    """Manage products in the configuration database."""


@product.command()
@click.option('-name', '-n', 'product_name', type=str, help="Show full info for given product.")
def info(product_name):
    """Show product info."""
    conf = get_default_drift_config()
    _header(conf)

    if product_name is None:
        tabulate(
            ['organization_name', 'product_name', 'state', 'deployables'],
            conf.get_table('products').find(),
            indent='  ',
        )
    else:
        product = conf.get_table('products').find({'product_name': product_name})
        if not product:
            click.secho("No product named {} found.".format(product_name), fg='red', bold=True)
            sys.exit(1)
        product = product[0]
        click.secho("Product {s.BRIGHT}{}{s.NORMAL}:".format(product['product_name'], **styles))
        click.echo(json.dumps(product, indent=4))


@product.command()
@click.argument('product-name', type=str)
@click.option('--edit', '-e', help="Use editor to modify the entry.", is_flag=True)
def add(product_name, edit):
    """Add a new product.\n
    PRODUCT_NAME is a 3-35 character long string containing only lower case letters digits and dashes.
    The product name must be prefixed with the organization short name and a dash.
    """
    if '-' not in product_name:
        click.secho("Error: The product name must be prefixed with the organization "
            "short name and a dash.", fg='red', bold=True)
        sys.exit(1)

    short_name = product_name.split('-', 1)[0]
    conf = get_default_drift_config()
    org = conf.get_table('organizations').find({'short_name': short_name})
    if not org:
        click.secho("No organization with short name {} found.".format(short_name), fg='red', bold=True)
        sys.exit(1)

    organization_name = org[0]['organization_name']

    with TSLocal() as ts:
        products = ts.get_table('products')
        entry = {
            'organization_name': organization_name,
            'product_name': product_name
        }

        if edit:
            edit = click.edit(json.dumps(entry, indent=4), editor='nano')
            if edit:
                entry = json.loads(edit)
        if products.find(entry):
            click.secho("Product {} already exists!".format(entry['product_name']), fg='red', bold=True)
            sys.exit(1)
        products.add(entry)

        _epilogue(ts)


@product.command()
@click.argument('product-name', type=str)
def edit(product_name):
    """Edit a product."""
    with TSLocal() as ts:
        products = ts.get_table('products')
        entry = products.get({'product_name': product_name})
        if not entry:
            click.secho("product {} not found!".format(product_name))
            sys.exit(1)

        edit = click.edit(json.dumps(entry, indent=4), editor='nano')
        if edit:
            entry = json.loads(edit)
            products.update(entry)


@cli.group()
@pass_repo
def tenant(repo):
    """Manage tenants in the configuration database."""


@tenant.command()
@click.option('-name', '-n', 'tenant_name', type=str, help="Show full info for given tenant.")
def info(tenant_name):
    """Show tenant info."""
    conf = get_default_drift_config()
    _header(conf)

    if tenant_name is None:
        tabulate(
            ['organization_name', 'product_name', 'tenant_name', 'reserved_at', 'reserved_by'],
            conf.get_table('tenant-names').find(),
            indent='  ',
        )
    else:
        tenant = conf.get_table('tenants').find({'tenant_name': tenant_name})
        if not tenant:
            click.secho("No tenant named {} found.".format(tenant_name), fg='red', bold=True)
            sys.exit(1)

        click.secho("Tenant {s.BRIGHT}{}{s.NORMAL}:".format(tenant_name, **styles))
        click.echo(json.dumps(tenant, indent=4))


@tenant.command()
@click.argument('tenant-name', type=str)
@click.argument('product-name', type=str)
@click.option('--edit', '-e', help="Use editor to modify the entry.", is_flag=True)
def add(tenant_name, product_name, edit):
    """Add a new tenant.\n
    TENANT_NAME is a 3-30 character long string containing only lower case letters digits and dashes.
    The tenant name must be prefixed with the organization short name and a dash.
    PRODUCT_NAME is the product which the tenant is associated with.
    """
    if edit:
        click.secho("Editing tier and deployable details not implemented yet. Don't use "
            "the --edit option!", fg='red')
        sys.exit(1)

    if '-' not in tenant_name:
        click.secho("Error: The tenant name must be prefixed with the organization "
            "short name and a dash.", fg='red', bold=True)
        sys.exit(1)

    short_name = tenant_name.split('-', 1)[0]
    conf = get_default_drift_config()
    org = conf.get_table('organizations').find({'short_name': short_name})
    if not org:
        click.secho("No organization with short name {} found.".format(short_name), fg='red', bold=True)
        sys.exit(1)

    organization_name = org[0]['organization_name']
    product = conf.get_table('products').find({'product_name': product_name})
    if not product:
        click.secho("No product named {} found.".format(product_name), fg='red', bold=True)
        sys.exit(1)
    product = product[0]


    with TSLocal() as ts:
        click.secho("Creating tenant {} for product {}.".format(tenant_name, product_name))
        tenant_names = ts.get_table('tenant-names')
        entry = {
            'tenant_name': tenant_name,
            'organization_name': organization_name,
            'product_name': product_name,
            'reserved_by': getpass.getuser(),
            'reserved_at': datetime.utcnow().isoformat() + 'Z',
        }

        if edit:
            edit = click.edit(json.dumps(entry, indent=4), editor='nano')
            if edit:
                entry = json.loads(edit)
        if tenant_names.find(entry):
            click.secho("Tenant {} already exists!".format(tenant_name), fg='red', bold=True)
            sys.exit(1)
        tenant_names.add(entry)

        _epilogue(ts)


@tenant.command()
@click.argument('tenant-name', type=str)
@click.option('--details', '-d', help="Edit the tenant and deployable details.", is_flag=True)
def edit(tenant_name, details):
    """Edit a tenant."""
    if details:
        click.secho("Editing tenant and deployable details not implemented yet!", fg='red')
        sys.exit(1)

    with TSLocal() as ts:
        tenants = ts.get_table('tenant-names')
        entry = tenants.get({'tenant_name': tenant_name})
        if not entry:
            click.secho("tenant {} not found!".format(tenant_name))
            sys.exit(1)

        edit = click.edit(json.dumps(entry, indent=4), editor='nano')
        if edit:
            entry = json.loads(edit)
            tenants.update(entry)


def _enumerate_plugins(entry_group, entry_name):
    """
    Return a list of Python plugins with entry map group and entry point
    name matching 'entry_group' and 'entry_name'.
    """
    ws = pkg_resources.WorkingSet()
    distributions, errors = ws.find_plugins(pkg_resources.Environment())
    for dist in distributions:
        entry_map = dist.get_entry_map()
        entry = entry_map.get(entry_group, {}).get(entry_name)
        if entry:
            meta = {}
            classifiers = []
            tags = []
            if dist.has_metadata('PKG-INFO'):
                for line in dist.get_metadata_lines('PKG-INFO'):
                    key, value = line.split(':', 1)
                    if key == 'Classifier':
                        v = value.strip()
                        classifiers.append(v)
                        if 'Drift :: Tag :: ' in v:
                            tags.append(v.replace('Drift :: Tag :: ', '').lower().strip())
                    else:
                        meta[key] = value

            yield {
                'dist': dist,
                'entry': entry,
                'meta': meta,
                'classifiers': classifiers,
                'tags': tags,
            }

def tabulate(headers, rows, indent=None, col_padding=None):
    """Pretty print tabular data."""
    indent = indent or ''
    col_padding = col_padding or 3

    # Calculate max width for each column
    col_size = [[len(h) for h in headers]]  # Width of header cols
    col_size += [[len(str(row.get(h, ''))) for h in headers] for row in rows]  # Width of col in each row
    col_size = [max(col) for col in zip(*col_size)]  # Find the largest

    # Sort rows
    def make_key(row):
        return ":".join([str(row.get(k, '')) for k in headers])

    rows = sorted(rows, key=make_key)

    for row in [headers] + rows:
        click.echo(indent, nl=False)
        for h, width in zip(headers, col_size):
            if row == headers:
                h = h.replace('_', ' ').title()  # Make header name pretty
                click.secho(h.ljust(width + col_padding), bold=True, nl=False)
            else:
                fg = 'black' if row.get('active', True) else 'white'
                click.secho(str(row.get(h, '')).ljust(width + col_padding), nl=False, fg=fg)
        click.echo()


PRETTY_FORMATTER = 'console256'
PRETTY_STYLE = 'tango'


def pretty(ob, lexer=None):
    """
    Return a pretty console text representation of 'ob'.
    If 'ob' is something else than plain text, specify it in 'lexer'.

    If 'ob' is not string, Json lexer is assumed.

    Command line switches can be used to control highlighting and style.
    """
    if lexer is None:
        if isinstance(ob, basestring):
            lexer = 'text'
        else:
            lexer = 'json'

    if lexer == 'json':
        ob = json.dumps(ob, indent=4, sort_keys=True)

    if got_pygments:
        lexerob = get_lexer_by_name(lexer)
        formatter = get_formatter_by_name(PRETTY_FORMATTER, style=PRETTY_STYLE)
        #from pygments.filters import *
        #lexerob.add_filter(VisibleWhitespaceFilter())
        ret = highlight(ob, lexerob, formatter)
    else:
        ret = ob

    return ret.rstrip()