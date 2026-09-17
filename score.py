import os
import sys
import json
import hashlib

# Fix for Windows Console Unicode errors
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Fix for Python 3.14 Protobuf TypeError
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import logging
import csv

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

import argparse

from pdf import PDFHandler
from github import fetch_and_display_github_info
from models import JSONResume, build_evaluation_model
from typing import List, Optional, Dict
from evaluator import ResumeEvaluator
from roles import Role, load_role, list_available_roles, scaffold_role
from pathlib import Path
from prompt import DEFAULT_MODEL, MODEL_PARAMETERS
from transform import (
    transform_evaluation_response,
    convert_json_resume_to_text,
    convert_github_data_to_text,
    convert_blog_data_to_text,
)
from config import CACHE_DIR, DEVELOPMENT_MODE

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)5s - %(lineno)5d - %(funcName)33s - %(levelname)5s - %(message)s",
)


def compute_evaluation_summary(evaluation, role: Role) -> Dict:
    """Turn a model evaluation into a stable JSON-friendly score report."""
    category_total = 0.0
    category_max = 0.0
    categories = []

    if evaluation and hasattr(evaluation, "scores") and evaluation.scores:
        scores_dump = evaluation.scores.model_dump()
        for category in role.categories:
            cat = scores_dump.get(category.key)
            if not cat:
                continue
            raw_score = float(cat.get("score") or 0)
            max_score = float(category.max)
            capped = min(raw_score, max_score)
            category_total += capped
            category_max += max_score
            categories.append(
                {
                    "key": category.key,
                    "label": category.label,
                    "score": capped,
                    "max": max_score,
                    "evidence": cat.get("evidence") or "",
                }
            )

    bonus_total = 0.0
    bonus_breakdown = ""
    if evaluation and hasattr(evaluation, "bonus_points") and evaluation.bonus_points:
        bonus_total = float(evaluation.bonus_points.total or 0)
        bonus_breakdown = evaluation.bonus_points.breakdown or ""

    deduction_total = 0.0
    deduction_reasons = ""
    if evaluation and hasattr(evaluation, "deductions") and evaluation.deductions:
        deduction_total = float(evaluation.deductions.total or 0)
        deduction_reasons = evaluation.deductions.reasons or ""

    overall = category_total + bonus_total - deduction_total
    max_possible = category_max + role.bonus_max
    if overall > max_possible:
        overall = max_possible
    overall = max(float(role.min_final_score), min(float(role.max_final_score), overall))

    return {
        "overall_score": overall,
        "category_max": category_max,
        "categories": categories,
        "bonus_points": bonus_total,
        "bonus_breakdown": bonus_breakdown,
        "deductions": deduction_total,
        "deduction_reasons": deduction_reasons,
        "key_strengths": list(getattr(evaluation, "key_strengths", None) or []),
        "areas_for_improvement": list(
            getattr(evaluation, "areas_for_improvement", None) or []
        ),
    }


def serialize_report(
    evaluation,
    role: Role,
    candidate_name: str,
    github_data: Optional[Dict] = None,
) -> Dict:
    """Public report payload used by the website and any other callers."""
    summary = compute_evaluation_summary(evaluation, role)
    github_profile = None
    if github_data and isinstance(github_data, dict):
        profile = github_data.get("profile") or {}
        projects = github_data.get("projects") or []
        github_profile = {
            "username": profile.get("username"),
            "public_repos": profile.get("public_repos"),
            "followers": profile.get("followers"),
            "project_count": len(projects),
            "open_source_count": sum(
                1 for project in projects if project.get("project_type") == "open_source"
            ),
            "self_project_count": sum(
                1 for project in projects if project.get("project_type") == "self_project"
            ),
        }

    return {
        "candidate_name": candidate_name,
        "role": {
            "name": role.name,
            "position_title": role.position_title,
        },
        **summary,
        "github": github_profile,
        "disclaimer": (
            "This score uses an intern hiring rubric, not a typical applicant "
            "tracking system. Most company ATS tools do not fetch GitHub."
        ),
    }


def print_evaluation_results(
    evaluation, role: Role, candidate_name: str = "Candidate"
):
    """Print evaluation results in a readable format."""
    print("\n" + "=" * 80)
    print(f"📊 RESUME EVALUATION RESULTS FOR: {candidate_name}")
    print("=" * 80)

    if not evaluation:
        print("❌ No evaluation data available")
        return

    summary = compute_evaluation_summary(evaluation, role)

    print(
        f"\n🎯 OVERALL SCORE: {summary['overall_score']:.1f}/{summary['category_max']:.0f}"
    )

    print("\n📈 DETAILED SCORES:")
    print("-" * 60)
    for category in summary["categories"]:
        print(f"{category['label']}: {category['score']}/{category['max']:.0f}")
        print(f"   Evidence: {category['evidence']}")
        print()

    if summary["bonus_points"]:
        print(f"\n⭐ BONUS POINTS: {summary['bonus_points']}")
        print("-" * 30)
        print(f"   {summary['bonus_breakdown']}")

    if summary["deductions"] > 0:
        print(f"\n⚠️  DEDUCTIONS: -{summary['deductions']}")
        print("-" * 30)
        if summary["deduction_reasons"]:
            print(f"   {summary['deduction_reasons']}")

    if summary["key_strengths"]:
        print("\n✅ KEY STRENGTHS:")
        print("-" * 30)
        for i, strength in enumerate(summary["key_strengths"], 1):
            print(f"  {i}. {strength}")

    if summary["areas_for_improvement"]:
        print("\n🔧 AREAS FOR IMPROVEMENT:")
        print("-" * 30)
        for i, area in enumerate(summary["areas_for_improvement"], 1):
            print(f"  {i}. {area}")

    print("\n" + "=" * 80)


def _cache_key_for_pdf(pdf_path: str) -> str:
    digest = hashlib.sha256()
    with open(pdf_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _emit_progress(progress, stage: str, message: str, percent: int):
    if not progress:
        return
    progress({"stage": stage, "message": message, "percent": percent})


def _evaluate_resume(
    resume_data: JSONResume,
    role: Role,
    evaluation_model,
    github_data: dict = None,
    blog_data: dict = None,
):
    """Evaluate the resume using AI and display results."""

    model_params = MODEL_PARAMETERS.get(DEFAULT_MODEL)
    evaluator = ResumeEvaluator(
        role=role,
        evaluation_model=evaluation_model,
        model_name=DEFAULT_MODEL,
        model_params=model_params,
    )

    # Convert JSON resume data to text
    resume_text = convert_json_resume_to_text(resume_data)

    # Add GitHub data if available
    if github_data:
        github_text = convert_github_data_to_text(github_data)
        resume_text += github_text

    # Add blog data if available
    if blog_data:
        blog_text = convert_blog_data_to_text(blog_data)
        resume_text += blog_text

    # Evaluate the enhanced resume
    evaluation_result = evaluator.evaluate_resume(resume_text)

    # print(evaluation_result)

    return evaluation_result


def is_valid_resume_data(resume_data: JSONResume) -> bool:
    """Check if the resume data has at least some extracted core content."""
    if not resume_data:
        return False
    core_sections = [
        resume_data.basics,
        resume_data.work,
        resume_data.education,
        resume_data.skills,
        resume_data.projects,
    ]
    return any(section is not None for section in core_sections)


def find_profile(profiles, network):
    if not profiles:
        return None
    return next(
        (p for p in profiles if p.network and p.network.lower() == network.lower()),
        None,
    )


def main(pdf_path, role: Role, progress=None, write_csv: bool = True):
    evaluation_model = build_evaluation_model(role)
    cache_key = _cache_key_for_pdf(pdf_path)
    cache_filename = str(CACHE_DIR / f"resumecache_{cache_key}.json")
    github_cache_filename = str(CACHE_DIR / f"githubcache_{cache_key}.json")
    _emit_progress(progress, "parse", "Reading resume PDF...", 5)

    resume_data = None
    cache_loaded = False

    # Check if cache exists and we're in development mode
    if DEVELOPMENT_MODE and os.path.exists(cache_filename):
        print(f"Loading cached data from {cache_filename}")
        try:
            cached_data = json.loads(Path(cache_filename).read_text(encoding="utf-8"))
            loaded_resume = JSONResume(**cached_data)
            if not is_valid_resume_data(loaded_resume):
                raise ValueError("Cached resume data contains no core content")
            resume_data = loaded_resume
            cache_loaded = True
            _emit_progress(progress, "parse", "Loaded cached resume parse.", 50)
        except Exception as e:
            print(f"⚠️ Warning: Invalid cache file {cache_filename}: {e}")
            print("Ignoring cache and reprocessing PDF...")
            try:
                os.remove(cache_filename)
            except Exception as delete_err:
                print(
                    f"Failed to delete invalid cache file {cache_filename}: {delete_err}"
                )

    if not cache_loaded:
        logger.debug(
            f"Extracting data from PDF"
            + (" and caching to " + cache_filename if DEVELOPMENT_MODE else "")
        )
        _emit_progress(progress, "parse", "Extracting resume sections with the model...", 12)
        pdf_handler = PDFHandler(progress=progress)
        resume_data = pdf_handler.extract_json_from_pdf(pdf_path)

        if resume_data == None:
            _emit_progress(progress, "error", "Could not extract resume content from the PDF.", 100)
            return None

        if DEVELOPMENT_MODE:
            if is_valid_resume_data(resume_data):
                os.makedirs(os.path.dirname(cache_filename), exist_ok=True)
                Path(cache_filename).write_text(
                    json.dumps(resume_data.model_dump(), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            else:
                logger.warning(
                    "Newly extracted resume data is empty/invalid. Skipping cache write."
                )

    # Check if cache exists and we're in development mode
    github_data = {}
    github_cache_loaded = False
    if DEVELOPMENT_MODE and os.path.exists(github_cache_filename):
        print(f"Loading cached data from {github_cache_filename}")
        try:
            loaded_github = json.loads(
                Path(github_cache_filename).read_text(encoding="utf-8")
            )
            if (
                not isinstance(loaded_github, dict)
                or not loaded_github
                or "profile" not in loaded_github
            ):
                raise ValueError("Cached GitHub data is invalid or empty")
            github_data = loaded_github
            github_cache_loaded = True
            _emit_progress(progress, "github", "Loaded cached GitHub data.", 65)
        except Exception as e:
            print(f"⚠️ Warning: Invalid GitHub cache file {github_cache_filename}: {e}")
            print("Ignoring GitHub cache and refetching...")
            try:
                os.remove(github_cache_filename)
            except Exception as delete_err:
                print(
                    f"Failed to delete invalid GitHub cache file {github_cache_filename}: {delete_err}"
                )

    if not github_cache_loaded:
        # Add validation to handle None values
        profiles = []
        if resume_data and hasattr(resume_data, "basics") and resume_data.basics:
            profiles = resume_data.basics.profiles or []
        github_profile = find_profile(profiles, "Github")

        if github_profile:
            _emit_progress(progress, "github", "Fetching public GitHub profile and repos...", 58)
            print(
                f"Fetching GitHub data"
                + (
                    " and caching to " + github_cache_filename
                    if DEVELOPMENT_MODE
                    else ""
                )
            )
            github_data = fetch_and_display_github_info(
                github_profile.url, position_title=role.position_title
            )

            if (
                DEVELOPMENT_MODE
                and github_data
                and isinstance(github_data, dict)
                and "profile" in github_data
            ):
                os.makedirs(os.path.dirname(github_cache_filename), exist_ok=True)
                Path(github_cache_filename).write_text(
                    json.dumps(github_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        else:
            _emit_progress(progress, "github", "No GitHub profile found on the resume.", 58)

    _emit_progress(progress, "evaluate", "Scoring against the selected rubric...", 72)
    score = _evaluate_resume(resume_data, role, evaluation_model, github_data)

    # Get candidate name for display
    candidate_name = os.path.basename(pdf_path).replace(".pdf", "")
    if (
        resume_data
        and hasattr(resume_data, "basics")
        and resume_data.basics
        and resume_data.basics.name
    ):
        candidate_name = resume_data.basics.name

    # Print evaluation results in readable format
    print_evaluation_results(score, role, candidate_name)
    report = serialize_report(score, role, candidate_name, github_data)
    _emit_progress(progress, "done", "Report ready.", 100)

    if DEVELOPMENT_MODE and write_csv:
        csv_row = transform_evaluation_response(
            file_name=os.path.basename(pdf_path),
            evaluation=score,
            resume_data=resume_data,
            github_data=github_data,
            role=role,
        )

        # Write CSV row to a role-specific file, since each role's columns differ.
        csv_path = f"resume_evaluations_{role.name}.csv"
        file_exists = os.path.exists(csv_path)

        with open(csv_path, "a", newline="", encoding="utf-8") as csvfile:
            fieldnames = list(csv_row.keys())
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            # Write headers if file doesn't exist
            if not file_exists:
                writer.writeheader()

            # Write the row
            writer.writerow(csv_row)

    return report if progress else score


if __name__ == "__main__":
    available_roles = list_available_roles()
    parser = argparse.ArgumentParser(
        description="Score a resume against a role's rubric."
    )
    parser.add_argument(
        "pdf_path", nargs="?", help="Path to the resume PDF to evaluate"
    )
    parser.add_argument(
        "--role",
        help="Role to score against (a directory name under roles/). "
        + (f"Available: {', '.join(available_roles)}" if available_roles else ""),
    )
    parser.add_argument(
        "--init-role",
        metavar="NAME",
        help="Scaffold a new role directory under roles/ with basic template "
        "files, then exit (does not score a resume).",
    )
    args = parser.parse_args()

    # Scaffold mode: create a new role and exit.
    if args.init_role:
        try:
            role_dir = scaffold_role(args.init_role)
        except ValueError as e:
            print(f"Error: {e}")
            exit(1)
        print(f"✅ Created role '{args.init_role}' at {role_dir}")
        print("   Edit role.json, criteria.jinja and system_message.jinja, then run:")
        print(f"   python score.py <pdf_path> --role {args.init_role}")
        exit(0)

    # Scoring mode: both pdf_path and --role are required.
    if not args.pdf_path or not args.role:
        parser.error("pdf_path and --role are required (or use --init-role NAME)")

    if not os.path.exists(args.pdf_path):
        print(f"Error: File '{args.pdf_path}' does not exist.")
        exit(1)

    try:
        role = load_role(args.role)
    except ValueError as e:
        print(f"Error: {e}")
        exit(1)

    main(args.pdf_path, role)
