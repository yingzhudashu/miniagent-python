"""执行器流式文本缓冲组件测试。"""

from miniagent.agent.execution_stream import StreamingBuffer


def test_streaming_buffer_consolidates_without_changing_content() -> None:
    buffer = StreamingBuffer()
    chunks = [f"{index}," for index in range(75)]
    for chunk in chunks:
        buffer.append(chunk)
    expected = "".join(chunks)
    assert buffer.getvalue() == expected
    assert len(buffer) == len(expected)


def test_streaming_buffer_clear_allows_safe_reuse() -> None:
    buffer = StreamingBuffer()
    buffer.append("before")
    buffer.clear()
    assert buffer.getvalue() == ""
    assert len(buffer) == 0
    buffer.append("after")
    assert buffer.getvalue() == "after"
