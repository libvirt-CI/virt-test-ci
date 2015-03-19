import os
import re
import sys
import time
import urllib
import urllib2
import json
import optparse
import fileinput
import traceback

from virttest import data_dir
from virttest import virsh
from virttest import utils_libvirtd
from virttest.staging import service
from autotest.client.shared import error
from autotest.client import utils

from report import Report
from state import States

reasons = {
    "BUG 886456": {
        "case": "virsh.change_media.floppy_test.positive_test.insert.options.live_floppy_rw.running_guest",
        "result": "mount: .* is not a valid block device",
    }
}


class LibvirtCI():

    def parse_args(self):
        parser = optparse.OptionParser(
            description='Continuouse integration of '
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
        self.args, self.real_args = parser.parse_args()

    def prepare_tests(self, whitelist='whitelist.test',
                      blacklist='blacklist.test'):
        """
        Get all tests to be run.

        When a whitelist is given, only tests in whitelist will be run.
        When a blacklist is given, tests in blacklist will be excluded.
        """
        def read_tests_from_file(file_name):
            """
            Read tests from a file
            """
            try:
                tests = []
                with open(file_name) as fp:
                    for line in fp:
                        if not line.strip().startswith('#'):
                            tests.append(line.strip())
                return tests
            except IOError:
                return None

        def get_all_tests():
            """
            Get all libvirt tests.
            """
            if type(self.onlys) == set and not self.onlys:
                return []

            cmd = './run -t libvirt --list-tests'
            if self.args.connect_uri:
                cmd += ' --connect-uri %s' % self.args.connect_uri
            if self.nos:
                cmd += ' --no %s' % ','.join(self.nos)
            if self.onlys:
                cmd += ' --tests %s' % ','.join(self.onlys)
            if self.args.config:
                cmd += ' -c %s' % self.args.config
            res = utils.run(cmd)
            out, err, exitcode = res.stdout, res.stderr, res.exit_status
            tests = []
            class_names = set()
            for line in out.splitlines():
                if line:
                    if line[0].isdigit():
                        test = re.sub(r'^[0-9]+ (.*) \(requires root\)$',
                                      r'\1', line)
                        if self.args.smoke:
                            class_name, _ = self.split_name(test)
                            if class_name in class_names:
                                continue
                            else:
                                class_names.add(class_name)
                        tests.append(test)
            return tests

        def change_to_only(change_list):
            """
            Transform the content of a change file to a only set.
            """
            onlys = set()
            for line in change_list:
                filename = line.strip()
                res = re.match('libvirt/tests/(cfg|src)/(.*).(cfg|py)',
                               filename)
                if res:
                    cfg_path = 'libvirt/tests/cfg/%s.cfg' % res.groups()[1]
                    tp_dir = data_dir.get_test_provider_dir(
                        'io-github-autotest-libvirt')
                    cfg_path = os.path.join(tp_dir, cfg_path)
                    try:
                        with open(cfg_path) as fcfg:
                            only = fcfg.readline().strip()
                            only = only.lstrip('-').rstrip(':').strip()
                            onlys.add(only)
                    except:
                        pass
            return onlys

        self.nos = set(['io-github-autotest-qemu'])
        self.onlys = None

        if self.args.only:
            self.onlys = set(self.args.only.split(','))

        if self.args.slice:
            slices = {}
            slice_opts = self.args.slice.split(',')
            slice_url = slice_opts[0]
            slice_opts = slice_opts[1:]
            config = urllib2.urlopen(slice_url)
            for line in config:
                key, val = line.split()
                slices[key] = val
            for slice_opt in slice_opts:
                if slice_opt in slices:
                    if self.onlys is None:
                        self.onlys = set(slices[slice_opt].split(','))
                    else:
                        self.onlys |= set(slices[slice_opt].split(','))
                elif slice_opt == 'other':
                    for key in slices:
                        self.nos |= set(slices[key].split(','))

        if self.args.no:
            self.nos |= set(self.args.no.split(','))
        if self.args.only_change:
            if self.onlys is not None:
                self.onlys &= change_to_only(self.libvirt_file_changed)
            else:
                self.onlys = change_to_only(self.libvirt_file_changed)

        if self.args.whitelist:
            tests = read_tests_from_file(whitelist)
        else:
            tests = get_all_tests()

        if self.args.blacklist:
            black_tests = read_tests_from_file(blacklist)
            tests = [t for t in tests if t not in black_tests]

        with open('run.test', 'w') as fp:
            for test in tests:
                fp.write(test + '\n')
        return tests

    def split_name(self, name):
        """
        Try to return the module name of a test.
        """
        if name.startswith('type_specific.io-github-autotest-libvirt'):
            name = name.split('.', 2)[2]

        if name.split('.')[0] in ['virsh']:
            package_name, name = name.split('.', 1)
        else:
            package_name = ""

        names = name.split('.', 1)
        if len(names) == 2:
            name, test_name = names
        else:
            name = names[0]
            test_name = name
        if package_name:
            class_name = '.'.join((package_name, name))
        else:
            class_name = name

        return class_name, test_name

    def bootstrap(self):
        from virttest import bootstrap

        test_dir = data_dir.get_backend_dir('libvirt')
        default_userspace_paths = ["/usr/bin/qemu-kvm", "/usr/bin/qemu-img"]
        base_dir = data_dir.get_data_dir()
        if os.path.exists(base_dir):
            if os.path.islink(base_dir) or os.path.isfile(base_dir):
                os.unlink(base_dir)
                os.mkdir(base_dir)
        bootstrap.bootstrap(test_name='libvirt', test_dir=test_dir,
                            base_dir=base_dir,
                            default_userspace_paths=default_userspace_paths,
                            check_modules=[],
                            online_docs_url=None,
                            interactive=False,
                            selinux=True,
                            restore_image=False,
                            verbose=True,
                            update_providers=False,
                            force_update=True)
        os.chdir(data_dir.get_root_dir())

    def prepare_env(self):
        """
        Prepare the environment before all tests.
        """

        def replace_pattern_in_file(file, search_exp, replace_exp):
            prog = re.compile(search_exp)
            for line in fileinput.input(file, inplace=1):
                match = prog.search(line)
                if match:
                    line = prog.sub(replace_exp, line)
                sys.stdout.write(line)

        utils_libvirtd.Libvirtd().restart()
        service.Factory.create_service("nfs").restart()

        if self.args.password:
            replace_pattern_in_file(
                "shared/cfg/guest-os/Linux.cfg",
                r'password = \S*',
                r'password = %s' % self.args.password)

        if self.args.os_variant:
            replace_pattern_in_file(
                "shared/cfg/guest-os/Linux/JeOS/19.x86_64.cfg",
                r'os_variant = \S*',
                r'os_variant = %s' % self.args.os_variant)

        if self.args.add_vms:
            vms_string = "virt-tests-vm1 " + " ".join(
                self.args.add_vms.split(','))
            replace_pattern_in_file(
                "shared/cfg/base.cfg",
                r'^\s*vms = .*\n',
                r'vms = %s\n' % vms_string)

        print 'Running bootstrap'
        sys.stdout.flush()
        self.bootstrap()

        restore_image = True
        if self.args.img_url:
            def progress_callback(count, block_size, total_size):
                pass
            print 'Downloading image from %s.' % self.args.img_url
            sys.stdout.flush()
            img_dir = os.path.join(
                os.path.realpath(data_dir.get_data_dir()), 'images/jeos-19-64.qcow2')
            urllib.urlretrieve(self.args.img_url, img_dir, progress_callback)
            restore_image = False

        if self.args.retain_vm:
            return

        print 'Removing VM\n',  # TODO: use virt-test api remove VM
        sys.stdout.flush()
        if self.args.connect_uri:
            virsh.destroy('virt-tests-vm1',
                          ignore_status=True,
                          uri=self.args.connect_uri)
            virsh.undefine('virt-tests-vm1',
                           '--snapshots-metadata --managed-save',
                           ignore_status=True,
                           uri=self.args.connect_uri)
        else:
            virsh.destroy('virt-tests-vm1', ignore_status=True)
            virsh.undefine('virt-tests-vm1', '--snapshots-metadata', ignore_status=True)
        if self.args.add_vms:
            for vm in self.args.add_vms.split(','):
                virsh.destroy(vm, ignore_status=True)
                virsh.undefine(vm, '--snapshots-metadata', ignore_status=True)

        print 'Installing VM',
        sys.stdout.flush()
        if 'lxc' in self.args.connect_uri:
            cmd = 'virt-install --connect=lxc:/// --name virt-tests-vm1 --ram 500 --noautoconsole'
            try:
                utils.run(cmd)
            except error.CmdError, e:
                raise Exception('   ERROR: Failed to install guest \n %s' % e)
        else:
            status, res, err_msg, result_line = self.run_test(
                'unattended_install.import.import.default_install.aio_native',
                restore_image=restore_image)
            if 'PASS' not in status:
                raise Exception('   ERROR: Failed to install guest \n %s' %
                                res.stderr)
            virsh.destroy('virt-tests-vm1')
        if self.args.add_vms:
            for vm in self.args.add_vms.split(','):
                cmd = 'virt-clone '
                if self.args.connect_uri:
                    cmd += '--connect=%s ' % self.args.connect_uri
                cmd += '--original=virt-tests-vm1 '
                cmd += '--name=%s ' % vm
                cmd += '--auto-clone'
                utils.run(cmd)

    def run_test(self, test, restore_image=False):
        """
        Run a specific test.
        """
        img_str = '' if restore_image else 'k'
        down_str = '' if restore_image else '--no-downloads'
        cmd = './run -v%st libvirt --keep-image-between-tests %s --tests %s' % (
            img_str, down_str, test)
        if self.args.connect_uri:
            cmd += ' --connect-uri %s' % self.args.connect_uri
        status = 'INVALID'
        try:
            res = utils.run(cmd, timeout=1200, ignore_status=True)
            lines = res.stdout.splitlines()
            for line in lines:
                if line.startswith('(1/1)'):
                    status = line.split()[2]
        except error.CmdError, e:
            res = e.result_obj
            status = 'TIMEOUT'
            res.duration = 1200
        except Exception, e:
            print "Exception when parsing stdout.\n%s" % res
            raise e

        os.chdir(data_dir.get_root_dir())  # Check PWD

        err_msg = []

        print 'Result: %s %.2f s' % (status, res.duration)

        result_line = ''
        for line in res.stderr.splitlines():
            if re.search('(INFO |ERROR)\| (SKIP|ERROR|FAIL|PASS)', line):
                result_line = line
            if 'FAIL' in status or 'ERROR' in status:
                if 'ERROR' in line:
                    err_msg.append('  %s' % line[9:])

        if status == 'INVALID' or status == 'TIMEOUT':
            for line in res.stdout.splitlines():
                err_msg.append(line)
        sys.stdout.flush()
        return status, res, err_msg, result_line

    def prepare_repos(self):
        """
        Prepare repos for the tests.
        """
        def merge_pulls(repo_name, pull_nos):
            branch_name = ','.join(pull_nos)
            cmd = 'git checkout -b %s' % branch_name
            res = utils.run(cmd, ignore_status=True)
            if res.exit_status:
                print res
                raise Exception('Failed to create branch %s' % branch_name)

            for pull_no in pull_nos:
                if pr_open(repo_name, pull_no):
                    patch_url = ('https://github.com/autotest'
                                 '/%s/pull/%s.patch' % (repo_name, pull_no))
                    patch_file = "/tmp/%s.patch" % pull_no
                    urllib.urlretrieve(patch_url, patch_file)
                    with open(patch_file, 'r') as pf:
                        if not pf.read().strip():
                            print 'WARING: empty content for PR #%s' % pull_no
                    try:
                        print 'Patching %s PR #%s' % (repo_name, pull_no)
                        cmd = 'git am -3 %s' % patch_file
                        res = utils.run(cmd)
                    except error.CmdError, e:
                        print e
                        raise Exception('Failed applying patch %s.' % pull_no)
                    finally:
                        os.remove(patch_file)
            return branch_name

        def file_changed(repo_name):
            cmd = 'git diff master --name-only'
            res = utils.run(cmd, ignore_status=True)
            if res.exit_status:
                print res
                raise Exception("Failed to get diff info against master")

            return res.stdout.strip().splitlines()

        def search_dep(line):
            pattern1 = r'autotest/virt-test#([0-9]*)'
            pattern2 = (r'https?://github.com/autotest/virt-test/(?:pull|issues)/([0-9]*)')
            res = set()
            match = re.findall(pattern1, line)
            res |= set(match)
            match = re.findall(pattern2, line)
            res |= set(match)
            return res

        def pr_open(repo_name, pr_number):
            oauth = ('?client_id=b6578298435c3eaa1e3d&client_secret'
                     '=59a1c828c6002ed4e8a9205486cf3fa86467a609')
            issues_url = 'https://api.github.com/repos/autotest/%s/issues/' % repo_name
            issue_url = issues_url + pr_number + oauth
            issue_u = urllib2.urlopen(issue_url)
            issue = json.load(issue_u)
            return issue['state'] == 'open'

        def libvirt_pr_dep(pr_numbers):
            oauth = ('?client_id=b6578298435c3eaa1e3d&client_secret'
                     '=59a1c828c6002ed4e8a9205486cf3fa86467a609')
            dep = set()
            for pr_number in pr_numbers:
                # Find PR's first comment for dependencies.
                issues_url = 'https://api.github.com/repos/autotest/tp-libvirt/issues/'
                issue_url = issues_url + pr_number + oauth
                issue_u = urllib2.urlopen(issue_url)
                issue = json.load(issue_u)
                for line in issue['body'].splitlines():
                    dep |= search_dep(line)

                # Find PR's other comments for dependencies.
                comments_url = issues_url + '%s/comments' % pr_number + oauth
                comments_u = urllib2.urlopen(comments_url)
                comments = json.load(comments_u)
                for comment in comments:
                    for line in comment['body'].splitlines():
                        dep |= search_dep(line)

            # Remove closed dependences:
            pruned_dep = set()
            for pr_number in dep:
                if pr_open('virt-test', pr_number):
                    pruned_dep.add(pr_number)

            return pruned_dep

        self.virt_branch_name, self.libvirt_branch_name = None, None

        libvirt_pulls = set()
        virt_test_pulls = set()

        if self.args.libvirt_pull:
            libvirt_pulls = set(self.args.libvirt_pull.split(','))

        if self.args.with_dependence:
            virt_test_pulls = libvirt_pr_dep(libvirt_pulls)

        if self.args.virt_test_pull:
            virt_test_pulls |= set(self.args.virt_test_pull.split(','))

        if virt_test_pulls:
            os.chdir(data_dir.get_root_dir())
            self.virt_branch_name = merge_pulls("virt-test", virt_test_pulls)
            if self.args.only_change:
                self.virt_file_changed = file_changed("virt-test")

        if libvirt_pulls:
            os.chdir(data_dir.get_test_provider_dir(
                'io-github-autotest-libvirt'))
            self.libvirt_branch_name = merge_pulls("tp-libvirt", libvirt_pulls)
            if self.args.only_change:
                self.libvirt_file_changed = file_changed("tp-libvirt")

        os.chdir(data_dir.get_root_dir())

    def restore_repos(self):
        """
        Checkout master branch and remove test branch.
        """
        def restore_repo(branch_name):
            cmd = 'git checkout master'
            res = utils.run(cmd, ignore_status=True)
            if res.exit_status:
                print res
            cmd = 'git branch -D %s' % branch_name
            res = utils.run(cmd, ignore_status=True)
            if res.exit_status:
                print res

        if self.virt_branch_name:
            os.chdir(data_dir.get_root_dir())
            restore_repo(self.virt_branch_name)

        if self.libvirt_branch_name:
            os.chdir(data_dir.get_test_provider_dir(
                'io-github-autotest-libvirt'))
            restore_repo(self.libvirt_branch_name)
        os.chdir(data_dir.get_root_dir())


    def get_reason(self, result_line):
        for name, reason in reasons.items():
            if (re.search(reason['case'], result_line) and
                    re.search(reason['result'], result_line)):
                return name

    def run(self):
        """
        Run continuous integrate for virt-test test cases.
        """
        self.parse_args()
        self.prepare_repos()

        if self.args.pre_cmd:
            print 'Running command line "%s" before test.' % self.args.pre_cmd
            res = utils.run(self.args.pre_cmd, ignore_status=True)
            print 'Result:'
            for line in str(res).splitlines():
                print line
        tests = self.prepare_tests()

        if self.args.list:
            for test in tests:
                short_name = test.split('.', 2)[2]
                print short_name
            return

        report = Report(self.args.fail_diff)
        if not tests:
            report.update("", "no_test.no_test", "", "", "", 0)
            print "No test to run!"
            return

        self.prepare_env()
        self.states = States()
        self.states.backup()
        try:
            for idx, test in enumerate(tests):
                short_name = test.split('.', 2)[2]
                print '%s (%d/%d) %s ' % (time.strftime('%X'), idx + 1,
                                          len(tests), short_name),
                sys.stdout.flush()

                status, res, err_msg, result_line = self.run_test(test)

                if not self.args.no_check:
                    diff_msg = self.states.check(
                        recover=(not self.args.no_recover))
                    if diff_msg:
                        diff_msg = ['   DIFF|%s' % l for l in diff_msg]
                        err_msg = diff_msg + err_msg

                if err_msg:
                    for line in err_msg:
                        print line
                sys.stdout.flush()

                reason = self.get_reason(result_line)

                class_name, test_name = self.split_name(test)

                report.update(test_name, class_name, status, reason,
                              res.stderr, err_msg, res.duration)
                report.save(self.args.report, self.args.txt_report)
            if self.args.post_cmd:
                print 'Running command line "%s" after test.' % self.args.post_cmd
                res = utils.run(self.args.post_cmd, ignore_status=True)
                print 'Result:'
                for line in str(res).splitlines():
                    print line
        except Exception:
            traceback.print_exc()
        finally:
            if not self.args.no_restore_pull:
                self.restore_repos()
            report.save(self.args.report, self.args.txt_report)