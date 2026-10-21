
import io
import json

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

import codigo_corrigido as cc


class FakeBedrockClient:
    def __init__(self, error: Exception | None = None, fail_when: str | None = None) -> None:
        self.calls: list[dict] = []
        self._error = error
        self._fail_when = fail_when

    def invoke_model(self, **kwargs):
        body = json.loads(kwargs["body"])
        self.calls.append(body)

        if self._error and (
            self._fail_when is None
            or self._fail_when in body["messages"][-1]["content"]
        ):
            raise self._error

        payload = {
            "content": [{"type": "text", "text": "resposta"}],
            "usage": {"input_tokens": 7, "output_tokens": 3},
            "stop_reason": "end_turn",
        }
        return {"body": io.BytesIO(json.dumps(payload).encode())}


class SpySink:
    def __init__(self) -> None:
        self.delivered: list[tuple[str, str]] = []

    def deliver(self, session_id, text):
        self.delivered.append((session_id, text))


def record(message_id="m1", message="oi", session_id="s1", raw_body=None):
    body = (
        raw_body
        if raw_body is not None
        else json.dumps({"message": message, "session_id": session_id})
    )
    return {"messageId": message_id, "body": body}


def build(client=None, store=None):
    client = client or FakeBedrockClient()
    store = store or cc.InMemorySessionStore()
    sink = SpySink()
    return cc.MessageProcessor(cc.BedrockChat(client), store, sink, "SYS"), client, store, sink


def run(processor, records, remaining_ms=60_000):
    return processor.process_batch(records, lambda: remaining_ms)


def test_sucesso_nao_reporta_falhas_e_entrega_resposta():
    processor, _, _, sink = build()
    assert run(processor, [record()]) == {"batchItemFailures": []}
    assert sink.delivered == [("s1", "resposta")]


def test_partial_batch_failure_reporta_apenas_a_mensagem_com_erro():
    throttling = ClientError(
        {"Error": {"Code": "ThrottlingException"}},
        "InvokeModel",
    )
    processor, _, _, _ = build(
        FakeBedrockClient(error=throttling, fail_when="quebra")
    )
    result = run(
        processor,
        [record("a", "ok"), record("b", "quebra"), record("c", "ok2")],
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "b"}]}


def test_historico_e_system_prompt_sao_injetados():
    processor, client, store, _ = build()
    run(processor, [record("m1", "primeira")])
    run(processor, [record("m2", "segunda")])

    second_call = client.calls[1]
    assert second_call["system"] == "SYS"
    assert [m["role"] for m in second_call["messages"]] == [
        "user",
        "assistant",
        "user",
    ]
    assert second_call["messages"][0]["content"] == "primeira"
    assert len(store.get_history("s1")) == 4


def test_historico_isolado_por_sessao():
    processor, client, _, _ = build()
    run(processor, [record("m1", "da A", session_id="A")])
    run(processor, [record("m2", "da B", session_id="B")])

    assert client.calls[1]["messages"] == [
        {"role": "user", "content": "da B"}
    ]


def test_historico_e_truncado_e_comeca_com_user():
    history = []

    for i in range(30):
        history += [
            {"role": "user", "content": f"u{i}"},
            {"role": "assistant", "content": f"a{i}"},
        ]

    history = history[1:]
    messages = cc.build_messages(history, "agora")

    assert messages[0]["role"] == "user"
    assert messages[-1] == {"role": "user", "content": "agora"}
    assert len(messages) <= 2 * cc.HISTORY_MAX_TURNS + 1


def test_mensagem_duplicada_nao_chama_o_modelo_de_novo():
    processor, client, _, sink = build()
    run(processor, [record("dup")])
    run(processor, [record("dup")])

    assert len(client.calls) == 1
    assert len(sink.delivered) == 1


@pytest.mark.parametrize(
    "raw_body",
    [
        "isso não é json",
        json.dumps({"message": "sem sessão"}),
        json.dumps({"message": "  ", "session_id": "s"}),
    ],
)
def test_poison_message_falha_sem_chamar_o_modelo(raw_body):
    processor, client, _, _ = build()

    assert run(processor, [record(raw_body=raw_body)]) == {
        "batchItemFailures": [{"itemIdentifier": "m1"}]
    }
    assert client.calls == []


def test_timeout_de_rede_e_tratado_como_retentavel():
    err = ReadTimeoutError(endpoint_url="https://bedrock")
    processor, _, _, _ = build(FakeBedrockClient(error=err))

    assert run(processor, [record()])["batchItemFailures"] == [
        {"itemIdentifier": "m1"}
    ]


@pytest.mark.parametrize(
    "code,retryable",
    [
        ("ThrottlingException", True),
        ("ModelTimeoutException", True),
        ("ValidationException", False),
    ],
)
def test_classificacao_de_erros_do_bedrock(code, retryable):
    chat = cc.BedrockChat(
        FakeBedrockClient(
            error=ClientError(
                {"Error": {"Code": code}},
                "InvokeModel",
            )
        )
    )

    with pytest.raises(cc.ModelInvocationError) as exc:
        chat.generate("SYS", [{"role": "user", "content": "oi"}])

    assert exc.value.retryable is retryable


def test_deadline_proximo_devolve_o_restante_do_lote_sem_chamar_o_modelo():
    processor, client, _, _ = build()

    result = run(
        processor,
        [record("a"), record("b")],
        remaining_ms=1_000,
    )

    assert result == {
        "batchItemFailures": [
            {"itemIdentifier": "a"},
            {"itemIdentifier": "b"},
        ]
    }
    assert client.calls == []


def test_falha_nao_grava_historico_nem_marca_como_processada():
    err = ClientError(
        {"Error": {"Code": "ThrottlingException"}},
        "InvokeModel",
    )
    processor, _, store, _ = build(FakeBedrockClient(error=err))

    run(processor, [record("m1")])

    assert store.get_history("s1") == []
    assert not store.is_processed("m1")


def test_reserva_impede_processamento_concorrente():
    store = cc.InMemorySessionStore()
    token = store.claim("m1")

    assert token is not None
    assert store.claim("m1") is None

    store.release("m1", token)

    assert store.claim("m1") is not None


def test_mensagem_em_processamento_retorna_ao_lote():
    store = cc.InMemorySessionStore()
    store.claim("m1")

    processor, client, _, sink = build(store=store)

    assert run(processor, [record("m1")]) == {
        "batchItemFailures": [{"itemIdentifier": "m1"}]
    }
    assert not client.calls
    assert not sink.delivered


def test_falha_libera_reserva_para_nova_tentativa():
    store = cc.InMemorySessionStore()
    err = ClientError(
        {"Error": {"Code": "ThrottlingException"}},
        "InvokeModel",
    )

    processor, _, _, _ = build(
        FakeBedrockClient(error=err),
        store=store,
    )

    run(processor, [record("m1")])

    assert store.claim("m1") is not None


def test_reserva_antiga_nao_pode_liberar_ou_finalizar_reserva_nova():
    store = cc.InMemorySessionStore()

    first = store.claim("m1")
    store.release("m1", first)

    second = store.claim("m1")

    assert first != second

    store.release("m1", first)

    assert store.claim("m1") is None

    with pytest.raises(RuntimeError):
        store.mark_processed("m1", first)

    store.mark_processed("m1", second)

    assert store.is_processed("m1")


def test_payload_nao_aceita_tipos_incorretos():
    processor, client, _, _ = build()
    payload = json.dumps(
        {"message": ["cancela"], "session_id": "s1"}
    )

    assert run(processor, [record(raw_body=payload)]) == {
        "batchItemFailures": [{"itemIdentifier": "m1"}]
    }
    assert not client.calls


def test_carregamento_de_prompt_por_arquivo(tmp_path, monkeypatch):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text(
        "Confirme antes de cancelar.",
        encoding="utf-8",
    )

    monkeypatch.setenv("SYSTEM_PROMPT_FILE", str(prompt_file))

    assert cc.load_system_prompt() == "Confirme antes de cancelar."
