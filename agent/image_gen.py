"""Image generation pipeline — Gemini API → PIL → Supabase Storage.

Generates images from text prompts using Gemini's image generation model.
All processing happens in-memory (no temp files).
The sync Gemini SDK call is wrapped in asyncio.to_thread() so that
asyncio.gather in loop.py actually runs multiple generations concurrently.
"""

import asyncio
import io
import uuid

from google import genai
from google.genai import types
from PIL import Image

import config
from agent.logging import get_logger

logger = get_logger("agent.image_gen")


def _generate_image_sync(prompt: str, api_key: str) -> tuple[bytes, int, int]:
    """Sync: call Gemini, validate with PIL, return (png_bytes, width, height)."""
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )

    image_data = response.candidates[0].content.parts[0].inline_data.data
    img = Image.open(io.BytesIO(image_data))
    width, height = img.size

    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    return png_buf.getvalue(), width, height


async def generate_image(
    prompt: str, space_id: str, supabase_client
) -> dict:
    """Generate an image from a text prompt and upload to Supabase Storage.

    Pipeline:
    1. Call Gemini via asyncio.to_thread (non-blocking)
    2. Upload PNG bytes to Supabase Storage
    3. Return metadata dict

    Args:
        prompt: Text description of the image to generate
        space_id: Space ID for storage path namespacing
        supabase_client: Async Supabase client with storage access

    Returns:
        dict with keys: storage_path, public_url, width, height

    Raises:
        RuntimeError or API errors if generation fails
    """
    # 1. Run sync Gemini + PIL in a thread so gather() parallelizes
    png_bytes, width, height = await asyncio.to_thread(
        _generate_image_sync, prompt, config.GOOGLE_API_KEY
    )

    # 2. Upload to Supabase Storage (async)
    filename = f"{uuid.uuid4()}.png"
    storage_path = f"{space_id}/{filename}"
    bucket = supabase_client.storage.from_("images")
    await bucket.upload(
        storage_path,
        png_bytes,
        file_options={"content-type": "image/png"},
    )

    # 3. Get public URL
    public_url = await bucket.get_public_url(storage_path)

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
