# pyright: reportUnknownMemberType=false
import litellm
import pytest

from common.errors import LifecycleError
from harness.chat import Chat

_MODEL = "openai/gpt-4o"
_INITIAL_PROMPT = "You are a helpful assistant."
_MAX_TOKENS = 1000
_TRUNCATE_BYTES = 20

_RAW_SHORT = "Hello world"
_RAW_LONG = "This is a very long string that will be truncated"

_EXPECTED_HISTORY_LEN_3 = 3
_EXPECTED_HISTORY_LEN_2 = 2
_EXPECTED_DROPPED_2 = 2


def test_append_user_after_construction_raises() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
    )
    with pytest.raises(LifecycleError) as exc:
        chat.append_user("Hi")
    assert exc.value.state == "user"
    assert exc.value.event == "append_user"


def test_append_assistant_then_user_succeeds() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
    )
    chat.append_assistant("Hello")
    chat.append_user("How are you?")
    assert len(chat.history) == _EXPECTED_HISTORY_LEN_3
    assert chat.history[1].role == "assistant"
    assert chat.history[2].role == "user"


def test_append_user_twice_raises() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
    )
    chat.append_assistant("Hello")
    chat.append_user("How are you?")
    with pytest.raises(LifecycleError) as exc:
        chat.append_user("Wait")
    assert exc.value.state == "user"
    assert exc.value.event == "append_user"


def test_truncate_terminal_output_unchanged() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
        terminal_output_truncate_bytes=_TRUNCATE_BYTES,
    )
    assert chat.truncate_terminal_output(_RAW_SHORT) == _RAW_SHORT


def test_truncate_terminal_output_truncated() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
        terminal_output_truncate_bytes=_TRUNCATE_BYTES,
    )
    result = chat.truncate_terminal_output(_RAW_LONG)
    assert result == "This is a \n...[truncated 29 bytes]...\n truncated"


def test_trims_oldest_pair_on_overflow() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=10,  # small limit
    )
    chat.append_assistant("A1")
    chat.append_user("U1")
    chat.append_assistant("A2")
    chat.append_user("U2")

    dropped = chat.trim_to_fit()

    assert dropped == _EXPECTED_DROPPED_2
    assert len(chat.history) == 1
    assert chat.history[0].content == _INITIAL_PROMPT


def test_trim_never_drops_first_turn() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=1,  # small limit to force trim
    )
    dropped = chat.trim_to_fit()
    assert dropped == 0
    assert len(chat.history) == 1
    assert chat.history[0].content == _INITIAL_PROMPT


def test_should_summarize() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=1000,
        summarize_below_free_tokens=900,
    )
    assert not chat.should_summarize()

    # Create a new chat with higher summarize_below_free_tokens to avoid modifying private attribute
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=1000,
        summarize_below_free_tokens=2000,
    )
    assert chat.should_summarize()


def test_summarize() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
    )
    chat.append_assistant("A1")
    chat.append_user("U1")
    chat.append_assistant("A2")

    chat.summarize("This is the summary")
    assert len(chat.history) == _EXPECTED_HISTORY_LEN_2
    assert chat.history[0].content == _INITIAL_PROMPT
    assert chat.history[1].role == "assistant"
    assert chat.history[1].content == "This is the summary"


def test_prompt_tokens() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
    )
    msg = [{"role": "user", "content": _INITIAL_PROMPT}]
    expected = litellm.token_counter(model=_MODEL, messages=msg)
    assert chat.prompt_tokens == expected


def test_merge_into_last_user_appends_to_existing_user_content() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
    )
    chat.append_assistant("assistant reply")
    chat.append_user("bash result")
    before_len = len(chat.history)

    chat.merge_into_last_user("\n\n[HEARTBEAT t=5s turn=1] continue")

    assert len(chat.history) == before_len
    assert chat.history[-1].role == "user"
    assert chat.history[-1].content == "bash result\n\n[HEARTBEAT t=5s turn=1] continue"


def test_merge_into_last_user_raises_when_last_role_is_assistant() -> None:
    chat = Chat(
        initial_user_prompt=_INITIAL_PROMPT,
        model=_MODEL,
        max_context_tokens=_MAX_TOKENS,
    )
    chat.append_assistant("assistant reply")

    with pytest.raises(LifecycleError) as exc:
        chat.merge_into_last_user("more content")

    assert exc.value.state == "assistant"
    assert exc.value.event == "merge_into_last_user"
