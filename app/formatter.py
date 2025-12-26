import html
from typing import List

TG_LIMIT = 3900

def format_tags_code_lines(tags: list[str]) -> str:
    if not tags:
        return "<code>(no tags)</code>"

    lines = []
    last = len(tags) - 1
    for i, t in enumerate(tags):
        suffix = "," if i != last else ""
        lines.append(f"<code>{html.escape(t)}</code>{suffix}")
    return "\n".join(lines)

def format_result_messages(data) -> List[str]:
    title = html.escape(getattr(data, "job_name", "") or "(not found)")
    url = html.escape(getattr(data, "url", "") or "")
    price = html.escape(getattr(data, "price", "") or "(not found)")
    days = html.escape(getattr(data, "days", "") or "(not found)")
    deadline = html.escape(getattr(data, "deadline", "") or "(not found)")

    desc_raw = getattr(data, "description", "") or "(not found)"
    desc_html = html.escape(desc_raw)

    tags_html = format_tags_code_lines(getattr(data, "tags", []) or [])

    meta_block = (
        f"<blockquote>Price: {price}\n"
        f"Days: {days}\n"
        f"Deadline: {deadline}</blockquote>\n\n"
        f"Tags:\n{tags_html}\n\n"
        f"{url}"
    )

    one = f"<b>{title}</b>\n\n<pre>{desc_html}</pre>\n\n{meta_block}"
    if len(one) <= TG_LIMIT:
        return [one]

    msgs: List[str] = []
    chunk_size = 3200
    chunks = [desc_raw[i:i + chunk_size] for i in range(0, len(desc_raw), chunk_size)]

    if chunks:
        msgs.append(f"<b>{title}</b>\n<pre>{html.escape(chunks[0])}</pre>")
        for ch in chunks[1:]:
            msgs.append(f"<pre>{html.escape(ch)}</pre>")
    else:
        msgs.append(f"<b>{title}</b>\n<pre>(not found)</pre>")

    msgs.append(meta_block)
    return msgs
