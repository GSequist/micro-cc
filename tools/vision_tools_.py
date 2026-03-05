from utils.helpers import sanitize_and_encode_image_
from models.anthropic import a_model_call
from models.litellm import l_model_call


async def vision(img_path: str, query: str, *, project_dir, end_resp="Anthropic") -> str:
    """Analyze images to extract visual information or answer questions.

    Handles photographs, diagrams, charts, screenshots, and images with text.

    Args:
        img_path: Absolute or relative path to the image file
        query: What to ask about the image
    """
    try:
        encoded_string = sanitize_and_encode_image_(img_path)
        if end_resp == "LiteLLM":
            resp = await l_model_call(input=query, encoded_image=encoded_string)
        else:
            resp = await a_model_call(input=query, encoded_image=encoded_string)
        return resp.content[0].text

    except Exception as e:
        return f"Vision error: {str(e)}"
