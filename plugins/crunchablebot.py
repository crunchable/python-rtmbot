from __future__ import print_function
from __future__ import unicode_literals
# don't convert to ascii in py2.7 when creating string to return

import os
import json
import gevent
from crunchable import Crunchable

outputs = []
crontabs = []
user_name = None
user_id = None

FILE = "plugins/tasks.json"

def head(text):
    try:
        first, rest = text.split(None, 1)
        return first, rest
    except ValueError:
        return text, None

def get_tasks():
    return json.loads(open(FILE, 'rb').read())
def save_tasks(tasks):
    with open(FILE, 'wb') as f:
        f.write(json.dumps(tasks, indent=2))
if not os.path.isfile(FILE):
    save_tasks({})
def add_new_task(identifier, task):
    tasks = get_tasks()
    tasks[identifier] = task
    save_tasks(tasks)

def send_task(task, attachments):
    global config
    token = config['CRUNCHABLE_TOKEN']
    client = Crunchable(token)
    request = client.request_free_text(attachments=attachments, **task)
    response = client.wait_for_task(request['id'])
    return response['response']

def learn_new_instruction(channel, text, override=False):
    identifier, instruction = head(text)
    if not override:
        tasks = get_tasks()
        if identifier in tasks:
            outputs.append([channel, "I already know how to do this! if you want to override - try 'crunchable reteach ...'"])
            return
    
    task = {'instruction': instruction}
    add_new_task(identifier, task)
    outputs.append([channel, 'Thanks! now I know how to do that!'])

def trigger_known_instruction(channel, user, task, text):
    outputs.append([channel, "I'm on it, <@{}> !".format(user)])
    response = send_task(task, attachments=[text])
    outputs.append([channel, "<@{}>, Here's your response: {}".format(user, response)])

def show_help_messsage(channel, tasks):
    outputs.append([channel, "Here's what I already know how to do:"])
    for identifier, task in tasks.iteritems():
        outputs.append([channel, "{} - {}".format(identifier, task['instruction'])])
    outputs.append([channel, "But you can easily teach me new stuff! simply use:"])
    show_teach_instruction(channel)

def show_teach_instruction(channel):
    outputs.append([channel, "crunchable teach <keyword> '<detailed instruction>'"])
    outputs.append([channel, "For example: crunchable teach gettimezone 'Search google for the timezone of the given city'"])    

def process_message(data):
    print(data)
    channel = data["channel"]
    text = data["text"]
    try:
        myname, moretext = head(text)
        if myname not in ['crunchable', '<@{}>:'.format(user_id), user_name]:
            return
    except ValueError:
        return

    try:
        if moretext.lower() == "are you ready?":
            return outputs.append([channel, "I was born ready!"])
        identifier, rest = head(moretext)
        identifier = identifier.lower()
        if identifier == 'teach':
            return learn_new_instruction(channel, rest)
        if identifier == 'reteach':
            return learn_new_instruction(channel, rest, override=True)
        tasks = get_tasks()
        if identifier == 'help':
            return show_help_messsage(channel, tasks)
        if any(identifier.startswith(x) for x in ['thank', '10x']):
            outputs.append([channel, "You're welcome, <@{}>!".format(data['user'])])
            return 
        if identifier in tasks:
            return gevent.spawn(trigger_known_instruction, channel, data['user'], tasks[identifier], rest)
    except ValueError:
        return outputs.append([channel, "Sorry, I'm not feeling so well... can you send someone to check in on me, please?"])

    outputs.append([channel, "Sorry, you'll have to teach me how to do this..."])
    outputs.append([channel, "Use this syntax to do so:"])
    show_teach_instruction(channel)

def process_user_info(data):
    global user_name
    global user_id
    if 'user' in data:
        user_name = data['user']
    if 'user_id' in data:
        user_id = data['user_id']

def catch_all(data):
    global config
    print(config)
    print("[ALL]", data)
