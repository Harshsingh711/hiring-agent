# Hiring Agent

Upload a resume. Get a ranked score, category breakdown, and a list of what to fix.

Hiring Agent is a local web app I built for scoring software resumes against an intern hiring rubric. Drop in a PDF and it returns an overall score, evidence for each category, strengths, and specific improvements. If the resume includes a GitHub profile, public repos are fetched and classified as personal projects vs. true open-source contributions.

This is a product wrapper around [HackerRank’s open-source hiring-agent](https://github.com/interviewstreet/hiring-agent). I added the upload UI, job progress, and a report built for candidates who want to see how a rubric would rank them.

It is **not** a typical ATS. Most company systems do not clone your GitHub. This one scores against a public intern rubric so you can see the gaps before you apply.

## What you get

- **Overall score** out of 100 (plus bonus and deductions)
- **Open source, self projects, production, technical skills** with written evidence
- **Where to improve** as the primary takeaway
- GitHub signal when a profile is on the resume

Default intern weights:

| Category | Max |
|---|---:|
| Open source | 35 |
| Self projects | 30 |
| Production experience | 25 |
| Technical skills | 10 |

## Quick start

Python 3.12 and a Gemini (or Ollama) model.

```bash
git clone https://github.com/Harshsingh711/hiring-agent.git
cd hiring-agent

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Set `DEFAULT_MODEL` and `GEMINI_API_KEY` in `.env`. Gemini models in `providers.json` include `gemini-3-flash-preview` and `gemini-3.6-flash`. Get a key from [Google AI Studio](https://aistudio.google.com/api-keys).

Start the site:

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), choose a rubric, and upload a PDF. A full run can take several minutes. Files stay on your machine.

### Deploy on Vercel

The GitHub repo is connected to Vercel. After you push, set these in the Vercel project **Environment Variables**:

| Name | Example |
|---|---|
| `GEMINI_API_KEY` | your Google AI Studio key |
| `DEFAULT_MODEL` | `gemini-3-flash-preview` |

Then redeploy. Vercel runs Python 3.12. Scoring can take longer than the Hobby function timeout, so a Pro plan (or running locally) is more reliable for full reports.

### CLI

```bash
python score.py path/to/resume.pdf --role software_engineering_intern
```

## How ranking works

1. Parse the PDF into contact, work, education, skills, projects, and awards.
2. If GitHub is listed, pull public repos and classify them.
3. Score against the selected role’s rubric.
4. Return category scores, bonus, deductions, strengths, and improvements.

Roles live in `roles/<name>/`. The shipped role is `software_engineering_intern`. Scaffold another with:

```bash
python score.py --init-role backend_engineer
```

## Stack

- FastAPI + a static frontend (`web/`)
- The original hiring-agent pipeline (`score.py`, `pdf.py`, `github.py`, `evaluator.py`)
- Gemini or local Ollama via `providers.json`

## Attribution

Scoring logic, prompts, and role rubrics come from [interviewstreet/hiring-agent](https://github.com/interviewstreet/hiring-agent) (MIT, © HackerRank). The website, upload/job API, and candidate-facing report are mine.

## License

[MIT](./LICENSE)
