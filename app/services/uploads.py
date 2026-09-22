"""安全处理用户上传的图片。"""
from pathlib import Path

from fastapi import HTTPException, UploadFile

MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _image_extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


async def read_image_upload(file: UploadFile) -> tuple[str, bytes]:
    declared_ext = Path(file.filename or "").suffix.lower()
    if declared_ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(422, "仅支持 PNG、JPG、JPEG 或 WebP 图片")

    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(422, "图片超过 2MB")

    actual_ext = _image_extension(data)
    if actual_ext is None:
        raise HTTPException(422, "上传内容不是有效图片")
    if actual_ext != declared_ext and not (actual_ext == ".jpg" and declared_ext == ".jpeg"):
        raise HTTPException(422, "图片扩展名与实际内容不匹配")
    return actual_ext, data
