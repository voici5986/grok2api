"""图片服务API路由"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.logger import logger
from app.services.grok.image_cache import image_cache_service


router = APIRouter()


@router.get("/images/{img_path:path}")
async def get_image(img_path: str):
    """获取缓存的图片

    Args:
        img_path: 图片路径，格式如 users-xxx-generated-xxx-image.jpg

    Returns:
        图片文件响应
    """
    try:
        # 将路径转换回原始格式（短横线转斜杠）
        original_path = "/" + img_path.replace('-', '/')

        # 检查缓存是否存在
        cache_path = image_cache_service.get_cached_image(original_path)

        if cache_path and cache_path.exists():
            logger.debug(f"[ImageAPI] 返回缓存图片: {cache_path}")
            return FileResponse(
                path=str(cache_path),
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "Access-Control-Allow-Origin": "*"
                }
            )

        # 图片不存在
        logger.warning(f"[ImageAPI] 图片未找到: {original_path}")
        raise HTTPException(status_code=404, detail="Image not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ImageAPI] 获取图片失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
