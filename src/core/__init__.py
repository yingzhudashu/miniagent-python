"""Mini Agent Python — Core engine module."""

from src.core.config import (
    MODEL_PROFILES,
    get_default_agent_config,
    get_default_model_config,
    apply_model_profile,
    merge_agent_config,
)
from src.core.logger import append_log, truncate, get_logger
from src.core.output_manager import OutputManager
from src.core.monitor import DefaultToolMonitor
from src.core.registry import DefaultToolRegistry
from src.core.loop_detector import LoopDetector
from src.core.keyword_index import KeywordIndex, extract_keywords
from src.core.instance_manager import InstanceManager
from src.core.process_tracker import (
    cleanup_all_processes,
    create_tracked_subprocess,
    register_process,
    deregister_process,
    get_tracked_count,
    get_active_processes,
)
from src.core.executor import AGENT_NAME, AGENT_IDENTITY
from src.core.planner import AGENT_NAME as PLANNER_AGENT_NAME

__all__ = [
    "MODEL_PROFILES",
    "get_default_agent_config",
    "get_default_model_config",
    "apply_model_profile",
    "merge_agent_config",
    "get_logger",
    "append_log",
    "truncate",
    "OutputManager",
    "DefaultToolMonitor",
    "DefaultToolRegistry",
    "LoopDetector",
    "KeywordIndex",
    "extract_keywords",
    "InstanceManager",
    "cleanup_all_processes",
    "create_tracked_subprocess",
    "register_process",
    "deregister_process",
    "get_tracked_count",
    "get_active_processes",
    "AGENT_NAME",
    "AGENT_IDENTITY",
    "PLANNER_AGENT_NAME",
]
