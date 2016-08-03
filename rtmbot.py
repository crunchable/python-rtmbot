#!/usr/bin/env python
import gevent.monkey; gevent.monkey.patch_all()
import sys
from argparse import ArgumentParser

import yaml
from rtmbot import RtmBot
import logging

def parse_args():
    parser = ArgumentParser()
    parser.add_argument(
        '-c',
        '--config',
        help='Full path to config file.',
        metavar='path'
    )
    return parser.parse_args()

# load args with config path
args = parse_args()
config = yaml.load(open(args.config or 'rtmbot.conf', 'r'))
bot = RtmBot(config)
while True:
    try:
        logging.info('starting bot')
        bot.start()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        logging.exception("Something wrong happened, restarting...") 

