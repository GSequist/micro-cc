import tiktoken
import json
import os

#############################################

tokenizer = tiktoken.get_encoding("cl100k_base")

############################################

WORK_FOLDER = os.path.join(os.getcwd(), "workspace/")

###########################################


def extract_json_(text):
    """Extract first valid JSON (object or array) from text and return as parsed object"""
    i = 0
    while i < len(text):
        if text[i] in ["{", "["]:
            open_char = text[i]
            close_char = "}" if open_char == "{" else "]"
            bracket_count = 1
            start = i
            i += 1

            while i < len(text) and bracket_count > 0:
                if text[i] == open_char:
                    bracket_count += 1
                elif text[i] == close_char:
                    bracket_count -= 1
                i += 1

            if bracket_count == 0:
                json_candidate = text[start:i]
                try:
                    parsed = json.loads(json_candidate)
                    return parsed  # Return parsed object/array
                except:
                    pass
        else:
            i += 1
    return {}  # Return empty dict, not string


####################################################
