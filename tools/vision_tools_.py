from utils.helpers import sanitize_and_encode_image_
from models.anthropic import model_call


async def vision(img_path: str, query: str, *, project_dir) -> str:
    """Analyze images to extract visual information or answer questions.

    Handles photographs, diagrams, charts, screenshots, and images with text.

    Args:
        img_path: Image filename in user workspace
        query: What to ask about the image
    """
    try:
        encoded_string = sanitize_and_encode_image_(img_path)
        vision_response = await model_call(
            input=query,
            encoded_image=encoded_string,
        )
        return vision_response.response[0].text

    except Exception as e:
        return f"Vision error: {str(e)}"
