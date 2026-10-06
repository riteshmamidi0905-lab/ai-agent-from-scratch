"""Structured (JSON) logging with request/run ids carried in contextvars so every log line from a request or a run is correlatable."""
import contextvars
import json
import logging
import sys
import time

request_id_var = contextvars.ContextVar("request_id", default="")
run_id_var = contextvars.ContextVar("run_id", default="")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z", "level": record.levelname,
             "logger": record.name, "msg": record.getMessage()}
        if request_id_var.get():
            d["request_id"] = request_id_var.get()
        if run_id_var.get():
            d["run_id"] = run_id_var.get()
        extra = getattr(record, "fields", None)
        if extra:
            d.update(extra)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(d, default=str)


def setup(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").disabled = True      # replaced by our own request log line


def log(logger, msg, **fields):
    logger.info(msg, extra={"fields": fields})
