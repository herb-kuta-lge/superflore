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

import glob
import os

from rosdistro.dependency_walker import DependencyWalker
from rosdistro.manifest_provider import get_release_tag
from rosdistro.rosdistro import RosPackage
from rosinstall_generator.distro import _generate_rosinstall
from rosinstall_generator.distro import get_package_names
from superflore.exceptions import NoPkgXml
from superflore.exceptions import UnresolvedDependency
from superflore.generators.bitbake.yocto_recipe import yoctoRecipe
from superflore.PackageMetadata import PackageMetadata
from superflore.utils import err
from superflore.utils import get_pkg_version
from superflore.utils import make_dir
from superflore.utils import ok
from superflore.utils import warn

org = "Open Source Robotics Foundation"
org_license = "BSD"


def regenerate_installer(
    overlay, pkg, distro, preserve_existing, tar_dir, md5_cache, sha256_cache
):
    make_dir("{0}/recipes-ros-{1}".format(overlay.repo.repo_dir, distro.name))
    version = get_pkg_version(distro, pkg)
    pkg_names = get_package_names(distro)[0]

    if pkg not in pkg_names:
        raise RuntimeError("Unknown package '%s'" % pkg)

    # check for an existing recipe
    existing = glob.glob(
        '{0}/recipes-ros-{1}/{2}/*.bb'.format(
            overlay.repo.repo_dir,
            distro.name,
            pkg
        )
    )
    # check for .inc files
    inc_files = None
    inc_files = glob.glob(
        '{0}/recipes-ros-{1}/{2}/*.inc'.format(
            overlay.repo.repo_dir,
            distro.name,
            pkg
        )
    )
    # check for patches
    patch_path = '/recipes-ros-{0}/{1}/files/'.format(distro.name, pkg)
    patch_path = overlay.repo.repo_dir + patch_path
    patches = None
    if os.path.exists(patch_path):
        patches = [
            f.replace(patch_path, '    ')
            for f in glob.glob('%s*.patch' % patch_path)
        ]
    if preserve_existing and existing:
        ok("recipe for package '%s' up to date, skpping..." % pkg)
        return None, []
    elif existing:
        overlay.repo.remove_file(existing[0])
    try:
        current = oe_installer(
            distro, pkg, tar_dir, md5_cache, sha256_cache, patches, inc_files
        )
        current.recipe.name = pkg.replace('_', '-')
    except Exception as e:
        err('Failed to generate installer for package {}!'.format(pkg))
        raise e
    try:
        recipe_text = current.recipe_text()
    except UnresolvedDependency:
        dep_err = 'Failed to resolve required dependencies for'
        err("{0} package {1}!".format(dep_err, pkg))
        unresolved = current.recipe.get_unresolved()
        for dep in unresolved:
            err(" unresolved: \"{}\"".format(dep))
        return None, current.recipe.get_unresolved()
    except NoPkgXml:
        err("Could not fetch pkg!")
        return None, []
    except KeyError as ke:
        err("Failed to parse data for package {}!".format(pkg))
        raise ke
    make_dir(
        "{0}/recipes-ros-{1}/{2}".format(
            overlay.repo.repo_dir,
            distro.name,
            pkg.replace('_', '-')
        )
    )
    success_msg = 'Successfully generated installer for package'
    ok('{0} \'{1}\'.'.format(success_msg, pkg))
    recipe_name = '{0}/recipes-ros-{1}/{2}/{2}_{3}.bb'.format(
        overlay.repo.repo_dir,
        distro.name,
        pkg.replace('_', '-'),
        version
    )
    try:
        with open('{0}'.format(recipe_name), "w") as recipe_file:
            recipe_file.write(recipe_text)
    except Exception as e:
        err("Failed to write recipe to disk!")
        raise e
    return current, []


def _gen_recipe_for_package(
    distro, pkg_name, pkg, repo, ros_pkg,
    pkg_rosinstall, tar_dir, md5_cache, sha256_cache, patches, incs
):
    pkg_dep_walker = DependencyWalker(distro)
    pkg_buildtool_deps = pkg_dep_walker.get_depends(pkg_name, "buildtool")
    pkg_build_deps = pkg_dep_walker.get_depends(pkg_name, "build")
    pkg_run_deps = pkg_dep_walker.get_depends(pkg_name, "run")
    src_uri = pkg_rosinstall[0]['tar']['uri']

    pkg_recipe = yoctoRecipe(
        pkg_name,
        distro,
        src_uri,
        tar_dir,
        md5_cache,
        sha256_cache,
        patches,
        incs
    )
    # add run dependencies
    for rdep in pkg_run_deps:
        pkg_recipe.add_depend(rdep)

    # add build dependencies
    for bdep in pkg_build_deps:
        pkg_recipe.add_depend(bdep)

    # add build tool dependencies
    for tdep in pkg_buildtool_deps:
        pkg_recipe.add_depend(tdep)

    # parse throught package xml
    try:
        pkg_xml = ros_pkg.get_package_xml(distro.name)
    except Exception:
        warn("fetch metadata for package {}".format(pkg_name))
        return pkg_recipe
    pkg_fields = PackageMetadata(pkg_xml)
    pkg_recipe.pkg_xml = pkg_xml
    pkg_recipe.license = pkg_fields.upstream_license
    pkg_recipe.description = pkg_fields.description
    pkg_recipe.homepage = pkg_fields.homepage
    return pkg_recipe


class oe_installer(object):
    def __init__(
        self, distro, pkg_name, tar_dir, md5_cache, sha256_cache, patches, inc
    ):
        pkg = distro.release_packages[pkg_name]
        repo = distro.repositories[pkg.repository_name].release_repository
        ros_pkg = RosPackage(pkg_name, repo)

        pkg_rosinstall = _generate_rosinstall(
            pkg_name, repo.url, get_release_tag(repo, pkg_name), True
        )

        self.recipe = _gen_recipe_for_package(
            distro, pkg_name, pkg, repo, ros_pkg, pkg_rosinstall,
            tar_dir, md5_cache, sha256_cache, patches, inc
        )

    def recipe_text(self):
        return self.recipe.get_recipe_text(org, org_license)
