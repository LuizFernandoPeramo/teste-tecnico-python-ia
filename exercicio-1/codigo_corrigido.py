
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "512"))
HISTORY_MAX_TURNS = int(os.environ.get("HISTORY_MAX_TURNS", "10"))
MAX_INPUT_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "4000"))
MIN_REMAINING_MS = int(os.environ.get("MIN_REMAINING_MS", "15000"))
BEDROCK_READ_TIMEOUT_S = int(os.environ.get("BEDROCK_READ_TIMEOUT_S", "20"))
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "ChatbotConsumer")

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "Você é um assistente virtual de suporte ao cliente. "
    "Responda de forma objetiva e cordial. Não forneça informações incorretas.",
)

TRANSIENT_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "ModelTimeoutException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelNotReadyException",
    }
)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "message": record.getMessage()}
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, ensure_ascii=False, default=str)


def _build_logger() -> logging.Logger:
    log = logging.getLogger("chatbot-consumer")
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


logger = _build_logger()


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": fields})


def emit_metric(name: str, value: float, unit: str = "Count", **dimensions: str) -> None:
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": METRIC_NAMESPACE,
                            "Dimensions": [list(dimensions)],
                            "Metrics": [{"Name": name, "Unit": unit}],
                        }
                    ],
                },
                name: value,
                **dimensions,
            }
        )
    )


class InvalidMessageError(Exception):
    pass


class ModelInvocationError(Exception):
    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    session_id: str
    text: str

    @classmethod
    def from_sqs_record(cls, record: dict[str, Any]) -> "IncomingMessage":
        try:
            body = json.loads(record["body"])
            text = str(body["message"]).strip()
            session_id = str(body["session_id"]).strip()
            message_id = str(record["messageId"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidMessageError(f"payload inválido: {type(exc).__name__}") from exc

        if not isinstance(body.get("message"), str) or not isinstance(body.get("session_id"), str):
            raise InvalidMessageError("message e session_id devem ser textos")

        if not text or not session_id:
            raise InvalidMessageError("message/session_id vazios")

        if len(text) > MAX_INPUT_CHARS:
            raise InvalidMessageError(f"mensagem excede {MAX_INPUT_CHARS} caracteres")

        return cls(message_id=message_id, session_id=session_id, text=text)


@dataclass(frozen=True)
class ModelReply:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None


class SessionStore(Protocol):
    def get_history(self, session_id: str) -> list[dict[str, str]]: ...
    def append_turn(self, session_id: str, user_text: str, assistant_text: str) -> None: ...
    def is_processed(self, message_id: str) -> bool: ...
    def claim(self, message_id: str) -> str | None: ...
    def release(self, message_id: str, claim_token: str) -> None: ...
    def mark_processed(self, message_id: str, claim_token: str) -> None: ...


class ResponseSink(Protocol):
    def deliver(self, session_id: str, text: str) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, str]]] = {}
        self._processed: set[str] = set()
        self._claimed: dict[str, str] = {}

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        return list(self._history.get(session_id, []))

    def append_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        turns = self._history.setdefault(session_id, [])
        turns.append({"role": "user", "content": user_text})
        turns.append({"role": "assistant", "content": assistant_text})

    def is_processed(self, message_id: str) -> bool:
        return message_id in self._processed

    def mark_processed(self, message_id: str, claim_token: str) -> None:
        if self._claimed.get(message_id) != claim_token:
            raise RuntimeError("reserva de processamento perdida")
        self._processed.add(message_id)
        del self._claimed[message_id]

    def claim(self, message_id: str) -> str | None:
        if message_id in self._claimed or message_id in self._processed:
            return None
        token = uuid.uuid4().hex
        self._claimed[message_id] = token
        return token

    def release(self, message_id: str, claim_token: str) -> None:
        if self._claimed.get(message_id) == claim_token:
            del self._claimed[message_id]


class DynamoSessionStore:
    SESSION_TTL_S = 24 * 3600
    MESSAGE_TTL_S = 6 * 3600

    def __init__(self, table: Any) -> None:
        self._table = table

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        item = self._table.get_item(Key={"pk": f"session#{session_id}"}).get("Item")
        return json.loads(item["history"]) if item else []

    def append_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        history = self.get_history(session_id)
        history += [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
        self._table.put_item(
            Item={
                "pk": f"session#{session_id}",
                "history": json.dumps(history[-2 * HISTORY_MAX_TURNS :], ensure_ascii=False),
                "ttl": int(time.time()) + self.SESSION_TTL_S,
            }
        )

    def is_processed(self, message_id: str) -> bool:
        item = self._table.get_item(Key={"pk": f"msg#{message_id}"}, ConsistentRead=True).get("Item")
        return bool(item and item.get("status") == "COMPLETED")

    def claim(self, message_id: str) -> str | None:
        now = int(time.time())
        token = uuid.uuid4().hex
        try:
            self._table.put_item(
                Item={
                    "pk": f"msg#{message_id}",
                    "status": "PROCESSING",
                    "owner": token,
                    "lease_until": now + 120,
                    "ttl": now + self.MESSAGE_TTL_S,
                },
                ConditionExpression="attribute_not_exists(pk) OR (#s = :processing AND lease_until < :now)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":processing": "PROCESSING", ":now": now},
            )
            return token
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise

    def mark_processed(self, message_id: str, claim_token: str) -> None:
        self._table.update_item(
            Key={"pk": f"msg#{message_id}"},
            UpdateExpression="SET #s = :done REMOVE lease_until",
            ConditionExpression="#s = :processing AND #owner = :owner",
            ExpressionAttributeNames={"#s": "status", "#owner": "owner"},
            ExpressionAttributeValues={
                ":done": "COMPLETED",
                ":processing": "PROCESSING",
                ":owner": claim_token,
            },
        )

    def release(self, message_id: str, claim_token: str) -> None:
        try:
            self._table.delete_item(
                Key={"pk": f"msg#{message_id}"},
                ConditionExpression="#s = :processing AND #owner = :owner",
                ExpressionAttributeNames={"#s": "status", "#owner": "owner"},
                ExpressionAttributeValues={":processing": "PROCESSING", ":owner": claim_token},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise


class LogResponseSink:
    def deliver(self, session_id: str, text: str) -> None:
        log_event("response_delivered", session_id=session_id, response_chars=len(text))


class BedrockChat:
    def __init__(self, client: Any, model_id: str = MODEL_ID, max_tokens: int = MAX_TOKENS) -> None:
        self._client = client
        self._model_id = model_id
        self._max_tokens = max_tokens

    def generate(self, system_prompt: str, messages: list[dict[str, str]]) -> ModelReply:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._max_tokens,
            "system": system_prompt,
            "messages": messages,
        }
        try:
            response = self._client.invoke_model(
                modelId=self._model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            raise ModelInvocationError(code or "ClientError", code in TRANSIENT_ERROR_CODES) from exc
        except BotoCoreError as exc:
            raise ModelInvocationError(type(exc).__name__, retryable=True) from exc

        text = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
        if not text:
            raise ModelInvocationError("EmptyModelResponse", retryable=True)

        usage = result.get("usage", {})
        return ModelReply(
            text=text,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            stop_reason=result.get("stop_reason"),
        )


def build_messages(history: list[dict[str, str]], user_text: str) -> list[dict[str, str]]:
    recent = history[-2 * HISTORY_MAX_TURNS :]
    while recent and recent[0]["role"] != "user":
        recent = recent[1:]
    return [*recent, {"role": "user", "content": user_text}]


class MessageProcessor:
    def __init__(
        self,
        chat: BedrockChat,
        store: SessionStore,
        sink: ResponseSink,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._chat = chat
        self._store = store
        self._sink = sink
        self._system_prompt = system_prompt

    def process_record(self, record: dict[str, Any]) -> None:
        message = IncomingMessage.from_sqs_record(record)
        context = {"message_id": message.message_id, "session_id": message.session_id}

        if self._store.is_processed(message.message_id):
            log_event("duplicate_skipped", **context)
            emit_metric("DuplicateSkipped", 1)
            return

        claim_token = self._store.claim(message.message_id)
        if claim_token is None:
            raise ModelInvocationError("MessageAlreadyInProgress", retryable=True)

        try:
            messages = build_messages(self._store.get_history(message.session_id), message.text)
            started = time.perf_counter()
            reply = self._chat.generate(self._system_prompt, messages)
            latency_ms = (time.perf_counter() - started) * 1000

            self._sink.deliver(message.session_id, reply.text)
            self._store.append_turn(message.session_id, message.text, reply.text)
            self._store.mark_processed(message.message_id, claim_token)
        except Exception:
            self._store.release(message.message_id, claim_token)
            raise

        log_event(
            "message_processed",
            **context,
            latency_ms=round(latency_ms),
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            stop_reason=reply.stop_reason,
            history_messages=len(messages) - 1,
        )
        emit_metric("ModelLatency", latency_ms, "Milliseconds")
        emit_metric("OutputTokens", reply.output_tokens)

    def process_batch(
        self, records: list[dict[str, Any]], remaining_ms: Callable[[], int]
    ) -> dict[str, list[dict[str, str]]]:
        failures: list[dict[str, str]] = []

        for index, record in enumerate(records):
            message_id = record.get("messageId", "unknown")

            if remaining_ms() < MIN_REMAINING_MS:
                pending = records[index:]
                failures.extend({"itemIdentifier": r["messageId"]} for r in pending)
                log_event("deadline_near", level=logging.WARNING, returned_to_queue=len(pending))
                emit_metric("DeadlineReturned", len(pending))
                break

            try:
                self.process_record(record)
            except InvalidMessageError as exc:
                log_event("invalid_message", level=logging.ERROR, message_id=message_id, error=str(exc))
                emit_metric("InvalidMessage", 1)
                failures.append({"itemIdentifier": message_id})
            except ModelInvocationError as exc:
                log_event(
                    "model_error",
                    level=logging.ERROR,
                    message_id=message_id,
                    error=str(exc),
                    retryable=exc.retryable,
                )
                emit_metric("ModelError", 1, ErrorType=str(exc))
                failures.append({"itemIdentifier": message_id})
            except Exception as exc:
                log_event("unexpected_error", level=logging.ERROR, message_id=message_id, error=repr(exc))
                emit_metric("UnexpectedError", 1)
                failures.append({"itemIdentifier": message_id})

        return {"batchItemFailures": failures}


def load_system_prompt() -> str:
    path = os.environ.get("SYSTEM_PROMPT_FILE")
    if not path:
        return SYSTEM_PROMPT

    content = Path(path).read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError("SYSTEM_PROMPT_FILE está vazio")
    return content


@lru_cache(maxsize=1)
def _build_default_processor() -> MessageProcessor:
    bedrock = boto3.client(
        "bedrock-runtime",
        config=Config(
            read_timeout=BEDROCK_READ_TIMEOUT_S,
            connect_timeout=3,
            retries={"max_attempts": 2, "mode": "adaptive"},
        ),
    )
    table = boto3.resource("dynamodb").Table(os.environ["SESSION_TABLE"])
    return MessageProcessor(BedrockChat(bedrock), DynamoSessionStore(table), LogResponseSink(), load_system_prompt())


def handler(event: dict[str, Any], context: Any) -> dict[str, list[dict[str, str]]]:
    return _build_default_processor().process_batch(
        event.get("Records", []), context.get_remaining_time_in_millis
    )


if __name__ == "__main__":
    import io

    class _FakeBedrockClient:
        def invoke_model(self, **kwargs: Any) -> dict[str, Any]:
            last = json.loads(kwargs["body"])["messages"][-1]["content"]
            if "falha" in last:
                raise ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")

            payload = {
                "content": [{"type": "text", "text": f"Eco: {last}"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            }
            return {"body": io.BytesIO(json.dumps(payload).encode())}

    def _record(mid: str, msg: str) -> dict[str, Any]:
        return {"messageId": mid, "body": json.dumps({"message": msg, "session_id": "s1"})}

    demo = MessageProcessor(BedrockChat(_FakeBedrockClient()), InMemorySessionStore(), LogResponseSink())
    out = demo.process_batch(
        [_record("m1", "olá"), _record("m2", "isso vai falha"), _record("m3", "e agora?")],
        remaining_ms=lambda: 60_000,
    )
    print("RESULTADO:", json.dumps(out))
