"""图片缓存服务模块"""

import asyncio
from pathlib import Path
from typing import Optional
from curl_cffi.requests import AsyncSession

from app.core.config import setting
from app.core.logger import logger
from app.services.grok.statsig import get_dynamic_headers


class ImageCacheService:
    """图片缓存服务"""

    def __init__(self):
        self.cache_dir = Path("data/temp")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _get_cache_filename(image_path: str) -> str:
        """将图片路径转换为缓存文件名"""
        # 移除开头的斜杠并替换所有斜杠为短横线
        filename = image_path.lstrip('/').replace('/', '-')
        return filename

    def _get_cache_path(self, image_path: str) -> Path:
        """获取缓存文件的完整路径"""
        filename = self._get_cache_filename(image_path)
        return self.cache_dir / filename

    async def download_image(self, image_path: str, auth_token: str) -> Optional[Path]:
        """下载并缓存图片

        Args:
            image_path: 图片路径，如 /users/xxx/generated/xxx/image.jpg
            auth_token: 认证令牌

        Returns:
            缓存文件路径，下载失败返回 None
        """
        cache_path = self._get_cache_path(image_path)

        if cache_path.exists():
            logger.debug(f"[ImageCache] 图片已缓存: {cache_path}")
            return cache_path

        image_url = f"https://assets.grok.com{image_path}"

        try:
            # 构建 Cookie
            cf_clearance = setting.grok_config.get("cf_clearance", "")
            cookie = f"{auth_token};{cf_clearance}" if cf_clearance else auth_token

            # 构建请求头
            headers = {
                **get_dynamic_headers(pathname=image_path),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-site",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "Referer": "https://grok.com/",
                "Cookie": cookie
            }

            # 代理配置
            proxy_url = setting.grok_config.get("proxy_url")
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

            async with AsyncSession() as session:
                logger.debug(f"[ImageCache] 开始下载图片: {image_url}")
                response = await session.get(
                    image_url,
                    headers=headers,
                    proxies=proxies,
                    timeout=30.0,
                    allow_redirects=True,
                    impersonate="chrome133a"
                )
                response.raise_for_status()

                cache_path.write_bytes(response.content)
                logger.debug(f"[ImageCache] 图片已缓存: {cache_path} ({len(response.content)} bytes)")

                asyncio.create_task(self.cleanup_cache())

                return cache_path

        except Exception as e:
            logger.error(f"[ImageCache] 下载图片失败: {e}")
            return None

    def get_cached_image(self, image_path: str) -> Optional[Path]:
        """获取缓存的图片路径

        Args:
            image_path: 图片路径

        Returns:
            缓存文件路径，不存在返回 None
        """
        cache_path = self._get_cache_path(image_path)
        return cache_path if cache_path.exists() else None

    async def cleanup_cache(self):
        """清理缓存目录，确保不超过配置的大小限制"""
        try:
            # 获取配置的最大缓存大小（MB）
            max_size_mb = setting.global_config.get("temp_max_size_mb", 500)
            max_size_bytes = max_size_mb * 1024 * 1024

            # 获取所有缓存文件及其大小和修改时间
            files = []
            total_size = 0

            for file_path in self.cache_dir.glob("*"):
                if file_path.is_file():
                    size = file_path.stat().st_size
                    mtime = file_path.stat().st_mtime
                    files.append((file_path, size, mtime))
                    total_size += size

            # 如果总大小未超限，无需清理
            if total_size <= max_size_bytes:
                logger.debug(f"[ImageCache] 缓存大小 {total_size / 1024 / 1024:.2f}MB，未超限")
                return

            logger.info(f"[ImageCache] 缓存大小 {total_size / 1024 / 1024:.2f}MB 超过限制 {max_size_mb}MB，开始清理")

            # 按修改时间排序（最旧的在前）
            files.sort(key=lambda x: x[2])

            # 删除最旧的文件直到总大小低于限制
            for file_path, size, _ in files:
                if total_size <= max_size_bytes:
                    break

                file_path.unlink()
                total_size -= size
                logger.debug(f"[ImageCache] 已删除缓存文件: {file_path}")

            logger.info(f"[ImageCache] 缓存清理完成，当前大小 {total_size / 1024 / 1024:.2f}MB")

        except Exception as e:
            logger.error(f"[ImageCache] 清理缓存失败: {e}")


# 全局实例
image_cache_service = ImageCacheService()
