# Copyright 2017 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys

from rosinstall_generator.distro import get_distro
from superflore.CacheManager import CacheManager
from superflore.generate_installers import generate_installers
from superflore.generators.bitbake.gen_packages import regenerate_installer
from superflore.generators.bitbake.ros_meta import RosMeta
from superflore.parser import get_parser
from superflore.repo_instance import RepoInstance
from superflore.TempfileManager import TempfileManager
from superflore.utils import clean_up
from superflore.utils import err
from superflore.utils import file_pr
from superflore.utils import gen_delta_msg
from superflore.utils import gen_missing_deps_msg
from superflore.utils import info
from superflore.utils import load_pr
from superflore.utils import ok
from superflore.utils import save_pr
from superflore.utils import url_to_repo_org
from superflore.utils import warn

# Modify if a new distro is added
active_distros = ['indigo', 'kinetic', 'lunar']


def main():
    preserve_existing = True
    overlay = None
    parser = get_parser('Deploy ROS packages into Yocto Linux')
    parser.add_argument(
        '--tar-archive-dir',
        help='location to store archived packages',
        type=str
    )
    selected_targets = active_distros
    args = parser.parse_args(sys.argv[1:])
    pr_comment = args.pr_comment
    if args.all:
        warn('"All" mode detected... this may take a while!')
        preserve_existing = False
    elif args.ros_distro:
        warn('"{0}" distro detected...'.format(args.ros_distro))
        selected_targets = [args.ros_distro]
        preserve_existing = False
    elif args.dry_run and args.pr_only:
        parser.error('Invalid args! cannot dry-run and file PR')
    elif args.pr_only and not args.output_repository_path:
        parser.error('Invalid args! no repository specified')
    elif args.pr_only:
        try:
            prev_overlay = RepoInstance(args.output_repository_path, False)
            msg, title = load_pr()
            prev_overlay.pull_request(msg, title)
            clean_up()
            sys.exit(0)
        except Exception as e:
            err('Failed to file PR!')
            err('reason: {0}'.format(e))
            sys.exit(1)
    repo_org = 'allenh1'
    repo_name = 'meta-ros'
    if args.upstream_repo:
        repo_org, repo_name = url_to_repo_org(args.upstream_repo)
    # open cached tar file if it exists
    with TempfileManager(args.output_repository_path) as _repo:
        if not args.output_repository_path:
            # give our group write permissions to the temp dir
            os.chmod(_repo, 17407)
        # clone if args.output-repository_path is None
        overlay = RosMeta(
            _repo,
            not args.output_repository_path,
            org=repo_org,
            repo=repo_name
        )
        if not args.only:
            pr_comment = pr_comment or (
                'Superflore yocto generator began regeneration of all '
                'packages form ROS distribution(s) %s on Meta-ROS from '
                'commit %s.' % (
                    selected_targets,
                    overlay.repo.get_last_hash()
                )
            )
        else:
            pr_comment = pr_comment or (
                'Superflore yocto generator began regeneration of package(s)'
                ' %s from ROS distro %s from Meta-ROS from commit %s.' % (
                    args.only,
                    args.ros_distro,
                    overlay.repo.get_last_hash()
                )
            )
        # generate installers
        total_installers = dict()
        total_broken = set()
        total_changes = dict()
        if args.tar_archive_dir:
            sha256_filename = '%s/sha256_cache.pickle' % args.tar_archive_dir
            md5_filename = '%s/md5_cache.pickle' % args.tar_archive_dir
        else:
            sha256_filename = None
            md5_filename = None
        with TempfileManager(args.tar_archive_dir) as tar_dir,\
            CacheManager(sha256_filename) as sha256_cache,\
            CacheManager(md5_filename) as md5_cache:  # noqa
            if args.only:
                for pkg in args.only:
                    info("Regenerating package '%s'..." % pkg)
                    try:
                        regenerate_installer(
                            overlay,
                            pkg,
                            get_distro(args.ros_distro),
                            preserve_existing,
                            tar_dir,
                            md5_cache,
                            sha256_cache
                        )
                    except KeyError:
                        err("No package to satisfy key '%s'" % pkg)
                        sys.exit(1)
                # Commit changes and file pull request
                regen_dict = dict()
                regen_dict[args.ros_distro] = args.only
                overlay.commit_changes(args.ros_distro)
                if args.dry_run:
                    save_pr(overlay, args.only, pr_comment)
                    sys.exit(0)
                delta = "Regenerated: '%s'\n" % args.only
                file_pr(overlay, delta, '', pr_comment, distro=args.ros_distro)
                ok('Successfully synchronized repositories!')
                sys.exit(0)

            for distro in selected_targets:
                distro_installers, distro_broken, distro_changes =\
                    generate_installers(
                        distro,
                        overlay,
                        regenerate_installer,
                        preserve_existing,
                        tar_dir,
                        md5_cache,
                        sha256_cache
                    )
                for key in distro_broken.keys():
                    for pkg in distro_broken[key]:
                        total_broken.add(pkg)
                total_changes[distro] = distro_changes
                total_installers[distro] = distro_installers

        num_changes = 0
        for distro_name in total_changes:
            num_changes += len(total_changes[distro_name])

        if num_changes == 0:
            info('ROS distro is up to date.')
            info('Exiting...')
            sys.exit(0)

        # remove duplicates
        delta = gen_delta_msg(total_changes)
        missing_deps = gen_missing_deps_msg(total_broken)
        # Commit changes and file pull request
        overlay.commit_changes(args.ros_distro)
        if args.dry_run:
            info('Running in dry mode, not filing PR')
            save_pr(overlay, delta, missing_deps, pr_comment)
            sys.exit(0)
        file_pr(overlay, delta, missing_deps, pr_comment)
        ok('Successfully synchronized repositories!')
