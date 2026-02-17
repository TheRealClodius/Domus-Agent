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

    async def upload(self, path: str, data: bytes, file_options=None):
        self.uploaded[path] = data
        return MagicMock()  # upload response

    async def get_public_url(self, path: str) -> str:
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


# ---------------------------------------------------------------------------
# _generate_image_sync and asyncio.to_thread tests
# ---------------------------------------------------------------------------


class TestGenerateImageSync:
    """_generate_image_sync extracts the sync Gemini+PIL work."""

    def _make_gemini_response(self, image_bytes: bytes):
        inline_data = MagicMock()
        inline_data.data = image_bytes
        inline_data.mime_type = "image/png"
        part = MagicMock()
        part.inline_data = inline_data
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]
        return response

    def _make_png_bytes(self, width=64, height=64) -> bytes:
        from PIL import Image
        img = Image.new("RGB", (width, height), color=(0, 255, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_returns_png_bytes_and_dimensions(self):
        """_generate_image_sync returns (png_bytes, width, height)."""
        png_bytes = self._make_png_bytes(100, 200)
        gemini_response = self._make_gemini_response(png_bytes)

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = gemini_response

        with patch("agent.image_gen.genai.Client", return_value=mock_client):
            from agent.image_gen import _generate_image_sync
            result = _generate_image_sync("test prompt", "test-key")

        assert isinstance(result, tuple)
        assert len(result) == 3
        png_data, w, h = result
        assert isinstance(png_data, bytes)
        assert w == 100
        assert h == 200

    def test_output_is_valid_png(self):
        """The returned bytes should be a valid PNG image."""
        from PIL import Image

        png_bytes = self._make_png_bytes(50, 50)
        gemini_response = self._make_gemini_response(png_bytes)

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = gemini_response

        with patch("agent.image_gen.genai.Client", return_value=mock_client):
            from agent.image_gen import _generate_image_sync
            png_data, _, _ = _generate_image_sync("test", "key")

        img = Image.open(io.BytesIO(png_data))
        assert img.format == "PNG"


class TestGenerateImageUsesToThread:
    """generate_image should call asyncio.to_thread with _generate_image_sync."""

    def _make_png_bytes(self, width=64, height=64) -> bytes:
        from PIL import Image
        img = Image.new("RGB", (width, height), color=(0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @patch("agent.image_gen.config")
    @patch("agent.image_gen.asyncio.to_thread", new_callable=AsyncMock)
    async def test_calls_to_thread_with_sync_function(self, mock_to_thread, mock_config):
        """generate_image should await asyncio.to_thread(_generate_image_sync, ...)."""
        mock_config.GOOGLE_API_KEY = "test-key"
        png_bytes = self._make_png_bytes()
        mock_to_thread.return_value = (png_bytes, 64, 64)

        client = MockSupabaseWithStorage()

        from agent.image_gen import generate_image, _generate_image_sync
        result = await generate_image("a cat", TEST_SPACE_ID, client)

        mock_to_thread.assert_called_once()
        call_args = mock_to_thread.call_args
        assert call_args[0][0] is _generate_image_sync
        assert call_args[0][1] == "a cat"
        assert call_args[0][2] == "test-key"

    @patch("agent.image_gen.config")
    @patch("agent.image_gen.asyncio.to_thread", new_callable=AsyncMock)
    async def test_supabase_upload_happens_after_thread(self, mock_to_thread, mock_config):
        """Supabase upload should still happen async after to_thread returns."""
        mock_config.GOOGLE_API_KEY = "test-key"
        png_bytes = self._make_png_bytes()
        mock_to_thread.return_value = (png_bytes, 64, 64)

        client = MockSupabaseWithStorage()

        from agent.image_gen import generate_image
        result = await generate_image("test", TEST_SPACE_ID, client)

        bucket = client.storage.from_("images")
        assert len(bucket.uploaded) == 1
        assert result["width"] == 64
        assert result["height"] == 64
