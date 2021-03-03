# Copyright 2021 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import unittest

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.relation import Provider, Consumer
from ops.testing import Harness

##########################
# Data used by all tests #
##########################

# Used to populate metadata.yaml
METADATA = {
    'relation_name': 'service',
    'interface_name': 'svc'
}

# Used to setup provder and cosumer data
CONFIG = {
    'relation_name': METADATA['relation_name'],
    'service_type': 'TestService',
    'service_version': '1.0.0'
}

# Template for Provider metadata.yaml
PROVIDER_META = '''
name: provider-charm
provides:
  {relation_name}:
    interface: {interface_name}
'''

# Template for Consumer metadata.yaml
CONSUMER_META = '''
name: provider-charm
requires:
  {relation_name}:
    interface: {interface_name}
'''

# Template for Provider/Consumer charm config.yaml
CONFIG_YAML = '''
options:
  relation_name:
    type: string
    description: 'Relation name used for testing'
    default: {relation_name}
  service_type:
    type: string
    description: 'Service type name used for testing'
    default: {service_type}
  service_version:
    type: string
    description: 'Service version used for testing'
    default: {service_version}
'''


class ProviderCharm(CharmBase):
    """A Provider charm used for testing"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args)
        self.provider = Provider(self,
                                 self.model.config['relation_name'],
                                 self.provides)

    def ready(self):
        self.provider.ready()

    def unready(self):
        self.provider.unready()

    @property
    def provides(self):
        return {'provides': {
            self.model.config['service_type']:
            self.model.config['service_version']}}


class ConsumerCharm(CharmBase):
    """A Consumer charm used for testing

    This charm records all events it receives in its stored state.
    """
    stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.stored.set_default(events=[])
        self.provider = Consumer(self,
                                 self.model.config['relation_name'],
                                 self.consumes)
        self.framework.observe(self.provider.on.invalid, self.on_invalid)
        self.framework.observe(self.provider.on.broken, self.on_broken)
        self.framework.observe(self.provider.on.available, self.on_available)

    @property
    def consumes(self):
        return {
            self.model.config['service_type']:
            self.model.config['service_version']}

    def on_invalid(self, event):
        self.stored.events.append({'Invalid': {}})

    def on_broken(self, event):
        self.stored.events.append({'Broken': {}})

    def on_available(self, event):
        self.stored.events.append({'Available': event.data})


class TestRelation(unittest.TestCase):

    def test_provider_posts_relation_data(self):
        harness, meta, config = self.default_setup('provider')
        harness.set_leader(True)

        rel_id = harness.add_relation(meta['relation_name'], 'aservice')
        harness.add_relation_unit(rel_id, 'aservice/0')
        data = harness.get_relation_data(rel_id, harness.model.app.name)
        provider_data = json.loads(data.get('provider_data'))
        self.assertIsNotNone(provider_data)
        services = provider_data.get('provides')
        self.assertEqual(len(services), 1)
        service = list(services.keys())[0]
        self.assertEqual(service, config['service_type'])
        self.assertEqual(services[service], config['service_version'])

    def test_consumer_emits_valid_relation_event(self):
        harness, meta, config = self.default_setup('consumer')

        provides = {'provides': {config['service_type']: config['service_version']},
                    'ready': True,
                    'config': 'provider_config'}
        provider_data = {'provider_data': json.dumps(provides)}
        rel_id = harness.add_relation(meta['relation_name'], 'aservice')
        harness.add_relation_unit(rel_id, 'aservice/0')
        harness.update_relation_data(rel_id, 'aservice', provider_data)

        received_events = harness.charm.stored.events
        self.assertEqual(len(received_events), 1)
        event = received_events.pop(0)
        self.assertTrue('Available' in event)
        data = event['Available']
        self.assertTrue('config' in data)
        config = data['config']
        self.assertEqual(config, 'provider_config')

    def test_consumer_emits_invalid_relation_event(self):
        harness, meta, config = self.default_setup('consumer')

        provides = {'provides': {config['service_type']: '0.9.0'},
                    'ready': True,
                    'config': 'provider_config'}
        provider_data = {'provider_data': json.dumps(provides)}
        rel_id = harness.add_relation(meta['relation_name'], 'aservice')
        harness.add_relation_unit(rel_id, 'aservice/0')

        with self.assertLogs(level='ERROR') as logger:
            harness.update_relation_data(rel_id, 'aservice', provider_data)
            self.assertTrue(logger.output is not None)

        received_events = harness.charm.stored.events
        self.assertEqual(len(received_events), 1)
        event = received_events.pop(0)
        self.assertTrue('Invalid' in event)

    def test_consumer_revalidates_provider_on_upgrade(self):
        harness, meta, config = self.default_setup('consumer')

        provides = {'provides': {config['service_type']: '1.0.0'},
                    'ready': True,
                    'config': 'provider_config'}
        provider_data = {'provider_data': json.dumps(provides)}

        rel_id = harness.add_relation(meta['relation_name'], 'aservice')
        harness.add_relation_unit(rel_id, 'aservice/0')
        harness.update_relation_data(rel_id, 'aservice', provider_data)
        self.assertEqual(len(harness.charm.stored.events), 1)
        events = harness.charm.stored.events.pop()
        self.assertTrue('Available' in events)
        self.assertEqual(len(harness.charm.stored.events), 0)
        harness.charm.on.upgrade_charm.emit()
        self.assertEqual(len(harness.charm.stored.events), 1)
        events = harness.charm.stored.events.pop()
        self.assertTrue('Available' in events)

    def test_provider_notifies_consumer_on_upgrade(self):
        harness, meta, config = self.default_setup('provider')
        harness.set_leader(True)

        rel_id = harness.add_relation(meta['relation_name'], 'aservice')
        harness.add_relation_unit(rel_id, 'aservice/0')
        data = harness.get_relation_data(rel_id, harness.model.app.name)
        provider_data = json.loads(data.get('provider_data'))
        provides = provider_data['provides']
        self.assertTrue('1.0.0' in provides.values())
        harness.charm.provider.provides = {'provides': {config['service_type']: '2.0.0'}}
        harness.charm.on.upgrade_charm.emit()
        data = harness.get_relation_data(rel_id, harness.model.app.name)
        provider_data = json.loads(data.get('provider_data'))
        provides = provider_data['provides']
        self.assertTrue('1.0.0' not in provides.values())
        self.assertTrue('2.0.0' in provides.values())

    def test_provider_notifies_consumer_on_ready_change(self):
        harness, meta, config = self.default_setup('provider')
        harness.set_leader(True)

        rel_id = harness.add_relation(meta['relation_name'], 'aservice')
        harness.add_relation_unit(rel_id, 'aservice/0')
        data = harness.get_relation_data(rel_id, harness.model.app.name)
        provider_data = json.loads(data.get('provider_data'))
        self.assertFalse(provider_data['ready'])
        harness.charm.ready()
        data = harness.get_relation_data(rel_id, harness.model.app.name)
        provider_data = json.loads(data.get('provider_data'))
        self.assertTrue(provider_data['ready'])
        harness.charm.unready()
        data = harness.get_relation_data(rel_id, harness.model.app.name)
        provider_data = json.loads(data.get('provider_data'))
        self.assertFalse(provider_data['ready'])

    def default_setup(self, setup):
        """Utility to instantiate test harness

        This utility can be used to instantiate a test harness for
        either a Provider charm or a Consumer charm.

        Args:
            setup: a string which is either `provider` or `consumer`.

        Returns:
            tuple: of harness object, metadata dict and config dict
        """
        config = CONFIG.copy()
        config_yaml = CONFIG_YAML.format(**config)

        meta = METADATA.copy()
        if setup == 'provider':
            meta_yaml = PROVIDER_META.format(**meta)
            harness = Harness(ProviderCharm, meta=meta_yaml, config=config_yaml)
        elif setup == 'consumer':
            meta_yaml = CONSUMER_META.format(**meta)
            harness = Harness(ConsumerCharm, meta=meta_yaml, config=config_yaml)
        else:
            raise ValueError("Setup type should be 'provider' or 'consumer'")

        self.addCleanup(harness.cleanup)
        harness.begin()

        return harness, meta, config
