#!/usr/bin/env python

import os
import sys
import shutil
import logging
import optparse

if __name__ != '__main__':
    logging.warning("Can't import ci_loader as a module, exiting.")
    sys.exit(1)


def _parse_args():
    parser = optparse.OptionParser(
        description='Continuous integration of '
        'virt-test libvirt test provider.')
    parser.add_option('--list', dest='list', action='store_true',
                      help='List all the test names')
    parser.add_option('--no', dest='no', action='store', default='',
                      help='Exclude specified tests.')
    parser.add_option('--only', dest='only', action='store', default='',
                      help='Run only for specified tests.')
    parser.add_option('--no-check', dest='no_check', action='store_true',
                      help='Disable checking state changes '
                      'after each test.')
    parser.add_option('--no-recover', dest='no_recover',
                      action='store_true',
                      help='Disable recover state changes '
                      'after each test.')
    parser.add_option('--connect-uri', dest='connect_uri', action='store',
                      default='', help='Run tests using specified uri.')
    parser.add_option('--additional-vms', dest='add_vms', action='store',
                      default='', help='Additional VMs for testing')
    parser.add_option('--smoke', dest='smoke', action='store_true',
                      help='Run one test for each script.')
    parser.add_option('--slice', dest='slice', action='store',
                      default='', help='Specify a URL to slice tests.')
    parser.add_option('--report', dest='report', action='store',
                      default='xunit_result.xml',
                      help='Exclude specified tests.')
    parser.add_option('--text-report', dest='txt_report', action='store',
                      default='report.txt',
                      help='Exclude specified tests.')
    parser.add_option('--white', dest='whitelist', action='store',
                      default='', help='Whitelist file contains '
                      'specified test cases to run.')
    parser.add_option('--black', dest='blacklist', action='store',
                      default='', help='Blacklist file contains '
                      'specified test cases to be excluded.')
    parser.add_option('--config', dest='config', action='store',
                      default='', help='Specify a custom Cartesian cfg '
                      'file')
    parser.add_option('--img-url', dest='img_url', action='store',
                      default='', help='Specify a URL to a custom image '
                      'file')
    parser.add_option('--os-variant', dest='os_variant', action='store',
                      default='', help='Specify the --os-variant option '
                      'when doing virt-install.')
    parser.add_option('--password', dest='password', action='store',
                      default='', help='Specify a password for logging '
                      'into guest')
    parser.add_option('--pull-virt-test', dest='virt_test_pull',
                      action='store', default='',
                      help='Merge specified virt-test pull requests. '
                      'Multiple pull requests are separated by ",", '
                      'example: --pull-virt-test 175,183')
    parser.add_option('--pull-libvirt', dest='libvirt_pull',
                      action='store', default='',
                      help='Merge specified tp-libvirt pull requests. '
                      'Multiple pull requests are separated by ",", '
                      'example: --pull-libvirt 175,183')
    parser.add_option('--reason-url', dest='reason_url', action='store',
                      default='',
                      help='Specify a URL to a JSON reason file')
    parser.add_option('--with-dependence', dest='with_dependence',
                      action='store_true',
                      help='Merge virt-test pull requests depend on')
    parser.add_option('--no-restore-pull', dest='no_restore_pull',
                      action='store_true', help='Do not restore repo '
                      'to branch master after test.')
    parser.add_option('--only-change', dest='only_change',
                      action='store_true', help='Only test tp-libvirt '
                      'test cases related to changed files.')
    parser.add_option('--fail-diff', dest='fail_diff',
                      action='store_true', help='Report tests who do '
                      'not clean up environment as a failure')
    parser.add_option('--retain-vm', dest='retain_vm',
                      action='store_true', help='Do not reinstall VM '
                      'before tests')
    parser.add_option('--pre-cmd', dest='pre_cmd',
                      action='store', help='Run a command line after '
                      'fetch the source code and before running the test.')
    parser.add_option('--post-cmd', dest='post_cmd',
                      action='store', help='Run a command line after '
                      'running the test')
    parser.add_option('--test-path', dest='test_path', action='store',
                      default='', help='Path for the test directory')
    parser.add_option('--autotest-repo', dest='autotest_repo', action='store',
                      default='https://github.com/autotest/autotest.git '
                      'master', help='URL and branch for autotest repo')
    parser.add_option('--virt-test-repo', dest='virt_test_repo',
                      action='store',
                      default='https://github.com/autotest/virt-test.git '
                      'master', help='URL and branch for virt-test repo')
    parser.add_option('--tp-libvirt-repo', dest='tp_libvirt_repo',
                      action='store',
                      default='https://github.com/autotest/tp-libvirt.git '
                      'master', help='URL and branch for tp-libvirt repo')
    parser.add_option('--tp-qemu-repo', dest='tp_qemu_repo', action='store',
                      default='https://github.com/autotest/tp-qemu.git master',
                      help='URL and branch for tp-qemu repo')
    args, real_args = parser.parse_args()
    return args


def _retrieve_repos():
    for repo in REPOS:
        repo_env_name = (repo + '_repo').replace('-', '_')
        repo_url, branch = getattr(ARGS, repo_env_name).split()

        logging.warning("Retrieving %s from %s" % (repo, repo_url))

        os.system('git clone --quiet --depth 1 %s %s --branch %s' %
                  (repo_url, repo, branch))


REPOS = ['autotest', 'virt-test', 'tp-libvirt', 'tp-qemu']
ENVS = {
    k.lstrip('CI_').lower(): v
    for k, v in os.environ.items()
    if k.startswith('CI_')
}
ARGS = _parse_args()
for key, value in ENVS.items():
    if hasattr(ARGS, key) and not getattr(ARGS, key):
        setattr(ARGS, key, value)

if 'test_path' in ENVS:
    test_path = ENVS['test_path']
else:
    test_path = os.getcwd()
    logging.warning("Environment variable CI_TEST_PATH not set. "
                    "Test in current directory.")

if os.getcwd() == test_path:
    workspace = os.getenv("WORKSPACE")
    if not all([os.path.exists(repo) for repo in REPOS]):
        if not workspace or not all([
                os.path.exists(os.path.join(workspace, repo))
                for repo in REPOS]):
            _retrieve_repos()
        else:
            for repo in REPOS:
                shutil.copytree(os.path.join(workspace, repo), repo)
    os.system('cp -r virt-test/* ./')
    os.makedirs('test-providers.d/downloads/')
    shutil.move('tp-libvirt', 'test-providers.d/downloads')
    shutil.move('tp-qemu', 'test-providers.d/downloads')

    from ci import LibvirtCI
    logging.warning("Start running libvirt CI in %s" % test_path)
    LibvirtCI(args=ARGS).run()
else:
    workspace = os.getenv("WORKSPACE")
    if workspace:
        if workspace != os.getcwd():
            logging.warning('')
    else:
        os.environ['WORKSPACE'] = os.getcwd()

    if not all([os.path.exists(repo) for repo in REPOS]):
        _retrieve_repos()

    if os.path.exists(test_path):
        logging.warning("Path %s exists. Cleaning up...", test_path)
        shutil.rmtree(test_path)
    shutil.copytree('.', test_path)
    os.chdir(test_path)
    os.system(' '.join(['python'] + sys.argv))