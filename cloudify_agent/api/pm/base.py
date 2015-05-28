#########
# Copyright (c) 2013 GigaSpaces Technologies Ltd. All rights reserved
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

import tempfile
import getpass
import os
import time

from celery import Celery

from cloudify.utils import LocalCommandRunner
from cloudify.utils import setup_logger
from cloudify import amqp_client
from cloudify.exceptions import CommandExecutionException

from cloudify_agent import VIRTUALENV
from cloudify_agent.api import utils
from cloudify_agent.api import errors
from cloudify_agent.api import exceptions
from cloudify_agent.api import defaults
from cloudify_agent.api.utils import get_storage_directory
from cloudify_agent import operations


class Daemon(object):

    """
    Base class for daemon implementations.
    Following is all the available common daemon keyword arguments. These
    will be available to any daemon without any configuration as instance
    attributes.

    ``manager_ip``:

        the ip address of the manager host. (Required)

    ``user``:

        the user this daemon will run under. default to the current user.

    ``name``:

        the name to give the daemon. This name will be a unique identifier of
        the daemon. meaning you will not be able to create more daemons with
        that name until a delete operation has been performed. defaults to
        a unique name generated by cloudify.

    ``queue``:

        the queue this daemon will listen to. It is possible to create
        different workers with the same queue, however this is discouraged.
        to create more workers that process tasks of a given queue, use the
        'min_workers' and 'max_workers' keys. defaults to <name>-queue.

    ``workdir``:

        working directory for runtime files (pid, log).
        defaults to the current working directory.

    ``broker_ip``:

        the ip address of the broker to connect to.
        defaults to the manager_ip value.

    ``broker_port``

        the connection port of the broker process.
        defaults to 5672.

    ``broker_url``:

        full url to the broker. if this key is specified,
        the broker_ip and broker_port keys are ignored.

        for example:
            amqp://192.168.9.19:6786

        if this is not specified, the broker url will be constructed from the
        broker_ip and broker_port like so:
        'amqp://guest:guest@<broker_ip>:<broker_port>//'

    ``manager_port``:

        the manager REST gateway port to connect to. defaults to 80.

    ``min_workers``:

        the minimum number of worker processes this daemon will manage. all
        workers will listen on the same queue allowing for higher
        concurrency when preforming tasks. defaults to 0.

    ``max_workers``:

        the maximum number of worker processes this daemon will manage.
        as tasks keep coming in, the daemon will expand its worker pool to
        handle more tasks concurrently. However, as the name
        suggests, it will never exceed this number. allowing for the control
        of resource usage. defaults to 5.

    ``extra_env_path``:

        path to a file containing environment variables to be added to the
        daemon environment. the file should be in the format of
        multiple 'export A=B' lines for linux, ot 'set A=B' for windows.
        defaults to None.

    ``log_level``:

        log level of the daemon process itself. defaults to debug.

    ``log_file``:

        location of the daemon log file. defaults to
        <workdir>/<name>.log

    ``pid_file``:

        location of the daemon pid file. defaults to
        <workdir>/<name>.pid

    ``includes``:

        a comma separated list of modules to include with this agent.
        if none if specified, only the built-in modules will be included.

        see `cloudify_agent.operations.CLOUDIFY_AGENT_BUILT_IN_TASK_MODULES`

        This option may also be passed as a regular JSON list.

    """

    # override this when adding implementations.
    PROCESS_MANAGEMENT = None

    # add specific mandatory parameters for different implementations.
    # they will be validated upon daemon creation
    MANDATORY_PARAMS = [
        'manager_ip'
    ]

    def __init__(self, logger=None, **params):

        """

        ####################################################################
        # When subclassing this, do not implement any logic inside the
        # constructor expect for in-memory calculations and settings, as the
        # daemon may be instantiated many times for an existing agent.
        ####################################################################

        :param logger: a logger to be used to log various subsequent
        operations.
        :type logger: logging.Logger

        :param params: key-value pairs as stated above.
        :type params dict

        """

        # configure logger
        self.logger = logger or setup_logger(
            logger_name='cloudify_agent.api.pm.{0}'
            .format(self.PROCESS_MANAGEMENT))

        # configure command runner
        self.runner = LocalCommandRunner(logger=self.logger)

        # Mandatory parameters
        self.validate_mandatory(params)
        self.manager_ip = params['manager_ip']

        # Optional parameters
        self.validate_optional(params)
        self.user = params.get('user') or getpass.getuser()
        self.broker_ip = params.get(
            'broker_ip') or self.manager_ip
        self.broker_port = params.get(
            'broker_port') or defaults.BROKER_PORT
        self.name = params.get(
            'name') or utils.generate_agent_name()
        self.queue = params.get(
            'queue') or '{0}-queue'.format(self.name)
        self.broker_url = params.get(
            'broker_url') or defaults.BROKER_URL.format(
            self.broker_ip,
            self.broker_port)
        self.manager_port = params.get(
            'manager_port') or defaults.MANAGER_PORT
        self.min_workers = params.get(
            'min_workers') or defaults.MIN_WORKERS
        self.max_workers = params.get(
            'max_workers') or defaults.MAX_WORKERS
        self.workdir = params.get(
            'workdir') or os.getcwd()
        self.extra_env_path = params.get('extra_env_path')
        self.log_level = params.get('log_level') or defaults.LOG_LEVEL
        self.log_file = params.get(
            'log_file') or os.path.join(self.workdir,
                                        '{0}.log'.format(self.name))
        self.pid_file = params.get(
            'pid_file') or os.path.join(self.workdir,
                                        '{0}.pid'.format(self.name))

        # accept the 'includes' parameter as a string as well
        # as a list. the string acceptance is important because this
        # class is instantiated by a CLI as well as API, and its not very
        # convenient to pass proper lists on CLI.
        includes = params.get('includes')
        if includes:
            if isinstance(includes, str):
                self.includes = includes.split(',')
            elif isinstance(includes, list):
                self.includes = includes
            else:
                raise ValueError("Unexpected type for 'includes' parameter: "
                                 "{0}. supported type are 'str' and 'list'"
                                 .format(type(includes)))
        else:
            self.includes = []

        # add built-in operations. check they don't already exist to avoid
        # duplicates, which may happen when cloning daemons.
        for module in operations.CLOUDIFY_AGENT_BUILT_IN_TASK_MODULES:
            if module not in self.includes:
                self.includes.append(module)

        # create working directory if its missing
        if not os.path.exists(self.workdir):
            self.logger.debug('Creating directory: {0}'.format(self.workdir))
            os.makedirs(self.workdir)

        # save as attributes so that they will be persisted in the json files.
        # we will make use of these values when loading agents by name.
        self.process_management = self.PROCESS_MANAGEMENT
        self.virtualenv = VIRTUALENV

        # initialize an internal celery client
        self.celery = Celery(broker=self.broker_url,
                             backend=self.broker_url)

    @classmethod
    def validate_mandatory(cls, params):

        """
        Validates that all mandatory parameters are given.

        :param params: parameters of the daemon.
        :type params: dict

        :raise DaemonMissingMandatoryPropertyError: in case one of the
        mandatory parameters is missing.
        """

        for param in cls.MANDATORY_PARAMS:
            if param not in params:
                raise errors.DaemonMissingMandatoryPropertyError(param)

    @staticmethod
    def validate_optional(params):

        """
        Validates any optional parameters given to the daemon.

        :param params: parameters of the daemon.
        :type params: dict

        :raise DaemonPropertiesError:
        in case one of the parameters is faulty.
        """

        min_workers = params.get('min_workers')
        max_workers = params.get('max_workers')

        if min_workers:
            if not str(min_workers).isdigit():
                raise errors.DaemonPropertiesError(
                    'min_workers is supposed to be a number '
                    'but is: {0}'
                    .format(min_workers)
                )
            min_workers = int(min_workers)

        if max_workers:
            if not str(max_workers).isdigit():
                raise errors.DaemonPropertiesError(
                    'max_workers is supposed to be a number '
                    'but is: {0}'
                    .format(max_workers)
                )
            max_workers = int(max_workers)

        if min_workers and max_workers:
            if min_workers > max_workers:
                raise errors.DaemonPropertiesError(
                    'min_workers cannot be greater than max_workers '
                    '[min_workers={0}, max_workers={1}]'
                    .format(min_workers, max_workers))

    ########################################################################
    # the following methods must be implemented by the sub-classes as they
    # may exhibit custom logic. usually this would be related to process
    # management specific configuration files.
    ########################################################################

    def configure(self):

        """
        Creates any necessary resources for the daemon. This method must
        create all necessary configuration of the daemon. After this method
        was completed successfully, it should be possible to start the daemon
        by running the command returned by the `start_command` method.

        :return: The daemon name.
        :rtype: str
        """
        raise NotImplementedError('Must be implemented by a subclass')

    def delete(self, force=defaults.DAEMON_FORCE_DELETE):

        """
        Delete any resources created by the daemon.

        :param force: if the daemon is still running, stop it before
        deleting it.
        :type force: bool
        """
        raise NotImplementedError('Must be implemented by a subclass')

    def set_includes(self):

        """
        Sets the includes list of the agent. This method must modify the
        includes configuration used when starting the agent.
        """
        raise NotImplementedError('Must be implemented by a subclass')

    def start_command(self):

        """
        A command line for starting the daemon.
        (e.g sudo service <name> start)
        """
        raise NotImplementedError('Must be implemented by a subclass')

    def stop_command(self):

        """
        A command line for stopping the daemon.
        (e.g sudo service <name> stop)
        """
        raise NotImplementedError('Must be implemented by a subclass')

    def status(self):

        """
        Query the daemon status, usually by running the status_command.

        :return: True if the service is running, False otherwise
        :rtype: bool
        """
        raise NotImplementedError('Must be implemented by a subclass')

    ########################################################################
    # the following methods is the common logic that would apply to any
    # process management implementation.
    ########################################################################

    def register(self, plugin):

        """
        Register an additional plugin. This method will enable the addition
        of operations defined in the plugin.

        :param plugin: The plugin name to register.
        :type plugin: str
        """

        self.logger.debug('Listing modules of plugin: {0}'.format(plugin))
        modules = utils.list_plugin_files(plugin)

        self.includes.extend(modules)

        # process management specific implementation
        self.logger.debug('Setting includes: {0}'.format(self.includes))
        self.set_includes()

    def create(self):

        """
        Creates the agent. This method may be served as a hook to some custom
        logic that needs to be implemented after the instance
        was instantiated.
        """
        pass

    def start(self,
              interval=defaults.START_INTERVAL,
              timeout=defaults.START_TIMEOUT,
              delete_amqp_queue=defaults.DELETE_AMQP_QUEUE_BEFORE_START):

        """
        Starts the daemon process.

        :param interval: the interval in seconds to sleep when waiting for
        the daemon to be ready.
        :type interval: int

        :param timeout: the timeout in seconds to wait for the daemon to be
        ready.
        :type timeout: int

        :param delete_amqp_queue: delete any queues with the name of the
        current daemon queue in the broker.
        :type delete_amqp_queue: bool

        :raise DaemonStartupTimeout: in case the agent failed to start in the
        given amount of time.
        :raise DaemonException: in case an error happened during the agent
        startup.

        """

        if delete_amqp_queue:
            self._delete_amqp_queues()
        start_command = self.start_command()
        self.logger.debug('Starting daemon with command: {0}'
                          .format(start_command))
        self.runner.run(start_command)
        end_time = time.time() + timeout
        while time.time() < end_time:
            self.logger.debug('Validating daemon {0} stats'.format(self.name))
            stats = utils.get_agent_stats(self.name, self.celery)
            if stats:
                self.logger.debug('Daemon {0} has started'.format(self.name))
                return
            self.logger.debug('Daemon {0} has not started yet. '
                              'Sleeping for {1} seconds...'
                              .format(self.name, interval))
            time.sleep(interval)
        self._verify_no_celery_error()
        raise exceptions.DaemonStartupTimeout(timeout, self.name)

    def stop(self,
             interval=defaults.STOP_INTERVAL,
             timeout=defaults.STOP_TIMEOUT):

        """
        Stops the daemon process.

        :param interval: the interval in seconds to sleep when waiting for
        the daemon to stop.
        :type interval: int

        :param timeout: the timeout in seconds to wait for the daemon to stop.
        :type timeout: int

        :raise DaemonShutdownTimeout: in case the agent failed to be stopped
        in the given amount of time.
        :raise DaemonException: in case an error happened during the agent
        shutdown.

        """

        stop_command = self.stop_command()
        self.logger.debug('Stopping daemon with command: {0}'
                          .format(stop_command))
        self.runner.run(stop_command)
        end_time = time.time() + timeout
        while time.time() < end_time:
            self.logger.debug('Validating daemon {0} stats'.format(self.name))
            # check the process has shutdown
            stats = utils.get_agent_stats(self.name, self.celery)
            if not stats:
                # make sure the status command also recognizes the
                # daemon is down
                status = self.status()
                if not status:
                    self.logger.debug('Daemon {0} has shutdown'
                                      .format(self.name, interval))
                    return
            self.logger.debug('Daemon {0} is still running. '
                              'Sleeping for {1} seconds...'
                              .format(self.name, interval))
            time.sleep(interval)
        self._verify_no_celery_error()
        raise exceptions.DaemonShutdownTimeout(timeout, self.name)

    def restart(self,
                start_timeout=defaults.START_TIMEOUT,
                start_interval=defaults.START_INTERVAL,
                stop_timeout=defaults.STOP_TIMEOUT,
                stop_interval=defaults.STOP_INTERVAL):

        """
        Restart the daemon process.

        :param start_interval: the interval in seconds to sleep when waiting
        for the daemon to start.
        :type start_interval: int

        :param start_timeout: The timeout in seconds to wait for the daemon
        to start.
        :type start_timeout: int

        :param stop_interval: the interval in seconds to sleep when waiting
        for the daemon to stop.
        :type stop_interval: int

        :param stop_timeout: the timeout in seconds to wait for the daemon
        to stop.
        :type stop_timeout: int

        :raise DaemonStartupTimeout: in case the agent failed to start in the
        given amount of time.
        :raise DaemonShutdownTimeout: in case the agent failed to be stopped
        in the given amount of time.
        :raise DaemonException: in case an error happened during startup or
        shutdown

        """

        self.stop(timeout=stop_timeout,
                  interval=stop_interval)
        self.start(timeout=start_timeout,
                   interval=start_interval)

    def _verify_no_celery_error(self):

        error_dump_path = os.path.join(
            get_storage_directory(self.user),
            '{0}.err'.format(self.name))

        # this means the celery worker had an uncaught
        # exception and it wrote its content
        # to the file above because of our custom exception
        # handler (see app.py)
        if os.path.exists(error_dump_path):
            with open(error_dump_path) as f:
                error = f.read()
            os.remove(error_dump_path)
            raise exceptions.DaemonException(error)

    def _delete_amqp_queues(self):
        client = amqp_client.create_client(self.broker_ip)
        try:
            channel = client.connection.channel()
            self.logger.debug('Deleting queue: {0}'.format(self.queue))
            channel.queue_delete(self.queue)
            pid_box_queue = 'celery@{0}.celery.pidbox'.format(self.queue)
            self.logger.debug('Deleting queue: {0}'.format(pid_box_queue))
            channel.queue_delete(pid_box_queue)
        finally:
            try:
                client.close()
            except Exception as e:
                self.logger.warning('Failed closing amqp client: {0}'
                                    .format(e))


class CronSupervisorMixin(Daemon):

    """
    This Mixin provides the ability for daemons to be supervised by the
    Linux crontab process. A crontab job will run periodically and query
    the daemon status, if the status command failed, the job will trigger
    the start command in order to respawn the daemon.

    Note that usually, process management systems have the re-spawning
    capability built in, this mixin should be used by basic process
    management implementation that are lacking it.
    """

    def __init__(self, logger=None, **params):
        super(CronSupervisorMixin, self).__init__(logger, **params)
        self.cron_respawn_delay = params.get('cron_respawn_delay', 1)
        self.cron_respawn = params.get('cron_respawn', False)
        self.cron_respawn_path = os.path.join(
            self.workdir, '{0}-respawn'.format(self.name))

    def status_command(self):

        """
        A command line for querying the daemon status. This must be
        implemented for the crontab detector to work.
        The command should result in a zero return code if the service is
        running, and a non-zero return code otherwise.
        (e.g sudo service <name> status)

        """
        raise NotImplementedError('Must be implemented by a subclass')

    def start(self, interval=defaults.START_INTERVAL,
              timeout=defaults.START_TIMEOUT,
              delete_amqp_queue=defaults.DELETE_AMQP_QUEUE_BEFORE_START):
        super(CronSupervisorMixin, self).start(interval, timeout,
                                               delete_amqp_queue)

        if self.cron_respawn:
            self._enable_cron_respawn()

    def stop(self, interval=defaults.STOP_INTERVAL,
             timeout=defaults.STOP_TIMEOUT):
        super(CronSupervisorMixin, self).stop(interval, timeout)

        if self.cron_respawn:
            self._disable_cron_respawn()

    def _enable_cron_respawn(self):
        self.logger.debug('Rendering respawn script from template')
        rendered = utils.render_template_to_file(
            template_path='respawn.sh.template',
            start_command=self.start_command(),
            status_command=self.status_command()
        )

        self.runner.run('cp {0} {1}'.format(
            rendered, self.cron_respawn_path))
        self.runner.run('rm {0}'.format(rendered))
        self.runner.run('chmod +x {0}'.format(self.cron_respawn_path))
        self.logger.debug('Respawn script created at {0}'
                          .format(self.cron_respawn_path))

        self.logger.debug('Adding respawn script to crontab')
        temp_cron = tempfile.mkstemp()[1]
        try:
            crontab = self.runner.run('crontab -l').output
        except CommandExecutionException as e:
            # no crontab entries, that's ok
            self.logger.warning(str(e))
            crontab = ''
        with open(temp_cron, 'a') as f:
            if crontab:
                f.write(crontab)
                f.write(os.linesep)
            f.write('*/{0} * * * * {1}'.format(
                self.cron_respawn_delay, self.cron_respawn_path))
            f.write(os.linesep)
        self.runner.run('crontab {0}'.format(temp_cron))
        self.runner.run('rm {0}'.format(temp_cron))
        self.logger.debug('Successfully added respawn script to crontab')

    def _disable_cron_respawn(self):
        self.logger.debug('Removing respawn crontab entry')
        crontab = self.runner.run('crontab -l').output
        new_cron = tempfile.mkstemp()[1]
        with open(new_cron, 'a') as f:
            for entry in crontab.splitlines():
                # filter out entry for this daemon
                if self.name not in entry:
                    f.write(entry)
        self.runner.run('crontab {0}'.format(new_cron))
        self.logger.debug('Successfully removed respawn crontab entry')
