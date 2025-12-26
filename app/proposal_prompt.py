import html
from typing import List

from .formatter import TG_LIMIT
from .parser import JobData


PROMPT_TEMPLATE = """You are a freelance proposal writer for LaborX. Write a short, client-facing reply in ENGLISH.

INPUT (parsed job data):
- title: {{TITLE}}
- url: {{URL}}
- price: {{PRICE}}
- days: {{DAYS}}
- deadline: {{DEADLINE}}
- description: {{DESCRIPTION}}
- portfolio_url: {{PORTFOLIO_URL}}

TASK:
Read the DESCRIPTION carefully and produce a concise reply that:
1) Shows you understood the request WITHOUT repeating all requirements.
2) Proposes a day-by-day milestone plan that fits exactly into the number of days in {{DAYS}}.
3) Asks ONLY the minimum essential questions required to finalize milestones (max 3).
4) Ends with this exact line (verbatim):
"If you have any questions, feel free to reach out — I’ll gladly clarify."

RULES:
- Keep it short: 90–140 words max.
- No long intros, no generic “benefits of working with me”, no separators like "====".
- Use specifics from DESCRIPTION (stack, deliverables, constraints) but compress them into 1 sentence.
- Do NOT list every bullet from DESCRIPTION. Mention only 1–2 key outcomes + 1–2 key constraints.
- If DESCRIPTION mentions a niche chain/tech and you’re not fully sure, position it safely as:
  "I can deliver the UI + deployment first and keep the structure ready for live data integration."
- Mention timeline awareness using {{DAYS}} and/or {{DEADLINE}} in ONE short sentence.
- Milestones format: "Day 1 — ...", "Day 2 — ...", etc. One line per day. No extra sub-bullets.
- Questions must be strictly necessary for scope/acceptance/deployment access.
  Examples: mockup link, data source/format, deployment target + DNS access.
- Always include a single line "Portfolio: {{PORTFOLIO_URL}}" right before the required closing line.
- Ask up to 3 questions. If there is only 1, use the header "A quick question:" (singular). If there are 2–3, use either "A couple of quick questions:" or "A few quick questions:" (plural).
- If no questions are strictly necessary, still ask exactly 1 best question and use "A quick question:".


OUTPUT STRUCTURE (exact order):
1) 1 sentence: confirm understanding (1–2 key outcomes) based on DESCRIPTION.
2) 1 sentence: confirm timeline ({{DAYS}} / {{DEADLINE}}).
3) Day-by-day plan for {{DAYS}}.
4) Use the header based on the number of questions:
   - 1 question → "A quick question:"
   - 2–3 questions → "A couple of quick questions:" or "A few quick questions:"
   Then list the questions as bullets (max 3).
5) Add one line: "Portfolio: {{PORTFOLIO_URL}}"
6) The required closing line above (exact text).

Now write the reply.
"""


def _val(v: str, default: str = "(not found)") -> str:
    v = (v or "").strip()
    return v if v else default


def build_filled_prompt(job: JobData, portfolio_url: str) -> str:
    title = _val(job.job_name)
    url = _val(job.url, default="")
    price = _val(job.price)
    days = _val(job.days)
    deadline = _val(job.deadline)
    desc = _val(job.description)

    prompt = PROMPT_TEMPLATE
    prompt = prompt.replace("{{TITLE}}", title)
    prompt = prompt.replace("{{URL}}", url)
    prompt = prompt.replace("{{PRICE}}", price)
    prompt = prompt.replace("{{DAYS}}", days)
    prompt = prompt.replace("{{DEADLINE}}", deadline)
    prompt = prompt.replace("{{DESCRIPTION}}", desc)
    prompt = prompt.replace("{{PORTFOLIO_URL}}", portfolio_url)
    return prompt


def format_proposal_prompt_messages(job: JobData, portfolio_url: str) -> List[str]:
    html.escape(_val(job.job_name))
    url = html.escape(_val(job.url, default=""))

    prompt = build_filled_prompt(job, portfolio_url)
    prompt_escaped = html.escape(prompt)

    one = f"<b>Your proposal for the vacancy above ⬆️</b>\n\n<pre>{prompt_escaped}</pre>\n\n{url}"
    if len(one) <= TG_LIMIT:
        return [one]

    msgs: List[str] = []
    chunk_size = 3200
    chunks = [prompt[i : i + chunk_size] for i in range(0, len(prompt), chunk_size)]

    if not chunks:
        return [f"<b>Your proposal for the vacancy above ⬆️</b>\n\n<pre>(empty)</pre>\n\n{url}"]

    msgs.append(f"<b>Your proposal for the vacancy above ⬆️</b>\n\n<pre>{html.escape(chunks[0])}</pre>")

    for ch in chunks[1:-1]:
        msgs.append(f"<pre>{html.escape(ch)}</pre>")

    if len(chunks) > 1:
        msgs.append(f"<pre>{html.escape(chunks[-1])}</pre>\n\n{url}")
    else:
        msgs[0] = msgs[0] + f"\n\n{url}"

    return msgs
