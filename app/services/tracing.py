"""
Lightweight tracing abstraction for agent runs.

Provides a simple interface for tracing agent execution with support for
console logging and optional Langfuse integration.
"""

import os
import logging
import hashlib
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv
load_dotenv()


def hash_phone_number(phone: str) -> str:
    """
    Hash a phone number for PII-safe logging.
    
    Args:
        phone: Phone number or WhatsApp ID
        
    Returns:
        SHA256 hash (first 16 chars) of the phone number
    """
    if not phone:
        return "unknown"
    # Remove common formatting characters
    cleaned = phone.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    # Hash and return first 16 characters
    return hashlib.sha256(cleaned.encode()).hexdigest()[:16]


class Tracer(ABC):
    """Abstract base class for tracing agent runs."""
    
    @abstractmethod
    def start_run(self, run_id: str, user_id: str, message_id: Optional[str] = None, 
                  business_id: Optional[str] = None) -> None:
        """
        Start a new agent run trace.
        
        Args:
            run_id: Unique identifier for this run
            user_id: User identifier (will be hashed for PII safety)
            message_id: Optional message ID from webhook
            business_id: Optional business ID
        """
        pass
    
    @abstractmethod
    def log_event(self, run_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """
        Log an event during the run.
        
        Args:
            run_id: Run identifier
            event_type: Type of event (e.g., "tool_call", "llm_call", "error")
            data: Event data dictionary
        """
        pass
    
    @abstractmethod
    def end_run(self, run_id: str, success: bool, error: Optional[str] = None, 
                latency_ms: Optional[float] = None) -> None:
        """
        End the agent run trace.
        
        Args:
            run_id: Run identifier
            success: Whether the run succeeded
            error: Error message if failed
            latency_ms: Total latency in milliseconds
        """
        pass


class ConsoleTracer(Tracer):
    """Console-based tracer that logs to standard logging."""
    
    def __init__(self, log_pii: bool = False):
        """
        Initialize console tracer.
        
        Args:
            log_pii: If True, log raw phone numbers and message text (for DEBUG only)
        """
        self.log_pii = log_pii or (os.getenv("TRACE_LOG_PII", "false").lower() == "true")
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.logger = logging.getLogger("tracer")
    
    def start_run(self, run_id: str, user_id: str, message_id: Optional[str] = None,
                  business_id: Optional[str] = None) -> None:
        """Start a new agent run trace."""
        hashed_user = hash_phone_number(user_id)
        
        run_data = {
            "run_id": run_id,
            "user_id_hash": hashed_user,
            "message_id": message_id,
            "business_id": business_id,
            "start_time": time.time(),
            "events": []
        }
        
        self.runs[run_id] = run_data
        
        log_msg = f"[TRACE] Run started: {run_id} | user={hashed_user}"
        if message_id:
            log_msg += f" | message_id={message_id}"
        if business_id:
            log_msg += f" | business_id={business_id}"
        
        self.logger.info(log_msg)
    
    def log_event(self, run_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """
        Log an event during the run.
        
        Args:
            run_id: Run identifier
            event_type: Type of event
            data: Event data
        """
        if run_id not in self.runs:
            self.logger.warning(f"[TRACE] Event logged for unknown run: {run_id}")
            return
        
        run_data = self.runs[run_id]
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "data": data
        }
        run_data["events"].append(event)
        
        # Log based on event type
        if event_type == "tool_call":
            tool_name = data.get("tool_name", "unknown")
            tool_args = data.get("args", {})
            # Sanitize tool args for logging (remove PII)
            safe_args = self._sanitize_tool_args(tool_args)
            self.logger.info(f"[TRACE] Tool call: {tool_name} | args={safe_args}")
        
        elif event_type == "tool_result":
            tool_name = data.get("tool_name", "unknown")
            success = data.get("success", False)
            error = data.get("error")
            if error:
                self.logger.warning(f"[TRACE] Tool result: {tool_name} | success={success} | error={error}")
            else:
                self.logger.info(f"[TRACE] Tool result: {tool_name} | success={success}")
        
        elif event_type == "llm_call":
            iteration = data.get("iteration", 0)
            has_tool_calls = data.get("has_tool_calls", False)
            self.logger.info(f"[TRACE] LLM call: iteration={iteration} | has_tool_calls={has_tool_calls}")
        
        elif event_type == "error":
            error_msg = data.get("error", "Unknown error")
            self.logger.error(f"[TRACE] Error: {error_msg}")
    
    def _sanitize_tool_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize tool arguments to remove PII."""
        sanitized = {}
        pii_fields = ["whatsapp_id", "wa_id", "phone", "phone_number", "name", "email"]
        
        for key, value in args.items():
            if key in pii_fields:
                if isinstance(value, str):
                    sanitized[key] = hash_phone_number(value) if "phone" in key or "id" in key else "[REDACTED]"
                else:
                    sanitized[key] = "[REDACTED]"
            elif key == "injected_business_context":
                sanitized[key] = "[BUSINESS_CONTEXT]"
            else:
                sanitized[key] = value
        
        return sanitized
    
    def end_run(self, run_id: str, success: bool, error: Optional[str] = None,
                latency_ms: Optional[float] = None) -> None:
        """End the agent run trace."""
        if run_id not in self.runs:
            self.logger.warning(f"[TRACE] End called for unknown run: {run_id}")
            return
        
        run_data = self.runs[run_id]
        
        # Calculate latency if not provided
        if latency_ms is None:
            start_time = run_data.get("start_time", time.time())
            latency_ms = (time.time() - start_time) * 1000
        
        # Count tool calls
        tool_calls = [e for e in run_data["events"] if e["type"] == "tool_call"]
        tool_count = len(tool_calls)
        
        # Check for errors
        errors = [e for e in run_data["events"] if e["type"] == "error"]
        error_count = len(errors)
        
        log_msg = f"[TRACE] Run ended: {run_id} | success={success} | latency={latency_ms:.2f}ms | tools={tool_count} | errors={error_count}"
        if error:
            log_msg += f" | error={error}"
        
        if success:
            self.logger.info(log_msg)
        else:
            self.logger.error(log_msg)
        
        # Clean up old run data (keep last 100 runs)
        if len(self.runs) > 100:
            oldest_runs = sorted(self.runs.items(), key=lambda x: x[1].get("start_time", 0))[:50]
            for old_run_id, _ in oldest_runs:
                del self.runs[old_run_id]


class LangfuseTracer(Tracer):
    """
    Langfuse tracer skeleton.
    
    Only initializes if LANGFUSE_SECRET_KEY is set in environment.
    Does not require langfuse package to be installed unless actually used.
    """
    
    def __init__(self):
        """Initialize Langfuse tracer if configured."""
        self.enabled = False
        self.langfuse_client = None
        
        langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        langfuse_host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        
        if langfuse_secret_key and langfuse_public_key:
            try:
                from langfuse import Langfuse
                self.langfuse_client = Langfuse(
                    secret_key=langfuse_secret_key,
                    public_key=langfuse_public_key,
                    host=langfuse_host
                )
                self.enabled = True
                logging.info("[TRACE] Langfuse tracer enabled")
            except ImportError:
                logging.warning("[TRACE] Langfuse package not installed. Install with: pip install langfuse")
            except Exception as e:
                logging.warning(f"[TRACE] Failed to initialize Langfuse: {e}")
        else:
            logging.debug("[TRACE] Langfuse not configured (missing LANGFUSE_SECRET_KEY or LANGFUSE_PUBLIC_KEY)")
    
    def start_run(self, run_id: str, user_id: str, message_id: Optional[str] = None,
                  business_id: Optional[str] = None) -> None:
        """Start a new agent run trace in Langfuse."""
        if not self.enabled:
            return
        
        try:
            hashed_user = hash_phone_number(user_id)
            # TODO: Implement Langfuse trace creation
            # self.trace = self.langfuse_client.trace(
            #     id=run_id,
            #     user_id=hashed_user,
            #     metadata={"message_id": message_id, "business_id": business_id}
            # )
            logging.debug(f"[TRACE] Langfuse trace started: {run_id}")
        except Exception as e:
            logging.warning(f"[TRACE] Langfuse start_run failed: {e}")
    
    def log_event(self, run_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """Log an event to Langfuse."""
        if not self.enabled:
            return
        
        try:
            # TODO: Implement Langfuse event logging
            # if event_type == "tool_call":
            #     self.trace.span(...)
            # elif event_type == "llm_call":
            #     self.trace.generation(...)
            logging.debug(f"[TRACE] Langfuse event logged: {event_type}")
        except Exception as e:
            logging.warning(f"[TRACE] Langfuse log_event failed: {e}")
    
    def end_run(self, run_id: str, success: bool, error: Optional[str] = None,
                latency_ms: Optional[float] = None) -> None:
        """End the Langfuse trace."""
        if not self.enabled:
            return
        
        try:
            # TODO: Implement Langfuse trace completion
            # self.trace.update(metadata={"success": success, "error": error, "latency_ms": latency_ms})
            logging.debug(f"[TRACE] Langfuse trace ended: {run_id}")
        except Exception as e:
            logging.warning(f"[TRACE] Langfuse end_run failed: {e}")


def get_tracer() -> Tracer:
    """
    Get the appropriate tracer based on environment configuration.
    
    Returns:
        Tracer instance (ConsoleTracer by default, LangfuseTracer if configured)
    """
    tracer_type = os.getenv("TRACER_TYPE", "console").lower()
    
    if tracer_type == "langfuse":
        tracer = LangfuseTracer()
        # Fall back to console if Langfuse not properly initialized
        if not tracer.enabled:
            logging.info("[TRACE] Falling back to ConsoleTracer (Langfuse not available)")
            return ConsoleTracer()
        return tracer
    else:
        return ConsoleTracer()


# Global tracer instance
tracer = get_tracer()
