'Application module.'

from services.chat_flow import StreamAccumulator


def _run(events):
    acc = StreamAccumulator()
    for e in events:
        acc.observe(e)
    return acc.streamed_text()


def test_tool_call_between_text_blocks_makes_a_paragraph_break():
    text = _run([
        {"type": "stream_delta", "delta": "Tracing it now."},
        {"type": "tool_use_start", "tool_name": "Read", "tool_id": "t1"},
        {"type": "tool_result", "tool_use_id": "t1"},
        {"type": "stream_delta", "delta": "Now the edits."},
    ])
    assert text == "Tracing it now.\n\nNow the edits."


def test_unstreamed_turns_still_get_a_paragraph_break():
    'Test unstreamed turns still get a paragraph break.'
    text = _run([
        {"type": "stream_delta", "delta": "Reading her pass now."},
        {"type": "tool_input", "tool_id": "t1", "tool_name": "Bash", "input": {}},
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        {"type": "stream_delta", "delta": "Her design is clean."},
    ])
    assert text == "Reading her pass now.\n\nHer design is clean."


def test_existing_whitespace_is_not_doubled():
    text = _run([
        {"type": "stream_delta", "delta": "One.\n"},
        {"type": "tool_use_start", "tool_name": "Read", "tool_id": "t1"},
        {"type": "stream_delta", "delta": "Two."},
    ])
    assert text == "One.\nTwo."


def test_reply_that_starts_after_a_tool_has_no_leading_break():
    text = _run([
        {"type": "tool_use_start", "tool_name": "Read", "tool_id": "t1"},
        {"type": "stream_delta", "delta": "Hello"},
        {"type": "stream_delta", "delta": " Bunny."},
    ])
    assert text == "Hello Bunny."


def test_deltas_inside_one_block_are_untouched():
    assert _run([
        {"type": "stream_delta", "delta": "Hel"},
        {"type": "stream_delta", "delta": "lo."},
    ]) == "Hello."
