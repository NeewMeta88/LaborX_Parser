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
"If you have any questions, feel free to reach out - I’ll gladly clarify."

RULES:
- Keep it short: 90–140 words max.
- No long intros, no generic “benefits of working with me”, no separators like "====".
- Use specifics from DESCRIPTION (stack, deliverables, constraints) but compress them into 1 sentence.
- Do NOT list every bullet from DESCRIPTION. Mention only 1–2 key outcomes + 1–2 key constraints.
- Use first-person singular only ("I", "my"). Do NOT use "we", "our", or "us".
- If DESCRIPTION mentions a niche chain/tech and you’re not fully sure, position it safely as:
  "I can deliver the UI + deployment first and keep the structure ready for live data integration."
- Mention timeline awareness using {{DAYS}} and/or {{DEADLINE}} in ONE short sentence.
- Avoid repeating the same opening in the first two sentences. Sentence 2 must NOT start with "I can". Prefer: "Timeline:", "Timing:", or "Schedule:".
- Sentence 2 must start with "Timeline:" and use one of these patterns: "Timeline: MVP in {{DAYS}} {{DEADLINE}}." or "Timeline: Delivery in {{DAYS}} {{DEADLINE}}."
- Sentence 1 should not always start with "I can". Use varied starts like: "I’d build..." or "I can help by...".
- Milestones format: "Day 1 - ...", "Day 2 - ...", etc. One line per day. No extra sub-bullets.
- Questions must be strictly necessary for scope/acceptance/deployment access.
  Examples: mockup link, data source/format, deployment target + DNS access.
- Always include a single line "Portfolio: {{PORTFOLIO_URL}}" right before the required closing line.
- Add exactly one blank line between the "Portfolio: {{PORTFOLIO_URL}}" line and the required closing line (do not place them directly next to each other).
- Ask up to 3 questions. If there is only 1, use the header "A quick question:" (singular). If there are 2–3, use either "A couple of quick questions:" or "A few quick questions:" (plural).
- If no questions are strictly necessary, still ask exactly 1 best question and use "A quick question:".
- Do NOT use the long dash character (—) anywhere. Use a normal hyphen "-" only.
- Any lists must use either numeric format "1. 2. 3." or letter format "a) b) c)". Do not use bullet points.
- Formatting: add exactly one blank line between logical blocks:
  (a) sentence 1 and sentence 2
  (b) sentence 2 and the Day-by-day plan
  (c) the Day-by-day plan and the questions header
  (d) the last question and the Portfolio line
  (e) the Portfolio line and the required closing line (already required)

OUTPUT STRUCTURE (exact order):
1) 1 sentence: confirm understanding (1–2 key outcomes) based on DESCRIPTION.
2) Add exactly one blank line.
3) 1 sentence starting with "Timeline:" that confirms timing using {{DAYS}} and/or {{DEADLINE}}.
4) Add exactly one blank line.
5) Day-by-day plan for {{DAYS}} (one line per day).
6) Add exactly one blank line.
7) Use the header based on the number of questions:
   a) 1 question -> "A quick question:"
   b) 2–3 questions -> "A couple of quick questions:" or "A few quick questions:"
   Then list the questions in numeric format (max 3).
8) Add exactly one blank line.
9) Add one line: "Portfolio: {{PORTFOLIO_URL}}"
10) Add exactly one blank line.
11) The required closing line above (exact text).

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
