from __future__ import unicode_literals
import sys
import os
import json
import collections

from pip import cmdoptions

from pip.basecommand import Command
try:
    from pip.log import logger
except ImportError:
    from pip import logger  # 6.0
from pip.index import PackageFinder
from pip.req import RequirementSet, InstallRequirement, parse_requirements
from pip.locations import build_prefix, src_prefix

from .dependency import trace_dependencies
from .version import __version__


def pretty_project_name(req):
    """Get project name in a pretty form:

    name-version

    """
    try:
        print(req.name, req.installed_version)
    except Exception:
        print("dep %s has a problem sire." % req.name)
        return req.name
    return '%s-%s' % (req.name, req.installed_version)


class DependencyChecker(Command):
    bundle = False
    name = 'dependency'

    def __init__(self, *args, **kw):
        super(DependencyChecker, self).__init__(*args, **kw)

        # fix prog name to be gluttony instead of pip dependancy
        self.parser.prog = 'gluttony'

        self.cmd_opts.add_option(cmdoptions.requirements.make())
        self.cmd_opts.add_option(cmdoptions.build_dir.make())
        self.cmd_opts.add_option(cmdoptions.download_cache.make())

        # cmdoptions.editable exist in pip's git
        self.parser.add_option(
            '-e', '--editable',
            dest='editables',
            action='append',
            default=[],
            metavar='VCS+REPOS_URL[@REV]#egg=PACKAGE',
            help='Install a package directly from a checkout. Source will be checked '
            'out into src/PACKAGE (lower-case) and installed in-place (using '
            'setup.py develop). You can run this on an existing directory/checkout (like '
            'pip install -e src/mycheckout). This option may be provided multiple times. '
            'Possible values for VCS are: svn, git, hg and bzr.')

        self.parser.add_option(
            '-d', '--download', '--download-dir', '--download-directory',
            dest='download_dir',
            metavar='DIR',
            default=None,
            help='Download packages into DIR instead of installing them')
        self.parser.add_option(
            '--src', '--source', '--source-dir', '--source-directory',
            dest='src_dir',
            metavar='DIR',
            default=None,
            help='Check out --editable packages into DIR (default %s)' % src_prefix)
        self.parser.add_option(
            '-U', '--upgrade',
            dest='upgrade',
            action='store_true',
            help='Upgrade all packages to the newest available version')
        self.parser.add_option(
            '-I', '--ignore-installed',
            dest='ignore_installed',
            action='store_true',
            help='Ignore the installed packages (reinstalling instead)')

        # options for output
        self.parser.add_option(
            '--dump',
            dest='dump',
            metavar='FILE',
            help='dump dependancy by level')
        self.parser.add_option(
            '-j', '--json',
            dest='json_file',
            metavar='FILE',
            help='JSON filename for result output')
        self.parser.add_option(
            '--pydot',
            dest='py_dot',
            metavar='FILE',
            help='Output dot file with pydot')
        self.parser.add_option(
            '--pygraphviz',
            dest='py_graphviz',
            metavar='FILE',
            help='Output dot file with PyGraphviz')
        self.parser.add_option(
            '--display', '--display-graph',
            dest='display_graph',
            action='store_true',
            help='Display graph with Networkx and matplotlib')
        self.parser.add_option(
            '-R', '--reverse',
            dest='reverse',
            action='store_true',
            help='Reverse the direction of edge')

        index_opts = cmdoptions.make_option_group(
            cmdoptions.index_group,
            self.parser,
        )

        self.parser.insert_option_group(0, index_opts)

    def _build_package_finder(self, options, index_urls, session):
        """
        Create a package finder appropriate to this install command.
        This method is meant to be overridden by subclasses, not
        called directly.
        """
        return PackageFinder(
            use_wheel=False,
            find_links=options.find_links,
            index_urls=index_urls,
            allow_external=options.allow_external,
            allow_unverified=options.allow_unverified,
            allow_all_external=options.allow_all_external,
            session=session,
        )

    def run(self, options, args):
        if not options.build_dir:
            options.build_dir = build_prefix
        if not options.src_dir:
            options.src_dir = src_prefix
        if options.download_dir:
            options.no_install = True
            options.ignore_installed = True
        else:
            options.build_dir = os.path.abspath(options.build_dir)
            options.src_dir = os.path.abspath(options.src_dir)
        session = self._build_session(options)
        index_urls = [options.index_url] + options.extra_index_urls
        if options.no_index:
            logger.notify('Ignoring indexes: %s' % ','.join(index_urls))
            index_urls = []
        finder = self._build_package_finder(options, index_urls, session)
        requirement_set = RequirementSet(
            build_dir=options.build_dir,
            src_dir=options.src_dir,
            download_dir=options.download_dir,
            download_cache=options.download_cache,
            upgrade=options.upgrade,
            ignore_installed=options.ignore_installed,
            ignore_dependencies=False,
            session=session,
        )

        for name in args:
            requirement_set.add_requirement(
                InstallRequirement.from_line(name, None))
        for name in options.editables:
            requirement_set.add_requirement(
                InstallRequirement.from_editable(name, default_vcs=options.default_vcs))
        for filename in options.requirements:
            for req in parse_requirements(filename, finder=finder, options=options):
                requirement_set.add_requirement(req)

        requirement_set.prepare_files(
            finder,
            force_root_egg_info=self.bundle,
            bundle=self.bundle,
        )

        return requirement_set

    def _output_json(self, json_file, dependencies):
        packages = set()
        json_deps = []
        for src, dest in dependencies:
            packages.add(src)
            packages.add(dest)
            json_deps.append([
                pretty_project_name(src),
                pretty_project_name(dest),
            ])

        json_packages = []
        for package in packages:
            json_packages.append(dict(
                name=package.name,
                installed_version=package.installed_version,
            ))

        with open(json_file, 'wt') as jfile:
            json.dump(dict(
                packages=json_packages,
                dependencies=json_deps,
            ), jfile, sort_keys=True, indent=4, separators=(',', ': '))

    def check_conflicts(self, dependencies):
        dependancies_flattened = collections.defaultdict(set)
        for dep1, dep2 in dependencies:
            try:
                if dep1.installed_version is not None:
                    dependancies_flattened[dep1.name].add(dep1.installed_version)
            except Exception:
                print("%s has an unknown version" % dep1.name)

            try:
                if dep2.installed_version is not None:
                    dependancies_flattened[dep2.name].add(dep2.installed_version)
            except Exception:
                print("%s has an unknown version" % dep2.name)

        for dependency_name, dependency_versions in dependancies_flattened.items():
            if dependency_versions and len(dependency_versions) > 1:
                print("Warning: This project requires %s in multiple versions:" % dependency_name, ",".join(dependency_versions))

    def output(self, options, args, dependencies):
        """Output result

        """

        if options.reverse:
            dependencies = map(lambda x: x[::-1], dependencies)

        if options.json_file:
            self._output_json(options.json_file, dependencies)
            logger.notify("Dependencies relationships result is in %s now",
                          options.json_file)

        self.check_conflicts(dependencies)

        if options.display_graph or options.py_dot or options.py_graphviz or options.dump:
            import networkx as nx

            # extract name and version
            def convert(pair):
                return (
                    pretty_project_name(pair[0]),
                    pretty_project_name(pair[1]),
                )
            plain_dependencies = map(convert, dependencies)
            dg = nx.DiGraph()
            dg.add_edges_from(plain_dependencies)

            if options.dump:
                dependancies_ordered = []
                for n, nbrs in dg.adjacency_iter():
                    for nbr, eattr in nbrs.items():
                        dependancies_ordered.append(nbr)

                dependancies_ordered = set(dependancies_ordered)
                with open(options.dump, mode='wt') as myfile:
                    myfile.write('\n'.join(dependancies_ordered))

            if options.py_dot:
                logger.notify("Writing dot to %s with Pydot ...",
                              options.py_dot)
                from networkx.drawing.nx_pydot import write_dot
                write_dot(dg, options.py_dot)
            if options.py_graphviz:
                logger.notify("Writing dot to %s with PyGraphviz ...",
                              options.py_graphviz)
                from networkx.drawing.nx_agraph import write_dot
                write_dot(dg, options.py_graphviz)
            if options.display_graph:
                import matplotlib.pyplot as plt
                logger.notify("Drawing graph ...")

                if not plain_dependencies:
                    logger.notify("There is no dependency to draw.")
                else:
                    pydot_graph = nx.drawing.to_pydot(dg)
                    pydot_graph.write_png("dependency.png")

    def main(self, args):
        options, args = self.parser.parse_args(args)
        if not args:
            self.parser.print_help()
            return

        level = 1  # Notify
        logger.level_for_integer(level)
        logger.consumers.extend([(level, sys.stdout)])
        # get all files
        requirement_set = self.run(options, args)
        # trace dependencies
        logger.notify("Tracing dependencies ...")
        dependencies = []
        values = None
        if hasattr(requirement_set.requirements, 'itervalues'):
            values = list(requirement_set.requirements.itervalues())
        elif hasattr(requirement_set.requirements, 'values'):
            values = list(requirement_set.requirements.values())
        for req in values:
            trace_dependencies(req, requirement_set, dependencies)
        # output the result
        logger.notify("Output result ...")
        self.output(options, args, dependencies)
        requirement_set.cleanup_files()


def main():
    command = DependencyChecker()
    command.main(sys.argv[1:])

if __name__ == '__main__':
    main()
