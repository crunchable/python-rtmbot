import re

BRACES_RE = re.compile("\{(.*?)\}", re.DOTALL)
def expand_braces(text):
    braces = BRACES_RE.findall(text)
    if len(braces) == 0:
        yield text
        return

    brace = braces[0]
    options = brace.split('|')
    for option in options:
        replaced = BRACES_RE.sub(option, text, count=1)
        for sub_opt in expand_braces(replaced):
            yield sub_opt



