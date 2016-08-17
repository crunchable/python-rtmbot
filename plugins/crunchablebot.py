from __future__ import print_function
from __future__ import unicode_literals
# don't convert to ascii in py2.7 when creating string to return

import os
import json
import gevent
from crunchable import Crunchable
from braceexpand import expand_braces
import logging
from threading import Lock
from contextlib import contextmanager
lock = Lock()


outputs = []
crontabs = []
user_name = None
user_id = None

FILE = "plugins/tasks.json"

def get_state_file():
    global config
    return config.get('CRUNCHABLE_STATE', 'plugins/state.json')

@contextmanager
def get_state():
    filename = get_state_file()
    with lock:
        try:
            state = json.loads(open(filename, 'rb').read())
        except IOError:
            state = {}
        if 'pending' not in state:
            state['pending'] = {}
        yield state
        open(filename, 'wb').write(json.dumps(state))

def store_pending(id, channel, user, identifier):
    with get_state() as state:
        state['pending'][id] = [channel, user, identifier]

def pop_pending(id):
    with get_state() as state:
        try:
            state['pending'].pop(id)
        except KeyError:
            logging.warn('tried to pop missing id: {}'.format(id))

def read_state():
    with get_state() as state:
        return state

def respond(channel, text):
    global outputs
    outputs.append([channel, text])

def respond_to_user(channel, user, text):
    if channel.startswith('D'): #private chat
        respond(channel, text)
    else:
        respond(channel, '<@{}>: '.format(user) + text)

def dm_to_user(channel, user, text):
    if channel.startswith('D'): #private chat
        respond(channel, text)
    else:
        outputs.append([channel, 'DM', user, text])

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

def get_crunchable_client():
    global config
    token = config['CRUNCHABLE_TOKEN']
    client = Crunchable(token)
    return client

def send_task(task, attachments):
    client = get_crunchable_client()
    request = client.request_free_text(attachments=attachments, **task)
    response = client.wait_for_task(request['id'])
    return response['response']

def wait_for_task(channel, user, client, task_id, identifier):
    response = client.wait_for_task(task_id)
    if response['status'] == 'complete':
        answer = response.get('response', '')
    else:
        answer = response.get('cruncher_feedback')
    respond_to_user(channel, user, "Here's your response (you asked: {} {})".format(identifier, response['attachments'][0]))
    respond_to_user(channel, user, "{}".format(answer))
    pop_pending(task_id)

def send_tasks(channel, user, identifier, task, attachments):
    client = get_crunchable_client()
    requests = [client.request_free_text(attachments=[att], **task) for att in attachments]
    [store_pending(req['id'], channel, user, identifier) for req in requests]
    [gevent.spawn(wait_for_task, channel, user, client, req['id'], identifier) for req in requests]

def recover_state():
    state = read_state()
    pending = state.get('pending', {})
    client = get_crunchable_client()
    for (id, [channel, user, identifier]) in pending.iteritems():
        gevent.spawn(wait_for_task, channel, user, client, id, identifier)

def setup():
    recover_state()

SOMETHING_ELSE = 'Nothing fits'
NOT_A_REQUEST = 'Irrelevant/Nonsense'

RECOGNIZE_TASK = dict(
    instruction="We are building a knowledge-base on how to execute various internet searches.\nHelp us match between the **request** quoted below and a list of relevant search instructions listed in the choices.\n\nChoose the one that you think fits the request.\nChoose **'{}'** if none of them match.\nChoose **'{}'** if the attached request does not look like a question at all (or if it's just some non-sense)\n\n**There is no need to perform any task! only choose the most relevant one!**".format(SOMETHING_ELSE, NOT_A_REQUEST),
    min_answers = 1,
    max_answers = 1,
    choices_type='text',
#    tags = ['role.crunch_qa'],
)

def crunchable_recognize_task(text):
    client = get_crunchable_client()
    tasks = get_tasks()
    attachments = ['**Request:** {}'.format(text)] #  + ['{}: {}'.format(identifier, task['instruction']) for (identifier, task) in sorted(tasks.iteritems())]
    # choices = sorted(tasks.keys()) + [SOMETHING_ELSE, NOT_A_REQUEST]
    choices = ['{}: {}'.format(identifier, task['instruction']) for (identifier, task) in sorted(tasks.iteritems())] + [SOMETHING_ELSE, NOT_A_REQUEST]
    request = client.request_multiple_choice(choices=choices, attachments=attachments, **RECOGNIZE_TASK)
    response = client.wait_for_task(request['id'])
    [choice] = response['response']
    return choice

AUTOLEARN_TASK = dict(
    instruction="Look at the **request** below, and help us write a good instruction on how to perform similar tasks.\nAlso, please give a name to these types of instructions. \n See below a list of examples of similar tasks. \n \n **If you are unable to write a good instruction, just leave the response fields blank**",
    responseTitles=["identifier","instruction"],
    tags=['role.crunch_qa'],
)

def crunchable_autolearn_task(channel, user, text):
    client = get_crunchable_client()
    tasks = get_tasks()
    attachments = ['**Request:** ' + text] + ["**Example:**\nIdentifier: {}\nInstruction: {}".format(identifier, task['instruction']) for (identifier, task) in sorted(tasks.iteritems())]
    request = client.request_free_text(attachments=attachments, **AUTOLEARN_TASK)
    response = client.wait_for_task(request['id'])['response']
    identifier = response['identifier'].strip()
    instruction = response['instruction'].strip()
    if (not identifier) or (not instruction):
        return
    task = {'instruction': instruction}
    add_new_task(identifier, task)
    dm_to_user(channel, user, "I learned something new today!")
    dm_to_user(channel, user, "@crunchable-bot {} <text> --- {}".format(identifier, instruction))
    trigger_known_instruction(channel, user, task, text, identifier)

def learn_new_instruction(channel, text, override=False):
    identifier, instruction = head(text)
    if not override:
        tasks = get_tasks()
        if identifier in tasks:
            respond(channel, "I already know how to do this! if you want to override - try '@crunchable-bot: reteach ...'")
            return
    
    task = {'instruction': instruction}
    add_new_task(identifier, task)
    respond(channel, 'Thanks! now I know how to do that!')

def handle_unrecognized_commmand(channel, user, text):
    respond(channel, "TYPING")
    respond_to_user(channel, user, "I'm on it!")
    task_identifier = crunchable_recognize_task(text)
    if task_identifier == NOT_A_REQUEST:
        return respond_to_user(channel, user, "Sorry, I didn't understand you")
    if task_identifier == SOMETHING_ELSE:
        gevent.spawn(crunchable_autolearn_task, channel, user, text)
        respond_to_user(channel, user, "OK, let me see if I can figure it out (you can also help me by teaching me how to do it)")
        show_teach_instruction(channel)
        return
    tasks = get_tasks()
    identifier = task_identifier.split(':')[0]
    task = tasks[identifier]
    trigger_known_instruction(channel, user, task, text, identifier)

def trigger_known_instruction(channel, user, task, text, identifier):
    respond_to_user(channel, user, "Please wait while I look for someone to answer you...")
    send_tasks(channel, user, identifier, task, attachments=expand_braces(text))

def show_help_messsage(channel, tasks):
    respond(channel, "Here's what I already know how to do:")
    for identifier, task in tasks.iteritems():
        respond(channel, "{} - {}".format(identifier, task['instruction']))
    respond(channel, "But you can easily teach me new stuff! simply use:")
    show_teach_instruction(channel)

def show_teach_instruction(channel):
    respond(channel, "@crunchable-bot: teach <keyword> '<detailed instruction>'")
    respond(channel, "For example: @crunchable-bot: teach gettimezone 'Search google for the timezone of the given city'")    

def process_message(data):
    channel = data["channel"]
    logging.info('crunchable sees {}'.format(data))
    if 'text' not in data:
        logging.warn('got data with no text {}'.format(data))
        return
    text = data["text"]
    if 'user' not in data:
        logging.warn('got data with no user {}'.format(data))
        return
    user = data['user']
    if user == user_id:
        # ignore what I say...
        return
    possible_names = {'crunchable', '<@{}>:'.format(user_id), '<@{}>'.format(user_id), user_name}
    if channel.startswith('D'):
        # private chat
        try:
            myname, moretext = head(text)
            if myname not in possible_names:
                moretext = text 
        except ValueError:
            moretext = text
    else:
        try:
            myname, moretext = head(text)
            if myname not in possible_names:
                return
        except ValueError:
            return
    try:
        if moretext.lower().replace('!','').strip() in ['hi', 'hello']:
            return respond(channel, "Hello there!")
        if moretext.lower().replace('!', '').replace('?','') == "are you ready":
            return respond(channel, "I was born ready!")
        if moretext.lower().replace('!', '').replace('?','') == "ping":
            return dm_to_user(channel, user, "Pong!")
        identifier, rest = head(moretext)
        lidentifier = identifier.lower()
        if lidentifier == 'teach':
            return learn_new_instruction(channel, rest)
        if lidentifier == 'reteach':
            return learn_new_instruction(channel, rest, override=True)
        tasks = get_tasks()
        if lidentifier == 'help':
            return show_help_messsage(channel, tasks)
        if any(lidentifier.startswith(x) for x in ['thank', '10x']):
            respond(channel, "You're welcome, <@{}>!".format(user))
            return 
        if identifier in tasks:
            logging.info("{} recognized as task".format(identifier))
            return gevent.spawn(trigger_known_instruction, channel, user, tasks[identifier], rest, identifier)
        if lidentifier in tasks:
            logging.info("{} recognized as task".format(lidentifier))
            return gevent.spawn(trigger_known_instruction, channel, user, tasks[lidentifier], rest, identifier)
        # unknown command, use crunchable to understand what the user wants
        logging.info("unrecognized identifier {}".format(moretext))
        gevent.spawn(handle_unrecognized_commmand, channel, user, moretext)
    except ValueError:
        return respond(channel, "Sorry, I'm not feeling so well... can you send someone to check in on me, please?")


def process_user_info(data):
    global user_name
    global user_id
    if 'user' in data:
        user_name = data['user']
    if 'user_id' in data:
        user_id = data['user_id']

def catch_all(data):
    return # disabled for now
    global config
    print(config)
    print("[ALL]", data)
