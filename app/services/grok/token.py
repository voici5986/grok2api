"""Grok Token 管理器模块"""

import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from curl_cffi import requests as curl_requests

from app.models.grok_models import TokenType, Models
from app.core.exception import GrokApiException
from app.core.logger import logger
from app.core.config import setting
from app.services.grok.statsig import get_dynamic_headers

# 常量定义
RATE_LIMIT_ENDPOINT = "https://grok.com/rest/rate-limits"
REQUEST_TIMEOUT = 30
IMPERSONATE_BROWSER = "chrome133a"
MAX_FAILURE_COUNT = 3
TOKEN_INVALID_CODE = 401  # SSO Token失效
STATSIG_INVALID_CODE = 403  # x-statsig-id失效


class GrokTokenManager:
    """
    Grok Token管理器
    
    单例模式的Token管理器，负责：
    - Token文件的读写操作
    - Token负载均衡
    - Token状态管理
    - 支持普通Token和Super Token
    """
    
    _instance: Optional['GrokTokenManager'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'GrokTokenManager':
        """单例模式实现"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化Token管理器"""
        if hasattr(self, '_initialized'):
            return

        self.token_file = Path(__file__).parents[3] / "data" / "token.json"
        self._file_lock = threading.Lock()
        self.token_file.parent.mkdir(parents=True, exist_ok=True)

        self._load_token_data()
        self._initialized = True

        logger.debug(f"[Token] 管理器初始化完成，文件: {self.token_file}")

    def _load_token_data(self) -> None:
        """加载Token数据"""
        default_data = {
            TokenType.NORMAL.value: {},
            TokenType.SUPER.value: {}
        }

        try:
            if self.token_file.exists():
                with open(self.token_file, "r", encoding="utf-8") as f:
                    self.token_data = json.load(f)
            else:
                self.token_data = default_data
                self._save_token_data()
                logger.debug("[Token] 创建新的Token数据文件")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"[Token] 加载Token数据失败: {str(e)}")
            self.token_data = default_data

    def _save_token_data(self) -> None:
        """保存Token数据到文件"""
        try:
            with self._file_lock:
                with open(self.token_file, "w", encoding="utf-8") as f:
                    json.dump(self.token_data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"[Token] 保存Token数据失败: {str(e)}")
            raise GrokApiException(
                f"Token数据保存失败: {str(e)}",
                "TOKEN_SAVE_ERROR",
                {"file_path": str(self.token_file)}
            )

    @staticmethod
    def _extract_sso_value(auth_token: str) -> Optional[str]:
        """从认证令牌中提取SSO值"""
        if "sso=" in auth_token:
            return auth_token.split("sso=")[1].split(";")[0]
        logger.warning("[Token] 无法从认证令牌中提取SSO值")
        return None

    def _find_token_data(self, sso_value: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """查找Token数据，返回(token_type, token_data)"""
        for token_type in [TokenType.NORMAL.value, TokenType.SUPER.value]:
            if sso_value in self.token_data[token_type]:
                return token_type, self.token_data[token_type][sso_value]
        return None, None

    @staticmethod
    def _get_proxy_config() -> Dict[str, str]:
        """获取代理配置"""
        proxy_url = setting.grok_config.get("proxy_url", "")
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}
        return {}

    def add_token(self, tokens: list[str], token_type: TokenType) -> None:
        """
        添加Token到管理器
        
        Args:
            tokens: Token列表
            token_type: Token类型
        """
        if not tokens:
            logger.debug("[Token] 尝试添加空的Token列表")
            return

        added_count = 0
        for token in tokens:
            if not token or not token.strip():
                logger.debug("[Token] 跳过空的Token")
                continue
                
            self.token_data[token_type.value][token] = {
                "createdTime": int(time.time() * 1000),
                "remainingQueries": -1,
                "heavyremainingQueries": -1,
                "status": "active",
                "failedCount": 0,
                "lastFailureTime": None,
                "lastFailureReason": None
            }
            added_count += 1
            
        self._save_token_data()
        logger.info(f"[Token] 成功添加 {added_count} 个 {token_type.value} Token")
    
    def delete_token(self, tokens: list[str], token_type: TokenType) -> None:
        """
        删除指定的Token
        
        Args:
            tokens: 要删除的Token列表
            token_type: Token类型
        """
        if not tokens:
            logger.debug("[Token] 尝试删除空的Token列表")
            return

        deleted_count = 0
        for token in tokens:
            if token in self.token_data[token_type.value]:
                del self.token_data[token_type.value][token]
                deleted_count += 1
            else:
                logger.debug(f"[Token] Token不存在: {token[:10]}...")

        self._save_token_data()
        logger.info(f"[Token] 成功删除 {deleted_count} 个 {token_type.value} Token")
    
    def get_all_token(self) -> Dict[str, Any]:
        """获取所有Token数据"""
        return self.token_data.copy()
    
    def get_token(self, model: str) -> str:
        """
        获取指定模型的Token
        
        Args:
            model: 模型名称
            
        Returns:
            str: 格式化的Cookie字符串
        """
        jwt_token = self.token_balancer(model)
        return f"sso-rw={jwt_token};sso={jwt_token}"
    
    def token_balancer(self, model: str) -> str:
        """
        Token负载均衡器 - 根据模型类型和剩余次数选择最优Token
        
        选择策略：
        1. 跳过 status=expired 的 token
        2. 跳过 remaining=0 的 token（已限流）
        3. 优先选择 remaining=-1 的 token（未使用）
        4. 如果没有 -1，选择 remaining 最大的 token（剩余最多）
        """
        def select_best_token(tokens_dict):
            """从 token 字典中选择最佳 token"""
            unused_tokens = []  # remaining = -1 的 token
            used_tokens = []    # remaining > 0 的 token
            
            for token_key, token_data in tokens_dict.items():
                # 跳过已失效的Token
                if token_data.get("status") == "expired":
                    continue
                
                remaining = int(token_data.get(remaining_field, -1))
                
                # 跳过已限流的 token (remaining = 0)
                if remaining == 0:
                    continue
                
                # 分类存储
                if remaining == -1:
                    unused_tokens.append(token_key)
                elif remaining > 0:
                    used_tokens.append((token_key, remaining))
            
            # 优先返回未使用的 token
            if unused_tokens:
                return unused_tokens[0], -1
            
            # 否则返回剩余次数最多的 token
            if used_tokens:
                used_tokens.sort(key=lambda x: x[1], reverse=True)
                return used_tokens[0][0], used_tokens[0][1]
            
            return None, None
        
        max_token_key = None
        max_remaining = None

        if model == "grok-4-heavy":
            # grok-4-heavy 只能使用Super Token，且使用 heavyremainingQueries
            remaining_field = "heavyremainingQueries"
            max_token_key, max_remaining = select_best_token(self.token_data[TokenType.SUPER.value])
        else:
            # 其他模型使用 remainingQueries（对应API的remainingTokens）
            remaining_field = "remainingQueries"
            
            # 优先使用普通Token
            max_token_key, max_remaining = select_best_token(self.token_data[TokenType.NORMAL.value])
            
            # 如果普通Token没有可用的，尝试使用Super Token
            if max_token_key is None:
                max_token_key, max_remaining = select_best_token(self.token_data[TokenType.SUPER.value])

        if max_token_key is None:
            raise GrokApiException(
                f"没有可用Token用于模型 {model}",
                "NO_AVAILABLE_TOKEN",
                {
                    "model": model,
                    "normal_count": len(self.token_data[TokenType.NORMAL.value]),
                    "super_count": len(self.token_data[TokenType.SUPER.value])
                }
            )

        status_text = "未使用" if max_remaining == -1 else f"剩余{max_remaining}次"
        logger.debug(f"[Token] 为模型 {model} 选择Token ({status_text})")
        return max_token_key
    
    def check_limits(self, auth_token: str, model: str) -> Optional[Dict[str, Any]]:
        """检查并更新模型速率限制"""
        try:
            rate_limit_model_name = Models.to_rate_limit(model)
            logger.debug(f"[Token] 检查模型 {model} (接口模型: {rate_limit_model_name}) 的速率限制")

            # 准备请求
            payload = {"requestKind": "DEFAULT", "modelName": rate_limit_model_name}
            cf_clearance = setting.grok_config.get("cf_clearance", "")
            cookie = f"{auth_token};{cf_clearance}" if cf_clearance else auth_token

            headers = get_dynamic_headers("/rest/rate-limits")
            headers["Cookie"] = cookie

            # 发送请求
            response = curl_requests.post(
                RATE_LIMIT_ENDPOINT,
                headers=headers,
                json=payload,
                impersonate=IMPERSONATE_BROWSER,
                timeout=REQUEST_TIMEOUT,
                **self._get_proxy_config()
            )

            if response.status_code == 200:
                rate_limit_data = response.json()
                logger.debug(f"[Token] 成功获取速率限制信息")
                self._save_to_storage(auth_token, model, rate_limit_data)
                return rate_limit_data
            else:
                logger.warning(f"[Token] 获取速率限制失败，状态码: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"[Token] 检查速率限制时发生错误: {str(e)}")
            return None

    @staticmethod
    def get_remaining(data: Dict[str, Any], model: str) -> int:
        """获取剩余Token数量"""
        try:
            if model == "grok-4-heavy":
                return data.get("remainingQueries", 0)
            else:
                return data.get("remainingTokens", 0)
        except Exception as e:
            logger.error(f"[Token] 解析剩余Token数量时发生错误: {str(e)}")
            return 0
    
    def _save_to_storage(self, auth_token: str, model: str, data: Dict[str, Any]) -> None:
        """存储速率限制信息到token.json"""
        try:
            sso_value = self._extract_sso_value(auth_token)
            if not sso_value:
                return

            # 根据模型类型获取剩余数量
            if model == "grok-4-heavy":
                normal_remaining = -1
                heavy_remaining = data.get("remainingQueries", -1)
            else:
                normal_remaining = data.get("remainingTokens", -1)
                heavy_remaining = -1

            self.update_limits(sso_value, normal_remaining, heavy_remaining)
            logger.info(f"[Token] 已更新限制: sso={sso_value[:10]}..., 通用={normal_remaining}, heavy={heavy_remaining}")

        except Exception as e:
            logger.error(f"[Token] 存储速率限制信息时发生错误: {str(e)}")
    
    def update_limits(self, sso_value: str, normal: int = None, heavy: int = None) -> None:
        """更新Token限制信息"""
        try:
            for token_type in [TokenType.NORMAL.value, TokenType.SUPER.value]:
                if sso_value in self.token_data[token_type]:
                    if normal is not None:
                        self.token_data[token_type][sso_value]["remainingQueries"] = normal
                    if heavy is not None:
                        self.token_data[token_type][sso_value]["heavyremainingQueries"] = heavy
                    
                    self._save_token_data()
                    logger.info(f"[Token] 已更新Token {sso_value[:10]}... 的限制信息")
                    return

            logger.warning(f"[Token] 未找到SSO值为 {sso_value[:10]}... 的Token")

        except Exception as e:
            logger.error(f"[Token] 更新Token限制时发生错误: {str(e)}")
    
    def record_token_failure(self, auth_token: str, status_code: int, error_message: str) -> None:
        """记录Token失败信息

        错误码说明：
        - 401: SSO Token失效，会标记Token为expired
        - 403: x-statsig-id失效，不影响Token状态

        Args:
            auth_token: 完整的认证Token (格式: sso-rw=xxx;sso=xxx)
            status_code: HTTP状态码
            error_message: 错误信息
        """
        try:
            # 403错误是x-statsig-id失效，不是Token问题
            if status_code == STATSIG_INVALID_CODE:
                logger.warning(f"[Token] x-statsig-id失效 (403)，需要更新配置文件中的x_statsig_id")
                return

            sso_value = self._extract_sso_value(auth_token)
            if not sso_value:
                return

            _, token_data = self._find_token_data(sso_value)
            if not token_data:
                logger.warning(f"[Token] 未找到SSO值为 {sso_value[:10]}... 的Token")
                return

            # 更新失败计数
            token_data["failedCount"] = token_data.get("failedCount", 0) + 1
            token_data["lastFailureTime"] = int(time.time() * 1000)
            token_data["lastFailureReason"] = f"{status_code}: {error_message}"

            logger.warning(
                f"[Token] Token {sso_value[:10]}... 失败 (状态码: {status_code}), "
                f"失败次数: {token_data['failedCount']}/{MAX_FAILURE_COUNT}, "
                f"原因: {error_message}"
            )

            # 只有401错误（SSO Token失效）且失败次数达到上限时，标记为失效
            if status_code == TOKEN_INVALID_CODE and token_data["failedCount"] >= MAX_FAILURE_COUNT:
                token_data["status"] = "expired"
                logger.error(
                    f"[Token] SSO Token {sso_value[:10]}... 已被标记为失效 "
                    f"(连续401错误{token_data['failedCount']}次)"
                )

            self._save_token_data()

        except Exception as e:
            logger.error(f"[Token] 记录Token失败信息时发生错误: {str(e)}")
    
    def reset_token_failure(self, auth_token: str) -> None:
        """重置Token失败计数

        当Token成功完成请求时调用此方法，用于清除失败记录。

        Args:
            auth_token: 完整的认证Token (格式: sso-rw=xxx;sso=xxx)
        """
        try:
            sso_value = self._extract_sso_value(auth_token)
            if not sso_value:
                return

            _, token_data = self._find_token_data(sso_value)
            if not token_data:
                logger.warning(f"[Token] 未找到SSO值为 {sso_value[:10]}... 的Token")
                return

            # 只有在有失败记录时才重置并保存
            if token_data.get("failedCount", 0) > 0:
                token_data["failedCount"] = 0
                token_data["lastFailureTime"] = None
                token_data["lastFailureReason"] = None

                self._save_token_data()
                logger.info(f"[Token] Token {sso_value[:10]}... 失败计数已重置")

        except Exception as e:
            logger.error(f"[Token] 重置Token失败计数时发生错误: {str(e)}")


# 全局Token管理器实例
token_manager = GrokTokenManager()
