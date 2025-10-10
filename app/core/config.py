"""配置管理器"""

import toml
from pathlib import Path
from typing import Dict, Any


class ConfigManager:
    """配置管理器"""

    def __init__(self) -> None:
        """初始化"""

        # 加载环境变量
        self.config_path: Path = Path(__file__).parents[2] / "data" / "setting.toml"
        self.global_config: Dict[str, Any] = self.load("global")
        self.grok_config: Dict[str, Any] = self.load("grok")

    def load(self, section: str) -> Dict[str, Any]:
        """配置加载器"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return toml.load(f)[section]
        except Exception as e:
            raise Exception(f"[Setting] 配置加载失败: {e}")

# 全局设置
setting = ConfigManager()

if __name__ == "__main__":
    print(setting.global_config)
    print(setting.grok_config)