from PIL import Image
import tiktoken
import base64
import json
import os
import io

############################################################################################################
##tokenizer

tokenizer = tiktoken.get_encoding("cl100k_base")

############################################################################################################
##dirs

WORK_FOLDER = os.path.join(os.getcwd(), "workspace/")

############################################################################################################


def sanitize_and_encode_image_(img_data):
    try:
        if isinstance(img_data, str) and os.path.exists(img_data):
            with Image.open(img_data) as img:
                img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG")
                return base64.b64encode(buffer.getvalue()).decode("utf-8")
        else:
            with Image.open(io.BytesIO(img_data)) as img:
                img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG")
                return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"Image encoding error: {e}")
        return None


###################################################################################################################


def extract_json_robust(text):
    """util to extract jsons"""
    results = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            bracket_count = 1
            start = i
            i += 1

            while i < len(text) and bracket_count > 0:
                if text[i] == "{":
                    bracket_count += 1
                elif text[i] == "}":
                    bracket_count -= 1
                i += 1

            if bracket_count == 0:
                json_candidate = text[start:i]
                try:
                    parsed = json.loads(json_candidate)
                    results.append(parsed)
                except:
                    pass
        else:
            i += 1
    return results


#################################

