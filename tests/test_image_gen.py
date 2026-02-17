"""Tests for agent/image_gen.py — Gemini image generation pipeline."""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_SPACE_ID


# ---------------------------------------------------------------------------
# MockStorage — simulates Supabase Storage upload + public_url
# ---------------------------------------------------------------------------


class MockStorageBucket:
    """Mock for supabase.storage.from_(bucket).upload() and get_public_url()."""

    def __init__(self):
        self.uploaded = {}  # path -> bytes

    def upload(self, path: str, data: bytes, file_options=None):
        self.uploaded[path] = data
        return MagicMock()  # upload response

    def get_public_url(self, path: str) -> str:
        return f"https://test.supabase.co/storage/v1/object/public/images/{path}"


class MockStorage:
    """Mock for supabase.storage."""

    def __init__(self):
        self._buckets: dict[str, MockStorageBucket] = {}

    def from_(self, bucket_name: str) -> MockStorageBucket:
        if bucket_name not in self._buckets:
            self._buckets[bucket_name] = MockStorageBucket()
        return self._buckets[bucket_name]


class MockSupabaseWithStorage:
    """Mock Supabase client with storage support."""

    def __init__(self):
        self.storage = MockStorage()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGenerateImage:
    """generate_image calls Gemini, processes the image, uploads to Storage."""

    def _make_gemini_response(self, image_bytes: bytes):
        """Create a mock Gemini response with inline image data."""
        inline_data = MagicMock()
        inline_data.data = image_bytes
        inline_data.mime_type = "image/png"

        part = MagicMock()
        part.inline_data = inline_data
        part.text = None

        content = MagicMock()
        content.parts = [part]

        candidate = MagicMock()
        candidate.content = content

        response = MagicMock()
        response.candidates = [candidate]
        return response

    def _make_png_bytes(self, width=64, height=64) -> bytes:
        """Create a minimal valid PNG image in memory."""
        from PIL import Image

        img = Image.new("RGB", (width, height), color=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @patch("agent.image_gen.config")
    async def test_generate_image_returns_expected_shape(self, mock_config):
        """Result dict has storage_path, public_url, width, height."""
        mock_config.GOOGLE_API_KEY = "test-key"

        png_bytes = self._make_png_bytes(256, 256)
        gemini_response = self._make_gemini_response(png_bytes)

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.return_value = gemini_response

        client = MockSupabaseWithStorage()

        with patch("agent.image_gen.genai.Client", return_value=mock_genai_client):
            from agent.image_gen import generate_image

            result = await generate_image(
                "a sunset over mountains", TEST_SPACE_ID, client
            )

        assert "storage_path" in result
        assert "public_url" in result
        assert "width" in result
        assert "height" in result
        assert result["width"] == 256
        assert result["height"] == 256

    @patch("agent.image_gen.config")
    async def test_generate_image_uploads_to_storage(self, mock_config):
        """Image bytes should be uploaded to Supabase Storage under images/{space_id}/."""
        mock_config.GOOGLE_API_KEY = "test-key"

        png_bytes = self._make_png_bytes(128, 128)
        gemini_response = self._make_gemini_response(png_bytes)

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.return_value = gemini_response

        client = MockSupabaseWithStorage()

        with patch("agent.image_gen.genai.Client", return_value=mock_genai_client):
            from agent.image_gen import generate_image

            result = await generate_image(
                "a beautiful landscape", TEST_SPACE_ID, client
            )

        bucket = client.storage.from_("images")
        assert len(bucket.uploaded) == 1
        uploaded_path = list(bucket.uploaded.keys())[0]
        assert uploaded_path.startswith(f"{TEST_SPACE_ID}/")
        assert uploaded_path.endswith(".png")

    @patch("agent.image_gen.config")
    async def test_generate_image_public_url_format(self, mock_config):
        """public_url should be a full URL pointing to the uploaded file."""
        mock_config.GOOGLE_API_KEY = "test-key"

        png_bytes = self._make_png_bytes()
        gemini_response = self._make_gemini_response(png_bytes)

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.return_value = gemini_response

        client = MockSupabaseWithStorage()

        with patch("agent.image_gen.genai.Client", return_value=mock_genai_client):
            from agent.image_gen import generate_image

            result = await generate_image("test prompt", TEST_SPACE_ID, client)

        assert result["public_url"].startswith("https://")
        assert "images/" in result["public_url"]

    @patch("agent.image_gen.config")
    async def test_generate_image_calls_gemini_with_prompt(self, mock_config):
        """Gemini should be called with the user's prompt."""
        mock_config.GOOGLE_API_KEY = "test-key"

        png_bytes = self._make_png_bytes()
        gemini_response = self._make_gemini_response(png_bytes)

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.return_value = gemini_response

        client = MockSupabaseWithStorage()

        with patch("agent.image_gen.genai.Client", return_value=mock_genai_client):
            from agent.image_gen import generate_image

            await generate_image("a cat wearing a hat", TEST_SPACE_ID, client)

        call_args = mock_genai_client.models.generate_content.call_args
        # The prompt should be in the contents argument
        assert "a cat wearing a hat" in str(call_args)

    @patch("agent.image_gen.config")
    async def test_generate_image_raises_on_gemini_failure(self, mock_config):
        """If Gemini API fails, the error should propagate."""
        mock_config.GOOGLE_API_KEY = "test-key"

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.side_effect = RuntimeError(
            "Gemini API error"
        )

        client = MockSupabaseWithStorage()

        with patch("agent.image_gen.genai.Client", return_value=mock_genai_client):
            from agent.image_gen import generate_image

            with pytest.raises(RuntimeError, match="Gemini API error"):
                await generate_image("failing prompt", TEST_SPACE_ID, client)

    @patch("agent.image_gen.config")
    async def test_generate_image_storage_path_contains_uuid(self, mock_config):
        """The storage path should contain a UUID filename."""
        import uuid

        mock_config.GOOGLE_API_KEY = "test-key"

        png_bytes = self._make_png_bytes()
        gemini_response = self._make_gemini_response(png_bytes)

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.return_value = gemini_response

        client = MockSupabaseWithStorage()

        with patch("agent.image_gen.genai.Client", return_value=mock_genai_client):
            from agent.image_gen import generate_image

            result = await generate_image("test", TEST_SPACE_ID, client)

        # Extract filename from storage_path (space_id/uuid.png)
        filename = result["storage_path"].split("/")[-1].replace(".png", "")
        # Should be a valid UUID
        uuid.UUID(filename)  # Raises ValueError if not valid
