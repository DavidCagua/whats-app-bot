"""
Media utilities: audio conversion (WebM → OGG) and outbound upload to Supabase.
Used by admin upload-media for voice notes.
"""

import logging
import os
import subprocess
import tempfile
import time
import uuid
from typing import Optional, Tuple


def convert_webm_to_ogg(data: bytes) -> Optional[Tuple[bytes, str]]:
    """
    Convert WebM audio to OGG (Opus) so Twilio/WhatsApp accept it.
    Returns (ogg_bytes, "audio/ogg") or None if ffmpeg fails or is missing.
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fin:
            fin.write(data)
            fin.flush()
            in_path = fin.name
        out_path = in_path.replace(".webm", ".ogg")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", in_path,
                    "-c:a", "libopus", "-b:a", "64k",
                    out_path,
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            with open(out_path, "rb") as f:
                ogg_data = f.read()
            return (ogg_data, "audio/ogg")
        finally:
            try:
                os.unlink(in_path)
            except OSError:
                pass
            try:
                os.unlink(out_path)
            except OSError:
                pass
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        logging.warning("[MEDIA_UTILS] WebM to OGG conversion failed: %s", e)
        return None


def upload_outbound_media_to_supabase(
    file_bytes: bytes, content_type: str, business_id: str
) -> Optional[str]:
    """Upload outbound media (e.g. voice note) to Supabase; return public URL or None."""
    from app.workers.media_job import _extension_from_content_type, _upload_to_supabase

    ext = _extension_from_content_type(content_type)
    if ext == "bin":
        ext = "ogg"
    path = f"outbound/{business_id or 'default'}/{int(time.time())}_{uuid.uuid4().hex[:12]}.{ext}"
    bucket = os.getenv("SUPABASE_BUCKET_MEDIA", "inbound-media")
    return _upload_to_supabase(file_bytes, path, content_type, bucket=bucket)
