"""FastAPI应用主入口"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.core.logger import logger
from app.core.exception import register_exception_handlers
from app.api.v1.chat import router as chat_router
from app.api.v1.models import router as models_router
from app.api.v1.images import router as images_router
from app.api.admin.manage import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.debug("[Web2API] 应用启动成功")
    yield
    logger.info("[Web2API] 应用关闭成功")


# 初始化日志
logger.info("[Web2API] 应用正在启动...")

# 创建FastAPI应用
app = FastAPI(
    title="Web2API",
    description="Web服务API",
    version="1.0.0",
    lifespan=lifespan
)

# 注册全局异常处理器
register_exception_handlers(app)

# 注册路由
app.include_router(chat_router, prefix="/v1")
app.include_router(models_router, prefix="/v1")
app.include_router(images_router)
app.include_router(admin_router)

# 挂载静态文件（注意：这个应该在API路由之后，避免拦截API请求）
app.mount("/static", StaticFiles(directory="app/template"), name="template")

@app.get("/")
async def root():
    """根路径"""
    return {"message": "Welcome to Web2API"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
