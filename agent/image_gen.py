"""Image generation pipeline — Gemini API → PIL → Supabase Storage.

Generates images from text prompts using Gemini's image generation model.
All processing happens in-memory (no temp files).
"""

import io
import uuid

from google import genai
from google.genai import types
from PIL import Image

import config
from agent.logging import get_logger

logger = get_logger("agent.image_gen")


async def generate_image(
    prompt: str, space_id: str, supabase_client
) -> dict:
    """Generate an image from a text prompt and upload to Supabase Storage.

    Pipeline:
    1. Call Gemini with the prompt (image generation mode)
    2. Extract raw image bytes from the response
    3. Open with PIL to get dimensions and ensure valid PNG
    4. Upload to Supabase Storage at images/{space_id}/{uuid}.png
    5. Return metadata dict

    Args:
        prompt: Text description of the image to generate
        space_id: Space ID for storage path namespacing
        supabase_client: Async Supabase client with storage access

    Returns:
        dict with keys: storage_path, public_url, width, height

    Raises:
        RuntimeError or API errors if generation fails
    """
    # 1. Call Gemini
    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.0-flash-exp",
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )

    # 2. Extract image bytes
    image_data = response.candidates[0].content.parts[0].inline_data.data

    # 3. PIL — validate and get dimensions
    img = Image.open(io.BytesIO(image_data))
    width, height = img.size

    # Save as PNG to a buffer
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()

    # 4. Upload to Supabase Storage
    filename = f"{uuid.uuid4()}.png"
    storage_path = f"{space_id}/{filename}"
    bucket = supabase_client.storage.from_("images")
    bucket.upload(
        storage_path,
        png_bytes,
        file_options={"content-type": "image/png"},
    )

    # 5. Get public URL
    public_url = bucket.get_public_url(storage_path)

    logger.info(
        "image_generated",
        extra={
            "space_id": space_id,
            "storage_path": storage_path,
            "width": width,
            "height": height,
        },
    )

    return {
        "storage_path": storage_path,
        "public_url": public_url,
        "width": width,
        "height": height,
    }
