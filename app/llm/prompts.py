from datetime import datetime


def system_prompt(resume_summary: str, stats: dict, role_profiles: list[str]) -> str:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    profiles_text = "\n".join(f"  - {p}" for p in role_profiles)
    return f"""You are a strategic career advisor and job search assistant for Bernardo Martinez.
Your goal is to help him land a senior leadership role in engineering.

## Candidate
- Target level: Senior Manager, Director, Sr. Director
- Focus areas: SRE, AI/ML Platform, Engineering Leadership
- Markets: US & Canada (requires visa sponsorship) | Mexico (salary ≥ 130k MXN, Manager+)

## Profile summary
{resume_summary}

## Active role profiles
{profiles_text}

## Current status ({now})
- Jobs scraped: {stats.get('total_jobs', 0)} | New today: {stats.get('new_today', 0)}
- High matches (70+): {stats.get('high_matches', 0)}
- Pending review: {stats.get('pending_review', 0)}
- Submitted this week: {stats.get('submitted_week', 0)}
- Last scrape: {stats.get('last_scrape', 'never')}

## Instructions
- When get_jobs returns a table_md field, copy it verbatim into your response — the titles are already formatted as clickable links, do not reformat them
- When presenting jobs, also mention the user can click a title to see the full description in a side panel
- Be specific and strategic — recommend concrete next actions
- You have memory of past conversations; reference prior decisions when relevant
- You can call tools to query jobs, draft applications, approve/reject, update lifecycle, and more
- Never suggest applying to jobs marked with no_sponsorship in US/CA markets
- When showing a pipeline update, use the lifecycle stage names: applied, phone_screen, technical, final_round, offer, negotiating, accepted, declined, rejected, ghosted
"""


def extract_resume(raw_text: str) -> list[dict]:
    return [
        {"role": "system", "content": "Extract structured information from this resume. Return ONLY valid JSON, no markdown."},
        {"role": "user", "content": f"""Resume:
{raw_text}

Return JSON:
{{
  "name": "...",
  "email": "...",
  "phone": "...",
  "location": "...",
  "linkedin_url": "...",
  "years_experience": 0,
  "target_roles": [],
  "skills": {{
    "languages": [],
    "frameworks": [],
    "databases": [],
    "cloud": [],
    "tools": []
  }},
  "experience": [
    {{"title": "...", "company": "...", "start": "YYYY-MM", "end": "YYYY-MM or present", "summary": "..."}}
  ],
  "education": [{{"degree": "...", "institution": "...", "year": 0}}],
  "certifications": [],
  "summary": "2-3 sentence professional summary"
}}"""},
    ]


def generate_search_queries(resume_json: dict, profile_name: str, focus: str, market: str) -> list[dict]:
    return [
        {"role": "system", "content": "Generate job search queries. Return ONLY valid JSON."},
        {"role": "user", "content": f"""Candidate profile:
{resume_json}

Role profile: {profile_name} | Focus: {focus} | Market: {market}

Generate 6-8 optimized search queries for this role profile targeting {market} market.

Location rules:
- For us_ca market: use broad locations like "United States", "Canada", or major tech hubs ("San Francisco, CA", "Seattle, WA", "New York, NY", "Austin, TX"). Always include at least 2 remote_only:true queries.
- For mx market: use "Mexico" or "Ciudad de Mexico".
- Never use a single city as the only location across all queries.

Return JSON:
{{
  "queries": [
    {{"search_term": "...", "location": "...", "remote_only": true/false}}
  ]
}}"""},
    ]


def score_job(resume_json: dict, job: dict, profile_name: str) -> list[dict]:
    return [
        {"role": "system", "content": "Score job-candidate fit. Return ONLY valid JSON."},
        {"role": "user", "content": f"""Candidate profile (role: {profile_name}):
{resume_json}

Job:
Title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')} | Remote: {job.get('is_remote')}
Salary: {job.get('salary_min', '?')} - {job.get('salary_max', '?')} {job.get('salary_currency', '')}
Description (first 2000 chars):
{str(job.get('description', ''))[:2000]}

Return JSON:
{{
  "score": 0,
  "reasoning": "...",
  "highlights": [],
  "red_flags": [],
  "recommend": true/false,
  "seniority_match": "perfect|good|stretch|mismatch"
}}"""},
    ]


def generate_cover_letter(resume_json: dict, job: dict, match: dict, profile_name: str) -> list[dict]:
    return [
        {"role": "system", "content": "Write a professional cover letter. Output only the letter text, no headers or subject line."},
        {"role": "user", "content": f"""Write a concise, specific cover letter (3 paragraphs, ~280 words).

Candidate: {resume_json.get('name')} | Role profile: {profile_name}
Applying for: {job.get('title')} at {job.get('company')}

Key match highlights:
{match.get('highlights_json', '[]')}

Candidate summary:
{resume_json.get('summary')}

Recent experience:
{resume_json.get('experience', [{}])[0] if resume_json.get('experience') else ''}

Job description (first 1500 chars):
{str(job.get('description', ''))[:1500]}

Be specific, avoid generic phrases, focus on leadership impact and technical depth relevant to this role."""},
    ]


def generate_answers(resume_json: dict, job: dict, questions: list[str]) -> list[dict]:
    q_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    return [
        {"role": "system", "content": "Answer job application questions honestly and professionally. Return ONLY valid JSON."},
        {"role": "user", "content": f"""Candidate: {resume_json.get('name')}
Applying for: {job.get('title')} at {job.get('company')}
Profile: {resume_json.get('summary')}

Questions:
{q_text}

Return JSON:
{{"answers": {{"1": "answer...", "2": "answer...", ...}}}}

Keep answers honest, concise, and results-oriented. Do not fabricate experience."""},
    ]


def summarize_conversation(messages: list[dict]) -> list[dict]:
    text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:300]}"
        for m in messages
        if m.get("content") and m["role"] in ("user", "assistant")
    )
    return [
        {"role": "system", "content": "Summarize this job search conversation. Be concise. Focus on decisions made, jobs discussed, and preferences expressed. Max 300 words."},
        {"role": "user", "content": text},
    ]
