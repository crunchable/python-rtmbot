#!/usr/bin/env python
from __future__ import unicode_literals
import sys
import glob
import os
import time
import logging
import json
import itertools

from slackclient import SlackClient
from websocket import WebSocketConnectionClosedException

sys.dont_write_bytecode = True

counter = itertools.count(1)

def channel_send_message(channel, message):
    global counter
    return channel.server.websocket.send(json.dumps({'id': counter.next(), 'type': 'message', 'text': message, 'channel': channel.id}))

class RtmBot(object):
    def __init__(self, config):
        '''
            Params:
                - config (dict):
                    - SLACK_TOKEN: your authentication token from Slack
                    - BASE_PATH (optional: defaults to execution directory) RtmBot will
                        look in this directory for plugins.
                    - LOGFILE (optional: defaults to rtmbot.log) The filename for logs, will
                        be stored inside the BASE_PATH directory
                    - DEBUG (optional: defaults to False) with debug enabled, RtmBot will
                        break on errors
        '''
        # set the config object
        self.config = config

        # set slack token
        self.token = config.get('SLACK_TOKEN')

        # set working directory for loading plugins or other files
        working_directory = os.path.dirname(sys.argv[0])
        self.directory = self.config.get('BASE_PATH', working_directory)
        if not self.directory.startswith('/'):
            path = '{}/{}'.format(os.getcwd(), self.directory)
            self.directory = os.path.abspath(path)

        # establish logging
        log_file = config.get('LOGFILE', 'rtmbot.log')
        logging.basicConfig(filename=os.path.join(self.directory, log_file),
                            level=logging.INFO,
                            format='%(asctime)s %(message)s')
        logging.info('Initialized in: {}'.format(self.directory))
        self.debug = self.config.get('DEBUG', False)

        # initialize stateful fields
        self.last_ping = 0
        self.bot_plugins = []
        self.slack_client = None

    def _dbg(self, debug_string):
        if self.debug:
            logging.info(debug_string)

    def connect(self):
        """Convenience method that creates Server instance"""
        self.slack_client = SlackClient(self.token)
        logging.info(self.slack_client)
        self.slack_client.rtm_connect()

    def _start(self):
        self.connect()
        self.load_plugins()
        self.get_user_info()
        while True:
            try:
                for reply in self.slack_client.rtm_read():
                    self.input(reply)
                self.crons()
                self.output()
                self.autoping()
                time.sleep(.1)
            except WebSocketConnectionClosedException as e:
                logging.exception("websocket connection was closed: {}".format(e))
                self.slack_client.rtm_connect()
   
    def get_user_info(self):
        user_info = self.slack_client.api_call('auth.test')
        user_info['type'] = 'user_info'
        self.input(user_info)

    def start(self):
        if 'DAEMON' in self.config:
            if self.config.get('DAEMON'):
                import daemon
                with daemon.DaemonContext():
                    self._start()
        self._start()

    def autoping(self):
        # hardcode the interval to 3 seconds
        now = int(time.time())
        if now > self.last_ping + 3:
            self.slack_client.server.ping()
            self.last_ping = now

    def input(self, data):
        if "type" in data:
            data['__slack_client'] = self.slack_client
            function_name = "process_" + data["type"]
            self._dbg("got {}".format(function_name))
            for plugin in self.bot_plugins:
                plugin.register_jobs()
                plugin.do(function_name, data)

    def output(self):
        for plugin in self.bot_plugins:
            limiter = False
            for output in plugin.do_output():
                channel = self.slack_client.server.channels.find(output[0])
                if channel is not None and output[1] is not None:
                    if limiter:
                        time.sleep(.1)
                        limiter = False
                    if output[1] == 'TYPING':
                        channel.server.send_to_websocket({"type": "typing", "channel": channel.id})
                    elif output[1] == 'DM':
                        try:
                            user, text = output[2:]
                            dm_channel_request = self.slack_client.api_call('im.open', user=user)
                            dm_channel_id = dm_channel_request['channel']['id']
                            dm_channel = self.slack_client.server.channels.find(dm_channel_id)
                            channel_send_message(dm_channel, text)
                        except Exception as e:
                            logging.error('error sending DM: {}'.format(e))
                    elif output[1] == 'FILE':
                        try:
                            content, filetype, filename = output[2:]
                            self.slack_client.server.api_call('files.upload', content=content, filetype=filetype, filename=filename, channels=[channel.id])                            
                        except Exception as e:
                            logging.error('error sending DM: {}'.format(e))
                    else:
                        channel_send_message(channel, output[1])
                    limiter = True

    def crons(self):
        for plugin in self.bot_plugins:
            plugin.do_jobs()

    def load_plugins(self):
        for plugin in glob.glob(self.directory + '/plugins/*'):
            sys.path.insert(0, plugin)
            sys.path.insert(0, self.directory + '/plugins/')
        for plugin in glob.glob(self.directory + '/plugins/*.py') + \
                glob.glob(self.directory + '/plugins/*/*.py'):
            logging.info(plugin)
            name = plugin.split('/')[-1][:-3]
            if name in self.config:
                logging.info("config found for: " + name)
            plugin_config = self.config.get(name, {})
            plugin_config['DEBUG'] = self.debug
            self.bot_plugins.append(Plugin(name, plugin_config))


class Plugin(object):

    def __init__(self, name, plugin_config=None):
        '''
        A plugin in initialized with:
            - name (str)
            - plugin config (dict) - (from the yaml config)
                Values in config:
                - DEBUG (bool) - this will be overridden if debug is set in config for this plugin
        '''
        if plugin_config is None:
            plugin_config = {}
        self.name = name
        self.jobs = []
        self.module = __import__(name)
        self.module.config = plugin_config
        self.debug = self.module.config.get('DEBUG', False)
        self.register_jobs()
        self.outputs = []
        if 'setup' in dir(self.module):
            self.module.setup()

    def register_jobs(self):
        if 'crontable' in dir(self.module):
            for interval, function in self.module.crontable:
                self.jobs.append(Job(interval, eval("self.module." + function), self.debug))
            if self.module.crontable:
                logging.info('crontab: {}'.format(self.module.crontable))
            self.module.crontable = []
        else:
            self.module.crontable = []

    def do(self, function_name, data):
        if function_name in dir(self.module):
            if self.debug is True:
                # this makes the plugin fail with stack trace in debug mode
                eval("self.module." + function_name)(data)
            else:
                # otherwise we log the exception and carry on
                try:
                    eval("self.module." + function_name)(data)
                except Exception:
                    logging.exception("problem in module {} {}".format(function_name, data))
        if "catch_all" in dir(self.module):
            if self.debug is True:
                # this makes the plugin fail with stack trace in debug mode
                self.module.catch_all(data)
            else:
                try:
                    self.module.catch_all(data)
                except Exception:
                    logging.exception("problem in catch all: {} {}".format(self.module, data))

    def do_jobs(self):
        for job in self.jobs:
            job.check()

    def do_output(self):
        output = []
        while True:
            if 'outputs' in dir(self.module):
                if len(self.module.outputs) > 0:
                    o = self.module.outputs.pop(0)
                    logging.info("output from {}: {}".format(self.module, o))
                    output.append(o)
                else:
                    break
            else:
                self.module.outputs = []
        return output


class Job(object):
    def __init__(self, interval, function, debug):
        self.function = function
        self.interval = interval
        self.lastrun = 0
        self.debug = debug

    def __str__(self):
        return "{} {} {}".format(self.function, self.interval, self.lastrun)

    def __repr__(self):
        return self.__str__()

    def check(self):
        if self.lastrun + self.interval < time.time():
            if self.debug is True:
                # this makes the plugin fail with stack trace in debug mode
                self.function()
            else:
                # otherwise we log the exception and carry on
                try:
                    self.function()
                except Exception:
                    logging.exception("Problem in job check: {}".format(self.function))
            self.lastrun = time.time()


class UnknownChannel(Exception):
    pass
