"""配置加载：YAML + .env，支持 ${ENV_VAR} 展开。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    """递归展开字符串里的 ${ENV_VAR} 引用。未设置的变量保留原样。"""
    if isinstance(value, str):
        def _sub(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))
        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class ModelConfig:
    default: str = "claude-sonnet-4-20250514"
    fallback: str = "deepseek-chat"


@dataclass
class ShellToolConfig:
    allowed_commands: list[str] = field(default_factory=list)
    timeout: int = 30


@dataclass
class CodeToolConfig:
    workspace_dirs: list[str] = field(default_factory=list)


@dataclass
class OntologyToolConfig:
    api_url: str = "http://myontology-api:8003"


@dataclass
class ToolsConfig:
    enabled: list[str] = field(default_factory=list)
    shell: ShellToolConfig = field(default_factory=ShellToolConfig)
    code: CodeToolConfig = field(default_factory=CodeToolConfig)
    ontology: OntologyToolConfig = field(default_factory=OntologyToolConfig)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecayConfig:
    default_half_life_days: int = 30
    fact_half_life_days: int = 180
    preference_half_life_days: int = 365


@dataclass
class MemoryConfig:
    decay: DecayConfig = field(default_factory=DecayConfig)
    consolidation_interval: int = 10


@dataclass
class TelegramConfig:
    token: str = ""
    polling_mode: bool = True


@dataclass
class GatewayConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


@dataclass
class Config:
    model: ModelConfig
    api_keys: dict[str, str]
    tools: ToolsConfig
    memory: MemoryConfig
    gateway: GatewayConfig
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        """加载 .env 再加载 YAML，展开环境变量引用。"""
        load_dotenv(override=False)

        cfg_path = Path(path)
        if not cfg_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {cfg_path}")

        with cfg_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        data = _expand_env(data)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        model_data = data.get("model", {}) or {}
        model = ModelConfig(
            default=model_data.get("default", ModelConfig.default),
            fallback=model_data.get("fallback", ModelConfig.fallback),
        )

        api_keys = dict(data.get("api_keys", {}) or {})

        tools_data = data.get("tools", {}) or {}
        shell_data = tools_data.get("shell", {}) or {}
        code_data = tools_data.get("code", {}) or {}
        ont_data = tools_data.get("ontology", {}) or {}
        tools = ToolsConfig(
            enabled=list(tools_data.get("enabled", []) or []),
            shell=ShellToolConfig(
                allowed_commands=list(shell_data.get("allowed_commands", []) or []),
                timeout=int(shell_data.get("timeout", 30)),
            ),
            code=CodeToolConfig(
                workspace_dirs=list(code_data.get("workspace_dirs", []) or []),
            ),
            ontology=OntologyToolConfig(
                api_url=ont_data.get("api_url", OntologyToolConfig.api_url),
            ),
            raw=tools_data,
        )

        mem_data = data.get("memory", {}) or {}
        decay_data = mem_data.get("decay", {}) or {}
        memory = MemoryConfig(
            decay=DecayConfig(
                default_half_life_days=int(
                    decay_data.get("default_half_life_days", 30)
                ),
                fact_half_life_days=int(decay_data.get("fact_half_life_days", 180)),
                preference_half_life_days=int(
                    decay_data.get("preference_half_life_days", 365)
                ),
            ),
            consolidation_interval=int(mem_data.get("consolidation_interval", 10)),
        )

        gw_data = data.get("gateway", {}) or {}
        tg_data = gw_data.get("telegram", {}) or {}
        gateway = GatewayConfig(
            telegram=TelegramConfig(
                token=tg_data.get("token", "") or "",
                polling_mode=bool(tg_data.get("polling_mode", True)),
            ),
        )

        return cls(
            model=model,
            api_keys=api_keys,
            tools=tools,
            memory=memory,
            gateway=gateway,
            raw=data,
        )

    def get_api_key(self, provider: str) -> str | None:
        """获取某个 provider 的 API key。未展开的 ${...} 视为缺失。"""
        val = self.api_keys.get(provider)
        if not val:
            return None
        if val.startswith("${") and val.endswith("}"):
            return None
        return val
