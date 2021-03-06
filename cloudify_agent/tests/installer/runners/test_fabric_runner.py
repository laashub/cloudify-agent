#########
# Copyright (c) 2015 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.

from mock import Mock, patch
from testtools import TestCase

from cloudify_agent.installer import exceptions

# these imports may run on a windows box, in which case they may fail. (if
# the pywin32 extensions). The tests wont run anyway because of the decorator,
# so we can just avoid this import.
try:
    from cloudify_agent.installer.runners.fabric_runner import FabricRunner
    from cloudify_agent.installer.runners.fabric_runner import (
        FabricCommandExecutionException,
    )
except ImportError:
    FabricRunner = None
    FabricCommandExecutionException = None

from cloudify_agent.tests import BaseTest
from cloudify_agent.tests.api.pm import only_os


##############################################################################
# note that this file only tests validation and defaults of the fabric runner.
# it does not test the actual functionality because that requires starting
# a vm. functional tests are executed as local workflow tests in the system
# tests framework
##############################################################################

@only_os('posix')
class TestDefaults(BaseTest, TestCase):
    def test_default_port(self):
        runner = FabricRunner(
            validate_connection=False,
            user='user',
            host='host',
            password='password')
        self.assertTrue(runner.port, 22)


@only_os('posix')
class TestValidations(BaseTest, TestCase):
    def test_no_host(self):
        self.assertRaisesRegex(
            exceptions.AgentInstallerConfigurationError,
            'Missing host',
            FabricRunner,
            validate_connection=False,
            user='user',
            password='password')

    def test_no_user(self):
        self.assertRaisesRegex(
            exceptions.AgentInstallerConfigurationError,
            'Missing user',
            FabricRunner,
            validate_connection=False,
            host='host',
            password='password')

    def test_no_key_no_password(self):
        self.assertRaisesRegex(
            exceptions.AgentInstallerConfigurationError,
            'Must specify either key or password',
            FabricRunner,
            validate_connection=False,
            host='host',
            user='password')


@only_os('posix')
class TestAbortException(BaseTest, TestCase):
    """Test behavior on fabric abort."""

    def test_exception_message(self):
        """Exception message is the same one used by fabric."""
        expected_message = '<message>'

        runner = FabricRunner(
            validate_connection=False,
            user='user',
            host='host',
            password='password',
        )

        connection_path = (
            'cloudify_agent.installer.runners.fabric_runner.Connection'
        )
        with patch(connection_path) as conn_factory:
            conn_factory.return_value.run.return_value = Mock(
                return_code=1,
                stderr=expected_message
            )
            try:
                runner.run('a command')
            except FabricCommandExecutionException as e:
                self.assertEqual(e.error, expected_message)
            else:
                self.fail('FabricCommandExecutionException not raised')
