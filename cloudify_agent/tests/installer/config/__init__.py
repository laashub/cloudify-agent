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

from mock import MagicMock, patch

from cloudify.context import BootstrapContext
from cloudify.mocks import MockCloudifyContext
from cloudify_agent.tests import agent_ssl_cert


def mock_context(agent_properties=None,
                 agent_runtime_properties=None,
                 agent_context=None,
                 **kwargs):

    agent_context = agent_context or {}
    agent_properties = agent_properties or {}
    agent_runtime_properties = agent_runtime_properties or {}

    context = {
        'node_id': 'test_node',
        'node_name': 'test_node',
        'blueprint_id': 'test_blueprint',
        'deployment_id': 'test_deployment',
        'execution_id': 'test_execution',
        'rest_token': 'test_token',
        'properties': {'cloudify_agent': agent_properties},
        'runtime_properties': {'cloudify_agent': agent_runtime_properties},
        'managers': [
            {
                'networks': {'default': '127.0.0.1'},
                'ca_cert_content': agent_ssl_cert.DUMMY_CERT,
                'hostname': 'cloudify'
            }
        ],
        'brokers': [
            {
                'networks': {'default': '127.0.0.1'},
                'ca_cert_content': agent_ssl_cert.DUMMY_CERT
            }
        ],
        'bootstrap_context': BootstrapContext(
            bootstrap_context={'cloudify_agent': agent_context}),
        'tenant': {
            'name': 'default_tenant',
            'rabbitmq_username': 'guest',
            'rabbitmq_password': 'guest',
            'rabbitmq_vhost': '/'
        }
    }
    context.update(kwargs)
    context = MockCloudifyContext(**context)
    context.installer = MagicMock()
    context._get_current_object = lambda: context
    return context


def get_tenant_mock():
    return patch('cloudify.utils.get_tenant', return_value={
        'name': 'default_tenant',
        'rabbitmq_username': 'guest',
        'rabbitmq_password': 'guest',
        'rabbitmq_vhost': '/'
    })
